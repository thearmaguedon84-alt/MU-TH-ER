# Mode local (100 % hors ligne) vs mode cloud

Jarvis tourne dans deux modes, choisis par une seule ligne dans `config.yaml` :

```yaml
mode: cloud    # cloud (Claude + ElevenLabs) | local (Ollama + Piper, 100% offline)
```

| | **cloud** (defaut) | **local** |
|---|---|---|
| LLM | Claude (API Anthropic) | Ollama (`qwen3.5:4b`...) |
| Voix (TTS) | ElevenLabs | Piper (FR) |
| Transcription (STT) | faster-whisper (local) | faster-whisper (local) |
| Qualite | maximale | bonne (dépend du modèle) |
| Cout | à l'usage (API) | gratuit |
| Vie privée | appels API | **rien ne sort de la machine** |
| Matériel | léger | GPU recommandé (voir plus bas) |

Le **STT est déjà local dans les deux modes** (faster-whisper, GPU si dispo).

## Passer en mode local

1. **Ollama** (version **récente** : le tool-calling de qwen3.5 buggait sur d'anciennes
   versions) : installe [ollama.com](https://ollama.com), puis récupère un modèle qui
   gère bien le *function calling* :
   ```bash
   ollama pull qwen3.5:4b       # ~6-8 Go VRAM (teste). Plus de VRAM: 9b/27b. Peu: 2b.
   ```
   Voir [ollama.com/library](https://ollama.com/library) pour les derniers modèles ;
   `python scripts/doctor.py` en conseille un selon ta VRAM.
2. **Voix Piper** (français) : télécharge une voix depuis
   [Piper FR](https://huggingface.co/rhasspy/piper-voices/tree/main/fr/fr_FR)
   (ex. `fr_FR-siwis-medium`), place le `.onnx` **et** son `.json` dans `voix/`.
3. Dans `config.yaml` : `mode: local` (et éventuellement `ollama.modele`,
   `piper.modele`).

Sans clé Claude ni ElevenLabs, le mode local fonctionne entièrement seul. (Si Piper
n'est pas configuré, Jarvis retombe sur la voix Windows SAPI.)

## Fiabilité réelle du mode local (honnête)

Testé sur cette base de code avec `qwen3.5:4b`/`9b` et `qwen2.5:7b` :

- ✅ **Les outils du quotidien marchent bien et vite** (2–4 s par tour avec `qwen3.5:4b`) :
  lumières Hue, ambiances/scènes, OBS, minuteurs, heure, météo, mémoire, volume/média...
- ⚙️ **`think` doit être désactivé** (`ollama.think: false`, défaut). Le « raisonnement »
  natif de qwen3.5 rend le modèle très lent et fait parfois rendre les appels d'outils
  en **texte** au lieu de les exécuter. Jarvis le désactive automatiquement.
- ⚠️ **Un petit modèle se noie avec trop d'outils.** Jarvis n'expose donc au modèle
  local qu'un **jeu réduit (24 outils sur 50)**, ciblé et fiable. Avec les 50 outils,
  même qwen3.5:4b devenait lent (>30 s) et ratait/textualisait ses appels.
- 👁️ **Vision** : qwen3.5 a la vision et lit déjà le texte des boutons (testé). La
  boucle navigateur/réservation en 100 % local devient donc **plausible** (roadmap) ;
  aujourd'hui elle reste **cloud recommandé** (pilotage complet non encore validé).

### Récupération sur échec d'appel d'outil

Si le modèle local rate un appel d'outil (JSON invalide), `OllamaProvider` **réessaie
une fois** avec une consigne plus directive, puis renvoie un message d'erreur clair
plutôt que de planter.

## Matrice de compatibilité des outils

| Catégorie | Outils | cloud | local |
|---|---|---|---|
| Domotique / PC | Hue, scènes, OBS, stats, volume, apps | ✅ | ✅ |
| Utilitaires | minuteur, heure, mémoire, personnalité, présence | ✅ | ✅ |
| Météo / web | météo, recherche web | ✅ | ✅ si en ligne\* |
| Productivité internet | Gmail, Google Agenda, deadlines, brief | ✅ | ☁️ cloud recommandé |
| Communication | Discord, Instagram, appels Twilio | ✅ | ☁️ cloud recommandé |
| Vision / agentique | réservation web, assistance navigateur, capture écran | ✅ | ❌ (vision requise) |
| Serveur MCP | domotique/PC exposés | ✅ | ✅ |

\* Les outils internet ne sont pas proposés au modèle local et, plus généralement,
échouent proprement avec un message clair s'il n'y a pas de réseau.

**En résumé** : le mode local couvre très bien la **domotique et le PC** en tout
confidentialité ; pour la **productivité internet** et surtout les **features à
vision** (navigateur, réservation), le **mode cloud est recommandé**. Ces dernières
utilisent Claude pour la boucle vision, même quand `mode: local` — il suffit d'une
clé `anthropic.cle`.

## Matériel recommandé (mode local)

- **faster-whisper** `medium` : ~2–3 Go de VRAM (GPU) ; repli CPU possible mais lent.
- **qwen3.5:4b** (quantifié Q4) : ~3 Go de VRAM.
- Une carte **6 Go de VRAM** (ex. RTX 2060/3060) fait tourner les deux confortablement.
  Plus de VRAM -> `qwen3.5:9b`/`27b` ; moins -> `qwen3.5:2b` (moins fiable sur les outils).
- Piper : négligeable, temps réel sur CPU.
- `python scripts/doctor.py` détecte ta VRAM et conseille le modèle adapté.

## Kokoro vs Piper (pourquoi Piper)

Kokoro (`kokoro-onnx`) ne propose qu'**une** voix française récente, de qualité
moyenne. **Piper** a plusieurs voix FR éprouvées (`fr_FR-siwis`, `fr_FR-tom`...), est
ultra-léger et temps réel sur CPU, et est déjà intégré au projet. C'est le meilleur
choix pour un TTS local français aujourd'hui — d'où le défaut `piper`.

**Les deux sont disponibles au choix** (`voix_locale` dans `config.yaml`) :

```yaml
voix_locale: piper     # ou "kokoro"
```

Pour **Kokoro** : `uv add kokoro-onnx`, télécharge `kokoro-v1.0.onnx` et
`voices-v1.0.bin` (dépôt kokoro-onnx sur GitHub/HuggingFace), puis :

```yaml
voix_locale: kokoro
kokoro:
  modele: "chemin/vers/kokoro-v1.0.onnx"
  voix: "chemin/vers/voices-v1.0.bin"
  voix_nom: "ff_siwis"   # voix française
```

Sans modèle configuré, Jarvis retombe sur la voix Windows (SAPI).
