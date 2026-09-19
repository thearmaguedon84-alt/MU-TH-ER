"""Generation video via WanGP (Wan2GP) — alternative a ComfyUI.

WanGP est optimise pour les cartes graphiques modestes (6 Go+) et supporte
Wan 2.1/2.2, LTX Video, Hunyuan et Flux. Il s installe separement et se
configure via la cle `video_wangp.dossier` dans config.yaml.

Par rapport au moteur ComfyUI (tools/video.py) :
- Demarrage plus rapide, moindre consommation memoire
- API Python directe (pas besoin de construire un graphe JSON)
- Supporte plusieurs modeles sans reconfiguration

Installation de WanGP :
  git clone https://github.com/deepbeepmeep/Wan2GP F:/IA/Wan2GP
  cd F:/IA/Wan2GP && pip install -r requirements.txt

Cles config.yaml :
  video_wangp.dossier     : chemin du depot clonable (defaut : F:\\IA\\Wan2GP)
  video_wangp.modele_t2v  : type de modele texte-vers-video
  video_wangp.modele_i2v  : type de modele image-vers-video
  video_wangp.etapes      : pas de diffusion (defaut : 20)
  video_wangp.args        : arguments CLI passes a init() (defaut : voir bas)
"""
import re
import shutil
import sys
import time
from pathlib import Path

from core.config import reglage
from core.dossiers import dossier
from core.file_gpu import enfile
from core.registre import outil

# Session unique : on initialise une seule fois par execution de Jarvis.
_SESSION = None
_DERNIERE: dict = {"chemin": None, "demande": None}


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def _racine() -> Path:
    return Path(reglage("video_wangp.dossier", r"F:\IA\Wan2GP"))


def _session():
    """Renvoie la session WanGP, l initialise au premier appel."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION

    racine = _racine()
    if not racine.exists():
        return None

    # WanGP n est pas un paquet pip standard : on l importe depuis son dossier.
    if str(racine) not in sys.path:
        sys.path.insert(0, str(racine))

    try:
        from shared.api import init  # type: ignore[import]
        args = reglage("video_wangp.args", None)
        if args is None:
            # sdpa = scaled dot-product attention (natif PyTorch 2+, rapide).
            # profile 4 = optimise pour 8-12 Go de VRAM.
            args = ["--attention", "sdpa", "--profile", "4"]
        _SESSION = init(
            root=racine,
            console_output=False,
            cli_args=list(args),
        )
        return _SESSION
    except Exception:
        return None


def _modele_t2v() -> str:
    return reglage("video_wangp.modele_t2v", "wan2_2_t2v_1_3B")


def _modele_i2v() -> str:
    return reglage("video_wangp.modele_i2v", "wan2_2_i2v_1_3B")


# ---------------------------------------------------------------------------
# Outil : generation
# ---------------------------------------------------------------------------

@outil(
    nom="generer_video_wangp",
    description=(
        "Fabrique une courte video avec WanGP (moteur Wan optimise GPU). "
        "Accepte une description texte seule ou une photo a animer. "
        "Pour 'fais une video avec WanGP', 'anime cette photo avec WanGP'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": (
                    "La scene ET le mouvement, en anglais. "
                    "Ex : 'slow push-in on a lighthouse, waves crashing'."
                ),
            },
            "image": {
                "type": "string",
                "description": (
                    "Photo de depart : 'ma photo', 'la derniere image', un nom. "
                    "Vide = depuis un texte seulement."
                ),
            },
            "duree": {
                "type": "integer",
                "description": "Duree en secondes (2 a 30). Defaut : 5.",
            },
            "format": {
                "type": "string",
                "description": "paysage (defaut), portrait ou carre.",
            },
        },
        "required": ["description"],
    },
    lent=True,
    phrase_attente=(
        "Je fabrique la video avec WanGP. Ca prend quelques minutes."
    ),
)
@enfile("video", "description")
def generer_video_wangp(description: str, image: str = "",
                        duree: int = 5, format: str = "") -> str:
    from tools.image import _en_anglais

    description = (description or "").strip()
    if not description:
        return "Que veux-tu que je filme ?"

    # Liberation VRAM si un autre moteur tourne.
    try:
        from core.vram import liberer
        liberer(pour="video_wangp", besoin=6.0)
    except Exception:
        pass

    sess = _session()
    if sess is None:
        return (
            "WanGP n est pas trouve. "
            f"Installe-le dans {_racine()} ou corrige la cle "
            "'video_wangp.dossier' dans config.yaml."
        )

    description = _en_anglais(description)
    duree = max(2, min(int(duree or 5), 30))

    # WanGP compte en frames a 24 fps.
    # Le nombre de frames doit etre un multiple de 4 + 1.
    frames = int(duree * 24)
    frames = frames - (frames % 4) + 1

    tailles: dict = {
        "portrait": (480, 832),
        "carre":    (640, 640),
        "carré":    (640, 640),
    }
    largeur, hauteur = tailles.get((format or "").lower(), (832, 480))

    settings: dict = {
        "prompt":              description,
        "resolution":          f"{largeur}x{hauteur}",
        "num_inference_steps": int(reglage("video_wangp.etapes", 20)),
        "video_length":        frames,
        "duration_seconds":    duree,
        "force_fps":           24,
    }

    # ---- Image de depart (image-to-video) --------------------------------
    source_image = None
    if image:
        from tools.modifier_image import _trouver
        source_image = _trouver(image)
        if source_image is None or not Path(source_image).exists():
            return "Je ne trouve pas l image de depart."

        settings["model_type"]        = _modele_i2v()
        settings["video_prompt_type"] = "IM"   # Image Mode
        settings["image_prompt"]      = str(source_image)

        # Adapter la resolution a l orientation de la photo.
        try:
            from PIL import Image as PILImage
            with PILImage.open(source_image) as im:
                if im.height > im.width:
                    settings["resolution"] = "480x832"
                elif im.height == im.width:
                    settings["resolution"] = "640x640"
        except Exception:
            pass
    else:
        settings["model_type"] = _modele_t2v()

    # ---- Generation -------------------------------------------------------
    try:
        job = sess.submit_task(settings)
        result = job.result()
    except Exception as e:
        return f"WanGP a echoue : {str(e)[:150]}"

    if not result.success:
        msgs = "; ".join(
            getattr(e, "message", str(e)) for e in (result.errors or [])
        )
        return f"WanGP n a pas abouti : {msgs[:150]}"

    fichiers = result.generated_files or []
    if not fichiers:
        return "WanGP n a produit aucun fichier."

    source = Path(str(fichiers[0]))
    if not source.exists():
        return "Le fichier produit est introuvable apres generation."

    # ---- Rangement --------------------------------------------------------
    cible = dossier("videos")
    propre = re.sub(r"[^a-z0-9]+", "-", description.lower())[:44].strip("-")
    destination = (
        cible / f"{time.strftime('%Y%m%d-%H%M%S')}-wangp-{propre}{source.suffix}"
    )
    shutil.copy(str(source), str(destination))

    _DERNIERE["chemin"]  = destination
    _DERNIERE["demande"] = description

    # Synchroniser avec video.py pour que les outils mail/ecran y aient acces.
    try:
        import tools.video as _v
        _v._DERNIERE["chemin"]  = destination
        _v._DERNIERE["demande"] = description
    except Exception:
        pass

    try:
        import os
        os.startfile(str(destination))
    except Exception:
        pass

    depuis = " depuis ta photo" if source_image else ""
    return f"Voila ta video WanGP de {duree} secondes{depuis}."


# ---------------------------------------------------------------------------
# Outil : lister les modeles disponibles
# ---------------------------------------------------------------------------

@outil(
    nom="modeles_wangp",
    description=(
        "Liste les modeles video disponibles dans WanGP "
        "(Wan 2.2, LTX, Hunyuan…). "
        "Pour 'quels modeles a WanGP', 'liste les modeles WanGP'."
    ),
    parametres={"type": "object", "properties": {}, "required": []},
)
def modeles_wangp() -> str:
    sess = _session()
    if sess is None:
        return f"WanGP introuvable (dossier configure : {_racine()})."
    try:
        modeles = sess.list_model_defs(main_output="video")
        if not modeles:
            return "Aucun modele trouve dans WanGP."
        lignes = [f"  - {m}" for m in modeles[:30]]
        return "Modeles video disponibles dans WanGP :\n" + "\n".join(lignes)
    except Exception as e:
        return f"Impossible de lister les modeles : {str(e)[:100]}"
