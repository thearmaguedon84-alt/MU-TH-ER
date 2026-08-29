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
from core.util import sans_accents

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
    # Le corps precise la vraie cause : sans ca on annoncait « Premium
    # necessaire » pour un simple appareil endormi.
    raison = ""
    try:
        raison = (r.json().get("error") or {}).get("reason") or ""
    except Exception:
        pass

    if r.status_code == 404 or raison == "NO_ACTIVE_DEVICE":
        return False, "aucun appareil"
    if r.status_code == 403:
        if raison == "PREMIUM_REQUIRED":
            return False, "Spotify refuse : un compte Premium est necessaire."
        return False, "Spotify a refuse la commande."
    if r.status_code == 401:
        _ACCES["jeton"] = None
        return False, "Autorisation Spotify expiree."
    if r.status_code >= 400:
        return False, f"Spotify a repondu {r.status_code}."
    try:
        return True, r.json()
    except Exception:
        return True, {}


def _lister_appareils():
    ok, d = _appel("GET", "/me/player/devices")
    if not ok or not isinstance(d, dict):
        return []
    return d.get("devices") or []


def _appareil():
    """Identifiant d'un appareil disponible, en privilegiant l'actif."""
    appareils = _lister_appareils()
    for a in appareils:
        if a.get("is_active"):
            return a.get("id")
    return appareils[0].get("id") if appareils else None


def _appareil_nomme(nom):
    """Appareil Spotify dont le nom ressemble a `nom`, ou None."""
    from difflib import SequenceMatcher

    cible = sans_accents(str(nom or "").lower()).strip()
    if not cible:
        return None
    meilleur, note_max = None, 0.0
    for a in _lister_appareils():
        n = sans_accents(str(a.get("name") or "").lower())
        if not n:
            continue
        if cible in n or n in cible:
            return a
        note = SequenceMatcher(None, cible, n).ratio()
        if note > note_max:
            meilleur, note_max = a, note
    return meilleur if note_max >= 0.55 else None


def _preparer_appareil(patienter=True):
    """Garantit qu'un appareil est pret a recevoir la lecture.

    Renvoie (identifiant, message). Si l'identifiant est None, le message
    explique ce qui manque.
    """
    import time

    appareils = _lister_appareils()

    # Un appareil deja actif : rien a faire.
    for a in appareils:
        if a.get("is_active"):
            return a.get("id"), ""

    # Ouvrir l application du PC si aucun ordinateur n est disponible. Un
    # telephone en veille compte comme un appareil, mais lui transferer la
    # lecture echoue : il faut une cible reellement joignable.
    a_un_ordi = any(a.get("type") == "Computer" for a in appareils)
    if not a_un_ordi and patienter:
        try:
            from tools.apps import launch_app
            launch_app("spotify")
        except Exception:
            pass
        for _ in range(14):              # jusqu'a ~21 s le temps qu'elle demarre
            time.sleep(1.5)
            nouveaux = _lister_appareils()
            if any(a.get("type") == "Computer" for a in nouveaux):
                appareils = nouveaux
                break
            if nouveaux:
                appareils = nouveaux

    if not appareils:
        return None, ("Aucun appareil Spotify. Ouvre l application Spotify, "
                      "puis redemande.")

    # Des appareils existent mais aucun n'est actif : on reveille le meilleur.
    # Un ordinateur est preferable a un telephone en veille.
    ordre = {"Computer": 0, "Speaker": 1, "TV": 2, "Smartphone": 3}
    appareils.sort(key=lambda a: ordre.get(a.get("type"), 9))
    cible = appareils[0]

    # Le transfert peut echouer alors que l appareil est parfaitement
    # utilisable (Spotify renvoie « Restriction violated » quand la cible est
    # deja selectionnee). On tente la lecture malgre tout : c est elle qui
    # tranchera.
    _appel("PUT", "/me/player",
           json={"device_ids": [cible.get("id")], "play": False})
    time.sleep(1.2)
    return cible.get("id"), ""


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
            "appareil": {"type": "string",
                         "description": ("Nom de l enceinte ou de l ecran Spotify vise. "
                                         "Vide = l appareil courant.")},
        },
        "required": ["recherche"],
    },
    lent=True,
    phrase_attente="Je cherche sur Spotify.",
)
def spotify_jouer(recherche: str, genre: str = "", appareil: str = "") -> str:
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

    # Pas de market=from_token : ce parametre exige la portee
    # user-read-private, absente de l autorisation, et Spotify renvoie alors
    # « Insufficient client scope » sur une simple recherche.
    ok, d = _appel("GET", "/search",
                   params={"q": recherche, "type": types, "limit": 5})
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

    if appareil:
        vise = _appareil_nomme(appareil)
        if vise is None:
            connus = ", ".join(a.get("name", "?") for a in _lister_appareils())
            return (f"Je ne peux pas viser {appareil} : Spotify ne le voit pas. "
                    "Demarre la diffusion depuis ton telephone, je prendrai "
                    "le relais."
                    + (f" Disponibles : {connus}." if connus else ""))
        _appel("PUT", "/me/player",
               json={"device_ids": [vise.get("id")], "play": False})
        import time as _t
        _t.sleep(1.2)
        app = vise.get("id")
    else:
        app, souci = _preparer_appareil()
        if not app:
            return souci

    ok, msg = _appel("PUT", "/me/player/play",
                     params={"device_id": app}, json=corps)
    if not ok:
        if msg == "aucun appareil":
            return ("Spotify n a pas d appareil pret. Ouvre l application "
                    "Spotify, puis redemande.")
        return f"Spotify a refuse de lancer {item.get('name', recherche)}."

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
    ok, d = _appel("GET", "/me/player/currently-playing")
    if not ok:
        return "Rien ne joue en ce moment." if d == "aucun appareil" else d
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


def _basculer(commande, etat_voulu, succes, deja):
    """Pause ou reprise, avec une seconde tentative.

    Spotify refuse parfois la commande juste apres un changement de piste
    (« Restriction violated ») ; une pause d une seconde suffit. Et si l etat
    est deja celui demande, autant le dire plutot que d annoncer une erreur.
    """
    import time

    ok, m = _appel("PUT", f"/me/player/{commande}")
    if ok:
        return succes

    time.sleep(1.0)
    ok, m = _appel("PUT", f"/me/player/{commande}")
    if ok:
        return succes

    # Toujours refuse : peut-etre parce que c est deja fait.
    ok2, etat = _appel("GET", "/me/player")
    if ok2 and isinstance(etat, dict) and etat.get("is_playing") == etat_voulu:
        return deja
    return m


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
        return _basculer("pause", False, "En pause.", "C etait deja en pause.")
    if a in ("reprendre", "play", "lecture", "continuer"):
        return _basculer("play", True, "Lecture reprise.", "Ca joue deja.")
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


@outil(
    nom="spotify_appareils",
    description="Enumere les appareils sur lesquels Spotify peut diffuser "
                "(ordinateur, telephone, enceintes, Chromecast reveilles). "
                "Pour 'ou peut jouer Spotify', 'quels appareils Spotify'.",
    parametres={"type": "object", "properties": {}, "required": []},
)
def spotify_appareils() -> str:
    if not configure():
        return "Spotify n est pas configure."
    apps = _lister_appareils()
    if not apps:
        return "Spotify ne voit aucun appareil."
    parts = []
    for a in apps:
        marque = " (en cours)" if a.get("is_active") else ""
        parts.append(f"{a.get('name')}{marque}")
    return "Spotify peut jouer sur : " + ", ".join(parts) + "."


@outil(
    nom="spotify_transferer",
    description=("Envoie la musique Spotify en cours vers un autre appareil. "
                 "Pour 'envoie la musique sur la tele', 'mets Spotify dans la "
                 "chambre', 'bascule le son sur l enceinte'."),
    parametres={
        "type": "object",
        "properties": {
            "appareil": {"type": "string",
                         "description": "Nom de l appareil de destination."},
        },
        "required": ["appareil"],
    },
)
def spotify_transferer(appareil: str) -> str:
    if not configure():
        return "Spotify n est pas configure."
    vise = _appareil_nomme(appareil)
    if vise is None:
        connus = ", ".join(a.get("name", "?") for a in _lister_appareils())
        # Un Chromecast n est visible de l API que pendant qu il diffuse
        # reellement du Spotify. Le recepteur peut etre lance a distance, mais
        # il ne rejoint pas le compte sans une authentification que Spotify
        # n expose plus. Il faut donc demarrer la diffusion depuis un
        # telephone ou le PC ; ensuite tout est pilotable a la voix.
        base = (f"Je ne peux pas envoyer Spotify sur {appareil} tout seul. "
                "Lance la lecture sur cet ecran depuis ton telephone, "
                "ensuite je pourrai la piloter.")
        return base + (f" Pour l instant Spotify joue sur : {connus}."
                       if connus else "")
    ok, m = _appel("PUT", "/me/player",
                   json={"device_ids": [vise.get("id")], "play": True})
    if not ok:
        return f"Spotify n a pas pu basculer vers {vise.get('name')}."
    return f"Musique envoyee sur {vise.get('name')}."
