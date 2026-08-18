"""Diffusion de YouTube sur un ecran Chromecast, a la voix.

YouTube est la seule grande plateforme dont le recepteur Chromecast accepte
d'etre pilote de l'exterieur : pychromecast sait lui demander une video par
son identifiant. Netflix, Prime Video, Disney+ ou myCANAL exigent au contraire
une authentification transmise par un canal prive, reservee a leurs propres
applications.

La recherche passe par yt-dlp, qui interroge YouTube sans clef d'API.
"""
import re

from core.registre import outil

# Une recherche prend environ une seconde et demie ; on garde les resultats
# recents pour que « la suivante » ou une redemande soit immediate.
_CACHE = {}


def _identifiant(texte):
    """Extrait l'identifiant d'une adresse YouTube, ou None."""
    t = (texte or "").strip()
    motifs = (
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([A-Za-z0-9_-]{11})",
        r"^([A-Za-z0-9_-]{11})$",
    )
    for m in motifs:
        trouve = re.search(m, t)
        if trouve:
            return trouve.group(1)
    return None


def chercher(recherche, combien=5):
    """Renvoie [(identifiant, titre)] pour une recherche YouTube."""
    if recherche in _CACHE:
        return _CACHE[recherche]
    try:
        import yt_dlp
    except ImportError:
        return []

    options = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": "in_playlist", "socket_timeout": 15,
    }
    try:
        with yt_dlp.YoutubeDL(options) as y:
            donnees = y.extract_info(f"ytsearch{combien}:{recherche}",
                                     download=False)
    except Exception:
        return []

    sortie = []
    for e in (donnees.get("entries") or []):
        if e and e.get("id"):
            sortie.append((e["id"], e.get("title") or recherche))
    _CACHE[recherche] = sortie
    if len(_CACHE) > 40:
        _CACHE.clear()
    return sortie


@outil(
    nom="youtube_caster",
    description=(
        "Cherche une video sur YouTube et la lance sur un ecran Chromecast. "
        "Pour 'mets telle chanson sur YouTube sur la tele', 'lance telle video "
        "sur le videoprojecteur'. Accepte aussi une adresse YouTube complete. "
        "Sans ecran precise, la video s'ouvre dans le navigateur du PC."
    ),
    parametres={
        "type": "object",
        "properties": {
            "recherche": {"type": "string",
                          "description": "Ce qu il faut chercher, ou une adresse YouTube."},
            "ecran": {"type": "string",
                      "description": "Nom de l ecran Chromecast. Vide = sur le PC."},
        },
        "required": ["recherche"],
    },
    lent=True,
    phrase_attente="Je cherche sur YouTube.",
)
def youtube_caster(recherche: str, ecran: str = "") -> str:
    recherche = (recherche or "").strip()
    if not recherche:
        return "Que veux-tu regarder ?"

    # Adresse directe ou recherche ?
    ident = _identifiant(recherche)
    titre = recherche
    if ident is None:
        resultats = chercher(recherche)
        if not resultats:
            return f"Je n ai rien trouve sur YouTube pour {recherche}."
        ident, titre = resultats[0]

    # Sans ecran nomme : on ouvre sur le PC, comme pour Plex.
    if not ecran:
        import webbrowser
        try:
            webbrowser.open(f"https://www.youtube.com/watch?v={ident}")
        except Exception:
            return "Je n ai pas pu ouvrir YouTube."
        return f"{titre} sur le PC."

    from tools.cast import _choisir
    appareil = _choisir(ecran)
    if appareil is None:
        return "Je ne vois pas cet ecran."

    try:
        from pychromecast.controllers.youtube import YouTubeController
        appareil.wait(timeout=12)
        controleur = YouTubeController()
        appareil.register_handler(controleur)
        controleur.play_video(ident)
    except Exception as e:
        return f"Echec de la diffusion : {e}"

    return f"{titre} sur {appareil.cast_info.friendly_name}."
