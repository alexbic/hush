import subprocess
import threading
import struct
import os
from config import PARAKEET_CLI, LANG_ID, MODEL_DIR

# Diagnostic messages that parakeet-cli prints to stdout but are not transcription
_SYSTEM_PREFIXES = (
    "Unable to load",
    "GetMPSGraph",
    "MPSGraph",
    "/Users/",
    "/private/",
)

# First run compiles CoreML model (~4 min). After caching: ~33 s for 15 s audio.
# 360 s covers cold start + up to ~2 min of audio.
_TIMEOUT = 360

def _clean(raw: str) -> str:
    lines = [l for l in raw.splitlines()
             if not any(l.startswith(p) or p in l for p in _SYSTEM_PREFIXES)]
    return "\n".join(lines).strip()

def _make_silent_wav(path: str, duration_s: float = 1.0, sample_rate: int = 16000):
    """Write a minimal silent mono 16-bit PCM WAV file."""
    n = int(sample_rate * duration_s)
    data = b"\x00" * (n * 2)
    with open(path, "wb") as f:
        # RIFF header
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + len(data)))
        f.write(b"WAVE")
        # fmt chunk
        f.write(b"fmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
        # data chunk
        f.write(b"data")
        f.write(struct.pack("<I", len(data)))
        f.write(data)

def warm_up():
    """Run a short silent audio through parakeet in background to trigger CoreML compilation."""
    def _run():
        tmp = "/tmp/_parakeet_warmup.wav"
        try:
            _make_silent_wav(tmp)
            env = os.environ.copy()
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
    import time as _t
    env = os.environ.copy()
    env["PARAKEET_LANG_ID"] = str(LANG_ID)
    env["PARAKEET_MODEL_DIR"] = MODEL_DIR
    result = subprocess.run(
        [PARAKEET_CLI, wav_path],
        capture_output=True,
        text=True,
        env=env,
        timeout=_TIMEOUT,
    )
    cleaned = _clean(result.stdout)
    try:
        with open("/tmp/vi_transcribe.log", "a") as f:
            f.write(f"\n[{_t.strftime('%H:%M:%S')}]\n"
                    f"  stdout={repr(result.stdout[:500])}\n"
                    f"  stderr_tail={repr(result.stderr[-300:])}\n"
                    f"  cleaned={repr(cleaned)}\n"
                    f"  rc={result.returncode}\n")
    except Exception:
        pass
    return cleaned
