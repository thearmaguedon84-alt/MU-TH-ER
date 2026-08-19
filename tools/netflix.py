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
        url = f"https://www.netflix.com/watch/{titre_id.strip()}" if titre_id.strip() else ACCUEIL
        etat = _preparer(cdp, url)
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

        deja = cdp.evaluer(
            "!!(chrome.cast && chrome.cast.session)", contexte=contexte, attente=10)
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
            return f"Netflix a refuse la diffusion : {motif[:60]}"

        vise = str(resultat).split("ok:", 1)[1] or nom_ecran
        if titre_id.strip():
            return f"Netflix est sur {vise}, sur le titre demande."
        return f"Netflix est sur {vise}."
    except Exception as e:
        return f"Echec : {str(e)[:80]}"
    finally:
        cdp.fermer()
