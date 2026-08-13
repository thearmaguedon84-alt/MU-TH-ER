import ctypes
import glob
import os
import subprocess
import time
import webbrowser
from pathlib import Path

from core.registre import outil

# Codes des touches multimedia Windows
_TOUCHES = {
    "muet": 0xAD,
    "baisser": 0xAE,
    "monter": 0xAF,
    "suivant": 0xB0,
    "precedent": 0xB1,
    "pause": 0xB3,
}

# ---------------------------------------------------------------------------
# Recherche d'applications dans le système


# Mots qui indiquent un raccourci secondaire (à éviter)
_MOTS_SECONDAIRES = (
    "uninstall", "desinstall", "reset", "repair", "help", "aide",
    "readme", "website", "site web", "skinned", "changelog", "update",
    "configuration", "config", "settings", "support", "community",
)


def _score_lnk(stem, cible):
    """Score un raccourci : plus c'est bas, mieux c'est."""
    s = stem.lower()
    # Pénalité si contient des mots secondaires
    penalite = sum(10 for m in _MOTS_SECONDAIRES if m in s)
    # Bonus si le stem commence par la cible
    debut = 0 if s.startswith(cible) else 5
    # Bonus si le stem est court (raccourci principal généralement plus court)
    longueur = len(stem)
    return penalite + debut + longueur


def _chercher_app_windows(nom):
    """
    Cherche un .exe ou .lnk correspondant a `nom` dans :
    - Menu Démarrer (utilisateur + global)
    - Program Files, Program Files (x86)
    - LocalAppData/Programs
    Renvoie le chemin trouvé (str) ou None.
    """
    from core.util import sans_accents
    cible = sans_accents(nom.lower().strip())

    dossiers_start = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path("C:/ProgramData/Microsoft/Windows/Start Menu/Programs"),
    ]
    dossiers_install = [
        Path("C:/Program Files"),
        Path("C:/Program Files (x86)"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
        Path(os.environ.get("APPDATA", "")),
    ]

    # 1. Chercher dans les raccourcis Menu Démarrer (.lnk)
    candidats = []
    for base in dossiers_start:
        if not base.exists():
            continue
        for lnk in base.rglob("*.lnk"):
            stem_n = sans_accents(lnk.stem.lower())
            if cible in stem_n:
                score = _score_lnk(lnk.stem, cible)
                candidats.append((score, str(lnk)))

    if candidats:
        candidats.sort(key=lambda x: x[0])
        return candidats[0][1]

    # 2. Chercher directement un .exe dans les dossiers d'installation
    for base in dossiers_install:
        if not base.exists():
            continue
        for exe in base.rglob("*.exe"):
            stem_n = sans_accents(exe.stem.lower())
            if cible == stem_n:
                return str(exe)
        # Deuxième passe : correspondance partielle
        for exe in base.rglob("*.exe"):
            stem_n = sans_accents(exe.stem.lower())
            if cible in stem_n and len(cible) > 3:
                return str(exe)

    return None

@outil(
    nom="ouvrir_application",
    description="Lance une application par son nom (Spotify, Discord, Notepad++, "
                "Chrome, VLC, Steam...) ou ouvre un site web (youtube, google...). "
                "Cherche automatiquement dans le Menu Démarrer et Program Files si "
                "l'app n'est pas dans la liste habituelle.",
    parametres={
        "type": "object",
        "properties": {
            "nom": {
                "type": "string",
                "description": "Nom de l'application ou du site "
                               "(spotify, discord, youtube, notepad++, vlc...)",
            }
        },
        "required": ["nom"],
    },
)
def ouvrir_application(nom: str) -> str:
    """Lance une application ou un site."""
    # 1. Verifier d'abord dans les apps configurees (config.yaml)
    from tools.apps import _trouver, _apps, launch_app as _launch_configured
    apps_conf = _apps()
    if apps_conf and _trouver(nom, apps_conf):
        return _launch_configured(nom)
    nom_min = nom.lower().strip()

    raccourcis = {
        "spotify": "spotify:",
        "navigateur": "https://www.google.com",
        "internet": "https://www.google.com",
        "google": "https://www.google.com",
        "youtube": "https://www.youtube.com",
        "calculatrice": "calc",
        "bloc-notes": "notepad",
        "notepad": "notepad",
        "explorateur": "explorer",
        "fichiers": "explorer",
        "parametres": "ms-settings:",
        "task manager": "taskmgr",
        "gestionnaire de taches": "taskmgr",
    }

    # Discord : chemin spécial
    if nom_min == "discord":
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Discord"
        maj = base / "Update.exe"
        if maj.exists():
            subprocess.Popen([str(maj), "--processStart", "Discord.exe"])
            return "Discord lancé."
        return "Discord introuvable."

    # Raccourcis connus
    if nom_min in raccourcis:
        cible = raccourcis[nom_min]
        try:
            if str(cible).startswith("http"):
                webbrowser.open(cible)
            else:
                subprocess.Popen(f'start "" "{cible}"', shell=True)
            return f"{nom} lancé."
        except Exception as e:
            return f"Impossible de lancer {nom} : {e}"

    # Recherche automatique dans le système
    chemin = _chercher_app_windows(nom)
    if chemin:
        try:
            os.startfile(chemin)
            return f"{nom} lancé (trouvé : {Path(chemin).name})."
        except Exception as e:
            return f"Trouvé {chemin} mais impossible de lancer : {e}"

    # Dernier recours : essayer directement avec start
    try:
        result = subprocess.run(
            f'start "" "{nom}"', shell=True, capture_output=True, timeout=3
        )
        if result.returncode == 0:
            return f"{nom} lancé."
    except Exception:
        pass

    return (f"Je ne trouve pas '{nom}'. "
            "Dis-moi son chemin complet ou ajoute-le avec la commande ajouter_app.")


@outil(
    nom="ouvrir_fichier",
    description="Ouvre un fichier ou dossier spécifique avec son application par "
                "défaut. Utilise un chemin complet (C:\\Users\\...) ou un chemin "
                "relatif. Fonctionne aussi pour les URLs.",
    parametres={
        "type": "object",
        "properties": {
            "chemin": {
                "type": "string",
                "description": "Chemin complet du fichier ou dossier à ouvrir.",
            }
        },
        "required": ["chemin"],
    },
)
def ouvrir_fichier(chemin: str) -> str:
    """Ouvre un fichier/dossier/URL avec l'app par défaut."""
    chemin = chemin.strip()
    if chemin.startswith("http"):
        webbrowser.open(chemin)
        return f"Ouvert dans le navigateur : {chemin}"
    p = Path(chemin)
    if p.exists():
        try:
            os.startfile(str(p))
            return f"Ouvert : {p.name}"
        except Exception as e:
            return f"Erreur à l'ouverture : {e}"
    # Tenter quand même (chemin réseau, variable d'env dans le chemin, etc.)
    try:
        subprocess.Popen(f'explorer "{chemin}"', shell=True)
        return f"Ouverture tentée : {chemin}"
    except Exception as e:
        return f"Introuvable : {chemin}"



def _presser(code, fois=1):
    for _ in range(fois):
        ctypes.windll.user32.keybd_event(code, 0, 0, 0)
        ctypes.windll.user32.keybd_event(code, 0, 2, 0)
        time.sleep(0.02)


@outil(
    nom="controler_media",
    description="Controle la lecture audio ou video en cours",
    parametres={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["pause", "suivant", "precedent", "muet"],
                "description": "Action a effectuer",
            }
        },
        "required": ["action"],
    },
)
def controler_media(action: str) -> str:
    """Controle la lecture et le volume.

    action : pause, suivant, precedent, monter, baisser, muet
    """
    action = action.lower().strip()
    if action not in _TOUCHES:
        return f"Action inconnue : {action}"

    fois = 5 if action in ("monter", "baisser") else 1
    _presser(_TOUCHES[action], fois)
    return f"Fait : {action}."


@outil(
    nom="regler_volume",
    description="Monte ou baisse le volume du systeme",
    parametres={
        "type": "object",
        "properties": {
            "sens": {"type": "string", "enum": ["monter", "baisser"]},
            "crans": {
                "type": "integer",
                "description": "Nombre de crans, 2 % chacun. 10 par defaut.",
            },
        },
        "required": ["sens"],
    },
)
def regler_volume(sens: str, crans: int = 10) -> str:
    """Monte ou baisse le volume d'un nombre de crans (2 % par cran)."""
    sens = sens.lower().strip()
    if sens not in ("monter", "baisser"):
        return "Sens invalide."
    _presser(_TOUCHES[sens], max(1, min(crans, 50)))
    return f"Volume {sens}."
