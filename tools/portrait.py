"""Mettre quelqu'un dans une scene, en gardant son visage.

Le principe n'est pas de coller un visage sur une image finie mais de
fabriquer l'image en tenant compte de l'identite : la lumiere, l'angle et la
peau concordent, ce qu'un collage ne donne jamais.

Le moteur est ComfyUI et non celui des images. Ce n'est pas un caprice :
l'interface de programmation de Forge accepte les demandes de ce genre,
repond que tout va bien, et n'en fait rien — mesure faite, l'ecart pixel a
pixel avec un rendu temoin etait de zero. Ici chaque etape est explicite.

La ressemblance se mesure plutot qu'elle ne s'apprecie : InsightFace donne une
empreinte de 512 nombres par visage, et le cosinus entre deux empreintes
tranche. Au-dela de 0,5, c'est la meme personne. Les essais rates de la veille
tournaient a 0,00 ; celui qui a marche, a 0,81.
"""
import json
import re
import shutil
import time
import uuid
from pathlib import Path

from core.config import reglage
from core.dossiers import dossier
from core.file_gpu import enfile
from core.registre import outil

ADRESSE = "http://127.0.0.1:8188"

NEGATIF = ("blurry, low quality, deformed, disfigured, extra limbs, "
           "bad anatomy, cartoon, painting, 3d render, airbrushed skin, "
           "plastic skin, model face")


def _repond(delai=3):
    try:
        import httpx
        return httpx.get(f"{ADRESSE}/system_stats",
                         timeout=delai).status_code == 200
    except Exception:
        return False


def visages_connus():
    """Les visages de reference enregistres, par nom."""
    d = reglage("visages", {}) or {}
    return {str(k).lower(): str(v) for k, v in d.items()} if isinstance(d, dict) else {}


def _trouver_visage(qui):
    """Retrouve la photo de reference d une personne.

    On accepte un nom enregistre, un chemin, ou un bout de nom de fichier —
    dans cet ordre de precision.
    """
    qui = (qui or "").strip().strip('"')
    connus = visages_connus()
    if not qui:
        defaut = reglage("visage_defaut", "")
        if defaut and Path(defaut).exists():
            return Path(defaut), "toi"
        if connus:
            nom, chemin = next(iter(connus.items()))
            return Path(chemin), nom
        return None, ""

    bas = qui.lower()
    for nom, chemin in connus.items():
        if nom in bas or bas in nom:
            if Path(chemin).exists():
                return Path(chemin), nom
    if Path(qui).is_file():
        return Path(qui), Path(qui).stem
    from tools.modifier_image import _trouver
    p = _trouver(qui)
    return (Path(p), qui) if p else (None, qui)


def _deposer(source):
    racine = Path(reglage("video.moteur", r"F:\IA\comfyui"))
    (racine / "input").mkdir(exist_ok=True)
    nom = f"visage-{uuid.uuid4().hex[:8]}{Path(source).suffix.lower()}"
    shutil.copy(str(source), str(racine / "input" / nom))
    return nom


def _graphe(visage, positif, negatif, largeur, hauteur, poids,
            graine):
    modele = reglage("portrait.modele", "RealVisXL_V5.0_fp16.safetensors")
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": modele}},
        "2": {"class_type": "InstantIDModelLoader",
              "inputs": {"instantid_file": "ip-adapter.bin"}},
        # Le processeur suffit : l analyse dure deux secondes et n a pas a
        # disputer la carte au modele, qui en a bien besoin.
        "3": {"class_type": "InstantIDFaceAnalysis",
              "inputs": {"provider": "CPU"}},
        "4": {"class_type": "ControlNetLoader",
              "inputs": {"control_net_name": "instantid-controlnet.safetensors"}},
        "5": {"class_type": "LoadImage", "inputs": {"image": visage}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"text": positif, "clip": ["1", 1]}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"text": negatif, "clip": ["1", 1]}},
        # end_at a 0,8 : l identite guide la composition puis lache la main,
        # sinon les derniers pas figent un visage plaque.
        "8": {"class_type": "ApplyInstantID",
              "inputs": {"instantid": ["2", 0], "insightface": ["3", 0],
                         "control_net": ["4", 0], "image": ["5", 0],
                         "model": ["1", 0], "positive": ["6", 0],
                         "negative": ["7", 0], "weight": poids,
                         "start_at": 0.0, "end_at": 0.8}},
        "9": {"class_type": "EmptyLatentImage",
              "inputs": {"width": largeur, "height": hauteur,
                         "batch_size": 1}},
        "10": {"class_type": "KSampler",
               "inputs": {"model": ["8", 0], "positive": ["8", 1],
                          "negative": ["8", 2], "latent_image": ["9", 0],
                          "seed": graine, "steps": 30, "cfg": 5.0,
                          "sampler_name": "dpmpp_2m", "scheduler": "karras",
                          "denoise": 1.0}},
        "11": {"class_type": "VAEDecode",
               "inputs": {"samples": ["10", 0], "vae": ["1", 2]}},
        "12": {"class_type": "SaveImage",
               "inputs": {"images": ["11", 0],
                          "filename_prefix": "muthur/portrait"}},
    }


def _graphe_transposition(visage, cible, positif, negatif, poids, force,
                         graine):
    """Reprend une image existante en y imposant un autre visage.

    On ne colle rien : l image est reencodee puis redessinee partiellement,
    avec l identite en guide. La force decide de ce qui subsiste — en
    dessous de 0,5 la composition tient mais le visage change peu, au-dela
    de 0,75 c est presque une nouvelle image.
    """
    modele = reglage("portrait.modele", "RealVisXL_V5.0_fp16.safetensors")
    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": modele}},
        "2": {"class_type": "InstantIDModelLoader",
              "inputs": {"instantid_file": "ip-adapter.bin"}},
        "3": {"class_type": "InstantIDFaceAnalysis",
              "inputs": {"provider": "CPU"}},
        "4": {"class_type": "ControlNetLoader",
              "inputs": {"control_net_name":
                         "instantid-controlnet.safetensors"}},
        "5": {"class_type": "LoadImage", "inputs": {"image": visage}},
        "13": {"class_type": "LoadImage", "inputs": {"image": cible}},
        # On borne la taille : au-dela la memoire graphique sature.
        "14": {"class_type": "ImageScaleToTotalPixels",
               "inputs": {"image": ["13", 0], "upscale_method": "lanczos",
                          "megapixels": 1.0,
                          "resolution_steps": 64}},
        "15": {"class_type": "VAEEncode",
               "inputs": {"pixels": ["14", 0], "vae": ["1", 2]}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"text": positif, "clip": ["1", 1]}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"text": negatif, "clip": ["1", 1]}},
        "8": {"class_type": "ApplyInstantID",
              "inputs": {"instantid": ["2", 0], "insightface": ["3", 0],
                         "control_net": ["4", 0], "image": ["5", 0],
                         "model": ["1", 0], "positive": ["6", 0],
                         "negative": ["7", 0], "weight": poids,
                         "start_at": 0.0, "end_at": 0.9}},
        "10": {"class_type": "KSampler",
               "inputs": {"model": ["8", 0], "positive": ["8", 1],
                          "negative": ["8", 2], "latent_image": ["15", 0],
                          "seed": graine, "steps": 30, "cfg": 5.0,
                          "sampler_name": "dpmpp_2m",
                          "scheduler": "karras", "denoise": force}},
        "11": {"class_type": "VAEDecode",
               "inputs": {"samples": ["10", 0], "vae": ["1", 2]}},
        "12": {"class_type": "SaveImage",
               "inputs": {"images": ["11", 0],
                          "filename_prefix": "muthur/transposition"}},
    }


def _attendre(tache, patience):
    import httpx
    racine = Path(reglage("video.moteur", r"F:\IA\comfyui"))
    debut = time.time()
    while time.time() - debut < patience:
        time.sleep(4)
        try:
            h = httpx.get(f"{ADRESSE}/history/{tache}", timeout=60).json()
        except Exception:
            continue
        if tache not in h:
            continue
        for lot in (h[tache].get("outputs") or {}).values():
            for f in lot.get("images") or []:
                p = (racine / (f.get("type") or "output")
                     / (f.get("subfolder") or "") / f["filename"])
                if p.exists():
                    return p, None
        etat = h[tache].get("status") or {}
        if etat.get("status_str") == "error":
            msg = [m for m in (etat.get("messages") or [])
                   if m[0] == "execution_error"]
            detail = (json.dumps(msg[-1][1], ensure_ascii=False)[:150]
                      if msg else "erreur")
            return None, detail
    return None, "delai depasse"




# En dessous de cette part de l image, le visage n a pas assez de pixels pour
# etre reconnaissable une fois la mise a l echelle faite. Mesure : a 1,2 % la
# ressemblance tombait a 0,07, la ou un portrait serre donne 0,80.
SEUIL_SERRE = 0.05


def _cadre_serre(boite, largeur, hauteur, marge=1.9):
    """Un carre autour du visage, avec de quoi couper dans les cheveux."""
    x1, y1, x2, y2 = boite
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    cote = max(x2 - x1, y2 - y1) * marge
    # Un visage n est pas centre dans sa boite : le front et le cou demandent
    # plus de place que les cotes.
    cy -= cote * 0.05
    cote = min(cote, largeur, hauteur)
    gx = min(max(cx - cote / 2, 0), largeur - cote)
    gy = min(max(cy - cote / 2, 0), hauteur - cote)
    return (int(gx), int(gy), int(gx + cote), int(gy + cote))


def _recaler_teint(morceau, temoin):
    """Aligne moyenne et ecart-type de chaque canal sur la zone d origine.

    Sans cela le gros plan revient plus clair ou plus froid, et le raccord se
    voit malgre le fondu du masque.
    """
    try:
        import numpy as np
        a = np.asarray(morceau, dtype="float32")
        b = np.asarray(temoin.resize(morceau.size), dtype="float32")
        for c in range(3):
            ma, sa = a[..., c].mean(), a[..., c].std() or 1.0
            mb, sb = b[..., c].mean(), b[..., c].std()
            a[..., c] = (a[..., c] - ma) * (sb / sa) + mb
        from PIL import Image as PILImage
        return PILImage.fromarray(a.clip(0, 255).astype("uint8"))
    except Exception:
        return morceau


def _masque_adouci(taille, douceur=0.16):
    """Un masque plein au centre, fondu sur les bords. Solution de repli quand
    on n a pas su localiser le visage produit."""
    from PIL import Image as PILImage, ImageDraw, ImageFilter
    m = PILImage.new("L", taille, 0)
    marge = int(min(taille) * douceur)
    ImageDraw.Draw(m).rectangle(
        [marge, marge, taille[0] - marge, taille[1] - marge], fill=255)
    return m.filter(ImageFilter.GaussianBlur(marge * 0.7))


def _masque_visage(taille, cadre, boite):
    """Une ellipse sur le visage seul, fondue sur ses bords.

    Coller tout le rectangle du gros plan ne marche pas : le modele redessine
    la tete a sa propre echelle, donc elle ressort trop grosse, et l ancienne
    chevelure reste visible en bordure. En ne reprenant que l ovale du visage,
    on garde la silhouette, les cheveux et les epaules d origine — seuls les
    traits changent, ce qui est exactement la demande.
    """
    from PIL import Image as PILImage, ImageDraw, ImageFilter
    m = PILImage.new("L", taille, 0)
    x1, y1, x2, y2 = boite
    # Repere du gros plan, et non de l image entiere.
    x1, x2 = x1 - cadre[0], x2 - cadre[0]
    y1, y2 = y1 - cadre[1], y2 - cadre[1]
    # Le menton et le front debordent de la boite detectee.
    dx, dy = (x2 - x1) * 0.16, (y2 - y1) * 0.20
    ImageDraw.Draw(m).ellipse([x1 - dx, y1 - dy, x2 + dx, y2 + dy], fill=255)
    return m.filter(ImageFilter.GaussianBlur(max(4, int((x2 - x1) * 0.14))))


def _aligner(morceau, boite_produite, boite_voulue):
    """Amene le visage produit sur la place et la taille de l ancien.

    Sans cela, coller revient a superposer deux visages qui ne se regardent
    pas : le modele a dessine les yeux ou il a voulu dans le carre qu on lui a
    donne.
    """
    from PIL import Image as PILImage
    gx1, gy1, gx2, gy2 = boite_produite
    ox1, oy1, ox2, oy2 = boite_voulue
    if gx2 - gx1 < 4 or gy2 - gy1 < 4:
        return morceau
    facteur = ((ox2 - ox1) / (gx2 - gx1) + (oy2 - oy1) / (gy2 - gy1)) / 2.0
    facteur = min(max(facteur, 0.25), 4.0)

    large = max(1, int(morceau.width * facteur))
    haut = max(1, int(morceau.height * facteur))
    agrandi = morceau.resize((large, haut), PILImage.LANCZOS)

    # On fait coincider les centres des deux visages.
    dx = int((ox1 + ox2) / 2 - (gx1 + gx2) / 2 * facteur)
    dy = int((oy1 + oy2) / 2 - (gy1 + gy2) / 2 * facteur)
    canevas = PILImage.new("RGB", morceau.size, (0, 0, 0))
    canevas.paste(agrandi, (dx, dy))
    return canevas


def _recoller(original, resultat_serre, cadre, boite):
    """Remet le visage travaille a sa place dans l image d origine."""
    from PIL import Image as PILImage
    fond = PILImage.open(original).convert("RGB")
    largeur, hauteur = cadre[2] - cadre[0], cadre[3] - cadre[1]
    brut = PILImage.open(resultat_serre).convert("RGB")
    echelle = largeur / float(brut.width)
    morceau = brut.resize((largeur, hauteur), PILImage.LANCZOS)

    # Ou le modele a-t-il mis le visage ? On le mesure au lieu de le supposer.
    _, _, produite = _visages_dans(resultat_serre)
    voulue = (boite[0] - cadre[0], boite[1] - cadre[1],
              boite[2] - cadre[0], boite[3] - cadre[1])
    if produite:
        morceau = _aligner(morceau, [v * echelle for v in produite], voulue)

    morceau = _recaler_teint(morceau, fond.crop(cadre))
    masque = (_masque_visage((largeur, hauteur), cadre, boite) if produite
              else _masque_adouci((largeur, hauteur)))
    fond.paste(morceau, cadre[:2], masque)
    return fond


def _visages_dans(chemin):
    """Combien de visages, quelle place occupe le plus grand, et ou il est.

    Rend (nombre, proportion, cadre) ; des None si l on n a pas su regarder —
    auquel cas on laisse passer, plutot que de bloquer sur une incertitude.
    """
    racine = Path(reglage("video.moteur", r"F:\IA\comfyui"))
    py = racine / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        return None, None, None
    script = """
import json, sys
import cv2
from insightface.app import FaceAnalysis
a = FaceAnalysis(name='antelopev2', root=r'{modeles}',
                 providers=['CPUExecutionProvider'])
a.prepare(ctx_id=-1, det_size=(640, 640))
i = cv2.imread(r'{image}')
if i is None:
    print(json.dumps([None, None, None]))
    sys.exit()
h, w = i.shape[0], i.shape[1]
v = a.get(i)
# Un visage a cheval sur le bord est presque toujours une fausse detection :
# sur l image de la pin-up, InsightFace en voyait un dans les palmiers.
v = [f for f in v if f.bbox[0] > -w * 0.01 and f.bbox[1] > -h * 0.01
     and f.bbox[2] < w * 1.01 and f.bbox[3] < h * 1.01]
if not v:
    print(json.dumps([0, 0.0, None]))
    sys.exit()
v.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
       reverse=True)
x1, y1, x2, y2 = [float(z) for z in v[0].bbox]
print(json.dumps([len(v), round((x2 - x1) * (y2 - y1) / (h * w), 4),
                  [x1, y1, x2, y2]]))
""".format(modeles=str(racine / "models" / "insightface"), image=str(chemin))
    try:
        import subprocess
        r = subprocess.run([str(py), "-c", script], capture_output=True,
                           text=True, timeout=300)
        sortie = (r.stdout or "").strip().split("\n")[-1]
        return tuple(json.loads(sortie))
    except Exception:
        return None, None, None


def ressemblance(reference, produite):
    """Mesure objective : meme personne ou non. None si on ne sait pas dire."""
    racine = Path(reglage("video.moteur", r"F:\IA\comfyui"))
    py = racine / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        return None
    code = (
        "import numpy as np, cv2, json\n"
        "from insightface.app import FaceAnalysis\n"
        "a=FaceAnalysis(name='antelopev2', root=r'%s',"
        " providers=['CPUExecutionProvider'])\n"
        "a.prepare(ctx_id=-1, det_size=(640,640))\n"
        "def e(p):\n"
        "    i=cv2.imread(p)\n"
        "    if i is None: return None\n"
        "    v=a.get(i)\n"
        "    if not v: return None\n"
        # Un visage a moitie hors du cadre est presque toujours une fausse
        # detection — sur son image, InsightFace « voyait » un visage dans les
        # palmiers du bord gauche, a dix-huit pixels hors champ.
        "    h,w=i.shape[0],i.shape[1]\n"
        "    v=[f for f in v if f.bbox[0]>-w*0.01 and f.bbox[1]>-h*0.01"
        " and f.bbox[2]<w*1.01 and f.bbox[3]<h*1.01]\n"
        "    if not v: print(json.dumps([0,0.0,None]))\n"
        "    else:\n"
        "     v.sort(key=lambda f:(f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]),"
        " reverse=True)\n"
        "    x=v[0].normed_embedding\n"
        "    return x/np.linalg.norm(x)\n"
        "u,v=e(r'%s'),e(r'%s')\n"
        "print(json.dumps(None if u is None or v is None"
        " else round(float(np.dot(u,v)),3)))\n"
        % (str(racine / "models" / "insightface"), reference, produite))
    try:
        import subprocess
        r = subprocess.run([str(py), "-c", code], capture_output=True,
                           text=True, timeout=300)
        return json.loads((r.stdout or "null").strip().split("\n")[-1])
    except Exception:
        return None


@outil(
    nom="portrait_dans_scene",
    description=(
        "Fabrique une image ou une personne connue apparait dans une scene "
        "inventee, en gardant son visage. Pour 'mets-moi en cosmonaute', "
        "'mets Paul en chevalier'. IMPORTANT : donne la scene en ANGLAIS, et "
        "decris le cadrage ('waist up', 'full body'). Ne sert PAS a retoucher "
        "une image existante."
    ),
    parametres={
        "type": "object",
        "properties": {
            "scene": {"type": "string",
                      "description": "La scene et le cadrage, en anglais."},
            "qui": {"type": "string",
                    "description": "Nom d une personne enregistree, ou nom de fichier. Vide = l utilisateur."},
            "format": {"type": "string",
                       "description": "portrait, paysage ou carre."},
            "ressemblance_forte": {"type": "boolean",
                                   "description": "Colle davantage au visage, au detriment de la scene."},
        },
        "required": ["scene"],
    },
    lent=True,
    phrase_attente="Je fabrique le portrait.",
)
@enfile("image", "scene")
def portrait_dans_scene(scene: str, qui: str = "", format: str = "",
                        ressemblance_forte: bool = False) -> str:
    from tools.image import _en_anglais
    from tools.video import _demarrer

    scene = (scene or "").strip()
    if not scene:
        return "Dans quelle scene veux-tu apparaitre ?"

    source, nom = _trouver_visage(qui)
    if source is None or not Path(source).exists():
        connus = ", ".join(visages_connus()) or "aucun"
        return (f"Je ne trouve pas de photo pour « {qui or 'toi'} ». "
                f"Visages enregistres : {connus}.")

    try:
        from core.vram import liberer
        liberer(pour="image", besoin=9.0)
    except Exception:
        pass
    if not _demarrer():
        return "Le moteur ne repond pas."

    scene = _en_anglais(scene)
    tailles = {"paysage": (1216, 832), "carre": (1024, 1024)}
    largeur, hauteur = tailles.get((format or "").lower(), (832, 1216))
    poids = 1.0 if ressemblance_forte else float(
        reglage("portrait.identite", 0.8))

    visage = _deposer(source)
    graine = int(time.time()) % 2**31
    montage = _graphe(visage, scene, NEGATIF, largeur, hauteur, poids, graine)

    try:
        import httpx
        r = httpx.post(f"{ADRESSE}/prompt",
                       json={"prompt": montage, "client_id": "jarvis"},
                       timeout=120)
        if r.status_code != 200:
            return f"Le moteur a refuse : {r.text[:110]}"
        tache = (r.json() or {}).get("prompt_id")
    except Exception as e:
        return f"Le portrait n a pas demarre : {str(e)[:70]}"
    if not tache:
        return "Le moteur n a pas accepte la demande."

    produit, souci = _attendre(tache, int(reglage("portrait.patience", 900)))
    if produit is None:
        return f"Le portrait n est pas arrive : {souci}"

    cible = dossier("images")
    propre = re.sub(r"[^a-z0-9]+", "-", scene.lower())[:40].strip("-")
    chemin = cible / f"{time.strftime('%Y%m%d-%H%M%S')}-portrait-{propre}.png"
    shutil.copy(str(produit), str(chemin))

    from tools.image import _DERNIERE
    _DERNIERE["chemin"] = chemin
    _DERNIERE["demande"] = scene
    try:
        import hud
        hud.publier_image("/image/" + chemin.name, scene)
    except Exception:
        pass
    try:
        import os
        os.startfile(str(chemin))
    except Exception:
        pass

    # On le dit franchement plutot que de laisser juger a l oeil.
    score = ressemblance(str(source), str(chemin))
    if score is None:
        jugement = ""
    elif score > 0.5:
        jugement = f" La ressemblance est bonne ({score})."
    elif score > 0.35:
        jugement = f" La ressemblance est moyenne ({score}), essaie « ressemblance forte »."
    else:
        jugement = f" La ressemblance est faible ({score}) : la photo de reference est peut-etre mal cadree."
    return f"Voila {nom} dans la scene.{jugement}"


@outil(
    nom="enregistrer_visage",
    description=("Enregistre la photo de reference d une personne, pour "
                 "pouvoir la mettre dans des scenes ensuite."),
    parametres={
        "type": "object",
        "properties": {
            "nom": {"type": "string", "description": "Le prenom de la personne."},
            "photo": {"type": "string",
                      "description": "Chemin ou nom de fichier de la photo."},
        },
        "required": ["nom", "photo"],
    },
)
def enregistrer_visage(nom: str, photo: str) -> str:
    from tools.modifier_image import _trouver
    p = _trouver(photo)
    if p is None or not Path(p).exists():
        return f"Je ne trouve pas la photo « {photo} »."

    score = None
    try:
        # On verifie qu un visage est bien detectable, sinon la reference ne
        # servira a rien et l utilisateur ne le saura qu apres coup.
        score = ressemblance(str(p), str(p))
    except Exception:
        pass
    if score is None:
        return ("Je ne detecte aucun visage sur cette photo. Prends-en une de "
                "face, bien eclairee, ou le visage n est pas coupe.")

    chemin = Path(__file__).resolve().parent.parent / "config.yaml"
    try:
        import io
        t = io.open(chemin, encoding="utf-8").read()
        ligne = "  %s: %s" % (nom.strip().lower(), p)
        if re.search(r"^visages:", t, re.M):
            t = re.sub(r"^visages:\s*$", "visages:\n" + ligne, t, count=1,
                       flags=re.M)
        else:
            t = t.rstrip("\n") + "\n\n# Photos de reference, pour mettre "
            t += "quelqu un dans une scene.\nvisages:\n" + ligne + "\n"
        io.open(chemin, "w", encoding="utf-8").write(t)
    except Exception as e:
        return f"Enregistrement impossible : {str(e)[:70]}"
    return f"C est note : {nom} sera reconnu a partir de {Path(p).name}."


@outil(
    nom="transposer_visage",
    description=(
        "Pose le visage d une personne sur une image DEJA EXISTANTE, en "
        "gardant la scene, la pose et la lumiere. Pour « mets le visage de "
        "Paul sur cette image ». Different de portrait_dans_scene, qui "
        "fabrique une scene neuve."),
    parametres={
        "type": "object",
        "properties": {
            "visage": {"type": "string",
                       "description": "Qui : nom enregistre ou nom de fichier."},
            "sur": {"type": "string",
                    "description": "L image a modifier : nom, ou la derniere."},
            "force": {"type": "string",
                      "description": "legere, moyenne ou forte. Moyenne par defaut."},
        },
        "required": ["visage"],
    },
    lent=True,
    phrase_attente="Je transpose le visage.",
)
@enfile("image", "visage")
def transposer_visage(visage: str, sur: str = "", force: str = "") -> str:
    from tools.modifier_image import _trouver
    from tools.video import _demarrer

    source, nom = _trouver_visage(visage)
    if source is None or not Path(source).exists():
        connus = ", ".join(visages_connus()) or "aucun"
        return (f"Je ne trouve pas de photo pour « {visage} ». "
                f"Visages enregistres : {connus}.")

    cible = _trouver(sur)
    if cible is None or not Path(cible).exists():
        return "Je ne trouve pas l image a modifier."
    if Path(cible).resolve() == Path(source).resolve():
        return "C est la meme image des deux cotes : precise laquelle modifier."

    # InstantID s appuie sur un visage detecte dans l image d arrivee pour
    # savoir ou poser les traits. S il n y en a pas, il n a pas d ancrage : le
    # debruitage repart librement et le modele invente quelqu un. Mieux vaut
    # le dire en trois secondes que le decouvrir en soixante.
    combien, part, boite = _visages_dans(cible)
    if combien == 0:
        # Le conseil doit renvoyer vers quelque chose qui existe. Renvoyer
        # vers une autre formulation de la meme demande ferait tourner en
        # rond — c est ce que faisait la version precedente.
        return (f"Il n y a pas de visage humain sur {Path(cible).name} : "
                f"la transposition s appuie sur un visage existant pour "
                f"savoir ou poser les traits, et sans lui elle fabriquerait "
                f"quelqu un au hasard. Je peux repeindre la tete a partir "
                f"d une description — « remplace la tete par une tete d homme "
                f"brun de quarante ans » — mais je ne sais pas encore y porter "
                f"la ressemblance de {nom} : pour cela il faut une image qui "
                f"contienne deja un visage humain.")
    # Un visage minuscule ne se transpose pas dans l image entiere : on
    # travaille en gros plan, puis on recolle.
    serre = None
    if boite and part is not None and 0 < part < SEUIL_SERRE:
        try:
            from PIL import Image as PILImage
            fond = PILImage.open(cible).convert("RGB")
            cadre = _cadre_serre(boite, fond.width, fond.height)
            morceau = fond.crop(cadre).resize((1024, 1024), PILImage.LANCZOS)
            serre = Path(cible).parent / (".serre-%s.png" % uuid.uuid4().hex[:8])
            morceau.save(serre)
        except Exception:
            serre = None
    trop_petit = part is not None and 0 < part < SEUIL_SERRE and serre is None


    try:
        from core.vram import liberer
        liberer(pour="image", besoin=9.0)
    except Exception:
        pass
    if not _demarrer():
        return "Le moteur ne repond pas."

    # Sous 0,5 le visage bouge a peine, au-dela de 0,75 la scene se defait.
    forces = {"legere": 0.45, "leger": 0.45, "faible": 0.45,
              "moyenne": 0.6, "moyen": 0.6,
              "forte": 0.75, "fort": 0.75, "complete": 0.75}
    intensite = forces.get((force or "").lower(), 0.6)
    identite = float(reglage("portrait.identite", 0.8))
    if serre is not None:
        # Sur un gros plan on ne cherche pas a preserver le visage d origine,
        # on le remplace : garder une intensite prudente ne fait que melanger
        # les deux traits et rendre une bouillie. Et l identite monte au
        # maximum, puisque plus rien d autre ne compte dans le cadre.
        intensite = max(intensite, float(reglage("portrait.serre_force", 0.85)))
        identite = 1.0

    graine = int(time.time()) % 2**31
    montage = _graphe_transposition(
        _deposer(source), _deposer(serre or cible),
        "photograph of a person, natural skin texture, sharp focus",
        NEGATIF, identite, intensite, graine)

    try:
        import httpx
        r = httpx.post(f"{ADRESSE}/prompt",
                       json={"prompt": montage, "client_id": "jarvis"},
                       timeout=120)
        if r.status_code != 200:
            return f"Le moteur a refuse : {r.text[:110]}"
        tache = (r.json() or {}).get("prompt_id")
    except Exception as e:
        return f"La transposition n a pas demarre : {str(e)[:70]}"
    if not tache:
        return "Le moteur n a pas accepte la demande."

    produit, souci = _attendre(tache, int(reglage("portrait.patience", 900)))
    if produit is None:
        return f"La transposition n a pas abouti : {souci}"

    rangement = dossier("images")
    chemin = rangement / ("%s-visage-%s.png" % (
        time.strftime("%Y%m%d-%H%M%S"), re.sub(r"[^a-z0-9]+", "-", nom.lower())[:20]))
    if serre is not None:
        # Le moteur n a travaille que le gros plan : l image rendue a
        # l utilisateur est l originale, avec le visage remis a sa place.
        try:
            _recoller(cible, produit, cadre, boite).save(chemin)
        except Exception:
            shutil.copy(str(produit), str(chemin))
        try:
            Path(serre).unlink()
        except Exception:
            pass
    else:
        shutil.copy(str(produit), str(chemin))

    from tools.image import _DERNIERE
    _DERNIERE["chemin"] = chemin
    _DERNIERE["demande"] = "visage de " + nom
    try:
        import hud
        hud.publier_image("/image/" + chemin.name, "visage de " + nom)
    except Exception:
        pass
    try:
        import os
        os.startfile(str(chemin))
    except Exception:
        pass

    score = ressemblance(str(source), str(chemin))
    if score is None:
        jugement = " Je n ai pas pu mesurer la ressemblance."
    elif score > 0.5:
        jugement = f" La ressemblance est bonne ({score})."
    elif score > 0.35:
        jugement = f" La ressemblance est moyenne ({score}), essaie une force plus elevee."
    elif trop_petit:
        # Le conseil doit suivre la cause. Monter la force sur un visage de
        # trente pixels ne fait qu inventer plus fort.
        jugement = (f" La ressemblance est faible ({score}) : le visage est"
                    f" minuscule dans cette image, il n y a pas assez de"
                    f" pixels pour le reconnaitre. Prends un plan plus serre.")
    else:
        jugement = (f" La ressemblance est faible ({score}) : monte la force,"
                    f" ou change de photo de reference.")
    return f"Visage de {nom} pose sur {Path(cible).name}.{jugement}"
