#!/usr/bin/env python3
"""HUSH Windows — точка входа (аналог src/main.py для macOS).

Запуск: python main_win.py
Требует Windows 11 / Windows 10 и Python 3.11+.

Логика:
  Right Alt           → запись пока зажато → транскрипция → вставить
  Shift + Right Alt   → открыть/закрыть главное окно
  При наличии сценария с prompt — прогнать текст через LLM перед вставкой.
"""

import sys
import os
import threading
import time
import queue as _qmod
import json
import uuid
import signal
from datetime import datetime, timezone
from pathlib import Path

# ── Проверка платформы ────────────────────────────────────────────────────────

if sys.platform != "win32":
    print("HUSH Windows требует Windows. Используйте src/main.py на macOS.", file=sys.stderr)
    sys.exit(1)

# ── Защита от запуска нескольких экземпляров (named mutex) ────────────────────

import ctypes

_MUTEX_NAME = "Global\\HUSHSingleInstance"
_mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    print("HUSH уже запущен.", file=sys.stderr)
    sys.exit(0)

# ── Путь к src/ (для импорта) ─────────────────────────────────────────────────

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC  = os.path.join(_HERE, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ── Алиас config → config_win (для совместимости с processor.py) ─────────────
# processor.py делает "from config import LLM_MODEL, N8N_WEBHOOK_URL"
# На Windows мы подставляем config_win вместо macOS-специфичного config.py
import importlib, types as _types
import config_win as _config_win_mod
sys.modules.setdefault("config", _config_win_mod)

# ── Импорт модулей ────────────────────────────────────────────────────────────

import provider_config
import processor
import recorder
import transcriber_win as transcriber
import injector_win   as injector
import overlay_win    as overlay

from config_win import (
    SAMPLE_RATE, VOICE_LANG, LLM_MODEL,
    CONFIG_DIR, log_path,
)

# ── Отладочный лог ────────────────────────────────────────────────────────────

_DBG_LOG = log_path("hush_win_debug.log")

def _dbg(msg: str):
    try:
        with open(_DBG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass

# ── История транскрипций ──────────────────────────────────────────────────────

HISTORY_FILE = CONFIG_DIR / "history.json"
_history:      list  = []
MAX_HISTORY            = 50
_current_hist_id: str | None = None
_current_session_id: str | None = None

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _load_history():
    global _history
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE, encoding="utf-8") as f:
                data = json.load(f)
            migrated = []
            for item in data:
                if isinstance(item, dict) and "id" in item:
                    migrated.append(item)
                else:
                    short = item.get("short", "") if isinstance(item, dict) else ""
                    full  = item.get("full",  "") if isinstance(item, dict) else ""
                    migrated.append({
                        "id":         str(uuid.uuid4()),
                        "created_at": _now_iso(),
                        "short":      short,
                        "full":       full,
                    })
            _history = migrated
    except Exception as e:
        _dbg(f"history load error: {e}")

def _save_history():
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(_history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _dbg(f"history save error: {e}")

def _add_to_history(text: str, parent_id: str = None) -> str:
    global _current_hist_id
    short = text[:55].replace("\n", " ")
    if len(text) > 55:
        short += "…"
    if _history and _history[0].get("full") == text:
        _current_hist_id = _history[0]["id"]
        return _history[0]["id"]
    new_id = str(uuid.uuid4())
    _history.insert(0, {
        "id":         new_id,
        "created_at": _now_iso(),
        "short":      short,
        "full":       text,
        "parent_id":  parent_id,
    })
    while len(_history) > MAX_HISTORY:
        _history.pop()
    _current_hist_id = new_id
    _save_history()
    return new_id

def _get_history() -> list:
    return [h for h in _history if not h.get("deleted")]

# ── Состояние сессии ──────────────────────────────────────────────────────────

_state = {
    "stream":      None,   # активный sounddevice stream
    "hotkey_held": False,
    "cancelled":   False,
}

_audio_queue  = _qmod.Queue()
_accum_texts  = []              # накопленные блоки текущей сессии
_accum_lock   = threading.Lock()
_worker_alive = False
_worker_lock  = threading.Lock()
_stopping     = 0               # счётчик работающих _stop_and_queue потоков

# HWND предыдущего активного окна — цель для вставки
_prev_hwnd: int = 0

# ── Блокировка обработки (LLM/вставка активна) ───────────────────────────────

_processing_locked = False

# ── Получить HWND перед появлением HUSH окна ─────────────────────────────────

def _capture_prev_hwnd():
    global _prev_hwnd
    try:
        hwnd = injector.get_foreground_hwnd()
        if hwnd:
            _prev_hwnd = hwnd
    except Exception as e:
        _dbg(f"capture_prev_hwnd error: {e}")

# ── Воркер транскрипции ───────────────────────────────────────────────────────

FINALIZE_GRACE_S = 3.0

def _is_session_active() -> bool:
    return (bool(_state.get("stream")) or
            _stopping > 0 or
            _worker_alive or
            not _audio_queue.empty() or
            bool(_accum_texts))


def _ensure_worker_running():
    global _worker_alive
    with _worker_lock:
        if _worker_alive:
            return
        _worker_alive = True
    threading.Thread(target=_transcription_worker, daemon=True,
                     name="hush-worker").start()


def _transcription_worker():
    global _worker_alive
    _last_activity = time.time()
    try:
        while True:
            try:
                wav_path = _audio_queue.get(timeout=0.2)
                _last_activity = time.time()
            except _qmod.Empty:
                if _state.get("stream") or _stopping > 0:
                    continue
                if not _audio_queue.empty():
                    continue
                if not _accum_texts:
                    overlay.show_idle()
                    return
                # Тихий режим: ждём grace-период затем завершаем
                if time.time() - _last_activity < FINALIZE_GRACE_S:
                    continue
                _session_finalize()
                return

            if _state.get("cancelled"):
                continue

            overlay.show_processing()
            text = transcriber.transcribe(wav_path)

            # Удаляем временный WAV
            try:
                os.remove(wav_path)
            except Exception:
                pass

            _last_activity = time.time()

            if _state.get("cancelled"):
                continue

            if text:
                with _accum_lock:
                    _accum_texts.append(text)
                overlay.show_result(text)
    finally:
        _worker_alive = False


def _session_finalize():
    global _processing_locked
    if _state.get("cancelled"):
        return
    _processing_locked = True
    try:
        _session_finalize_inner()
    finally:
        _processing_locked = False


def _session_finalize_inner():
    with _accum_lock:
        texts = list(_accum_texts)
        _accum_texts.clear()

    if not texts:
        overlay.show_idle()
        return

    full_text = "\n\n".join(texts)

    # Берём активный сценарий из overlay (первый с prompt, если есть)
    sc = _get_active_scenario()

    if sc and sc.get("prompt"):
        # LLM обработка
        final_text = processor.process_with_prompt(
            full_text, sc["prompt"], model=sc.get("model")
        )
        if not final_text:
            final_text = full_text
    else:
        final_text = full_text

    _add_to_history(final_text)
    overlay.append_block(final_text)
    overlay.show_idle()

    # Вставка в предыдущее окно
    _commit_and_paste(final_text)


def _get_active_scenario() -> dict | None:
    """Возвращает текущий активный сценарий из overlay (если есть prompt)."""
    # В простом режиме: первый сценарий без prompt — «без обработки»
    # Пользователь выбирает сценарий через кнопки в main window.
    # Здесь возвращаем None (вставляем сырой текст), если явно не выбран сценарий.
    return None


def _commit_and_paste(text: str):
    """Вставляет текст в ранее активное окно."""
    def _do():
        time.sleep(0.25)
        injector.paste_text(text, prev_hwnd=_prev_hwnd)
        _dbg(f"paste done: {text[:40]!r}")
    threading.Thread(target=_do, daemon=True, name="hush-paste").start()

# ── Callbacks для overlay ─────────────────────────────────────────────────────

def _on_scenario(sc: dict, idx: int):
    """Пользователь нажал кнопку сценария в main window."""
    text = overlay.get_text()
    if not text:
        return

    prompt = sc.get("prompt", "")
    if not prompt.strip():
        # Вставить без обработки
        _commit_and_paste(text)
        return

    def _do():
        result = processor.process_with_prompt(text, prompt, model=sc.get("model"))
        if result:
            overlay.set_active_scenario(idx)
            overlay.append_block(result)
            _add_to_history(result)
    threading.Thread(target=_do, daemon=True, name="hush-sc").start()


def _on_paste(mode: str = "raw"):
    """Кнопка [ВСТАВИТЬ] в main window."""
    text = overlay.get_text()
    if not text:
        return
    _add_to_history(text)
    _commit_and_paste(text)


def _on_copy(mode: str = "raw"):
    """Кнопка [КОПИРОВАТЬ] в main window."""
    text = overlay.get_text()
    if not text:
        return
    _add_to_history(text)
    try:
        import ctypes as _c
        import ctypes.wintypes as _cw
        _u32 = _c.windll.user32
        _k32 = _c.windll.kernel32
        GMEM_MOVEABLE  = 0x0002
        CF_UNICODETEXT = 13
        encoded = (text + "\x00").encode("utf-16-le")
        h = _k32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        ptr = _k32.GlobalLock(h)
        _c.memmove(ptr, encoded, len(encoded))
        _k32.GlobalUnlock(h)
        if _u32.OpenClipboard(None):
            _u32.EmptyClipboard()
            _u32.SetClipboardData(CF_UNICODETEXT, h)
            _u32.CloseClipboard()
    except Exception as e:
        _dbg(f"copy error: {e}")

# ── Хоткей (pynput) ───────────────────────────────────────────────────────────

_hotkey_listener = None


def _on_hotkey_press(full_mode: bool = False):
    global _prev_hwnd
    if _state["hotkey_held"]:
        return
    _state["hotkey_held"] = True

    # Shift+Right Alt → открыть/закрыть главное окно
    if full_mode:
        overlay._dispatch(lambda: (overlay._main_win.deiconify()
                                   if overlay._main_win else overlay._create_main_window()))
        return

    # Блокировка во время LLM
    if _processing_locked:
        return

    if _state.get("stream"):
        return  # уже записываем

    # Запоминаем текущее активное окно ПЕРЕД тем как наш хоткей появился
    _capture_prev_hwnd()

    # Сбрасываем флаг отмены
    _state["cancelled"] = False

    _dbg("hotkey press — start recording")
    overlay.show_recording()
    _state["stream"] = recorder.start()


def _on_hotkey_release():
    global _stopping
    if not _state["hotkey_held"]:
        return
    _state["hotkey_held"] = False

    stream = _state.get("stream")
    if not stream:
        return

    def _stop_and_queue():
        global _stopping
        _stopping += 1
        try:
            wav_path, _ = recorder.stop(stream)
            _state["stream"] = None
            _dbg(f"recording stopped, wav={bool(wav_path)}")
            if _state.get("cancelled") or not wav_path:
                return
            _audio_queue.put(wav_path)
            _ensure_worker_running()
        finally:
            _stopping -= 1

    threading.Thread(target=_stop_and_queue, daemon=True,
                     name="hush-stopper").start()


def _setup_hotkey():
    global _hotkey_listener
    from pynput import keyboard as kb

    pressed = set()
    shift_keys = {kb.Key.shift, kb.Key.shift_r, kb.Key.shift_l}

    def on_press(key):
        if key in shift_keys:
            pressed.add(key)
        elif key == kb.Key.alt_r and kb.Key.alt_r not in pressed:
            pressed.add(kb.Key.alt_r)
            shift_held = bool(pressed & shift_keys)
            _on_hotkey_press(full_mode=shift_held)

    def on_release(key):
        if key in shift_keys:
            pressed.discard(key)
        elif key == kb.Key.alt_r and kb.Key.alt_r in pressed:
            pressed.discard(kb.Key.alt_r)
            _on_hotkey_release()

    _hotkey_listener = kb.Listener(on_press=on_press, on_release=on_release)
    _hotkey_listener.daemon = True
    _hotkey_listener.start()
    return _hotkey_listener


# ── Sleep/Wake (Windows) ──────────────────────────────────────────────────────

def _setup_sleep_monitor():
    """Переподключает sounddevice после выхода системы из сна."""
    import ctypes

    WM_POWERBROADCAST  = 0x0218
    PBT_APMRESUMESUSPEND = 0x0007

    class _PwrListener(ctypes.Structure):
        pass

    def _on_wake():
        _dbg("system wake — reinit sounddevice")
        try:
            import sounddevice as sd
            sd._terminate()
            sd._initialize()
        except Exception as e:
            _dbg(f"sounddevice reinit error: {e}")
        # Перезапускаем pynput listener
        global _hotkey_listener
        old = _hotkey_listener
        if old:
            try:
                old.stop()
            except Exception:
                pass
        _setup_hotkey()
        _dbg("hotkey listener restarted after wake")

    # Регистрируем WM_POWERBROADCAST через скрытое окно (CreateWindowEx)
    # Это простой подход без win32gui зависимости.
    def _monitor():
        try:
            import ctypes.wintypes as cwt
            WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, cwt.HWND, cwt.UINT, cwt.WPARAM, cwt.LPARAM)

            def wnd_proc(hwnd, msg, wp, lp):
                if msg == WM_POWERBROADCAST and wp == PBT_APMRESUMESUSPEND:
                    threading.Thread(target=_on_wake, daemon=True).start()
                return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wp, lp)

            wc = ctypes.windll.user32
            className = "HushPwrWnd"
            hinstance = ctypes.windll.kernel32.GetModuleHandleW(None)

            # WNDCLASSW
            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style",         ctypes.c_uint),
                    ("lpfnWndProc",   WNDPROC),
                    ("cbClsExtra",    ctypes.c_int),
                    ("cbWndExtra",    ctypes.c_int),
                    ("hInstance",     cwt.HINSTANCE),
                    ("hIcon",         cwt.HICON),
                    ("hCursor",       cwt.HCURSOR),
                    ("hbrBackground", cwt.HBRUSH),
                    ("lpszMenuName",  cwt.LPCWSTR),
                    ("lpszClassName", cwt.LPCWSTR),
                ]

            wc_obj = WNDCLASSW()
            _proc_ref       = WNDPROC(wnd_proc)  # удерживаем ссылку
            wc_obj.lpfnWndProc   = _proc_ref
            wc_obj.lpszClassName = className
            wc_obj.hInstance     = hinstance

            ctypes.windll.user32.RegisterClassW(ctypes.byref(wc_obj))

            HWND_MESSAGE = -3
            hwnd = ctypes.windll.user32.CreateWindowExW(
                0, className, "HushPwr", 0, 0, 0, 0, 0,
                HWND_MESSAGE, None, hinstance, None
            )

            # Цикл сообщений
            msg_obj = ctypes.wintypes.MSG()
            while ctypes.windll.user32.GetMessageW(ctypes.byref(msg_obj), hwnd, 0, 0) != 0:
                ctypes.windll.user32.TranslateMessage(ctypes.byref(msg_obj))
                ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg_obj))
        except Exception as e:
            _dbg(f"power monitor error: {e}")

    threading.Thread(target=_monitor, daemon=True, name="hush-power-mon").start()


# ── Мониторинг провайдеров ────────────────────────────────────────────────────

def _start_provider_monitor():
    def _loop():
        while True:
            time.sleep(30)
            if any(v is False for v in provider_config._status.values()):
                provider_config.probe_all()
    threading.Thread(target=_loop, daemon=True, name="hush-provider-mon").start()


# ── Запуск ────────────────────────────────────────────────────────────────────

def main():
    print("HUSH Windows запускается...", flush=True)

    # Загружаем провайдеры и историю
    provider_config.load()
    _load_history()

    # Инициализируем overlay (UI регистрирует callbacks)
    overlay.init(
        on_scenario_cb = _on_scenario,
        on_history_cb  = _get_history,
        on_paste_cb    = _on_paste,
        on_copy_cb     = _on_copy,
    )

    # Хоткей (pynput)
    _setup_hotkey()

    # Мониторинг сна
    _setup_sleep_monitor()

    # Проверяем провайдеров
    provider_config.add_status_callback(overlay.update_provider_status)
    provider_config.probe_all()
    _start_provider_monitor()

    # Прогрев Whisper (загрузка модели в фоне)
    transcriber.warm_up()

    # Обработчики сигналов
    signal.signal(signal.SIGINT,  lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    print("HUSH готов. Right Alt — запись. Shift+Right Alt — главное окно.", flush=True)

    # Запускаем tkinter mainloop (блокирующий)
    overlay.run()


if __name__ == "__main__":
    main()
