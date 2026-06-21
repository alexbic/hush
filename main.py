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

# ── Single-instance guard via lock file ──────────────────────────────────────
_LOCK_FILE = "/tmp/hush.lock"
try:
    import fcntl
    _lock_fd = open(_LOCK_FILE, "w")
    fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _lock_fd.write(str(os.getpid()))
    _lock_fd.flush()
except (IOError, OSError):
    sys.exit(0)   # another instance is already running — exit silently

import recorder
import transcriber

_DBG_LOG = "/tmp/vi_debug.log"
def _dbg(msg):
    try:
        with open(_DBG_LOG, "a") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass
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
    """Remove soft-deleted items not referenced by any live entry (as parent_id or session block)."""
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
                    # Migrate old format {"short":…,"full":…} or list
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
            _cleanup_deleted()   # purge unreferenced deleted entries on startup
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
    """Add text to history; return the new entry's UUID."""
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
    """Create or update the session entry for the current overlay session.
    Each block is also saved individually; the session entry references block IDs.
    If content is unchanged, only updates last_used timestamp.
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
                    # Content unchanged — just record last_used, no rewrite to history list
                    h["last_used"] = _now_iso()
                    _save_history()
                else:
                    # Content changed — update in place
                    h["full"]        = full_text
                    h["short"]       = short
                    h["blocks_text"] = block_texts
                    h["blocks"]      = block_ids
                    h["last_used"]   = _now_iso()
                    _save_history()
                return

    # No current session — create new one
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
    # NOTE: _current_hist_id is NOT set here — sessions are separate from undo tracking
    _save_history()

def _on_session_end():
    """Called when overlay hides — next open starts a fresh session."""
    global _current_session_id, _full_mode_standby
    _current_session_id = None
    _full_mode_standby = False
    _state["silent"] = True   # after any window close, default back to silent mode

def _get_history():
    """Return only live (non-deleted) items for display."""
    return [h for h in _history if not h.get("deleted")]

def _on_delete_history(ids: list):
    """Soft-delete history items by UUID (mark deleted=True, keep in file for parent refs)."""
    id_set = set(ids)
    for item in _history:
        if item["id"] in id_set:
            item["deleted"] = True
    _save_history()
    overlay.refresh_hist_panel()

def _on_merge_history(text: str, source_ids: list) -> str:
    """Merge: create new entry, soft-delete sources (no panel refresh)."""
    new_id = _add_to_history(text)
    id_set = set(source_ids)
    for item in _history:
        if item["id"] in id_set:
            item["deleted"] = True
    _save_history()
    # No refresh_hist_panel call — panel is already closed after merge
    return new_id

# ── Предыдущее приложение (для корректной вставки) ───────────────────────────

def _is_excluded_app(app) -> bool:
    """True for apps we never want as paste target (background daemons, Python, our own process)."""
    if app is None:
        return True
    try:
        own_pid = AppKit.NSRunningApplication.currentApplication().processIdentifier()
        if app.processIdentifier() == own_pid:
            return True
        # Exclude background-only processes (activation policy 2 = Prohibited — no UI)
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


_prev_app            = None   # NSRunningApplication before overlay appeared
_current_hist_id     = None   # UUID of the most recently added/active history item
_active_scenario_idx = None   # index of currently applied scenario (for undo)

# ── Запись / транскрипция ─────────────────────────────────────────────────────

_state = {
    "stream":      None,
    "hotkey_held": False,
    "silent":      True,    # True = silent mode (default); False = full mode (Shift+⌥)
    "cancelled":   False,   # set to abort in-flight operations
}

_audio_queue      = _qmod.Queue()   # wav_path strings pending transcription
_accum_texts      = []               # text chunks accumulated this session
_accum_lock       = threading.Lock()
_worker_alive     = False            # True while transcription worker thread is running
_worker_lock      = threading.Lock()
_stopping         = 0               # count of _stop_and_queue threads currently running
_in_countdown     = False           # True while grace-period countdown is showing
_processing_locked = False          # True during LLM/paste — Alt is disabled

# Per-session temp directory: /tmp/hush_session_YYYYMMDD_HHMMSS/
_session_dir   = None
_chunk_counter = 0
_chunk_lock    = threading.Lock()

# Grace period: how long to wait after last activity before finalizing session
FINALIZE_GRACE_S = 4.0

# Activation:
#   Right ⌥ alone    → silent mode (record while held, transcribe on release)
#   Shift + Right ⌥  → open full-mode window (standby); then ⌥ alone records there
#   Double-tap ⌥     → cancel current session and close all overlays
_full_mode_standby = False   # True after Shift+⌥ opened the full-mode window
_last_release_time = 0.0
DOUBLE_TAP_WINDOW  = 0.40    # seconds between release and next press to count as double-tap

_kbd = kb.Controller()


def _is_session_active() -> bool:
    """True if recording, stopping, transcribing, or accumulated text exists."""
    return (bool(_state.get("stream")) or
            _stopping > 0 or
            _worker_alive or
            not _audio_queue.empty() or
            bool(_accum_texts))


def _session_dir_cleanup():
    """Remove current session temp directory."""
    global _session_dir
    import shutil
    if _session_dir:
        shutil.rmtree(_session_dir, ignore_errors=True)
        _session_dir = None


def _session_reset(clear_accum: bool = True):
    """Clear state for a fresh session. Set clear_accum=False to keep blocks in full mode."""
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
    """Abort everything: recording, transcription queue, accumulated text."""
    global _full_mode_standby
    _full_mode_standby = False
    _state["silent"]    = True   # reset to default silent mode after any cancel
    _dbg("_cancel_all()")
    _state["cancelled"] = True
    _state["stream"]    = None
    transcriber.cancel()
    while not _audio_queue.empty():
        try: _audio_queue.get_nowait()
        except Exception: pass
    with _accum_lock:
        # Save whatever was transcribed to history before discarding
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
    """Drain _audio_queue; finalize session after FINALIZE_GRACE_S of inactivity."""
    global _worker_alive, _in_countdown
    _last_activity    = time.time()
    _countdown_shown  = False   # True while countdown animation is visible
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
                # Nothing in queue — decide whether to finalize or keep waiting
                if _state.get("stream") or _stopping > 0:
                    if _countdown_shown:
                        _countdown_shown = False
                        _in_countdown = False
                        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                            overlay.cancel_countdown_silent)
                    continue   # still recording / stop thread running
                if not _audio_queue.empty():
                    continue   # item added between timeout and here
                if not _accum_texts:
                    # Nothing accumulated — close overlay and exit cleanly
                    if _state.get("silent"):
                        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide)
                        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide_silent)
                    return
                # Full mode: don't auto-finalize. Worker exits; _accum_texts stays for next chunk.
                # User pastes manually via the [↵] button in the window.
                if not _state.get("silent"):
                    return
                # Silent mode: show countdown then finalize
                if not _countdown_shown:
                    _countdown_shown = True
                    _in_countdown = True
                    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                        lambda: overlay.show_countdown_silent(FINALIZE_GRACE_S))
                if time.time() - _last_activity < FINALIZE_GRACE_S:
                    continue   # still within grace window
                # Grace period expired → finalize, then always exit.
                _countdown_shown = False
                _in_countdown = False
                _session_finalize()
                return

            if _state.get("cancelled"):
                continue

            # Show scan animation only if not currently recording
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
                    # Full mode: pass only the NEW chunk — show_result() appends it
                    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                        lambda t=text: overlay.show_result(t))
    finally:
        _worker_alive = False


def _session_finalize():
    """All audio transcribed, grace period expired. Apply scenario or stay (full mode)."""
    global _processing_locked
    # Clean up session dir BEFORE locking — so any new press after this point
    # always creates a fresh session dir (eliminates race with _stop_and_queue).
    _session_dir_cleanup()
    if _state.get("cancelled"):
        return
    _processing_locked = True
    try:
        _session_finalize_inner()
    finally:
        _processing_locked = False


def _session_finalize_inner():
    """Core paste/LLM logic called under _processing_locked."""

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
                _processing_locked = False   # release immediately — don't wait for LLM to cancel

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
    """Force immediate finalize during countdown (double-tap during grace period)."""
    global _in_countdown
    _in_countdown = False
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
        overlay.cancel_countdown_silent)
    _session_finalize()


def _force_paste_raw_now():
    """Enter during countdown: paste accumulated raw text immediately, skip scenario."""
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
    """Called when Right ⌥ is pressed.
    full_mode=True  (Shift held) → open full-mode window without starting recording.
    full_mode=False → silent or continue current session mode.
    Double-tap ⌥    → cancel everything and close overlays.
    """
    global _prev_app, _active_scenario_idx, _full_mode_standby
    if _state["hotkey_held"]:
        return
    if overlay.is_editing_scenario():
        return

    # Double-tap: cancel current session (works in any mode)
    if not full_mode and time.time() - _last_release_time < DOUBLE_TAP_WINDOW:
        _state["hotkey_held"] = True   # set so release handler clears it
        _cancel_all()
        return

    _state["hotkey_held"] = True

    front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if not _is_excluded_app(front):
        _prev_app = front
        overlay.set_prev_app_icon(_prev_app)
    _active_scenario_idx = None

    active = _is_session_active()

    # During LLM processing: any press triggers interrupt (paste raw immediately)
    if _processing_locked:
        fn = overlay.get_silent_interrupt_fn()
        if fn:
            threading.Thread(target=fn, daemon=True, name="hush-interrupt").start()
        return

    if _state.get("stream"):
        return  # already recording

    if full_mode and not active and not _full_mode_standby:
        # Shift+⌥: open full-mode window (standby) — do NOT start recording.
        # Release of this ⌥ tap will be ignored (stream=None → _on_hotkey_release exits early).
        # Next ⌥ press (no Shift needed) will record inside the full-mode window.
        _session_reset()
        _full_mode_standby = True
        _state["silent"] = False
        overlay._silent_mode = False
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide_silent)
        overlay.show_recording()
        return  # no recorder.start()

    if _full_mode_standby:
        # ⌥ pressed while full-mode window is open → start recording there
        _full_mode_standby = False
        _session_reset()
        _state["silent"] = False
        overlay._silent_mode = False
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide_silent)
        overlay.show_recording()
    elif not active and _state.get("silent", True):
        # Truly fresh → silent mode
        _session_reset()
        _state["silent"] = True
        overlay._silent_mode = True
        overlay.show_recording_silent(_prev_app)
    elif not active and not _state.get("silent", True):
        # Full mode between chunks — keep existing _accum_texts
        _session_reset(clear_accum=False)
        _state["silent"] = False
        overlay._silent_mode = False
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide_silent)
        overlay.show_recording()
    else:
        # Resume recording in current active session (preserve mode)
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
        _stopping += 1   # prevent premature finalize while we're processing
        try:
            wav_path, _ = recorder.stop(stream)
            _state["stream"] = None
            _dbg(f"_stop_and_queue: wav={bool(wav_path)}, cancelled={_state.get('cancelled')}")
            if _state.get("cancelled") or not wav_path:
                return

            # Lazy: create session dir on first chunk of this session
            if not _session_dir:
                ts = time.strftime("%Y%m%d_%H%M%S")
                new_dir = f"/tmp/hush_session_{ts}"
                os.makedirs(new_dir, exist_ok=True)
                _session_dir = new_dir
                _chunk_counter = 0

            # Move chunk to session dir with sequential name
            with _chunk_lock:
                _chunk_counter += 1
                seq = _chunk_counter
            dest = os.path.join(_session_dir, f"chunk_{seq:04d}.wav")
            try:
                os.rename(wav_path, dest)
            except Exception:
                dest = wav_path  # fallback: use original UUID path

            _audio_queue.put(dest)

            # Show scan animation immediately on release (recording just stopped)
            if _state.get("silent"):
                AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                    overlay.show_recognizing_silent)
            else:
                AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                    overlay.show_transcribing)

            _ensure_worker_running()
        finally:
            _stopping -= 1  # always decrement, even on error

    threading.Thread(target=_stop_and_queue, daemon=True, name="hush-stopper").start()

# ── Сценарии ──────────────────────────────────────────────────────────────────

def _undo_last_scenario():
    """Revert overlay to the parent of the current history item."""
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

    # Same scenario clicked again → undo (restore parent context)
    if _active_scenario_idx == sc_idx:
        _undo_last_scenario()
        return

    prompt = sc.get("prompt", "")
    if not prompt.strip():
        _commit_and_paste(text)
        return

    cancel_ev = threading.Event()

    # Always add original text to history and capture its ID as parent for undo
    parent_id = _add_to_history(text)

    def _interrupt():
        cancel_ev.set()
        # Immediately restore UI without waiting for LLM to finish
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
            lambda: overlay.show_scenario_result(text, hist_id=parent_id)
        )

    overlay.show_processing(sc.get("name", ""), sc_idx=sc_idx, interrupt_fn=_interrupt)

    def _do():
        global _active_scenario_idx
        result = processor.process_with_prompt(text, prompt, model=sc.get("model")).strip()
        if cancel_ev.is_set():
            # _interrupt already restored the UI; nothing to do here
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
    """Activate the previously focused app using bundle ID (most reliable across all apps)."""
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

    # Hide overlay on main thread first
    def on_main():
        if prev_app_ref:
            prev_app_ref.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        overlay.hide()
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(on_main)

    try:
        time.sleep(0.35)
        _activate_prev_app()   # bundle-ID backup in case activateWithOptions_ insufficient
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
            _dbg("paste: SKIPPED — no Accessibility permission. Text is in clipboard, use Cmd+V manually.")
    except Exception as e:
        _dbg(f"paste ERROR: {e}")

def _on_history_load(item_id: str):
    """Called when user loads item(s) from history panel into the editor."""
    global _current_hist_id, _active_scenario_idx, _current_session_id
    _current_hist_id     = item_id
    _active_scenario_idx = None
    # If loading a session, link to it — additions will UPDATE it, not create duplicates
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
    """Remove all Markdown markers, returning clean readable plain text."""
    lines    = text.split('\n')
    out      = []
    in_code  = False

    def _inline_strip(s: str) -> str:
        s = re.sub(r'\\([\\`*_{}\[\]()#+\-.!|])', r'\1', s)   # escape sequences
        s = re.sub(r'\*{3}(.+?)\*{3}', r'\1', s)              # bold+italic
        s = re.sub(r'\*\*(.+?)\*\*',   r'\1', s)              # bold
        s = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'\1', s)   # italic *
        s = re.sub(r'(?<!_)\b_([^_\n]+)_\b(?!_)',  r'\1', s)  # italic _
        s = re.sub(r'~~(.+?)~~', r'\1', s)                    # strikethrough
        s = re.sub(r'`([^`]+)`',  r'\1', s)                   # inline code
        s = re.sub(r'!\[([^\]]*)\]\([^\)]*\)', r'\1', s)      # images → alt
        s = re.sub(r'\[([^\]]+)\]\([^\)]*\)',  r'\1', s)      # links → text
        s = re.sub(r'<(https?://[^>]+)>', r'\1', s)           # auto links
        s = re.sub(r'\[\^[^\]]+\]', '', s)                    # footnote refs
        return s

    for line in lines:
        stripped = line.strip()

        # Code fences — replace ``` with a readable label, keep code content clean
        if stripped.startswith('```'):
            if not in_code:
                in_code = True
                lang = stripped[3:].strip()
                out.append(_code_label(lang) + ':')
            else:
                in_code = False
                out.append('')  # blank line after code block
            continue
        if in_code:
            out.append(line)
            continue

        # GFM alert type markers — skip the type line, keep body
        if re.match(r'^>\s*\[!(NOTE|WARNING|TIP|IMPORTANT|CAUTION)\]', stripped, re.I):
            continue

        # Horizontal rules → blank line
        if re.match(r'^[-*_]{3,}\s*$', stripped):
            out.append('')
            continue

        # Footnote definitions
        if re.match(r'^\[\^[^\]]+\]:', stripped):
            continue

        # Blockquotes — strip all > prefixes (handles any nesting depth)
        if stripped.startswith('>'):
            line = re.sub(r'^(>\s*)+', '', stripped)

        # Headings — strip #
        h_m = re.match(r'^#{1,6}\s+(.*)', line)
        if h_m:
            line = h_m.group(1)

        # Tables — extract cell content, join with |
        if stripped.count('|') >= 2:
            if re.match(r'^\|?[\s\-:|]+(\|[\s\-:|]+)+\|?$', stripped):
                continue  # separator row
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            line = '  |  '.join(_inline_strip(c) for c in cells if c)
            out.append(line)
            continue

        # Definition list `: definition`
        dl_m = re.match(r'^:\s+(.*)', line)
        if dl_m:
            out.append('  ' + _inline_strip(dl_m.group(1)))
            continue

        # Checkboxes
        line = re.sub(r'^(\s*)- \[[xX]\]\s*', r'\1[x] ', line)
        line = re.sub(r'^(\s*)- \[ \]\s*',    r'\1[ ] ', line)
        # Lists — keep dash and indent, remove trailing whitespace-only bullets
        line = re.sub(r'^(\s*)[-*+]\s+', r'\1- ', line)

        # Inline formatting
        line = _inline_strip(line)
        # Two-space hard line break → strip trailing spaces
        line = line.rstrip()
        out.append(line)

    result = '\n'.join(out)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result.strip()


def _on_copy(mode: str = "raw"):
    """Ctrl+Enter → copy context to clipboard, save to history, then clear the overlay."""
    text = overlay.get_text()
    if not text:
        return
    # Save to history before clearing
    if overlay.get_block_texts():
        _upsert_session()
    else:
        current = next((h for h in _history if h.get("id") == _current_hist_id), None)
        if not (current and current["full"] == text):
            _add_to_history(text, parent_id=_current_hist_id)
    # Copy to clipboard (with or without MD markers)
    text_to_copy = text if mode == "md" else _strip_markdown(text)
    subprocess.run(["pbcopy"], input=text_to_copy.encode("utf-8"), check=False)
    # Clear context window
    overlay.hide(force=True)
    # Return focus to previous app via bundle ID (localizedName unreliable for some apps)
    def _refocus():
        time.sleep(0.3)
        _activate_prev_app()
    threading.Thread(target=_refocus, daemon=True).start()


def _on_paste(mode: str = "raw"):
    """[↵] / Shift+Enter → plain paste (strip MD); Alt+Shift+Enter → paste MD as-is."""
    global _full_mode_standby
    text = overlay.get_text()
    if not text:
        return
    if overlay.get_block_texts():
        # Blocks present → upsert session (no-op if content unchanged, updates last_used)
        _upsert_session()
    else:
        # No blocks — plain _tv text → regular history entry
        current = next((h for h in _history if h.get("id") == _current_hist_id), None)
        if not (current and current["full"] == text):
            _add_to_history(text, parent_id=_current_hist_id)

    # In full mode: apply default scenario only on Shift+Enter (not on [↵] button)
    full_sc = overlay.get_full_default_scenario()
    if (not _state.get("silent") and full_sc and full_sc.get("prompt")
            and overlay.get_active_sc() is None
            and mode in ("shift_enter", "md")):
        def _apply_and_paste(sc=full_sc, raw=text, m=mode):
            cancel_ev = threading.Event()
            def _interrupt():
                cancel_ev.set()
                AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                    lambda: overlay.show_result(raw))
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
            # After paste from full mode: reset to silent mode for next session
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

    # After paste from full mode: reset to silent mode for next session
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
    # _commit_and_paste blocks with sleep — must run off main thread to avoid UI freeze
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

# ── Keep-alive timer (Ctrl+C fix) ─────────────────────────────────────────────

class _KATgt(AppKit.NSObject):
    """No-op timer target — keeps Python signal handler alive inside NSApp.run()."""
    def ping_(self, t): pass

def _setup_keepalive():
    tgt = _KATgt.alloc().init()
    AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.25, tgt, _KATgt.ping_, None, True
    )

# ── Workspace observer — динамический трекинг целевого приложения ─────────────

class _AppObserver(AppKit.NSObject):
    """Observes NSWorkspaceDidActivateApplicationNotification to track paste target."""
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


_app_observer = None   # keep strong reference


class _SleepObserver(AppKit.NSObject):
    """Handles system sleep/wake to reinitialize PortAudio after wake."""

    def systemWillSleep_(self, notification):
        """On sleep: abort any active recording cleanly."""
        stream = _state.get("stream")
        if stream:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            _state["stream"] = None

    def systemDidWake_(self, notification):
        """On wake: reinitialize PortAudio so sounddevice works again."""
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
    """Check AX permission and prompt the user if missing. pynput needs this."""
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
    """On first install: copy parakeet-cli and models from bundle to stable paths.
    Stable paths preserve CoreML cache across app rebuilds/updates.
    Subsequent launches skip this (paths already exist)."""
    import shutil
    import config as _cfg

    # parakeet-cli → ~/.local/bin/parakeet-cli
    stable_bin = os.path.expanduser("~/.local/bin/parakeet-cli")
    if not os.path.isfile(stable_bin) and os.path.isfile(_cfg._bundle_parakeet):
        os.makedirs(os.path.dirname(stable_bin), exist_ok=True)
        shutil.copy2(_cfg._bundle_parakeet, stable_bin)
        os.chmod(stable_bin, 0o755)
        _dbg("first-run: copied parakeet-cli to ~/.local/bin/")

    # models → ~/.local/share/hush/models/<model>
    stable_models = os.path.expanduser("~/.local/share/hush/models")
    stable_model  = os.path.join(stable_models, "parakeet-tdt-0.6b-v3-coreml")
    if not os.path.exists(stable_model) and os.path.isdir(_cfg._bundle_models):
        os.makedirs(stable_models, exist_ok=True)
        _dbg("first-run: copying models (~400 MB) to ~/.local/share/hush/ …")
        shutil.copytree(_cfg._bundle_models, stable_model)
        _dbg("first-run: models copied")


class _AppDelegate(AppKit.NSObject):
    """Minimal NSApplicationDelegate so macOS runs applicationDidFinishLaunching:
    before the Scene lifecycle tries to set up windows. Without this delegate,
    NSSceneStatusItem gets a zero-height window when launched via `open .app`."""

    def applicationDidFinishLaunching_(self, notification):
        _first_run_setup()          # copy binary+models to stable paths (once)
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
