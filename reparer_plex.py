"""Repointe les bibliotheques Plex de G: vers Z:.

Le disque a change de lettre : Plex conserve les anciens chemins et repond 404
sur chaque fichier. On remplace chaque emplacement G:\\... par son equivalent
Z:\\... quand celui-ci existe reellement, puis on relance l'analyse.

Aucun media n'est touche : seule la configuration des bibliotheques change.
"""
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.registre import charger_outils          # noqa: E402
charger_outils()
from tools.plex import _get, _jeton               # noqa: E402

BASE = "http://127.0.0.1:32400"


def equivalent_z(chemin):
    """Chemin correspondant sur Z:, s'il existe."""
    if not chemin or not chemin.upper().startswith("G:"):
        return None
    candidat = "Z:" + chemin[2:]
    return candidat if os.path.exists(candidat) else None


def main():
    jeton = _jeton()
    sections = _get("/library/sections")
    if sections is None:
        sys.exit("Le serveur Plex ne repond pas.")

    a_traiter = []
    for d in sections:
        emplacements = [(l.get("id"), l.get("path")) for l in d if l.tag == "Location"]
        nouveaux, change = [], False
        for _, chemin in emplacements:
            remplacant = equivalent_z(chemin)
            if remplacant:
                nouveaux.append(remplacant)
                change = True
            elif chemin and os.path.exists(chemin):
                nouveaux.append(chemin)          # emplacement encore valide
            elif chemin:
                print(f"   [ignore] introuvable des deux cotes : {chemin}")
        if change and nouveaux:
            a_traiter.append((d, nouveaux))

    if not a_traiter:
        print("Aucune bibliotheque a corriger.")
        return

    print(f"{len(a_traiter)} bibliotheque(s) a repointer :\n")
    for d, nouveaux in a_traiter:
        print(f"  {d.get('title')}")
        for n in nouveaux:
            print(f"      -> {n}")

        params = [
            ("name", d.get("title") or ""),
            ("type", d.get("type") or ""),
            ("agent", d.get("agent") or ""),
            ("scanner", d.get("scanner") or ""),
            ("language", d.get("language") or "fr-FR"),
        ]
        params += [("location", n) for n in nouveaux]
        params.append(("X-Plex-Token", jeton))

        r = requests.put(f"{BASE}/library/sections/{d.get('key')}",
                         params=params, timeout=30)
        if r.status_code >= 400:
            print(f"      ECHEC ({r.status_code}) : {r.text[:120]}")
            continue

        requests.get(f"{BASE}/library/sections/{d.get('key')}/refresh",
                     params={"X-Plex-Token": jeton}, timeout=30)
        print("      corrige, analyse lancee")
        time.sleep(1)

    print()
    print("Les analyses tournent en arriere-plan. Verification des chemins :")
    sections = _get("/library/sections")
    for d in sections:
        chemins = [l.get("path") for l in d if l.tag == "Location"]
        etat = "OK" if all(os.path.exists(c) for c in chemins if c) else "A VOIR"
        print(f"   {etat:7s} {d.get('title'):26s} {chemins}")


if __name__ == "__main__":
    main()
