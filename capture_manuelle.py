"""Enregistre le reseau pendant que l'utilisateur agit a la main.

Tourne en fond pendant une duree donnee, sans rien commander : c'est lui qui
clique. On note tout ce que Chrome envoie, on jette le bruit, et on ecrit le
resultat dans un fichier que Jarvis relira ensuite.

Le but n'est pas de rejouer tout de suite, mais de VOIR l'interface reelle du
service : quelles adresses portent la recherche, la liste des chaines, le
lancement de la lecture. Une fois ces adresses connues, on n'a plus besoin de
deviner un bouton dans une page.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.apprendre_reseau import _brancher, _interessante  # noqa: E402
from tools.navigateur_cast import demarrer_chrome  # noqa: E402

DUREE = int(sys.argv[1]) if len(sys.argv) > 1 else 180
SORTIE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "recettes", "_capture.json")


def main():
    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    # Marqueur de depart : Jarvis saura que l'enregistrement a bien commence.
    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump({"etat": "en cours", "debut": time.time()}, f)

    if not demarrer_chrome():
        with open(SORTIE, "w", encoding="utf-8") as f:
            json.dump({"etat": "echec", "raison": "navigateur"}, f)
        return

    cdp = _brancher()
    if cdp is None:
        with open(SORTIE, "w", encoding="utf-8") as f:
            json.dump({"etat": "echec", "raison": "connexion"}, f)
        return

    cdp.demander("Network.enable", {"maxPostDataSize": 131072})
    cdp.vider()

    lot = []
    t0 = time.time()
    while time.time() - t0 < DUREE:
        time.sleep(5)
        lot.extend(cdp.vider())

    cdp.demander("Network.disable")
    cdp.fermer()

    demandes, reponses = {}, {}
    for e in lot:
        m, p = e.get("method"), e.get("params", {})
        if m == "Network.requestWillBeSent":
            demandes[p.get("requestId")] = p
        elif m == "Network.responseReceived":
            reponses[p.get("requestId")] = p.get("response", {})

    trames = []
    for ident, p in demandes.items():
        r = p.get("request", {})
        if not _interessante(r, p.get("type", "")):
            continue
        rep = reponses.get(ident, {})
        trames.append({
            "methode": r.get("method", "GET"),
            "url": r.get("url"),
            # 2000 octets coupaient les requetes GraphQL avant leurs variables.
            "corps": (r.get("postData") or "")[:200000],
            "type": p.get("type", ""),
            "statut": rep.get("status", 0),
            "mime": rep.get("mimeType", ""),
            "instant": round(p.get("timestamp", 0), 2),
        })
    trames.sort(key=lambda t: t["instant"])

    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump({"etat": "fini", "vues": len(demandes),
                   "retenues": len(trames), "trames": trames},
                  f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
