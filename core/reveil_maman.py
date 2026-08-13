"""Second mot de reveil « Maman », en complement d'openWakeWord.

Pourquoi ce module existe
-------------------------
openWakeWord ne fonctionne qu'avec des modeles acoustiques pre-entraines, et il
n'en existe aucun pour « maman ». L'entrainement d'un modele sur mesure est
bloque sous Windows (la chaine officielle est Linux seulement). On detecte donc
le mot par transcription, mais avec trois precautions qui font toute la
difference avec la version qui avait fige la machine :

  1. Whisper « tiny » sur CPU (123 ms par segment), pas « medium » sur GPU ;
  2. seulement sur les segments ou l'on entend effectivement parler, jamais en
     continu sur chaque bloc audio ;
  3. dans un fil separe : la boucle audio principale n'attend jamais. Si le fil
     prend du retard, les segments sont abandonnes plutot que mis en attente.

En cas de probleme quelconque (modele absent, exception), le detecteur se
desactive tout seul et Jarvis continue normalement avec « Hey Jarvis ».
"""
import logging
import queue
import re
import threading
import time
import unicodedata

import numpy as np

LOG = logging.getLogger("jarvis")

TAUX = 16000


def _plat(texte):
    """Minuscules, sans accents ni ponctuation."""
    t = unicodedata.normalize("NFD", (texte or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z ]", " ", t).split()


class DetecteurMaman:
    """Ecoute les blocs audio et signale la prononciation de « Maman »."""

    def __init__(self, actif=True, mots=("maman", "mamans", "mamant", "manman", "mamane",
                          "mother", "muthur"),
                 seuil_voix=0.020, duree_min=0.25, duree_max=6.0,
                 mots_max=3, garde=3.0, modele="tiny",
                 blocs_silence=6):
        self.actif = bool(actif)
        self.mots = tuple(m.lower() for m in mots)
        self.seuil_voix = float(seuil_voix)
        self.duree_min = float(duree_min)
        self.duree_max = float(duree_max)
        self.mots_max = int(mots_max)      # au-dela, c'est une phrase, pas un appel
        self.garde = float(garde)          # silence impose apres un declenchement
        # Nombre de blocs de 80 ms de silence qui terminent un segment.
        # Il en faut assez pour franchir la virgule de "Maman, lance X",
        # sinon le segment est coupe en deux et la commande est perdue.
        self.blocs_silence = int(blocs_silence)
        self.nom_modele = modele

        self._modele = None
        self._file = queue.Queue(maxsize=2)
        self._signal = threading.Event()
        self._dernier = 0.0
        self._audio_commande = None   # audio du segment si une commande suit

        # Accumulation du segment en cours
        self._segment = []
        self._silences = 0
        self._avant = []                   # pre-tampon, evite de couper l'attaque

        if self.actif:
            threading.Thread(target=self._boucle, daemon=True).start()

    # ------------------------------------------------------------ cote micro

    def alimenter(self, bloc):
        """Recoit un bloc audio float32. Ne bloque jamais, ne leve jamais."""
        if not self.actif:
            return
        try:
            self._accumuler(bloc)
        except Exception:
            LOG.debug("reveil_maman: bloc ignore", exc_info=True)

    def _accumuler(self, bloc):
        niveau = float(np.sqrt(np.mean(bloc ** 2)))

        if niveau > self.seuil_voix:
            if not self._segment:
                # Debut de parole : on repart du pre-tampon
                self._segment = list(self._avant)
            self._segment.append(bloc)
            self._silences = 0
            # Segment deja trop long pour etre un simple appel : on abandonne
            if len(self._segment) * len(bloc) > self.duree_max * TAUX * 1.5:
                self._segment = []
            return

        # Silence
        self._avant.append(bloc)
        if len(self._avant) > 3:
            self._avant.pop(0)

        if not self._segment:
            return
        self._silences += 1
        if self._silences < self.blocs_silence:
            self._segment.append(bloc)
            return

        segment, self._segment, self._silences = self._segment, [], 0
        duree = sum(len(b) for b in segment) / TAUX
        if not (self.duree_min <= duree <= self.duree_max):
            return
        try:
            self._file.put_nowait(np.concatenate(segment))
        except queue.Full:
            pass                            # analyseur en retard : on laisse tomber

    # ------------------------------------------------------------ cote analyse

    def _charger(self):
        from faster_whisper import WhisperModel
        # CPU volontairement : le GPU reste libre pour la transcription principale.
        return WhisperModel(self.nom_modele, device="cpu", compute_type="int8",
                            cpu_threads=2)

    def _boucle(self):
        try:
            self._modele = self._charger()
        except Exception as e:
            LOG.warning("reveil_maman desactive (modele indisponible : %s)", e)
            self.actif = False
            return

        while True:
            audio = self._file.get()
            try:
                if time.time() - self._dernier < self.garde:
                    continue
                segments, _ = self._modele.transcribe(
                    audio.astype(np.float32), language="fr", beam_size=1,
                    without_timestamps=True, condition_on_previous_text=False)
                mots = _plat(" ".join(s.text for s in segments))
                if not mots:
                    continue

                # Le mot de reveil doit ouvrir le segment : « maman » au milieu
                # d'une phrase ("j'ai appele ma maman") ne doit rien declencher.
                if mots[0] not in self.mots:
                    continue

                self._dernier = time.time()
                # « Maman » seul dure environ 0,8 s. Au-dela, il y a autre chose
                # dans le segment : on garde l'audio pour que la boucle le
                # retranscrive proprement avec le modele medium. On se fie a la
                # duree autant qu'au nombre de mots, car « tiny » tronque
                # parfois la fin de la phrase.
                suite = len(mots) > self.mots_max or len(audio) / TAUX > 1.25
                self._audio_commande = audio if suite else None
                self._signal.set()
            except Exception:
                LOG.debug("reveil_maman: segment ignore", exc_info=True)

    # ------------------------------------------------------------ cote boucle

    def declenche(self):
        """Vrai une seule fois si « Maman » vient d'etre entendu."""
        if self._signal.is_set():
            self._signal.clear()
            return True
        return False

    def recuperer_audio(self):
        """Audio du segment si une commande suivait le mot de reveil, sinon None."""
        audio, self._audio_commande = self._audio_commande, None
        return audio

    def vider(self):
        """Oublie le segment en cours (apres un reveil par un autre moyen)."""
        self._segment = []
        self._silences = 0
        self._signal.clear()
