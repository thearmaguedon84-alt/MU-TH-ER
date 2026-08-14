"""Pilotage du serveur Plex et diffusion sur les ecrans Chromecast.

Principe retenu : Plex sert a TROUVER le media, le Chromecast le lit
directement depuis le serveur. On ne depend donc pas de l'application Plex
installee sur la television — n'importe quel Chromecast fait l'affaire.

Le jeton d'acces est lu dans la base de registre, la ou Plex l'ecrit sur
Windows (HKCU\\Software\\Plex, Inc.\\Plex Media Server). Rien a configurer tant
que le serveur tourne sur cette machine.
"""
import urllib.parse
import urllib.request
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


def disponible():
    return _get("/identity") is not None


# ------------------------------------------------------------------ recherche

def _normaliser(s):
    return sans_accents(str(s or "").lower()).strip()


def _chercher(titre, limite=12):
    """Cherche un film ou un episode. Renvoie une liste d'elements Video."""
    racine = _get("/search", {"query": titre, "limit": limite})
    if racine is None:
        return []
    resultats = []
    for n in racine:
        if n.tag in ("Video", "Directory") and n.get("type") in (
                "movie", "episode", "show"):
            resultats.append(n)
    return resultats


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
        return "Le serveur Plex ne repond pas."
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
        return "Le serveur Plex ne repond pas."

    candidats = _classer(titre, _chercher(titre))
    if not candidats:
        return f"Je n ai pas trouve {titre} dans Plex."

    # On essaie les resultats dans l ordre : le premier dont le fichier est
    # reellement accessible l emporte. Un meme titre existe souvent en
    # plusieurs exemplaires, dont certains sur un disque debranche.
    # On identifie l ecran d abord : son adresse determine quelle interface
    # reseau annoncer dans l URL du flux.
    from tools.cast import _choisir
    appareil = _choisir(ecran)
    if appareil is None:
        return ("Je ne vois pas cet ecran." if ecran
                else "Je ne vois aucun ecran Chromecast.")
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
