import sys, time
sys.path.insert(0, r"C:/Users/thear/Documents/jarvis-assistant-vocal")
import core.raccourcis as R
J=open("visage.log","w",encoding="utf-8")
for p in ["remplace le visage sur 20260901-072130-portrait-highlander-on-a-scottish-cliff-with-a-sw par celui de la photo vernoux 21-04-07 058"]:
    t0=time.time()
    r=R.essayer(R._plat(p))
    J.write("%s | %.0f s\n" % (r, time.time()-t0)); J.flush()
J.write("TERMINE"); J.close()
