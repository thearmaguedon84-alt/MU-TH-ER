"""Fabriquer de courtes videos, en local.

Le moteur est ComfyUI et le modele Wan 2.2 TI2V-5B, qui fait le texte-vers-video
et la photo-vers-video avec les memes poids : animer une photo n'est qu'un cas
particulier ou l'on fournit la premiere image.

Trois choix imposes par une carte de douze giga-octets :

- **Les poids sont charges en fp8.** Le fichier fait 9,3 Go en pleine
  precision, ce qui ne laisse rien pour le calcul. En demi-precision il en
  occupe la moitie, pour une difference de qualite qu'on ne voit pas.
- **Le format par defaut est 704x1280 ou 1280x704, jamais plus.** Au-dela, la
  memoire sature et le rendu s'effondre.
- **Cinq secondes.** La duree se compte en images : 121 a 24 par seconde. Le
  cout monte vite et lineairement.

ComfyUI travaille en file d'attente : on depose un montage, on attend, on
recupere. Le montage est construit ici plutot que lu depuis un fichier, pour
que les reglages restent lisibles au meme endroit que les explications.
"""
import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from core.config import reglage
from core.dossiers import dossier
from core.registre import outil

ADRESSE = "http://127.0.0.1:8188"

# Ce que le modele ne doit pas produire. Une video ratee l'est souvent par
# scintillement ou par deformation progressive, d'ou ces termes precis.
NEGATIF = ("blurry, low quality, distorted, deformed, flickering, jittery, "
           "morphing, watermark, text, static image, overexposed")

_DERNIERE = {"chemin": None, "demande": None}


def _repond(delai=3):
    try:
        import httpx
        return httpx.get(f"{ADRESSE}/system_stats", timeout=delai).status_code == 200
    except Exception:
        return False


def _demarrer(patience=300):
    if _repond():
        return True
    racine = Path(reglage("video.moteur", r"F:\IA\comfyui"))
    py = racine / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        return False
    try:
        subprocess.Popen([str(py), "main.py", "--port", "8188"],
                         cwd=str(racine),
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        return False
    debut = time.time()
    while time.time() - debut < patience:
        time.sleep(4)
        if _repond():
            return True
    return False


def _deposer_image(source):
    """ComfyUI ne lit que son propre dossier d entree : on y copie la photo."""
    racine = Path(reglage("video.moteur", r"F:\IA\comfyui"))
    entree = racine / "input"
    entree.mkdir(exist_ok=True)
    nom = f"muthur-{uuid.uuid4().hex[:8]}{Path(source).suffix.lower()}"
    shutil.copy(str(source), str(entree / nom))
    return nom


def _montage(description, largeur, hauteur, images, graine, depart=None,
             etapes=20):
    """Le graphe envoye a ComfyUI, tel qu il l attend."""
    g = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": reglage("video.modele",
                                              "wan2.2_ti2v_5B_fp16.safetensors"),
                         # fp8 : deux fois moins de memoire, difference invisible.
                         "weight_dtype": "fp8_e4m3fn"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                         "type": "wan"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"text": description, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"text": NEGATIF, "clip": ["2", 0]}},
        "7": {"class_type": "Wan22ImageToVideoLatent",
              "inputs": {"vae": ["3", 0], "width": largeur, "height": hauteur,
                         "length": images, "batch_size": 1}},
        # Le decalage 8 est celui que recommande Wan pour ce modele ; en
        # dessous, le mouvement devient mou.
        "8": {"class_type": "ModelSamplingSD3",
              "inputs": {"model": ["1", 0], "shift": 8.0}},
        "9": {"class_type": "KSampler",
              "inputs": {"model": ["8", 0], "positive": ["4", 0],
                         "negative": ["5", 0], "latent_image": ["7", 0],
                         "seed": graine, "steps": etapes, "cfg": 5.0,
                         "sampler_name": "uni_pc", "scheduler": "simple",
                         "denoise": 1.0}},
        "10": {"class_type": "VAEDecode",
               "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
        "11": {"class_type": "CreateVideo",
               "inputs": {"images": ["10", 0], "fps": 24.0}},
        "12": {"class_type": "SaveVideo",
               "inputs": {"video": ["11", 0], "filename_prefix": "muthur/sequence",
                          "format": "auto", "codec": "auto"}},
    }
    if depart:
        g["6"] = {"class_type": "LoadImage", "inputs": {"image": depart}}
        g["7"]["inputs"]["start_image"] = ["6", 0]
    return g


def _attendre(tache, patience):
    import httpx
    debut = time.time()
    while time.time() - debut < patience:
        time.sleep(6)
        try:
            h = httpx.get(f"{ADRESSE}/history/{tache}", timeout=30).json()
        except Exception:
            continue
        if tache not in h:
            continue
        sorties = (h[tache].get("outputs") or {})
        for lot in sorties.values():
            for cle in ("videos", "gifs", "images"):
                for f in lot.get(cle) or []:
                    if f.get("filename"):
                        return f
        statut = (h[tache].get("status") or {})
        if statut.get("status_str") == "error" or statut.get("completed"):
            return None
    return None


def _recuperer(fiche):
    racine = Path(reglage("video.moteur", r"F:\IA\comfyui"))
    sous = fiche.get("subfolder") or ""
    chemin = racine / (fiche.get("type") or "output") / sous / fiche["filename"]
    return chemin if chemin.exists() else None


@outil(
    nom="generer_video",
    description=(
        "Fabrique une courte video avec le moteur local, soit a partir d'une "
        "description seule, soit en animant une photo existante. IMPORTANT : "
        "donne la description en ANGLAIS, et decris le MOUVEMENT autant que la "
        "scene ('slow camera push in, hair moving in the wind'). Pour 'fais-moi "
        "une video de', 'anime cette photo'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "description": {"type": "string",
                            "description": "La scene ET le mouvement, en anglais."},
            "image": {"type": "string",
                      "description": "Photo de depart : 'ma photo', 'la derniere image', un nom. Vide = depuis rien."},
            "duree": {"type": "integer",
                      "description": "Secondes. 5 par defaut, 10 au plus."},
            "format": {"type": "string",
                       "description": "paysage, portrait ou carre."},
            "ecran": {"type": "string", "description": "Nom d un ecran."},
        },
        "required": ["description"],
    },
    lent=True,
    phrase_attente="Je fabrique la video. C est long, plusieurs minutes.",
)
def generer_video(description: str, image: str = "", duree: int = 5,
                  format: str = "", ecran: str = "") -> str:
    from tools.image import _en_anglais

    description = (description or "").strip()
    if not description:
        return "Que veux-tu que je filme ?"

    try:
        from core.vram import liberer
        liberer(pour="video", besoin=9.0)
    except Exception:
        pass

    if not _demarrer():
        return "Le moteur video ne repond pas."

    description = _en_anglais(description)
    duree = max(2, min(int(duree or 5), 10))
    # La longueur doit tomber sur un multiple de 4, plus un.
    images = int(duree * 24)
    images = images - (images % 4) + 1

    tailles = {"portrait": (704, 1280), "carre": (960, 960)}
    largeur, hauteur = tailles.get((format or "").lower(), (1280, 704))

    depart = None
    if image:
        from tools.modifier_image import _trouver
        source = _trouver(image)
        if source is None or not Path(source).exists():
            return "Je ne trouve pas l image de depart."
        depart = _deposer_image(source)
        # On suit le format de la photo plutot que d imposer le notre.
        try:
            from PIL import Image as PILImage
            with PILImage.open(source) as im:
                if im.height > im.width:
                    largeur, hauteur = 704, 1280
                elif im.height == im.width:
                    largeur, hauteur = 960, 960
        except Exception:
            pass

    graine = int(time.time()) % 2**31
    montage = _montage(description, largeur, hauteur, images, graine, depart,
                       int(reglage("video.etapes", 20)))

    try:
        import httpx
        r = httpx.post(f"{ADRESSE}/prompt",
                       json={"prompt": montage, "client_id": "jarvis"},
                       timeout=120)
        if r.status_code != 200:
            return f"Le moteur a refuse le montage : {r.text[:110]}"
        tache = (r.json() or {}).get("prompt_id")
    except Exception as e:
        return f"La video n a pas demarre : {str(e)[:70]}"
    if not tache:
        return "Le moteur n a pas accepte la demande."

    fiche = _attendre(tache, int(reglage("video.patience", 3600)))
    if not fiche:
        return "La video n est pas arrivee dans le temps imparti."
    produit = _recuperer(fiche)
    if produit is None:
        return "Le fichier produit est introuvable."

    cible = dossier("videos")
    propre = re.sub(r"[^a-z0-9]+", "-", description.lower())[:44].strip("-")
    chemin = cible / f"{time.strftime('%Y%m%d-%H%M%S')}-{propre}{produit.suffix}"
    shutil.copy(str(produit), str(chemin))
    _DERNIERE["chemin"] = chemin
    _DERNIERE["demande"] = description

    if ecran:
        return f"Voila. {envoyer_video_ecran(ecran=ecran)}"
    try:
        import os
        os.startfile(str(chemin))
    except Exception:
        pass
    depuis = " depuis ta photo" if depart else ""
    return f"Voila ta video de {duree} secondes{depuis}."


@outil(
    nom="envoyer_video_ecran",
    description="Joue la derniere video fabriquee sur une television.",
    parametres={
        "type": "object",
        "properties": {
            "ecran": {"type": "string", "description": "Nom de l ecran."},
        },
        "required": ["ecran"],
    },
    lent=True,
    phrase_attente="J envoie la video.",
)
def envoyer_video_ecran(ecran: str) -> str:
    chemin = _DERNIERE.get("chemin")
    if not chemin or not Path(chemin).exists():
        return "Je n ai pas de video sous la main."
    from tools.cast import _adresse_pour, _choisir
    appareil = _choisir(ecran)
    if appareil is None:
        return f"Je ne trouve pas {ecran}."
    port = int(reglage("hud.port", 8770))
    url = (f"http://{_adresse_pour(appareil.cast_info.host)}:{port}"
           f"/video/{Path(chemin).name}")
    try:
        appareil.wait(timeout=12)
        appareil.media_controller.play_media(url, "video/mp4")
        appareil.media_controller.block_until_active(timeout=20)
    except Exception as e:
        return f"L envoi a echoue : {str(e)[:60]}"
    return f"Ca passe sur {appareil.cast_info.friendly_name}."
