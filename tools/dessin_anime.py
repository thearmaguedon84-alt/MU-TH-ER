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
            courant = {"decor": m.group(1).strip(), "repliques": []}
            plans.append(courant)
            continue
        if _DIDASCALIE.match(ligne):
            continue
        m = _REPLIQUE.match(ligne)
        if not m:
            continue
        if courant is None:
            courant = {"decor": "", "repliques": []}
            plans.append(courant)
        courant["repliques"].append((m.group(1).strip().upper(),
                                     m.group(2).strip()))
    return [p for p in plans if p["repliques"] or p["decor"]]


def _voix_pour(nom):
    """La meme voix pour le meme personnage, d un rendu a l autre.

    Un tirage au hasard donnerait des voix differentes a chaque essai, et l on
    ne reconnaitrait plus personne. On derive donc la voix du nom lui-meme.
    """
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


def _image_plan(decor, personnages, soignee=True, papier=False):
    """Le decor d un plan, dans le style du film."""
    demande = STYLE_PAPIER + ", empty scenery, no characters, no people" \
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
STYLE_PAPIER = ("construction paper cutout animation, crude simple flat "
                "shapes, thick black outlines, flat solid colours, no "
                "shading, no gradient, no texture, no text")

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
        return decoupage, json.loads(fiche.read_text(encoding="utf-8"))

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
           "sheila", "mackey", "jimbo", "stephen", "linda", "maire"}
GRANDEUR_ADULTE = 1.5


def _plat(nom):
    """Le nom reduit a ce qui compte pour le reconnaitre."""
    return re.sub(r"[^a-z0-9]+", "-", (nom or "").lower()).strip("-")


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


def _plan_papier(exe, decor, distribution, repliques, sons, cible):
    """Un plan joue par des marionnettes : elles se balancent et parlent.

    On fabrique les images une par une plutot que d empiler des filtres :
    ouvrir une bouche au bon moment n est pas exprimable en filtre, et le
    calcul reste negligeable a cote de tout le reste.
    """
    import numpy as np
    from PIL import Image, ImageDraw

    L, H, FPS = 1280, 720, 25
    silence = 0.35
    debut_par_replique, horloge = [], 0.25
    for s in sons:
        debut_par_replique.append((horloge, horloge + _duree(s)))
        horloge += _duree(s) + silence
    duree = max(horloge + 0.4, 2.0)

    # Le relevé de chaque réplique, une fois pour toutes.
    enveloppes = [_enveloppe(exe, s, FPS) for s in sons]

    fond = Image.open(decor).convert("RGB").resize((L, H), Image.LANCZOS)

    # Les personnages, poses au sol, repartis sur la largeur.
    poses = []
    noms = list(distribution.keys())
    ouverts = [Image.open(distribution[n][0]).convert("RGBA") for n in noms]

    # Une hauteur commune, mais reduite tant que la bande deborde : a cinq
    # personnages, la taille d un seul les faisait se chevaucher et sortir du
    # cadre. On les veut alignes et entiers, pas empiles.
    grands = [GRANDEUR_ADULTE if _plat(n) in ADULTES else 1.0 for n in noms]
    haut = int(H * 0.55)
    while haut > H * 0.18:
        large = sum(max(1, int(o.width * haut * g / o.height))
                    for o, g in zip(ouverts, grands))
        plus_haut = haut * max(grands)
        if large <= L * 0.88 and plus_haut <= H * 0.80:
            break
        haut -= 6

    grandeurs = [GRANDEUR_ADULTE if _plat(n) in ADULTES else 1.0 for n in noms]
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
        penchees = {a: (img if a == 0 else
                        img.rotate(a, resample=Image.BICUBIC, expand=False))
                    for a in ANGLES}
        # Un dessin coupe a la taille se pose sur le bord bas de l image :
        # la coupe se confond alors avec le cadre, au lieu de flotter au-dessus
        # du sol. On le reconnait a ce que son encre touche son propre bord.
        alpha = np.asarray(img.split()[-1])
        coupe = bool((alpha[-2:, :] > 128).mean() > 0.06)
        pied = H - img.height + (int(H * 0.06) if coupe else -int(H * 0.08))
        poses.append((nom, img, int(curseur), pied, distribution[nom][1],
                      penchees))
        curseur += img.width + ecart

    travail = cible.parent / (cible.stem + "-images")
    travail.mkdir(parents=True, exist_ok=True)
    total = int(duree * FPS)
    for n in range(total):
        t = n / FPS
        vue = fond.copy()
        for j, (nom, img, x, y, repere, penchees) in enumerate(poses):
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

            pris = min(ANGLES, key=lambda a: abs(a - angle))
            piece = penchees[pris]
            dy = int(round(saut))
            vue.paste(piece, (x, y - dy), piece)

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
                bx = x + cx + rx
                by = y - dy + cy + ry
                bl = max(5, repere["l"] * img.width)
                large, haute = _forme_bouche(niveau)
                dx, dy = bl * large / 2, max(1.0, bl * haute / 2)
                ImageDraw.Draw(vue).ellipse(
                    [bx - dx, by - dy, bx + dx, by + dy], fill=(20, 20, 20))
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
                 anime: bool = False, ecran: str = "") -> str:
    exe = _ffmpeg()
    if not exe:
        return "ffmpeg est introuvable, je ne peux pas monter le film."

    # Un script s ecrit, il ne se dicte pas : on accepte donc aussi le nom du
    # fichier qui le contient. C est ainsi qu on l emploiera vraiment.
    script = _lire_script(script)

    plans = _analyser(script)
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
                       for j, (qui, texte) in enumerate(plan["repliques"])]
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
                for qui, _ in plan["repliques"]:
                    if qui in distribution:
                        continue
                    decoupage, repere = _cutout(qui)
                    if decoupage:
                        distribution[qui] = (decoupage, repere)
                manquants = [q for q, _ in plan["repliques"]
                             if q not in distribution]
                if manquants:
                    return ("Je n ai pas de dessin pour %s. Depose-les dans "
                            "%s, ou donne-moi une planche a decouper."
                            % (", ".join(sorted(set(manquants))), PERSONNAGES))
                if not distribution:
                    continue
                if _plan_papier(exe, image, distribution, plan["repliques"],
                                sons, morceau) is None:
                    continue
            else:
                image = _image_plan(plan["decor"], personnages)
                if image is None:
                    continue
                if _plan_en_video(exe, image, sons, morceau) is None:
                    continue
            faits.append(morceau)

            # Les sous-titres suivent l horloge du film, pas celle du plan.
            debut = horloge + 0.25
            for j, (qui, texte) in enumerate(plan["repliques"]):
                if j < len(sons):
                    d = _duree(sons[j])
                    dits.append((debut, debut + d, qui, texte))
                    debut += d + 0.35
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

    if ecran:
        from tools.video import envoyer_video_ecran
        return "Voila le film. %s" % envoyer_video_ecran(ecran=ecran)
    try:
        import os
        os.startfile(str(cible))
    except Exception:
        pass
    return ("Dessin anime de %d plans et %d repliques, %.0f secondes. "
            "Les sous-titres sont a cote, en .srt."
            % (len(faits), len(dits), _duree(cible)))
