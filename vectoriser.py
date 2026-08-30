# -*- coding: utf-8 -*-
"""Transformer un releve vert en traits que l'interface peut dessiner.

Le principe est le meme que pour le premier specimen : on ne redessine pas
l'image, on en extrait le squelette des traits, puis on suit chaque trait
comme le ferait une main. C'est ce qui permet a l'interface de le tracer
progressivement plutot que de l'afficher d'un coup.

Trois etapes :

1. **Isoler le dessin.** Les releves portent un panneau de donnees a droite et
   du texte en haut et en bas. On ne garde que la zone de la creature, sans
   quoi les lettres seraient tracees elles aussi.
2. **Squelettiser.** Un trait epais de trois pixels devient une ligne d'un
   pixel : sans cela, on suivrait deux fois chaque contour.
3. **Suivre et simplifier.** On parcourt le squelette de proche en proche, et
   on supprime les points qui n'apportent rien a la courbe.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from skimage.morphology import skeletonize

# Ce que l'interface attend : un canevas de 300 sur 190.
LARGEUR, HAUTEUR = 300.0, 190.0


def isoler(chemin, boite):
    """Ne garde que la zone du dessin, en noir et blanc."""
    img = Image.open(chemin).convert("RGB")
    L, H = img.size
    g, d, h, b = boite
    img = img.crop((int(L * g), int(H * h), int(L * d), int(H * b)))
    a = np.asarray(img).astype(np.int16)
    # Le trace est vert sur fond noir : on cherche un vert franc, pas une
    # simple luminosite, pour ignorer les reflets et le grain de l'ecran.
    vert = (a[:, :, 1] > 60) & (a[:, :, 1] > a[:, :, 0] + 18) \
        & (a[:, :, 1] > a[:, :, 2] + 18)
    return vert


def tracer(sq):
    """Suit le squelette et en tire des polylignes."""
    H, W = sq.shape
    reste = set(zip(*np.nonzero(sq)))
    voisins = [(-1, -1), (-1, 0), (-1, 1), (0, -1),
               (0, 1), (1, -1), (1, 0), (1, 1)]

    def autour(p):
        y, x = p
        for dy, dx in voisins:
            q = (y + dy, x + dx)
            if q in reste:
                yield q

    def degre(p):
        return sum(1 for _ in autour(p))

    chemins = []
    # On part des extremites : un trait suivi depuis son bout ne se coupe pas
    # en deux morceaux.
    departs = [p for p in list(reste) if degre(p) == 1]
    while reste:
        depart = None
        while departs:
            c = departs.pop()
            if c in reste:
                depart = c
                break
        if depart is None:
            depart = next(iter(reste))
        chemin = [depart]
        reste.discard(depart)
        while True:
            suite = next(autour(chemin[-1]), None)
            if suite is None:
                break
            chemin.append(suite)
            reste.discard(suite)
        if len(chemin) >= 3:
            chemins.append(chemin)
    return chemins


def simplifier(points, tolerance=0.9):
    """Douglas-Peucker : on garde les points qui font la forme."""
    if len(points) < 3:
        return points
    a, b = np.array(points[0], float), np.array(points[-1], float)
    ab = b - a
    norme = np.hypot(*ab)
    if norme < 1e-9:
        ecarts = [np.hypot(*(np.array(p, float) - a)) for p in points]
    else:
        # numpy 2 a retire le produit vectoriel en dimension deux : on
        # ecrit le determinant a la main, c est la meme chose.
        ecarts = [abs(ab[0] * (p[1] - a[1]) - ab[1] * (p[0] - a[0])) / norme
                  for p in points]
    i = int(np.argmax(ecarts))
    if ecarts[i] > tolerance:
        return simplifier(points[:i + 1], tolerance)[:-1] + \
            simplifier(points[i:], tolerance)
    return [points[0], points[-1]]


def convertir(chemin, boite, tolerance=0.9, epaisseur_min=4):
    vert = isoler(chemin, boite)
    sq = skeletonize(vert)
    bruts = tracer(sq)
    H, W = sq.shape
    # Mise a l'echelle du canevas, en gardant les proportions.
    facteur = min(LARGEUR / W, HAUTEUR / H)
    dx = (LARGEUR - W * facteur) / 2
    dy = (HAUTEUR - H * facteur) / 2

    sortie = []
    for c in bruts:
        if len(c) < epaisseur_min:
            continue
        pts = [(x * facteur + dx, y * facteur + dy) for y, x in c]
        pts = simplifier(pts, tolerance)
        if len(pts) >= 2:
            sortie.append([[round(x, 1), round(y, 1)] for x, y in pts])
    return sortie


if __name__ == "__main__":
    source = Path(sys.argv[1])
    # gauche, droite, haut, bas — en fraction de l'image.
    boite = tuple(float(x) for x in sys.argv[2].split(","))
    tol = float(sys.argv[3]) if len(sys.argv) > 3 else 0.9
    chemins = convertir(source, boite, tol)
    points = sum(len(c) for c in chemins)
    print(json.dumps({"chemins": len(chemins), "points": points}))
    Path(sys.argv[4]).write_text(json.dumps(chemins), encoding="utf-8")
