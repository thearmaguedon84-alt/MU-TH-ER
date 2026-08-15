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
import queue
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
# HTTPS : necessaire pour que le micro fonctionne sur un telephone.
HTTPS = False
PROTOCOLE = "http"
_FICHIER_HTML = Path(__file__).parent / "hud.html"
# Interface alternative MU-TH-UR (Nostromo), servie sur /mother.
# Elle consomme exactement le meme flux : rien d'autre ne change.
_FICHIER_MOTHER = Path(__file__).parent / "hud_mother.html"
# Page allegee pour telephone, servie sur /tel.
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


# ---------------------------------------------------------------- serveur


class _Poignee(BaseHTTPRequestHandler):
    """Sert la page et le flux SSE. Le reste renvoie 404."""

    def log_message(self, *args):
        pass  # pas de bruit dans la console

    def do_GET(self):
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
        else:
            self.send_error(404)

    def do_POST(self):
        """Reception d'une commande envoyee par la page mobile."""
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


def _certificat():
    """Chemin d'un certificat auto-signe, cree au besoin.

    Les navigateurs exigent une page securisee pour donner acces au micro.
    Un certificat auto-signe suffit : il faudra accepter l'avertissement une
    fois sur le telephone, puis la reconnaissance vocale fonctionnera.
    """
    dossier = Path(__file__).parent
    cert = dossier / "hud_cert.pem"
    if cert.exists():
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

    # Toutes les adresses de la machine, pour que le certificat soit valable
    # quelle que soit celle utilisee par le telephone.
    adresses = {"127.0.0.1"}
    try:
        for info in _s.getaddrinfo(_s.gethostname(), None, _s.AF_INET):
            adresses.add(info[4][0])
    except Exception:
        pass

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

    # HTTPS si demande : sans page securisee, le micro du telephone reste muet.
    global PROTOCOLE
    if HTTPS:
        cert = _certificat()
        if cert is not None:
            try:
                import ssl
                contexte = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                contexte.load_cert_chain(certfile=str(cert))
                _SERVEUR.socket = contexte.wrap_socket(_SERVEUR.socket,
                                                       server_side=True)
                PROTOCOLE = "https"
            except Exception as e:
                print(f"  [HUD] HTTPS indisponible ({e}), on reste en clair.")

    thread = threading.Thread(target=_SERVEUR.serve_forever, daemon=True)
    thread.start()

    print(f"HUD sur {PROTOCOLE}://127.0.0.1:{PORT}/" + ("  (visible sur le reseau)" if HOTE == "0.0.0.0" else ""))
    print(f"     MU-TH-UR sur {PROTOCOLE}://127.0.0.1:{PORT}/mother")
    if HOTE == "0.0.0.0":
        import socket as _s
        try:
            _c = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
            _c.connect(("192.168.1.1", 80))
            _ip = _c.getsockname()[0]
            _c.close()
            print(f"     Telephone sur {PROTOCOLE}://{_ip}:{PORT}/tel")
        except Exception:
            pass
    if ouvrir:
        try:
            webbrowser.open(f"{PROTOCOLE}://127.0.0.1:{PORT}/")
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
