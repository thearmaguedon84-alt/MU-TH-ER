"""Netflix : franchir le portail des profils, puis diffuser sur un ecran.

Deux choses ont ete etablies en observant, pas en supposant.

Netflix sait diffuser, contrairement a ce qu'on lit souvent. Son emetteur
n'utilise pas l'API v3 comme myCANAL mais l'API historique `chrome.cast`, et il
n'affiche son bouton qu'une fois une session ouverte — d'ou l'impression qu'il
n'en a pas. En designant l'ecran a l'avance puis en demandant la session, le
recepteur Netflix demarre (`CA5E8412`) et `/watch/<id>` y envoie le titre.

Le vrai obstacle etait ailleurs : le portail « Qui est-ce ? » se remet en
travers a presque chaque chargement, et rien ne s'affiche derriere. Toutes les
tentatives de lecture du catalogue echouaient pour cette seule raison. On le
franchit donc systematiquement avant d'agir, en choisissant un profil fixe.
"""
import json
import os
import time

from core.config import reglage
from core.registre import outil
from core.util import sans_accents
from tools.navigateur_cast import (_brancher, _choisir_ecran, _sinks,
                                   demarrer_chrome)

Q = chr(39)
ACCUEIL = "https://www.netflix.com/browse"


def _profil_voulu():
    return str(reglage("netflix.profil", "") or "").strip()


def _portail_visible(cdp, contexte=None):
    return cdp.evaluer(
        "/Qui est-ce|Who\\u2019s watching|Who's watching/i.test(document.body.innerText)",
        contexte=contexte, attente=15) is True


def franchir_portail(cdp):
    """Choisit le profil par defaut si le portail se presente.

    Le nom est cherche parmi les elements de texte, puis on remonte au premier
    ancetre cliquable : c'est le lien qui porte l'action, pas le libelle.
    Sans profil configure, on prend le premier propose.
    """
    if not _portail_visible(cdp):
        return "deja passe"

    nom = _profil_voulu()
    script = (
        "(() => {"
        " const voulu = NOM;"
        " const clicable = e => e.closest('a,button,[role=button],li[tabindex],div[tabindex]') || e;"
        " let cible = null;"
        " if (voulu) {"
        "   const n = [...document.querySelectorAll('*')].find(e =>"
        "     e.children.length === 0 &&"
        "     (e.textContent || '').trim().toLowerCase() === voulu.toLowerCase());"
        "   if (n) cible = clicable(n);"
        " }"
        " if (!cible) {"
        "   const liens = [...document.querySelectorAll('a[href*=SwitchProfile], "
        "[data-uia*=profile] a, .profile-link')];"
        "   cible = liens.find(a => !/ajouter|add|gerer|manage/i.test(a.textContent || ''));"
        " }"
        " if (!cible) return 'profil introuvable';"
        " cible.click();"
        " return 'choisi';"
        "})()"
    ).replace("'", Q).replace("NOM", f'"{nom}"' if nom else '""')

    resultat = cdp.evaluer(script, geste=True, attente=25)
    time.sleep(9)
    if _portail_visible(cdp):
        return "portail toujours la"
    return "franchi" if resultat == "choisi" else str(resultat)


def _preparer(cdp, url=""):
    """Charge une page Netflix en s'assurant d'etre derriere le portail."""
    cdp.demander("Page.navigate", {"url": url or ACCUEIL})
    time.sleep(12)
    etat = franchir_portail(cdp)
    if etat == "portail toujours la":
        return etat
    # Franchir le portail ramene a l'accueil : il faut redemander la page.
    if url and etat == "franchi":
        cdp.demander("Page.navigate", {"url": url})
        time.sleep(12)
    return "pret"


def _contexte_cast(cdp):
    """Contexte JavaScript qui detient l'API de diffusion."""
    cdp.demander("Runtime.enable")
    time.sleep(2)
    for e in cdp.evenements:
        if e.get("method") != "Runtime.executionContextCreated":
            continue
        c = e["params"]["context"]
        if cdp.evaluer("!!(window.chrome && chrome.cast && chrome.cast.requestSession)",
                       contexte=c["id"], attente=10) is True:
            return c["id"]
    return None





def _liberer_ecran(nom):
    """Ferme l'application qui occupe un ecran, s'il y en a une.

    Chrome refuse d'ouvrir une session sur une destination deja occupee, et le
    refuse sans le dire. Mieux vaut donc liberer d'abord.
    """
    try:
        from tools.cast import _choisir
        appareil = _choisir(nom)
        if appareil is None:
            return False
        appareil.wait(timeout=8)
        application = appareil.status.display_name if appareil.status else None
        if not application or application == "Backdrop":
            return False
        appareil.quit_app()
        time.sleep(5)
        return True
    except Exception:
        return False

def _memes_noms(a, b):
    """Deux noms d'ecran designent-ils le meme appareil ?"""
    from difflib import SequenceMatcher
    x = " ".join(sans_accents((a or "").lower()).split())
    y = " ".join(sans_accents((b or "").lower()).split())
    if not x or not y:
        return False
    return x == y or x in y or y in x or SequenceMatcher(None, x, y).ratio() > 0.85


def _transferer(cdp):
    """Recharge la page pour que Netflix adopte la session en cours.

    Une session ouverte hors de son controle est ignoree par son emetteur : il
    n envoie donc aucun titre. Le rechargement le fait repartir, retrouver la
    session par son ecouteur, et transferer la lecture.
    """
    cdp.demander("Page.reload")
    time.sleep(14)
    franchir_portail(cdp)

def _aller_au_lecteur(cdp, ident, patience=40):
    """Amene la page sur un lecteur qui joue vraiment.

    Renvoie True si une video est en cours, False sinon.
    """
    cdp.demander("Page.navigate", {"url": f"https://www.netflix.com/watch/{ident}"})
    time.sleep(10)
    franchir_portail(cdp)

    # Redirige vers la fiche : c'est une serie, il faut choisir un episode.
    ou = cdp.evaluer("location.pathname", attente=12) or ""
    if "/title/" in ou:
        lien = cdp.evaluer(
            "(() => {"
            " const a = [...document.querySelectorAll('a[href*=/watch/]')]"
            "   .find(x => !/bande-annonce|trailer/i.test("
            "        (x.getAttribute('aria-label') || x.textContent || '')));"
            " return a ? a.getAttribute('href') : null;"
            "})()".replace("'", Q), attente=20)
        if not lien:
            return False
        url = lien if lien.startswith("http") else "https://www.netflix.com" + lien
        cdp.demander("Page.navigate", {"url": url})
        time.sleep(10)

    # Attendre un flux reel : sans cela la television resterait a charger.
    #
    # Nuance apprise a l usage : quand une diffusion est deja en cours, Netflix
    # arrete la lecture locale et la confie a la television. Exiger que la
    # video avance sur le PC reviendrait alors a attendre pour rien.
    debut = time.time()
    while time.time() - debut < patience:
        etat = cdp.evaluer(
            "(() => {"
            " const diffuse = !!(window.chrome && chrome.cast && chrome.cast.session);"
            " const v = document.getElementsByTagName('video')[0];"
            " if (!v) return false;"
            " if (diffuse) return v.readyState >= 2;"
            " return v.readyState >= 3 && v.currentTime > 0;"
            "})()".replace("'", Q), attente=12)
        if etat is True:
            return True
        time.sleep(4)
    return False

@outil(
    nom="netflix_profil",
    description=(
        "Retient le profil Netflix a utiliser par defaut, pour que Jarvis "
        "franchise seul l'ecran 'Qui est-ce ?'. Pour 'prends mon profil "
        "Netflix', 'utilise le profil Serge sur Netflix'."
    ),
    parametres={
        "type": "object",
        "properties": {"nom": {"type": "string", "description": "Nom du profil."}},
        "required": ["nom"],
    },
)
def netflix_profil(nom: str) -> str:
    from core.config import definir
    nom = (nom or "").strip()
    if not nom:
        return "Quel profil ?"
    try:
        definir("netflix.profil", nom)
    except Exception as e:
        return f"Je n arrive pas a retenir ce reglage : {str(e)[:60]}"
    return f"Je prendrai le profil {nom} sur Netflix."


@outil(
    nom="netflix_caster",
    description=(
        "Ouvre Netflix sur un ecran : la tele lance l'application Netflix, pas "
        "une recopie d'ecran. Un identifiant de titre peut etre donne pour "
        "aller directement dessus. Pour 'mets Netflix sur la tele du bas'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "ecran": {"type": "string", "description": "Nom de l ecran vise."},
            "titre_id": {"type": "string",
                         "description": "Identifiant Netflix du titre, si connu."},
        },
        "required": ["ecran"],
    },
    lent=True,
    phrase_attente="Je prepare Netflix.",
)
def netflix_caster(ecran: str, titre_id: str = "") -> str:
    if not demarrer_chrome():
        return "Je n arrive pas a lancer le navigateur."
    cdp = _brancher()
    if cdp is None:
        return "Le navigateur ne repond pas."

    try:
        if titre_id.strip():
            etat = _preparer(cdp)
            if etat == "portail toujours la":
                nom = _profil_voulu()
                precision = f" Je cherchais le profil {nom}." if nom else ""
                return ("Netflix me bloque sur l ecran des profils." + precision +
                        " Dis-moi quel profil prendre.")
            if not _aller_au_lecteur(cdp, titre_id.strip()):
                return ("Je n arrive pas a lancer la lecture de ce titre. "
                        "Il n a peut-etre pas d episode disponible.")
        else:
            etat = _preparer(cdp)
            if etat == "portail toujours la":
                nom = _profil_voulu()
                precision = f" Je cherchais le profil {nom}." if nom else ""
                return ("Netflix me bloque sur l ecran des profils." + precision +
                        " Dis-moi quel profil prendre.")

        contexte = _contexte_cast(cdp)
        if contexte is None:
            return "Netflix n expose pas de diffusion sur cette page."

        disponibles = _sinks(cdp)
        if not disponibles:
            return "Le navigateur ne voit aucun ecran."
        choix = _choisir_ecran(ecran, disponibles)
        if choix is None:
            noms = ", ".join(n for n, _ in disponibles)
            return f"Je ne trouve pas l ecran {ecran}. Disponibles : {noms}."
        nom_ecran = choix[0]

        # L ecran est-il deja pris ? On le demande au televiseur lui-meme :
        # la page peut avoir oublie sa session, lui non.
        _liberer_ecran(nom_ecran)

        cdp.demander("Cast.setSinkToUse", {"sinkName": nom_ecran})
        time.sleep(1)

        # L API historique fonctionne par rappels : on l'enveloppe pour pouvoir
        # l'attendre, et on borne l'attente pour ne pas rester suspendu.
        resultat = cdp.evaluer(
            "new Promise(res => {"
            " try {"
            "  chrome.cast.requestSession("
            "    s => res('ok:' + (s.receiver ? s.receiver.friendlyName : '?')),"
            "    e => res('refus:' + (e && (e.code || e.description) || e)));"
            "  setTimeout(() => res('sans reponse'), 25000);"
            " } catch (e) { res('exception:' + e.message); }"
            "})".replace("'", Q),
            contexte=contexte, geste=True, attente=45)

        if not resultat or not str(resultat).startswith("ok:"):
            motif = str(resultat or "sans reponse")
            if "cancel" in motif.lower():
                return "La diffusion a ete annulee."
            if "sans reponse" in motif:
                return ("Netflix ne repond pas a la demande de diffusion. "
                        "Une diffusion est peut-etre deja en cours sur un "
                        "autre appareil.")
            return f"Netflix a refuse la diffusion : {motif[:60]}"

        vise = str(resultat).split("ok:", 1)[1] or nom_ecran

        # La session est ouverte, mais Netflix ne l a pas demandee : son
        # emetteur l ignore et n envoie donc aucun titre, d ou une television
        # qui charge sans fin. En rechargeant, son code se reinitialise,
        # retrouve la session existante par son ecouteur, et transfere la
        # lecture de lui-meme.
        if titre_id.strip():
            cdp.demander("Page.reload")
            time.sleep(14)
            franchir_portail(cdp)
            debut = time.time()
            while time.time() - debut < 30:
                if cdp.evaluer(
                        "(() => { const v = document.getElementsByTagName(\'video\')[0];"
                        " return v ? v.readyState >= 3 : false; })()", attente=10) is True:
                    break
                time.sleep(4)
            return f"Netflix est sur {vise}, sur le titre demande."
        return f"Netflix est sur {vise}."
    except Exception as e:
        return f"Echec : {str(e)[:80]}"
    finally:
        cdp.fermer()


# --------------------------------------------------------------- recherche

_GABARIT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "recettes", "netflix_recherche.json")


def _note(demande, titre):
    """Ressemblance entre le titre demande et un titre propose."""
    from difflib import SequenceMatcher
    d = " ".join(sans_accents((demande or "").lower()).split())
    t = " ".join(sans_accents((titre or "").lower()).split())
    if not d or not t:
        return 0.0
    if d == t:
        return 1.0
    if t.startswith(d):
        # « Stranger Things » doit passer devant « Stranger Things : le making-of »
        return 0.95 - min(0.2, (len(t) - len(d)) / 200.0)
    if d in t:
        return 0.8
    return SequenceMatcher(None, d, t).ratio()


def _resultats(reponse):
    """Titres et identifiants contenus dans une reponse de recherche."""
    sortie = []
    try:
        sections = reponse["data"]["page"]["sections"]["edges"]
    except Exception:
        return sortie
    for s in sections:
        noeud = s.get("node") or {}
        # La galerie porte les vraies fiches ; l autre section n a que des
        # suggestions de saisie, sans identifiant exploitable.
        if noeud.get("__typename") != "PinotGallerySection":
            continue
        for e in (noeud.get("entities") or {}).get("edges") or []:
            v = e.get("node") or {}
            entite = v.get("unifiedEntity") or {}
            ident = entite.get("videoId")
            nom = v.get("displayString")
            if ident and nom:
                sortie.append((str(nom), str(ident), entite.get("__typename", "")))
    return sortie


def chercher(cdp, titre):
    """Recherche Netflix, rejouee depuis la page : [(nom, id, genre)]."""
    if not os.path.exists(_GABARIT):
        return []
    with open(_GABARIT, encoding="utf-8") as f:
        gabarit = json.load(f)

    corps = gabarit["corps"].replace("{{terme}}", (titre or "").strip())
    script = (
        "(async () => { try {"
        "  const r = await fetch(URL, {method:'POST', credentials:'include',"
        "    headers:{'Content-Type':'application/json'}, body: CORPS});"
        "  if (!r.ok) return JSON.stringify({statut: r.status});"
        "  return await r.text();"
        "} catch (e) { return JSON.stringify({erreur: e.message}); } })()"
    ).replace("'", Q).replace("URL", json.dumps(gabarit["url"])).replace(
        "CORPS", json.dumps(corps))

    brut = cdp.evaluer(script, attente=60)
    if not brut:
        return []
    try:
        return _resultats(json.loads(brut))
    except Exception:
        return []


@outil(
    nom="netflix_chercher",
    description="Cherche un titre dans le catalogue Netflix et dit ce qui existe.",
    parametres={
        "type": "object",
        "properties": {"titre": {"type": "string", "description": "Titre cherche."}},
        "required": ["titre"],
    },
    lent=True,
    phrase_attente="Je cherche sur Netflix.",
)
def netflix_chercher(titre: str) -> str:
    if not demarrer_chrome():
        return "Je n arrive pas a lancer le navigateur."
    cdp = _brancher()
    if cdp is None:
        return "Le navigateur ne repond pas."
    try:
        if _preparer(cdp) == "portail toujours la":
            return "Netflix me bloque sur l ecran des profils."
        trouves = chercher(cdp, titre)
    finally:
        cdp.fermer()
    if not trouves:
        return f"Je ne trouve rien sur Netflix pour {titre}."
    return "Sur Netflix : " + ", ".join(n for n, _, _ in trouves[:5]) + "."


@outil(
    nom="netflix_jouer",
    description=(
        "Cherche un titre sur Netflix et le lance, sur un ecran si un ecran "
        "est demande. Pour 'mets Stranger Things sur Netflix sur la tele du "
        "bas', 'lance tel film sur Netflix'."
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
    phrase_attente="Je lance sur Netflix.",
)
def netflix_jouer(titre: str, ecran: str = "") -> str:
    if not demarrer_chrome():
        return "Je n arrive pas a lancer le navigateur."
    cdp = _brancher()
    if cdp is None:
        return "Le navigateur ne repond pas."

    try:
        if _preparer(cdp) == "portail toujours la":
            return "Netflix me bloque sur l ecran des profils."

        trouves = chercher(cdp, titre)
        if not trouves:
            return f"Je ne trouve rien sur Netflix pour {titre}."

        notes = sorted(((_note(titre, n), n, i) for n, i, _ in trouves),
                       reverse=True)
        note, nom, ident = notes[0]
        if note < 0.55:
            autres = ", ".join(n for _, n, _ in notes[:3])
            return f"Je ne suis pas sur du titre. Netflix propose : {autres}."
    finally:
        cdp.fermer()

    if not ecran:
        if not demarrer_chrome():
            return "Je n arrive pas a lancer le navigateur."
        cdp2 = _brancher()
        if cdp2 is None:
            return "Le navigateur ne repond pas."
        try:
            cdp2.demander("Page.navigate",
                          {"url": f"https://www.netflix.com/watch/{ident}"})
            time.sleep(3)
            cdp2.demander("Page.bringToFront")
        finally:
            cdp2.fermer()
        return f"{nom} sur le PC."

    reponse = netflix_caster(ecran=ecran, titre_id=ident)
    if reponse.startswith("Netflix est sur"):
        return f"{nom} sur {reponse.split('Netflix est sur ', 1)[1].rstrip('.').split(',')[0]}."
    return reponse
