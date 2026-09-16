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
# que Serge reclamait depuis le debut. Quand le modele existe on l emploie,
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
        morceaux.append(("mmh", "mmmh", "mmmmh")[min(2, _syllabes(mot) - 1)]
                        + ponctuation)
    rendu = "".join(morceaux).strip()
    return rendu or "mmph mmph"


def _plat(nom):
    # Les accents deviennent leur lettre nue : « gerald » accentue doit
    # retrouver son extrait, pas devenir « g-rald ».
    nu = unicodedata.normalize("NFD", (nom or "").lower())
    nu = "".join(c for c in nu if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", nu).strip("-")


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


for piece in demande["pieces"]:
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
            moteur.tts_to_file(text=_b, speaker_wav=piece["extrait"],
                               language=demande.get("langue", "fr"),
                               file_path=_f, split_sentences=False,
                               gpt_cond_len=60, gpt_cond_chunk_len=6,
                               temperature=0.95, repetition_penalty=8.0,
                               speed=1.08)
            _y, _sr = librosa.load(_f, sr=None, mono=True)
            if _trop_court(_y, _b, _sr):
                moteur.tts_to_file(text=_b, speaker_wav=piece["extrait"],
                                   language=demande.get("langue", "fr"),
                                   file_path=_f, split_sentences=False,
                                   gpt_cond_len=60, gpt_cond_chunk_len=6,
                                   temperature=0.85, repetition_penalty=2.0,
                                   speed=1.08)
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
    moteur.tts_to_file(text=piece["texte"], speaker_wav=piece["extrait"],
                       language=demande.get("langue", "fr"),
                       file_path=piece["sortie"], split_sentences=False,
                       gpt_cond_len=60, gpt_cond_chunk_len=6,
                       # Ces trois reglages agissent sur la diction. Compares
                       # a l oreille sur la meme phrase : le debit vif et les
                       # repetitions penalisees donnent une parole plus seche,
                       # plus proche du dessin anime que du livre audio.
                       # Ils n apprennent rien au modele pour autant — seul
                       # un re-entrainement le ferait.
                       temperature=0.95, repetition_penalty=8.0, speed=1.08)
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
        extrait = extrait_de(personnage)
        if not extrait:
            continue
        # Ce qui est dit une fois n est pas redit : on garde sous l empreinte
        # du texte, de la voix et de l extrait qui l a produite.
        fin = modele_fin(personnage)
        # Le modele entre dans l empreinte : sinon un reentrainement laisserait
        # le cache servir l ancienne voix indefiniment.
        cle = hashlib.sha1(
            ("%s|%s|%s|%d|%s|v7" % (_plat(personnage), texte, langue,
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
                        "modele_fin": str(fin) if fin else ""})

    if a_faire:
        demande = {"modele": MODELE, "langue": langue,
                   "pieces": [{k: p[k] for k in
                                ("texte", "extrait", "sortie", "etouffe",
                                 "modele_fin")}
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
