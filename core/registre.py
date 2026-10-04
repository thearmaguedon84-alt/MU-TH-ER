"""Registre d'outils : decorateur @outil, auto-decouverte, confirmation vocale.

Chaque outil est une fonction decoree par @outil dans un fichier de tools/.
Au demarrage, charger_outils() importe tous les modules de tools/ pour peupler
le registre. Le reste de l'assistant n'a plus a connaitre les outils un par un.
"""
import importlib
import pkgutil

_REGISTRE = {}      # nom -> Outil
_EN_ATTENTE = None  # (Outil, args) en attente d'une confirmation vocale


class Outil:
    """Metadonnees + fonction d'un outil."""

    def __init__(self, fonction, nom, description, parametres, confirmation,
                 lent, phrase_attente, annonce, mcp_expose):
        self.fonction = fonction
        self.nom = nom
        self.description = description
        self.parametres = parametres
        self.confirmation = confirmation      # demande "tu confirmes ?" avant d'agir
        self.lent = lent                      # accuse de reception pendant l'execution
        self.phrase_attente = phrase_attente  # phrase d'attente si lent
        self.annonce = annonce                # fn(args) -> phrase de confirmation
        self.mcp_expose = mcp_expose          # visible via le serveur MCP externe ?


def outil(nom, description, parametres=None, confirmation=False, lent=False,
          phrase_attente=None, annonce=None, mcp_expose=False):
    """Decorateur : enregistre une fonction comme outil de l'assistant.

    mcp_expose : par securite, un outil n'est PAS expose au serveur MCP par
    defaut (un agent externe ne doit voir que ce qu'on autorise explicitement).
    """
    def deco(fonction):
        _REGISTRE[nom] = Outil(
            fonction, nom, description,
            parametres or {"type": "object", "properties": {}},
            confirmation, lent, phrase_attente, annonce, mcp_expose)
        return fonction
    return deco


def charger_outils():
    """Importe tous les modules de tools/ pour remplir le registre."""
    import tools
    for module in pkgutil.iter_modules(tools.__path__):
        importlib.import_module(f"tools.{module.name}")


def ajuster_arguments(outil_obj, arguments):
    """Rapproche les arguments recus des parametres reellement declares.

    Un modele local invente volontiers un synonyme : 'titre' pour 'nom',
    'texte' pour 'contenu'. Plutot que de planter sur un TypeError, on
    reattribue chaque valeur au parametre le plus proche encore libre.
    """
    from difflib import SequenceMatcher

    if not isinstance(arguments, dict):
        return {}
    attendus = list((outil_obj.parametres or {}).get("properties", {}).keys())
    if not attendus:
        return {}

    propres, restants = {}, []
    for cle, valeur in arguments.items():
        if cle in attendus:
            propres[cle] = valeur
        else:
            restants.append((cle, valeur))

    libres = [a for a in attendus if a not in propres]
    orphelins = []
    for cle, valeur in restants:
        if not libres:
            break
        # Le parametre libre dont le nom ressemble le plus a la clef recue
        note = [(SequenceMatcher(None, str(cle).lower(), a.lower()).ratio(), a)
                for a in libres]
        note.sort(reverse=True)
        meilleur_score, meilleur = note[0]
        if meilleur_score >= 0.45 or len(libres) == 1:
            propres[meilleur] = valeur
            libres.remove(meilleur)
        else:
            orphelins.append(valeur)

    # Aucune ressemblance mais un parametre obligatoire manque : on attribue
    # dans l ordre plutot que de perdre la valeur. Sans ca, spotify_jouer
    # appele avec {'query': ...} arrivait sans aucune recherche.
    if orphelins:
        requis = [a for a in (outil_obj.parametres or {}).get("required", [])
                  if a in libres]
        for a in requis:
            if not orphelins:
                break
            propres[a] = orphelins.pop(0)
            libres.remove(a)
        # Puis les parametres optionnels encore libres, dans l ordre declare
        for a in list(libres):
            if not orphelins:
                break
            propres[a] = orphelins.pop(0)
            libres.remove(a)

    return propres


def get(nom):
    return _REGISTRE.get(nom)


def tous():
    return list(_REGISTRE.values())


# Outils NON exposes au modele local (mode local) : soit ils exigent internet et/ou
# de la vision (impossibles/peu fiables hors ligne), soit ils noieraient un petit
# modele 7b. En mode local on garde un jeu d'outils reduit et fiable (domotique, PC,
# minuteurs, memoire, meteo...). Ces memes outils s'auto-desactivent hors ligne.
_NON_LOCAUX = {
    # Doublons masques au modele local : la fonction reste appelable en Python
    # (raccourcis, delegation interne), seul son nom disparait de la liste
    # proposee au LLM, qui hesitait entre deux outils equivalents.
    #   launch_app     -> couvert par ouvrir_application, qui lui delegue
    #   regler_volume  -> couvert par controler_media + regler_volume_systeme
    "launch_app", "regler_volume",
    # Le modele local enregistrait des souvenirs sur des remarques
    # anodines. La memoire passe maintenant par un raccourci explicite.
    "remember",
    "capture_screen", "faire_brief",
    "lire_mails", "lire_mail", "preparer_mail", "envoyer_mail", "mettre_a_la_corbeille",
    "get_events", "create_event", "delete_event", "get_deadlines",
    # Chercher est justement ce dont un petit modele a le plus besoin : il
    # ignore l actualite et ne peut rien verifier. En revanche on lui retire
    # lire_page : une demarche en deux temps le perdait, et il finissait par
    # inventer des adresses pour nourrir la lecture.
    "lire_page",
    "book_appointment", "confirmer_reservation",
    "browser_open", "browser_current_page", "browser_tabs", "browser_close_tabs",
    "browser_interact",
    "call_with_message", "call_and_book", "cout_appels",
    "instagram_resume", "rafraichir_instagram",
    "get_mentions_summary", "get_channel_summary",
}


def schemas_api(local_seulement=False):
    """Schemas au format Anthropic (name, description, input_schema).

    local_seulement=True : ne renvoie que les outils utilisables par un modele
    local (mode Ollama), en excluant les outils internet/vision (_NON_LOCAUX).
    """
    return [{"name": o.nom, "description": o.description, "input_schema": o.parametres}
            for o in _REGISTRE.values()
            if not (local_seulement and o.nom in _NON_LOCAUX)]


def noms_lents():
    return {o.nom for o in _REGISTRE.values() if o.lent}


def exposes_mcp():
    """Liste des outils autorises a etre exposes via le serveur MCP externe."""
    return [o for o in _REGISTRE.values() if o.mcp_expose]


def phrase_attente(noms):
    """Phrase d'accuse de reception pour le premier outil lent appele."""
    for o in _REGISTRE.values():
        if o.nom in noms and o.lent and o.phrase_attente:
            return o.phrase_attente
    return "D'accord, je m'en occupe."


# ---------------------------------------------------------------- confirmation

def mettre_en_attente(outil_obj, args):
    """Range une action a confirmer. Renvoie un resultat neutre pour Claude."""
    global _EN_ATTENTE
    _EN_ATTENTE = (outil_obj, args)
    return "En attente de la confirmation vocale de l'utilisateur."


def annonce_en_attente():
    """Phrase a prononcer pour demander l'accord, ou None si rien en attente."""
    if _EN_ATTENTE is None:
        return None
    outil_obj, args = _EN_ATTENTE
    if outil_obj.annonce:
        try:
            return outil_obj.annonce(args)
        except Exception:
            pass
    return f"Je vais executer {outil_obj.nom}."


def executer_confirme():
    """Execute l'action en attente et renvoie son resultat."""
    global _EN_ATTENTE
    if _EN_ATTENTE is None:
        return ""
    outil_obj, args = _EN_ATTENTE
    _EN_ATTENTE = None
    try:
        return outil_obj.fonction(**args)
    except Exception:
        return "Desole, je n'ai pas reussi a faire ca."


def annuler_confirme():
    global _EN_ATTENTE
    _EN_ATTENTE = None
