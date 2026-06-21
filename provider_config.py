"""Provider configuration: ~/.config/hush/providers.json
Handles load/save, migration from ~/.hush_env, async availability probing.
"""

import json
import os
import threading
import urllib.request
import urllib.error

PROVIDERS_FILE = os.path.expanduser("~/.config/hush/providers.json")
_HUSH_ENV      = os.path.expanduser("~/.hush_env")

_DEFAULTS = {
    "ollama":    {"base_url": "http://localhost:11434", "default_model": "qwen3:8b"},
    "anthropic": {"api_key": ""},
    "openai":    {"api_key": "", "base_url": "https://api.openai.com/v1"},
    "glm":       {"api_key": ""},
}

_data          = {}
_status        = {"ollama": None, "anthropic": None, "openai": None, "glm": None}
_ollama_models = []   # list of model name strings from /api/tags
_status_cbs    = []   # callbacks fired when status changes

PROVIDER_LABELS = {
    "ollama":    "Ollama",
    "anthropic": "Anthropic",
    "openai":    "OpenAI",
    "glm":       "GLM",
}

# Well-known cloud models for the scenario model picker
CLOUD_MODELS = [
    "anthropic:claude-haiku-4-5-20251001",
    "anthropic:claude-sonnet-4-6",
    "anthropic:claude-opus-4-8",
    "openai:gpt-4o-mini",
    "openai:gpt-4o",
    "glm:glm-5-turbo",
    "glm:glm-5",
    "glm:glm-4.7",
    "glm:glm-4.7-flash",
    "glm:glm-4.5",
    "glm:glm-4.5-air",
]


def load():
    """Load providers.json; fill defaults; migrate ~/.hush_env if needed."""
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
    _migrate_env()


def save():
    try:
        os.makedirs(os.path.dirname(PROVIDERS_FILE), exist_ok=True)
        with open(PROVIDERS_FILE, "w") as f:
            json.dump(_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[providers] save error: {e}")


def _migrate_env():
    """Import keys from ~/.hush_env if providers.json lacks them."""
    if not os.path.exists(_HUSH_ENV):
        return
    env = {}
    try:
        with open(_HUSH_ENV) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        return
    mapping = [
        ("ANTHROPIC_API_KEY",    "anthropic", "api_key"),
        ("OPENAI_API_KEY",       "openai",    "api_key"),
        ("GLM_API_KEY",          "glm",       "api_key"),
        ("OLLAMA_BASE_URL",      "ollama",    "base_url"),
        ("OLLAMA_DEFAULT_MODEL", "ollama",    "default_model"),
    ]
    changed = False
    for env_key, provider, field in mapping:
        if env.get(env_key) and not _data[provider].get(field):
            _data[provider][field] = env[env_key]
            changed = True
    if changed:
        save()


def get(provider: str, key: str, default: str = "") -> str:
    return _data.get(provider, {}).get(key, default)


def set_field(provider: str, key: str, value: str):
    _data.setdefault(provider, {})[key] = value
    save()


def get_status(provider: str):
    """None = not yet probed; True = available; False = unavailable."""
    return _status.get(provider)


def get_ollama_models() -> list:
    return list(_ollama_models)


def all_model_options() -> list:
    """Models for scenario picker: Ollama (if running) + cloud (only if key set)."""
    result = [f"ollama:{m}" for m in _ollama_models]
    for entry in CLOUD_MODELS:
        provider = entry.split(":")[0]
        if _data.get(provider, {}).get("api_key", ""):
            result.append(entry)
    return result


def add_status_callback(fn):
    _status_cbs.append(fn)


def _notify():
    for fn in _status_cbs:
        try:
            fn()
        except Exception:
            pass


def probe_all():
    """Start async availability check for all providers."""
    threading.Thread(target=_probe_ollama, daemon=True, name="hush-probe-ollama").start()
    threading.Thread(target=_probe_cloud,  daemon=True, name="hush-probe-cloud").start()


def probe_ollama():
    """Re-probe Ollama only (after URL change)."""
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
    """Show first 8 + last 4 chars of an API key."""
    if not key:
        return ""
    if len(key) <= 14:
        return key
    return key[:8] + "·" * 6 + key[-4:]
