"""Pilotage de ce qui passe sur un ecran Chromecast, quelle que soit l'application.

Decouverte utile : la plupart des recepteurs — CANAL+, YouTube, Plex, et tout
ce qui utilise le lecteur standard — publient leur etat sur l'espace de noms
`com.google.cast.media`. On ne peut pas LANCER une session CANAL+ ou Netflix
depuis l'exterieur, mais une fois qu'elle tourne, on peut la mettre en pause,
la reprendre, avancer, reculer, l'arreter, et savoir ce qui passe.

C'est la difference entre demarrer une lecture, qui exige une authentification
propre a l'editeur, et commander une lecture en cours, qui passe par le
protocole commun de Google Cast.
"""
from core.registre import outil


def _appareil(nom):
    from tools.cast import _choisir, _decouvrir
    if nom:
        return _choisir(nom)
    # Sans nom : l'ecran qui joue quelque chose, sinon le premier
    for c in _decouvrir():
        try:
            c.wait(timeout=4)
            if c.app_id and c.status.display_name not in (None, "Backdrop"):
                return c
        except Exception:
            continue
    appareils = _decouvrir()
    return appareils[0] if appareils else None


def _etat(appareil):
    """Statut media a jour, ou None."""
    import time
    try:
        appareil.wait(timeout=10)
        m = appareil.media_controller
        try:
            m.update_status()
            time.sleep(1.2)
        except Exception:
            pass
        return m.status
    except Exception:
        return None


@outil(
    nom="ecran_en_cours",
    description=(
        "Dit ce qui passe actuellement sur un ecran Chromecast, quelle que "
        "soit l'application : CANAL+, YouTube, Plex, Netflix... Pour 'qu est-ce "
        "qui passe sur la tele', 'on regarde quoi'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "ecran": {"type": "string",
                      "description": "Nom de l ecran. Vide = celui qui joue."},
        },
        "required": [],
    },
    lent=True,
)
def ecran_en_cours(ecran: str = "") -> str:
    appareil = _appareil(ecran)
    if appareil is None:
        return "Je ne vois pas cet ecran." if ecran else "Aucun ecran trouve."

    nom_ecran = str(appareil.cast_info.friendly_name)
    try:
        appareil.wait(timeout=10)
        application = appareil.status.display_name
    except Exception:
        return f"{nom_ecran} ne repond pas."

    if not application or application == "Backdrop":
        return f"Rien ne passe sur {nom_ecran}."

    s = _etat(appareil)
    if s is None or not s.title:
        return f"{application} est ouvert sur {nom_ecran}."

    etats = {"PLAYING": "en lecture", "PAUSED": "en pause",
             "BUFFERING": "en chargement", "IDLE": "a l arret"}
    etat = etats.get(s.player_state, "")
    return f"{s.title}, sur {application}, {etat} sur {nom_ecran}."


@outil(
    nom="ecran_controle",
    description=(
        "Commande la lecture en cours sur un ecran Chromecast, quelle que soit "
        "l'application : pause, reprendre, arreter, avancer, reculer. "
        "Fonctionne avec CANAL+, YouTube, Plex et tout lecteur compatible. "
        "Pour 'mets la tele en pause', 'reprends sur la tele', 'avance de 30 "
        "secondes', 'arrete la tele'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "description": "pause, reprendre, stop, avancer, reculer"},
            "ecran": {"type": "string",
                      "description": "Nom de l ecran. Vide = celui qui joue."},
            "secondes": {"type": "integer",
                         "description": "Duree du saut pour avancer ou reculer (30 par defaut)."},
        },
        "required": ["action"],
    },
    lent=True,
)
def ecran_controle(action: str, ecran: str = "", secondes: int = 30) -> str:
    appareil = _appareil(ecran)
    if appareil is None:
        return "Je ne vois pas cet ecran." if ecran else "Aucun ecran trouve."

    nom_ecran = str(appareil.cast_info.friendly_name)
    a = (action or "").strip().lower()

    try:
        appareil.wait(timeout=10)
        m = appareil.media_controller
    except Exception:
        return f"{nom_ecran} ne repond pas."

    # Arreter ne demande aucun support particulier : on ferme l'application.
    if a in ("stop", "arreter", "arrete", "coupe", "ferme", "quitte"):
        try:
            m.stop()
        except Exception:
            try:
                appareil.quit_app()
            except Exception as e:
                return f"Arret impossible : {str(e)[:60]}"
        return f"Arrete sur {nom_ecran}."

    s = _etat(appareil)
    if s is None:
        return f"Je n arrive pas a lire l etat de {nom_ecran}."

    try:
        if a in ("pause", "suspends", "stoppe"):
            if not s.supports_pause:
                return f"Cette application ne se laisse pas mettre en pause."
            m.pause()
            return f"En pause sur {nom_ecran}."

        if a in ("reprendre", "reprends", "play", "lecture", "continue"):
            m.play()
            return f"Lecture reprise sur {nom_ecran}."

        if a in ("avancer", "avance", "suivant"):
            if not s.supports_seek:
                return "Cette application ne se laisse pas deplacer."
            m.seek((s.current_time or 0) + max(1, int(secondes)))
            return f"{int(secondes)} secondes plus loin."

        if a in ("reculer", "recule", "retour", "precedent"):
            if not s.supports_seek:
                return "Cette application ne se laisse pas deplacer."
            m.seek(max(0, (s.current_time or 0) - max(1, int(secondes))))
            return f"{int(secondes)} secondes en arriere."
    except Exception as e:
        return f"Commande refusee : {str(e)[:60]}"

    return "Action inconnue."
