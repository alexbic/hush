"""HUSH Windows — весь UI (аналог overlay.py на macOS AppKit).

Компоненты:
  • System tray (pystray) — иконка, меню, управление
  • Pill indicator (tkinter Toplevel без рамки) — статус записи, правый нижний угол
  • Main window (tkinter) — блоки текста, сценарии, кнопка [ВСТАВИТЬ]
  • Settings window (tkinter) — провайдеры, API-ключи

Цветовая схема: тёмная emerald
  фон:     #0d1f1a
  акцент:  #22c55e  (зелёный)
  текст:   #d1fae5  (светло-зелёный)
  dim:     #4ade80
  panel:   #0f2a1e
"""

import sys
import os
import time
import json
import threading
import tkinter as tk
from tkinter import font as tkfont
import queue as _qmod

if sys.platform != "win32":
    raise ImportError("overlay_win.py is Windows-only.")

# ── Цвета ─────────────────────────────────────────────────────────────────────

C_BG      = "#0d1f1a"
C_PANEL   = "#0f2a1e"
C_ACCENT  = "#22c55e"
C_TEXT    = "#d1fae5"
C_DIM     = "#4ade80"
C_WARN    = "#facc15"
C_ERR     = "#f87171"
C_BORDER  = "#166534"
C_BTN_BG  = "#14532d"
C_BTN_HOV = "#166534"
C_IDLE    = "#6b7280"

# ── Состояние ─────────────────────────────────────────────────────────────────

_st: dict = {
    "lang":      "ru",
    "font_size": 13,
}

_callbacks: dict = {
    "on_scenario":  None,   # fn(sc: dict, idx: int)
    "on_history":   None,   # fn() -> list
    "on_paste":     None,   # fn(mode="raw")
    "on_copy":      None,   # fn(mode="raw")
}

# Блоки текущей сессии: [{id, text}, ...]
_blocks:      list = []
_blocks_lock  = threading.Lock()

# Сценарии
_scenarios:   list = []
_active_sc:   int | None = None   # индекс применённого сценария

# Текущий язык (читается transcriber_win через get_current_lang)
_current_lang = "ru"

# ── Глобальные ссылки на окна ─────────────────────────────────────────────────

_root:        tk.Tk | None           = None
_pill:        tk.Toplevel | None     = None
_main_win:    tk.Toplevel | None     = None
_settings_win: tk.Toplevel | None   = None

# Переменные для динамического текста pill
_pill_label:  tk.Label | None       = None
_pill_dot:    tk.Label | None       = None

# Очередь действий в UI-потоке (потокобезопасно)
_ui_queue: _qmod.Queue = _qmod.Queue()

# Статус для pill
_STATUS_IDLE       = "idle"
_STATUS_RECORDING  = "recording"
_STATUS_PROCESSING = "processing"
_STATUS_RESULT     = "result"
_pill_status       = _STATUS_IDLE

# ── Сценарии ──────────────────────────────────────────────────────────────────

SCENARIOS_FILE = os.path.join(os.path.expanduser("~"), ".config", "hush", "scenarios.json")

_DEFAULT_SCENARIOS = [
    {
        "name":   "Без обработки",
        "prompt": "",
        "model":  None,
    },
    {
        "name":   "Исправить текст",
        "prompt": "Исправь грамматику и пунктуацию, не меняй смысл. Верни только исправленный текст.",
        "model":  None,
    },
    {
        "name":   "Краткое резюме",
        "prompt": "Сделай краткое резюме ключевых мыслей. Верни только резюме.",
        "model":  None,
    },
]


def _load_scenarios():
    global _scenarios
    try:
        if os.path.exists(SCENARIOS_FILE):
            with open(SCENARIOS_FILE, encoding="utf-8") as f:
                _scenarios = json.load(f)
            return
    except Exception:
        pass
    _scenarios = list(_DEFAULT_SCENARIOS)


def _save_scenarios():
    try:
        os.makedirs(os.path.dirname(SCENARIOS_FILE), exist_ok=True)
        with open(SCENARIOS_FILE, "w", encoding="utf-8") as f:
            json.dump(_scenarios, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[overlay_win] scenarios save error: {e}", file=sys.stderr)


# ── Поток-безопасный диспетчер UI ─────────────────────────────────────────────

def _dispatch(fn):
    """Поставить fn в очередь UI-потока (thread-safe)."""
    _ui_queue.put(fn)


def _pump_queue():
    """Опустошает _ui_queue — вызывается из tkinter after()."""
    try:
        while True:
            fn = _ui_queue.get_nowait()
            try:
                fn()
            except Exception as e:
                print(f"[overlay_win] ui action error: {e}", file=sys.stderr)
    except _qmod.Empty:
        pass
    if _root and _root.winfo_exists():
        _root.after(50, _pump_queue)


# ── Создание иконки для трея (PIL) ────────────────────────────────────────────

def _make_tray_icon():
    """Создаёт PIL.Image — зелёный круг с буквой H (если нет hush.ico)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    # Ищем готовую иконку рядом с исполняемым файлом или в assets/
    _here = os.path.dirname(os.path.abspath(__file__))
    for candidate in [
        os.path.join(_here, "..", "assets", "hush.ico"),
        os.path.join(_here, "..", "hush.ico"),
        os.path.join(_here, "hush.ico"),
    ]:
        candidate = os.path.normpath(candidate)
        if os.path.isfile(candidate):
            try:
                return Image.open(candidate).resize((64, 64)).convert("RGBA")
            except Exception:
                pass

    # Рисуем программно
    size = 64
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([2, 2, size - 2, size - 2], fill="#22c55e")
    try:
        font = ImageFont.truetype("arial.ttf", 34)
    except Exception:
        font = ImageFont.load_default()
    draw.text((size // 2, size // 2), "H", font=font, fill="#0d1f1a", anchor="mm")
    return img


# ── Pill (индикатор статуса) ──────────────────────────────────────────────────

def _pill_geom():
    """Правый нижний угол экрана с отступом 24px."""
    sw = _root.winfo_screenwidth()
    sh = _root.winfo_screenheight()
    w, h = 260, 44
    x = sw - w - 24
    y = sh - h - 60   # 60px от низа чтобы не перекрывать таскбар
    return w, h, x, y


def _create_pill():
    global _pill, _pill_label, _pill_dot

    _pill = tk.Toplevel(_root)
    _pill.overrideredirect(True)               # без заголовка и рамок
    _pill.wm_attributes("-topmost", True)      # всегда поверх
    _pill.wm_attributes("-alpha", 0.92)
    _pill.configure(bg=C_BG)

    w, h, x, y = _pill_geom()
    _pill.geometry(f"{w}x{h}+{x}+{y}")

    frame = tk.Frame(_pill, bg=C_BG, bd=0)
    frame.pack(fill="both", expand=True, padx=2, pady=2)

    # Цветная точка слева
    _pill_dot = tk.Label(
        frame, text="●", font=("Segoe UI", 14), bg=C_BG, fg=C_IDLE
    )
    _pill_dot.pack(side="left", padx=(10, 6))

    # Текст статуса
    _pill_label = tk.Label(
        frame,
        text="HUSH — готов",
        font=("Segoe UI", 10),
        bg=C_BG, fg=C_DIM,
        anchor="w",
    )
    _pill_label.pack(side="left", fill="x", expand=True, padx=(0, 10))

    # Поддержка перетаскивания
    _pill._drag_x = 0
    _pill._drag_y = 0

    def on_drag_start(e):
        _pill._drag_x = e.x
        _pill._drag_y = e.y

    def on_drag_move(e):
        dx = e.x - _pill._drag_x
        dy = e.y - _pill._drag_y
        x  = _pill.winfo_x() + dx
        y  = _pill.winfo_y() + dy
        _pill.geometry(f"+{x}+{y}")

    _pill.bind("<ButtonPress-1>",   on_drag_start)
    _pill.bind("<B1-Motion>",       on_drag_move)

    _pill.withdraw()   # скрыт по умолчанию


def _pill_show(text: str, dot_color: str = C_ACCENT):
    if not _pill:
        return
    _pill_label.config(text=text)
    _pill_dot.config(fg=dot_color)
    _pill.deiconify()
    _pill.lift()


def _pill_hide():
    if _pill:
        _pill.withdraw()


# ── Main window ───────────────────────────────────────────────────────────────

def _create_main_window():
    global _main_win

    if _main_win and _main_win.winfo_exists():
        _main_win.deiconify()
        _main_win.lift()
        return

    _main_win = tk.Toplevel(_root)
    _main_win.title("HUSH — диктовка")
    _main_win.configure(bg=C_BG)
    _main_win.geometry("520x600")
    _main_win.wm_attributes("-topmost", True)
    _main_win.protocol("WM_DELETE_WINDOW", _main_win.withdraw)

    # ── Заголовок ──
    hdr = tk.Frame(_main_win, bg=C_PANEL, height=42)
    hdr.pack(fill="x")
    tk.Label(hdr, text="HUSH", font=("Segoe UI", 14, "bold"),
             bg=C_PANEL, fg=C_ACCENT).pack(side="left", padx=14, pady=8)

    # Кнопка настроек
    tk.Button(
        hdr, text="⚙", font=("Segoe UI", 12),
        bg=C_PANEL, fg=C_DIM, bd=0, cursor="hand2",
        activebackground=C_BTN_HOV, activeforeground=C_TEXT,
        command=lambda: _dispatch(open_settings),
    ).pack(side="right", padx=8, pady=6)

    # ── Область сценариев ──
    sc_frame = tk.Frame(_main_win, bg=C_BG)
    sc_frame.pack(fill="x", padx=12, pady=(8, 0))
    _rebuild_scenario_buttons(sc_frame)

    # ── Текстовый блок ──
    txt_frame = tk.Frame(_main_win, bg=C_PANEL, bd=1, relief="flat")
    txt_frame.pack(fill="both", expand=True, padx=12, pady=8)

    _main_win._text = tk.Text(
        txt_frame,
        bg=C_PANEL, fg=C_TEXT,
        font=("Segoe UI", _st.get("font_size", 13)),
        wrap="word", bd=0, padx=8, pady=8,
        insertbackground=C_ACCENT,
        selectbackground=C_BORDER,
        selectforeground=C_TEXT,
        relief="flat",
        cursor="xterm",
    )
    _main_win._text.pack(fill="both", expand=True)
    _main_win._text.config(state="disabled")

    # Заполняем текущими блоками
    _refresh_text_area()

    # ── Нижняя панель кнопок ──
    btn_frame = tk.Frame(_main_win, bg=C_BG)
    btn_frame.pack(fill="x", padx=12, pady=(0, 10))

    def _btn(parent, label, cmd, side="left"):
        b = tk.Button(
            parent, text=label,
            font=("Segoe UI", 10, "bold"),
            bg=C_BTN_BG, fg=C_TEXT,
            activebackground=C_ACCENT, activeforeground="#0d1f1a",
            bd=0, padx=14, pady=6, cursor="hand2",
            command=cmd,
        )
        b.pack(side=side, padx=4)
        return b

    _btn(btn_frame, "ВСТАВИТЬ", lambda: _on_paste_btn())
    _btn(btn_frame, "КОПИРОВАТЬ", lambda: _on_copy_btn(), side="left")
    _btn(btn_frame, "ОЧИСТИТЬ",   lambda: _on_clear_btn(), side="right")

    _main_win.withdraw()


def _rebuild_scenario_buttons(frame: tk.Frame):
    """Перестраивает кнопки сценариев в frame."""
    for w in frame.winfo_children():
        w.destroy()
    for i, sc in enumerate(_scenarios):
        _make_sc_btn(frame, sc, i)


def _make_sc_btn(parent, sc: dict, idx: int):
    color = C_ACCENT if idx == _active_sc else C_DIM
    b = tk.Button(
        parent,
        text=sc.get("name", f"Сценарий {idx+1}"),
        font=("Segoe UI", 9),
        bg=C_BTN_BG, fg=color,
        activebackground=C_ACCENT, activeforeground="#0d1f1a",
        bd=0, padx=10, pady=4,
        cursor="hand2",
        command=lambda s=sc, i=idx: _on_scenario_click(s, i),
    )
    b.pack(side="left", padx=2, pady=2)


def _on_scenario_click(sc: dict, idx: int):
    cb = _callbacks.get("on_scenario")
    if cb:
        threading.Thread(target=cb, args=(sc, idx), daemon=True,
                         name="hush-scenario").start()


def _on_paste_btn():
    cb = _callbacks.get("on_paste")
    if cb:
        threading.Thread(target=cb, args=("raw",), daemon=True,
                         name="hush-paste").start()


def _on_copy_btn():
    cb = _callbacks.get("on_copy")
    if cb:
        threading.Thread(target=cb, args=("raw",), daemon=True,
                         name="hush-copy").start()


def _on_clear_btn():
    global _blocks, _active_sc
    with _blocks_lock:
        _blocks.clear()
    _active_sc = None
    _refresh_text_area()


def _refresh_text_area():
    if not _main_win or not _main_win.winfo_exists():
        return
    tv = getattr(_main_win, "_text", None)
    if not tv:
        return
    with _blocks_lock:
        combined = "\n\n".join(b["text"] for b in _blocks)
    tv.config(state="normal")
    tv.delete("1.0", "end")
    if combined:
        tv.insert("end", combined)
    tv.config(state="disabled")
    tv.see("end")


# ── Settings window ────────────────────────────────────────────────────────────

def open_settings():
    global _settings_win
    try:
        import provider_config as _pc
    except ImportError:
        return

    if _settings_win and _settings_win.winfo_exists():
        _settings_win.deiconify()
        _settings_win.lift()
        return

    _settings_win = tk.Toplevel(_root)
    _settings_win.title("HUSH — настройки")
    _settings_win.configure(bg=C_BG)
    _settings_win.geometry("460x480")
    _settings_win.wm_attributes("-topmost", True)
    _settings_win.protocol("WM_DELETE_WINDOW", _settings_win.withdraw)

    def _lbl(parent, text, side="top", **kw):
        tk.Label(parent, text=text, bg=C_BG, fg=C_DIM,
                 font=("Segoe UI", 9), anchor="w", **kw).pack(
            side=side, fill="x", padx=14, pady=(6, 0))

    def _entry(parent, default="", show=None):
        e = tk.Entry(
            parent,
            bg=C_PANEL, fg=C_TEXT,
            font=("Segoe UI", 10),
            insertbackground=C_ACCENT,
            bd=0, relief="flat",
            show=show or "",
        )
        e.insert(0, default)
        e.pack(fill="x", padx=14, pady=(2, 0), ipady=4)
        return e

    # Заголовок
    tk.Label(_settings_win, text="Настройки провайдеров", font=("Segoe UI", 13, "bold"),
             bg=C_BG, fg=C_ACCENT).pack(pady=(14, 6))

    # Anthropic
    _lbl(_settings_win, "Anthropic API Key")
    e_ant = _entry(_settings_win, default=_pc.get("anthropic", "api_key"), show="*")

    # OpenAI
    _lbl(_settings_win, "OpenAI API Key")
    e_oai = _entry(_settings_win, default=_pc.get("openai", "api_key"), show="*")

    # GLM
    _lbl(_settings_win, "GLM (Z.ai) API Key")
    e_glm = _entry(_settings_win, default=_pc.get("glm", "api_key"), show="*")

    # Ollama
    _lbl(_settings_win, "Ollama Base URL")
    e_olm = _entry(_settings_win, default=_pc.get("ollama", "base_url", "http://localhost:11434"))

    _lbl(_settings_win, "Ollama Default Model")
    e_olm_mdl = _entry(_settings_win, default=_pc.get("ollama", "default_model", "qwen3:8b"))

    # Язык диктовки
    _lbl(_settings_win, "Язык распознавания (ru / en / es)")
    e_lang = _entry(_settings_win, default=_current_lang)

    # Модель Whisper
    _lbl(_settings_win, "Размер модели Whisper (tiny / base / small / medium)")
    try:
        from config_win import WHISPER_MODEL_SIZE
        _ws_default = WHISPER_MODEL_SIZE
    except Exception:
        _ws_default = "base"
    e_ws = _entry(_settings_win, default=_ws_default)

    def _save():
        global _current_lang
        _pc.set_field("anthropic", "api_key",     e_ant.get().strip())
        _pc.set_field("openai",    "api_key",     e_oai.get().strip())
        _pc.set_field("glm",       "api_key",     e_glm.get().strip())
        _pc.set_field("ollama",    "base_url",    e_olm.get().strip())
        _pc.set_field("ollama",    "default_model", e_olm_mdl.get().strip())
        lang = e_lang.get().strip().lower()
        if lang in ("ru", "en", "es"):
            _current_lang = lang
        os.environ["HUSH_WHISPER_MODEL"] = e_ws.get().strip()
        _pc.probe_all()
        _settings_win.withdraw()

    tk.Button(
        _settings_win, text="Сохранить",
        font=("Segoe UI", 10, "bold"),
        bg=C_ACCENT, fg="#0d1f1a",
        activebackground=C_DIM, activeforeground="#0d1f1a",
        bd=0, padx=20, pady=8, cursor="hand2",
        command=_save,
    ).pack(pady=14)


# ── System Tray (pystray) ─────────────────────────────────────────────────────

_tray_icon = None

def _build_tray_menu():
    try:
        import pystray
    except ImportError:
        return None

    def _open(_icon, _item):
        _dispatch(_create_main_window)
        _dispatch(lambda: _main_win.deiconify() if _main_win else None)

    def _hist(_icon, _item):
        cb = _callbacks.get("on_history")
        if cb:
            _dispatch(lambda: _show_history_popup(cb()))

    def _settings(_icon, _item):
        _dispatch(open_settings)

    def _exit(_icon, _item):
        _icon.stop()
        _dispatch(_root.quit)

    return pystray.Menu(
        pystray.MenuItem("Открыть",    _open,     default=True),
        pystray.MenuItem("История",    _hist),
        pystray.MenuItem("Настройки",  _settings),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Выход",      _exit),
    )


def _show_history_popup(items: list):
    """Простой список истории в новом окне."""
    if not items:
        return
    w = tk.Toplevel(_root)
    w.title("HUSH — история")
    w.configure(bg=C_BG)
    w.geometry("400x350")
    w.wm_attributes("-topmost", True)

    lb = tk.Listbox(
        w, bg=C_PANEL, fg=C_TEXT,
        font=("Segoe UI", 10),
        selectbackground=C_BORDER,
        bd=0, relief="flat",
    )
    lb.pack(fill="both", expand=True, padx=8, pady=8)

    for item in items[:30]:
        lb.insert("end", item.get("short", "")[:80])

    def on_select(e):
        sel = lb.curselection()
        if not sel:
            return
        idx = sel[0]
        full = items[idx].get("full", "")
        append_block(full)
        _dispatch(lambda: _main_win.deiconify() if _main_win else None)
        w.destroy()

    lb.bind("<Double-Button-1>", on_select)
    lb.bind("<Return>", on_select)


def _start_tray():
    global _tray_icon
    try:
        import pystray
    except ImportError:
        print("[overlay_win] pystray not installed — tray disabled", file=sys.stderr)
        return

    img  = _make_tray_icon()
    if img is None:
        return

    menu = _build_tray_menu()
    _tray_icon = pystray.Icon("HUSH", img, "HUSH — диктовка", menu)

    t = threading.Thread(target=_tray_icon.run, daemon=True, name="hush-tray")
    t.start()


# ── Анимация точки (запись/обработка) ────────────────────────────────────────

_anim_running = False

def _start_pill_anim():
    global _anim_running
    if _anim_running:
        return
    _anim_running = True

    def _blink():
        colors = [C_ACCENT, C_DIM, C_IDLE, C_DIM]
        i = 0
        while _anim_running and _pill and _pill.winfo_exists():
            _pill_dot.config(fg=colors[i % len(colors)])
            i += 1
            time.sleep(0.35)

    threading.Thread(target=_blink, daemon=True, name="hush-pill-anim").start()


def _stop_pill_anim():
    global _anim_running
    _anim_running = False


# ── Публичный API (вызывается из main_win.py) ─────────────────────────────────

def init(on_scenario_cb, on_history_cb, on_paste_cb, on_copy_cb):
    """Инициализирует UI. Вызывать ДО run()."""
    _callbacks["on_scenario"] = on_scenario_cb
    _callbacks["on_history"]  = on_history_cb
    _callbacks["on_paste"]    = on_paste_cb
    _callbacks["on_copy"]     = on_copy_cb
    _load_scenarios()


def show_recording():
    """Показать pill «Запись идёт»."""
    def _do():
        _pill_show("● Запись...", dot_color=C_ERR)
        _start_pill_anim()
    _dispatch(_do)


def show_processing():
    """Показать pill «Обработка»."""
    def _do():
        _stop_pill_anim()
        _pill_show("⟳ Обработка...", dot_color=C_WARN)
    _dispatch(_do)


def show_idle():
    """Скрыть pill, вернуться в idle."""
    def _do():
        _stop_pill_anim()
        _pill_hide()
    _dispatch(_do)


def show_result(text: str):
    """Показать транскрибированный текст в pill (короткая версия) и добавить в main window."""
    def _do():
        _stop_pill_anim()
        preview = text[:60].replace("\n", " ")
        if len(text) > 60:
            preview += "…"
        _pill_show(preview, dot_color=C_ACCENT)
        _root.after(3000, show_idle)
    _dispatch(_do)


def append_block(text: str, block_id: str = None):
    """Добавляет блок текста в main window."""
    import uuid as _uuid
    with _blocks_lock:
        _blocks.append({"id": block_id or str(_uuid.uuid4()), "text": text})
    _dispatch(_refresh_text_area)


def get_text() -> str:
    """Возвращает всё содержимое текстового поля main window."""
    with _blocks_lock:
        return "\n\n".join(b["text"] for b in _blocks)


def get_block_texts() -> list:
    with _blocks_lock:
        return [b["text"] for b in _blocks]


def get_block_hist_data() -> list:
    with _blocks_lock:
        return list(_blocks)


def set_active_scenario(idx: int | None):
    global _active_sc
    _active_sc = idx


def get_active_sc() -> int | None:
    return _active_sc


def get_current_lang() -> str:
    return _current_lang


def update_provider_status():
    """Обновляет статус провайдеров (вызывается когда provider_config завершает probe)."""
    # Для Windows UI достаточно просто вывести в лог; в settings окне обновится при следующем открытии
    try:
        import provider_config as _pc
        statuses = {p: _pc.get_status(p) for p in ("ollama", "anthropic", "openai", "glm")}
        print(f"[overlay_win] provider status: {statuses}", flush=True)
    except Exception:
        pass


def run():
    """Запускает главный цикл tkinter + трей. БЛОКИРУЮЩИЙ вызов."""
    global _root, _pill, _main_win

    _root = tk.Tk()
    _root.withdraw()   # главное окно скрыто — только трей

    _create_pill()
    _create_main_window()

    # Запускаем опрос очереди
    _root.after(50, _pump_queue)

    # Запускаем трей в отдельном потоке
    _start_tray()

    # Показываем короткое приветствие
    _dispatch(lambda: _pill_show("HUSH запущен — Right Alt для диктовки", dot_color=C_ACCENT))
    _root.after(3500, lambda: _dispatch(_pill_hide))

    _root.mainloop()
