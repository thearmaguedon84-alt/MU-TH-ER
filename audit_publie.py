"""Verifier ce qui est reellement en ligne, et non ce qu'on croit y avoir mis.

On ne se fie pas au dossier local : on clone depuis GitHub et on fouille le
contenu de chaque fichier, plus l'historique complet. Un secret peut avoir ete
ajoute puis retire dans un enregistrement anterieur — il reste alors accessible
a quiconque clone.

La recherche porte sur des empreintes precises : les cles d'API ont des
prefixes reconnaissables, les mots de passe d'application Google une forme
fixe, et les valeurs personnelles de ce projet sont connues.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEPOT = "https://github.com/thearmaguedon84-alt/MU-TH-ER.git"

EMPREINTES = [
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "cle API Anthropic"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{30,}"), "cle API Google"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}"), "jeton GitHub"),
    (re.compile(r"\bBQ[A-Za-z0-9_-]{80,}"), "jeton Spotify"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "jeton Slack"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "cle privee"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@(?:gmail|hotmail|outlook|yahoo|free|"
                r"orange|sfr|wanadoo)\.[a-z]{2,4}\b", re.I), "adresse mail"),
    (re.compile(r"(?:mot_de_passe|password|mdp|passwd)\s*[:=]\s*"
                r"[\"']([^\"'\s]{6,})[\"']", re.I), "mot de passe"),
    (re.compile(r"(?:jeton|token|cle|key|secret)\s*[:=]\s*"
                r"[\"']([A-Za-z0-9_\-]{20,})[\"']", re.I), "jeton ou cle"),
    (re.compile(r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "adresse de reseau prive"),
    (re.compile(r"\b[a-z0-9-]{5,}\.ts\.net\b"), "nom de machine du reseau prive"),
    (re.compile(r"\bthearmaguedon\b", re.I), "identifiant personnel"),
    (re.compile(r"C:[\\/]Users[\\/](?!<|utilisateur|user\b)[A-Za-z]{3,}",
                re.I), "chemin personnel"),
]

# Ce qui ressemble a un exemple n'en est pas un.
EXEMPLE = re.compile(r"ton[.\-_]?adresse|votre|exemple|example|sample|xxx+|"
                     r"placeholder|<[^>]+>|remplir|a-remplir", re.I)

LISIBLES = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".html", ".js",
            ".css", ".bat", ".cfg", ".ini", ".toml", ".lock", ""}


def fouiller(racine):
    trouvailles = []
    for chemin in racine.rglob("*"):
        if not chemin.is_file() or ".git" in chemin.parts:
            continue
        if chemin.suffix.lower() not in LISIBLES:
            continue
        try:
            texte = chemin.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for motif, quoi in EMPREINTES:
            for m in motif.finditer(texte):
                d = texte.rfind("\n", 0, m.start()) + 1
                f = texte.find("\n", m.end())
                ligne = texte[d:f if f > 0 else None].strip()
                if EXEMPLE.search(ligne):
                    continue
                trouvailles.append(
                    (str(chemin.relative_to(racine)),
                     texte[:m.start()].count("\n") + 1, quoi, ligne[:110]))
    return trouvailles


def main():
    dossier = Path(tempfile.mkdtemp(prefix="audit_"))
    depot = dossier / "depot"
    print()
    print("  Verification de ce qui est publie")
    print("  " + "-" * 34)
    print("  Clonage depuis GitHub...")
    r = subprocess.run(["git", "clone", "--quiet", DEPOT, str(depot)],
                       capture_output=True, text=True)
    if r.returncode:
        print("  Clonage impossible :", (r.stderr or "")[:120])
        return

    fichiers = [p for p in depot.rglob("*")
                if p.is_file() and ".git" not in p.parts]
    print(f"  {len(fichiers)} fichiers recuperes.")

    n = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=depot,
                       capture_output=True, text=True).stdout.strip()
    print(f"  {n} enregistrement(s) dans l'historique publie.")

    print()
    print("  Recherche de secrets dans le contenu...")
    trouvailles = fouiller(depot)

    if trouvailles:
        print()
        print(f"  {len(trouvailles)} TROUVAILLE(S) :")
        for f, ligne, quoi, extrait in trouvailles[:40]:
            print(f"     {f}:{ligne}  —  {quoi}")
            print(f"        {extrait}")
    else:
        print("  Aucun secret dans les fichiers publies.")

    print()
    print("  Historique : recherche dans tous les enregistrements...")
    q = subprocess.run(
        ["git", "log", "--all", "-p", "-S", "sk-ant-", "--oneline"],
        cwd=depot, capture_output=True, text=True, errors="replace")
    print("     cle Anthropic ayant existe :",
          "OUI" if q.stdout.strip() else "non")
    for terme, quoi in (("@gmail.com", "adresse Gmail"),
                        ("ts.net", "reseau prive"),
                        ("refresh_token", "jeton Spotify")):
        s = subprocess.run(["git", "log", "--all", "-S", terme, "--oneline"],
                           cwd=depot, capture_output=True, text=True,
                           errors="replace")
        lignes = [x for x in s.stdout.split("\n") if x.strip()]
        print(f"     {quoi} ayant existe :",
              f"OUI ({len(lignes)} enregistrement(s))" if lignes else "non")

    shutil.rmtree(dossier, ignore_errors=True)


if __name__ == "__main__":
    main()
