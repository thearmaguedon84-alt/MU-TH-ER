"""Chercher sur le web, et rapporter une reponse — pas une liste de sites.

Premiere version trop naive : elle rendait les extraits de recherche tels
quels. Or pour « resultat du dernier match de l'OM », ces extraits ne sont que
des descriptions de sites — « retrouvez tous les scores sur… ». Un grand modele
aurait enchaine de lui-meme sur la lecture d'une page ; un modele de sept
milliards recite ce qu'on lui donne.

L'outil fait donc le travail complet :

1. il interroge l'actualite quand la question porte sur un evenement, car les
   depeches datent leurs informations et les resument ;
2. il juge si les extraits disent quelque chose — des chiffres, des dates, un
   score — ou s'ils se contentent de decrire un site ;
3. s'ils ne disent rien, il ouvre lui-meme la meilleure page et en extrait les
   passages qui repondent.

Le modele n'a plus qu'a lire. C'est le bon partage : la mecanique en Python,
la formulation au modele.
"""
import re
import time

from core.registre import outil

_CACHE = {}
_DUREE_CACHE = 300

# Adresses rendues par une recherche : le seul terrain de lecture permis.
_VUES = set()

# Sites dont le contenu principal n'est pas dans la page.
_ILLISIBLES = ("youtube.com", "twitter.com", "x.com", "instagram.com",
               "facebook.com", "tiktok.com", "pinterest.")

# Une question d'actualite merite les depeches plutot que les pages generales.
_ACTUALITE = re.compile(
    r"\b(?:resultat|resultats|score|match|gagne|gagnant|vainqueur|bat|battu|"
    r"hier|aujourd hui|ce matin|ce soir|derniere?|recent|actualite|news|"
    r"mort|deces|elu|election|annonce|sorti|prix|cours|meteo)\b")

# Ce qui distingue un extrait qui informe d'un extrait qui presente un site.
_VIDE = re.compile(
    r"\b(?:retrouvez|toute l actualite|tous les resultats|suivez|decouvrez|"
    r"consultez|site officiel|abonnez|en direct sur|toutes les infos)\b")


# Mots trop courants pour distinguer un sujet d'un autre.
_OUTILS_LANGUE = {
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou", "a", "au",
    "aux", "en", "dans", "sur", "pour", "par", "avec", "sans", "ce", "cet",
    "cette", "est", "sont", "quel", "quelle", "quels", "quelles", "qui", "que",
    "quoi", "dernier", "derniere", "dernieres", "derniers", "internet", "web",
    "cherche", "recherche", "trouve", "moi", "stp", "resultat", "resultats",
}

# Au-dela, une information d'actualite n'en est plus une.
_JOURS_MAX = 45


def _marquants(question):
    """Mots qui distinguent le sujet. Les sigles courts comptent aussi.

    Exiger quatre lettres ecartait « OM », « PSG », « JO » — precisement les
    mots qui portent le sujet. On descend a deux caracteres, en retirant les
    mots outils.
    """
    import unicodedata
    q = unicodedata.normalize("NFKD", (question or "").lower())
    q = "".join(c for c in q if not unicodedata.combining(c))
    mots = [m for m in re.findall(r"[a-z0-9]{2,}", q) if m not in _OUTILS_LANGUE]
    return set(mots)


def _parle_du_sujet(titre, extrait, marquants):
    """Le resultat reprend-il au moins un mot marquant de la question ?"""
    if not marquants:
        return True
    import unicodedata
    texte = unicodedata.normalize("NFKD", f"{titre} {extrait}".lower())
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    presents = re.findall(r"[a-z0-9]{2,}", texte)
    return bool(marquants & set(presents))


def _trop_vieux(date):
    """Une date au format AAAA-MM-JJ est-elle hors de la periode utile ?"""
    if not date:
        return False
    try:
        import datetime
        d = datetime.date.fromisoformat(date[:10])
        return (datetime.date.today() - d).days > _JOURS_MAX
    except Exception:
        return False


def _du_cache(clef):
    e = _CACHE.get(clef)
    return e[1] if e and time.time() - e[0] < _DUREE_CACHE else None


def _en_cache(clef, valeur):
    _CACHE[clef] = (time.time(), valeur)
    if len(_CACHE) > 60:
        _CACHE.clear()
    return valeur


def _nettoyer(texte, limite=340):
    return re.sub(r"\s+", " ", texte or "").strip()[:limite]


def _informatif(extrait):
    """L'extrait dit-il quelque chose, ou presente-t-il seulement un site ?"""
    t = (extrait or "").lower()
    if len(t) < 40:
        return False
    if _VIDE.search(t):
        return False
    # Un fait porte presque toujours un nombre : un score, une date, un prix.
    return bool(re.search(r"\d", t))


def chercher(question, combien=6, region="fr-fr"):
    """Resultats de recherche : [(titre, extrait, adresse, date)]."""
    clef = ("r", question.lower().strip())
    garde = _du_cache(clef)
    if garde is not None:
        return garde
    try:
        from ddgs import DDGS
    except ImportError:
        return []

    sortie = []
    try:
        with DDGS() as d:
            # Les depeches d'abord si la question porte sur un evenement :
            # elles sont datees et vont droit au fait.
            actualite = bool(_ACTUALITE.search(question.lower()))
            if actualite:
                try:
                    # Borne de temps : les archives ne repondent pas a une
                    # question sur ce qui vient de se passer.
                    for r in d.news(question, region=region, max_results=6,
                                    timelimit="w"):
                        sortie.append((r.get("title", ""),
                                       _nettoyer(r.get("body", "")),
                                       r.get("url") or r.get("href", ""),
                                       (r.get("date") or "")[:10]))
                except Exception:
                    pass
            for r in d.text(question, region=region, safesearch="moderate",
                            max_results=combien,
                            timelimit="m" if actualite else None):
                sortie.append((r.get("title", ""), _nettoyer(r.get("body", "")),
                               r.get("href", ""), ""))
    except Exception:
        return sortie

    marquants = _marquants(question)
    retenus = []
    for titre, extrait, adresse, date in sortie:
        if not adresse:
            continue
        if _trop_vieux(date):
            continue
        if not _parle_du_sujet(titre, extrait, marquants):
            continue
        retenus.append((titre, extrait, adresse, date))

    # Le plus recent d'abord : l'ordre du moteur ne tient pas compte du temps.
    retenus.sort(key=lambda s: s[3] or "", reverse=True)

    for _, _, a, _ in retenus:
        _VUES.add(a)
    return _en_cache(clef, retenus)


def _texte_page(adresse):
    """Texte principal d'une page, ou chaine vide."""
    garde = _du_cache(("p", adresse))
    if garde is not None:
        return garde
    try:
        import httpx
        r = httpx.get(adresse, timeout=12, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; Jarvis)"})
        r.raise_for_status()
        from lxml import html as lh
        arbre = lh.fromstring(r.text)
        for mauvais in arbre.xpath("//script|//style|//nav|//header|//footer|"
                                   "//aside|//form|//noscript"):
            mauvais.getparent().remove(mauvais)
        morceaux = arbre.xpath("//h1//text()|//h2//text()|//p//text()|//li//text()")
        texte = re.sub(r"\s+", " ", " ".join(m.strip() for m in morceaux)).strip()
    except Exception:
        return ""
    return _en_cache(("p", adresse), texte[:12000])


def _passages(texte, question, combien=4):
    """Phrases de la page qui repondent le mieux a la question."""
    mots = {m for m in re.findall(r"\w{4,}", question.lower())}
    phrases = re.split(r"(?<=[.!?])\s+", texte)
    notees = []
    for p in phrases:
        if not (25 < len(p) < 320):
            continue
        bas = p.lower()
        note = sum(1 for m in mots if m in bas)
        if re.search(r"\d+\s*[-–:]\s*\d+", p):   # un score
            note += 3
        elif re.search(r"\d", p):
            note += 1
        if note:
            notees.append((note, p.strip()))
    notees.sort(reverse=True)
    vues, sortie = set(), []
    for _, p in notees:
        if p[:40] in vues:
            continue
        vues.add(p[:40])
        sortie.append(p)
        if len(sortie) >= combien:
            break
    return sortie


@outil(
    nom="chercher_web",
    description=(
        "Cherche une information sur internet et rapporte ce qui repond a la "
        "question, sources comprises. A utiliser des que la question porte sur "
        "l'actualite, un resultat, un prix, une date recente, ou tout ce qui a "
        "pu changer : ne reponds jamais de memoire dans ces cas-la."
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

    utiles = [t for t in trouves if _informatif(t[1])]

    # Aucun extrait ne dit rien de concret : on va lire la page nous-memes
    # plutot que de rendre une liste de sites, qui ne repond a personne.
    lus = []
    if len(utiles) < 2:
        for titre, _, adresse, _ in trouves[:4]:
            if any(s in adresse for s in _ILLISIBLES):
                continue
            passages = _passages(_texte_page(adresse), question)
            if passages:
                lus.append((titre, adresse, passages))
            if len(lus) >= 2:
                break

    lignes = []
    for titre, extrait, adresse, date in utiles[:4]:
        hote = re.sub(r"^https?://(www\.)?", "", adresse).split("/")[0]
        prefixe = f"[{date}] " if date else ""
        lignes.append(f"- {prefixe}{extrait} ({hote})")
    for titre, adresse, passages in lus:
        hote = re.sub(r"^https?://(www\.)?", "", adresse).split("/")[0]
        for p in passages[:3]:
            lignes.append(f"- {p} ({hote})")

    if not lignes:
        return (f"J ai cherche « {question} » mais les pages trouvees ne "
                f"donnent pas la reponse. Dis-moi si tu veux que je precise "
                f"la recherche.")

    return ("Elements trouves sur internet pour « " + question + " » :\n" +
            "\n".join(lignes[:8]) +
            "\n\nDonne la reponse en une ou deux phrases a partir de ces "
            "elements, en francais. Ne retiens que ce qui repond vraiment a la "
            "question : ecarte sans le mentionner tout element hors sujet. Ne "
            "renvoie pas vers des sites, reponds. Si rien ne repond, dis "
            "simplement que tu n as pas trouve.")


@outil(
    nom="lire_page",
    description=(
        "Lit une page web et en renvoie le texte principal. A n'utiliser "
        "qu'avec une adresse rendue par chercher_web. N'invente jamais "
        "d'adresse."
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
    if adresse not in _VUES:
        return ("Je ne lis que les pages trouvees par une recherche. "
                "Utilise chercher_web d abord.")
    texte = _texte_page(adresse)
    if len(texte) < 120:
        return "Cette page ne contient pas de texte lisible."
    return texte[:4000]
