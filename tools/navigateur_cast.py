"""Diffusion depuis Chrome, pilote par le protocole de debogage.

Certaines plateformes — myCANAL, Netflix, Prime Video — refusent qu'on lance
une lecture de l'exterieur : leur recepteur exige une authentification que
seule leur page web detient. La contourner est impossible ; en revanche on
peut faire faire le travail a Chrome lui-meme.

Chrome expose un domaine `Cast` dans son protocole de debogage : on peut y
enumerer les ecrans, en choisir un, puis declencher la diffusion. La page fait
alors l'authentification comme si tu avais clique toi-meme.

Jarvis garde son propre profil Chrome (dossier .chrome_jarvis) : tu t'y
connectes une fois a tes services, et il reste connecte. Ton Chrome habituel
n'est pas touche.
"""
import json
import os
import subprocess
import threading
import time
import urllib.request

from core.config import reglage
from core.registre import outil
from core.util import sans_accents

PORT_DEBUG = 9333
_PROFIL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       ".chrome_jarvis")
_PROCESSUS = None
_VERROU = threading.Lock()


def _chrome():
    """Chemin de l'executable Chrome."""
    depuis_config = reglage("navigateur.chrome", "")
    if depuis_config and os.path.exists(depuis_config):
        return depuis_config
    for p in (r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(p):
            return p
    return None


def _repond():
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT_DEBUG}/json/version", timeout=3)
        return True
    except Exception:
        return False


def demarrer_chrome(url="about:blank", visible=True):
    """Lance le Chrome de Jarvis s'il ne tourne pas deja."""
    global _PROCESSUS
    with _VERROU:
        if _repond():
            return True
        exe = _chrome()
        if exe is None:
            return False
        os.makedirs(_PROFIL, exist_ok=True)
        args = [
            exe,
            f"--remote-debugging-port={PORT_DEBUG}",
            "--remote-allow-origins=*",
            f"--user-data-dir={_PROFIL}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate",
            url,
        ]
        if not visible:
            args.insert(1, "--headless=new")
        try:
            _PROCESSUS = subprocess.Popen(args)
        except Exception:
            return False
        for _ in range(20):
            time.sleep(0.8)
            if _repond():
                return True
        return False


def _connexion(cible=None):
    """Ouvre une connexion au protocole de debogage sur un onglet."""
    import websocket
    pages = json.loads(
        urllib.request.urlopen(f"http://127.0.0.1:{PORT_DEBUG}/json/list",
                               timeout=8).read())
    pages = [p for p in pages if p.get("type") == "page"]
    if not pages:
        return None
    page = pages[0]
    if cible:
        for p in pages:
            if cible in (p.get("url") or ""):
                page = p
                break
    return websocket.create_connection(page["webSocketDebuggerUrl"],
                                       timeout=25, suppress_origin=True)


def _dialoguer(ws, methode, params=None, identifiant=1):
    ws.send(json.dumps({"id": identifiant, "method": methode,
                        "params": params or {}}))


def ecrans(delai=20):
    """Ecrans que Chrome voit : [(nom, identifiant)]."""
    if not demarrer_chrome():
        return []
    ws = None
    try:
        ws = _connexion()
        if ws is None:
            return []
        _dialoguer(ws, "Cast.enable")
        t0 = time.time()
        vus = []
        while time.time() - t0 < delai:
            try:
                ws.settimeout(3)
                msg = json.loads(ws.recv())
            except Exception:
                continue
            if msg.get("method") == "Cast.sinksUpdated":
                lot = msg["params"].get("sinks") or msg["params"].get("sinkNames") or []
                trouves = [(s.get("name"), s.get("id")) if isinstance(s, dict) else (s, s)
                           for s in lot]
                # Chrome decouvre les ecrans progressivement : le premier lot
                # n en contient souvent qu un. On garde le plus complet et on
                # laisse le temps aux suivants d arriver.
                if len(trouves) > len(vus):
                    vus = trouves
                    t0 = min(t0, time.time() - delai + 6)
        return vus
    except Exception:
        return []
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass


def _choisir_ecran(nom, disponibles):
    """Ecran dont le nom ressemble le plus a `nom`."""
    from difflib import SequenceMatcher
    if not disponibles:
        return None
    if not nom:
        return disponibles[0]
    cible = sans_accents(str(nom).lower()).strip()
    meilleur, note_max = None, 0.0
    for n, ident in disponibles:
        nn = sans_accents(str(n).lower())
        if cible in nn or nn in cible:
            return (n, ident)
        note = SequenceMatcher(None, cible, nn).ratio()
        if note > note_max:
            meilleur, note_max = (n, ident), note
    return meilleur if note_max >= 0.55 else None


@outil(
    nom="navigateur_ecrans",
    description="Enumere les ecrans que le navigateur de Jarvis peut utiliser "
                "pour diffuser une page web.",
    parametres={"type": "object", "properties": {}, "required": []},
    lent=True,
    phrase_attente="Je regarde les ecrans.",
)
def navigateur_ecrans() -> str:
    vus = ecrans()
    if not vus:
        return "Le navigateur ne voit aucun ecran."
    return "Ecrans disponibles : " + ", ".join(n for n, _ in vus) + "."


@outil(
    nom="diffuser_page",
    description=(
        "Ouvre une page web dans le navigateur de Jarvis et la diffuse sur un "
        "ecran. Sert pour les services qui refusent d'etre lances autrement, "
        "comme myCANAL, Netflix ou Prime Video : c'est le navigateur qui "
        "s'authentifie. La premiere fois, il faut se connecter au service dans "
        "ce navigateur."
    ),
    parametres={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Adresse de la page a diffuser."},
            "ecran": {"type": "string", "description": "Nom de l ecran vise."},
        },
        "required": ["url"],
    },
    lent=True,
    phrase_attente="Je prepare la diffusion.",
)
def diffuser_page(url: str, ecran: str = "") -> str:
    if not demarrer_chrome(url=url):
        return "Je n arrive pas a lancer le navigateur."

    disponibles = ecrans()
    if not disponibles:
        return "Le navigateur ne voit aucun ecran."

    choix = _choisir_ecran(ecran, disponibles)
    if choix is None:
        noms = ", ".join(n for n, _ in disponibles)
        return f"Je ne trouve pas l ecran {ecran}. Disponibles : {noms}."

    nom_ecran, _ident = choix
    ws = None
    try:
        ws = _connexion()
        if ws is None:
            return "Le navigateur ne repond pas."

        # Ouvrir la page demandee
        _dialoguer(ws, "Page.navigate", {"url": url}, identifiant=1)
        time.sleep(4)

        # Choisir l'ecran puis lancer la diffusion de l onglet
        _dialoguer(ws, "Cast.enable", identifiant=2)
        time.sleep(2)
        _dialoguer(ws, "Cast.setSinkToUse", {"sinkName": nom_ecran}, identifiant=3)
        time.sleep(1)
        _dialoguer(ws, "Cast.startTabMirroring", {"sinkName": nom_ecran}, identifiant=4)

        erreurs = []
        t0 = time.time()
        while time.time() - t0 < 12:
            try:
                ws.settimeout(2)
                msg = json.loads(ws.recv())
            except Exception:
                continue
            if msg.get("method") == "Cast.issueUpdated":
                erreurs.append(msg["params"].get("issueMessage", ""))
            if msg.get("id") == 4:
                if "error" in msg:
                    return f"Diffusion refusee : {msg['error'].get('message', '')[:70]}"
                break
        if erreurs:
            return f"Diffusion signalee en erreur : {erreurs[0][:70]}"
    except Exception as e:
        return f"Echec : {str(e)[:80]}"
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    return f"Page diffusee sur {nom_ecran}."


@outil(
    nom="arreter_diffusion_page",
    description="Arrete la diffusion lancee depuis le navigateur de Jarvis.",
    parametres={"type": "object", "properties": {}, "required": []},
)
def arreter_diffusion_page() -> str:
    if not _repond():
        return "Le navigateur de Jarvis ne tourne pas."
    ws = None
    try:
        ws = _connexion()
        if ws is None:
            return "Le navigateur ne repond pas."
        _dialoguer(ws, "Cast.stopCasting", {"sinkName": ""}, identifiant=1)
        time.sleep(1)
        return "Diffusion arretee."
    except Exception as e:
        return f"Echec : {str(e)[:70]}"
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass
