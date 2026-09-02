import sys, time
sys.path.insert(0, r"C:/Users/thear/Documents/jarvis-assistant-vocal")
import core.raccourcis as R
J=open("clip.log","w",encoding="utf-8")
for p in ["monte un clip avec mes videos de xenomorphe",
          "fais moi un clip avec mes images de licorne"]:
    t0=time.time()
    r=R.essayer(R._plat(p))
    J.write("%s -> %s | %.0f s\n" % (p, r, time.time()-t0)); J.flush()
J.write("TERMINE"); J.close()
