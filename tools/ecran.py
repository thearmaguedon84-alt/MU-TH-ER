"""Capture d'ecran : Jarvis peut VOIR ce qui est affiche (bloc image envoye a Claude)."""
from core.registre import outil

# Cote le plus large envoye a Claude (recommandation vision d'Anthropic).
LARGEUR_CAPTURE = 1568


@outil(
    nom="capture_screen",
    description="Capture l'ecran de l'utilisateur pour VOIR ce qui y est affiche. A "
                "utiliser des que la question fait reference a l'ecran ou a ce qui est "
                "visible : 'qu'est-ce que c'est', 'lis ca', 'cette erreur', 'mon ecran', "
                "'ce message', 'traduis ce texte', 'qu'est-ce qui est ouvert', etc. "
                "L'image est renvoyee et tu peux ensuite la decrire ou la lire.",
    parametres={
        "type": "object",
        "properties": {
            "ecran": {"type": "integer",
                      "description": "Ecran a capturer : 0 = principal (defaut), "
                                     "1 = premier, 2 = deuxieme."}
        },
    },
    lent=True,
    phrase_attente="Je regarde ton ecran.",
)
def capture_screen(ecran: int = 0):
    """Capture l'ecran et renvoie une image JPEG (base64) que Claude peut voir.

    ecran : 0 = ecran principal (defaut), 1 = premier ecran, 2 = deuxieme...
    Renvoie un dict {"image": {...}, "apercu": ...} en cas de succes, sinon une
    chaine d'erreur. Le dispatch transforme l'image en bloc image.
    """
    try:
        import base64
        import io
        import mss
        from PIL import Image
    except ImportError:
        return "La capture d'ecran n'est pas installee (mss et Pillow)."

    try:
        with mss.mss() as sct:
            # monitors[0] = tous les ecrans reunis ; [1] = principal, [2] = second...
            moniteurs = sct.monitors
            if ecran and 1 <= ecran < len(moniteurs):
                cible = moniteurs[ecran]
            else:
                cible = moniteurs[1] if len(moniteurs) > 1 else moniteurs[0]
            brut = sct.grab(cible)

        image = Image.frombytes("RGB", brut.size, brut.rgb)
        largeur, hauteur = image.size
        if largeur > LARGEUR_CAPTURE:
            ratio = LARGEUR_CAPTURE / largeur
            image = image.resize((LARGEUR_CAPTURE, max(1, round(hauteur * ratio))))

        tampon = io.BytesIO()
        image.save(tampon, format="JPEG", quality=80)
        b64 = base64.b64encode(tampon.getvalue()).decode("ascii")
        return {
            "image": {"media_type": "image/jpeg", "data": b64},
            "apercu": f"Capture ecran {ecran or 1} ({image.size[0]}x{image.size[1]}).",
        }
    except Exception as e:
        return f"Impossible de capturer l'ecran : {e}"
