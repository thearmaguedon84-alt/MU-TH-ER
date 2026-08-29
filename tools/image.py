"""Fabriquer des images, en local, et les montrer.

Le moteur est Forge, lance a la demande : il occupe plusieurs giga-octets de
memoire graphique et n'a aucune raison de tourner en permanence. Jarvis le
demarre au premier besoin, attend qu'il reponde, puis s'en sert.

Deux choix de conception meritent d'etre dits :

- **La description est traduite avant d'etre envoyee.** Ces modeles sont
  entraines sur des legendes anglaises et comprennent mal le francais. Le
  modele de Jarvis fait la traduction ; l'outil precise seulement ce qu'il
  attend.
- **Rien n'est ajoute a la demande.** Ni style impose, ni terme de qualite
  greffe d'office. Ce qui est demande est ce qui est genere, et l'utilisateur
  garde la main sur le resultat.

Les images sont gardees dans `images/`, nommees par leur horodatage et le
debut de la demande, pour pouvoir les retrouver.
"""
import base64
import os
import re
import subprocess
import time
from pathlib import Path

from core.config import reglage
from core.registre import outil

RACINE = Path(__file__).resolve().parent.parent
DOSSIER = RACINE / "images"
ADRESSE = "http://127.0.0.1:7860"

# Derniere image produite : sert a « envoie-la sur la tele ».
_DERNIERE = {"chemin": None, "demande": None}


def _moteur_repond(delai=3):
    try:
        import httpx
        r = httpx.get(f"{ADRESSE}/sdapi/v1/options", timeout=delai)
        return r.status_code == 200
    except Exception:
        return False


def _demarrer_moteur(patience=180):
    """Lance Forge s'il ne tourne pas, et attend qu'il reponde.

    Le premier demarrage charge le modele en memoire graphique : c'est long,
    et il n'y a rien a faire d'autre qu'attendre.
    """
    if _moteur_repond():
        return True

    dossier = reglage("images.forge", r"F:\IA\forge")
    lanceur = Path(dossier) / "webui-user.bat"
    if not lanceur.exists():
        return False
    try:
        subprocess.Popen(["cmd", "/c", str(lanceur)], cwd=str(dossier),
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
    except Exception:
        return False

    debut = time.time()
    while time.time() - debut < patience:
        time.sleep(4)
        if _moteur_repond():
            return True
    return False



# Mots qui trahissent une demande restee en francais.
_FRANCAIS = re.compile(
    r"\b(?:un|une|des|le|la|les|avec|dans|sur|qui|pour|deguise|maillot|"
    r"plage|chien|chat|poulet|voiture|maison|homme|femme|enfant|ciel|mer)\b",
    re.I)


def _en_anglais(texte):
    """Traduit une demande en anglais si elle ne l est pas deja.

    Ces modeles sont entraines sur des legendes anglaises : une demande en
    francais donne des resultats decevants sans qu on comprenne pourquoi. La
    traduction est une tache courte et cadree, que le modele local fait bien.
    """
    if not _FRANCAIS.search(texte or ""):
        return texte
    try:
        import httpx
        hote = reglage("ollama.hote", "http://127.0.0.1:11434")
        r = httpx.post(
            f"{hote}/api/generate",
            json={
                "model": reglage("ollama.modele", "qwen2.5:7b"),
                "prompt": ("Translate this image description to English. "
                           "Keep every detail, add nothing, invent nothing. "
                           "Answer with the translation only, no quotes.\n\n"
                           + texte),
                "stream": False,
                "options": {"temperature": 0.1},
            },
            timeout=60,
        )
        traduit = (r.json().get("response") or "").strip().strip('"')
        # Une traduction qui explose en longueur est un modele qui brode.
        if traduit and len(traduit) < len(texte) * 3:
            return traduit
    except Exception:
        pass
    return texte

def _nom_de_fichier(demande):
    propre = re.sub(r"[^a-z0-9]+", "-", (demande or "image").lower())[:48]
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{propre.strip('-')}.png"


@outil(
    nom="generer_image",
    description=(
        "Fabrique une image a partir d'une description, avec le moteur local. "
        "IMPORTANT : donne la description en ANGLAIS, meme si la demande est "
        "en francais — le modele ne comprend que l'anglais. Traduis fidelement "
        "sans rien ajouter. Pour 'fais-moi une image de', 'dessine', 'genere "
        "une image'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Ce qu il faut representer, en anglais.",
            },
            "format": {
                "type": "string",
                "description": "carre, portrait ou paysage. Carre par defaut.",
            },
            "ecran": {
                "type": "string",
                "description": "Nom d un ecran pour l y envoyer. Vide = sur le PC.",
            },
        },
        "required": ["description"],
    },
    lent=True,
    phrase_attente="Je fabrique l image.",
)
def generer_image(description: str, format: str = "", ecran: str = "") -> str:
    description = (description or "").strip()
    if not description:
        return "Que veux-tu que je represente ?"

    description = _en_anglais(description)

    if not _demarrer_moteur():
        return ("Le moteur d images ne repond pas. Verifie qu il est installe "
                "et que le chemin est bon dans les reglages.")

    tailles = {"portrait": (832, 1216), "paysage": (1216, 832)}
    largeur, hauteur = tailles.get((format or "").lower(), (1024, 1024))

    try:
        import httpx
        r = httpx.post(
            f"{ADRESSE}/sdapi/v1/txt2img",
            json={
                "prompt": description,
                "steps": int(reglage("images.etapes", 28)),
                "cfg_scale": float(reglage("images.guidage", 5.5)),
                "width": largeur,
                "height": hauteur,
                "sampler_name": reglage("images.echantillonneur", "DPM++ 2M"),
                "scheduler": "Karras",
            },
            timeout=300,
        )
        r.raise_for_status()
        images = r.json().get("images") or []
    except Exception as e:
        return f"La generation a echoue : {str(e)[:70]}"

    if not images:
        return "Le moteur n a rien renvoye."

    DOSSIER.mkdir(exist_ok=True)
    chemin = DOSSIER / _nom_de_fichier(description)
    chemin.write_bytes(base64.b64decode(images[0].split(",", 1)[-1]))
    _DERNIERE["chemin"] = chemin
    _DERNIERE["demande"] = description

    # L'interface l'affiche des qu'elle arrive.
    try:
        import hud
        hud.publier_image("/image/" + chemin.name, description)
    except Exception:
        pass

    if ecran:
        envoi = envoyer_image_ecran(ecran=ecran)
        return f"Voila. {envoi}"

    try:
        os.startfile(str(chemin))
    except Exception:
        pass
    return "Voila ton image."


@outil(
    nom="envoyer_image_ecran",
    description=("Envoie la derniere image fabriquee sur un ecran. Pour "
                 "'envoie-la sur la tele', 'affiche-la sur le videoprojecteur'."),
    parametres={
        "type": "object",
        "properties": {
            "ecran": {"type": "string", "description": "Nom de l ecran."},
        },
        "required": ["ecran"],
    },
    lent=True,
    phrase_attente="Je l envoie sur l ecran.",
)
def envoyer_image_ecran(ecran: str) -> str:
    chemin = _DERNIERE.get("chemin")
    if not chemin or not Path(chemin).exists():
        return "Je n ai pas d image sous la main."

    from tools.cast import _adresse_pour, _choisir
    appareil = _choisir(ecran)
    if appareil is None:
        return f"Je ne trouve pas l ecran {ecran}."

    port = int(reglage("hud.port", 8770))
    adresse = _adresse_pour(appareil.cast_info.host)
    url = f"http://{adresse}:{port}/image/{Path(chemin).name}"

    try:
        appareil.wait(timeout=12)
        # Une image passe par le lecteur standard : inutile de faire charger
        # une page au televiseur pour si peu.
        appareil.media_controller.play_media(url, "image/png")
        appareil.media_controller.block_until_active(timeout=20)
    except Exception as e:
        return f"L envoi a echoue : {str(e)[:60]}"
    return f"Elle est sur {appareil.cast_info.friendly_name}."


@outil(
    nom="images_recentes",
    description="Dit combien d images ont ete fabriquees et ou elles sont.",
    parametres={"type": "object", "properties": {}, "required": []},
)
def images_recentes() -> str:
    if not DOSSIER.is_dir():
        return "Je n ai encore fabrique aucune image."
    fichiers = sorted(DOSSIER.glob("*.png"), key=lambda p: -p.stat().st_mtime)
    if not fichiers:
        return "Je n ai encore fabrique aucune image."
    return (f"{len(fichiers)} images dans le dossier images. "
            f"La derniere : {fichiers[0].stem.split('-', 2)[-1].replace('-', ' ')}.")
