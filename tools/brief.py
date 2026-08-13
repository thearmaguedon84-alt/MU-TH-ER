"""Brief : heure + meteo + apercu des derniers mails, en un seul outil."""
from core.registre import outil
from tools.mail import _mail_configure, lire_mails
from tools.meteo import meteo
from tools.temps import heure_et_date


@outil(
    nom="faire_brief",
    description="Fait un brief : l'heure, la meteo et un apercu des derniers mails. "
                "A utiliser quand l'utilisateur dit 'fais-moi un brief', 'quoi de "
                "neuf', 'ma journee'. Apres le brief, propose de lire, repondre ou "
                "jeter un mail.",
    lent=True,
    phrase_attente="D'accord, je te prepare ton brief, un instant.",
)
def faire_brief() -> str:
    """Brief du moment : heure, meteo, deadlines Loopstr et apercu des nouveaux mails."""
    morceaux = [heure_et_date(), meteo()]
    try:
        from tools.loopstr import deadlines_brief
        deadlines = deadlines_brief()
        if deadlines:
            morceaux.append(deadlines)
    except Exception:
        pass
    if _mail_configure():
        morceaux.append(lire_mails(5))
    return " ".join(morceaux)
