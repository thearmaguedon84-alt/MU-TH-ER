"""Chercher sur le web, et lire une page.

Le modele qui fait tourner Jarvis repond de memoire : il ignore ce qui s'est
passe depuis son entrainement, et ne peut rien verifier. Deux outils suffisent
a lever cette limite — chercher, puis lire.

Le resultat est volontairement court. Une reponse destinee a etre prononcee ne
supporte pas trois pages de contexte : on rend les extraits utiles et leurs
sources, le modele en tire une phrase.

Les recherches recentes sont gardees quelques minutes : demander deux fois la
meme chose arrive souvent dans une conversation parlee, et une recherche coute
une seconde et demie.
"""
import re
import time

from core.registre import outil

_CACHE = {}
_DUREE_CACHE = 300

# Adresses rendues par une recherche : le seul terrain de lecture permis.
_VUES = set()

# Sites dont le contenu principal est ailleurs que dans la page : inutile
# d'essayer de les lire, la recherche suffit.
_ILLISIBLES = ("youtube.com", "twitter.com", "x.com", "instagram.com",
               "facebook.com", "tiktok.com")


def _du_cache(clef):
    entree = _CACHE.get(clef)
    if entree and time.time() - entree[0] < _DUREE_CACHE:
        return entree[1]
    return None


def _en_cache(clef, valeur):
    _CACHE[clef] = (time.time(), valeur)
    if len(_CACHE) > 60:
        _CACHE.clear()
    return valeur


def _nettoyer(texte, limite=320):
    t = re.sub(r"\s+", " ", texte or "").strip()
    return t[:limite]


def chercher(question, combien=5, region="fr-fr"):
    """Resultats de recherche : [(titre, extrait, adresse)]."""
    clef = ("r", question.lower().strip(), combien)
    garde = _du_cache(clef)
    if garde is not None:
        return garde
    try:
        from ddgs import DDGS
    except ImportError:
        return []
    try:
        with DDGS() as d:
            lot = list(d.text(question, region=region, safesearch="moderate",
                              max_results=combien))
    except Exception:
        return []
    sortie = [(r.get("title", ""), _nettoyer(r.get("body", "")),
               r.get("href", "")) for r in lot if r.get("href")]
    # On retient les adresses rencontrees : seules celles-la pourront etre
    # lues ensuite. Un modele qui invente une adresse sera arrete net.
    for _, _, a in sortie:
        _VUES.add(a)
    return _en_cache(clef, sortie)


@outil(
    nom="chercher_web",
    description=(
        "Cherche une information sur internet et renvoie les extraits les plus "
        "pertinents avec leurs sources. A utiliser des que la question porte "
        "sur l'actualite, un prix, un resultat sportif, une date recente, ou "
        "tout ce qui a pu changer : ne reponds jamais de memoire dans ces "
        "cas-la. Pour 'cherche sur internet', 'qui a gagne', 'quelles "
        "nouvelles de'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "question": {"type": "string",
                         "description": "Ce qu il faut chercher, en quelques mots."},
        },
        "required": ["question"],
    },
    lent=True,
    phrase_attente="Je cherche sur internet.",
)
def chercher_web(question: str) -> str:
    question = (question or "").strip()
    if not question:
        return "Que veux-tu que je cherche ?"

    trouves = chercher(question)
    if not trouves:
        return f"Je n ai rien trouve sur internet pour {question}."

    lignes = []
    for titre, extrait, adresse in trouves[:4]:
        hote = re.sub(r"^https?://(www\.)?", "", adresse).split("/")[0]
        lignes.append(f"- {_nettoyer(titre, 90)} ({hote}) : {extrait}")
    return ("Resultats de recherche pour « " + question + " » :\n" +
            "\n".join(lignes) +
            "\n\nReponds brievement a partir de ces elements, en citant la "
            "source si elle compte. Si les extraits ne suffisent pas, dis-le.")


@outil(
    nom="lire_page",
    description=(
        "Lit une page web et en renvoie le texte principal. A n'utiliser "
        "qu'avec une adresse rendue par chercher_web, et seulement si les "
        "extraits ne suffisent pas. N'invente jamais d'adresse : commence "
        "toujours par chercher_web."
    ),
    parametres={
        "type": "object",
        "properties": {
            "adresse": {"type": "string", "description": "Adresse de la page."},
        },
        "required": ["adresse"],
    },
    lent=True,
    phrase_attente="Je lis la page.",
)
def lire_page(adresse: str) -> str:
    adresse = (adresse or "").strip()
    if not adresse.startswith("http"):
        return "Ce n est pas une adresse valable."
    if any(s in adresse for s in _ILLISIBLES):
        return "Cette page ne se lit pas ainsi ; la recherche donnera mieux."

    # Garde-fou contre les adresses inventees : on ne lit que ce qu une
    # recherche a effectivement rendu.
    if adresse not in _VUES:
        return ("Je ne lis que les pages trouvees par une recherche. "
                "Utilise chercher_web d abord, puis reprends une des adresses "
                "rendues.")

    garde = _du_cache(("p", adresse))
    if garde is not None:
        return garde

    try:
        import httpx
        r = httpx.get(adresse, timeout=15, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; Jarvis)"})
        r.raise_for_status()
        brut = r.text
    except Exception as e:
        return f"Je n arrive pas a ouvrir cette page : {str(e)[:60]}"

    try:
        from lxml import html as lh
        arbre = lh.fromstring(brut)
        # Le decor n'apporte rien et noie le propos.
        for mauvais in arbre.xpath(
                "//script|//style|//nav|//header|//footer|//aside|//form"):
            mauvais.getparent().remove(mauvais)
        morceaux = [t.strip() for t in arbre.xpath("//p//text()|//h1//text()|"
                                                   "//h2//text()|//li//text()")]
        texte = " ".join(m for m in morceaux if m)
    except Exception:
        texte = re.sub(r"<[^>]+>", " ", brut)

    texte = re.sub(r"\s+", " ", texte).strip()
    if len(texte) < 120:
        return "Cette page ne contient pas de texte lisible."
    # Assez pour repondre, pas au point d'ensevelir le modele.
    return _en_cache(("p", adresse), texte[:4000])
