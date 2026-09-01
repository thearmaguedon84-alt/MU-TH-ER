import sys, time
sys.path.insert(0, r"C:/Users/thear/Documents/jarvis-assistant-vocal")
from tools.flux import image_soignee
t0=time.time()
r=image_soignee.__wrapped__(description="un vieux forgeron ecossais dans son atelier, il tient un marteau, une enseigne en bois derriere lui porte le texte MU-TH-UR 6000, lumiere de forge, photographie", format="paysage")
open("flux1.log","w",encoding="utf-8").write("%s | %.0f s" % (r, time.time()-t0))
