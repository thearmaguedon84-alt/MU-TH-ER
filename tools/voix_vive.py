"""Une voix clonee pour la conversation : le modele reste charge.

Les dessins animes lancent le moteur de voix a chaque film, et il met une
trentaine de secondes a charger son modele. Pour une replique de film, c est
acceptable ; pour repondre a quelqu un, non. Ici un petit serveur tourne dans
l environnement du moteur, charge le modele une fois, et dit chaque phrase en
quelques secondes.

Il ne demarre que lorsqu on en a besoin (personnalite Gerald), se ferme quand
on change de personnalite, et cede sa place sur la carte graphique a une
generation : core.vram l arrete comme les autres moteurs. Tant qu il n est
pas pret, on parle avec la voix de synthese : on ne fait jamais attendre.
"""
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from pathlib import Path

LOG = logging.getLogger("jarvis")

SERVEUR = r'''
import sys, json, os, wave, re
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("COQUI_TOS_AGREED", "1")
import numpy as np
import torch
from TTS.tts.configs.xtts_config import XttsConfig
from TTS.tts.models.xtts import Xtts

d = json.loads(sys.argv[1])
racine = os.path.join(os.path.expanduser("~"), "AppData", "Local", "tts",
                      "tts_models--multilingual--multi-dataset--xtts_v2")
config = XttsConfig()
config.load_json(os.path.join(racine, "config.json"))
m = Xtts.init_from_config(config)
m.load_checkpoint(config, checkpoint_path=d["modele"] or os.path.join(racine, "model.pth"),
                  vocab_path=os.path.join(racine, "vocab.json"), use_deepspeed=False)
m.cuda().eval()
gpt, orateur = m.get_conditioning_latents(audio_path=[d["extrait"]], gpt_cond_len=30,
                                          max_ref_length=60)


def morceaux(texte, taille=190):
    texte = " ".join(texte.split())
    if len(texte) <= taille:
        return [texte] if texte else []
    sortie, ligne = [], ""
    for bout in re.split(r"(?<=[.!?,;:])\s+", texte):
        if ligne and len(ligne) + 1 + len(bout) > taille:
            sortie.append(ligne)
            ligne = bout
        else:
            ligne = (ligne + " " + bout).strip()
    if ligne:
        sortie.append(ligne)
    return sortie


print("PRET", flush=True)
for ligne in sys.stdin:
    ligne = ligne.strip()
    if not ligne or ligne == "STOP":
        break
    try:
        q = json.loads(ligne)
        ondes = []
        for bout in morceaux(q["texte"]):
            with torch.inference_mode():
                onde = m.inference(bout, "fr", gpt, orateur, temperature=0.95,
                                   repetition_penalty=8.0)["wav"]
                # Coupee trop tot : on redit avec la penalite ordinaire.
                if len(bout) > 15 and len(onde) / 24000.0 < len(bout) / 30.0:
                    autre = m.inference(bout, "fr", gpt, orateur, temperature=0.85,
                                        repetition_penalty=2.0)["wav"]
                    if len(autre) > len(onde):
                        onde = autre
            ondes.append(np.asarray(onde, dtype=np.float32))
            ondes.append(np.zeros(int(0.12 * 24000), dtype=np.float32))
        x = np.concatenate(ondes) if ondes else np.zeros(1, dtype=np.float32)
        x = x / (np.abs(x).max() + 1e-6) * 0.95
        with wave.open(q["sortie"], "wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(24000)
            f.writeframes((x * 32767).astype("<i2").tobytes())
        print("OK " + q["sortie"], flush=True)
    except Exception as e:
        print("ERR " + repr(e)[:200], flush=True)
'''

_VERROU = threading.Lock()
_ETAT = {"proc": None, "qui": None, "pret": False, "demarrage": False}


def _dossier():
    d = Path(tempfile.gettempdir()) / "jarvis_voix_vive"
    d.mkdir(exist_ok=True)
    return d


def pret(qui):
    """Vrai si le serveur de ce personnage repond deja."""
    p = _ETAT["proc"]
    return (_ETAT["pret"] and _ETAT["qui"] == qui and p is not None
            and p.poll() is None)


def demarrer(qui):
    """Lance le serveur en arriere-plan (une trentaine de secondes)."""
    if pret(qui) or (_ETAT["demarrage"] and _ETAT["qui"] == qui):
        return
    arreter()
    # Une generation occupe la carte : on ne lui dispute pas la memoire.
    try:
        from core.file_gpu import en_cours
        if en_cours():
            return
    except Exception:
        pass
    from tools import voix_clonee as V
    extrait = V.extrait_de(qui)
    if not extrait or not Path(V.MOTEUR).exists():
        return
    fin = None if V._plat(qui) in V.SANS_MODELE_FIN else V.modele_fin(qui)
    script = _dossier() / "serveur.py"
    script.write_text(SERVEUR, encoding="utf-8")
    demande = {"modele": str(fin) if fin else "", "extrait": str(extrait)}
    _ETAT.update(qui=qui, pret=False, demarrage=True)

    def lancer():
        try:
            p = subprocess.Popen(
                [str(V.MOTEUR), "-u", str(script), json.dumps(demande)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _ETAT["proc"] = p
            debut = time.time()
            while time.time() - debut < 240:
                ligne = p.stdout.readline()
                if not ligne:
                    break
                if ligne.startswith("PRET"):
                    _ETAT["pret"] = True
                    LOG.info("voix vive : %s pret en %.0f s", qui,
                             time.time() - debut)
                    break
        except Exception:
            LOG.exception("voix vive : demarrage")
        finally:
            _ETAT["demarrage"] = False
    threading.Thread(target=lancer, daemon=True).start()


def arreter():
    """Ferme le serveur (changement de personnalite, carte a liberer)."""
    p = _ETAT["proc"]
    _ETAT.update(proc=None, pret=False, demarrage=False)
    if p is None or p.poll() is not None:
        return False
    try:
        p.stdin.write("STOP\n")
        p.stdin.flush()
        p.wait(timeout=5)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
    LOG.info("voix vive : arretee")
    return True


def synthetiser(qui, texte, delai=40):
    """(echantillons int16, frequence) avec la voix clonee, ou None."""
    if not pret(qui):
        return None
    import numpy as np
    sortie = _dossier() / ("%s.wav" % uuid.uuid4().hex[:8])
    with _VERROU:
        p = _ETAT["proc"]
        try:
            p.stdin.write(json.dumps({"texte": texte, "sortie": str(sortie)},
                                     ensure_ascii=False) + "\n")
            p.stdin.flush()
            reponse = {}

            def lire():
                reponse["ligne"] = p.stdout.readline()
            fil = threading.Thread(target=lire, daemon=True)
            fil.start()
            fil.join(delai)
            ligne = reponse.get("ligne", "")
        except Exception:
            LOG.exception("voix vive : envoi")
            arreter()
            return None
    if not ligne.startswith("OK") or not sortie.exists():
        LOG.warning("voix vive : %s", (ligne or "pas de reponse").strip()[:120])
        if not ligne:
            arreter()     # bloque : on repartira proprement
        return None
    try:
        with wave.open(str(sortie), "rb") as f:
            sr = f.getframerate()
            donnees = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2")
        return donnees.copy(), sr
    finally:
        try:
            os.remove(sortie)
        except Exception:
            pass
