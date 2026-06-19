import os

# When running as .app bundle py2app sets RESOURCEPATH → Contents/Resources.
# When running from source _APP_DIR is the project directory.
_RSRC     = os.environ.get("RESOURCEPATH")
_APP_DIR  = _RSRC if _RSRC else os.path.dirname(os.path.abspath(__file__))

# parakeet-cli: look next to source files first, then ~/.local/bin
_local_parakeet = os.path.join(_APP_DIR, "parakeet-cli")
PARAKEET_CLI = _local_parakeet if os.path.exists(_local_parakeet) \
               else os.path.expanduser("~/.local/bin/parakeet-cli")

MODEL_DIR = os.path.join(_APP_DIR, "models", "parakeet-tdt-0.6b-v3-coreml")
LANG_ID = int(os.environ.get("VOICE_LANG_ID", "157"))  # 157=ru, 64=en

# LLM для обработки текста в сценариях.
# Формат поля model в сценарии: "провайдер:модель", например:
#   "ollama:qwen3:8b"                  — локально через Ollama
#   "anthropic:claude-haiku-4-5-20251001" — Anthropic API
#   "openai:gpt-4o-mini"               — OpenAI API
#   "glm:glm-4-flash"                  — GLM API
# null в сценарии → авто: сначала Ollama, при недоступности — Anthropic

OLLAMA_BASE_URL     = os.environ.get("OLLAMA_BASE_URL",     "http://localhost:11434")
OLLAMA_DEFAULT_MODEL = os.environ.get("OLLAMA_DEFAULT_MODEL", "qwen3:8b")
LLM_MODEL           = os.environ.get("VOICE_LLM_MODEL",     "claude-haiku-4-5-20251001")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY",    "")
GLM_API_KEY       = os.environ.get("GLM_API_KEY",       "")
N8N_WEBHOOK_URL   = os.environ.get("VOICE_N8N_WEBHOOK", "")

AUDIO_TMP = "/tmp/voice_input.wav"
SAMPLE_RATE = 16000
HOTKEY = "<alt_r>"  # Right Option key
