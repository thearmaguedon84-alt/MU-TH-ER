"""Envoi de l'interface de Jarvis sur un ecran Chromecast, a la voix.

Le HUD est une page web servie par le PC. Pour qu'un Chromecast puisse
l'afficher, deux conditions :

  1. le serveur doit ecouter sur le reseau local et non seulement sur
     127.0.0.1 (reglage hud.hote dans config.yaml) ;
  2. l'adresse annoncee au Chromecast doit etre celle de la carte reseau qui
     le voit. Sur ce PC, un adaptateur VPN capte la route par defaut : demander
     naivement « mon adresse IP » renvoie celle du VPN, que le Chromecast ne
     peut pas joindre. On choisit donc l'interface du meme sous-reseau que
     l'appareil vise.

L'affichage passe par DashCast, le recepteur qui sait montrer une page web.
"""
import socket
import threading
import time

from core.config import reglage
from core.registre import outil
from core.util import sans_accents

_APPAREILS = []          # cache de la derniere decouverte
_NAVIGATEUR = None       # navigateur zeroconf, garde ouvert volontairement
_DERNIERE = 0.0
_VERROU = threading.Lock()
_DUREE_CACHE = 120.0     # secondes


# ------------------------------------------------------------------ reseau

def _adresses_locales():
    """Toutes les adresses IPv4 de la machine."""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        return sorted({i[4][0] for i in infos})
    except Exception:
        return []


def _adresse_pour(hote_cible):
    """Adresse locale joignable depuis `hote_cible`.

    On privilegie une adresse du meme /24, ce qui ecarte les adaptateurs VPN
    et les adresses de lien local.
    """
    prefixe = ".".join(str(hote_cible).split(".")[:3])
    candidates = [a for a in _adresses_locales()
                  if not a.startswith("169.254.")]
    for a in candidates:
        if ".".join(a.split(".")[:3]) == prefixe:
            return a
    # A defaut : ce que le systeme choisirait pour joindre cet hote
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((str(hote_cible), 8009))
        a = s.getsockname()[0]
        s.close()
        return a
    except Exception:
        return candidates[0] if candidates else "127.0.0.1"


# ------------------------------------------------------------------ appareils

def _decouvrir(forcer=False, delai=8):
    """Liste des Chromecast du reseau, avec un cache court."""
    global _APPAREILS, _DERNIERE
    with _VERROU:
        if not forcer and _APPAREILS and time.time() - _DERNIERE < _DUREE_CACHE:
            return _APPAREILS
        try:
            import pychromecast
            # Le navigateur zeroconf doit rester VIVANT : pychromecast s en sert
            # encore au moment de la connexion pour resoudre l adresse. L arreter
            # ici provoquait "Zeroconf instance loop must be running".
            casts, nav = pychromecast.get_chromecasts(timeout=delai)
            global _NAVIGATEUR
            if _NAVIGATEUR is not None and _NAVIGATEUR is not nav:
                try:
                    pychromecast.discovery.stop_discovery(_NAVIGATEUR)
                except Exception:
                    pass
            _NAVIGATEUR = nav
            _APPAREILS = list(casts)
            _DERNIERE = time.time()
        except Exception:
            _APPAREILS = []
        return _APPAREILS


def _parmi(appareils, nom):
    """Meilleur appareil d une liste pour un nom donne, ou None."""
    from difflib import SequenceMatcher
    cible = sans_accents(str(nom).lower()).strip()
    meilleur, note_max = None, 0.0
    for c in appareils:
        n = sans_accents(str(c.cast_info.friendly_name).lower())
        if cible in n or n in cible:
            return c
        note = SequenceMatcher(None, cible, n).ratio()
        if note > note_max:
            meilleur, note_max = c, note
    return meilleur if note_max >= 0.55 else None


def _choisir(nom):
    """Retrouve un appareil par son nom, meme approximatif.

    La decouverte est bornee dans le temps : un televiseur occupe ou lent a
    repondre peut manquer a l appel, et l on repondait alors qu il n existe
    pas. On refait donc une decouverte complete avant d abandonner — quelques
    secondes valent mieux qu un refus injustifie.
    """
    appareils = _decouvrir()
    if not nom:
        return appareils[0] if appareils else None

    trouve = _parmi(appareils, nom) if appareils else None
    if trouve is not None:
        return trouve

    complets = _decouvrir(forcer=True)
    if not complets or len(complets) == len(appareils or []):
        # Rien de neuf : inutile d insister.
        return _parmi(complets, nom) if complets else None
    return _parmi(complets, nom)


# ------------------------------------------------------------------ outils

@outil(
    nom="lister_ecrans",
    description="Enumere les ecrans Chromecast disponibles sur le reseau. "
                "Pour 'quels ecrans tu vois', 'liste les chromecasts'.",
    parametres={"type": "object", "properties": {}, "required": []},
    lent=True,
    phrase_attente="Je cherche les ecrans.",
)
def lister_ecrans() -> str:
    appareils = _decouvrir(forcer=True)
    if not appareils:
        return "Je ne vois aucun ecran Chromecast sur le reseau."
    noms = [str(c.cast_info.friendly_name) for c in appareils]
    if len(noms) == 1:
        return f"Un seul ecran : {noms[0]}."
    return "Ecrans disponibles : " + ", ".join(noms[:-1]) + " et " + noms[-1] + "."


@outil(
    nom="caster_jarvis",
    description=(
        "Affiche l'interface de Jarvis sur un ecran Chromecast. Pour 'affiche "
        "toi sur la tele', 'caste sur le videoprojecteur', 'mets ton interface "
        "sur la tv du salon'. Le nom de l'ecran peut etre approximatif."
    ),
    parametres={
        "type": "object",
        "properties": {
            "ecran": {"type": "string",
                      "description": "Nom de l ecran vise. Vide = le premier trouve."},
            "interface": {"type": "string",
                          "description": "jarvis ou mother. Vide = celle en cours."},
        },
        "required": [],
    },
    lent=True,
    phrase_attente="Je prepare l affichage.",
)
def caster_jarvis(ecran: str = "", interface: str = "") -> str:
    appareil = _choisir(ecran)
    if appareil is None:
        if not _decouvrir():
            return "Je ne vois aucun ecran Chromecast sur le reseau."
        return f"Je ne trouve pas d ecran nomme {ecran}."

    hote_cast = appareil.cast_info.host
    adresse = _adresse_pour(hote_cast)
    port = int(reglage("hud.port", 8770))

    # Quelle page ? Celle demandee, sinon celle qui correspond au mode courant.
    page = (interface or "").strip().lower()
    if page not in ("jarvis", "mother"):
        page = ("mother" if reglage("assistant.personnalite", "") == "mere"
                else "jarvis")
    # Le parametre dit a la page qu elle est affichee ailleurs : elle jouera
    # alors la voix elle-meme. Sans lui, la parole resterait sur le PC, car
    # c est le televiseur qui charge la page et non une recopie de l ecran.
    url = (f"http://{adresse}:{port}/" + ("mother" if page == "mother" else "")
           + "?voix=1")

    try:
        from pychromecast.controllers.dashcast import DashCastController
        appareil.wait(timeout=12)
        controleur = DashCastController()
        appareil.register_handler(controleur)
        controleur.load_url(url, force=True)
    except Exception as e:
        return f"Echec de l affichage : {e}"

    nom = str(appareil.cast_info.friendly_name)
    return f"Interface envoyee sur {nom}."


@outil(
    nom="arreter_cast",
    description="Coupe l'affichage de Jarvis sur un ecran Chromecast. "
                "Pour 'arrete le cast', 'enleve toi de la tele'.",
    parametres={
        "type": "object",
        "properties": {
            "ecran": {"type": "string",
                      "description": "Nom de l ecran. Vide = tous les ecrans."},
        },
        "required": [],
    },
    lent=True,
)
def arreter_cast(ecran: str = "") -> str:
    if ecran:
        appareil = _choisir(ecran)
        cibles = [appareil] if appareil else []
    else:
        cibles = _decouvrir()

    if not cibles:
        return "Aucun ecran a arreter."

    arretes = []
    for c in cibles:
        try:
            c.wait(timeout=8)
            if c.app_id:                    # quelque chose tourne dessus
                c.quit_app()
                arretes.append(str(c.cast_info.friendly_name))
        except Exception:
            continue
    if not arretes:
        return "Aucun ecran n affichait quelque chose."
    return "Affichage coupe sur " + ", ".join(arretes) + "."
