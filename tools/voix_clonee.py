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
import unicodedata
import subprocess
import tempfile
from pathlib import Path

from core.dossiers import dossier

MOTEUR = Path(r"F:\IA\voix\.venv\Scripts\python.exe")
MODELE = "tts_models/multilingual/multi-dataset/xtts_v2"

# Les modeles reentraines personnage par personnage. Le clonage a la volee
# rend le timbre ; seul un reentrainement rend le phrase, et c est le phrase
# que l utilisateur reclamait depuis le debut. Quand le modele existe on l emploie,
# sinon on retombe sur le clonage : aucun personnage n est casse par
# l absence du sien.
MODELES_FINS = Path(r"F:\IA\voix\entrainement")
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
        morceaux.append(("mmph", "mmmpph", "mmmmpph")[min(2, _syllabes(mot) - 1)]
                        + ponctuation)
    rendu = "".join(morceaux).strip()
    return rendu or "mmph mmph"


# Table de chiffrement Kenny South Park (base 3 : M=0, P=1, F=2).
# Source : dcode.fr/code-kenny-southpark — chaque lettre -> trigramme unique.
# A=MMM, B=MMP, C=MMF, D=MPM ... Z=FFP.
# La sequence de phonemes mm/pp/ff est sans rapport avec les mots d origine :
# un auditeur ne peut pas reconstituer le texte, meme en ecoutant attentivement.
_TABLE_KENNY = {
    chr(ord("a") + i): "".join("MPF"[d] for d in [(i // 9) % 3,
                                                    (i // 3) % 3,
                                                    i % 3])
    for i in range(26)
}


def kenny_code(texte):
    """Encode le texte en mumble Kenny compact : UN son par lettre (pas trois).

    On prend le premier caractere du trigramme South Park (M/P/F) et on le
    convertit en une onomatopee monosyllabique : M->mmm, P->mph, F->mff.
    Resultat 3x plus court qu avec le trigramme complet, toujours incomprehensible.
    """
    nu = unicodedata.normalize("NFD", (texte or "").lower())
    nu = "".join(c for c in nu if unicodedata.category(c) != "Mn")
    _SON1 = {"M": "mmm", "P": "mph", "F": "mff"}
    mots = []
    for bout in nu.split():
        lettres = [c for c in bout if c.isalpha()]
        if not lettres:
            continue
        sons = " ".join(_SON1[_TABLE_KENNY.get(c, "MMM")[0]] for c in lettres)
        mots.append(sons)
    return " ".join(mots) or "mmm"


def _plat(nom):
    # Les accents deviennent leur lettre nue : « gerald » accentue doit
    # retrouver son extrait, pas devenir « g-rald ».
    nu = unicodedata.normalize("NFD", (nom or "").lower())
    nu = "".join(c for c in nu if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", nu).strip("-")


# Deux personnages, un seul comedien : Mr Garrison parle avec la voix traquee
# sur Mrs Garrison (saisons 9 a 11), c est la meme personne dans la serie.
MEME_VOIX = {"garrison": "mrsgarrison", "mr-garrison": "mrsgarrison"}


def extrait_de(personnage):
    """L extrait de reference d un personnage, s il en a un."""
    cle = _plat(personnage)
    cle = MEME_VOIX.get(cle, cle)
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

# Reglages calmes (t 0,75 / penalite 2) : essayes puis abandonnes, le modele
# s emballe bien plus souvent (Manu : 9 phrases sur 60 etirees, jusqu a 21 s).
CALMES = set()

# Le modele reentraine de Stan (8 septembre) perd plus de mots que le clonage
# sur la reference actuelle : WER 0,07 a 0,12 contre 0,04. On le contourne.
SANS_MODELE_FIN = {"stan"}

# Ce qui parle sous un tissu. La valeur est celle qu on a mesuree sur la
# serie : le centre spectral de Kenny tombe a 1780 hertz quand celui des
# autres enfants tient les 3000.
ETOUFFES = {"kenny": 1900.0}

# Personnages court-circuitant XTTS : on utilise edge_tts + ffmpeg a la place.
# XTTS clone une voix pour produire des tokens "mmph" et le resultat sonne faux.
# edge_tts avec filtre parka (highpass+lowpass+echo) reproduit bien la paroi
# de la parka de Kenny.
PARKA = {"kenny"}

# Personnages dont l extrait est trop court pour XTTS (< 6 s) ou dont le clone
# sonne robotique / adulte. On leur attribue directement une voix edge_tts
# juvenile sans passer par le clonage.
# Stan : extrait de 6 s -> clone robotique. Voix canadienne juvenile choisie
# pour son accent proche de l americain de South Park.
VOIX_ENFANTS = {
}


def _banque_kenny():
    """Les vrais marmonnements de Kenny, decoupes dans son extrait de reference.

    Chaque bout de son continu (au dessus d un seuil, lacunes de moins de 60 ms
    recollees) devient une piece de la banque. Rend (pieces, frequence), ou
    (None, None) si l extrait manque ou n est pas un wav lisible.
    """
    import wave
    import numpy as np
    f = extrait_de("kenny")
    if not f:
        return None, None
    try:
        with wave.open(str(f)) as w:
            sr = w.getframerate()
            x = np.frombuffer(w.readframes(w.getnframes()),
                              dtype="<i2").astype(np.float32) / 32768.0
            if w.getnchannels() > 1:
                x = x.reshape(-1, w.getnchannels()).mean(1)
    except Exception:
        return None, None
    pas = int(0.010 * sr)
    n = len(x) // pas
    if n < 50:
        return None, None
    r = np.sqrt((x[:n * pas].reshape(n, pas) ** 2).mean(1))
    actif = r > 0.08 * np.percentile(r, 98)
    bouts, i = [], 0
    while i < n:
        if actif[i]:
            j = i
            while j < n and actif[j]:
                j += 1
            if bouts and i - bouts[-1][1] < 6:
                bouts[-1][1] = j
            else:
                bouts.append([i, j])
            i = j
        else:
            i += 1
    banque = []
    fondu = int(0.008 * sr)
    for a, b in bouts:
        seg = x[a * pas:b * pas].copy()
        if len(seg) < 0.09 * sr:
            continue
        niveau = float(np.sqrt((seg ** 2).mean())) + 1e-6
        seg = seg / niveau * 0.1
        if _f0_median(seg, sr) > KENNY_F0_MAX:
            continue
        seg[:fondu] *= np.linspace(0, 1, fondu)
        seg[-fondu:] *= np.linspace(1, 0, fondu)
        banque.append(seg.astype(np.float32))
    return (banque, sr) if len(banque) >= 10 else (None, None)


# Au dela, ce n est plus un marmonnement mais un cri ou un rire aigu : la
# mediane de la banque est a 315 Hz, ces pieces sont a 430-490 Hz.
KENNY_F0_MAX = 430.0


def _f0_median(x, sr):
    """Hauteur mediane d un son, par autocorrelation sur des trames de 40 ms.
    Rend 0.0 si rien n est voise."""
    import numpy as np
    n, h = int(0.04 * sr), int(0.02 * sr)
    lo, hi = int(sr / 500), int(sr / 70)
    fs = []
    for i in range(0, len(x) - n, h):
        t = x[i:i + n] - x[i:i + n].mean()
        if np.sqrt((t ** 2).mean()) < 0.03:
            continue
        ac = np.correlate(t, t, "full")[n - 1:]
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] / (ac[0] + 1e-9) > 0.35:
            fs.append(sr / k)
    return float(np.median(fs)) if fs else 0.0


# Duree d une syllabe de Kenny : le francais parle a environ cinq syllabes par
# seconde, pauses comprises.
KENNY_SYLLABE = 0.15


def _kenny_banque(texte, sortie):
    """Kenny avec ses vrais mmph : une piece de sa banque par mot, de la duree
    du mot dit normalement, les pauses de la ponctuation gardees."""
    import hashlib
    import random
    import wave
    import numpy as np
    from scipy.signal import resample_poly
    banque, sr = _banque_kenny()
    if not banque:
        return None
    hasard = random.Random(int(hashlib.sha1(
        (texte or "").encode("utf-8")).hexdigest()[:8], 16))
    morceaux, dernier, idx = [], -1, 0
    mots_du_texte = re.findall(r"[^\s]+", texte or "")
    nb_mots = len(mots_du_texte)
    for bout in mots_du_texte:
        idx += 1
        mot = re.sub(r"[^\w'\u00e0-\u00ff]", "", bout)
        ponct = "".join(c for c in bout if c in ",.;:!?")
        if mot:
            cible = KENNY_SYLLABE * _syllabes(mot)
            ordre = sorted(range(len(banque)),
                           key=lambda k: abs(len(banque[k]) / sr - cible))
            choix = [k for k in ordre[:6] if k != dernier] or ordre[:1]
            k = hasard.choice(choix)
            dernier = k
            seg = banque[k]
            # Jamais accelere : accelerer un son le rend plus aigu, et Kenny
            # sonnait comme s il riait. On ralentit au plus de 15 %, et la
            # phrase baisse un peu vers sa fin comme une phrase dite.
            avance = (idx + 1.0) / max(1, nb_mots)
            facteur = min(1.0, max(0.85, (len(seg) / sr) / cible))
            facteur *= 0.96 if avance > 0.66 else 0.99
            seg = resample_poly(seg, 100, int(round(100 * facteur)))
            maxi = int(cible * 1.1 * sr)
            if len(seg) > maxi:
                seg = seg[:maxi].copy()
                f = int(0.02 * sr)
                seg[-f:] *= np.linspace(1, 0, f)
            morceaux.append(seg.astype(np.float32))
            morceaux.append(np.zeros(int(0.04 * sr), dtype=np.float32))
        if ponct:
            pause = 0.30 if any(c in ponct for c in ".!?:;") else 0.15
            morceaux.append(np.zeros(int(pause * sr), dtype=np.float32))
    if not morceaux:
        return None
    y = np.concatenate(morceaux)
    y = y / (np.abs(y).max() + 1e-6) * 0.95
    with wave.open(str(sortie), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((y * 32767).astype("<i2").tobytes())
    return str(sortie) if Path(sortie).exists() else None


def _kenny_edge_tts(texte, sortie):
    """Synthese Kenny : tokens mmph via edge_tts + filtre parka South Park."""
    import asyncio
    try:
        import edge_tts
    except ImportError:
        return None
    texte_mmph = kenny_code(texte)

    async def _synth():
        comm = edge_tts.Communicate(texte_mmph, "en-US-GuyNeural",
                                    rate="+10%", pitch="+8Hz")
        donnees = b""
        async for m in comm.stream():
            if m["type"] == "audio":
                donnees += m["data"]
        return donnees

    mp3 = asyncio.run(_synth())
    if not mp3:
        return None
    tmp = str(sortie) + ".tmp.mp3"
    Path(tmp).write_bytes(mp3)
    exe = r"C:\Program Files\DownloadHelper CoApp\ffmpeg.exe"
    try:
        from tools.clip import _ffmpeg as _get_ffmpeg
        exe = _get_ffmpeg() or exe
    except Exception:
        pass
    subprocess.run(
        [exe, "-y", "-i", tmp,
         "-af", ("highpass=f=80,lowpass=f=350,"
                 "aecho=0.7:0.3:22:0.4,volume=3.0"),
         str(sortie)],
        capture_output=True, timeout=60
    )
    try:
        Path(tmp).unlink()
    except Exception:
        pass
    return str(sortie) if Path(sortie).exists() else None


def _enfant_edge_tts(texte, sortie, voix, rate, pitch):
    """Synthese directe edge_tts pour les personnages dont XTTS donne de mauvais resultats."""
    import asyncio
    try:
        import edge_tts
    except ImportError:
        return None

    async def _synth():
        comm = edge_tts.Communicate(texte, voix, rate=rate, pitch=pitch)
        donnees = b""
        async for m in comm.stream():
            if m["type"] == "audio":
                donnees += m["data"]
        return donnees

    mp3 = asyncio.run(_synth())
    if not mp3:
        return None
    tmp = str(sortie) + ".tmp.mp3"
    Path(tmp).write_bytes(mp3)
    exe = r"C:\Program Files\DownloadHelper CoApp\ffmpeg.exe"
    try:
        from tools.clip import _ffmpeg as _get_ffmpeg
        exe = _get_ffmpeg() or exe
    except Exception:
        pass
    subprocess.run(
        [exe, "-y", "-i", tmp, str(sortie)],
        capture_output=True, timeout=60
    )
    try:
        Path(tmp).unlink()
    except Exception:
        pass
    return str(sortie) if Path(sortie).exists() else None


PROGRAMME = """
import sys, json, os, wave, re
# Le moteur tourne sous un Python dont la sortie est en cp1252. Le
# message d erreur d XTTS contient un emoji : l afficher tuait le
# processus au moment meme ou il rattrapait l erreur, et emportait
# toutes les repliques suivantes avec lui.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import librosa
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


_fins = {}


def _charger_fin(chemin):
    # Le modele reentraine d un personnage, garde une seule fois en memoire :
    # le recharger par replique couterait plus cher que la synthese.
    if chemin in _fins:
        return _fins[chemin]
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts
    racine = os.path.join(os.path.expanduser("~"), "AppData", "Local", "tts",
                          "tts_models--multilingual--multi-dataset--xtts_v2")
    config = XttsConfig()
    config.load_json(os.path.join(racine, "config.json"))
    m = Xtts.init_from_config(config)
    m.load_checkpoint(config, checkpoint_path=chemin,
                      vocab_path=os.path.join(racine, "vocab.json"),
                      use_deepspeed=False)
    m.cuda().eval()
    _fins[chemin] = m
    return m


def _ecrire(chemin, x, sr):
    x = np.asarray(x, dtype=np.float32)
    x = x / (np.abs(x).max() + 1e-6) * 0.95
    with wave.open(chemin, "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr)
        f.writeframes((x * 32767).astype("<i2").tobytes())


# XTTS previent des deux cent soixante-treize caracteres et refuse au-dela de
# quatre cents jetons. On reste largement en dessous : plus la phrase est
# longue, plus le modele s arrete tot, bien avant sa limite affichee.
LONGUEUR = 190


def _morceaux(texte, taille=LONGUEUR):
    # Le texte en morceaux dicibles, coupes la ou la voix respirerait : les
    # fins de phrase d abord, les virgules ensuite, les mots en dernier
    # recours. Jamais au milieu d un mot : la prononciation en dependrait.
    texte = " ".join((texte or "").split())
    if len(texte) <= taille:
        return [texte] if texte else []

    bouts = [b for b in re.split(r"(?<=[.!?])\\s+", texte) if b.strip()]
    fins = []
    for bout in bouts:
        if len(bout) <= taille:
            fins.append(bout)
            continue
        grains = [g for g in re.split(r"(?<=[,;:])\\s+", bout) if g.strip()]
        tampon = ""
        for grain in grains:
            if len(grain) > taille:
                # Aucune respiration ecrite sur tout ce passage : on coupe aux
                # mots, faute de mieux.
                if tampon:
                    fins.append(tampon)
                    tampon = ""
                ligne = ""
                for mot in grain.split():
                    if ligne and len(ligne) + 1 + len(mot) > taille:
                        fins.append(ligne)
                        ligne = mot
                    else:
                        ligne = (ligne + " " + mot).strip()
                tampon = ligne
                continue
            if tampon and len(tampon) + 1 + len(grain) > taille:
                fins.append(tampon)
                tampon = grain
            else:
                tampon = (tampon + " " + grain).strip()
        if tampon:
            fins.append(tampon)
    return [f for f in fins if f.strip()]


def _recoller(bouts, sr):
    # Bout a bout, avec un souffle court entre : la coupe tombe sur une
    # virgule ou un point, ou la voix aurait respire de toute maniere.
    if not bouts:
        return np.zeros(1, dtype=np.float32)
    if len(bouts) == 1:
        return np.asarray(bouts[0], dtype=np.float32)
    souffle = np.zeros(int(0.16 * sr), dtype=np.float32)
    assemble = []
    for i, b in enumerate(bouts):
        assemble.append(np.asarray(b, dtype=np.float32))
        if i < len(bouts) - 1:
            assemble.append(souffle)
    return np.concatenate(assemble)


def _trop_court(onde, texte, sr):
    # Le modele s est-il arrete avant la fin ? Le francais se debite autour de
    # quinze caracteres la seconde ; en dessous de la moitie de ce qu on
    # attend, la phrase a ete coupee, pas dite vite.
    attendu = len(texte) / 15.0
    return attendu > 1.0 and (len(onde) / float(sr)) < attendu * 0.5


def _trop_long(n_echantillons, texte, sr):
    # Le modele s emballe parfois : la phrase s etire d une queue de souffle ou
    # de babillage (8 s pour trois secondes de texte, jusqu a 21 s chez Manu).
    # Debit normal : environ vingt caracteres la seconde. Au dela de 1,7 fois
    # ce temps plus 1,2 s, la phrase est etiree.
    return (n_echantillons / float(sr)) > 1.7 * len(texte) / 20.0 + 1.2


def _clone_a_fichier(texte, extrait, langue, fichier, T, R, S):
    # Clone une replique dans `fichier`. Si elle est etiree on la redit, deux
    # fois au plus, et on garde la plus courte : seule celle qui s est emballee
    # est etiree, une phrase saine n est jamais rallongee.
    essais = []
    for k in range(3):
        tmp = "%s.e%d.wav" % (fichier, k)
        moteur.tts_to_file(text=texte, speaker_wav=extrait, language=langue,
                           file_path=tmp, split_sentences=False,
                           gpt_cond_len=60, gpt_cond_chunk_len=6,
                           temperature=T, repetition_penalty=R, speed=S)
        y, sr = librosa.load(tmp, sr=None, mono=True)
        essais.append((len(y), tmp))
        if not _trop_long(len(y), texte, sr):
            break
        print("phrase etiree (%.1f s), on la redit" % (len(y) / float(sr)))
    meilleur = min(essais)[1]
    os.replace(meilleur, fichier)
    for _, tmp in essais:
        if tmp != meilleur and os.path.exists(tmp):
            os.remove(tmp)


for piece in demande["pieces"]:
    _T, _R, _S = (0.75, 2.0, 1.0) if piece.get("calme") else (0.95, 8.0, 1.08)
    fin = piece.get("modele_fin")
    if fin and os.path.exists(fin):
        # Un modele reentraine sur ce personnage rend le phrase, que le
        # clonage a la volee ne rend pas. Meme reference, memes reglages :
        # seul le modele change. Si quoi que ce soit echoue on retombe sur
        # le clonage, pour qu un film ne se perde jamais la-dessus.
        try:
            m = _charger_fin(fin)
            # L empreinte vocale est calculee une fois pour toute la replique :
            # les morceaux la partagent, donc le timbre ne bouge pas de l un a
            # l autre.
            gpt, orateur = m.get_conditioning_latents(
                audio_path=[piece["extrait"]], gpt_cond_len=30,
                max_ref_length=60)
            langue = demande.get("langue", "fr")
            bouts = []
            for bout in _morceaux(piece["texte"]):
                onde = m.inference(bout, langue, gpt, orateur,
                                   temperature=0.95,
                                   repetition_penalty=8.0)["wav"]
                if _trop_court(onde, bout, 24000):
                    # Une penalite forte pousse le modele vers le jeton d arret
                    # quand la phrase s allonge. On redit ce morceau avec la
                    # penalite ordinaire, et on garde le meilleur des deux.
                    second = m.inference(bout, langue, gpt, orateur,
                                         temperature=0.85,
                                         repetition_penalty=2.0)["wav"]
                    if len(second) > len(onde):
                        onde = second
                bouts.append(np.asarray(onde, dtype=np.float32))
            _ecrire(piece["sortie"], _recoller(bouts, 24000), 24000)
            print("modele reentraine : %s" % os.path.basename(piece["sortie"]))
            if piece.get("etouffe"):
                etouffer(piece["sortie"], piece["etouffe"])
            continue
        except Exception as souci:
            print("modele reentraine indisponible (%r), on clone" % (souci,))
    # Le clonage a la volee bute sur la meme limite : on lui donne aussi le
    # texte par morceaux, et on recolle. Le decoupage maison vaut mieux que
    # split_sentences, qui ne sait pas couper une phrase unique trop longue.
    _bouts = _morceaux(piece["texte"])
    if len(_bouts) > 1:
        _ondes, _sr = [], 24000
        for _b in _bouts:
            _f = piece["sortie"] + ".bout.wav"
            _clone_a_fichier(_b, piece["extrait"], demande.get("langue", "fr"),
                             _f, _T, _R, _S)
            _y, _sr = librosa.load(_f, sr=None, mono=True)
            if _trop_court(_y, _b, _sr):
                moteur.tts_to_file(text=_b, speaker_wav=piece["extrait"],
                                   language=demande.get("langue", "fr"),
                                   file_path=_f, split_sentences=False,
                                   gpt_cond_len=60, gpt_cond_chunk_len=6,
                                   temperature=min(_T, 0.85), repetition_penalty=2.0,
                                   speed=_S)
                _z, _sr = librosa.load(_f, sr=None, mono=True)
                if len(_z) > len(_y):
                    _y = _z
            _ondes.append(_y)
            try:
                os.remove(_f)
            except Exception:
                pass
        _ecrire(piece["sortie"], _recoller(_ondes, _sr), _sr)
        if piece.get("etouffe"):
            etouffer(piece["sortie"], piece["etouffe"])
        continue
    # Debit vif et repetitions penalisees : compares a l oreille, une parole plus
    # seche, plus proche du dessin anime que du livre audio.
    _clone_a_fichier(piece["texte"], piece["extrait"],
                     demande.get("langue", "fr"), piece["sortie"],
                     _T, _R, _S)
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



def modele_fin(personnage):
    # Le modele reentraine de ce personnage, s il en a un. On prend le plus
    # recent : chaque reprise d entrainement ecrit un nouveau dossier, et
    # c est toujours le dernier qui a le plus appris.
    nom = _plat(personnage)
    nom = MEME_VOIX.get(nom, nom)
    lot = []
    for dossier in MODELES_FINS.glob("voix-%s-*" % nom):
        poids = dossier / "best_model.pth"
        if poids.exists():
            lot.append((poids.stat().st_mtime, poids))
    return max(lot)[1] if lot else None

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
        # Personnages avec voix edge_tts directe (extrait trop court pour XTTS)
        enfant = VOIX_ENFANTS.get(_plat(personnage))
        if enfant:
            result = _enfant_edge_tts(texte, sortie, *enfant)
            if result:
                resultats[str(sortie)] = str(sortie)
            continue
        # Kenny et autres parka : edge_tts + filtre ffmpeg, pas XTTS
        if _plat(personnage) in PARKA:
            result = (_kenny_banque(texte, sortie)
                      or _kenny_edge_tts(texte, sortie))
            if result:
                resultats[str(sortie)] = str(sortie)
            continue

        extrait = extrait_de(personnage)
        if not extrait:
            continue
        # Ce qui est dit une fois n est pas redit : on garde sous l empreinte
        # du texte, de la voix et de l extrait qui l a produite.
        fin = None if _plat(personnage) in SANS_MODELE_FIN else modele_fin(personnage)
        # Le modele entre dans l empreinte : sinon un reentrainement laisserait
        # le cache servir l ancienne voix indefiniment.
        cle = hashlib.sha1(
            ("%s|%s|%s|%d|%s|v12" % (_plat(personnage), texte, langue,
                                    extrait.stat().st_size,
                                    fin.stat().st_mtime if fin else "-")
             ).encode("utf-8")
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
                        "etouffe": ETOUFFES.get(_plat(personnage)),
                        "modele_fin": str(fin) if fin else "",
                        "calme": _plat(personnage) in CALMES})

    if a_faire:
        demande = {"modele": MODELE, "langue": langue,
                   "pieces": [{k: p[k] for k in
                                ("texte", "extrait", "sortie", "etouffe",
                                 "modele_fin", "calme")}
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
