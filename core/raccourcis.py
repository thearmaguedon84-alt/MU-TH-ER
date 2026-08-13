"""Raccourcis deterministes : repondre sans passer par le LLM.

Le modele local se trompe souvent d'outil. Pour toutes les commandes courantes,
on reconnait la phrase ici et on appelle directement le bon outil. Le LLM ne
sert plus que pour la conversation libre et les demandes inattendues.

Point d'entree unique : essayer(question) -> str | None
  - str  : la commande a ete reconnue et executee, voici la phrase a dire.
  - None : rien de reconnu, laisser le LLM s'en charger.
"""
import re

from core.util import sans_accents

# --------------------------------------------------------------- utilitaires

def _plat(s):
    """Minuscules, sans accents, ponctuation reduite a des espaces."""
    s = sans_accents((s or "").lower())
    s = s.replace("'", " ").replace("’", " ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _contient(texte, cles):
    return any(c in texte for c in cles)


# Articles a retirer devant un nom d'application
ARTICLES = ("le ", "la ", "les ", "l ", "un ", "une ", "du ", "de la ", "de ",
            "mon ", "ma ", "mes ", "jeu ", "appli ", "application ", "logiciel ")

NOMBRES = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
    "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11,
    "douze": 12, "quinze": 15, "vingt": 20, "trente": 30, "quarante": 40,
    "quarante cinq": 45, "soixante": 60, "demi": 30,
}


def _nettoyer_cible(t):
    """Retire les articles et mots parasites devant un nom d'application."""
    t = t.strip()
    change = True
    while change:
        change = False
        for a in ARTICLES:
            if t.startswith(a):
                t = t[len(a):].strip()
                change = True
    # Retirer une politesse finale
    t = re.sub(r"\b(s il te plait|stp|merci|maintenant|tout de suite)\b", "", t)
    return re.sub(r"\s+", " ", t).strip()


# --------------------------------------------------------------- media

MEDIA = (
    (("mets en pause", "met en pause", "mets sur pause", "pause la",
      "pause le", "pause la musique", "suspends", "pause"),
     "play_pause", 1),
    (("reprends", "reprend", "continue la lecture", "continue le film",
      "relance la lecture", "remets la musique", "remet la musique"),
     "play_pause", 1),
    (("morceau suivant", "chanson suivante", "musique suivante",
      "titre suivant", "piste suivante", "passe a la suivante",
      "change de musique", "chanson d apres", "suivante", "suivant"),
     "suivant", 1),
    (("morceau precedent", "chanson precedente", "musique precedente",
      "titre precedent", "piste precedente", "reviens en arriere",
      "precedente", "precedent"),
     "precedent", 1),
    (("monte le son", "monte le volume", "augmente le son",
      "augmente le volume", "plus fort", "monte un peu"),
     "volume_haut", 5),
    (("baisse le son", "baisse le volume", "diminue le son",
      "diminue le volume", "moins fort", "baisse un peu"),
     "volume_bas", 5),
    (("coupe le son", "mets en sourdine", "coupe le volume",
      "remets le son", "sourdine", "muet"),
     "muet", 1),
)

STOP_FILM = ("stop vlc", "ferme vlc", "quitte vlc", "arrete vlc", "coupe vlc",
             "stoppe vlc", "stop le film", "arrete le film", "coupe le film",
             "stoppe le film", "arrete la lecture", "coupe la video",
             "stoppe la video", "ferme le film", "arrete la video",
             "stop film", "arrete film", "stopper le film")


def _media(t):
    if _contient(t, STOP_FILM):
        from tools.media import stopper_film
        return stopper_film()
    for cles, action, rep in MEDIA:
        if _contient(t, cles):
            from tools.controle import controler_media
            return controler_media(action=action, repetitions=rep)
    # Volume a un pourcentage precis
    m = re.search(r"(?:volume|son)\D{0,12}(\d{1,3})\s*(?:pour ?cent|%)?", t)
    if m and _contient(t, ("mets", "met", "regle", "passe", "volume a")):
        from tools.controle import regler_volume_systeme
        return regler_volume_systeme(pourcentage=int(m.group(1)))
    return None


# --------------------------------------------------------------- films

MOTS_FILM = ("film", "video", "episode", "serie", "documentaire")
VERBES_LANCER = ("lance", "lancer", "ouvre", "ouvrir", "demarre", "demarrer",
                 "mets", "met", "joue", "jouer", "execute", "demarrez",
                 "active", "start")


def _film(t):
    """'lance le film X' / 'mets un film' -> lancer_film."""
    if not _contient(t, MOTS_FILM):
        return None
    m = re.search(r"\b(?:" + "|".join(VERBES_LANCER) + r")\b\s+(?:a |au |aux |sur )?(.*)", t)
    if not m:
        return None
    reste = m.group(1)
    # Retirer le mot "film"/"video" et les articles
    reste = re.sub(r"\b(le|la|les|un|une|des|du)\b", " ", reste)
    reste = re.sub(r"\b(film|video|episode|serie|documentaire)\b", " ", reste)
    titre = _nettoyer_cible(reste)
    if titre in ("", "au hasard", "aleatoire", "random", "n importe quoi"):
        titre = ""
    from tools.media import lancer_film
    return lancer_film(nom=titre)


# --------------------------------------------------------------- applications

def _proche(cible, apps, seuil):
    """Meilleure application dont le nom ressemble a `cible`, ou None.

    Whisper deforme les noms propres (« Elden Ring » -> « Downring »).
    Une comparaison exacte echoue alors, une comparaison floue rattrape.
    """
    from difflib import SequenceMatcher
    cible_c = cible.replace(" ", "")
    meilleur, note_max = None, 0.0
    for clef in apps:
        k = _plat(clef).replace(" ", "")
        if not k:
            continue
        note = SequenceMatcher(None, cible_c, k).ratio()
        if note > note_max:
            meilleur, note_max = clef, note
    return meilleur if note_max >= seuil else None


def _chercher_app(cible, seuil_flou):
    """Retrouve une application par nom, avec plusieurs strategies."""
    from tools.apps import _apps, _trouver
    apps = _apps()
    if not apps or len(cible) < 3:
        return None, None

    clef = _trouver(cible, apps)
    if clef is None:
        # L apostrophe est devenue une espace : "baldur s gate" -> "baldurs gate"
        recolle = re.sub(r"\b(\w+) s\b", r"\1s", cible)
        if recolle != cible:
            clef = _trouver(recolle, apps)
    if clef is None:
        # Suffixe parasite ("sur steam", "s il te plait")
        cible2 = re.sub(r"\b(sur|avec|via)\b.*$", "", cible).strip()
        if cible2 and cible2 != cible:
            clef = _trouver(cible2, apps)
    if clef is None:
        clef = _proche(cible, apps, seuil_flou)
    return clef, apps


def _application(t):
    """'lance Elden Ring', 'ouvre Spotify', ou un nom d'application seul."""
    from tools.apps import launch_app

    m = re.search(r"\b(?:" + "|".join(VERBES_LANCER) + r")\b\s+(?:a |au |aux |sur )?(.+)", t)
    if m:
        # Un verbe de lancement : on peut se permettre d'etre tolerant.
        cible = _nettoyer_cible(m.group(1))
        clef, _ = _chercher_app(cible, seuil_flou=0.72)
        if clef:
            return launch_app(nom=clef)
        return None

    # Pas de verbe : « Elden Ring » tout seul. On exige une ressemblance forte,
    # sinon toute phrase anodine finirait par lancer un programme.
    cible = _nettoyer_cible(t)
    if len(cible.split()) > 4:
        return None
    clef, _ = _chercher_app(cible, seuil_flou=0.86)
    if clef:
        return launch_app(nom=clef)
    return None


# --------------------------------------------------------------- divers

def _heure(t):
    if _contient(t, ("quelle heure", "il est quelle heure", "on est quel jour",
                     "quel jour on est", "quelle date", "la date du jour",
                     "quel jour sommes nous")):
        from tools.temps import heure_et_date
        return heure_et_date()
    return None


def _meteo(t):
    if _contient(t, ("quel temps", "la meteo", "meteo du jour", "il fait beau",
                     "va t il pleuvoir", "il va pleuvoir", "il fait combien",
                     "quelle temperature", "temps qu il fait")):
        from tools.meteo import meteo
        return meteo()
    return None


def _minuteur(t):
    if not _contient(t, ("minuteur", "minuterie", "reveille moi", "previens moi",
                         "compte a rebours", "chrono", "rappelle moi dans")):
        return None
    secondes = None
    m = re.search(r"(\d+)\s*(seconde|minute|heure)", t)
    if m:
        n, unite = int(m.group(1)), m.group(2)
        secondes = n * {"seconde": 1, "minute": 60, "heure": 3600}[unite]
    else:
        for mot, val in NOMBRES.items():
            m2 = re.search(r"\b" + mot + r"\b\s*(seconde|minute|heure)", t)
            if m2:
                unite = m2.group(1)
                secondes = val * {"seconde": 1, "minute": 60, "heure": 3600}[unite]
                break
    if not secondes:
        return None
    from tools.temps import lancer_minuteur
    return lancer_minuteur(secondes=secondes, libelle="")


def _stats(t):
    if _contient(t, ("etat du pc", "etat de la machine", "combien de ram",
                     "utilisation du processeur", "charge du cpu",
                     "temperature du pc", "stats systeme", "espace disque")):
        from tools.stats import get_system_stats
        return get_system_stats()
    return None


def _capture(t):
    if _contient(t, ("capture d ecran", "fais une capture", "screenshot",
                     "prends une capture")):
        from tools.ecran import capture_screen
        return capture_screen()
    return None



# --------------------------------------------------------------- ton MU-TH-UR

# Les raccourcis renvoient des phrases toutes faites, ecrites pour Jarvis.
# En mode "mere" on les remplace par leur equivalent clinique. Purement
# cosmetique : aucune action n'est modifiee.
TON_MERE = {
    "C'est fait.": "Execute.",
    "Film arrete.": "Lecture interrompue.",
    "VLC n etait pas en cours.": "Aucune lecture en cours.",
    "Lecture arretee.": "Lecture interrompue.",
    "Piste suivante.": "Sequence suivante.",
    "Piste precedente.": "Sequence precedente.",
    "Volume augmente.": "Niveau sonore augmente.",
    "Volume baisse.": "Niveau sonore reduit.",
    "Son coupe.": "Sortie audio coupee.",
}


def _au_ton_mere(reponse):
    """Adapte la formulation d'un raccourci au registre MU-TH-UR."""
    from core.config import reglage
    if reglage("assistant.personnalite", "") != "mere":
        return reponse
    if reponse in TON_MERE:
        return TON_MERE[reponse]
    # Formulations construites dynamiquement
    if reponse.endswith(" lance dans VLC."):
        return "Lecture engagee : " + reponse[:-len(" lance dans VLC.")] + "."
    if reponse.endswith(" lance."):
        return "Programme engage : " + reponse[:-len(" lance.")] + "."
    if reponse.startswith("Volume a "):
        return "Niveau sonore " + reponse[len("Volume a "):]
    if reponse.startswith("Je n ai pas trouve"):
        return "Aucune correspondance dans les archives."
    return reponse


# --------------------------------------------------------------- point d'entree

# L'ordre compte : une application CONNUE l'emporte (sinon "ouvre Prime Video"
# partirait dans la logique film a cause du mot "video"). Un titre inconnu
# retombe naturellement sur _film.
ETAPES = (_media, _application, _film, _heure, _meteo, _minuteur, _stats, _capture)


def essayer(question):
    """Traite la phrase si elle correspond a un raccourci connu.

    Renvoie la phrase a dire, ou None s'il faut passer la main au LLM.
    """
    t = _plat(question)
    if not t:
        return None
    for etape in ETAPES:
        try:
            reponse = etape(t)
        except Exception:
            # Un raccourci qui casse ne doit jamais bloquer Jarvis :
            # on laisse simplement le LLM prendre le relais.
            continue
        if reponse:
            return _au_ton_mere(reponse)
    return None
