# CONTEXTE CLAUDE — Projet Jarvis

> **Reprise de session :** stager ce fichier + `logs/jarvis.log` (fin)
> pour récupérer tout le contexte en moins d'une minute.

Mis à jour : 2026-09-16

---

## Identité

- **Nom :** Jarvis — assistant vocal local (interface : MU-TH-UR, hommage Alien/Nostromo)
- **Version :** v1.1.0 (2026-09-02)
- **Dossier :** `C:\Users\thear\Documents\jarvis-assistant-vocal\`
- **OS :** Windows, Python 3.13, venv dans `.venv\`
- **GitHub :** https://github.com/thearmaguedon84-alt/MU-TH-ER
- **Langue :** français uniquement

---

## Stack technique

| Composant | Outil |
|---|---|
| Wake word | openWakeWord ("Hey Jarvis") |
| STT | faster-whisper (local GPU) |
| LLM | Ollama local (qwen3.5:4b) ou Claude API (mode cloud) |
| TTS | edge-tts (voix Neural fr), Piper/Kokoro local, ElevenLabs cloud |
| Raccourcis | core/raccourcis.py — regex + flou, LLM en dernier recours |
| Config | config.yaml (non versionné, exclu du git) |
| Interface | hud.py — WebSocket + HTML (MU-TH-UR) + mobile (hud_tel.html) |

Architecture : chaque outil = tools/<nom>.py avec décorateur @outil, autodécouvert.

---

## Fichiers clés

```
hud.py                        Interface MU-TH-UR
core/raccourcis.py            Moteur de raccourcis déterministes
core/config.py                Chargement config.yaml
core/file_gpu.py              File d'attente GPU (@enfile)
tools/dessin_anime.py         DERNIER TRAVAIL — module South Park
tools/voix_clonee.py          Clonage de voix (dire_tout)
tools/chasse_voix.py          Extraction extraits vocaux
tools/entrainer_voix.py       Entraînement voix clonée
tools/essayer_voix.py         Test voix clonée
tools/clip.py                 Montage clips vidéo
tools/video.py                Génération vidéo Wan 2.2
tools/image.py                Génération image SDXL
tools/flux.py                 Génération image Flux (soignée)
logs/jarvis.log               Activité récente (lire la fin)
```

---

## Fonctionnalités intégrées (v1.1.0)

- Domotique : Philips Hue (allumer, luminosité, couleur, scènes)
- PC : OBS, stats système, lancement apps, volume, capture écran vision
- Médias : Spotify, Plex, YouTube, fichiers locaux
- Streaming TV : Chromecast, myCANAL (EpgId), Netflix (GraphQL), Prime (recopie écran)
- Web : recherche, réservation Playwright, assistance Chrome CDP
- Communication : Discord, Instagram multi-comptes, Twilio SMS + appel temps réel
- Productivité : Google Agenda, Gmail, brief du matin, mémoire long terme, météo, minuteurs
- Présence : ping téléphone, modes automatiques
- Création images : SDXL via Forge + correction mains/visages, Flux via ComfyUI
- Création musique : ACE-Step 1.5 (45 s de musique en 3 min)
- Création vidéo : Wan 2.2 image-vers-vidéo (5 s en 7 min), upscale x2
- Portraits : InstantID, transposition visage, mesure cosinus, fondu Poisson
- Montage clips : ffmpeg, fondu enchaîné, ping-pong
- Serveur MCP : expose les outils à Claude Desktop et Hermes

---

## Module dessin animé South Park — état au 14/09/2026

**Fichier :** tools/dessin_anime.py (58 Ko)

### Deux modes
- papier=True (défaut) : marionnettes découpées sur fond Flux, bouche animée, bras articulés. Rapide.
- anime=True : vraie animation plan par plan via Wan 2.2. Très lent.

### Pipeline mode papier
1. Analyse script (_analyser) : décor + répliques par personnage
2. TTS edge-tts par réplique (_dire) — ou voix clonée si extrait dispo (voix_clonee.dire_tout)
3. Génération décor vide Flux (_image_plan, style South Park)
4. Chargement cutouts personnages (_cutout -> PERSONNAGES/*.png + .json)
5. Rendu plan par plan (_plan_papier) : 25fps, bouche animée sur énergie RMS
6. Assemblage ffmpeg (_assembler) + sous-titres .srt

### Personnages adultes (taille x1.5)
chef, garrison, randy, gerald, sharon, liane, sheila, mackey, jimbo, stephen, linda, maire

### Style graphique
"flat 2D cutout cartoon animation still, thick black outlines, simple geometric shapes,
flat solid colours, no gradient, no shading, no text, wide establishing shot, plain sky"

### Raccourcis vocaux
- _dessin_anime : depuis fichier script ou texte
- _dessin_anime_dicte : depuis parole directe
- _video_mail : envoyer le dernier dessin animé par mail

### Tests du 14/09/2026
Générations toto, toto2, toto3, toto4, toto6 — Gerald, Cartman — envoyés par mail. Fonctionnait.

### Problèmes connus
- Bug generer_video "Je ne trouve pas mon_ecran" (07h14 et 07h31 le 14/09)
- _image_mail utilisé par erreur pour vidéo (toujours utiliser _video_mail)

---

## Procédure de reprise rapide pour Claude

```
1. Demander accès dossier : C:\Users\thear\Documents\jarvis-assistant-vocal\
2. Stager et lire : CONTEXTE_CLAUDE.md
3. Stager et lire fin de : logs/jarvis.log
4. Reprendre sur "Dernier travail en cours" ci-dessous
```

---

## Dernier travail en cours

16/09/2026 — Mise en place du système de sauvegarde de contexte.
Module dessin_anime.py South Park opérationnel.
Prochaine étape : a définir avec Serge.
