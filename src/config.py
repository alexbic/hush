import os
import provider_config

# Загружаем конфиг провайдеров при импорте. В v2.2 providers.json имеет
# schema_version: 2; старый формат v1 мигрируется автоматически (см. provider_config.load).
provider_config.load()
# Перенос ~/.hush_env (если есть) — оставлен для обратной совместимости.
provider_config.migrate_hush_env_if_any()

# При запуске как .app bundle py2app устанавливает RESOURCEPATH → Contents/Resources.
_RSRC     = os.environ.get("RESOURCEPATH")
_APP_DIR  = _RSRC if _RSRC else os.path.dirname(os.path.abspath(__file__))

# parakeet-cli: предпочитаем ~/.local/bin (стабильный путь = CoreML кэш переживает пересборки)
_stable_parakeet = os.path.expanduser("~/.local/bin/parakeet-cli")
_bundle_parakeet = os.path.join(_APP_DIR, "parakeet-cli")
PARAKEET_CLI = _stable_parakeet if os.path.isfile(_stable_parakeet) \
               else _bundle_parakeet

# CoreML модель: предпочитаем ~/.local/share/hush (стабильный путь), иначе из bundle.
# ВАЖНО: вычисляем в момент вызова (через геттер), потому что при первом запуске
# модель скачивается ПОСЛЕ импорта config — константа, вычисленная один раз,
# закэшировала бы несуществующий bundle path.
_stable_models = os.path.expanduser("~/.local/share/hush/models/parakeet-tdt-0.6b-v3-coreml")
_bundle_models = os.path.join(_APP_DIR, "models", "parakeet-tdt-0.6b-v3-coreml")

def get_model_dir() -> str:
    """Вернуть путь к CoreML модели — вычисляется заново при каждом вызове,
    чтобы подхватить путь после завершения first-run скачивания."""
    return _stable_models if os.path.isdir(_stable_models) else _bundle_models

# Обратная совместимость: MODEL_DIR как свойство-в-момент-импорта (старый код
# может импортировать это имя напрямую). В v3.0 будет удалён.
MODEL_DIR = get_model_dir()
# Языковые ID для Parakeet (token indices в parakeet_vocab.json)
LANG_IDS = {"ru": 157, "en": 64, "es": 171}
LANG_ID  = int(os.environ.get("VOICE_LANG_ID", "157"))  # резерв: env-override или ru

# Запасное имя модели Anthropic (используется когда в сценарии provider=anthropic, но имя модели не указано)
LLM_MODEL = os.environ.get("VOICE_LLM_MODEL", "claude-haiku-4-5-20251001")

# n8n webhook (опционально; заменяет LLM когда prompt сценария начинается с "n8n:")
N8N_WEBHOOK_URL = os.environ.get("VOICE_N8N_WEBHOOK", "")

AUDIO_TMP   = "/tmp/hush_audio.wav"
SAMPLE_RATE = 16000
HOTKEY      = "<fn>"  # Fn key

# ── Живые геттеры — читают из provider_config в момент вызова ────────────────
# Используются в processor.py, чтобы изменения UI вступали в силу без перезапуска.
# В v2.2 провайдер может быть удалён пользователем — возвращаем "" в таком случае.
def _field_or_empty(pid: str, key: str) -> str:
    rec = provider_config.get_provider(pid)
    if rec is None:
        return ""
    return rec.get(key, "") or ""


def get_anthropic_key() -> str:
    return _field_or_empty("anthropic", "api_key")


def get_openai_key() -> str:
    return _field_or_empty("openai", "api_key")


def get_glm_key() -> str:
    return _field_or_empty("glm", "api_key")


def get_ollama_url() -> str:
    return _field_or_empty("ollama", "base_url") or "http://localhost:11434"


def get_ollama_model() -> str:
    return _field_or_empty("ollama", "default_model") or "qwen3:8b"


# ── Константы для обратной совместимости (читаются один раз при импорте) ─────
# DEPRECATED в v2.2 — предпочтительны геттеры выше или прямой доступ к
# provider_config.get_provider(). В v3.0 будут удалены.
ANTHROPIC_API_KEY    = get_anthropic_key()
OPENAI_API_KEY       = get_openai_key()
GLM_API_KEY          = get_glm_key()
OLLAMA_BASE_URL      = get_ollama_url()
OLLAMA_DEFAULT_MODEL = get_ollama_model()
