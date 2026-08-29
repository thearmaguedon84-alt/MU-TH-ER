"""Recenser les applications de la machine, pour pouvoir les lancer a la voix.

Trois gisements, du plus fiable au plus approximatif : les raccourcis du menu
Demarrer et du Bureau, les applications du Microsoft Store, et les jeux Steam.
Chacun donne un nom lisible et un moyen de lancement.

On enregistre plusieurs formes du meme nom — « photoshop », « adobe
photoshop », « adobe photoshop 2021 » — parce qu'on ne prononce jamais le nom
complet d'un logiciel. Les mots trop courts sont ecartes : « ai » ou « go »
attraperaient n'importe quoi.
"""
import os
import re
from pathlib import Path

from core.registre import outil

# Ce qui n'est jamais ce qu'on veut lancer : desinstalleurs, aides, licences.
REBUTS = re.compile(
    r"\b(uninstall|desinstall|readme|licence|license|aide|help|documentation|"
    r"manuel|manual|changelog|support|reparer|repair|configuration|config|"
    r"parametres|settings|website|site web|web site)\b", re.I)


def _lisible(nom):
    """Nettoie un nom de raccourci pour en faire un mot prononcable."""
    n = re.sub(r"\.(lnk|url|exe)$", "", nom, flags=re.I)
    n = re.sub(r"\s*[\(\[].*?[\)\]]", "", n)          # (x64), [2021]
    n = re.sub(r"\s{2,}", " ", n).strip()
    return n


def _variantes(nom):
    """Formes sous lesquelles on peut demander une application."""
    n = _lisible(nom).lower()
    formes = {n}
    mots = n.split()
    # Prefixes successifs : « adobe », « adobe photoshop », etc.
    for i in range(1, len(mots)):
        formes.add(" ".join(mots[:i]))
    # Le dernier mot seul quand il est distinctif : « photoshop ».
    if mots and len(mots[-1]) >= 5:
        formes.add(mots[-1])
    return {f for f in formes if len(f) >= 3 and not REBUTS.search(f)}


def _raccourcis():
    """Raccourcis du menu Demarrer et du Bureau."""
    trouves = {}
    dossiers = []
    for var in ("APPDATA", "ProgramData", "USERPROFILE"):
        base = os.environ.get(var)
        if not base:
            continue
        dossiers += [
            Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            Path(base) / "Desktop",
        ]
    for d in dossiers:
        if not d.is_dir():
            continue
        for chemin in d.rglob("*.lnk"):
            nom = _lisible(chemin.stem)
            if not nom or REBUTS.search(nom):
                continue
            try:
                cible = _cible_du_raccourci(chemin)
            except Exception:
                cible = None
            if cible:
                for f in _variantes(nom):
                    trouves.setdefault(f, cible)
    return trouves


def _cible_du_raccourci(chemin):
    """Programme vise par un raccourci Windows."""
    import subprocess
    ps = ("$s = New-Object -ComObject WScript.Shell; "
          f"$s.CreateShortcut('{chemin}').TargetPath")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=15)
    cible = (r.stdout or "").strip()
    return cible if cible and Path(cible).exists() else None


def _magasin():
    """Applications du Microsoft Store, lancables par leur identifiant."""
    import subprocess
    trouves = {}
    ps = ("Get-StartApps | Where-Object { $_.AppID -like '*!*' } | "
          "ForEach-Object { $_.Name + '|' + $_.AppID }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=60)
    except Exception:
        return trouves
    for ligne in (r.stdout or "").splitlines():
        if "|" not in ligne:
            continue
        nom, ident = ligne.split("|", 1)
        nom = _lisible(nom.strip())
        if not nom or REBUTS.search(nom):
            continue
        for f in _variantes(nom):
            trouves.setdefault(f, "shell:AppsFolder\\" + ident.strip())
    return trouves


def _steam():
    """Jeux Steam, lances par leur identifiant plutot que par un fichier."""
    trouves = {}
    bases = []

    # Le registre dit ou Steam est reellement installe : le deduire des
    # dossiers Programmes echoue des qu il a ete deplace.
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\\Valve\\Steam") as k:
            bases.append(Path(winreg.QueryValueEx(k, "SteamPath")[0]) / "steamapps")
    except Exception:
        pass

    for var in ("ProgramFiles(x86)", "ProgramFiles"):
        b = os.environ.get(var)
        if b:
            bases.append(Path(b) / "Steam" / "steamapps")

    # Emplacements courants sur un second disque, quand rien ne les declare.
    for lettre in "CDEFGHZ":
        for suite in ("SteamLibrary", "Steam", "Games/Steam"):
            bases.append(Path(f"{lettre}:/{suite}/steamapps"))

    # Steam declare ses autres bibliotheques dans un fichier : sans le lire,
    # on ignore tous les jeux installes sur un second disque, ce qui est le
    # cas le plus courant des qu on a plus de quelques titres.
    for principale in list(bases):
        declaration = principale / "libraryfolders.vdf"
        if not declaration.exists():
            continue
        try:
            texte = declaration.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for chemin in re.findall(r'"path"\s*"([^"]+)"', texte):
            autre = Path(chemin.replace("\\\\", "\\")) / "steamapps"
            if autre.is_dir() and autre not in bases:
                bases.append(autre)
    for b in bases:
        if not b.is_dir():
            continue
        for manifeste in b.glob("appmanifest_*.acf"):
            try:
                texte = manifeste.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            ident = re.search(r'"appid"\s*"(\d+)"', texte)
            nom = re.search(r'"name"\s*"([^"]+)"', texte)
            if ident and nom:
                for f in _variantes(nom.group(1)):
                    trouves.setdefault(f, f"steam://rungameid/{ident.group(1)}")
    return trouves


def recenser(silencieux=True):
    """Parcourt la machine et enregistre ce qui peut etre lance."""
    from core.config import definir, reglage

    trouves = {}
    for source in (_steam, _magasin, _raccourcis):
        try:
            trouves.update(source())
        except Exception:
            continue

    if not trouves:
        return "Aucune application trouvee."

    connues = dict(reglage("apps", {}) or {})
    avant = len(connues)
    # Ce qui est deja connu n'est pas ecrase : un chemin corrige a la main
    # doit survivre a un nouveau recensement.
    for nom, cible in trouves.items():
        connues.setdefault(nom, cible)
    definir("apps", connues)

    nouvelles = len(connues) - avant
    return (f"{len(connues)} applications reconnues, dont {nouvelles} "
            f"ajoutees a l instant.")


@outil(
    nom="recenser_applications",
    description=("Parcourt l'ordinateur et enregistre les applications "
                 "installees, pour pouvoir les lancer a la voix. Pour "
                 "'recense mes applications', 'cherche mes programmes'."),
    parametres={"type": "object", "properties": {}, "required": []},
    lent=True,
    phrase_attente="Je regarde ce qui est installe.",
)
def recenser_applications() -> str:
    return recenser()
