"""HUSH Windows — транскрибация через faster-whisper (аналог transcriber.py).

Модель загружается при первом вызове warm_up() и кэшируется в памяти.
Все последующие вызовы transcribe() используют уже загруженную модель.

Пути моделей: ~/.local/share/hush/models/faster-whisper-{size}
compute_type:  "int8" на CPU, "float16" на CUDA.
"""

import os
import sys
import threading
import time
import tempfile
import struct
import wave

# ── Конфиг ────────────────────────────────────────────────────────────────────

# Импортируем config_win (Windows-конфиг) но допускаем fallback значения
# чтобы модуль можно было импортировать изолированно в тестах.
try:
    from config_win import MODELS_DIR, WHISPER_MODEL_SIZE, VOICE_LANG, log_path
    _MODELS_DIR      = str(MODELS_DIR)
    _DEFAULT_SIZE    = WHISPER_MODEL_SIZE
    _DEFAULT_LANG    = VOICE_LANG
    _LOG             = log_path("hush_transcribe_win.log")
except ImportError:
    _MODELS_DIR   = os.path.join(os.path.expanduser("~"), ".local", "share", "hush", "models")
    _DEFAULT_SIZE = "base"
    _DEFAULT_LANG = "ru"
    _LOG          = os.path.join(tempfile.gettempdir(), "hush_transcribe_win.log")

# ── Состояние ─────────────────────────────────────────────────────────────────

_model        = None          # faster_whisper.WhisperModel после загрузки
_model_lock   = threading.Lock()
_model_size   = _DEFAULT_SIZE
_cancel_event = threading.Event()   # взводится в cancel(), сбрасывается перед каждой транскрипцией

# ── Логирование ───────────────────────────────────────────────────────────────

def _log(msg: str):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] [transcriber_win] {msg}\n"
    print(line, end="", flush=True)
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

# ── Загрузка модели ───────────────────────────────────────────────────────────

def _model_dir(size: str) -> str:
    return os.path.join(_MODELS_DIR, f"faster-whisper-{size}")

def _load_model(size: str = None):
    """Загружает WhisperModel. Блокирует вызывающий поток до завершения."""
    global _model, _model_size
    size = size or _DEFAULT_SIZE
    model_path = _model_dir(size)
    os.makedirs(_MODELS_DIR, exist_ok=True)

    _log(f"loading model '{size}' (device=auto, compute_type=int8) ...")
    try:
        from faster_whisper import WhisperModel

        # Если локальная модель уже скачана — используем её путь напрямую.
        # Иначе faster-whisper скачает из HuggingFace Hub и закэширует в model_path.
        if os.path.isdir(model_path):
            source = model_path
            _log(f"  found local model at {model_path}")
        else:
            # faster-whisper кэширует модели в стандартный HF_HOME/hub; укажем явно
            source = f"Systran/faster-whisper-{size}"
            _log(f"  will download '{source}' and cache to HF hub cache")

        model = WhisperModel(
            source,
            device="auto",          # CUDA если доступна, иначе CPU
            compute_type="int8",    # int8 хорошо работает на CPU; на CUDA можно float16
            download_root=_MODELS_DIR,
        )
        with _model_lock:
            _model      = model
            _model_size = size
        _log(f"model '{size}' loaded OK")
    except Exception as e:
        _log(f"ERROR loading model: {e}")
        raise


def warm_up():
    """Загружает модель в фоновом потоке при старте приложения.
    Безопасно вызывать многократно — повторная загрузка не происходит."""
    with _model_lock:
        if _model is not None:
            return   # уже загружена

    def _run():
        try:
            _load_model()
        except Exception as e:
            _log(f"warm_up failed: {e}")

    threading.Thread(target=_run, daemon=True, name="hush-whisper-warmup").start()


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _ensure_model():
    """Синхронно убеждаемся что модель загружена (блокирует если warm_up ещё работает)."""
    with _model_lock:
        if _model is not None:
            return _model

    # Модель ещё не загружена (warm_up не вызывался) — загружаем синхронно
    _load_model()
    with _model_lock:
        return _model


def _make_silent_wav(path: str, duration_s: float = 0.5, sample_rate: int = 16000):
    """Создаёт короткий тихий WAV — используется в тестах warm_up."""
    n    = int(sample_rate * duration_s)
    data = b"\x00" * (n * 2)
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + len(data)))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
        f.write(b"data")
        f.write(struct.pack("<I", len(data)))
        f.write(data)


def _get_language() -> str:
    """Возвращает текущий язык из overlay_win (если доступен) или из config_win."""
    try:
        from overlay_win import get_current_lang
        lang = get_current_lang()
        if lang:
            return lang
    except Exception:
        pass
    return _DEFAULT_LANG


# ── Транскрибация ─────────────────────────────────────────────────────────────

def transcribe(wav_path: str) -> str:
    """Транскрибирует WAV-файл, возвращает строку текста (или "" при ошибке/отмене).

    Использует кэшированную модель (загружает если нужно).
    Язык берётся из overlay_win.get_current_lang() или из config_win.VOICE_LANG.
    """
    _cancel_event.clear()

    # Базовая проверка входного файла
    try:
        if not os.path.exists(wav_path):
            _log(f"SKIP: file not found: {wav_path}")
            return ""
        size = os.path.getsize(wav_path)
        if size < 256:
            _log(f"SKIP: file too small ({size} bytes): {wav_path}")
            return ""
    except Exception as e:
        _log(f"SKIP: stat error: {e}")
        return ""

    t0   = time.time()
    lang = _get_language()

    try:
        model = _ensure_model()
    except Exception as e:
        _log(f"ERROR: could not load model: {e}")
        return ""

    if _cancel_event.is_set():
        return ""

    try:
        _log(f"transcribing {wav_path!r} (lang={lang}) ...")
        segments, info = model.transcribe(
            wav_path,
            language=lang,
            beam_size=5,
            vad_filter=True,           # отфильтровываем тишину
            vad_parameters={
                "min_silence_duration_ms": 300,
                "speech_pad_ms": 200,
            },
        )

        parts = []
        for seg in segments:
            if _cancel_event.is_set():
                _log("cancelled during segment iteration")
                return ""
            text = seg.text.strip()
            if text:
                parts.append(text)

        result = " ".join(parts).strip()
        elapsed = time.time() - t0
        _log(f"transcribed in {elapsed:.1f}s: {result[:80]!r}")
        return result

    except Exception as e:
        _log(f"ERROR during transcription: {e}")
        return ""


def cancel():
    """Прерывает текущую транскрибацию (устанавливает флаг отмены)."""
    _log("cancel() called")
    _cancel_event.set()
