"""
Assistant vocal local, avec mot d'activation et actions.

Dites « Hey Jarvis », parlez, taisez-vous. Il repond et agit.
Chaine : openWakeWord -> faster-whisper -> Claude (+ outils) -> ElevenLabs/SAPI

Architecture : les outils vivent dans tools/ (auto-decouverts via core.registre),
les reglages et secrets dans config.yaml (via core.config).

Usage : uv run python jarvis14.py
"""

import os
import queue
import re
import subprocess
import threading
import time
import wave
from collections import deque
from pathlib import Path

# Magasin de certificats Windows (comme git) au lieu du bundle certifi.
# Indispensable si un antivirus/proxy intercepte le TLS, sinon les appels HTTPS
# (Claude, Gmail) echouent avec "certificate verify failed". Avant tout reseau.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

import numpy as np
import openwakeword
import sounddevice as sd
from faster_whisper import WhisperModel
from openwakeword.model import Model as WakeModel

from core import config, journal, memoire, personnalite, registre, voix
from core.util import sans_accents
from tools.lumieres import allumer_si_nuit, charger_pieces_hue

# ---------------------------------------------------------------- reglages

MICRO = config.reglage("audio.micro", 1)
# None = sortie audio par defaut de Windows (suit l'enceinte/casque actif).
HAUT_PARLEUR = config.reglage("audio.haut_parleur", None)

# Le choix du modele LLM (Claude/Ollama) et de la voix (ElevenLabs/Piper) est gere
# par les providers (core/llm.py, core/tts.py), selon config.yaml (mode: cloud|local).
MODELE_WHISPER = config.reglage("whisper.modele", "medium")

TAUX = 16000
BLOC = 1280

SEUIL_REVEIL = 0.5
SEUIL_INTERRUPTION = 0.7   # plus strict : le micro entend aussi l'enceinte
SEUIL_PAROLE_SUR = 0.025
BLOCS_AVANT_VERIF = 5      # 5 x 80 ms = 0,4 s de parole continue
DELAI_ENTRE_VERIFS = 1.0
SEUIL_SILENCE = 0.010
SILENCE_FIN = 1.2
DUREE_MAX = 20

# Fenetre de suivi : apres une reponse, Jarvis reste a l'ecoute ce nombre de
# secondes pour enchainer une nouvelle demande sans redire "Hey Jarvis".
DUREE_SUITE = config.reglage("assistant.duree_suite", 10)

LOG = journal.obtenir()

# Sentinel renvoye par repondre() quand une action attend une confirmation vocale.
SENTINEL_CONFIRM = "__confirmation__"

# Regles de base (format vocal, outils). La personnalite (persona) est ajoutee
# devant, et la memoire derriere, par _refaire_systeme.
SYSTEME_BASE = (
    "REGLE ABSOLUE : une seule phrase courte (10 mots max). Jamais de question de confirmation (pas de 'est-ce que', 'est-ce bien', 'est-ce ce que'). "
    "(une seule si possible), sans listes, sans titres, sans asterisques ni emoji. "
    "Parle naturellement, en francais. Va a l'essentiel. Ne pose jamais deux fois "
    "la meme question et ne redemande pas une confirmation deja demandee. "
    "Tu disposes d'outils pour agir sur l'ordinateur : utilise-les SYSTEMATIQUEMENT "
    "quand l'utilisateur demande une action physique. "
    "JAMAIS de reponse purement orale si un outil existe : "
    "stop/arrete/coupe/ferme un film ou VLC = appelle stopper_film ; "
    "lance jeu/appli = appelle ouvrir_application. "
    "Confirme brievement apres l'appel. "
    "Quand l'utilisateur exprime une preference, mentionne un proche ou parle d'un "
    "projet en cours, appelle remember pour t'en souvenir, sans le commenter. "
    "Pour les mails : prepare un brouillon avec preparer_mail et lis-le ; appelle "
    "envoyer_mail quand l'utilisateur veut envoyer (le systeme demandera confirmation). "
    "Si la question fait reference a ce qui est affiche (qu'est-ce que c'est, lis "
    "ca, cette erreur, mon ecran, ce message), appelle capture_screen puis reponds "
    "d'apres l'image."
)

# Consigne systeme courante (persona + regles + memoire). Passee a chaque appel
# Claude via le parametre `system`, distinct de la liste des messages.
SYSTEME_COURANT = SYSTEME_BASE


def _refaire_systeme(memoire_courante):
    """Recompose la consigne systeme : personnalite + regles + memoire."""
    global SYSTEME_COURANT
    nom_persona = config.reglage("assistant.personnalite", personnalite.DEFAUT)
    persona = personnalite.persona(nom_persona)
    SYSTEME_COURANT = (persona + "\n\n" + SYSTEME_BASE
                       + memoire.texte_pour_systeme(memoire_courante))


# ---------------------------------------------------------------- HUD (option)

try:
    import hud
except Exception:
    hud = None

_dernier_etat_hud = None


def _hud(methode, *args):
    """Relaie un appel au HUD sans jamais interrompre l'assistant."""
    if hud is None:
        return
    global _dernier_etat_hud
    if methode == "etat":
        if args and args[0] == _dernier_etat_hud:
            return
        _dernier_etat_hud = args[0] if args else None
    try:
        getattr(hud, methode)(*args)
    except Exception:
        pass


def _niv_hud(bloc):
    """Convertit le niveau brut du micro en une valeur 0..1 pour le coeur."""
    return min(1.0, niveau(bloc) / 0.2)


# ---------------------------------------------------------------- audio


def niveau(bloc_float):
    return float(np.sqrt(np.mean(bloc_float**2)))


def jouer(chemin_wav):
    with wave.open(str(chemin_wav), "rb") as f:
        taux = f.getframerate()
        donnees = f.readframes(f.getnframes())
    audio = np.frombuffer(donnees, dtype=np.int16)
    sd.play(audio, samplerate=taux, device=HAUT_PARLEUR)
    sd.wait()


def bip(frequence=880, duree=0.12):
    t = np.linspace(0, duree, int(TAUX * duree), endpoint=False)
    onde = (0.25 * np.sin(2 * np.pi * frequence * t)).astype(np.float32)
    sd.play(onde, samplerate=TAUX, device=HAUT_PARLEUR)
    sd.wait()


_PROCESSUS_PAROLE = None
_INTERRUPTION = threading.Event()
_PARLE = threading.Event()   # vrai UNIQUEMENT pendant que Jarvis joue de l'audio :
                             # c'est la seule fenetre ou on ecoute une interruption.


def couper_parole():
    """Arrete immediatement la synthese en cours (ElevenLabs ou SAPI)."""
    _INTERRUPTION.set()
    try:
        sd.stop()          # coupe la lecture ElevenLabs sur le haut-parleur
    except Exception:
        pass
    processus = _PROCESSUS_PAROLE
    if processus is not None and processus.poll() is None:
        try:
            processus.terminate()
        except OSError:
            pass


def _jouer_audio(audio, frequence):
    """Joue un tableau int16 mono sur le haut-parleur, interruptible."""
    if _INTERRUPTION.is_set():
        return
    sd.play(audio, samplerate=frequence, device=HAUT_PARLEUR)
    while not _INTERRUPTION.is_set():
        courant = sd.get_stream()
        if courant is None or not courant.active:
            break
        time.sleep(0.03)
    if _INTERRUPTION.is_set():
        sd.stop()


def dire(texte, interruptible=True):
    """Prononce un texte via le provider TTS courant (ElevenLabs en cloud, Piper en
    local) ; repli sur la voix Windows (SAPI) si le provider est indisponible.

    interruptible=False : le barge-in est desactive pendant cette phrase (utilise
    pour la question de confirmation : la reponse de l'utilisateur est un oui/non,
    pas une interruption)."""
    if _INTERRUPTION.is_set():
        return
    from core.tts import tts
    resultat = tts().synthetiser(texte)
    if interruptible:
        _PARLE.set()      # a partir d'ici Jarvis parle : on peut l'interrompre
    try:
        if resultat is not None:
            _jouer_audio(*resultat)
        else:
            _dire_sapi(texte)
    finally:
        _PARLE.clear()    # fin de la parole : plus d'interruption possible


def _dire_sapi(texte):
    """Synthese vocale Windows (SAPI), voix francaise si disponible.

    Le texte est envoye au script PowerShell par l'entree standard, jamais dans
    -Command : une apostrophe francaise ne peut pas casser le littoral. Le flux
    stdin est lu en UTF-8. L'appel est interruptible via couper_parole().
    """
    global _PROCESSUS_PAROLE

    if _INTERRUPTION.is_set():
        return

    script = (
        "[Console]::InputEncoding = [Text.Encoding]::UTF8; "
        "$t = [Console]::In.ReadToEnd(); "
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$fr = $s.GetInstalledVoices() | "
        "Where-Object { $_.VoiceInfo.Culture.Name -like 'fr*' } | "
        "Select-Object -First 1; "
        "if ($fr) { $s.SelectVoice($fr.VoiceInfo.Name) }; "
        "$s.Rate = 1; "
        "$s.Speak($t)"
    )

    processus = subprocess.Popen(
        ["powershell", "-NoProfile", "-Command", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    _PROCESSUS_PAROLE = processus
    try:
        _, erreurs = processus.communicate(input=texte.encode("utf-8"))
        if processus.returncode and not _INTERRUPTION.is_set():
            details = (erreurs or b"").decode("utf-8", "replace").strip()
            print(f"  [SAPI] echec (code {processus.returncode}) : {details}")
    finally:
        _PROCESSUS_PAROLE = None


# ---------------------------------------------------------------- nettoyage

# Ce que Whisper entend a la place de "Hey Jarvis" quand le tampon
# glissant en rattrape la fin.
RESIDUS = (
    "avis", "service", "jarvis", "hey jarvis", "harvis", "arvis",
    "javis", "charvis", "chavis", "davis", "y a vis", "a vis",
    "la vis", "et vis", "ervice", "servi", "sers vis",
    # Second mot de reveil : sans ca, dire "Maman" pendant la fenetre
    # d ecoute est pris pour une commande (et finit en remember()).
    "maman", "mamans", "mamant", "manman", "mamane", "hey maman",
)

# Ce que Whisper invente quand il n'entend que du silence.
HALLUCINATIONS = (
    "amara.org", "sous-titres", "sous titres", "merci d'avoir regarde",
    "abonnez-vous", "abonnez vous", "a la prochaine video",
    "n'oubliez pas de vous abonner", "sous-titrage",
)

# Mots qui coupent la parole PUIS relancent l'ecoute (tu veux redire quelque chose).
MOTS_RELANCE = (
    "attends", "attend", "arrete", "arrete-toi", "arrete toi", "stop",
    "une seconde", "deux secondes", "minute", "pardon", "non non",
)

# Mots qui coupent la parole et terminent (tu as fini, il se tait).
MOTS_FIN = (
    "tais-toi", "tais toi", "chut", "silence", "ferme-la", "la ferme",
    "c'est bon", "ok merci", "d'accord merci", "laisse tomber",
)

# Mots d'accord pour une confirmation vocale.
MOTS_OUI = (
    "oui", "ouais", "ouep", "vas-y", "vas y", "confirme", "confirmer",
    "d'accord", "daccord", "ok", "okay", "envoie", "envoi", "fais",
    "yes", "carrement", "bien sur", "parfait", "valide", "valider",
)


def type_arret(texte):
    """Renvoie 'relance', 'fin' ou None selon l'ordre d'arret detecte."""
    plat = sans_accents(texte)
    plat = "".join(c if c.isalnum() or c in " '-" else " " for c in plat)
    if any(m in plat for m in MOTS_RELANCE):
        return "relance"
    if any(m in plat for m in MOTS_FIN):
        return "fin"
    return None


def _est_oui(texte):
    """Vrai si la transcription exprime un accord (oui/vas-y/confirme...)."""
    if not texte:
        return False
    plat = sans_accents(texte)
    plat = "".join(c if c.isalnum() or c in " '-" else " " for c in plat)
    return any(m in plat for m in MOTS_OUI)


def nettoyer(texte):
    """Retire le residu du mot d'activation en tete de transcription."""
    t = texte.strip()

    plat = sans_accents(t)
    if any(h in plat for h in HALLUCINATIONS):
        return ""

    # Cas "Avis, ouvre YouTube" ou "Jarvis : ouvre YouTube"
    tete = None
    for sep in (",", ":", ".", "!", "?"):
        if sep in t[:20]:
            avant, _, apres = t.partition(sep)
            if len(avant.split()) <= 3:
                tete, reste = avant, apres
                break

    if tete is not None:
        if tete.strip().lower().strip("'’") in RESIDUS:
            t = reste.strip()

    # Cas sans ponctuation : "Jarvis ouvre YouTube"
    mots = t.split()
    if mots and mots[0].lower().strip(",.:;!?") in RESIDUS:
        t = " ".join(mots[1:])

    return t.strip()


# ---------------------------------------------------------------- parole en flux

FIN_PHRASE = re.compile(r"(.+?[.!?…]+[\s ]*|.+?\n)", re.S)


def _parleur(fil):
    """Thread qui lit les phrases au fur et a mesure qu'elles arrivent."""
    while True:
        phrase = fil.get()
        if phrase is None:
            break
        if _INTERRUPTION.is_set():
            continue
        texte = phrase.strip()
        if texte:
            dire(texte)


def dire_en_flux(morceaux):
    """Consomme un generateur de fragments et les dit phrase par phrase."""
    fil = queue.Queue()
    thread = threading.Thread(target=_parleur, args=(fil,), daemon=True)
    thread.start()

    tampon = ""
    complet = []
    try:
        for fragment in morceaux:
            if _INTERRUPTION.is_set():
                break
            if not fragment:
                continue
            tampon += fragment
            complet.append(fragment)
            while True:
                trouve = FIN_PHRASE.match(tampon)
                if not trouve:
                    break
                phrase = trouve.group(1)
                tampon = tampon[len(phrase):]
                if len(phrase.strip()) >= 2:
                    fil.put(phrase)
        if tampon.strip():
            fil.put(tampon)
    finally:
        fil.put(None)
        thread.join()

    return "".join(complet).strip()


# ---------------------------------------------------------------- dialogue


def _executer_outils(blocs):
    """Execute les outils demandes par Claude et renvoie leurs resultats.

    S'appuie sur le registre. Logge chaque appel, ne crashe jamais (une
    exception d'outil devient une reponse comprehensible), et met les outils a
    confirmation en attente au lieu de les executer tout de suite.
    """
    resultats = []
    for bloc in blocs:
        if getattr(bloc, "type", None) != "tool_use":
            continue
        nom = bloc.name
        arguments = bloc.input or {}
        outil = registre.get(nom)

        if outil is None:
            resultat = f"Outil inconnu : {nom}"
        elif outil.confirmation:
            resultat = registre.mettre_en_attente(outil, arguments)
        else:
            try:
                resultat = outil.fonction(**arguments)
            except Exception:
                LOG.exception("outil %s a plante (args=%s)", nom, arguments)
                resultat = "Desole, je n'ai pas reussi a faire ca."

        LOG.info("outil %s args=%s -> %s", nom, arguments, str(resultat)[:200])

        # Cas image (capture d'ecran) : bloc image dans le tool_result.
        if isinstance(resultat, dict) and resultat.get("image"):
            img = resultat["image"]
            apercu = resultat.get("apercu", "Capture d'ecran envoyee.")
            print(f"  [outil] {nom}({arguments}) -> {apercu}")
            _hud("outil", nom, apercu[:60])
            contenu = [{
                "type": "image",
                "source": {"type": "base64", "media_type": img["media_type"],
                           "data": img["data"]},
            }]
        else:
            print(f"  [outil] {nom}({arguments}) -> {str(resultat)[:80]}")
            _hud("outil", nom, str(resultat)[:60])
            contenu = str(resultat)

        resultats.append({
            "type": "tool_result",
            "tool_use_id": bloc.id,
            "content": contenu,
        })

        if nom in ("remember", "forget", "changer_personnalite"):
            _refaire_systeme(memoire.charger())

    return resultats


def repondre(historique):
    """Interroge Claude et boucle sur les appels d'outils jusqu'a la reponse.

    Pour les outils lents, prononce un accuse de reception en parallele. Pour
    les outils a confirmation, prononce l'annonce et renvoie SENTINEL_CONFIRM
    (la suite est geree par traiter, qui capture la reponse oui/non).
    """
    from core.llm import llm
    fournisseur = llm()
    if not fournisseur.disponible():
        mode = config.reglage("mode", "cloud")
        if mode == "local":
            return ("Le modele local (Ollama) n'est pas joignable. Verifie qu'Ollama "
                    "tourne et que le modele est telecharge.")
        return "Ma cle Claude n'est pas configuree."

    fil_accuse = None
    accuse_donne = False

    while True:
        if _INTERRUPTION.is_set():
            if fil_accuse:
                fil_accuse.join(timeout=2)
            return ""
        try:
            reponse = fournisseur.repondre(
                SYSTEME_COURANT, historique,
                registre.schemas_api(local_seulement=(fournisseur.nom == "Ollama")))
        except Exception as e:
            print(f"  [{fournisseur.nom}] erreur : {e}")
            LOG.exception("appel LLM en echec")
            return "Je n'arrive pas a joindre le modele pour le moment."

        if reponse.stop_reason == "tool_use":
            _hud("etat", "reflexion")
            noms = [b.name for b in reponse.content
                    if getattr(b, "type", None) == "tool_use"]
            if (not accuse_donne and not _INTERRUPTION.is_set()
                    and any(n in registre.noms_lents() for n in noms)):
                accuse_donne = True
                _hud("etat", "parole")
                fil_accuse = threading.Thread(
                    target=dire, args=(registre.phrase_attente(noms),), daemon=True)
                fil_accuse.start()

            historique.append({"role": "assistant", "content": reponse.content})
            resultats = _executer_outils(reponse.content)
            historique.append({"role": "user", "content": resultats})

            annonce = registre.annonce_en_attente()
            if annonce:
                if fil_accuse:
                    fil_accuse.join()
                _hud("etat", "parole")
                if not _INTERRUPTION.is_set():
                    dire(annonce + " Tu confirmes ?", interruptible=False)
                return SENTINEL_CONFIRM
            continue

        # Reponse finale. On attend la fin de l'accuse pour ne pas parler dessus.
        if fil_accuse:
            fil_accuse.join()
        texte = " ".join(
            b.text for b in reponse.content if getattr(b, "type", None) == "text"
        ).strip()
        historique.append({"role": "assistant", "content": texte})
        _hud("etat", "parole")
        if texte and not _INTERRUPTION.is_set():
            dire(texte)
        return texte


# ---------------------------------------------------------------- whisper


def _ajouter_dll_nvidia():
    """Rend les DLL cuBLAS et cuDNN visibles pour faster-whisper."""
    racines = []
    try:
        import nvidia
        racines = [Path(p) for p in getattr(nvidia, "__path__", [])]
    except ImportError:
        pass

    if not racines:
        import sysconfig
        base = Path(sysconfig.get_paths()["purelib"]) / "nvidia"
        if base.exists():
            racines = [base]

    dossiers = []
    for racine in racines:
        dossiers.extend(racine.glob("*/bin"))
        dossiers.extend(racine.glob("*/lib"))

    for dossier in dossiers:
        chemin = str(dossier)
        if chemin not in os.environ["PATH"]:
            os.environ["PATH"] = chemin + os.pathsep + os.environ["PATH"]
        try:
            os.add_dll_directory(chemin)
        except (OSError, AttributeError):
            pass


def charger_whisper():
    """Charge Whisper sur GPU si possible, sinon sur CPU."""
    _ajouter_dll_nvidia()

    try:
        modele = WhisperModel(MODELE_WHISPER, device="cuda", compute_type="float16")
        modele.transcribe(np.zeros(TAUX, dtype=np.float32), language="fr")
        print(f"Whisper {MODELE_WHISPER} sur GPU.")
        return modele
    except Exception as e:
        print(f"GPU indisponible ({type(e).__name__}), bascule sur CPU.")

    for taille in (MODELE_WHISPER, "small"):
        try:
            modele = WhisperModel(taille, device="cpu", compute_type="int8")
            print(f"Whisper {taille} sur CPU.")
            return modele
        except Exception:
            continue

    raise RuntimeError("Impossible de charger Whisper.")


# ---------------------------------------------------------------- principal


def capturer(flux, tampon):
    """Enregistre depuis le micro jusqu'au silence. Renvoie l'audio ou None."""
    morceaux = list(tampon)
    debut = time.time()
    dernier_son = time.time()

    while True:
        bloc, _ = flux.read(BLOC)
        bloc = bloc.flatten()
        morceaux.append(bloc)
        _hud("niveau", _niv_hud(bloc))

        if niveau(bloc) > SEUIL_SILENCE:
            dernier_son = time.time()
        if time.time() - dernier_son > SILENCE_FIN:
            break
        if time.time() - debut > DUREE_MAX:
            print("  (trop long, je coupe)")
            break

    tampon.clear()
    audio = np.concatenate(morceaux)
    return audio if len(audio) >= TAUX * 0.6 else None


def attendre_suite(flux, tampon, duree=DUREE_SUITE):
    """Ecoute quelques secondes apres une reponse, sans mot d'activation.

    Renvoie True si l'utilisateur recommence a parler, False si silence.
    """
    _hud("etat", "ecoute")
    tampon.clear()
    debut = time.time()
    blocs_voix = 0
    while time.time() - debut < duree:
        try:
            bloc, _ = flux.read(BLOC)
        except Exception:
            return False
        bloc = bloc.flatten()
        tampon.append(bloc)
        _hud("niveau", _niv_hud(bloc))
        if niveau(bloc) > SEUIL_PAROLE_SUR:
            blocs_voix += 1
            if blocs_voix >= 3:
                return True
        else:
            blocs_voix = 0
    return False


def repondre_en_ecoutant(historique, flux, reveil, whisper):
    """Repond tout en surveillant le micro (mot d'activation ou ordre d'arret).

    Renvoie (texte, interrompu, relancer).
    """
    _INTERRUPTION.clear()
    resultat = {}

    def travail():
        try:
            resultat["texte"] = repondre(historique)
        except Exception as e:
            resultat["erreur"] = e

    thread = threading.Thread(target=travail, daemon=True)
    thread.start()

    interrompu = False
    relancer = False

    # Detection d'un ordre ("attends", "stop"...) prononce PAR-DESSUS Jarvis. Le micro
    # entend aussi l'enceinte : on suit en continu le niveau de reference (l'echo de
    # Jarvis) et on ne reagit que si tu parles nettement PLUS FORT que cet echo. On
    # transcrit alors seulement TON extrait (pas les 2 s dominees par la voix de Jarvis).
    facteur = float(config.reglage("interruption.facteur", 1.8))
    seuil_min = float(config.reglage("interruption.seuil", SEUIL_PAROLE_SUR))
    blocs_requis = int(config.reglage("interruption.blocs", BLOCS_AVANT_VERIF))
    debug = bool(config.reglage("interruption.debug", False))

    base = None            # niveau moyen de l'echo de Jarvis (suivi en continu)
    tampon = []            # audio de TA parole par-dessus
    blocs_sur = 0
    derniere_verif = 0.0

    while thread.is_alive():
        try:
            bloc, _ = flux.read(BLOC)
        except Exception:
            break
        bloc = bloc.flatten()
        _hud("niveau", _niv_hud(bloc))

        # On ne surveille l'interruption QUE pendant que Jarvis parle vraiment.
        # Pendant qu'il reflechit (appel LLM, outils), on ne coupe rien : la reponse
        # ne peut donc pas etre "perdue" par une fausse detection avant d'etre dite.
        if not _PARLE.is_set():
            base = None
            blocs_sur = 0
            tampon = []
            continue

        # voie 1 : le mot d'activation
        scores = reveil.predict((bloc * 32767).astype(np.int16))
        if max(scores.values()) >= SEUIL_INTERRUPTION:
            couper_parole()
            interrompu, relancer = True, True
            print("  [micro] Je me tais.")
            break

        # voie 2 : un ordre d'arret prononce par-dessus
        niv = niveau(bloc)
        if base is None:
            base = niv
        seuil_sur = max(seuil_min, base * facteur)
        base = 0.97 * base + 0.03 * niv    # suit lentement l'echo de Jarvis

        if niv > seuil_sur:
            tampon.append(bloc)
            blocs_sur += 1
        else:
            if 0 < blocs_sur < blocs_requis:
                tampon = []                # trop court : simple bruit, on oublie
            blocs_sur = 0

        # On ne coupe QUE si on reconnait un mot d'arret ("attends", "stop"...)
        # dans ce que tu dis par-dessus. Ainsi Jarvis ne peut jamais se couper
        # lui-meme (sa propre voix n'est pas un mot d'arret) : pas de boucle.
        maintenant = time.time()
        if (blocs_sur >= blocs_requis
                and maintenant - derniere_verif > DELAI_ENTRE_VERIFS):
            derniere_verif = maintenant
            extrait = np.concatenate(tampon[-30:])
            tampon = []
            blocs_sur = 0
            try:
                segments, _ = whisper.transcribe(extrait, language="fr", beam_size=1)
                dit = " ".join(s.text for s in segments).strip()
            except Exception:
                dit = ""
            if debug:
                print(f"  [micro debug] niv={niv:.3f} base={base:.3f} "
                      f"seuil={seuil_sur:.3f} -> entendu={dit!r}")
            categorie = type_arret(dit) if dit else None
            if categorie:
                couper_parole()
                interrompu = True
                relancer = (categorie == "relance")
                action = "Je t'ecoute" if relancer else "Compris"
                print(f"  [micro] {action} : {dit}")
                break

    thread.join(timeout=10)
    reveil.reset()

    if "erreur" in resultat:
        raise resultat["erreur"]

    return resultat.get("texte", ""), interrompu, relancer


def _confirmer(interrompu, relancer, whisper, historique, flux):
    """Capture la reponse oui/non a une demande de confirmation et agit."""
    if interrompu:
        registre.annuler_confirme()
        return "", relancer

    _INTERRUPTION.clear()
    _hud("etat", "ecoute")
    audio_conf = capturer(flux, deque())
    reponse = ""
    if audio_conf is not None:
        seg, _ = whisper.transcribe(audio_conf, language="fr", beam_size=5)
        reponse = nettoyer(" ".join(s.text for s in seg).strip())
    print(f"  [confirmation] {reponse or '(rien)'}")

    if _est_oui(reponse):
        res = registre.executer_confirme()
    else:
        registre.annuler_confirme()
        res = "D'accord, j'annule."

    _hud("etat", "parole")
    if res and not _INTERRUPTION.is_set():
        dire(res)
    historique.append({"role": "assistant", "content": res})
    return res, False


def _tronquer(historique):
    if len(historique) > 40:
        del historique[:len(historique) - 40]
        # Claude exige que la conversation commence par un vrai tour utilisateur.
        while historique and not (
            historique[0]["role"] == "user"
            and isinstance(historique[0]["content"], str)
        ):
            historique.pop(0)


def traiter(audio, whisper, historique, flux, reveil):
    """Transcrit, repond, parle. Renvoie True si on doit enchainer (relance)."""
    import numpy as _np
    _rms = float(_np.sqrt(_np.mean(audio**2)))
    _dur = len(audio) / TAUX
    print(f"  [debug] durée={_dur:.1f}s  RMS={_rms:.5f}", flush=True)
    segments, _info = whisper.transcribe(audio, language="fr", beam_size=5)
    segs = list(segments)
    _brut = " ".join(s.text for s in segs).strip()
    print(f"  [debug] whisper brut={_brut!r}", flush=True)
    question = nettoyer(_brut)

    if not question or len(question) < 3:
        print("  (rien compris)\n")
        return False

    # --- raccourcis deterministes (voir core/raccourcis.py) ---
    # Le modele local se trompe souvent d'outil : toutes les commandes
    # courantes sont reconnues ici et executees sans passer par le LLM.
    from core import raccourcis as _raccourcis
    _res = _raccourcis.essayer(question)
    if _res:
        _hud("dire_vous", question)
        _hud("dire_jarvis", _res)
        print(f"  Vous : {question}")
        print(f"  Jarvis : {_res}\n")
        dire(_res)
        return False
    # --- fin raccourcis ---

    print(f"  Vous : {question}")
    _hud("dire_vous", question)
    _hud("etat", "reflexion")
    historique.append({"role": "user", "content": question})

    texte, interrompu, relancer = repondre_en_ecoutant(historique, flux, reveil, whisper)

    if texte == SENTINEL_CONFIRM:
        texte, relancer = _confirmer(interrompu, relancer, whisper, historique, flux)
    elif not texte:
        texte = "C'est fait."
        if not interrompu:
            _hud("etat", "parole")
            dire(texte)

    _hud("dire_jarvis", texte)
    print(f"  Jarvis : {texte}\n")
    _tronquer(historique)
    return relancer


def main():
    print("Chargement des modeles...")

    registre.charger_outils()
    voix.definir_parleur(dire)

    # Second mot de reveil « Maman » : fil separe, n'influence jamais la
    # detection « Hey Jarvis » ci-dessous. Voir core/reveil_maman.py.
    from core.reveil_maman import DetecteurMaman
    _cfg_maman = config.reglage("reveil_maman", {}) or {}
    maman = DetecteurMaman(
        actif=_cfg_maman.get("actif", True),
        seuil_voix=_cfg_maman.get("seuil_voix", 0.020),
        garde=_cfg_maman.get("garde", 3.0),
        modele=_cfg_maman.get("modele", "tiny"),
    )
    _persona_normale = _cfg_maman.get("persona_normale", "neutre")
    _bascule_persona = _cfg_maman.get("bascule_persona", True)

    reveil = WakeModel(wakeword_model_paths=[str(
        Path(openwakeword.__file__).parent / "resources" / "models" / "hey_jarvis_v0.1.onnx"
    )])

    whisper = charger_whisper()

    # Les appels telephoniques reutilisent ce Whisper pour transcrire les reponses.
    from tools.appels import definir_transcripteur
    definir_transcripteur(lambda chemin: " ".join(
        s.text for s in whisper.transcribe(chemin, language="fr", beam_size=5)[0]).strip())
    # V2 (conversation temps reel) : transcription d'un tableau audio (16kHz float32).
    from tools.appel_direct import definir_transcripteur_direct
    definir_transcripteur_direct(lambda audio: " ".join(
        s.text for s in whisper.transcribe(audio, language="fr", beam_size=1)[0]).strip())

    charger_pieces_hue()
    allumer_si_nuit()

    from tools.presence import demarrer_presence
    demarrer_presence()

    from tools.discord_bot import demarrer_discord
    demarrer_discord()

    from tools.instagram import demarrer_refresh_instagram
    demarrer_refresh_instagram()

    from core.llm import llm
    _fournisseur = llm()
    print(f"Mode : {config.reglage('mode', 'cloud')} — LLM {_fournisseur.nom}, "
          f"TTS {__import__('core.tts', fromlist=['tts']).tts().nom}.")
    if not _fournisseur.disponible():
        if config.reglage("mode", "cloud") == "local":
            print("ATTENTION : Ollama injoignable. Lance 'ollama serve' et verifie le "
                  "modele (config ollama.modele).")
        else:
            print("ATTENTION : aucune cle Claude dans config.yaml (anthropic.cle). "
                  "L'assistant ne pourra pas repondre.")

    _hud("demarrer")
    _hud("config", _fournisseur.nom, f"whisper {MODELE_WHISPER}")

    faits = memoire.charger()
    if faits:
        print(f"Memoire : {len(faits)} information(s).")
    _refaire_systeme(faits)
    historique = []

    flux = sd.InputStream(
        samplerate=TAUX, channels=1, dtype="float32",
        device=MICRO, blocksize=BLOC,
    )
    flux.start()

    _mots_reveil = 'Hey Jarvis' + (' ou Maman' if maman.actif else '')
    print(f'\nPret. Dites "{_mots_reveil}". Ctrl+C pour quitter.\n')
    print('Vous pouvez le couper en redisant "Hey Jarvis" pendant qu\'il parle.\n')

    tampon = deque(maxlen=6)
    enchainer = False

    try:
        while True:
            suite = enchainer
            if not enchainer:
                bloc, _ = flux.read(BLOC)
                bloc = bloc.flatten()
                tampon.append(bloc)

                _hud("etat", "veille")
                _hud("niveau", _niv_hud(bloc))

                maman.alimenter(bloc)
                par_maman = maman.declenche()

                scores = reveil.predict((bloc * 32767).astype(np.int16))
                if not par_maman and max(scores.values()) < SEUIL_REVEIL:
                    continue
                reveil.reset()
                maman.vider()

                # Reveil par « Maman » : on bascule en personnalite MU-TH-UR.
                # Reveil par « Hey Jarvis » : on revient a la personnalite
                # habituelle. Rien n'est ecrit si le mode est deja le bon.
                if _bascule_persona:
                    _voulue = "mere" if par_maman else _persona_normale
                    if config.reglage("assistant.personnalite", "") != _voulue:
                        config.definir("assistant.personnalite", _voulue)
                        _refaire_systeme(memoire.charger())
                if par_maman:
                    print("  [micro] Reveil par Maman.")

            enchainer = False
            _hud("etat", "ecoute")
            if not suite:
                print("  [micro] Oui ?")
                bip()

            audio = capturer(flux, tampon)
            if audio is None:
                print("  (rien entendu)\n")
                continue

            if traiter(audio, whisper, historique, flux, reveil):
                enchainer = True
                continue

            print(f"  [micro] J'ecoute encore {int(DUREE_SUITE)} s...")
            enchainer = attendre_suite(flux, tampon)
            if not enchainer:
                _hud("etat", "veille")

    except KeyboardInterrupt:
        print("\nAu revoir.")
    finally:
        couper_parole()
        flux.stop()
        flux.close()


if __name__ == "__main__":
    main()
