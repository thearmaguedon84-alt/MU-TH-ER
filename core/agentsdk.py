"""Provider Claude via l'abonnement (Claude Code en mode -p).

Contrairement a ClaudeProvider qui appelle l'API avec une cle et consomme des
credits, celui-ci passe par le binaire `claude` authentifie avec l'abonnement
de l'utilisateur : l'usage est decompte des limites de l'abonnement.

Claude Code n'expose pas nativement les outils de Jarvis. On s'en sert donc
comme d'un pur moteur de raisonnement : on lui decrit les outils disponibles,
et on lui demande de repondre par un objet JSON indiquant soit l'outil a
appeler, soit la phrase a dire. Jarvis execute ensuite lui-meme.
"""
import json
import logging
import os
import re
import shutil
import subprocess

LOG = logging.getLogger("jarvis")

CONSIGNE = """Tu es le moteur de decision d'un assistant vocal francais.

On te donne la liste des outils disponibles et la conversation en cours.
Tu dois repondre EXCLUSIVEMENT par un objet JSON valide, sans aucun texte
avant ou apres, sans balises de code.

Deux formes possibles :

  {"outil": "nom_de_l_outil", "args": {"parametre": "valeur"}}
  {"reponse": "phrase courte a dire a voix haute"}

Regles :
- Des que la demande implique une action sur l'ordinateur, utilise un outil.
  Ne reponds jamais par du texte quand un outil peut faire le travail.
- Pour lancer un jeu ou une application, l'outil est launch_app.
- Pour pause, lecture, piste suivante ou volume, l'outil est controler_media.
- Les phrases parlees font 15 mots maximum, sans liste ni ponctuation
  decorative, puisqu'elles sont lues a voix haute.
- Si tu ne comprends pas, renvoie {"reponse": "..."} en demandant de repeter.
- Les arguments vont TOUJOURS dans "args", jamais a la racine de l objet.
- Pas de balises de code, pas de ``` : uniquement l objet JSON brut.
"""


def _extraire_json(texte):
    """Recupere le premier objet JSON d'une reponse, meme entoure de texte."""
    t = (texte or "").strip()
    # Retirer d'eventuelles balises de code
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except Exception:
        pass
    # Chercher le premier bloc {...} equilibre
    debut = t.find("{")
    if debut == -1:
        return None
    profondeur = 0
    for i in range(debut, len(t)):
        if t[i] == "{":
            profondeur += 1
        elif t[i] == "}":
            profondeur -= 1
            if profondeur == 0:
                try:
                    return json.loads(t[debut:i + 1])
                except Exception:
                    return None
    return None


class AgentSDKProvider:
    """Claude via l'abonnement, en appelant le binaire `claude -p`."""

    nom = "Claude (abonnement)"

    def __init__(self):
        from core.config import reglage
        self.binaire = shutil.which("claude") or "claude"
        self.modele = reglage("agentsdk.modele", "haiku")
        self.delai = int(reglage("agentsdk.delai", 90))
        self._verifie = None      # None = pas encore teste

    # -- environnement : surtout pas de cle API, sinon c'est facture ----------
    def _env(self):
        env = dict(os.environ)
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            env.pop(var, None)
        return env

    def _appeler(self, prompt, systeme=None):
        """Lance `claude -p`, prompt transmis par stdin (pas de limite de taille)."""
        cmd = [
            self.binaire, "-p",
            "--output-format", "json",
            "--model", self.modele,
            # Claude Code ne doit pas utiliser ses propres outils : on veut
            # seulement son raisonnement, Jarvis agit lui-meme.
            "--disallowedTools", "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task,NotebookEdit",
            "--permission-mode", "dontAsk",
            # Ne pas charger les serveurs MCP de l'utilisateur : divise par
            # deux le temps de demarrage du processus.
            "--strict-mcp-config",
        ]
        if systeme:
            # --system-prompt REMPLACE le prompt de codage de Claude Code.
            # Avec --append, Claude Code garde son identite et refuse le role.
            cmd += ["--system-prompt", systeme]

        r = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=self.delai,
            env=self._env(), shell=False,
        )
        try:
            donnees = json.loads(r.stdout or "{}")
        except Exception:
            raise RuntimeError(f"sortie illisible : {(r.stdout or '')[:200]}")

        resultat = donnees.get("result", "")
        if donnees.get("is_error"):
            raise RuntimeError(resultat or "erreur claude")
        return resultat

    def disponible(self):
        """Vrai si le binaire existe et que la session est connectee."""
        if self._verifie is not None:
            return self._verifie
        if not shutil.which(self.binaire) and not os.path.exists(self.binaire):
            self._verifie = False
            return False
        try:
            self._appeler("Reponds exactement : PRET")
            self._verifie = True
        except Exception as e:
            motif = str(e)
            if "Not logged in" in motif or "login" in motif.lower():
                LOG.info("claude non connecte : lancer `claude login`")
            else:
                LOG.info("claude indisponible : %s", motif[:120])
            self._verifie = False
        return self._verifie

    # -- mise en forme de la conversation ------------------------------------
    def _texte_historique(self, historique):
        lignes = []
        for m in historique:
            role, contenu = m.get("role"), m.get("content")
            if isinstance(contenu, str):
                qui = "Utilisateur" if role == "user" else "Assistant"
                lignes.append(f"{qui}: {contenu}")
                continue
            for item in contenu or []:
                if isinstance(item, dict):
                    if item.get("type") == "tool_result":
                        c = item.get("content")
                        if isinstance(c, list):
                            c = "[image]"
                        lignes.append(f"Resultat de l'outil: {c}")
                    elif item.get("type") == "text":
                        lignes.append(f"Utilisateur: {item.get('text', '')}")
                elif getattr(item, "type", None) == "text":
                    lignes.append(f"Assistant: {item.text}")
                elif getattr(item, "type", None) == "tool_use":
                    lignes.append(
                        f"Assistant a appele: {item.name} {json.dumps(item.input or {}, ensure_ascii=False)}")
        return "\n".join(lignes)

    def _resume_outils(self, outils):
        """Version compacte des schemas : nom, description, parametres."""
        lignes = []
        for o in outils:
            params = (o.get("input_schema") or {}).get("properties") or {}
            noms = ", ".join(params.keys()) or "aucun"
            desc = " ".join((o.get("description") or "").split())[:200]
            lignes.append(f"- {o['name']}({noms}) : {desc}")
        return "\n".join(lignes)

    def repondre(self, systeme, historique, outils):
        from core.llm import Bloc, Reponse

        prompt = (
            f"<outils>\n{self._resume_outils(outils)}\n</outils>\n\n"
            f"<contexte>\n{systeme}\n</contexte>\n\n"
            f"<conversation>\n{self._texte_historique(historique)}\n</conversation>\n\n"
            "Reponds maintenant par un seul objet JSON."
        )

        brut = self._appeler(prompt, systeme=CONSIGNE)
        donnees = _extraire_json(brut)

        if not isinstance(donnees, dict):
            # Pas de JSON : on prend le texte tel quel.
            return Reponse("end", [Bloc("text", text=(brut or "").strip()[:300])])

        nom_outil = (donnees.get("outil") or donnees.get("tool")
                     or donnees.get("name") or donnees.get("nom_outil"))
        if nom_outil:
            args = donnees.get("args")
            if not isinstance(args, dict):
                args = donnees.get("arguments")
            if not isinstance(args, dict):
                # Le modele met parfois les arguments a plat : on ramasse tout
                # ce qui n'est pas une clef de structure.
                ignore = {"outil", "tool", "name", "nom_outil", "args",
                          "arguments", "reponse", "response"}
                args = {k: v for k, v in donnees.items() if k not in ignore}
            return Reponse("tool_use", [
                Bloc("tool_use", id="call_0", name=str(nom_outil), input=args)
            ])

        return Reponse("end", [Bloc("text", text=str(donnees.get("reponse", "")).strip())])
