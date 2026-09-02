"""Attendre la fin du rendu, puis envoyer la sequence par courriel.

Il a demande l envoi avant que la video n existe. Plutot que de le faire
revenir, on guette : le journal de l essai passe de « en cours » a une phrase
de fin, et c est le signal. On borne l attente a deux heures — au-dela, quelque
chose a mal tourne et un courriel de plus n aiderait pas.
"""
import io, sys, time
sys.path.insert(0, r"C:/Users/thear/Documents/jarvis-assistant-vocal")

J = "envoi.log"
def dire(t):
    with open(J, "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + str(t) + "\n")

open(J, "w", encoding="utf-8").close()
debut = time.time()
etat = ""
while time.time() - debut < 7200:
    time.sleep(20)
    try:
        etat = io.open("suite.log", encoding="utf-8").read()
    except Exception:
        continue
    if etat and "en cours" not in etat:
        break
dire("rendu termine : " + etat[:160])

if "secondes" not in etat and "Voila" not in etat:
    dire("le rendu n a pas abouti, rien a envoyer")
    raise SystemExit

# La video vient d etre rangee : on laisse le fichier se fermer.
time.sleep(5)
from tools.video import envoyer_video_mail
r = envoyer_video_mail(destinataire="")
dire("envoi : " + str(r))
