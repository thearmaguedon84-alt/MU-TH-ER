"""Pilotage complet de Spotify par l'API Web.

Les touches media (tools/controle.py) suffisent pour pause, suivant et volume,
mais elles ne savent pas CHOISIR quoi jouer. Ici on parle a l'API de Spotify :
recherche d'un titre, d'un album, d'un artiste ou d'une playlist, lecture sur
l'appareil de son choix, et lecture de l'etat courant.

Configuration : lancer une fois `configurer_spotify.py`, qui obtient le jeton
de rafraichissement et l'ecrit dans config.yaml. Rien d'autre a faire ensuite,
le jeton d'acces se renouvelle tout seul.

Un compte Premium est exige par Spotify pour toute commande de lecture ; en
compte gratuit, seules les consultations fonctionnent.
"""
import base64
import time

import requests

from core.config import reglage
from core.registre import outil

API = "https://api.spotify.com/v1"
JETON_URL = "https://accounts.spotify.com/api/token"

# Jeton d'acces courant, garde en memoire (valable une heure environ)
_ACCES = {"jeton": None, "expire": 0.0}


# ------------------------------------------------------------------ jetons

def _identifiants():
    return (reglage("spotify.client_id", ""),
            reglage("spotify.client_secret", ""),
            reglage("spotify.refresh_token", ""))


def configure():
    cid, secret, refresh = _identifiants()
    return bool(cid and secret and refresh)


def _jeton():
    """Jeton d'acces valide, renouvele si besoin."""
    if _ACCES["jeton"] and time.time() < _ACCES["expire"] - 30:
        return _ACCES["jeton"]

    cid, secret, refresh = _identifiants()
    if not (cid and secret and refresh):
        return None

    entete = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    r = requests.post(
        JETON_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh},
        headers={"Authorization": f"Basic {entete}"},
        timeout=15,
    )
    if r.status_code != 200:
        return None
    d = r.json()
    _ACCES["jeton"] = d.get("access_token")
    _ACCES["expire"] = time.time() + int(d.get("expires_in", 3600))
    return _ACCES["jeton"]


def _appel(methode, chemin, **kw):
    """Appel API. Renvoie (ok, donnees_ou_message)."""
    jeton = _jeton()
    if not jeton:
        return False, "Spotify n est pas configure."
    try:
        r = requests.request(
            methode, API + chemin, timeout=15,
            headers={"Authorization": f"Bearer {jeton}"}, **kw)
    except Exception as e:
        return False, f"Spotify injoignable : {e}"

    if r.status_code == 204:
        return True, {}
    if r.status_code == 404:
        return False, "Aucun appareil Spotify actif. Ouvre Spotify et lance un titre."
    if r.status_code == 403:
        return False, "Spotify refuse : un compte Premium est necessaire."
    if r.status_code == 401:
        _ACCES["jeton"] = None
        return False, "Autorisation Spotify expiree."
    if r.status_code >= 400:
        return False, f"Spotify a repondu {r.status_code}."
    try:
        return True, r.json()
    except Exception:
        return True, {}


def _appareil():
    """Identifiant d'un appareil disponible, en privilegiant l'actif."""
    ok, d = _appel("GET", "/me/player/devices")
    if not ok or not isinstance(d, dict):
        return None
    appareils = d.get("devices") or []
    if not appareils:
        return None
    for a in appareils:
        if a.get("is_active"):
            return a.get("id")
    return appareils[0].get("id")


# ------------------------------------------------------------------ outils

@outil(
    nom="spotify_jouer",
    description=(
        "Cherche puis lance une musique sur Spotify : un titre, un album, un "
        "artiste ou une playlist. Pour 'mets Nirvana sur Spotify', 'joue "
        "l album Nevermind', 'lance ma playlist du matin', 'mets du jazz'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "recherche": {"type": "string",
                          "description": "Ce qu il faut chercher : titre, album, artiste, playlist."},
            "genre": {"type": "string",
                      "description": "titre, album, artiste ou playlist. Vide = au mieux."},
        },
        "required": ["recherche"],
    },
    lent=True,
    phrase_attente="Je cherche sur Spotify.",
)
def spotify_jouer(recherche: str, genre: str = "") -> str:
    if not configure():
        return "Spotify n est pas configure."
    recherche = (recherche or "").strip()
    if not recherche:
        return "Que veux-tu ecouter ?"

    genres = {"titre": "track", "morceau": "track", "chanson": "track",
              "album": "album", "artiste": "artist", "groupe": "artist",
              "playlist": "playlist", "liste": "playlist"}
    t = genres.get((genre or "").strip().lower(), "")
    types = t or "track,album,artist,playlist"

    ok, d = _appel("GET", "/search",
                   params={"q": recherche, "type": types, "limit": 5,
                           "market": "from_token"})
    if not ok:
        return d

    # On prend le meilleur resultat, en respectant l'ordre de preference
    ordre = [t] if t else ["artist", "album", "playlist", "track"]
    choix = None
    for cle in ordre:
        lot = (d.get(cle + "s") or {}).get("items") or []
        lot = [x for x in lot if x]
        if lot:
            choix = (cle, lot[0])
            break
    if not choix:
        return f"Je n ai rien trouve pour {recherche}."

    cle, item = choix
    corps = ({"uris": [item["uri"]]} if cle == "track"
             else {"context_uri": item["uri"]})

    params = {}
    app = _appareil()
    if app:
        params["device_id"] = app

    ok, msg = _appel("PUT", "/me/player/play", params=params, json=corps)
    if not ok:
        return msg

    nom = item.get("name", recherche)
    if cle == "track":
        artistes = ", ".join(a["name"] for a in item.get("artists", [])[:2])
        return f"Je lance {nom}{' de ' + artistes if artistes else ''}."
    if cle == "album":
        artistes = ", ".join(a["name"] for a in item.get("artists", [])[:2])
        return f"Je lance l album {nom}{' de ' + artistes if artistes else ''}."
    if cle == "artist":
        return f"Je lance {nom}."
    return f"Je lance la playlist {nom}."


@outil(
    nom="spotify_en_cours",
    description="Dit quel morceau passe sur Spotify. Pour 'c est quoi cette "
                "chanson', 'qu est-ce qui passe', 'quel est ce morceau'.",
    parametres={"type": "object", "properties": {}, "required": []},
)
def spotify_en_cours() -> str:
    if not configure():
        return "Spotify n est pas configure."
    ok, d = _appel("GET", "/me/player/currently-playing", params={"market": "from_token"})
    if not ok:
        return d
    if not d or not d.get("item"):
        return "Rien ne joue en ce moment."
    item = d["item"]
    artistes = ", ".join(a["name"] for a in item.get("artists", [])[:2])
    album = (item.get("album") or {}).get("name", "")
    reponse = f"{item.get('name')}"
    if artistes:
        reponse += f", de {artistes}"
    if album and album != item.get("name"):
        reponse += f", sur l album {album}"
    return reponse + "."


@outil(
    nom="spotify_controle",
    description="Commande la lecture Spotify : pause, reprendre, morceau "
                "suivant ou precedent, lecture aleatoire.",
    parametres={
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "description": "pause, reprendre, suivant, precedent, aleatoire"},
        },
        "required": ["action"],
    },
)
def spotify_controle(action: str) -> str:
    if not configure():
        return "Spotify n est pas configure."
    a = (action or "").strip().lower()

    if a in ("pause", "stop", "arreter"):
        ok, m = _appel("PUT", "/me/player/pause")
        return "En pause." if ok else m
    if a in ("reprendre", "play", "lecture", "continuer"):
        ok, m = _appel("PUT", "/me/player/play")
        return "Lecture reprise." if ok else m
    if a in ("suivant", "next", "prochain"):
        ok, m = _appel("POST", "/me/player/next")
        return "Morceau suivant." if ok else m
    if a in ("precedent", "previous", "retour"):
        ok, m = _appel("POST", "/me/player/previous")
        return "Morceau precedent." if ok else m
    if a in ("aleatoire", "shuffle", "melange"):
        ok, m = _appel("PUT", "/me/player/shuffle", params={"state": "true"})
        return "Lecture aleatoire activee." if ok else m
    return "Action inconnue."


@outil(
    nom="spotify_volume",
    description="Regle le volume de Spotify a un pourcentage precis.",
    parametres={
        "type": "object",
        "properties": {
            "pourcentage": {"type": "integer", "description": "De 0 a 100."},
        },
        "required": ["pourcentage"],
    },
)
def spotify_volume(pourcentage: int = 50) -> str:
    if not configure():
        return "Spotify n est pas configure."
    try:
        v = max(0, min(100, int(pourcentage)))
    except (TypeError, ValueError):
        return "Il me faut un nombre entre 0 et 100."
    ok, m = _appel("PUT", "/me/player/volume", params={"volume_percent": v})
    return f"Volume Spotify a {v} pour cent." if ok else m
