"""Un dessin anime a partir d un script.

Ce qui fait un dessin anime, ce n est pas l animation mais le dialogue : une
image fixe avec deux voix qui se repondent se regarde, vingt secondes de
mouvement muet, non. L outil est donc construit autour du texte.

Deux modes, parce que la video coute cher : des planches animees d un lent
mouvement de camera (une minute de film en trois minutes de calcul), ou une
vraie animation plan par plan (sept minutes de calcul pour cinq secondes).
"""
import math
import re
import unicodedata
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from core.config import reglage
from core.dossiers import dossier
from core.file_gpu import enfile
from core.registre import outil

DOSSIER = dossier("videos")

# Le dernier dessin anime fabrique. Les videos se rangent dans le meme
# dossier et partagent la meme case de memoire cote video : sans trace
# propre, « envoie-moi le dernier dessin anime » attrape la derniere
# video, qui peut etre tout autre chose.
_DERNIER_DESSIN = {}

# Le style, tenu a part : il s applique a tous les plans, sinon le dessin
# change d aspect d une scene a l autre et le film se defait.
STYLE = ("flat 2D cutout cartoon animation still, thick black outlines, "
         "simple geometric shapes, flat solid colours, no gradient, "
         "no shading, no text, wide establishing shot, plain sky")

# Des voix nettes et distinctes. Le grave et l aigu comptent plus que le
# timbre : a l oreille, c est ce qui separe deux personnages.
VOIX = [
    ("fr-FR-HenriNeural", "+6%", "-30Hz"),
    ("fr-FR-DeniseNeural", "+10%", "+35Hz"),
    ("fr-CH-FabriceNeural", "+0%", "+18Hz"),
    ("fr-FR-EloiseNeural", "+14%", "+50Hz"),
    ("fr-BE-CharlineNeural", "+4%", "-10Hz"),
    ("fr-CA-AntoineNeural", "+8%", "+8Hz"),
    ("fr-CH-ArianeNeural", "-4%", "-20Hz"),
    ("fr-BE-GerardNeural", "+2%", "-40Hz"),
]

# Ce qui ouvre un plan plutot qu une replique.
_DECOR = re.compile(r"^\s*(?:decor|d[ée]cor|scene|sc[èe]ne|plan|lieu|int|ext)"
                    r"\s*[:.\-]\s*(.+)$", re.I)
# « NOM: replique ». Le nom est court et sans ponctuation interne.
_REPLIQUE = re.compile(r"^\s*([A-Za-zÀ-ÿ0-9 '\-]{2,24}?)\s*[:>]\s*(.+)$")
# Ce qui est joue plutot que dit.
_DIDASCALIE = re.compile(r"^\s*[\[(](.+)[\])]\s*$")



def _lire_script(script):
    """Le texte du script, qu on ait donne le texte ou le nom du fichier."""
    brut = (script or "").strip()
    if "\n" in brut or len(brut) > 200:
        return brut
    if not brut:
        return brut

    from tools.clip import _resoudre
    coins = [dossier("documents"), Path.home() / "Documents",
             Path.home() / "Desktop", Path.home() / "Downloads"]

    # « le dernier script » : celui qu il vient d ecrire, sans avoir a le
    # nommer. C est la demande la plus frequente juste apres l avoir sauve.
    if re.fullmatch(r"(?:le\s+)?derni[eè]re?s?|dernier|celui\s+d\s*avant", brut, re.I):
        lot = []
        for coin in coins:
            if Path(coin).is_dir():
                lot += [f for f in Path(coin).glob("*.txt")]
        if lot:
            return Path(max(lot, key=lambda f: f.stat().st_mtime)).read_text(
                encoding="utf-8", errors="replace")
        return brut

    # L extension prononcee ou tapee ne fait pas partie du nom cherche.
    cle = re.sub(r"[^a-z0-9]", "", re.sub(r"[.\s](?:txt|md|text|srt)$", "",
                                          brut, flags=re.I).lower())
    # On ne passe pas par la resolution des medias : elle exige six
    # caracteres, garde utile pour un nom de video, absurde pour « bus ».
    fichier = None
    lot = []
    for coin in coins:
        if Path(coin).is_dir():
            lot += [f for f in Path(coin).iterdir()
                    if f.is_file() and f.suffix.lower() in
                    (".txt", ".md", ".text", ".srt")]
    for f in lot:
        if re.sub(r"[^a-z0-9]", "", f.stem.lower()) == cle:
            fichier = f
            break
    if fichier is None and cle:
        proches = [f for f in lot
                   if cle in re.sub(r"[^a-z0-9]", "", f.stem.lower())]
        if proches:
            fichier = max(proches, key=lambda f: f.stat().st_mtime)
    if fichier is None and Path(brut).is_file():
        fichier = Path(brut)
    if fichier is None:
        return brut
    for encodage in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return Path(fichier).read_text(encoding=encodage)
        except Exception:
            continue
    return brut


def _analyser(script):
    """Le script en plans : un decor, des repliques.

    On accepte qu il n y ait pas de decor du tout — beaucoup de scripts
    commencent par une replique. Un premier plan implicite est ouvert.
    """
    plans, courant = [], None
    for ligne in (script or "").replace("\r", "").split("\n"):
        if not ligne.strip():
            continue
        m = _DECOR.match(ligne)
        if m:
            courant = {"decor": m.group(1).strip(), "repliques": [],
                       "actions": []}
            plans.append(courant)
            continue
        m = _DIDASCALIE.match(ligne)
        if m:
            # « (CARTMAN entre par la gauche) » : un deplacement, joue au
            # moment ou il est ecrit. Les autres didascalies restent muettes.
            action = _lire_action(m.group(1))
            if action:
                if courant is None:
                    courant = {"decor": "", "repliques": [], "actions": []}
                    plans.append(courant)
                courant["actions"].append((len(courant["repliques"]), action))
            continue
        m = _REPLIQUE.match(ligne)
        if not m:
            continue
        if courant is None:
            courant = {"decor": "", "repliques": [], "actions": []}
            plans.append(courant)
        courant["repliques"].append((_nom_canonique(m.group(1)),
                                     m.group(2).strip()))
    return [p for p in plans if p["repliques"] or p["decor"]]


# Des mots qu un texte aplati prive d accent, et qu une voix lit alors de
# travers. Seulement des cas sans ambiguite : « a » et « a » (avoir ou
# preposition), « ou » et « ou » ne se devinent pas sans comprendre la phrase.
_ACCENTS = {
    "ca": "ça", "deja": "déjà", "voila": "voilà", "la-bas": "là-bas",
    "etre": "être", "tres": "très", "apres": "après", "meme": "même",
    "memes": "mêmes", "pere": "père", "mere": "mère", "frere": "frère",
    "ecole": "école", "francais": "français", "francaise": "française",
    "garcon": "garçon", "garcons": "garçons", "probleme": "problème",
    "problemes": "problèmes", "etait": "était", "etaient": "étaient",
    "etes": "êtes", "ete": "été", "eleves": "élèves",
    "fete": "fête", "tete": "tête", "bete": "bête", "foret": "forêt",
    "arrete": "arrête", "arretez": "arrêtez", "pret": "prêt",
    "prets": "prêts", "noel": "Noël", "deuxieme": "deuxième",
    "premiere": "première", "derniere": "dernière", "general": "général",
    "genial": "génial", "geniale": "géniale", "democratie": "démocratie",
    "economie": "économie", "economies": "économies",
    "president": "président", "reve": "rêve", "verite": "vérité",
    "societe": "société", "securite": "sécurité",
    "evidemment": "évidemment", "deteste": "déteste",
    "interessant": "intéressant", "desole": "désolé", "desolee": "désolée",
    "plutot": "plutôt", "bientot": "bientôt",
    "hopital": "hôpital", "theatre": "théâtre", "lycee": "lycée",
    "lycees": "lycées", "cinema": "cinéma", "telephone": "téléphone",
    "tele": "télé", "medecin": "médecin", "musee": "musée",
}


def _diction(texte):
    """Le texte d une replique tel qu une voix doit le lire.

    Un texte aplati (« c est ca ») est lu mot a mot : « c... est » et « ka ».
    On remet les apostrophes des elisions et les accents certains.
    """
    t = texte or ""
    # « c est », « j ai », « qu il », « l autre » : une lettre seule devant
    # une voyelle, c est une elision dont l apostrophe est tombee.
    t = re.sub(r"\b([cjldnmtsCJLDNMTS]|[Qq]u|[Jj]usqu|[Ll]orsqu|[Pp]uisqu)\s+"
               r"(?=[aeiouyhàâäéèêëîïôöùûüAEIOUYHÀÂÉÈÊ])", r"\1'", t)
    t = re.sub(r"\baujourd\s+hui\b", "aujourd'hui", t, flags=re.I)

    def accent(m):
        mot = m.group(0)
        bon = _ACCENTS.get(mot.lower())
        if not bon:
            return mot
        return bon[0].upper() + bon[1:] if mot[0].isupper() else bon
    # Un mot entier, jamais un morceau : « ca » seul devient « ça », mais
    # « cadeau » ou « Jamaica » ne bougent pas.
    return re.sub(r"(?<![\wÀ-ÿ])[A-Za-z][A-Za-z\-]*(?![\wÀ-ÿ])", accent, t)


def _nom_canonique(nom):
    """« SERVIETTESKY » -> « SERVIETTE » : le nom sous lequel le dessin et la
    voix sont ranges. La dictee passait deja par ces surnoms ; un script
    ecrit a la main, non, et Serviettesky y restait sans visage ni voix."""
    nom = (nom or "").strip()
    try:
        from core.raccourcis import SURNOMS
    except Exception:
        return nom.upper()
    vrai = SURNOMS.get(re.sub(r"[^a-z0-9]", "", _plat(nom)))
    return (vrai or nom).upper()


# --------------------------------------------------------------- deplacements

# Verbe -> genre de deplacement. L ordre compte : « s en va » avant « va ».
_VERBES_ACTION = (
    (r"entre|arrive|debarque|surgit", "entre"),
    (r"sort|part|s\s*en\s*va|quitte|s\s*enfuit|file", "sort"),
    (r"traverse", "traverse"),
    (r"recule", "recule"),
    (r"saute|bondit", "saute"),
    (r"tombe|chute|s\s*ecroule|glisse", "tombe"),
    (r"court|fonce|se\s*precipite", "court"),
    (r"marche|va|avance|s\s*approche|approche|rejoint|se\s*dirige|vient",
     "marche"),
)
_ACTION = re.compile(
    # Un nom d un ou deux mots (« mr garrison ») ; le second d au moins deux
    # lettres, pour ne pas avaler le « s » de « s approche ».
    r"^\s*([a-z0-9][a-z0-9-]{1,23}(?:\s+[a-z0-9-]{2,23})?)\s+(%s)\b(.*)$"
    % "|".join(v for v, _ in _VERBES_ACTION))

# Temps que prend chaque geste, en secondes. Fixe plutot que calcule sur la
# distance : les sous-titres doivent connaitre l horloge avant le rendu.
DUREES_ACTION = {"entre": 1.6, "sort": 1.6, "traverse": 2.2, "vers": 1.3,
                 "aller": 1.3, "recule": 0.7, "saute": 0.8, "tombe": 1.6}
# Ceux qui bloquent le dialogue : on ne parle pas avant d etre entre, et le
# plan ne se ferme pas avant la sortie. Les autres laissent parler aussitot.
BLOQUANTS = {"entre", "sort", "traverse"}


def _lire_action(texte):
    """« CARTMAN entre par la gauche » -> {qui, type, ...}, ou None."""
    from core.util import sans_accents
    # L apostrophe devient une espace : « s'approche » -> « s approche ».
    t = sans_accents((texte or "").lower()).replace("’", " ").replace("'", " ")
    t = re.sub(r"[^a-z0-9 -]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    m = _ACTION.match(t)
    if not m:
        return None
    qui, verbe, reste = m.group(1).strip(), m.group(2), m.group(3)
    genre = next(g for v, g in _VERBES_ACTION if re.fullmatch(v, verbe))
    action = {"qui": _nom_canonique(qui), "vitesse": 1.0}
    if genre == "court" or re.search(r"\ben\s+courant\b", reste):
        action["vitesse"] = 1.8
    # « doite », « droit » : la dictee ecorche souvent le mot.
    cote = ("droite" if re.search(r"\bdr?oite?\b", reste) else
            "gauche" if re.search(r"\bgauche\b", reste) else "")
    if genre in ("entre", "sort"):
        action.update(type=genre, cote=cote)
    elif genre in ("traverse", "recule", "saute", "tombe"):
        action["type"] = genre
    else:
        # « marche vers Gerald », « s approche de Kenny », « va a gauche ».
        cible = re.search(r"\b(?:vers|de|pres\s+de|jusqu\s*a)\s+"
                          r"(?:l\s*|le\s+|la\s+)?([a-z0-9][a-z0-9'-]{1,23})",
                          reste)
        if verbe == "rejoint":
            cible = re.search(r"^\s*([a-z0-9][a-z0-9'-]{1,23})", reste)
        lieu = ("centre" if re.search(r"\b(?:centre|milieu)\b", reste)
                else cote)
        if lieu:
            action.update(type="aller", lieu=lieu)
        elif cible and cible.group(1) not in ("la", "le", "les", "l"):
            action.update(type="vers", cible=_nom_canonique(cible.group(1)))
        else:
            action.update(type="vers", cible="")   # le plus proche
    return action


def _chronologie(durees_sons, actions):
    """L horloge d un plan : quand chaque replique et chaque geste commence.

    Rend (debuts des repliques, [(t0, t1, action)], duree du plan). Sans
    geste, c est exactement l horloge d avant : 0,25 s d amorce, puis les
    repliques separees de 0,35 s.
    """
    horloge = 0.25
    par_index = {}
    for idx, act in actions or []:
        par_index.setdefault(idx, []).append(act)
    debuts, gestes = [], []
    for j in range(len(durees_sons) + 1):
        for act in par_index.get(j, []):
            d = DUREES_ACTION.get(act["type"], 1.2) / act.get("vitesse", 1.0)
            gestes.append((horloge, horloge + d, act))
            if act["type"] in BLOQUANTS or j == len(durees_sons):
                horloge += d
            else:
                horloge += 0.3
        if j < len(durees_sons):
            debuts.append((horloge, horloge + durees_sons[j]))
            horloge += durees_sons[j] + 0.35
    fin = max([horloge + 0.4, 2.0] + [t1 + 0.4 for _, t1, _ in gestes])
    return debuts, gestes, fin


VOIX_FIXES = {"GARRISON": ("fr-FR-HenriNeural", "+4%", "+12Hz")}

# Une personne reelle dans un film publie parle avec une voix de caricature,
# nettement pas la sienne : une imitation de guignol, pas un hypertrucage.
VOIX_PARODIE = {"MANU": ("fr-FR-HenriNeural", "+12%", "+38Hz")}


def _voix_pour(nom):
    """La meme voix pour le meme personnage, d un rendu a l autre.

    Un tirage au hasard donnerait des voix differentes a chaque essai, et l on
    ne reconnaitrait plus personne. On derive donc la voix du nom lui-meme.
    """
    # Mrs Garrison est Mr Garrison apres son changement de sexe : meme voix.
    nom = {"MRSGARRISON": "GARRISON"}.get((nom or "").upper(), nom)
    # Le tirage lui donnait une voix quebecoise : une voix fixe en attendant
    # sa voix clonee.
    if (nom or "").upper() in VOIX_FIXES:
        return VOIX_FIXES[nom.upper()]
    somme = sum(ord(c) for c in (nom or "?").upper())
    return VOIX[somme % len(VOIX)]


def _dire(texte, voix, debit, hauteur, cible):
    """Une replique, en mp3. Rend None si la synthese echoue."""
    try:
        import asyncio

        import edge_tts

        async def _synth():
            comm = edge_tts.Communicate(texte, voix, rate=debit, pitch=hauteur)
            donnees = b""
            async for morceau in comm.stream():
                if morceau["type"] == "audio":
                    donnees += morceau["data"]
            return donnees

        mp3 = asyncio.run(_synth())
        if not mp3:
            return None
        Path(cible).write_bytes(mp3)
        return cible
    except Exception:
        return None


def _ffmpeg():
    from tools.clip import _ffmpeg as f
    return f()


def _duree(fichier):
    from tools.clip import _duree as d
    return d(fichier)


# Ce qui, dans la description d un decor, fait apparaitre du monde ou du
# texte. Le moteur (Flux, cfg 1) ne lit pas de consigne negative : dire « pas
# de foule » ne sert a rien, ne pas parler de foule, si.
_DECOR_PEUPLE = re.compile(
    r"(?:\b(?:une|un|des|la|le|les|de|a|an|the|some|with|avec)\s+)*"
    r"\b(?:foules?|gens|personnes?|passants?|manifestants?|lyc[ée]ens?|"
    r"[ée]l[èe]ves?|enfants?|habitants?|spectateurs?|public|policiers?|"
    r"crowds?|people|persons?|students?|protesters?|kids?|children|"
    r"pupils?|bystanders?|audience|men|women|man|woman)\b"
    r"(?:\s+(?:de|d'|of|with|avec)\b)*", re.I)
_DECOR_ECRIT = re.compile(
    r"\b(?:pancartes?|banderoles?|affiches?|panneaux?|slogans?|tags?|"
    r"graffitis?|inscriptions?|[ée]crit\w*|signs?|banners?|posters?|"
    r"placards?|billboards?|writings?|texts?|letters?|words?)\b", re.I)


def _decor_vide(decor):
    """La description d un decor sans personne ni texte a dessiner.

    Les personnages sont des decoupages poses par-dessus : un figurant
    dessine dans le fond, c est un inconnu au hasard. Et le moteur ecrit mal :
    une pancarte devient du charabia. Les pancartes restent, mais vierges.
    """
    d = decor or ""
    # Les noms des personnages et ce qu ils font n ont rien a faire dans le
    # decor (« Randy arrive en costume ») : on ne garde que le lieu.
    for f in PERSONNAGES.glob("*.png"):
        nom = f.stem.split("-")[0]
        if len(nom) >= 3:
            d = re.sub(r"\b%s\b[^,.;]*" % re.escape(nom), "", d, flags=re.I)
    d = re.sub(r"\b(?:Mr|Mrs|Monsieur|Madame)\b[^,.;]*", "", d)
    d = _DECOR_PEUPLE.sub("", d)
    d = _DECOR_ECRIT.sub("blank cardboard boards", d)
    # La ville du decor s appelle SUD-EUROPA : c est le seul nom qui doit
    # apparaitre ecrit (sur l ecole, un panneau d entree de ville).
    d = re.sub(r"\bsouth\s*-?\s*park\b", "SUD-EUROPA", d, flags=re.I)
    d = re.sub(r"\s{2,}", " ", re.sub(r"(?:\s*[,;]\s*)+", ", ", d)).strip(" ,;.")
    return d


def _image_plan(decor, personnages, soignee=True, papier=False):
    """Le decor d un plan, dans le style du film."""
    if papier:
        decor = _decor_vide(decor)
    # En positif, puisque le moteur ignore le negatif : un lieu desert, des
    # surfaces nues.
    demande = (STYLE_PAPIER + ", completely deserted place, nobody around, "
               "quiet empty scenery, the town is named SUD-EUROPA, walls and "
               "boards are plain and blank, the only lettering anywhere is "
               "the exact word \"SUD-EUROPA\" on the main building sign") \
        if papier else STYLE
    if personnages:
        demande += ", characters present: " + ", ".join(sorted(personnages))
    if decor:
        demande += ", scene: " + decor
    if soignee:
        from tools.flux import image_soignee
        reponse = image_soignee.__wrapped__(description=demande,
                                            format="paysage")
    else:
        from tools.image import generer_image
        reponse = generer_image.__wrapped__(description=demande,
                                            format="paysage")
    from tools.image import _DERNIERE
    chemin = _DERNIERE.get("chemin")
    return Path(chemin) if chemin and Path(chemin).exists() else None


def _plan_en_video(exe, image, sons, cible):
    """Une image, les repliques du plan par-dessus, un lent mouvement.

    La duree ne se decide pas : c est celle de la parole. Une image qui
    disparait avant la fin d une phrase est le defaut le plus visible d un
    montage de ce genre.
    """
    silence = 0.35
    duree = sum(_duree(s) + silence for s in sons) + 0.5
    duree = max(duree, 2.0)

    entrees = ["-loop", "1", "-framerate", "25", "-t", "%.2f" % duree,
               "-i", str(image)]
    for s in sons:
        entrees += ["-i", str(s)]

    filtres = [
        "[0:v]scale=2560:1440:force_original_aspect_ratio=increase,"
        "crop=2560:1440,zoompan=z='min(zoom+0.00025,1.10)':d=1:"
        "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1280x720:fps=25,"
        "trim=duration=%.2f,setpts=PTS-STARTPTS,fps=25,format=yuv420p,"
        "setsar=1[v]" % duree]

    # Les repliques s enchainent avec un silence entre elles : sans respiration,
    # deux personnages se coupent la parole et l on ne suit plus.
    morceaux = []
    depart = 0.25
    for i, s in enumerate(sons):
        filtres.append("[%d:a]adelay=%d|%d,apad[a%d]"
                       % (i + 1, int(depart * 1000), int(depart * 1000), i))
        morceaux.append("[a%d]" % i)
        depart += _duree(s) + silence
    if morceaux:
        filtres.append("%samix=inputs=%d:duration=longest:normalize=0,"
                       "atrim=duration=%.2f[a]"
                       % ("".join(morceaux), len(morceaux), duree))
        sortie = ["-map", "[v]", "-map", "[a]"]
    else:
        sortie = ["-map", "[v]"]

    args = (entrees + ["-filter_complex", ";".join(filtres)] + sortie +
            ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-t", "%.2f" % duree])
    r = subprocess.run([exe, "-y"] + args + [str(cible)], capture_output=True,
                       text=True, errors="replace", timeout=1800)
    return cible if cible.exists() else None


def _assembler(exe, plans, cible):
    """Les plans bout a bout. On reencode : leurs durees different."""
    liste = cible.with_suffix(".txt")
    liste.write_text("".join("file '%s'\n" % str(p).replace("\\", "/")
                             for p in plans), encoding="utf-8")
    subprocess.run([exe, "-y", "-f", "concat", "-safe", "0", "-i", str(liste),
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    str(cible)], capture_output=True, timeout=3600)
    try:
        liste.unlink()
    except Exception:
        pass
    return cible if cible.exists() else None


def _sous_titres(plans_dits, cible):
    """Un fichier de sous-titres a cote du film.

    On ne les incruste pas : incrustes, ils ne se retirent plus. A cote, le
    lecteur les propose et l on choisit.
    """
    def horodater(t):
        h, reste = divmod(t, 3600)
        m, s = divmod(reste, 60)
        return "%02d:%02d:%06.3f" % (h, m, s)

    lignes, n = [], 1
    for debut, fin, qui, texte in plans_dits:
        lignes.append(str(n))
        lignes.append("%s --> %s" % (horodater(debut).replace(".", ","),
                                     horodater(fin).replace(".", ",")))
        lignes.append("%s : %s" % (qui.title(), texte))
        lignes.append("")
        n += 1
    Path(cible).write_text("\n".join(lignes), encoding="utf-8")



# --------------------------------------------------------- decoupage papier

# Le style, pour le decor comme pour les personnages. Ce qui compte : des
# aplats, un contour noir franc, aucune ombre — ce qui se decoupe dans du
# papier de couleur ne connait ni degrade ni relief.
STYLE_PAPIER = ("South Park TV show art style, construction paper cutout "
                "animation, crude childlike flat shapes, thick black outlines, "
                "flat solid bright colours, no shading, no gradient, no "
                "texture, no text, Colorado mountain town, simple rectangular "
                "buildings, bright blue sky, white snow peaks in background, "
                "flat green ground")

# Magenta : aucune peau, aucun vetement de ce style ne s en approche. Le
# detourage ne mange donc jamais le personnage.
FOND_UNI = "solid magenta background"

PERSONNAGES = dossier("documents") / "personnages"


def _detourer(source, cible):
    """Retire le fond uni et rend un decoupage a bords nets.

    On ne cherche pas une couleur exacte : le moteur ne rend jamais deux fois
    le meme magenta. On cherche sa signature — rouge et bleu forts, vert
    faible — qui, elle, est stable.
    """
    import numpy as np
    from PIL import Image

    a = np.asarray(Image.open(source).convert("RGB")).astype(np.int16)
    r, v, b = a[..., 0], a[..., 1], a[..., 2]
    fond = (r > 110) & (b > 80) & (v < r - 55) & (v < b + 70)
    if fond.mean() < 0.15:
        return None  # le fond n a pas ete respecte : inutilisable
    alpha = np.where(fond, 0, 255).astype("uint8")
    ys, xs = np.where(alpha > 0)
    if len(xs) < 500:
        return None
    image = Image.fromarray(np.dstack([a.astype("uint8"), alpha]), "RGBA")
    image = image.crop((int(xs.min()), int(ys.min()),
                        int(xs.max()) + 1, int(ys.max()) + 1))
    image.save(cible)
    return cible


def _yeux(alpha, rgb, H, L):
    """La paire d yeux du visage : le repere le plus sur du dessin.

    On ne cherche pas une couleur mais un rang : le haut d un personnage est
    fait de deux grands tons — sa peau et le blanc de ses yeux — et le
    second est le plus clair. Le creme de Cartman est plus sombre que la peau
    d Annie, aucune valeur fixe ne pouvait les servir tous deux.

    Puis l on verifie que c est bien une paire. Sur le grand dessin de Chef,
    le ton le plus clair du haut du corps est son tablier : clair, oui, mais
    seul, enorme, et au milieu du ventre.
    """
    import numpy as np
    from scipy import ndimage

    dedans = (alpha > 0)
    dedans[int(H * 0.62):, :] = False
    clairs = dedans & (rgb.min(axis=2) > 110)
    if clairs.sum() < 40:
        return None

    gros = (rgb[clairs] // 24 * 24).astype(np.int16)
    vus, combien = np.unique(gros, axis=0, return_counts=True)
    ordre = np.argsort(-combien)
    tons = [(vus[i], int(combien[i])) for i in ordre[:6]
            if combien[i] > clairs.sum() * 0.04]
    if not tons:
        return None
    ton = max(tons, key=lambda t: int(t[0].min()))[0]
    if len(tons) > 1 and int(ton.min()) <= int(tons[0][0].min()):
        return None

    blanc = clairs & (np.abs(rgb - ton).max(axis=2) < 34)
    marques, n = ndimage.label(blanc)
    if n < 2:
        return None
    boites = []
    for i, (fy, fx) in enumerate(ndimage.find_objects(marques), start=1):
        aire = int((marques[fy, fx] == i).sum())
        if aire < max(20, H * L * 0.0012):
            continue
        boites.append((aire, fy.start, fy.stop, fx.start, fx.stop))
    boites.sort(reverse=True)

    # La paire : deux taches jumelles, a la meme hauteur, ecartees d environ
    # leur propre largeur, dans le haut du dessin.
    for i in range(min(4, len(boites))):
        for j in range(i + 1, min(5, len(boites))):
            a, b = boites[i], boites[j]
            ha, hb = a[2] - a[1], b[2] - b[1]
            la, lb = a[4] - a[3], b[4] - b[3]
            if max(a[0], b[0]) > 3 * min(a[0], b[0]):
                continue                      # pas jumelles
            if abs((a[1] + a[2]) / 2 - (b[1] + b[2]) / 2) > max(ha, hb) * 0.6:
                continue                      # pas a la meme hauteur
            ecart = abs((a[3] + a[4]) / 2 - (b[3] + b[4]) / 2)
            if not (max(la, lb) * 0.5 < ecart < max(la, lb) * 3.5):
                continue                      # trop colles ou trop loin
            haut, gauche = min(a[1], b[1]), min(a[3], b[3])
            bas, droite = max(a[2], b[2]), max(a[4], b[4])
            if (haut + bas) / 2 > H * 0.45 or (droite - gauche) > L * 0.7:
                continue                      # trop bas, ou trop large
            return (haut, bas, gauche, droite)
    return None

def _bouche(decoupage):
    """Ou se trouve la bouche, en fraction du decoupage.

    On part des yeux — deux ovales blancs, sans equivoque — et l on ne
    cherche la bouche que dans la bande posee juste dessous, a leur largeur.
    Ailleurs, un dessin est plein de taches sombres qui n en sont pas : un nez,
    un col, des mains, un lettrage sur une veste.
    """
    try:
        import numpy as np
        from PIL import Image
        from scipy import ndimage

        a = np.asarray(Image.open(decoupage).convert("RGBA"))
        alpha, rgb = a[..., 3], a[..., :3].astype(np.int16)
        H, L = alpha.shape

        oeil = _yeux(alpha, rgb, H, L)
        if oeil is None:
            return None
        oy1, oy2, ox1, ox2 = oeil
        haut, large = max(1, oy2 - oy1), max(1, ox2 - ox1)

        # Une veste blanche n est pas un oeil : si la tache est aussi haute
        # qu un tiers du dessin, ce n est pas un visage qu on a trouve.
        if haut > H * 0.35 or large > L * 0.75:
            return None

        # La bouche se tient juste sous les yeux, dans leur largeur. Mesure
        # sur les six visages ou elle est dessinee : un tiers de la hauteur
        # des yeux plus bas, et jamais plus.
        return {"x": float((ox1 + ox2) / 2 / L),
                "y": float((oy2 + haut * 0.35) / H),
                "l": float(large * 0.34 / L)}
    except Exception:
        return None

def _cutout(nom, description=""):
    """Le decoupage d un personnage, fabrique une fois puis conserve."""
    import json

    PERSONNAGES.mkdir(parents=True, exist_ok=True)
    propre = re.sub(r"[^a-z0-9]+", "-", (nom or "").lower()).strip("-")
    fiche = PERSONNAGES / (propre + ".json")
    decoupage = PERSONNAGES / (propre + ".png")
    if decoupage.exists() and fiche.exists():
        return decoupage, repere_de(propre)

    # On n invente plus un personnage absent. Fabriquer d office donnait des
    # enfants sans rapport avec ce qu il avait en tete, et le film partait de
    # travers sans que rien ne le signale.
    return None, None



def _fond_dominant(a, marge=4):
    """La couleur du fond : celle qui domine le pourtour de l image.

    Ni la mediane des bords, qui rate un fond legerement irregulier, ni la
    couleur la plus repandue de l image, qui prend la peau d un personnage
    pour un fond des lors qu il occupe le cadre. Le fond se definit par sa
    position, pas par sa quantite : il touche le bord, et un personnage cadre
    ne fait pas le tour de son image.
    """
    import numpy as np
    bords = np.concatenate([
        a[:marge].reshape(-1, 3), a[-marge:].reshape(-1, 3),
        a[:, :marge].reshape(-1, 3), a[:, -marge:].reshape(-1, 3)])
    grossier = (bords // 8 * 8).astype(np.int16)
    vues, comptes = np.unique(grossier, axis=0, return_counts=True)
    return vues[int(np.argmax(comptes))].astype(np.int16) + 4

def _enregistrer(nom, morceau, alpha):
    """Range un decoupage sous son nom, avec le repere de sa bouche."""
    import json

    import numpy as np
    from PIL import Image

    propre = re.sub(r"[^a-z0-9]+", "-", (nom or "").lower()).strip("-")
    if not propre:
        return None
    PERSONNAGES.mkdir(parents=True, exist_ok=True)
    decoupage = PERSONNAGES / (propre + ".png")
    Image.fromarray(np.dstack([morceau, alpha]), "RGBA").save(decoupage)
    repere = _bouche(decoupage) or {"x": 0.5, "y": 0.30, "l": 0.14}
    (PERSONNAGES / (propre + ".json")).write_text(json.dumps(repere),
                                                  encoding="utf-8")
    return decoupage, repere


def _bandes(profil, seuil=0, creux_mini=3):
    """Les segments pleins d un profil, avec leurs bornes.

    Un creux d une ou deux lignes n est pas une separation : c est un trait
    fin ou un defaut de compression. On exige donc un blanc franc.
    """
    segments, debut, vide = [], None, 0
    for i, v in enumerate(profil):
        if v > seuil:
            if debut is None:
                debut = i
            vide = 0
        else:
            if debut is not None:
                vide += 1
                if vide >= creux_mini:
                    segments.append((debut, i - vide + 1))
                    debut, vide = None, 0
    if debut is not None:
        segments.append((debut, len(profil)))
    return segments


def _grille(combien, largeur, hauteur, elancement=1.7):
    """Colonnes et rangees, deduites du nombre de personnages.

    On essaie tous les arrangements et l on garde celui qui donne des cases a
    la forme d une personne debout : plus hautes que larges.
    """
    meilleur, ecart_mini = (combien, 1), None
    for colonnes in range(1, combien + 1):
        rangees = -(-combien // colonnes)
        if colonnes * rangees > combien + colonnes - 1:
            continue          # trop de cases vides
        forme = (hauteur / rangees) / max(1.0, largeur / colonnes)
        ecart = abs(forme - elancement)
        if ecart_mini is None or ecart < ecart_mini:
            meilleur, ecart_mini = (colonnes, rangees), ecart
    return meilleur


def _taches(masque, finesse=3):
    """Les taches de dessin, avec leur taille et leur centre.

    On erode d abord : les noms ecrits sous les personnages sont faits de
    traits fins et disparaissent, tandis qu un personnage ne perd que sa
    bordure. Sans cela le nom sert de pont entre deux rangees et deux
    personnages ne comptent que pour un.
    """
    import numpy as np
    from scipy import ndimage

    # Un blanc enferme dans un contour est du dessin : le tablier blanc d un
    # personnage sur une planche blanche, le blanc d un oeil. Seul le fond, qui
    # touche le bord de l image, n est enferme nulle part.
    plein = ndimage.binary_fill_holes(masque)
    noyaux = ndimage.binary_opening(plein, structure=np.ones((finesse,
                                                              finesse)))
    etiquettes, combien = ndimage.label(noyaux)
    if not combien:
        return etiquettes, []
    rangs = range(1, combien + 1)
    tailles = ndimage.sum(noyaux, etiquettes, rangs)
    centres = ndimage.center_of_mass(noyaux, etiquettes, rangs)
    _taches.plein = plein
    return etiquettes, [(int(t), c[0], c[1], i)
                        for i, (t, c) in enumerate(zip(tailles, centres), 1)]


def _silhouette(etiquettes, graines, masque, epaisseur=2):
    """Le contour du personnage, a partir de ses taches erodees.

    D un seul morceau, on rend a la tache l epaisseur que l erosion lui a
    prise. En plusieurs morceaux, c est que le dessin ne trace pas la
    silhouette — une veste blanche sur un fond blanc n a pas de flancs. On
    tend alors une enveloppe autour des morceaux : elle deborde un peu, mais
    elle rend l homme entier.
    """
    import numpy as np
    from scipy import ndimage

    depart = np.isin(etiquettes, list(graines))
    zone = ndimage.binary_dilation(depart, structure=np.ones((3, 3)),
                                   iterations=epaisseur) & masque
    # Le titre de la planche frole le pompon de Stan et s y accroche. Mais
    # c est une tache a part, que la grille n a donnee a personne : ce qui
    # appartient a une autre tache n est pas a lui.
    zone &= ~((etiquettes > 0) & ~depart)
    zone = ndimage.binary_fill_holes(zone)
    if len(graines) < 2:
        return _sans_miettes(zone)

    from skimage.morphology import convex_hull_image
    ys, xs = np.where(zone)
    y1, y2 = int(ys.min()), int(ys.max()) + 1
    x1, x2 = int(xs.min()), int(xs.max()) + 1
    tendue = np.zeros_like(zone)
    tendue[y1:y2, x1:x2] = convex_hull_image(zone[y1:y2, x1:x2])
    return tendue


def _corps_principal(zone):
    """La plus grosse piece d un decoupage."""
    import numpy as np
    from scipy import ndimage

    pieces, combien = ndimage.label(zone)
    if combien < 2:
        return zone
    tailles = ndimage.sum(zone, pieces, range(1, combien + 1))
    return pieces == int(np.argmax(tailles)) + 1


def _sans_miettes(zone, fil=5):
    """Ne garde que le corps : le reste vient de la planche.

    Ce qui flotte a cote part de soi. Ce qui ne tient que par un fil — le
    titre de la planche accroche au pompon d un bonnet — demande de rompre
    le fil : on amincit, et si deux corps apparaissent la ou l on en voulait
    un, le petit etait un intrus.
    """
    import numpy as np
    from scipy import ndimage

    zone = _corps_principal(zone)
    noyau = ndimage.binary_opening(zone, structure=np.ones((fil, fil)))
    _, combien = ndimage.label(noyau)
    if combien < 2:
        return zone
    garde = _corps_principal(noyau)
    return ndimage.binary_dilation(garde, structure=np.ones((3, 3)),
                                   iterations=fil // 2) & zone


def decouper_planche(planche, noms, tolerance=24, colonnes=0):
    """Decoupe une planche en un fichier par personnage.

    La grille sert a savoir qui habite ou ; le contour, lui, est pris sur le
    dessin. Un personnage qui deborde de sa case reste entier, et la legende de
    la case voisine ne peut plus entrer dans le cadre.
    """
    import numpy as np
    from PIL import Image

    a = np.asarray(Image.open(planche).convert("RGB")).astype(np.int16)
    masque = np.abs(a - _fond_dominant(a)).max(axis=2) > tolerance
    H, L = masque.shape
    etiquettes, taches = _taches(masque)
    if not taches:
        return []

    n = len(noms)
    if colonnes:
        cols, rangees = colonnes, -(-n // colonnes)
    else:
        cols, rangees = _grille(n, L, H)

    retenus, pris = [], set()
    for indice, nom in enumerate(noms):
        ligne, colonne = divmod(indice, cols)
        y1, y2 = H * ligne / rangees, H * (ligne + 1) / rangees
        x1, x2 = L * colonne / cols, L * (colonne + 1) / cols

        chez_lui = [t for t in taches
                    if t[3] not in pris and y1 <= t[1] < y2 and x1 <= t[2] < x2]
        if not chez_lui:
            continue
        # Un personnage peut se briser en plusieurs taches : le blanc de sa
        # veste sur un fond blanc coupe le lien. Toutes celles de la case qui
        # comptent sont a lui.
        gros = max(t[0] for t in chez_lui)
        graines = {t[3] for t in chez_lui if t[0] >= max(150, gros * 0.10)}
        if not graines:
            continue
        pris |= graines

        zone = _silhouette(etiquettes, graines, masque)
        ys, xs = np.where(zone)
        yb1, yb2 = int(ys.min()), int(ys.max()) + 1
        xb1, xb2 = int(xs.min()), int(xs.max()) + 1
        fait = _enregistrer(nom, a[yb1:yb2, xb1:xb2].astype("uint8"),
                            np.where(zone[yb1:yb2, xb1:xb2],
                                     255, 0).astype("uint8"))
        if fait:
            retenus.append(nom)
    return retenus


def detourer_personnage(image, nom, tolerance=24):
    """Un seul personnage dans un fichier : on retire son fond.

    Un dessin livre avec un fond transparent est deja detoure : son canal
    alpha dit exactement ou il s arrete, et chercher une couleur de fond qui
    n existe pas ne ferait que degrader ce qu on nous donne.
    """
    import numpy as np
    from PIL import Image

    ouverte = Image.open(image)
    rgba = np.asarray(ouverte.convert("RGBA"))
    a = rgba[..., :3].astype(np.int16)

    # Deux fonds peuvent coexister : celui que le canal alpha declare, et
    # celui qui est peint dans l image — un blanc, ou le damier que certains
    # exports laissent en dur sous un alpha pourtant opaque. On exige donc
    # les deux : est du dessin ce qui est a la fois opaque et different du
    # fond peint.
    masque = (rgba[..., 3] > 128)
    masque &= np.abs(a - _fond_dominant(a)).max(axis=2) > tolerance
    if masque.mean() > 0.02:
        return _ranger_detourage(nom, a, masque)
    etiquettes, taches = _taches(masque)
    if not taches:
        return None
    gros = max(t[0] for t in taches)
    graines = {t[3] for t in taches if t[0] >= gros * 0.10}
    zone = _silhouette(etiquettes, graines, masque)
    ys, xs = np.where(zone)
    if len(xs) < 150:
        return None
    y1, y2 = int(ys.min()), int(ys.max()) + 1
    x1, x2 = int(xs.min()), int(xs.max()) + 1
    return _enregistrer(nom, a[y1:y2, x1:x2].astype("uint8"),
                        np.where(zone[y1:y2, x1:x2], 255, 0).astype("uint8"))


def _ranger_detourage(nom, a, masque):
    """Enregistre un dessin isole, resserre sur ce qu il contient.

    Le fond n est pas ce qui a la couleur du fond : c est ce que le bord de
    l image peut atteindre. La distinction compte pour la toque blanche de
    Chef sur un fond blanc — elle a la couleur du fond mais elle est enfermee
    dans son contour, donc le bord ne l atteint pas, donc elle est a lui.
    """
    import numpy as np
    from scipy import ndimage

    creux = ~masque
    marques, n = ndimage.label(creux)
    if n:
        dehors = set(int(v) for v in np.concatenate([
            marques[0], marques[-1], marques[:, 0], marques[:, -1]]) if v)
        if dehors:
            masque = masque | (creux & ~np.isin(marques, list(dehors)))
    zone = _sans_miettes(ndimage.binary_fill_holes(masque))
    ys, xs = np.where(zone)
    if len(xs) < 150:
        return None
    y1, y2 = int(ys.min()), int(ys.max()) + 1
    x1, x2 = int(xs.min()), int(xs.max()) + 1
    return _enregistrer(nom, a[y1:y2, x1:x2].astype("uint8"),
                        np.where(zone[y1:y2, x1:x2], 255, 0).astype("uint8"))


@outil(
    nom="importer_personnages",
    description=(
        "Prepare des personnages dessines pour l animation : une planche "
        "entiere avec ses noms, ou un dessin par personnage. Pour 'importe "
        "les personnages de cette planche', 'ajoute ce personnage'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "image": {"type": "string",
                      "description": "Nom du fichier de la planche ou du dessin."},
            "noms": {"type": "string",
                     "description": "Les noms, separes par des virgules, dans l ordre de lecture. Un seul nom pour un dessin unique."},
        },
        "required": ["image", "noms"],
    },
    lent=True,
    phrase_attente="Je prepare les personnages.",
)
def importer_personnages(image: str, noms: str) -> str:
    from tools.modifier_image import _par_le_nom

    chemin = Path(image) if Path(image).is_file() else _par_le_nom(image)
    if chemin is None or not Path(chemin).exists():
        return f"Je ne trouve pas l image « {image} »."

    liste = [n.strip() for n in re.split(r"[,;]", noms or "") if n.strip()]
    if not liste:
        return "Donne-moi les noms des personnages, dans l ordre de lecture."

    if len(liste) == 1:
        fait = detourer_personnage(chemin, liste[0])
        if not fait:
            return "Je n ai pas su detourer ce dessin."
        retenus = liste
    else:
        retenus = decouper_planche(chemin, liste)
    if not retenus:
        return "Je n ai reconnu aucun personnage sur cette image."
    return ("%d personnage(s) prepare(s) : %s. Ils sont dans %s et serviront "
            "tels quels dans les films."
            % (len(retenus), ", ".join(retenus), PERSONNAGES))

# Les inclinaisons possibles d une piece de carton qu on agite : assez fines
# pour que le mouvement soit continu, assez peu nombreuses pour etre calculees
# une seule fois par personnage.
ANGLES = (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0)

# Les adultes depassent les enfants d une bonne tete. Une mesure automatique
# serait plus elegante, mais le rapport tete-corps ne se lit proprement que
# sur les visages ou la paire d yeux se detache, soit un tiers d entre eux :
# une liste que l on complete vaut mieux qu une mesure qui se trompe.
ADULTES = {"chef", "garrison", "randy", "gerald", "sharon", "liane",
           "sheila", "mackey", "jimbo", "stephen", "linda", "maire", "manu",
           "satan", "mrsgarrison"}
GRANDEUR_ADULTE = 1.5
# Des decoupages aux proportions humaines (une photo, pas un dessin de la
# serie) : la tete n y fait qu un septieme du corps, contre la moitie chez les
# adultes de South Park. A hauteur egale sa tete parait minuscule ; on le
# grandit donc, cadre en buste, le bas du corps hors champ.
GRANDEURS = {"manu": 2.1}


def fiche_de(propre):
    """La fiche d un personnage telle qu elle est rangee (bouche, taille)."""
    import json
    try:
        return json.loads((PERSONNAGES / (propre + ".json")).read_text(
            encoding="utf-8"))
    except Exception:
        return {}


def repere_de(propre):
    """La fiche, corrections d office comprises.

    Une correction faite a la voix (« corrige » dans la fiche) l emporte sur
    celles du code : c est la plus recente, et c est celle qu on a vue.
    """
    repere = fiche_de(propre)
    if propre in BOUCHES_FORCEES and not repere.get("corrige"):
        repere = dict(repere, **BOUCHES_FORCEES[propre])
    return repere


def _grandeur(nom):
    cle = _plat(nom)
    # Ce que la fiche dit d abord : c est la qu un personnage ajoute a la
    # voix est declare adulte ou grandi, sans toucher au code.
    fiche = fiche_de(cle)
    if fiche.get("grandeur"):
        return float(fiche["grandeur"])
    if "adulte" in fiche and cle not in GRANDEURS:
        return GRANDEUR_ADULTE if fiche["adulte"] else 1.0
    return GRANDEURS.get(cle, GRANDEUR_ADULTE if cle in ADULTES else 1.0)

# Positions de bouche corrigees manuellement (detection auto incorrecte).
# Chef a une grande toque qui pousse les yeux haut dans l image, d ou la
# bouche detectee sur le nez plutot que sur le bas du visage.
# x, y : centre de la bouche en fraction de l image (0 = haut/gauche).
# l : largeur de la bouche en fraction de la largeur de l image.
BOUCHES_FORCEES = {
    "chef": {"x": 0.50, "y": 0.385, "l": 0.14, "fin_bras": 0.795},
    # Mesuree sur ses levres rouges (27/09) : le reperage automatique la
    # posait sur les lunettes, deux fois trop large.
    # fin_bras : ses mains s arretent a 71 % de sa hauteur (mesure sur la
    # peau). Sans cela la bande des bras emportait le pantalon.
    "mrsgarrison": {"x": 0.539, "y": 0.278, "l": 0.10, "fin_bras": 0.71},
}


def _plat(nom):
    """Le nom reduit a ce qui compte pour le reconnaitre.

    Les accents deviennent leur lettre nue, pas un tiret : l utilisateur ecrit
    « Gerald » avec un accent, et le transformer en « g-ralde » le rendait
    introuvable. Une lettre accentuee est la meme lettre.
    """
    nu = unicodedata.normalize("NFD", (nom or "").lower())
    nu = "".join(c for c in nu if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", nu).strip("-")


def _enveloppe(exe, son, fps, lissage=2):
    """L energie d une replique, image par image, ramenee entre 0 et 1.

    C est ce relevé qui ouvrira la bouche. On decode en PCM brut avec ffmpeg
    plutot que d ajouter une bibliotheque d analyse : une moyenne quadratique
    par image suffit, et elle suit la parole de tres pres.
    """
    import numpy as np

    try:
        brut = subprocess.run(
            [exe, "-v", "quiet", "-i", str(son), "-f", "s16le", "-ac", "1",
             "-ar", "16000", "-"], capture_output=True, timeout=300).stdout
        x = np.frombuffer(brut, dtype="<i2").astype(np.float32) / 32768.0
    except Exception:
        return None
    if x.size < 160:
        return None

    par_image = max(1, int(16000 / fps))
    entiers = x[: (x.size // par_image) * par_image].reshape(-1, par_image)
    niveaux = np.sqrt((entiers ** 2).mean(axis=1))

    # Un lissage court : sans lui la bouche tremble sur chaque micro-attaque
    # et l on voit le calcul plutot que la parole.
    if lissage > 1:
        noyau = np.ones(lissage) / lissage
        niveaux = np.convolve(niveaux, noyau, mode="same")

    fort = float(np.percentile(niveaux, 95))
    if fort <= 1e-5:
        return None
    return np.clip(niveaux / fort, 0.0, 1.0)


# Les formes de bouche, de la plus fermee a la plus ouverte. Chacune est
# donnee en fractions de la largeur de reference : largeur, hauteur.
FORMES = ((0.90, 0.06), (0.70, 0.30), (0.95, 0.60), (1.05, 0.95))


def _forme_bouche(niveau):
    """Quelle bouche, pour quelle energie."""
    if niveau < 0.10:
        return FORMES[0]
    if niveau < 0.32:
        return FORMES[1]
    if niveau < 0.62:
        return FORMES[2]
    return FORMES[3]


def _dessiner_bouche(vue, bx, by, bl, niveau):
    """Une bouche a la maniere de la serie : contour, interieur, langue.

    Un ovale noir plein fait un trou dans le visage. La serie dessine un
    contour ferme, un interieur sombre, et laisse voir la langue quand la
    bouche s ouvre grand. Trois traits au lieu d un, et le personnage parle
    au lieu d etre perce.
    """
    from PIL import ImageDraw
    crayon = ImageDraw.Draw(vue)
    large, haute = _forme_bouche(niveau)
    dx = bl * large / 2
    dy = max(1.0, bl * haute / 2)

    if niveau < 0.10:
        # Au repos, la bouche est un trait : c est ainsi qu on la dessine.
        crayon.line([bx - dx * 0.8, by, bx + dx * 0.8, by],
                    fill=(30, 25, 25), width=max(2, int(bl * 0.09)))
        return

    cadre = [bx - dx, by - dy, bx + dx, by + dy]
    crayon.ellipse(cadre, fill=(38, 22, 22),
                   outline=(20, 15, 15), width=max(1, int(bl * 0.06)))
    if haute > 0.45:
        # La langue : un croissant clair au fond, visible seulement quand la
        # bouche est franchement ouverte.
        lx, ly = dx * 0.62, dy * 0.42
        crayon.ellipse([bx - lx, by + dy - ly * 1.7, bx + lx, by + dy - 1],
                       fill=(150, 74, 74))



# Les positions que peut prendre un bras, en degres autour de l epaule.
BALANCEMENTS = (-14.0, -9.0, -4.5, 0.0, 4.5, 9.0, 14.0)


def _decouper_bras(img, repere, part=0.20):
    """Detache les deux bras du corps et rend leur pivot d epaule.

    Rend (corps, bras) ou chaque bras est (image, x, y, pivot_x, pivot_y),
    les coordonnees etant relatives au coin haut-gauche du personnage. Rend
    (img, []) si la silhouette ne s y prete pas — un personnage vu de face,
    bras croises, n a pas de bras a detacher.
    """
    import numpy as np
    from PIL import Image

    # Bras croises ou colles au corps (Mr Garrison) : rien a detacher. Sans
    # cela la bande des bras descend le long du pantalon, et un morceau de
    # jambe se balance comme une troisieme jambe.
    if repere.get("bras_fixes"):
        return img, []

    a = np.asarray(img.convert("RGBA"))
    alpha = a[..., 3] > 128
    H, L = alpha.shape

    # Le torse commence sous la tete, et la tete finit sous la bouche.
    y1 = int(min(H - 4, (repere.get("y", 0.3) + 0.10) * H))
    y2 = int(H * 0.93)
    # Correction manuelle : chez Chef, la bande des bras descendait le long du
    # pantalon (largeur du pantalon superieure aux deux tiers de celle des
    # mains) et les jambes se balancaient avec les bras. Les bras s arretent
    # aux mains, a cette fraction de la hauteur.
    if repere.get("fin_bras"):
        y2 = min(y2, int(H * repere["fin_bras"]))
    if y2 - y1 < 12:
        return img, []

    bande = alpha[y1:y2]
    gauches, droites = [], []
    for ligne in bande:
        xs = np.where(ligne)[0]
        gauches.append(xs[0] if len(xs) else -1)
        droites.append(xs[-1] if len(xs) else -1)
    gauches, droites = np.array(gauches), np.array(droites)
    larges = np.where(gauches >= 0, droites - gauches, 0)
    if (larges > 0).sum() < 8:
        return img, []

    # Le bras s arrete aux hanches. En dessous il n y a plus que les jambes,
    # et la silhouette y est nettement plus etroite que la ou les bras
    # pendent : on coupe la bande des que la largeur tombe sous les deux
    # tiers de son maximum, sans quoi on decoupe un mollet en croyant
    # prendre une main.
    plein = int(larges.max())
    fin = len(larges)
    for i in range(len(larges)):
        if larges[i] < plein * 0.66:
            fin = i
            break
    if fin < 8:
        return img, []
    gauches, droites, larges = gauches[:fin], droites[:fin], larges[:fin]
    y2 = y1 + fin
    epaisseur = max(3, int(np.median(larges[larges > 0]) * part))

    bord_g = np.array(gauches)
    bord_d = np.array(droites)
    masque_g = np.zeros_like(alpha)
    masque_d = np.zeros_like(alpha)
    for i in range(len(bord_g)):
        if bord_g[i] < 0:
            continue
        y = y1 + i
        masque_g[y, bord_g[i]:bord_g[i] + epaisseur] = True
        masque_d[y, max(0, bord_d[i] - epaisseur + 1):bord_d[i] + 1] = True
    masque_g &= alpha
    masque_d &= alpha

    if masque_g.sum() < 40 or masque_d.sum() < 40:
        return img, []

    corps = a.copy()
    corps[..., 3] = np.where(masque_g | masque_d, 0, a[..., 3])
    membres = []
    for masque, cote in ((masque_g, "g"), (masque_d, "d")):
        ys, xs = np.where(masque)
        bx1, bx2 = int(xs.min()), int(xs.max()) + 1
        by1, by2 = int(ys.min()), int(ys.max()) + 1
        # On rembourre : une piece qui tourne deborde de son cadre.
        marge = max(8, (by2 - by1) // 2)
        piece = np.zeros((by2 - by1 + 2 * marge, bx2 - bx1 + 2 * marge, 4),
                         dtype="uint8")
        bout = a[by1:by2, bx1:bx2].copy()
        bout[..., 3] = np.where(masque[by1:by2, bx1:bx2], bout[..., 3], 0)
        piece[marge:marge + (by2 - by1), marge:marge + (bx2 - bx1)] = bout
        # L epaule : le coin haut, du cote du corps.
        pivot_x = marge + ((bx2 - bx1 - 1) if cote == "g" else 0)
        pivot_y = marge
        membres.append((Image.fromarray(piece, "RGBA"), bx1 - marge,
                        by1 - marge, pivot_x, pivot_y))
    return Image.fromarray(corps, "RGBA"), membres

class _Trajet:
    """Ou se trouve un personnage, et comment il se tient, a chaque instant.

    Des points de passage (t, x) relies en ligne droite ; entre deux points
    qui different, il marche. S y ajoutent les sauts et les chutes, qui ne
    changent pas sa place.
    """

    def __init__(self, x0, largeur):
        self.points = [(0.0, float(x0))]
        self.largeur = largeur
        self.sauts, self.chutes = [], []
        self.vitesses = []          # (t0, t1, vitesse) des marches

    def x(self, t):
        pts = self.points
        if t <= pts[0][0]:
            return pts[0][1]
        for (ta, xa), (tb, xb) in zip(pts, pts[1:]):
            if ta <= t <= tb:
                return xa if tb == ta else xa + (xb - xa) * (t - ta) / (tb - ta)
        return pts[-1][1]

    def aller(self, t0, t1, x_cible, vitesse=1.0):
        depart = self.x(t0)
        # Un nouveau trajet remplace ce qui restait du precedent.
        self.points = [p for p in self.points if p[0] < t0] + \
            [(t0, depart), (t1, float(x_cible))]
        self.vitesses.append((t0, t1, vitesse))

    def pas(self, t):
        """Vitesse de marche a l instant t (0 s il est immobile)."""
        for t0, t1, v in self.vitesses:
            if t0 <= t <= t1 and abs(self.x(t1) - self.x(t0)) > 2:
                return v
        return 0.0


def _trajets(poses, gestes, L):
    """Chaque geste du script traduit en trajet, pour chaque personnage."""
    trajets = {}
    for nom, img, x, _, _, _, _, _ in poses:
        trajets[nom] = _Trajet(x, img.width)
    par_plat = {_plat(n): n for n in trajets}

    def trouver(qui):
        cle = _plat(qui)
        if cle in par_plat:
            return par_plat[cle]
        for plat, n in par_plat.items():
            if plat.startswith(cle) or cle.startswith(plat):
                return n
        return None

    # Ceux qui entrent sont hors champ jusque-la, du cote par ou ils viennent.
    for t0, t1, act in gestes:
        n = trouver(act["qui"])
        if n and act["type"] == "entre":
            tr = trajets[n]
            if len(tr.points) == 1:
                place = tr.points[0][1]
                cote = act.get("cote") or ("gauche" if place < L / 2
                                           else "droite")
                dehors = -tr.largeur - 20 if cote == "gauche" else L + 20
                tr.points = [(0.0, float(dehors))]
                act["_place"] = place

    for t0, t1, act in sorted(gestes, key=lambda g: g[0]):
        n = trouver(act["qui"])
        if not n:
            continue
        tr, genre, v = trajets[n], act["type"], act.get("vitesse", 1.0)
        ici = tr.x(t0)
        if genre == "entre":
            tr.aller(t0, t1, act.get("_place", L / 2 - tr.largeur / 2), v)
        elif genre == "sort":
            cote = act.get("cote") or ("gauche" if ici + tr.largeur / 2 < L / 2
                                       else "droite")
            tr.aller(t0, t1, -tr.largeur - 20 if cote == "gauche" else L + 20,
                     v)
        elif genre == "traverse":
            vers_droite = ici + tr.largeur / 2 < L / 2
            tr.aller(t0, t1, L * 0.9 - tr.largeur if vers_droite else L * 0.1,
                     v)
        elif genre == "aller":
            lieu = act.get("lieu")
            cible = {"gauche": L * 0.1, "droite": L * 0.9 - tr.largeur}.get(
                lieu, L / 2 - tr.largeur / 2)
            tr.aller(t0, t1, cible, v)
        elif genre == "vers":
            autre = trouver(act["cible"]) if act.get("cible") else None
            if autre is None or autre == n:
                # « s approche » sans dire de qui : le plus proche.
                autres = [m for m in trajets if m != n]
                if not autres:
                    continue
                autre = min(autres, key=lambda m: abs(trajets[m].x(t0) - ici))
            ox = trajets[autre].x(t0)
            ow = trajets[autre].largeur
            # On s arrete a cote de lui, du cote d ou l on vient.
            cible = (ox - tr.largeur - 12) if ici < ox else (ox + ow + 12)
            tr.aller(t0, t1, cible, v)
        elif genre == "recule":
            # On recule en s eloignant du plus proche, ou vers le bord.
            autres = [m for m in trajets if m != n]
            proche = (min(autres, key=lambda m: abs(trajets[m].x(t0) - ici))
                      if autres else None)
            loin = (-1 if proche and trajets[proche].x(t0) > ici else 1) \
                if proche else (-1 if ici > L / 2 else 1)
            tr.aller(t0, t1, ici + loin * 170, 0.8)
        elif genre == "saute":
            tr.sauts.append((t0, t1))
        elif genre == "tombe":
            tr.chutes.append((t0, t1))
    return trajets


def _poser(calque, piece, x, y):
    """Colle une piece sur un calque transparent, meme en debordant."""
    try:
        calque.alpha_composite(piece, (int(x), int(y)))
    except Exception:
        calque.paste(piece, (int(x), int(y)), piece)


def _plan_papier(exe, decor, distribution, repliques, sons, cible,
                 actions=None):
    """Un plan joue par des marionnettes : elles se balancent, parlent et,
    si le script le dit, se deplacent.

    On fabrique les images une par une plutot que d empiler des filtres :
    ouvrir une bouche au bon moment n est pas exprimable en filtre, et le
    calcul reste negligeable a cote de tout le reste.
    """
    import numpy as np
    from PIL import Image, ImageDraw, ImageOps

    L, H, FPS = 1280, 720, 25
    debut_par_replique, gestes, duree = _chronologie(
        [_duree(s) for s in sons], actions)

    # Le relevé de chaque réplique, une fois pour toutes.
    enveloppes = [_enveloppe(exe, s, FPS) for s in sons]

    fond = Image.open(decor).convert("RGB").resize((L, H), Image.LANCZOS)

    # Les personnages, poses au sol, repartis sur la largeur. Qui entre par
    # la droite prend une place a droite, et inversement : sinon ils se
    # croisent pour rejoindre une place tiree de l ordre de parole.
    poses = []
    cote_d_entree = {}
    for _, act in actions or []:
        if act["type"] == "entre" and act.get("cote"):
            cote_d_entree.setdefault(_plat(act["qui"]), act["cote"])
    noms = sorted(distribution.keys(), key=lambda n: {
        "gauche": 0, "droite": 2}.get(cote_d_entree.get(_plat(n)), 1))
    ouverts = [Image.open(distribution[n][0]).convert("RGBA") for n in noms]

    # Une hauteur commune, mais reduite tant que la bande deborde : a cinq
    # personnages, la taille d un seul les faisait se chevaucher et sortir du
    # cadre. On les veut alignes et entiers, pas empiles.
    grands = [_grandeur(n) for n in noms]
    haut = int(H * 0.55)
    while haut > H * 0.18:
        large = sum(max(1, int(o.width * haut * g / o.height))
                    for o, g in zip(ouverts, grands))
        # Un personnage cadre en buste depasse par le bas, voulu : il ne doit
        # pas faire rapetisser les autres.
        plus_haut = haut * max(min(g, GRANDEUR_ADULTE) for g in grands)
        if large <= L * 0.88 and plus_haut <= H * 0.80:
            break
        haut -= 6

    grandeurs = [_grandeur(n) for n in noms]
    tailles = [(max(1, int(o.width * haut * g / o.height)), int(haut * g))
               for o, g in zip(ouverts, grandeurs)]
    total = sum(w for w, _ in tailles)
    ecart = (L - total) / (len(noms) + 1)
    curseur = ecart
    for i, nom in enumerate(noms):
        img = ouverts[i].resize(tailles[i], Image.LANCZOS)
        # Les inclinaisons sont calculees une fois : faire tourner un
        # decoupage coute cher, et un plan de quatorze secondes compte trois
        # cent cinquante images.
        # Les bras se detachent quand ils pendent le long du corps. Alors le
        # corps ne s incline plus : deux mouvements composes se contrarient,
        # et le balancement des bras suffit a le faire vivre.
        corps, membres = _decouper_bras(img, distribution[nom][1])
        if membres:
            penchees = {0.0: corps}
            tournes = [{b: piece.rotate(b, resample=Image.BICUBIC,
                                        center=(px, py))
                        for b in BALANCEMENTS}
                       for piece, dx, dy, px, py in membres]
            attaches = [(dx, dy) for _, dx, dy, _, _ in membres]
        else:
            penchees = {a: (img if a == 0 else
                            img.rotate(a, resample=Image.BICUBIC,
                                       expand=False))
                        for a in ANGLES}
            tournes, attaches = [], []
        # Un dessin coupe a la taille se pose sur le bord bas de l image :
        # la coupe se confond alors avec le cadre, au lieu de flotter au-dessus
        # du sol. On le reconnait a ce que son encre touche son propre bord.
        alpha = np.asarray(img.split()[-1])
        coupe = bool((alpha[-2:, :] > 128).mean() > 0.06)
        pied = H - img.height + (int(H * 0.06) if coupe else -int(H * 0.08))
        # Plus grand que l image : la tete reste dans le cadre, c est le bas
        # du corps qui sort.
        pied = max(pied, int(H * 0.06))
        poses.append((nom, img, int(curseur), pied, distribution[nom][1],
                      penchees, tournes, attaches))
        curseur += img.width + ecart

    trajets = _trajets(poses, gestes, L)

    travail = cible.parent / (cible.stem + "-images")
    travail.mkdir(parents=True, exist_ok=True)
    total = int(duree * FPS)
    regard = {}         # nom -> vrai s il regarde vers la gauche (miroir)
    for n in range(total):
        t = n / FPS
        vue = fond.copy()
        for j, (nom, img, x, y, repere, penchees, tournes,
                attaches) in enumerate(poses):
            parle, niveau = False, 0.0
            for k, (a, b) in enumerate(debut_par_replique):
                if nom == repliques[k][0] and a <= t <= b:
                    parle = True
                    env = enveloppes[k] if k < len(enveloppes) else None
                    if env is not None and len(env):
                        niveau = float(env[min(len(env) - 1,
                                               int((t - a) * FPS))])
                    else:
                        # Sans relevé, on retombe sur le battement d avant.
                        niveau = 0.8 if int(t * 10) % 2 == 0 else 0.0
                    break

            # Tout le monde respire, meme en silence : une scene ou seul
            # celui qui parle bouge a l air d un photomontage. Chacun sur sa
            # phase, sinon ils battent la mesure ensemble.
            phase = j * 1.7
            saut = 2.0 * math.sin(t * 1.6 + phase)
            angle = 0.8 * math.sin(t * 1.1 + phase)
            if parle:
                # Celui qui parle est agite comme une marionnette qu on tient
                # a la main pendant qu elle dit son texte.
                saut += 5.0 * abs(math.sin(t * 5.5 + phase))
                angle += 2.2 * math.sin(t * 4.0 + phase)

            # Le deplacement : ou il est, s il marche, s il saute ou tombe.
            tr = trajets[nom]
            xt = tr.x(t)
            pas = tr.pas(t)
            if pas:
                # Il avance en sautillant, comme dans la serie : pas de
                # jambes qui marchent, un rebond a chaque pas.
                saut += 9.0 * pas * abs(math.sin(t * 9.0 * pas + phase))
                avant = tr.x(max(0.0, t - 1.0 / FPS))
                if abs(xt - avant) > 0.5:
                    regard[nom] = xt < avant      # vers la gauche : miroir
            for t0, t1 in tr.sauts:
                if t0 <= t <= t1:
                    u = (t - t0) / (t1 - t0)
                    saut += 150.0 * 4 * u * (1 - u)
            chute = 0.0
            for t0, t1 in tr.chutes:
                if t0 <= t <= t1:
                    u = (t - t0) / (t1 - t0)
                    # Il bascule, reste a terre, puis se releve.
                    chute = 85.0 * (min(1.0, u / 0.25) if u < 0.7
                                    else max(0.0, (1 - u) / 0.3))

            # Tout est dessine sur un calque a sa taille, avec une marge :
            # le retourner ou le faire basculer d un bloc garde la bouche et
            # les bras a leur place.
            marge = img.height if chute else 40
            calque = Image.new("RGBA", (img.width + 2 * marge,
                                        img.height + 2 * marge), (0, 0, 0, 0))
            if tournes:
                # Le corps reste droit ; ce sont les bras qui parlent.
                pris = 0.0
                _poser(calque, penchees[0.0], marge, marge)
                geste = 4.0 * math.sin(t * 1.3 + phase)
                if parle:
                    geste += 9.0 * math.sin(t * 3.1 + phase) + 4.0 * niveau
                if pas:
                    geste += 10.0 * math.sin(t * 9.0 * pas + phase)
                for c, (jeu, (ax, ay)) in enumerate(zip(tournes, attaches)):
                    vise = geste if c == 0 else -geste
                    proche = min(BALANCEMENTS, key=lambda b: abs(b - vise))
                    _poser(calque, jeu[proche], marge + ax, marge + ay)
            else:
                pris = min(ANGLES, key=lambda a: abs(a - angle))
                _poser(calque, penchees[pris], marge, marge)

            # La bouche est posee sur lui : elle penche avec lui. Sans cela
            # elle glisserait hors du visage des qu il s incline. Et elle
            # s ouvre a la mesure de ce qu il dit a cet instant.
            if parle and repere:
                cx, cy = img.width / 2.0, img.height / 2.0
                px = repere["x"] * img.width - cx
                py = repere["y"] * img.height - cy
                rad = math.radians(pris)
                rx = px * math.cos(rad) + py * math.sin(rad)
                ry = -px * math.sin(rad) + py * math.cos(rad)
                bl = max(5, repere["l"] * img.width)
                _dessiner_bouche(calque, marge + cx + rx, marge + cy + ry,
                                 bl, niveau)

            if regard.get(nom):
                calque = ImageOps.mirror(calque)
            if chute:
                # Il tombe a la renverse, pivot aux pieds : tourne vers la
                # gauche, il tombe a droite (sens horaire, angle negatif).
                sens = -1 if regard.get(nom) else 1
                calque = calque.rotate(sens * chute, resample=Image.BICUBIC,
                                       center=(calque.width / 2,
                                               marge + img.height))
            dy = int(round(saut))
            vue.paste(calque, (int(round(xt)) - marge, y - dy - marge),
                      calque)
        vue.save(travail / ("%05d.png" % n))

    entrees = ["-framerate", str(FPS), "-i", str(travail / "%05d.png")]
    for s in sons:
        entrees += ["-i", str(s)]
    filtres, morceaux = [], []
    for i, s in enumerate(sons):
        d = int(debut_par_replique[i][0] * 1000)
        filtres.append("[%d:a]adelay=%d|%d,apad[a%d]" % (i + 1, d, d, i))
        morceaux.append("[a%d]" % i)
    args = entrees
    if morceaux:
        filtres.append("%samix=inputs=%d:duration=longest:normalize=0,"
                       "atrim=duration=%.2f[a]"
                       % ("".join(morceaux), len(morceaux), duree))
        args += ["-filter_complex", ";".join(filtres), "-map", "0:v",
                 "-map", "[a]"]
    args += ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-t", "%.2f" % duree]
    subprocess.run([exe, "-y"] + args + [str(cible)], capture_output=True,
                   timeout=3600)
    shutil.rmtree(travail, ignore_errors=True)
    return cible if cible.exists() else None


def tenues_de(nom):
    """Les tenues disponibles pour quelqu un, la premiere etant l ordinaire."""
    cle = _plat(nom)
    if not cle or not PERSONNAGES.is_dir():
        return []
    sortie = []
    for f in sorted(PERSONNAGES.glob("*.png")):
        plat = _plat(f.stem)
        if plat == cle:
            sortie.insert(0, ("", f))
        elif plat.startswith(cle + "-"):
            sortie.append((plat[len(cle) + 1:], f))
    return sortie


def dessin_de(nom, tenue=""):
    """Le dessin de quelqu un dans la tenue demandee.

    Faute de la tenue demandee, on rend sa tenue ordinaire : mieux vaut un
    film ou Cartman est mal habille qu un message d erreur.
    """
    lot = tenues_de(nom)
    if not lot:
        return None
    if tenue:
        voulu = _plat(tenue)
        for etiquette, f in lot:
            if etiquette == voulu:
                return f
        for etiquette, f in lot:
            if etiquette and (voulu in etiquette or etiquette in voulu):
                return f
    return lot[0][1]


def catalogue(cible=None, colonnes=6, haut=210):
    """Une planche de tout ce qui est disponible, tenue par tenue.

    C est ce qu on regarde avant d ecrire un script, et c est la que se voit
    un decoupage rate.
    """
    import json

    from PIL import Image, ImageDraw

    lot = sorted(PERSONNAGES.glob("*.png"))
    if not lot:
        return None
    lignes = -(-len(lot) // colonnes)
    planche = Image.new("RGB", (colonnes * 160, lignes * (haut + 20)),
                        (32, 32, 38))
    crayon = ImageDraw.Draw(planche)
    for i, f in enumerate(lot):
        img = Image.open(f).convert("RGBA")
        e = min(150 / img.width, (haut - 8) / img.height)
        vue = img.resize((max(1, int(img.width * e)),
                          max(1, int(img.height * e))))
        x = (i % colonnes) * 160 + (160 - vue.width) // 2
        y = (i // colonnes) * (haut + 20) + (haut - vue.height)
        planche.paste(vue, (x, y), vue)
        try:
            repere = repere_de(f.stem)
            bx = x + repere["x"] * vue.width
            by = y + repere["y"] * vue.height
            bl = max(5, repere["l"] * vue.width)
            crayon.ellipse([bx - bl / 2, by - bl / 3, bx + bl / 2,
                            by + bl / 3], outline=(255, 60, 60), width=2)
        except Exception:
            pass
        crayon.text(((i % colonnes) * 160 + 5,
                     (i // colonnes) * (haut + 20) + haut + 4),
                    f.stem, fill=(225, 225, 130))
    cible = Path(cible) if cible else (PERSONNAGES / "_catalogue.png")
    planche.save(cible)
    return cible


@outil(
    nom="catalogue_personnages",
    description=(
        "Montre tous les personnages disponibles et leurs tenues, avec le "
        "repere de bouche. Pour 'montre-moi les personnages', 'quelles "
        "tenues j ai', 'fais le catalogue'."
    ),
    parametres={"type": "object", "properties": {}, "required": []},
)
def catalogue_personnages() -> str:
    lot = sorted(PERSONNAGES.glob("*.png"))
    if not lot:
        return ("Je n ai aucun personnage. Depose des dessins, ou donne-moi "
                "une planche a decouper.")
    par_personne = {}
    for f in lot:
        cle = _plat(f.stem).split("-")[0]
        par_personne.setdefault(cle, []).append(f.stem)
    fichier = catalogue()
    detail = ", ".join(
        "%s (%d)" % (n, len(v)) if len(v) > 1 else n
        for n, v in sorted(par_personne.items()))
    return ("%d dessins pour %d personnages : %s. La planche est dans %s."
            % (len(lot), len(par_personne), detail, fichier))

@outil(
    nom="dessin_anime",
    description=(
        "Fabrique un dessin anime a partir d un script : un decor par plan, "
        "une voix par personnage, les images calees sur la parole. Pour "
        "'fais-moi un dessin anime avec ce script', 'anime ce dialogue'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "script": {"type": "string",
                       "description": "Le script. « DECOR: ... » ouvre un plan, « NOM: replique » fait parler."},
            "titre": {"type": "string", "description": "Nom du film."},
            "papier": {"type": "boolean",
                       "description": "Decoupage papier : personnages detoures, bouche qui bat. Le vrai procede."},
            "anime": {"type": "boolean",
                      "description": "Vraie animation plan par plan. Beaucoup plus long."},
            "ecran": {"type": "string", "description": "Nom d un ecran."},
        },
        "required": ["script"],
    },
    lent=True,
    phrase_attente="Je fabrique le dessin anime, ca prendra un moment.",
)
@enfile("dessin anime", "titre")
def dessin_anime(script: str, titre: str = "", papier: bool = True,
                 anime: bool = False, ecran: str = "",
                 ouvrir: bool = True, sans_clone: str = "") -> str:
    # sans_clone : personnages (separes par des virgules) qui ne prennent pas
    # leur voix clonee dans ce film-ci — une personne reelle dans un film
    # destine a etre publie.
    parodies = {_plat(n) for n in (sans_clone or "").split(",") if n.strip()}
    exe = _ffmpeg()
    if not exe:
        return "ffmpeg est introuvable, je ne peux pas monter le film."

    # Un script s ecrit, il ne se dicte pas : on accepte donc aussi le nom du
    # fichier qui le contient. C est ainsi qu on l emploiera vraiment.
    script = _lire_script(script)

    plans = _analyser(script)
    # Ce qui sera dit (et sous-titre) : apostrophes et accents remis.
    for plan in plans:
        plan["repliques"] = [(qui, _diction(texte))
                             for qui, texte in plan["repliques"]]
    if not plans:
        return ("Je n ai pas reconnu de script. Ecris-le dans un fichier "
                "texte range dans Documents/MU-TH-UR : une ligne par "
                "replique, « NOM: ce qu il dit », et « DECOR: ... » pour "
                "changer de plan. Puis dis-moi son nom.")

    travail = DOSSIER / (".anime-%s" % uuid.uuid4().hex[:8])
    travail.mkdir(parents=True, exist_ok=True)
    faits, dits, horloge = [], [], 0.0
    personnages = set()
    try:
        for i, plan in enumerate(plans):
            for qui, _ in plan["repliques"]:
                personnages.add(qui)

            # Les voix d abord : elles decident de la duree du plan.
            # Celles qui sont clonees se demandent d un seul coup : charger le
            # modele prend une demi-minute, le faire par replique couterait
            # plus cher que tout le reste du film.
            clonees = {}
            try:
                from tools import voix_clonee
                lot = [(qui, texte,
                        travail / ("p%02d-r%02d.wav" % (i, j)))
                       for j, (qui, texte) in enumerate(plan["repliques"])
                       if _plat(qui) not in parodies]
                clonees = voix_clonee.dire_tout(lot)
            except Exception:
                clonees = {}

            sons = []
            for j, (qui, texte) in enumerate(plan["repliques"]):
                clone = travail / ("p%02d-r%02d.wav" % (i, j))
                if str(clone) in clonees and clone.exists():
                    sons.append(clone)
                    continue
                # Pas d extrait pour ce personnage : sa voix de synthese fait
                # l affaire, et le film se fait quand meme.
                voix, debit, hauteur = _voix_pour(qui)
                if _plat(qui) in parodies:
                    voix, debit, hauteur = VOIX_PARODIE.get(
                        (qui or "").upper(), (voix, debit, hauteur))
                f = travail / ("p%02d-r%02d.mp3" % (i, j))
                if _dire(texte, voix, debit, hauteur, f):
                    sons.append(f)

            morceau = travail / ("plan-%02d.mp4" % i)
            if papier:
                # Le decor se fabrique vide : les personnages sont des pieces
                # rapportees, comme dans le procede d origine.
                image = _image_plan(plan["decor"], set(), papier=True)
                if image is None:
                    continue
                distribution = {}
                # Ceux qui parlent, puis ceux qui ne font que passer : un
                # personnage peut traverser le plan sans rien dire.
                parlants = {_plat(q) for q, _ in plan["repliques"]}
                acteurs = [q for q, _ in plan["repliques"]] + [
                    a["qui"] for _, a in plan.get("actions", [])
                    if _plat(a["qui"]) not in parlants]
                for qui in acteurs:
                    if qui in distribution:
                        continue
                    decoupage, repere = _cutout(qui)
                    if decoupage:
                        distribution[qui] = (decoupage, repere)
                manquants = [q for q in acteurs if q not in distribution]
                if manquants:
                    return ("Je n ai pas de dessin pour %s. Depose-les dans "
                            "%s, ou donne-moi une planche a decouper."
                            % (", ".join(sorted(set(manquants))), PERSONNAGES))
                if not distribution:
                    continue
                if _plan_papier(exe, image, distribution, plan["repliques"],
                                sons, morceau,
                                plan.get("actions", [])) is None:
                    continue
            else:
                image = _image_plan(plan["decor"], personnages)
                if image is None:
                    continue
                if _plan_en_video(exe, image, sons, morceau) is None:
                    continue
            faits.append(morceau)

            # Les sous-titres suivent l horloge du film, pas celle du plan —
            # et celle du plan tient compte des deplacements.
            debuts, _, _ = _chronologie(
                [_duree(s) for s in sons],
                plan.get("actions", []) if papier else [])
            for j, (qui, texte) in enumerate(plan["repliques"]):
                if j < len(debuts):
                    a, b = debuts[j]
                    dits.append((horloge + a, horloge + b, qui, texte))
            horloge += _duree(morceau)

        if not faits:
            return "Aucun plan n a abouti."

        propre = re.sub(r"[^a-z0-9]+", "-", (titre or "dessin-anime").lower())
        cible = DOSSIER / ("%s-%s.mp4" % (time.strftime("%Y%m%d-%H%M%S"),
                                          propre[:40].strip("-")))
        if _assembler(exe, faits, cible) is None:
            return "Les plans sont faits mais l assemblage a echoue."
        _sous_titres(dits, cible.with_suffix(".srt"))
    finally:
        shutil.rmtree(travail, ignore_errors=True)

    from tools.video import _DERNIERE
    _DERNIERE["chemin"] = cible
    _DERNIERE["demande"] = titre or "dessin anime"
    _DERNIER_DESSIN["chemin"] = cible
    _DERNIER_DESSIN["demande"] = titre or "dessin anime"

    if ecran:
        from tools.video import envoyer_video_ecran
        return "Voila le film. %s" % envoyer_video_ecran(ecran=ecran)
    # Le film du jour se fabrique a l aube : il ne doit pas s ouvrir seul
    # sur un ecran ou personne ne le regarde.
    if ouvrir:
        try:
            import os
            os.startfile(str(cible))
        except Exception:
            pass
    return ("Dessin anime de %d plans et %d repliques, %.0f secondes. "
            "Les sous-titres sont a cote, en .srt."
            % (len(faits), len(dits), _duree(cible)))
