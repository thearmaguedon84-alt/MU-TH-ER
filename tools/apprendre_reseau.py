"""Apprendre une action web en observant le reseau, puis savoir la refaire.

Beaucoup de services n'ont pas d'API publique : myCANAL, Prime Video, un
intranet, un site de reservation. Mais quand tu cliques, ton navigateur envoie
des requetes precises. Ces requetes SONT une interface — non documentee, mais
bien reelle.

Ce module regarde ce que Chrome envoie pendant que tu fais l'action a la main,
en retient l'essentiel, et sait le refaire ensuite. Deux choix comptent :

- On observe le RESEAU, pas l'ecran. Un bouton qui change de place, de couleur
  ou de langue ne casse rien. Seule une refonte de l'API du service casserait,
  et c'est bien plus rare qu'une refonte graphique.

- On rejoue DEPUIS la page, avec fetch(). La requete part donc du navigateur
  lui-meme, avec ses cookies, ses jetons, son origine et sa session. Rien a
  recopier, rien a rafraichir, aucun jeton a voler : si tu es connecte dans le
  Chrome de Jarvis, ca marche ; si tu ne l'es pas, ca echoue proprement.

Les recettes vivent dans recettes/. Elles peuvent contenir des identifiants de
session : le dossier n'est pas versionne.
"""
import json
import os
import re
import threading
import time

from core.registre import outil
from tools.navigateur_cast import PORT_DEBUG, demarrer_chrome  # noqa: F401

_RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOSSIER = os.path.join(_RACINE, "recettes")

# Enregistrements en cours, par nom.
_EN_COURS = {}

# Hotes qui ne font que mesurer l'audience ou remonter des erreurs : ils
# polluent l'enregistrement sans jamais rien declencher.
BRUIT = (
    "google-analytics", "googletagmanager", "doubleclick", "googlesyndication",
    "scorecardresearch", "sentry.io", "datadoghq", "newrelic", "bam.nr-data",
    "hotjar", "segment.io", "segment.com", "amplitude", "mixpanel", "branch.io",
    "criteo", "facebook.net", "facebook.com/tr", "gstatic.com", "clarity.ms",
    "adobedtm", "omtrdc.net", "demdex.net", "adsrvr", "onetrust", "cookielaw",
    "optimizely", "quantserve", "taboola", "outbrain", "smartadserver",
    "casalemedia", "pubmatic", "rubiconproject", "adnxs", "id5-sync",
)

# Types de ressources qui ne declenchent aucune action.
TYPES_INUTILES = {
    "Image", "Font", "Stylesheet", "Media", "Ping", "Manifest", "TextTrack",
    "CSPViolationReport", "Prefetch", "SignedExchange", "Preflight",
}

# En-tetes que fetch() refuse qu'on fixe, ou que le navigateur remplit mieux
# lui-meme. Les en-tetes d'authentification, eux, sont precieux : on les garde.
ENTETES_INTERDITES = {
    "host", "connection", "content-length", "cookie", "origin", "referer",
    "user-agent", "accept-encoding", "accept-charset", "date", "dnt",
    "expect", "keep-alive", "te", "trailer", "transfer-encoding", "upgrade",
    "via", "proxy-authorization", "access-control-request-method",
    "access-control-request-headers",
}


# Le client du protocole vit avec le navigateur : un seul exemplaire, partage.
from tools.navigateur_cast import Cdp, _brancher, _page  # noqa: E402,F401


# --------------------------------------------------------------- tri du bruit

def _interessante(requete, type_ressource):
    url = requete.get("url") or ""
    if not url.startswith("http"):
        return False
    if type_ressource in TYPES_INUTILES:
        return False
    if any(b in url for b in BRUIT):
        return False
    # Les fichiers de lecture video arrivent par milliers et ne declenchent rien.
    if re.search(r"\.(m3u8|mpd|ts|m4s|cmfv|cmfa|jpg|png|svg|woff2?|css|ico)(\?|$)", url):
        return False
    return True


def _rang(trame):
    """Ce qui declenche une action passe avant ce qui se contente de lire."""
    if trame["methode"] != "GET":
        return 0
    if "json" in (trame.get("mime") or ""):
        return 1
    return 2


# --------------------------------------------------------------- apprentissage

def _chemin(nom):
    os.makedirs(_DOSSIER, exist_ok=True)
    propre = re.sub(r"[^a-z0-9_-]+", "_", (nom or "").strip().lower()).strip("_")
    return os.path.join(_DOSSIER, f"{propre or 'sans_nom'}.json"), propre


@outil(
    nom="apprendre_action",
    description=(
        "Commence a observer ce que le navigateur envoie, pour apprendre une "
        "action que l'utilisateur va faire a la main et pouvoir la refaire "
        "ensuite tout seul. Pour 'apprends ce que je fais', 'regarde comment je "
        "lance myCanal', 'enregistre cette action'. Ensuite l'utilisateur fait "
        "l'action dans le navigateur de Jarvis, puis dit que c'est fini."
    ),
    parametres={
        "type": "object",
        "properties": {
            "nom": {"type": "string",
                    "description": "Nom court de l action, par exemple 'lancer canal sur la tv'."},
            "url": {"type": "string",
                    "description": "Page ou commencer. Vide = onglet actuel."},
        },
        "required": ["nom"],
    },
    lent=True,
    phrase_attente="Je me prepare a observer.",
)
def apprendre_action(nom: str, url: str = "") -> str:
    if not demarrer_chrome(url=url or "about:blank"):
        return "Je n arrive pas a lancer le navigateur."

    _, propre = _chemin(nom)
    if propre in _EN_COURS:
        return f"J observe deja {nom}."

    cdp = _brancher()
    if cdp is None:
        return "Le navigateur ne repond pas."

    if url:
        cdp.demander("Page.navigate", {"url": url})
        time.sleep(3)

    # 128 ko de corps : assez pour une requete d'action, pas pour une video.
    cdp.demander("Network.enable", {"maxPostDataSize": 131072})
    cdp.vider()
    _EN_COURS[propre] = {"cdp": cdp, "nom": nom, "debut": time.time()}

    return (f"J observe. Fais l action dans mon navigateur, puis dis-moi "
            f"que c est fini.")


@outil(
    nom="apprendre_terminer",
    description=(
        "Arrete l'observation du reseau et retient l'action apprise. "
        "Pour 'c est fini', 'j ai fini', 'tu as vu ?', 'retiens ca'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "nom": {"type": "string", "description": "Nom donne au depart."},
            "variable": {
                "type": "string",
                "description": ("Valeur qui devra changer d une fois sur l autre, "
                                "par exemple le titre cherche. Vide si l action "
                                "est toujours identique."),
            },
        },
        "required": ["nom"],
    },
    lent=True,
    phrase_attente="Je fais le tri.",
)
def apprendre_terminer(nom: str, variable: str = "") -> str:
    _, propre = _chemin(nom)
    session = _EN_COURS.pop(propre, None)
    if session is None:
        # Peut-etre un nom approchant : on ne fait pas perdre l'enregistrement.
        if len(_EN_COURS) == 1:
            propre, session = next(iter(_EN_COURS.items()))
            _EN_COURS.clear()
        else:
            return "Je n observais rien sous ce nom."

    cdp = session["cdp"]
    lot = cdp.vider()
    cdp.demander("Network.disable")
    cdp.fermer()

    # Reconstituer requetes et reponses.
    demandes, reponses = {}, {}
    origine = ""
    for e in lot:
        m = e.get("method")
        p = e.get("params", {})
        if m == "Network.requestWillBeSent":
            demandes[p.get("requestId")] = p
            if not origine and p.get("documentURL", "").startswith("http"):
                origine = p["documentURL"]
        elif m == "Network.responseReceived":
            reponses[p.get("requestId")] = p.get("response", {})

    trames = []
    for ident, p in demandes.items():
        r = p.get("request", {})
        if not _interessante(r, p.get("type", "")):
            continue
        rep = reponses.get(ident, {})
        statut = rep.get("status", 0)
        # Une requete qui a echoue n'apprend rien de bon.
        if statut and not (200 <= statut < 400):
            continue
        entetes = {k: v for k, v in (r.get("headers") or {}).items()
                   if k.lower() not in ENTETES_INTERDITES and not k.startswith(":")}
        trames.append({
            "methode": r.get("method", "GET"),
            "url": r.get("url"),
            "entetes": entetes,
            "corps": r.get("postData"),
            "mime": rep.get("mimeType", ""),
            "statut": statut,
            "instant": p.get("timestamp", 0),
        })

    if not trames:
        return ("Je n ai rien vu passer d utile. Soit l action n a rien envoye, "
                "soit elle s est faite dans un autre onglet.")

    trames.sort(key=lambda t: t["instant"])
    for t in trames:
        t.pop("instant", None)

    # Parametrage : on remplace la valeur variable par un emplacement nomme.
    marque = None
    if variable and variable.strip():
        v = variable.strip()
        from urllib.parse import quote
        formes = {v, quote(v), quote(v, safe=""), v.replace(" ", "+"),
                  v.replace(" ", "-"), v.lower(), v.replace(" ", "%20")}
        marque = "{{valeur}}"
        touchees = 0
        for t in trames:
            for f in sorted(formes, key=len, reverse=True):
                if f and f in (t["url"] or ""):
                    t["url"] = t["url"].replace(f, marque)
                    touchees += 1
                if f and t.get("corps") and f in t["corps"]:
                    t["corps"] = t["corps"].replace(f, marque)
                    touchees += 1
        if not touchees:
            marque = None

    recette = {
        "nom": session["nom"],
        "origine": origine,
        "variable": bool(marque),
        "trames": trames,
    }
    chemin, _ = _chemin(session["nom"])
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(recette, f, ensure_ascii=False, indent=1)

    actions = sum(1 for t in trames if t["methode"] != "GET")
    detail = f"{len(trames)} requetes retenues, dont {actions} qui agissent"
    if marque:
        detail += ", avec une valeur qui pourra changer"
    return f"C est appris : {session['nom']}. {detail}."


# --------------------------------------------------------------- restitution

def _rejouer_dans_page(cdp, trame, valeur):
    """Fait partir la requete depuis la page : cookies et session compris."""
    url = (trame["url"] or "").replace("{{valeur}}", valeur)
    corps = trame.get("corps")
    if corps:
        corps = corps.replace("{{valeur}}", valeur)

    options = {
        "method": trame["methode"],
        "headers": trame.get("entetes") or {},
        "credentials": "include",
        "redirect": "follow",
    }
    if corps is not None and trame["methode"] not in ("GET", "HEAD"):
        options["body"] = corps

    expression = (
        "(async () => { try {"
        f"  const r = await fetch({json.dumps(url)}, {json.dumps(options)});"
        "   return r.status;"
        "} catch (e) { return 'erreur: ' + e.message; } })()"
    )
    reponse = cdp.demander("Runtime.evaluate", {
        "expression": expression, "awaitPromise": True, "returnByValue": True,
    }, attente=30)
    return (reponse.get("result", {}).get("result", {}) or {}).get("value")


@outil(
    nom="refaire_action",
    description=(
        "Refait une action apprise en observant le reseau, sans cliquer nulle "
        "part. Pour 'refais ce que tu as appris', 'relance l action machin'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "nom": {"type": "string", "description": "Nom de l action apprise."},
            "valeur": {"type": "string",
                       "description": "Valeur variable, si l action en attend une."},
        },
        "required": ["nom"],
    },
    lent=True,
    phrase_attente="Je refais l action.",
)
def refaire_action(nom: str, valeur: str = "") -> str:
    chemin, propre = _chemin(nom)
    if not os.path.exists(chemin):
        # Tolerance sur le nom : on cherche le plus proche.
        from difflib import get_close_matches
        connus = [f[:-5] for f in os.listdir(_DOSSIER)] if os.path.isdir(_DOSSIER) else []
        proches = get_close_matches(propre, connus, n=1, cutoff=0.5)
        if not proches:
            return f"Je n ai pas appris d action nommee {nom}."
        chemin = os.path.join(_DOSSIER, proches[0] + ".json")

    with open(chemin, encoding="utf-8") as f:
        recette = json.load(f)

    if not demarrer_chrome(url=recette.get("origine") or "about:blank"):
        return "Je n arrive pas a lancer le navigateur."

    cdp = _brancher()
    if cdp is None:
        return "Le navigateur ne repond pas."

    try:
        # Se placer sur le bon site : c'est lui qui detient la session.
        origine = recette.get("origine") or ""
        if origine:
            actuel = cdp.demander("Runtime.evaluate", {
                "expression": "location.origin", "returnByValue": True})
            vu = (actuel.get("result", {}).get("result", {}) or {}).get("value") or ""
            from urllib.parse import urlparse
            if urlparse(origine).netloc not in vu:
                cdp.demander("Page.navigate", {"url": origine})
                time.sleep(5)

        statuts = []
        for t in recette["trames"]:
            statuts.append(_rejouer_dans_page(cdp, t, valeur))
            time.sleep(0.25)
    finally:
        cdp.fermer()

    reussies = sum(1 for s in statuts if isinstance(s, int) and 200 <= s < 400)
    refus = [s for s in statuts if s in (401, 403)]
    if refus:
        return ("Le service a refuse : il faut sans doute se reconnecter dans "
                "mon navigateur.")
    if not reussies:
        return "L action n a rien donne. Le service a peut-etre change."
    return f"C est fait. {reussies} requetes sur {len(statuts)} sont passees."


@outil(
    nom="actions_apprises",
    description="Liste les actions que Jarvis a apprises en observant le reseau.",
    parametres={"type": "object", "properties": {}, "required": []},
)
def actions_apprises() -> str:
    if not os.path.isdir(_DOSSIER):
        return "Je n ai encore rien appris."
    noms = []
    for f in sorted(os.listdir(_DOSSIER)):
        if not f.endswith(".json"):
            continue
        try:
            with open(os.path.join(_DOSSIER, f), encoding="utf-8") as fh:
                noms.append(json.load(fh).get("nom") or f[:-5])
        except Exception:
            continue
    if not noms:
        return "Je n ai encore rien appris."
    return "J ai appris : " + ", ".join(noms) + "."
