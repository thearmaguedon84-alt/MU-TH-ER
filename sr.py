import sys, time
sys.path.insert(0, r"C:/Users/thear/Documents/jarvis-assistant-vocal")
from tools.portrait import transposer_visage
t0=time.time()
r=transposer_visage.__wrapped__(visage="vernoux", sur="humain-AVANT.png")
open("serre.log","w",encoding="utf-8").write("%s | %.0f s" % (r, time.time()-t0))
