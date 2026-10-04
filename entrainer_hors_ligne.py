"""Reentraine une voix pendant que Jarvis est arrete, puis le relance.

Lance par l outil entrainer_voix_hors_ligne, en processus detache. Il faut
onze giga-octets a l entrainement ; Jarvis en garde deux pour son ecoute, et
ne se coupe pas lui-meme pendant une heure. Ce programme le fait a sa place :
il attend qu aucun travail ne tourne sur la carte, arrete Jarvis, entraine,
le relance quoi qu il arrive, et envoie le compte rendu par mail.

    python entrainer_hors_ligne.py <personnage> [epoques]
"""
import subprocess
import sys
import time
import traceback
from pathlib import Path

ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI))
JOURNAL = ICI / "logs" / "entrainement_hors_ligne.log"


def note(texte):
    JOURNAL.parent.mkdir(exist_ok=True)
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), texte))


def arreter_jarvis():
    # Meme filtre que redemarrer_jarvis.ps1 : les lignes de commande qui
    # finissent par le nom, jamais ce programme-ci.
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | Where-Object { "
                    "$_.CommandLine -match 'jarvis14\\.py\\s*$' -or "
                    "$_.CommandLine -match 'lancer_jarvis\\.bat.?\\s*$' } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
                    "-ErrorAction SilentlyContinue }"],
                   capture_output=True, timeout=60)


def relancer_jarvis():
    subprocess.Popen(["cmd.exe", "/c", "start", "", str(ICI / "lancer_jarvis.bat")],
                     cwd=str(ICI))


def envoyer(sujet, corps):
    try:
        import smtplib
        from email.message import EmailMessage
        from tools.mail import MAIL_ADRESSE, _mail_configure, _mail_mdp
        if not _mail_configure():
            return
        msg = EmailMessage()
        msg["From"] = msg["To"] = MAIL_ADRESSE
        msg["Subject"] = sujet
        msg.set_content(corps)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(MAIL_ADRESSE, _mail_mdp())
            s.send_message(msg)
    except Exception:
        note("mail impossible : " + traceback.format_exc()[-300:])


def main():
    qui = sys.argv[1]
    epoques = float(sys.argv[2]) if len(sys.argv) > 2 else 40
    note("demande : %s, %d epoques" % (qui, epoques))
    # Laisse a Jarvis le temps de dire qu il s en va.
    time.sleep(15)

    # On n interrompt jamais un travail en cours : on attend qu il finisse.
    from core.file_gpu import en_cours
    while en_cours():
        time.sleep(30)

    arreter_jarvis()
    note("Jarvis arrete")
    rendu = ""
    try:
        time.sleep(5)
        try:
            from core.vram import liberer
            liberer(pour="entrainement", besoin=11.5)
        except Exception:
            pass
        from tools.entrainer_voix import entrainer_voix
        rendu = entrainer_voix(qui=qui, epoques=epoques)
        note("fini : " + rendu.splitlines()[0][:200])
    except Exception:
        rendu = "L entrainement a plante :\n" + traceback.format_exc()[-1500:]
        note(rendu)
    finally:
        relancer_jarvis()
        note("Jarvis relance")
    envoyer("Voix de %s : entrainement termine" % qui, rendu)


if __name__ == "__main__":
    main()
