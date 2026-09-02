"""Fabriquer de la musique, en local.

Le moteur est ACE-Step 1.5, lance a la demande sur le port 8001. Il travaille
en deux temps : on depose une tache, puis on interroge son avancement. Ce
fonctionnement en file d'attente est le sien ; on s'y plie.

Trois choix de conception :

- **Le style est traduit, les paroles non.** Le modele attend des etiquettes de
  genre en anglais (« french chanson, accordion, waltz ») mais chante dans la
  langue qu'on lui donne. Traduire les paroles reviendrait a changer la
  chanson.
- **La memoire graphique est liberee avant.** Douze giga-octets ne suffisent
  pas a loger en meme temps le modele de langage, le moteur d'images et celui
  de musique. On decharge les deux autres ; ils se rechargent tout seuls.
- **Le modele turbo par defaut.** Huit etapes au lieu de cinquante. Sur une
  3060, c'est la difference entre une minute et un quart d'heure.
"""
import json
import re
import subprocess
import time
from pathlib import Path

from core.config import reglage
from core.file_gpu import enfile
from core.registre import outil

from core.dossiers import dossier

RACINE = Path(__file__).resolve().parent.parent
DOSSIER = dossier("musiques")
ADRESSE = "http://127.0.0.1:8001"

_DERNIERE = {"chemin": None, "demande": None}


def _repond(delai=3):
    try:
        import httpx
        return httpx.get(f"{ADRESSE}/health", timeout=delai).status_code == 200
    except Exception:
        return False


def _demarrer(patience=300):
    """Lance le serveur de musique s il ne tourne pas."""
    if _repond():
        return True
    dossier = Path(reglage("musique.moteur", r"F:\IA\acestep"))
    lanceur = dossier / "start_api_server.bat"
    if not lanceur.exists():
        return False
    try:
        subprocess.Popen(["cmd", "/c", str(lanceur)], cwd=str(dossier),
                         creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        return False
    debut = time.time()
    while time.time() - debut < patience:
        time.sleep(4)
        if _repond():
            return True
    return False


def _liberer_gpu():
    """Fait de la place. Les autres moteurs se rechargeront a leur tour."""
    try:
        from core.vram import liberer
        return liberer(pour="musique")
    except Exception:
        return []


def _style_en_anglais(texte):
    """Traduit la description de style, pas les paroles."""
    from tools.image import _en_anglais
    return _en_anglais(texte)


def _instrumental(texte):
    return bool(re.search(r"\binstrumental\b|\bsans (?:parole|voix|chant)\b|"
                          r"\bmusique seule\b", (texte or "").lower()))


@outil(
    nom="generer_musique",
    description=(
        "Compose un morceau de musique avec le moteur local. Donne le STYLE en "
        "anglais sous forme d'etiquettes ('french chanson, accordion, waltz, "
        "warm vocals'), mais laisse les PAROLES dans la langue voulue. Pour "
        "'fais-moi une chanson', 'compose un morceau', 'genere une musique'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "style": {"type": "string",
                      "description": "Genre et ambiance, en anglais, separes par des virgules."},
            "paroles": {"type": "string",
                        "description": "Les paroles, dans leur langue. Vide = instrumental."},
            "duree": {"type": "integer",
                      "description": "Duree en secondes. 60 par defaut, 240 au plus."},
            "langue": {"type": "string",
                       "description": "Langue des paroles : fr, en, es... fr par defaut."},
            "ecran": {"type": "string", "description": "Nom d un ecran pour l y jouer."},
        },
        "required": ["style"],
    },
    lent=True,
    phrase_attente="Je compose le morceau.",
)
@enfile("musique", "style")
def generer_musique(style: str, paroles: str = "", duree: int = 60,
                    langue: str = "fr", ecran: str = "") -> str:
    style = (style or "").strip()
    if not style:
        return "Quel style de musique veux-tu ?"

    if not _demarrer():
        return ("Le moteur de musique ne repond pas. Verifie le chemin dans "
                "les reglages.")
    _liberer_gpu()

    style = _style_en_anglais(style)
    duree = max(15, min(int(duree or 60), 240))
    instrumental = not (paroles or "").strip()

    charge = {
        "prompt": style,
        "lyrics": "[instrumental]" if instrumental else paroles.strip(),
        "vocal_language": (langue or "fr")[:2],
        "audio_duration": duree,
        "audio_format": "mp3",
        "model": reglage("musique.modele", "acestep-v15-turbo"),
        "inference_steps": int(reglage("musique.etapes", 8)),
        # Le petit modele de langage remplit ce qui manque : tempo, tonalite,
        # structure. Sans lui le resultat est plat.
        "thinking": True,
    }

    try:
        import httpx
        r = httpx.post(f"{ADRESSE}/release_task", json=charge, timeout=120)
        r.raise_for_status()
        tache = ((r.json() or {}).get("data") or {}).get("task_id")
    except Exception as e:
        return f"La composition n a pas demarre : {str(e)[:70]}"
    if not tache:
        return "Le moteur n a pas accepte la demande."

    # Premiere demande : le moteur telecharge puis charge ses modeles, ce qui
    # prend une bonne demi-heure. Les suivantes sont en minutes. On accorde
    # donc large, et on ne s inquiete que si rien ne bouge du tout.
    patience = int(reglage("musique.patience", 3600))
    fichier, debut = None, time.time()
    while time.time() - debut < patience:
        time.sleep(6)
        try:
            q = httpx.post(f"{ADRESSE}/query_result",
                           json={"task_id_list": [tache]}, timeout=60)
            lot = (q.json() or {}).get("data") or []
            if not lot:
                continue
            etat = lot[0].get("status")
            if etat == 2:
                return "La composition a echoue."
            if etat == 1:
                brut = lot[0].get("result") or "[]"
                morceaux = json.loads(brut) if isinstance(brut, str) else brut
                if morceaux:
                    fichier = morceaux[0].get("file")
                break
        except Exception:
            continue

    if not fichier:
        return "Le morceau n est pas arrive dans le temps imparti."

    try:
        url = fichier if fichier.startswith("http") else ADRESSE + fichier
        audio = httpx.get(url, timeout=180).content
    except Exception as e:
        return f"Le telechargement a echoue : {str(e)[:60]}"

    DOSSIER.mkdir(exist_ok=True)
    propre = re.sub(r"[^a-z0-9]+", "-", style.lower())[:44].strip("-")
    chemin = DOSSIER / f"{time.strftime('%Y%m%d-%H%M%S')}-{propre}.mp3"
    chemin.write_bytes(audio)
    _DERNIERE["chemin"] = chemin
    _DERNIERE["demande"] = style

    if ecran:
        return f"Voila. {envoyer_musique_ecran(ecran=ecran)}"
    try:
        import os
        os.startfile(str(chemin))
    except Exception:
        pass
    quoi = "ton instrumental" if instrumental else "ta chanson"
    return f"Voila {quoi}, {duree} secondes."


@outil(
    nom="envoyer_musique_ecran",
    description=("Joue le dernier morceau compose sur une enceinte ou une "
                 "television. Pour 'mets-le sur la tele'."),
    parametres={
        "type": "object",
        "properties": {
            "ecran": {"type": "string", "description": "Nom de l appareil."},
        },
        "required": ["ecran"],
    },
    lent=True,
    phrase_attente="Je l envoie.",
)
def envoyer_musique_ecran(ecran: str) -> str:
    chemin = _DERNIERE.get("chemin")
    if not chemin or not Path(chemin).exists():
        return "Je n ai pas de morceau sous la main."

    from tools.cast import _adresse_pour, _choisir
    appareil = _choisir(ecran)
    if appareil is None:
        return f"Je ne trouve pas {ecran}."

    # Le serveur de Jarvis sert deja les fichiers : on lui emprunte sa route.
    port = int(reglage("hud.port", 8770))
    adresse = _adresse_pour(appareil.cast_info.host)
    url = f"http://{adresse}:{port}/musique/{Path(chemin).name}"
    try:
        appareil.wait(timeout=12)
        appareil.media_controller.play_media(url, "audio/mpeg")
        appareil.media_controller.block_until_active(timeout=20)
    except Exception as e:
        return f"L envoi a echoue : {str(e)[:60]}"
    return f"Ca joue sur {appareil.cast_info.friendly_name}."


@outil(
    nom="musiques_recentes",
    description="Dit combien de morceaux ont ete composes et ou ils sont.",
    parametres={"type": "object", "properties": {}, "required": []},
)
def musiques_recentes() -> str:
    if not DOSSIER.is_dir():
        return "Je n ai encore compose aucun morceau."
    f = sorted(DOSSIER.glob("*.mp3"), key=lambda p: -p.stat().st_mtime)
    if not f:
        return "Je n ai encore compose aucun morceau."
    return (f"{len(f)} morceaux dans le dossier musiques. Le dernier : "
            f"{f[0].stem.split('-', 2)[-1].replace('-', ' ')}.")
