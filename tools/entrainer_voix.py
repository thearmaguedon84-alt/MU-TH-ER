"""Ré-entraîner la voix d'un personnage sur ses extraits certifiés.

Le dernier morceau de la boucle. Le clonage à la volée rend le timbre ; seul un
ré-entraînement rend le phrasé, et c'est le phrasé que Serge réclamait depuis le
début — « c'est sa voix mais ça ne va pas ».

Ce que l'entraînement suppose, et qui n'est pas négociable :

**Des extraits certifiés, pas une récolte brute.** L'expérience a été faite :
treize minutes ramassées par la machine sonnent moins bien que soixante-cinq
secondes validées à l'oreille. La quantité ne remplace pas la certitude, et un
jeu sale produit une voix moyenne entre plusieurs personnes.

**Cinq minutes au moins.** En dessous, le ré-entraînement est symbolique — la
référence propre suffit et coûte une seconde au lieu d'une heure. On refuse
plutôt que de faire tourner la carte pour rien.

**On repart du modèle précédent** quand il y en a un. Chaque reprise apprend
depuis ce qui est déjà su plutôt que de recommencer : Gerald est passé de 3,9 à
1,4 de perte en trois reprises successives.

Et on surveille la perte d'évaluation. Si elle remonte, le modèle apprend les
extraits par cœur au lieu d'apprendre la voix, et il faut des données neuves
plutôt que des époques de plus. Le compte rendu le dit noir sur blanc.
"""
import csv
import glob
import json
import os
import subprocess
import time
import wave
from pathlib import Path

from core.dossiers import dossier
from core.file_gpu import enfile
from core.registre import outil

from tools import chasse_voix
from tools import voix_clonee

JEUX = Path(r"F:\IA\voix\jeux")
ENTRAINEMENT = Path(r"F:\IA\voix\entrainement")
BASE = Path(r"F:\IA\voix\base")
MODELES = (Path.home() / "AppData" / "Local" / "tts"
           / "tts_models--multilingual--multi-dataset--xtts_v2")

# En dessous, la reference propre suffit : une heure de carte pour un gain
# inaudible n est pas un bon echange.
MINIMUM = 300.0

_PREPARER = chasse_voix._COMMUN + r'''
attendre_la_carte()
os.makedirs(os.path.join(d["jeu"], "wavs"), exist_ok=True)
for vieux in glob.glob(os.path.join(d["jeu"], "wavs", "*.wav")):
    os.remove(vieux)

from faster_whisper import WhisperModel
ecoute = WhisperModel("medium", device="cuda", compute_type="float16")
lignes = []
for i, source in enumerate(sorted(glob.glob(os.path.join(d["certifies"],
                                                         "*.wav")))):
    y, _ = librosa.load(source, sr=SR, mono=True)
    fichier = "%s-%04d.wav" % (d["qui"], i)
    chemin = os.path.join(d["jeu"], "wavs", fichier)
    ecrire(chemin, y)
    segs, _ = ecoute.transcribe(chemin, language="fr", beam_size=5)
    segs = list(segs)
    texte = " ".join(x.text.strip() for x in segs).strip()
    sur = float(np.mean([x.avg_logprob for x in segs])) if segs else -9.0
    # Une transcription douteuse gate l apprentissage : le modele apprendrait
    # a dire un texte qui n est pas celui qu il entend.
    if sur < -0.85 or len(texte) < 4:
        os.remove(chemin)
        continue
    lignes.append((os.path.join("wavs", fichier), texte))
del ecoute
torch.cuda.empty_cache()

coupe = max(2, len(lignes) // 12)
import csv
for etiquette, part in (("train", lignes[coupe:]), ("eval", lignes[:coupe])):
    with open(os.path.join(d["jeu"], "metadata_%s.csv" % etiquette), "w",
              encoding="utf-8", newline="") as f:
        e = csv.writer(f, delimiter="|", quoting=csv.QUOTE_NONE,
                       escapechar="\\")
        e.writerow(["audio_file", "text", "speaker_name"])
        for fichier, texte in part:
            e.writerow([fichier, texte, d["qui"]])
print("PRETES %d" % len(lignes))
print("FINI")
'''


def _dernier_modele(nom):
    lot = []
    for chemin in ENTRAINEMENT.glob("voix-%s-*" % nom):
        poids = chemin / "best_model.pth"
        if poids.exists():
            lot.append((poids.stat().st_mtime, poids))
    return max(lot)[1] if lot else None


def _duree(dossier_certifies):
    total = 0.0
    for chemin in sorted(dossier_certifies.glob("*.wav")):
        try:
            with wave.open(str(chemin)) as w:
                total += w.getnframes() / w.getframerate()
        except Exception:
            pass
    return total


@outil(
    nom="entrainer_voix",
    description=(
        "Reentraine la voix d un personnage sur ses extraits certifies. "
        "Le clonage rend le timbre, le reentrainement rend le phrase. Une "
        "heure de calcul. Pour 'reentraine la voix de X', 'ameliore le "
        "phrase de X'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "qui": {"type": "string", "description": "Le personnage."},
            "epoques": {"type": "number",
                        "description": "Passages sur les donnees, 40 par defaut."},
        },
        "required": ["qui"],
    },
    lent=True,
    phrase_attente="Je reentraine sa voix, ca prendra une bonne heure.",
)
@enfile("entrainement de voix", "qui")
def entrainer_voix(qui: str, epoques: float = 40) -> str:
    nom = voix_clonee._plat(qui)
    certifies = chasse_voix.CERTIFIES / nom
    if not certifies.exists() or not any(certifies.glob("*.wav")):
        return ("%s n a pas d extraits certifies. Passe d abord par la "
                "chasse : je propose, tu ecoutes, tu gardes." % qui)
    duree = _duree(certifies)
    if duree < MINIMUM:
        return ("%s n a que %.1f minutes d extraits certifies. En dessous de "
                "cinq, le reentrainement ne s entend pas et sa reference "
                "propre fait deja le travail — mieux vaut un tour de chasse "
                "de plus qu une heure de carte pour rien."
                % (qui, duree / 60))

    # L entrainement demande onze giga-octets : si ComfyUI est charge, il
    # n y a pas la place et mieux vaut le dire que d attendre en silence.
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=30)
        libre = int(r.stdout.strip().splitlines()[0])
    except Exception:
        libre = 99999
    if libre < 11000:
        return ("Il ne reste que %d Mo libres sur la carte, et un "
                "entrainement en demande onze mille. Ferme ce qui l occupe "
                "— la generation d images la garde chargee — et redemande."
                % libre)

    jeu = JEUX / nom
    jeu.mkdir(parents=True, exist_ok=True)
    texte = chasse_voix._lancer(
        _PREPARER, {"qui": nom, "certifies": str(certifies), "jeu": str(jeu)},
        "preparer-%s.log" % nom)
    if "PRETES" not in texte:
        return "La preparation a echoue. Fin du journal :\n" + texte[-1200:]
    combien = int(texte.split("PRETES")[1].split()[0])

    # Le programme d entrainement tourne dans l environnement du moteur,
    # pas dans celui de Jarvis : il vit donc a cote de lui, et surtout pas
    # dans tools/ ou le registre essaierait de l importer.
    entraineur = Path(r"F:\IA\voix\entrainer_moteur.py")
    if not entraineur.exists():
        return ("Le programme d entrainement est introuvable (%s)."
                % entraineur)

    demande = {
        "qui": nom, "jeu": str(jeu), "epoques": int(epoques),
        "sortie": str(ENTRAINEMENT), "lot": 3, "accumulation": 84,
        "dvae": str(BASE / "dvae.pth"), "mel": str(BASE / "mel_stats.pth"),
        "xtts": str(MODELES / "model.pth"),
        "vocab": str(MODELES / "vocab.json"),
    }
    # On repart de ce qui est deja su plutot que de recommencer.
    reprise = _dernier_modele(nom)
    if reprise:
        demande["reprise"] = str(reprise)

    journal = chasse_voix.TRAVAIL / ("entrainement-%s.log" % nom)
    chasse_voix.TRAVAIL.mkdir(parents=True, exist_ok=True)
    debut = time.time()
    with open(journal, "w", encoding="utf-8") as j:
        subprocess.run([str(voix_clonee.MOTEUR), "-u", str(entraineur),
                        json.dumps(demande)], stdout=j,
                       stderr=subprocess.STDOUT,
                       creationflags=getattr(subprocess,
                                             "CREATE_NO_WINDOW", 0))
    suivi = journal.read_text(encoding="utf-8", errors="replace")
    if "TERMINE" not in suivi[-4000:]:
        return ("L entrainement de %s n a pas abouti. Fin du journal :\n%s"
                % (qui, suivi[-1500:]))

    # La perte d evaluation : si elle remonte, le modele apprend les extraits
    # par coeur au lieu d apprendre la voix.
    pertes = []
    for ligne in suivi.splitlines():
        if "avg_loss:" in ligne:
            morceau = ligne.split("avg_loss:")[1].split("(")[0]
            for bruit in ("\x1b[92m", "\x1b[91m", "\x1b[0m"):
                morceau = morceau.replace(bruit, "")
            try:
                pertes.append(float(morceau.strip()))
            except ValueError:
                pass
    verdict = ""
    if len(pertes) >= 4:
        verdict = ("\nPerte d evaluation : %.3f au depart, %.3f au mieux."
                   % (pertes[0], min(pertes)))
        if pertes[-1] > min(pertes) + 0.01:
            verdict += ("\nElle est REMONTEE a la fin (%.3f) : il apprend les "
                        "extraits par coeur. Un tour de chasse de plus "
                        "servira davantage que des epoques."
                        % pertes[-1])
        else:
            verdict += ("\nElle descendait encore : des epoques de plus "
                        "peuvent gagner un peu.")

    return ("%s est reentraine sur %d repliques (%.1f minutes), %d epoques en "
            "%.0f minutes.%s\n\nSes films l emploieront des maintenant — "
            "`voix_clonee` prend le modele le plus recent de chaque "
            "personnage."
            % (qui.title(), combien, duree / 60, int(epoques),
               (time.time() - debut) / 60, verdict))
