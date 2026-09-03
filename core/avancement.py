"""Ou en est le travail en cours, en pourcentage.

Trois sources, par ordre de fiabilite : ce que le moteur dit de lui-meme,
le rang du segment pour les travaux en plusieurs morceaux, et a defaut le
temps ecoule rapporte a une duree typique.

Rien ici ne doit pouvoir faire tomber l appelant : tout est enveloppe, et
l absence de reponse vaut « je ne sais pas », ce qui se traduit par une jauge
qui n apparait pas plutot que par une erreur.
"""
import json
import threading
import time

# Duree typique d un travail, en secondes, mesuree sur une RTX 3060. Ne sert
# que lorsque le moteur ne dit rien de lui-meme.
_TYPIQUE = {
    "image": 25.0,
    "image soignee": 60.0,
    "musique": 200.0,
    "video": 1150.0,
    "portrait": 90.0,
    "zone": 60.0,
    "clip": 15.0,
}

_NOMS = {
    "image": "Image",
    "image soignee": "Image soignee",
    "musique": "Musique",
    "video": "Video",
    "portrait": "Portrait",
    "zone": "Retouche",
    "clip": "Montage",
}

# Ce que la liaison avec ComfyUI a rapporte en dernier.
_COMFY = {"valeur": 0, "total": 0, "quand": 0.0}
_FIL = None
_VERROU = threading.Lock()


def _ecouter_comfyui(adresse="ws://127.0.0.1:8188/ws?clientId=jauge"):
    """Suit les etapes annoncees par ComfyUI, en continu.

    La liaison tombe quand le moteur redemarre — ce qui arrive a chaque
    rafraichissement. On se rebranche sans bruit plutot que de s arreter.
    """
    import websocket
    while True:
        try:
            ws = websocket.WebSocket()
            ws.connect(adresse, timeout=10)
            ws.settimeout(60)
            while True:
                message = ws.recv()
                if isinstance(message, (bytes, bytearray)):
                    continue
                d = json.loads(message)
                if d.get("type") == "progress":
                    data = d.get("data") or {}
                    with _VERROU:
                        _COMFY["valeur"] = int(data.get("value") or 0)
                        _COMFY["total"] = int(data.get("max") or 0)
                        _COMFY["quand"] = time.time()
                elif d.get("type") == "executing" and not (
                        d.get("data") or {}).get("node"):
                    with _VERROU:
                        _COMFY["valeur"] = _COMFY["total"] = 0
        except Exception:
            with _VERROU:
                _COMFY["valeur"] = _COMFY["total"] = 0
            time.sleep(5)


def demarrer_ecoute():
    """Ouvre la liaison avec ComfyUI, une fois pour toutes."""
    global _FIL
    if _FIL is not None:
        return
    try:
        _FIL = threading.Thread(target=_ecouter_comfyui, daemon=True)
        _FIL.start()
    except Exception:
        _FIL = None


def _part_comfyui():
    """La fraction annoncee par ComfyUI, ou None si elle est perimee."""
    with _VERROU:
        valeur, total, quand = (_COMFY["valeur"], _COMFY["total"],
                                _COMFY["quand"])
    # Une valeur qui n a pas bouge depuis deux minutes ne decrit plus rien.
    if total <= 0 or time.time() - quand > 120:
        return None
    return max(0.0, min(valeur / float(total), 1.0))


def _part_forge():
    """La fraction annoncee par Forge, ou None."""
    try:
        import httpx
        r = httpx.get("http://127.0.0.1:7860/sdapi/v1/progress",
                      params={"skip_current_image": "true"}, timeout=3)
        d = r.json()
        p = float(d.get("progress") or 0.0)
        return p if 0 < p <= 1 else None
    except Exception:
        return None


def etat():
    """Ce qui occupe la machine, et a quel point c est avance.

    Rend None quand rien ne tourne : l interface efface alors sa jauge.
    """
    try:
        from core.file_gpu import en_cours
        travail = en_cours()
    except Exception:
        travail = None
    if not travail:
        return None

    quoi = travail.get("quoi") or ""
    debut = float(travail.get("depuis") or time.time())
    ecoule = max(0.0, time.time() - debut)

    fraction = _part_comfyui()
    if fraction is None and quoi.startswith("image"):
        fraction = _part_forge()

    # Un travail en plusieurs morceaux : le moteur ne connait que le morceau
    # en cours, nous seuls savons combien il en reste.
    fait, total = travail.get("fait") or 0, travail.get("total") or 0
    detail = travail.get("detail") or ""
    if total > 1:
        dans_le_morceau = fraction if fraction is not None else 0.0
        pourcent = 100.0 * (fait + dans_le_morceau) / total
        etape = "%d/%d" % (min(fait + 1, total), total)
    elif fraction is not None:
        pourcent = 100.0 * fraction
        etape = ""
    else:
        # Le moteur ne dit rien : on estime, sans jamais annoncer la fin.
        typique = _TYPIQUE.get(quoi, 120.0)
        pourcent = min(95.0, 100.0 * ecoule / typique)
        etape = ""

    return {
        "quoi": quoi,
        "nom": _NOMS.get(quoi, quoi.capitalize() or "Travail"),
        "detail": detail[:60],
        "etape": etape,
        "pourcent": int(max(0, min(round(pourcent), 100))),
        "sur": fraction is not None or total > 1,
        "ecoule": int(ecoule),
    }
