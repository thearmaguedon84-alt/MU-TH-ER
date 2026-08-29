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
          _spotify_appareil,
          _spotify, _youtube, _diffuser_service, _chaine_tv,
          _streaming, _plex, _plex_sans_ecran, _musique_sans_source,
          _ecran_lecture, _media, _courrier, _application, _film,
          _heure, _meteo, _minuteur, _stats, _capture, _web)


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
