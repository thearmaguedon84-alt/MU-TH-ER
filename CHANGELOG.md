# Changelog

Toutes les évolutions notables de Jarvis. Format inspiré de
[Keep a Changelog](https://keepachangelog.com/fr/) ; versionnage
[SemVer](https://semver.org/lang/fr/).

## [1.1.0] — 2026-09-02

Fabrication d'images, de musique et de vidéos en local, et le montage qui les
réunit.

### Création
- **Images** — Stable Diffusion (SDXL) via Forge, avec correction automatique
  des mains et des visages, et une demande négative par défaut qui évite les
  compositions absurdes.
- **Images très soignées** — Flux quantifié via ComfyUI : le texte écrit dans
  l'image et les scènes complexes tiennent, là où SDXL invente. Variante rapide
  par défaut (4 étapes, 45 s), variante patiente sur demande — « prends ton
  temps ».
- **Musique** — ACE-Step 1.5 : morceaux chantés ou instrumentaux, paroles et
  style au choix. Quarante-cinq secondes de musique en trois minutes.
- **Vidéo** — Wan 2.2 image-vers-vidéo. Au-delà de cinq secondes, les segments
  se relaient : la dernière image de l'un amorce le suivant, avec un rappel
  périodique de l'image d'origine pour que le personnage ne dérive pas. Passe
  d'agrandissement ×2 en fin de rendu.
- **Portraits** — InstantID : le visage d'une photo de référence porté sur une
  scène inventée, ou transposé sur une image existante. La ressemblance est
  mesurée (cosinus entre empreintes de visage) et annoncée.
- **Remplacement de zone** — tête, visage ou mains repeints seuls, sans
  refabriquer le reste de l'image.
- **Montage de clips** — images ou séquences assemblées sur un morceau, calées
  sur sa durée, enchaînées en fondu ; les séquences font l'aller-retour pour
  combler sans laisser voir la reprise.

### Fonctionnement
- Arbitrage de la mémoire graphique entre les moteurs : celui qui ne sert plus
  est déchargé avant que le suivant ne charge. Sans cela, deux modèles sur une
  carte de douze giga-octets ne sont pas deux fois plus lents mais dix.
- File d'attente : une seule fabrication à la fois, les suivantes patientent et
  le disent. Un jeton laissé par un processus mort est repris automatiquement.
- Redémarrage automatique du moteur vidéo quand sa mémoire s'est fragmentée.
- Les créations sont rangées dans les dossiers Windows habituels — `Images`,
  `Musique`, `Vidéos`, `Documents` — sous `MU-TH-UR`.
- Les décisions des raccourcis sont journalisées, y compris leurs pannes : sans
  cette trace, un raccourci qui échoue ressemble à une phrase mal comprise.

### Corrigé
- **Transposition de visage : la mauvaise image était prise.** Le rôle de
  chaque image se déduisait de son rang dans la phrase. Or « prends le visage
  sur A et mets-le sur B » et « remplace le visage sur B par celui de A »
  disent la même chose en ordre inverse. C'est le verbe qui décide désormais :
  *prendre* désigne une source, *remplacer* une cible dont la source suit
  « par », *mettre* l'inverse.
- **Transposition sur un plan large.** Un visage occupant deux pour cent du
  cadre ne survit pas au redimensionnement. Il est maintenant découpé,
  travaillé en gros plan, puis recollé — masque taillé sur les points de
  contour pour que les cheveux d'origine restent devant, fondu de Poisson,
  netteté et grain alignés sur la photo d'accueil. Mesuré : 0,03 avant, 0,82
  après.
- **Un visage détecté à cheval sur le bord de l'image** — presque toujours une
  fausse détection — n'est plus retenu comme point d'ancrage.
- **Montage de clips.** Le bouclage infini d'une séquence bloquait l'assemblage
  sur la première ; le zoom lent multipliait la durée par cent. Le sujet
  demandé était par ailleurs ignoré dès que le mot français différait du nom de
  fichier anglais — « xénomorphe » contre `xenomorph` — et le montage retombait
  en silence sur les fichiers les plus récents.
- **Noms de fichiers reconnus sans extension** : « ma photo vacances 21-04-07
  058 » se dit ainsi à voix haute, et n'était pas reconnu.
- Une demande de clip contenant le mot « vidéos » partait vers la génération de
  vidéo au lieu du montage.

## [1.0.0] — 2026-08-07

Première version publique. Assistant vocal local en français, architecture
modulaire, deux modes (cloud / local hors ligne).

### Cœur
- Chaîne vocale : openWakeWord (« Hey Jarvis ») → faster-whisper (STT local) →
  LLM → synthèse vocale, avec fenêtre de suivi pour enchaîner sans redire le mot-clé.
- Architecture modulaire : chaque outil est un fichier de `tools/` auto-découvert
  (décorateur `@outil`), registre central, `config.yaml` unique non versionné.
- Confirmation vocale des actions irréversibles ; interruption « attends/stop »
  (barge-in anti-écho) ; accusé de réception immédiat pour les outils lents.
- Abstraction des providers (mode `cloud` | `local`) :
  - LLM : **Claude** (Anthropic) ou **Ollama** (`qwen3.5:4b`…), même interface.
  - TTS : **ElevenLabs** ou, en local, **Piper** / **Kokoro** (repli voix Windows).
  - STT : faster-whisper (local dans les deux modes, GPU si dispo).

### Outils
- Domotique : Philips Hue (allumer, luminosité, couleur), ambiances/scènes.
- PC & streaming : OBS (direct, enregistrement, scènes, replay), stats système,
  lancement d'apps, média/volume, capture d'écran (vision).
- Productivité : Google Agenda (tous les agendas, y compris iCal abonnés),
  Gmail, brief du matin, mémoire long terme, minuteurs, météo, personnalités.
- Présence : détection par ping du téléphone, modes automatiques.
- Web : recherche, réservation pilotée par LLM (Playwright), assistance sur le
  vrai Chrome (CDP) avec domaines protégés en lecture seule.
- Communication : Discord (mentions + messages du jour), Instagram (abonnés &
  vues vs la veille, multi-comptes, refresh auto des tokens), appels Twilio
  (message simple V1, conversation temps réel V2).
- Serveur MCP : expose les outils domotique/PC à tout client MCP (Claude Desktop,
  Hermes), liste blanche par outil.

### Confidentialité & sécurité
- Secrets et données perso jamais versionnés (config, mémoire, logs, transcriptions
  d'appels, tokens OAuth, profils navigateur).
- Appels téléphoniques : présentation honnête comme assistant automatisé ; jamais
  de données bancaires ni de mots de passe ; blocage des numéros surtaxés.

### Installation
- Installateur interactif `scripts/setup.py` et diagnostic `scripts/doctor.py`.
- `INSTALL_WITH_AI.md` (FR/EN) : guide agnostique pour installer via n'importe
  quelle IA gratuite.
- Documentation par intégration dans `docs/`, README FR (défaut) + EN.

[1.0.0]: https://github.com/sosoj92/jarvis-assistant-vocal/releases/tag/v1.0.0
