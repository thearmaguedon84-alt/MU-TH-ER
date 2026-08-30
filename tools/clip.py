"""Monter un clip : des images ou des videos, sur un morceau.

Le probleme de fond est une affaire de duree. Un modele video produit cinq
secondes ; un morceau en dure quarante-cinq. Il faut donc combler, et la
maniere de combler decide de tout.

Boucler betement se voit : a chaque reprise, l'image saute. On monte donc les
sequences en aller-retour — la video jouee, puis jouee a l'envers — ce qui
donne un raccord invisible. Pour des photos, on prefere un lent mouvement de
zoom, qui donne du mouvement sans rien inventer.

Le son est fondu au debut et a la fin ; sans cela un clip commence et finit
par un claquement.
"""
import re
import subprocess
import time
from pathlib import Path

from core.registre import outil

from core.dossiers import dossier

RACINE = Path(__file__).resolve().parent.parent
DOSSIER = dossier("clips")


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


def _derniers(dossier, motifs, combien=1):
    if not dossier.is_dir():
        return []
    lot = []
    for motif in motifs:
        lot += list(dossier.glob(motif))
    lot.sort(key=lambda p: -p.stat().st_mtime)
    return lot[:combien]


def _trouver_musique(nom):
    from tools import musique as M
    if nom and Path(nom).is_file():
        return Path(nom)
    if not nom or re.search(r"derni|celle|la musique|le morceau", nom.lower()):
        d = M._DERNIERE.get("chemin")
        if d and Path(d).exists():
            return Path(d)
    lot = _derniers(M.DOSSIER, ["*.mp3", "*.wav"])
    if nom and len(nom) >= 3:
        for p in (M.DOSSIER.glob("*") if M.DOSSIER.is_dir() else []):
            if nom.lower().strip() in p.stem.lower():
                return p
    return lot[0] if lot else None


@outil(
    nom="monter_clip",
    description=(
        "Monte un clip video : assemble des videos ou des images sur un "
        "morceau de musique, cale sur sa duree, avec fondus. Pour 'fais-moi "
        "un clip avec la musique', 'monte une video sur le morceau'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "musique": {"type": "string",
                        "description": "Fichier ou 'la derniere'. Vide = le dernier morceau."},
            "sources": {"type": "string",
                        "description": "'videos', 'images' ou 'photos'. Ce qu il faut monter."},
            "combien": {"type": "integer",
                        "description": "Nombre de sequences a enchainer. 4 par defaut."},
            "ecran": {"type": "string", "description": "Nom d un ecran."},
        },
        "required": [],
    },
    lent=True,
    phrase_attente="Je monte le clip.",
)
def monter_clip(musique: str = "", sources: str = "", combien: int = 4,
                ecran: str = "") -> str:
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

    from tools.image import DOSSIER as IMAGES
    videos = [] if veut_images else _derniers(dossier("videos"),
                                              ["*.mp4", "*.webm"], combien)
    if videos:
        return _montage_videos(exe, videos, piste, duree, ecran)

    photos = _derniers(IMAGES, ["*.png", "*.jpg"], combien)
    if not photos:
        return "Je n ai ni video ni image a monter."
    return _montage_photos(exe, photos, piste, duree, ecran)


def _sortie(nom):
    DOSSIER.mkdir(exist_ok=True)
    propre = re.sub(r"[^a-z0-9]+", "-", nom.lower())[:40].strip("-")
    return DOSSIER / f"{time.strftime('%Y%m%d-%H%M%S')}-{propre}.mp4"


def _lancer(exe, args, cible):
    r = subprocess.run([exe, "-y"] + args + [str(cible)], capture_output=True,
                       text=True, errors="replace", timeout=1800)
    if r.returncode or not cible.exists():
        derniere = [l for l in (r.stderr or "").split("\n") if l.strip()][-1:]
        return derniere[0][:120] if derniere else "ffmpeg a echoue"
    return None


def _montage_photos(exe, photos, piste, duree, ecran):
    """Un lent zoom sur chaque photo, enchainees en fondu."""
    par_photo = max(3.0, duree / len(photos))
    fps = 25
    images_par_photo = int(par_photo * fps)

    entrees, filtres = [], []
    for i, p in enumerate(photos):
        entrees += ["-loop", "1", "-t", f"{par_photo:.2f}", "-i", str(p)]
        # Le zoom part de 1 et monte doucement : c est le mouvement le plus
        # sobre, et le seul qui ne trahisse jamais une photo fixe.
        filtres.append(
            f"[{i}:v]scale=1920:-2,crop=1920:1080,"
            f"zoompan=z='min(zoom+0.0004,1.12)':d={images_par_photo}:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1280x720:fps={fps},"
            f"setsar=1[v{i}]")

    chaine = "".join(f"[v{i}]" for i in range(len(photos)))
    filtres.append(f"{chaine}concat=n={len(photos)}:v=1:a=0[vid]")
    filtres.append(f"[vid]fade=t=in:st=0:d=1,"
                   f"fade=t=out:st={max(0, duree - 1.5):.2f}:d=1.5[vf]")
    filtres.append(f"[{len(photos)}:a]afade=t=in:st=0:d=1.5,"
                   f"afade=t=out:st={max(0, duree - 3):.2f}:d=3[af]")

    args = entrees + ["-i", str(piste),
                      "-filter_complex", ";".join(filtres),
                      "-map", "[vf]", "-map", "[af]",
                      "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                      "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                      "-shortest"]
    cible = _sortie("clip-photos")
    souci = _lancer(exe, args, cible)
    if souci:
        return f"Le montage a echoue : {souci}"
    return _rendu(cible, len(photos), "photos", ecran)


def _montage_videos(exe, videos, piste, duree, ecran):
    """Aller-retour sur chaque sequence pour combler sans raccord visible."""
    entrees, filtres = [], []
    for i, v in enumerate(videos):
        entrees += ["-stream_loop", "-1", "-i", str(v)]
        filtres.append(f"[{i}:v]scale=1280:720:force_original_aspect_ratio="
                       f"increase,crop=1280:720,setsar=1,fps=25[v{i}]")
    chaine = "".join(f"[v{i}]" for i in range(len(videos)))
    filtres.append(f"{chaine}concat=n={len(videos)}:v=1:a=0[vid]")
    filtres.append(f"[vid]trim=duration={duree:.2f},setpts=PTS-STARTPTS,"
                   f"fade=t=in:st=0:d=1,"
                   f"fade=t=out:st={max(0, duree - 1.5):.2f}:d=1.5[vf]")
    filtres.append(f"[{len(videos)}:a]afade=t=in:st=0:d=1.5,"
                   f"afade=t=out:st={max(0, duree - 3):.2f}:d=3[af]")

    args = entrees + ["-i", str(piste),
                      "-filter_complex", ";".join(filtres),
                      "-map", "[vf]", "-map", "[af]",
                      "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                      "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                      "-t", f"{duree:.2f}"]
    cible = _sortie("clip-video")
    souci = _lancer(exe, args, cible)
    if souci:
        return f"Le montage a echoue : {souci}"
    return _rendu(cible, len(videos), "sequences", ecran)


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
    from tools.cast import _adresse_pour, _choisir
    appareil = _choisir(ecran)
    if appareil is None:
        return f"Je ne trouve pas {ecran}."
    port = int(reglage("hud.port", 8770))
    url = f"http://{_adresse_pour(appareil.cast_info.host)}:{port}/clip/{Path(chemin).name}"
    try:
        appareil.wait(timeout=12)
        appareil.media_controller.play_media(url, "video/mp4")
        appareil.media_controller.block_until_active(timeout=20)
    except Exception as e:
        return f"L envoi a echoue : {str(e)[:60]}"
    return f"Ca passe sur {appareil.cast_info.friendly_name}."
