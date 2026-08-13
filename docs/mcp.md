# Serveur MCP de Jarvis

Expose les outils domotique/PC de Jarvis via le **Model Context Protocol**, pour
qu'ils soient pilotables par n'importe quel client MCP (Claude Desktop, Hermes
Agent, MCP Inspector...). Le serveur tourne **independamment** de l'assistant
vocal : les deux peuvent tourner en meme temps.

```bash
uv run python -m jarvis.mcp_server
```

Reutilise le registre d'outils (`tools/`) : chaque outil marque `mcp_expose=True`
est publie automatiquement avec son nom, sa description et son schema de
parametres. Aucune duplication.

## Outils exposes (14)

Domotique et PC uniquement, par securite :

| Outil | Role |
|---|---|
| `allumer_lumiere`, `regler_luminosite`, `changer_couleur` | Lumieres Hue |
| `activer_mode` | Ambiances (off, retour, film, stream) |
| `start_stream`, `stop_stream`*, `start_record`, `stop_record`, `switch_scene`, `save_replay` | OBS |
| `get_system_stats` | GPU / CPU / RAM / disque |
| `lancer_minuteur`, `heure_et_date`, `meteo` | Utilitaires |

`*` `stop_stream` demande une confirmation (voir plus bas).

**Non exposes** (sensibles, volontairement prives) : mails, memoire personnelle,
lancement d'apps, capture d'ecran, brief, presence, Discord.

## Securite

- **Exposition en opt-in** : un outil n'est visible via MCP que s'il porte
  `mcp_expose=True`. Un nouvel outil ajoute dans `tools/` est donc **prive par
  defaut** — c'est voulu (un agent externe ne doit voir que l'autorise).
- **Confirmation** : les outils `confirmation=True` exposes (ex. `stop_stream`)
  ne s'executent pas seuls. Sans argument `confirm: true`, ils renvoient une
  demande de confirmation que le client doit relayer, puis rappeler avec
  `confirm: true`. Pas d'action irreversible silencieuse.
- **Journal** : tous les appels externes sont ecrits dans `logs/mcp.log`.

## Exposer un outil de plus

Dans son decorateur `@outil(...)`, ajoute `mcp_expose=True`. Il apparait au
prochain demarrage du serveur, sans autre code.

```python
@outil(nom="mon_outil", mcp_expose=True, description="...", parametres={...})
def mon_outil(...): ...
```

## Brancher dans Claude Desktop

Edite `claude_desktop_config.json` (Windows :
`%APPDATA%\Claude\claude_desktop_config.json`) :

```json
{
  "mcpServers": {
    "jarvis": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\Users\\ton-utilisateur\\jarvis-vocal",
               "python", "-m", "jarvis.mcp_server"]
    }
  }
}
```

Redemarre Claude Desktop. Les 14 outils apparaissent ; demande par ex.
« allume la chambre » ou « donne-moi les stats systeme ».

## Brancher dans Hermes Agent

Meme principe (transport stdio). Dans la configuration MCP de Hermes, declare un
serveur :

```json
{
  "name": "jarvis",
  "command": "uv",
  "args": ["run", "--directory", "C:\\Users\\ton-utilisateur\\jarvis-vocal",
           "python", "-m", "jarvis.mcp_server"]
}
```

(Adapte le chemin. Si Hermes attend une simple ligne de commande, utilise
`uv run --directory C:\Users\ton-utilisateur\jarvis-vocal python -m jarvis.mcp_server`.)

## Client distant (HTTP/SSE)

Pour un client qui n'est pas sur la meme machine, passe en HTTP dans
`config.yaml` :

```yaml
mcp:
  transport: "http"     # au lieu de "stdio"
  host: "0.0.0.0"       # ou 127.0.0.1
  port: 8765
```

Le serveur ecoute alors en streamable-http ; le client se connecte a
`http://<machine>:8765`. (Le transport stdio reste recommande pour les clients
desktop locaux.)

## Test rapide (MCP Inspector)

```bash
npx @modelcontextprotocol/inspector uv run --directory C:\Users\ton-utilisateur\jarvis-vocal python -m jarvis.mcp_server
```

Dans l'inspecteur, appelle `allumer_lumiere` avec `{"piece": "chambre",
"allumer": true}` : ta lumiere Hue doit s'allumer.
