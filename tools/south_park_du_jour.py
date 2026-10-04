"""South Park du jour : un court episode tire de la une du Monde.

Chaque matin, Mother lit la une, ecarte les sujets graves, fait ecrire un
script par Claude et le confie a dessin_anime. Le film est ensuite remis en
format vertical (TikTok, Reels) et envoye par mail en apercu. Rien ne part en
ligne sans « publie » : une blague ratee sur l actualite ne se rattrape pas.

Reglages (config.yaml, section south_park_du_jour) :
  actif: true          fabrication quotidienne automatique
  heure: 7             pas avant cette heure
  modele: sonnet       le scenariste (abonnement Claude)
  instagram: ""        nom du compte dedie dans instagram.comptes
"""
import datetime as dt
import json
import logging
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from core.config import reglage
from core.dossiers import dossier
from core.registre import outil

LOG = logging.getLogger("jarvis")
_RACINE = Path(__file__).resolve().parent.parent
ETAT = _RACINE / "logs" / "south_park_du_jour.json"
FLUX = "https://www.lemonde.fr/rss/une.xml"

# Les personnes reelles jouent en caricature : jamais avec leur voix clonee
# (leur faire dire des repliques inventees avec leur vraie voix serait un
# hypertrucage), mais avec une voix de guignol, et sans fait invente.
PARODIES = {"manu": "Emmanuel Macron, le president"}
EXCLUS = set()

# Une une sur un drame ne se tourne pas en blague. Le scenariste a la meme
# consigne ; ce filtre la tient meme s il l oublie.
_GRAVE = re.compile(
    r"\b(?:morts?|mortes?|tu[ée]e?s?|d[ée]c[èe]s|d[ée]c[ée]d[ée]e?s?|attentats?|"
    r"meurtres?|assassin\w*|viols?|violée?s?|victimes?|otages?|massacres?|"
    r"g[ée]nocides?|suicides?|p[ée]docrimin\w*|f[ée]minicides?|deuil|"
    r"obs[èe]ques|s[ée]ismes?|naufrages?|crash|tuerie|fusillade|"
    r"bless[ée]e?s?|disparu\w*|enl[èe]vement)\b", re.I)

_VERROU = threading.Lock()


# ------------------------------------------------------------------ l etat

def _etat():
    try:
        return json.loads(ETAT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _sauver(etat):
    ETAT.parent.mkdir(parents=True, exist_ok=True)
    ETAT.write_text(json.dumps(etat, ensure_ascii=False, indent=1),
                    encoding="utf-8")


# ------------------------------------------------------------------ la une

def _une(combien=14):
    """Les titres de la une : [(titre, chapo)], sujets graves retires."""
    import requests
    import xml.etree.ElementTree as ET
    r = requests.get(FLUX, timeout=20,
                     headers={"User-Agent": "Mozilla/5.0 (MU-TH-UR)"})
    r.raise_for_status()
    sujets = []
    for item in ET.fromstring(r.content).findall(".//item"):
        titre = " ".join((item.findtext("title") or "").split())
        chapo = " ".join(re.sub(r"<[^>]+>", " ",
                                item.findtext("description") or "").split())
        if not titre or _GRAVE.search(titre + " " + chapo):
            continue
        sujets.append((titre, chapo[:300]))
    return sujets[:combien]


# ------------------------------------------------------------ la distribution

def _distribution():
    """Les personnages jouables, et parmi eux ceux qui ont leur vraie voix."""
    from tools.dessin_anime import PERSONNAGES
    noms = sorted({f.stem.split("-")[0].lower()
                   for f in PERSONNAGES.glob("*.png")
                   if not f.stem.startswith("_")} - EXCLUS)
    voix = dossier("documents") / "voix"
    avec_voix = [n for n in noms if (voix / ("%s.wav" % n)).exists()
                 and n not in PARODIES]
    return noms, avec_voix


SYSTEME = """Tu es scenariste pour une parodie francaise dans l esprit de South Park.
On te donne la une du Monde du jour. Tu ecris un episode tres court, drole,
mordant mais jamais haineux, en francais, dans l esprit irreverencieux de
la serie.

Regles absolues :
- Choisis UN sujet de la une, de preference politique, economique, societal ou
  sportif. Jamais un drame, un deces, un attentat, une catastrophe.
- Les personnes reelles peuvent etre citees mais n apparaissent JAMAIS a l ecran
  et ne parlent jamais, sauf celles donnees comme caricatures dans la liste.
  Seuls les personnages de la liste jouent.
- Une caricature (ex : MANU pour Emmanuel Macron) se moque de son image et de
  ses positions publiques connues, a la maniere des Guignols. Elle ne fait
  jamais d annonce, de promesse ou d aveu qui pourrait passer pour une vraie
  declaration, et elle ne parle que de sujets reellement a la une.
- N invente aucun fait sur une personne reelle : l humour vient des reactions
  et des theories absurdes des personnages, pas de fausses nouvelles. Une
  personne reelle n appelle personne, ne demande rien, ne dit rien d invente
  (« Untel m a appele », « Untel a dit que » sont interdits) ; seuls ses actes
  publics reels, tels qu ils sont a la une, peuvent etre cites et moques.
- Pas d insulte visant une origine, une religion, un handicap, une orientation.
- 2 ou 3 plans, 10 a 14 repliques en tout, 20 mots maximum par replique.
- Ecris les nombres en toutes lettres et evite les sigles (les voix les lisent).
- Ecris un francais correct, avec TOUS les accents, cedilles et apostrophes :
  « c'est », « ça », « déjà », « été », « démocratie ». Le texte est lu par une
  voix : « ca » serait prononce « ka », « c est » serait hache.

Rends EXACTEMENT ce format, rien d autre. Exemple de forme (le sujet et les
repliques sont a inventer, les noms viennent de la liste fournie) :

TITRE: La retraite à soixante-dix ans
LEGENDE: Cartman a un plan pour les retraites #SouthPark #Parodie #Retraites
---
DECOR: South Park elementary school classroom
(CARTMAN entre par la gauche)
CARTMAN: Les gars, le gouvernement veut faire payer les vieux. Moi je trouve ça génial.
KYLE: Cartman, ta grand-mère est retraitée.
CARTMAN: Justement, elle me doit encore vingt dollars.
DECOR: South Park town street with snow
RANDY: Qui a dit qu'on touchait aux retraites ?

Le lieu apres DECOR s ecrit EN ANGLAIS et decrit un lieu VIDE : ni personnage,
ni foule, ni action, ni texte a lire (pas de pancarte ecrite, pas de slogan).
Les personnages sont poses par-dessus, le texte est dans les repliques. Chaque replique commence par le nom
du personnage en majuscules, pris dans la liste. Une didascalie de
deplacement s ecrit seule sur sa ligne entre parentheses.
"""


# Le registre des dialogues, regle par south_park_du_jour.ton :
# 0 = tout public, 1 = familier, 2 = cru comme la serie (par defaut).
TONS = {
    0: "Registre tout public : pas de gros mots.",
    1: "Registre familier : quelques jurons (merde, putain), sans plus.",
    2: ("Registre CRU, comme la vraie serie : les personnages jurent sans "
        "arret (putain, merde, bordel, connard, enfoire, ta gueule, va te "
        "faire foutre), s insultent entre eux avec mauvaise foi, humour "
        "pipi-caca et absurde assume. Cartman est odieux, egoiste et "
        "manipulateur, Kyle s enerve et le traite de gros lard, Randy "
        "s emballe et jure, Kenny dit des horreurs incomprehensibles, Mr "
        "Garrison est aigri et cynique. Il faut que ca jure VRAIMENT : au "
        "moins la moitie des repliques contiennent un gros mot ou une "
        "insulte, et la chute est la plus crue. Sois FIDELE a la serie : "
        "Cartman peut etre raciste, antisemite envers Kyle, de mauvaise foi "
        "sur tout, comme dans l original, a condition que ce soit lui le "
        "ridicule (Kyle le remet a sa place, la chute se retourne contre "
        "lui) et jamais le groupe vise. Seules limites : pas d insulte "
        "raciale ou homophobe proprement dite (les mots qui font fermer un "
        "compte), rien de sexuel impliquant les enfants, et aucune "
        "declaration inventee d une personne reelle."),
}


def _systeme():
    try:
        ton = int(reglage("south_park_du_jour.ton", 2))
    except Exception:
        ton = 2
    return SYSTEME + "\n" + TONS.get(ton, TONS[2]) + "\n"


def _demander(prompt):
    """Le texte du scenariste : Claude par l abonnement, sinon le modele local."""
    try:
        from core.agentsdk import AgentSDKProvider
        p = AgentSDKProvider()
        p.modele = reglage("south_park_du_jour.modele", "sonnet")
        p.delai = 300
        texte = p._appeler(prompt, systeme=_systeme())
        LOG.info("south park du jour: script ecrit par Claude (%s)", p.modele)
        return texte
    except Exception as e:
        LOG.warning("south park du jour: Claude indisponible (%s), repli local", e)
    import requests
    hote = reglage("ollama.hote", "http://localhost:11434").rstrip("/")
    r = requests.post(hote + "/api/chat", timeout=300, json={
        "model": reglage("ollama.modele", "qwen2.5:7b"), "stream": False,
        "messages": [{"role": "system", "content": _systeme()},
                     {"role": "user", "content": prompt}],
        "options": {"temperature": 0.8}})
    r.raise_for_status()
    LOG.warning("south park du jour: script ecrit par le modele LOCAL")
    return (r.json().get("message") or {}).get("content", "")


def _normaliser(texte):
    """Un scenario mis en page (titres, gras, crochets) ramene au format.

    Claude ecrit volontiers comme pour un studio : « ## SCENE 1 — INT.
    SALON », « **[RANDY]** : ... », « *(il sort)* ». Le fond est bon, seule
    la forme change : on la remet a plat plutot que de jeter l episode.
    """
    sortie = []
    for l in (texte or "").splitlines():
        l = l.strip()
        m = re.match(r"^#+\s*(?:SC[EÈ]NE\s*\d*\s*[—–\-:]\s*)?(.*)$", l, re.I)
        if m and l.startswith("#"):
            lieu = re.sub(r"^(?:INT|EXT)\.?\s*", "", m.group(1)).strip()
            lieu = re.sub(r"\s*[—–]\s*(?:JOUR|NUIT|SOIR|MATIN)\s*$", "", lieu,
                          flags=re.I)
            # Un titre d episode n est pas un lieu.
            if lieu and re.search(r"(?:SC[EÈ]NE|INT|EXT|[—–])", m.group(1), re.I):
                sortie.append("DECOR: " + lieu.replace("—", ",").replace("–", ","))
            continue
        l = l.replace("**", "").replace("__", "")
        l = re.sub(r"\[([A-Za-zÀ-ÿ' \-]{2,24})\]", r"\1", l)
        # Didascalie en italique, seule sur sa ligne : muette.
        if re.fullmatch(r"\*[^*]+\*", l):
            continue
        l = re.sub(r"\*\([^)]*\)\*|\*[^*]*\*", " ", l)
        sortie.append(re.sub(r"\s{2,}", " ", l).strip())
    return "\n".join(sortie)


CORRECTEUR = """Tu es correcteur d orthographe francaise. On te donne des
phrases numerotees « 3| texte ». Rends EXACTEMENT les memes numeros, une ligne
par phrase, au format « 3| texte corrige », en corrigeant seulement l
orthographe : accents, cedilles, apostrophes (c'est, ça, déjà, l'école,
qu'il). Garde les memes mots, le meme registre familier, la meme ponctuation.
Aucune autre ligne, aucun commentaire, pas de markdown."""


def _accentuer(script):
    """Le script relu, accents et apostrophes remis.

    Le scenariste ecrit parfois tout sans accent (il imite la consigne) : une
    voix lit alors « ca » comme « ka ». Une relecture par le meme modele, sur
    l orthographe seule, coute quelques secondes et vaut mieux qu un dictionnaire.
    """
    lettres = re.findall(r"[a-zà-ÿ]", script.lower())
    accents = re.findall(r"[à-ÿ]", script.lower())
    if not lettres or len(accents) / len(lettres) > 0.015:
        return script     # deja accentue
    # Seul le texte des repliques part en relecture, numerote : les noms, les
    # decors et l ordre ne peuvent pas bouger, on les remet nous-memes.
    lignes = script.splitlines()
    repliques = {}
    for i, l in enumerate(lignes):
        m = re.match(r"^(\s*[A-Za-zÀ-ÿ0-9 '\-]{2,24}?\s*:\s*)(.+)$", l)
        if m and not re.match(r"^\s*D[EÉ]COR\b", l, re.I):
            repliques[i] = (m.group(1), m.group(2))
    if not repliques:
        return script
    demande = "\n".join("%d| %s" % (i, t) for i, (_, t) in repliques.items())
    try:
        from core.agentsdk import AgentSDKProvider
        p = AgentSDKProvider()
        p.modele = "haiku"
        p.delai = 120
        rendu = p._appeler(demande, systeme=CORRECTEUR) or ""
    except Exception as e:
        LOG.warning("south park du jour: relecture impossible (%s)", e)
        return script
    corriges = {int(m.group(1)): m.group(2).strip()
                for m in re.finditer(r"^\s*(\d+)\s*\|\s*(.+)$", rendu, re.M)}
    for i, (tete, texte) in repliques.items():
        # Le correcteur souligne parfois ses corrections en gras (**mot**) :
        # les sous-titres l afficheraient tel quel.
        nouveau = re.sub(r"\*\*|__|`", "", corriges.get(i, "")).strip()
        # Une correction qui change trop le texte n est plus une correction.
        if nouveau and abs(len(nouveau) - len(texte)) <= max(8, len(texte) // 5):
            lignes[i] = tete + nouveau
    return "\n".join(lignes)


def _lire_reponse(texte, permis):
    """(titre, legende, script) ou une raison de refus."""
    from tools.dessin_anime import _analyser, _plat
    texte = (texte or "").strip().strip("`")
    if "**" in texte or re.search(r"^#", texte, re.M):
        texte = _normaliser(texte)
    titre = re.search(r"^\s*TITRE\s*:\s*(.+)$", texte, re.M)
    legende = re.search(r"^\s*L[EÉ]GENDE\s*:\s*(.+)$", texte, re.M)
    script = texte.split("---", 1)[1] if "---" in texte else texte
    # Le titre et la legende ne sont pas des repliques, meme quand le
    # separateur manque ; les lignes « --- » non plus.
    script = "\n".join(
        l for l in script.splitlines()
        if not re.match(r"^\s*(?:TITRE|L[EÉ]GENDE)\s*:|^\s*-{3,}\s*$", l, re.I))
    # « CARTMAN: (surpris) Ouah » : le jeu entre parentheses serait lu a voix
    # haute. Seules les didascalies seules sur leur ligne restent.
    script = "\n".join(
        re.sub(r"\s*\([^)]*\)\s*", " ", l).rstrip()
        if re.match(r"^\s*[A-Za-zÀ-ÿ' \-]{2,24}\s*:", l) else l
        for l in script.splitlines())
    # Un modele faible recopie parfois l exemple de forme tel quel.
    script = "\n".join(l for l in script.splitlines()
                       if not re.match(r"^\s*(?:NOM|PERSONNAGE)\s*:", l, re.I))
    # Trop long, le film deborde la minute et le calcul s eternise : on
    # coupe apres la seizieme replique, a la fin d une ligne.
    lignes, dites = [], 0
    for l in script.splitlines():
        if re.match(r"^\s*[A-Za-zÀ-ÿ' \-]{2,24}\s*:", l) and \
                not re.match(r"^\s*D[EÉ]COR\s*:", l, re.I):
            if dites >= 16:
                break
            dites += 1
        lignes.append(l)
    script = "\n".join(lignes)
    # Un personnage hors liste (une personne reelle, le plus souvent) perd ses
    # repliques : elle n apparait jamais et ne parle jamais, comme convenu.
    from tools.dessin_anime import _nom_canonique
    gardees = []
    for l in script.splitlines():
        m = re.match(r"^\s*([A-Za-zÀ-ÿ0-9 '\-]{2,24}?)\s*[:>]", l)
        if m and not re.match(r"^\s*D[EÉ]COR\b", l, re.I) and \
                _plat(_nom_canonique(m.group(1))) not in permis:
            continue
        gardees.append(l)
    script = "\n".join(gardees)
    plans = _analyser(script)
    repliques = [q for p in plans for q, _ in p["repliques"]]
    if len(repliques) < 6:
        return None, "trop peu de repliques (%d)" % len(repliques)
    return ((titre.group(1).strip() if titre else "South Park du jour"),
            (legende.group(1).strip() if legende else ""),
            script.strip()), ""


def _ecrire(sujets, avec=""):
    noms, avec_voix = _distribution()
    from tools.dessin_anime import _plat
    impose = [_plat(n) for n in (avec or "").split(",") if _plat(n) in
              {_plat(x) for x in noms}]
    if impose:
        # Une distribution demandee : eux, et personne d autre.
        noms = [n for n in noms if _plat(n) in impose]
        avec_voix = [n for n in avec_voix if _plat(n) in impose]
    permis = {_plat(n) for n in noms}
    une = "\n".join("- %s. %s" % (t, c) for t, c in sujets)
    prompt = (("Distribution imposee : seulement %s, qui parlent tous.\n"
               % ", ".join(n.upper() for n in impose) if impose else "") +
              "Personnages qui ont leur vraie voix (a privilegier) : %s.\n"
              "Autres personnages possibles : %s.\n"
              "Caricatures de personnes reelles (a utiliser seulement si le "
              "sujet les concerne) : %s.\n\nLa une du jour :\n%s\n\n"
              "Rends uniquement le format demande : TITRE:, LEGENDE:, ---, "
              "puis des lignes DECOR: et NOM: replique. Pas de markdown, "
              "pas de gras, pas de titres de scene, 10 a 14 repliques."
              % (", ".join(n.upper() for n in avec_voix),
                 ", ".join(n.upper() for n in noms
                           if n not in avec_voix and n not in PARODIES),
                 ", ".join("%s (%s)" % (n.upper(), qui)
                           for n, qui in PARODIES.items() if n in noms),
                 une))
    raison = ""
    for _ in range(2):
        brut = _demander(prompt + ("\n\nVersion precedente refusee : %s. "
                                   "Recommence en respectant les regles."
                                   % raison if raison else ""))
        resultat, raison = _lire_reponse(brut, permis)
        if resultat:
            titre, legende, script = resultat
            return titre, legende, _accentuer(script)
        LOG.warning("south park du jour: script refuse (%s)", raison)
    raise RuntimeError("le scenariste n a pas rendu de script valable (%s)"
                       % raison)


# ------------------------------------------------------------ le format vertical

def _vertical(film, titre):
    """Le film en 1080x1920 : fond flou, image au centre, sous-titres incrustes
    et la mention de parodie en tete, toute la duree."""
    from tools.dessin_anime import _ffmpeg, _duree
    exe = _ffmpeg()
    travail = Path(film).parent / (".vertical-%s" % uuid.uuid4().hex[:6])
    travail.mkdir()
    try:
        srt = Path(film).with_suffix(".srt")
        if srt.exists():
            shutil.copy(srt, travail / "dits.srt")
        else:
            (travail / "dits.srt").write_text("", encoding="utf-8")
        duree = _duree(film) or 60
        h, reste = divmod(duree, 3600)
        m, s = divmod(reste, 60)
        fin = "%d:%02d:%05.2f" % (h, m, s)
        propre = re.sub(r"[{}\\]", "", titre)
        # En ASS et non en SRT : seul ce format place le texte au pixel pres
        # sur une image de 1080 x 1920.
        (travail / "bandeau.ass").write_text(
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
            "SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
            "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Titre,Arial,66,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
            "-1,0,0,0,100,100,0,0,1,5,0,8,50,50,170,1\n"
            "Style: Mention,Arial,38,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
            "-1,0,0,0,100,100,0,0,1,3,0,2,50,50,70,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:00.00,%s,Titre,,0,0,0,,SOUTH PARK DU JOUR\\N%s\n"
            "Dialogue: 0,0:00:00.00,%s,Mention,,0,0,0,,Parodie - personnages "
            "et voix generes par IA\n" % (fin, propre, fin),
            encoding="utf-8")
        shutil.copy(film, travail / "film.mp4")
        filtre = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=20:2[fond];"
            "[0:v]scale=1080:-2[image];"
            "[fond][image]overlay=0:(H-h)/2,"
            "subtitles=dits.srt:force_style='FontName=Arial,FontSize=11,"
            "Bold=1,Outline=2,Shadow=0,Alignment=2,MarginV=45',"
            "ass=bandeau.ass[v]")
        cible = Path(film).with_name(Path(film).stem + "-vertical.mp4")
        r = subprocess.run(
            [exe, "-y", "-i", "film.mp4", "-filter_complex", filtre,
             "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-crf", "24",
             "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-b:a", "128k", "-movflags", "+faststart", "vertical.mp4"],
            cwd=str(travail), capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        if r.returncode != 0 or not (travail / "vertical.mp4").exists():
            raise RuntimeError("ffmpeg vertical : %s" % r.stderr[-400:])
        shutil.move(str(travail / "vertical.mp4"), str(cible))
        return cible
    finally:
        shutil.rmtree(travail, ignore_errors=True)


# ------------------------------------------------------------ l apercu

def _envoyer_apercu(etat):
    """L apercu par mail, avec la marche a suivre pour publier."""
    import smtplib
    from email.message import EmailMessage
    from tools.mail import MAIL_ADRESSE, _mail_configure, _mail_mdp
    if not _mail_configure():
        return False
    msg = EmailMessage()
    msg["From"] = MAIL_ADRESSE
    msg["To"] = MAIL_ADRESSE
    msg["Subject"] = "South Park du jour : %s" % etat["titre"]
    # La legende seule sur ses lignes : un appui long la copie entiere.
    corps = ("%s\n\nParodie. Personnages et voix générés par IA.\n\n"
             "--------------------------------\n"
             "Publier depuis le téléphone (30 s) :\n"
             "1. Appui long sur la vidéo jointe > Enregistrer.\n"
             "2. Instagram : + > Reel > choisir la vidéo. Coller la légende "
             "ci-dessus.\n"
             "   Paramètres avancés > activer « Libellé IA » (contenu généré "
             "par IA).\n"
             "3. TikTok : + > Importer > la vidéo, même légende, activer "
             "« Contenu généré par IA ».\n\n"
             "Pour l abandonner : « Mother, jette le South Park du jour »."
             % (etat.get("legende") or etat["titre"]))
    video = Path(etat["vertical"])
    if video.exists() and video.stat().st_size < 24 * 1024 * 1024:
        msg.set_content(corps)
        msg.add_attachment(video.read_bytes(), maintype="video",
                           subtype="mp4", filename="south-park-du-jour.mp4")
    else:
        msg.set_content(corps + "\n\nLa video est trop lourde pour un mail : "
                        "elle est dans %s." % video)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(MAIL_ADRESSE, _mail_mdp())
        s.send_message(msg)
    return True


# ------------------------------------------------------------ la fabrication

def fabriquer(avec=""):
    """Le film du jour, de la une au mail d apercu. Rend une phrase.

    avec : personnages imposes, separes par des virgules (« serviette,gerald »).
    """
    if not _VERROU.acquire(blocking=False):
        return "Le South Park du jour est deja en fabrication."
    try:
        aujourd = dt.date.today().isoformat()
        _sauver({"date": aujourd, "statut": "fabrication"})
        sujets = _une()
        if not sujets:
            _sauver({"date": aujourd, "statut": "echec",
                     "raison": "une vide ou que des sujets graves"})
            return "La une d aujourd hui ne se prete pas a la parodie."
        titre, legende, script = _ecrire(sujets, avec)
        (_RACINE / "logs").mkdir(exist_ok=True)
        (_RACINE / "logs" / "south_park_du_jour.txt").write_text(
            script, encoding="utf-8")

        from tools import dessin_anime as D
        rendu = D.dessin_anime(script=script, titre="south park " + titre,
                               papier=True, ouvrir=False,
                               sans_clone=",".join(PARODIES))
        film = D._DERNIER_DESSIN.get("chemin")
        if not film or not Path(film).exists() or \
                time.time() - Path(film).stat().st_mtime > 3600:
            raise RuntimeError("le film n a pas abouti : %s" % rendu)
        vertical = _vertical(film, titre)
        etat = {"date": aujourd, "statut": "en_attente", "titre": titre,
                "legende": legende, "sujet": sujets[0][0],
                "film": str(film), "vertical": str(vertical)}
        _sauver(etat)
        try:
            _envoyer_apercu(etat)
        except Exception:
            LOG.exception("south park du jour: mail d apercu")
        LOG.info("south park du jour pret : %s", vertical)
        return ("Le South Park du jour est pret : %s. Je te l ai envoye par "
                "mail. Dis publie pour le mettre en ligne." % titre)
    except Exception as e:
        LOG.exception("south park du jour")
        _sauver({"date": dt.date.today().isoformat(), "statut": "echec",
                 "raison": str(e)[:300]})
        return "Le South Park du jour a echoue : %s" % str(e)[:150]
    finally:
        _VERROU.release()


def demarrer_south_park_du_jour():
    """Au demarrage : une fois par jour, des l heure venue (ou des que le PC
    s allume, s il etait eteint a cette heure-la)."""
    if not reglage("south_park_du_jour.actif", False):
        return

    def boucle():
        time.sleep(180)   # laisser Jarvis finir de demarrer
        while True:
            try:
                maintenant = dt.datetime.now()
                if (maintenant.hour >= int(reglage("south_park_du_jour.heure", 7))
                        and _etat().get("date") != maintenant.date().isoformat()):
                    fabriquer()
            except Exception:
                LOG.exception("south park du jour: boucle")
            time.sleep(600)
    threading.Thread(target=boucle, daemon=True,
                     name="south-park-du-jour").start()


# ------------------------------------------------------------ la publication

def _publier_instagram(etat):
    """Un Reel par l API officielle, envoi direct du fichier (resumable)."""
    import requests
    from tools.instagram import _comptes
    voulu = (reglage("south_park_du_jour.instagram", "") or "").strip().lower()
    if not voulu:
        raise RuntimeError("aucun compte Instagram dedie n est regle "
                           "(south_park_du_jour.instagram)")
    compte = next((c for c in _comptes() if c["nom"].lower() == voulu), None)
    if not compte:
        raise RuntimeError("le compte %s n est pas dans instagram.comptes" % voulu)
    token, uid = compte["token"], compte["user_id"]
    hote = ("https://graph.instagram.com" if token.startswith("IG")
            else "https://graph.facebook.com")
    version = reglage("instagram.version_api", "v21.0")
    legende = "%s\n\nParodie. Personnages et voix generes par IA." % (
        etat.get("legende") or etat["titre"])
    r = requests.post("%s/%s/%s/media" % (hote, version, uid), timeout=60, data={
        "media_type": "REELS", "upload_type": "resumable",
        "caption": legende, "access_token": token})
    r.raise_for_status()
    conteneur = r.json()
    video = Path(etat["vertical"]).read_bytes()
    r = requests.post(conteneur["uri"], timeout=600, data=video, headers={
        "Authorization": "OAuth " + token, "offset": "0",
        "file_size": str(len(video))})
    r.raise_for_status()
    for _ in range(60):
        time.sleep(10)
        s = requests.get("%s/%s/%s" % (hote, version, conteneur["id"]),
                         timeout=30, params={"fields": "status_code",
                                             "access_token": token}).json()
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") == "ERROR":
            raise RuntimeError("Instagram a refuse la video")
    else:
        raise RuntimeError("Instagram n a pas fini de traiter la video")
    r = requests.post("%s/%s/%s/media_publish" % (hote, version, uid),
                      timeout=60, data={"creation_id": conteneur["id"],
                                        "access_token": token})
    r.raise_for_status()
    return r.json().get("id")


def _annonce_publication(args):
    etat = _etat()
    plateforme = (args or {}).get("plateforme") or "instagram"
    if "tik" in plateforme.lower() or \
            not reglage("south_park_du_jour.instagram", ""):
        return ("Je te renvoie « %s » par mail pour que tu le publies ?"
                % etat.get("titre", "le South Park du jour"))
    return ("Je publie « %s » sur Instagram ?"
            % etat.get("titre", "le South Park du jour"))


@outil(
    nom="south_park_du_jour",
    description=(
        "Fabrique tout de suite l episode South Park du jour, tire de la une "
        "du Monde, et l envoie en apercu par mail. Pour 'fais le South Park "
        "du jour', 'refais l episode du jour', 'un dessin anime avec Kyle et "
        "Randy sur l actualite'."
    ),
    parametres={"type": "object", "properties": {
        "avec": {"type": "string",
                 "description": "Personnages imposes, separes par des virgules."}}},
    lent=True,
    phrase_attente="Je lis la une et je lance l episode, ca prendra un moment.",
)
def south_park_du_jour(avec: str = "") -> str:
    threading.Thread(target=fabriquer, kwargs={"avec": avec},
                     daemon=True).start()
    return ("C est lance. Je t envoie l apercu par mail des que le film est "
            "pret, en general un quart d heure.")


@outil(
    nom="publier_south_park",
    description=(
        "Met en ligne l episode South Park du jour deja fabrique (apercu "
        "valide). Pour 'publie le South Park', 'poste la video du jour'."
    ),
    parametres={"type": "object", "properties": {
        "plateforme": {"type": "string",
                       "description": "instagram (par defaut) ou tiktok."}}},
    confirmation=True,
    annonce=_annonce_publication,
    lent=True,
    phrase_attente="Je publie la video.",
)
def publier_south_park(plateforme: str = "instagram") -> str:
    etat = _etat()
    if etat.get("statut") != "en_attente" or not etat.get("vertical"):
        return "Il n y a pas d episode en attente de publication."
    plateforme = (plateforme or "instagram").lower()
    # Sans compte branche (ou pour TikTok), on publie a la main depuis le
    # telephone : on y renvoie la video et la legende prete a coller.
    if "tik" in plateforme or not reglage("south_park_du_jour.instagram", ""):
        try:
            _envoyer_apercu(etat)
        except Exception:
            LOG.exception("south park du jour: renvoi du mail")
            return "Je n ai pas reussi a te renvoyer la video par mail."
        return ("Je te renvoie la video et sa legende par mail : publie-la "
                "depuis ton telephone, c est l affaire de trente secondes.")
    try:
        ident = _publier_instagram(etat)
    except Exception as e:
        LOG.exception("south park du jour: publication instagram")
        return "La publication a echoue : %s" % str(e)[:150]
    etat.update(statut="publie", instagram=ident,
                publie_le=dt.datetime.now().isoformat(timespec="minutes"))
    _sauver(etat)
    return "C est en ligne sur Instagram."


@outil(
    nom="jeter_south_park",
    description=(
        "Abandonne l episode South Park du jour sans le publier. Pour 'jette "
        "le South Park', 'ne publie pas la video du jour'."
    ),
)
def jeter_south_park() -> str:
    etat = _etat()
    if etat.get("statut") != "en_attente":
        return "Il n y a pas d episode en attente."
    etat["statut"] = "jete"
    _sauver(etat)
    return "D accord, l episode du jour ne sera pas publie."
