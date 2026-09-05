"""La voix des personnages, clonee depuis un extrait.

Le moteur de clonage vit dans son propre environnement : ni celui de Jarvis,
qui est en Python 3.13 ou il ne s installe pas, ni celui de ComfyUI, dont les
paquets se brouilleraient. On l appelle donc comme on appelle un outil
exterieur, par un petit programme jete a chaque fois.

Un extrait depose dans `voix/<personnage>.wav` suffit a donner sa voix a
quelqu un. Sans extrait, on ne rend rien et l appelant retombe sur la voix de
synthese : le film se fait quand meme, et l on ajoute les voix une par une.
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from core.dossiers import dossier

MOTEUR = Path(r"F:\IA\voix\.venv\Scripts\python.exe")
MODELE = "tts_models/multilingual/multi-dataset/xtts_v2"
EXTRAITS = dossier("documents") / "voix"
CACHE = dossier("documents") / "voix" / ".dit"

# Les formats qu on accepte comme extrait de reference.
SONS = (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus")


def _syllabes(mot):
    """Combien de syllabes dans un mot francais, a peu pres.

    Ses groupes de voyelles, moins le « e » final qui ne se prononce pas.
    Approximatif et suffisant : on cherche un rythme, pas une scansion.
    """
    sons = re.findall(r"[aeiouyàâäéèêëïîôöùûüœ]+", mot.lower())
    combien = len(sons)
    if combien > 1 and re.search(r"e$", mot.lower()):
        combien -= 1
    return max(1, combien)


def marmonner(texte):
    """Le rythme d une replique, sans ses mots.

    Un « mmph » par syllabe, la ponctuation gardee pour que les pauses
    tombent au meme endroit. Kenny dit donc exactement aussi longtemps que
    les autres, et sa bouche bat au bon moment — mais il n y a rien a
    comprendre, ce qui est tout ce qu on lui demande.
    """
    morceaux = []
    for bout in re.findall(r"[^\s]+|\s+", texte or ""):
        if bout.isspace():
            morceaux.append(" ")
            continue
        mot = re.sub(r"[^\w'\u00e0-\u00ff]", "", bout)
        ponctuation = "".join(c for c in bout if c in ",.;:!?")
        if not mot:
            morceaux.append(ponctuation)
            continue
        # Mesure : un jeton par mot, dimensionne a ses syllabes, tombe a
        # 7,8 secondes la ou la replique dite normalement en fait 6,6. Un
        # « mmph » par syllabe en mettait vingt-quatre — Kenny marmonnait au
        # ralenti pendant que la scene l attendait.
        morceaux.append(("mmh", "mmmh", "mmmmh")[min(2, _syllabes(mot) - 1)]
                        + ponctuation)
    rendu = "".join(morceaux).strip()
    return rendu or "mmph mmph"


def _plat(nom):
    return re.sub(r"[^a-z0-9]+", "-", (nom or "").lower()).strip("-")


def extrait_de(personnage):
    """L extrait de reference d un personnage, s il en a un."""
    cle = _plat(personnage)
    if not cle or not EXTRAITS.is_dir():
        return None
    lot = [f for f in sorted(EXTRAITS.iterdir())
           if f.is_file() and f.suffix.lower() in SONS]
    # Le nom exact d abord.
    for f in lot:
        if _plat(f.stem) == cle:
            return f
    # Puis un fichier qui commence par ce nom : « Gerald Broflovski dans
    # s.mp3 » est bien la voix de Gerald, et lui demander de renommer ses
    # fichiers serait une exigence sans raison.
    for f in lot:
        plat = _plat(f.stem)
        if plat.startswith(cle + "-") or plat == cle:
            return f
    for f in lot:
        if cle in _plat(f.stem).split("-"):
            return f
    return None


def voix_disponibles():
    """Qui a une voix clonee, et depuis quel extrait."""
    if not EXTRAITS.is_dir():
        return {}
    return {_plat(f.stem): f for f in sorted(EXTRAITS.iterdir())
            if f.is_file() and f.suffix.lower() in SONS}


def pret():
    """Le moteur est-il installe ?"""
    return MOTEUR.exists()


# Ceux qui ne prononcent pas les mots. Kenny marmonne : on ne lui donne pas
# la replique, on lui donne son rythme.
MARMONNENT = {"kenny"}

# Ce qui parle sous un tissu. La valeur est celle qu on a mesuree sur la
# serie : le centre spectral de Kenny tombe a 1780 hertz quand celui des
# autres enfants tient les 3000.
ETOUFFES = {"kenny": 1900.0}

PROGRAMME = """
import sys, json, os, wave
os.environ.setdefault("COQUI_TOS_AGREED", "1")
import numpy as np
demande = json.loads(sys.argv[1])
from TTS.api import TTS
moteur = TTS(demande["modele"]).to(demande.get("carte", "cuda"))


def etouffer(chemin, vise):
    \"\"\"Remet la parka : on coupe les aigus jusqu a retrouver la mesure.\"\"\"
    import librosa, scipy.signal as sig
    y, sr = librosa.load(chemin, sr=None, mono=True)
    for coupure in (3000, 2600, 2300, 2000, 1750, 1500, 1200):
        b, a = sig.butter(4, coupure / (sr / 2), btype="low")
        z = sig.filtfilt(b, a, y).astype(np.float32)
        centre = float(librosa.feature.spectral_centroid(y=z, sr=sr).mean())
        if centre <= vise:
            break
    z = z / (np.abs(z).max() + 1e-6) * 0.95
    with wave.open(chemin, "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr)
        f.writeframes((z * 32767).astype("<i2").tobytes())
    return centre


for piece in demande["pieces"]:
    moteur.tts_to_file(text=piece["texte"], speaker_wav=piece["extrait"],
                       language=demande.get("langue", "fr"),
                       file_path=piece["sortie"], split_sentences=False,
                       gpt_cond_len=60, gpt_cond_chunk_len=6)
    if piece.get("etouffe"):
        print("etouffe %s -> %.0f Hz"
              % (os.path.basename(piece["sortie"]),
                 etouffer(piece["sortie"], piece["etouffe"])))
print("fait")
"""


def dire(personnage, texte, sortie, langue="fr"):
    """Fait dire une replique avec la voix clonee du personnage.

    Rend le chemin du son, ou None si ce personnage n a pas d extrait —
    auquel cas l appelant se rabat sur la voix de synthese.
    """
    return dire_tout([(personnage, texte, sortie)], langue).get(str(sortie))


def dire_tout(pieces, langue="fr"):
    """Plusieurs repliques d un coup : le modele n est charge qu une fois.

    Charger XTTS prend une trentaine de secondes ; le faire par replique
    couterait plus cher que la synthese elle-meme.
    """
    if not pret():
        return {}

    CACHE.mkdir(parents=True, exist_ok=True)
    a_faire, resultats = [], {}
    for personnage, texte, sortie in pieces:
        extrait = extrait_de(personnage)
        if not extrait:
            continue
        # Ce qui est dit une fois n est pas redit : on garde sous l empreinte
        # du texte, de la voix et de l extrait qui l a produite.
        cle = hashlib.sha1(
            ("%s|%s|%s|%d|v5" % (_plat(personnage), texte, langue,
                                 extrait.stat().st_size)).encode("utf-8")
        ).hexdigest()[:16]
        garde = CACHE / (cle + ".wav")
        if garde.exists():
            Path(sortie).write_bytes(garde.read_bytes())
            resultats[str(sortie)] = str(sortie)
            continue
        if _plat(personnage) in MARMONNENT:
            texte = marmonner(texte)
        a_faire.append({"texte": texte, "extrait": str(extrait),
                        "sortie": str(garde), "vise": str(sortie),
                        "etouffe": ETOUFFES.get(_plat(personnage))})

    if a_faire:
        demande = {"modele": MODELE, "langue": langue,
                   "pieces": [{k: p[k] for k in
                                ("texte", "extrait", "sortie", "etouffe")}
                              for p in a_faire]}
        # Le programme est ecrit a cote du cache, a un endroit stable :
        # un fichier jete dans le dossier temporaire du systeme s est revele
        # peu fiable, et surtout on ne voyait rien quand il echouait.
        script = CACHE / "_dire.py"
        script.write_text(PROGRAMME, encoding="utf-8")
        trace = CACHE / "_dernier.log"
        try:
            r = subprocess.run([str(MOTEUR), "-u", str(script),
                                json.dumps(demande)],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=3600)
            trace.write_text((r.stdout or "") + "\n--- erreurs ---\n"
                             + (r.stderr or ""), encoding="utf-8")
        except Exception as souci:
            trace.write_text("echec : %r" % (souci,), encoding="utf-8")
        for p in a_faire:
            garde = Path(p["sortie"])
            if garde.exists():
                Path(p["vise"]).write_bytes(garde.read_bytes())
                resultats[p["vise"]] = p["vise"]
    return resultats
