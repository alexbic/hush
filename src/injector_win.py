"""HUSH Windows — вставка текста в активное приложение.

Стратегия:
  1. Сохранить текущее содержимое clipboard (текст + опционально другие форматы)
  2. Поместить новый текст в clipboard
  3. Послать Ctrl+V в предыдущее активное окно
  4. Восстановить оригинальный clipboard через 0.6 сек (после того как Ctrl+V отработал)

Предпочтительный путь: win32clipboard (ctypes, без зависимостей) + win32api/ctypes Ctrl+V.
Fallback: pyperclip + pyautogui.
"""

import sys
import time
import threading
import ctypes
import ctypes.wintypes

if sys.platform != "win32":
    raise ImportError("injector_win.py is Windows-only.")

# ── Константы WinAPI ──────────────────────────────────────────────────────────

CF_UNICODETEXT = 13
CF_TEXT        = 1
GMEM_MOVEABLE  = 0x0002

# Клавишные константы для SendInput
INPUT_KEYBOARD  = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL      = 0x11
VK_V            = 0x56

# ── Структуры SendInput ───────────────────────────────────────────────────────

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         ctypes.wintypes.WORD),
        ("wScan",       ctypes.wintypes.WORD),
        ("dwFlags",     ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("_pad", ctypes.c_byte * 28)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.wintypes.DWORD), ("_u", _INPUT_UNION)]


# ── win32clipboard через ctypes ────────────────────────────────────────────────

_u32 = ctypes.windll.user32    # type: ignore[attr-defined]
_k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]


def _open_clipboard(retries: int = 5) -> bool:
    for _ in range(retries):
        if _u32.OpenClipboard(None):
            return True
        time.sleep(0.05)
    return False


def _clipboard_get_text() -> str | None:
    """Читает CF_UNICODETEXT из clipboard (не открывает/закрывает — вызывать внутри open/close)."""
    h = _u32.GetClipboardData(CF_UNICODETEXT)
    if not h:
        return None
    ptr = _k32.GlobalLock(h)
    if not ptr:
        return None
    try:
        return ctypes.wstring_at(ptr)
    finally:
        _k32.GlobalUnlock(h)


def _clipboard_set_text(text: str):
    """Помещает текст в clipboard (не открывает/закрывает — вызывать внутри open/close)."""
    encoded = (text + "\x00").encode("utf-16-le")
    h = _k32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
    if not h:
        raise OSError("GlobalAlloc failed")
    ptr = _k32.GlobalLock(h)
    if not ptr:
        _k32.GlobalFree(h)
        raise OSError("GlobalLock failed")
    ctypes.memmove(ptr, encoded, len(encoded))
    _k32.GlobalUnlock(h)
    _u32.EmptyClipboard()
    result = _u32.SetClipboardData(CF_UNICODETEXT, h)
    if not result:
        _k32.GlobalFree(h)
        raise OSError("SetClipboardData failed")


def _send_ctrl_v():
    """Посылает нажатие Ctrl+V через SendInput (работает в большинстве приложений)."""
    inputs = (INPUT * 4)()

    # Ctrl down
    inputs[0].type       = INPUT_KEYBOARD
    inputs[0]._u.ki.wVk  = VK_CONTROL

    # V down
    inputs[1].type       = INPUT_KEYBOARD
    inputs[1]._u.ki.wVk  = VK_V

    # V up
    inputs[2].type            = INPUT_KEYBOARD
    inputs[2]._u.ki.wVk       = VK_V
    inputs[2]._u.ki.dwFlags   = KEYEVENTF_KEYUP

    # Ctrl up
    inputs[3].type            = INPUT_KEYBOARD
    inputs[3]._u.ki.wVk       = VK_CONTROL
    inputs[3]._u.ki.dwFlags   = KEYEVENTF_KEYUP

    sent = _u32.SendInput(4, inputs, ctypes.sizeof(INPUT))
    return sent == 4


# ── Публичный API ─────────────────────────────────────────────────────────────

def paste_text(text: str, prev_hwnd: int = None):
    """Вставляет текст в целевое окно.

    Args:
        text:      Строка для вставки.
        prev_hwnd: HWND предыдущего активного окна (если известен).
                   Если передан — сначала SetForegroundWindow к нему.
    """
    if not text:
        return

    # Переключаемся на предыдущее окно перед вставкой
    if prev_hwnd:
        _u32.SetForegroundWindow(prev_hwnd)
        time.sleep(0.15)   # дать системе переключиться

    # 1. Сохраняем текущий clipboard
    saved_text: str | None = None
    if _open_clipboard():
        try:
            saved_text = _clipboard_get_text()
        finally:
            _u32.CloseClipboard()

    # 2. Кладём наш текст
    set_ok = False
    if _open_clipboard():
        try:
            _clipboard_set_text(text)
            set_ok = True
        except Exception as e:
            _log_err(f"clipboard set failed: {e}")
        finally:
            _u32.CloseClipboard()

    if not set_ok:
        # Fallback через pyperclip
        _fallback_paste(text, prev_hwnd)
        return

    # 3. Небольшая задержка — clipboard должен обновиться прежде чем приложение получит Ctrl+V
    time.sleep(0.12)

    # 4. Ctrl+V
    ok = _send_ctrl_v()
    if not ok:
        _log_err("SendInput Ctrl+V failed, trying pyautogui fallback")
        _fallback_ctrl_v()

    # 5. Восстанавливаем clipboard через 0.6 сек в фоне
    if saved_text is not None:
        def _restore():
            time.sleep(0.6)
            if _open_clipboard():
                try:
                    _clipboard_set_text(saved_text)
                except Exception:
                    pass
                finally:
                    _u32.CloseClipboard()
        threading.Thread(target=_restore, daemon=True, name="hush-cb-restore").start()


def _fallback_ctrl_v():
    """Отправляет Ctrl+V через pyautogui если SendInput не сработал."""
    try:
        import pyautogui
        pyautogui.hotkey("ctrl", "v")
    except Exception as e:
        _log_err(f"pyautogui Ctrl+V failed: {e}")


def _fallback_paste(text: str, prev_hwnd: int = None):
    """Полный fallback через pyperclip + pyautogui."""
    try:
        import pyperclip
        import pyautogui
        if prev_hwnd:
            _u32.SetForegroundWindow(prev_hwnd)
            time.sleep(0.15)
        pyperclip.copy(text)
        time.sleep(0.12)
        pyautogui.hotkey("ctrl", "v")
    except Exception as e:
        _log_err(f"fallback paste failed: {e}")


def get_foreground_hwnd() -> int:
    """Возвращает HWND текущего активного окна (для сохранения перед открытием HUSH UI)."""
    return _u32.GetForegroundWindow()


def _log_err(msg: str):
    """Быстрый лог ошибок в stderr (не блокирует)."""
    import traceback
    print(f"[injector_win] {msg}", file=sys.stderr, flush=True)
