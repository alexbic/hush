import re
import signal
import subprocess
import threading
import struct
import os
from config import PARAKEET_CLI, LANG_ID, LANG_IDS, MODEL_DIR

_current_proc = None   # type: subprocess.Popen | None
_proc_lock    = threading.Lock()

def cancel():
    """Немедленно завершить выполняющийся subprocess parakeet."""
    with _proc_lock:
        proc = _current_proc
    if proc and proc.poll() is None:
        try:
            proc.kill()
        except Exception:
            pass

# Первый запуск компилирует CoreML модель (~4 мин). После кэширования: ~33 с для 15 с аудио.
# 360 с покрывает холодный старт + до ~2 мин аудио.
_TIMEOUT = 360

# Паттерны, которые parakeet-cli иногда вставляет в одну строку с транскрипцией.
# Вырезаем их inline (не всю строку), чтобы не потерять транскрипцию.
_INLINE_NOISE = re.compile(
    r'(Unable to load[^\n]*?@ GetMPSGraphExecutable'
    r'|GetMPSGraph\w*'
    r'|MPSGraph\w*'
    r'|/Users/\S+'
    r'|/private/\S+)',
    re.DOTALL
)

def _clean(raw: str) -> str:
    # Удаляем системный шум везде где он встречается, затем убираем пустые строки
    cleaned = _INLINE_NOISE.sub('', raw)
    lines = [l.strip() for l in cleaned.splitlines() if l.strip()]
    return "\n".join(lines).strip()

def _make_silent_wav(path: str, duration_s: float = 1.0, sample_rate: int = 16000):
    """Записывает минимальный тихий моно 16-бит PCM WAV файл."""
    n = int(sample_rate * duration_s)
    data = b"\x00" * (n * 2)
    with open(path, "wb") as f:
        # RIFF заголовок
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + len(data)))
        f.write(b"WAVE")
        # fmt чанк
        f.write(b"fmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
        # data чанк
        f.write(b"data")
        f.write(struct.pack("<I", len(data)))
        f.write(data)

def warm_up():
    """Прогоняет короткое тихое аудио через parakeet в фоне для запуска компиляции CoreML."""
    def _run():
        tmp = "/tmp/_parakeet_warmup.wav"
        try:
            _make_silent_wav(tmp)
            env = os.environ.copy()
            try:
                from overlay import _st
                env["PARAKEET_LANG_ID"] = str(LANG_IDS.get(_st.get("lang", "ru"), LANG_ID))
            except Exception:
                env["PARAKEET_LANG_ID"] = str(LANG_ID)
            env["PARAKEET_MODEL_DIR"] = MODEL_DIR
            subprocess.run(
                [PARAKEET_CLI, tmp],
                capture_output=True,
                env=env,
                timeout=_TIMEOUT,
            )
        except Exception:
            pass
        finally:
            try:
                os.remove(tmp)
            except Exception:
                pass
    threading.Thread(target=_run, daemon=True, name="parakeet-warmup").start()

def transcribe(wav_path: str) -> str:
    global _current_proc
    import time as _t
    # Защита: parakeet падает с ошибкой ExtAudioFileOpenURL на отсутствующих/пустых файлах
    try:
        if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 256:
            with open("/tmp/vi_transcribe.log", "a") as _f:
                _f.write(f"\n[{_t.strftime('%H:%M:%S')}] SKIP: invalid file {wav_path}\n")
            return ""
    except Exception:
        return ""
    env = os.environ.copy()
    # Берём lang_id из текущего языка UI; LANG_ID — резерв если _st недоступен
    try:
        from overlay import _st
        env["PARAKEET_LANG_ID"] = str(LANG_IDS.get(_st.get("lang", "ru"), LANG_ID))
    except Exception:
        env["PARAKEET_LANG_ID"] = str(LANG_ID)
    env["PARAKEET_MODEL_DIR"] = MODEL_DIR
    proc = subprocess.Popen(
        [PARAKEET_CLI, wav_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    with _proc_lock:
        _current_proc = proc
    try:
        stdout_b, stderr_b = proc.communicate(timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout_b, stderr_b = proc.communicate()
    finally:
        with _proc_lock:
            if _current_proc is proc:
                _current_proc = None
    if proc.returncode == -9:  # завершён через cancel()
        return ""
    result_stdout = stdout_b.decode("utf-8", errors="replace")
    result_stderr = stderr_b.decode("utf-8", errors="replace")
    cleaned = _clean(result_stdout)
    try:
        with open("/tmp/vi_transcribe.log", "a") as f:
            f.write(f"\n[{_t.strftime('%H:%M:%S')}]\n"
                    f"  stdout={repr(result_stdout[:500])}\n"
                    f"  stderr_tail={repr(result_stderr[-300:])}\n"
                    f"  cleaned={repr(cleaned)}\n"
                    f"  rc={proc.returncode}\n")
    except Exception:
        pass
    return cleaned
