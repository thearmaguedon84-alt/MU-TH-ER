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
import os
import re
import time
from pathlib import Path

from core.file_gpu import enfile
from core.registre import outil

_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _dossiers_images():
    """Endroits ou l'on range naturellement ses images."""
    import os
    profil = Path(os.environ.get("USERPROFILE", ""))
    return [profil / "Pictures", profil / "Downloads", profil / "Desktop",
            profil / "Images", profil / "Téléchargements", profil / "Bureau"]


def _est_creation(chemin):
    """Un fichier fabrique par MU-TH-UR, par opposition aux affaires perso."""
    from core.dossiers import est_une_creation
    return est_une_creation(chemin)


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
                # « ma derniere photo » designe une photo a soi. Les creations
                # de MU-TH-UR vivent maintenant dans le meme arbre : il faut
                # les ecarter, sinon les deux notions se confondent.
                if _est_creation(f):
                    continue
                if (f.suffix.lower() in _EXTENSIONS and f.is_file()
                        and f.stat().st_mtime > max(date, limite)):
                    meilleure, date = f, f.stat().st_mtime
            except Exception:
                continue
    return meilleure


def _chercher_partout(mots, dossiers, profondeur=3, plafond=40000):
    """Cherche un fichier par bouts de nom, sous-dossiers compris.

    Les photos ne vivent pas a la racine de « Images » mais dans des dossiers
    par annee ou par evenement. Ne regarder que le premier niveau revenait a
    ne jamais trouver ce qu on nomme.
    """
    trouves = []
    vus = 0
    for base in dossiers:
        if not base.is_dir():
            continue
        for chemin, sous, fichiers in os.walk(base):
            # On ne descend pas indefiniment, et on evite les dossiers caches.
            niveau = len(Path(chemin).relative_to(base).parts)
            if niveau >= profondeur:
                sous[:] = []
            sous[:] = [s for s in sous if not s.startswith(".")]
            for f in fichiers:
                vus += 1
                if vus > plafond:
                    return trouves
                if not f.lower().endswith(_EXTENSIONS):
                    continue
                bas = f.lower()
                if all(m in bas for m in mots):
                    trouves.append(Path(chemin) / f)
    return trouves


def _trouver(designation):
    """Retrouve l'image visee par une designation parlee ou un chemin."""
    from tools.image import DOSSIER, _DERNIERE

    d = (designation or "").strip().strip('"')

    if d and Path(d).is_file():
        return Path(d)

    bas = d.lower()
    if not d or re.search(r"derniere?\s+(?:image|generation|creation)|celle"
                          r"|que\s+tu\s+viens\s+de|cette\s+image"
                          r"|^l\s*image$|^image$",
                          bas):
        if _DERNIERE.get("chemin") and Path(_DERNIERE["chemin"]).exists():
            return Path(_DERNIERE["chemin"])
        faites = sorted(DOSSIER.glob("*.png"), key=lambda p: -p.stat().st_mtime) \
            if DOSSIER.is_dir() else []
        if faites:
            return faites[0]

    if re.search(r"\bphoto\b|\bma derniere\b|\bmes images\b|\btelechargee?\b", bas):
        return _image_recente()

    # Un nom partiel : on cherche partout, sous-dossiers compris. Les photos
    # ne vivent pas a la racine d Images mais dans des dossiers par annee.
    if len(d) >= 3:
        mots = [m for m in re.findall(r"\w{2,}", bas)
                if m not in ("jpg", "jpeg", "png", "webp", "bmp", "photo",
                             "image", "fichier", "partir")]
        if mots:
            candidats = _chercher_partout(mots, _dossiers_images() + [DOSSIER])
            if candidats:
                return max(candidats, key=lambda p: p.stat().st_mtime)

    # Une designation qui ressemble a un nom de fichier et qu on n a pas su
    # resoudre ne doit pas retomber sur autre chose : l utilisateur croirait
    # avoir ete entendu. C est le defaut qui a le plus coute en confiance.
    if d and re.search(r"\d{3,}", bas):
        raise LookupError(d[:70])

    # Dernier recours. Il ne doit JAMAIS ramener une photo personnelle : c est
    # ainsi qu une demande mal comprise finissait par retoucher indefiniment
    # la meme photo de famille. En cas de doute, on prefere ne rien trouver et
    # le dire, plutot que de toucher a ce qui n a pas ete demande.
    if _DERNIERE.get("chemin") and Path(_DERNIERE["chemin"]).exists():
        return Path(_DERNIERE["chemin"])
    faites = sorted(DOSSIER.glob("*.png"), key=lambda p: -p.stat().st_mtime) \
        if DOSSIER.is_dir() else []
    if faites:
        return faites[0]
    # Une photo personnelle seulement si la demande la designait vraiment.
    if re.search(r"\bma\b|\bmes\b|\bphoto\b", bas):
        return _image_recente()
    return None


# Une consigne n est pas une description. « Enleve la tete de la dame et mets
# une tete de poule » ne veut rien dire pour un moteur de diffusion : il faut
# lui decrire l image d arrivee, « une femme a tete de poule, bras leves ».
# Le modele local fait cette reformulation en une phrase.
RE_CONSIGNE = re.compile(
    r"\b(?:enleve|enlever|retire|retirer|remplace|remplacer|ajoute|ajouter|"
    r"mets|mettre|met|supprime|supprimer|change|changer)\b", re.I)


def _decrire_resultat(consigne, source=None):
    """Transforme une consigne de retouche en description de l image voulue."""
    from tools.image import _en_anglais
    from core.config import reglage

    if not RE_CONSIGNE.search(consigne or ""):
        return _en_anglais(consigne)

    try:
        import httpx
        hote = reglage("ollama.hote", "http://127.0.0.1:11434")
        r = httpx.post(
            f"{hote}/api/generate",
            json={
                "model": reglage("ollama.modele", "qwen2.5:7b"),
                "prompt": (
                    "An image is going to be edited. Below is the edit "
                    "instruction, in French. Write, in English, a short "
                    "caption describing the IMAGE AFTER the edit \u2014 not the "
                    "instruction itself. One sentence, no quotes, no "
                    "explanation.\n\nInstruction: " + consigne),
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=60,
        )
        decrite = (r.json().get("response") or "").strip().strip('"')
        if 3 < len(decrite) < 400:
            return decrite
    except Exception:
        pass
    return _en_anglais(consigne)


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
            "par_mail": {"type": "boolean",
                         "description": "Envoyer le resultat par mail."},
        },
        "required": ["description"],
    },
    lent=True,
    phrase_attente="Je reprends l image.",
)
@enfile("image", "description")
def modifier_image(description: str, image: str = "", force: str = "",
                   ecran: str = "", par_mail: bool = False) -> str:
    from tools.image import (DOSSIER, _DERNIERE, _demarrer_moteur, _en_anglais,
                             _envoyer_par_mail, _liberer_vram, _nom_de_fichier,
                             envoyer_image_ecran)

    description = (description or "").strip()
    if not description:
        return "En quoi veux-tu la transformer ?"

    source = _trouver(image)
    if source is None or not Path(source).exists():
        return ("Je ne trouve pas l image. Dis-moi son nom, ou depose-la dans "
                "tes images et demande « ma derniere photo ».")

    description = _decrire_resultat(description, source)
    _liberer_vram()
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

    mail = ""
    if par_mail:
        mail = " " + _envoyer_par_mail(chemin, description)

    origine = Path(source).name
    if ecran:
        return (f"Repris depuis {origine}. "
                f"{envoyer_image_ecran(ecran=ecran)}{mail}")

    try:
        import os
        os.startfile(str(chemin))
    except Exception:
        pass
    return f"Voila, repris depuis {origine}.{mail}"


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
