"""LLM постобработка транскрибированного текста.

Маршрутизация по строке модели сценария "provider:model_name":
    ollama:qwen3:8b              → локальный Ollama
    anthropic:claude-haiku-...   → Anthropic API
    openai:gpt-4o-mini           → OpenAI-совместимый API
    glm:glm-4-flash              → GLM (Zhipu) API (openai-compat)
    my-mistral:mistral-large     → любой пользовательский провайдер (v2.2)
    null / "" / "auto:..."       → авто: Ollama → Anthropic как запасной

В v2.2 провайдер ищется через provider_config.get_provider(provider_id);
протокол вызова (ollama/anthropic/openai-compat) берётся из записи провайдера,
а не из хардкод-веток. Добавить новый провайдер можно без правок здесь.
"""

import re
import json
import urllib.request
import urllib.error
import provider_config as _pc
from config import LLM_MODEL, N8N_WEBHOOK_URL


# ── Маршрутизация ─────────────────────────────────────────────────────────────

def _parse(model_str):
    """'provider:model' → (provider, model).  None/'' → ('auto', '')."""
    if not model_str:
        return "auto", ""
    provider, _, rest = model_str.partition(":")
    return provider.lower(), rest


def process_with_prompt(text: str, prompt: str, model: str = None) -> str:
    """Прогоняет транскрибированный текст через LLM с заданным prompt сценария."""
    if not prompt.strip():
        _log(f"skip (empty prompt)")
        return text

    if prompt.startswith("n8n:"):
        return _n8n(text)

    provider, model_name = _parse(model)
    _log(f"→ {provider}:{model_name or '(default)'} | text={text[:40]!r}")

    try:
        if provider == "auto":
            return _auto_route(prompt, text)

        # Найти запись провайдера (по id или label — обратно-совместимо со старыми сценариями)
        rec = _pc.find_by_label_or_id(provider)
        if rec is None:
            _log(f"  unknown provider {provider!r}, fallback to auto")
            return _auto_route(prompt, text)

        return _dispatch_by_protocol(rec, model_name, prompt, text)

    except Exception as e:
        _log(f"✗ {provider} error: {e}")
        return text


def _dispatch_by_protocol(rec: dict, model_name: str, system: str, text: str) -> str:
    """Вызвать LLM в соответствии с протоколом провайдера.
    rec — словарь записи провайдера (id/label/protocol/base_url/api_key/...)."""
    protocol = rec.get("protocol", "openai-compat")
    pid      = rec.get("id", "?")
    if protocol == "ollama":
        m = model_name or rec.get("default_model") or "qwen3:8b"
        result = _ollama(rec, system, text, m)
        _log(f"← {pid}:{m} ok | result={result[:60]!r}")
        return result
    if protocol == "anthropic":
        m = model_name or rec.get("default_model") or LLM_MODEL
        result = _anthropic(rec, system, text, m)
        _log(f"← {pid}:{m} ok | result={result[:60]!r}")
        return result
    # default — openai-compat
    m = model_name or rec.get("default_model") or ""
    if not m:
        raise ValueError(f"no model specified for {pid}")
    result = _openai_compat(rec, system, text, m)
    _log(f"← {pid}:{m} ok | result={result[:60]!r}")
    return result


def _auto_route(system: str, text: str) -> str:
    """Авто-маршрутизация: первый доступный провайдер по приоритету.
    Ollama (если отвечает) → любой с api_key (anthropic/openai-compat) → raw text."""
    # 1) Ollama-провайдеры со статусом True
    for rec in _pc.list_providers():
        if rec.get("protocol") == "ollama" and _pc.get_status(rec["id"]) is True:
            try:
                m = rec.get("default_model") or "qwen3:8b"
                result = _ollama(rec, system, text, m)
                _log(f"← auto→{rec['id']}:{m} ok")
                return result
            except Exception as e1:
                _log(f"  {rec['id']} failed: {e1}")
                continue
    # 2) Любой cloud-провайдер с заданным ключом (anthropic приоритетнее для совместимости)
    for pid in ("anthropic", "openai", "glm"):
        rec = _pc.get_provider(pid)
        if rec and rec.get("api_key"):
            try:
                return _dispatch_by_protocol(rec, rec.get("default_model") or "", system, text)
            except Exception as e2:
                _log(f"  {pid} failed: {e2}")
                continue
    # 3) Любой другой пользовательский провайдер с ключом
    for rec in _pc.list_providers():
        if rec.get("id") in ("anthropic", "openai", "glm", "ollama"):
            continue
        if rec.get("protocol") != "ollama" and rec.get("api_key"):
            try:
                return _dispatch_by_protocol(rec, rec.get("default_model") or "", system, text)
            except Exception as e3:
                _log(f"  {rec['id']} failed: {e3}")
                continue
    _log("  no available provider, returning raw text")
    return text


def _log(msg: str):
    import time
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] [processor] {msg}\n"
    print(line, end="", flush=True)
    try:
        with open("/tmp/hush_processor.log", "a") as f:
            f.write(line)
    except Exception:
        pass


# ── Провайдеры ────────────────────────────────────────────────────────────────

def _ollama(rec: dict, system: str, text: str, model: str) -> str:
    base = (rec.get("base_url") or "http://localhost:11434").rstrip("/")
    payload = json.dumps({
        "model":    model,
        "think":    False,   # отключаем chain-of-thought (qwen3, deepseek-r1)
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
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.loads(resp.read())
    result = data["message"]["content"].strip()
    result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL).strip()
    return result


# Anthropic SDK кэшируется по api_key, чтобы не пересоздавать клиент на каждый вызов.
_anthropic_client     = None
_anthropic_client_key = None


def _anthropic(rec: dict, system: str, text: str, model: str) -> str:
    global _anthropic_client, _anthropic_client_key
    key = rec.get("api_key") or ""
    base = rec.get("base_url") or ""
    if _anthropic_client is None or _anthropic_client_key != key:
        import anthropic
        kwargs = {"api_key": key}
        # Anthropic SDK принимает base_url опционально
        if base:
            kwargs["base_url"] = base
        _anthropic_client     = anthropic.Anthropic(**kwargs)
        _anthropic_client_key = key
    msg = _anthropic_client.messages.create(
        model=model,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": text}],
    )
    return msg.content[0].text.strip()


def _openai_compat(rec: dict, system: str, text: str, model: str) -> str:
    base_url = (rec.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    api_key  = rec.get("api_key") or ""

    # o3/o1 серия не поддерживает max_tokens — используем max_completion_tokens
    _o_series = model.startswith(("o1", "o3", "o4"))
    _tok_key  = "max_completion_tokens" if _o_series else "max_tokens"
    payload = json.dumps({
        "model":    model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": text},
        ],
        _tok_key:      2048,
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
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        # НЕ печатаем api_key; body может содержать детали, но не ключ
        _log(f"  HTTP {e.code} body: {body[:300]}")
        raise
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
