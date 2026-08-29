"""Reprendre une image existante plutot que de partir de rien.

Le moteur repart de l'image fournie au lieu du bruit. Un seul reglage compte
vraiment : la force de la reprise. En dessous de 0,4 l'image d'origine reste
tres lisible et on ne fait que la reteinter ; au-dela de 0,7 il n'en reste
guere que la composition.

Designer l'image a la voix est le point delicat. Trois facons, de la plus
precise a la plus commode :

- un chemin complet, s'il est dicte ou colle ;
- « la derniere image », celle que Jarvis vient de fabriquer ;
- « ma derniere photo », la plus recente trouvee dans les dossiers usuels.
"""
import base64
import re
import time
from pathlib import Path

from core.registre import outil

_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _dossiers_images():
    """Endroits ou l'on range naturellement ses images."""
    import os
    profil = Path(os.environ.get("USERPROFILE", ""))
    return [profil / "Pictures", profil / "Downloads", profil / "Desktop",
            profil / "Images", profil / "Téléchargements", profil / "Bureau"]


def _image_recente(dossiers=None, depuis_jours=0):
    """Image la plus recemment modifiee dans les dossiers usuels.

    Sans borne de temps par defaut : une photo de l an dernier reste la plus
    recente si l on n en a pas pris depuis, et la refuser n aurait aucun sens.
    """
    limite = (time.time() - depuis_jours * 86400) if depuis_jours else 0
    meilleure, date = None, 0
    for d in (dossiers or _dossiers_images()):
        if not d.is_dir():
            continue
        for f in d.iterdir():
            try:
                if (f.suffix.lower() in _EXTENSIONS and f.is_file()
                        and f.stat().st_mtime > max(date, limite)):
                    meilleure, date = f, f.stat().st_mtime
            except Exception:
                continue
    return meilleure


def _trouver(designation):
    """Retrouve l'image visee par une designation parlee ou un chemin."""
    from tools.image import DOSSIER, _DERNIERE

    d = (designation or "").strip().strip('"')

    if d and Path(d).is_file():
        return Path(d)

    bas = d.lower()
    if not d or re.search(r"derniere?\s+(?:image|generation|creation)|celle",
                          bas):
        if _DERNIERE.get("chemin") and Path(_DERNIERE["chemin"]).exists():
            return Path(_DERNIERE["chemin"])
        faites = sorted(DOSSIER.glob("*.png"), key=lambda p: -p.stat().st_mtime) \
            if DOSSIER.is_dir() else []
        if faites:
            return faites[0]

    if re.search(r"\bphoto\b|\bma derniere\b|\bmes images\b|\btelechargee?\b", bas):
        return _image_recente()

    # Un nom partiel : on cherche dans les dossiers usuels et dans les notres.
    if len(d) >= 3:
        mots = [m for m in re.findall(r"\w{3,}", bas)]
        candidats = []
        for dossier in _dossiers_images() + [DOSSIER]:
            if not dossier.is_dir():
                continue
            for f in dossier.iterdir():
                try:
                    if f.suffix.lower() in _EXTENSIONS and all(
                            m in f.stem.lower() for m in mots):
                        candidats.append(f)
                except Exception:
                    continue
        if candidats:
            return max(candidats, key=lambda p: p.stat().st_mtime)

    return _image_recente()


@outil(
    nom="modifier_image",
    description=(
        "Reprend une image existante et la transforme selon une description. "
        "IMPORTANT : donne la description en ANGLAIS, comme pour generer_image. "
        "L'image peut etre 'la derniere' (celle que tu viens de fabriquer), "
        "'ma derniere photo', un bout de nom de fichier, ou un chemin complet. "
        "Pour 'transforme cette image en', 'refais ma photo en style'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "description": {"type": "string",
                            "description": "Ce que doit devenir l image, en anglais."},
            "image": {"type": "string",
                      "description": "Quelle image : 'la derniere', 'ma photo', un nom ou un chemin."},
            "force": {"type": "string",
                      "description": "legere, moyenne ou forte. Moyenne par defaut."},
            "ecran": {"type": "string",
                      "description": "Nom d un ecran pour l y envoyer."},
        },
        "required": ["description"],
    },
    lent=True,
    phrase_attente="Je reprends l image.",
)
def modifier_image(description: str, image: str = "", force: str = "",
                   ecran: str = "") -> str:
    from tools.image import (DOSSIER, _DERNIERE, _demarrer_moteur, _en_anglais,
                             _nom_de_fichier, envoyer_image_ecran)

    description = (description or "").strip()
    if not description:
        return "En quoi veux-tu la transformer ?"

    source = _trouver(image)
    if source is None or not Path(source).exists():
        return ("Je ne trouve pas l image. Dis-moi son nom, ou depose-la dans "
                "tes images et demande « ma derniere photo ».")

    description = _en_anglais(description)
    if not _demarrer_moteur():
        return "Le moteur d images ne repond pas."

    # Sous 0,4 on reteinte, au-dela de 0,7 il ne reste que la composition.
    forces = {"legere": 0.35, "leger": 0.35, "faible": 0.35,
              "moyenne": 0.55, "moyen": 0.55,
              "forte": 0.78, "fort": 0.78, "complete": 0.78}
    reprise = forces.get((force or "").lower(), 0.55)

    try:
        import httpx
        from PIL import Image as PILImage
        import io

        # On borne la taille : au-dela, la memoire graphique sature et le
        # moteur rend une image noire sans rien expliquer.
        img = PILImage.open(source).convert("RGB")
        cote = max(img.size)
        if cote > 1280:
            f = 1280 / cote
            img = img.resize((int(img.width * f), int(img.height * f)))
        tampon = io.BytesIO()
        img.save(tampon, format="PNG")
        encodee = base64.b64encode(tampon.getvalue()).decode()

        r = httpx.post(
            "http://127.0.0.1:7860/sdapi/v1/img2img",
            json={
                "init_images": [encodee],
                "prompt": description,
                "denoising_strength": reprise,
                "steps": 30,
                "cfg_scale": 6.0,
                "width": img.width,
                "height": img.height,
                "sampler_name": "DPM++ 2M",
                "scheduler": "Karras",
            },
            timeout=420,
        )
        r.raise_for_status()
        images = r.json().get("images") or []
    except Exception as e:
        return f"La reprise a echoue : {str(e)[:70]}"

    if not images:
        return "Le moteur n a rien renvoye."

    DOSSIER.mkdir(exist_ok=True)
    chemin = DOSSIER / _nom_de_fichier("reprise-" + description)
    chemin.write_bytes(base64.b64decode(images[0].split(",", 1)[-1]))
    _DERNIERE["chemin"] = chemin
    _DERNIERE["demande"] = description

    try:
        import hud
        hud.publier_image("/image/" + chemin.name, description)
    except Exception:
        pass

    origine = Path(source).name
    if ecran:
        return f"Repris depuis {origine}. {envoyer_image_ecran(ecran=ecran)}"

    try:
        import os
        os.startfile(str(chemin))
    except Exception:
        pass
    return f"Voila, repris depuis {origine}."


@outil(
    nom="derniere_image_trouvee",
    description=("Dit quelle image serait reprise si on demandait une "
                 "modification, sans rien modifier."),
    parametres={
        "type": "object",
        "properties": {
            "image": {"type": "string", "description": "Designation eventuelle."},
        },
        "required": [],
    },
)
def derniere_image_trouvee(image: str = "") -> str:
    trouvee = _trouver(image)
    if trouvee is None:
        return "Je ne trouve aucune image."
    return f"Je reprendrais {Path(trouvee).name}."
