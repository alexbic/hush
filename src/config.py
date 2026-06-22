import os
import provider_config

# Загружаем конфиг провайдеров при импорте (также мигрирует ~/.hush_env → providers.json)
provider_config.load()

# При запуске как .app bundle py2app устанавливает RESOURCEPATH → Contents/Resources.
_RSRC     = os.environ.get("RESOURCEPATH")
_APP_DIR  = _RSRC if _RSRC else os.path.dirname(os.path.abspath(__file__))

# parakeet-cli: предпочитаем ~/.local/bin (стабильный путь = CoreML кэш переживает пересборки)
_stable_parakeet = os.path.expanduser("~/.local/bin/parakeet-cli")
_bundle_parakeet = os.path.join(_APP_DIR, "parakeet-cli")
PARAKEET_CLI = _stable_parakeet if os.path.isfile(_stable_parakeet) \
               else _bundle_parakeet

# CoreML модель: предпочитаем ~/.local/share/hush (стабильный путь), иначе из bundle
_stable_models = os.path.expanduser("~/.local/share/hush/models/parakeet-tdt-0.6b-v3-coreml")
_bundle_models  = os.path.join(_APP_DIR, "models", "parakeet-tdt-0.6b-v3-coreml")
MODEL_DIR = _stable_models if os.path.isdir(_stable_models) else _bundle_models
# Языковые ID для Parakeet (token indices в parakeet_vocab.json)
LANG_IDS = {"ru": 157, "en": 64, "es": 171}
LANG_ID  = int(os.environ.get("VOICE_LANG_ID", "157"))  # резерв: env-override или ru

# Запасное имя модели Anthropic (используется когда в сценарии provider=anthropic, но имя модели не указано)
LLM_MODEL = os.environ.get("VOICE_LLM_MODEL", "claude-haiku-4-5-20251001")

# n8n webhook (опционально; заменяет LLM когда prompt сценария начинается с "n8n:")
N8N_WEBHOOK_URL = os.environ.get("VOICE_N8N_WEBHOOK", "")

AUDIO_TMP   = "/tmp/hush_audio.wav"
SAMPLE_RATE = 16000
HOTKEY      = "<alt_r>"  # Right Option key

# ── Живые геттеры — читают из provider_config в момент вызова ────────────────
# Используются в processor.py, чтобы изменения UI вступали в силу без перезапуска.
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

# ── Константы для обратной совместимости (читаются один раз при импорте) ─────
# Используются в старых code path; предпочтительны геттеры provider_config.
ANTHROPIC_API_KEY    = provider_config.get("anthropic", "api_key")
OPENAI_API_KEY       = provider_config.get("openai",    "api_key")
GLM_API_KEY          = provider_config.get("glm",       "api_key")
OLLAMA_BASE_URL      = provider_config.get("ollama",    "base_url",      "http://localhost:11434")
OLLAMA_DEFAULT_MODEL = provider_config.get("ollama",    "default_model", "qwen3:8b")
