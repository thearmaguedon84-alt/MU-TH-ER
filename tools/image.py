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
from core.file_gpu import enfile
from core.registre import outil

from core.dossiers import dossier

RACINE = Path(__file__).resolve().parent.parent
DOSSIER = dossier("images")
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

    # Le paquet tout-en-un se lance par run.bat, qui charge d abord son propre
    # Python. Appeler le lanceur interne recreerait un environnement et
    # tenterait de tout reinstaller.
    dossier = reglage("images.forge", r"F:\IA\forge_prete")
    lanceur = Path(dossier) / "run.bat"
    if not lanceur.exists():
        return False
    try:
        subprocess.Popen(["cmd", "/c", "run.bat"], cwd=str(dossier),
                         creationflags=subprocess.CREATE_NO_WINDOW)
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

def _liberer_vram():
    """Delegue a l arbitre, qui sait ce que les autres moteurs occupent."""
    try:
        from core.vram import liberer
        return liberer(pour="image")
    except Exception:
        pass
    return _liberer_vram_ancien()


def _liberer_vram_ancien():
    """Decharge les modeles d Ollama avant une generation.

    Les douze giga-octets de la carte sont partages entre le modele de langage
    et le modele d images. Quand les deux y tiennent de force, le moteur
    deborde sur la memoire vive et passe de 1,4 image par seconde a une image
    toutes les neuf secondes — six fois plus lent, sans rien dire.

    Ollama rechargera son modele a la prochaine phrase, en quelques secondes.
    """
    try:
        import httpx
        r = httpx.get("http://127.0.0.1:11434/api/ps", timeout=4)
        for m in (r.json() or {}).get("models") or []:
            nom = m.get("name") or m.get("model")
            if nom:
                httpx.post("http://127.0.0.1:11434/api/generate",
                           json={"model": nom, "keep_alive": 0}, timeout=20)
    except Exception:
        pass


# Ce qu on ne veut jamais voir. Un moteur de diffusion n a aucune notion de ce
# qui est rate ; il faut le lui dire. L absence de cette liste explique une
# bonne part des images ou les objets se fondent les uns dans les autres.
NEGATIF = ("deformed, disfigured, malformed, mutated, fused together, merged "
           "objects, extra limbs, extra heads, duplicate, cloned, bad "
           "anatomy, melting, blurry, low quality, jpeg artifacts, "
           "watermark, signature, text")


def _nom_de_fichier(demande):
    propre = re.sub(r"[^a-z0-9]+", "-", (demande or "image").lower())[:48]
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{propre.strip('-')}.png"


# ------------------------------------------------------- retouche des mains

# Un modele de diffusion dessine tout a l echelle de l image entiere : sur un
# personnage en pied, une main occupe quelques dizaines de pixels — bien trop
# peu pour cinq doigts credibles. D ou les mains ratees, defaut d origine de
# ces modeles et non de la demande.
#
# La correction consiste a reperer les mains apres coup, a les recadrer en
# pleine resolution, a les redessiner, puis a les recoller. C est ce que fait
# ADetailer, et c est la seule methode qui marche vraiment.

_ADETAILER = {"dispo": None}


def _adetailer_dispo():
    """Une seule verification par session : la reponse ne change pas."""
    if _ADETAILER["dispo"] is None:
        try:
            import httpx
            r = httpx.get(f"{ADRESSE}/sdapi/v1/scripts", timeout=8)
            noms = (r.json() or {}).get("txt2img") or []
            _ADETAILER["dispo"] = any("adetailer" in str(n).lower()
                                      for n in noms)
        except Exception:
            _ADETAILER["dispo"] = False
    return _ADETAILER["dispo"]


def _retouche(mains=True, visages=True):
    """Reglages ADetailer. Le recadrage a 512 est ce qui change tout."""
    unites = []
    if visages:
        unites.append({
            "ad_model": "face_yolov8s.pt",
            "ad_confidence": 0.3,
            # Assez pour nettoyer les traits, pas assez pour changer le visage.
            "ad_denoising_strength": 0.4,
            "ad_inpaint_only_masked": True,
            "ad_inpaint_only_masked_padding": 32,
            "ad_use_inpaint_width_height": True,
            "ad_inpaint_width": 512,
            "ad_inpaint_height": 512,
        })
    if mains:
        unites.append({
            "ad_model": "hand_yolov8n.pt",
            # Une main mal formee se detecte mal : on abaisse le seuil, quitte
            # a retoucher une fois de trop.
            "ad_confidence": 0.25,
            "ad_denoising_strength": 0.5,
            "ad_prompt": "detailed hand, five fingers, correct anatomy",
            "ad_negative_prompt": ("deformed hand, extra fingers, fused "
                                   "fingers, missing fingers, mutated"),
            "ad_inpaint_only_masked": True,
            "ad_inpaint_only_masked_padding": 32,
            "ad_use_inpaint_width_height": True,
            "ad_inpaint_width": 512,
            "ad_inpaint_height": 512,
        })
    if not unites:
        return None
    return {"ADetailer": {"args": [True, False] + unites}}


def _modele_actuel():
    try:
        import httpx
        r = httpx.get(f"{ADRESSE}/sdapi/v1/options", timeout=8)
        return (r.json() or {}).get("sd_model_checkpoint") or ""
    except Exception:
        return ""


def modeles_disponibles():
    """Liste des modeles installes.

    Forge repond parfois 500 sur /sd-models alors que tout va bien par
    ailleurs. Le dossier, lui, ne ment jamais : on s en sert en secours.
    """
    try:
        import httpx
        r = httpx.get(f"{ADRESSE}/sdapi/v1/sd-models", timeout=15)
        if r.status_code == 200:
            noms = [m.get("model_name") or m.get("title") for m in r.json()]
            if noms:
                return noms
    except Exception:
        pass
    dossier = Path(reglage("images.forge", "")) / "webui" / "models" / "Stable-diffusion"
    if not dossier.is_dir():
        return []
    return sorted(p.name for p in dossier.glob("*.safetensors"))


def utiliser_modele(fragment):
    """Bascule de modele. Le chargement prend une trentaine de secondes."""
    fragment = (fragment or "").strip().lower()
    if not fragment:
        return False
    if fragment in (_modele_actuel() or "").lower():
        return True
    for nom in modeles_disponibles():
        if nom and fragment in nom.lower():
            try:
                import httpx
                httpx.post(f"{ADRESSE}/sdapi/v1/options",
                           json={"sd_model_checkpoint": nom}, timeout=300)
                return True
            except Exception:
                return False
    return False


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
            "par_mail": {
                "type": "boolean",
                "description": "Envoyer l image par mail a l utilisateur.",
            },
            "soigner": {
                "type": "boolean",
                "description": ("Retoucher mains et visages apres coup. Vrai "
                                "par defaut ; faux si on demande d aller "
                                "vite ou sans retouche."),
            },
        },
        "required": ["description"],
    },
    lent=True,
    phrase_attente="Je fabrique l image.",
)
@enfile("image", "description")
def generer_image(description: str, format: str = "", ecran: str = "",
                  par_mail: bool = False, soigner: bool = True) -> str:
    description = (description or "").strip()
    if not description:
        return "Que veux-tu que je represente ?"

    description = _en_anglais(description)

    if not _demarrer_moteur():
        return ("Le moteur d images ne repond pas. Verifie qu il est installe "
                "et que le chemin est bon dans les reglages.")

    tailles = {"portrait": (832, 1216), "paysage": (1216, 832)}
    largeur, hauteur = tailles.get((format or "").lower(), (1024, 1024))

    _liberer_vram()

    modele = reglage("images.modele", "")
    if modele:
        utiliser_modele(modele)

    charge = {
        "prompt": description,
        "negative_prompt": reglage("images.negatif", NEGATIF),
        "steps": int(reglage("images.etapes", 28)),
        "cfg_scale": float(reglage("images.guidage", 5.5)),
        "width": largeur,
        "height": hauteur,
        "sampler_name": reglage("images.echantillonneur", "DPM++ 2M"),
        "scheduler": "Karras",
    }
    if soigner and reglage("images.soigner", True) and _adetailer_dispo():
        greffe = _retouche()
        if greffe:
            charge["alwayson_scripts"] = greffe

    try:
        import httpx
        r = httpx.post(f"{ADRESSE}/sdapi/v1/txt2img", json=charge, timeout=600)
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

    mail = ""
    if par_mail or reglage("images.mail_auto", False):
        mail = " " + _envoyer_par_mail(chemin, description)

    if ecran:
        envoi = envoyer_image_ecran(ecran=ecran)
        return f"Voila. {envoi}{mail}"

    try:
        os.startfile(str(chemin))
    except Exception:
        pass
    return f"Voila ton image.{mail}"


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


# --------------------------------------------------------------- envoi par mail

# Une image envoyee par mail quitte la machine : elle transite par Gmail et y
# reste. C'est le seul endroit du parcours ou cela arrive, et c'est un choix
# explicite de l'utilisateur, jamais un comportement par defaut.

_TYPES = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg",
          ".webp": "webp", ".gif": "gif", ".bmp": "bmp"}


def _envoyer_par_mail(chemin, description="", destinataire=""):
    """Envoie une image en piece jointe. Sans destinataire : a soi-meme."""
    from tools import mail as messagerie

    if not messagerie._mail_configure():
        return "La messagerie n est pas configuree, je ne l ai pas envoyee."

    chemin = Path(chemin)
    if not chemin.exists():
        return "Je n ai pas l image sous la main."

    adresse = (destinataire or "").strip() or messagerie.MAIL_ADRESSE
    sujet = " ".join((description or chemin.stem).split())[:70] or "Image"

    try:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = messagerie.MAIL_ADRESSE
        msg["To"] = adresse
        msg["Subject"] = sujet
        msg.set_content(
            f"Image fabriquee en local par Jarvis.\n\n"
            f"Demande : {description or '(non precisee)'}\n"
            f"Fichier : {chemin.name}\n")
        msg.add_attachment(chemin.read_bytes(), maintype="image",
                           subtype=_TYPES.get(chemin.suffix.lower(), "png"),
                           filename=chemin.name)
        with smtplib.SMTP_SSL(messagerie.SMTP_SERVEUR, messagerie.SMTP_PORT) as smtp:
            smtp.login(messagerie.MAIL_ADRESSE, messagerie._mail_mdp())
            smtp.send_message(msg)
    except Exception as e:
        return f"L envoi par mail a echoue : {str(e)[:60]}"

    if destinataire:
        return f"Envoyee a {adresse}."
    return "Je te l ai envoyee par mail."


def _annonce_envoi_image(args):
    qui = (args or {}).get("destinataire") or "toi-meme"
    return f"Je vais envoyer l image par mail a {qui}."


@outil(
    nom="envoyer_image_mail",
    description=("Envoie par mail, en piece jointe, la derniere image "
                 "fabriquee. Sans destinataire, elle part vers l adresse de "
                 "l utilisateur. Pour « envoie-la moi par mail »."),
    parametres={
        "type": "object",
        "properties": {
            "destinataire": {
                "type": "string",
                "description": "Adresse du destinataire. Vide = a soi-meme.",
            },
        },
        "required": [],
    },
    confirmation=True,
    annonce=_annonce_envoi_image,
    lent=True,
    phrase_attente="J envoie le mail.",
)
def envoyer_image_mail(destinataire: str = "") -> str:
    chemin = _DERNIERE.get("chemin")
    if not chemin or not Path(chemin).exists():
        return "Je n ai pas d image sous la main. Demande-m en une d abord."
    return _envoyer_par_mail(chemin, _DERNIERE.get("demande") or "",
                             destinataire)


def envoyer_derniere_a_soi():
    """Raccourci : la derniere image, a soi-meme, sans confirmation."""
    chemin = _DERNIERE.get("chemin")
    if not chemin or not Path(chemin).exists():
        return "Je n ai pas d image sous la main."
    return _envoyer_par_mail(chemin, _DERNIERE.get("demande") or "")

@outil(
    nom="refaire_image",
    description=("Relance la derniere demande d image a l identique, avec un "
                 "autre tirage. Pour « refais-la », « une autre version », "
                 "« encore une ». La diffusion est une loterie : relancer est "
                 "souvent plus efficace que reformuler."),
    parametres={
        "type": "object",
        "properties": {
            "ecran": {"type": "string", "description": "Nom d un ecran."},
        },
        "required": [],
    },
    lent=True,
    phrase_attente="J en refais une.",
)
def refaire_image(ecran: str = "") -> str:
    demande = _DERNIERE.get("demande")
    if not demande:
        return "Je n ai pas de demande precedente sous la main."
    return generer_image(description=demande, ecran=ecran)
