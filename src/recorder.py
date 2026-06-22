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

    # Добавляем 300мс тишины в начало: Parakeet TDT срезает первый слог когда речь
    # начинается с нулевого фрейма (модель ожидает небольшую ведущую тишину).
    _PRE = int(SAMPLE_RATE * 0.30)
    audio = np.concatenate([np.zeros(_PRE, dtype=np.float32), audio])

    # Нормализация по пику: целевой пик 0.65 независимо от длины высказывания.
    # RMS-нормализация не работает для коротких слов: слово 0.7с в 15с окне
    # сильно усиливает gain → клиппинг (старый код), или RMS по самому слову
    # высокий → gain < 1.0 → сигнал слишком тихий для внутреннего VAD parakeet (новый код).
    # Нормализация по пику не зависит от длины и гарантирует громкий чистый сигнал.
    peak = float(np.max(np.abs(audio)))
    if peak > 0.001:
        gain = min(0.65 / peak, 8.0)
        audio = audio * gain

    # Адаптивное дополнение: короткие записи (отдельные слова) получают только 500мс тишины в конце.
    # Принудительные 15с дополнения заглушают короткие слова в тишине (4% речи → TDT blank
    # token доминирует и подавляет все реальные токены → Tokens total=0).
    # Более длинные записи получают до 15с, чтобы parakeet имел полный контекст.
    _TAIL      = int(SAMPLE_RATE * 0.5)     # 500мс тишины в конце
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
        f.setsampwidth(2)  # 16-бит
        f.setframerate(SAMPLE_RATE)
        pcm = (audio * 32767).astype(np.int16)
        f.writeframes(pcm.tobytes())

    return wav_path, 0
