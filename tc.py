
import subprocess, time
from pathlib import Path
J=Path(r"F:/IA/torch_comfy.log")
def dire(t):
    with open(J,"a",encoding="utf-8") as f: f.write(time.strftime("%H:%M:%S ")+str(t)+chr(10))
J.write_text("", encoding="utf-8")
dire("mise a jour de torch")
r=subprocess.run([r"F:/IA/comfyui/.venv/Scripts/python.exe","-m","pip","install",
  "--upgrade","torch==2.7.1","torchvision==0.22.1","torchaudio==2.7.1",
  "--index-url","https://download.pytorch.org/whl/cu128"],
  capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=5400)
dire("code %s" % r.returncode)
for l in ((r.stdout or "")+(r.stderr or "")).split(chr(10))[-12:]:
    if l.strip(): dire("  "+l[:160])
r=subprocess.run([r"F:/IA/comfyui/.venv/Scripts/python.exe","-c",
  "import torch;print(torch.__version__, torch.cuda.is_available())"],
  capture_output=True,text=True,timeout=300)
dire("verif : "+(r.stdout or r.stderr).strip()[:120])
dire("TERMINE")
