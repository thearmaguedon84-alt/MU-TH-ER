import sys, time
sys.path.insert(0, r"C:/Users/thear/Documents/jarvis-assistant-vocal")
import core.raccourcis as R
p = "FAIT MOI UN CLIP VIDEO AVEC LA VIDEO 20260902-162943-OU-LE-A-GIGER-STYLE-BIOMECHANICAL-CREATU.MP4 ET LA MUSIQUE 20260902-185330-GRUNGE-FROM-20S-VERSE-IN-ENGLISH-ABOUT-DIRTY.MP3"
t0=time.time()
r=R.essayer(R._plat(p))
open("clip2.log","w",encoding="utf-8").write("%s | %.0f s" % (r, time.time()-t0))
