"""Ouverture des plateformes de streaming sur un titre precis.

Ces services n'exposent aucune interface publique : impossible de chercher
dans leur catalogue ou de lancer une lecture par programme, contrairement a
Spotify ou Plex. Ce qu'on peut faire, et qui reste utile, c'est ouvrir
directement la page de recherche du titre demande — il ne reste qu'un clic.

La diffusion vers un Chromecast se heurte a la meme limite que Spotify : le
recepteur exige une authentification que ces services ne publient pas.
"""
import urllib.parse
import webbrowser

from core.registre import outil
from core.util import sans_accents

# Chaque plateforme : nom parle, adresse de recherche, alias reconnus
PLATEFORMES = {
    "netflix": {
        "nom": "Netflix",
        "url": "https://www.netflix.com/search?q={q}",
        "alias": ("netflix", "net flix", "nefflix", "netflixe"),
    },
    "primevideo": {
        "nom": "Prime Video",
        "url": "https://www.primevideo.com/search?phrase={q}",
        "alias": ("prime video", "prime", "amazon prime", "amazon video"),
    },
    "disney": {
        "nom": "Disney Plus",
        "url": "https://www.disneyplus.com/search?q={q}",
        "alias": ("disney", "disney plus", "disney+"),
    },
    "canal": {
        "nom": "myCANAL",
        "url": "https://www.canalplus.com/recherche?q={q}",
        "alias": ("mycanal", "my canal", "canal plus", "canal+", "canalplus"),
    },
    "youtube": {
        "nom": "YouTube",
        "url": "https://www.youtube.com/results?search_query={q}",
        "alias": ("youtube", "you tube", "youtoube"),
    },
}


def reconnaitre(texte):
    """Identifiant de la plateforme citee dans le texte, ou None."""
    t = sans_accents((texte or "").lower())
    for cle, info in PLATEFORMES.items():
        if any(a in t for a in info["alias"]):
            return cle
    return None


@outil(
    nom="streaming_chercher",
    description=(
        "Ouvre la recherche d'un titre sur Netflix, Prime Video, Disney Plus "
        "ou YouTube. Pour 'cherche Stranger Things sur Netflix', 'trouve tel "
        "film sur Prime'. Ces services n'ayant pas d'interface publique, on ne "
        "peut pas lancer la lecture directement : la page de recherche s'ouvre."
    ),
    parametres={
        "type": "object",
        "properties": {
            "titre": {"type": "string", "description": "Ce qu il faut chercher."},
            "plateforme": {"type": "string",
                           "description": "netflix, primevideo, disney ou youtube."},
        },
        "required": ["titre"],
    },
)
def streaming_chercher(titre: str, plateforme: str = "netflix") -> str:
    # « canal » est une clef, pas un alias : sans ce test, une clef valide
    # repassait par la reconnaissance d alias et retombait sur Netflix.
    p = (plateforme or "").strip().lower()
    cle = p if p in PLATEFORMES else (reconnaitre(p) or "netflix")
    info = PLATEFORMES[cle]

    titre = (titre or "").strip()
    if not titre:
        # Sans titre, on ouvre simplement l'accueil
        base = info["url"].split("/search")[0].split("/results")[0]
        try:
            webbrowser.open(base)
        except Exception:
            return f"Je n ai pas pu ouvrir {info['nom']}."
        return f"{info['nom']} ouvert."

    url = info["url"].format(q=urllib.parse.quote(titre))
    try:
        webbrowser.open(url)
    except Exception:
        return f"Je n ai pas pu ouvrir {info['nom']}."
    return f"{titre} cherche sur {info['nom']}."


@outil(
    nom="streaming_ouvrir",
    description="Ouvre une plateforme de streaming sans recherche : Netflix, "
                "Prime Video, Disney Plus, YouTube.",
    parametres={
        "type": "object",
        "properties": {
            "plateforme": {"type": "string",
                           "description": "netflix, primevideo, disney ou youtube."},
        },
        "required": ["plateforme"],
    },
)
def streaming_ouvrir(plateforme: str) -> str:
    return streaming_chercher(titre="", plateforme=plateforme)
