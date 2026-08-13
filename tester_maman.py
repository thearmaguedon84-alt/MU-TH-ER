"""Verifie ce que Whisper entend quand TU dis « Maman ».

Usage :  .venv\\Scripts\\python.exe tester_maman.py

Le script ecoute le micro, decoupe ce que tu dis en segments comme le fait le
detecteur, et affiche la transcription brute de chacun. Si ta prononciation
ressort systematiquement autrement que « maman », ajoute la forme entendue dans
config.yaml sous reveil_maman.mots, puis relance Jarvis.
"""
import sys
import time

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

TAUX = 16000
BLOC = 1280
SEUIL = 0.020
DUREE_MIN, DUREE_MAX = 0.25, 1.9


def main():
    print("Chargement du modele tiny...")
    modele = WhisperModel("tiny", device="cpu", compute_type="int8", cpu_threads=2)

    print()
    print("Dis « Maman » plusieurs fois, normalement, a ta distance habituelle.")
    print("Essaie aussi des phrases ordinaires pour reperer les faux declenchements.")
    print("Ctrl+C pour arreter.")
    print()

    segment, silences, avant = [], 0, []
    connus = ("maman", "mamans", "mamant", "manman", "mamane", "mother", "muthur")

    flux = sd.InputStream(samplerate=TAUX, channels=1, dtype="float32",
                          blocksize=BLOC)
    flux.start()
    try:
        while True:
            bloc, _ = flux.read(BLOC)
            bloc = bloc.flatten()
            niveau = float(np.sqrt(np.mean(bloc ** 2)))

            if niveau > SEUIL:
                if not segment:
                    segment = list(avant)
                segment.append(bloc)
                silences = 0
                continue

            avant.append(bloc)
            if len(avant) > 3:
                avant.pop(0)
            if not segment:
                continue

            silences += 1
            if silences < 3:
                segment.append(bloc)
                continue

            audio = np.concatenate(segment)
            segment, silences = [], 0
            duree = len(audio) / TAUX

            if not (DUREE_MIN <= duree <= DUREE_MAX):
                print(f"  [ignore] segment de {duree:.2f}s "
                      f"(hors de la plage {DUREE_MIN}-{DUREE_MAX}s)")
                continue

            t0 = time.time()
            segs, _ = modele.transcribe(audio, language="fr", beam_size=1,
                                        without_timestamps=True,
                                        condition_on_previous_text=False)
            texte = " ".join(s.text for s in segs).strip()
            ms = (time.time() - t0) * 1000

            mots = "".join(c if c.isalpha() or c == " " else " "
                           for c in texte.lower()).split()
            reconnu = any(m in connus for m in mots) and len(mots) <= 3
            marque = "  >>> DECLENCHE" if reconnu else ""
            print(f"  {duree:.2f}s  {ms:4.0f}ms  {texte!r}{marque}")

    except KeyboardInterrupt:
        print("\nArret.")
    finally:
        flux.stop()
        flux.close()


if __name__ == "__main__":
    main()
