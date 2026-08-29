"""Scanne le Bureau, la barre des taches, le menu Demarrer et les apps du Store,
puis enregistre tout dans config.yaml sous la clef 'apps'.

Usage :  .venv\\Scripts\\python.exe scan_apps.py
"""
import os
import re
import subprocess
import sys

import yaml

# ------------------------------------------------------------------ constantes

# Extensions de documents / archives : jamais des "apps"
EXT_DOCS = (".pdf", ".txt", ".zip", ".rar", ".7z", ".doc", ".docx", ".xls",
            ".xlsx", ".jpg", ".jpeg", ".png", ".gif", ".mp3", ".mp4", ".mkv",
            ".avi", ".iso", ".log", ".ini", ".cfg")

# Cibles a rejeter (extension de la cible resolue)
CIBLE_EXT_REJET = (".msc", ".cpl", ".dll", ".url", ".chm", ".hlp", ".txt",
                   ".pdf", ".html", ".htm")

# Si la cible contient un de ces fragments, on rejette
CIBLE_REJET = (
    "wildtangent", "system32/control.exe", "system32\\control.exe",
    "appvlp.exe", "installer/{", "installer\\{",
    "/windows/installer", "\\windows\\installer",
    "unins", "setup.exe", "vcredist",
)

# Si le NOM contient un de ces fragments, on rejette
NOM_REJET = (
    "uninstall", "desinstall", "readme", "lisez", "guide", "aide ",
    "help", "support center", "reset preferences", "documentation",
    "elamigos", "aller aux jeux", "jeux wildtangent", "centre de telechargement",
    "configuration", "parametres de", "settings", "tweak tool", "changelog",
    "site officiel", "official site", "pastebin", "releases and updates",
    "administrative tools", "component services", "computer management",
    "event viewer", "services", "odbc", "performance monitor",
    "resource monitor", "system configuration", "system information",
    "task scheduler", "disk cleanup", "defragment", "iscsi", "recovery drive",
    "registry editor", "windows memory", "print management", "local security",
    "windows tools", "outils windows", "observateur", "planificateur",
    "moniteur de", "nettoyage de disque", "defragmenter", "informations systeme",
    "configuration du systeme", "editeur du registre", "strategie de securite",
    "gestion de l'impression", "gestion de l'ordinateur", "services composants",
    "sources de donnees", "diagnostic de la memoire", "lecteur de recuperation",
    "outils d'administration", "initiateur iscsi",
)

# Fragments a retirer des noms d'affichage
BRUIT = (" - raccourci", "- raccourci", " raccourci",
         ".lnk", ".url", ".exe")

# Apps du Store a exposer
STORE_VOULUES = ("spotify", "netflix", "prime video", "xbox", "disney")


def _sans_accents(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


# ------------------------------------------------------------------ resolution

def resoudre_lnk(chemin):
    """Renvoie la cible d'un raccourci .lnk, ou None."""
    try:
        import win32com.client
        sh = win32com.client.Dispatch("WScript.Shell")
        rac = sh.CreateShortCut(chemin)
        cible = (rac.Targetpath or "").strip()
        return cible or None
    except Exception:
        return None


def resoudre_url(chemin):
    """Renvoie l'URL contenue dans un fichier .url (steam:// et compagnie)."""
    try:
        with open(chemin, encoding="utf-8", errors="ignore") as f:
            for ligne in f:
                if ligne.lower().startswith("url="):
                    u = ligne.split("=", 1)[1].strip()
                    # On ne garde que les protocoles d'app, pas les liens web
                    if u.lower().startswith(("steam://", "com.epicgames",
                                             "uplay://", "origin://",
                                             "battlenet://", "spotify:")):
                        return u
                    return None
    except Exception:
        pass
    return None


def apps_du_store():
    """Renvoie {nom: shell:AppsFolder\\AppID} pour les apps du Store voulues."""
    resultat = {}
    ps = "Get-StartApps | ForEach-Object { $_.Name + '|||' + $_.AppID }"
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=40,
        )
    except Exception:
        return resultat
    for ligne in (r.stdout or "").splitlines():
        if "|||" not in ligne:
            continue
        nom, appid = ligne.split("|||", 1)
        nom, appid = nom.strip(), appid.strip()
        if not nom or not appid or "!" not in appid:
            continue
        if any(v in nom.lower() for v in STORE_VOULUES):
            resultat[nom] = "shell:AppsFolder\\" + appid
    return resultat


# ------------------------------------------------------------------ nettoyage

def nom_propre(fichier):
    """'ELDEN RING.lnk' -> 'elden ring' ; 'X - Raccourci.lnk' -> 'x'."""
    n = fichier
    bas = n.lower()
    for b in BRUIT:
        if bas.endswith(b):
            n = n[: len(n) - len(b)]
            bas = n.lower()
    n = n.replace("\u2019", "'").replace("\u00b4", "'")
    n = re.sub(r"\s+", " ", n).strip(" -_=")
    return n.lower()


def rejeter(nom, cible):
    """True si cette entree ne doit pas etre enregistree."""
    nom_a = _sans_accents(nom)
    cible_b = cible.lower().replace("\\", "/")
    if len(nom) < 2 or nom.startswith("="):
        return True
    if any(k in nom_a for k in NOM_REJET):
        return True
    if cible_b.endswith(CIBLE_EXT_REJET):
        return True
    if any(k in cible_b for k in CIBLE_REJET):
        return True
    # Outils systeme dans system32, sauf quelques exceptions utiles
    if "/windows/system32/" in cible_b:
        utile = ("cmd.exe", "notepad.exe", "calc.exe", "mspaint.exe",
                 "explorer.exe", "snippingtool.exe")
        if not any(u in cible_b for u in utile):
            return True
    return False


def alias_pour(nom):
    """Genere des alias utiles pour la reconnaissance vocale."""
    out = set()
    base = nom.lower()
    out.add(base)
    sans_apo = base.replace("'", "").replace("\u2019", "")
    out.add(sans_apo)
    out.add(re.sub(r"[-_]+", " ", sans_apo))
    mots = [m for m in re.split(r"[^a-z0-9]+", _sans_accents(sans_apo)) if m]
    if len(mots) >= 3:
        out.add(" ".join(mots[:2]))
    if len(mots) >= 2 and len(mots[0]) >= 4:
        out.add(mots[0])
    return {a.strip() for a in out if len(a.strip()) >= 3}


# ------------------------------------------------------------------ scan

def scanner():
    """Parcourt tous les emplacements et renvoie {nom: cible}."""
    profil = os.environ.get("USERPROFILE", "")
    appdata = os.environ.get("APPDATA", "")
    dossiers = [
        os.path.join(profil, "Desktop"),
        os.path.join(profil, "OneDrive", "Desktop"),
        r"C:\Users\Public\Desktop",
        os.path.join(appdata,
                     r"Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"),
        os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs"),
        r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs",
    ]

    trouve = {}

    for dossier in dossiers:
        if not os.path.isdir(dossier):
            continue
        profondeur_max = 1 if "Start Menu" in dossier else 0
        for racine, sous, fichiers in os.walk(dossier):
            rel = os.path.relpath(racine, dossier)
            niveau = 0 if rel == "." else rel.count(os.sep) + 1
            if niveau > profondeur_max:
                sous[:] = []
                continue
            for f in fichiers:
                bas = f.lower()
                if bas == "desktop.ini" or bas.endswith(EXT_DOCS):
                    continue
                chemin = os.path.join(racine, f)
                if bas.endswith(".lnk"):
                    cible = resoudre_lnk(chemin)
                elif bas.endswith(".url"):
                    cible = resoudre_url(chemin)
                elif bas.endswith(".exe"):
                    cible = chemin
                else:
                    continue
                if not cible:
                    continue
                nom = nom_propre(f)
                if rejeter(nom, cible):
                    continue
                trouve.setdefault(nom, cible.replace("\\", "/"))

    for nom, cible in apps_du_store().items():
        trouve.setdefault(nom_propre(nom), cible)

    return trouve


def avec_alias(apps):
    """Ajoute les alias generes, sans ecraser une entree existante."""
    complet = dict(apps)
    for nom, cible in apps.items():
        for a in alias_pour(nom):
            complet.setdefault(a, cible)
    return complet


# ------------------------------------------------------------------ ecriture

def main():
    racine = os.path.dirname(os.path.abspath(__file__))
    chemin_cfg = os.path.join(racine, "config.yaml")

    with open(chemin_cfg, encoding="utf-8") as f:
        cfg = yaml.safe_load(f.read()) or {}

    # On repart d'une base propre : seules les entrees ecrites a la main restent
    manuelles = {
        "baldurs gate": "D:/Games/Baldurs Gate 3/bin/bg3.exe",
        "clair obscur": "D:/Games/Clair Obscur Expedition 33/Expedition33_Steam.exe",
        "clair obscur expedition 33":
            "D:/Games/Clair Obscur Expedition 33/Expedition33_Steam.exe",
        "expedition 33":
            "D:/Games/Clair Obscur Expedition 33/Expedition33_Steam.exe",
    }

    trouvees = scanner()
    completes = avec_alias(trouvees)

    fusion = dict(completes)
    fusion.update(manuelles)

    cfg["apps"] = dict(sorted(fusion.items()))

    with open(chemin_cfg, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False,
                  sort_keys=False)

    print(f"{len(trouvees)} applications detectees.")
    print(f"{len(fusion)} entrees au total (alias compris).")
    print()
    for nom in sorted(trouvees):
        print(f"  {nom:40s} -> {trouvees[nom][:58]}")


if __name__ == "__main__":
    main()
