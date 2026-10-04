"""Diffusion depuis Chrome, pilote par le protocole de debogage.

Deux facons de mettre une page sur un ecran, et elles ne se valent pas.

La premiere recopie l'onglet : simple, universelle, mais c'est une image, avec
la perte de qualite et la latence que cela suppose. Les services proteges y
affichent souvent un carre noir.

La seconde est la bonne : demander a la page d'ouvrir elle-meme sa session de
diffusion, par son propre SDK Cast. Le recepteur maison du service se lance
alors sur la tele — CANAL+, YouTube, ce que la page sait faire — avec ses
droits et ses jetons. Normalement un selecteur d'ecran s'affiche et attend un
clic ; Cast.setSinkToUse permet de designer l'ecran a l'avance, et le selecteur
ne parait pas. Rien n'est contourne : on remplace le clic, pas l'autorisation.

Jarvis garde son propre profil Chrome (.chrome_jarvis) : tu t'y connectes une
fois a tes services, et il reste connecte. Ton Chrome habituel n'est pas
touche.
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


# --------------------------------------------------------------- navigateur

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


# --------------------------------------------------------------- protocole

class Cdp:
    """Un dialogue avec Chrome : on demande, et on ecoute en parallele.

    Le protocole melange sur une meme connexion les reponses aux demandes et
    les evenements spontanes. Un fil dedie les trie au fur et a mesure, ce qui
    evite de rater un evenement pendant qu'on attend une reponse.
    """

    def __init__(self, ws):
        self.ws = ws
        self.numero = 0
        self.evenements = []
        self.reponses = {}
        self._envoi = threading.Lock()
        self._collecte = threading.Lock()
        self.actif = True
        self.fil = threading.Thread(target=self._lire, daemon=True)
        self.fil.start()

    def _lire(self):
        while self.actif:
            try:
                self.ws.settimeout(1)
                msg = json.loads(self.ws.recv())
            except Exception:
                continue
            if "id" in msg:
                self.reponses[msg["id"]] = msg
            elif msg.get("method"):
                with self._collecte:
                    # Garde-fou : une page bavarde ne doit pas remplir la memoire.
                    if len(self.evenements) < 6000:
                        self.evenements.append(msg)

    def demander(self, methode, params=None, attente=15):
        with self._envoi:
            self.numero += 1
            ident = self.numero
            try:
                self.ws.send(json.dumps({"id": ident, "method": methode,
                                         "params": params or {}}))
            except Exception:
                return {}
        t0 = time.time()
        while time.time() - t0 < attente:
            if ident in self.reponses:
                return self.reponses.pop(ident)
            time.sleep(0.03)
        return {}

    def evaluer(self, expression, contexte=None, geste=False, attente=45):
        """Execute du JavaScript et rend la valeur, ou None."""
        params = {"expression": expression, "returnByValue": True,
                  "awaitPromise": True, "userGesture": geste}
        if contexte is not None:
            params["contextId"] = contexte
        r = self.demander("Runtime.evaluate", params, attente=attente)
        res = r.get("result", {})
        if res.get("exceptionDetails"):
            return None
        return (res.get("result", {}) or {}).get("value")

    def vider(self):
        with self._collecte:
            lot, self.evenements = self.evenements, []
        return lot

    def fermer(self):
        self.actif = False
        try:
            self.ws.close()
        except Exception:
            pass


def _page(cible=""):
    """Onglet a piloter : celui demande, sinon le premier qui montre quelque chose."""
    try:
        pages = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{PORT_DEBUG}/json/list", timeout=8).read())
    except Exception:
        return None
    pages = [p for p in pages if p.get("type") == "page"]
    if not pages:
        return None
    if cible:
        for p in pages:
            if cible.lower() in (p.get("url") or "").lower():
                return p
    for p in pages:
        if (p.get("url") or "about:blank") not in ("about:blank", "chrome://newtab/"):
            return p
    return pages[0]


def _brancher(cible=""):
    import websocket
    p = _page(cible)
    if p is None:
        return None
    try:
        ws = websocket.create_connection(p["webSocketDebuggerUrl"],
                                         timeout=25, suppress_origin=True,
                                         max_size=12 * 1024 * 1024)
    except Exception:
        return None
    return Cdp(ws)


def _connexion(cible=None):
    """Ancienne interface : une simple connexion websocket."""
    cdp = _brancher(cible or "")
    return cdp.ws if cdp else None


# --------------------------------------------------------------- ecrans

def _sinks(cdp, delai=18):
    """Ecrans que Chrome voit, en laissant la decouverte se completer.

    Chrome les annonce un par un : s'arreter au premier lot n'en montre qu'un.
    """
    cdp.demander("Cast.enable")
    t0 = time.time()
    vus = []
    while time.time() - t0 < delai:
        time.sleep(1)
        for e in cdp.vider():
            if e.get("method") == "Cast.sinksUpdated":
                lot = e["params"].get("sinks") or e["params"].get("sinkNames") or []
                trouves = [(s.get("name"), s.get("id")) if isinstance(s, dict) else (s, s)
                           for s in lot]
                if len(trouves) > len(vus):
                    vus = trouves
                    # Un lot plus complet vient d'arriver : laisser une chance
                    # aux suivants, sans repartir de zero.
                    t0 = min(t0, time.time() - delai + 6)
    return vus


def ecrans(delai=18):
    """Ecrans que Chrome voit : [(nom, identifiant)]."""
    if not demarrer_chrome():
        return []
    cdp = _brancher()
    if cdp is None:
        return []
    try:
        return _sinks(cdp, delai)
    finally:
        cdp.fermer()


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


# --------------------------------------------------------------- diffusion native

def _contexte_cast(cdp, patience=30):
    """Contexte JavaScript qui detient le SDK Cast.

    Le SDK n'est charge que lorsqu'un lecteur tourne : on laisse donc a la page
    le temps de le mettre en place avant de conclure.
    """
    cdp.demander("Runtime.enable")
    t0 = time.time()
    while time.time() - t0 < patience:
        time.sleep(2)
        contextes = [e["params"]["context"] for e in cdp.evenements
                     if e.get("method") == "Runtime.executionContextCreated"]
        for c in contextes:
            if cdp.evaluer("(typeof cast !== 'undefined' && !!cast.framework)",
                           contexte=c["id"], attente=8) is True:
                return c["id"]
    return None


@outil(
    nom="caster_service",
    description=(
        "Diffuse un service video sur un ecran en demandant a la page d'ouvrir "
        "elle-meme sa session : la tele lance alors l'application du service, "
        "pas une recopie d'ecran. Marche pour myCANAL, YouTube et tout site "
        "qui sait caster. Le lecteur doit tourner dans le navigateur de "
        "Jarvis, et l'utilisateur y etre connecte au service."
    ),
    parametres={
        "type": "object",
        "properties": {
            "url": {"type": "string",
                    "description": "Page a ouvrir avant de diffuser. Vide = page actuelle."},
            "ecran": {"type": "string", "description": "Nom de l ecran vise."},
        },
        "required": [],
    },
    lent=True,
    phrase_attente="Je prepare la diffusion.",
)
def caster_service(url: str = "", ecran: str = "") -> str:
    if not demarrer_chrome(url=url or "about:blank"):
        return "Je n arrive pas a lancer le navigateur."

    cdp = _brancher()
    if cdp is None:
        return "Le navigateur ne repond pas."

    try:
        if url:
            cdp.demander("Page.navigate", {"url": url})
            time.sleep(6)

        contexte = _contexte_cast(cdp)
        if contexte is None:
            return ("Le lecteur n est pas pret : lance la lecture dans mon "
                    "navigateur, ou verifie que tu y es connecte au service.")

        disponibles = _sinks(cdp)
        if not disponibles:
            return "Le navigateur ne voit aucun ecran."

        choix = _choisir_ecran(ecran, disponibles)
        if choix is None:
            noms = ", ".join(n for n, _ in disponibles)
            return f"Je ne trouve pas l ecran {ecran}. Disponibles : {noms}."
        nom_ecran = choix[0]

        deja = cdp.evaluer(
            "!!cast.framework.CastContext.getInstance().getCurrentSession()",
            contexte=contexte, attente=10)
        if deja is True:
            return f"Une diffusion est deja en cours. Je ne touche a rien."

        # Designer l'ecran avant la demande : sans cela, Chrome ouvre son
        # selecteur et attend un clic.
        cdp.demander("Cast.setSinkToUse", {"sinkName": nom_ecran})
        time.sleep(1)

        # Le geste utilisateur est simule : le SDK refuse une demande qui n'en
        # vient pas, exactement comme il refuserait un script de page.
        resultat = cdp.evaluer(
            "cast.framework.CastContext.getInstance().requestSession()"
            ".then(() => 'ok').catch(e => 'refus:' + (e.code || e))",
            contexte=contexte, geste=True, attente=60)

        if resultat != "ok":
            motif = str(resultat or "sans reponse").replace("refus:", "")
            if "cancel" in motif.lower():
                return "La diffusion a ete annulee."
            return f"Le service a refuse la diffusion : {motif[:60]}"

        time.sleep(3)
        appareil = cdp.evaluer(
            "(() => { const s = cast.framework.CastContext.getInstance()"
            ".getCurrentSession(); return s ? s.getCastDevice().friendlyName : null; })()",
            contexte=contexte, attente=10)
        return f"C est diffuse sur {appareil or nom_ecran}."
    except Exception as e:
        return f"Echec : {str(e)[:80]}"
    finally:
        cdp.fermer()


@outil(
    nom="arreter_caster_service",
    description="Ferme la session de diffusion ouverte par la page.",
    parametres={"type": "object", "properties": {}, "required": []},
    lent=True,
)
def arreter_caster_service() -> str:
    if not _repond():
        return "Le navigateur de Jarvis ne tourne pas."
    cdp = _brancher()
    if cdp is None:
        return "Le navigateur ne repond pas."
    try:
        contexte = _contexte_cast(cdp, patience=8)
        if contexte is None:
            return "Aucune diffusion en cours."
        fait = cdp.evaluer(
            "(() => { const c = cast.framework.CastContext.getInstance();"
            " if (!c.getCurrentSession()) return 'rien';"
            " c.endCurrentSession(true); return 'ok'; })()",
            contexte=contexte, geste=True, attente=20)
        return "Diffusion arretee." if fait == "ok" else "Aucune diffusion en cours."
    finally:
        cdp.fermer()


# --------------------------------------------------------------- recopie d'onglet

@outil(
    nom="diffuser_page",
    description=(
        "Recopie un onglet du navigateur de Jarvis sur un ecran. Solution de "
        "repli : l'image est retransmise telle quelle, avec une qualite "
        "moindre, et les services proteges y affichent souvent un ecran noir. "
        "Preferer caster_service quand la page sait diffuser elle-meme."
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

    cdp = _brancher()
    if cdp is None:
        return "Le navigateur ne repond pas."
    try:
        cdp.demander("Page.navigate", {"url": url})
        time.sleep(4)

        disponibles = _sinks(cdp)
        if not disponibles:
            return "Le navigateur ne voit aucun ecran."
        choix = _choisir_ecran(ecran, disponibles)
        if choix is None:
            noms = ", ".join(n for n, _ in disponibles)
            return f"Je ne trouve pas l ecran {ecran}. Disponibles : {noms}."
        nom_ecran = choix[0]

        cdp.demander("Cast.setSinkToUse", {"sinkName": nom_ecran})
        time.sleep(1)
        reponse = cdp.demander("Cast.startTabMirroring", {"sinkName": nom_ecran},
                               attente=25)
        if "error" in reponse:
            return f"Diffusion refusee : {reponse['error'].get('message','')[:70]}"
        return f"Page diffusee sur {nom_ecran}."
    except Exception as e:
        return f"Echec : {str(e)[:80]}"
    finally:
        cdp.fermer()


@outil(
    nom="arreter_diffusion_page",
    description="Arrete la recopie d'onglet lancee depuis le navigateur de Jarvis.",
    parametres={"type": "object", "properties": {}, "required": []},
)
def arreter_diffusion_page() -> str:
    if not _repond():
        return "Le navigateur de Jarvis ne tourne pas."
    cdp = _brancher()
    if cdp is None:
        return "Le navigateur ne repond pas."
    try:
        cdp.demander("Cast.stopCasting", {"sinkName": ""})
        return "Diffusion arretee."
    finally:
        cdp.fermer()
