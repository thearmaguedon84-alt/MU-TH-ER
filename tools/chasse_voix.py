"""Chasser la voix d'un personnage dans une collection d'épisodes.

Ce que deux jours de tâtonnements ont appris, ramené à trois outils. La méthode
tient en une boucle : on part d'un ou deux moments repérés à l'oreille, on
ramasse tout ce qui leur ressemble, Serge écoute et garde, ses extraits validés
deviennent les repères du tour suivant. Trois tours ont suffi pour Randy, deux
pour Cartman.

Les cinq leçons qui ont coûté cher, et qui sont ici plutôt que dans ma tête :

**Le doublage change d'une époque à l'autre.** Le Randy de la saison 19 et celui
de la saison 22 ne sont pas le même comédien — 0,51 entre eux, quand la même
personne donne 0,75. Chasser à travers les époques, c'est chercher trois
personnes à la fois. D'où `verifier_reperes`, à passer avant toute chasse.

**Jamais de phrase coupée.** Les extraits vont de deux à onze secondes, entiers.
Une coupe à cinq secondes arrache l'intonation, qui court sur la phrase entière
— c'est ce que Serge appelait « le phrasé qui ne va pas ».

**Le maximum de cent ancres bat le maximum de deux**, quel que soit le
personnage : c'est de l'arithmétique, pas de la ressemblance. Pour comparer un
candidat à un autre personnage, on prend le neuvième décile, insensible au
nombre d'ancres.

**Un repoussoir vaut mieux qu'un seuil.** Les personnages déjà terminés servent
à écarter leurs propres répliques : sans Gerald en repoussoir, la moitié de la
récolte de Randy aurait été du Gerald. Chaque voix finie rend la suivante plus
propre.

**Aucun filtre qui n'ait été mesuré sur du matériel connu.** J'en ai réglé
quatre à l'œil ; les quatre rejetaient les bons extraits. Ce qui tranche, c'est
l'oreille de Serge sur un tirage ; la machine ne fait que proposer.
"""
import json
import subprocess
import uuid
from pathlib import Path

from core.config import reglage
from core.dossiers import dossier
from core.file_gpu import enfile
from core.registre import outil

from tools import voix_clonee

TRAVAIL = dossier("documents") / "voix" / ".chasse"
CANDIDATS = dossier("documents") / "voix" / "candidats"
CERTIFIES = dossier("documents") / "voix" / "certifies"

FILMS = (".avi", ".mkv", ".mp4", ".m4v", ".mov")


def _ffmpeg():
    from tools import dessin_anime
    return dessin_anime._ffmpeg()


def _racines():
    """Ou chercher les episodes : ses dossiers, et sa mediatheque."""
    lot = [dossier("videos"), dossier("documents")]
    ou = reglage("chasse.episodes", r"Z:\\film")
    if ou:
        lot.insert(0, Path(ou))
    return [p for p in lot if p and Path(p).exists()]


def _episodes(ou):
    """Les films d un dossier, ou le film qu on nous designe par son nom.

    Il donne rarement un chemin complet : il ecrit le nom du fichier tel
    qu il le voit, ou un bout du nom. On cherche donc chez lui plutot que
    d exiger qu il cherche pour nous.
    """
    chemin = Path(ou)
    if chemin.is_absolute() and chemin.exists():
        if chemin.is_file():
            return [str(chemin)]
        return sorted(str(p) for p in chemin.rglob("*")
                      if p.suffix.lower() in FILMS)

    for racine in _racines():
        candidat = Path(racine) / ou
        if candidat.exists():
            if candidat.is_file():
                return [str(candidat)]
            return sorted(str(p) for p in candidat.rglob("*")
                          if p.suffix.lower() in FILMS)

    # Rien de tel quel : on cherche par ressemblance de nom. Un dossier
    # compte autant qu un fichier — c est ainsi qu on designe une saison.
    cherche = str(ou).lower().strip()
    trouves = []
    for racine in _racines():
        for p in Path(racine).rglob("*"):
            if cherche not in p.name.lower():
                continue
            if p.is_dir():
                trouves += [str(q) for q in p.rglob("*")
                            if q.suffix.lower() in FILMS]
            elif p.suffix.lower() in FILMS:
                trouves.append(str(p))
        if trouves:
            break
    return sorted(set(trouves))


def _lancer(programme, demande, journal):
    """Le moteur de clonage vit dans son propre environnement."""
    TRAVAIL.mkdir(parents=True, exist_ok=True)
    marque = uuid.uuid4().hex[:8]
    script = TRAVAIL / ("_%s.py" % marque)
    script.write_text(programme, encoding="utf-8")
    # La demande passe par un fichier, pas par la ligne de commande : deux
    # cent cinquante chemins d episodes depassent la longueur maximale d une
    # ligne de commande Windows, et l erreur ne dit pas pourquoi.
    ordre = TRAVAIL / ("_%s.json" % marque)
    ordre.write_text(json.dumps(demande), encoding="utf-8")
    trace = TRAVAIL / journal
    with open(trace, "w", encoding="utf-8") as j:
        subprocess.run([str(voix_clonee.MOTEUR), "-u", str(script),
                        str(ordre)], stdout=j,
                       stderr=subprocess.STDOUT,
                       creationflags=getattr(subprocess,
                                             "CREATE_NO_WINDOW", 0))
    texte = trace.read_text(encoding="utf-8", errors="replace")
    for jetable in (script, ordre):
        try:
            jetable.unlink()
        except OSError:
            pass
    return texte


def _decoder_reperes(reperes, duree):
    """« episode@4:23 » en moments utilisables."""
    lot = []
    for morceau in str(reperes).split(","):
        morceau = morceau.strip()
        if not morceau or "@" not in morceau:
            continue
        fichier, quand = morceau.rsplit("@", 1)
        secondes = 0.0
        for part in quand.strip().split(":"):
            try:
                secondes = secondes * 60 + float(part)
            except ValueError:
                secondes = 0.0
        trouve = _episodes(fichier.strip())
        if not trouve:
            continue
        lot.append({"fichier": trouve[0], "quand": secondes,
                    "duree": float(duree),
                    "quoi": "%s@%s" % (Path(trouve[0]).stem[:16],
                                       quand.strip())})
    return lot


def _decoder_numeros(numeros):
    """« 4 debut 2, 15 fin 2, 38 bouts 0-4 6-99 » en ordres de découpe."""
    garde = []
    for morceau in str(numeros).replace(";", ",").split(","):
        mots = morceau.replace("-", " - ").split()
        if not mots:
            continue
        try:
            numero = int(mots[0])
        except ValueError:
            continue
        if len(mots) == 1:
            garde.append(numero)
            continue
        quoi = mots[1].lower()
        if quoi in ("debut", "fin", "tete", "queue"):
            try:
                garde.append([numero, quoi, float(mots[2])])
            except (IndexError, ValueError):
                garde.append(numero)
        elif quoi == "bouts":
            bouts, reste = [], [m for m in mots[2:] if m != "-"]
            for i in range(0, len(reste) - 1, 2):
                try:
                    bouts.append([float(reste[i]), float(reste[i + 1])])
                except ValueError:
                    pass
            garde.append([numero, "bouts", bouts] if bouts else numero)
        else:
            garde.append(numero)
    return garde


_COMMUN = r'''
import os, sys, json, glob, wave, time, pickle, subprocess, traceback
# Le moteur tourne sous un Python dont la sortie est en cp1252 : un guillemet
# francais dans le compte rendu faisait echouer le dernier print, apres tout
# le travail. On ecrit en utf-8, et on remplace plutot que d echouer.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("COQUI_TOS_AGREED", "1")
import numpy as np
import torch
os.add_dll_directory(os.path.join(os.path.dirname(torch.__file__), "lib"))
import librosa

SR = 22050
# L argument est le chemin d un fichier de demande, ou la demande elle-meme
# quand elle est courte : les deux se rencontrent.
if os.path.exists(sys.argv[1]):
    with open(sys.argv[1], encoding="utf-8") as _f:
        d = json.load(_f)
else:
    d = json.loads(sys.argv[1])
T0 = time.time()


def dire(*m):
    print("[%5.1f min] " % ((time.time() - T0) / 60)
          + " ".join(str(x) for x in m), flush=True)


def place_libre(besoin=3000):
    # La memoire LIBRE, pas la memoire utilisee : ComfyUI reste charge avec
    # huit giga-octets, et attendre une carte vide reviendrait a attendre
    # toujours. Ce qui compte est qu il reste la place dont on a besoin.
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=30)
        return int(r.stdout.strip().splitlines()[0]) >= besoin
    except Exception:
        return True


def attendre_la_carte(besoin=3000):
    for _ in range(360):
        if place_libre(besoin):
            time.sleep(10)
            if place_libre(besoin):
                return
        time.sleep(30)
    dire("la carte ne s est pas liberee ; on tente quand meme")


def ecrire(chemin, x, sr=SR):
    x = np.asarray(x, dtype=np.float32)
    x = x / (np.abs(x).max() + 1e-6) * 0.95
    with wave.open(chemin, "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr)
        f.writeframes((x * 32767).astype("<i2").tobytes())


def prelever(ffmpeg, film, quand, duree, vers):
    # librosa ne lit pas un .mkv ni un .avi : on passe par ffmpeg, qui sait
    # aussi n extraire que la fenetre voulue au lieu de tout decoder.
    subprocess.run([ffmpeg, "-y", "-v", "error", "-ss", str(float(quand)),
                    "-t", str(float(duree)), "-i", film, "-vn",
                    "-map", "0:a:0", "-ac", "1", "-ar", str(SR), vers],
                   capture_output=True)
    if not os.path.exists(vers):
        return None
    return librosa.load(vers, sr=SR, mono=True)[0]


def decouper(m, ordre):
    # Son oreille dit ou l autre commence ; on obeit.
    quoi = ordre[0]
    if quoi == "debut":
        return [m[: int(float(ordre[1]) * SR)]]
    if quoi == "fin":
        return [m[: max(0, len(m) - int(float(ordre[1]) * SR))]]
    if quoi == "tete":
        return [m[int(float(ordre[1]) * SR):]]
    if quoi == "queue":
        # Garder la FIN : « les deux dernieres secondes ». Symetrique de
        # « debut », et il l emploie autant.
        return [m[-int(float(ordre[1]) * SR):]]
    if quoi == "bouts":
        return [m[int(float(a) * SR): int(float(b) * SR)] for a, b in ordre[1]]
    return [m]
'''

_VERIFIER = _COMMUN + r'''
attendre_la_carte()
os.makedirs(d["travail"], exist_ok=True)
from TTS.api import TTS
modele = TTS(d["modele"]).to("cuda").synthesizer.tts_model
tmp = os.path.join(d["travail"], "_v.wav")


def empreinte(m):
    ecrire(tmp, m)
    _, e = modele.get_conditioning_latents(audio_path=[tmp])
    v = e.squeeze().detach().cpu().numpy().astype(np.float64)
    return v / (np.linalg.norm(v) + 1e-9)


bouts, noms = [], []
# On garde ce qu on a preleve : quand les chiffres disent que les reperes
# ne s accordent pas, la seule facon de savoir pourquoi est de les ecouter.
garde = d["ecoute"]
os.makedirs(garde, exist_ok=True)
for vieux in glob.glob(os.path.join(garde, "*.wav")):
    os.remove(vieux)
for i, repere in enumerate(d["reperes"], 1):
    fenetre = os.path.join(garde, "repere-%d.wav" % i)
    y = prelever(d["ffmpeg"], repere["fichier"], repere["quand"],
                 repere["duree"], fenetre)
    if y is None or len(y) < SR:
        print("  %s : impossible a lire" % repere["quoi"])
        continue
    bouts.append(empreinte(y))
    noms.append("%d %s" % (i, repere["quoi"]))

print("ENTRE EUX (meme personne : 0,65 et plus)")
for i in range(len(bouts)):
    for j in range(i + 1, len(bouts)):
        print("  %-24s %-24s %.3f"
              % (noms[i], noms[j], float(np.dot(bouts[i], bouts[j]))))

if d["connus"]:
    print("")
    print("CONTRE LES VOIX DEJA CONNUES (9e decile)")
    for nom, ou in d["connus"].items():
        lot = [empreinte(librosa.load(c, sr=SR, mono=True)[0])
               for c in sorted(glob.glob(os.path.join(ou, "*.wav")))[:40]]
        if not lot:
            continue
        for k, v in enumerate(bouts):
            print("  %-24s vs %-12s %.3f"
                  % (noms[k], nom,
                     np.percentile([float(np.dot(v, q)) for q in lot], 90)))
print("FINI")
'''

_CHASSER = _COMMUN + r'''
attendre_la_carte()
os.makedirs(d["travail"], exist_ok=True)
os.makedirs(d["sortie"], exist_ok=True)
from TTS.api import TTS
modele = TTS(d["modele"]).to("cuda").synthesizer.tts_model
tmp = os.path.join(d["travail"], "_c.wav")


def empreinte(m):
    ecrire(tmp, m)
    _, e = modele.get_conditioning_latents(audio_path=[tmp])
    v = e.squeeze().detach().cpu().numpy().astype(np.float64)
    return v / (np.linalg.norm(v) + 1e-9)


# Les reperes : des moments designes a l oreille, ou les extraits deja
# certifies du personnage. Les seconds valent mieux que les premiers.
lui = []
fenetre = os.path.join(d["travail"], "_f.wav")
for repere in d["reperes"]:
    y = prelever(d["ffmpeg"], repere["fichier"], repere["quand"],
                 repere["duree"], fenetre)
    if y is not None and len(y) >= SR:
        lui.append(empreinte(y))
for c in sorted(glob.glob(os.path.join(d["certifies"], "*.wav"))):
    lui.append(empreinte(librosa.load(c, sr=SR, mono=True)[0]))
dire("%d reperes" % len(lui))

# Le repoussoir : les personnages deja finis. Sans lui, la recolte de Randy
# aurait ete a moitie du Gerald.
contre = []
for ou in d["repoussoirs"]:
    for c in sorted(glob.glob(os.path.join(ou, "*.wav")))[:60]:
        contre.append(empreinte(librosa.load(c, sr=SR, mono=True)[0]))
dire("%d extraits en repoussoir" % len(contre))

recolte = []
for k, episode in enumerate(d["episodes"], 1):
    if sum(len(m) / SR for _, m, _ in recolte) >= d["vise"]:
        dire("assez de matiere a l episode", k)
        break
    son = os.path.join(d["travail"], "_ep.wav")
    subprocess.run([d["ffmpeg"], "-y", "-i", episode, "-vn", "-map", "0:a:0",
                    "-ac", "1", "-ar", str(SR), son], capture_output=True)
    if not os.path.exists(son):
        continue
    try:
        y, sr = librosa.load(son, sr=SR, mono=True)
    except Exception:
        os.remove(son)
        continue
    # Des plages entieres delimitees par le silence : jamais de phrase coupee
    # au milieu, c est la que vit l intonation.
    plages = [(a, b) for a, b in librosa.effects.split(
        y, top_db=35, frame_length=2048, hop_length=512)
        if d["mini"] <= (b - a) / sr <= d["maxi"]]
    if not plages:
        os.remove(son)
        continue
    plats = [float(librosa.feature.spectral_flatness(y=y[a:b]).mean())
             for a, b in plages]
    plafond = float(np.percentile(plats, d["centile"]))
    pris = 0
    for (a, b), plat in zip(plages, plats):
        if plat > plafond:
            continue
        m = y[a:b]
        try:
            v = empreinte(m)
        except Exception:
            continue
        s = max(float(np.dot(v, x)) for x in lui)
        if s < d["seuil"]:
            continue
        if s >= d["doublon"]:
            # C est un extrait qu il a deja certifie, retrouve dans le meme
            # episode. Inutile de lui refaire ecouter ce qu il a garde.
            continue
        g = 0.0
        if contre:
            # Le 9e decile, pas le maximum : le maximum de cent ancres bat
            # celui de deux quel que soit le personnage.
            g = float(np.percentile([float(np.dot(v, x)) for x in contre], 90))
            if s < g + d["marge"]:
                continue
        recolte.append((s, m.copy(), "%s a %d min %02d s (%.2f / %.2f)"
                        % (os.path.basename(episode)[:34],
                           (a / sr) // 60, (a / sr) % 60, s, g)))
        pris += 1
    os.remove(son)
    if pris or k % 5 == 0:
        dire("  %-34s %3d plages -> %2d (total %d)"
             % (os.path.basename(episode)[:34], len(plages), pris,
                len(recolte)))

del modele
torch.cuda.empty_cache()
recolte.sort(key=lambda x: -x[0])
dire("%d candidats, %.1f min"
     % (len(recolte), sum(len(m) / SR for _, m, _ in recolte) / 60))
with open(os.path.join(d["travail"], "recolte-%s.pkl" % d["qui"]), "wb") as f:
    pickle.dump(recolte, f)

from faster_whisper import WhisperModel
ecoute = WhisperModel("medium", device="cuda", compute_type="float16")
for vieux in glob.glob(os.path.join(d["sortie"], "*.wav")):
    os.remove(vieux)
lignes = []
for i, (s, m, ou) in enumerate(recolte[: d["combien"]], 1):
    f = os.path.join(d["sortie"], "%s-%03d.wav" % (d["qui"], i))
    ecrire(f, m)
    segs, _ = ecoute.transcribe(f, language="fr", beam_size=5)
    lignes.append("  %3d  %.1f s  %s\n       %s"
                  % (i, len(m) / SR, ou,
                     " ".join(x.text.strip() for x in segs).strip()[:70]))
del ecoute
torch.cuda.empty_cache()
with open(os.path.join(d["sortie"], "_liste.txt"), "w",
          encoding="utf-8") as f:
    f.write("\n".join(lignes))
print("LISTE")
print("\n".join(lignes))
print("FINI")
'''

_GARDER = _COMMUN + r'''
os.makedirs(d["certifies"], exist_ok=True)
with open(d["recolte"], "rb") as f:
    lot = pickle.load(f)
lot.sort(key=lambda x: -x[0])

deja = len(glob.glob(os.path.join(d["certifies"], "*.wav")))
n = 0
for entree in d["garde"]:
    if isinstance(entree, int):
        morceaux = [lot[entree - 1][1]]
    else:
        morceaux = decouper(lot[entree[0] - 1][1], entree[1:])
    for bout in morceaux:
        # Moins de deux secondes : l empreinte devient du bruit. On abandonne
        # plutot que d esperer.
        if len(bout) / SR < 2.0:
            continue
        ecrire(os.path.join(d["certifies"],
                            "%s-%04d.wav" % (d["qui"], deja + n)), bout)
        n += 1

total = sorted(glob.glob(os.path.join(d["certifies"], "*.wav")))
duree = 0.0
for c in total:
    with wave.open(c) as w:
        duree += w.getnframes() / w.getframerate()
print("AJOUTES %d" % n)
print("TOTAL %d extraits, %.1f min" % (len(total), duree / 60))

# La reference : tous les extraits certifies bout a bout, un blanc entre eux.
if d.get("reference") and total:
    blanc = np.zeros(int(0.25 * SR), dtype=np.float32)
    bouts = []
    for c in total:
        y, _ = librosa.load(c, sr=SR, mono=True)
        bouts.append(y)
        bouts.append(blanc)
    ecrire(d["reference"], np.concatenate(bouts))
    print("REFERENCE %s" % os.path.basename(d["reference"]))
print("FINI")
'''


@outil(
    nom="verifier_reperes",
    description=(
        "Verifie que des moments reperes a l oreille sont bien la meme "
        "personne, et les compare aux voix deja connues. A passer avant "
        "toute chasse. Pour 'est-ce bien la meme voix', 'verifie mes "
        "reperes'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "reperes": {
                "type": "string",
                "description": ("Les moments separes par des virgules : "
                                "'episode@4:23, episode@13:31'."),
            },
            "duree": {
                "type": "number",
                "description": "Secondes prises a chaque moment, 7 par defaut.",
            },
        },
        "required": ["reperes"],
    },
    lent=True,
    phrase_attente="Je verifie que ce sont bien les memes voix.",
)
@enfile("verification de voix")
def verifier_reperes(reperes: str, duree: float = 7.0) -> str:
    lot = _decoder_reperes(reperes, duree)
    if not lot:
        return ("Je n ai pas compris les moments. Ecris-les "
                "« nom-du-fichier@4:23 », separes par des virgules.")
    connus = {}
    if CERTIFIES.exists():
        for chemin in sorted(CERTIFIES.iterdir()):
            if chemin.is_dir() and any(chemin.glob("*.wav")):
                connus[chemin.name] = str(chemin)
    demande = {"reperes": lot, "connus": connus,
               "modele": voix_clonee.MODELE, "travail": str(TRAVAIL),
               "ecoute": str(CANDIDATS / "_reperes"),
               "ffmpeg": _ffmpeg()}
    texte = _lancer(_VERIFIER, demande, "verifier.log")
    if "ENTRE EUX" not in texte:
        return "La verification a echoue. Fin du journal :\n" + texte[-1200:]
    utile = texte[texte.index("ENTRE EUX"):].replace("\nFINI", "").strip()
    return (utile + "\n\nMeme personne au-dela de 0,65 ; en dessous de 0,55, "
            "deux personnes differentes — ou deux doublages differents, ce "
            "qui revient au meme pour la chasse.\n\nLes fenetres prelevees "
            "sont dans %s : si les chiffres ne s accordent pas, ecoute-les, "
            "c est souvent qu une fenetre a attrape le voisin."
            % (CANDIDATS / "_reperes"))


@outil(
    nom="chasser_voix",
    description=(
        "Cherche les repliques d un personnage dans une collection d "
        "episodes, a partir de moments repares a l oreille ou de ses "
        "extraits deja certifies. Rend une liste numerotee a ecouter. Pour "
        "'trouve-moi les repliques de X', 'chasse la voix de X'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "qui": {"type": "string", "description": "Le personnage."},
            "ou": {"type": "string",
                   "description": "Dossier des episodes, ou un episode seul."},
            "reperes": {
                "type": "string",
                "description": ("Moments « episode@4:23 » separes par des "
                                "virgules. Inutile si le personnage a deja "
                                "des extraits certifies."),
            },
            "combien": {"type": "number",
                        "description": "Candidats a proposer, 40 par defaut."},
            "seuil": {"type": "number",
                      "description": "Ressemblance minimale, 0,55 par defaut."},
        },
        "required": ["qui", "ou"],
    },
    lent=True,
    phrase_attente="Je cherche sa voix dans les episodes, ca prendra un moment.",
)
@enfile("chasse de voix", "qui")
def chasser_voix(qui: str, ou: str, reperes: str = "", combien: float = 40,
                 seuil: float = 0.55) -> str:
    nom = voix_clonee._plat(qui)
    episodes = _episodes(ou)
    if not episodes:
        return "Je ne trouve pas d episodes dans %s." % ou
    lot = _decoder_reperes(reperes, 7.0) if reperes else []
    siens = CERTIFIES / nom
    a_des_certifies = siens.exists() and any(siens.glob("*.wav"))
    if not lot and not a_des_certifies:
        return ("Il me faut un point de depart : donne-moi un ou deux moments "
                "ou tu es sur de l entendre, ecrits « episode@4:23 ». Sans "
                "ca je chercherais au hasard — c est l erreur qui m a coute "
                "une nuit entiere sur Randy.")

    repoussoirs = []
    if CERTIFIES.exists():
        repoussoirs = [str(p) for p in sorted(CERTIFIES.iterdir())
                       if p.is_dir() and p.name != nom and any(p.glob("*.wav"))]

    demande = {
        "qui": nom, "reperes": lot, "certifies": str(siens),
        "repoussoirs": repoussoirs, "episodes": episodes,
        "travail": str(TRAVAIL), "sortie": str(CANDIDATS / nom),
        "modele": voix_clonee.MODELE, "ffmpeg": _ffmpeg(),
        "mini": 2.0, "maxi": 11.0, "seuil": float(seuil), "marge": 0.0,
        "doublon": 0.95,
        "centile": 90, "vise": 2400.0, "combien": int(combien),
    }
    texte = _lancer(_CHASSER, demande, "chasse-%s.log" % nom)
    if "LISTE" not in texte:
        return "La chasse n a rien donne. Fin du journal :\n" + texte[-1200:]
    liste = texte[texte.index("LISTE"):].split("\n", 1)[1]
    liste = liste.replace("\nFINI", "").strip()
    return ("Les candidats sont dans %s.\n\n%s\n\n"
            "Ecoute-les et dis-moi les numeros a garder. Pour un extrait "
            "mixte, precise ou : « 12 debut 2 » garde les deux premieres "
            "secondes, « 15 fin 2 » retire les deux dernieres, « 29 tete 1 » "
            "retire la premiere. Je decoupe au lieu de jeter."
            % (CANDIDATS / nom, liste))


@outil(
    nom="garder_voix",
    description=(
        "Range les candidats valides a l oreille parmi les extraits certifies "
        "d un personnage, et refait sa reference. Pour 'garde les numeros 1, "
        "3 et 7', 'je garde le 2 sauf la derniere seconde'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "qui": {"type": "string", "description": "Le personnage."},
            "numeros": {
                "type": "string",
                "description": ("Les numeros a garder, separes par des "
                                "virgules. Une decoupe se precise apres le "
                                "numero : « 4 debut 2 » garde les deux "
                                "premieres secondes, « 12 queue 2 » les deux "
                                "dernieres, « 15 fin 2 » retire les deux "
                                "dernieres, « 29 tete 1 » retire la premiere, "
                                "« 38 bouts 0-4 6-99 » garde ces morceaux."),
            },
        },
        "required": ["qui", "numeros"],
    },
)
def garder_voix(qui: str, numeros: str) -> str:
    nom = voix_clonee._plat(qui)
    recolte = TRAVAIL / ("recolte-%s.pkl" % nom)
    if not recolte.exists():
        return "Je n ai pas de recolte pour %s. Lance d abord la chasse." % qui
    garde = _decoder_numeros(numeros)
    if not garde:
        return ("Je n ai pas compris les numeros. Separe-les par des "
                "virgules ; pour une decoupe, « 4 debut 2 » ou « 15 fin 2 ».")
    demande = {"qui": nom, "recolte": str(recolte),
               "certifies": str(CERTIFIES / nom), "garde": garde,
               "reference": str(voix_clonee.EXTRAITS / ("%s.wav" % nom))}
    texte = _lancer(_GARDER, demande, "garder-%s.log" % nom)
    lignes = [l for l in texte.splitlines()
              if l.startswith(("AJOUTES", "TOTAL", "REFERENCE"))]
    if not lignes:
        return "Le rangement a echoue. Journal :\n" + texte[-1000:]
    resume = ("\n".join(lignes).replace("AJOUTES", "Ajoutes :")
              .replace("TOTAL", "En tout :")
              .replace("REFERENCE", "Reference refaite :"))
    return (resume + "\n\nSa reference est a jour : les films l emploieront "
            "des maintenant. Au-dela de cinq minutes d extraits, un "
            "reentrainement vaut le coup.")
