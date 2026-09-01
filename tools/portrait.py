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
        "    v.sort(key=lambda f:(f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]),"
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
