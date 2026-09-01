import sys, time
sys.path.insert(0, r"C:/Users/thear/Documents/jarvis-assistant-vocal")
import core.raccourcis as R
t0=time.time()
r=R.essayer("fais moi une image tres soignee d un vieux pecheur breton reparant son filet sur le port")
open("bout2.log","w",encoding="utf-8").write("%s | %.0f s" % (r, time.time()-t0))
