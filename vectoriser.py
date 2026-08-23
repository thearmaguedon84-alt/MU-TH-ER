"""Convertir le releve fourni en chemins que le terminal pourra tracer.

Dessiner une anatomie a la main en coordonnees etait au-dessus de mes moyens :
le resultat tenait du coquillage. On part donc du modele lui-meme.

Le principe : isoler les traits verts, les reduire a une epaisseur d'un pixel,
puis suivre chaque trait pour en faire une suite de points. On obtient des
chemins, c'est-a-dire exactement ce qu'un traceur dessinerait — et non une
image plaquee. Le terminal pourra donc les tracer un a un, au son de la tete
d'impression, comme pour le dessin precedent.

Les bandes de texte du haut et du bas sont ecartees : elles sont deja dans
l'interface, les reprendre ferait doublon.
"""
import json
import os
import sys

import numpy as np
from PIL import Image
from skimage.morphology import skeletonize

SOURCE = os.path.join(
    os.environ["APPDATA"], "Claude", "local-agent-mode-sessions",
    "327b4f54-e932-4f68-a54f-a353ef86e8d5",
    "11c10fec-c2d5-4fc4-83cc-85d2d6601924",
    "local_f988af02-2f2b-4577-90ea-6e628cdf7147", "uploads",
    "27f86623-4bd1-4dad-b28c-faea8d28ce83.png")

# Le dessin occupe la partie centrale ; au-dessus et en dessous, du texte.
HAUT, BAS = 196, 858   # au-dessus et en dessous : du texte, deja dans l interface
LARGEUR, HAUTEUR = 300, 190          # repere de destination
MIN_POINTS = 6                       # en deca, c'est du grain, pas un trait


def masque():
    a = np.asarray(Image.open(SOURCE).convert("RGB")).astype(int)
    vert = a[:, :, 1] - (a[:, :, 0] + a[:, :, 2]) // 2
    m = vert > 38
    m[:HAUT] = False
    m[BAS:] = False
    return m


def voisins(y, x, forme):
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                ny, nx = y + dy, x + dx
                if 0 <= ny < forme[0] and 0 <= nx < forme[1]:
                    yield ny, nx


def suivre(sq):
    """Decompose un squelette en chemins continus.

    On part des extremites — les points n'ayant qu'un seul voisin — puis on
    avance tant qu'il reste du chemin. Les boucles fermees, sans extremite,
    sont traitees ensuite depuis un point quelconque.
    """
    reste = {(int(y), int(x)) for y, x in zip(*np.nonzero(sq))}
    forme = sq.shape
    degre = {}
    for y, x in reste:
        degre[(y, x)] = sum(1 for v in voisins(y, x, forme) if v in reste)

    chemins = []

    def marcher(depart):
        chemin = [depart]
        reste.discard(depart)
        courant = depart
        while True:
            suite = [v for v in voisins(*courant, forme) if v in reste]
            if not suite:
                break
            # Preferer la continuite : le voisin le plus aligne avec le pas
            # precedent, pour ne pas zigzaguer aux intersections.
            if len(chemin) > 1 and len(suite) > 1:
                py, px = chemin[-2]
                cy, cx = courant
                dy, dx = cy - py, cx - px
                suite.sort(key=lambda v: -((v[0]-cy)*dy + (v[1]-cx)*dx))
            courant = suite[0]
            reste.discard(courant)
            chemin.append(courant)
        return chemin

    for p in sorted([p for p, d in degre.items() if d == 1]):
        if p in reste:
            chemins.append(marcher(p))
    while reste:
        chemins.append(marcher(next(iter(sorted(reste)))))
    return chemins


def simplifier(points, seuil=1.2):
    """Douglas-Peucker : garde la forme, jette les points inutiles."""
    if len(points) < 3:
        return points
    a, b = points[0], points[-1]
    ax, ay = a[1], a[0]
    bx, by = b[1], b[0]
    dx, dy = bx - ax, by - ay
    norme = (dx * dx + dy * dy) ** 0.5 or 1
    pire, idx = 0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i][1], points[i][0]
        d = abs(dy * px - dx * py + bx * ay - by * ax) / norme
        if d > pire:
            pire, idx = d, i
    if pire <= seuil:
        return [a, b]
    return simplifier(points[:idx + 1], seuil)[:-1] + simplifier(points[idx:], seuil)


def main():
    m = masque()
    print("pixels retenus :", int(m.sum()))

    sq = skeletonize(m)
    print("apres amincissement :", int(sq.sum()))

    bruts = suivre(sq)
    print("chemins bruts :", len(bruts))

    ys, xs = np.nonzero(sq)
    y0, y1 = ys.min(), ys.max()
    x0, x1 = xs.min(), xs.max()
    # Une marge : sans elle la machoire vient mordre le bord du cadre.
    MARGE = 0.93
    echelle = min(LARGEUR / (x1 - x0), HAUTEUR / (y1 - y0)) * MARGE
    decx = (LARGEUR - (x1 - x0) * echelle) / 2
    decy = (HAUTEUR - (y1 - y0) * echelle) / 2

    sortie = []
    for c in bruts:
        if len(c) < MIN_POINTS:
            continue
        c = simplifier(c, 1.1)
        pts = [[round((x - x0) * echelle + decx, 1),
                round((y - y0) * echelle + decy, 1)] for y, x in c]
        if len(pts) >= 2:
            sortie.append(pts)

    # Ordre de trace : un balayage de gauche a droite, comme la tete d un
    # traceur. Trier par longueur ferait surgir des fragments au hasard aux
    # quatre coins ; le balayage donne une progression qu on suit du regard.
    sortie.sort(key=lambda c: (min(p[0] for p in c), min(p[1] for p in c)))
    total = sum(len(c) for c in sortie)
    print(f"chemins retenus : {len(sortie)} | points : {total}")

    with open("specimen_chemins.json", "w", encoding="utf-8") as f:
        json.dump(sortie, f, separators=(",", ":"))
    print("ecrit dans specimen_chemins.json",
          os.path.getsize("specimen_chemins.json"), "octets")


if __name__ == "__main__":
    main()
