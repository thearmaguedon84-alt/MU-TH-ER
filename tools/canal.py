"""Les chaines de myCANAL, par leur nom, jusque sur la tele.

Trois choses ont ete relevees en observant le trafic reel, et non devinees :

- la liste des chaines arrive dans la reponse a `InitLiveTV`, chacune portant
  un **EpgId** (312 = TF1, 154 = Arte) ;
- une chaine s'ouvre en ajoutant `?channel=<EpgId>` a l'adresse de la grille ;
- une fois le lecteur en route, la page sait ouvrir sa propre session de
  diffusion, ce qui met le recepteur CANAL+ sur la tele.

Mis bout a bout : « mets Arte sur la tele du bas » devient une suite d'actions
deterministes, sans jamais chercher un bouton dans une page.

L'index est garde sur disque, comme celui des films : le relire coute une
seconde, le reconstruire en coute trente.
"""
import json
import os
import time

from core.registre import outil
from core.util import sans_accents
from tools.navigateur_cast import _brancher, caster_service, demarrer_chrome

GRILLE = ("https://www.canalplus.com/live/tab/live-tv/en-direct-v5/"
          "pid114075-toutes-les-chaines-avec-multilive.html")

_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      ".cache_canal.json")
_AGE_MAX = 30 * 24 * 3600
_INDEX = None


# --------------------------------------------------------------- index

def _extraire(donnees):
    """Chaines contenues dans une reponse InitLiveTV : [(nom, epgid, numero)]."""
    try:
        groupes = donnees["ServiceResponse"]["OutData"]["PDS"]["ChannelsGroups"]
        groupes = groupes["ChannelsGroup"]
    except Exception:
        return []
    if isinstance(groupes, dict):
        groupes = [groupes]

    chaines, vus = [], set()
    for g in groupes:
        lot = g.get("Channels") or []
        if isinstance(lot, dict):
            lot = lot.get("Channel") or []
        for c in lot:
            epg = str(c.get("EpgId") or "").strip()
            nom = (c.get("Name") or "").strip()
            if not epg or not nom or epg in vus:
                continue
            vus.add(epg)
            chaines.append({"nom": nom, "epg": epg,
                            "numero": str(c.get("NumZap") or ""),
                            "castable": str(c.get("IsCastable")) == "true"})
    return chaines


def _relever():
    """Ouvre la grille et retient la reponse qui porte la liste des chaines.

    On ne rejoue pas la requete nous-memes : elle demande des jetons que seule
    la page detient. On la laisse partir, et on lit la reponse au passage.
    """
    if not demarrer_chrome():
        return []
    cdp = _brancher()
    if cdp is None:
        return []
    try:
        cdp.demander("Network.enable", {"maxPostDataSize": 1024})
        cdp.vider()
        cdp.demander("Page.navigate", {"url": GRILLE})

        identifiants = {}
        t0 = time.time()
        while time.time() - t0 < 40:
            time.sleep(2)
            for e in cdp.vider():
                p = e.get("params", {})
                if e.get("method") == "Network.responseReceived":
                    url = (p.get("response") or {}).get("url", "")
                    if "InitLiveTV" in url:
                        identifiants[p.get("requestId")] = True
            if identifiants:
                time.sleep(2)
                break

        for ident in identifiants:
            corps = cdp.demander("Network.getResponseBody",
                                 {"requestId": ident}, attente=25)
            texte = (corps.get("result", {}) or {}).get("body") or ""
            if not texte:
                continue
            try:
                chaines = _extraire(json.loads(texte))
            except Exception:
                continue
            if chaines:
                return chaines
        return []
    finally:
        cdp.fermer()


def index(forcer=False):
    """Chaines connues, depuis le disque si l'index est encore frais."""
    global _INDEX
    if _INDEX is not None and not forcer:
        return _INDEX

    if not forcer and os.path.exists(_CACHE):
        if time.time() - os.path.getmtime(_CACHE) < _AGE_MAX:
            try:
                with open(_CACHE, encoding="utf-8") as f:
                    _INDEX = json.load(f)
                if _INDEX:
                    return _INDEX
            except Exception:
                pass

    chaines = _relever()
    if chaines:
        _INDEX = chaines
        try:
            with open(_CACHE, "w", encoding="utf-8") as f:
                json.dump(chaines, f, ensure_ascii=False)
        except Exception:
            pass
    elif _INDEX is None:
        _INDEX = []
    return _INDEX


# --------------------------------------------------------------- recherche

def _clef(texte):
    t = sans_accents((texte or "").lower())
    # « canal+ » et « canal plus » doivent se rejoindre, comme « m6 » et « m 6 »
    t = t.replace("+", " plus ")
    return " ".join(t.split())


def _note(demande, nom):
    from difflib import SequenceMatcher
    d, n = _clef(demande), _clef(nom)
    if not d or not n:
        return 0.0
    if d == n:
        return 1.0
    # Un nom court comme « tf1 » ne doit pas se noyer dans « tf1 series films »
    mots = n.split()
    if d in mots:
        return 0.95
    if n.startswith(d + " ") or n.endswith(" " + d):
        return 0.9
    if d in n:
        return 0.8
    return SequenceMatcher(None, d, n).ratio()


def chercher(demande):
    """Meilleure chaine pour un nom parle, ou None."""
    chaines = index()
    if not chaines:
        return None
    notee = sorted(((_note(demande, c["nom"]), c) for c in chaines),
                   key=lambda x: x[0], reverse=True)
    meilleure, chaine = notee[0]
    return chaine if meilleure >= 0.7 else None


# --------------------------------------------------------------- outils

@outil(
    nom="canal_chaines",
    description="Liste les chaines disponibles sur myCANAL.",
    parametres={"type": "object", "properties": {}, "required": []},
    lent=True,
    phrase_attente="Je releve les chaines.",
)
def canal_chaines() -> str:
    chaines = index()
    if not chaines:
        return ("Je n arrive pas a lire la liste des chaines. Es-tu connecte a "
                "myCANAL dans mon navigateur ?")
    noms = [c["nom"] for c in chaines[:40]]
    return f"{len(chaines)} chaines, dont : " + ", ".join(noms) + "."


@outil(
    nom="canal_chaine",
    description=(
        "Ouvre une chaine de myCANAL et, si un ecran est demande, la diffuse "
        "dessus : la tele lance alors l'application CANAL+. Pour 'mets Arte "
        "sur la tele du bas', 'lance TF1', 'passe sur France 2'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "chaine": {"type": "string", "description": "Nom de la chaine."},
            "ecran": {"type": "string",
                      "description": "Nom de l ecran. Vide = sur le PC."},
        },
        "required": ["chaine"],
    },
    lent=True,
    phrase_attente="Je lance la chaine.",
)
def canal_chaine(chaine: str, ecran: str = "") -> str:
    trouvee = chercher(chaine)
    if trouvee is None:
        if not index():
            return ("Je n arrive pas a lire la liste des chaines. Es-tu "
                    "connecte a myCANAL dans mon navigateur ?")
        return f"Je ne trouve pas de chaine qui ressemble a {chaine}."

    url = f"{GRILLE}?channel={trouvee['epg']}"

    if not ecran:
        if not demarrer_chrome(url=url):
            return "Je n arrive pas a lancer le navigateur."
        cdp = _brancher()
        if cdp is None:
            return "Le navigateur ne repond pas."
        try:
            cdp.demander("Page.navigate", {"url": url})
            time.sleep(2)
            cdp.demander("Page.bringToFront")
        finally:
            cdp.fermer()
        return f"{trouvee['nom']} sur le PC."

    if not trouvee["castable"]:
        return f"{trouvee['nom']} ne se laisse pas diffuser."

    # caster_service ouvre la page, attend que le lecteur charge son SDK, puis
    # lui demande la session : le recepteur CANAL+ demarre sur la tele.
    reponse = caster_service(url=url, ecran=ecran)
    if reponse.startswith("C est diffuse"):
        return f"{trouvee['nom']}, {reponse[0].lower()}{reponse[1:]}"
    return reponse
