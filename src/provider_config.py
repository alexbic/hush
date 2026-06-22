"""Конфигурация провайдеров LLM: ~/.config/hush/providers.json"""

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

_data         = {}
_status       = {"ollama": None, "anthropic": None, "openai": None, "glm": None}
_status_cbs   = []

# Динамически загруженные модели по каждому провайдеру
_models: dict[str, list[str]] = {
    "ollama":    [],
    "anthropic": [],
    "openai":    [],
    "glm":       [],
}

# Резервный список моделей — используется если API недоступен
_FALLBACK_MODELS: dict[str, list[str]] = {
    "anthropic": [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-6",
        "claude-opus-4-8",
    ],
    "openai": [
        "gpt-4o-mini",
        "gpt-4o",
        "o1-mini",
        "o3-mini",
    ],
    "glm": [
        "glm-4.7-flash",
        "glm-4.7",
        "glm-4.5-air",
        "glm-4.5",
    ],
}

PROVIDER_LABELS = {
    "ollama":    "Ollama",
    "anthropic": "Anthropic",
    "openai":    "OpenAI",
    "glm":       "GLM (Z.ai)",
}


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
        print(f"[providers] ошибка сохранения: {e}")


def get(provider: str, key: str, default: str = "") -> str:
    return _data.get(provider, {}).get(key, default)


def set_field(provider: str, key: str, value: str):
    _data.setdefault(provider, {})[key] = value
    save()


def get_status(provider: str):
    return _status.get(provider)


def get_ollama_models() -> list:
    return list(_models["ollama"])


def available_providers() -> list:
    """Провайдеры, которые сейчас доступны (ключ задан или Ollama запущена)."""
    result = []
    if _status.get("ollama") is True:
        result.append("ollama")
    for pid in ("anthropic", "openai", "glm"):
        if _data.get(pid, {}).get("api_key", ""):
            result.append(pid)
    return result


def models_for_provider(pid: str) -> list:
    """Список моделей (без префикса провайдера) для указанного провайдера.

    Возвращает динамически загруженные модели если они есть,
    иначе резервный список.
    """
    fetched = _models.get(pid, [])
    if fetched:
        return list(fetched)
    return list(_FALLBACK_MODELS.get(pid, []))


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
    threading.Thread(target=_probe_anthropic, daemon=True, name="hush-probe-anthropic").start()
    threading.Thread(target=_probe_openai,    daemon=True, name="hush-probe-openai").start()
    threading.Thread(target=_probe_glm,       daemon=True, name="hush-probe-glm").start()


def probe_ollama():
    threading.Thread(target=_probe_ollama, daemon=True, name="hush-probe-ollama").start()


def _probe_ollama():
    """Опрашивает Ollama /api/tags и загружает список установленных моделей."""
    base = _data.get("ollama", {}).get("base_url", "http://localhost:11434").rstrip("/")
    try:
        req = urllib.request.Request(
            f"{base}/api/tags",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
        _models["ollama"] = [m["name"] for m in data.get("models", [])]
        _status["ollama"] = True
    except Exception:
        _models["ollama"] = []
        _status["ollama"] = False
    _notify()


def _probe_anthropic():
    """Проверяет API-ключ Anthropic и загружает список доступных моделей."""
    key = _data.get("anthropic", {}).get("api_key", "")
    if not key or len(key) <= 10:
        _status["anthropic"] = False
        _notify()
        return
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        # Оставляем только claude-модели, сортируем по имени (новые первыми)
        ids = [m["id"] for m in data.get("data", []) if "claude" in m.get("id", "")]
        _models["anthropic"] = sorted(ids, reverse=True) if ids else list(_FALLBACK_MODELS["anthropic"])
        _status["anthropic"] = True
    except urllib.error.HTTPError as e:
        _status["anthropic"] = e.code != 401
        _models["anthropic"] = list(_FALLBACK_MODELS["anthropic"])
    except Exception:
        _status["anthropic"] = False
        _models["anthropic"] = list(_FALLBACK_MODELS["anthropic"])
    _notify()


def _probe_openai():
    """Проверяет API-ключ OpenAI и загружает список GPT/o-моделей."""
    key = _data.get("openai", {}).get("api_key", "")
    base = _data.get("openai", {}).get("base_url", "https://api.openai.com/v1").rstrip("/")
    if not key or len(key) <= 10:
        _status["openai"] = False
        _notify()
        return
    try:
        req = urllib.request.Request(
            f"{base}/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        # Оставляем только GPT и o-серию, исключаем embedding/audio/image
        _KEEP = ("gpt-4", "gpt-3.5", "o1", "o3", "o4")
        _SKIP = ("instruct", "vision", "embedding", "audio", "tts", "dall", "whisper", "realtime")
        ids = [
            m["id"] for m in data.get("data", [])
            if any(m["id"].startswith(p) for p in _KEEP)
            and not any(s in m["id"] for s in _SKIP)
        ]
        _models["openai"] = sorted(ids, reverse=True) if ids else list(_FALLBACK_MODELS["openai"])
        _status["openai"] = True
    except urllib.error.HTTPError as e:
        _status["openai"] = e.code != 401
        _models["openai"] = list(_FALLBACK_MODELS["openai"])
    except Exception:
        _status["openai"] = False
        _models["openai"] = list(_FALLBACK_MODELS["openai"])
    _notify()


def _probe_glm():
    """Проверяет API-ключ GLM (Z.ai) и загружает список доступных моделей."""
    key = _data.get("glm", {}).get("api_key", "")
    base = _data.get("glm", {}).get("base_url", "https://api.z.ai/api/paas/v4").rstrip("/")
    if not key or len(key) <= 10:
        _status["glm"] = False
        _notify()
        return
    try:
        req = urllib.request.Request(
            f"{base}/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        ids = [m["id"] for m in data.get("data", []) if "glm" in m.get("id", "").lower()]
        _models["glm"] = sorted(ids, reverse=True) if ids else list(_FALLBACK_MODELS["glm"])
        _status["glm"] = True
    except urllib.error.HTTPError as e:
        _status["glm"] = e.code != 401
        _models["glm"] = list(_FALLBACK_MODELS["glm"])
    except Exception:
        _status["glm"] = False
        _models["glm"] = list(_FALLBACK_MODELS["glm"])
    _notify()


def mask_key(key: str) -> str:
    """Маскирует API-ключ для отображения: первые 8 + точки + последние 4 символа."""
    if not key:
        return ""
    if len(key) <= 14:
        return key
    return key[:8] + "·" * 6 + key[-4:]
