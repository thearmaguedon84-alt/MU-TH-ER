
import urllib.request, os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
url = "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors"
cible = r"F:\IA\forge\models\Stable-diffusion\sd_xl_base_1.0.safetensors"
def avance(n, taille, total):
    if total > 0 and n % 400 == 0:
        print(f"{n*taille/2**30:.2f} / {total/2**30:.2f} Go", flush=True)
urllib.request.urlretrieve(url, cible, avance)
print("termine :", os.path.getsize(cible)/2**30, "Go")
