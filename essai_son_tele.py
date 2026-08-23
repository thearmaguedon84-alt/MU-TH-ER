"""Le televiseur accepte-t-il de jouer un son sans qu'on ait clique ?

C'est la question qui decide de tout. Si oui, la voix peut suivre l'interface
diffusee. Si non, aucun arrangement du code n'y changera rien, et il faudra
une autre voie.

On sert une page d'essai sur un port a part — inutile de redemarrer Jarvis —
et on la fait charger par la television. La page essaie deux choses : un
element audio, et une synthese immediate. Elle affiche a l'ecran ce qui a ete
accepte, de sorte que le verdict se lit sans meme tendre l'oreille.
"""
import http.server
import io
import math
import socketserver
import struct
import sys
import threading
import time
import wave

sys.path.insert(0, ".")

PORT = 8790

PAGE = """<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<title>Essai de son</title><style>
body{margin:0;background:#020806;color:#35ff6a;font:700 5vw/1.5 monospace;
display:flex;flex-direction:column;align-items:center;justify-content:center;
height:100vh;text-align:center}
#e{font-size:2.6vw;opacity:.85;margin-top:4vh;font-weight:400}
</style></head><body>
<div>ESSAI DE SON</div>
<div id="e">demarrage…</div>
<audio id="a" autoplay loop src="/ton.wav"></audio>
<script>
var e = document.getElementById("e"), a = document.getElementById("a");
var lignes = [];
function dire(t){ lignes.push(t); e.innerHTML = lignes.join("<br>"); }

a.volume = 1;
var p = a.play();
if (p && p.then) {
  p.then(function(){ dire("ELEMENT AUDIO : ACCEPTE"); })
   .catch(function(err){ dire("ELEMENT AUDIO REFUSE : " + err.name); });
} else { dire("ELEMENT AUDIO : lance"); }

try {
  var C = window.AudioContext || window.webkitAudioContext;
  var c = new C();
  var o = c.createOscillator(), g = c.createGain();
  o.frequency.value = 440; g.gain.value = 0.12;
  o.connect(g); g.connect(c.destination); o.start();
  setInterval(function(){ o.frequency.value = (o.frequency.value === 440) ? 660 : 440; }, 700);
  setTimeout(function(){ dire("CONTEXTE AUDIO : " + c.state.toUpperCase()); }, 1500);
} catch (err) { dire("OSCILLATEUR REFUSE"); }
</script></body></html>"""


def ton():
    """Deux notes alternees, bien audibles."""
    taux, duree = 22050, 4.0
    tampon = io.BytesIO()
    with wave.open(tampon, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(taux)
        donnees = bytearray()
        for i in range(int(taux * duree)):
            f0 = 523 if (i // (taux // 2)) % 2 == 0 else 784
            v = int(12000 * math.sin(2 * math.pi * f0 * i / taux))
            donnees += struct.pack("<h", v)
        f.writeframes(bytes(donnees))
    return tampon.getvalue()


AUDIO = ton()


class Poignee(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        chemin = self.path.split("?")[0]
        if chemin == "/ton.wav":
            corps, mime = AUDIO, "audio/wav"
        else:
            corps, mime = PAGE.encode("utf-8"), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)


def main():
    socketserver.TCPServer.allow_reuse_address = True
    serveur = socketserver.TCPServer(("0.0.0.0", PORT), Poignee)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    print("serveur d essai sur le port", PORT)

    from tools.cast import _adresse_pour, _choisir
    appareil = _choisir("Tv en bas")
    if appareil is None:
        print("televiseur introuvable")
        return
    hote = appareil.cast_info.host
    url = f"http://{_adresse_pour(hote)}:{PORT}/"
    print("adresse envoyee :", url)

    from pychromecast.controllers.dashcast import DashCastController
    appareil.wait(timeout=12)
    c = DashCastController()
    appareil.register_handler(c)
    time.sleep(1)
    c.load_url(url, force=True)
    print("page envoyee sur la television — laisse-la afficher une minute")
    time.sleep(150)
    serveur.shutdown()
    print("serveur d essai arrete")


if __name__ == "__main__":
    main()
