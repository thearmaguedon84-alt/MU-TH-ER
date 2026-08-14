"""Autorise Jarvis a piloter ton compte Spotify. A lancer une seule fois.

    .venv\\Scripts\\python.exe configurer_spotify.py

Avant de lancer ce script :

 1. Va sur https://developer.spotify.com/dashboard et connecte-toi.
 2. « Create app ». Nom et description libres (« Jarvis » convient).
 3. Dans « Redirect URIs », mets exactement :  http://127.0.0.1:8899/callback
 4. Coche « Web API », enregistre.
 5. Ouvre l'app creee, « Settings » : tu y trouves le Client ID, et le Client
    Secret derriere « View client secret ».

Le script te demande ces deux valeurs, ouvre ton navigateur pour que tu
autorises l'acces, puis enregistre un jeton de rafraichissement dans
config.yaml. Ce jeton se renouvelle tout seul : c'est la derniere fois que tu
t'occupes de cette configuration.
"""
import base64
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
import yaml

PORT = 8899
REDIRECTION = f"http://127.0.0.1:{PORT}/callback"

# Le strict necessaire : lire l'etat de lecture et le commander.
PORTEE = " ".join([
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "playlist-read-private",
])

_recu = {}


class _Retour(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _recu.update({k: v[0] for k, v in params.items()})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if "code" in _recu:
            corps = ("<h2 style='font-family:sans-serif'>C'est bon.</h2>"
                     "<p style='font-family:sans-serif'>Tu peux fermer cet "
                     "onglet et revenir au terminal.</p>")
        else:
            corps = ("<h2 style='font-family:sans-serif'>Autorisation refusee.</h2>"
                     "<p style='font-family:sans-serif'>Relance le script.</p>")
        self.wfile.write(corps.encode("utf-8"))


def main():
    # Les identifiants sont peut-etre deja dans config.yaml : on ne redemande
    # que ce qui manque.
    try:
        with open("config.yaml", encoding="utf-8") as f:
            deja = (yaml.safe_load(f.read()) or {}).get("spotify", {}) or {}
    except Exception:
        deja = {}

    cid = (deja.get("client_id") or "").strip()
    secret = (deja.get("client_secret") or "").strip()

    if cid and secret:
        print("Identifiants trouves dans config.yaml.")
        print(f"  Client ID     : {cid[:4]}...{cid[-4:]}")
        print("  Client Secret : (enregistre)")
    else:
        print(__doc__)
        print("-" * 68)
        if not cid:
            cid = input("Client ID     : ").strip()
        if not secret:
            secret = input("Client Secret : ").strip()
    if not cid or not secret:
        sys.exit("Il faut les deux valeurs. Abandon.")

    etat = secrets.token_urlsafe(16)
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": REDIRECTION,
        "scope": PORTEE,
        "state": etat,
        "show_dialog": "true",
    })

    serveur = HTTPServer(("127.0.0.1", PORT), _Retour)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()

    print()
    print("J'ouvre ton navigateur pour l'autorisation.")
    print("Si rien ne s'ouvre, colle cette adresse a la main :")
    print()
    print(url)
    print()
    try:
        webbrowser.open(url)
    except Exception:
        pass

    print("J'attends ton accord dans le navigateur...")
    for _ in range(600):                      # 5 minutes de patience
        if "code" in _recu or "error" in _recu:
            break
        import time
        time.sleep(0.5)
    serveur.shutdown()

    if "error" in _recu:
        sys.exit(f"Spotify a refuse : {_recu['error']}")
    if "code" not in _recu:
        sys.exit("Aucune reponse recue. Relance le script.")
    if _recu.get("state") != etat:
        sys.exit("Reponse incoherente (etat different). Abandon par prudence.")

    entete = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "authorization_code",
              "code": _recu["code"],
              "redirect_uri": REDIRECTION},
        headers={"Authorization": f"Basic {entete}"},
        timeout=20,
    )
    if r.status_code != 200:
        sys.exit(f"Echange du code refuse ({r.status_code}) : {r.text[:200]}")

    d = r.json()
    refresh = d.get("refresh_token")
    if not refresh:
        sys.exit("Spotify n'a pas renvoye de jeton de rafraichissement.")

    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f.read()) or {}
    cfg["spotify"] = {"client_id": cid, "client_secret": secret,
                      "refresh_token": refresh}
    with open("config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False,
                  sort_keys=False)

    print()
    print("Enregistre dans config.yaml.")
    print("Verification...")

    from tools.spotify import spotify_en_cours
    print("  ", spotify_en_cours())
    print()
    print("Termine. Relance Jarvis et dis : « mets Nirvana sur Spotify ».")


if __name__ == "__main__":
    main()
