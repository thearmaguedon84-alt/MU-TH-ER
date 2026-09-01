import sys, time
sys.path.insert(0, r"C:/Users/thear/Documents/jarvis-assistant-vocal")
import core.raccourcis as R
p = "REMPLACE LE VISAGE DE 20260830-231535-ZONE-BEACH-PIN-UP-HEAD-FULL-HEAD-REPLACING-THE-H.PNG PAR CELUI DE VERNOUX 21-04-07 058.JPG"
t0=time.time()
r=R.essayer(R._plat(p))
open("visage.log","w",encoding="utf-8").write("%s | %.0f s" % (r, time.time()-t0))
