import os
import provider_config

# Load providers config on import (also migrates ~/.hush_env → providers.json)
provider_config.load()

# When running as .app bundle py2app sets RESOURCEPATH → Contents/Resources.
_RSRC     = os.environ.get("RESOURCEPATH")
_APP_DIR  = _RSRC if _RSRC else os.path.dirname(os.path.abspath(__file__))

# parakeet-cli: prefer ~/.local/bin (stable path = CoreML cache survives rebuilds)
_stable_parakeet = os.path.expanduser("~/.local/bin/parakeet-cli")
_bundle_parakeet = os.path.join(_APP_DIR, "parakeet-cli")
PARAKEET_CLI = _stable_parakeet if os.path.isfile(_stable_parakeet) \
               else _bundle_parakeet

# CoreML model: prefer ~/.local/share/hush (stable), fall back to bundle
_stable_models = os.path.expanduser("~/.local/share/hush/models/parakeet-tdt-0.6b-v3-coreml")
_bundle_models  = os.path.join(_APP_DIR, "models", "parakeet-tdt-0.6b-v3-coreml")
MODEL_DIR = _stable_models if os.path.isdir(_stable_models) else _bundle_models
LANG_ID   = int(os.environ.get("VOICE_LANG_ID", "157"))  # 157=ru, 64=en

# Fallback Anthropic model name (used when scenario has provider=anthropic but no model name)
LLM_MODEL = os.environ.get("VOICE_LLM_MODEL", "claude-haiku-4-5-20251001")

# n8n webhook (optional; overrides LLM when scenario prompt starts with "n8n:")
N8N_WEBHOOK_URL = os.environ.get("VOICE_N8N_WEBHOOK", "")

AUDIO_TMP   = "/tmp/hush_audio.wav"
SAMPLE_RATE = 16000
HOTKEY      = "<alt_r>"  # Right Option key

# ── Live getters — read from provider_config at call time ────────────────────
# processor.py uses these so UI changes take effect without restarting.
def get_anthropic_key() -> str:
    return provider_config.get("anthropic", "api_key")

def get_openai_key() -> str:
    return provider_config.get("openai", "api_key")

def get_glm_key() -> str:
    return provider_config.get("glm", "api_key")

def get_ollama_url() -> str:
    return provider_config.get("ollama", "base_url", "http://localhost:11434")

def get_ollama_model() -> str:
    return provider_config.get("ollama", "default_model", "qwen3:8b")

# ── Backward-compat constants (read once at import) ──────────────────────────
# Still used by older code paths; provider_config getters are preferred.
ANTHROPIC_API_KEY    = provider_config.get("anthropic", "api_key")
OPENAI_API_KEY       = provider_config.get("openai",    "api_key")
GLM_API_KEY          = provider_config.get("glm",       "api_key")
OLLAMA_BASE_URL      = provider_config.get("ollama",    "base_url",      "http://localhost:11434")
OLLAMA_DEFAULT_MODEL = provider_config.get("ollama",    "default_model", "qwen3:8b")
