# -*- coding: utf-8 -*-
"""Fabriquer un economiseur d'ecran autonome pour telephone.

Autonome veut dire : un seul fichier, aucun serveur, aucun reseau. Les quatre
releves y sont inscrits, le son est synthetise a la volee, et la page tient
hors ligne une fois ouverte.

Deux contraintes de telephone ont dicte la forme :

- **Le son ne demarre qu'apres un geste.** Aucun navigateur mobile ne laisse
  une page jouer un son sans que l'utilisateur ait touche l'ecran. D'ou
  l'ecran d'accueil : le premier appui sert a la fois a lancer et a autoriser.
- **L'ecran s'eteint tout seul.** On demande donc a le garder allume, quand le
  telephone le permet, et l'on repose la demande apres chaque retour.
"""
import json
from pathlib import Path

# Le dossier du projet se deduit de l emplacement de ce fichier : un chemin
# ecrit en dur ne marche que sur une machine, et publie le nom de son
# proprietaire.
RACINE = Path(__file__).resolve().parent

releves = json.loads((RACINE / "specimens.json").read_text(encoding="utf-8"))
DONNEES = json.dumps(releves, separators=(",", ":"))

PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#000000">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<title>MU-TH-UR 6000</title>
<style>
  :root { --vert: #35ff6a; }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; background: #000; overflow: hidden;
    -webkit-user-select: none; user-select: none; -webkit-tap-highlight-color: transparent; }
  body { font: 400 14px/1.5 "Courier New", ui-monospace, monospace;
    color: var(--vert); text-shadow: 0 0 6px rgba(53,255,106,.55); }

  /* Le grain et les lignes de balayage font davantage pour l illusion que
     n importe quel effet de lueur. */
  #grain { position: fixed; inset: 0; z-index: 3; pointer-events: none;
    background: repeating-linear-gradient(to bottom,
      rgba(0,0,0,0) 0 2px, rgba(0,0,0,.28) 2px 4px);
    mix-blend-mode: multiply; }
  #vignette { position: fixed; inset: 0; z-index: 2; pointer-events: none;
    background: radial-gradient(ellipse at center,
      rgba(0,0,0,0) 55%, rgba(0,0,0,.75) 100%); }

  #accueil { position: fixed; inset: 0; z-index: 10; display: flex;
    flex-direction: column; align-items: center; justify-content: center;
    gap: 18px; text-align: center; padding: 24px; }
  #accueil h1 { font-size: 17px; letter-spacing: 3px; font-weight: 400; margin: 0; }
  #accueil p { font-size: 12px; opacity: .75; margin: 0; letter-spacing: 1px; }
  #accueil.parti { display: none; }
  .clignote { animation: c 1.1s steps(1) infinite; }
  @keyframes c { 50% { opacity: .25; } }

  #scene { position: fixed; inset: 0; display: none; }
  #scene.visible { display: block; }

  .haut, .bas { position: absolute; left: 4%; right: 4%; white-space: pre;
    font-size: 11px; letter-spacing: .5px; }
  .haut { top: 3.5%; }
  .bas { bottom: 4%; font-size: 10px; }
  .droite { position: absolute; right: 0; top: 0; text-align: right; }

  /* En hauteur le dessin occupe le haut et la fiche le bas ; en largeur ils
     se placent cote a cote, comme sur le releve d origine. */
  canvas { position: absolute; left: 3%; top: 12%; width: 94%;
    image-rendering: pixelated; }
  .fiche { position: absolute; left: 5%; right: 5%; bottom: 13%;
    white-space: pre-line; font-size: 10.5px; line-height: 1.45;
    letter-spacing: .4px; columns: 2; column-gap: 14px; opacity: .9; }

  @media (orientation: landscape) {
    canvas { left: 3%; top: 50%; transform: translateY(-52%); width: 62%; }
    .fiche { left: auto; right: 4%; top: 18%; bottom: auto; width: 30%;
      columns: 1; font-size: 11px; }
  }
</style>
</head>
<body>

<div id="accueil">
  <h1>MU-TH-UR 6000</h1>
  <p>RELEVES DE SPECIMENS</p>
  <p class="clignote">TOUCHER L ECRAN POUR COMMENCER</p>
</div>

<div id="scene">
  <div class="haut">***** MU/TH-UR 6000 *****<span class="droite" id="date"></span></div>
  <canvas id="toile" width="300" height="190"></canvas>
  <div class="fiche" id="fiche"></div>
  <div class="bas" id="bas"></div>
</div>

<div id="vignette"></div>
<div id="grain"></div>

<script>
const RELEVES = __DONNEES__;

const $ = (s) => document.querySelector(s);
const toile = $("#toile"), scene = $("#scene"), accueil = $("#accueil");
let indice = -1, animation = 0, SPECIMEN = [];

/* ---------------------------------------------------------------- le son */

/* Le bruit d une tete d impression : un choc court, du bruit filtre plutot
   qu une note. Une note donnerait un carillon, pas une machine. */
const Son = {
  ctx: null,
  actif: false,
  demarrer() {
    if (this.ctx) return;
    const C = window.AudioContext || window.webkitAudioContext;
    if (!C) return;
    this.ctx = new C();
    this.ctx.resume();
    this.actif = true;
  },
  frappe() {
    if (!this.actif || !this.ctx) return;
    const t = this.ctx.currentTime;
    const n = 0.02 * this.ctx.sampleRate | 0;
    const tampon = this.ctx.createBuffer(1, n, this.ctx.sampleRate);
    const d = tampon.getChannelData(0);
    // Bruit qui s eteint vite : la percussion du marteau sur le papier.
    for (let i = 0; i < n; i++) {
      d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / n, 3.2);
    }
    const src = this.ctx.createBufferSource();
    src.buffer = tampon;
    const passe = this.ctx.createBiquadFilter();
    passe.type = "bandpass";
    passe.frequency.value = 2600 + Math.random() * 900;
    passe.Q.value = 0.9;
    const vol = this.ctx.createGain();
    vol.gain.value = 0.09;
    src.connect(passe); passe.connect(vol); vol.connect(this.ctx.destination);
    src.start(t);
  },
};

/* ------------------------------------------------------------- le trace */

function avancer() {
  indice = (indice + 1) % RELEVES.length;
  const r = RELEVES[indice];
  SPECIMEN = r.chemins;
  $("#fiche").textContent = (r.lignes || []).join("\\n");
  $("#bas").textContent = r.bas || "";
  const d = new Date(), z = (n) => String(n).padStart(2, "0");
  $("#date").textContent = "LM 05-20-79\\n" +
    z(d.getHours()) + ":" + z(d.getMinutes()) + ":" + z(d.getSeconds());
}

function tracer(secondes) {
  const c = toile.getContext("2d");
  c.clearRect(0, 0, toile.width, toile.height);
  c.lineWidth = 0.9; c.lineJoin = "round"; c.lineCap = "round";
  c.strokeStyle = "rgba(53,255,106,.92)";

  const total = SPECIMEN.length, depart = performance.now();
  const duree = secondes * 1000;
  let tracees = 0, dernierSon = 0;
  cancelAnimationFrame(animation);

  function etape(maintenant) {
    const avance = Math.min(1, (maintenant - depart) / duree);
    const cible = Math.ceil(total * avance);
    // On ne repeint que les nouveaux traits : le reste est deja pose.
    for (let k = tracees; k < cible; k++) {
      const ch = SPECIMEN[k];
      if (!ch || ch.length < 2) continue;
      c.beginPath();
      ch.forEach((p, i) => i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1]));
      c.stroke();
    }
    if (cible > tracees) {
      tracees = cible;
      // Un son par trait serait un grondement : on espace au minimum.
      if (maintenant - dernierSon > 42) { Son.frappe(); dernierSon = maintenant; }
    }
    if (avance < 1) animation = requestAnimationFrame(etape);
  }
  animation = requestAnimationFrame(etape);
}

/* ------------------------------------------------------------ le cycle */

const AFFICHAGE = 40000;   // quarante secondes par releve
const TRACE = 5;           // dont cinq de trace

function cycle() {
  avancer();
  tracer(TRACE);
  setTimeout(cycle, AFFICHAGE);
}

/* ------------------------------------------------- ecran et plein ecran */

let veilleur = null;
async function garderAllume() {
  try {
    if ("wakeLock" in navigator) {
      veilleur = await navigator.wakeLock.request("screen");
    }
  } catch (e) { /* refuse : tant pis, l ecran s eteindra */ }
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") garderAllume();
});

function commencer() {
  accueil.classList.add("parti");
  scene.classList.add("visible");
  Son.demarrer();
  garderAllume();
  const e = document.documentElement;
  if (e.requestFullscreen) e.requestFullscreen().catch(() => {});
  cycle();
}

accueil.addEventListener("click", commencer);
accueil.addEventListener("touchstart", commencer, { passive: true });

// Un appui pendant la veille passe au releve suivant.
scene.addEventListener("click", () => { avancer(); tracer(TRACE); });
</script>
</body>
</html>
"""

sortie = RACINE / "veille.html"
sortie.write_text(PAGE.replace("__DONNEES__", DONNEES), encoding="utf-8")
print("veille.html : %.0f Ko, %d releves"
      % (sortie.stat().st_size / 1024, len(releves)))
