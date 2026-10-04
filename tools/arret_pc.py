"""Eteindre l'ordinateur, avec de quoi se raviser.

Une extinction ne se rattrape pas : un travail non enregistre est perdu, une
sauvegarde en cours est coupee. Deux garde-fous plutot qu'un.

D'abord la confirmation : a la voix, Jarvis annonce ce qu'il s'apprete a faire
et attend un accord franc ; depuis le telephone, l'accord vient d'un double
appui volontaire. Ensuite un delai avant coupure, pendant lequel « annule »
suffit — Windows sait revenir sur une extinction programmee tant qu'elle n'a
pas commence.

Sur la mise en oeuvre : `shutdown.exe` echoue avec l'erreur 203 quand le
processus appelant n'a pas le privilege d'extinction, ce qui arrive des qu on
sort d une session interactive ordinaire. On passe donc par l'API Windows en
reclamant explicitement `SeShutdownPrivilege`, et on ne retombe sur la commande
que si cette voie echoue.
"""
import ctypes
import subprocess
from ctypes import wintypes

from core.config import reglage
from core.registre import outil

DELAI_DEFAUT = 45

# Raisons Windows : « planifie », « autre », « application ». Sans cela le
# journal d evenements marque l arret comme inattendu.
RAISON = 0x00040000 | 0x00000000


def _delai():
    try:
        valeur = int(reglage("systeme.delai_extinction", DELAI_DEFAUT))
    except Exception:
        valeur = DELAI_DEFAUT
    # En dessous de dix secondes, se raviser devient illusoire.
    return max(10, min(600, valeur))


def _obtenir_privilege():
    """Reclame le droit d'eteindre pour le processus courant."""
    class LUID(ctypes.Structure):
        _fields_ = [("basse", wintypes.DWORD), ("haute", wintypes.LONG)]

    class LUID_ET_ATTRIBUTS(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributs", wintypes.DWORD)]

    class PRIVILEGES(ctypes.Structure):
        _fields_ = [("nombre", wintypes.DWORD), ("privilege", LUID_ET_ATTRIBUTS)]

    avapi = ctypes.WinDLL("advapi32", use_last_error=True)
    noyau = ctypes.WinDLL("kernel32", use_last_error=True)

    # Sans type de retour declare, ctypes ramene le pseudo-handle du processus
    # a un entier 32 bits et Windows repond « handle invalide ».
    noyau.GetCurrentProcess.restype = wintypes.HANDLE
    avapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.HANDLE)]

    jeton = wintypes.HANDLE()
    if not avapi.OpenProcessToken(noyau.GetCurrentProcess(),
                                  0x0020 | 0x0008, ctypes.byref(jeton)):
        return False
    try:
        luid = LUID()
        if not avapi.LookupPrivilegeValueW(None, "SeShutdownPrivilege",
                                           ctypes.byref(luid)):
            return False
        p = PRIVILEGES()
        p.nombre = 1
        p.privilege.Luid = luid
        p.privilege.Attributs = 0x00000002  # active
        ctypes.set_last_error(0)
        avapi.AdjustTokenPrivileges(jeton, False, ctypes.byref(p), 0, None, None)
        # 1300 signifie que le privilege n est pas detenu du tout.
        return ctypes.get_last_error() == 0
    finally:
        noyau.CloseHandle(jeton)


def _programmer(secondes, redemarrage, message):
    """Programme l'arret par l'API. Renvoie (reussi, explication)."""
    if not _obtenir_privilege():
        return False, "privilege refuse"
    avapi = ctypes.WinDLL("advapi32", use_last_error=True)
    avapi.InitiateSystemShutdownExW.argtypes = [
        wintypes.LPWSTR, wintypes.LPWSTR, wintypes.DWORD,
        wintypes.BOOL, wintypes.BOOL, wintypes.DWORD]
    ok = avapi.InitiateSystemShutdownExW(
        None, message, int(secondes), False, bool(redemarrage), RAISON)
    if ok:
        return True, ""
    return False, f"erreur {ctypes.get_last_error()}"


def _annuler():
    avapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _obtenir_privilege()
    return bool(avapi.AbortSystemShutdownW(None))


def _repli(args):
    """Ancienne commande, au cas ou l'API serait indisponible."""
    try:
        r = subprocess.run(["shutdown"] + args, capture_output=True, text=True,
                           timeout=20)
        return r.returncode == 0
    except Exception:
        return False


def _arreter(secondes, redemarrage=False):
    verbe = "redemarrage" if redemarrage else "extinction"
    message = f"Jarvis : {verbe} dans {secondes} secondes."

    _annuler()  # un arret deja programme ferait echouer le suivant
    ok, motif = _programmer(secondes, redemarrage, message)
    if ok:
        return True, ""
    if _repli(["/r" if redemarrage else "/s", "/t", str(secondes)]):
        return True, ""
    return False, motif


@outil(
    nom="eteindre_pc",
    description=(
        "Eteint l'ordinateur apres un court delai pendant lequel l'extinction "
        "peut encore etre annulee. Pour 'eteins le PC', 'arrete l'ordinateur', "
        "'coupe la machine'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "delai": {"type": "integer",
                      "description": "Secondes avant extinction. Vide = reglage par defaut."},
        },
        "required": [],
    },
    confirmation=True,
    annonce="Tu veux vraiment que j eteigne l ordinateur ?",
)
def eteindre_pc(delai: int = 0) -> str:
    secondes = max(10, min(600, int(delai))) if delai else _delai()
    ok, motif = _arreter(secondes, redemarrage=False)
    if not ok:
        return f"Je n arrive pas a eteindre : {motif}."
    return (f"J eteins dans {secondes} secondes. Dis-moi d annuler si tu "
            f"changes d avis.")


@outil(
    nom="annuler_extinction",
    description=(
        "Annule une extinction ou un redemarrage programme. Pour 'annule', "
        "'annule l extinction', 'n eteins pas', 'laisse allume'."
    ),
    parametres={"type": "object", "properties": {}, "required": []},
)
def annuler_extinction() -> str:
    if _annuler() or _repli(["/a"]):
        return "C est annule, l ordinateur reste allume."
    return "Il n y avait rien de programme."


@outil(
    nom="redemarrer_pc",
    description="Redemarre l'ordinateur apres un court delai annulable.",
    parametres={
        "type": "object",
        "properties": {
            "delai": {"type": "integer", "description": "Secondes avant redemarrage."},
        },
        "required": [],
    },
    confirmation=True,
    annonce="Tu veux vraiment que je redemarre l ordinateur ?",
)
def redemarrer_pc(delai: int = 0) -> str:
    secondes = max(10, min(600, int(delai))) if delai else _delai()
    ok, motif = _arreter(secondes, redemarrage=True)
    if not ok:
        return f"Je n arrive pas a redemarrer : {motif}."
    return f"Je redemarre dans {secondes} secondes. Dis-moi d annuler si besoin."


@outil(
    nom="verrouiller_pc",
    description=("Verrouille la session sans rien fermer. Pour 'verrouille le "
                 "PC', 'ferme la session'."),
    parametres={"type": "object", "properties": {}, "required": []},
)
def verrouiller_pc() -> str:
    try:
        if ctypes.WinDLL("user32").LockWorkStation():
            return "Session verrouillee."
    except Exception:
        pass
    return "Je n arrive pas a verrouiller la session."
