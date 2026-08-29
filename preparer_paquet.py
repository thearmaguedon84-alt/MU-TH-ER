"""Fabriquer l'archive a distribuer, sans rien qui soit personnel.

Le danger de l'operation n'est pas technique, il est humain : un dossier zippe
a la main emporte le fichier de reglages, les certificats, le profil de
navigateur ou l'on est reste connecte a Netflix, les index de bibliotheque, les
journaux de conversation. Une seule fois suffit.

On ne compresse donc pas le dossier. On demande a git la liste de ce qui est
suivi — le fichier .gitignore ayant deja ecarte tout ce qui est personnel — et
on verifie ensuite, nom par nom, qu'aucun fichier sensible ne s'est glisse
dedans. Si le controle echoue, l'archive n'est pas produite.

Usage : python preparer_paquet.py
"""
import re
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

RACINE = Path(__file__).resolve().parent

# Ce qui ne doit jamais sortir d'ici, quelle qu'en soit la raison.
INTERDITS = (
    "config.yaml", "hud_cert.pem", "ts_cert.pem", "ts_key.pem",
    ".cache_films.json", ".cache_plex.json", ".cache_canal.json",
    "google_token.json", "google_credentials.json",
    ".chrome_jarvis", "recettes/", "logs/", ".venv",
)
MOTIFS_INTERDITS = (
    re.compile(r"\.log$"),
    re.compile(r"\.session$"),
    re.compile(r"\.pem$"),
    re.compile(r"token", re.I),
    re.compile(r"credential", re.I),
)

# Ajoutes a l'archive bien qu'absents du depot.
EN_PLUS = ("installer.py", "installer_fenetre.py", "INSTALLER.bat",
           "INSTALLER_FENETRE.bat", "INSTALLATION.md")


def suivis():
    r = subprocess.run(["git", "ls-files"], cwd=RACINE, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        sys.exit("git indisponible : impossible d'etablir la liste sure.")
    return [f for f in r.stdout.split("\n") if f.strip()]


def suspect(chemin):
    bas = chemin.lower()
    for i in INTERDITS:
        if i.lower() in bas:
            return f"nom interdit ({i})"
    for m in MOTIFS_INTERDITS:
        if m.search(chemin):
            return f"motif interdit ({m.pattern})"
    return None


def contenu_suspect(chemin):
    """Un secret oublie dans un fichier de code ou de documentation."""
    p = RACINE / chemin
    if p.suffix.lower() not in (".py", ".md", ".yaml", ".yml", ".json",
                                ".html", ".txt", ".bat"):
        return None
    try:
        texte = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    # Ce qui ressemble a un exemple n'en est pas un : les fichiers de
    # documentation en sont pleins, et un controle qui crie au loup finit
    # par etre ignore.
    exemples = re.compile(
        r"ton[.\-_]?adresse|votre|exemple|example|sample|xxx+|"
        r"a\.remplir|remplir|placeholder|<[^>]+>", re.I)

    empreintes = (
        (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "cle Anthropic", False),
        (re.compile(r"\bAIza[A-Za-z0-9_-]{30,}"), "cle Google", False),
        (re.compile(r"\b[A-Za-z0-9._%+-]+@gmail\.com\b"), "adresse Gmail", True),
        # Un mot de passe d'application Google : quatre groupes de quatre
        # lettres, mais uniquement la ou un reglage le reclame. Sans ce
        # contexte, n'importe quelle phrase francaise declenchait l'alerte.
        (re.compile(r"(?:mot_de_passe|password|mdp)[^\n]{0,20}"
                    r"[\"\']([a-z]{4}\s?){4}[\"\']", re.I),
         "mot de passe d'application", False),
        (re.compile(r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
         "adresse du reseau prive", False),
        (re.compile(r"\b[a-z0-9-]{6,}\.ts\.net\b"), "nom du reseau prive", False),
        # Un chemin utilisateur trahit le nom du compte Windows. Les exemples
        # de documentation sont tolerés, les vrais chemins non.
        (re.compile(r"C:[\\/]Users[\\/](?!Public|ton-|votre|<)[A-Za-z]{3,}", re.I),
         "chemin personnel", True),
    )
    for motif, quoi, tolere_exemple in empreintes:
        for m in motif.finditer(texte):
            debut = texte.rfind(chr(10), 0, m.start()) + 1
            fin = texte.find(chr(10), m.end())
            ligne = texte[debut:fin if fin > 0 else None]
            if tolere_exemple and exemples.search(ligne):
                continue
            return f"{quoi} ligne {texte[:m.start()].count(chr(10)) + 1}"
    return None


def main():
    print()
    print("  Preparation de l'archive a distribuer")
    print("  " + "-" * 38)

    fichiers = suivis()
    print(f"  {len(fichiers)} fichiers suivis par git.")

    refuses = []
    for f in fichiers:
        motif = suspect(f)
        if motif:
            refuses.append((f, motif))
    if refuses:
        print()
        print("  ARRET : des fichiers personnels sont suivis par git.")
        for f, motif in refuses:
            print(f"     {f}  —  {motif}")
        print()
        print("  Retire-les avec : git rm --cached <fichier>")
        sys.exit(1)

    print("  Aucun fichier personnel dans la liste.")

    fuites = []
    for f in fichiers:
        quoi = contenu_suspect(f)
        if quoi:
            fuites.append((f, quoi))
    if fuites:
        print()
        print("  ARRET : un secret apparait dans le contenu d'un fichier.")
        for f, quoi in fuites:
            print(f"     {f}  —  {quoi}")
        sys.exit(1)

    print("  Aucun secret dans le contenu des fichiers.")

    for extra in EN_PLUS:
        if not (RACINE / extra).exists():
            print(f"  ATTENTION : {extra} est absent, il ne sera pas inclus.")

    nom = f"jarvis-installation-{date.today().isoformat()}.zip"
    cible = RACINE / nom
    with zipfile.ZipFile(cible, "w", zipfile.ZIP_DEFLATED) as z:
        for f in fichiers:
            z.write(RACINE / f, f"jarvis/{f}")
        for extra in EN_PLUS:
            p = RACINE / extra
            if p.exists() and extra not in fichiers:
                z.write(p, f"jarvis/{extra}")

    taille = cible.stat().st_size / 2 ** 20
    print()
    print(f"  Archive prete : {nom}  ({taille:.1f} Mo)")
    print()
    print("  Elle ne contient ni reglages, ni identifiants, ni certificats,")
    print("  ni index de bibliotheque, ni profil de navigateur.")
    print("  Tes collegues lancent installer.py et repondent aux questions.")


if __name__ == "__main__":
    main()
