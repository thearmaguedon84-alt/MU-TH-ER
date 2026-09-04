"""Decouper des extraits de voix dans un episode, pour le clonage.

On ne demande pas a la machine de reconnaitre les personnages : elle ne sait
pas le faire sans exemple. On lui demande de separer les voix, ce qu elle fait
tres bien — on decoupe aux silences, on mesure le timbre de chaque morceau, on
regroupe ce qui se ressemble. Chaque groupe est une voix, et l oreille humaine
met le nom dessus.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from core.dossiers import dossier
from core.registre import outil

VOIX = dossier("documents") / "voix"
CANDIDATS = VOIX / "candidats"


def _plat(nom):
    return re.sub(r"[^a-z0-9]+", "-", (nom or "").lower()).strip("-")


ANALYSE = """
import sys, json, os, subprocess, wave
import numpy as np
import librosa
from sklearn.cluster import KMeans

d = json.loads(sys.argv[1])
y, sr = librosa.load(d["son"], sr=22050, mono=True)

# On decoupe aux silences : chaque morceau est une phrase, ou un bout de
# phrase, mais jamais deux voix a cheval.
bouts = librosa.effects.split(y, top_db=32, frame_length=2048, hop_length=512)
morceaux = []
for a, b in bouts:
    duree = (b - a) / sr
    if 0.7 <= duree <= 6.0:
        morceaux.append((int(a), int(b), duree))
if len(morceaux) < d["combien"] * 3:
    print(json.dumps({"erreur": "pas assez de parole dans cet extrait"}))
    raise SystemExit

# Le timbre de chaque morceau : ses coefficients spectraux, sa hauteur, sa
# largeur de bande. C est ce qui distingue une voix d une autre.
traits = []
for a, b, _ in morceaux:
    m = y[a:b]
    mfcc = librosa.feature.mfcc(y=m, sr=sr, n_mfcc=20)
    f0 = librosa.yin(m, fmin=60, fmax=500, sr=sr)
    f0 = f0[np.isfinite(f0)]
    traits.append(np.concatenate([
        mfcc.mean(axis=1), mfcc.std(axis=1),
        [np.median(f0) if len(f0) else 0.0],
        [librosa.feature.spectral_centroid(y=m, sr=sr).mean()],
    ]))
traits = np.array(traits)
traits = (traits - traits.mean(axis=0)) / (traits.std(axis=0) + 1e-6)

groupes = KMeans(n_clusters=d["combien"], n_init=10,
                 random_state=0).fit_predict(traits)

# Un candidat par voix : ses morceaux les plus longs, mis bout a bout jusqu a
# vingt secondes. XTTS se cale bien mieux sur vingt secondes que sur trois.
sortie = []
for g in range(d["combien"]):
    lot = sorted([m for m, q in zip(morceaux, groupes) if q == g],
                 key=lambda m: -m[2])
    if not lot:
        continue
    pris, total = [], 0.0
    for a, b, duree in lot:
        pris.append(y[a:b])
        total += duree
        if total >= 20.0:
            break
    if total < 6.0:
        continue
    # Un souffle entre les phrases : sans cela on entend les coupes.
    silence = np.zeros(int(sr * 0.18), dtype=np.float32)
    assemble = np.concatenate(
        [x for p in pris for x in (p.astype(np.float32), silence)])
    assemble = assemble / (np.abs(assemble).max() + 1e-6) * 0.95
    chemin = os.path.join(d["dossier"], "candidat-%d.wav" % (g + 1))
    with wave.open(chemin, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes((assemble * 32767).astype("<i2").tobytes())
    hauteurs = [traits[i][40] for i, q in enumerate(groupes) if q == g]
    sortie.append({"numero": g + 1, "duree": round(total, 1),
                   "morceaux": len(pris), "chemin": chemin})

print(json.dumps({"candidats": sortie}))
"""


def _ffmpeg():
    from tools.clip import _ffmpeg as f
    return f()


@outil(
    nom="preparer_extraits_voix",
    description=(
        "Decoupe dans un episode des extraits de voix a ecouter, un par voix "
        "trouvee, pour servir ensuite au clonage. Pour 'prepare des extraits "
        "de voix depuis cet episode', 'trouve-moi des voix dans ce fichier'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "fichier": {"type": "string",
                        "description": "Chemin ou nom de l episode."},
            "combien": {"type": "integer",
                        "description": "Nombre de voix a separer. 8 par defaut."},
            "depart": {"type": "integer",
                       "description": "Minute de depart. 2 par defaut, pour passer le generique."},
            "duree": {"type": "integer",
                      "description": "Minutes analysees. 14 par defaut."},
        },
        "required": ["fichier"],
    },
    lent=True,
    phrase_attente="Je cherche les voix dans l episode.",
)
def preparer_extraits_voix(fichier: str, combien: int = 8, depart: int = 2,
                           duree: int = 14) -> str:
    from tools import voix_clonee

    source = Path(fichier)
    if not source.exists():
        return f"Je ne trouve pas le fichier « {fichier} »."
    exe = _ffmpeg()
    if not exe:
        return "ffmpeg est introuvable."
    if not voix_clonee.pret():
        return "Le moteur de voix n est pas installe."

    shutil.rmtree(CANDIDATS, ignore_errors=True)
    CANDIDATS.mkdir(parents=True, exist_ok=True)

    son = CANDIDATS / "_episode.wav"
    subprocess.run([exe, "-y", "-ss", str(depart * 60), "-t", str(duree * 60),
                    "-i", str(source), "-vn", "-ac", "1", "-ar", "22050",
                    str(son)], capture_output=True, timeout=1800)
    if not son.exists():
        return "Je n ai pas pu extraire le son de ce fichier."

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as f:
        f.write(ANALYSE)
        script = f.name
    try:
        r = subprocess.run(
            [str(voix_clonee.MOTEUR), script,
             json.dumps({"son": str(son), "dossier": str(CANDIDATS),
                         "combien": max(2, min(16, combien))})],
            capture_output=True, text=True, timeout=3600)
    finally:
        try:
            os.unlink(script)
        except Exception:
            pass
    son.unlink(missing_ok=True)

    lignes = [l for l in (r.stdout or "").splitlines() if l.startswith("{")]
    if not lignes:
        return "L analyse a echoue : %s" % (r.stderr or "")[-200:]
    reponse = json.loads(lignes[-1])
    if "erreur" in reponse:
        return reponse["erreur"]

    trouves = reponse["candidats"]
    if not trouves:
        return "Je n ai separe aucune voix exploitable dans cet extrait."
    return ("%d voix separees, rangees dans %s : %s. Ecoute-les et dis-moi "
            "qui est qui — « le candidat 3 c est Kyle » — et je les "
            "installerai."
            % (len(trouves), CANDIDATS,
               ", ".join("candidat %d (%.0f s)" % (c["numero"], c["duree"])
                         for c in trouves)))


@outil(
    nom="ranger_extrait_voix",
    description=(
        "Installe un candidat ecoute comme la voix d un personnage. Pour "
        "'le candidat 3 c est Kyle', 'range le candidat 5 pour Cartman'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "numero": {"type": "integer", "description": "Numero du candidat."},
            "personnage": {"type": "string", "description": "Son nom."},
        },
        "required": ["numero", "personnage"],
    },
)
def ranger_extrait_voix(numero: int, personnage: str) -> str:
    candidat = CANDIDATS / ("candidat-%d.wav" % int(numero))
    if not candidat.exists():
        return f"Je n ai pas de candidat numero {numero}."
    nom = _plat(personnage)
    if not nom:
        return "Donne-moi le nom du personnage."
    VOIX.mkdir(parents=True, exist_ok=True)
    cible = VOIX / (nom + ".wav")
    shutil.copy(candidat, cible)
    return ("C est la voix de %s desormais. Elle servira au prochain film."
            % personnage.title())
