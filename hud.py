"""
Interface visuelle facon reacteur arc pour l'assistant vocal.

Sert une petite page web en local et lui pousse l'etat en temps reel
via Server-Sent Events. Bibliotheque standard uniquement : aucun paquet
a installer. Le serveur tourne dans un thread daemon, donc importer ce
module et appeler demarrer() n'empeche jamais le programme de quitter.

Exemple :

    import hud
    hud.demarrer()
    hud.config("qwen3.5:4b", "whisper medium")
    hud.etat("ecoute")
    hud.niveau(0.6)
    hud.dire_vous("allume la lumiere de la chambre")
    hud.outil("allumer_lumiere", "chambre -> on")
    hud.dire_jarvis("C'est fait, la chambre est allumee.")

Lance directement (python hud.py) il joue un scenario en boucle pour
voir le rendu sans le reste de l'assistant.
"""

import json
import os
import queue
import socket
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------- reglages

PORT = 8770
# Adresse d ecoute. 127.0.0.1 = accessible seulement depuis ce PC.
# 0.0.0.0 = visible sur le reseau local, indispensable pour qu un
# Chromecast puisse afficher la page.
HOTE = "127.0.0.1"
# Serveur HTTPS supplementaire, sur PORT + 1. Les navigateurs n'autorisent le
# micro que sur une page securisee : sans lui, la dictee est impossible depuis
# un telephone. Le serveur HTTP reste en place pour l'acces depuis le PC.
HTTPS = False
PORT_HTTPS = None          # calcule au demarrage : PORT + 1
_SERVEUR_HTTPS = None
_FICHIER_HTML = Path(__file__).parent / "hud.html"
# Interface alternative MU-TH-UR (Nostromo), servie sur /mother.
# Elle consomme exactement le meme flux : rien d'autre ne change.
_FICHIER_MOTHER = Path(__file__).parent / "hud_mother.html"
# Page allegee pour telephone, servie sur /tel.
_FICHIER_TEL_MOTHER = Path(__file__).parent / "hud_tel_mother.html"
_FICHIER_TEL = Path(__file__).parent / "hud_tel.html"

# Etats possibles, envoyes tels quels a la page.
VEILLE = "veille"
ECOUTE = "ecoute"
REFLEXION = "reflexion"
PAROLE = "parole"

# ---------------------------------------------------------------- etat partage

# Instantane courant, renvoye a chaque nouveau client pour qu'il affiche
# tout de suite le bon etat sans attendre le prochain evenement.
_ETAT = {
    "etat": VEILLE,
    "niveau": 0.0,
    "modele": "",
    "stt": "",
    # Interface souhaitee : "jarvis" ou "mother". Les pages ouvertes s'y
    # redirigent d'elles-memes quand la valeur change.
    "interface": "jarvis",
}

# Une file par onglet connecte. Le verrou protege l'ensemble.
_CLIENTS = set()
_VERROU = threading.Lock()

# Dernieres lignes de transcription, rejouees a la reconnexion d'un client.
_HISTORIQUE = deque(maxlen=40)

_SERVEUR = None

# Commandes envoyees depuis un telephone, en attente de traitement.
# La boucle principale les consomme via commande_en_attente().
_COMMANDES = queue.Queue(maxsize=20)

# Enregistrements envoyes par un telephone, en attente de transcription.
_AUDIOS = queue.Queue(maxsize=5)


def audio_en_attente():
    """Prochain enregistrement recu d'un telephone, ou None."""
    try:
        return _AUDIOS.get_nowait()
    except queue.Empty:
        return None


def commande_en_attente():
    """Prochaine commande envoyee depuis une page, ou None."""
    try:
        return _COMMANDES.get_nowait()
    except queue.Empty:
        return None


# Fonction appelee quand une page demande un changement de mode (bouton).
# Renseignee par l'assistant via sur_changement_mode().
_SUR_MODE = None


def sur_changement_mode(fonction):
    """Enregistre la fonction a appeler quand une page demande un mode."""
    global _SUR_MODE
    _SUR_MODE = fonction


def _diffuser(evenement):
    """Envoie un evenement (dict) a tous les clients connectes."""
    donnees = json.dumps(evenement, ensure_ascii=False)
    with _VERROU:
        morts = []
        for fil in _CLIENTS:
            try:
                fil.put_nowait(donnees)
            except queue.Full:
                # Client qui ne lit plus : on l'abandonne.
                morts.append(fil)
        for fil in morts:
            _CLIENTS.discard(fil)


# ---------------------------------------------------------------- API publique


def etat(nom):
    """Change l'etat visuel : veille, ecoute, reflexion ou parole."""
    _ETAT["etat"] = nom
    _diffuser({"t": "etat", "v": nom})


def niveau(valeur):
    """Regle le niveau du micro, entre 0 et 1. Fait enfler le coeur."""
    v = max(0.0, min(float(valeur), 1.0))
    _ETAT["niveau"] = v
    _diffuser({"t": "niveau", "v": v})



# Derniere parole prononcee, mise a disposition des interfaces. Une seule est
# gardee : ce qui vient d'etre dit interesse, ce qui l'a ete avant, non.
_VOIX = {"numero": 0, "donnees": None}


def publier_specimen(duree=24):
    """Demande aux interfaces de tracer le specimen tout de suite.

    Le dessin se declenche normalement apres une demi-minute d inactivite.
    Cet ordre permet de l appeler quand on veut, sans attendre la veille.
    """
    _diffuser({"t": "specimen", "duree": int(duree)})


def publier_image(url, description=""):
    """Signale une image fraiche aux interfaces, qui l afficheront."""
    _diffuser({"t": "image", "url": url, "texte": str(description)[:120]})


def publier_voix(donnees_wav):
    """Signale une parole aux interfaces, qui pourront la jouer.

    Sert a la diffusion : la recopie d'un onglet ne capte que l'audio de la
    page, pas celui du systeme. En faisant jouer la voix par la page, elle
    arrive sur l'ecran distant.
    """
    if not donnees_wav:
        return
    _VOIX["numero"] += 1
    _VOIX["donnees"] = donnees_wav
    _diffuser({"t": "voix", "url": "/voix.wav?v=%d" % _VOIX["numero"]})

def dire_vous(texte):
    """Ajoute une ligne de transcription cote utilisateur."""
    evenement = {"t": "vous", "texte": str(texte)}
    _HISTORIQUE.append(evenement)
    _diffuser(evenement)


def dire_jarvis(texte, duree=None):
    """Ajoute une ligne de transcription cote assistant.

    duree : longueur en secondes de la phrase parlee, si elle est connue. Les
    interfaces qui animent la frappe s'en servent pour finir d'ecrire en meme
    temps que la voix se tait.
    """
    evenement = {"t": "jarvis", "texte": str(texte)}
    if duree:
        evenement["duree"] = round(float(duree), 2)
    _HISTORIQUE.append(evenement)
    _diffuser(evenement)


def outil(nom, detail=""):
    """Signale un appel d'outil dans la transcription."""
    evenement = {"t": "outil", "nom": str(nom), "detail": str(detail)}
    _HISTORIQUE.append(evenement)
    _diffuser(evenement)


def interface(nom):
    """Demande aux pages ouvertes d'afficher « jarvis » ou « mother »."""
    nom = "mother" if str(nom) == "mother" else "jarvis"
    if _ETAT.get("interface") == nom:
        return
    _ETAT["interface"] = nom
    _diffuser({"t": "interface", "v": nom})


def config(modele, stt):
    """Renseigne le releve d'etat : modele de langage et moteur d'ecoute."""
    _ETAT["modele"] = modele
    _ETAT["stt"] = stt
    _diffuser({"t": "config", "modele": modele, "stt": stt})



_ICONE = {}


def _icone(taille=512, mother=False):
    """Icone de l'application, dessinee une fois puis gardee en memoire."""
    clef = (taille, mother)
    if clef in _ICONE:
        return _ICONE[clef]
    try:
        import io

        from PIL import Image, ImageDraw
    except Exception:
        return None

    image = Image.new("RGBA", (taille, taille), (6, 18, 26, 255))
    d = ImageDraw.Draw(image)
    c = taille / 2
    # Trois cercles concentriques, comme la pastille du HUD.
    # MU-TH-UR se distingue au premier coup d oeil : vert phosphore contre
    # cyan, faute de quoi les deux icones se confondent sur l ecran d accueil.
    teinte = (53, 255, 106) if mother else (34, 211, 238)
    for rayon, couleur, epaisseur in (
            (0.42, teinte + (255,), max(2, taille // 64)),
            (0.30, teinte + (130,), max(2, taille // 96)),
            (0.17, teinte + (255,), 0)):
        boite = [c - taille * rayon, c - taille * rayon,
                 c + taille * rayon, c + taille * rayon]
        if epaisseur:
            d.ellipse(boite, outline=couleur, width=epaisseur)
        else:
            d.ellipse(boite, fill=couleur)

    tampon = io.BytesIO()
    image.save(tampon, format="PNG")
    _ICONE[clef] = tampon.getvalue()
    return _ICONE[clef]


_MANIFESTE = json.dumps({
    "name": "Jarvis",
    "short_name": "Jarvis",
    "start_url": "/tel",
    "scope": "/",
    # « standalone » retire la barre d adresse : c est ce qui distingue une
    # application d un signet.
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#06121a",
    "theme_color": "#06121a",
    "icons": [
        {"src": "/icone-192.png", "sizes": "192x192", "type": "image/png",
         "purpose": "any maskable"},
        {"src": "/icone-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "any maskable"},
    ],
}, ensure_ascii=False)


_MANIFESTE_MOTHER = json.dumps({
    "name": "MU-TH-UR",
    "short_name": "MU-TH-UR",
    "start_url": "/tel/mother",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#030a06",
    "theme_color": "#030a06",
    "icons": [
        {"src": "/icone-mother-192.png", "sizes": "192x192",
         "type": "image/png", "purpose": "any maskable"},
        {"src": "/icone-mother-512.png", "sizes": "512x512",
         "type": "image/png", "purpose": "any maskable"},
    ],
}, ensure_ascii=False)


# Sans cache : cette interface commande le PC en direct, un etat garde en
# reserve serait un etat faux.
_OUVRIER = "self.addEventListener('fetch', () => {});"

# ---------------------------------------------------------------- serveur


class _Poignee(BaseHTTPRequestHandler):
    """Sert la page et le flux SSE. Le reste renvoie 404."""

    def log_message(self, *args):
        pass  # pas de bruit dans la console

    def do_GET(self):
        # Le chemin porte la chaine de requete : "/tel/mother?" n est pas
        # "/tel/mother". Un navigateur ou une application installee ajoutent
        # volontiers un point d interrogation, et toutes les routes a
        # comparaison exacte tombaient alors en 404.
        self.path = self.path.split("?", 1)[0] or "/"
        if self.path == "/flux":
            self._flux()
        elif self.path in ("/tel", "/telephone", "/mobile", "/phone"):
            self._page(_FICHIER_TEL)
        elif self.path.startswith("/mode/"):
            self._mode(self.path.rsplit("/", 1)[-1])
        elif self.path in ("/mother", "/mother.html", "/muthur", "/maman"):
            self._page(_FICHIER_MOTHER)
        elif self.path in ("/", "/hud.html", "/index.html"):
            self._page()
        elif self.path in ("/tel/mother", "/telmother", "/mother/tel",
                           "/maman/tel"):
            self._page(_FICHIER_TEL_MOTHER)
        elif self.path == "/voix.wav":
            if _VOIX["donnees"]:
                self._brut(_VOIX["donnees"], "audio/wav")
            else:
                self.send_error(404)
        elif self.path == "/essai-son":
            # Page de diagnostic : verifie si un televiseur accepte de jouer
            # un son sans geste prealable.
            self._page(Path(__file__).parent / "essai_son.html")
        elif self.path.startswith("/image/"):
            # Les images fabriquees, servies a l interface et aux televiseurs.
            nom = os.path.basename(self.path)
            from core.dossiers import dossier
            fichier = dossier("images") / nom
            if fichier.exists() and fichier.suffix.lower() == ".png":
                self._brut(fichier.read_bytes(), "image/png")
            else:
                self.send_error(404)
        elif self.path.startswith("/musique/"):
            # Les morceaux composes, pour les enceintes et les televiseurs.
            nom = os.path.basename(self.path)
            from core.dossiers import dossier
            fichier = dossier("musiques") / nom
            if fichier.exists() and fichier.suffix.lower() in (".mp3", ".wav"):
                self._brut(fichier.read_bytes(),
                           "audio/mpeg" if fichier.suffix.lower() == ".mp3"
                           else "audio/wav")
            else:
                self.send_error(404)
        elif self.path.startswith("/clip/") or self.path.startswith("/video/"):
            # Clips montes et sequences video, pour les televiseurs.
            nom = os.path.basename(self.path)
            sous = "clips" if self.path.startswith("/clip/") else "videos"
            from core.dossiers import dossier
            fichier = dossier(sous) / nom
            if fichier.exists() and fichier.suffix.lower() in (".mp4", ".webm"):
                self._brut(fichier.read_bytes(),
                           "video/mp4" if fichier.suffix.lower() == ".mp4"
                           else "video/webm")
            else:
                self.send_error(404)
        elif self.path in ("/veille", "/veille.html", "/tel/veille",
                           "/economiseur"):
            # L economiseur autonome : un seul fichier, qui n a besoin de rien.
            self._page(Path(__file__).parent / "veille.html")
        elif self.path == "/specimens.json":
            # Les quatre releves complets. Trop lourds pour etre inscrits
            # dans la page, servis a part et mis en cache par le navigateur.
            try:
                with open(Path(__file__).parent / "specimens.json", "rb") as f:
                    self._brut(f.read(), "application/json")
            except Exception:
                self.send_error(404)
        elif self.path == "/specimen.json":
            # Servi a part : les deux interfaces y puisent, aucune n en garde
            # une copie.
            try:
                with open(Path(__file__).parent / "specimen_chemins.json",
                          "rb") as f:
                    self._brut(f.read(), "application/json")
            except Exception:
                self.send_error(404)
        elif self.path == "/manifest-mother.webmanifest":
            self._brut(_MANIFESTE_MOTHER.encode("utf-8"),
                       "application/manifest+json")
        elif self.path.startswith("/icone-mother-"):
            try:
                taille = int(self.path.split("-")[2].split(".")[0])
            except Exception:
                taille = 512
            image = _icone(taille, mother=True)
            if image is None:
                self.send_error(404)
            else:
                self._brut(image, "image/png")
        elif self.path == "/manifest.webmanifest":
            self._brut(_MANIFESTE.encode("utf-8"), "application/manifest+json")
        elif self.path == "/sw.js":
            self._brut(_OUVRIER.encode("utf-8"), "application/javascript")
        elif self.path.startswith("/icone-"):
            try:
                taille = int(self.path.split("-")[1].split(".")[0])
            except Exception:
                taille = 512
            image = _icone(taille)
            if image is None:
                self.send_error(404)
            else:
                self._brut(image, "image/png")
        else:
            self.send_error(404)

    def _brut(self, contenu, type_mime):
        """Envoie un contenu tel quel."""
        self.send_response(200)
        self.send_header("Content-Type", type_mime)
        self.send_header("Content-Length", str(len(contenu)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(contenu)
        except Exception:
            pass

    def do_POST(self):
        self.path = self.path.split("?", 1)[0] or "/"
        """Reception d'une commande ou d'un enregistrement du telephone."""
        if self.path == "/audio":
            self._audio()
            return
        if self.path != "/commande":
            self.send_error(404)
            return
        try:
            taille = int(self.headers.get("Content-Length") or 0)
            corps = self.rfile.read(min(taille, 4000)).decode("utf-8", "replace")
            texte = json.loads(corps).get("texte", "")
        except Exception:
            texte = ""
        texte = str(texte).strip()[:300]

        if not texte:
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        try:
            _COMMANDES.put_nowait(texte)
            reponse = b'{"ok":true}'
        except queue.Full:
            reponse = b'{"ok":false,"raison":"file pleine"}'

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(reponse)))
        self.end_headers()
        self.wfile.write(reponse)

    def _audio(self):
        """Recoit un enregistrement brut et le met en file pour Whisper."""
        try:
            taille = int(self.headers.get("Content-Length") or 0)
            if taille <= 0 or taille > 8_000_000:
                raise ValueError("taille invalide")
            donnees = self.rfile.read(taille)
            type_mime = self.headers.get("Content-Type", "audio/ogg")
        except Exception:
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        try:
            _AUDIOS.put_nowait((donnees, type_mime))
            corps = b'{"ok":true}'
        except queue.Full:
            corps = b'{"ok":false}'

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def _mode(self, voulu):
        """Bascule demandee par un bouton de l'interface."""
        voulu = "mother" if voulu == "mother" else "jarvis"
        if _SUR_MODE is not None:
            try:
                _SUR_MODE(voulu)
            except Exception:
                pass
        else:
            interface(voulu)
        # On renvoie vers la page correspondante
        self.send_response(303)
        self.send_header("Location", "/mother" if voulu == "mother" else "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _page(self, fichier=None):
        fichier = fichier or _FICHIER_HTML
        try:
            corps = fichier.read_bytes()
        except OSError:
            self.send_error(500, f"{fichier.name} introuvable")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def _flux(self):
        """Une connexion SSE = une file dediee, videe jusqu'a la deconnexion."""
        fil = queue.Queue(maxsize=200)
        with _VERROU:
            _CLIENTS.add(fil)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            # Instantane : etat courant puis historique recent, pour qu'un
            # onglet qui arrive (ou revient) soit tout de suite a jour.
            self._pousser({"t": "etat", "v": _ETAT["etat"]})
            self._pousser({"t": "niveau", "v": _ETAT["niveau"]})
            self._pousser({"t": "config", "modele": _ETAT["modele"],
                           "stt": _ETAT["stt"]})
            self._pousser({"t": "interface", "v": _ETAT["interface"]})
            for evenement in list(_HISTORIQUE):
                self._pousser(evenement)

            # Flux continu. Le timeout sert a envoyer un battement de coeur
            # qui garde la connexion (et detecte les clients partis).
            while True:
                try:
                    donnees = fil.get(timeout=15)
                    self._ecrire(donnees)
                except queue.Empty:
                    self.wfile.write(b": battement\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # l'onglet a ete ferme
        finally:
            with _VERROU:
                _CLIENTS.discard(fil)

    def _pousser(self, evenement):
        self._ecrire(json.dumps(evenement, ensure_ascii=False))

    def _ecrire(self, donnees):
        self.wfile.write(b"data: " + donnees.encode("utf-8") + b"\n\n")
        self.wfile.flush()


class _Serveur(ThreadingHTTPServer):
    """Serveur HUD silencieux sur les deconnexions clientes (onglet ferme/rechargé)."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionError, OSError)):
            return  # deconnexion normale : pas de traceback dans la console
        super().handle_error(request, client_address)




def _tailscale():
    """Chemin de l'outil du reseau prive, ou None."""
    for c in (r"C:\Program Files\Tailscale\tailscale.exe",
              r"C:\Program Files (x86)\Tailscale\tailscale.exe"):
        if Path(c).exists():
            return c
    return None


def _jours_restants(chemin):
    """Jours avant expiration d'un certificat, ou None s'il est illisible."""
    try:
        import datetime

        from cryptography import x509
        c = x509.load_pem_x509_certificate(Path(chemin).read_bytes())
        reste = c.not_valid_after_utc - datetime.datetime.now(
            datetime.timezone.utc)
        return reste.days
    except Exception:
        return None


def _renouveler_certificat_reseau(dossier):
    """Redemande le certificat s'il approche de son terme.

    Un echec n'est pas grave : l'ancien reste valable jusqu'a sa date, et le
    certificat auto-signe prendrait le relais ensuite.
    """
    cert = dossier / "ts_cert.pem"
    if not cert.exists():
        return
    jours = _jours_restants(cert)
    if jours is None or jours > 15:
        return

    outil = _tailscale()
    if outil is None:
        return
    try:
        import json
        import subprocess
        etat = json.loads(subprocess.run(
            [outil, "status", "--json"], capture_output=True, text=True,
            timeout=30).stdout)
        nom = (etat.get("Self") or {}).get("DNSName", "").rstrip(".")
        if not nom:
            return
        subprocess.run([outil, "cert",
                        "--cert-file", str(cert),
                        "--key-file", str(dossier / "ts_key.pem"), nom],
                       capture_output=True, timeout=120)
    except Exception:
        pass

def _adresses_locales():
    """Adresses IPv4 de la machine, toutes interfaces confondues.

    `getaddrinfo` sur le nom de la machine ne rend que les interfaces connues
    du resolveur : une adresse de reseau prive, ajoutee apres coup, lui
    echappe. On interroge donc les interfaces elles-memes, et on retombe sur
    l ancienne methode si ce n est pas possible.
    """
    adresses = {"127.0.0.1"}
    try:
        import psutil
        for cartes in psutil.net_if_addrs().values():
            for c in cartes:
                if getattr(c, "family", None) != socket.AF_INET or not c.address:
                    continue
                # Les adresses en 169.254 sont attribuees faute de mieux par
                # des interfaces sans reseau, et changent a chaque demarrage :
                # les retenir ferait refabriquer le certificat sans cesse.
                if c.address.startswith("169.254."):
                    continue
                adresses.add(c.address)
    except Exception:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None,
                                           socket.AF_INET):
                adresses.add(info[4][0])
        except Exception:
            pass
    return adresses


def _certificat_couvre(chemin, adresses):
    """Le certificat existant vaut-il encore pour toutes ces adresses ?"""
    try:
        from cryptography import x509
        c = x509.load_pem_x509_certificate(chemin.read_bytes())
        fin = c.not_valid_after_utc
        import datetime
        if fin < datetime.datetime.now(datetime.timezone.utc):
            return False
        connues = {str(v) for v in
                   c.extensions.get_extension_for_class(
                       x509.SubjectAlternativeName).value.get_values_for_type(
                           x509.IPAddress)}
        return set(adresses) <= connues
    except Exception:
        # Dans le doute on refabrique : cela coute une seconde.
        return False

def _certificat():
    """Chemin d'un certificat auto-signe, cree au besoin.

    Les navigateurs exigent une page securisee pour donner acces au micro.
    Un certificat auto-signe suffit : il faudra accepter l'avertissement une
    fois sur le telephone, puis la reconnaissance vocale fonctionnera.
    """
    dossier = Path(__file__).parent
    # Un certificat delivre par le reseau prive vaut mieux que le notre : il
    # est reconnu par les navigateurs, donc plus d avertissement de securite.
    # On ne le fabrique pas ici ; s il est la, on s en sert.
    _renouveler_certificat_reseau(dossier)
    vrai = dossier / "ts_cert.pem"
    if vrai.exists() and (dossier / "ts_key.pem").exists():
        return vrai

    cert = dossier / "hud_cert.pem"
    if cert.exists():
        # Une interface ajoutee depuis — un reseau prive, par exemple — ne
        # figure pas dans un certificat deja ecrit : on le refait alors.
        if _certificat_couvre(cert, _adresses_locales()):
            return cert
        try:
            cert.unlink()
        except Exception:
            return cert

    try:
        import datetime
        import ipaddress
        import socket as _s

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except Exception:
        return None

    adresses = _adresses_locales()

    cle = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nom = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Jarvis HUD")])
    autres = [x509.DNSName("localhost")]
    for a in adresses:
        try:
            autres.append(x509.IPAddress(ipaddress.ip_address(a)))
        except Exception:
            continue

    maintenant = datetime.datetime.now(datetime.timezone.utc)
    certificat = (
        x509.CertificateBuilder()
        .subject_name(nom).issuer_name(nom)
        .public_key(cle.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(maintenant - datetime.timedelta(days=1))
        .not_valid_after(maintenant + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(autres), critical=False)
        .sign(cle, hashes.SHA256())
    )

    with open(cert, "wb") as f:
        f.write(cle.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()))
        f.write(certificat.public_bytes(serialization.Encoding.PEM))
    return cert


def demarrer(ouvrir=True):
    """Lance le serveur dans un thread daemon et ouvre le navigateur.

    Sans effet si le serveur tourne deja. Renvoie l'instance du serveur.
    """
    global _SERVEUR
    if _SERVEUR is not None:
        return _SERVEUR

    _SERVEUR = _Serveur((HOTE, PORT), _Poignee)
    _SERVEUR.daemon_threads = True

    # Second serveur, chiffre, pour que le micro du telephone soit autorise.
    global _SERVEUR_HTTPS, PORT_HTTPS
    if HTTPS:
        cert = _certificat()
        if cert is not None:
            try:
                import ssl
                PORT_HTTPS = PORT + 1
                _SERVEUR_HTTPS = _Serveur((HOTE, PORT_HTTPS), _Poignee)
                _SERVEUR_HTTPS.daemon_threads = True

                # core/llm.py injecte truststore, qui remplace ssl.SSLContext
                # par une version pensee pour le client : elle echoue cote
                # serveur. On la met de cote pendant la creation du contexte,
                # puis on la remet en place pour ne rien casser ailleurs.
                try:
                    import truststore
                    truststore.extract_from_ssl()
                except Exception:
                    truststore = None
                try:
                    contexte = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    # Le notre porte cle et certificat dans un seul fichier ;
                    # celui du reseau prive les separe. On fournit donc la cle
                    # a part quand elle existe.
                    cle = cert.parent / "ts_key.pem"
                    if cert.name == "ts_cert.pem" and cle.exists():
                        contexte.load_cert_chain(certfile=str(cert),
                                                 keyfile=str(cle))
                    else:
                        contexte.load_cert_chain(certfile=str(cert))
                finally:
                    if truststore is not None:
                        try:
                            truststore.inject_into_ssl()
                        except Exception:
                            pass
                _SERVEUR_HTTPS.socket = contexte.wrap_socket(
                    _SERVEUR_HTTPS.socket, server_side=True)
                threading.Thread(target=_SERVEUR_HTTPS.serve_forever,
                                 daemon=True).start()
            except Exception as e:
                print(f"  [HUD] HTTPS indisponible ({e}).")
                _SERVEUR_HTTPS, PORT_HTTPS = None, None

    thread = threading.Thread(target=_SERVEUR.serve_forever, daemon=True)
    thread.start()

    print(f"HUD sur http://127.0.0.1:{PORT}/" + ("  (visible sur le reseau)" if HOTE == "0.0.0.0" else ""))
    print(f"     MU-TH-UR sur http://127.0.0.1:{PORT}/mother")
    if HOTE == "0.0.0.0":
        import socket as _s
        try:
            _c = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
            _c.connect(("192.168.1.1", 80))
            _ip = _c.getsockname()[0]
            _c.close()
            if PORT_HTTPS:
                print(f"     Telephone sur https://{_ip}:{PORT_HTTPS}/tel")
            else:
                print(f"     Telephone sur http://{_ip}:{PORT}/tel"
                      "   (micro indisponible sans HTTPS)")
        except Exception:
            pass
    if ouvrir:
        try:
            webbrowser.open(f"http://127.0.0.1:{PORT}/")
        except Exception:
            pass
    return _SERVEUR


# ---------------------------------------------------------------- demonstration


def _scenario():
    """Joue une conversation type en boucle pour tester le rendu."""
    import math

    config("qwen3.5:4b", "whisper medium")

    tours = [
        ("allume la lumiere de la chambre",
         "allumer_lumiere", "chambre -> on",
         "C'est fait, la chambre est allumee."),
        ("mets le salon en bleu",
         "changer_couleur", "salon -> bleu",
         "Voila, le salon passe en bleu."),
        ("quelle heure est-il",
         "heure_et_date", "",
         "Il est vingt-deux heures dix, le mardi cinq aout."),
        ("baisse la chambre a trente pour cent",
         "regler_luminosite", "chambre -> 30%",
         "La chambre est reglee a trente pour cent."),
    ]

    pas = 0
    while True:
        for question, nom_outil, detail, reponse in tours:
            # Veille : le fond respire doucement.
            etat(VEILLE)
            for _ in range(24):
                pas += 1
                niveau(0.04 + 0.03 * (0.5 + 0.5 * math.sin(pas * 0.15)))
                time.sleep(0.05)

            # Ecoute : le niveau du micro grimpe pendant que l'on parle.
            etat(ECOUTE)
            dire_vous(question)
            for i in range(36):
                pas += 1
                base = 0.35 + 0.35 * abs(math.sin(i * 0.35))
                niveau(base + 0.1 * math.sin(pas * 0.9))
                time.sleep(0.045)
            niveau(0.05)

            # Reflexion : appel d'outil.
            etat(REFLEXION)
            if nom_outil:
                time.sleep(0.4)
                outil(nom_outil, detail)
            time.sleep(0.9)

            # Parole : reponse en ambre.
            etat(PAROLE)
            dire_jarvis(reponse)
            for i in range(30):
                pas += 1
                niveau(0.3 + 0.25 * abs(math.sin(pas * 0.6)))
                time.sleep(0.05)
            niveau(0.05)
            time.sleep(0.5)


if __name__ == "__main__":
    demarrer()
    print("Scenario de demonstration en boucle. Ctrl+C pour quitter.")
    try:
        _scenario()
    except KeyboardInterrupt:
        print("\nArret.")
