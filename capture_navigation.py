"""Noter ou la page va quand l'utilisateur ouvre une chaine.

myCANAL est une application : cliquer une chaine ne recharge rien, l'adresse
change sans requete de page. On ecoute donc les changements d'adresse internes
autant que les navigations classiques, et on note en meme temps les appels
reseau qui suivent — c'est la que se voit l'identifiant demande.

Un seul clic suffit : le motif obtenu vaut ensuite pour toutes les chaines.
"""
import json
import os
import sys
import time

sys.path.insert(0, ".")

from tools.navigateur_cast import _brancher, demarrer_chrome  # noqa: E402

DUREE = int(sys.argv[1]) if len(sys.argv) > 1 else 150
SORTIE = os.path.join("recettes", "_navigation.json")

INTERESSANT = ("hodor", "ltv.services", "secure-webtv-static", "routemeup",
               "canalplus.com/live", "player.canalplus")


def main():
    os.makedirs("recettes", exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump({"etat": "en cours"}, f)

    demarrer_chrome()
    cdp = _brancher("canalplus.com")
    if cdp is None:
        with open(SORTIE, "w", encoding="utf-8") as f:
            json.dump({"etat": "echec"}, f)
        return

    cdp.demander("Page.enable")
    cdp.demander("Network.enable", {"maxPostDataSize": 8192})
    cdp.vider()

    adresses, appels = [], []
    t0 = time.time()
    while time.time() - t0 < DUREE:
        time.sleep(3)
        for e in cdp.vider():
            m, p = e.get("method"), e.get("params", {})
            if m == "Page.navigatedWithinDocument":
                adresses.append(("interne", p.get("url", "")))
            elif m == "Page.frameNavigated":
                adresses.append(("page", (p.get("frame") or {}).get("url", "")))
            elif m == "Network.requestWillBeSent":
                u = p.get("request", {}).get("url", "")
                if any(i in u for i in INTERESSANT):
                    appels.append((round(p.get("timestamp", 0), 1),
                                   p["request"].get("method", ""), u[:220]))

    cdp.demander("Network.disable")
    cdp.fermer()

    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump({"etat": "fini", "adresses": adresses, "appels": appels},
                  f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
