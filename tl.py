import sys, time, httpx
sys.path.insert(0, r"C:/Users/thear/Documents/jarvis-assistant-vocal")
from tools.flux import _graphe
from tools.video import _attendre
J=open("tailles.log","w",encoding="utf-8")
for l,h in [(768,768),(896,896),(1024,1024),(1152,896)]:
    g=_graphe("a red apple on a wooden table", l, h, 7, 4, 3.5, "flux1-schnell-Q4_K_S.gguf")
    t0=time.time()
    r=httpx.post("http://127.0.0.1:8188/prompt", json={"prompt":g,"client_id":"m"}, timeout=120)
    f=_attendre(r.json()["prompt_id"], 1200)
    J.write("%dx%d : %.0f s %s\n" % (l,h,time.time()-t0, "ok" if f else "ECHEC")); J.flush()
J.write("TERMINE"); J.close()
