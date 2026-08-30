"""Arbitrer la memoire graphique entre les moteurs.

Quatre moteurs se partagent douze giga-octets : le modele de langage d'Ollama,
le moteur d'images, celui de musique, celui de video. Chacun garde ses poids en
memoire une fois charge, et aucun ne sait que les autres existent.

Quand deux d'entre eux y tiennent de force, la carte deborde sur la memoire
vive et tout ralentit d'un facteur dix — sans le moindre message. Mesure faite
sur cette machine : une image passe de vingt-cinq secondes a deux cent trente.

D'ou cet arbitre. Avant chaque travail lourd, on libere la place occupee par
les moteurs dont on n'a pas besoin. Ils se rechargeront a leur tour, ce qui
coute quelques secondes pour Ollama, une minute pour le moteur d'images, et
plusieurs minutes pour celui de musique — d'ou l'ordre de preference : on
prefere toujours decharger ce qui se recharge vite.
"""
import os
import time

from core.config import reglage

# Ce que chacun occupe, mesure sur une 3060 de douze giga-octets.
_COUT = {"ollama": 5.5, "image": 6.2, "musique": 5.6, "video": 8.0}


def _octets_libres():
    try:
        import subprocess
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
        utilise, total = (int(x) for x in r.stdout.split(",")[:2])
        return (total - utilise) / 1024.0
    except Exception:
        return None


def _ollama():
    """Ollama recharge son modele en quelques secondes : on le libere sans etat d ame."""
    try:
        import httpx
        hote = reglage("ollama.hote", "http://127.0.0.1:11434")
        r = httpx.get(f"{hote}/api/ps", timeout=4)
        for m in (r.json() or {}).get("models") or []:
            nom = m.get("name") or m.get("model")
            if nom:
                httpx.post(f"{hote}/api/generate",
                           json={"model": nom, "keep_alive": 0}, timeout=20)
        return True
    except Exception:
        return False


def _image_occupe():
    try:
        import httpx
        d = httpx.get("http://127.0.0.1:7860/sdapi/v1/progress",
                      timeout=4).json()
        etat = d.get("state") or {}
        return bool(etat.get("job")) and (d.get("progress") or 0) > 0
    except Exception:
        return False


def _image():
    """Decharger le modele de Forge, et l arreter si cela ne suffit pas.

    Sa commande de dechargement existe mais ne rend pas la memoire : mesure
    sur cette machine, Forge gardait six giga-octets apres l avoir appelee, ce
    qui faisait passer un rendu video de vingt-quatre secondes par etape a
    trois cent trente. On verifie donc le resultat, et on arrete le processus
    s il n a rien lache. Il redemarre tout seul en une quarantaine de
    secondes a la prochaine image.
    """
    if _image_occupe():
        return False
    avant = _octets_libres()
    try:
        import httpx
        httpx.post("http://127.0.0.1:7860/sdapi/v1/unload-checkpoint",
                   timeout=30)
    except Exception:
        pass
    time.sleep(3)
    apres = _octets_libres()
    if avant is not None and apres is not None and apres - avant > 1.5:
        return True
    return _arreter("forge")


def _arreter(marqueur):
    """Faute d une commande de dechargement, on arrete le serveur.

    Brutal mais sans risque : ces serveurs n ont aucun etat a perdre, et Jarvis
    les relance a la demande suivante.
    """
    try:
        import psutil
    except Exception:
        return False
    moi = os.getpid()
    arretes = 0
    for p in psutil.process_iter(["pid", "exe"]):
        try:
            chemin = (p.info.get("exe") or "").lower()
            if p.info["pid"] != moi and marqueur in chemin:
                p.kill()
                arretes += 1
        except Exception:
            continue
    if arretes:
        time.sleep(3)
    return arretes > 0


def _occupe(url, lire):
    """Un moteur au travail ne doit jamais etre arrete.

    L arbitre a coute un rendu video de huit minutes : il a libere la memoire
    pour une image alors que la video etait a la moitie. Rien ne le lui
    interdisait. Desormais on demande d abord.
    """
    try:
        import httpx
        return bool(lire(httpx.get(url, timeout=4).json()))
    except Exception:
        # Injoignable : soit il est mort, soit il rame. Dans le doute on ne
        # tue pas, sauf s il ne repond meme pas au port.
        return False


def _video_occupe():
    return _occupe("http://127.0.0.1:8188/queue",
                   lambda d: (d.get("queue_running") or [])
                   or (d.get("queue_pending") or []))


def _musique_occupee():
    return _occupe("http://127.0.0.1:8001/v1/stats",
                   lambda d: ((d.get("data") or d).get("running") or 0)
                   or ((d.get("data") or d).get("pending") or 0))


def _musique():
    if _musique_occupee():
        return False
    return _arreter("acestep")


def _video():
    if _video_occupe():
        return False
    return _arreter("comfyui")


# On libere dans cet ordre : le moins cher a recharger d abord. Inutile
# d arreter le moteur de musique si liberer Ollama a suffi.
_ORDRE = [("ollama", _ollama), ("image", _image),
          ("video", _video), ("musique", _musique)]


def liberer(pour="image", besoin=None):
    """Fait de la place pour le moteur demande.

    On s arrete des qu il y a assez de memoire : chaque dechargement de plus
    coutera un rechargement plus tard.
    """
    if not reglage("gpu.arbitrer", True):
        return []

    besoin = besoin or _COUT.get(pour, 6.0)
    libere = []
    for nom, action in _ORDRE:
        if nom == pour:
            continue
        dispo = _octets_libres()
        if dispo is not None and dispo >= besoin:
            break
        if action():
            libere.append(nom)
    return libere
