"""LLM post-processing for transcribed text.

Routing by model string format:  "provider:model_name"
  ollama:qwen3:8b              → local Ollama
  anthropic:claude-haiku-...   → Anthropic API
  openai:gpt-4o-mini           → OpenAI-compatible API
  glm:glm-4-flash              → GLM (Zhipu) API
  null / ""                    → auto: Ollama → Anthropic fallback
"""

import re
import json
import urllib.request
import urllib.error
import provider_config as _pc
from config import LLM_MODEL, N8N_WEBHOOK_URL


# ── Routing ───────────────────────────────────────────────────────────────────

def _parse(model_str):
    """'provider:model' → (provider, model).  None/'' → ('auto', '')."""
    if not model_str:
        return "auto", ""
    provider, _, rest = model_str.partition(":")
    return provider.lower(), rest


def process_with_prompt(text: str, prompt: str, model: str = None) -> str:
    """Run transcribed text through an LLM using the given scenario prompt."""
    if not prompt.strip():
        return text

    if prompt.startswith("n8n:"):
        return _n8n(text)

    provider, model_name = _parse(model)

    try:
        if provider == "ollama":
            return _ollama(prompt, text, model_name or _pc.get("ollama", "default_model", "qwen3:8b"))

        if provider == "anthropic":
            return _anthropic(prompt, text, model_name or LLM_MODEL)

        if provider in ("openai", "glm"):
            return _openai_compat(prompt, text, model_name, provider)

        # auto: Ollama first, Anthropic as fallback
        try:
            return _ollama(prompt, text, _pc.get("ollama", "default_model", "qwen3:8b"))
        except Exception:
            if _pc.get("anthropic", "api_key"):
                return _anthropic(prompt, text, LLM_MODEL)
            return text

    except Exception as e:
        print(f"[processor] {provider} error: {e}")
        return text


# ── Providers ─────────────────────────────────────────────────────────────────

def _ollama(system: str, text: str, model: str) -> str:
    base = _pc.get("ollama", "base_url", "http://localhost:11434").rstrip("/")
    payload = json.dumps({
        "model":    model,
        "think":    False,   # disable chain-of-thought (qwen3, deepseek-r1)
        "messages": [
            {"role": "system",  "content": system},
            {"role": "user",    "content": text},
        ],
        "stream": False,
        "options": {"temperature": 0.3},
    }).encode()
    req = urllib.request.Request(
        f"{base}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    result = data["message"]["content"].strip()
    result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL).strip()
    return result


_anthropic_client     = None
_anthropic_client_key = None   # track which key the client was built with

def _anthropic(system: str, text: str, model: str) -> str:
    global _anthropic_client, _anthropic_client_key
    key = _pc.get("anthropic", "api_key")
    if _anthropic_client is None or _anthropic_client_key != key:
        import anthropic
        _anthropic_client     = anthropic.Anthropic(api_key=key)
        _anthropic_client_key = key
    msg = _anthropic_client.messages.create(
        model=model,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": text}],
    )
    return msg.content[0].text.strip()


def _openai_compat(system: str, text: str, model: str, provider: str) -> str:
    if provider == "glm":
        base_url = "https://open.bigmodel.cn/api/paas/v4"
        api_key  = _pc.get("glm", "api_key")
    else:
        base_url = _pc.get("openai", "base_url", "https://api.openai.com/v1").rstrip("/")
        api_key  = _pc.get("openai", "api_key")

    payload = json.dumps({
        "model":    model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": text},
        ],
        "max_tokens":  2048,
        "temperature": 0.3,
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"].strip()


def _n8n(text: str) -> str:
    if not N8N_WEBHOOK_URL:
        return text
    payload = json.dumps({"text": text, "mode": "agent"}).encode()
    req = urllib.request.Request(
        N8N_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return data.get("text", data.get("result", text))
    except Exception as e:
        print(f"[processor] n8n error: {e}")
        return text
