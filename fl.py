
import shutil, time
from pathlib import Path
from huggingface_hub import hf_hub_download
J = Path(r"F:/IA/flux.log")
def dire(t):
    with open(J, "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + str(t) + chr(10))
J.write_text("", encoding="utf-8")
C = Path(r"F:/IA/comfyui/models")
# fp8 tout-en-un : le modele complet dans un seul fichier, le plus simple a
# charger. Sur douze giga-octets il faut de l offload, ComfyUI le fait seul.
paires = [
    ("Comfy-Org/flux1-dev", "flux1-dev-fp8.safetensors", "checkpoints", "flux1-dev-fp8.safetensors"),
]
for repo, fichier, sous, nom in paires:
    d = C / sous
    d.mkdir(parents=True, exist_ok=True)
    dest = d / nom
    if dest.exists():
        dire("deja la : " + nom); continue
    try:
        dire("debut " + nom)
        p = hf_hub_download(repo, fichier)
        shutil.copy(p, dest)
        dire("fini %s  %.2f Go" % (nom, dest.stat().st_size / 2**30))
    except Exception as e:
        dire("ECHEC %s : %s" % (nom, str(e)[:200]))
dire("TERMINE")
