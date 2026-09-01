"""Flux : le rendu soigne, quand la ressemblance ne compte pas.

Flux ne remplace pas SDXL, il occupe une place que rien ne tenait. SDXL est
rapide et sait porter une identite grace a InstantID ; il bute en revanche sur
le texte ecrit dans l image et sur les scenes ou beaucoup d elements doivent
se tenir ensemble. Flux tient les deux, et compte les doigts nettement mieux.

Il coute cher : douze giga-octets de modele sur une carte qui en a douze.
ComfyUI en deverse une partie en memoire vive, et une image prend des minutes
la ou SDXL prend des secondes. C est un choix au coup par coup.

Deux details qui ne s inventent pas :

- **Le guidage ne se regle pas la ou on croit.** Flux est un modele distille :
  le `cfg` du KSampler doit rester a 1, et c est `FluxGuidance` qui tient le
  role. Monter le cfg brule l image.
- **La demande negative ne sert a rien.** A cfg 1 elle n est jamais evaluee.
  On laisse un texte vide plutot que d entretenir l illusion qu elle agit.
"""
import re
import shutil
import time
from pathlib import Path

from core.config import reglage
from core.dossiers import dossier
from core.file_gpu import enfile
from core.registre import outil

ADRESSE = "http://127.0.0.1:8188"
# Quatre etapes contre vingt-quatre : une minute contre sept.
RAPIDE = "flux1-schnell-Q4_K_S.gguf"
PATIENT = "flux1-dev-Q4_K_S.gguf"
MODELE = RAPIDE

# Les formats, en multiples de 64 : Flux travaille par blocs de seize pixels
# dans l espace latent et se degrade sur des tailles qui ne tombent pas juste.
FORMATS = {
    "portrait": (896, 1152),
    "paysage": (1152, 896),
    "carre": (1024, 1024),
    "large": (1216, 832),
}


def _moteur():
    """Le meme moteur que la video : ComfyUI. On reutilise son demarrage."""
    from tools.video import _demarrer
    return _demarrer()


def _graphe(description, largeur, hauteur, graine, etapes, guidage,
            modele=None):
    """Le reseau quantifie, l encodeur a part, le decodeur a part.

    Les charger separement n est pas une coquetterie : c est ce qui permet a
    ComfyUI de decharger l encodeur des qu il a fini, et donc au reseau
    d avoir la carte pour lui seul.
    """
    return {
        "1": {"class_type": "UnetLoaderGGUF",
              "inputs": {"unet_name": modele or reglage("image.flux",
                                                    MODELE)}},
        "2": {"class_type": "DualCLIPLoader",
              "inputs": {"clip_name1": "t5xxl_fp8_scaled.safetensors",
                         "clip_name2": "clip_l.safetensors",
                         "type": "flux",
                         # L encodeur sur le processeur. Il ne travaille
                         # qu une fois, en quelques secondes, et libere ainsi
                         # cinq giga-octets : sans cela il reste en memoire
                         # pendant l echantillonnage, la carte deborde sur la
                         # RAM et une etape passe de trois secondes a soixante.
                         "device": "cpu"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": "flux-ae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"text": description, "clip": ["2", 0]}},
        # A cfg 1 le negatif n est jamais evalue ; il doit exister quand meme
        # parce que le KSampler l exige.
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "", "clip": ["2", 0]}},
        "6": {"class_type": "FluxGuidance",
              "inputs": {"conditioning": ["4", 0], "guidance": guidage}},
        "7": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": largeur, "height": hauteur,
                         "batch_size": 1}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "positive": ["6", 0],
                         "negative": ["5", 0], "latent_image": ["7", 0],
                         "seed": graine, "steps": etapes, "cfg": 1.0,
                         "sampler_name": "euler", "scheduler": "simple",
                         "denoise": 1.0}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": "muthur/flux"}},
    }

@outil(
    nom="image_soignee",
    description=(
        "Genere une image tres soignee avec Flux : plus lent que l image "
        "ordinaire, mais bien meilleur sur les mains, le texte ecrit dans "
        "l image et les scenes compliquees. Pour 'une image tres soignee', "
        "'en haute qualite', 'avec Flux', 'soigne le rendu'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "description": {"type": "string",
                            "description": "Ce qu il faut representer."},
            "format": {"type": "string",
                       "description": "portrait, paysage, carre ou large."},
            "patient": {"type": "boolean",
                        "description": "Prendre le temps du meilleur rendu : sept minutes au lieu d une."},
            "ecran": {"type": "string", "description": "Nom d un ecran."},
            "par_mail": {"type": "boolean",
                         "description": "Envoyer l image par courriel."},
        },
        "required": ["description"],
    },
    lent=True,
    phrase_attente="Je soigne celle-la, ca prendra quelques minutes.",
)
@enfile("image soignee", "description")
def image_soignee(description: str, format: str = "",
                  patient: bool = False, ecran: str = "",
                  par_mail: bool = False) -> str:
    import httpx

    from tools.image import _en_anglais

    description = (description or "").strip()
    if not description:
        return "Que veux-tu que je dessine ?"

    # La traduction AVANT de liberer la carte, et non apres : elle passe par
    # le modele de langue, qui reprend cinq giga-octets. Fait dans l autre
    # sens, il evincait Flux qui devait tout recharger — quatre minutes
    # perdues sur une image qui en demande une.
    # Flux comprend les phrases entieres mieux que les listes de mots-clefs :
    # on lui parle en anglais, mais on ne hache pas la demande.
    texte = _en_anglais(description)

    try:
        from core.vram import liberer
        liberer(pour="video", besoin=10.0)
    except Exception:
        pass
    if not _moteur():
        return "Le moteur d images soignees ne repond pas."
    largeur, hauteur = FORMATS.get((format or "").lower(), FORMATS["carre"])
    graine = int(time.time()) % 2**31
    # Schnell est distille : au-dela de quatre etapes il ne gagne rien et perd
    # du temps. Dev, lui, a besoin de ses vingt-quatre.
    modele = PATIENT if patient else reglage("image.flux", RAPIDE)
    etapes = (int(reglage("image.flux_etapes_patient", 24)) if patient
              else int(reglage("image.flux_etapes", 4)))
    g = _graphe(texte, largeur, hauteur, graine, etapes,
                float(reglage("image.flux_guidage", 3.5)), modele)

    t0 = time.time()
    try:
        r = httpx.post(f"{ADRESSE}/prompt",
                       json={"prompt": g, "client_id": "jarvis-flux"},
                       timeout=120)
        if r.status_code != 200:
            return f"Le moteur a refuse : {r.text[:120]}"
        tache = (r.json() or {}).get("prompt_id")
    except Exception as e:
        return f"L image n a pas demarre : {str(e)[:70]}"

    from tools.video import _attendre, _recuperer
    fiche = _attendre(tache, int(reglage("image.flux_patience", 1800)))
    if not fiche:
        return "L image soignee n est pas arrivee a temps."
    produit = _recuperer(fiche)
    if produit is None:
        return "Le fichier produit est introuvable."

    cible = dossier("images")
    propre = re.sub(r"[^a-z0-9]+", "-", texte.lower())[:44].strip("-")
    chemin = cible / f"{time.strftime('%Y%m%d-%H%M%S')}-{propre}.png"
    shutil.copy(str(produit), str(chemin))

    from tools import image as I
    I._DERNIERE["chemin"] = chemin
    I._DERNIERE["demande"] = texte

    suite = ""
    if par_mail:
        suite = " " + I.envoyer_derniere_a_soi()
    if ecran:
        return f"Voila.{suite} {I.envoyer_image_ecran(ecran=ecran)}"
    try:
        import os
        os.startfile(str(chemin))
    except Exception:
        pass
    return f"Voila, soignee, en {time.time() - t0:.0f} secondes.{suite}"
