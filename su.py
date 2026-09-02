import sys, time
sys.path.insert(0, r"C:/Users/thear/Documents/jarvis-assistant-vocal")
from tools.video import generer_video
t0=time.time()
r=generer_video.__wrapped__(description="the creature dances on the beach, rhythmic body movement, camera steady", image="20260902-145323-realistic-photo-of-a-xenomorph-in-a-wide-shot-or", duree=10)
open("suite.log","w",encoding="utf-8").write("%s | %.0f s" % (r, time.time()-t0))
