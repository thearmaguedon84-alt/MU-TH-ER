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

STOP_FILM = ("arrete le film", "coupe le film", "stoppe le film",
             "coupe la lecture", "stoppe la lecture", "arrete la musique",
             "coupe la musique", "arrete tout", "coupe tout",
             "arrete la video", "coupe la video", "arrete la lecture",
             "arrete la diffusion", "coupe la diffusion",
             "stop la diffusion", "stoppe la diffusion", "arrete le cast",
             "stop vlc", "ferme vlc", "quitte vlc", "arrete vlc", "coupe vlc",
             "stoppe vlc", "stop le film", "arrete le film", "coupe le film",
             "stoppe le film", "arrete la lecture", "coupe la video",
             "stoppe la video", "ferme le film", "arrete la video",
             "stop film", "arrete film", "stopper le film")


# Mots qui designent un ecran de diffusion
ECRANS = ("tele", "television", "tv", "chromecast", "videoprojecteur",
          "video projecteur", "projecteur", "ecran", "en bas", "chambre",
          "salon", "dante")

# Verbes d'arret
ARRETS = ("arrete", "arrete", "stop", "stoppe", "coupe", "ferme", "quitte",
          "eteins", "termine")


def _arreter_diffusion(silencieux=False):
    """Coupe ce qui joue sur les Chromecast. Renvoie la liste des ecrans.

    Les appareils sont interroges en parallele : en serie, quatre ecrans
    injoignables faisaient attendre pres d une demi-minute avant la reponse.
    """
    import threading
    from tools.cast import _decouvrir

    arretes = []
    verrou = threading.Lock()

    def traiter(c):
        try:
            c.wait(timeout=3)
            if c.app_id:                 # quelque chose tourne dessus
                c.quit_app()
                with verrou:
                    arretes.append(str(c.cast_info.friendly_name))
        except Exception:
            pass

    fils = [threading.Thread(target=traiter, args=(c,), daemon=True)
            for c in _decouvrir()]
    for f in fils:
        f.start()
    for f in fils:
        f.join(timeout=5)
    return arretes


def _diffusion_active():
    """Vrai si au moins un ecran joue quelque chose."""
    from tools.cast import _decouvrir
    for c in _decouvrir():
        try:
            c.wait(timeout=4)
            if c.app_id:
                return True
        except Exception:
            continue
    return False


# Verbe d'arret conjugue, suivi de ce qu'il faut arreter. Une liste figee
# ratait « coupes VLC » pour un simple pluriel de transcription.
RE_ARRET = re.compile(
    r"\b(?:arrete|arretes|arreter|stop|stoppe|stoppes|stopper|coupe|coupes|"
    r"couper|ferme|fermes|fermer|quitte|quittes|quitter|termine|termines)\b"
    # Articles et petits mots tolerees : « arretes LA musique »
    r".{0,16}?\b(?:vlc|film|films|video|videos|lecture|musique|zik|"
    r"diffusion|plex|serie|episode|morceau|chanson|album|spotify|tout)\b")


def _media(t):
    # Arret vise sur un ecran : « coupe les minions sur la tv en bas »
    if _contient(t, ARRETS) and _contient(t, ECRANS):
        arretes = _arreter_diffusion()
        if arretes:
            return "Diffusion coupee sur " + ", ".join(arretes) + "."
        # Rien sur les ecrans : peut-etre VLC
        from tools.media import stopper_film
        return stopper_film()

    if RE_ARRET.search(t) or _contient(t, STOP_FILM):
        from tools.media import stopper_film
        # « vlc » nomme explicitement : inutile d aller couper les televisions.
        if re.search(r"\bvlc\b", t):
            return stopper_film()
        # Sinon « arrete le film » doit arreter CE QUI JOUE, ou que ce soit.
        arretes = _arreter_diffusion()
        vlc = stopper_film()
        if arretes:
            return "Diffusion coupee sur " + ", ".join(arretes) + "."
        return vlc
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
VERBES_LANCER = ("lance", "lancez", "lancer", "ouvre", "ouvrez", "ouvrir",
                 "demarre", "demarrez", "demarrer",
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


# On ne lance jamais ces applications a la voix : Jarvis se relancerait
# lui-meme (il y a un raccourci JARVIS sur le bureau), ce qui fait tourner
# deux instances en concurrence sur le micro.
APPS_INTERDITES = ("jarvis", "javis", "harvis", "jarvis assistant")


def _chercher_app(cible, seuil_flou):
    """Retrouve une application par nom, avec plusieurs strategies."""
    from tools.apps import _apps, _trouver
    apps = _apps()
    if not apps or len(cible) < 3:
        return None, None
    if cible in APPS_INTERDITES:
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
    if clef and _plat(clef) in APPS_INTERDITES:
        return None, apps
    return clef, apps


def _application(t):
    """'lance Elden Ring', 'ouvre Spotify', ou un nom d'application seul."""
    from tools.apps import launch_app

    # Une phrase d'arret ne doit jamais lancer un programme : « Stopez Spotify »
    # ouvrait l'application au lieu de couper la musique.
    if RE_ARRET.search(t) or _contient(t, ARRETS):
        return None

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



def _arret_spotify(t):
    """« arrete Spotify », « stoppe la lecture Spotify » -> pause Spotify.

    Ce raccourci passe AVANT l'arret generique : sans lui, une phrase
    mentionnant Spotify coupait les televisions, ce qui n'a aucun rapport.
    """
    if "spotify" not in t:
        return None

    arret = bool(RE_ARRET.search(t)) or _contient(t, ARRETS)
    pause = _contient(t, ("pause", "suspends", "sur pause"))
    reprise = _contient(t, ("reprends", "reprend", "continue", "relance la lecture",
                            "remets", "remet"))
    if not (arret or pause or reprise):
        return None

    from tools import spotify as S
    if not S.configure():
        return None
    if reprise and not arret:
        return S.spotify_controle(action="reprendre")
    return S.spotify_controle(action="pause")


def _spotify(t):
    """Voir aussi _spotify_appareil, appele en premier pour les transferts."""
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
    m = re.search(r"\b(?:mets|met|mettez|mettre|joue|jouez|jouer|lance|lancez|lancer|"
                  r"balance|passe|passez|passer|recherche|cherche|chercher|"
                  r"ecoute|ecoutez|ecouter)\b"
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

    m = re.search(r"\b(?:affiche|affiches|affichez|cast|caste|castes|caster|"
                  r"diffuse|diffuses|diffusez|envoie|envoies|projette|projettes|"
                  r"balance|balances)\b"
                  r"(?:\s+(?:toi|s? ?toi|moi|le|la|ca))?"
                  r".{0,12}?\bsur\s+(?:la|le|l|mon|ma)?\s*(.+)", t)
    if not m:
        return None
    cible = _nettoyer_cible(m.group(1))
    cible = re.sub(r"\b(ecran|television|tele|tv|chromecast)\b", " ", cible).strip()
    from tools.cast import caster_jarvis
    # Sans nom exploitable, on laisse l outil choisir le premier ecran
    return caster_jarvis(ecran=cible if len(cible) >= 3 else _premier_ecran())



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
        premier = _premier_ecran()
        if not premier:
            return "Je ne vois aucun ecran Chromecast."
        if _musical(t):
            return P.plex_musique(recherche=_sans_mot_musical(titre), ecran=premier)
        return P.plex_jouer(titre=titre, ecran=premier)

    # Sinon l ecran doit correspondre a un Chromecast connu, sans quoi ce n est
    # pas une demande de diffusion (ex : « mets un film sur VLC »).
    from tools.cast import _choisir
    if _choisir(ecran_n or ecran) is None:
        return None
    if _musical(t):
        return P.plex_musique(recherche=_sans_mot_musical(titre),
                              ecran=ecran_n or ecran)
    return P.plex_jouer(titre=titre, ecran=ecran_n or ecran)



def _memoire(t):
    """Memoire sur demande explicite seulement.

    Le modele local enregistrait spontanement des remarques de conversation.
    Ici il faut une intention claire : « souviens-toi que... », « retiens
    que... », « rappelle-moi que... ».
    """
    m = re.search(r"\b(?:souviens? toi|retiens|rappelle toi|note|memorise)\b"
                  r"(?:\s+(?:que|qu|de|du|des))?\s+(.+)", t)
    if m:
        contenu = m.group(1).strip()
        if len(contenu) < 3:
            return None
        from tools.memoire import remember
        return remember(categorie="note", contenu=contenu, cle="")

    m = re.search(r"\b(?:qu est ce que tu sais|de quoi tu te souviens|"
                  r"tu te souviens|rappelle moi)\b(?:\s+(?:sur|de|du|des|a propos de))?"
                  r"\s*(.*)", t)
    if m:
        sujet = m.group(1).strip()
        from tools.memoire import recall
        return recall(requete=sujet) if sujet else recall(requete="")
    return None



def _spotify_appareil(t):
    """Deplacer la musique d un appareil a l autre, ou lister les destinations."""
    from tools import spotify as S

    if _contient(t, ("quels appareils spotify", "ou peut jouer spotify",
                     "appareils spotify", "ou joue spotify")):
        return S.spotify_appareils()

    if not S.configure():
        return None

    m = re.search(r"\b(?:envoie|bascule|balance|passe|mets|met|deplace)\b\s+"
                  r"(?:la|le|l)?\s*(?:musique|zik|son|audio|spotify)\s+"
                  r"(?:sur|dans|vers)\s+(?:la|le|l|mon|ma)?\s*(.+)", t)
    if not m:
        m = re.search(r"\bspotify\s+(?:sur|dans|vers)\s+(?:la|le|l)?\s*(.+)", t)
    if not m:
        return None

    cible = _nettoyer_cible(m.group(1))
    if len(cible) < 3:
        return None
    return S.spotify_transferer(appareil=cible)



# Indices qu'il s'agit de musique et non d'un film
MOTS_MUSIQUE = ("album", "chanson", "morceau", "musique", "titre de",
                "disque", "playlist", "zik", "artiste", "groupe",
                "en musique", "de la musique")


def _premier_ecran():
    """Nom du premier Chromecast trouve, ou "" s il n y en a aucun.

    Depuis que l absence de destination signifie « sur le PC », une demande
    « sur la tele » doit etre resolue en un nom reel, sans quoi elle repartirait
    vers l ordinateur.
    """
    from tools.cast import _decouvrir
    appareils = _decouvrir()
    if not appareils:
        return ""
    # « sur la tele » doit viser un televiseur, pas la premiere enceinte
    # trouvee : l ordre de decouverte varie d une fois sur l autre.
    def rang(c):
        n = sans_accents(str(c.cast_info.friendly_name).lower())
        if "tv" in n or "tele" in n:
            return 0
        if "salon" in n or "bas" in n:
            return 1
        if "projecteur" in n:
            return 2
        return 3
    return str(sorted(appareils, key=rang)[0].cast_info.friendly_name)


def _musical(t):
    """Vrai si la phrase parle de musique plutot que de video."""
    return _contient(t, MOTS_MUSIQUE)


def _sans_mot_musical(titre):
    """Retire l indice de genre : « l album Combat Rock » -> « Combat Rock ».

    Sans ca, la recherche portait sur « album combat rock » et ne trouvait
    rien, le mot parasite faisant chuter la ressemblance.
    """
    # « en musique », « de la musique » : on retire la locution entiere,
    # sinon la preposition orpheline pollue la recherche.
    t = re.sub(r"\b(en|de la|du|des|de|d|avec)\s+(musique|zik)\b", " ", titre)
    t = re.sub(r"\b(l|le|la|les)?\s*(album|chanson|morceau|musique|disque|"
               r"playlist|zik|artiste|groupe|titre)\b", " ", t)
    t = re.sub(r"\b(de la|du|des|de|d)\b", " ", t)
    t = re.sub(r"\s+(en|de|du|des|a|au|aux|sur|dans|avec)\s*$", " ", t)
    return _nettoyer_cible(t) or titre



def _plex_sans_ecran(t):
    """« mets Black Sabbath dans Zik », « lance Matrix dans Plex ».

    Sans destination nommee, la lecture se fait sur le PC. Ces formulations
    n etaient reconnues par aucun raccourci et partaient au LLM, qui choisissait
    souvent la video alors qu on demandait de la musique.
    """
    from tools import plex as P

    # Faut-il chercher dans la musique ou dans les films ?
    dans_zik = _contient(t, ("dans zik", "dans la zik", "dans ma musique",
                             "dans la musique", "en musique", "dans mes albums"))
    dans_plex = _contient(t, ("dans plex", "sur plex", "depuis plex",
                              "dans la bibliotheque"))
    if not (dans_zik or dans_plex):
        return None

    m = re.search(r"\b(?:mets|met|mettez|joue|jouez|lance|lancez|passe|passez|"
                  r"balance|ecoute|ecoutez|regarde|regardez|met moi|mets moi)\b"
                  r"\s+(?:moi\s+)?(?:du|de la|des|le|la|les|l|un|une)?\s*(.+)", t)
    cible = m.group(1) if m else t

    # Une destination peut malgre tout etre nommee : « ... en musique sur la
    # tv en bas ». On l extrait avant de nettoyer le titre.
    ecran = ""
    m_ecran = re.search(r"\bsur\s+(?:la|le|l|mon|ma)?\s*(.+)$", cible)
    if m_ecran:
        candidat = m_ecran.group(1)
        if not re.search(r"\bplex\b", candidat):
            propre = re.sub(r"\b(ecran|television|tele|tv|chromecast)\b", " ", candidat)
            propre = _nettoyer_cible(propre)
            generique = re.search(r"\b(ecran|television|tele|tv|chromecast)\b", candidat)
            from tools.cast import _choisir
            if propre and _choisir(propre) is not None:
                ecran = propre
                cible = cible[:m_ecran.start()]
            elif generique:
                ecran = _premier_ecran()      # « sur la tele » : premier ecran
                cible = cible[:m_ecran.start()]

    # On retire les localisations et les mots de genre
    cible = re.sub(r"\b(?:qui est|qu il y a|qui se trouve|se trouve)\b", " ", cible)
    cible = re.sub(r"\bdans (?:la |le |les |ma |mes )?(?:zik|musique|plex|"
                   r"bibliotheque|albums)\b", " ", cible)
    cible = re.sub(r"\b(?:sur|depuis) plex\b", " ", cible)
    cible = _sans_mot_musical(_nettoyer_cible(cible))
    if len(cible) < 2:
        return None

    # « l album de X sur Plex » : le mot « album » l emporte sur « Plex ».
    # Sans ca, toute demande musicale mentionnant Plex partait vers les films.
    if dans_zik or _musical(t):
        return P.plex_musique(recherche=cible, ecran=ecran)
    return P.plex_jouer(titre=cible, ecran=ecran)



def _musique_sans_source(t):
    """« ecoute l album X », « mets du Green Day » -> Spotify par defaut.

    Sans source nommee, on privilegie Spotify : son catalogue couvre tout,
    alors que la bibliotheque locale ne contient qu'une partie. Une demande
    visant explicitement Plex a deja ete traitee plus haut.
    """
    from tools import spotify as S

    # Il faut une intention musicale claire, sinon on capterait « lance un film »
    if not _musical(t):
        return None
    if _contient(t, ("film", "video", "serie", "episode")):
        return None

    m = re.search(r"\b(?:mets|met|mettez|mettre|joue|jouez|jouer|lance|lancez|"
                  r"lancer|passe|passez|balance|ecoute|ecoutez|ecouter|"
                  r"met moi|mets moi)\b"
                  r"\s+(?:moi\s+)?(?:du|de la|des|de|le|la|les|l|un|une)?\s*(.+)", t)
    if not m:
        return None

    cible = m.group(1)

    # Destination eventuelle : « ... sur la tele » vise un ecran, pas Spotify
    ecran = ""
    m_ecran = re.search(r"\bsur\s+(?:la|le|l|mon|ma)?\s*(.+)$", cible)
    if m_ecran:
        candidat = m_ecran.group(1)
        propre = re.sub(r"\b(ecran|television|tele|tv|chromecast)\b", " ", candidat)
        propre = _nettoyer_cible(propre)
        generique = re.search(r"\b(ecran|television|tele|tv|chromecast)\b", candidat)
        from tools.cast import _choisir
        if propre and _choisir(propre) is not None:
            ecran, cible = propre, cible[:m_ecran.start()]
        elif generique:
            ecran, cible = _premier_ecran(), cible[:m_ecran.start()]

    genre = ""
    for mot, g in (("album", "album"), ("playlist", "playlist"),
                   ("artiste", "artiste"), ("groupe", "artiste"),
                   ("chanson", "titre"), ("morceau", "titre"),
                   ("titre", "titre")):
        if re.search(r"\b" + mot + r"\b", cible):
            genre = g
            break

    cible = _sans_mot_musical(_nettoyer_cible(cible))
    if len(cible) < 2:
        return None

    # Un ecran a ete demande : la musique locale sait diffuser, pas Spotify
    if ecran:
        from tools import plex as P
        return P.plex_musique(recherche=cible, ecran=ecran)

    if not S.configure():
        from tools import plex as P
        return P.plex_musique(recherche=cible, ecran="")
    return S.spotify_jouer(recherche=cible, genre=genre)



def _streaming(t):
    """« cherche Stranger Things sur Netflix », « ouvre Prime Video ».

    Ces plateformes n exposent aucune interface publique : on ne peut pas
    lancer la lecture, seulement ouvrir la recherche du titre.
    """
    from tools.streaming import reconnaitre, streaming_chercher

    plateforme = reconnaitre(t)
    if plateforme is None:
        return None

    m = re.search(r"\b(?:cherche|recherche|trouve|mets|met|lance|regarde|"
                  r"regardez|ouvre|ouvrez|joue)\b\s+(.+?)"
                  r"\s+sur\s+(?:netflix|net flix|prime video|prime|amazon prime|"
                  r"disney plus|disney|youtube|you tube|mycanal|my canal|"
                  r"canal plus|canalplus|canal)\b", t)
    if m:
        titre = _nettoyer_cible(m.group(1))
        titre = re.sub(r"^(?:le |la |les |l )?(?:film|serie|episode)\s+", "", titre)
        titre = _nettoyer_cible(titre)
        if len(titre) >= 2:
            return streaming_chercher(titre=titre, plateforme=plateforme)

    # « ouvre Netflix » tout court
    if re.search(r"\b(?:ouvre|ouvrez|lance|lancez|demarre|affiche)\b", t):
        return streaming_chercher(titre="", plateforme=plateforme)
    return None



def _youtube(t):
    """« mets telle video sur YouTube sur la tele ».

    YouTube est la seule grande plateforme dont le recepteur Chromecast se
    laisse piloter : on peut donc reellement lancer une video, pas seulement
    ouvrir une page de recherche.
    """
    if not re.search(r"\byou ?tube\b", t):
        return None

    from tools.youtube import youtube_caster

    m = re.search(r"\b(?:mets|met|mettez|joue|jouez|lance|lancez|passe|passez|"
                  r"regarde|regardez|montre|cherche|ecoute|ecoutez)\b"
                  r"\s+(?:moi\s+)?(?:(?:la|le|les|l|un|une|du|de la)\s+)?(.+)", t)
    if not m:
        return None
    reste = m.group(1)

    # Destination eventuelle, avant de retirer le mot « youtube »
    ecran = ""
    # « ... sur YouTube sur la tele » : c est le DERNIER « sur » qui designe
    # l ecran ; le premier nomme la plateforme.
    m_ecran = None
    for trouve in re.finditer(r"\bsur\s+(?:la|le|l|mon|ma)?\s*([^,]+)$", reste):
        m_ecran = trouve
    for trouve in re.finditer(r"\bsur\s+(?:la|le|l|mon|ma)?\s*(.+?)(?=\s+sur\s+|$)", reste):
        if not re.search(r"\byou ?tube\b", trouve.group(1)):
            m_ecran = trouve
    if m_ecran:
        candidat = m_ecran.group(1)
        if not re.search(r"\byou ?tube\b", candidat):
            propre = re.sub(r"\b(ecran|television|tele|tv|chromecast)\b", " ", candidat)
            propre = _nettoyer_cible(propre)
            generique = re.search(r"\b(ecran|television|tele|tv|chromecast)\b", candidat)
            from tools.cast import _choisir
            if propre and _choisir(propre) is not None:
                ecran, reste = propre, reste[:m_ecran.start()]
            elif generique:
                ecran, reste = _premier_ecran(), reste[:m_ecran.start()]

    # On retire la mention de la plateforme
    reste = re.sub(r"\b(?:sur|dans|via|depuis)\s+you ?tube\b", " ", reste)
    reste = re.sub(r"\byou ?tube\b", " ", reste)
    cible = _nettoyer_cible(reste)
    if len(cible) < 2:
        return None
    return youtube_caster(recherche=cible, ecran=ecran)



def _ecran_lecture(t):
    """Pilote ce qui passe sur un ecran, quelle que soit l application.

    CANAL+, YouTube, Plex et la plupart des recepteurs publient leur etat sur
    l espace de noms standard de Google Cast : on ne peut pas demarrer leur
    lecture, mais on peut la commander une fois lancee.
    """
    if not _contient(t, ECRANS):
        return None

    from tools.ecran_cast import ecran_controle, ecran_en_cours

    # Quel ecran ?
    ecran = ""
    m = re.search(r"\bsur\s+(?:la|le|l|mon|ma)?\s*(.+)$", t)
    if m:
        propre = re.sub(r"\b(ecran|television|tele|tv|chromecast)\b", " ", m.group(1))
        propre = _nettoyer_cible(propre)
        from tools.cast import _choisir
        if propre and _choisir(propre) is not None:
            ecran = propre

    if _contient(t, ("qu est ce qui passe", "on regarde quoi", "qu est ce qu on regarde",
                     "c est quoi sur la", "qu y a t il sur")):
        return ecran_en_cours(ecran=ecran)

    for cles, action in (
        (("mets en pause", "met en pause", "pause", "suspends"), "pause"),
        (("reprends", "reprend", "relance la lecture", "continue"), "reprendre"),
        (("avance", "saute", "passe devant"), "avancer"),
        (("recule", "reviens en arriere", "retour arriere"), "reculer"),
    ):
        if _contient(t, cles):
            m_sec = re.search(r"(\d{1,3})\s*(?:secondes?|s)\b", t)
            sec = int(m_sec.group(1)) if m_sec else 30
            return ecran_controle(action=action, ecran=ecran, secondes=sec)
    return None



def _diffuser_service(t):
    """« lance myCanal sur la tele » : Chrome ouvre le service et le diffuse.

    Les plateformes qui refusent d etre pilotees de l exterieur acceptent en
    revanche que leur propre page web lance la diffusion. Jarvis passe donc par
    son navigateur, qui s authentifie comme le ferait un clic.
    """
    from tools.streaming import PLATEFORMES, reconnaitre

    plateforme = reconnaitre(t)
    if plateforme is None:
        return None
    # YouTube a son propre outil, bien meilleur : on le laisse passer
    if plateforme == "youtube":
        return None

    # Il faut une destination : sinon c est une simple recherche
    if not _contient(t, ECRANS):
        return None

    ecran = ""
    m = re.search(r"\bsur\s+(?:la|le|l|mon|ma)?\s*([^,]+)$", t)
    if m:
        propre = re.sub(r"\b(ecran|television|tele|tv|chromecast)\b", " ", m.group(1))
        propre = _nettoyer_cible(propre)
        from tools.cast import _choisir
        if propre and _choisir(propre) is not None:
            ecran = propre
        else:
            ecran = _premier_ecran()

    # Titre eventuel a chercher sur la plateforme
    titre = ""
    m_titre = re.search(r"\b(?:cherche|recherche|trouve|mets|met|lance|regarde|"
                        r"regardez|joue)\b\s+(.+?)\s+sur\s+", t)
    if m_titre:
        titre = _nettoyer_cible(m_titre.group(1))
        titre = re.sub(r"^(?:le |la |les |l )?(?:film|serie|episode|cast|"
                       r"diffusion|lecture)\s+", "", titre)
        titre = re.sub(r"^(?:de |du |des |d )\s*", "", titre).strip()
        # " lance le cast de my canal sur la tv " : ce qui reste n est pas un
        # titre mais le nom du service. Chercher cela ne donnerait rien.
        if reconnaitre(titre) is not None or len(titre) < 2:
            titre = ""

    # Prime a sa propre chaine : recherche par adresse, lien de lecture, puis
    # recopie d ecran faute de diffusion native. Elle fait mieux que le
    # traitement generique.
    # Netflix sait diffuser nativement, mais son portail de profils bloque
    # tout tant qu il n est pas franchi : son module s en charge.
    if plateforme == "netflix":
        if titre:
            from tools.netflix import netflix_jouer
            return netflix_jouer(titre=titre, ecran=ecran)
        if ecran:
            from tools.netflix import netflix_caster
            return netflix_caster(ecran=ecran)

    if plateforme == "primevideo" and titre:
        from tools.prime import prime_jouer
        return prime_jouer(titre=titre, ecran=ecran)

    info = PLATEFORMES[plateforme]
    url = (info["url"].format(q=__import__("urllib.parse", fromlist=["quote"]).quote(titre))
           if titre else info["url"].split("/search")[0].split("/recherche")[0])

    # On demande d abord a la page de diffuser elle-meme : la tele lance alors
    # l application du service. La recopie d onglet ne sert que si la page ne
    # sait pas le faire, car elle degrade l image et se heurte aux protections.
    from tools.navigateur_cast import caster_service, diffuser_page
    reponse = caster_service(url=url, ecran=ecran)
    if reponse.startswith("Le lecteur n est pas pret"):
        return diffuser_page(url=url, ecran=ecran)
    return reponse



# Mots qui designent la television en direct plutot qu un fichier ou un morceau.
DIRECT = ("chaine", "chaines", "direct", "la tele", "television", "canal",
          "mycanal", "my canal", "tnt")


def _chaine_tv(t):
    """« mets Arte sur la tele du bas » : chaine myCANAL, jusqu a l ecran.

    Une chaine se reconnait a son nom, pas a une recherche floue : on exige
    donc une correspondance nette, et le contexte d une television.
    """
    if not re.search(r"\b(?:mets|met|mettez|mettre|lance|lancez|lancer|passe|"
                     r"passez|bascule|regarde|regardez|zappe|balance)\b", t):
        return None

    ecran_demande = _contient(t, ECRANS)
    if not ecran_demande and not any(d in t for d in DIRECT):
        return None

    # Ce qui suit le verbe, avant la destination.
    m = re.search(r"\b(?:mets|met|mettez|mettre|lance|lancez|lancer|passe|"
                  r"passez|bascule|regarde|regardez|zappe|balance)\b"
                  r"\s+(?:moi\s+)?(?:sur\s+)?(.+)", t)
    if not m:
        return None
    cible = m.group(1)
    cible = re.split(r"\bsur\b", cible)[0]
    cible = re.sub(r"^(?:la |le |les |l )?(?:chaine|chaines)\s+", "", cible)
    cible = _nettoyer_cible(cible)
    if not cible or len(cible) < 2:
        return None

    from tools.canal import chercher, canal_chaine, _note
    trouvee = chercher(cible)
    if trouvee is None:
        return None
    # Sans contexte explicite de television, on n accepte qu un nom exact :
    # sinon un titre de film finirait sur une chaine au nom voisin.
    exigence = 0.75 if any(d in t for d in DIRECT) else 0.93
    if _note(cible, trouvee["nom"]) < exigence:
        return None

    ecran = ""
    if ecran_demande:
        m_ecran = re.search(r"\bsur\s+(?:la|le|l|mon|ma)?\s*([^,]+)$", t)
        propre = ""
        if m_ecran:
            propre = re.sub(r"\b(ecran|television|tele|tv|chromecast)\b", " ",
                            m_ecran.group(1))
            propre = _nettoyer_cible(propre)
        from tools.cast import _choisir
        ecran = propre if (propre and _choisir(propre) is not None) else _premier_ecran()

    return canal_chaine(chaine=trouvee["nom"], ecran=ecran)



# Phrase envoyee par le bouton du telephone : l appui volontaire vaut accord.
PHRASE_TELEPHONE = "extinction confirmee depuis le telephone"

RE_ANNULE_ARRET = re.compile(
    r"\b(?:annule|annuler|stoppe|arrete)\b[^.]{0,24}"
    r"\b(?:extinction|arret|redemarrage|eteindre)\b"
    r"|n[\u2019' ]?eteins? pas|laisse[\s-]*(?:le\s+)?(?:pc|ordi|ordinateur)?\s*allume"
    r"|annule l arret")

RE_VERROU = re.compile(
    r"\b(?:verrouille|verrouiller|bloque)\b[^.]{0,18}"
    r"\b(?:pc|ordi|ordinateur|session|machine)\b")


def _extinction(t):
    """Extinction, redemarrage et verrouillage de l ordinateur.

    Place avant les arrets de lecture : « arrete l ordinateur » ne doit pas
    finir en « arrete la musique ». On exige donc que la machine soit nommee.
    """
    from tools.arret_pc import (annuler_extinction, eteindre_pc,
                                redemarrer_pc, verrouiller_pc)

    if RE_ANNULE_ARRET.search(t):
        return annuler_extinction()

    if t.strip() == PHRASE_TELEPHONE:
        return eteindre_pc()

    if RE_VERROU.search(t):
        return verrouiller_pc()

    # Il faut a la fois une intention et la machine nommee : sans cela,
    # « coupe tout » ou « arrete le film » viendraient ici par erreur.
    machine = r"(?:pc|ordi|ordinateur|machine|tour|station)"
    if re.search(r"\b(?:redemarre|redemarrer|reboot|relance)\b[^.]{0,18}\b"
                 + machine + r"\b", t):
        return redemarrer_pc()

    if re.search(r"\b(?:eteins|eteindre|eteint|coupe|couper|arrete|arreter|"
                 r"ferme|fermer)\b[^.]{0,18}\b" + machine + r"\b", t):
        return eteindre_pc()

    return None



# Mots qui designent explicitement le web : sans eux, « cherche Interstellar »
# doit rester une recherche de film, pas une requete en ligne.
RE_WEB = re.compile(
    r"\b(?:sur\s+)?(?:internet|le net|le web|google|en ligne)\b"
    r"|\brecherche web\b|\bcherche moi sur\b")


def _web(t):
    """« cherche la meteo de demain sur internet ».

    On n intercepte que les demandes qui nomment le web. Le modele garde la
    main pour tout le reste : c est lui qui decide si une question merite une
    verification en ligne, et il a l outil pour le faire.
    """
    if not RE_WEB.search(t):
        return None
    # Le verbe est facultatif : « resultat sur internet du dernier match »
    # nomme le web sans demander explicitement de chercher, et veut pourtant
    # une recherche. On prend la phrase entiere a defaut de verbe.
    m = re.search(r"\b(?:cherche|recherche|trouve|regarde|renseigne toi sur|"
                  r"informe toi sur|va voir)\b\s+(?:moi\s+)?(.+)", t)
    question = RE_WEB.sub(" ", m.group(1) if m else t)
    question = re.sub(r"\b(?:pour moi|s il te plait|stp)\b", " ", question)
    question = " ".join(question.split()).strip(" ,.")
    if len(question) < 3:
        return None
    from tools.web import chercher_web
    return chercher_web(question=question)



# « et envoie-la moi par mail » se greffe sur n importe quelle demande d image.
# La mention doit etre retiree du texte avant qu il ne devienne la description,
# sans quoi le moteur dessinerait consciencieusement une enveloppe.
RE_PAR_MAIL = re.compile(r"\b(?:par|via)\s+(?:mail|e-?mail|courriel|mel)\b")


def _sans_mention_mail(t):
    t = RE_PAR_MAIL.sub(" ", t)
    # Les pronoms s empilent : « envoie-la-moi ». Il faut tous les manger,
    # sinon un « moi » orphelin finit dans la description de l image.
    t = re.sub(r"\bet\s+(?:tu\s+m\s*)?(?:envoie|envoies|envoi|envoyer|"
               r"transmets|transmet)(?:\s+(?:moi|la|le|les|ca|nous))*\b",
               " ", t)
    t = re.sub(r"\b(?:envoie|envoies|envoyer)(?:\s+(?:moi|la|le|les|ca))*\s*$",
               " ", t)
    return " ".join(t.split()).strip(" ,.")


RE_SANS_RETOUCHE = re.compile(
    r"\bsans retouche\b|\bvite fait\b|\bsans corriger\b|\bbrut\b")


RE_IMAGE = re.compile(
    r"\b(?:fais|fait|fabrique|genere|generer|cree|creer|dessine|dessiner|"
    r"montre|produis|sors)\b[^.]{0,26}?"
    r"\b(?:images?|dessins?|illustrations?|photos?|visuels?|rendus?)\b"
    r"|\b(?:une?\s+|l\s*)?(?:image|illustration|dessin|visuel)\s+(?:de|d|du|des|avec)\b"
    r"|\bdessine[- ]moi\b|\ben image\b"
    r"|\bmontre[- ]moi\s+a\s+quoi\s+ressemble\b")

# Questions sur la capacite elle-meme : le modele repondait de memoire, sans
# regarder ses outils, et niait savoir faire ce qu il sait faire.
RE_SAIT_IMAGE = re.compile(
    r"\b(?:tu sais|sais tu|tu peux|peux tu|est ce que tu (?:sais|peux))\b"
    r"[^.?]{0,34}\b(?:images?|dessins?|illustrations?)\b")



# Les mots par lesquels on demande du soin plutot que de la vitesse. « avec
# flux » y figure parce que c est ainsi qu on finit par le nommer une fois
# qu on sait qu il existe.
RE_SOIGNEE = re.compile(
    r"\b(?:tres\s+)?(?:soignees?|soignes?|peaufinees?|lechees?|"
    r"haute\s+qualite|meilleure\s+qualite|qualite\s+maximale|"
    r"tres\s+belle|superbe|impeccable|detaillee?s?)\b"
    r"|\b(?:avec|en|sous|via)\s+flux\b")

# Quand on accepte d attendre sept minutes plutot qu une.
RE_PATIENT = re.compile(
    r"\bprends?\s+ton\s+temps\b|\bau\s+maximum\b|\bqualite\s+maximale\b"
    r"|\ble\s+mieux\s+possible\b|\bmeme\s+si\s+c\s+est\s+long\b"
    r"|\bsans\s+te\s+presser\b")

# Ce qui, malgre le mot « soigne », ne demande pas une image neuve : la
# retouche d une partie de ce qui existe deja.
RE_SOIN_RETOUCHE = re.compile(
    r"\bsoigne\w*\s+(?:les?\s+|la\s+|ses\s+|mes\s+)?"
    r"(?:mains?|doigts?|visages?|yeux|dents|details?)\b")

def _image(t):
    """« fais-moi une image d un alien en maillot de bain ».

    La traduction vers l anglais se fait dans l outil : passer par le modele
    ici couterait un aller-retour de plus pour le meme resultat.
    """
    if RE_SAIT_IMAGE.search(t):
        return ("Oui. Je fabrique des images en local, sans rien envoyer "
                "dehors. Dis-moi ce que tu veux voir : « fais-moi une image "
                "d un alien a la plage ». Je peux aussi l envoyer sur une "
                "television.")

    if not RE_IMAGE.search(t):
        return None

    par_mail = bool(RE_PAR_MAIL.search(t))
    if par_mail:
        t = _sans_mention_mail(t)

    # La correction des mains coute une quinzaine de secondes : on laisse la
    # possibilite de s en passer.
    soigner = not RE_SANS_RETOUCHE.search(t)
    if not soigner:
        t = RE_SANS_RETOUCHE.sub(" ", t)

    sujet = re.split(r"\b(?:image|dessin|illustration|photo|visuel|rendu)\b", t, 1)
    sujet = sujet[-1] if len(sujet) > 1 else t
    # « dessine » ne contient pas « dessin » au sens des limites de mots : le
    # verbe survivait au decoupage et se retrouvait dans la description.
    sujet = re.sub(r"^\s*(?:fais|fabrique|genere|generer|cree|creer|dessine|"
                   r"dessiner|montre)\b\s*(?:moi\s+)?", "", sujet.strip(),
                   flags=re.I)
    sujet = re.sub(r"^\s*(?:une?|le|la|les|de|d|du|des|avec|representant|"
                   r"montrant|qui)\b\s*", "", sujet.strip(), flags=re.I)

    ecran = ""
    if _contient(t, ECRANS):
        # Le point de coupe est le DERNIER « sur » : sans le prefixe gourmand,
        # « un chat sur un skateboard sur la tele » perdait le skateboard.
        m = re.search(r"^.*\b(sur)\s+(?:la|le|l|mon|ma)?\s*([^,]+)$", sujet)
        if m:
            propre = re.sub(r"\b(ecran|television|tele|tv|chromecast)\b", " ",
                            m.group(2))
            propre = _nettoyer_cible(propre)
            from tools.cast import _choisir
            if propre and _choisir(propre) is not None:
                ecran = propre
                sujet = sujet[:m.start(1)]
        if not ecran:
            ecran = _premier_ecran()
            sujet = re.sub(r"^(.*)\bsur\s+(?:la|le|l|mon|ma)?\s*[^,]+$", r"\1", sujet)

    format_voulu = ""
    if re.search(r"\bportrait\b|\bvertical\b", sujet):
        format_voulu = "portrait"
    elif re.search(r"\bpaysage\b|\bhorizontal\b", sujet):
        format_voulu = "paysage"

    if format_voulu:
        # Le mot de format a servi, il n a plus rien a faire dans la demande.
        sujet = re.sub(r"\b(?:en\s+)?(?:portrait|paysage|vertical|horizontal)\b",
                       " ", sujet)

    sujet = re.sub(r"^\s*(?:de|d|du|des|moi)\b\s*", "", sujet.strip())
    sujet = " ".join(sujet.split()).strip(" ,.")
    if len(sujet) < 3:
        return None

    # « fais-moi une photo de X, et remplace sa tete par Y » : on cree, puis
    # on remplace sur le resultat. La consigne de remplacement ne doit pas
    # se retrouver dans la description, sinon le moteur dessine les deux.
    zone_apres = _zone_demandee(t)
    if zone_apres:
        sujet = re.split(r"\b(?:je veux que tu|enleve|enlever|retire|remplace|"
                         r"remplacer|et mets?|puis mets?)\b", sujet)[0]
        sujet = " ".join(sujet.split()).strip(" ,.")
        if len(sujet) < 3:
            sujet = "a person, full body, photograph"

    # « une image tres soignee » : c est Flux qu on veut. Il est bien plus
    # lent, donc on ne le choisit que sur demande explicite — et jamais quand
    # il faudra ensuite remplacer une zone, ce que seul SDXL sait faire.
    if RE_SOIGNEE.search(t) and not RE_SOIN_RETOUCHE.search(t) and not zone_apres:
        sujet_soigne = " ".join(RE_SOIGNEE.sub(" ", sujet).split()).strip(" ,.")
        from tools.flux import image_soignee
        return image_soignee(description=sujet_soigne or sujet,
                             format=format_voulu,
                             patient=bool(RE_PATIENT.search(t)),
                             ecran=ecran, par_mail=par_mail)

    from tools.image import generer_image
    faite = generer_image(description=sujet, format=format_voulu, ecran=ecran,
                          par_mail=par_mail and not zone_apres,
                          soigner=soigner)
    if not zone_apres:
        return faite

    from tools.zone import remplacer_zone
    return remplacer_zone(par=zone_apres["par"], zone=zone_apres["zone"],
                          image="la derniere image", ecran=ecran,
                          par_mail=par_mail)



# Le refus explicite de reprendre une image existante. Sans lui, une demande
# de creation reformulee retombait indefiniment sur le meme fichier.
RE_PAS_REPRENDRE = re.compile(
    r"\bne repren\w+\b|\barrete de repren\w+\b|\bpas repren\w+\b"
    r"|\bnouvelle image\b|\bgener\w+ une nouvelle\b|\bune nouvelle\b"
    r"|\bdepuis le (?:prompt|texte)\b|\ba partir du (?:prompt|texte)\b")


RE_MODIF_IMAGE = re.compile(
    r"\b(?:modifie?|modifier|transforme|transformer|retouche|retoucher|"
    r"refais|refaire|reprends?|rempr?ends?|reprendre|change|changer|"
    r"remplace|remplacer|enleve|enlever|ajoute|ajouter)\b[^.]{0,40}?"
    r"\b(?:image|photo|dessin|illustration|visuel)\b")

# Les facons de designer l image a reprendre. On les retire du texte : ce qui
# reste est la consigne.
# Un nom de fichier dit a voix haute ou tape : « ma photo vacances 21-04-07
# 058.jpg ». Il prime sur toute designation vague, puisqu il est precis.
# Attention : la phrase arrive DEJA aplatie — sans accents, sans ponctuation.
# « vacances 21-04-07 058.jpg » y devient « vacances 21 04 07 058 jpg ». Une
# expression qui attend un point ne peut donc jamais correspondre. On cherche
# les mots qui precedent l extension, devenue un mot comme un autre.
RE_NOM_FICHIER = re.compile(
    r"\b([a-z0-9][a-z0-9]*(?:\s+[a-z0-9]+){0,6})\s+(jpe?g|png|webp|bmp)\b")

# « ma photo X », « la photo qui s appelle X » : le nom suit le mot photo.
RE_PHOTO_NOMMEE = re.compile(
    r"\b(?:photos?|images?|fichiers?)\s+(?:qui\s+s\s*appelle\s+|"
    r"nommee?\s+|intitulee?\s+)?([\w\-]+(?:[ _\-][\w\-]+){0,4})",
    re.I)



# Un nom de fichier survit mal a l aplatissement : « vacances 21-04-07 058.jpg »
# devient « vacances 21 04 07 058 jpg », et sans extension il ne reste que des
# mots. On s appuie donc sur ce qui distingue un nom de fichier d une phrase :
# de longues suites de chiffres, que le francais courant ne contient pas.
# Les mots qui, en francais, articulent une phrase et ne peuvent donc pas
# se trouver au milieu d un nom de fichier.
_ARRET = (r"(?!(?:par|celui|celle|ceux|celles|comme|et|puis|pour|avec|"
          r"sur|dans|mais|donc|reference|remplace|remplacer|mets|met|mettre|"
          r"prends|prend|prendre|utilise|utiliser|applique|appliquer|"
          r"transpose|transposer|colle|coller|pose|poser|ajoute|greffe|"
          r"incruste|recupere|extrais|copie|reprends|reprend|montre|affiche|"
          r"anime|genere|image|images|photo|photos|fichier|fichiers)\b)")

RE_NOM_SANS_EXTENSION = re.compile(
    r"\b((?:" + _ARRET + r"[a-z]{3,}\s+)?(?:\d{4,}(?:\s+\d+)*|\d{2,}(?:\s+\d{2,}){1,6})"
    r"(?:\s+" + _ARRET + r"[a-z0-9]{2,}){0,8})")

# Les mots qui introduisent une image, et qui ne font pas partie du nom.
_AVANT_NOM = (r"^(?:et|puis|sur|dans|de|du|la|le|les|l|ma|mon|mes|une?|"
              r"photos?|images?|fichiers?|partir|depuis|prends?|prend|"
              r"remplace|mets?|avec|comme|anime|animer|transforme|modifie?|"
              r"retouche|genere|generer|fais|fait|montre|reprends?)\s+")


_LIAISON = (r"(?:comme|et|puis|pour|sur|dans|avec|afin|ensuite|remplace|"
            r"mets?|mettre|reference|en|de|du|des|par|celui|celle)")


def _elaguer(nom, garder="debut"):
    """Retire les mots de liaison colles au nom du fichier."""
    nom = (nom or "").strip()
    precedent = None
    while nom and nom != precedent:
        precedent = nom
        nom = re.sub(_AVANT_NOM, "", nom).strip()
    # Une extension prononcee ne fait pas partie du nom.
    nom = re.sub(r"\s+(jpe?g|png|webp|bmp)\b.*$", "", nom)
    # Les mots de liaison decoupent la phrase. Selon ce qui a servi d ancre,
    # le nom est avant ou apres : les chiffres ancrent le debut, l extension
    # ancre la fin. « ... sur humain avant png » : le nom est le dernier bout.
    morceaux = re.split(r"\s+%s\b\s*" % _LIAISON, nom)
    morceaux = [m for m in morceaux if m.strip()]
    if not morceaux:
        return ""
    retenu = (morceaux[-1] if garder == "fin" else morceaux[0]).strip()
    # Le bout retenu porte souvent encore ses articles : « la photo portrait
    # 3 ». On relance l elagage dessus, sinon le nom ne se resout pas.
    precedent = None
    while retenu and retenu != precedent:
        precedent = retenu
        retenu = re.sub(_AVANT_NOM, "", retenu).strip()
    return retenu


# Trois familles de verbes, qui ne rangent pas leurs complements pareil.
_PRISE = (r"prends?|prendre|utilise\w*|utiliser|reprends?|recupere\w*|"
          r"extrais?|extraire|copie\w*")
# Remplacer : le complement direct disparait, le complement en « par » arrive.
_REMPLACE = r"remplace\w*|remplacer|change\w*|changer|echange\w*|substitue\w*"
# Poser : le complement direct arrive, le complement en « sur » recoit.
_POSE = (r"mets?|mettre|colle\w*|coller|applique\w*|appliquer|transpose\w*|"
         r"transposer|pose\w*|poser|incruste\w*|greffe\w*|ajoute\w*")

_VERBES = re.compile(r"\b(%s|%s|%s)\b" % (_PRISE, _REMPLACE, _POSE))


def _famille(verbe):
    if re.fullmatch(_PRISE, verbe):
        return "prise"
    if re.fullmatch(_REMPLACE, verbe):
        return "remplace"
    return "pose"


# « le visage de », « celui de la photo » : la designation qui suit possede le
# visage. Vrai avec les verbes de pose, faux avec remplacer.
_POSSESSIF = re.compile(
    r"\b(?:visages?|tetes?|tronches?|figures?|celui|celle)\s+"
    r"(?:de\s+|du\s+|d\s+)?(?:la\s+|le\s+|les\s+|ma\s+|mon\s+|mes\s+)?"
    r"(?:photos?\s+|images?\s+|fichiers?\s+)?$")


def _role(avant, depuis_le_debut):
    """« reference » ou « cible » pour la designation qui suit ce bout.

    On cherche le dernier verbe qui la gouverne, puis on lit ce qui separe ce
    verbe de la designation : c est la que se joue le sens.
    """
    verbes = list(_VERBES.finditer(avant))
    reste = avant
    if verbes:
        famille = _famille(verbes[-1].group(1))
        reste = avant[verbes[-1].end():]
    else:
        # Rien devant : le verbe qui commande est le dernier rencontre.
        tous = list(_VERBES.finditer(depuis_le_debut))
        if not tous:
            return ""
        famille = _famille(tous[-1].group(1))

    # « par celui de Y » : ce qui vient apres « par » est toujours ce qui
    # arrive, donc la reference. C est vrai pour toutes les familles.
    if re.search(r"\bpar\b", reste):
        return "reference"

    if famille == "prise":
        return "reference"
    if famille == "remplace":
        # Le complement direct de remplacer est ce qui disparait.
        return "cible"
    # Poser : le visage « de » quelqu un est la source ; « sur » quelque chose
    # est la destination.
    if _POSSESSIF.search(reste):
        return "reference"
    return "cible"


def _place(t, nom, defaut):
    """Ou commence vraiment le nom retenu, une fois les mots de liaison otes.

    Le motif attrape souvent un mot de trop devant — « sur 20260901... ». En
    gardant sa position, la fenetre d analyse s arrete avant ce mot, et l on
    perd justement le mot qui dit le role.
    """
    ou = t.find(nom, defaut)
    return ou if ou >= 0 else defaut


def images_situees(t):
    """Les images nommees, avec leur place dans la phrase."""
    vues = []
    for m in RE_NOM_FICHIER.finditer(t):
        n = _elaguer(m.group(1), garder="fin")
        if len(n) >= 3:
            vues.append((_place(t, n, m.start()), m.end(), n))
    for m in RE_NOM_SANS_EXTENSION.finditer(t):
        n = _elaguer(m.group(1))
        if len(n) < 4:
            continue
        if any(abs(d - m.start()) < 8 for d, _, _ in vues):
            continue
        vues.append((_place(t, n, m.start()), m.end(), n))
    vues.sort()
    sortie, deja = [], []
    for debut, fin, n in vues:
        if any(n in d or d in n for d in deja):
            continue
        deja.append(n)
        sortie.append((debut, fin, n))
    return sortie


def visage_et_cible(t):
    """Quelle image donne le visage, quelle image le recoit.

    Rend un couple, chaque membre pouvant etre vide. Se fier au rang etait
    l erreur : « remplace le visage sur B par celui de A » range la cible en
    premier, « prends le visage sur A et mets-le sur B » la range en second.
    """
    situees = images_situees(t)
    if not situees:
        return "", ""
    roles = []
    precedent = 0
    for debut, fin, nom in situees:
        roles.append((_role(t[precedent:debut], t[:debut]), nom))
        precedent = fin

    reference = next((n for r, n in roles if r == "reference"), "")
    cible = next((n for r, n in roles if r == "cible"), "")
    # Aucun indice : on retombe sur l ordre d apparition, qui reste le cas le
    # plus frequent.
    if not reference and not cible:
        reference = roles[0][1]
        cible = roles[1][1] if len(roles) > 1 else ""
    elif not reference:
        reference = next((n for r, n in roles if n != cible), "")
    elif not cible:
        cible = next((n for r, n in roles if n != reference), "")
    return reference, cible


def images_designees(t):
    """Toutes les images nommees dans la phrase, dans l ordre.

    On rend des designations, pas des chemins : c est a _trouver de les
    resoudre, lui seul sachant ou chercher.
    """
    vues = []
    for m in RE_NOM_FICHIER.finditer(t):
        n = _elaguer(m.group(1), garder="fin")
        if len(n) >= 3:
            vues.append((m.start(), n))
    for m in RE_NOM_SANS_EXTENSION.finditer(t):
        n = _elaguer(m.group(1))
        if len(n) < 4:
            continue
        if any(abs(p - m.start()) < 8 for p, _ in vues):
            continue
        vues.append((m.start(), n))
    vues.sort()
    sortie = []
    for _, n in vues:
        if not any(n in d or d in n for d in sortie):
            sortie.append(n)
    return sortie

RE_DESIGNE_IMAGE = re.compile(
    r"\b(?:l\s*)?(?:image|photo)\s+que\s+tu\s+(?:viens\s+de\s+\w+|as\s+\w+)"
    r"|\bla\s+derniere\s+(?:image|photo|creation|generation)\b"
    r"|\bta\s+derniere\s+(?:image|creation)\b"
    r"|\bma\s+derniere\s+(?:photo|image)\b"
    r"|\bcette\s+(?:image|photo)\b"
    r"|\bl\s*(?:image|photo)\b|\bma\s+photo\b")


# « remplace la tete par une tete de poule » : une zone precise, pas toute
# l image. Le detourage donne un resultat propre la ou la reprise globale
# refaisait le decor et laissait l ancienne tete en transparence.
RE_ZONE = re.compile(
    r"\b(?:remplace|remplacer|mets?|mettre|change|changer|colle|coller)\b"
    r"[^.]{0,45}?\b(tetes?|visages?|figures?|faces?|mains?)\b"
    r"|\b(tetes?|visages?|mains?)\b[^.]{0,25}\ba la place\b")


# « fais-moi une chanson sur ... ». Distinguer une demande de composition
# d une demande de lecture est le point delicat : « mets de la musique » veut
# dire Spotify, « compose une musique » veut dire le moteur.
RE_MUSIQUE = re.compile(
    r"\b(?:compose|composer|invente|inventer|ecris|ecrire|fabrique|fais|fait|"
    r"genere|generer|cree|creer)\b[^.]{0,30}?"
    r"\b(?:chansons?|musiques?|morceaux?|melodies?|airs?|instrumentaux?|"
    r"instrumental|jingles?)\b")

RE_SAIT_MUSIQUE = re.compile(
    r"\b(?:tu sais|sais tu|tu peux|peux tu)\b[^.?]{0,34}"
    r"\b(?:musiques?|chansons?|morceaux?)\b")


# « fais-moi un clip avec la musique » : le montage, pas la composition.
RE_CLIP = re.compile(
    r"\b(?:monte|monter|assemble|assembler|fais|fait|cree|creer|genere)\b"
    r"[^.]{0,30}?\bclips?\b"
    r"|\bclips?\b[^.]{0,30}?\b(?:avec|sur)\b[^.]{0,30}?"
    r"\b(?:musique|morceau|chanson)\b")


# « refais-la » : relancer le meme tirage est souvent la bonne reponse a une
# image ratee, plus efficace que de reformuler la demande.
RE_REFAIRE = re.compile(
    r"\b(?:refais|refaire|recommence|recommencer|relance|relancer|reessaye|"
    r"reessayer)\b[^.]{0,20}?\b(?:la|le|ca|une autre|image|dessin)\b"
    r"|\bune autre (?:version|image|fois)\b|\bencore une\b"
    r"|\bpas terrible\b|\bratee?\b|\bloupee?\b")


# « montre le specimen » : le dessin de veille, mais a la demande.
RE_SPECIMEN = re.compile(
    r"\b(?:montre|affiche|dessine|trace|lance|fais)\b[^.]{0,24}?"
    r"\b(?:specimen|xenomorphe|creature|alien|bestiole|dessin de veille)\b"
    r"|\ble specimen\b|\bimage de veille\b|\becran de veille\b")


# « fais-moi une video de », « anime cette photo ». Le mot « clip » est
# reserve au montage : ici on fabrique une sequence, on ne l assemble pas.
RE_VIDEO = re.compile(
    r"\b(?:fais|fait|fabrique|genere|generer|cree|creer|filme|filmer)\b"
    r"[^.]{0,26}?\b(?:videos?|sequences?|animations?|films?)\b"
    r"|(?<!dessin )\b(?:anime|animer|fais bouger|met en mouvement)\b"
    r"|\bvideos?\s+(?:de|d|du|des|avec)\b")


# « mets-moi en cosmonaute », « mets Paul en chevalier ». Le visage vient
# d une photo de reference, la scene est inventee autour.
RE_PORTRAIT = re.compile(
    r"\b(?:mets?|mettre|met|transforme|deguise|habille|imagine|dessine)\b"
    r"\s*(?:moi|nous|le|la|les)?\s*"
    r"(?:\b[a-z]+\b\s+)?\ben\b\s+(.+)")

# Qui : « mets-moi », ou un prenom juste apres le verbe.
RE_QUI = re.compile(
    r"\b(?:mets?|mettre|met|transforme|deguise|habille)\b\s+(?:moi|nous)\b"
    r"|\b(?:mets?|mettre|met|transforme|deguise|habille)\b\s+([a-z]{3,})\s+en\b")


# « mets le visage de Paul sur cette image » : deux images dans la phrase,
# la premiere donne l identite, la seconde recoit.
# La cible peut etre nommee sans le mot « image » : « ... sur humain-AVANT.png ».
# On n exige donc plus ce mot, et l on verifie ensuite qu une cible existe.
RE_TRANSPOSER = re.compile(
    r"\b(?:visages?|tetes?|tronches?)\b[^.]{0,60}?\b(?:sur|dans)\b"
    r"|\bremplace\w*\s+(?:le\s+)?visage\b"
    r"|\btranspose\w*\b[^.]{0,30}\bvisage\b"
    # « remplace la tete de X par celui de Y » : ni « sur » ni « dans », et le
    # mot est « tete », pas « visage ». C est pourtant la meme demande, et
    # elle partait vers le modele, qui peignait le nom du fichier.
    r"|\b(?:remplace|change|echange)\w*\s+(?:la\s+|le\s+|les\s+|sa\s+|"
    r"son\s+|ma\s+|mon\s+)?(?:tetes?|visages?|figures?)\b[^.]{0,80}?\bpar\b")


def _transposer_visage(t):
    """« prends le visage de Paul et mets-le sur cette image »."""
    if not RE_TRANSPOSER.search(t):
        return None

    # Qui : un nom enregistre cite dans la phrase, sinon la premiere image
    # nommee sert de reference.
    from tools.portrait import visages_connus
    qui = ""
    for nom in visages_connus():
        if re.search(r"\b%s\b" % re.escape(nom), t):
            qui = nom
            break

    reference, sur = visage_et_cible(t)
    if qui:
        # Le nom enregistre a servi : la photo qui le porte ne doit pas etre
        # prise pour la cible.
        if sur and qui in sur:
            sur = reference if reference and qui not in reference else ""
    else:
        if not reference:
            # « remplace la tete par une tete de poule » tombe ici : aucune
            # photo de reference n est nommee, donc ce n est pas une
            # transposition mais une description a peindre. On rend la main
            # au remplacement de zone plutot que de reclamer un nom.
            return None
        qui = reference
    if not sur:
        d = RE_DESIGNE_IMAGE.search(t)
        sur = d.group(0) if d else "la derniere image"

    force = ""
    if re.search(r"\blegerement\b|\bun peu\b", t):
        force = "legere"
    elif re.search(r"\bfortement\b|\bvraiment\b|\bcompletement\b", t):
        force = "forte"

    from tools.portrait import transposer_visage
    return transposer_visage(visage=qui, sur=sur, force=force)

def _portrait(t):
    """« mets-moi en highlander sur une falaise »."""
    if not RE_PORTRAIT.search(t):
        return None
    # « mets la musique », « mets en pause » : ce ne sont pas des portraits.
    if re.search(r"\ben pause\b|\bla musique\b|\ben route\b|\ben marche\b"
                 r"|\ben veille\b|\ben mode\b", t):
        return None

    m = RE_PORTRAIT.search(t)
    scene = " ".join(m.group(1).split()).strip(" ,.")
    if len(scene) < 3:
        return None

    qui = ""
    q = RE_QUI.search(t)
    if q and q.group(1):
        qui = q.group(1)
        # Le prenom ne fait pas partie de la scene.
        scene = re.sub(r"^%s\s+" % re.escape(qui), "", scene)

    forte = bool(re.search(r"\bressemblance forte\b|\bplus ressemblant\b"
                           r"|\bvraiment moi\b", t))
    format_voulu = ""
    if re.search(r"\bpaysage\b|\bhorizontal\b", t):
        format_voulu = "paysage"
    elif re.search(r"\bcarree?\b", t):
        format_voulu = "carre"

    # Les mots de reglage ont servi : ils n ont rien a faire dans la scene,
    # sinon le moteur dessinerait un pompier « ressemblance forte ».
    scene = re.sub(r"\bressemblance forte\b|\bplus ressemblant\b"
                   r"|\bvraiment moi\b|\ben paysage\b|\ben carree?\b"
                   r"|\bhorizontal\b|\bvertical\b", " ", scene)
    scene = " ".join(scene.split()).strip(" ,.")
    if len(scene) < 3:
        return None

    from tools.portrait import portrait_dans_scene
    return portrait_dans_scene(scene=scene, qui=qui, format=format_voulu,
                               ressemblance_forte=forte)


def _video(t):
    """« fais-moi une video de trois secondes d un chat, en portrait »."""
    if not RE_VIDEO.search(t):
        return None

    # Duree : « de cinq secondes », ou en toutes lettres pour les petits
    # nombres, que la reconnaissance vocale ecrit souvent ainsi.
    duree = 5
    m = re.search(r"(\d{1,2})\s*(?:secondes?|s)\b", t)
    if m:
        duree = int(m.group(1))
    else:
        mots = {"deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6,
                "sept": 7, "huit": 8, "neuf": 9, "dix": 10,
                "quinze": 15, "vingt": 20, "trente": 30, "quarante": 40,
                "cinquante": 50, "soixante": 60}
        m = re.search(r"\b(%s)\s+secondes?\b" % "|".join(mots), t)
        if m:
            duree = mots[m.group(1)]

    format_voulu = ""
    if re.search(r"\bportrait\b|\bvertical\b|\bdebout\b", t):
        format_voulu = "portrait"
    elif re.search(r"\bcarree?\b", t):
        format_voulu = "carre"
    elif re.search(r"\bpaysage\b|\bhorizontal\b", t):
        format_voulu = "paysage"

    # Animer une image existante plutot que de partir de rien. Un nom de
    # fichier l emporte sur une designation vague : il est plus precis.
    image = ""
    designees = images_designees(t)
    image = designees[0] if designees else ""
    m_nom = None if designees else RE_NOM_FICHIER.search(t)
    if m_nom:
        # L expression remonte gloutonnement les mots qui precedent le nom :
        # « anime ma photo portrait-3.jpg » capturait le verbe avec. On rogne
        # ce qui n appartient pas au nom du fichier.
        image = re.sub(r"^(?:anime|animer|prends?|prend|photos?|images?|"
                       r"fichiers?|ma|mon|mes|la|le|les|de|du|des|d|"
                       r"partir|depuis|avec|sur)\s+", " ",
                       m_nom.group(1), flags=re.I)
        while True:
            court = re.sub(r"^(?:anime|animer|prends?|prend|photos?|images?|"
                           r"fichiers?|ma|mon|mes|la|le|les|de|du|des|d|"
                           r"partir|depuis|avec|sur)\s+", " ", image.strip(),
                           flags=re.I)
            if court.strip() == image.strip():
                break
            image = court
        image = image.strip()
    if not image:
        d = RE_DESIGNE_IMAGE.search(t)
        if d:
            image = d.group(0)

    ecran = _premier_ecran() if _contient(t, ECRANS) else ""

    # Ce qui reste apres le verbe et les reglages decrit la scene.
    sujet = re.split(r"\b(?:videos?|sequences?|animations?|films?)\b", t, 1)
    sujet = sujet[-1] if len(sujet) > 1 else t
    sujet = re.sub(r"\b(?:anime|animer|fais bouger|met en mouvement)\b",
                   " ", sujet)
    sujet = RE_NOM_FICHIER.sub(" ", sujet)
    sujet = RE_DESIGNE_IMAGE.sub(" ", sujet)
    sujet = re.sub(r"\ba partir de\b|\bdepuis\b|\bavec ma\b", " ", sujet)
    # La duree a servi : elle ne doit plus figurer dans la scene, en chiffres
    # comme en toutes lettres, ni le « pendant » qui l introduisait.
    sujet = re.sub(r"\b(?:pendant|durant|de|d)?\s*\d{1,2}\s*"
                   r"(?:secondes?|s)\b", " ", sujet)
    sujet = re.sub(r"\b(?:pendant|durant|de|d)?\s*(?:deux|trois|quatre|cinq|"
                   r"six|sept|huit|neuf|dix|quinze|vingt|trente|"
                   r"quarante|cinquante|soixante)\s+secondes?\b", " ", sujet)
    sujet = re.sub(r"^\s*(?:pendant|durant)\b\s*", " ", sujet.strip())
    sujet = re.sub(r"\b(?:en\s+)?(?:portrait|paysage|carree?|vertical|"
                   r"horizontal)\b", " ", sujet)
    if ecran:
        sujet = re.sub(r"\bsur\s+(?:la|le|l|mon|ma)?\s*[^,]+$", " ", sujet)
    sujet = re.sub(r"^\s*(?:moi|de|d|du|des|une?|le|la|les|avec|qui)\b\s*",
                   " ", sujet.strip())
    sujet = " ".join(sujet.split()).strip(" ,.")

    if len(sujet) < 3 and not image:
        return None
    if len(sujet) < 3:
        # On anime la photo sans autre consigne : un mouvement discret.
        sujet = "subtle natural motion, slow camera push in"

    from tools.video import generer_video
    return generer_video(description=sujet, image=image, duree=duree,
                         format=format_voulu, ecran=ecran)


def _specimen(t):
    """« montre-moi le specimen », « affiche l image de veille »."""
    if not RE_SPECIMEN.search(t):
        return None
    # « fais-moi une image d un alien » reste une demande de generation. Mais
    # « affiche l image de veille » n en est pas une, bien qu elle contienne
    # le mot image : le mot « veille » ou « specimen » tranche.
    explicite = re.search(r"\bspecimen\b|\bxenomorphe\b|\bveille\b", t)
    if RE_IMAGE.search(t) and not explicite:
        return None
    duree = 24
    m = re.search(r"(\d{1,3})\s*secondes?", t)
    if m:
        duree = max(6, min(int(m.group(1)), 120))
    try:
        import hud
        hud.publier_specimen(duree)
    except Exception:
        return "L interface ne repond pas."
    return "Specimen affiche."


def _refaire_image(t):
    """« refais-la », « une autre version », « genere-en une nouvelle »."""
    if not (RE_REFAIRE.search(t) or RE_PAS_REPRENDRE.search(t)):
        return None
    # « fais-moi une image de X » reste une creation neuve, pas une relance.
    if RE_IMAGE.search(t):
        return None
    # On ne relance que si une image a bien ete faite juste avant.
    from tools.image import _DERNIERE, refaire_image
    if not _DERNIERE.get("demande"):
        return None
    ecran = _premier_ecran() if _contient(t, ECRANS) else ""
    return refaire_image(ecran=ecran)


# Ce qui suit « clip avec mes ... » sans nommer un sujet : les mots de
# quantite, et les noms des supports eux-memes.
_HORS_THEME = {"derniere", "dernier", "dernieres", "derniers", "musique",
               "morceau", "chanson", "recentes", "recents", "generees",
               "generes", "toutes", "quelques", "plusieurs", "videos",
               "video", "images", "image", "photos", "photo", "sequences",
               "sequence", "clips", "films"}


def _clip(t):
    """« monte un clip avec la derniere musique et mes images »."""
    if not RE_CLIP.search(t):
        return None
    sources = ""
    if re.search(r"\b(?:images?|photos?)\b", t):
        sources = "images"
    elif re.search(r"\bvideos?\b|\bsequences?\b", t):
        sources = "videos"

    combien = 4
    m = re.search(r"(\d{1,2})\s*(?:images?|photos?|videos?|sequences?)", t)
    if m:
        combien = int(m.group(1))

    ecran = ""
    if _contient(t, ECRANS):
        ecran = _premier_ecran()

    # « un clip avec mes videos de xenomorphe » : le mot qui suit designe la
    # serie voulue. Les fichiers portent leur demande d origine dans leur nom,
    # donc chercher ce mot dedans suffit a retrouver la bonne matiere.
    theme = ""
    motif = re.compile(r"\b(?:images?|photos?|videos?|sequences?|clips?)\s+"
                       r"(?:de\s+|du\s+|des\s+|sur\s+|avec\s+)?"
                       r"(?:la\s+|le\s+|les\s+|un\s+|une\s+|mes\s+|"
                       r"mon\s+|ma\s+)?([a-z]{5,})")
    ou = 0
    while True:
        m = motif.search(t, ou)
        if not m:
            break
        if m.group(1) not in _HORS_THEME:
            theme = m.group(1)
            break
        # « clip avec mes videos de xenomorphe » : le mot ecarte est lui-meme
        # un support. On repart de lui, pas apres lui, sinon le sujet qui le
        # suit n est jamais examine.
        ou = m.start(1)

    from tools.clip import monter_clip
    return monter_clip(sources=sources, combien=combien, theme=theme,
                       ecran=ecran)


def _musique(t):
    """« compose une chanson douce a la guitare sur l automne »."""
    if RE_SAIT_MUSIQUE.search(t):
        return ("Oui. Je compose des morceaux en local, avec ou sans paroles. "
                "Dis-moi le style : « compose une chanson douce a la guitare ».")
    if not RE_MUSIQUE.search(t):
        return None

    # Ce qui suit le mot « chanson » (ou son equivalent) est le style voulu.
    coupe = re.split(r"\b(?:chansons?|musiques?|morceaux?|melodies?|airs?|"
                     r"instrumentaux?|instrumental|jingles?)\b", t, maxsplit=1)
    style = coupe[1] if len(coupe) > 1 else ""
    style = re.sub(r"^\s*(?:de|d|du|des|sur|avec|dans|en|qui|pour|le|la|les|"
                   r"un|une)\b\s*", " ", style.strip())

    duree = 60
    m = re.search(r"(\d{1,3})\s*(?:secondes?|s)\b", t)
    if m:
        duree = int(m.group(1))
    else:
        m = re.search(r"(\d{1,2})\s*(?:minutes?|min)\b", t)
        if m:
            duree = int(m.group(1)) * 60

    ecran = ""
    if _contient(t, ECRANS):
        ecran = _premier_ecran()
        style = re.sub(r"\bsur\s+(?:la|le|l|mon|ma)?\s*[^,]+$", " ", style)

    style = " ".join(style.split()).strip(" ,.")
    if len(style) < 3:
        style = "pleasant instrumental music"

    from tools.musique import generer_musique
    return generer_musique(style=style, duree=duree, ecran=ecran)


def _zone_demandee(t):
    """Ce que la phrase demande de remplacer, ou None.

    Extrait a part pour que la generation puisse l enchainer : creer l image,
    puis y remplacer la zone.
    """
    if not RE_ZONE.search(t):
        return None
    # Le garde-fou demandait qu un support soit nomme. Mais « remplace la tete
    # par une tete de poule » ne nomme rien et ne veut pourtant rien dire
    # d autre que : sur la derniere image. La construction elle-meme suffit a
    # lever le doute — un verbe de remplacement, une partie du corps, un
    # complement en « par ».
    explicite = re.search(r"\b(?:remplace|change|echange|mets?)\w*\s+"
                          r"(?:la\s+|le\s+|les\s+|sa\s+|son\s+|ses\s+|"
                          r"mes\s+)?(?:tetes?|visages?|figures?|mains?)\b"
                          # « tete de lit », « tete d affiche » : la tete y est
                          # une figure de style, pas une partie du corps.
                          r"(?!\s+(?:de\s+lit|de\s+pont|d\s+affiche|"
                          r"de\s+serie|de\s+liste))"
                          r"[^.]{0,40}?\bpar\b", t)
    if not explicite and not re.search(
            r"\b(?:image|photo|dessin|illustration|visuel|dame|"
            r"femme|homme|personnage|elle|lui)\b", t):
        return None

    zone = "tete"
    if re.search(r"\bmains?\b", t):
        zone = "mains"
    elif re.search(r"\bvisages?|figures?\b", t):
        zone = "visage"

    m = re.search(r"\bpar\s+(.+?)(?:\s+a la place|$)", t)
    if not m:
        m = re.search(r"\b(?:mets?|mettre|colle)\s+(.+?)\s+a la place", t)
    if not m:
        return None
    par = " ".join(m.group(1).split()).strip(" ,.")
    par = RE_DESIGNE_IMAGE.sub(" ", par)
    par = re.sub(r"\b(?:sur|dans|de)\s*$", " ", par.strip())
    par = re.sub(r"^(?:une?|le|la|les|des|du|de)\s+", "", par.strip())
    # Les consignes de style appartiennent a la creation, pas au decoupage.
    par = re.sub(r"\ble tout en\b.*|\bfais attention\b.*|\ben photo"
                 r"[- ]?realis\w*\b", " ", par)
    par = " ".join(par.split()).strip(" ,.")
    if len(par) < 3:
        return None
    return {"par": par, "zone": zone}


def _remplacer_zone(t):
    """« remplace la tete de la dame par une tete de poule »."""
    # Si la phrase demande de CREER une image, c est la creation qui mene ;
    # elle enchainera le remplacement elle-meme. Sans cette porte, une
    # demande de creation retombait indefiniment sur la derniere image.
    if RE_IMAGE.search(t) and not RE_DESIGNE_IMAGE.search(t):
        return None
    if re.search(r"\bnouvelle\b|\bdepuis le (?:prompt|texte)\b|"
                 r"\bne repren\w+\b|\barrete de repren\w+\b", t):
        return None

    demande = _zone_demandee(t)
    if demande is None:
        return None

    image = ""
    d = RE_DESIGNE_IMAGE.search(t)
    if d:
        image = d.group(0)

    par_mail = bool(RE_PAR_MAIL.search(t))
    from tools.zone import remplacer_zone
    return remplacer_zone(par=demande["par"], zone=demande["zone"],
                          image=image, par_mail=par_mail)


def _modifier_image(t):
    """« transforme ma derniere photo en dessin anime ».

    On separe ce qui designe l image de ce qu elle doit devenir : le mot
    « en » marque presque toujours la frontiere entre les deux.
    """
    if not RE_MODIF_IMAGE.search(t):
        return None
    # Meme regle que pour le remplacement de zone : creer l emporte sur
    # reprendre. « Fais-moi une photo et transforme-la en... » doit fabriquer.
    if RE_IMAGE.search(t) and not RE_DESIGNE_IMAGE.search(t):
        return None
    if RE_PAS_REPRENDRE.search(t):
        return None

    par_mail = bool(RE_PAR_MAIL.search(t))
    if par_mail:
        t = _sans_mention_mail(t)

    m = re.search(r"\b(?:modifie?|modifier|transforme|transformer|retouche|"
                  r"retoucher|refais|refaire|reprends?|rempr?ends?|reprendre|"
                  r"change|changer|remplace|remplacer)\b\s*(?:moi\s+)?(.+)", t)
    if not m:
        # « enleve la tete de l image et mets... » : le verbe porte sur le
        # contenu, pas sur l image. Toute la phrase est alors la consigne.
        m = re.search(r"\b(?:enleve|enlever|ajoute|ajouter)\b.*", t)
        if not m:
            return None
        reste = m.group(0)
    else:
        reste = m.group(1)

    # On cherche d abord comment l image est designee. C est plus sur que de
    # decouper sur « en », qui manque des que la consigne n en contient pas.
    # Un nom de fichier precis l emporte sur toute designation vague.
    nommees = images_designees(t)
    d = RE_DESIGNE_IMAGE.search(reste)
    if nommees:
        quelle = nommees[0]
        voulu = reste
        for nom in nommees:
            voulu = voulu.replace(nom, " ")
        voulu = RE_DESIGNE_IMAGE.sub(" ", voulu)
        voulu = re.sub(r"\ben\s+", " ", voulu, count=1)
    elif d:
        quelle = d.group(0)
        voulu = (reste[:d.start()] + " " + reste[d.end():])
        voulu = re.sub(r"^\s*(?:et|,)\s*", " ", voulu)
    else:
        coupe = re.split(r"\ben\s+", reste, maxsplit=1)
        quelle = coupe[0]
        voulu = coupe[1] if len(coupe) > 1 else ""

    # Les adverbes d intensite servent a la force, pas a decrire l image.
    intensite = r"\b(?:legerement|leger|un peu|completement|fortement|" \
                r"beaucoup|vraiment)\b"
    quelle = re.sub(intensite, " ", quelle)
    voulu = re.sub(intensite, " ", voulu)
    # Le « en » de « en aquarelle » a servi de separateur : il ne fait plus
    # partie de la description. Et retirer la designation laisse parfois une
    # preposition orpheline.
    voulu = re.sub(r"^\s*(?:en|vers|dans|comme)\s+", " ", voulu)
    voulu = re.sub(r"\s+(?:de|du|des|d)\s+(?=et\b|$)", " ", voulu)
    voulu = re.sub(r"\s+(?:de|du|des|d)\s*$", " ", voulu)
    quelle = " ".join(quelle.split()).strip(" ,.")
    voulu = " ".join(voulu.split()).strip(" ,.")

    force = ""
    if re.search(r"\blegerement\b|\bun peu\b|\bleger\b", t):
        force = "legere"
    elif re.search(r"\bcompletement\b|\bfortement\b|\bbeaucoup\b", t):
        force = "forte"

    ecran = ""
    if _contient(t, ECRANS):
        ecran = _premier_ecran()
        voulu = re.sub(r"\bsur\s+(?:la|le|l|mon|ma)?\s*[^,]+$", "", voulu)

    voulu = " ".join(voulu.split()).strip(" ,.")
    if len(voulu) < 3:
        return None

    from tools.modifier_image import modifier_image
    return modifier_image(description=voulu, image=quelle, force=force,
                          ecran=ecran, par_mail=par_mail)


# « envoie-la moi par mail » : le pronom remplace le nom, et aucun raccourci
# ne reconnaissait la phrase. Le modele choisissait alors un outil au hasard.
RE_ENVOI_PRONOM = re.compile(
    r"\benvoie?[- ]?(?:moi|la|le|les)\b|\benvoie?[- ]?(?:moi)\s+(?:la|le)\b")


def _image_mail(t):
    """« envoie-moi la derniere image par mail ».

    Place apres les raccourcis de generation : une demande qui fabrique ET
    envoie doit d abord fabriquer.
    """
    if not RE_PAR_MAIL.search(t) or "@" in t:
        return None
    if not re.search(r"\benvoi", t):
        return None
    # Soit le nom est dit, soit un pronom y renvoie : « envoie-la moi ».
    if not (re.search(r"\b(?:image|photo|dessin|illustration|visuel)\b", t)
            or RE_ENVOI_PRONOM.search(t)):
        return None
    from tools.image import _DERNIERE, envoyer_derniere_a_soi
    if not _DERNIERE.get("chemin"):
        return None
    return envoyer_derniere_a_soi()



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
ETAPES = (_mode, _extinction, _memoire, _arret_spotify, _cast,
          _spotify_appareil, _portrait, _clip, _video, _musique,
          _spotify, _youtube, _diffuser_service, _chaine_tv, _streaming,
          _plex, _plex_sans_ecran, _musique_sans_source, _ecran_lecture,
          _specimen, _refaire_image, _transposer_visage, _remplacer_zone,
          _modifier_image, _media, _courrier, _application, _film,
          _heure, _meteo, _minuteur, _stats, _capture, _web, _image,
          _image_mail)



def _tracer(quoi, detail=""):
    """Une ligne dans le journal, sans jamais faire echouer la demande."""
    try:
        # Passer par le journal maison : le logger n a de destination que si
        # obtenir() l a configure, et un logger muet ne se remarque pas.
        from core.journal import obtenir
        obtenir().info("raccourci %s%s", quoi,
                       (" : " + detail) if detail else "")
    except Exception:
        pass


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
        except LookupError as absente:
            # Un nom d image donne mais introuvable : on le dit, plutot que
            # de laisser le modele improviser sur autre chose.
            return _au_ton_mere(
                "Je ne trouve pas d image nommee « %s ». "
                "Verifie le nom, ou dis-moi dans quel dossier elle est."
                % str(absente))
        except Exception as panne:
            # Un raccourci qui casse ne doit jamais bloquer Jarvis : on laisse
            # le modele prendre le relais. Mais on le dit — sans cette trace,
            # une panne ici ressemble a une phrase mal comprise, et l on
            # cherche des heures du mauvais cote.
            _tracer("panne dans %s" % getattr(etape, "__name__", "?"),
                    "%s: %s" % (type(panne).__name__, str(panne)[:120]))
            continue
        if reponse:
            _tracer(getattr(etape, "__name__", "?"), t[:160])
            return _au_ton_mere(reponse)
    # Aucun raccourci : la demande part vers le modele, qui devra deviner les
    # arguments. C est la que naissent les « ecran = vacances 21-04-07 058.jpg ».
    _tracer("aucun, le modele prend la main", t[:160])
    return None
