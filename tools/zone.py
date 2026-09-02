"""Remplacer une partie d'une image, et elle seule.

Reprendre l'image entiere pour changer une tete ne marche pas : le moteur
redessine tout, le decor derive, et l'ancienne tete reste en transparence sous
la nouvelle. C'est ce qu'on a obtenu au premier essai.

La bonne methode est le detourage : reperer la zone, ne redessiner qu'elle,
recoller. ADetailer sait faire exactement cela, et ses detecteurs sont deja
installes. On lui confie donc le travail, en demandant au passage a la reprise
globale de ne rien changer (force 0,05), pour que seule la zone visee bouge.
"""
import base64
import io
import re
import time
from pathlib import Path

from core.file_gpu import enfile
from core.registre import outil

# Ce que l'on sait detecter, et par quel detecteur.
ZONES = {
    "visage": "face_yolov8s.pt",
    "tete": "face_yolov8s.pt",
    "figure": "face_yolov8s.pt",
    "face": "face_yolov8s.pt",
    "main": "hand_yolov8n.pt",
    "mains": "hand_yolov8n.pt",
    "personne": "person_yolov8n-seg.pt",
    "silhouette": "person_yolov8n-seg.pt",
    "corps": "person_yolov8n-seg.pt",
}


def _detecteur(zone):
    z = (zone or "").lower()
    for mot, modele in ZONES.items():
        if mot in z:
            return modele
    return "face_yolov8s.pt"


@outil(
    nom="remplacer_zone",
    description=(
        "Remplace UNE PARTIE d'une image sans toucher au reste : la tete, le "
        "visage, les mains. Pour 'remplace la tete par une tete de poule', "
        "'mets un visage de chat a la place'. IMPORTANT : donne 'par' en "
        "ANGLAIS. Prefere cet outil a modifier_image des qu'il s'agit d'une "
        "partie precise plutot que de toute l'image."
    ),
    parametres={
        "type": "object",
        "properties": {
            "par": {"type": "string",
                    "description": "Ce qui doit apparaitre a la place, en anglais."},
            "zone": {"type": "string",
                     "description": "tete, visage, mains ou personne."},
            "image": {"type": "string",
                      "description": "Quelle image : 'la derniere', 'ma photo', un nom."},
            "ampleur": {"type": "string",
                        "description": "large, normal ou serre. Elargit la zone reprise."},
            "ecran": {"type": "string", "description": "Nom d un ecran."},
            "par_mail": {"type": "boolean", "description": "Envoyer par mail."},
        },
        "required": ["par"],
    },
    lent=True,
    phrase_attente="Je remplace la zone.",
)
@enfile("image", "par")
def remplacer_zone(par: str, zone: str = "tete", image: str = "",
                   ampleur: str = "", ecran: str = "",
                   par_mail: bool = False) -> str:
    from tools.image import (DOSSIER, _DERNIERE, _demarrer_moteur, _en_anglais,
                             _envoyer_par_mail, _liberer_vram, _nom_de_fichier,
                             envoyer_image_ecran, _adetailer_dispo, ADRESSE)
    from tools.modifier_image import _trouver

    par = (par or "").strip()
    if not par:
        return "Par quoi veux-tu que je la remplace ?"

    source = _trouver(image)
    if source is None or not Path(source).exists():
        return "Je ne trouve pas l image."

    par = _en_anglais(par)
    _liberer_vram()
    if not _demarrer_moteur():
        return "Le moteur d images ne repond pas."
    if not _adetailer_dispo():
        return ("Le detourage n est pas disponible : l extension de retouche "
                "n est pas chargee.")

    # Sur une tete, il faut mordre franchement au-dela du visage ; sur une
    # main, deborder autant deborderait sur le bras.
    dilatation = {"tete": 78, "visage": 56, "mains": 24,
                  "main": 24, "personne": 32}.get((zone or "tete").lower(), 64)
    if ampleur:
        a = ampleur.lower()
        if re.search(r"large|grand|beaucoup|toute", a):
            dilatation = int(dilatation * 1.6)
        elif re.search(r"petit|serre|juste|peu", a):
            dilatation = int(dilatation * 0.5)

    # On dit explicitement ce qu on ne veut plus voir : sans cela, la
    # chevelure d origine reapparait autour de la nouvelle tete.
    negatif = "blurry, deformed, doubled, disfigured, extra head"
    if (zone or "").lower() in ("tete", "visage", "figure", "face"):
        negatif += (", human hair, human face, human ears, hair strands, "
                    # Le moteur redresse et tourne la tete a sa guise : il
                    # faut lui interdire explicitement les trois-quarts.
                    "profile view, side view, three-quarter view, turned "
                    "head, tilted head, looking away, oversized head, "
                    "giant head, head too large")
        if not re.search(r"\bhead\b|\bmask\b", par.lower()):
            par = par + " head"
        par += (", full head replacing the human head, no human hair, "
                # La tete doit suivre le corps : de face si le corps est de
                # face. Sans le dire, le moteur la fait pivoter.
                "front view facing the camera directly, head upright and "
                "squarely aligned with the shoulders, symmetrical, "
                "same size as a human head, proportionate to the neck and "
                "body, seamless neck")

    try:
        import httpx
        from PIL import Image as PILImage

        img = PILImage.open(source).convert("RGB")
        cote = max(img.size)
        if cote > 1280:
            f = 1280 / cote
            img = img.resize((int(img.width * f), int(img.height * f)))
        tampon = io.BytesIO()
        img.save(tampon, format="PNG")
        encodee = base64.b64encode(tampon.getvalue()).decode()

        charge = {
            "init_images": [encodee],
            "prompt": par,
            # Presque zero : la passe globale ne doit rien changer. Tout le
            # travail se fait dans la zone detectee.
            "denoising_strength": 0.05,
            "steps": 20,
            "cfg_scale": 6.0,
            "width": img.width,
            "height": img.height,
            "sampler_name": "DPM++ 2M",
            "scheduler": "Karras",
            "alwayson_scripts": {
                "ADetailer": {
                    "args": [
                        True,
                        False,          # ne pas sauter les reprises d image
                        {
                            "ad_model": _detecteur(zone),
                            "ad_prompt": par,
                            "ad_negative_prompt": negatif,
                            "ad_confidence": 0.25,
                            # Eleve : on veut vraiment autre chose a la place.
                            "ad_denoising_strength": 0.95,
                            "ad_inpaint_only_masked": True,
                            # Large : le moteur a besoin de voir les epaules
                            # pour donner a la nouvelle tete la bonne taille.
                            "ad_inpaint_only_masked_padding": 128,
                            "ad_mask_blur": 20,
                            # Le detecteur ne cadre que le visage. Sans cette
                            # dilatation, les cheveux restent autour de la
                            # nouvelle tete et la trahissent.
                            "ad_dilate_erode": dilatation,
                            "ad_use_inpaint_width_height": True,
                            "ad_inpaint_width": 1024,
                            "ad_inpaint_height": 1024,
                            "ad_use_steps": True,
                            "ad_steps": 36,
                            "ad_use_cfg_scale": True,
                            "ad_cfg_scale": 7.0,
                        },
                    ]
                }
            },
        }
        r = httpx.post(f"{ADRESSE}/sdapi/v1/img2img", json=charge, timeout=900)
        r.raise_for_status()
        images = r.json().get("images") or []
    except Exception as e:
        return f"Le remplacement a echoue : {str(e)[:80]}"

    if not images:
        return "Le moteur n a rien renvoye."

    DOSSIER.mkdir(exist_ok=True)
    chemin = DOSSIER / _nom_de_fichier("zone-" + par)
    chemin.write_bytes(base64.b64decode(images[0].split(",", 1)[-1]))
    _DERNIERE["chemin"] = chemin
    _DERNIERE["demande"] = par

    try:
        import hud
        hud.publier_image("/image/" + chemin.name, par)
    except Exception:
        pass

    mail = " " + _envoyer_par_mail(chemin, par) if par_mail else ""
    origine = Path(source).name
    if ecran:
        return (f"Remplace sur {origine}. "
                f"{envoyer_image_ecran(ecran=ecran)}{mail}")
    try:
        import os
        os.startfile(str(chemin))
    except Exception:
        pass
    return f"Voila, seule la zone a change sur {origine}.{mail}"
