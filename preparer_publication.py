"""Preparer un depot publiable, a l'historique neuf.

Retirer un fichier du suivi ne l'efface pas du passe : il reste dans chaque
enregistrement ou il figurait, et quiconque clone le depot le recupere. Or
l'historique de ce projet contient un index de bibliotheque Plex, un journal de
conversations et des enregistrements de voix.

Reecrire l'historique est possible mais delicat, et laisse souvent des restes.
On fait plus simple et plus sur : un depot neuf, un seul enregistrement, ne
contenant que les fichiers verifies. Le depot de travail reste intact de son
cote, avec toute son histoire.

Usage : python preparer_publication.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent
CIBLE = RACINE.parent / (RACINE.name + "-publication")


def sur(chemin):
    """Le meme controle que pour l'archive : on ne se fie pas au hasard."""
    sys.path.insert(0, str(RACINE))
    import preparer_paquet as controle
    motif = controle.suspect(chemin)
    if motif:
        return motif
    return controle.contenu_suspect(chemin)


def main():
    print()
    print("  Preparation du depot publiable")
    print("  " + "-" * 31)

    r = subprocess.run(["git", "ls-files"], cwd=RACINE, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    fichiers = [f for f in r.stdout.split("\n") if f.strip()]

    refuses = [(f, sur(f)) for f in fichiers if sur(f)]
    if refuses:
        print("\n  ARRET : contenu personnel detecte.")
        for f, motif in refuses:
            print(f"     {f}  —  {motif}")
        sys.exit(1)
    print(f"  {len(fichiers)} fichiers verifies, aucun contenu personnel.")

    if CIBLE.exists():
        # Git marque ses objets en lecture seule : une suppression ordinaire
        # les laisse en place et le dossier survit a moitie.
        def forcer(fonction, chemin, _):
            import stat
            try:
                os.chmod(chemin, stat.S_IWRITE)
                fonction(chemin)
            except Exception:
                pass
        shutil.rmtree(CIBLE, onexc=forcer)
    CIBLE.mkdir(parents=True, exist_ok=True)

    for f in fichiers:
        dest = CIBLE / f
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(RACINE / f, dest)
    for extra in ("installer.py", "installer_fenetre.py", "INSTALLER.bat",
                  "INSTALLER_FENETRE.bat", "INSTALLATION.md"):
        p = RACINE / extra
        if p.exists() and extra not in fichiers:
            shutil.copyfile(p, CIBLE / extra)

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=CIBLE)
    subprocess.run(["git", "add", "-A"], cwd=CIBLE)
    subprocess.run(
        ["git", "-c", "user.name=Jarvis", "-c", "user.email=jarvis@local",
         "commit", "-q", "-m",
         "Jarvis : assistant vocal local, avec l'interface MU-TH-UR"],
        cwd=CIBLE)

    q = subprocess.run(["git", "log", "--oneline"], cwd=CIBLE,
                       capture_output=True, text=True, encoding="utf-8")
    taille = sum(f.stat().st_size for f in CIBLE.rglob("*") if f.is_file())

    print(f"  Depot cree : {CIBLE}")
    print(f"  Un seul enregistrement : {q.stdout.strip()}")
    print(f"  {len(fichiers)} fichiers, {taille / 2**20:.1f} Mo")
    print()
    print("  L'historique est neuf : rien de ton passe n'y figure.")
    print()
    print("  Il te reste a creer le depot sur github.com, puis :")
    print(f"     cd {CIBLE}")
    print("     git remote add origin https://github.com/<toi>/<depot>.git")
    print("     git push -u origin main")


if __name__ == "__main__":
    main()
