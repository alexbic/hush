"""HUSH Windows — конфигурация (аналог config.py без parakeet и macOS-путей)."""

import os
import sys
import tempfile
from pathlib import Path

# Запрещаем импорт на не-Windows платформах
if sys.platform != "win32":
    raise ImportError("config_win.py is Windows-only. Use config.py on macOS/Linux.")

import provider_config

# Загружаем конфиг провайдеров при импорте
provider_config.load()

# ── Пути ──────────────────────────────────────────────────────────────────────

# Модели faster-whisper: ~/.local/share/hush/models/faster-whisper-{size}
MODELS_DIR = Path.home() / ".local" / "share" / "hush" / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Размер модели Whisper по умолчанию
WHISPER_MODEL_SIZE = os.environ.get("HUSH_WHISPER_MODEL", "base")

# Временный WAV для записи
AUDIO_TMP = Path(tempfile.gettempdir()) / "hush_audio.wav"

# Конфигурационный каталог (общий с macOS — providers.json, scenarios.json, history.json)
CONFIG_DIR = Path.home() / ".config" / "hush"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# ── Параметры записи ──────────────────────────────────────────────────────────

SAMPLE_RATE = 16000

# Язык распознавания: ru / en / es (влияет на faster-whisper language=)
VOICE_LANG = os.environ.get("VOICE_LANG", "ru")

# ── LLM ───────────────────────────────────────────────────────────────────────

# Резервное имя модели Anthropic если в сценарии не указана
LLM_MODEL = os.environ.get("VOICE_LLM_MODEL", "claude-haiku-4-5-20251001")

# n8n webhook (опционально)
N8N_WEBHOOK_URL = os.environ.get("VOICE_N8N_WEBHOOK", "")

# ── Хоткей ────────────────────────────────────────────────────────────────────

# Right Alt (AltGr) — основной триггер диктовки
# Shift + Right Alt — открыть главное окно
HOTKEY = "<alt_r>"

# ── Живые геттеры провайдеров ────────────────────────────────────────────────
# Читают из provider_config в момент вызова — изменения в UI вступают в силу
# без перезапуска приложения.

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

# ── Константы для обратной совместимости с processor.py ──────────────────────
ANTHROPIC_API_KEY    = provider_config.get("anthropic", "api_key")
OPENAI_API_KEY       = provider_config.get("openai",    "api_key")
GLM_API_KEY          = provider_config.get("glm",       "api_key")
OLLAMA_BASE_URL      = provider_config.get("ollama",    "base_url",      "http://localhost:11434")
OLLAMA_DEFAULT_MODEL = provider_config.get("ollama",    "default_model", "qwen3:8b")

# ── Пути для логов ────────────────────────────────────────────────────────────

LOG_DIR = Path(tempfile.gettempdir())

def log_path(name: str) -> str:
    return str(LOG_DIR / name)
