"""Brancher le compte Instagram du South Park du jour, depuis le presse-papiers.

On copie le jeton genere sur developers.facebook.com (API Instagram avec
connexion Instagram), puis on lance ce programme. Il :
  - lit le jeton dans le presse-papiers (il ne s affiche jamais) ;
  - demande a Instagram a quel compte il appartient ;
  - verifie qu il a le droit de publier ;
  - l ajoute a config.yaml (instagram.comptes) sous le nom « parodie » et
    le designe comme compte du South Park du jour.
Jarvis prolonge ensuite le jeton tout seul tous les dix jours.

    .venv\\Scripts\\python.exe brancher_instagram.py
"""
import re
import subprocess
import sys
from pathlib import Path

import requests

ICI = Path(__file__).resolve().parent
CONFIG = ICI / "config.yaml"
NOM = "parodie"
API = "https://graph.instagram.com/v21.0"


def presse_papiers():
    r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                       capture_output=True, text=True, encoding="utf-8")
    return (r.stdout or "").strip()


def main():
    jeton = presse_papiers()
    if not re.fullmatch(r"[A-Za-z0-9_\-|.]{40,}", jeton or ""):
        print("Le presse-papiers ne contient pas de jeton. Copie-le sur le site "
              "developpeur (bouton « Generer un token »), puis relance.")
        return 1

    moi = requests.get(API + "/me", timeout=20, params={
        "fields": "user_id,username,account_type", "access_token": jeton}).json()
    if "error" in moi:
        print("Instagram refuse ce jeton :", moi["error"].get("message"))
        return 1
    uid, pseudo = str(moi.get("user_id") or moi.get("id")), moi.get("username")
    print("Compte :", pseudo, "| type :", moi.get("account_type"))

    limite = requests.get("%s/%s/content_publishing_limit" % (API, uid),
                          timeout=20, params={"access_token": jeton}).json()
    if "error" in limite:
        print("Ce jeton ne permet pas de publier (permission « publication de "
              "contenu » manquante) :", limite["error"].get("message"))
        return 1
    print("Droit de publier : oui")

    texte = CONFIG.read_text(encoding="utf-8")
    if re.search(r"^\s*-?\s*nom:\s*['\"]?%s['\"]?\s*$" % NOM, texte, re.M):
        # Deja branche : on remplace seulement le jeton et l identifiant.
        texte = re.sub(
            r"(-\s*nom:\s*['\"]?%s['\"]?\s*\n\s*user_id:\s*).*\n(\s*access_token:\s*).*"
            % NOM, lambda m: m.group(1) + "'%s'\n" % uid + m.group(2) + jeton,
            texte, count=1)
    else:
        bloc = "  - nom: %s\n    user_id: '%s'\n    access_token: %s\n" % (
            NOM, uid, jeton)
        texte, n = re.subn(r"^(  comptes:\s*\n)", lambda m: m.group(1) + bloc,
                           texte, count=1, flags=re.M)
        if not n:
            print("Section instagram.comptes introuvable dans config.yaml.")
            return 1
    texte = re.sub(r"^(south_park_du_jour:\s*\n(?:  .*\n)*?  instagram:\s*).*$",
                   lambda m: m.group(1) + NOM, texte, count=1, flags=re.M)
    CONFIG.write_text(texte, encoding="utf-8")
    print("Branche : le South Park du jour publiera sur @%s." % pseudo)
    print("Redemarre Jarvis (bouton du telephone ou redemarrer_jarvis.ps1).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
