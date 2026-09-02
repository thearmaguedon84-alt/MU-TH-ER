"""Fabriquer de courtes videos, en local.

Le moteur est ComfyUI et le modele Wan 2.2 TI2V-5B, qui fait le texte-vers-video
et la photo-vers-video avec les memes poids : animer une photo n'est qu'un cas
particulier ou l'on fournit la premiere image.

Trois choix imposes par une carte de douze giga-octets :

- **Les poids sont charges en fp8.** Le fichier fait 9,3 Go en pleine
  precision, ce qui ne laisse rien pour le calcul. En demi-precision il en
  occupe la moitie, pour une difference de qualite qu'on ne voit pas.
- **Le format par defaut est 704x1280 ou 1280x704, jamais plus.** Au-dela, la
  memoire sature et le rendu s'effondre.
- **Cinq secondes.** La duree se compte en images : 121 a 24 par seconde. Le
  cout monte vite et lineairement.

ComfyUI travaille en file d'attente : on depose un montage, on attend, on
recupere. Le montage est construit ici plutot que lu depuis un fichier, pour
que les reglages restent lisibles au meme endroit que les explications.
"""
import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from core.config import reglage
from core.dossiers import dossier
from core.file_gpu import enfile
from core.registre import outil

ADRESSE = "http://127.0.0.1:8188"

# Ce que le modele ne doit pas produire. Une video ratee l'est souvent par
# scintillement ou par deformation progressive, d'ou ces termes precis.
# Ce que le modele ne sait pas nommer mais sait dessiner. Les noms propres ne
# lui evoquent rien de precis ; les formes, si.
_LEXIQUE = {
    r"\bxenomorphes?\b|\bxenomorphs?\b|\balien du film\b":
        "a Giger-style biomechanical creature, glossy jet black ribbed "
        "exoskeleton, very long smooth eyeless elongated curved skull, no "
        "face, inner second jaw, skeletal elongated limbs, clawed fingers, "
        "long segmented spiked tail, dripping saliva, hunched predatory posture",
    r"\bfacehuggers?\b|\bfacehugger\b":
        "a pale parasitic creature with eight long bony finger-like legs, "
        "bulbous body and a long coiled muscular tail",
    r"\bpredators?\b":
        "a towering armoured hunter with dreadlocked tendrils, tusked "
        "mandibles and a metal mask",
}


def _developper(texte):
    """Remplace les noms propres par ce qu ils designent."""
    import re as _re
    for motif, forme in _LEXIQUE.items():
        if _re.search(motif, texte, _re.I):
            texte = _re.sub(motif, forme, texte, flags=_re.I)
    return texte


NEGATIF = ("blurry, low quality, distorted, deformed, flickering, jittery, "
           "morphing, watermark, text, static image, overexposed")

_DERNIERE = {"chemin": None, "demande": None}


def _repond(delai=3):
    try:
        import httpx
        return httpx.get(f"{ADRESSE}/system_stats", timeout=delai).status_code == 200
    except Exception:
        return False


def _demarrer(patience=300):
    if _repond():
        return True
    racine = Path(reglage("video.moteur", r"F:\IA\comfyui"))
    py = racine / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        return False
    try:
        # DETACHED_PROCESS en plus : sans cela le moteur herite d une
        # console, et la fermeture de cette console tue un rendu en cours.
        # C est ainsi qu un calcul de quatre-vingt-dix minutes s est perdu a
        # quatre-vingt-cinq pour cent.
        # --cache-none : ComfyUI garde par defaut TOUS les modeles charges.
        # L encodeur de texte pese 6,4 Go et le modele video 4,8 : ensemble
        # ils saturent une carte de douze, et le rendu deborde sur la memoire
        # vive. Mesure : quarante minutes par segment au lieu de six. On lui
        # demande donc de decharger ce qui ne sert plus.
        subprocess.Popen([str(py), "main.py", "--port", "8188",
                          "--cache-none"],
                         cwd=str(racine),
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NO_WINDOW
                         | subprocess.DETACHED_PROCESS)
    except Exception:
        return False
    debut = time.time()
    while time.time() - debut < patience:
        time.sleep(4)
        if _repond():
            return True
    return False


def _age_moteur():
    """Depuis combien de minutes le moteur tourne-t-il ?"""
    try:
        import psutil
        racine = str(Path(reglage("video.moteur", r"F:\IA\comfyui"))).lower()
        for p in psutil.process_iter(["pid", "exe", "create_time"]):
            if racine in (p.info.get("exe") or "").lower():
                return (time.time() - p.info["create_time"]) / 60.0
    except Exception:
        pass
    return 0.0


def _port_libre(port=8188, patience=40):
    """Attend que le port soit reellement rendu.

    Un processus qui tient dix giga-octets sur la carte ne meurt pas
    instantanement. Relancer avant qu il ait lache le port donne un second
    moteur qui echoue a se lier et disparait : on se retrouve alors sans
    moteur du tout, ce qui est pire que de n avoir rien fait.
    """
    import socket
    debut = time.time()
    while time.time() - debut < patience:
        s = socket.socket()
        s.settimeout(1)
        pris = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
        if not pris:
            return True
        time.sleep(2)
    return False


def _arreter_moteur():
    try:
        import psutil
        racine = str(Path(reglage("video.moteur", r"F:\IA\comfyui"))).lower()
        vises = [p for p in psutil.process_iter(["pid", "exe"])
                 if racine in (p.info.get("exe") or "").lower()]
        for p in vises:
            try:
                p.kill()
            except Exception:
                pass
        # On attend leur disparition effective, puis celle du port.
        try:
            psutil.wait_procs(vises, timeout=30)
        except Exception:
            time.sleep(6)
        return _port_libre()
    except Exception:
        return False


def _rafraichir(seuil=20):
    """Relance le moteur s il traine depuis trop longtemps.

    Sa memoire se fragmente a l usage. Quinze secondes de redemarrage valent
    mieux qu un segment qui met quarante minutes au lieu de six.
    """
    if not reglage("video.rafraichir", True):
        return False
    if _age_moteur() < seuil:
        return False
    if not _arreter_moteur():
        # Il refuse de mourir : mieux vaut le garder vieux que sans moteur.
        return False
    if _demarrer():
        return True
    # Un second essai : le premier echoue parfois sur un port pas encore rendu.
    _port_libre()
    return _demarrer()


def _deposer_image(source):
    """ComfyUI ne lit que son propre dossier d entree : on y copie la photo."""
    racine = Path(reglage("video.moteur", r"F:\IA\comfyui"))
    entree = racine / "input"
    entree.mkdir(exist_ok=True)
    nom = f"muthur-{uuid.uuid4().hex[:8]}{Path(source).suffix.lower()}"
    shutil.copy(str(source), str(entree / nom))
    return nom


def _montage(description, largeur, hauteur, images, graine, depart=None,
             etapes=20):
    """Le graphe envoye a ComfyUI, tel qu il l attend."""
    g = {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": reglage("video.modele",
                                              "wan2.2_ti2v_5B_fp16.safetensors"),
                         # fp8 : deux fois moins de memoire, difference invisible.
                         "weight_dtype": "fp8_e4m3fn"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                         "type": "wan"}},
        "3": {"class_type": "VAELoader",
              "inputs": {"vae_name": "wan2.2_vae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode",
              "inputs": {"text": description, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"text": NEGATIF, "clip": ["2", 0]}},
        "7": {"class_type": "Wan22ImageToVideoLatent",
              "inputs": {"vae": ["3", 0], "width": largeur, "height": hauteur,
                         "length": images, "batch_size": 1}},
        # Le decalage 8 est celui que recommande Wan pour ce modele ; en
        # dessous, le mouvement devient mou.
        "8": {"class_type": "ModelSamplingSD3",
              "inputs": {"model": ["1", 0], "shift": 8.0}},
        "9": {"class_type": "KSampler",
              "inputs": {"model": ["8", 0], "positive": ["4", 0],
                         "negative": ["5", 0], "latent_image": ["7", 0],
                         "seed": graine, "steps": etapes, "cfg": 5.0,
                         "sampler_name": "uni_pc", "scheduler": "simple",
                         "denoise": 1.0}},
        "10": {"class_type": "VAEDecode",
               "inputs": {"samples": ["9", 0], "vae": ["3", 0]}},
        "11": {"class_type": "CreateVideo",
               "inputs": {"images": ["10", 0], "fps": 24.0}},
        "12": {"class_type": "SaveVideo",
               "inputs": {"video": ["11", 0], "filename_prefix": "muthur/sequence",
                          "format": "auto", "codec": "auto"}},
    }
    if depart:
        g["6"] = {"class_type": "LoadImage", "inputs": {"image": depart}}
        g["7"]["inputs"]["start_image"] = ["6", 0]
    return g



# L agrandisseur : x2, photographique. Il ne cree rien, il restitue.
AGRANDISSEUR = "RealESRGAN_x2.pth"


def _graphe_agrandir(fichier):
    return {
        "1": {"class_type": "LoadVideo", "inputs": {"file": fichier}},
        "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
        "3": {"class_type": "UpscaleModelLoader",
              "inputs": {"model_name": reglage("video.agrandisseur",
                                               AGRANDISSEUR)}},
        "4": {"class_type": "ImageUpscaleWithModel",
              "inputs": {"upscale_model": ["3", 0], "image": ["2", 0]}},
        # On reprend la cadence lue dans la source : la recopier a la main est
        # le meilleur moyen de fabriquer un ralenti sans s en apercevoir.
        "5": {"class_type": "CreateVideo",
              "inputs": {"images": ["4", 0], "fps": ["2", 2]}},
        "6": {"class_type": "SaveVideo",
              "inputs": {"video": ["5", 0],
                         "filename_prefix": "muthur/agrandi",
                         "format": "auto", "codec": "auto"}},
    }


def _agrandir(chemin):
    """Double la definition d une sequence. Rend None si ca n a pas marche.

    Le cout est d une minute environ pour cinq secondes ; c est peu au regard
    des sept minutes de generation, et cela se voit sur les visages.
    """
    if not reglage("video.agrandir", True):
        return None
    import httpx

    chemin = Path(chemin)
    if not chemin.exists():
        return None
    racine = Path(reglage("video.moteur", r"F:\IA\comfyui"))
    entree = racine / "input"
    entree.mkdir(exist_ok=True)
    nom = f"agrandir-{uuid.uuid4().hex[:8]}{chemin.suffix}"
    try:
        shutil.copy(str(chemin), str(entree / nom))
        r = httpx.post(f"{ADRESSE}/prompt",
                       json={"prompt": _graphe_agrandir(nom),
                             "client_id": "jarvis"}, timeout=120)
        if r.status_code != 200:
            return None
        fiche = _attendre((r.json() or {}).get("prompt_id"),
                          int(reglage("video.patience_agrandir", 1800)))
        produit = _recuperer(fiche) if fiche else None
        return produit if produit and produit.exists() else None
    except Exception:
        return None
    finally:
        try:
            (entree / nom).unlink()
        except Exception:
            pass

def _attendre(tache, patience):
    import httpx
    debut = time.time()
    while time.time() - debut < patience:
        time.sleep(6)
        try:
            h = httpx.get(f"{ADRESSE}/history/{tache}", timeout=30).json()
        except Exception:
            continue
        if tache not in h:
            continue
        sorties = (h[tache].get("outputs") or {})
        for lot in sorties.values():
            for cle in ("videos", "gifs", "images"):
                for f in lot.get(cle) or []:
                    if f.get("filename"):
                        return f
        statut = (h[tache].get("status") or {})
        if statut.get("status_str") == "error" or statut.get("completed"):
            return None
    return None


def _recuperer(fiche):
    racine = Path(reglage("video.moteur", r"F:\IA\comfyui"))
    sous = fiche.get("subfolder") or ""
    chemin = racine / (fiche.get("type") or "output") / sous / fiche["filename"]
    return chemin if chemin.exists() else None


SEGMENT = 5      # ce que le modele sait faire d une traite
RAPPEL = 3       # tous les combien on revient a l image de reference


def _recaler_couleur(image, reference):
    """Ramene une image aux couleurs d une autre.

    Chaque passage dans le modele decale legerement la teinte et le contraste.
    Sur six relais le decalage devient une derive complete — du sombre vers le
    delave dans notre cas. On recale donc moyenne et ecart-type de chaque
    canal sur l image de depart : c est sommaire, mais cela suffit a arreter
    la derive, et cela ne touche pas au contenu.
    """
    try:
        import numpy as np
        from PIL import Image as PILImage

        a = np.asarray(PILImage.open(image).convert("RGB")).astype("float32")
        b = np.asarray(PILImage.open(reference).convert("RGB")).astype("float32")
        for c in range(3):
            ecart = a[:, :, c].std()
            if ecart < 1e-3:
                continue
            a[:, :, c] = ((a[:, :, c] - a[:, :, c].mean()) / ecart
                          * b[:, :, c].std() + b[:, :, c].mean())
        PILImage.fromarray(a.clip(0, 255).astype("uint8")).save(image)
        return True
    except Exception:
        return False


def _derniere_image(video, vers):
    """Extrait la derniere image d une sequence, pour amorcer la suivante."""
    try:
        import imageio_ffmpeg
        import subprocess
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        # -sseof : on se place juste avant la fin, sans relire tout le fichier.
        r = subprocess.run([exe, "-y", "-sseof", "-0.2", "-i", str(video),
                            "-frames:v", "1", "-q:v", "2", str(vers)],
                           capture_output=True, timeout=180)
        return vers if vers.exists() else None
    except Exception:
        return None


def _bout_a_bout(morceaux, cible):
    """Colle les segments sans fondu : ils sont deja continus."""
    import subprocess
    import imageio_ffmpeg
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    liste = cible.with_suffix(".txt")
    liste.write_text("".join("file '%s'\n" % str(m).replace("\\", "/")
                             for m in morceaux), encoding="utf-8")
    r = subprocess.run([exe, "-y", "-f", "concat", "-safe", "0",
                        "-i", str(liste), "-c", "copy", str(cible)],
                       capture_output=True, timeout=900)
    if not cible.exists():
        # Les segments peuvent differer d un rien : on reencode alors.
        subprocess.run([exe, "-y", "-f", "concat", "-safe", "0",
                        "-i", str(liste), "-c:v", "libx264", "-preset",
                        "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                        str(cible)], capture_output=True, timeout=1800)
    try:
        liste.unlink()
    except Exception:
        pass
    return cible if cible.exists() else None


@outil(
    nom="generer_video",
    description=(
        "Fabrique une courte video avec le moteur local, soit a partir d'une "
        "description seule, soit en animant une photo existante. IMPORTANT : "
        "donne la description en ANGLAIS, et decris le MOUVEMENT autant que la "
        "scene ('slow camera push in, hair moving in the wind'). Pour 'fais-moi "
        "une video de', 'anime cette photo'."
    ),
    parametres={
        "type": "object",
        "properties": {
            "description": {"type": "string",
                            "description": "La scene ET le mouvement, en anglais."},
            "image": {"type": "string",
                      "description": "Photo de depart : 'ma photo', 'la derniere image', un nom. Vide = depuis rien."},
            "duree": {"type": "integer",
                      "description": "Secondes. 5 par defaut, 10 au plus."},
            "format": {"type": "string",
                       "description": "paysage, portrait ou carre."},
            "ecran": {"type": "string", "description": "Nom d un ecran."},
        },
        "required": ["description"],
    },
    lent=True,
    phrase_attente="Je fabrique la video. C est long, plusieurs minutes.",
)
@enfile("video", "description")
def generer_video(description: str, image: str = "", duree: int = 5,
                  format: str = "", ecran: str = "") -> str:
    from tools.image import _en_anglais

    description = (description or "").strip()
    if not description:
        return "Que veux-tu que je filme ?"

    try:
        from core.vram import liberer
        liberer(pour="video", besoin=9.0)
    except Exception:
        pass

    if not _demarrer():
        return "Le moteur video ne repond pas."
    _rafraichir(45)

    description = _developper(_en_anglais(_developper(description)))
    duree = max(2, min(int(duree or 5), 60))
    # La longueur doit tomber sur un multiple de 4, plus un.
    images = int(duree * 24)
    images = images - (images % 4) + 1

    tailles = {"portrait": (480, 832), "carre": (640, 640)}
    largeur, hauteur = tailles.get((format or "").lower(), (832, 480))

    depart = None
    if image:
        from tools.modifier_image import _trouver
        source = _trouver(image)
        if source is None or not Path(source).exists():
            return "Je ne trouve pas l image de depart."
        depart = _deposer_image(source)
        # On suit le format de la photo plutot que d imposer le notre.
        try:
            from PIL import Image as PILImage
            with PILImage.open(source) as im:
                if im.height > im.width:
                    largeur, hauteur = 704, 1280
                elif im.height == im.width:
                    largeur, hauteur = 960, 960
        except Exception:
            pass

    # Au-dela de ce que le modele sait faire d une traite, on enchaine : la
    # derniere image d un segment devient la premiere du suivant.
    if duree > SEGMENT:
        return _enchainer(description, duree, largeur, hauteur, depart,
                          ecran)

    graine = int(time.time()) % 2**31
    montage = _montage(description, largeur, hauteur, images, graine, depart,
                       int(reglage("video.etapes", 20)))

    try:
        import httpx
        r = httpx.post(f"{ADRESSE}/prompt",
                       json={"prompt": montage, "client_id": "jarvis"},
                       timeout=120)
        if r.status_code != 200:
            return f"Le moteur a refuse le montage : {r.text[:110]}"
        tache = (r.json() or {}).get("prompt_id")
    except Exception as e:
        return f"La video n a pas demarre : {str(e)[:70]}"
    if not tache:
        return "Le moteur n a pas accepte la demande."

    fiche = _attendre(tache, int(reglage("video.patience", 3600)))
    if not fiche:
        return "La video n est pas arrivee dans le temps imparti."
    produit = _recuperer(fiche)
    if produit is None:
        return "Le fichier produit est introuvable."
    # La passe de finesse. Elle peut echouer sans consequence : on garde
    # alors la sequence telle quelle.
    produit = _agrandir(produit) or produit

    cible = dossier("videos")
    propre = re.sub(r"[^a-z0-9]+", "-", description.lower())[:44].strip("-")
    chemin = cible / f"{time.strftime('%Y%m%d-%H%M%S')}-{propre}{produit.suffix}"
    shutil.copy(str(produit), str(chemin))
    _DERNIERE["chemin"] = chemin
    _DERNIERE["demande"] = description

    if ecran:
        return f"Voila. {envoyer_video_ecran(ecran=ecran)}"
    try:
        import os
        os.startfile(str(chemin))
    except Exception:
        pass
    depuis = " depuis ta photo" if depart else ""
    return f"Voila ta video de {duree} secondes{depuis}."


def _enchainer(description, duree, largeur, hauteur, depart, ecran):
    """Fabrique une longue sequence par segments qui se relaient."""
    import httpx

    nombre = (int(duree) + SEGMENT - 1) // SEGMENT
    # Un enchainement dure une demi-heure ou plus : autant partir d un moteur
    # propre plutot que de le voir ralentir au troisieme segment.
    _rafraichir()
    # Le rafraichissement peut echouer : sans cette verification on deposait
    # les segments dans le vide et l on rendait « aucun segment n a abouti »
    # sans dire pourquoi.
    if not _repond() and not _demarrer():
        return "Le moteur video ne repond plus. Relance-le ou reessaie."
    cible = dossier("videos")
    travail = cible / ".segments"
    travail.mkdir(parents=True, exist_ok=True)
    morceaux = []
    amorce = depart
    # Une image fournie par l utilisateur est le sujet, pas un point de
    # depart : chaque segment y revient, quitte a perdre le raccord du
    # mouvement. Deux relais suffisaient a ne plus reconnaitre sa photo.
    fournie = depart is not None
    # Les images de relais ne servent qu au chainage : on ne les garde pas.
    a_effacer = []
    # La toute premiere image sert d etalon : couleur et personnage.
    reference = None

    for i in range(nombre):
        images = SEGMENT * 24
        images = images - (images % 4) + 1
        graine = (int(time.time()) + i * 7919) % 2**31
        montage = _montage(description, largeur, hauteur, images, graine,
                           amorce, int(reglage("video.etapes", 20)))
        try:
            r = httpx.post(f"{ADRESSE}/prompt",
                           json={"prompt": montage, "client_id": "jarvis"},
                           timeout=120)
            tache = (r.json() or {}).get("prompt_id") if r.status_code == 200 else None
        except Exception:
            tache = None
        if not tache:
            break
        fiche = _attendre(tache, int(reglage("video.patience", 5400)))
        if not fiche:
            break
        produit = _recuperer(fiche)
        if produit is None:
            break
        bout = travail / f"segment-{i:02d}{produit.suffix}"
        shutil.copy(str(produit), str(bout))
        morceaux.append(bout)

        # La derniere image amorce le segment suivant : c est ce qui rend la
        # jointure invisible.
        if i + 1 < nombre:
            vue = travail / f"relais-{i:02d}.png"
            if _derniere_image(bout, vue) is None:
                break
            if fournie:
                # On repart toujours de l original, jamais de sa copie.
                amorce = depart
                continue
            if reference is None:
                # La premiere image du premier segment devient l etalon.
                reference = travail / "reference.png"
                try:
                    import shutil as _s
                    _s.copy(str(vue), str(reference))
                except Exception:
                    reference = None
                a_effacer.append(reference) if reference else None
            elif (i + 1) % RAPPEL == 0:
                # On revient a l etalon : on perd le raccord du mouvement,
                # on garde le personnage. C est le meilleur des deux maux.
                try:
                    import shutil as _s
                    _s.copy(str(reference), str(vue))
                except Exception:
                    pass
            else:
                _recaler_couleur(vue, reference)
            amorce = _deposer_image(vue)
            a_effacer.append(vue)

    if not morceaux:
        return "Aucun segment n a abouti."

    # La finesse vient a la fin, segment par segment : quatre cents images
    # agrandies d un coup ne tiennent pas sur la carte, cent vingt si.
    affines = []
    for m in morceaux:
        fin = _agrandir(m)
        affines.append(fin if fin else m)

    propre = re.sub(r"[^a-z0-9]+", "-", description.lower())[:40].strip("-")
    final = cible / f"{time.strftime('%Y%m%d-%H%M%S')}-{propre}.mp4"
    assemble = _bout_a_bout(affines, final)
    for m in morceaux + a_effacer:
        try:
            m.unlink()
        except Exception:
            pass
    try:
        travail.rmdir()
    except Exception:
        pass
    if assemble is None:
        return "Les segments sont faits mais l assemblage a echoue."

    _DERNIERE["chemin"] = assemble
    _DERNIERE["demande"] = description
    faits = len(morceaux) * SEGMENT
    if ecran:
        return (f"Sequence de {faits} secondes en {len(morceaux)} segments. "
                f"{envoyer_video_ecran(ecran=ecran)}")
    return (f"Voila ta sequence de {faits} secondes, "
            f"montee a partir de {len(morceaux)} segments.")


@outil(
    nom="envoyer_video_ecran",
    description="Joue la derniere video fabriquee sur une television.",
    parametres={
        "type": "object",
        "properties": {
            "ecran": {"type": "string", "description": "Nom de l ecran."},
        },
        "required": ["ecran"],
    },
    lent=True,
    phrase_attente="J envoie la video.",
)
def envoyer_video_ecran(ecran: str) -> str:
    chemin = _DERNIERE.get("chemin")
    if not chemin or not Path(chemin).exists():
        return "Je n ai pas de video sous la main."
    from tools.cast import _adresse_pour, _choisir
    appareil = _choisir(ecran)
    if appareil is None:
        return f"Je ne trouve pas {ecran}."
    port = int(reglage("hud.port", 8770))
    url = (f"http://{_adresse_pour(appareil.cast_info.host)}:{port}"
           f"/video/{Path(chemin).name}")
    try:
        appareil.wait(timeout=12)
        appareil.media_controller.play_media(url, "video/mp4")
        appareil.media_controller.block_until_active(timeout=20)
    except Exception as e:
        return f"L envoi a echoue : {str(e)[:60]}"
    return f"Ca passe sur {appareil.cast_info.friendly_name}."
