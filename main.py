#!/usr/bin/env python3
"""Voice Input System — системный голосовой ввод с Parakeet TDT.
Запускается как фоновый процесс без иконки в меню и Dock.
Активация только хоткеем (Right ⌥); двойное нажатие открывает историю.
"""
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
    global _current_session_id
    _current_session_id = None

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
    """True for apps we never want as paste target (Python, our own process)."""
    if app is None:
        return True
    try:
        own_pid = AppKit.NSRunningApplication.currentApplication().processIdentifier()
        if app.processIdentifier() == own_pid:
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
    "silent":      False,   # True when in silent mode (set synchronously before async UI)
    "cancelled":   False,   # True when double-tap cancels an in-progress recording
}

_last_release_time = 0.0
DOUBLE_TAP_WINDOW  = 0.40   # seconds between release→press to trigger double-tap

_kbd = kb.Controller()   # universal keyboard for paste — works in any app via HID tap


def _on_hotkey_press():
    global _prev_app, _active_scenario_idx
    if _state["hotkey_held"]:
        return
    if overlay.is_editing_scenario():
        return
    _state["hotkey_held"] = True

    front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if not _is_excluded_app(front):
        _prev_app = front
        overlay.set_prev_app_icon(_prev_app)
    _active_scenario_idx = None   # new session clears active filter

    # Double-tap: cancel any in-progress recording, open context window (no history)
    if time.time() - _last_release_time < DOUBLE_TAP_WINDOW:
        _dbg("DOUBLE_TAP fired")
        _state["stream"]    = None
        _state["silent"]    = False
        _state["cancelled"] = True   # tell _do() to abort if still running
        overlay.hide_silent()        # close silent strip without touching main overlay
        overlay.show_recording()
        return

    # Single press: silent mode when window is closed, full mode when already open
    if overlay.is_idle():
        _state["silent"] = True    # set synchronously BEFORE async UI dispatch
        overlay._silent_mode = True  # also set overlay flag immediately (thread-safe read)
        overlay.show_recording_silent(_prev_app)
    else:
        _state["silent"] = False
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
        return   # double-tap mode or no recording started

    def _do():
        wav_path, _ = recorder.stop(stream)
        _dbg(f"_do: stop done, cancelled={_state.get('cancelled')}, wav={bool(wav_path)}")
        # Double-tap cancelled this session while we were recording/transcribing
        if _state.get("cancelled"):
            _state["cancelled"] = False
            _dbg("_do: cancelled, returning")
            return
        if not wav_path:
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide)
            return

        if _state.get("silent"):
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                overlay.show_recognizing_silent
            )
        else:
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.show_transcribing)

        text = transcriber.transcribe(wav_path)
        if _state.get("cancelled"):
            _state["cancelled"] = False
            return
        if not text:
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(overlay.hide)
            return

        if _state.get("silent"):
            silent_sc = overlay.get_silent_scenario()
            if silent_sc and silent_sc.get("prompt"):
                # LLM scenario: show card with recognized text + interrupt overlay
                cancel_ev = threading.Event()

                def _interrupt(raw=text, ev=cancel_ev):
                    ev.set()
                    raw_s = _strip_markdown(raw)
                    subprocess.run(["pbcopy"], input=raw_s.encode("utf-8"), check=False)
                    _add_to_history(raw)
                    _commit_and_paste(raw_s)

                AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                    lambda fn=_interrupt, t=text: overlay.show_processing_silent(fn, t)
                )

                final_text = processor.process_with_prompt(
                    text, silent_sc["prompt"], model=silent_sc.get("model"))

                if not cancel_ev.is_set():
                    final_s = _strip_markdown(final_text)
                    subprocess.run(["pbcopy"], input=final_s.encode("utf-8"), check=False)
                    _add_to_history(final_text)
                    _commit_and_paste(final_s)
            else:
                # No scenario: brief pause so user can see the text, then paste
                time.sleep(0.8)
                final_s = _strip_markdown(text)
                subprocess.run(["pbcopy"], input=final_s.encode("utf-8"), check=False)
                _add_to_history(text)
                _commit_and_paste(final_s)
        else:
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda: overlay.show_result(text)
            )

    threading.Thread(target=_do, daemon=True).start()

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
    """Скрываем оверлей, активируем предыдущее приложение, вставляем текст."""
    prev_app_ref = _prev_app

    def on_main():
        if prev_app_ref:
            prev_app_ref.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
        overlay.hide()
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(on_main)

    def _do():
        try:
            time.sleep(0.35)
            _activate_prev_app()   # bundle-ID backup in case activateWithOptions_ insufficient
            time.sleep(0.35)
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
            time.sleep(0.15)

            # Check Accessibility trust before injecting keystrokes
            try:
                import ApplicationServices as _AS
                ax_ok = _AS.AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": False})
            except Exception:
                ax_ok = True  # assume ok if check fails
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
                print("⚠️  Нет Accessibility разрешения. Текст в буфере — вставьте вручную Cmd+V.")
        except Exception as e:
            _dbg(f"paste ERROR: {e}")
    threading.Thread(target=_do, daemon=True).start()

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

    if mode == "md":
        # Alt+Shift+Enter — paste MD text as-is (preserves markers for rich editors)
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
        _commit_and_paste(text)
    else:
        # Shift+Enter — strip MD markers, paste plain text
        text_to_paste = _strip_markdown(text)
        subprocess.run(["pbcopy"], input=text_to_paste.encode("utf-8"), check=False)
        _commit_and_paste(text_to_paste)

# ── Хоткей ────────────────────────────────────────────────────────────────────

def _setup_hotkey():
    pressed = set()

    def on_press(key):
        if key == kb.Key.alt_r and kb.Key.alt_r not in pressed:
            pressed.add(kb.Key.alt_r)
            _on_hotkey_press()

    def on_release(key):
        if key == kb.Key.alt_r and kb.Key.alt_r in pressed:
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


def _setup_app_observer():
    global _app_observer
    _app_observer = _AppObserver.alloc().init()
    ws = AppKit.NSWorkspace.sharedWorkspace()
    ws.notificationCenter().addObserver_selector_name_object_(
        _app_observer,
        objc.selector(_app_observer.appActivated_, selector=b"appActivated:"),
        AppKit.NSWorkspaceDidActivateApplicationNotification,
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


class _AppDelegate(AppKit.NSObject):
    """Minimal NSApplicationDelegate so macOS runs applicationDidFinishLaunching:
    before the Scene lifecycle tries to set up windows. Without this delegate,
    NSSceneStatusItem gets a zero-height window when launched via `open .app`."""

    def applicationDidFinishLaunching_(self, notification):
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
