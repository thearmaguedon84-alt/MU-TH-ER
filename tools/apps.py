"""Lancement d'applications et de jeux, via un mapping dans config.yaml.

apps: { "borderlands": "C:\\...\\game.exe", "spotify": "spotify:",
        "un_jeu_steam": "steam://rungameid/XXXX" }

Si l'app demandee est inconnue, Jarvis propose de l'ajouter ; l'ajout passe par
ajouter_app (confirmation requise) qui ecrit dans config.yaml.
"""
import os

from core.config import definir, reglage
from core.registre import outil
from core.util import sans_accents


def _apps():
    return reglage("apps", {}) or {}


def _normaliser(s):
    """Minuscules, sans accents, apostrophes supprimees, tirets -> espace."""
    import re
    s = sans_accents(s.lower().strip())
    s = s.replace("'", "").replace("’", "")  # baldur's -> baldurs
    s = re.sub(r"[-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _trouver(nom, apps):
    """Retrouve la clef correspondant a `nom` (exacte puis souple)."""
    cible = _normaliser(nom)
    for k in apps:
        if _normaliser(k) == cible:
            return k
    for k in apps:
        kn = _normaliser(k)
        if kn and (kn in cible or cible in kn):
            return k
    for k in apps:
        mots_k = _normaliser(k).split()
        if mots_k and all(m in cible for m in mots_k):
            return k
    return None


@outil(
    nom="launch_app",
    description="Lance une application ou un jeu configure (Borderlands, Spotify, "
                "OBS...). Pour 'lance Borderlands', 'ouvre Spotify', 'demarre OBS'. "
                "Supporte les jeux Steam (steam://rungameid). Si l'app est inconnue, "
                "renvoie un message : propose alors a l'utilisateur de l'ajouter.",
    parametres={
        "type": "object",
        "properties": {
            "nom": {"type": "string", "description": "Nom de l'application ou du jeu."}
        },
        "required": ["nom"],
    },
)
def launch_app(nom: str) -> str:
    apps = _apps()
    clef = _trouver(nom, apps)
    if clef is None:
        return (f"Je ne connais pas {nom}. Donne-moi son chemin d'installation, "
                "ou son identifiant Steam (steam deux points slash slash rungameid "
                "slash numero), et je l'ajouterai.")
    cible = apps[clef]
    import subprocess
    try:
        if cible.startswith("shell:AppsFolder"):
            # Application du Microsoft Store (Spotify, Netflix, Xbox...)
            subprocess.Popen(["explorer.exe", cible.replace("/", chr(92))])
        elif "://" in cible or cible.startswith("spotify:"):
            # Protocole : steam://, com.epicgames.launcher://, spotify:
            subprocess.Popen(["cmd", "/c", "start", "", cible], shell=False)
        else:
            chemin = cible.replace("/", chr(92))
            dossier = os.path.dirname(chemin)
            try:
                # Beaucoup de jeux exigent d'etre lances depuis leur dossier
                if os.path.isdir(dossier):
                    subprocess.Popen([chemin], cwd=dossier)
                else:
                    os.startfile(chemin)
            except OSError as err:
                # WinError 740 : le programme demande les droits administrateur.
                # Popen ne sait pas declencher UAC, ShellExecute si.
                if getattr(err, "winerror", None) != 740:
                    raise
                import ctypes
                rc = ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", chemin, None, dossier or None, 1)
                if rc <= 32:
                    return (f"{clef} demande les droits administrateur "
                            "et le lancement a ete refuse.")
        return f"{clef} lance."
    except Exception as e:
        return f"Impossible de lancer {clef} : {e}"


def _annonce_ajout(args):
    return f"Je vais ajouter {args.get('nom', 'cette application')} a tes applications."


@outil(
    nom="ajouter_app",
    description="Ajoute une application ou un jeu au mapping (config.yaml) : un nom "
                "et un chemin .exe OU un identifiant Steam (steam://rungameid/NUMERO). "
                "A utiliser quand l'utilisateur donne le chemin d'une app inconnue.",
    parametres={
        "type": "object",
        "properties": {
            "nom": {"type": "string", "description": "Nom court de l'application."},
            "chemin": {"type": "string",
                       "description": "Chemin du .exe ou identifiant steam://rungameid/..."},
        },
        "required": ["nom", "chemin"],
    },
    confirmation=True,
    annonce=_annonce_ajout,
)
def ajouter_app(nom: str, chemin: str) -> str:
    nom = (nom or "").strip()
    chemin = (chemin or "").strip()
    if not nom or not chemin:
        return "Il me faut un nom et un chemin."
    apps = _apps()
    apps[nom.lower()] = chemin.replace(chr(92), chr(47))  # YAML-safe: backslash->slash
    definir("apps", apps)
    return f"{nom} ajoute. Tu peux maintenant dire : lance {nom}."
