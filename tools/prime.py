"""Prime Video : chercher un titre et l'envoyer sur un ecran.

Prime se comporte a l'inverse de Netflix sur les deux points qui comptent.

Sa recherche repond a une simple adresse : `/search?phrase=...` renvoie une
page ou chaque affiche porte un lien `/detail/<identifiant>`. Pas de frappe a
simuler, pas d'API a reconstituer.

En revanche son lecteur web n'expose aucune diffusion : ni `cast.framework`, ni
`chrome.cast`, aucun script d'envoi, verifie lecteur en marche. La seule voie
est donc la recopie d'onglet — et elle passe, image comprise, ce que la
protection anti-copie ne garantissait pas d'avance.

Le prefixe `/-/fr/` demande la version francaise ; la langue de lecture suit
celle du compte, que ce prefixe ne remplace pas entierement.
"""
import json
import re
import time
import urllib.parse

from core.registre import outil
from core.util import sans_accents
from tools.navigateur_cast import (_brancher, _choisir_ecran, _sinks,
                                   demarrer_chrome)

RACINE = "https://www.primevideo.com/-/fr"
RECHERCHE = RACINE + "/search?phrase={q}"

_RESULTATS = (
    "JSON.stringify([...document.querySelectorAll('a[href*=detail]')]"
    ".map(a => ({h: (a.getAttribute('href') || '').split('?')[0],"
    "            t: ((a.querySelector('img') || {}).alt"
    "                || a.getAttribute('aria-label')"
    "                || a.textContent || '').trim().slice(0, 60)}))"
    ".filter(o => o.h && o.t).slice(0, 12))"
).replace("'", chr(39))


def _note(demande, titre):
    """Ressemblance entre ce qui est demande et un titre propose."""
    from difflib import SequenceMatcher
    d = " ".join(sans_accents((demande or "").lower()).split())
    t = " ".join(sans_accents((titre or "").lower()).split())
    if not d or not t:
        return 0.0
    if d == t:
        return 1.0
    if t.startswith(d):
        return 0.92
    if d in t:
        # Une collection ou une suite ne doit pas passer devant l original
        penalite = 0.1 if re.search(r"collection|saga|coffret|\b[2-9]\b", t) else 0.0
        return 0.85 - penalite
    return SequenceMatcher(None, d, t).ratio()


def _premiers(cdp, titre):
    """Resultats de recherche pour un titre : [(note, nom, chemin)]."""
    url = RECHERCHE.format(q=urllib.parse.quote(titre))
    cdp.demander("Page.navigate", {"url": url})
    time.sleep(14)
    try:
        lot = json.loads(cdp.evaluer(_RESULTATS, attente=25) or "[]")
    except Exception:
        return []
    vus, sortie = set(), []
    for o in lot:
        chemin = o["h"]
        if chemin in vus:
            continue
        vus.add(chemin)
        sortie.append((_note(titre, o["t"]), o["t"], chemin))
    sortie.sort(reverse=True)
    return sortie


@outil(
    nom="prime_chercher",
    description="Cherche un titre sur Prime Video et dit ce qui existe.",
    parametres={
        "type": "object",
        "properties": {"titre": {"type": "string", "description": "Titre cherche."}},
        "required": ["titre"],
    },
    lent=True,
    phrase_attente="Je cherche sur Prime.",
)
def prime_chercher(titre: str) -> str:
    if not demarrer_chrome():
        return "Je n arrive pas a lancer le navigateur."
    cdp = _brancher()
    if cdp is None:
        return "Le navigateur ne repond pas."
    try:
        trouves = _premiers(cdp, titre)
    finally:
        cdp.fermer()
    if not trouves:
        return f"Je ne trouve rien sur Prime pour {titre}."
    return "Sur Prime : " + ", ".join(n for _, n, _ in trouves[:5]) + "."


@outil(
    nom="prime_jouer",
    description=(
        "Ouvre un titre sur Prime Video et, si un ecran est demande, recopie "
        "l'onglet dessus. Prime n'ayant pas de diffusion propre, c'est l'image "
        "de l'ordinateur qui est retransmise. Pour 'mets Reacher sur Prime sur "
        "la tele du bas'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "titre": {"type": "string", "description": "Titre a lancer."},
            "ecran": {"type": "string",
                      "description": "Nom de l ecran. Vide = sur le PC."},
        },
        "required": ["titre"],
    },
    lent=True,
    phrase_attente="Je lance sur Prime.",
)
def prime_jouer(titre: str, ecran: str = "") -> str:
    if not demarrer_chrome():
        return "Je n arrive pas a lancer le navigateur."
    cdp = _brancher()
    if cdp is None:
        return "Le navigateur ne repond pas."

    try:
        trouves = _premiers(cdp, titre)
        if not trouves:
            return f"Je ne trouve rien sur Prime pour {titre}."

        note, nom, chemin = trouves[0]
        if note < 0.55:
            autres = ", ".join(n for _, n, _ in trouves[:3])
            return f"Je ne suis pas sur du titre. Prime propose : {autres}."

        # Le prefixe de langue demande la version francaise de la fiche.
        url = RACINE + chemin if chemin.startswith("/") else chemin
        cdp.demander("Page.navigate", {"url": url})
        time.sleep(10)

        # La fiche porte un lien de lecture explicite : on le suit plutot que
        # de cliquer. Une adresse est stable, un bouton se deplace.
        lien = cdp.evaluer(
            "(() => {"
            " const b = document.querySelector(\'[data-testid=dp-atf-play-button]\')"
            "   || [...document.querySelectorAll(\'a[href*=autoplay=1]\')]"
            "      .find(a => !/bande-annonce|trailer/i.test(a.getAttribute(\'aria-label\') || a.textContent || \'\'));"
            " return b ? b.getAttribute(\'href\') : null;"
            "})()", attente=25)

        lecture = "sans lien"
        if lien:
            depart = RACINE + lien if lien.startswith("/") else lien
            cdp.demander("Page.navigate", {"url": depart})
            lecture = "lancee"
        time.sleep(14)

        if not ecran:
            cdp.demander("Page.bringToFront")
            if lecture == "lancee":
                return f"{nom} sur le PC."
            return f"{nom} est ouvert sur le PC. Il reste a lancer la lecture."

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
                               attente=30)
        if "error" in reponse:
            return f"Diffusion refusee : {reponse['error'].get('message','')[:60]}"

        if lecture != "lancee":
            return (f"{nom} est sur {nom_ecran}, mais je n ai pas trouve le "
                    f"bouton de lecture : lance-la depuis le PC.")
        return f"{nom} sur {nom_ecran}."
    except Exception as e:
        return f"Echec : {str(e)[:80]}"
    finally:
        cdp.fermer()
