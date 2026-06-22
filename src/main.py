#!/usr/bin/env python3
"""Voice Input System — системный голосовой ввод с Parakeet TDT.
Запускается как фоновый процесс без иконки в меню и Dock.
Активация только хоткеем (Right ⌥); двойное нажатие открывает историю.
"""
import queue as _qmod
import re
import subprocess
import threading
import signal
import time
import json
import os
import sys
import uuid
from datetime import datetime, timezone
import AppKit
import objc
from pynput import keyboard as kb

# ── Защита от запуска нескольких экземпляров через lock-файл ─────────────────
_LOCK_FILE = "/tmp/hush.lock"
try:
    import fcntl
    _lock_fd = open(_LOCK_FILE, "w")
    fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _lock_fd.write(str(os.getpid()))
    _lock_fd.flush()
except (IOError, OSError):
    sys.exit(0)   # другой экземпляр уже запущен — тихо завершаемся

import recorder
import transcriber

_DBG_LOG = "/tmp/vi_debug.log"
def _dbg(msg):
    try:
        with open(_DBG_LOG, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass
import provider_config
import processor
import injector
import overlay

# ── История транскрипций (долгосрочная, сохраняется в файл) ──────────────────

HISTORY_FILE = os.path.expanduser("~/.config/hush/history.json")
_history     = []   # [{"id": str, "created_at": str, "short": str, "full": str}, ...]
MAX_HISTORY  = 50

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _cleanup_deleted():
    """Удаляет мягко-удалённые элементы, на которые не ссылается ни одна живая запись (как parent_id или блок сессии)."""
    global _history
    live_parent_ids = {h.get("parent_id") for h in _history if not h.get("deleted")}
    live_session_blocks = set()
    for h in _history:
        if not h.get("deleted") and h.get("type") == "session":
            live_session_blocks.update(h.get("blocks", []))
    protected = live_parent_ids | live_session_blocks
    _history = [h for h in _history if not h.get("deleted") or h["id"] in protected]

def _load_history():
    global _history
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, encoding="utf-8") as f:
                data = json.load(f)
            migrated = []
            for item in data:
                if isinstance(item, dict) and "id" in item:
                    migrated.append(item)
                else:
                    # Мигрируем старый формат {"short":…,"full":…} или list
                    if isinstance(item, (list, tuple)):
                        short, full = item[0], item[1]
                    else:
                        short = item.get("short", "")
                        full  = item.get("full", "")
                    migrated.append({
                        "id":         str(uuid.uuid4()),
                        "created_at": _now_iso(),
                        "short":      short,
                        "full":       full,
                    })
            _history = migrated
            _cleanup_deleted()   # удаляем неиспользуемые удалённые записи при старте
    except Exception as e:
        print(f"[history] load error: {e}")

def _save_history():
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(_history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[history] save error: {e}")

def _add_to_history(text: str, parent_id: str = None) -> str:
    """Добавляет текст в историю; возвращает UUID новой записи."""
    global _current_hist_id
    short = text[:55].replace('\n', ' ')
    if len(text) > 55:
        short += '…'
    if _history and _history[0]["full"] == text:
        _current_hist_id = _history[0]["id"]
        with open("/tmp/vi_undo_debug.log", "a") as _f:
            _f.write(f"[history] DUP found, returning id={_history[0]['id']}, parent_id_arg={parent_id}\n")
        return _history[0]["id"]
    new_id = str(uuid.uuid4())
    _history.insert(0, {
        "id":         new_id,
        "created_at": _now_iso(),
        "short":      short,
        "full":       text,
        "parent_id":  parent_id,
    })
    if len(_history) > MAX_HISTORY:
        _history.pop()
    _current_hist_id = new_id
    _save_history()
    with open("/tmp/vi_undo_debug.log", "a") as _f:
        _f.write(f"[history] NEW id={new_id}, parent_id={parent_id}, text={text[:40]!r}\n")
    return new_id

_current_session_id = None   # UUID of the session currently being built in the overlay

def _upsert_session():
    """Создаёт или обновляет запись сессии для текущей overlay-сессии.
    Каждый блок также сохраняется отдельно; запись сессии ссылается на ID блоков.
    Если содержимое не изменилось, обновляет только метку времени last_used.
    """
    global _current_session_id, _current_hist_id
    block_data  = overlay.get_block_hist_data()   # [{id, text}, ...]
    if not block_data:
        return
    block_texts = [d["text"] for d in block_data]
    block_ids   = [d["id"]   for d in block_data]
    full_text   = "\n\n".join(block_texts)
    short = full_text[:55].replace('\n', ' ')
    if len(full_text) > 55:
        short += '…'

    if _current_session_id:
        for h in _history:
            if h["id"] == _current_session_id and not h.get("deleted"):
                if h.get("full") == full_text:
                    # Содержимое не изменилось — только обновляем last_used, без перезаписи в список истории
                    h["last_used"] = _now_iso()
                    _save_history()
                else:
                    # Содержимое изменилось — обновляем на месте
                    h["full"]        = full_text
                    h["short"]       = short
                    h["blocks_text"] = block_texts
                    h["blocks"]      = block_ids
                    h["last_used"]   = _now_iso()
                    _save_history()
                return

    # Нет текущей сессии — создаём новую
    new_id = str(uuid.uuid4())
    _history.insert(0, {
        "id":          new_id,
        "type":        "session",
        "created_at":  _now_iso(),
        "short":       short,
        "full":        full_text,
        "blocks_text": block_texts,
        "blocks":      block_ids,
    })
    if len(_history) > MAX_HISTORY:
        _history.pop()
    _current_session_id = new_id
    # ПРИМЕЧАНИЕ: _current_hist_id здесь НЕ устанавливается — сессии отделены от отслеживания undo
    _save_history()

def _on_session_end():
    """Вызывается когда overlay скрывается — следующее открытие начинает новую сессию."""
    global _current_session_id, _full_mode_standby
    _current_session_id = None
    _full_mode_standby = False
    _state["silent"] = True
    _state["fd_skip"] = False   # сбрасываем пропуск full_default при каждом закрытии сессии
    with _accum_lock:
        _accum_texts.clear()   # очищаем оставшиеся блоки чтобы _is_session_active() → False

def _get_history():
    """Возвращает только живые (не удалённые) элементы для отображения."""
    return [h for h in _history if not h.get("deleted")]

def _on_delete_history(ids: list):
    """Мягкое удаление элементов истории по UUID (помечает deleted=True, сохраняет в файле для ссылок на родителей)."""
    id_set = set(ids)
    for item in _history:
        if item["id"] in id_set:
            item["deleted"] = True
    _save_history()
    overlay.refresh_hist_panel()

def _on_merge_history(text: str, source_ids: list) -> str:
    """Слияние: создаёт новую запись, мягко удаляет исходные (без обновления панели)."""
    new_id = _add_to_history(text)
    id_set = set(source_ids)
    for item in _history:
        if item["id"] in id_set:
            item["deleted"] = True
    _save_history()
    # Вызов refresh_hist_panel не нужен — панель уже закрыта после слияния
    return new_id

# ── Предыдущее приложение (для корректной вставки) ───────────────────────────

def _is_excluded_app(app) -> bool:
    """True для приложений, которые никогда не должны быть целью вставки (фоновые демоны, Python, наш процесс)."""
    if app is None:
        return True
    try:
        own_pid = AppKit.NSRunningApplication.currentApplication().processIdentifier()
        if app.processIdentifier() == own_pid:
            return True
        # Исключаем фоновые процессы (activation policy 2 = Prohibited — нет UI)
        if app.activationPolicy() == AppKit.NSApplicationActivationPolicyProhibited:
            return True
        name = str(app.localizedName() or "").lower()
        bid  = str(app.bundleIdentifier() or "").lower()
        exe  = ""
        if app.executableURL():
            exe = str(app.executableURL().lastPathComponent() or "").lower()
        return "python" in name or "python" in bid or "python" in exe
    except Exception:
        return True


_prev_app            = None   # NSRunningApplication до появления overlay
_current_hist_id     = None   # UUID последнего добавленного/активного элемента истории
_active_scenario_idx = None   # индекс текущего применённого сценария (для undo)

# ── Запись / транскрипция ─────────────────────────────────────────────────────

_state = {
    "stream":      None,
    "hotkey_held": False,
    "silent":      True,    # True = тихий режим (по умолчанию); False = полный режим (Shift+⌥)
    "cancelled":   False,   # устанавливается для прерывания текущих операций
}

_audio_queue      = _qmod.Queue()   # строки wav_path, ожидающие транскрипции
_accum_texts      = []               # накопленные текстовые чанки текущей сессии
_accum_lock       = threading.Lock()
_worker_alive     = False            # True пока работает поток транскрипции
_worker_lock      = threading.Lock()
_stopping         = 0               # количество потоков _stop_and_queue, работающих в данный момент
_in_countdown     = False           # True пока показывается обратный отсчёт grace-периода
_processing_locked = False          # True во время LLM/вставки — Alt отключён

# Временная директория сессии: /tmp/hush_session_YYYYMMDD_HHMMSS/
_session_dir   = None
_chunk_counter = 0
_chunk_lock    = threading.Lock()

# Grace-период: сколько ждать после последней активности перед завершением сессии
FINALIZE_GRACE_S = 4.0

# Активация:
#   Right ⌥ одиночное    → тихий режим (запись пока зажато, транскрипция при отпускании)
#   Shift + Right ⌥      → открыть окно полного режима (ожидание); затем ⌥ одиночное записывает там
#   Двойное нажатие ⌥    → отменить текущую сессию и закрыть все overlays
_full_mode_standby = False   # True после того как Shift+⌥ открыл окно полного режима
_last_release_time = 0.0   # хранится для таймингов обработчика отпускания

_kbd = kb.Controller()


def _is_session_active() -> bool:
    """True если идёт запись, остановка, транскрипция или есть накопленный текст."""
    return (bool(_state.get("stream")) or
            _stopping > 0 or
            _worker_alive or
            not _audio_queue.empty() or
            bool(_accum_texts))


def _session_dir_cleanup():
    """Удаляет временную директорию текущей сессии."""
    global _session_dir
    import shutil
    if _session_dir:
        shutil.rmtree(_session_dir, ignore_errors=True)
        _session_dir = None


def _session_reset(clear_accum: bool = True):
    """Очищает состояние для новой сессии. clear_accum=False сохраняет блоки в полном режиме."""
    global _session_dir, _chunk_counter
    if clear_accum:
        with _accum_lock:
            _accum_texts.clear()
    _state["cancelled"] = False
    while not _audio_queue.empty():
        try: _audio_queue.get_nowait()
        except Exception: pass
    _session_dir   = None
    _chunk_counter = 0


def _cancel_all():
    """Прерывает всё: запись, очередь транскрипции, накопленный текст."""
    global _full_mode_standby
    _full_mode_standby = False
    _state["silent"]    = True   # сбрасываем в тихий режим по умолчанию после любой отмены
    _dbg("_cancel_all()")
    _state["cancelled"] = True
    _state["stream"]    = None
    transcriber.cancel()
    while not _audio_queue.empty():
        try: _audio_queue.get_nowait()
        except Exception: pass
    with _accum_lock:
        # Сохраняем всё что было транскрибировано в историю перед очисткой
        if _accum_texts:
            snapshot = " ".join(_accum_texts)
            _accum_texts.clear()
        else:
            snapshot = None
    if snapshot:
        _add_to_history(snapshot)
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
        lambda: overlay.hide(force=True))
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide_silent)


def _ensure_worker_running():
    global _worker_alive
    with _worker_lock:
        if _worker_alive:
            return
        _worker_alive = True
    threading.Thread(target=_transcription_worker, daemon=True,
                     name="hush-worker").start()


def _transcription_worker():
    """Опустошает _audio_queue; завершает сессию после FINALIZE_GRACE_S бездействия."""
    global _worker_alive, _in_countdown
    _last_activity    = time.time()
    _countdown_shown  = False   # True пока показывается анимация обратного отсчёта
    try:
        while True:
            try:
                wav_path = _audio_queue.get(timeout=0.2)
                _last_activity   = time.time()
                if _countdown_shown:
                    _countdown_shown = False
                    _in_countdown = False
                    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                        overlay.cancel_countdown_silent)
            except _qmod.Empty:
                # Очередь пуста — решаем: завершать или продолжать ждать
                if _state.get("stream") or _stopping > 0:
                    if _countdown_shown:
                        _countdown_shown = False
                        _in_countdown = False
                        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                            overlay.cancel_countdown_silent)
                    continue   # ещё идёт запись / работает stop-поток
                if not _audio_queue.empty():
                    continue   # элемент добавлен между timeout и этим местом
                if not _accum_texts:
                    # Ничего не накоплено — закрываем overlay и выходим чисто
                    if _state.get("silent"):
                        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide)
                        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide_silent)
                    return
                # Полный режим: не завершаем автоматически. Воркер выходит; _accum_texts остаётся для следующего чанка.
                # Пользователь вставляет вручную через кнопку [↵] в окне.
                if not _state.get("silent"):
                    return
                # Тихий режим: показываем обратный отсчёт затем завершаем
                if not _countdown_shown:
                    _countdown_shown = True
                    _in_countdown = True
                    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                        lambda: overlay.show_countdown_silent(FINALIZE_GRACE_S))
                if time.time() - _last_activity < FINALIZE_GRACE_S:
                    continue   # ещё в пределах grace-окна
                # Grace-период истёк → завершаем, затем всегда выходим.
                _countdown_shown = False
                _in_countdown = False
                _session_finalize()
                return

            if _state.get("cancelled"):
                continue

            # Показываем анимацию сканирования только если не идёт запись
            if not _state.get("stream"):
                if _state.get("silent"):
                    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                        overlay.show_recognizing_silent)
                else:
                    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                        overlay.show_transcribing)

            text = transcriber.transcribe(wav_path)
            _last_activity = time.time()

            if _state.get("cancelled"):
                continue

            if text:
                with _accum_lock:
                    _accum_texts.append(text)
                if _state.get("silent"):
                    accum = "\n\n".join(_accum_texts)
                    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                        lambda t=accum: overlay.update_silent_accumulation(t))
                    if not _state.get("stream"):
                        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                            overlay.show_recognizing_silent)
                else:
                    # Полный режим: передаём только НОВЫЙ чанк — show_result() его добавляет
                    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                        lambda t=text: overlay.show_result(t))
    finally:
        _worker_alive = False


def _session_finalize():
    """Всё аудио транскрибировано, grace-период истёк. Применяем сценарий или остаёмся (полный режим)."""
    global _processing_locked
    # Очищаем директорию сессии ДО блокировки — чтобы любое новое нажатие после этой точки
    # всегда создавало новую директорию (устраняет гонку с _stop_and_queue).
    _session_dir_cleanup()
    if _state.get("cancelled"):
        return
    _processing_locked = True
    try:
        _session_finalize_inner()
    finally:
        _processing_locked = False


def _session_finalize_inner():
    """Основная логика вставки/LLM, вызывается под _processing_locked."""

    with _accum_lock:
        texts = list(_accum_texts)
        _accum_texts.clear()

    if not texts:
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide)
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide_silent)
        return

    full_text = "\n\n".join(texts)

    if _state.get("silent"):
        silent_sc = overlay.get_silent_scenario()
        if silent_sc and silent_sc.get("prompt"):
            cancel_ev = threading.Event()

            def _interrupt(raw=full_text, ev=cancel_ev):
                global _processing_locked
                ev.set()
                raw_s = _strip_markdown(raw)
                subprocess.run(["pbcopy"], input=raw_s.encode("utf-8"), check=False)
                _add_to_history(raw)
                _commit_and_paste(raw_s)
                _processing_locked = False   # снимаем немедленно — не ждём пока LLM отменится

            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda fn=_interrupt, t=full_text: overlay.show_processing_silent(fn, t))

            final_text = processor.process_with_prompt(
                full_text, silent_sc["prompt"], model=silent_sc.get("model"))

            if not cancel_ev.is_set():
                final_s = _strip_markdown(final_text)
                subprocess.run(["pbcopy"], input=final_s.encode("utf-8"), check=False)
                _add_to_history(final_text)
                _commit_and_paste(final_s)
        else:
            time.sleep(0.8)
            final_s = _strip_markdown(full_text)
            subprocess.run(["pbcopy"], input=final_s.encode("utf-8"), check=False)
            _add_to_history(full_text)
            _commit_and_paste(final_s)
    else:
        subprocess.run(["pbcopy"], input=full_text.encode("utf-8"), check=False)
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
            lambda: overlay.show_result(full_text))


def _force_finalize_now():
    """Принудительно завершает немедленно во время обратного отсчёта (двойное нажатие в grace-период)."""
    global _in_countdown
    _in_countdown = False
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
        overlay.cancel_countdown_silent)
    _session_finalize()


def _force_paste_raw_now():
    """Enter во время обратного отсчёта: немедленно вставляет накопленный сырой текст, пропускает сценарий."""
    global _in_countdown
    if not _in_countdown:
        return
    _in_countdown = False
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.cancel_countdown_silent)
    with _accum_lock:
        texts = list(_accum_texts)
        _accum_texts.clear()
    if not texts:
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide_silent)
        return
    full_text = "\n\n".join(texts)
    raw = _strip_markdown(full_text)
    _add_to_history(full_text)
    _session_dir_cleanup()
    threading.Thread(
        target=lambda: _commit_and_paste(raw),
        daemon=True, name="hush-enter-raw"
    ).start()


def _on_hotkey_press(full_mode: bool = False):
    """Вызывается при нажатии Right ⌥.
    full_mode=True  (Shift+⌥) → переключить окно полного режима открыть/закрыть.
    full_mode=False (⌥ одиночное) → тихий режим или продолжить текущую сессию.
    """
    global _prev_app, _active_scenario_idx, _full_mode_standby
    if _state["hotkey_held"]:
        return

    _state["hotkey_held"] = True

    # Shift+⌥: переключить окно полного режима
    if full_mode:
        if _full_mode_standby or _is_session_active():
            # Полный режим открыт → закрываем
            _cancel_all()
            return
        # Полный режим закрыт → открываем (ожидание, запись ещё не началась)
        front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if not _is_excluded_app(front):
            _prev_app = front
            overlay.set_prev_app_icon(_prev_app)
        _active_scenario_idx = None
        _session_reset()
        _full_mode_standby = True
        _state["silent"] = False
        overlay._silent_mode = False
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide_silent)
        overlay.show_recording()
        return  # no recorder.start()

    # Обычное ⌥ (без Shift):
    front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if not _is_excluded_app(front):
        _prev_app = front
        overlay.set_prev_app_icon(_prev_app)
    _active_scenario_idx = None

    active = _is_session_active()

    # Во время обработки LLM: любое нажатие вызывает прерывание (немедленно вставляем сырой текст)
    if _processing_locked:
        fn = overlay.get_silent_interrupt_fn()
        if fn:
            threading.Thread(target=fn, daemon=True, name="hush-interrupt").start()
        return

    if _state.get("stream"):
        return  # запись уже идёт

    if _full_mode_standby:
        # ⌥ нажато пока открыто окно полного режима → начинаем запись там
        _full_mode_standby = False
        _session_reset()
        _state["silent"] = False
        overlay._silent_mode = False
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide_silent)
        overlay.show_recording()
    elif not active and _state.get("silent", True):
        # Совсем новая сессия → тихий режим
        _session_reset()
        _state["silent"] = True
        overlay._silent_mode = True
        overlay.show_recording_silent(_prev_app)
    elif not active and not _state.get("silent", True):
        # Полный режим между чанками — сохраняем существующие _accum_texts
        _session_reset(clear_accum=False)
        _state["silent"] = False
        overlay._silent_mode = False
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide_silent)
        overlay.show_recording()
    else:
        # Продолжаем запись в текущей активной сессии (сохраняем режим)
        if _state.get("silent"):
            overlay.show_recording_silent(_prev_app)
        else:
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide_silent)
            overlay.show_recording()

    _state["stream"] = recorder.start(on_chunk=overlay.update_waveform)


def _on_hotkey_release():
    global _last_release_time
    if not _state["hotkey_held"]:
        return
    _state["hotkey_held"] = False
    _last_release_time = time.time()

    stream = _state.get("stream")
    if not stream:
        return

    def _stop_and_queue():
        global _stopping, _chunk_counter, _session_dir
        _stopping += 1   # предотвращаем преждевременное завершение пока идёт обработка
        try:
            wav_path, _ = recorder.stop(stream)
            _state["stream"] = None
            _dbg(f"_stop_and_queue: wav={bool(wav_path)}, cancelled={_state.get('cancelled')}")
            if _state.get("cancelled") or not wav_path:
                return

            # Ленивое создание: создаём директорию сессии на первом чанке
            if not _session_dir:
                ts = time.strftime("%Y%m%d_%H%M%S")
                new_dir = f"/tmp/hush_session_{ts}"
                os.makedirs(new_dir, exist_ok=True)
                _session_dir = new_dir
                _chunk_counter = 0

            # Перемещаем чанк в директорию сессии с последовательным именем
            with _chunk_lock:
                _chunk_counter += 1
                seq = _chunk_counter
            dest = os.path.join(_session_dir, f"chunk_{seq:04d}.wav")
            try:
                os.rename(wav_path, dest)
            except Exception:
                dest = wav_path  # запасной вариант: используем исходный UUID путь

            _audio_queue.put(dest)

            # Сразу показываем анимацию сканирования при отпускании (запись только что остановилась)
            if _state.get("silent"):
                AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                    overlay.show_recognizing_silent)
            else:
                AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                    overlay.show_transcribing)

            _ensure_worker_running()
        finally:
            _stopping -= 1  # всегда декрементируем, даже при ошибке

    threading.Thread(target=_stop_and_queue, daemon=True, name="hush-stopper").start()

# ── Сценарии ──────────────────────────────────────────────────────────────────

def _undo_last_scenario():
    """Откатывает overlay к родителю текущего элемента истории."""
    global _active_scenario_idx, _current_hist_id
    def _log(msg):
        with open("/tmp/vi_undo_debug.log", "a") as f:
            f.write(msg + "\n")
    _log(f"[undo] _current_hist_id={_current_hist_id} history_len={len(_history)}")
    current = next((h for h in _history if h["id"] == _current_hist_id), None)
    _log(f"[undo] current id={current.get('id') if current else None} parent_id={current.get('parent_id') if current else None}")
    if not current or not current.get("parent_id"):
        _log(f"[undo] ABORT: no current or no parent_id")
        return
    parent = next((h for h in _history if h["id"] == current["parent_id"]), None)
    _log(f"[undo] parent={'found' if parent else 'NOT FOUND'}")
    if not parent:
        _log(f"[undo] ABORT: parent not found in history")
        return
    _current_hist_id     = parent["id"]
    _active_scenario_idx = None
    overlay.set_active_scenario(None)
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
        lambda: overlay.show_scenario_result(parent["full"])
    )


def _on_scenario(sc: dict, sc_idx: int = 0):
    global _active_scenario_idx

    text = overlay.get_text()
    if not text:
        return

    # Тот же сценарий нажат повторно → undo (восстанавливаем родительский контекст)
    if _active_scenario_idx == sc_idx:
        _undo_last_scenario()
        return

    prompt = sc.get("prompt", "")
    if not prompt.strip():
        _commit_and_paste(text)
        return

    cancel_ev = threading.Event()

    # Всегда добавляем исходный текст в историю и сохраняем его ID как parent для undo
    parent_id = _add_to_history(text)

    def _interrupt():
        cancel_ev.set()
        # Немедленно восстанавливаем UI, не дожидаясь завершения LLM
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
            lambda: overlay.show_scenario_result(text, hist_id=parent_id)
        )

    overlay.show_processing(sc.get("name", ""), sc_idx=sc_idx, interrupt_fn=_interrupt)

    def _do():
        global _active_scenario_idx
        result = processor.process_with_prompt(text, prompt, model=sc.get("model")).strip()
        if cancel_ev.is_set():
            # _interrupt уже восстановил UI; здесь ничего делать не нужно
            return
        result_id = _add_to_history(result, parent_id=parent_id)
        subprocess.run(["pbcopy"], input=result.encode("utf-8"), check=False)
        _active_scenario_idx = sc_idx
        def on_main():
            overlay.set_active_scenario(sc_idx)
            overlay.show_scenario_result(result, hist_id=result_id)
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(on_main)

    threading.Thread(target=_do, daemon=True).start()

def _activate_prev_app():
    """Активирует предыдущее приложение по bundle ID (наиболее надёжный способ для любых приложений)."""
    bundle = _prev_app.bundleIdentifier() if _prev_app else None
    if bundle:
        subprocess.run(
            ["osascript", "-e", f'tell application id "{bundle}" to activate'],
            check=False
        )


def _commit_and_paste(text: str):
    """Скрываем оверлей, активируем предыдущее приложение, вставляем текст.
    Runs synchronously in the caller's thread (worker) so _processing_locked stays
    True until paste is fully done — prevents race where hide() kills the new session."""
    prev_app_ref = _prev_app

    # Сначала скрываем overlay в главном потоке
    def on_main():
        if prev_app_ref:
            prev_app_ref.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        overlay.hide()
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(on_main)

    try:
        time.sleep(0.35)
        _activate_prev_app()   # запасной вариант по bundle-ID если activateWithOptions_ недостаточно
        time.sleep(0.35)
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
        time.sleep(0.15)

        try:
            import ApplicationServices as _AS
            ax_ok = _AS.AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": False})
        except Exception:
            ax_ok = True
        _dbg(f"paste: AX trusted={ax_ok}, firing Cmd+V")

        if ax_ok:
            _kbd.press(kb.Key.cmd)
            _kbd.tap('v')
            _kbd.release(kb.Key.cmd)
            _dbg("paste: pynput Cmd+V done")
            time.sleep(0.05)
            _kbd.tap(' ')   # trailing space so next dictation joins cleanly
        else:
            _dbg("paste: ПРОПУЩЕНО — нет разрешения Accessibility. Текст в буфере обмена, используйте Cmd+V вручную.")
    except Exception as e:
        _dbg(f"paste ERROR: {e}")

def _on_history_load(item_id: str):
    """Вызывается когда пользователь загружает элемент(ы) из панели истории в редактор."""
    global _current_hist_id, _active_scenario_idx, _current_session_id
    _current_hist_id     = item_id
    _active_scenario_idx = None
    # Если загружается сессия, связываемся с ней — добавления будут ОБНОВЛЯТЬ её, а не создавать дубликаты
    loaded = next((h for h in _history if h and h.get("id") == item_id), None)
    if loaded and loaded.get("type") == "session":
        _current_session_id = item_id
    else:
        _current_session_id = None


_CODE_LANG_LABELS = {
    'javascript': 'Код на JavaScript', 'js':   'Код на JavaScript',
    'typescript': 'Код на TypeScript', 'ts':   'Код на TypeScript',
    'python':     'Код на Python',     'py':   'Код на Python',
    'bash':       'Команды Bash',      'sh':   'Команды Bash',
    'shell':      'Команды Bash',      'zsh':  'Команды Bash',
    'html':       'Код HTML',          'css':  'Код CSS',
    'sql':        'Код SQL',           'json': 'Данные JSON',
    'yaml':       'Конфигурация YAML', 'yml':  'Конфигурация YAML',
    'xml':        'Код XML',           'go':   'Код на Go',
    'rust':       'Код на Rust',       'java': 'Код на Java',
    'c':          'Код на C',          'cpp':  'Код на C++',
    'c++':        'Код на C++',        'cs':   'Код на C#',
    'kotlin':     'Код на Kotlin',     'swift':'Код на Swift',
    'ruby':       'Код на Ruby',       'rb':   'Код на Ruby',
    'php':        'Код PHP',           'r':    'Код на R',
    'text':       'Блок текста',       'txt':  'Блок текста',
    'plain':      'Блок текста',       'md':   'Блок Markdown',
    'markdown':   'Блок Markdown',     'diff': 'Изменения (diff)',
    'dockerfile': 'Dockerfile',        'toml': 'Конфигурация TOML',
    'ini':        'Конфигурация INI',  'env':  'Переменные окружения',
}

def _code_label(lang: str) -> str:
    key = lang.strip().lower()
    return _CODE_LANG_LABELS.get(key, f'Код {lang.upper()}' if lang else 'Блок кода')


def _strip_markdown(text: str) -> str:
    """Удаляет все маркеры Markdown, возвращает чистый читаемый plain text."""
    lines    = text.split('\n')
    out      = []
    in_code  = False

    def _inline_strip(s: str) -> str:
        s = re.sub(r'\\([\\`*_{}\[\]()#+\-.!|])', r'\1', s)   # escape-последовательности
        s = re.sub(r'\*{3}(.+?)\*{3}', r'\1', s)              # жирный+курсив
        s = re.sub(r'\*\*(.+?)\*\*',   r'\1', s)              # жирный
        s = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'\1', s)   # курсив *
        s = re.sub(r'(?<!_)\b_([^_\n]+)_\b(?!_)',  r'\1', s)  # курсив _
        s = re.sub(r'~~(.+?)~~', r'\1', s)                    # зачёркнутый
        s = re.sub(r'`([^`]+)`',  r'\1', s)                   # inline код
        s = re.sub(r'!\[([^\]]*)\]\([^\)]*\)', r'\1', s)      # картинки → alt
        s = re.sub(r'\[([^\]]+)\]\([^\)]*\)',  r'\1', s)      # ссылки → текст
        s = re.sub(r'<(https?://[^>]+)>', r'\1', s)           # авто-ссылки
        s = re.sub(r'\[\^[^\]]+\]', '', s)                    # ссылки на сноски
        return s

    for line in lines:
        stripped = line.strip()

        # Code fences — заменяем ``` читаемой меткой, содержимое кода оставляем чистым
        if stripped.startswith('```'):
            if not in_code:
                in_code = True
                lang = stripped[3:].strip()
                out.append(_code_label(lang) + ':')
            else:
                in_code = False
                out.append('')  # пустая строка после блока кода
            continue
        if in_code:
            out.append(line)
            continue

        # GFM alert-маркеры — пропускаем строку типа, сохраняем тело
        if re.match(r'^>\s*\[!(NOTE|WARNING|TIP|IMPORTANT|CAUTION)\]', stripped, re.I):
            continue

        # Горизонтальные разделители → пустая строка
        if re.match(r'^[-*_]{3,}\s*$', stripped):
            out.append('')
            continue

        # Определения сносок
        if re.match(r'^\[\^[^\]]+\]:', stripped):
            continue

        # Цитаты — убираем все префиксы > (обрабатывает любую глубину вложенности)
        if stripped.startswith('>'):
            line = re.sub(r'^(>\s*)+', '', stripped)

        # Заголовки — убираем #
        h_m = re.match(r'^#{1,6}\s+(.*)', line)
        if h_m:
            line = h_m.group(1)

        # Таблицы — извлекаем содержимое ячеек, объединяем через |
        if stripped.count('|') >= 2:
            if re.match(r'^\|?[\s\-:|]+(\|[\s\-:|]+)+\|?$', stripped):
                continue  # строка-разделитель
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            line = '  |  '.join(_inline_strip(c) for c in cells if c)
            out.append(line)
            continue

        # Список определений `: definition`
        dl_m = re.match(r'^:\s+(.*)', line)
        if dl_m:
            out.append('  ' + _inline_strip(dl_m.group(1)))
            continue

        # Чекбоксы
        line = re.sub(r'^(\s*)- \[[xX]\]\s*', r'\1[x] ', line)
        line = re.sub(r'^(\s*)- \[ \]\s*',    r'\1[ ] ', line)
        # Списки — сохраняем дефис и отступ, убираем пустые маркеры в конце
        line = re.sub(r'^(\s*)[-*+]\s+', r'\1- ', line)

        # Inline-форматирование
        line = _inline_strip(line)
        # Жёсткий перенос строки через два пробела → убираем trailing пробелы
        line = line.rstrip()
        out.append(line)

    result = '\n'.join(out)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def _on_copy(mode: str = "raw"):
    """Ctrl+Enter → копирует контекст в буфер обмена, сохраняет в историю, затем очищает overlay."""
    text = overlay.get_text()
    if not text:
        return
    # Сохраняем в историю перед очисткой
    if overlay.get_block_texts():
        _upsert_session()
    else:
        current = next((h for h in _history if h.get("id") == _current_hist_id), None)
        if not (current and current["full"] == text):
            _add_to_history(text, parent_id=_current_hist_id)
    # Копируем в буфер обмена (с MD-маркерами или без)
    text_to_copy = text if mode == "md" else _strip_markdown(text)
    subprocess.run(["pbcopy"], input=text_to_copy.encode("utf-8"), check=False)
    # Очищаем контекстное окно
    overlay.hide(force=True)
    # Возвращаем фокус предыдущему приложению по bundle ID (localizedName ненадёжен для некоторых приложений)
    def _refocus():
        time.sleep(0.3)
        _activate_prev_app()
    threading.Thread(target=_refocus, daemon=True).start()


def _on_paste(mode: str = "raw"):
    """Shift+Enter → raw paste (no scenario); [Отправить] → apply full_default if set; Alt+Shift+Enter → paste MD."""
    global _full_mode_standby
    text = overlay.get_text()
    if not text:
        return
    if overlay.get_block_texts():
        # Блоки присутствуют → upsert сессии (no-op если содержимое не изменилось, обновляет last_used)
        _upsert_session()
    else:
        # Нет блоков — обычный текст _tv → обычная запись в историю
        current = next((h for h in _history if h.get("id") == _current_hist_id), None)
        if not (current and current["full"] == text):
            _add_to_history(text, parent_id=_current_hist_id)

    # В полном режиме: применяем сценарий по умолчанию только на Shift+Enter (не на кнопку [↵])
    full_sc = overlay.get_full_default_scenario()
    if (not _state.get("silent") and full_sc and full_sc.get("prompt")
            and overlay.get_active_sc() is None
            and not _state.get("fd_skip")
            and mode in ("shift_enter", "md")):
        def _apply_and_paste(sc=full_sc, raw=text, m=mode):
            cancel_ev = threading.Event()
            def _interrupt():
                cancel_ev.set()
                _state["fd_skip"] = True   # следующий [Отправить] вставит сырой текст без full_default цикла
                AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                    overlay.restore_ready)
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda: overlay.show_processing(sc.get("name", ""), interrupt_fn=_interrupt))
            result = processor.process_with_prompt(raw, sc["prompt"], model=sc.get("model"))
            if cancel_ev.is_set():
                return
            result = result.strip()
            _add_to_history(result)
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda r=result: overlay.show_scenario_result(r))
            time.sleep(0.6)
            # После вставки из полного режима: сбрасываем в тихий режим для следующей сессии
            if not _state.get("silent"):
                _state["silent"] = True
                _full_mode_standby = False
                with _accum_lock:
                    _accum_texts.clear()
            final = result if m == "md" else _strip_markdown(result)
            subprocess.run(["pbcopy"], input=final.encode("utf-8"), check=False)
            _commit_and_paste(final)
        threading.Thread(target=_apply_and_paste, daemon=True, name="hush-fd-paste").start()
        return

    # После вставки из полного режима: сбрасываем в тихий режим для следующей сессии
    if not _state.get("silent"):
        _state["silent"] = True
        _full_mode_standby = False
        with _accum_lock:
            _accum_texts.clear()

    if mode == "md":
        text_to_paste = text
    else:
        text_to_paste = _strip_markdown(text)

    subprocess.run(["pbcopy"], input=text_to_paste.encode("utf-8"), check=False)
    # _commit_and_paste блокирует через sleep — должен работать вне главного потока чтобы не заморозить UI
    threading.Thread(target=_commit_and_paste, args=(text_to_paste,),
                     daemon=True, name="hush-paste").start()

# ── Хоткей ────────────────────────────────────────────────────────────────────

def _setup_hotkey():
    pressed = set()
    shift_keys = {kb.Key.shift, kb.Key.shift_r, kb.Key.shift_l}

    def on_press(key):
        if key in shift_keys:
            pressed.add(key)
        elif key == kb.Key.enter and _in_countdown:
            threading.Thread(target=_force_paste_raw_now, daemon=True,
                             name="hush-enter-raw").start()
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

    listener = kb.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()
    return listener

# ── Keep-alive таймер (фикс Ctrl+C) ──────────────────────────────────────────

class _KATgt(AppKit.NSObject):
    """Пустой target таймера — держит Python signal handler живым внутри NSApp.run()."""
    def ping_(self, t): pass

def _setup_keepalive():
    tgt = _KATgt.alloc().init()
    AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.25, tgt, _KATgt.ping_, None, True
    )


def _start_provider_monitor():
    """Фоновый поток: повторно проверяет провайдеров каждые 30с пока хоть один недоступен."""
    import threading, time

    def _loop():
        while True:
            time.sleep(30)
            if any(v is False for v in provider_config._status.values()):
                provider_config.probe_all()

    threading.Thread(target=_loop, daemon=True, name="hush-provider-monitor").start()


# ── Workspace observer — динамический трекинг целевого приложения ─────────────

class _AppObserver(AppKit.NSObject):
    """Отслеживает NSWorkspaceDidActivateApplicationNotification для определения цели вставки."""
    def appActivated_(self, notification):
        global _prev_app
        info = notification.userInfo()
        if info is None:
            return
        app = info.get("NSWorkspaceApplicationKey")
        if _is_excluded_app(app):
            return
        _prev_app = app
        overlay.set_prev_app_icon(app)


_app_observer = None   # сохраняем сильную ссылку


class _SleepObserver(AppKit.NSObject):
    """Обрабатывает системный сон/пробуждение для переинициализации PortAudio после пробуждения."""

    def systemWillSleep_(self, notification):
        """При засыпании: чисто прерываем любую активную запись."""
        stream = _state.get("stream")
        if stream:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            _state["stream"] = None

    def systemDidWake_(self, notification):
        """При пробуждении: переинициализируем PortAudio чтобы sounddevice снова работал."""
        import sounddevice as sd
        try:
            sd._terminate()
        except Exception:
            pass
        try:
            sd._initialize()
        except Exception:
            pass
        _dbg("systemDidWake_: PortAudio reinitialized")


_sleep_observer = None


def _setup_app_observer():
    global _app_observer, _sleep_observer
    _app_observer = _AppObserver.alloc().init()
    ws = AppKit.NSWorkspace.sharedWorkspace()
    ws.notificationCenter().addObserver_selector_name_object_(
        _app_observer,
        objc.selector(_app_observer.appActivated_, selector=b"appActivated:"),
        AppKit.NSWorkspaceDidActivateApplicationNotification,
        None,
    )
    _sleep_observer = _SleepObserver.alloc().init()
    ws.notificationCenter().addObserver_selector_name_object_(
        _sleep_observer,
        objc.selector(_sleep_observer.systemWillSleep_, selector=b"systemWillSleep:"),
        AppKit.NSWorkspaceWillSleepNotification,
        None,
    )
    ws.notificationCenter().addObserver_selector_name_object_(
        _sleep_observer,
        objc.selector(_sleep_observer.systemDidWake_, selector=b"systemDidWake:"),
        AppKit.NSWorkspaceDidWakeNotification,
        None,
    )


# ── Запуск ────────────────────────────────────────────────────────────────────

def _check_accessibility():
    """Проверяет разрешение AX и показывает запрос если оно отсутствует. pynput требует его."""
    try:
        import ApplicationServices
        trusted = ApplicationServices.AXIsProcessTrustedWithOptions(
            {"AXTrustedCheckOptionPrompt": True}
        )
        _dbg(f"AX trusted: {trusted}")
        if not trusted:
            print("⚠️  Нет разрешения Accessibility. Откройте Системные настройки → "
                  "Конфиденциальность → Accessibility и добавьте HUSH (или python3).")
    except Exception as e:
        _dbg(f"AX check error: {e}")


def _first_run_setup():
    """При первой установке: копирует parakeet-cli и модели из bundle в стабильные пути.
    Стабильные пути сохраняют CoreML кэш при пересборках/обновлениях приложения.
    Последующие запуски пропускают это (пути уже существуют)."""
    import shutil
    import config as _cfg

    # parakeet-cli → ~/.local/bin/parakeet-cli (копируем бинарник)
    stable_bin = os.path.expanduser("~/.local/bin/parakeet-cli")
    if not os.path.isfile(stable_bin) and os.path.isfile(_cfg._bundle_parakeet):
        os.makedirs(os.path.dirname(stable_bin), exist_ok=True)
        shutil.copy2(_cfg._bundle_parakeet, stable_bin)
        os.chmod(stable_bin, 0o755)
        _dbg("first-run: copied parakeet-cli to ~/.local/bin/")

    # модели → ~/.local/share/hush/models/<model>
    stable_models = os.path.expanduser("~/.local/share/hush/models")
    stable_model  = os.path.join(stable_models, "parakeet-tdt-0.6b-v3-coreml")
    if not os.path.exists(stable_model) and os.path.isdir(_cfg._bundle_models):
        os.makedirs(stable_models, exist_ok=True)
        _dbg("first-run: copying models (~400 MB) to ~/.local/share/hush/ …")
        shutil.copytree(_cfg._bundle_models, stable_model)
        _dbg("first-run: models copied")


class _AppDelegate(AppKit.NSObject):
    """Минимальный NSApplicationDelegate чтобы macOS запускал applicationDidFinishLaunching:
    до того как Scene lifecycle пытается создать окна. Без этого делегата
    NSSceneStatusItem получает окно нулевой высоты при запуске через `open .app`."""

    def applicationDidFinishLaunching_(self, notification):
        _first_run_setup()          # копируем бинарник+модели в стабильные пути (один раз)
        _check_accessibility()
        _load_history()
        overlay.init(
            _on_scenario,
            on_history_callback=_get_history,
            on_paste_callback=_on_paste,
            on_copy_callback=_on_copy,
            on_history_delete_callback=_on_delete_history,
            on_history_load_callback=_on_history_load,
            on_history_merge_callback=_on_merge_history,
            on_add_history_callback=_add_to_history,
            on_update_session_callback=_upsert_session,
            on_session_end_callback=_on_session_end,
        )
        overlay.set_undo_scenario_callback(_undo_last_scenario)
        _setup_hotkey()
        _setup_keepalive()
        _setup_app_observer()
        # Асинхронно проверяем всех LLM провайдеров; обновляем точки статуса в overlay когда готово
        provider_config.add_status_callback(
            lambda: AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                overlay.update_provider_status))
        provider_config.probe_all()
        _start_provider_monitor()
        transcriber.warm_up()
        print("Voice Input запущен. Right ⌥ — запись, Right ⌥ × 2 — история. Ctrl+C — выход.")

    def applicationShouldTerminateAfterLastWindowClosed_(self, sender):
        return False


def main():
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    signal.signal(signal.SIGINT,  lambda *_: app.terminate_(None))
    signal.signal(signal.SIGTERM, lambda *_: app.terminate_(None))

    delegate = _AppDelegate.alloc().init()
    app.setDelegate_(delegate)

    app.run()

if __name__ == "__main__":
    main()
