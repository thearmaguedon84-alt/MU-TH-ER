"""Ou rangent les creations.

Un seul endroit decide, pour que les outils, l interface web et la recherche
soient d accord. Les reglages peuvent tout redefinir ; les valeurs par defaut
suivent les dossiers Windows, dans un sous-dossier MU-TH-UR.
"""
import os
from pathlib import Path

from core.config import reglage

MAISON = Path(os.environ.get("USERPROFILE", str(Path.home())))
MARQUE = "MU-TH-UR"

_DEFAUTS = {
    "images": MAISON / "Pictures" / MARQUE,
    "musiques": MAISON / "Music" / MARQUE,
    "videos": MAISON / "Videos" / MARQUE,
    "clips": MAISON / "Videos" / MARQUE / "clips",
    "documents": MAISON / "Documents" / MARQUE,
}


def dossier(quoi, creer=True):
    """Le dossier ou ranger un type de creation."""
    chemin = reglage(f"dossiers.{quoi}", "")
    d = Path(chemin) if chemin else _DEFAUTS.get(quoi, MAISON / MARQUE)
    if creer:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    return d


def est_une_creation(chemin):
    """Vrai si le fichier vient de MU-TH-UR et non des affaires de l utilisateur."""
    try:
        return MARQUE.lower() in str(Path(chemin).resolve()).lower()
    except Exception:
        return False
