"""Une carte graphique, un travail a la fois.

Deux travaux lourds lances ensemble ne vont pas deux fois moins vite : ils
vont dix fois moins vite chacun. Mesure faite sur cette machine — une image
demandee pendant un rendu video annoncait quatre heures et quarante-trois
minutes, contre vingt-cinq secondes seule. Les deux se battaient pour la meme
memoire et debordaient sur la memoire vive.

D'ou cette file. Elle ne rend rien plus rapide : elle empeche seulement de
tout ralentir en meme temps. Le second travail attend son tour, et
l'utilisateur est prevenu de ce qui se passe plutot que de constater une
lenteur inexplicable.

Le jeton est un fichier, et non un verrou en memoire : les moteurs vivent dans
des processus separes, et Jarvis lui-meme peut etre relance en cours de route.
Un jeton dont le processus n'existe plus, ou vieux de plus de trois heures,
est considere comme abandonne.
"""
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

JETON = Path(__file__).resolve().parent.parent / ".gpu_travail.json"
ABANDON = 3 * 3600  # au-dela, on considere le travail perdu

# Ce qu'on dit a l'utilisateur, par genre de travail.
_NOMS = {
    "image": "une image",
    "video": "une video",
    "musique": "un morceau",
    "clip": "un montage",
}


def _vivant(pid):
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        return True


def en_cours():
    """Ce qui occupe la carte, ou None."""
    try:
        d = json.loads(JETON.read_text(encoding="utf-8"))
    except Exception:
        return None
    if time.time() - d.get("depuis", 0) > ABANDON:
        return None
    if not _vivant(d.get("pid", 0)):
        return None
    return d


def libelle(d=None):
    """Une phrase disant ce qui tourne, pour l annoncer."""
    d = d or en_cours()
    if not d:
        return ""
    quoi = _NOMS.get(d.get("quoi"), "un travail")
    minutes = int((time.time() - d.get("depuis", time.time())) / 60)
    detail = (d.get("detail") or "")[:60]
    debut = f"Je fabrique deja {quoi}"
    if detail:
        debut += f" ({detail})"
    if minutes >= 1:
        debut += f", commencee il y a {minutes} minutes"
    return debut


def _prendre(quoi, detail):
    try:
        JETON.parent.mkdir(parents=True, exist_ok=True)
        f = os.open(str(JETON), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(f, "w", encoding="utf-8") as s:
            json.dump({"quoi": quoi, "detail": detail, "pid": os.getpid(),
                       "depuis": time.time()}, s)
        return True
    except FileExistsError:
        # Le jeton existe : il est peut-etre perime.
        if en_cours() is None:
            try:
                JETON.unlink()
            except Exception:
                pass
            return _prendre(quoi, detail)
        return False
    except Exception:
        return True  # en cas de doute, on ne bloque pas l utilisateur


def _rendre():
    try:
        d = json.loads(JETON.read_text(encoding="utf-8"))
        if d.get("pid") == os.getpid():
            JETON.unlink()
    except Exception:
        pass


@contextmanager
def tour(quoi, detail="", patience=4 * 3600):
    """Attend son tour, fait le travail, rend la main.

    On attend plutot que de refuser : l utilisateur a demande quelque chose,
    il l aura. Simplement pas tout de suite, et il sait pourquoi.
    """
    debut = time.time()
    while not _prendre(quoi, detail):
        if time.time() - debut > patience:
            break
        time.sleep(5)
    try:
        yield
    finally:
        _rendre()


def annonce_attente(quoi):
    """Ce que Jarvis dit si la carte est deja prise. Vide si elle est libre."""
    d = en_cours()
    if not d:
        return ""
    if d.get("quoi") == quoi and d.get("pid") == os.getpid():
        return ""
    return libelle(d) + ". Je m occupe de ta demande juste apres."

def enfile(quoi, champ=None):
    """Decorateur : le travail attend son tour avant de commencer.

    A poser SOUS le decorateur d outil, pour que ce soit la version encadree
    qui soit enregistree. On ne touche pas au corps de la fonction : une file
    d attente n a pas a se meler de ce qu elle fait attendre.
    """
    import functools

    def decorateur(f):
        @functools.wraps(f)
        def enveloppe(*args, **kwargs):
            detail = ""
            if champ:
                detail = str(kwargs.get(champ)
                             or (args[0] if args else ""))[:60]
            with tour(quoi, detail):
                return f(*args, **kwargs)
        return enveloppe
    return decorateur
