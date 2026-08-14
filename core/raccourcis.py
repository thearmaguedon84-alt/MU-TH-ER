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
    # « ... sur VLC » designe le lecteur, pas le titre du film
    reste = re.sub(r"\b(?:sur|avec|dans)\s+(?:vlc|le lecteur|le player|media player)\b.*$", " ", reste)
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




def _mode(t):
    """« mode maman » / « mode jarvis » sans passer par le LLM.

    Le modele local oublie souvent d'appeler changer_personnalite, ce qui
    laissait l'assistant bloque en MU-TH-UR. Ici c'est deterministe.
    """
    vers_mere = ("mode maman", "mode mere", "mode mother", "mode muthur",
                 "passe en maman", "deviens maman", "mode nostromo",
                 "mode alien", "active maman")
    vers_normal = ("mode jarvis", "mode normal", "mode neutre", "mode standard",
                   "redeviens jarvis", "redeviens normal", "reviens en jarvis",
                   "quitte le mode maman", "arrete le mode maman",
                   "desactive maman", "retour normal")

    if _contient(t, vers_mere):
        cible = "mere"
    elif _contient(t, vers_normal):
        cible = "neutre"
    else:
        return None

    from core import config, personnalite
    if config.reglage("assistant.personnalite", "") != cible:
        config.definir("assistant.personnalite", cible)
    # L'ecran suit, s'il y a un HUD.
    try:
        import hud
        hud.interface("mother" if cible == "mere" else "jarvis")
    except Exception:
        pass
    return "Mode maman active." if cible == "mere" else "Mode normal active."



def _courrier(t):
    """« lis mes mails », « j ai des mails ? » -> lire_mails.

    Passe par un raccourci car les outils mail ne sont pas proposes au modele
    local (voir _NON_LOCAUX dans core/registre.py).
    """
    cles = ("mes mails", "mes mail", "mes e mails", "mes emails", "mes courriels",
            "ma boite mail", "ma messagerie", "ma boite aux lettres",
            "lis les mails", "lire les mails", "nouveaux mails",
            "j ai des mails", "j ai du courrier", "regarde les mails",
            "consulte les mails", "verifie les mails", "tri de ma boite")
    # « lis mes 3 derniers mails » : le nombre s intercale dans la formule
    motif = re.search(r"\b(?:mes|les)\b.{0,14}?\b(?:mails?|e ?mails?|courriels?)\b", t)
    if not _contient(t, cles) and not motif:
        return None
    # Combien ? « lis mes trois derniers mails »
    nombre = 5
    m = re.search(r"(\d+)\s+(?:derniers?\s+)?(?:mails?|e ?mails?|courriels?)", t)
    if m:
        nombre = max(1, min(int(m.group(1)), 10))
    from tools.mail import lire_mails
    return lire_mails(nombre=nombre)



def _spotify(t):
    """« mets Nirvana sur Spotify », « c est quoi cette chanson ».

    Les touches media couvrent deja pause et volume ; ici on gere ce qu elles
    ne savent pas faire : choisir quoi jouer, et dire ce qui passe.
    """
    from tools import spotify as S

    if _contient(t, ("qu est ce qui passe", "c est quoi cette chanson",
                     "c est quoi ce morceau", "quel est ce morceau",
                     "quelle est cette chanson", "quelle chanson",
                     "c est quoi cette musique", "qu est ce qu on ecoute")):
        return S.spotify_en_cours()

    # « mets/joue/lance/cherche <quelque chose> sur Spotify »
    m = re.search(r"\b(?:mets|met|joue|lance|balance|passe|recherche|cherche)\b"
                  r"\s+(.+?)\s+sur\s+spotify\b", t)
    if not m:
        # « sur Spotify, mets <quelque chose> »
        m = re.search(r"\bspotify\b.*?\b(?:mets|met|joue|lance)\b\s+(.+)", t)
    if not m:
        return None

    cible = _nettoyer_cible(m.group(1))
    if len(cible) < 2:
        return None

    # La demande est claire : si Spotify n est pas configure, on le DIT.
    # Avant, on laissait la main et « lance l album X sur Spotify » finissait
    # par simplement ouvrir l application, ce qui n a aucun sens.
    if not S.configure():
        return ("Spotify n est pas encore configure. "
                "Lance le script de configuration une fois.")

    # Genre demande explicitement ?
    genre = ""
    for mot, g in (("album", "album"), ("playlist", "playlist"),
                   ("artiste", "artiste"), ("groupe", "artiste"),
                   ("titre", "titre"), ("chanson", "titre"),
                   ("morceau", "titre")):
        if re.search(r"\b" + mot + r"\b", cible):
            genre = g
            cible = re.sub(r"\b" + mot + r"\b", " ", cible).strip()
            break

    cible = re.sub(r"^(?:de|du|des|d)\s+", "", _nettoyer_cible(cible))
    cible = _nettoyer_cible(cible)
    if len(cible) < 2:
        return None
    return S.spotify_jouer(recherche=cible, genre=genre)



def _cast(t):
    """« affiche toi sur la tele », « caste sur le videoprojecteur »."""
    if _contient(t, ("arrete le cast", "stop le cast", "coupe le cast",
                     "enleve toi de la tele", "arrete de caster",
                     "arrete l affichage")):
        from tools.cast import arreter_cast
        return arreter_cast()

    if _contient(t, ("quels ecrans", "liste les ecrans", "liste les chromecast",
                     "quels chromecast", "ecrans disponibles")):
        from tools.cast import lister_ecrans
        return lister_ecrans()

    m = re.search(r"\b(?:affiche toi|affiche s? ?toi|caste?|diffuse|envoie toi|"
                  r"mets toi|balance toi)\b.*?\bsur\s+(?:la|le|l|mon|ma)?\s*(.+)", t)
    if not m:
        return None
    cible = _nettoyer_cible(m.group(1))
    cible = re.sub(r"\b(ecran|television|tele|tv|chromecast)\b", " ", cible).strip()
    from tools.cast import caster_jarvis
    # Sans nom exploitable, on laisse l outil choisir le premier ecran
    return caster_jarvis(ecran=cible if len(cible) >= 3 else "")



def _plex(t):
    """« mets Toy Story sur la tele », « cherche Matrix dans Plex »."""
    from tools import plex as P

    if _contient(t, ("cherche", "est ce que j ai", "tu as", "trouve")) and \
       _contient(t, ("dans plex", "sur plex", "dans la bibliotheque")):
        m = re.search(r"\b(?:cherche|trouve|est ce que j ai|tu as)\s+(.+?)"
                      r"\s+(?:dans|sur)\s+(?:plex|la bibliotheque)", t)
        if m:
            return P.plex_chercher(titre=_nettoyer_cible(m.group(1)))

    # « mets/lance/joue <titre> sur <ecran> », avec ou sans "depuis plex"
    m = re.search(r"\b(?:mets|met|lance|joue|diffuse|balance)\b\s+(.+?)"
                  r"\s+sur\s+(?:la|le|l|mon|ma)?\s*(.+)", t)
    if not m:
        return None
    titre = _nettoyer_cible(re.sub(r"\b(?:depuis|avec|via)\s+plex\b", " ", m.group(1)))
    ecran = _nettoyer_cible(m.group(2))
    ecran = re.sub(r"\b(depuis|avec|via)\s+plex\b", " ", ecran).strip()

    # Spotify a son propre raccourci ; ici on ne traite que les ecrans
    if "spotify" in ecran or len(titre) < 2:
        return None
    generique = re.search(r"\b(ecran|television|tele|tv|chromecast|salon)\b", ecran)
    ecran_n = re.sub(r"\b(ecran|television|tele|tv|chromecast)\b", " ", ecran)
    ecran_n = re.sub(r"\s+", " ", ecran_n).strip()

    if generique and not ecran_n:
        # « sur la tele » sans autre precision : le premier ecran fera l affaire
        return P.plex_jouer(titre=titre, ecran="")

    # Sinon l ecran doit correspondre a un Chromecast connu, sans quoi ce n est
    # pas une demande de diffusion (ex : « mets un film sur VLC »).
    from tools.cast import _choisir
    if _choisir(ecran_n or ecran) is None:
        return None
    return P.plex_jouer(titre=titre, ecran=ecran_n or ecran)


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
    if reponse.startswith("La messagerie n est pas configuree") or \
       reponse.startswith("La messagerie n'est pas configuree"):
        return "Liaison de communication non etablie."
    return reponse


# --------------------------------------------------------------- point d'entree

# L'ordre compte : une application CONNUE l'emporte (sinon "ouvre Prime Video"
# partirait dans la logique film a cause du mot "video"). Un titre inconnu
# retombe naturellement sur _film.
ETAPES = (_mode, _cast, _spotify, _plex, _media, _courrier, _application,
          _film, _heure, _meteo, _minuteur, _stats, _capture)


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
