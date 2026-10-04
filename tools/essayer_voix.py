"""Faire dire une phrase à un personnage, pour l'entendre.

Le geste qu'on a répété vingt fois pendant la reconstruction des voix : avant
de monter un film de sept secondes qui coûte trois minutes de calcul, on veut
juste savoir si la voix tient. Monter un film pour ça, c'est payer le décor,
le découpage et l'encodage pour écouter deux phrases.

L'outil emploie exactement la chaîne des films — même référence, même modèle
ré-entraîné, mêmes réglages — pour que ce qu'on entend ici soit ce qu'on
entendra là-bas. Une écoute qui ne prédit pas le film ne sert à rien.

Plusieurs phrases d'un coup si on veut : le modèle met une demi-minute à se
charger, et le faire deux fois pour deux phrases coûte plus cher que les deux
synthèses réunies.
"""
import time
import uuid
from pathlib import Path

from core.dossiers import dossier
from core.file_gpu import enfile
from core.registre import outil

from tools import voix_clonee

SORTIE = dossier("documents") / "voix" / "essais"


@outil(
    nom="essayer_voix",
    description=(
        "Fait dire une ou plusieurs phrases a un personnage et rend les "
        "fichiers son, sans monter de film. Pour 'fais dire a Cartman que...', "
        "'je veux entendre la voix de Gerald'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "qui": {"type": "string", "description": "Le personnage."},
            "texte": {
                "type": "string",
                "description": ("Ce qu il dit. Plusieurs phrases se separent "
                                "par une barre verticale."),
            },
        },
        "required": ["qui", "texte"],
    },
    lent=True,
    phrase_attente="Je lui fais dire ca, un instant.",
)
@enfile("essai de voix", "qui")
def essayer_voix(qui: str, texte: str) -> str:
    nom = voix_clonee._plat(qui)
    extrait = voix_clonee.extrait_de(nom)
    if not extrait:
        return ("Je n ai pas d extrait de %s. Depose un son dans le dossier "
                "des voix, ou passe par la chasse." % qui)

    phrases = [p.strip() for p in str(texte).split("|") if p.strip()]
    if not phrases:
        return "Il faudrait me dire quoi lui faire dire."

    SORTIE.mkdir(parents=True, exist_ok=True)
    marque = "%s-%s" % (nom, uuid.uuid4().hex[:6])
    pieces, fichiers = [], []
    for i, phrase in enumerate(phrases, 1):
        chemin = SORTIE / ("%s-%d.wav" % (marque, i))
        pieces.append((nom, phrase, chemin))
        fichiers.append(chemin)

    debut = time.time()
    voix_clonee.dire_tout(pieces)
    faits = [f for f in fichiers if f.exists() and f.stat().st_size > 1000]
    if not faits:
        return ("La synthese n a rien produit. Le journal du moteur est dans "
                "%s." % (voix_clonee.CACHE / "_dernier.log"))

    fin = voix_clonee.modele_fin(nom)
    comment = ("son modele reentraine" if fin
               else "le clonage a la volee — il n a pas encore de modele "
                    "reentraine")
    return ("%s, avec %s :\n%s\n\n%.0f secondes de calcul. Les fichiers sont "
            "dans %s."
            % (qui.title(), comment,
               "\n".join("  %s   « %s »" % (f.name, p)
                         for f, p in zip(faits, phrases)),
               time.time() - debut, SORTIE))
