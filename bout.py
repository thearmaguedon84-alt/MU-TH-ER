import sys, time
sys.path.insert(0, r"C:/Users/thear/Documents/jarvis-assistant-vocal")
from tools.video import generer_video
t0=time.time()
r=generer_video.__wrapped__(description="a white kitten on a red sofa, slow camera push in", duree=5)
open("bout.log","w",encoding="utf-8").write("%s | %.0f s" % (r, time.time()-t0))
