"""Un dessin anime a partir d un script.

Ce qui fait un dessin anime, ce n est pas l animation mais le dialogue : une
image fixe avec deux voix qui se repondent se regarde, vingt secondes de
mouvement muet, non. L outil est donc construit autour du texte.

Deux modes, parce que la video coute cher : des planches animees d un lent
mouvement de camera (une minute de film en trois minutes de calcul), ou une
vraie animation plan par plan (sept minutes de calcul pour cinq secondes).
"""
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


def _bouche(decoupage):
    """Ou se trouve la bouche, en fraction de la hauteur du decoupage.

    Le visage est la plus grande tache claire du haut ; la bouche se tient aux
    trois quarts de sa hauteur, au milieu. Mesure sur le dessin plutot que
    supposition : d un personnage a l autre, la tete n est pas a la meme
    hauteur.
    """
    try:
        import numpy as np
        from PIL import Image
        from scipy import ndimage

        a = np.asarray(Image.open(decoupage).convert("RGBA"))
        alpha, rgb = a[..., 3], a[..., :3].astype(np.int16)
        H, L = alpha.shape
        clair = ((alpha > 0) & (rgb.min(axis=2) > 165)
                 & (abs(rgb[..., 0] - rgb[..., 2]) < 80))
        clair[int(H * 0.55):, :] = False
        etiquettes, combien = ndimage.label(clair)
        if not combien:
            return None
        tailles = ndimage.sum(clair, etiquettes, range(1, combien + 1))
        ys, xs = np.where(etiquettes == int(np.argmax(tailles)) + 1)
        hauteur = ys.max() - ys.min()
        if hauteur < H * 0.08:
            return None
        return {"x": float((xs.min() + xs.max()) / 2 / L),
                "y": float((ys.min() + hauteur * 0.74) / H),
                "l": float((xs.max() - xs.min()) * 0.30 / L)}
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

    demande = "%s, a character named %s%s, standing, full body, facing the " \
              "viewer, isolated on a %s" % (
                  STYLE_PAPIER, nom.title(),
                  (", " + description) if description else "", FOND_UNI)
    from tools.flux import image_soignee
    image_soignee.__wrapped__(description=demande, format="carre")
    from tools.image import _DERNIERE
    brut = _DERNIERE.get("chemin")
    if not brut or not Path(brut).exists():
        return None, None
    if _detourer(brut, decoupage) is None:
        return None, None
    repere = _bouche(decoupage) or {"x": 0.5, "y": 0.28, "l": 0.12}
    fiche.write_text(json.dumps(repere), encoding="utf-8")
    return decoupage, repere


def _plan_papier(exe, decor, distribution, repliques, sons, cible):
    """Un plan joue par des marionnettes : elles se balancent et parlent.

    On fabrique les images une par une plutot que d empiler des filtres :
    ouvrir une bouche au bon moment n est pas exprimable en filtre, et le
    calcul reste negligeable a cote de tout le reste.
    """
    from PIL import Image, ImageDraw

    L, H, FPS = 1280, 720, 25
    silence = 0.35
    debut_par_replique, horloge = [], 0.25
    for s in sons:
        debut_par_replique.append((horloge, horloge + _duree(s)))
        horloge += _duree(s) + silence
    duree = max(horloge + 0.4, 2.0)

    fond = Image.open(decor).convert("RGB").resize((L, H), Image.LANCZOS)

    # Les personnages, poses au sol, repartis sur la largeur.
    poses = []
    noms = list(distribution.keys())
    for i, nom in enumerate(noms):
        decoupage, repere = distribution[nom]
        img = Image.open(decoupage).convert("RGBA")
        haut = int(H * 0.58)
        img = img.resize((max(1, int(img.width * haut / img.height)), haut),
                         Image.LANCZOS)
        x = int(L * (i + 1) / (len(noms) + 1) - img.width / 2)
        poses.append((nom, img, x, H - haut - int(H * 0.06), repere))

    travail = cible.parent / (cible.stem + "-images")
    travail.mkdir(parents=True, exist_ok=True)
    total = int(duree * FPS)
    for n in range(total):
        t = n / FPS
        vue = fond.copy()
        for j, (nom, img, x, y, repere) in enumerate(poses):
            parle = any(nom == repliques[k][0] and a <= t <= b
                        for k, (a, b) in enumerate(debut_par_replique))
            # Le balancement : deux pixels. Plus, il saute ; moins, on ne voit
            # rien.
            saut = int(2 * abs((t * 6 + j) % 2 - 1)) if parle else 0
            vue.paste(img, (x, y - saut), img)
            # La bouche s ouvre et se ferme cinq fois par seconde : c est le
            # rythme du procede, et il suffit a faire parler un dessin.
            if parle and repere and int(t * 10) % 2 == 0:
                bx = x + repere["x"] * img.width
                by = y - saut + repere["y"] * img.height
                bl = max(4, repere["l"] * img.width)
                ImageDraw.Draw(vue).ellipse(
                    [bx - bl / 2, by - bl * 0.42, bx + bl / 2, by + bl * 0.42],
                    fill=(20, 20, 20))
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
            sons = []
            for j, (qui, texte) in enumerate(plan["repliques"]):
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
