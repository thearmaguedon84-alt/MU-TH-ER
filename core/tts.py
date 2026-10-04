"""Abstraction de la synthese vocale (TTS) : cloud ou local, meme interface.

Chaque provider expose `synthetiser(texte)` qui renvoie (audio_int16, frequence)
ou None. jarvis14 se charge de JOUER l'audio (avec sa gestion d'interruption) et
retombe sur la voix Windows (SAPI) si le provider renvoie None.

  - ElevenLabsProvider : cloud (qualite max), voix configurable.
  - PiperProvider      : local, 100% offline, voix francaise Piper (.onnx).

Choix par config.yaml (mode: cloud | local). En local sans modele Piper, ou en
cloud sans cle ElevenLabs, on retombe proprement sur SAPI.

Note honnete sur le TTS local francais : Piper est recommande (voix FR eprouvees
comme fr_FR-siwis / fr_FR-tom, tres leger, temps reel sur CPU). Kokoro (kokoro-onnx)
ne propose qu'une voix FR recente et de qualite moyenne ; Piper est un meilleur
choix pour le francais aujourd'hui.
"""
import json
import logging
import urllib.request
from pathlib import Path

# Magasin de certificats Windows (Malwarebytes intercepte le TLS : sans ca, l'appel
# a l'API ElevenLabs echoue et Jarvis retombe sur la voix Windows).
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

from core.config import reglage

LOG = logging.getLogger("jarvis")
_RACINE = Path(__file__).resolve().parent.parent


class ProviderTTS:
    nom = "?"

    def disponible(self):
        return True

    def synthetiser(self, texte):
        """Renvoie (numpy int16 mono, frequence_hz) ou None si indisponible."""
        return None


# --------------------------------------------------------------- ElevenLabs

class ElevenLabsProvider(ProviderTTS):
    nom = "ElevenLabs"

    def __init__(self):
        self.cle = reglage("elevenlabs.cle", "")
        self.voix = reglage("elevenlabs.voix", "")
        self.modele = reglage("elevenlabs.modele", "eleven_flash_v2_5")
        self._voix_resolue = None

    def disponible(self):
        return bool(self.cle)

    def _resoudre_voix(self):
        if self.voix:
            return self.voix
        if self._voix_resolue:
            return self._voix_resolue
        try:
            requete = urllib.request.Request(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": self.cle})
            with urllib.request.urlopen(requete, timeout=6) as reponse:
                d = json.loads(reponse.read().decode("utf-8"))
            self._voix_resolue = d["voices"][0]["voice_id"]
        except Exception:
            self._voix_resolue = "21m00Tcm4TlvDq8ikWAM"   # Rachel, par defaut
        return self._voix_resolue

    def synthetiser(self, texte):
        try:
            import miniaudio
            import numpy as np
        except ImportError:
            return None
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self._resoudre_voix()}"
        charge = {"text": texte, "model_id": self.modele}
        # Flash/Turbo v2.5 acceptent language_code : on force le francais pour une
        # bonne prononciation des accents (e accent, c cedille...) quelle que soit
        # la voix (sinon la langue est auto-detectee et parfois lue en anglais).
        if any(x in self.modele for x in ("flash", "turbo")):
            charge["language_code"] = reglage("elevenlabs.langue", "fr")
        corps = json.dumps(charge).encode("utf-8")
        requete = urllib.request.Request(url, data=corps, method="POST", headers={
            "xi-api-key": self.cle, "Content-Type": "application/json",
            "Accept": "audio/mpeg"})
        try:
            with urllib.request.urlopen(requete, timeout=15) as reponse:
                mp3 = reponse.read()
            decode = miniaudio.decode(
                mp3, nchannels=1, sample_rate=24000,
                output_format=miniaudio.SampleFormat.SIGNED16)
            return np.frombuffer(decode.samples, dtype=np.int16), 24000
        except Exception as e:
            print(f"  [ElevenLabs] indisponible ({e}), repli voix Windows.")
            return None


# --------------------------------------------------------------- Piper (local)

class PiperProvider(ProviderTTS):
    nom = "Piper"

    def __init__(self):
        self.modele = reglage("piper.modele", "")
        self._voix = None

    def _chemin(self):
        if not self.modele:
            # a defaut, prend le premier .onnx trouve dans voix/
            trouves = list((_RACINE / "voix").glob("*.onnx"))
            return trouves[0] if trouves else None
        p = Path(self.modele)
        return p if p.is_absolute() else (_RACINE / p)

    def disponible(self):
        c = self._chemin()
        return bool(c and c.exists())

    def synthetiser(self, texte):
        try:
            import numpy as np
            from piper import PiperVoice
        except ImportError:
            print("  [Piper] librairie piper-tts absente.")
            return None
        chemin = self._chemin()
        if chemin is None or not chemin.exists():
            print("  [Piper] aucun modele de voix (.onnx) dans voix/. Voir docs.")
            return None
        try:
            if self._voix is None:
                self._voix = PiperVoice.load(str(chemin))
            brut = b"".join(self._voix.synthesize_stream_raw(texte))
            return np.frombuffer(brut, dtype=np.int16), self._voix.config.sample_rate
        except Exception as e:
            print(f"  [Piper] echec ({e}), repli voix Windows.")
            return None


# --------------------------------------------------------------- Kokoro (local)

class KokoroProvider(ProviderTTS):
    nom = "Kokoro"

    def __init__(self):
        self.modele = reglage("kokoro.modele", "")
        self.voix = reglage("kokoro.voix", "")
        self.voix_nom = reglage("kokoro.voix_nom", "ff_siwis")
        self._k = None

    def disponible(self):
        return bool(self.modele and Path(self.modele).exists())

    def synthetiser(self, texte):
        try:
            import numpy as np
            from kokoro_onnx import Kokoro
        except ImportError:
            print("  [Kokoro] librairie absente. Installe : uv add kokoro-onnx")
            return None
        if not (self.modele and Path(self.modele).exists()):
            print("  [Kokoro] modele introuvable (kokoro.modele). Voir docs/local.md.")
            return None
        try:
            if self._k is None:
                self._k = Kokoro(self.modele, self.voix)
            samples, freq = self._k.create(texte, voice=self.voix_nom, speed=1.0, lang="fr-fr")
            audio = (np.asarray(samples) * 32767).astype(np.int16)
            return audio, freq
        except Exception as e:
            print(f"  [Kokoro] echec ({e}), repli voix Windows.")
            return None




# --------------------------------------------------------------- Edge TTS (Microsoft neural)

def effet_ordinateur(echantillons, taux, intensite=1.0):
    """Donne a une voix naturelle le grain d'un interphone de vaisseau.

    Trois etages : une bande passante etroite (comme une liaison radio), une
    saturation douce qui durcit les attaques, puis une tres legere modulation
    en anneau qui apporte le cote metallique. L'ensemble reste intelligible :
    c'est un habillage, pas un vocodeur.
    """
    import numpy as np
    from scipy.signal import butter, sosfilt

    x = echantillons.astype(np.float32) / 32768.0
    sec = float(np.sqrt(np.mean(x ** 2))) or 1e-6

    # 1. Bande passante 320 Hz - 3200 Hz : le timbre « haut-parleur de bord »
    nyq = taux / 2.0
    sos = butter(4, [320.0 / nyq, min(3200.0, nyq * 0.98) / nyq],
                 btype="band", output="sos")
    y = sosfilt(sos, x)

    # 2. Saturation douce : durcit sans distordre franchement
    gain = 1.0 + 2.2 * intensite
    y = np.tanh(y * gain) / np.tanh(gain)

    # 3. Modulation en anneau tres discrete (~55 Hz) : grain electronique
    t = np.arange(len(y), dtype=np.float32) / taux
    y *= (1.0 - 0.10 * intensite) + 0.10 * intensite * np.sin(2 * np.pi * 55.0 * t)

    # Reaccorder le volume sur l'original, sans saturer
    apres = float(np.sqrt(np.mean(y ** 2))) or 1e-6
    y *= sec / apres
    crete = float(np.max(np.abs(y))) or 1.0
    if crete > 0.99:
        y *= 0.99 / crete

    return (y * 32767.0).astype(np.int16)


class EdgeTTSProvider(ProviderTTS):
    nom = "EdgeTTS"

    def __init__(self):
        self.voix = reglage("edge.voix", "fr-FR-DeniseNeural")
        # Voix distincte du mode MU-TH-UR : plus lente et plus grave, pour un
        # rendu d'ordinateur de bord. Modifiable dans config.yaml.
        self.voix_mere = reglage("edge.voix_mere", "fr-CH-ArianeNeural")
        self.debit_mere = reglage("edge.debit_mere", "-16%")
        self.hauteur_mere = reglage("edge.hauteur_mere", "-22Hz")
        self.effet_mere = float(reglage("edge.effet_mere", 1.0))

    def _reglage_voix(self):
        """Voix et prosodie selon la personnalite active, relue a chaque phrase."""
        if reglage("assistant.personnalite", "") == "mere":
            return self.voix_mere, self.debit_mere, self.hauteur_mere
        return self.voix, "+0%", "+0Hz"

    def disponible(self):
        try:
            import edge_tts  # noqa
            return True
        except ImportError:
            return False

    def synthetiser(self, texte):
        try:
            import asyncio
            import miniaudio
            import numpy as np
            import edge_tts

            voix, debit, hauteur = self._reglage_voix()

            async def _synth():
                comm = edge_tts.Communicate(texte, voix, rate=debit, pitch=hauteur)
                audio = b""
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        audio += chunk["data"]
                return audio

            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    raise RuntimeError
                mp3 = loop.run_until_complete(_synth())
            except (RuntimeError, Exception):
                mp3 = asyncio.run(_synth())

            decoded = miniaudio.decode(
                mp3, nchannels=1, sample_rate=24000,
                output_format=miniaudio.SampleFormat.SIGNED16
            )
            ech = np.frombuffer(decoded.samples, dtype=np.int16)
            # En mode MU-TH-UR seulement : le reste du temps la voix est intacte.
            if self.effet_mere > 0 and reglage("assistant.personnalite", "") == "mere":
                try:
                    ech = effet_ordinateur(ech, 24000, self.effet_mere)
                except Exception as err:
                    print(f"  [voix] effet ignore ({err})")
            return ech, 24000
        except Exception as e:
            print(f"  [EdgeTTS] echec ({e}), repli voix Windows.")
            return None
# --------------------------------------------------------------- fabrique

_TTS = None


def tts():
    """Provider TTS courant.
    - cloud : ElevenLabs
    - local + voix_locale=elevenlabs : ElevenLabs (cle requise) avec LLM Ollama
    - local + voix_locale=kokoro     : Kokoro (offline)
    - local (defaut)                 : Piper (offline)
    """
    global _TTS
    if _TTS is None:
        mode = (reglage("mode", "cloud") or "cloud").lower()
        if mode == "local":
            moteur = (reglage("voix_locale", "piper") or "piper").lower()
            if moteur == "elevenlabs":
                _TTS = ElevenLabsProvider()
            elif moteur == "edge":
                _TTS = EdgeTTSProvider()
            elif moteur == "kokoro":
                _TTS = KokoroProvider()
            else:
                _TTS = PiperProvider()
        else:
            _TTS = ElevenLabsProvider()
        LOG.info("provider TTS : %s (mode %s, moteur %s)", _TTS.nom, mode,
                 reglage("voix_locale", "piper"))
    return _TTS
