"""Outils media : cherche et lance des films/series sur les disques locaux.

La recherche note chaque fichier et retient le MEILLEUR, au lieu de prendre le
premier rencontre. Les noms de release ("Film.2019.FRENCH.BDRip.x264-EXTREME_
wWw.Extreme-Down.Xyz") sont nettoyes avant comparaison : sans ca, les mots
parasites communs a des centaines de fichiers faussent tout.
"""
import json
import os
import random
import re
import subprocess
import time
from difflib import SequenceMatcher
from pathlib import Path

from core.config import reglage
from core.registre import outil
from core.util import sans_accents

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v", ".flv", ".webm",
             ".ts", ".mpg", ".mpeg"}

# Mots de release a ignorer : ils n'appartiennent pas au titre.
BRUIT = {
    "french", "truefrench", "vostfr", "vost", "subfrench", "multi", "vf", "vo",
    "vff", "vfq", "vfi", "bdrip", "brrip", "bluray", "webrip", "web", "hdrip",
    "dvdrip", "dvdscr", "hdlight", "hdtv", "remux", "proper", "repack",
    "internal", "limited", "extended", "unrated", "custom", "readnfo",
    "x264", "x265", "h264", "h265", "xvid", "divx", "hevc", "avc",
    "ac3", "aac", "dts", "dd5", "mp3", "atmos",
    "1080p", "720p", "480p", "2160p", "4k", "uhd", "sd", "hd",
    "extreme", "down", "wawacity", "www", "wwww", "tv", "xyz", "ninja",
    "irish", "rocks", "video", "com", "net", "org", "info", "eu", "ec", "lol",
    "md", "ts", "stvfrv", "newcine", "venue", "ulysse", "team", "torrent",
    "zone", "telechargement", "fichier", "film", "films",
}

# Un jeton purement technique : 4 chiffres (annee) ou motif SxxExx
RE_ANNEE = re.compile(r"^(19|20)\d{2}$")
RE_EPISODE = re.compile(r"^s\d{1,2}e\d{1,3}$", re.I)


def _vlc_chemin():
    """Trouve l'exe VLC sur le systeme."""
    for c in (r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
              r"C:\Program Files\VideoLAN\VLC\vlc.exe"):
        if Path(c).exists():
            return c
    return "vlc"


# ------------------------------------------------------------------ nettoyage

def _jetons(texte):
    """Decoupe un nom de fichier en mots utiles, sans les tags de release."""
    t = sans_accents((texte or "").lower())
    t = t.replace("'", "").replace("\u2019", "")
    t = re.sub(r"[^a-z0-9]+", " ", t)
    bruts = [m for m in t.split() if m]

    # Tout ce qui suit l'annee est de la technique : on coupe.
    for i, m in enumerate(bruts):
        if RE_ANNEE.match(m) and i > 0:
            bruts = bruts[:i]
            break

    return [m for m in bruts
            if m not in BRUIT and not RE_ANNEE.match(m) and not RE_EPISODE.match(m)]


def _titre_propre(nom_fichier):
    """Version lisible et comparable du titre."""
    return " ".join(_jetons(nom_fichier))


# ------------------------------------------------------------------ notation

def _note(requete_j, titre_j):
    """Note de 0 a 1 : a quel point le titre correspond a la requete."""
    if not requete_j or not titre_j:
        return 0.0

    req = " ".join(requete_j)
    tit = " ".join(titre_j)

    # Egalite parfaite
    if req == tit:
        return 1.0

    # La requete est contenue telle quelle dans le titre
    if req in tit:
        # Plus le titre est proche de la requete, meilleure est la note
        return 0.80 + 0.19 * (len(req) / max(len(tit), 1))

    # Correspondance mot a mot
    trouves = 0
    total_ratio = 0.0
    for mq in requete_j:
        meilleur = 0.0
        for mt in titre_j:
            if mq == mt:
                meilleur = 1.0
                break
            # Prefixe : "mandalo" pour "mandalorian"
            if len(mq) >= 4 and mt.startswith(mq):
                meilleur = max(meilleur, 0.92)
                continue
            r = SequenceMatcher(None, mq, mt).ratio()
            if r > meilleur:
                meilleur = r
        if meilleur >= 0.78:
            trouves += 1
            total_ratio += meilleur

    if trouves == 0:
        return 0.0

    couverture = trouves / len(requete_j)      # part de la requete retrouvee
    qualite = total_ratio / trouves            # a quel point ces mots collent
    # Penaliser les titres bavards : "cars" ne doit pas gagner sur un fichier
    # dont le titre fait dix mots et n'en partage qu'un.
    concision = len(requete_j) / max(len(titre_j), len(requete_j))

    return 0.75 * couverture * qualite + 0.25 * concision * qualite


# ------------------------------------------------------------------- index
# Parcourir 4500 fichiers sur un lecteur reseau prend plusieurs secondes. On le
# fait une seule fois et on garde le resultat : en memoire pour la session, et
# sur disque pour les demarrages suivants.

_INDEX = None                                   # [(chemin, jetons_du_titre)]
_CACHE = Path(__file__).resolve().parent.parent / ".cache_films.json"
_AGE_MAX = 24 * 3600                            # secondes avant reconstruction


def _construire_index(dossiers):
    """Parcourt les dossiers et renvoie [(chemin, jetons)]."""
    entrees = []
    for d in dossiers:
        try:
            for racine, sous, fichiers in os.walk(d):
                sous[:] = [s for s in sous if not s.startswith(".")]
                for f in fichiers:
                    if Path(f).suffix.lower() in VIDEO_EXT:
                        entrees.append((str(Path(racine) / f), _jetons(Path(f).stem)))
        except (PermissionError, OSError):
            continue
    return entrees


def index(dossiers=None, forcer=False):
    """Index des videos, construit une fois puis reutilise."""
    global _INDEX
    if _INDEX is not None and not forcer:
        return _INDEX

    if dossiers is None:
        dossiers = [d for d in _dossiers_de_recherche("") if d.exists()]

    if not forcer and _CACHE.exists():
        try:
            if time.time() - _CACHE.stat().st_mtime < _AGE_MAX:
                donnees = json.loads(_CACHE.read_text(encoding="utf-8"))
                _INDEX = [(c, j) for c, j in donnees]
                return _INDEX
        except Exception:
            pass

    _INDEX = _construire_index(dossiers)
    try:
        _CACHE.write_text(json.dumps(_INDEX, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return _INDEX


def _chercher_videos(dossier, nom=None, max_resultats=200):
    """Cherche dans l'index.

    Sans `nom` : renvoie jusqu'a max_resultats chemins (pour le tirage au sort).
    Avec `nom` : renvoie [(note, chemin)] trie, meilleure note d'abord.
    """
    entrees = index([Path(dossier)] if dossier else None)

    if not nom:
        return [Path(c) for c, _ in entrees[:max_resultats]]

    requete_j = _jetons(nom)
    trouves = []
    for chemin, jetons_titre in entrees:
        note = _note(requete_j, jetons_titre)
        if note >= 0.45:
            trouves.append((note, Path(chemin)))
    trouves.sort(key=lambda x: x[0], reverse=True)
    return trouves


def _lancer_avec_vlc(chemin):
    try:
        subprocess.Popen([_vlc_chemin(), str(chemin)])
        return True
    except Exception:
        try:
            os.startfile(str(chemin))
            return True
        except Exception:
            return False


def _dossiers_de_recherche(lecteur):
    if lecteur:
        l = lecteur.strip().rstrip(":\\/ ")
        return [Path(f"{l.upper()}:\\")] if len(l) == 1 else [Path(lecteur)]
    cfg = reglage("media.dossiers", [])
    if cfg:
        return [Path(d) for d in cfg]
    profil = os.environ.get("USERPROFILE", "C:\\Users\\Default")
    dossiers = [Path(profil) / "Videos"]
    for lettre in "DEFGHIJKLMNOPQRSTUVWXYZ":
        p = Path(f"{lettre}:\\")
        if p.exists():
            dossiers.append(p)
    return dossiers


@outil(
    nom="lancer_film",
    description=(
        "Cherche un FILM ou une SERIE VIDEO par son nom sur le disque et le lance avec VLC. "
        "UNIQUEMENT pour des films/videos (mkv, mp4, avi...), JAMAIS pour des jeux ou applications. "
        "Utilise quand l'utilisateur dit 'lance le film X', 'joue X', 'mets X dans VLC', "
        "'un film qui s appelle X'. Si aucun nom, lance un film au hasard. "
        "NE PAS utiliser pour lancer des jeux video comme BG3, Expedition 33, Steam, etc."
    ),
    parametres={
        "type": "object",
        "properties": {
            "nom": {
                "type": "string",
                "description": (
                    "Titre du film ou serie a chercher. NE PAS mettre un chemin ou dossier ici. "
                    "Exemples : Dolly, Inception, Mandalorian, Columbo. Vide = film au hasard."
                ),
            },
            "lecteur": {
                "type": "string",
                "description": (
                    "Lettre du lecteur ou chemin du dossier. "
                    "Ex: 'H', 'Z', 'Z:\\Film'. Vide = cherche partout."
                ),
            },
        },
        "required": [],
    },
)
def lancer_film(nom: str = "", lecteur: str = "") -> str:
    nom = (nom or "").strip()
    dossiers = [d for d in _dossiers_de_recherche(lecteur) if d.exists()]
    if not dossiers:
        return "Je n ai pas acces au dossier des films."

    if not nom:
        tous = _chercher_videos(None, None, max_resultats=5000)
        if not tous:
            return "Aucun fichier video trouve."
        choix = random.choice(tous)
        return (f"{_titre_propre(choix.stem)} lance dans VLC."
                if _lancer_avec_vlc(choix) else f"Impossible de lancer {choix.name}.")

    notes = _chercher_videos(None, nom)
    if not notes:
        return f"Je n ai pas trouve de film correspondant a {nom}."

    notes.sort(key=lambda x: x[0], reverse=True)
    meilleure, choix = notes[0]

    # Trop incertain : on le dit plutot que de lancer n importe quoi.
    if meilleure < 0.55:
        propositions = ", ".join(_titre_propre(c.stem) for _, c in notes[:3])
        return f"Je ne suis pas sur. Tu voulais dire : {propositions} ?"

    return (f"{_titre_propre(choix.stem)} lance dans VLC."
            if _lancer_avec_vlc(choix) else f"Impossible de lancer {choix.name}.")


@outil(
    nom="stopper_film",
    description=(
        "OBLIGATOIRE : appelle cet outil des que l'utilisateur dit stop, arrete, coupe, "
        "ferme, quitte, stoppe en rapport avec VLC, un film, une video ou une serie. "
        "Ne pas juste repondre a l'oral : TOUJOURS appeler cet outil."
    ),
    parametres={"type": "object", "properties": {}, "required": []},
)
def stopper_film() -> str:
    """Tue le processus VLC."""
    r = subprocess.run(["taskkill", "/F", "/IM", "vlc.exe"],
                       capture_output=True, text=True)
    return "Film arrete." if r.returncode == 0 else "VLC n etait pas en cours."
