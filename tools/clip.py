"""Monter un clip : des images ou des videos, sur un morceau.

Le probleme de fond est une affaire de duree. Un modele video produit cinq
secondes ; un morceau en dure quarante-cinq. Il faut donc combler, et la
maniere de combler decide de tout.

Boucler betement se voit : a chaque reprise, l'image saute. On monte donc les
sequences en aller-retour — jouees, puis rejouees a l'envers — ce qui donne un
raccord invisible et double la matiere. Pour des photos, un lent mouvement de
zoom donne du mouvement sans rien inventer.

Les sequences s'enchainent en fondu enchaine plutot que bout a bout : sur une
musique continue, une coupe franche s'entend presque autant qu'elle se voit.
"""
import re
import subprocess
import time
from pathlib import Path

from core.dossiers import dossier
from core.file_gpu import enfile
from core.registre import outil

RACINE = Path(__file__).resolve().parent.parent
DOSSIER = dossier("clips")

# Combien de temps dure un fondu enchaine entre deux sequences.
FONDU = 1.0

# Ce qui trahit un fichier d'essai plutot qu'une creation. On les ecarte quand
# l'utilisateur ne demande rien de precis.
_REBUTS = re.compile(r"(avant|apres|test|essai|temoin|ref|reference|"
                     r"identite|ressemblance|humain-)", re.I)


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        return shutil.which("ffmpeg")


def _duree(fichier):
    """Duree en secondes, lue dans ce que ffmpeg raconte sur le fichier."""
    exe = _ffmpeg()
    if not exe:
        return 0.0
    r = subprocess.run([exe, "-i", str(fichier)], capture_output=True,
                       text=True, errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", r.stderr or "")
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)



def _porte(nom, mot):
    """Ce nom de fichier parle-t-il de ce sujet ?

    On compare sur un radical plutot que sur le mot entier : il dit
    « xenomorphe », le fichier porte « xenomorph ». Le « e » final suffisait a
    tout faire echouer, et le meme piege attend « guitare » contre « guitar »
    ou n importe quel pluriel.
    """
    nom, mot = nom.lower(), mot.lower()
    if mot in nom:
        return True
    # Les trois dernieres lettres sont celles qui varient d une langue et d un
    # nombre a l autre ; en deca de cinq lettres on ne coupe plus, sous peine
    # de rapprocher des mots sans rapport.
    radical = mot[:max(5, len(mot) - 3)]
    return len(radical) >= 5 and radical in nom


def _choisir(dossier_source, motifs, combien, theme=""):
    """Les fichiers a monter, du plus recent au plus ancien.

    Un theme filtre sur le nom : les fichiers portent leur demande d'origine,
    donc « xenomorphe » suffit a retrouver la serie. Sans theme, on ecarte les
    fichiers d'essai — sinon le clip se remplit de temoins de laboratoire.
    """
    if not dossier_source.is_dir():
        return []
    lot = []
    for motif in motifs:
        lot += list(dossier_source.glob(motif))
    lot.sort(key=lambda p: -p.stat().st_mtime)

    mots = [m for m in re.split(r"[^a-z0-9]+", (theme or "").lower())
            if len(m) >= 4]
    if mots:
        vises = [p for p in lot
                 if all(_porte(p.stem, m) for m in mots)]
        if vises:
            return vises[:combien]
        vises = [p for p in lot if any(_porte(p.stem, m) for m in mots)]
        if vises:
            return vises[:combien]
        # Un sujet demande et introuvable : on ne remplace pas en silence par
        # autre chose. C est ce qui donnait un chaton dans un clip de
        # xenomorphes, sans un mot d explication.
        return []
    propres = [p for p in lot if not _REBUTS.search(p.stem)]
    return (propres or lot)[:combien]


def _trouver_musique(nom):
    from tools import musique as M
    if nom and Path(nom).is_file():
        return Path(nom)
    if not nom or re.search(r"derni|celle|la musique|le morceau", nom.lower()):
        d = M._DERNIERE.get("chemin")
        if d and Path(d).exists():
            return Path(d)
    if nom and len(nom) >= 3 and M.DOSSIER.is_dir():
        cle = nom.lower().strip()
        for p in M.DOSSIER.glob("*"):
            if cle in p.stem.lower():
                return p
    lot = _choisir(M.DOSSIER, ["*.mp3", "*.wav"], 1)
    return lot[0] if lot else None


@outil(
    nom="monter_clip",
    description=(
        "Monte un clip video : assemble des videos ou des images sur un "
        "morceau de musique, cale sur sa duree, avec fondus enchaines. Pour "
        "'fais-moi un clip avec la musique', 'monte une video sur le morceau'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "musique": {"type": "string",
                        "description": "Fichier ou 'la derniere'. Vide = le dernier morceau."},
            "sources": {"type": "string",
                        "description": "'videos', 'images' ou 'photos'. Ce qu il faut monter."},
            "theme": {"type": "string",
                      "description": "Mot present dans le nom des fichiers a retenir, par exemple 'xenomorphe'."},
            "combien": {"type": "integer",
                        "description": "Nombre de sequences a enchainer. 4 par defaut."},
            "ecran": {"type": "string", "description": "Nom d un ecran."},
        },
        "required": [],
    },
    lent=True,
    phrase_attente="Je monte le clip.",
)
@enfile("clip", "musique")
def monter_clip(musique: str = "", sources: str = "", theme: str = "",
                combien: int = 4, ecran: str = "") -> str:
    exe = _ffmpeg()
    if not exe:
        return "ffmpeg est introuvable, je ne peux pas monter."

    piste = _trouver_musique(musique)
    if piste is None:
        return "Je n ai pas de musique a mettre dessous."
    duree = _duree(piste)
    if duree < 1:
        return "Je n arrive pas a lire la duree du morceau."

    combien = max(1, min(int(combien or 4), 12))
    veut_images = bool(re.search(r"image|photo", (sources or "").lower()))
    veut_videos = bool(re.search(r"video|sequence|film", (sources or "").lower()))

    from tools.image import DOSSIER as IMAGES
    if not veut_images:
        videos = _choisir(dossier("videos"), ["*.mp4", "*.webm"], combien,
                          theme)
        if videos:
            return _montage_videos(exe, videos, piste, duree, ecran)
        if veut_videos:
            return (f"Je n ai pas de video qui parle de « {theme} »."
                    if theme else "Je n ai pas de video a monter.")

    photos = _choisir(IMAGES, ["*.png", "*.jpg"], combien, theme)
    if not photos:
        if theme:
            return (f"Je ne trouve rien qui parle de « {theme} ». Les fichiers "
                    f"portent le nom de la demande qui les a produits : "
                    f"essaie un autre mot, ou demande un clip sans preciser "
                    f"de sujet.")
        return "Je n ai ni video ni image a monter."
    return _montage_photos(exe, photos, piste, duree, ecran)


def _sortie(nom):
    DOSSIER.mkdir(parents=True, exist_ok=True)
    propre = re.sub(r"[^a-z0-9]+", "-", nom.lower())[:40].strip("-")
    return DOSSIER / f"{time.strftime('%Y%m%d-%H%M%S')}-{propre}.mp4"


def _lancer(exe, args, cible):
    r = subprocess.run([exe, "-y"] + args + [str(cible)], capture_output=True,
                       text=True, errors="replace", timeout=3600)
    if r.returncode or not cible.exists():
        lignes = [l for l in (r.stderr or "").split(chr(10)) if l.strip()]
        return lignes[-1][:160] if lignes else "ffmpeg a echoue"
    return None


def _enchainer(etiquettes, part, sortie):
    """Enchaine les sequences en fondu, et rend les lignes de filtre.

    Chaque fondu mange FONDU secondes de recouvrement : la sequence suivante
    commence avant que la precedente ne finisse. Les decalages se cumulent
    donc en retirant ce recouvrement a chaque fois.
    """
    lignes = []
    courant = etiquettes[0]
    for i in range(1, len(etiquettes)):
        decalage = i * (part - FONDU)
        prochain = f"[x{i}]" if i < len(etiquettes) - 1 else sortie
        lignes.append(f"{courant}{etiquettes[i]}xfade=transition=fade:"
                      f"duration={FONDU}:offset={decalage:.2f}{prochain}")
        courant = prochain
    if len(etiquettes) == 1:
        lignes.append(f"{courant}null{sortie}")
    return lignes


def _son(indice, duree):
    """La piste, fondue aux deux bouts. Sans cela, ca claque."""
    return (f"[{indice}:a]afade=t=in:st=0:d=1.5,"
            f"afade=t=out:st={max(0, duree - 3):.2f}:d=3,"
            f"atrim=duration={duree:.2f}[af]")


_FIN = ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"]


def _montage_photos(exe, photos, piste, duree, ecran):
    """Un lent zoom sur chaque photo, enchainees en fondu."""
    n = len(photos)
    # Avec des fondus qui se recouvrent, la duree utile de chaque photo est
    # plus longue que la simple division : chaque raccord en reprend un bout.
    part = max(3.0, (duree + (n - 1) * FONDU) / n)
    fps = 25
    images = int(part * fps)

    entrees, filtres = [], []
    for i, p in enumerate(photos):
        entrees += ["-loop", "1", "-framerate", str(fps),
                    "-t", f"{part:.2f}", "-i", str(p)]
        # On agrandit avant de zoomer : zoompan travaille en nombres entiers,
        # et sur une image a la taille finale le mouvement saccade.
        # d=1 rend une image pour une image ; le zoom s accumule d une image
        # a la suivante. Avec d superieur a 1 la duree serait multipliee.
        filtres.append(
            f"[{i}:v]scale=2560:1440:force_original_aspect_ratio=increase,"
            f"crop=2560:1440,"
            f"zoompan=z='min(zoom+0.00035,1.14)':d=1:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1280x720:fps={fps},"
            f"trim=duration={part:.2f},setpts=PTS-STARTPTS,fps={fps},"
            f"format=yuv420p,setsar=1[v{i}]")

    filtres += _enchainer([f"[v{i}]" for i in range(n)], part, "[vx]")
    filtres.append(f"[vx]trim=duration={duree:.2f},setpts=PTS-STARTPTS,"
                   f"fade=t=in:st=0:d=1,"
                   f"fade=t=out:st={max(0, duree - 1.5):.2f}:d=1.5[vf]")
    filtres.append(_son(n, duree))

    args = entrees + ["-i", str(piste),
                      "-filter_complex", ";".join(filtres),
                      "-map", "[vf]", "-map", "[af]"] + _FIN + \
        ["-t", f"{duree:.2f}"]
    cible = _sortie("clip-photos")
    souci = _lancer(exe, args, cible)
    if souci:
        return f"Le montage a echoue : {souci}"
    return _rendu(cible, n, "photos", ecran)


def _montage_videos(exe, videos, piste, duree, ecran):
    """Aller-retour sur chaque sequence, puis boucle, puis fondu enchaine."""
    n = len(videos)
    part = max(3.0, (duree + (n - 1) * FONDU) / n)

    entrees, filtres = [], []
    for i, v in enumerate(videos):
        entrees += ["-i", str(v)]
        # L aller-retour : la sequence, puis la meme a l envers. Le raccord
        # est invisible parce que la derniere image de l aller est aussi la
        # premiere du retour.
        filtres.append(
            f"[{i}:v]scale=1280:720:force_original_aspect_ratio=increase,"
            f"crop=1280:720,fps=25,setsar=1,split[a{i}][b{i}]")
        filtres.append(f"[b{i}]reverse[r{i}]")
        # loop compte en images et garde tout en memoire : on borne la reserve
        # a ce qu on va reellement rejouer.
        filtres.append(
            f"[a{i}][r{i}]concat=n=2:v=1:a=0,"
            f"loop=loop=-1:size=32000:start=0,"
            f"trim=duration={part:.2f},setpts=PTS-STARTPTS,fps=25,"
            f"format=yuv420p,setsar=1[v{i}]")

    filtres += _enchainer([f"[v{i}]" for i in range(n)], part, "[vx]")
    filtres.append(f"[vx]trim=duration={duree:.2f},setpts=PTS-STARTPTS,"
                   f"fade=t=in:st=0:d=1,"
                   f"fade=t=out:st={max(0, duree - 1.5):.2f}:d=1.5[vf]")
    filtres.append(_son(n, duree))

    args = entrees + ["-i", str(piste),
                      "-filter_complex", ";".join(filtres),
                      "-map", "[vf]", "-map", "[af]"] + _FIN + \
        ["-t", f"{duree:.2f}"]
    cible = _sortie("clip-video")
    souci = _lancer(exe, args, cible)
    if souci:
        return f"Le montage a echoue : {souci}"
    return _rendu(cible, n, "sequences", ecran)


def _rendu(cible, nombre, quoi, ecran):
    _DERNIER["chemin"] = cible
    if ecran:
        return f"Clip monte. {envoyer_clip_ecran(ecran=ecran)}"
    try:
        import os
        os.startfile(str(cible))
    except Exception:
        pass
    return (f"Clip monte a partir de {nombre} {quoi}, "
            f"{cible.stat().st_size / 2**20:.0f} Mo.")


_DERNIER = {"chemin": None}


@outil(
    nom="envoyer_clip_ecran",
    description="Joue le dernier clip monte sur une television.",
    parametres={
        "type": "object",
        "properties": {
            "ecran": {"type": "string", "description": "Nom de l ecran."},
        },
        "required": ["ecran"],
    },
    lent=True,
    phrase_attente="J envoie le clip.",
)
def envoyer_clip_ecran(ecran: str) -> str:
    from core.config import reglage
    chemin = _DERNIER.get("chemin")
    if not chemin or not Path(chemin).exists():
        return "Je n ai pas de clip sous la main."
    from tools.cast import _adresse_pour, _choisir as _ecran
    appareil = _ecran(ecran)
    if appareil is None:
        return f"Je ne trouve pas {ecran}."
    port = int(reglage("hud.port", 8770))
    url = (f"http://{_adresse_pour(appareil.cast_info.host)}:{port}"
           f"/clip/{Path(chemin).name}")
    try:
        appareil.wait(timeout=12)
        appareil.media_controller.play_media(url, "video/mp4")
        appareil.media_controller.block_until_active(timeout=20)
    except Exception as e:
        return f"L envoi a echoue : {str(e)[:60]}"
    return f"Ca passe sur {appareil.cast_info.friendly_name}."
