import sounddevice as sd
import numpy as np
import wave
import threading
import uuid
import os
from config import SAMPLE_RATE

_lock = threading.Lock()
_recording = False
_frames = []

# Parakeet TDT обрабатывает ровно 15 секунд за раз
_CHUNK_SAMPLES = 240000


def start(on_chunk=None):
    """Начинает запись голоса. Возвращает stream object."""
    global _recording, _frames

    with _lock:
        _recording = True
        _frames = []

    def callback(indata, frames, time, status):
        if _recording:
            with _lock:
                _frames.append(indata.copy())
            if on_chunk:
                on_chunk(indata.flatten())

    stream = sd.InputStream(
        callback=callback,
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=int(SAMPLE_RATE * 0.02),
    )
    stream.start()
    return stream


def stop(stream):
    """Останавливает запись. Возвращает (wav_path, 0) или (None, 0) при ошибке."""
    global _recording
    with _lock:
        _recording = False
    stream.stop()
    stream.close()

    with _lock:
        if not _frames:
            return None, 0
        audio = np.concatenate(_frames, axis=0).flatten()

    if audio.size == 0:
        return None, 0

    # Prepend 300ms silence: Parakeet TDT cuts first syllable when speech
    # starts at frame 0 (model expects a brief leading silence).
    _PRE = int(SAMPLE_RATE * 0.30)
    audio = np.concatenate([np.zeros(_PRE, dtype=np.float32), audio])

    # Peak normalization: target peak 0.65 regardless of utterance length.
    # RMS-based normalization fails for short words: a 0.7s word in a 15s window
    # drives gain high → clipping (old code), or the RMS over just the word is
    # high → gain < 1.0 → signal too quiet for parakeet's internal VAD (new code).
    # Peak normalization is length-independent and guarantees a loud clean signal.
    peak = float(np.max(np.abs(audio)))
    if peak > 0.001:
        gain = min(0.65 / peak, 8.0)
        audio = audio * gain

    # Adaptive padding: short recordings (single words) only get 500ms tail silence.
    # Forcing 15s of padding buries short words in silence (4% speech → TDT blank
    # token dominates and suppresses all real tokens → Tokens total=0).
    # Longer recordings get up to 15s so parakeet has full context.
    _TAIL      = int(SAMPLE_RATE * 0.5)     # 500ms trailing silence
    _target    = min(audio.size + _TAIL, _CHUNK_SAMPLES)
    if audio.size < _target:
        pad = np.zeros(_target - audio.size, dtype=np.float32)
        audio = np.concatenate([audio, pad])

    try:
        import time as _t
        _rms = float(np.sqrt(np.mean(audio ** 2)))
        _pk  = float(np.max(np.abs(audio)))
        _dur = audio.size / SAMPLE_RATE
        with open("/tmp/vi_recorder.log", "a") as _f:
            _f.write(f"[{_t.strftime('%H:%M:%S')}] dur={_dur:.1f}s rms={_rms:.4f} peak={_pk:.4f} gain={gain:.2f}\n")
    except Exception:
        pass

    wav_path = f"/tmp/hush_chunk_{uuid.uuid4().hex[:8]}.wav"
    with wave.open(wav_path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)  # 16-bit
        f.setframerate(SAMPLE_RATE)
        pcm = (audio * 32767).astype(np.int16)
        f.writeframes(pcm.tobytes())

    return wav_path, 0
