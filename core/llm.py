"""Abstraction du modele de langage : le reste du code ignore quel provider tourne.

Deux implementations, choisies par config.yaml (mode: cloud | local) :
  - ClaudeProvider  : API Anthropic (cloud, defaut).
  - OllamaProvider  : Ollama en local (http://localhost:11434), 100% offline.

Les deux exposent la meme methode `repondre(systeme, historique, outils)` et
renvoient un objet a la forme d'une reponse Anthropic (.stop_reason + .content,
chaque bloc ayant .type / .text / .name / .input / .id). Ainsi la boucle de
dialogue de jarvis14 ne change pas selon le provider.

L'historique reste au format "content blocks" d'Anthropic ; OllamaProvider le
traduit vers/depuis le format d'Ollama de facon interne.
"""
import json
import logging

# Magasin de certificats Windows (Malwarebytes intercepte le TLS : sans ca, les
# appels a l'API Anthropic echouent en "certificate verify failed").
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

from core.config import reglage

LOG = logging.getLogger("jarvis")


class Bloc:
    """Imite un bloc de contenu Anthropic (text ou tool_use)."""

    def __init__(self, type, text=None, id=None, name=None, input=None):
        self.type = type
        self.text = text
        self.id = id
        self.name = name
        self.input = input


class Reponse:
    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content


# --------------------------------------------------------------- interface

class ProviderLLM:
    nom = "?"

    def disponible(self):
        return True

    def repondre(self, systeme, historique, outils):
        raise NotImplementedError


# --------------------------------------------------------------- Claude (cloud)

class ClaudeProvider(ProviderLLM):
    nom = "Claude"

    def __init__(self):
        import anthropic
        cle = reglage("anthropic.cle", "")
        self.modele = reglage("anthropic.modele", "claude-haiku-4-5")
        self.client = anthropic.Anthropic(api_key=cle) if cle else None
        self._secours = None    # OllamaProvider active en cas de panne Claude

    def disponible(self):
        """Vrai seulement si la cle a l'air reelle (les vraies font ~100 signes)."""
        cle = reglage("anthropic.cle", "") or ""
        if not cle.startswith("sk-ant-") or len(cle) < 40:
            return False
        return self.client is not None

    def repondre(self, systeme, historique, outils):
        # Une panne cote Claude (credits epuises, quota, reseau) ne doit pas
        # arreter Jarvis : on bascule sur Ollama pour le reste de la session.
        if self._secours is not None:
            return self._secours.repondre(systeme, historique, outils)
        try:
            # La reponse native Anthropic a deja la bonne forme.
            return self.client.messages.create(
                model=self.modele,
                max_tokens=1024,
                system=[{"type": "text", "text": systeme,
                         "cache_control": {"type": "ephemeral"}}],
                messages=historique,
                tools=outils,
            )
        except Exception as e:
            motif = str(e)
            if "credit balance is too low" in motif:
                raison = "credits Anthropic epuises"
            elif "rate_limit" in motif or "429" in motif:
                raison = "quota Anthropic atteint"
            elif "authentication" in motif or "401" in motif:
                raison = "cle API refusee"
            else:
                raison = motif[:80]
            LOG.warning("Claude indisponible (%s) : bascule sur Ollama", raison)
            print(f"\n  [!] Claude indisponible ({raison}). Bascule sur Ollama.\n")
            self._secours = OllamaProvider()
            self.nom = "Ollama (repli)"
            return self._secours.repondre(systeme, historique, outils)


# --------------------------------------------------------------- Ollama (local)

class OllamaProvider(ProviderLLM):
    nom = "Ollama"

    def __init__(self):
        self.hote = reglage("ollama.hote", "http://localhost:11434").rstrip("/")
        self.modele = reglage("ollama.modele", "qwen3.5:4b")

    def disponible(self):
        try:
            import requests
            requests.get(f"{self.hote}/api/version", timeout=3)
            return True
        except Exception:
            return False

    # -- traduction historique Anthropic -> messages Ollama --
    def _traduire(self, systeme, historique):
        messages = [{"role": "system", "content": systeme}]
        for m in historique:
            role, contenu = m.get("role"), m.get("content")
            if role == "user":
                if isinstance(contenu, str):
                    messages.append({"role": "user", "content": contenu})
                else:
                    for item in contenu or []:
                        if not isinstance(item, dict):
                            continue
                        if item.get("type") == "tool_result":
                            c = item.get("content")
                            if isinstance(c, list):   # bloc image
                                c = "[image capturee — la vision n'est pas disponible en mode local]"
                            messages.append({"role": "tool", "content": str(c)})
                        elif item.get("type") == "image":
                            messages.append({"role": "user",
                                             "content": "[image — vision indisponible en local]"})
            else:  # assistant
                if isinstance(contenu, str):
                    messages.append({"role": "assistant", "content": contenu})
                else:
                    texte = " ".join(b.text for b in (contenu or [])
                                     if getattr(b, "type", None) == "text" and b.text)
                    appels = [b for b in (contenu or []) if getattr(b, "type", None) == "tool_use"]
                    msg = {"role": "assistant", "content": texte}
                    if appels:
                        msg["tool_calls"] = [
                            {"function": {"name": b.name, "arguments": b.input or {}}}
                            for b in appels]
                    messages.append(msg)
        return messages

    def _outils(self, outils):
        return [{"type": "function", "function": {
            "name": o["name"], "description": o["description"],
            "parameters": o.get("input_schema", {"type": "object", "properties": {}})}}
            for o in outils]

    def _chat(self, messages, tools, nudge=None):
        import requests
        if nudge:
            messages = messages + [{"role": "user", "content": nudge}]
        # think=false : desactive le "raisonnement" natif (qwen3.5, etc.). Sinon le
        # modele est tres lent et rend parfois ses appels d'outils en texte au lieu
        # de les executer. Un modele sans thinking ignore ce parametre.
        r = requests.post(f"{self.hote}/api/chat", timeout=120, json={
            "model": self.modele, "messages": messages, "tools": tools,
            "stream": False, "think": bool(reglage("ollama.think", False)),
            "options": {"temperature": 0.3}})
        r.raise_for_status()
        return r.json()

    def _parser(self, rep):
        import re as _re
        msg = rep.get("message", {}) or {}
        blocs = []
        texte = (msg.get("content") or "").strip()

        # Appels outils reels (format Ollama)
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function", {}) or {}
            args = fn.get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args)
            blocs.append(Bloc("tool_use", id=f"call_{i}", name=fn.get("name"), input=args or {}))

        if texte:
            blocs.append(Bloc("text", text=texte))
        stop = "tool_use" if any(b.type == "tool_use" for b in blocs) else "end"
        return Reponse(stop, blocs)

    def repondre(self, systeme, historique, outils):
        messages = self._traduire(systeme, historique)
        tools = self._outils(outils)
        try:
            return self._parser(self._chat(messages, tools))
        except Exception as e:
            LOG.warning("ollama: 1er essai en echec (%s), retry plus directif", e)
            # Retry unique, avec une consigne plus stricte sur l'appel d'outil.
            nudge = ("Rappel : pour agir, appelle l'outil approprie via un tool call "
                     "avec des arguments JSON valides ; sinon reponds simplement en texte.")
            try:
                return self._parser(self._chat(messages, tools, nudge=nudge))
            except Exception:
                LOG.exception("ollama: echec apres retry")
                return Reponse("end", [Bloc("text", text=(
                    "Desole, le modele local n'a pas reussi a traiter la demande "
                    "correctement. Reessaie en reformulant, ou repasse en mode cloud."))])


# --------------------------------------------------------------- fabrique

_LLM = None


def _essayer(fabrique, etiquette):
    """Instancie un provider et verifie qu'il repond ; None si indisponible."""
    try:
        p = fabrique()
        if p.disponible():
            return p
        LOG.info("provider %s indisponible", etiquette)
    except Exception as e:
        LOG.info("provider %s inutilisable : %s", etiquette, e)
    return None


def llm():
    """Provider LLM courant, avec repli automatique.

    mode: cloud -> Claude, et si la cle manque ou le reseau est coupe, Ollama.
    mode: local -> Ollama, et si Ollama ne repond pas, Claude.
    """
    global _LLM
    if _LLM is not None:
        return _LLM

    def _agentsdk():
        from core.agentsdk import AgentSDKProvider
        return AgentSDKProvider()

    mode = (reglage("mode", "cloud") or "cloud").lower()
    if mode == "local":
        ordre = [(OllamaProvider, "Ollama"), (_agentsdk, "Claude abonnement"),
                 (ClaudeProvider, "Claude API")]
    elif mode in ("abonnement", "agentsdk", "subscription"):
        ordre = [(_agentsdk, "Claude abonnement"), (OllamaProvider, "Ollama"),
                 (ClaudeProvider, "Claude API")]
    else:
        ordre = [(ClaudeProvider, "Claude API"), (_agentsdk, "Claude abonnement"),
                 (OllamaProvider, "Ollama")]

    for fabrique, etiquette in ordre:
        p = _essayer(fabrique, etiquette)
        if p is not None:
            _LLM = p
            LOG.info("provider LLM : %s (mode %s)", p.nom, mode)
            print(f"  Cerveau : {p.nom}")
            return _LLM

    # Aucun provider : on renvoie quand meme Ollama, il dira l'erreur lui-meme.
    _LLM = OllamaProvider()
    LOG.warning("aucun provider LLM disponible, repli sur Ollama")
    return _LLM


def reinitialiser():
    """Force la reselection du provider (apres modification de config.yaml)."""
    global _LLM
    _LLM = None
