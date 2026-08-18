"""Pilotage du serveur Plex et diffusion sur les ecrans Chromecast.

Principe retenu : Plex sert a TROUVER le media, le Chromecast le lit
directement depuis le serveur. On ne depend donc pas de l'application Plex
installee sur la television — n'importe quel Chromecast fait l'affaire.

Le jeton d'acces est lu dans la base de registre, la ou Plex l'ecrit sur
Windows (HKCU\\Software\\Plex, Inc.\\Plex Media Server). Rien a configurer tant
que le serveur tourne sur cette machine.
"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
import xml.etree.ElementTree as ET

from core.config import reglage
from core.registre import outil
from core.util import sans_accents

_JETON = None
PORT = 32400

# Extensions que les Chromecast lisent nativement, sans transcodage
CONTENEURS_OK = {"mp4", "m4v", "webm", "mkv"}


# ------------------------------------------------------------------ acces

def _jeton():
    """Jeton Plex, lu une fois dans le registre Windows."""
    global _JETON
    if _JETON is not None:
        return _JETON
    depuis_config = reglage("plex.jeton", "")
    if depuis_config:
        _JETON = depuis_config
        return _JETON
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Plex, Inc.\Plex Media Server")
        _JETON = winreg.QueryValueEx(k, "PlexOnlineToken")[0]
    except Exception:
        _JETON = ""
    return _JETON


def _hote():
    return reglage("plex.hote", "127.0.0.1")


def _get(chemin, params=None):
    """Appel XML au serveur Plex. Renvoie l'element racine ou None."""
    jeton = _jeton()
    if not jeton:
        return None
    p = dict(params or {})
    p["X-Plex-Token"] = jeton
    url = f"http://{_hote()}:{PORT}{chemin}?" + urllib.parse.urlencode(p)
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/xml"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return ET.fromstring(r.read())
    except Exception:
        return None


_DERNIER_DEMARRAGE = 0.0


def _demarrer_serveur():
    """Lance Plex Media Server et attend qu'il reponde.

    Le serveur ne demarre pas avec Windows et s'arrete quand on ferme sa
    fenetre : sans ca, chaque demande echouait tant qu'on ne l'avait pas
    relance a la main.
    """
    global _DERNIER_DEMARRAGE
    import os
    import subprocess

    # Ne pas s'acharner : une tentative toutes les 60 secondes au plus
    if time.time() - _DERNIER_DEMARRAGE < 60:
        return False
    _DERNIER_DEMARRAGE = time.time()

    exe = reglage("plex.executable",
                  r"C:\Program Files\Plex\Plex Media Server\Plex Media Server.exe")
    if not os.path.exists(exe):
        return False
    try:
        subprocess.Popen([exe], cwd=os.path.dirname(exe))
    except Exception:
        return False

    for _ in range(12):                 # jusqu'a ~18 s
        time.sleep(1.5)
        if _get("/identity") is not None:
            return True
    return False


def disponible(demarrer=True):
    """Vrai si le serveur repond, en le lancant au besoin."""
    if _get("/identity") is not None:
        return True
    if not demarrer:
        return False
    return _demarrer_serveur()


# ------------------------------------------------------------------ recherche

def _normaliser(s):
    return sans_accents(str(s or "").lower()).strip()


# Mots vides : ils ne servent a rien pour retrouver un titre
VIDES = {"le", "la", "les", "un", "une", "des", "du", "de", "et", "a", "au",
         "aux", "the", "and", "of", "in", "on", "sur", "dans", "film", "video"}


def _brut(requete, limite=12):
    """Un appel de recherche Plex, sans traitement."""
    racine = _get("/search", {"query": requete, "limit": limite})
    if racine is None:
        return []
    return [n for n in racine
            if n.tag in ("Video", "Directory")
            and n.get("type") in ("movie", "episode", "show")]


# ------------------------------------------------------------------- index
# Plex ne pardonne aucune faute de frappe. On conserve la liste des titres pour
# pouvoir retrouver « Les Minions » a partir de « mignon ».

_TITRES = None
_MOTS = None
_CACHE_TITRES = Path(__file__).resolve().parent.parent / ".cache_plex.json"
_AGE_MAX = 24 * 3600


def _construire_index():
    """[(ratingKey, titre, annee, type)] pour toute la bibliotheque."""
    entrees = []
    sections = _get("/library/sections")
    if sections is None:
        return entrees
    for d in sections:
        if d.get("type") not in ("movie", "show"):
            continue
        contenu = _get(f"/library/sections/{d.get('key')}/all")
        if contenu is None:
            continue
        for n in contenu:
            rk, t = n.get("ratingKey"), n.get("title")
            if rk and t:
                entrees.append([rk, t, n.get("year") or "",
                                n.get("type") or "movie"])
    return entrees


def index_titres(forcer=False):
    """Titres de la bibliotheque, construits une fois puis reutilises."""
    global _TITRES
    if _TITRES is not None and not forcer:
        return _TITRES

    if not forcer and _CACHE_TITRES.exists():
        try:
            if time.time() - _CACHE_TITRES.stat().st_mtime < _AGE_MAX:
                _TITRES = json.loads(_CACHE_TITRES.read_text(encoding="utf-8"))
                return _TITRES
        except Exception:
            pass

    _TITRES = _construire_index()
    try:
        _CACHE_TITRES.write_text(json.dumps(_TITRES, ensure_ascii=False),
                                 encoding="utf-8")
    except Exception:
        pass
    return _TITRES


def _mots_index():
    """Dictionnaire mot -> identifiants, construit une fois."""
    global _MOTS
    if _MOTS is not None:
        return _MOTS
    _MOTS = {}
    for rk, t, annee, genre in index_titres():
        for m in _normaliser(t).split():
            if len(m) >= 4 and m not in VIDES:
                _MOTS.setdefault(m, []).append(rk)
    return _MOTS


def _proches_dans_index(titre, seuil=0.62, combien=6):
    """Titres ressemblant a `titre`, du plus proche au moins proche.

    On s appuie sur get_close_matches, qui elimine tres vite les candidats
    sans rapport : une comparaison exhaustive sur 4600 titres prenait une
    quinzaine de secondes, ici c est immediat.
    """
    from difflib import get_close_matches

    cible = _normaliser(titre)
    if not cible:
        return []

    entrees = index_titres()
    normalises = {}
    for rk, t, annee, genre in entrees:
        normalises.setdefault(_normaliser(t), rk)

    trouves = []

    # 1. Ressemblance sur le titre entier
    for t in get_close_matches(cible, list(normalises), n=combien, cutoff=seuil):
        trouves.append(normalises[t])

    # 2. Ressemblance mot a mot : « mignon » rattrape « minions » la ou la
    #    comparaison globale echoue sur un titre long.
    mots_cible = [m for m in cible.split() if len(m) >= 4 and m not in VIDES]
    if len(trouves) < combien and mots_cible:
        table = _mots_index()
        vocabulaire = list(table)
        for mc in mots_cible[:3]:
            for mot in get_close_matches(mc, vocabulaire, n=3, cutoff=0.7):
                for rk in table[mot][:4]:
                    if rk not in trouves:
                        trouves.append(rk)
            if len(trouves) >= combien:
                break

    return trouves[:combien]


def _fiches(cles):
    """Recupere les elements complets a partir de leurs identifiants."""
    out = []
    for rk in cles:
        fiche = _get(f"/library/metadata/{rk}")
        if fiche is None:
            continue
        for n in fiche:
            if n.tag in ("Video", "Directory"):
                out.append(n)
                break
    return out


def _chercher(titre, limite=12):
    """Cherche un film ou un episode.

    La recherche de Plex est litterale : « mignon » ne trouve pas
    « Les Minions ». Comme la reconnaissance vocale deforme reguliement les
    titres, on retente mot par mot quand la requete complete ne donne rien,
    puis on laisse le classement par ressemblance faire le tri.
    """
    resultats = _brut(titre, limite)
    if resultats:
        return resultats

    mots = [m for m in _normaliser(titre).split()
            if len(m) >= 4 and m not in VIDES]
    vus, groupes = set(), []
    for mot in mots[:4]:
        for n in _brut(mot, limite):
            cle = n.get("ratingKey")
            if cle and cle not in vus:
                vus.add(cle)
                groupes.append(n)

    # Toujours rien : l index local, seul capable de rattraper une faute
    # (« mignon » -> « Les Minions »), ce que la recherche Plex ne fait pas.
    if not groupes:
        groupes = _fiches(_proches_dans_index(titre))
    return groupes


def _meilleur(titre, candidats):
    """Classe les resultats : correspondance exacte d'abord, puis proximite."""
    from difflib import SequenceMatcher
    cible = _normaliser(titre)
    note = []
    for n in candidats:
        t = _normaliser(n.get("title"))
        if not t:
            continue
        if t == cible:
            r = 1.0
        elif cible in t or t in cible:
            r = 0.9
        else:
            r = SequenceMatcher(None, cible, t).ratio()
        # Un film est plus souvent demande qu'une serie entiere
        if n.get("type") == "movie":
            r += 0.05
        note.append((r, n))
    if not note:
        return None
    note.sort(key=lambda x: x[0], reverse=True)
    return note[0][1] if note[0][0] >= 0.5 else None


def _classer(titre, candidats):
    """Tous les resultats pertinents, du plus probable au moins probable."""
    from difflib import SequenceMatcher
    cible = _normaliser(titre)
    note = []
    for n in candidats:
        t = _normaliser(n.get("title"))
        if not t:
            continue
        if t == cible:
            r = 1.0
        elif cible in t or t in cible:
            r = 0.9
        else:
            r = SequenceMatcher(None, cible, t).ratio()
        if n.get("type") == "movie":
            r += 0.05
        if r >= 0.5:
            note.append((r, n))
    note.sort(key=lambda x: x[0], reverse=True)
    return [n for _, n in note]


def _premier_episode(cle_serie):
    """Pour une serie, renvoie le premier episode disponible."""
    saisons = _get(cle_serie)
    if saisons is None:
        return None
    for saison in saisons:
        if saison.get("type") != "season":
            continue
        episodes = _get(saison.get("key"))
        if episodes is None:
            continue
        for ep in episodes:
            if ep.tag == "Video":
                return ep
    return None


def _flux(video, hote_cast=None):
    """URL lisible par un Chromecast, et type de contenu.

    Un meme film peut exister en plusieurs exemplaires, parfois sur un disque
    debranche : Plex garde la fiche mais le fichier renvoie 404. On retient
    donc la premiere version dont le fichier est reellement present, en
    privilegiant les conteneurs que le Chromecast lit sans transcodage.
    """
    import os
    candidats = []
    for media in video:
        if media.tag != "Media":
            continue
        conteneur = (media.get("container") or "").lower()
        for p in media:
            if p.tag == "Part" and p.get("key"):
                fichier = p.get("file") or ""
                accessible = bool(fichier) and os.path.exists(fichier)
                # Trie : fichier present d abord, puis conteneur bien supporte
                rang = (0 if accessible else 1,
                        0 if conteneur in CONTENEURS_OK else 1)
                candidats.append((rang, p, conteneur))

    if not candidats:
        return None, None
    candidats.sort(key=lambda x: x[0])
    rang, partie, _ = candidats[0]
    if rang[0] == 1:
        # Aucune version accessible : le disque est probablement debranche
        return None, "hors ligne"

    # L'adresse annoncee doit etre celle que CE Chromecast peut joindre.
    # Sans son adresse, on retombait sur l interface du VPN, injoignable
    # depuis le reseau domestique.
    hote = reglage("plex.hote_reseau", "")
    if not hote:
        from tools.cast import _adresse_pour
        hote = _adresse_pour(hote_cast or "192.168.1.1")

    url = (f"http://{hote}:{PORT}{partie.get('key')}"
           f"?X-Plex-Token={_jeton()}")
    conteneur = (partie.get("container") or "mp4").lower()
    type_contenu = {"mkv": "video/x-matroska", "mp4": "video/mp4",
                    "m4v": "video/mp4", "avi": "video/x-msvideo",
                    "webm": "video/webm"}.get(conteneur, "video/mp4")
    return url, type_contenu


# ------------------------------------------------------------------ outils

@outil(
    nom="plex_chercher",
    description="Cherche un film ou une serie dans la bibliotheque Plex et dit "
                "ce qui a ete trouve, sans rien lancer. Pour 'est-ce que j ai "
                "tel film', 'cherche tel titre dans Plex'.",
    parametres={
        "type": "object",
        "properties": {
            "titre": {"type": "string", "description": "Titre a chercher."},
        },
        "required": ["titre"],
    },
    lent=True,
    phrase_attente="Je cherche dans Plex.",
)
def plex_chercher(titre: str) -> str:
    if not disponible():
        return "Le serveur Plex ne demarre pas. Lance-le a la main."
    res = _chercher(titre)
    if not res:
        return f"Rien trouve pour {titre} dans Plex."
    noms = []
    for n in res[:5]:
        t = n.get("title")
        annee = n.get("year")
        genre = {"movie": "film", "show": "serie", "episode": "episode"}.get(
            n.get("type"), "")
        noms.append(f"{t}{f' ({annee})' if annee else ''}" + (f", {genre}" if genre else ""))
    if len(noms) == 1:
        return f"J ai trouve {noms[0]}."
    return "J ai trouve : " + " ; ".join(noms) + "."


def _fichier(element):
    """Chemin disque de la premiere version accessible, ou None."""
    import os
    for media in element:
        if media.tag != "Media":
            continue
        for part in media:
            if part.tag == "Part" and part.get("file"):
                if os.path.exists(part.get("file")):
                    return part.get("file")
    return None


def _jouer_local(chemins, titre):
    """Ouvre les fichiers dans VLC, sur cet ordinateur."""
    import subprocess
    from tools.media import _vlc_chemin

    chemins = [c for c in chemins if c]
    if not chemins:
        return None
    try:
        subprocess.Popen([_vlc_chemin(), *chemins])
    except Exception:
        try:
            import os
            os.startfile(chemins[0])
        except Exception:
            return None
    suite = f", {len(chemins)} morceaux" if len(chemins) > 1 else ""
    return f"{titre} sur le PC{suite}."


@outil(
    nom="plex_jouer",
    description=(
        "Cherche un film ou une serie dans Plex et le lance sur un ecran "
        "Chromecast. Pour 'mets tel film sur la tele', 'lance telle serie sur "
        "le videoprojecteur', 'joue tel titre depuis Plex'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "titre": {"type": "string", "description": "Titre du film ou de la serie."},
            "ecran": {"type": "string",
                      "description": "Nom de l ecran Chromecast. Vide = le premier trouve."},
        },
        "required": ["titre"],
    },
    lent=True,
    phrase_attente="Je cherche et je prepare la diffusion.",
)
def plex_jouer(titre: str, ecran: str = "") -> str:
    if not disponible():
        return "Le serveur Plex ne demarre pas. Lance-le a la main."

    candidats = _classer(titre, _chercher(titre))
    if not candidats:
        return f"Je n ai pas trouve {titre} dans Plex."

    # On essaie les resultats dans l ordre : le premier dont le fichier est
    # reellement accessible l emporte. Un meme titre existe souvent en
    # plusieurs exemplaires, dont certains sur un disque debranche.
    # Sans ecran nomme, on reste sur le PC : allumer la television parce que
    # personne n a precise de destination est le contraire de ce qu on veut.
    appareil = None
    hote_cast = None
    if ecran:
        from tools.cast import _choisir
        appareil = _choisir(ecran)
        if appareil is None:
            return "Je ne vois pas cet ecran."
        hote_cast = appareil.cast_info.host

    url = type_contenu = nom_affiche = None
    hors_ligne = None
    for choix in candidats[:6]:
        if choix.get("type") == "show":
            ep = _premier_episode(choix.get("key"))
            if ep is None:
                continue
            nom = f"{choix.get('title')}, {ep.get('title')}"
            cible = ep
        else:
            annee = choix.get("year")
            nom = choix.get("title") + (f" ({annee})" if annee else "")
            cible = choix

        if appareil is None:
            # Lecture locale : le chemin disque suffit
            f = _fichier(cible)
            if f:
                resultat = _jouer_local([f], nom)
                if resultat:
                    return resultat
            continue

        u, ct = _flux(cible, hote_cast)
        if u:
            url, type_contenu, nom_affiche = u, ct, nom
            break
        if ct == "hors ligne" and hors_ligne is None:
            hors_ligne = nom

    if not url:
        if hors_ligne:
            return f"{hors_ligne} est dans Plex mais son disque n est pas connecte."
        return f"Je ne trouve pas de fichier lisible pour {titre}."

    try:
        appareil.wait(timeout=12)
        lecteur = appareil.media_controller
        lecteur.play_media(url, type_contenu, title=nom_affiche)
        lecteur.block_until_active(timeout=15)
    except Exception as e:
        return f"Echec de la diffusion : {e}"

    return f"{nom_affiche} sur {appareil.cast_info.friendly_name}."


@outil(
    nom="plex_controle",
    description="Commande la lecture en cours sur un ecran : pause, reprendre, "
                "arreter, avancer ou reculer.",
    parametres={
        "type": "object",
        "properties": {
            "action": {"type": "string",
                       "description": "pause, reprendre, stop, avancer, reculer"},
            "ecran": {"type": "string", "description": "Nom de l ecran. Vide = le premier."},
        },
        "required": ["action"],
    },
)
def plex_controle(action: str, ecran: str = "") -> str:
    from tools.cast import _choisir
    appareil = _choisir(ecran)
    if appareil is None:
        return "Je ne vois pas cet ecran."
    a = (action or "").strip().lower()
    try:
        appareil.wait(timeout=10)
        m = appareil.media_controller
        if a in ("pause", "stop court"):
            m.pause(); return "En pause."
        if a in ("reprendre", "play", "lecture", "continuer"):
            m.play(); return "Lecture reprise."
        if a in ("stop", "arreter", "arrete"):
            # La session media appartient au processus qui l a ouverte ; depuis
            # un autre, stop() est refuse. Fermer l application marche toujours.
            try:
                m.stop()
            except Exception:
                appareil.quit_app()
            return "Lecture arretee."
        if a in ("avancer", "avance"):
            m.seek((m.status.current_time or 0) + 30); return "Trente secondes plus loin."
        if a in ("reculer", "recule"):
            m.seek(max(0, (m.status.current_time or 0) - 30)); return "Trente secondes en arriere."
    except Exception as e:
        return f"Commande impossible : {e}"
    return "Action inconnue."


# ------------------------------------------------------------------ musique

# Types audio renvoyes par la recherche Plex
TYPES_AUDIO = ("artist", "album", "track")

# Nombre de pistes envoyees d'un coup : au-dela le Chromecast s'etrangle
PISTES_MAX = 40


def _chercher_audio(recherche, limite=12):
    """Cherche un artiste, un album ou un morceau."""
    racine = _get("/search", {"query": recherche, "limit": limite})
    trouves = []
    if racine is not None:
        for n in racine:
            if n.get("type") in TYPES_AUDIO:
                trouves.append(n)
    if trouves:
        return trouves

    # Repli sur l'index : la recherche Plex ne pardonne pas les fautes
    for rk in _proches_dans_index(recherche):
        fiche = _get(f"/library/metadata/{rk}")
        if fiche is None:
            continue
        for n in fiche:
            if n.get("type") in TYPES_AUDIO:
                trouves.append(n)
    return trouves


def _pistes(element, profondeur=0):
    """Toutes les pistes d'un artiste, d'un album, ou la piste elle-meme.

    On interroge « allLeaves » plutot que de descendre artiste -> album ->
    piste : pendant une analyse, la hierarchie des albums peut etre vide
    alors que les pistes existent deja.
    """
    if element.get("type") == "track" or element.tag == "Track":
        return [element]

    rk = element.get("ratingKey")
    if not rk:
        # La clef ressemble a /library/metadata/7198/children
        cle = element.get("key") or ""
        morceaux = [m for m in cle.split("/") if m.isdigit()]
        rk = morceaux[-1] if morceaux else None
    if not rk:
        return []

    contenu = _get(f"/library/metadata/{rk}/allLeaves")
    if contenu is None:
        # Repli : descente classique par les enfants
        contenu = _get(element.get("key") or "")
        if contenu is None:
            return []

    sortie = []
    for n in contenu:
        if n.tag == "Track" or n.get("type") == "track":
            sortie.append(n)
        elif profondeur < 2 and n.get("type") in ("album", "artist"):
            sortie.extend(_pistes(n, profondeur + 1))
        if len(sortie) >= PISTES_MAX:
            break
    return sortie[:PISTES_MAX]


def _flux_audio(piste, hote_cast=None):
    """URL et type de contenu d'une piste, ou (None, None)."""
    import os

    for media in piste:
        if media.tag != "Media":
            continue
        conteneur = (media.get("container") or "mp3").lower()
        for part in media:
            if part.tag != "Part" or not part.get("key"):
                continue
            fichier = part.get("file") or ""
            if fichier and not os.path.exists(fichier):
                continue                 # disque debranche
            hote = reglage("plex.hote_reseau", "")
            if not hote:
                from tools.cast import _adresse_pour
                hote = _adresse_pour(hote_cast or "192.168.1.1")
            url = (f"http://{hote}:{PORT}{part.get('key')}"
                   f"?X-Plex-Token={_jeton()}")
            types = {"mp3": "audio/mpeg", "flac": "audio/flac",
                     "m4a": "audio/mp4", "aac": "audio/aac",
                     "ogg": "audio/ogg", "wav": "audio/wav",
                     "wma": "audio/x-ms-wma"}
            return url, types.get(conteneur, "audio/mpeg")
    return None, None


@outil(
    nom="plex_musique",
    description=(
        "Cherche de la musique dans la bibliotheque Plex et la diffuse sur un "
        "ecran ou une enceinte Chromecast. Pour 'mets AC/DC sur la tele', "
        "'joue l album Combat Rock dans le salon', 'diffuse The Clash'. "
        "A la difference de Spotify, tout se pilote sans toucher au telephone."
    ),
    parametres={
        "type": "object",
        "properties": {
            "recherche": {"type": "string",
                          "description": "Artiste, album ou titre a chercher."},
            "ecran": {"type": "string",
                      "description": "Nom de l ecran ou de l enceinte. Vide = le premier."},
        },
        "required": ["recherche"],
    },
    lent=True,
    phrase_attente="Je cherche dans ta musique.",
)
def plex_musique(recherche: str, ecran: str = "") -> str:
    if not disponible():
        return "Le serveur Plex ne demarre pas. Lance-le a la main."

    candidats = _chercher_audio(recherche)
    if not candidats:
        return f"Je n ai pas trouve {recherche} dans ta musique."

    # Sans ecran nomme : lecture sur le PC.
    appareil = None
    hote_cast = None
    if ecran:
        from tools.cast import _choisir
        appareil = _choisir(ecran)
        if appareil is None:
            return "Je ne vois pas cet ecran."
        hote_cast = appareil.cast_info.host

    # Le meilleur candidat qui donne au moins une piste jouable
    pistes, nom_affiche = [], ""
    for choix in candidats[:5]:
        p = _pistes(choix)
        if not p:
            continue

        if appareil is None:
            fichiers = [f for t in p for f in [_fichier(t)] if f]
            if fichiers:
                titre = choix.get("title") or recherche
                nom = (f"l album {titre}" if choix.get("type") == "album"
                       else titre)
                resultat = _jouer_local(fichiers, nom)
                if resultat:
                    return resultat
            continue
        flux = [(u, ct, t) for t in p
                for u, ct in [_flux_audio(t, hote_cast)] if u]
        if flux:
            pistes = flux
            titre = choix.get("title") or recherche
            genre = choix.get("type")
            nom_affiche = (f"l album {titre}" if genre == "album"
                           else titre)
            break

    if not pistes:
        # Distinguer « rien trouve » de « trouve mais disque debranche » :
        # l essentiel de la musique vit souvent sur un disque externe.
        manquants = 0
        for choix in candidats[:3]:
            for t in _pistes(choix):
                for m in t:
                    if m.tag != "Media":
                        continue
                    for part in m:
                        if part.tag == "Part" and part.get("file"):
                            import os as _os
                            if not _os.path.exists(part.get("file")):
                                manquants += 1
                            break
                    break
        if manquants:
            return (f"J ai trouve {recherche}, mais les fichiers sont sur un "
                    "disque qui n est pas connecte.")
        return f"J ai trouve {recherche} mais aucun fichier lisible."

    try:
        appareil.wait(timeout=12)
        lecteur = appareil.media_controller
        premier_url, premier_type, premiere = pistes[0]
        lecteur.play_media(premier_url, premier_type,
                           title=premiere.get("title") or nom_affiche,
                           stream_type="BUFFERED")
        lecteur.block_until_active(timeout=15)
        # Les suivantes en file d attente
        for url, ct, t in pistes[1:]:
            lecteur.play_media(url, ct, title=t.get("title") or "",
                               stream_type="BUFFERED", enqueue=True)
    except Exception as e:
        return f"Echec de la diffusion : {e}"

    combien = len(pistes)
    return (f"{nom_affiche} sur {appareil.cast_info.friendly_name}"
            + (f", {combien} morceaux." if combien > 1 else "."))
