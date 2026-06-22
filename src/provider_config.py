"""Provider configuration: ~/.config/hush/providers.json"""

import json
import os
import threading
import urllib.request
import urllib.error

PROVIDERS_FILE = os.path.expanduser("~/.config/hush/providers.json")

_DEFAULTS = {
    "ollama":    {"base_url": "http://localhost:11434", "default_model": "qwen3:8b"},
    "anthropic": {"api_key": ""},
    "openai":    {"api_key": "", "base_url": "https://api.openai.com/v1"},
    "glm":       {"api_key": "", "base_url": "https://api.z.ai/api/paas/v4"},
}

_data          = {}
_status        = {"ollama": None, "anthropic": None, "openai": None, "glm": None}
_ollama_models = []
_status_cbs    = []

PROVIDER_LABELS = {
    "ollama":    "Ollama",
    "anthropic": "Anthropic",
    "openai":    "OpenAI",
    "glm":       "GLM (Z.ai)",
}

CLOUD_MODELS = [
    "anthropic:claude-haiku-4-5-20251001",
    "anthropic:claude-sonnet-4-6",
    "anthropic:claude-opus-4-8",
    "openai:gpt-4o-mini",
    "openai:gpt-4o",
    "glm:glm-4.7",
    "glm:glm-4.7-flash",
    "glm:glm-4.5",
    "glm:glm-4.5-air",
]


def load():
    global _data
    if os.path.exists(PROVIDERS_FILE):
        try:
            with open(PROVIDERS_FILE) as f:
                _data = json.load(f)
        except Exception:
            _data = {}
    else:
        _data = {}
    for provider, defs in _DEFAULTS.items():
        _data.setdefault(provider, {})
        for k, v in defs.items():
            _data[provider].setdefault(k, v)


def save():
    try:
        os.makedirs(os.path.dirname(PROVIDERS_FILE), exist_ok=True)
        with open(PROVIDERS_FILE, "w") as f:
            json.dump(_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[providers] save error: {e}")


def get(provider: str, key: str, default: str = "") -> str:
    return _data.get(provider, {}).get(key, default)


def set_field(provider: str, key: str, value: str):
    _data.setdefault(provider, {})[key] = value
    save()


def get_status(provider: str):
    return _status.get(provider)


def get_ollama_models() -> list:
    return list(_ollama_models)


def available_providers() -> list:
    """Provider ids that are currently usable (key set or Ollama running)."""
    result = []
    if _status.get("ollama") is True:
        result.append("ollama")
    for pid in ("anthropic", "openai", "glm"):
        if _data.get(pid, {}).get("api_key", ""):
            result.append(pid)
    return result


def models_for_provider(pid: str) -> list:
    """Model names (no provider: prefix) for a given provider."""
    if pid == "ollama":
        return list(_ollama_models)
    return [
        entry.split(":", 1)[1]
        for entry in CLOUD_MODELS
        if entry.startswith(f"{pid}:")
    ]


def add_status_callback(fn):
    _status_cbs.append(fn)


def _notify():
    for fn in _status_cbs:
        try:
            fn()
        except Exception:
            pass


def probe_all():
    threading.Thread(target=_probe_ollama, daemon=True, name="hush-probe-ollama").start()
    threading.Thread(target=_probe_cloud,  daemon=True, name="hush-probe-cloud").start()


def probe_ollama():
    threading.Thread(target=_probe_ollama, daemon=True, name="hush-probe-ollama").start()


def _probe_ollama():
    global _ollama_models
    base = _data.get("ollama", {}).get("base_url", "http://localhost:11434").rstrip("/")
    try:
        req = urllib.request.Request(
            f"{base}/api/tags",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
        _ollama_models = [m["name"] for m in data.get("models", [])]
        _status["ollama"] = True
    except Exception:
        _ollama_models = []
        _status["ollama"] = False
    _notify()


def _probe_cloud():
    for provider in ("anthropic", "openai", "glm"):
        key = _data.get(provider, {}).get("api_key", "")
        _status[provider] = bool(key and len(key) > 10)
    _notify()


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 14:
        return key
    return key[:8] + "·" * 6 + key[-4:]
