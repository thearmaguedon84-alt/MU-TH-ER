"""Controle media global : Spotify, VLC, YouTube, Netflix, n'importe quel lecteur.

Utilise les touches media de Windows (VK_MEDIA_*), interceptees par l'application
qui a le focus media du systeme. Aucune dependance externe, aucun compte.
"""
import ctypes

from core.registre import outil

# Codes de touches virtuelles Windows
VK = {
    "play_pause":   0xB3,   # VK_MEDIA_PLAY_PAUSE
    "stop":         0xB2,   # VK_MEDIA_STOP
    "suivant":      0xB0,   # VK_MEDIA_NEXT_TRACK
    "precedent":    0xB1,   # VK_MEDIA_PREV_TRACK
    "volume_haut":  0xAF,   # VK_VOLUME_UP
    "volume_bas":   0xAE,   # VK_VOLUME_DOWN
    "muet":         0xAD,   # VK_VOLUME_MUTE
}

# Synonymes -> action canonique
ALIAS = {
    "pause": "play_pause",
    "play": "play_pause",
    "lecture": "play_pause",
    "reprendre": "play_pause",
    "reprend": "play_pause",
    "toggle": "play_pause",
    "play_pause": "play_pause",
    "stop": "stop",
    "arreter": "stop",
    "next": "suivant",
    "suivant": "suivant",
    "suivante": "suivant",
    "prochain": "suivant",
    "previous": "precedent",
    "precedent": "precedent",
    "precedente": "precedent",
    "retour": "precedent",
    "volume_up": "volume_haut",
    "volume_haut": "volume_haut",
    "plus_fort": "volume_haut",
    "monter": "volume_haut",
    "volume_down": "volume_bas",
    "volume_bas": "volume_bas",
    "moins_fort": "volume_bas",
    "baisser": "volume_bas",
    "mute": "muet",
    "muet": "muet",
    "silence": "muet",
}

LIBELLES = {
    "play_pause":  "C'est fait.",
    "stop":        "Lecture arretee.",
    "suivant":     "Piste suivante.",
    "precedent":   "Piste precedente.",
    "volume_haut": "Volume augmente.",
    "volume_bas":  "Volume baisse.",
    "muet":        "Son coupe.",
}

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001


def _frapper(vk, repetitions=1):
    """Envoie une touche media au systeme (globale, pas liee a une fenetre)."""
    user32 = ctypes.windll.user32
    for _ in range(max(1, repetitions)):
        user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
        user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)


@outil(
    nom="controler_media",
    description=(
        "OBLIGATOIRE pour toute commande de lecture audio ou video : mettre en "
        "pause, reprendre, piste suivante ou precedente, monter ou baisser le "
        "volume, couper le son. Fonctionne avec Spotify, VLC, YouTube, Netflix "
        "et tout lecteur. Exemples : pause, mets en pause, reprends la musique, "
        "chanson suivante, morceau precedent, monte le son, baisse le volume, "
        "coupe le son."
    ),
    parametres={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": ("play_pause, stop, suivant, precedent, "
                                "volume_haut, volume_bas, muet"),
            },
            "repetitions": {
                "type": "integer",
                "description": ("Nombre de crans pour le volume (defaut 1). "
                                "Un cran = 2%. Utiliser 5 pour un changement net."),
            },
        },
        "required": ["action"],
    },
)
def controler_media(action="play_pause", repetitions=1):
    """Envoie une commande media globale a Windows."""
    a = str(action or "").strip().lower().replace(" ", "_").replace("-", "_")
    canon = ALIAS.get(a)
    if canon is None:
        dispo = ", ".join(sorted(set(ALIAS.values())))
        return f"Action inconnue : {action}. Disponibles : {dispo}."

    try:
        rep = int(repetitions)
    except (TypeError, ValueError):
        rep = 1
    # Le volume bouge par crans de 2 % : on frappe plusieurs fois
    if canon in ("volume_haut", "volume_bas") and rep <= 1:
        rep = 4

    _frapper(VK[canon], rep)
    return LIBELLES.get(canon, "C'est fait.")


@outil(
    nom="regler_volume_systeme",
    description=(
        "Regle le volume general de Windows a un pourcentage precis. "
        "Pour 'mets le volume a 30', 'volume a 50 pour cent'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "pourcentage": {
                "type": "integer",
                "description": "Niveau voulu, de 0 a 100.",
            },
        },
        "required": ["pourcentage"],
    },
)
def regler_volume_systeme(pourcentage=50):
    """Met le volume systeme au pourcentage demande."""
    try:
        cible = max(0, min(100, int(pourcentage)))
    except (TypeError, ValueError):
        return "Il me faut un nombre entre 0 et 100."

    # On descend a zero puis on remonte par crans de 2 %
    _frapper(VK["volume_bas"], 50)
    _frapper(VK["volume_haut"], round(cible / 2))
    return f"Volume a {cible} pour cent."
