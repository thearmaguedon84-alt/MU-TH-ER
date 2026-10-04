# L'épisode du jour

Chaque matin, Jarvis fabrique un court dessin animé parodique tiré de la une
du *Monde*, et te l'envoie par mail. Rien n'est publié sans ton accord.

> « Fais le South Park du jour » · « Un dessin animé avec Kyle et Randy sur
> l'actualité » · « Publie le South Park » · « Jette le South Park du jour »

## Ce qu'il faut

- **Les personnages** : des dessins détourés dans `Documents/MU-TH-UR/personnages`
  (aucun n'est fourni). « Ajoute le personnage X » t'aide à les préparer.
- **Le moteur d'images** Flux (ComfyUI) pour les décors — voir le README.
- **Claude** pour écrire le scénario : installe Claude Code et connecte-le une
  fois avec `claude auth login`. Sans lui, Jarvis se rabat sur le modèle local,
  nettement moins drôle.
- **Gmail** configuré (section `mail`) pour recevoir l'aperçu.
- Facultatif : des **voix clonées** (XTTS) pour les personnages ; sinon chacun
  reçoit une voix de synthèse distincte.

## Réglages (`config.yaml`)

```yaml
south_park_du_jour:
  actif: true
  heure: 7
  modele: sonnet
  instagram: ""      # vide : la vidéo et sa légende arrivent par mail
```

## Comment ça marche

1. Lecture de la une, sujets graves écartés (décès, attentats, catastrophes).
2. Scénario écrit par Claude, relu pour les accents et les apostrophes.
3. Fabrication : décors vides de figurants, voix, sous-titres.
4. Format vertical 1080×1920, sous-titres incrustés, mention « parodie, IA ».
5. Mail avec la vidéo et la légende prête à coller dans Instagram ou TikTok.

## Les règles

Une personne réelle n'apparaît qu'en **caricature** (`PARODIES` dans
`tools/south_park_du_jour.py`), avec une voix de synthèse qui n'est pas la
sienne — jamais sa voix clonée. Active la mention « contenu généré par IA » au
moment de publier.
