"""Конфигурация LLM-провайдеров: ~/.config/hush/providers.json (schema v2).

Формат v2 — список провайдеров, каждый со своим endpoint/ключом/протоколом:
    {
      "schema_version": 2,
      "providers": [
        {"id":"ollama","label":"Ollama","protocol":"ollama",
         "base_url":"http://localhost:11434","api_key":"","default_model":"qwen3:8b","builtin":true},
        ...
      ]
    }

Протоколы: "ollama" | "anthropic" | "openai-compat".
ID провайдера — slug из label (стабилен при переименовании); попадает в строку модели
сценария как "provider_id:model_name".

При первом запуске с новым кодом старый формат v1 (dict keyed by provider id)
автоматически мигрируется в v2; оригинал копируется в providers.legacy.json.
"""

import json
import os
import re
import threading
import urllib.request
import urllib.error

PROVIDERS_FILE       = os.path.expanduser("~/.config/hush/providers.json")
PROVIDERS_LEGACY     = os.path.expanduser("~/.config/hush/providers.legacy.json")
SCHEMA_VERSION       = 2

SUPPORTED_PROTOCOLS  = ("ollama", "anthropic", "openai-compat")

# ─── Базовые (builtin) провайдеры — сеятели при первом запуске ────────────────
_BUILTIN_SEED = [
    {"id": "ollama",    "label": "Ollama",     "protocol": "ollama",
     "base_url": "http://localhost:11434", "api_key": "", "default_model": "qwen3:8b",
     "builtin": True},
    {"id": "anthropic", "label": "Anthropic",  "protocol": "anthropic",
     "base_url": "https://api.anthropic.com/v1", "api_key": "", "default_model": "",
     "builtin": True},
    {"id": "openai",    "label": "OpenAI",     "protocol": "openai-compat",
     "base_url": "https://api.openai.com/v1", "api_key": "", "default_model": "",
     "builtin": True},
    {"id": "glm",       "label": "GLM (Z.ai)", "protocol": "openai-compat",
     "base_url": "https://api.z.ai/api/paas/v4", "api_key": "", "default_model": "",
     "builtin": True},
]

# ─── Резервные списки моделей (когда /models недоступен или пуст) ──────────────
_FALLBACK_MODELS = {
    "anthropic": [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-6",
        "claude-opus-4-8",
    ],
    "openai-compat": [
        "gpt-4o-mini",
        "gpt-4o",
        "o1-mini",
        "o3-mini",
    ],
}

# Спец-фильтры моделей по id (исторически для glm отбрасываем всё без "glm")
_ID_MODEL_FILTERS = {
    "glm": lambda mid: "glm" in mid.lower(),
}

# Фильтрация openai-compat списка моделей по умолчанию (для id == "openai")
_OPENAI_KEEP = ("gpt-4", "gpt-3.5", "o1", "o3", "o4")
_OPENAI_SKIP = ("instruct", "vision", "embedding", "audio", "tts",
                "dall", "whisper", "realtime")


def _openai_default_filter(model_ids):
    keep = [m for m in model_ids
            if any(m.startswith(p) for p in _OPENAI_KEEP)
            and not any(s in m for s in _OPENAI_SKIP)]
    return sorted(keep, reverse=True) if keep else []


# ─── Внутреннее состояние ─────────────────────────────────────────────────────
_data: dict = {"schema_version": SCHEMA_VERSION, "providers": []}
_status: dict[str, bool | None] = {}        # {provider_id: True|False|None}
_models: dict[str, list[str]] = {}          # {provider_id: [model_name,...]}
_status_cbs = []


# ═══════════════════════════════════════════════════════════════════════════
# Slug-генерация
# ═══════════════════════════════════════════════════════════════════════════
def _slugify(label: str, existing_ids: set) -> str:
    """Преобразовать label в уникальный slug: 'My Mistral' → 'my-mistral'.

    Если slug уже занят — добавляет суффикс -2, -3, ..."""
    base = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")
    if not base:
        base = "provider"
    if base not in existing_ids:
        return base
    i = 2
    while f"{base}-{i}" in existing_ids:
        i += 1
    return f"{base}-{i}"


def _builtin_record(pid: str):
    """Вернуть копию builtin-записи по id или None."""
    for rec in _BUILTIN_SEED:
        if rec["id"] == pid:
            return dict(rec)
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Загрузка / сохранение / миграция
# ═══════════════════════════════════════════════════════════════════════════
def load():
    """Загрузить providers.json. Если файл отсутствует или старого формата —
    инициализировать (4 builtin) или мигрировать v1 → v2 соответственно."""
    global _data, _status, _models
    if not os.path.exists(PROVIDERS_FILE):
        _data = {"schema_version": SCHEMA_VERSION,
                 "providers": [dict(p) for p in _BUILTIN_SEED]}
        save()
    else:
        try:
            with open(PROVIDERS_FILE) as f:
                raw = json.load(f)
        except Exception:
            raw = None

        if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
            # Старый формат v1 (или повреждён) — мигрировать
            migrated = _migrate_v1_to_v2(raw if isinstance(raw, dict) else {})
            _data = migrated
            save()
        else:
            _data = raw
            _normalize_records()
            _seed_missing_builtins()

    # Инициализировать статусы/модели для всех известных провайдеров
    _status = {p["id"]: None for p in _data.get("providers", [])}
    _models = {p["id"]: []        for p in _data.get("providers", [])}


def _normalize_records():
    """Гарантировать, что каждая запись имеет все обязательные поля."""
    providers = _data.setdefault("providers", [])
    cleaned = []
    seen_ids = set()
    for p in providers:
        if not isinstance(p, dict):
            continue
        pid = p.get("id") or ""
        if not pid or pid in seen_ids:
            continue
        seen_ids.add(pid)
        rec = {
            "id":            pid,
            "label":         p.get("label") or pid,
            "protocol":      p.get("protocol") if p.get("protocol") in SUPPORTED_PROTOCOLS
                              else "openai-compat",
            "base_url":      p.get("base_url", "") or "",
            "api_key":       p.get("api_key", "") or "",
            "default_model": p.get("default_model", "") or "",
            "builtin":       bool(p.get("builtin", False)),
        }
        cleaned.append(rec)
    _data["providers"] = cleaned
    _data["schema_version"] = SCHEMA_VERSION


def _seed_missing_builtins():
    """Гарантировать, что builtin-провайдеры присутствуют в schema v2.

    Старый UI `[КЛЮЧИ]` ожидает записи ollama/anthropic/openai/glm. Если
    пользователь вручную удалил их из providers.json, quietly восстановим
    дефолтные записи вместо поломки старого экрана настроек.
    """
    providers = _data.setdefault("providers", [])
    existing = {p.get("id") for p in providers if isinstance(p, dict)}
    changed = False
    for rec in _BUILTIN_SEED:
        if rec["id"] not in existing:
            providers.append(dict(rec))
            changed = True
    if changed:
        _normalize_records()


def _migrate_v1_to_v2(old_data: dict) -> dict:
    """Конвертировать старый формат v1 (dict keyed by id) в v2 (список записей).

    Создаёт backup оригинала в providers.legacy.json (если его ещё нет).
    Сохраняет все api_key/base_url/default_model из старого файла.
    """
    try:
        if not os.path.exists(PROVIDERS_LEGACY) and os.path.exists(PROVIDERS_FILE):
            with open(PROVIDERS_FILE) as f:
                legacy_text = f.read()
            with open(PROVIDERS_LEGACY, "w") as f:
                f.write(legacy_text)
    except Exception as e:
        print(f"[providers] migrate: backup failed: {e}")

    # Стартуем с builtin-семян и переопределяем значениями из старого файла
    by_id = {p["id"]: dict(p) for p in _BUILTIN_SEED}
    if isinstance(old_data, dict):
        for pid, fields in old_data.items():
            if not isinstance(fields, dict):
                continue
            rec = by_id.get(pid)
            if rec is None:
                # Неизвестный id из v1 — создай запись openai-compat по умолчанию
                rec = {"id": pid, "label": pid, "protocol": "openai-compat",
                       "base_url": "", "api_key": "", "default_model": "",
                       "builtin": False}
                by_id[pid] = rec
            # Перенести значения полей
            for k in ("base_url", "api_key", "default_model"):
                if k in fields and fields[k]:
                    rec[k] = fields[k]
            if "label" in fields and fields["label"]:
                rec["label"] = fields["label"]

    providers = list(by_id.values())
    return {"schema_version": SCHEMA_VERSION, "providers": providers}


def save():
    try:
        os.makedirs(os.path.dirname(PROVIDERS_FILE), exist_ok=True)
        with open(PROVIDERS_FILE, "w") as f:
            json.dump(_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[providers] ошибка сохранения: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Публичный API: доступ / мутация
# ═══════════════════════════════════════════════════════════════════════════
def list_providers() -> list:
    """Вернуть список всех записей провайдеров (свежие копии dict-ов)."""
    return [dict(p) for p in _data.get("providers", [])]


def get_provider(pid: str):
    """Найти запись провайдера по id. Возвращает копию dict или None."""
    for p in _data.get("providers", []):
        if p.get("id") == pid:
            return dict(p)
    return None


def _get_provider_ref(pid: str):
    """Вернуть ссылку на внутренний dict провайдера (для мутации)."""
    for p in _data.get("providers", []):
        if p.get("id") == pid:
            return p
    return None


def find_by_label_or_id(name: str):
    """Найти провайдера по id (точно) или по label (case-insensitive).
    Используется при разборе 'provider:model' в сценариях — поддерживает
    обратную совместимость со старыми сценариями, где model = '<id>:<model>'."""
    if not name:
        return None
    name_l = name.lower()
    for p in _data.get("providers", []):
        if p.get("id") == name:
            return dict(p)
    for p in _data.get("providers", []):
        if (p.get("label") or "").lower() == name_l:
            return dict(p)
    return None


def add_provider(label: str, protocol: str, base_url: str = "",
                 api_key: str = "", default_model: str = "") -> str:
    """Добавить провайдера. Генерирует уникальный slug-id из label.
    Возвращает id добавленной записи."""
    if protocol not in SUPPORTED_PROTOCOLS:
        protocol = "openai-compat"
    existing_ids = {p["id"] for p in _data.get("providers", [])}
    pid = _slugify(label, existing_ids)
    rec = {
        "id":            pid,
        "label":         label or pid,
        "protocol":      protocol,
        "base_url":      base_url,
        "api_key":       api_key,
        "default_model": default_model,
        "builtin":       False,
    }
    _data.setdefault("providers", []).append(rec)
    _status[pid] = None
    _models[pid] = []
    save()
    return pid


def update_provider(pid: str, **fields) -> bool:
    """Обновить поля провайдера. Поддерживаемые ключи:
    label, protocol, base_url, api_key, default_model.
    Возвращает True если запись найдена и обновлена."""
    rec = _get_provider_ref(pid)
    if rec is None:
        return False
    if "label" in fields and fields["label"]:
        rec["label"] = fields["label"]
    if "protocol" in fields:
        if fields["protocol"] in SUPPORTED_PROTOCOLS:
            rec["protocol"] = fields["protocol"]
    for k in ("base_url", "api_key", "default_model"):
        if k in fields:
            rec[k] = fields[k] or ""
    save()
    return True


def remove_provider(pid: str) -> bool:
    """Удалить провайдера по id. Возвращает True если был удалён."""
    providers = _data.get("providers", [])
    for i, p in enumerate(providers):
        if p.get("id") == pid:
            providers.pop(i)
            _status.pop(pid, None)
            _models.pop(pid, None)
            save()
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# Совместимость со старым API (использовался в overlay/main/config)
# ═══════════════════════════════════════════════════════════════════════════
def get(provider: str, key: str, default: str = "") -> str:
    """Совместимый getter: get('anthropic','api_key') → значение поля или default.
    Искать провайдера по id; если нет — вернуть default."""
    rec = get_provider(provider)
    if rec is None:
        return default
    val = rec.get(key, "")
    return val if val not in (None, "") else default


def set_field(provider: str, key: str, value: str):
    """Совместимый setter. Если провайдер не существует — создаёт его с
    protocol=openai-compat по умолчанию (для надёжности)."""
    rec = _get_provider_ref(provider)
    if rec is None:
        if not provider:
            return
        builtin = _builtin_record(provider)
        existing_ids = {p["id"] for p in _data.get("providers", [])}
        pid = provider if builtin else _slugify(provider, existing_ids)
        rec = builtin or {
            "id": pid,
            "label": provider,
            "protocol": "openai-compat",
            "base_url": "",
            "api_key": "",
            "default_model": "",
            "builtin": False,
        }
        _data.setdefault("providers", []).append(rec)
        _status[pid] = None
        _models[pid] = []
    if key in ("label", "protocol", "base_url", "api_key", "default_model", "builtin"):
        rec[key] = value
    save()


# ═══════════════════════════════════════════════════════════════════════════
# Доступность / модели
# ═══════════════════════════════════════════════════════════════════════════
def available_providers() -> list:
    """Список id провайдеров, пригодных к использованию:
    - ollama-протокол: доступен если _status[id] is True (сервер отвечает)
    - остальные: доступны если задан api_key (статус может быть ещё не определён)
    """
    result = []
    for p in _data.get("providers", []):
        pid = p.get("id")
        if not pid:
            continue
        if p.get("protocol") == "ollama":
            if _status.get(pid) is True:
                result.append(pid)
        else:
            if p.get("api_key", ""):
                result.append(pid)
    return result


def models_for_provider(pid: str) -> list:
    """Список моделей (без префикса провайдера) для указанного id.
    Динамически загруженные модели优先; иначе резерв по protocol/id."""
    fetched = _models.get(pid, [])
    if fetched:
        return list(fetched)
    rec = get_provider(pid)
    if not rec:
        return []
    # fallback по protocol (и id для glm/openai)
    if rec.get("protocol") == "anthropic":
        return list(_FALLBACK_MODELS["anthropic"])
    if rec.get("protocol") == "openai-compat":
        if pid == "openai":
            return list(_FALLBACK_MODELS["openai-compat"])
        if pid == "glm":
            return ["glm-4.7-flash", "glm-4.7", "glm-4.6",
                    "glm-4.5-flash", "glm-4.5-air", "glm-4.5"]
        return []
    return []


# ═══════════════════════════════════════════════════════════════════════════
# Probe (проверка доступности) — обобщённые по protocol
# ═══════════════════════════════════════════════════════════════════════════
def add_status_callback(fn):
    _status_cbs.append(fn)


def _notify():
    for fn in _status_cbs:
        try:
            fn()
        except Exception:
            pass


def probe_all():
    """Запустить проверку всех провайдеров (каждая в своём потоке)."""
    for rec in _data.get("providers", []):
        threading.Thread(
            target=_safe_probe, args=(rec,), daemon=True,
            name=f"hush-probe-{rec.get('id', 'x')}",
        ).start()


def probe_one(pid: str):
    """Запустить проверку одного провайдера по id (для ручной кнопки [Проверить])."""
    rec = get_provider(pid)
    if rec is None:
        return
    threading.Thread(
        target=_safe_probe, args=(rec,), daemon=True,
        name=f"hush-probe-{pid}",
    ).start()


def _safe_probe(rec: dict):
    """Обёртка: вызывает _dispatch_probe и ловит любые исключения."""
    try:
        _dispatch_probe(rec)
    except Exception as e:
        pid = rec.get("id", "?")
        _status[pid] = False
        _models[pid] = []
        print(f"[providers] probe {pid}: unexpected error: {e}")
        _notify()


def _dispatch_probe(rec: dict):
    """Маршрутизация probe-функции по полю protocol."""
    protocol = rec.get("protocol", "openai-compat")
    if protocol == "ollama":
        _probe_ollama(rec)
    elif protocol == "anthropic":
        _probe_anthropic(rec)
    else:
        _probe_openai_compat(rec)


def _probe_ollama(rec: dict):
    """Ollama: GET {base_url}/api/tags, без авторизации. Список моделей из data['models']."""
    pid = rec["id"]
    base = (rec.get("base_url") or "http://localhost:11434").rstrip("/")
    try:
        req = urllib.request.Request(
            f"{base}/api/tags",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
        _models[pid] = [m["name"] for m in data.get("models", [])]
        _status[pid] = True
    except Exception:
        _models[pid] = []
        _status[pid] = False
    _notify()


def _probe_anthropic(rec: dict):
    """Anthropic: GET {base_url}/models, x-api-key, фильтр 'claude'."""
    pid = rec["id"]
    key = rec.get("api_key", "")
    base = (rec.get("base_url") or "https://api.anthropic.com/v1").rstrip("/")
    if not key or len(key) <= 10:
        _status[pid] = False
        _models[pid] = list(_FALLBACK_MODELS["anthropic"]) if key else []
        _notify()
        return
    try:
        req = urllib.request.Request(
            f"{base}/models",
            headers={
                "x-api-key":          key,
                "anthropic-version":  "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        ids = [m["id"] for m in data.get("data", []) if "claude" in m.get("id", "")]
        _models[pid] = sorted(ids, reverse=True) if ids else list(_FALLBACK_MODELS["anthropic"])
        _status[pid] = True
    except urllib.error.HTTPError as e:
        _status[pid] = e.code != 401
        _models[pid] = list(_FALLBACK_MODELS["anthropic"])
    except Exception:
        _status[pid] = False
        _models[pid] = list(_FALLBACK_MODELS["anthropic"])
    _notify()


def _probe_openai_compat(rec: dict):
    """OpenAI-compat: GET {base_url}/models, Bearer. Фильтрация зависит от id."""
    pid = rec["id"]
    key = rec.get("api_key", "")
    base = (rec.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    if not key or len(key) <= 10:
        _status[pid] = False
        _models[pid] = list(_FALLBACK_MODELS["openai-compat"]) if pid == "openai" and key else []
        _notify()
        return
    try:
        req = urllib.request.Request(
            f"{base}/models",
            headers={"Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        ids = [m["id"] for m in data.get("data", [])]
        # Применить фильтр по id (например glm оставляет только 'glm-...'),
        # для 'openai' — стандартный gpt/o-фильтр, для остальных — без фильтра.
        if pid in _ID_MODEL_FILTERS:
            kept = [m for m in ids if _ID_MODEL_FILTERS[pid](m)]
            _models[pid] = sorted(kept, reverse=True) if kept else []
        elif pid == "openai":
            _models[pid] = _openai_default_filter(ids)
        else:
            _models[pid] = sorted(ids) if ids else []
        _status[pid] = True
    except urllib.error.HTTPError as e:
        _status[pid] = e.code != 401
        _models[pid] = (list(_FALLBACK_MODELS["openai-compat"])
                        if pid == "openai" else [])
    except Exception:
        _status[pid] = False
        _models[pid] = (list(_FALLBACK_MODELS["openai-compat"])
                        if pid == "openai" else [])
    _notify()


# ═══════════════════════════════════════════════════════════════════════════
# Статус / утилиты отображения
# ═══════════════════════════════════════════════════════════════════════════
def get_status(provider: str):
    return _status.get(provider)


def get_models_cache(pid: str) -> list:
    """Список динамически загруженных моделей (без fallback) — для отладки/UI."""
    return list(_models.get(pid, []))


# ── Миграция ~/.hush_env → providers.json (старая, оставлена для надёжности) ──
def migrate_hush_env_if_any():
    """Если существует ~/.hush_env (старый источник ключей) и providers.json
    не содержит ключей — переносим ключи и переименовываем .hush_env."""
    env_path = os.path.expanduser("~/.hush_env")
    if not os.path.exists(env_path):
        return
    # Если пользователь уже настроил providers.json, не перетираем значения из
    # устаревшего файла. Это особенно важно после перехода на schema v2.
    if any((get_provider(pid) or {}).get("api_key") for pid in ("anthropic", "openai", "glm")):
        return
    if (get_provider("ollama") or {}).get("base_url") not in ("", "http://localhost:11434"):
        return
    try:
        migrated = False
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip().upper()
                v = v.strip().strip('"').strip("'")
                if not v:
                    continue
                if k in ("ANTHROPIC_API_KEY",):
                    set_field("anthropic", "api_key", v)
                    migrated = True
                elif k in ("OPENAI_API_KEY",):
                    set_field("openai", "api_key", v)
                    migrated = True
                elif k in ("GLM_API_KEY",):
                    set_field("glm", "api_key", v)
                    migrated = True
                elif k in ("OLLAMA_BASE_URL",):
                    set_field("ollama", "base_url", v)
                    migrated = True
        if migrated:
            os.rename(env_path, env_path + ".migrated")
    except Exception as e:
        print(f"[providers] hush_env migration: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Маскировка ключа для отображения
# ═══════════════════════════════════════════════════════════════════════════
def mask_key(key: str) -> str:
    """Маскирует API-ключ для отображения: первые 8 + точки + последние 4 символа."""
    if not key:
        return ""
    if len(key) <= 14:
        return "·" * len(key)
    return key[:8] + "·" * 6 + key[-4:]
