"""Verifier que le JavaScript des interfaces tient debout.

Une accolade en trop suffit a rendre une page muette : le script ne s'execute
pas, les boutons ne se construisent jamais, et rien n'indique la cause. C'est
arrive apres une suppression de bloc un peu large — la page s'affichait
normalement, mais plus aucune commande ne partait.

Ce controle prend deux secondes et evite d'avoir a le decouvrir a l'usage.
A lancer apres toute modification d'une page.
"""
import re
import subprocess
import sys
from pathlib import Path

PAGES = ("hud.html", "hud_mother.html", "hud_tel.html", "hud_tel_mother.html")


def blocs(page):
    """Scripts d'une page, hors ceux charges depuis l'exterieur."""
    texte = Path(page).read_text(encoding="utf-8")
    return [b for b in re.findall(r"<script[^>]*>(.*?)</script>", texte, re.S)
            if b.strip()]


def equilibre(page):
    """Balises ouvrantes et fermantes en nombre egal."""
    t = Path(page).read_text(encoding="utf-8")
    ecarts = []
    for balise in ("div", "form", "script", "style", "header", "footer"):
        o = len(re.findall(r"<%s[\s>]" % balise, t))
        f = t.count("</%s>" % balise)
        if o != f:
            ecarts.append(f"{balise} {o}/{f}")
    return ecarts


def main():
    souci = False
    for page in PAGES:
        if not Path(page).exists():
            continue
        for i, js in enumerate(blocs(page)):
            r = subprocess.run(["node", "--check", "-"], input=js,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode:
                souci = True
                premiere = (r.stderr or "").strip().split("\n")
                print(f"  {page} bloc {i} : ERREUR")
                for ligne in premiere[:4]:
                    print("     ", ligne[:110])
            else:
                print(f"  {page} bloc {i} : ok")

        ecarts = equilibre(page)
        if ecarts:
            souci = True
            print(f"  {page} : balises desequilibrees — {', '.join(ecarts)}")

    print()
    print("Toutes les pages tiennent debout." if not souci
          else "Au moins une page est cassee : elle sera muette a l'affichage.")
    return 1 if souci else 0


if __name__ == "__main__":
    sys.exit(main())
