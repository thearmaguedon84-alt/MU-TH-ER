
import shutil, time
from pathlib import Path
from huggingface_hub import hf_hub_download
J = Path(r"F:/IA/flux.log")
def dire(t):
    with open(J, "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + str(t) + chr(10))
C = Path(r"F:/IA/comfyui/models")
# Schnell : le meme modele distille pour rendre en quatre etapes au lieu de
# vingt-quatre. Sur cette carte c est la difference entre sept minutes et une.
lot = [("city96/FLUX.1-schnell-gguf", "flux1-schnell-Q4_K_S.gguf", "unet", "flux1-schnell-Q4_K_S.gguf")]
for repo, fichier, sous, nom in lot:
    d = C / sous; d.mkdir(parents=True, exist_ok=True)
    dest = d / nom
    if dest.exists():
        dire("deja la : " + nom); continue
    try:
        dire("debut " + nom)
        p = hf_hub_download(repo, fichier)
        shutil.copy(p, dest)
        shutil.rmtree(Path(p).parent.parent.parent, ignore_errors=True)
        dire("fini %s  %.2f Go" % (nom, dest.stat().st_size / 2**30))
    except Exception as e:
        dire("ECHEC %s : %s" % (nom, str(e)[:200]))
dire("TERMINE-SCHNELL")
