"""Ajouter et regler un personnage a la voix, sans toucher au code.

Le decoupage lui-meme vit dans dessin_anime (importer_personnages). Ici, ce
qui l entoure : trouver l image (la page du wiki s ouvre, on enregistre, la
derniere image telechargee est prise), dire qu il est adulte, le grandir, et
recaler sa bouche quand le reperage automatique l a mise sur le nez.
"""
import json
import re
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

from core.registre import outil

WIKI = "https://southpark.fandom.com"
_IMAGES = (".png", ".webp", ".jpg", ".jpeg", ".gif")


def _telechargements():
    coins = [Path.home() / "Downloads", Path("D:/téléchargement"),
             Path("D:/telechargement")]
    return [c for c in coins if c.is_dir()]


def _derniere_image(age_max=3 * 3600):
    """La derniere image enregistree, si elle date de moins de trois heures."""
    lot = [f for c in _telechargements() for f in c.iterdir()
           if f.is_file() and f.suffix.lower() in _IMAGES]
    if not lot:
        return None
    f = max(lot, key=lambda x: x.stat().st_mtime)
    return f if time.time() - f.stat().st_mtime < age_max else None


def _page_wiki(nom):
    """L adresse de sa page sur le wiki South Park (recherche si inconnue)."""
    try:
        import requests
        r = requests.get(WIKI + "/api.php", timeout=15, params={
            "action": "opensearch", "search": nom, "limit": 5,
            "format": "json"}, headers={"User-Agent": "MU-TH-UR/1.0"})
        titres = r.json()[1]
        # Le titre qui contient le nom tel quel, sinon le premier propose.
        bon = next((t for t in titres if nom.lower() in t.lower()),
                   titres[0] if titres else None)
        if bon:
            return WIKI + "/wiki/" + quote(bon.replace(" ", "_"))
    except Exception:
        pass
    return WIKI + "/wiki/Special:Search?query=" + quote(nom)


def _propre(nom):
    from tools.dessin_anime import _plat
    return _plat(nom)


def _ecrire_fiche(propre, fiche):
    from tools.dessin_anime import PERSONNAGES
    (PERSONNAGES / (propre + ".json")).write_text(
        json.dumps(fiche, ensure_ascii=False), encoding="utf-8")


def _apercu(propre):
    """Le personnage seul, sa bouche cerclee de rouge, ouvert a l ecran."""
    from PIL import Image, ImageDraw
    from tools.dessin_anime import PERSONNAGES, repere_de
    img = Image.open(PERSONNAGES / (propre + ".png")).convert("RGBA")
    e = 700 / img.height
    vue = img.resize((max(1, int(img.width * e)), 700))
    fond = Image.new("RGB", (vue.width + 80, 760), (32, 32, 38))
    fond.paste(vue, (40, 30), vue)
    r = repere_de(propre)
    if r.get("x") is not None:
        bx, by = 40 + r["x"] * vue.width, 30 + r["y"] * vue.height
        bl = max(10, r["l"] * vue.width)
        ImageDraw.Draw(fond).ellipse(
            [bx - bl / 2, by - bl / 3, bx + bl / 2, by + bl / 3],
            outline=(255, 60, 60), width=4)
    cible = PERSONNAGES / ("_apercu-%s.png" % propre)
    fond.save(cible)
    try:
        import os
        os.startfile(str(cible))
    except Exception:
        pass
    return cible


@outil(
    nom="ajouter_personnage",
    description=(
        "Ajoute un personnage pour les dessins animes. Sans image, ouvre sa "
        "page sur le wiki South Park pour enregistrer son image ; avec une "
        "image recente dans Telechargements, la decoupe et le range. Pour "
        "'ajoute le personnage Token', 'ajoute Mr Mackey, c est un adulte', "
        "'c est bon j ai enregistre l image'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "nom": {"type": "string", "description": "Le nom du personnage."},
            "adulte": {"type": "boolean",
                       "description": "Vrai pour un adulte (plus grand)."},
            "image": {"type": "string",
                      "description": "Fichier precis, sinon la derniere image telechargee."},
        },
        "required": ["nom"],
    },
    lent=True,
    phrase_attente="Je m occupe du personnage.",
)
def ajouter_personnage(nom: str, adulte: bool = False, image: str = "") -> str:
    propre = _propre(nom)
    if not propre:
        return "Il me faut le nom du personnage."
    chemin = Path(image) if image and Path(image).is_file() else None
    if chemin is None and image:
        from tools.modifier_image import _par_le_nom
        trouve = _par_le_nom(image)
        chemin = Path(trouve) if trouve else None
    if chemin is None:
        chemin = _derniere_image()
    if chemin is None:
        page = _page_wiki(nom)
        webbrowser.open(page)
        return ("J ouvre la page de %s sur le wiki. Enregistre son image en "
                "pied dans Telechargements, puis redis-moi « ajoute %s »."
                % (nom, nom))

    from tools.dessin_anime import fiche_de, importer_personnages
    rendu = importer_personnages(str(chemin), propre)
    fiche = fiche_de(propre)
    if not fiche:
        return "Le decoupage de %s a echoue : %s" % (nom, rendu)
    fiche["adulte"] = bool(adulte)
    _ecrire_fiche(propre, fiche)
    _apercu(propre)
    return ("%s est pret, decoupe depuis %s%s. Je t affiche l apercu : la "
            "bouche est cerclee de rouge. Si elle est mal placee, dis par "
            "exemple « monte la bouche de %s »."
            % (nom, chemin.name, ", en adulte" if adulte else "", nom))


@outil(
    nom="regler_personnage",
    description=(
        "Regle un personnage des dessins animes : adulte ou enfant, plus grand "
        "ou plus petit, et la position de sa bouche. Pour 'Butters est un "
        "adulte', 'fais Token plus grand', 'monte la bouche de Kyle', 'la "
        "bouche de Chef plus a gauche', 'bouche de Wendy plus petite', "
        "'montre-moi Token'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "nom": {"type": "string", "description": "Le personnage."},
            "adulte": {"type": "boolean",
                       "description": "Vrai pour adulte, faux pour enfant."},
            "taille": {"type": "string",
                       "description": "'plus grand' ou 'plus petit'."},
            "bouche": {"type": "string",
                       "description": ("'monte', 'descend', 'gauche', 'droite', "
                                       "'plus large' ou 'plus petite'.")},
            "combien": {"type": "integer",
                        "description": "Nombre de crans, 1 par defaut."},
            "bras": {"type": "string",
                     "description": ("'fixes' si ses bras ne doivent pas "
                                     "bouger (bras croises, troisieme jambe "
                                     "qui bouge), 'mobiles' sinon.")},
        },
        "required": ["nom"],
    },
)
def regler_personnage(nom: str, adulte: bool = None, taille: str = "",
                      bouche: str = "", combien: int = 1,
                      bras: str = "") -> str:
    from tools.dessin_anime import PERSONNAGES, _grandeur, repere_de
    propre = _propre(nom)
    if not (PERSONNAGES / (propre + ".png")).exists():
        return ("Je n ai pas %s. Dis « ajoute le personnage %s »."
                % (nom, nom))
    fiche = repere_de(propre)
    crans = max(1, min(int(combien or 1), 10))
    fait = []

    if adulte is not None:
        fiche["adulte"] = bool(adulte)
        fiche.pop("grandeur", None)
        fait.append("adulte" if adulte else "enfant")

    if bras:
        fixes = bool(re.search(r"fixe|bouge pas|immobile|croise", bras.lower()))
        fiche["bras_fixes"] = fixes
        fait.append("bras fixes" if fixes else "bras mobiles")

    t = (taille or "").lower()
    if t:
        actuelle = float(fiche.get("grandeur") or _grandeur(propre))
        facteur = 1.1 ** crans
        if re.search(r"petit|moins|r[ée]dui", t):
            facteur = 1 / facteur
        fiche["grandeur"] = round(actuelle * facteur, 3)
        fait.append("taille %.2f" % fiche["grandeur"])

    b = (bouche or "").lower()
    if b and fiche.get("x") is not None:
        pas = 0.012 * crans
        if re.search(r"mont|haut", b):
            fiche["y"] = round(fiche["y"] - pas, 3)
        elif re.search(r"desc|bas", b):
            fiche["y"] = round(fiche["y"] + pas, 3)
        elif re.search(r"gauche", b):
            fiche["x"] = round(fiche["x"] - pas, 3)
        elif re.search(r"droite", b):
            fiche["x"] = round(fiche["x"] + pas, 3)
        elif re.search(r"larg|grand", b):
            fiche["l"] = round(fiche["l"] * 1.15 ** crans, 3)
        elif re.search(r"petit|etroit|étroit|moins", b):
            fiche["l"] = round(fiche["l"] / 1.15 ** crans, 3)
        else:
            return ("Je n ai pas compris pour la bouche : dis monte, descend, "
                    "gauche, droite, plus large ou plus petite.")
        fiche["corrige"] = True
        fait.append("bouche deplacee")

    _ecrire_fiche(propre, fiche)
    _apercu(propre)
    if not fait:
        return "Voici %s, la bouche cerclee de rouge." % nom
    return "C est fait pour %s : %s. Je t affiche l apercu." % (
        nom, ", ".join(fait))
