"""Installation de Jarvis et de MU-TH-UR.

Pose les questions necessaires, et seulement celles-la. Chaque service est
facultatif : refuser Spotify n'empeche pas le reste de fonctionner, la
fonction est simplement absente.

Deux principes de fond :

- **rien n'est devine.** Ce qui depend de la machine — chemins, peripheriques
  audio, applications installees — est releve, pas suppose.
- **rien n'est transmis.** Les identifiants saisis vont directement dans le
  fichier de reglages local, ne sont jamais reaffiches, et ce fichier n'est
  jamais distribue.

Usage : python installer.py
"""
import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parent
CONFIG = RACINE / "config.yaml"
VENV = RACINE / ".venv"

PYTHON_MIN = (3, 11)
MODELE_OLLAMA = "qwen2.5:7b"
VOIX_PIPER = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/"
    "upmc/medium/fr_FR-upmc-medium.onnx",
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/"
    "upmc/medium/fr_FR-upmc-medium.onnx.json",
)


# --------------------------------------------------------------- affichage

def titre(texte):
    print()
    print("  " + texte.upper())
    print("  " + "-" * len(texte))


def dire(texte=""):
    print("  " + texte if texte else "")


def demander(question, defaut="", secret=False):
    """Pose une question. Une reponse vide garde la valeur par defaut."""
    indication = f" [{defaut}]" if defaut and not secret else ""
    while True:
        if secret:
            reponse = getpass.getpass(f"  {question} : ").strip()
        else:
            reponse = input(f"  {question}{indication} : ").strip()
        if reponse:
            return reponse
        if defaut or not secret:
            return defaut


def oui_non(question, defaut=True):
    marque = "O/n" if defaut else "o/N"
    while True:
        r = input(f"  {question} ({marque}) : ").strip().lower()
        if not r:
            return defaut
        if r in ("o", "oui", "y", "yes"):
            return True
        if r in ("n", "non", "no"):
            return False


# --------------------------------------------------------------- etapes

def verifier_python():
    titre("verification de python")
    if sys.version_info < PYTHON_MIN:
        dire(f"Python {'.'.join(map(str, PYTHON_MIN))} ou plus recent est "
             f"necessaire ; celui-ci est {platform.python_version()}.")
        dire("Installe-le depuis python.org, puis relance ce fichier.")
        raise SystemExit(1)
    dire(f"Python {platform.python_version()} : convient.")


def environnement():
    titre("environnement et dependances")
    if VENV.exists():
        dire("Environnement deja present, il sera reutilise.")
    else:
        dire("Creation de l'environnement isole...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)

    py = VENV / ("Scripts" if os.name == "nt" else "bin") / "python"
    dire("Installation des dependances — quelques minutes.")
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip", "-q"],
                   check=False)
    r = subprocess.run([str(py), "-m", "pip", "install", "-r",
                        str(RACINE / "requirements.txt"), "-q"])
    if r.returncode:
        dire("Une dependance n'a pas pu s'installer. Relance ce fichier ;")
        dire("si cela persiste, note le message ci-dessus.")
        raise SystemExit(1)
    dire("Dependances installees.")
    return py


def ollama():
    titre("moteur de langage local")
    exe = shutil.which("ollama")
    if exe is None:
        for c in (r"C:\Users\%s\AppData\Local\Programs\Ollama\ollama.exe"
                  % os.environ.get("USERNAME", ""),):
            if Path(c).exists():
                exe = c
    if exe is None:
        dire("Ollama fait tourner le modele sur ta machine — rien ne sort")
        dire("de chez toi. Il n'est pas installe.")
        dire()
        if oui_non("L'installer maintenant ?"):
            r = subprocess.run(["winget", "install", "--id", "Ollama.Ollama",
                                "--accept-package-agreements",
                                "--accept-source-agreements"])
            if r.returncode:
                dire("L'installation a echoue. Telecharge-le sur ollama.com,")
                dire("puis relance ce fichier.")
                raise SystemExit(1)
            exe = shutil.which("ollama") or exe
        else:
            dire("Sans lui, Jarvis ne pourra pas repondre. Installe-le depuis")
            dire("ollama.com puis relance ce fichier.")
            raise SystemExit(1)

    dire(f"Telechargement du modele {MODELE_OLLAMA} — environ 4,7 Go.")
    subprocess.run([exe or "ollama", "pull", MODELE_OLLAMA])
    dire("Modele pret.")


def voix():
    titre("voix de synthese")
    dossier = RACINE / "voix"
    dossier.mkdir(exist_ok=True)
    cible = dossier / "fr_FR-upmc-medium.onnx"
    if cible.exists():
        dire("Voix francaise deja presente.")
        return
    dire("Telechargement de la voix francaise — environ 60 Mo.")
    for url in VOIX_PIPER:
        nom = dossier / url.rsplit("/", 1)[-1]
        try:
            urllib.request.urlretrieve(url, nom)
        except Exception as e:
            dire(f"Echec du telechargement : {str(e)[:60]}")
            dire("Jarvis utilisera la voix de Windows en attendant.")
            return
    dire("Voix installee.")


# --------------------------------------------------------------- reglages

def reglages():
    titre("reglages")
    dire("Chaque service est facultatif. Passe ceux qui ne t'interessent pas :")
    dire("la fonction sera simplement absente, rien ne sera casse.")
    dire()

    c = {}

    # --- identite
    c["utilisateur"] = {
        "nom": demander("Ton prenom", os.environ.get("USERNAME", "").title()),
    }
    c["assistant"] = {"personnalite": "jarvis"}

    # --- cerveau
    dire()
    dire("Jarvis peut reflechir en local (gratuit, prive, plus lent) ou via")
    dire("l'API d'Anthropic (payante, nettement meilleure sur les outils).")
    if oui_non("Utiliser une cle Anthropic en plus du modele local ?", False):
        cle = demander("Cle API (elle ne sera pas affichee)", secret=True)
        if cle:
            c["anthropic"] = {"cle": cle, "modele": "claude-haiku-4-5"}
    c["ollama"] = {"modele": MODELE_OLLAMA}
    c["whisper"] = {"modele": demander(
        "Modele de transcription (tiny, base, small, medium)", "small")}
    c["voix_locale"] = "edge"

    # --- services facultatifs
    dire()
    if oui_non("Configurer Spotify ?", False):
        dire("Cree une application sur developer.spotify.com, puis :")
        c["spotify"] = {
            "client_id": demander("Identifiant client", secret=True),
            "client_secret": demander("Cle secrete", secret=True),
        }
        dire("Le jeton d'acces sera demande au premier usage.")

    dire()
    if oui_non("Configurer Plex ?", False):
        c["plex"] = {
            "hote": demander("Adresse du serveur", "http://127.0.0.1:32400"),
            "jeton": demander("Jeton Plex", secret=True),
        }

    dire()
    if oui_non("Configurer la lecture des mails (Gmail) ?", False):
        dire("Il faut un mot de passe d'application, pas ton mot de passe")
        dire("habituel : myaccount.google.com puis Securite.")
        c["mail"] = {
            "adresse": demander("Adresse Gmail"),
            "mot_de_passe_app": demander("Mot de passe d'application",
                                         secret=True),
        }

    dire()
    if oui_non("Utiliser les televisions Chromecast ?", True):
        dire("Elles seront trouvees toutes seules sur le reseau.")
        dire("Pour myCANAL, Netflix ou Prime, tu te connecteras une fois")
        dire("dans le navigateur dedie que Jarvis ouvrira.")
        c["netflix"] = {"profil": demander(
            "Nom de ton profil Netflix, si tu en as un", "")}

    # --- interface
    dire()
    c["hud"] = {
        "port": int(demander("Port de l'interface web", "8770")),
        "hote": "0.0.0.0" if oui_non(
            "Rendre l'interface accessible depuis ton telephone ?", True)
        else "127.0.0.1",
        "https": True,
    }
    c["systeme"] = {"delai_extinction": 45}

    return c


def ecrire_config(c):
    titre("enregistrement des reglages")
    if CONFIG.exists():
        sauve = CONFIG.with_suffix(".yaml.avant")
        shutil.copyfile(CONFIG, sauve)
        dire(f"Reglages precedents conserves dans {sauve.name}.")

    exemple = RACINE / "config.example.yaml"
    base = {}
    if exemple.exists():
        try:
            import yaml
            base = yaml.safe_load(exemple.read_text(encoding="utf-8")) or {}
        except Exception:
            base = {}

    def fondre(a, b):
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                fondre(a[k], v)
            else:
                a[k] = v
        return a

    final = fondre(base, c)
    try:
        import yaml
        CONFIG.write_text(
            yaml.safe_dump(final, allow_unicode=True, sort_keys=True),
            encoding="utf-8")
    except Exception:
        CONFIG.write_text(json.dumps(final, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    dire("Reglages ecrits dans config.yaml.")
    dire("Ce fichier contient tes identifiants : ne le partage jamais.")


def recenser_applications(py):
    titre("applications installees")
    dire("Recensement des programmes de ta machine, pour pouvoir les lancer")
    dire("a la voix.")
    r = subprocess.run(
        [str(py), "-c",
         "import core.registre as R; R.charger_outils();"
         " from tools.recenser import recenser;"
         " print(recenser())"],
        cwd=str(RACINE), capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if r.returncode == 0 and r.stdout.strip():
        dire(r.stdout.strip()[:200])
    else:
        dire("Recensement automatique indisponible ; tu pourras ajouter tes")
        dire("applications a la voix : « ajoute telle application ».")


def raccourcis(py):
    titre("raccourcis")
    if os.name != "nt":
        dire("Raccourcis non crees : systeme non Windows.")
        return
    bureau = Path(os.environ["USERPROFILE"]) / "Desktop"
    lancer = RACINE / "lancer_jarvis.bat"
    lancer.write_text(
        "@echo off\r\n"
        f'cd /d "{RACINE}"\r\n'
        f'"{py}" jarvis14.py\r\n'
        "pause\r\n", encoding="utf-8")

    arreter = RACINE / "arreter_jarvis.bat"
    if not arreter.exists():
        arreter.write_text(
            "@echo off\r\n"
            "powershell -NoProfile -Command \"Get-CimInstance Win32_Process |"
            " Where-Object { $_.CommandLine -like '*jarvis14.py*' } |"
            " ForEach-Object { Stop-Process -Id $_.ProcessId -Force }\"\r\n"
            "echo Jarvis est arrete.\r\n"
            "pause\r\n", encoding="utf-8")

    for source, nom, icone in ((lancer, "Jarvis", "shell32.dll,44"),
                               (arreter, "Arreter Jarvis", "shell32.dll,27")):
        ps = (
            "$s = New-Object -ComObject WScript.Shell; "
            f"$r = $s.CreateShortcut('{bureau / (nom + '.lnk')}'); "
            f"$r.TargetPath = '{source}'; "
            f"$r.WorkingDirectory = '{RACINE}'; "
            f"$r.IconLocation = '{icone}'; $r.Save()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True)
    dire("Deux raccourcis places sur le Bureau : Jarvis, et Arreter Jarvis.")


# --------------------------------------------------------------- deroule

def main():
    print()
    print("  ================================================")
    print("    JARVIS  ·  assistant vocal local")
    print("    avec l'interface MU-TH-UR")
    print("  ================================================")
    dire()
    dire("Cette installation prend une quinzaine de minutes, dont l'essentiel")
    dire("en telechargements. Tu peux la relancer sans risque.")
    dire()
    if not oui_non("Commencer ?"):
        return

    verifier_python()
    py = environnement()
    ollama()
    voix()
    c = reglages()
    ecrire_config(c)
    recenser_applications(py)
    raccourcis(py)

    titre("installation terminee")
    dire("Lance Jarvis par le raccourci du Bureau, puis dis « Hey Jarvis ».")
    dire()
    dire("Interface sur ce PC      : http://127.0.0.1:%d/" % 8770)
    dire("Interface MU-TH-UR       : http://127.0.0.1:%d/mother" % 8770)
    dire("Depuis un telephone      : voir INSTALLATION.md, section reseau")
    dire()
    dire("Pour arreter proprement : le raccourci « Arreter Jarvis ».")
    dire("Ne lance jamais deux Jarvis a la fois, il refusera de demarrer.")
    dire()
    input("  Entree pour fermer... ")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Installation interrompue. Rien n'est casse, relance quand tu veux.")
    except SystemExit:
        input("\n  Entree pour fermer... ")
