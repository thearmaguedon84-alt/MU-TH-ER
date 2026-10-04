"""Petites fonctions utilitaires partagees par le coeur et les outils."""
import unicodedata


def sans_accents(texte):
    """Minuscules, sans accents. Sert aux comparaisons souples."""
    t = unicodedata.normalize("NFKD", texte.lower())
    return "".join(c for c in t if not unicodedata.combining(c))
