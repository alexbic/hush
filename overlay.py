"""
Voice Input — Terminal Green UI.
Session window: opens on hotkey press, accumulates text, closes on [×] or PASTE.
Branch: ui/terminal-green
"""
import re
import sys
import math
import threading
import time
import json
import os
import urllib.request
import AppKit
import objc
import html2text as _html2text
import markdown as _markdown
import provider_config as _pc

# ── Scenarios ─────────────────────────────────────────────────────────────────

SCENARIOS_FILE = os.path.expanduser("~/.config/hush/scenarios.json")

def _html_to_md(html: str) -> str:
    h = _html2text.HTML2Text()
    h.ignore_links    = False
    h.ignore_images   = True
    h.body_width      = 0       # no line wrapping
    h.unicode_snob    = True
    h.protect_links   = False
    h.mark_code       = True
    return h.handle(html).strip()


_C_YELLOW = None  # lazy-init to avoid early _rgba calls
def _md_yellow():
    global _C_YELLOW
    if _C_YELLOW is None:
        _C_YELLOW = _rgba(1.0, 1.0, 0.33)
    return _C_YELLOW

def _md_to_raw_attrs(md_text: str):
    """Syntax-highlight raw Markdown source. Returns NSMutableAttributedString."""
    fs   = _st.get("font_size", 12)
    font = _mono(fs)
    C_YEL = _md_yellow()
    mstr = AppKit.NSMutableAttributedString.alloc().initWithString_attributes_(
        md_text, {
            AppKit.NSForegroundColorAttributeName: C_GREEN_DIM,
            AppKit.NSFontAttributeName: font,
        })
    COL = AppKit.NSForegroundColorAttributeName
    FNT = AppKit.NSFontAttributeName
    def _paint(start, end, color, bold=False):
        rng = AppKit.NSMakeRange(start, end - start)
        mstr.addAttribute_value_range_(COL, color, rng)
        if bold:
            mstr.addAttribute_value_range_(FNT, _mono(fs, bold=True), rng)
    for m in re.finditer(r'^#{1,6} .+', md_text, re.MULTILINE):
        _paint(m.start(), m.end(), C_CYAN, bold=True)
    for m in re.finditer(r'\*\*[^*\n]+\*\*', md_text):
        _paint(m.start(), m.end(), C_TEXT, bold=True)
    for m in re.finditer(r'(?<!\*)\*[^*\n]+\*(?!\*)', md_text):
        _paint(m.start(), m.end(), C_CYAN)
    for m in re.finditer(r'`[^`\n]+`', md_text):
        _paint(m.start(), m.end(), C_YEL)
    for m in re.finditer(r'^```.*', md_text, re.MULTILINE):
        _paint(m.start(), m.end(), C_YEL)
    for m in re.finditer(r'^> .+', md_text, re.MULTILINE):
        _paint(m.start(), m.end(), C_GREEN_DIM)
    for m in re.finditer(r'^(\s*[-*+] |\s*\d+\. )', md_text, re.MULTILINE):
        _paint(m.start(), m.end(), C_GREEN_BR)
    for m in re.finditer(r'^[-*_]{3,}\s*$', md_text, re.MULTILINE):
        _paint(m.start(), m.end(), C_GREEN_DIM)
    return mstr


def _md_to_styled_attrs(md_text: str):
    """Render Markdown to NSAttributedString with theme-aware colors."""
    try:
        html_body = _markdown.markdown(
            md_text,
            extensions=["fenced_code", "tables", "nl2br"]
        )
        fs = _st.get("font_size", 12)

        def _c(col):
            if hasattr(col, 'redComponent'):
                return "#{:02X}{:02X}{:02X}".format(
                    int(col.redComponent()   * 255),
                    int(col.greenComponent() * 255),
                    int(col.blueComponent()  * 255))
            return "#{:02X}{:02X}{:02X}".format(
                int(col[0]*255), int(col[1]*255), int(col[2]*255))

        tx  = _c(C_TEXT)
        acc = _c(C_GREEN)
        dim = _c(C_GREEN_DIM)

        styled = (
            '<html><head><meta charset="utf-8"><style>'
            f'body{{font-family:"-apple-system","SF Pro Text","Helvetica Neue",sans-serif;'
            f'font-size:{fs}px;font-weight:300;'
            f'color:{tx};background:transparent;margin:0;padding:0;}}'
            f'h1{{font-size:{fs+3}px;color:{tx};font-weight:600;margin:4px 0;}}'
            f'h2{{font-size:{fs+1}px;color:{tx};font-weight:500;margin:3px 0;}}'
            f'h3{{font-size:{fs}px;color:{tx};font-weight:500;margin:2px 0;}}'
            f'strong{{color:{tx};font-weight:600;}}'
            f'em{{color:{acc};font-style:italic;font-weight:300;}}'
            f'code{{color:{dim};font-family:"SF Mono","Menlo",monospace;font-size:{fs-1}px;}}'
            f'pre{{color:{dim};font-family:"SF Mono","Menlo",monospace;font-size:{fs-1}px;'
            f'padding:4px;border-left:2px solid {acc};}}'
            f'a{{color:{acc};text-decoration:none;}}'
            'li{margin:1px 0;}p{margin:2px 0;}'
            f'hr{{border:none;border-top:1px solid {dim};}}'
            f'</style></head><body>{html_body}</body></html>'
        )
        ns_data = AppKit.NSData.dataWithBytes_length_(
            styled.encode("utf-8"), len(styled.encode("utf-8")))
        result = AppKit.NSAttributedString.alloc().initWithHTML_documentAttributes_(ns_data, None)
        if result and result[0]:
            attrs = result[0]
            # initWithHTML always appends \n — strip it so blocks don't get an extra blank line
            raw = str(attrs.string())
            trailing = len(raw) - len(raw.rstrip('\n'))
            if trailing > 0 and len(raw) > trailing:
                mutable = AppKit.NSMutableAttributedString.alloc().initWithAttributedString_(attrs)
                mutable.deleteCharactersInRange_(
                    AppKit.NSMakeRange(len(raw) - trailing, trailing))
                return mutable
            return attrs
    except Exception as e:
        print(f"[overlay] _md_to_styled_attrs: {e}")
    return AppKit.NSAttributedString.alloc().initWithString_attributes_(
        md_text, {
            AppKit.NSForegroundColorAttributeName: C_TEXT,
            AppKit.NSFontAttributeName: _mono(_st.get("font_size", 12)),
        })

DEFAULT_SCENARIOS = [
    {"name": "Очистить", "label": {"ru": "ЧИСТКА", "en": "CLEAN",  "es": "LIMPI"},
     "model": None,
     "prompt": (
         "Отредактируй как профессиональный редактор: расставь знаки препинания и заглавные буквы, "
         "убери слова-паразиты (ну, вот, значит, типа, как бы, короче), "
         "расставь кавычки-ёлочки «» для цитат и выделений, "
         "русифицированные англицизмы (интернет, сайт, файл, дедлайн, контент) — пиши по-русски, "
         "профессиональные термины (API, CLI, GitHub) — оставляй как есть. "
         "Сохрани смысл и стиль. Только результат без пояснений."
     )},
    {"name": "Письмо",  "label": {"ru": "ПИСЬМО", "en": "LETTER", "es": "CARTA"},
     "model": None,
     "prompt": "Оформи как деловое письмо. Вежливо, структурировано. Только текст письма."},
    {"name": "Задачи",  "label": {"ru": "ЗАДАЧИ", "en": "TASKS",  "es": "TAREA"},
     "model": None,
     "prompt": "Преобразуй в список задач с чекбоксами (- [ ] ...). Только список."},
]

def load_scenarios():
    if os.path.exists(SCENARIOS_FILE):
        try:
            with open(SCENARIOS_FILE) as f:
                data = json.load(f)
            for sc in data:
                if "label" not in sc:
                    fallback = sc.get("name", "?").upper()[:6]
                    sc["label"] = {"ru": fallback, "en": fallback, "es": fallback}
                elif not isinstance(sc["label"], dict):
                    fallback = str(sc["label"])[:6]
                    sc["label"] = {"ru": fallback, "en": fallback, "es": fallback}
                if "model" not in sc:
                    sc["model"] = None
            return data
        except Exception:
            pass
    return list(DEFAULT_SCENARIOS)

def save_scenarios(sc):
    with open(SCENARIOS_FILE, "w") as f:
        json.dump(sc, f, ensure_ascii=False, indent=2)


def _model_available(model_str: str) -> bool:
    """Quick reachability check for a provider:model string. Runs in background thread."""
    if not model_str:
        return True
    provider, _, model_name = model_str.partition(":")
    provider = provider.lower()
    if provider == "ollama":
        try:
            from config import OLLAMA_BASE_URL
            req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags")
            with urllib.request.urlopen(req, timeout=2) as r:
                data = json.loads(r.read())
            names = [m["name"] for m in data.get("models", [])]
            return any(n == model_name or n.startswith(model_name + ":") for n in names)
        except Exception:
            return False
    if provider == "anthropic":
        try:
            from config import ANTHROPIC_API_KEY
            return bool(ANTHROPIC_API_KEY)
        except Exception:
            return False
    if provider == "openai":
        try:
            from config import OPENAI_API_KEY
            return bool(OPENAI_API_KEY)
        except Exception:
            return False
    if provider == "glm":
        try:
            from config import GLM_API_KEY
            return bool(GLM_API_KEY)
        except Exception:
            return False
    return True


# ── Settings persistence ───────────────────────────────────────────────────────

SETTINGS_FILE = os.path.expanduser("~/.config/hush/settings.json")

def _load_settings() -> dict:
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _screen_key() -> str:
    """Stable identifier for the current screen layout (resolution + count)."""
    try:
        screens = AppKit.NSScreen.screens()
        parts = []
        for s in screens:
            f = s.frame()
            parts.append(f"{int(f.size.width)}x{int(f.size.height)}")
        return "|".join(parts)
    except Exception:
        return "unknown"

def _save_settings():
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        data = {
            "opacity":       _st["opacity"],
            "font_size":     _st["font_size"],
            "lang":          _st.get("lang", "ru"),
            "hotkey_copy":   _st.get("hotkey_copy", "ctrl"),
            "theme":         _st.get("theme", "emerald"),
            "silent_pos":    _cfg_saved.get("silent_pos", {}),
            "win_pos":       _cfg_saved.get("win_pos", {}),
            "magnet_on":     _cfg_saved.get("magnet_on",     {}),
            "magnet_offset": _cfg_saved.get("magnet_offset", {}),
            "magnet_free":   _cfg_saved.get("magnet_free",   {}),
            "panels_open":   _cfg_saved.get("panels_open",   {}),
        }
        with open(SETTINGS_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[settings] save error: {e}")

# ── i18n ──────────────────────────────────────────────────────────────────────

STRINGS = {
    "ru": {
        "idle":        "> ожидание",
        "recording":   "[●] запись",
        "transcribing":"[...] распознавание",
        "ready":       "> готово",
        "hist":        "> история",
        "opacity":     "> прозрачность",
        "font":        "> шрифт",
        "hist_hdr":    "> история",
        "hist_empty":  "—",
        "btn_del":     "[УДАЛИТЬ]",
        "btn_merge":   "[СЛИТЬ]",
        "btn_append":  "[ДОБАВИТЬ]",
        "btn_replace": "[ЗАМЕНИТЬ]",
        "btn_hist":      "[ИСТ]",
        "btn_cfg":       "⚙",
        "cfg_scenarios": "> сценарии",
        "btn_quit":      "[ВЫХОД]",
        "btn_save":      "[СОХР]",
        "btn_cancel":    "[ОТМН]",
        "btn_discard":    "[СБРОС]",
        "edit_confirm":   "сохранить изменения?",
        "sc_edit_hdr":    "> сценарий",
        "btn_sc_delete":  "[УДАЛИТЬ]",
        "delete_confirm": "удалить сценарий?",
        "sc_silent":      "тихий режим",
        "sc_full_default": "full mode по умолч.",
        "hotkey":         "> буфер ↩",
        "btn_sc_accept":  "[ОТПРАВИТЬ]",
        "btn_sc_undo":    "[ОТМЕНИТЬ]",
        "btn_scene":      "[СЦЕНАРИЙ]",
        "cfg_opacity":    "прозрачность",
        "cfg_font":       "шрифт",
        "cfg_lang":       "язык",
        "cfg_hotkey":     "мастер-клавиша",
        "cfg_theme":      "цвет",
        "cfg_scenes":     "сценарии",
        "hist_mixed":     "ВСЕ",
        "hist_sessions":  "СЕССИИ",
        "hist_blocks":    "БЛОКИ",
        "about_body": (
            "HUSH\n"
            "Hear · Understand · Shape · Hand back\n"
            "\n"
            "Голосовой ввод с умной обработкой.\n"
            "Настраиваемые сценарии: правка, перевод,\n"
            "суммаризация — одним нажатием.\n"
            "Работает локально. Ни единого сервера.\n"
            "\n"
            "— S.T.F.U. —\n"
            "Speak · Transcribe · Format · Use"
        ),
    },
    "en": {
        "idle":        "> waiting",
        "recording":   "[●] recording",
        "transcribing":"[...] transcribing",
        "ready":       "> ready",
        "hist":        "> history",
        "opacity":     "> opacity",
        "font":        "> font",
        "hist_hdr":    "> history",
        "hist_empty":  "—",
        "btn_del":     "[DELETE]",
        "btn_merge":   "[MERGE]",
        "btn_append":  "[APPEND]",
        "btn_replace": "[REPLACE]",
        "btn_hist":      "[HIST]",
        "btn_cfg":       "⚙",
        "cfg_scenarios": "> scenarios",
        "btn_quit":      "[QUIT]",
        "btn_save":      "[SAVE]",
        "btn_cancel":    "[CNCL]",
        "btn_discard":    "[DISC]",
        "edit_confirm":   "save changes?",
        "sc_edit_hdr":    "> scenario",
        "btn_sc_delete":  "[DELETE]",
        "delete_confirm": "delete scenario?",
        "sc_silent":      "silent mode",
        "sc_full_default": "full mode default",
        "hotkey":         "> copy ↩",
        "btn_sc_accept":  "[SEND]",
        "btn_sc_undo":    "[CANCEL]",
        "btn_scene":      "[SCENE]",
        "cfg_opacity":    "opacity",
        "cfg_font":       "font",
        "cfg_lang":       "language",
        "cfg_hotkey":     "modifier key",
        "cfg_theme":      "color",
        "cfg_scenes":     "scenarios",
        "hist_mixed":     "ALL",
        "hist_sessions":  "SESSIONS",
        "hist_blocks":    "BLOCKS",
        "about_body": (
            "HUSH\n"
            "Hear · Understand · Shape · Hand back\n"
            "\n"
            "Smart voice input with custom processing.\n"
            "Scenarios: editing, translation, summary —\n"
            "one click. Runs locally. No servers.\n"
            "\n"
            "— S.T.F.U. —\n"
            "Speak · Transcribe · Format · Use"
        ),
    },
    "es": {
        "idle":        "> esperando",
        "recording":   "[●] grabando",
        "transcribing":"[...] transcribiendo",
        "ready":       "> listo",
        "hist":        "> historial",
        "opacity":     "> opacidad",
        "font":        "> fuente",
        "hist_hdr":    "> historial",
        "hist_empty":  "—",
        "btn_del":     "[BORRAR]",
        "btn_merge":   "[UNIR]",
        "btn_append":  "[AÑADIR]",
        "btn_replace": "[REEMPLAZAR]",
        "btn_hist":      "[HIST]",
        "btn_cfg":       "⚙",
        "cfg_scenarios": "> escenarios",
        "btn_quit":      "[SALIR]",
        "btn_save":      "[GUAR]",
        "btn_cancel":    "[CNCL]",
        "btn_discard":    "[DESC]",
        "edit_confirm":   "¿guardar cambios?",
        "sc_edit_hdr":    "> escenario",
        "btn_sc_delete":  "[BORRAR]",
        "delete_confirm": "¿borrar escenario?",
        "sc_silent":      "modo silencioso",
        "sc_full_default": "full mode defecto",
        "hotkey":         "> copia ↩",
        "btn_sc_accept":  "[ENVIAR]",
        "btn_sc_undo":    "[CANCELAR]",
        "btn_scene":      "[ESCENA]",
        "cfg_opacity":    "opacidad",
        "cfg_font":       "fuente",
        "cfg_lang":       "idioma",
        "cfg_hotkey":     "tecla mod.",
        "cfg_theme":      "color",
        "cfg_scenes":     "escenarios",
        "hist_mixed":     "TODO",
        "hist_sessions":  "SESIONES",
        "hist_blocks":    "BLOQUES",
        "about_body": (
            "HUSH\n"
            "Hear · Understand · Shape · Hand back\n"
            "\n"
            "Entrada de voz con procesamiento inteligente.\n"
            "Escenarios: edición, traducción, resumen —\n"
            "un clic. Funciona localmente. Sin servidores.\n"
            "\n"
            "— S.T.F.U. —\n"
            "Speak · Transcribe · Format · Use"
        ),
    },
}

LANGS = ["ru", "en", "es"]   # order in config panel (top → bottom)

def _T(key: str) -> str:
    return STRINGS.get(_st.get("lang", "ru"), STRINGS["ru"]).get(key, key)

def _sc_label(sc: dict) -> str:
    """Return scenario label in current language (EN is fallback), max 6 chars."""
    return _sc_label_for(sc, _st.get("lang", "ru"))

def _refresh_status_label():
    """Sync main-window status label + HIST/CFG button labels to current language."""
    if _lbl:
        mode = _st.get("mode", "idle")
        key_map   = {"idle": "idle", "ready": "ready",
                     "recording": "recording", "transcribing": "transcribing",
                     "history_open": "hist"}
        color_map = {"recording": C_REC, "transcribing": C_GREEN_DIM,
                     "history_open": C_GREEN_DIM}
        key = key_map.get(mode, "idle")
        _lbl.setStringValue_(_T(key))
        _lbl.setTextColor_(color_map.get(mode, C_IDLE))
    if _action_hist_btn:
        _action_hist_btn.setAttributedTitle_(_atitle(_T("btn_hist"), size=12, color=C_CYAN))
    if _cfg_hdr_btn:
        _cfg_hdr_btn.setAttributedTitle_(_atitle(_T("btn_cfg"), size=11, color=C_GREEN))
    if _hist_corner_btn:
        _hist_corner_btn.setAttributedTitle_(_atitle("☰", size=14, color=C_GREEN_DIM))

# ── State ─────────────────────────────────────────────────────────────────────

_cfg_saved = _load_settings()

def _silent_default_pos(win_w: int, win_h: int):
    """Default position: centred horizontally, 6% above bottom of visible area."""
    scr = AppKit.NSScreen.mainScreen()
    vis = scr.visibleFrame()
    sx = int(vis.origin.x + (vis.size.width - win_w) / 2)
    sy = int(vis.origin.y + max(20, int(vis.size.height * 0.06)))
    return sx, sy

def _silent_load_pos(win_w: int, win_h: int):
    """Load saved position for current screen layout; fall back to default if out of bounds."""
    key = _screen_key()
    saved = _cfg_saved.get("silent_pos", {}).get(key)
    if saved:
        cx, sy = saved["cx"], saved["sy"]
        scr = AppKit.NSScreen.mainScreen()
        vis = scr.visibleFrame()
        sx = int(cx - win_w / 2)
        # Validate: window must fit within visible area
        in_x = vis.origin.x <= sx and sx + win_w <= vis.origin.x + vis.size.width
        in_y = vis.origin.y <= sy and sy + win_h <= vis.origin.y + vis.size.height
        if in_x and in_y:
            return sx, int(sy)
    return _silent_default_pos(win_w, win_h)

def _silent_save_pos(win):
    """Persist current window centre X + bottom Y for this screen layout."""
    fr  = win.frame()
    cx  = fr.origin.x + fr.size.width / 2
    sy  = fr.origin.y
    key = _screen_key()
    pos = _cfg_saved.setdefault("silent_pos", {})
    pos[key] = {"cx": cx, "sy": sy}
    _save_settings()


def _win_default_pos():
    """Default main window position: top-right corner of main screen."""
    scr = AppKit.NSScreen.mainScreen().frame()
    x   = scr.origin.x + scr.size.width - W - 20
    y   = scr.origin.y + scr.size.height - H - 60
    return int(x), int(y)

def _win_load_pos():
    """Load saved main window position; validate against any screen; fall back to default."""
    key   = _screen_key()
    saved = _cfg_saved.get("win_pos", {}).get(key)
    if saved:
        sx, sy = saved["x"], saved["y"]
        for screen in AppKit.NSScreen.screens():
            vis  = screen.visibleFrame()
            in_x = vis.origin.x <= sx and sx + W <= vis.origin.x + vis.size.width
            in_y = vis.origin.y <= sy and sy + H <= vis.origin.y + vis.size.height
            if in_x and in_y:
                return int(sx), int(sy)
    return _win_default_pos()

def _win_save_pos():
    """Save main window origin (collapsed size) to settings."""
    if not _win:
        return
    fr  = _win.frame()
    key = _screen_key()
    _cfg_saved.setdefault("win_pos", {})[key] = {"x": fr.origin.x, "y": fr.origin.y}
    _save_settings()


# ── Magnet window system ───────────────────────────────────────────────────────

_PANEL_GAP = 10

def _repel_from_others(panel):
    """Push panel away from overlapping windows (anti-overlap)."""
    others = [p for p in (_win, _cfg_panel, _prov_panel, _sc_editor_panel)
              if p is not None and p is not panel and p.isVisible()]
    if not others:
        return
    fr = panel.frame()
    x, y, w, h = fr.origin.x, fr.origin.y, fr.size.width, fr.size.height
    for _ in range(8):
        moved = False
        for other in others:
            of = other.frame()
            ox, oy, ow, oh = of.origin.x, of.origin.y, of.size.width, of.size.height
            G = _PANEL_GAP
            h_left  = x + w + G - ox
            h_right = ox + ow + G - x
            v_top   = y + h + G - oy
            v_bot   = oy + oh + G - y
            if h_left > 0 and h_right > 0 and v_top > 0 and v_bot > 0:
                candidates = [
                    (h_right,  0),
                    (-h_left,  0),
                    (0,  v_bot),
                    (0, -v_top),
                ]
                dx, dy = min(candidates, key=lambda c: c[0]**2 + c[1]**2)
                x += dx; y += dy; moved = True
        if not moved:
            break
    panel.setFrameOrigin_(AppKit.NSMakePoint(x, y))


_MAGNET_KEYS    = ["cfg", "hist", "editor", "providers"]  # tag=index
_MAGNET_DEFAULT = {"cfg": True, "hist": True, "editor": True, "providers": True}
_magnet_on      = dict(_MAGNET_DEFAULT)
_magnet_offset  = {}   # {key: (dx, dy)} from _win origin when ON
_magnet_free_pos = {}  # {key: (x, y)} saved free position when OFF
_magnet_btns    = {}   # {key: NSButton} for UI updates


def _magnet_save():
    _cfg_saved["magnet_on"]     = dict(_magnet_on)
    _cfg_saved["magnet_offset"] = {k: list(v) for k, v in _magnet_offset.items()}
    _cfg_saved["magnet_free"]   = {k: list(v) for k, v in _magnet_free_pos.items()}
    _save_settings()

def _magnet_load():
    global _magnet_on, _magnet_offset, _magnet_free_pos
    loaded = _cfg_saved.get("magnet_on", {})
    _magnet_on = {k: loaded.get(k, _MAGNET_DEFAULT.get(k, False)) for k in _MAGNET_DEFAULT}
    raw = _cfg_saved.get("magnet_offset", {})
    _magnet_offset = {k: tuple(v) for k, v in raw.items()}
    raw = _cfg_saved.get("magnet_free", {})
    _magnet_free_pos = {k: tuple(v) for k, v in raw.items()}

def _panel_by_key(key):
    return {"cfg":       globals().get("_cfg_panel"),
            "hist":      globals().get("_hist_panel"),
            "editor":    globals().get("_sc_editor_panel"),
            "providers": globals().get("_prov_panel")}.get(key)

def _toggle_magnet(key):
    panel  = _panel_by_key(key)
    was_on = _magnet_on.get(key, False)
    if was_on:
        if panel:
            try:
                pf = panel.frame()
                _magnet_free_pos[key] = (pf.origin.x, pf.origin.y)
            except Exception:
                pass
        _magnet_on[key] = False
        if key in _magnet_free_pos and panel:
            fx, fy = _magnet_free_pos[key]
            try:
                panel.setFrameOrigin_(AppKit.NSMakePoint(fx, fy))
            except Exception:
                pass
    else:
        win = globals().get("_win")
        if panel and win:
            try:
                pf = panel.frame()
                wf = win.frame()
                _magnet_offset[key] = (pf.origin.x - wf.origin.x, pf.origin.y - wf.origin.y)
            except Exception:
                pass
        _magnet_on[key] = True
    _magnet_save()
    _update_magnet_btn(key)

def _update_magnet_btn(key):
    btn = _magnet_btns.get(key)
    if not btn:
        return
    try:
        is_on = _magnet_on.get(key, False)
        btn.setAlphaValue_(1.0 if is_on else 0.35)
    except Exception:
        pass

_SNAP_GAP = 6   # gap between panels in a chain


def _panel_side(key, ww, wh, pw, ph):
    """Return which side ("left","right","top","bottom") a magneted panel is on."""
    if key not in _magnet_offset:
        return None
    dx, dy = _magnet_offset[key]
    if abs(dx) >= abs(dy):
        return "left" if dx <= 0 else "right"
    return "top" if dy >= 0 else "bottom"


def _end_of_side_pos(side, excl_key, wx, wy, ww, wh, pw, ph):
    """Return absolute (nx, ny) for a new panel placed at the END of the chain
    on the given side (past all panels already there, excluding excl_key)."""
    G = _SNAP_GAP
    others = []
    for k in _MAGNET_KEYS:
        if k == excl_key or not _magnet_on.get(k, False) or k not in _magnet_offset:
            continue
        if _panel_side(k, ww, wh, pw, ph) == side:
            others.append(_magnet_offset[k])

    if side == "left":
        base_dx = -(pw + G) if not others else min(d[0] for d in others) - pw - G
        return int(wx + base_dx), int(wy)
    if side == "right":
        base_dx = ww + G if not others else max(d[0] for d in others) + pw + G
        return int(wx + base_dx), int(wy)
    if side == "top":
        base_dy = wh + G if not others else max(d[1] for d in others) + ph + G
        return int(wx), int(wy + base_dy)
    # bottom
    base_dy = -(ph + G) if not others else min(d[1] for d in others) - ph - G
    return int(wx), int(wy + base_dy)


def _opp_side(off_left, off_right, off_top, off_bottom):
    if off_left:   return "right"
    if off_right:  return "left"
    if off_bottom: return "top"
    return "bottom"


def _snap_attached_panels_live(new_wx, new_wy):
    """Called DURING main-window drag (before repositioning): if any magneted panel
    would exit the screen at new_wx/new_wy, update its offset to the opposite side."""
    win = globals().get("_win")
    if not win:
        return
    screen = AppKit.NSScreen.mainScreen()
    if not screen:
        return
    vis = screen.visibleFrame()
    vx, vy = vis.origin.x, vis.origin.y
    vw, vh = vis.size.width, vis.size.height
    mf  = win.frame()
    ww, wh = mf.size.width, mf.size.height
    MARGIN = 20

    for key, pname in [("cfg","_cfg_panel"), ("hist","_hist_panel"),
                        ("editor","_sc_editor_panel"), ("providers","_prov_panel")]:
        if not _magnet_on.get(key, False) or key not in _magnet_offset:
            continue
        p = globals().get(pname)
        if not p:
            continue
        try:
            if not p.isVisible():
                continue
            pf = p.frame()
            pw, ph = pf.size.width, pf.size.height
        except Exception:
            continue

        dx, dy = _magnet_offset[key]
        px, py = new_wx + dx, new_wy + dy
        off_right  = px + pw > vx + vw + MARGIN
        off_left   = px < vx - MARGIN
        off_top    = py + ph > vy + vh + MARGIN
        off_bottom = py < vy - MARGIN
        if not (off_right or off_left or off_top or off_bottom):
            continue

        side = _opp_side(off_left, off_right, off_top, off_bottom)
        nx, ny = _end_of_side_pos(side, key, new_wx, new_wy, ww, wh, pw, ph)
        nx = max(vx, min(nx, vx + vw - pw))
        ny = max(vy, min(ny, vy + vh - ph))
        _magnet_offset[key] = (nx - new_wx, ny - new_wy)


def _smart_snap_panel(key, panel):
    """On mouseUp: if panel is off-screen, jump to opposite side at end of chain."""
    win = globals().get("_win")
    if not win or not panel:
        return
    screen = AppKit.NSScreen.mainScreen()
    if not screen:
        return
    vis = screen.visibleFrame()
    pf  = panel.frame()
    mf  = win.frame()
    px, py = pf.origin.x, pf.origin.y
    pw, ph = pf.size.width, pf.size.height
    vx, vy = vis.origin.x, vis.origin.y
    vw, vh = vis.size.width, vis.size.height
    wx, wy = mf.origin.x, mf.origin.y
    ww, wh = mf.size.width, mf.size.height
    MARGIN = 20

    off_right  = px + pw > vx + vw + MARGIN
    off_left   = px < vx - MARGIN
    off_top    = py + ph > vy + vh + MARGIN
    off_bottom = py < vy - MARGIN
    if not (off_right or off_left or off_top or off_bottom):
        return

    side = _opp_side(off_left, off_right, off_top, off_bottom)
    nx, ny = _end_of_side_pos(side, key, wx, wy, ww, wh, pw, ph)
    nx = max(vx, min(nx, vx + vw - pw))
    ny = max(vy, min(ny, vy + vh - ph))

    panel.setFrameOrigin_(AppKit.NSMakePoint(nx, ny))
    if _magnet_on.get(key, False):
        _magnet_offset[key] = (nx - wx, ny - wy)
    else:
        _magnet_free_pos[key] = (nx, ny)
    _magnet_save()


def _update_panel_drag_end(key, panel):
    win = globals().get("_win")
    if not panel or not win:
        return
    try:
        pf = panel.frame()
        if _magnet_on.get(key, False):
            wf = win.frame()
            _magnet_offset[key] = (pf.origin.x - wf.origin.x, pf.origin.y - wf.origin.y)
        else:
            _magnet_free_pos[key] = (pf.origin.x, pf.origin.y)
        _magnet_save()
    except Exception:
        pass
    # Snap panel to opposite side if it went off-screen
    try:
        _smart_snap_panel(key, panel)
    except Exception:
        pass

def _mkmagnet_btn(key, cv, x, y, w=22, h=22):
    is_on = _magnet_on.get(key, False)
    btn   = _mkbtn("🧲", color=AppKit.NSColor.whiteColor(), size=13)
    btn.setFrame_(AppKit.NSMakeRect(x, y, w, h))
    btn.setAlphaValue_(1.0 if is_on else 0.35)
    btn.setTag_(_MAGNET_KEYS.index(key))
    btn.setTarget_(_btn_t)
    btn.setAction_(BtnTarget.panelMagnet_)
    cv.addSubview_(btn)
    _magnet_btns[key] = btn
    return btn


_WIN_ALPHA = 1.0    # pill (silent mode) is always fully opaque

_st = {
    "mode":          "idle",   # idle | recording | transcribing | ready | history_open
    "text":          "",       # accumulated session text
    "opacity":       _cfg_saved.get("opacity",   0.88),  # expanded window alpha
    "font_size":     _cfg_saved.get("font_size", 13.0),
    "lang":          _cfg_saved.get("lang",      "ru"),
    "hotkey_copy":   _cfg_saved.get("hotkey_copy", "ctrl"),
    "theme":         _cfg_saved.get("theme",     "emerald"),
    "scenarios":     load_scenarios(),
    "active_sc":     None,     # index of currently-applied scenario, or None
    "is_md":         False,    # True when current text looks like Markdown
    "md_mode":       False,    # True = showing rendered terminal view, False = raw
    "rich_fmt":      None,     # None | "rtf" | "html" — set when rich text pasted
    "rich_mode":     False,    # True = showing original rich formatting in text view
    "rich_attrs":    None,     # NSAttributedString snapshot from pasteboard (rich paste)
    "_silent_sc_idx_legacy": _cfg_saved.get("silent_sc_idx"),  # migration only, not used
}

_on_scenario_cb        = None  # (scenario_dict, idx) → None
_on_history_cb         = None  # () → [dict, ...]
_on_paste_cb           = None  # () → None  (Shift+Enter / [↵])
_on_copy_cb            = None  # () → None  (Ctrl+Enter — copy to clipboard, no paste)
_on_history_delete_cb  = None  # ([str]) → None  (delete by UUID)
_on_history_load_cb    = None  # (item_id_or_None) → None  (item loaded into editor)
_on_history_merge_cb   = None  # (text: str, source_ids: list) → str  (merge+delete sources)

_hist_ctrl = None  # strong ref to _HistCtrl — prevents GC while panel is open

# ── Waveform ──────────────────────────────────────────────────────────────────

_wf_lock  = threading.Lock()
_WF_N     = 24
_wf_bars  = [0.0] * _WF_N   # current display height (smoothed)
_wf_peaks = [0.0] * _WF_N   # peak-hold per bar
_wf_t     = 0.0              # idle animation phase (incremented by timer)

def update_waveform(chunk_float32):
    """Called from audio callback with raw float32 PCM."""
    n  = _WF_N
    sz = max(1, len(chunk_float32) // n)
    new = []
    for i in range(n):
        seg = chunk_float32[i*sz : (i+1)*sz]
        v   = min(1.0, (float(max(abs(x) for x in seg)) if len(seg) > 0 else 0.0) * 32.0)
        new.append(v)
    with _wf_lock:
        for i, nv in enumerate(new):
            ov = _wf_bars[i]
            # Fast attack, slow gravity decay
            _wf_bars[i]  = nv if nv > ov else ov * 0.60
            # Peak hold: jump up instantly, fall slowly
            if nv >= _wf_peaks[i]:
                _wf_peaks[i] = nv
            else:
                _wf_peaks[i] = max(0.0, _wf_peaks[i] - 0.018)

def _clear_waveform():
    with _wf_lock:
        _wf_bars[:]  = [0.0] * _WF_N
        _wf_peaks[:] = [0.0] * _WF_N

# ── Sound pool ────────────────────────────────────────────────────────────────

_snd_pool = []
_snd_idx  = 0
_last_snd = 0.0

def _init_sounds():
    global _snd_pool
    snd = AppKit.NSSound.soundNamed_("Pop")
    if snd:
        _snd_pool = [snd.copy() for _ in range(8)]
        for s in _snd_pool:
            s.setVolume_(0.15)

def _click():
    """Play a short typewriter click (throttled)."""
    global _snd_idx, _last_snd
    t = time.time()
    if t - _last_snd < 0.03 or not _snd_pool:
        return
    _last_snd = t
    s = _snd_pool[_snd_idx % len(_snd_pool)]
    _snd_idx += 1
    if not s.isPlaying():
        s.play()

# ── Terminal Colours ──────────────────────────────────────────────────────────

def _rgba(r, g, b, a=1.0):
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)

C_BG         = (0.039, 0.039, 0.071)
C_GREEN      = _rgba(0.00, 0.80, 0.00)
C_GREEN_BR   = _rgba(0.33, 1.00, 0.33)
C_GREEN_DIM  = _rgba(0.05, 0.65, 0.05)       # dim = readable on dark bg, no alpha
C_GREEN_BORD = _rgba(0.00, 0.80, 0.00, 0.35) # border — alpha OK
C_TEXT       = _rgba(0.33, 1.00, 0.33)
C_IDLE       = _rgba(0.00, 0.72, 0.00)       # idle = bright enough to read on dark bg
C_REC        = _rgba(1.00, 0.33, 0.33)
C_YEL        = _rgba(1.00, 0.85, 0.00)
C_AMBER_DIM  = _rgba(0.90, 0.60, 0.10)   # full_default scenario normal state
C_AMBER_BR   = _rgba(1.00, 0.80, 0.30)   # full_default scenario active state
C_PINK       = _rgba(1.00, 0.25, 0.60)   # recognition EQ — same in window and silent
C_CYAN       = _rgba(0.33, 1.00, 1.00)
C_BAR_ON     = _rgba(0.33, 1.00, 0.33)
C_BAR_OFF    = _rgba(0.00, 0.15, 0.00)

# ── Color themes ───────────────────────────────────────────────────────────────
# Color palettes sourced from Roclea admin panel (TC terminal color set)
_THEMES = {
    # ── Светлые темы — текст тёмный, без alpha у текстовых цветов ────────────────
    "paper": {  # кремовый фон, тёмно-зелёный текст
        "C_BG": (0.945, 0.925, 0.855),
        "C_GREEN":     _rgba(0.05, 0.30, 0.05),   # нормальный
        "C_GREEN_BR":  _rgba(0.02, 0.16, 0.02),   # акцент — темнее
        "C_GREEN_DIM": _rgba(0.18, 0.46, 0.18),   # dim = светлее (меньше контраст)
        "C_GREEN_BORD":_rgba(0.05, 0.30, 0.05, 0.30),
        "C_TEXT":      _rgba(0.02, 0.16, 0.02),
        "C_IDLE":      _rgba(0.14, 0.42, 0.14),   # чуть светлее нормального
        "C_CYAN":      _rgba(0.00, 0.20, 0.52),
        "C_BAR_ON":    _rgba(0.05, 0.30, 0.05),
        "C_BAR_OFF":   _rgba(0.882, 0.862, 0.792),
    },
    "sky": {  # светло-голубой фон, тёмно-синий текст
        "C_BG": (0.875, 0.920, 0.965),
        "C_GREEN":     _rgba(0.00, 0.18, 0.52),
        "C_GREEN_BR":  _rgba(0.00, 0.08, 0.36),
        "C_GREEN_DIM": _rgba(0.14, 0.32, 0.62),
        "C_GREEN_BORD":_rgba(0.00, 0.18, 0.52, 0.30),
        "C_TEXT":      _rgba(0.00, 0.08, 0.36),
        "C_IDLE":      _rgba(0.10, 0.26, 0.58),
        "C_CYAN":      _rgba(0.46, 0.00, 0.46),
        "C_BAR_ON":    _rgba(0.00, 0.18, 0.52),
        "C_BAR_OFF":   _rgba(0.808, 0.852, 0.898),
    },
    "sand": {  # тёплый бежевый фон, тёмно-коричневый текст
        "C_BG": (0.965, 0.925, 0.835),
        "C_GREEN":     _rgba(0.38, 0.20, 0.00),
        "C_GREEN_BR":  _rgba(0.22, 0.10, 0.00),
        "C_GREEN_DIM": _rgba(0.52, 0.34, 0.12),
        "C_GREEN_BORD":_rgba(0.38, 0.20, 0.00, 0.30),
        "C_TEXT":      _rgba(0.22, 0.10, 0.00),
        "C_IDLE":      _rgba(0.46, 0.28, 0.08),
        "C_CYAN":      _rgba(0.00, 0.26, 0.44),
        "C_BAR_ON":    _rgba(0.38, 0.20, 0.00),
        "C_BAR_OFF":   _rgba(0.898, 0.858, 0.768),
    },
    "arctic": {  # ледяной фон, тёмный тил
        "C_BG": (0.880, 0.950, 0.960),
        "C_GREEN":     _rgba(0.00, 0.30, 0.40),
        "C_GREEN_BR":  _rgba(0.00, 0.16, 0.26),
        "C_GREEN_DIM": _rgba(0.12, 0.44, 0.52),
        "C_GREEN_BORD":_rgba(0.00, 0.30, 0.40, 0.30),
        "C_TEXT":      _rgba(0.00, 0.16, 0.26),
        "C_IDLE":      _rgba(0.08, 0.38, 0.48),
        "C_CYAN":      _rgba(0.44, 0.00, 0.44),
        "C_BAR_ON":    _rgba(0.00, 0.30, 0.40),
        "C_BAR_OFF":   _rgba(0.812, 0.882, 0.892),
    },
    # ── Тёмные темы — текст яркий, без alpha у текстовых цветов ─────────────────
    "emerald": {  # тёмно-зелёный фон, зелёный текст
        "C_BG": (0.024, 0.063, 0.024),
        "C_GREEN":     _rgba(0.00, 0.80, 0.00),
        "C_GREEN_BR":  _rgba(0.33, 1.00, 0.33),
        "C_GREEN_DIM": _rgba(0.05, 0.65, 0.05),   # dim = читаемый, но мягче C_TEXT
        "C_GREEN_BORD":_rgba(0.00, 0.80, 0.00, 0.35),
        "C_TEXT":      _rgba(0.33, 1.00, 0.33),
        "C_IDLE":      _rgba(0.00, 0.72, 0.00),   # idle = чуть темнее C_GREEN
        "C_CYAN":      _rgba(0.33, 1.00, 1.00),
        "C_BAR_ON":    _rgba(0.33, 1.00, 0.33),
        "C_BAR_OFF":   _rgba(0.00, 0.18, 0.00),
    },
    "ocean": {  # тёмно-синий фон, cyan текст
        "C_BG": (0.016, 0.035, 0.145),
        "C_GREEN":     _rgba(0.00, 0.55, 0.75),
        "C_GREEN_BR":  _rgba(0.33, 1.00, 1.00),
        "C_GREEN_DIM": _rgba(0.00, 0.50, 0.68),   # dim = читаемый teal
        "C_GREEN_BORD":_rgba(0.00, 0.55, 0.75, 0.35),
        "C_TEXT":      _rgba(0.33, 1.00, 1.00),
        "C_IDLE":      _rgba(0.00, 0.60, 0.82),   # idle = чуть ярче dim
        "C_CYAN":      _rgba(1.00, 1.00, 0.33),
        "C_BAR_ON":    _rgba(0.33, 1.00, 1.00),
        "C_BAR_OFF":   _rgba(0.00, 0.06, 0.20),
    },
    "neon": {  # тёмно-фиолетовый фон, magenta текст
        "C_BG": (0.090, 0.020, 0.130),
        "C_GREEN":     _rgba(0.75, 0.00, 0.75),
        "C_GREEN_BR":  _rgba(1.00, 0.33, 1.00),
        "C_GREEN_DIM": _rgba(0.62, 0.00, 0.62),   # dim = читаемый magenta
        "C_GREEN_BORD":_rgba(0.75, 0.00, 0.75, 0.35),
        "C_TEXT":      _rgba(1.00, 0.33, 1.00),
        "C_IDLE":      _rgba(0.72, 0.00, 0.72),   # idle = между dim и C_GREEN
        "C_CYAN":      _rgba(0.33, 1.00, 1.00),
        "C_BAR_ON":    _rgba(1.00, 0.33, 1.00),
        "C_BAR_OFF":   _rgba(0.15, 0.00, 0.18),
    },
    "gold": {  # тёмно-янтарный фон, жёлтый текст
        "C_BG": (0.130, 0.085, 0.008),
        "C_GREEN":     _rgba(0.85, 0.65, 0.00),
        "C_GREEN_BR":  _rgba(1.00, 1.00, 0.33),
        "C_GREEN_DIM": _rgba(0.68, 0.52, 0.00),   # dim = читаемый amber
        "C_GREEN_BORD":_rgba(0.85, 0.65, 0.00, 0.35),
        "C_TEXT":      _rgba(1.00, 1.00, 0.33),
        "C_IDLE":      _rgba(0.78, 0.60, 0.00),   # idle = между dim и C_GREEN
        "C_CYAN":      _rgba(0.33, 1.00, 0.33),
        "C_BAR_ON":    _rgba(1.00, 1.00, 0.33),
        "C_BAR_OFF":   _rgba(0.18, 0.12, 0.00),
    },
}
# (name, bg_tuple, accent_NSColor) — first _N_LIGHT = light themes (top row), rest = dark
_N_LIGHT = 4
_THEME_META = [
    # светлые (top row, 4 шт)
    ("paper",   (0.945, 0.925, 0.855), _rgba(0.08, 0.42, 0.08)),
    ("sky",     (0.875, 0.920, 0.965), _rgba(0.00, 0.28, 0.62)),
    ("sand",    (0.965, 0.925, 0.835), _rgba(0.52, 0.32, 0.00)),
    ("arctic",  (0.880, 0.950, 0.960), _rgba(0.00, 0.42, 0.52)),
    # тёмные (bottom row, 4 шт)
    ("emerald", (0.024, 0.063, 0.024), _rgba(0.33, 1.00, 0.33)),
    ("ocean",   (0.016, 0.035, 0.145), _rgba(0.33, 1.00, 1.00)),
    ("neon",    (0.090, 0.020, 0.130), _rgba(1.00, 0.33, 1.00)),
    ("gold",    (0.130, 0.085, 0.008), _rgba(1.00, 1.00, 0.33)),
]

def _apply_theme(name, _save=True):
    global C_BG, C_GREEN, C_GREEN_BR, C_GREEN_DIM, C_GREEN_BORD, C_TEXT, C_IDLE, C_CYAN, C_BAR_ON, C_BAR_OFF
    t = _THEMES.get(name, _THEMES["emerald"])
    C_BG         = t["C_BG"]
    C_GREEN      = t["C_GREEN"]
    C_GREEN_BR   = t["C_GREEN_BR"]
    C_GREEN_DIM  = t["C_GREEN_DIM"]
    C_GREEN_BORD = t["C_GREEN_BORD"]
    C_TEXT       = t["C_TEXT"]
    C_IDLE       = t["C_IDLE"]
    C_CYAN       = t["C_CYAN"]
    C_BAR_ON     = t["C_BAR_ON"]
    C_BAR_OFF    = t["C_BAR_OFF"]
    _st["theme"] = name
    if _save:
        _save_settings()
    p = globals().get("_pill")
    if p:
        p.setNeedsDisplay_(True)
    # Sync _tv text color so typing and setString_() always match the theme
    tv = globals().get("_tv")
    if tv:
        tv.setTextColor_(C_TEXT)
        tv.setTypingAttributes_({
            AppKit.NSFontAttributeName:            _mono(_st.get("font_size", 12)),
            AppKit.NSForegroundColorAttributeName: C_TEXT,
        })
    # Refresh all theme-colored buttons — only after UI is built
    if globals().get("_pill"):
        _refresh_status_label()
        _apply_theme_to_all_windows()
    exp_btn = globals().get("_expand_btn")
    if exp_btn:
        lbl = "[─]" if globals().get("_expanded") else "[□]"
        exp_btn.setAttributedTitle_(_atitle(lbl, size=12, color=C_GREEN_DIM))
    _apply_all_panels_alpha()


def _apply_theme_to_all_windows():
    """Redraw / rebuild all visible secondary windows to pick up new C_* colours."""
    # History panel — rebuild if open (uses TerminalView bg + many coloured buttons)
    hist = globals().get("_hist_panel")
    if hist and hasattr(hist, "isVisible") and hist.isVisible():
        hist.orderOut_(None)
        hist.close()
        on_hist = globals().get("_on_history_cb")
        if on_hist:
            history = on_hist()
            _show_hist_panel(history)

    # Recursively redraw all subviews — needed for panels with deep view hierarchies
    def _redisplay_tree(v):
        v.setNeedsDisplay_(True)
        for sub in list(v.subviews()):
            _redisplay_tree(sub)

    # All secondary panels — drawRect_ reads C_* globals; recurse for nested views
    for key in ("_cfg_panel", "_hist_panel", "_sc_editor_panel", "_about_panel"):
        p = globals().get(key)
        if p and hasattr(p, "isVisible") and p.isVisible():
            cv = p.contentView()
            if cv:
                _redisplay_tree(cv)

    # Silent mode windows
    for key in ("_silent_win",):
        sw = globals().get(key)
        if sw and hasattr(sw, "contentView"):
            cv = sw.contentView()
            if cv:
                _redisplay_tree(cv)

    # Re-render rich blocks with new theme colors
    for _b in list(globals().get("_rich_blocks", [])):
        try:
            _b._rendered = _md_to_styled_attrs(_b._md_text)
            if _b._inner_tv and not getattr(_b, "_md_mode", False):
                _b._inner_tv.textStorage().setAttributedString_(_b._rendered)
        except Exception:
            pass
    # Update magnet button colors
    for _mkey in list(_magnet_btns):
        _update_magnet_btn(_mkey)

def _apply_all_panels_alpha():
    """Apply _st['opacity'] to _win and all open panels. Silent strip (_silent_win) is excluded."""
    alpha = _st.get("opacity", 0.88)
    win = globals().get("_win")
    if win:
        win.setAlphaValue_(alpha)
    for key in ("_cfg_panel", "_hist_panel", "_sc_editor_panel", "_about_panel"):
        p = globals().get(key)
        if p and hasattr(p, "isVisible") and p.isVisible():
            p.setAlphaValue_(alpha)

# Apply saved theme at startup (updates globals before UI is built)
_apply_theme(_cfg_saved.get("theme", "emerald"), _save=False)

# ── Views ─────────────────────────────────────────────────────────────────────

def _tf_cell_adj(cell, frame):
    """Return frame adjusted for vertical centering + left padding (for NSTextFieldCell)."""
    sz = cell.cellSizeForBounds_(frame)
    dy = max(0.0, (frame.size.height - sz.height) / 2.0)
    return AppKit.NSMakeRect(
        frame.origin.x + 5,
        frame.origin.y + dy,
        frame.size.width - 10,
        sz.height)


class _CenteredTextFieldCell(AppKit.NSTextFieldCell):
    """NSTextFieldCell that centers text vertically and adds left padding."""

    def drawInteriorWithFrame_inView_(self, frame, view):
        objc.super(_CenteredTextFieldCell, self).drawInteriorWithFrame_inView_(
            _tf_cell_adj(self, frame), view)

    def editWithFrame_inView_editor_delegate_event_(self, frame, view, ed, dlg, ev):
        objc.super(_CenteredTextFieldCell, self
            ).editWithFrame_inView_editor_delegate_event_(
            _tf_cell_adj(self, frame), view, ed, dlg, ev)

    def selectWithFrame_inView_editor_delegate_start_length_(
            self, frame, view, ed, dlg, start, length):
        objc.super(_CenteredTextFieldCell, self
            ).selectWithFrame_inView_editor_delegate_start_length_(
            _tf_cell_adj(self, frame), view, ed, dlg, start, length)


class _PlaceholderTextView(AppKit.NSTextView):
    """NSTextView with a placeholder drawn when empty."""
    _ph = ""

    def setPlaceholder_(self, txt):
        self._ph = txt
        self.setNeedsDisplay_(True)

    def drawRect_(self, dirty):
        objc.super(_PlaceholderTextView, self).drawRect_(dirty)
        if self._ph and not self.string().strip():
            ph_col = AppKit.NSColor.colorWithRed_green_blue_alpha_(
                C_IDLE.redComponent(), C_IDLE.greenComponent(), C_IDLE.blueComponent(), 0.45)
            attrs = {
                AppKit.NSForegroundColorAttributeName: ph_col,
                AppKit.NSFontAttributeName:            _mono(9),
            }
            AppKit.NSString.stringWithString_(self._ph).drawAtPoint_withAttributes_(
                AppKit.NSMakePoint(5, 2), attrs)

    def didChangeText(self):
        objc.super(_PlaceholderTextView, self).didChangeText()
        self.setNeedsDisplay_(True)


class TerminalSlider(AppKit.NSControl):
    """Terminal-style horizontal slider: green track + rectangle thumb."""

    def initWithFrame_(self, frame):
        self = objc.super(TerminalSlider, self).initWithFrame_(frame)
        if self is None:
            return None
        self._val = 0.5
        self._min = 0.0
        self._max = 1.0
        return self

    def setFloatValue_(self, v):
        self._val = float(v)
        self.setNeedsDisplay_(True)

    def floatValue(self):
        return float(self._val)

    def setMinValue_(self, v): self._min = float(v)
    def setMaxValue_(self, v): self._max = float(v)

    def drawRect_(self, rect):
        b  = self.bounds()
        w  = b.size.width
        h  = b.size.height
        # No background fill — transparent, inherits panel dark background
        # Track (thin line)
        th = 2.0
        ty = (h - th) / 2
        # Unfilled track
        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.00, 0.30, 0.00, 1.0).set()
        AppKit.NSRectFill(AppKit.NSMakeRect(0, ty, w, th))
        # Thumb
        tw = 10.0
        th2 = h * 0.65
        ty2 = (h - th2) / 2
        t   = (self._val - self._min) / max(0.001, self._max - self._min)
        tx  = t * (w - tw)
        # Filled track
        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.10, 0.60, 0.10, 1.0).set()
        AppKit.NSRectFill(AppKit.NSMakeRect(0, ty, tx + tw / 2, 2.0))
        # Thumb rectangle
        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.33, 1.00, 0.33, 1.0).set()
        AppKit.NSBezierPath.fillRect_(AppKit.NSMakeRect(tx, ty2, tw, th2))

    def mouseDown_(self, event):
        self._updateFromEvent_(event)

    def mouseDragged_(self, event):
        self._updateFromEvent_(event)

    def mouseUp_(self, event):
        self._updateFromEvent_(event)

    def _updateFromEvent_(self, event):
        loc = self.convertPoint_fromView_(event.locationInWindow(), None)
        b   = self.bounds()
        t   = max(0.0, min(1.0, loc.x / b.size.width))
        self._val = self._min + t * (self._max - self._min)
        self.setNeedsDisplay_(True)
        self.sendAction_to_(self.action(), self.target())


class TerminalView(AppKit.NSView):
    """Dark background + CRT scanlines + green border."""

    def drawRect_(self, rect):
        b    = self.bounds()
        a    = 1.0
        r, g, bb = C_BG
        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, bb, a).set()
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, 4, 4)
        path.fill()
        # CRT scanlines — near-invisible on light themes, subtle on dark
        bg_lum = (C_BG[0] + C_BG[1] + C_BG[2]) / 3
        sl_factor = 0.995 if bg_lum > 0.5 else 0.80  # light: 0.5% / dark: 20%
        sr, sg, sb = C_BG[0] * sl_factor, C_BG[1] * sl_factor, C_BG[2] * sl_factor
        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(sr, sg, sb, a).set()
        yy = 0.0
        while yy < b.size.height:
            AppKit.NSRectFill(AppKit.NSMakeRect(0, yy, b.size.width, 2))
            yy += 4
        # Border
        C_GREEN_BORD.set()
        border = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSInsetRect(b, 0.5, 0.5), 3.5, 3.5
        )
        border.setLineWidth_(1.0)
        border.stroke()

    def updateTrackingAreas(self):
        objc.super(TerminalView, self).updateTrackingAreas()
        for a in list(self.trackingAreas()):
            self.removeTrackingArea_(a)
        opts = (AppKit.NSTrackingMouseEnteredAndExited |
                AppKit.NSTrackingActiveAlways |
                AppKit.NSTrackingInVisibleRect)
        self.addTrackingArea_(
            AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(), opts, self, None))

    def mouseEntered_(self, event):
        if _proc_hover_v and not _proc_hover_v.isHidden():
            _proc_hover_v._hover_active = True
            _proc_hover_v.setNeedsDisplay_(True)
        elif _silent_hover_v:
            _silent_hover_v.setHidden_(False)

    def mouseExited_(self, event):
        if _proc_hover_v and not _proc_hover_v.isHidden():
            _proc_hover_v._hover_active = False
            _proc_hover_v.setNeedsDisplay_(True)
        if _silent_hover_v:
            _silent_hover_v.setHidden_(True)


class _AboutBgView(TerminalView):
    """About card background — click anywhere (not on a subview button) to close."""

    def updateTrackingAreas(self):
        objc.super(_AboutBgView, self).updateTrackingAreas()
        for a in list(self.trackingAreas()):
            self.removeTrackingArea_(a)
        opts = (AppKit.NSTrackingCursorUpdate |
                AppKit.NSTrackingActiveAlways |
                AppKit.NSTrackingInVisibleRect)
        self.addTrackingArea_(
            AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(), opts, self, None))

    def cursorUpdate_(self, event):
        AppKit.NSCursor.arrowCursor().set()

    def mouseDown_(self, event):
        _main(_hide_about_view)

    def acceptsFirstMouse_(self, event):
        return True


class _WalletView(AppKit.NSView):
    """Wallet donate button.
    Image always fills the full view frame (no compression).
    White background is a separate layer BEHIND the image:
      - height is fixed (crops bills at top ~22%, coin at bottom ~8%)
      - width animates from _BG_W_CLOSED to _BG_W_OPEN on hover
    Image switches from closed→open at midpoint of animation."""

    _VW          = 90     # view width  (pt)
    _VH          = 68     # view height (pt)
    _TOP_CROP    = 0.14   # fraction cropped from top (bills area)
    _BOT_CROP    = 0.02   # fraction cropped from bottom (coin area)
    _BG_W_CLOSED = 62     # white bg width when closed (wallet body width)
    _BG_W_OPEN   = 90     # white bg width when open (full view width)

    def initWithFrame_(self, frame):
        self = objc.super(_WalletView, self).initWithFrame_(frame)
        if self is None:
            return None
        _dir = os.path.dirname(os.path.abspath(__file__))
        w, h = float(self._VW), float(self._VH)

        def _prescale(path):
            raw = AppKit.NSImage.alloc().initWithContentsOfFile_(path)
            if raw is None:
                return None
            out = AppKit.NSImage.alloc().initWithSize_(AppKit.NSMakeSize(w, h))
            out.lockFocus()
            raw.drawInRect_fromRect_operation_fraction_(
                AppKit.NSMakeRect(0, 0, w, h), AppKit.NSZeroRect,
                AppKit.NSCompositeSourceOver, 1.0)
            out.unlockFocus()
            return out

        self._img_close = _prescale(os.path.join(_dir, "wallet-close.png"))
        self._img_open  = _prescale(os.path.join(_dir, "wallet-light.png"))
        self._frac   = 0.0
        self._target = 0.0
        self._timer  = None
        return self

    def updateTrackingAreas(self):
        objc.super(_WalletView, self).updateTrackingAreas()
        for a in list(self.trackingAreas()):
            self.removeTrackingArea_(a)
        opts = (AppKit.NSTrackingMouseEnteredAndExited |
                AppKit.NSTrackingCursorUpdate |
                AppKit.NSTrackingActiveAlways |
                AppKit.NSTrackingInVisibleRect)
        self.addTrackingArea_(
            AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(), opts, self, None))

    def cursorUpdate_(self, event):
        AppKit.NSCursor.pointingHandCursor().set()

    def _start_timer(self, target):
        self._target = target
        if self._timer is None:
            self._timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1/60, self, _WalletView.tick_, None, True)
            AppKit.NSRunLoop.mainRunLoop().addTimer_forMode_(
                self._timer, AppKit.NSRunLoopCommonModes)

    def tick_(self, timer):
        step = 0.12
        if self._target > self._frac:
            self._frac = min(self._frac + step, self._target)
        else:
            self._frac = max(self._frac - step, self._target)
        self.setNeedsDisplay_(True)
        if abs(self._frac - self._target) < 0.001:
            self._frac = self._target
            timer.invalidate()
            self._timer = None

    def mouseEntered_(self, event):
        self._start_timer(1.0)

    def mouseExited_(self, event):
        self._start_timer(0.0)

    def mouseDown_(self, event):
        import subprocess
        subprocess.Popen(["open", "https://pay.alexbic.net/?mode=donate"])

    def acceptsFirstMouse_(self, event):
        return True

    def resetCursorRects(self):
        self.addCursorRect_cursor_(self.bounds(), AppKit.NSCursor.pointingHandCursor())

    def drawRect_(self, rect):
        f  = self._frac
        W  = float(self._VW)
        H  = float(self._VH)
        full = AppKit.NSMakeRect(0, 0, W, H)

        # ── Layer 1: white bg — only on dark themes, only this animates ─────────
        bg_lum = (C_BG[0] + C_BG[1] + C_BG[2]) / 3
        if bg_lum < 0.5:   # dark theme — wallet needs white backdrop for contrast
            bg_y = H * self._BOT_CROP
            bg_h = H * (1.0 - self._TOP_CROP - self._BOT_CROP)
            bg_w = self._BG_W_CLOSED + (self._BG_W_OPEN - self._BG_W_CLOSED) * f
            bg_x = W - bg_w   # right-aligned
            bg_path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                AppKit.NSMakeRect(bg_x, bg_y, bg_w, bg_h), 8, 8)
            AppKit.NSColor.whiteColor().setFill()
            bg_path.fill()

        # ── Layer 2: closed wallet — static, full view, fades out ────────────
        if self._img_close:
            self._img_close.drawInRect_fromRect_operation_fraction_(
                full, AppKit.NSZeroRect, AppKit.NSCompositeSourceOver, 1.0 - f)

        # ── Layer 3: open wallet — static, full view, fades in ───────────────
        if self._img_open:
            self._img_open.drawInRect_fromRect_operation_fraction_(
                full, AppKit.NSZeroRect, AppKit.NSCompositeSourceOver, f)


def _draw_wf_bars(bars, peaks, bounds, bar_w=3.0, gap=2.5):
    """Draw equalizer bars with peak-hold dots and idle breathing animation.
    bar_w and gap are fixed in points; number of bars is computed from width."""
    b     = bounds
    w, h  = b.size.width, b.size.height
    n_src = len(bars)
    # Fit as many bars as possible into available width
    n     = max(4, int((w + gap) / (bar_w + gap)))
    r     = bar_w / 2

    # Total signal energy — drives idle vs active look
    energy = sum(bars) / max(1, n_src)
    active = energy > 0.015

    total_w = n * bar_w + (n - 1) * gap
    x0      = (w - total_w) / 2   # center the group

    for i in range(n):
        src_i = int(i * n_src / n)
        amp   = bars[src_i]
        peak  = peaks[src_i] if peaks else 0.0

        if active:
            bh    = max(2.0, amp * h * 0.90)
            color = C_BAR_ON if amp > 0.05 else C_BAR_OFF
        else:
            # Idle breathing: gentle sine wave rippling across bars
            phase = _wf_t * 2.5 + i * 0.55
            idle  = 0.10 + 0.08 * math.sin(phase)
            bh    = max(2.0, idle * h)
            color = C_BAR_OFF

        x = x0 + i * (bar_w + gap)
        y = (h - bh) / 2
        p = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSMakeRect(x, y, bar_w, bh), r, r)
        color.set()
        p.fill()

        # Peak-hold dot: 2px bright mark above the bar
        if active and peak > 0.08:
            dot_h = max(1.5, bar_w * 0.5)
            dot_y = (h - peak * h * 0.90) / 2 - dot_h - 1
            if dot_y > 0:
                dp = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    AppKit.NSMakeRect(x, dot_y, bar_w, dot_h), r * 0.5, r * 0.5)
                C_BAR_ON.colorWithAlphaComponent_(0.85).set()
                dp.fill()


class WaveformView(AppKit.NSView):
    """Reactive equalizer bars with peak-hold and idle breathing."""

    def drawRect_(self, rect):
        with _wf_lock:
            bars  = list(_wf_bars)
            peaks = list(_wf_peaks)
        _draw_wf_bars(bars, peaks, self.bounds(), bar_w=3.0, gap=2.5)


class _SilentWaveformView(AppKit.NSView):
    """Compact equalizer for silent strip — same bar size, fewer bars fit."""

    def drawRect_(self, rect):
        with _wf_lock:
            bars  = list(_wf_bars)
            peaks = list(_wf_peaks)
        _draw_wf_bars(bars, peaks, self.bounds(), bar_w=3.5, gap=2.5)


# Module-level equalizer animation state
_eq_t             = 0.0   # scan position 0→1→0 (for scan mode)
_eq_dir           = 1     # scan direction: +1 or -1
_eq_pulse_t       = 0.0   # pulse position 0→1 repeating (for pulse-from-center mode)
_eq_countdown_t   = 0.0   # countdown fill 0→1 (for countdown mode)
_eq_countdown_start = 0.0 # time.time() when countdown started (0 = inactive)
_eq_countdown_dur   = 2.0 # countdown duration in seconds

class EqBarsView(AppKit.NSView):
    """Equal-height equalizer bars with animated highlight.

    Mode 0 (scan): bright window slides left → right → left.
    Mode 1 (pulse): two bright fronts expand from center to edges, then reset.
    """
    _N    = 18   # number of bars
    _mode = 0    # 0=scan, 1=pulse
    _col  = None

    def setMode_(self, m): self._mode = m
    def setCol_(self, c):  self._col  = c

    def drawRect_(self, rect):
        global _eq_t, _eq_pulse_t
        b    = self.bounds()
        w, h = b.size.width, b.size.height
        col  = self._col if self._col else _rgba(1.0, 0.3, 0.7, 1.0)
        bar_w = 3.0
        gap   = 2.5
        n     = max(4, int((w + gap) / (bar_w + gap)))
        total = n * bar_w + (n - 1) * gap
        x0    = (w - total) / 2   # center group
        PI2   = math.pi * 2

        for i in range(n):
            fi = i / max(1, n - 1)   # 0.0 → 1.0 across bars

            if self._mode == 0:
                # Gaussian peak slides left → right → left
                peak_pos = 0.12 + 0.76 * _eq_t
                sigma    = 0.18
                dist     = fi - peak_pos
                h_factor = 0.06 + 0.94 * math.exp(-(dist * dist) / (2 * sigma * sigma))
                alpha    = 0.25 + 0.75 * h_factor
                bar_col  = col
            elif self._mode == 1:
                # Ripple spreads outward from center
                center    = 0.5
                dc        = abs(fi - center) * 2.0
                sigma_env = 0.65
                envelope  = math.exp(-(dc * dc) / (2 * sigma_env * sigma_env))
                ripple    = 0.5 + 0.5 * math.sin(PI2 * (dc * 1.5 - _eq_pulse_t))
                h_factor  = max(0.05, envelope * (0.20 + 0.80 * ripple))
                alpha     = 0.20 + 0.80 * envelope
                bar_col   = col
            else:
                # Countdown fill: bars fill left→right with green→red gradient
                filled   = fi <= _eq_countdown_t
                rr       = min(1.0, fi * 2.0)          # 0→1 as bar fills
                gg       = max(0.0, 1.0 - fi * 1.4)    # 1→0 fading to red
                bar_col  = _rgba(rr, gg, 0.05, 1.0)
                h_factor = 0.80 if filled else 0.30
                alpha    = 0.95 if filled else 0.15

            bar_h = max(2.0, h_factor * h)
            x     = x0 + i * (bar_w + gap)
            y     = (h - bar_h) / 2
            r     = bar_w / 2
            p     = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                AppKit.NSMakeRect(x, y, bar_w, bar_h), r, r)
            bar_col.colorWithAlphaComponent_(alpha).set()
            p.fill()


class _SilentBgView(AppKit.NSView):
    """Opaque pill background for silent strip — uses theme C_BG colour."""
    def drawRect_(self, rect):
        b = self.bounds()
        r = min(b.size.height / 2, 14.0)
        p = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, r, r)
        br, bg, bb = C_BG
        _rgba(br, bg, bb, 0.96).set()
        p.fill()


class _AppIconView(AppKit.NSView):
    """Draws app icon image directly — no NSImageView bezel, no frame, clean.
    Call applyRoundedMask_() after initWithFrame_ to clip iOS-style white bezel."""
    _img = None

    def applyRoundedMask_(self, radius):
        self.setWantsLayer_(True)
        lay = self.layer()
        lay.setCornerRadius_(radius)
        lay.setMasksToBounds_(True)

    def setImage_(self, img):
        self._img = img
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        if not self._img:
            return
        self._img.drawInRect_fromRect_operation_fraction_(
            self.bounds(), AppKit.NSZeroRect, AppKit.NSCompositeSourceOver, 1.0)

    def mouseDown_(self, event):
        if event.clickCount() == 2:
            _main(_toggle_expand)

    def acceptsFirstMouse_(self, event):
        return True


class _HoverOverlayView(AppKit.NSView):
    """Semi-transparent overlay shown on hover during LLM processing; click = interrupt.
    Works for both silent strip (_silent_interrupt_fn) and main window (_proc_interrupt_fn).
    Text is drawn directly in drawRect_ to avoid NSTextField swallowing mouse events."""
    def drawRect_(self, rect):
        # Clear dirty rect first — prevents double-draw artifact when alpha changes
        AppKit.NSColor.clearColor().set()
        AppKit.NSRectFill(rect)
        b = self.bounds()
        TOP_PAD  = 8   # top inset so overlay doesn't kiss the separator
        bg_rect  = AppKit.NSMakeRect(0, 0, b.size.width, b.size.height - TOP_PAD)
        r        = min(bg_rect.size.height / 2, 12.0)
        p = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bg_rect, r, r)
        _rgba(0.05, 0.07, 0.05, 0.92).set()
        p.fill()
        hint = getattr(self, '_hint', None)
        if hint:
            ps = AppKit.NSMutableParagraphStyle.alloc().init()
            ps.setAlignment_(AppKit.NSTextAlignmentCenter)
            sz    = 14
            astr  = AppKit.NSAttributedString.alloc().initWithString_attributes_(
                hint, {
                    AppKit.NSFontAttributeName:            _mono(sz),    # light mono
                    AppKit.NSForegroundColorAttributeName: C_TEXT,       # scheme text color
                    AppKit.NSParagraphStyleAttributeName:  ps,
                })
            text_h = sz + 4
            # center within the padded background rect
            cy = (bg_rect.size.height - text_h) / 2
            astr.drawInRect_(AppKit.NSMakeRect(8, cy, b.size.width - 16, text_h))

    def mouseDown_(self, event):
        fn = _proc_interrupt_fn if getattr(self, '_is_main', False) else _silent_interrupt_fn
        if fn:
            import threading as _th
            _th.Thread(target=fn, daemon=True).start()

    def acceptsFirstMouse_(self, event):
        return True


class _SilentContentView(AppKit.NSView):
    """Transparent content view for silent strip; provides mouse tracking for LLM hover."""
    def mouseEntered_(self, event):
        if _silent_hover_v:
            _silent_hover_v.setHidden_(False)

    def mouseExited_(self, event):
        if _silent_hover_v:
            _silent_hover_v.setHidden_(True)

    def updateTrackingAreas(self):
        for a in list(self.trackingAreas()):
            self.removeTrackingArea_(a)
        opts = (AppKit.NSTrackingMouseEnteredAndExited |
                AppKit.NSTrackingActiveAlways |
                AppKit.NSTrackingInVisibleRect)
        self.addTrackingArea_(
            AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(), opts, self, None))

    def acceptsFirstMouse_(self, event): return True


class _FlippedView(AppKit.NSView):
    """NSView with y=0 at top (for scroll lists)."""
    def isFlipped(self): return True


class _DotSep(AppKit.NSView):
    """Draws a perfectly-centred green dot for scenario separators."""
    def drawRect_(self, rect):
        b  = self.bounds()
        cx = b.size.width  / 2
        cy = b.size.height / 2
        r  = 1.8
        path = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
            AppKit.NSMakeRect(cx - r, cy - r, r * 2, r * 2))
        _rgba(0.33, 1.00, 0.33, 0.88).set()
        path.fill()


class _HoverBtn(AppKit.NSButton):
    """Borderless button that brightens on hover — for terminal-style checkboxes."""

    def updateTrackingAreas(self):
        objc.super(_HoverBtn, self).updateTrackingAreas()
        for a in list(self.trackingAreas()):
            self.removeTrackingArea_(a)
        opts = (AppKit.NSTrackingMouseEnteredAndExited |
                AppKit.NSTrackingCursorUpdate |
                AppKit.NSTrackingActiveAlways |
                AppKit.NSTrackingInVisibleRect)
        area = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), opts, self, None)
        self.addTrackingArea_(area)

    def cursorUpdate_(self, event):
        AppKit.NSCursor.pointingHandCursor().set()

    def mouseEntered_(self, event):
        lbl = getattr(self, '_chk_lbl', "")
        sz  = getattr(self, '_chk_sz',  9)
        self.setAttributedTitle_(_atitle(lbl, size=sz, color=C_GREEN_BR))

    def mouseExited_(self, event):
        lbl = getattr(self, '_chk_lbl',    "")
        sz  = getattr(self, '_chk_sz',      9)
        col = getattr(self, '_normal_col', C_GREEN_DIM)
        self.setAttributedTitle_(_atitle(lbl, size=sz, color=col))


class _BlockHoverBtn(AppKit.NSButton):
    """Hover button inside rich blocks — uses fixed-bounds tracking so it works when hidden."""

    def refreshTracking(self):
        """Rebuild tracking area; call after setHidden_(False) so hover fires correctly."""
        for a in list(self.trackingAreas()):
            self.removeTrackingArea_(a)
        opts = (AppKit.NSTrackingMouseEnteredAndExited |
                AppKit.NSTrackingCursorUpdate |
                AppKit.NSTrackingActiveAlways)
        area = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), opts, self, None)
        self.addTrackingArea_(area)

    def cursorUpdate_(self, event):
        AppKit.NSCursor.pointingHandCursor().set()

    def mouseEntered_(self, event):
        self.setAttributedTitle_(_atitle(
            getattr(self, '_chk_lbl', ""),
            size=getattr(self, '_chk_sz', 9),
            color=C_GREEN_BR))

    def mouseExited_(self, event):
        self.setAttributedTitle_(_atitle(
            getattr(self, '_chk_lbl', ""),
            size=getattr(self, '_chk_sz', 9),
            color=getattr(self, '_normal_col', C_GREEN_DIM)))


class _HistCtrl(AppKit.NSObject):
    """Per-panel state + ObjC actions for history checkboxes."""

    def toggle_(self, sender):
        idx = int(sender.tag())
        if idx == -1:
            if len(self._sel) == len(self._items):
                self._sel.clear()
            else:
                self._sel = set(range(len(self._items)))
        else:
            if idx in self._sel:
                self._sel.discard(idx)
            else:
                self._sel.add(idx)
        self._refresh()

    def delete_(self, sender):
        ids = [self._items[i]["id"] for i in sorted(self._sel)
               if 0 <= i < len(self._items)]
        if _hist_panel and _hist_panel.isVisible():
            _hist_panel.orderOut_(None)
        if ids and self._on_delete:
            self._on_delete(ids)   # main.py removes by UUID, calls refresh_hist_panel

    def _selected_items(self):
        """Return selected items oldest-first."""
        indices = sorted(self._sel, reverse=True)
        return [self._items[i] for i in indices if 0 <= i < len(self._items)]

    def histReplace_(self, sender):
        """Replace current overlay text with selected items (no new history entry)."""
        items_sel = self._selected_items()
        combined  = "\n\n".join(item["full"] for item in items_sel)
        loaded_id = items_sel[0]["id"] if len(items_sel) == 1 else None
        if _hist_panel and _hist_panel.isVisible():
            _hist_panel.orderOut_(None)
        if combined:
            _load_history_combined(combined, loaded_id=loaded_id)

    def histMerge_(self, sender):
        """Merge selected items into ONE new block in the current session, soft-delete sources."""
        items_sel = self._selected_items()
        texts = []
        for item in items_sel:
            if item.get("type") == "session":
                for t in (item.get("blocks_text") or []):
                    if t.strip():
                        texts.append(t.strip())
            else:
                t = item.get("full", "").strip()
                if t:
                    texts.append(t)
        combined   = "\n\n".join(texts)
        source_ids = [item["id"] for item in items_sel]
        if not combined:
            return
        new_id = None
        if getattr(self, '_on_merge', None):
            new_id = self._on_merge(combined, source_ids)
        if _hist_panel and _hist_panel.isVisible():
            _hist_panel.orderOut_(None)
        _add_rich_block(combined, hist_id=new_id)

    def histAppend_(self, sender):
        """Add selected items to current session as separate blocks.
        Sessions are expanded into their individual blocks.
        """
        items_sel = self._selected_items()
        if _hist_panel and _hist_panel.isVisible():
            _hist_panel.orderOut_(None)
        for item in items_sel:
            if item.get("type") == "session":
                block_texts = item.get("blocks_text") or []
                block_ids   = item.get("blocks") or []
                for i, text in enumerate(block_texts):
                    if not text.strip():
                        continue
                    bid = block_ids[i] if i < len(block_ids) else None
                    _add_rich_block(text.strip(), hist_id=bid)
            else:
                text = item.get("full", "").strip()
                if text:
                    _add_rich_block(text, hist_id=item.get("id"))

    def histFilter_(self, sender):
        global _hist_filter
        mode = str(sender.representedObject())
        if mode == _hist_filter:
            return
        _hist_filter = mode
        # Highlight active tab
        for m, btn in (self._tab_btns or {}).items():
            col = C_CYAN if m == mode else C_GREEN_DIM
            btn.setAttributedTitle_(_atitle(self._tab_labels.get(m, m), size=9, color=col))
        # Re-filter and rebuild scroll content
        self._items = _hist_filter_items(self._all_items, mode)
        self._sel   = set()
        self._chk   = {}
        if self._all_btn:
            self._all_btn._chk_lbl    = "[ ]"
            self._all_btn._normal_col = C_GREEN_DIM
            self._all_btn.setAttributedTitle_(_atitle("[ ]", size=9, color=C_GREEN_DIM))
        _rebuild_hist_scroll(self)

    def _refresh(self):
        n      = len(self._items)
        all_on = (n > 0 and len(self._sel) == n)
        if self._all_btn:
            lbl = "[✓]" if all_on else "[ ]"
            col = C_GREEN_BR if all_on else C_GREEN_DIM
            self._all_btn._chk_lbl   = lbl
            self._all_btn._normal_col = col
            self._all_btn.setAttributedTitle_(_atitle(lbl, size=9, color=col))
        for idx, btn in self._chk.items():
            on  = idx in self._sel
            lbl = "[✓]" if on else "[ ]"
            col = C_GREEN_BR if on else C_GREEN_DIM
            btn._chk_lbl   = lbl
            btn._normal_col = col
            btn.setAttributedTitle_(_atitle(lbl, size=9, color=col))
        has = len(self._sel) > 0
        for attr in ('_del_btn', '_merge_btn', '_append_btn', '_replace_btn'):
            b = getattr(self, attr, None)
            if b:
                b.setHidden_(not has)


class DragPanel(AppKit.NSPanel):
    """Borderless draggable panel — drag from anywhere via sendEvent_ interception."""
    def canBecomeKeyWindow(self): return True

    def cancelOperation_(self, sender):
        hide(force=True)

    def sendEvent_(self, event):
        _LD = 1; _LU = 2; _LDRAG = 6; _THRESH2 = 25.0   # 5 px threshold
        t = event.type()
        if t == _LD:
            self._wd_s = AppKit.NSEvent.mouseLocation()
            self._wd_o = self.frame().origin
            self._wd_a = False
        elif t == _LDRAG and getattr(self, '_wd_s', None) is not None:
            cur = AppKit.NSEvent.mouseLocation()
            dx  = cur.x - self._wd_s.x
            dy  = cur.y - self._wd_s.y
            if self._wd_a or dx*dx + dy*dy > _THRESH2:
                self._wd_a = True
                new_x = self._wd_o.x + dx
                new_y = self._wd_o.y + dy
                try: _snap_attached_panels_live(new_x, new_y)
                except Exception: pass
                self.setFrameOrigin_(AppKit.NSMakePoint(new_x, new_y))
                _reposition_attached_panels()
        elif t == _LU:
            did_drag = getattr(self, '_wd_a', False)
            self._wd_s = None; self._wd_a = False
            if did_drag:
                for _key, _pname in [("cfg",       "_cfg_panel"),
                                      ("hist",      "_hist_panel"),
                                      ("editor",    "_sc_editor_panel"),
                                      ("providers", "_prov_panel")]:
                    _p = globals().get(_pname)
                    if _p and _p.isVisible():
                        try:
                            if _magnet_on.get(_key, False):
                                _smart_snap_panel(_key, _p)
                            else:
                                _repel_from_others(_p)
                        except Exception:
                            pass
                try: _magnet_save()
                except Exception: pass
        objc.super(DragPanel, self).sendEvent_(event)


class _SilentPanel(AppKit.NSPanel):
    """Thin header-strip panel for silent mode — draggable, Escape or click to dismiss."""
    def canBecomeKeyWindow(self): return True

    def cancelOperation_(self, sender):
        _main(lambda: hide(force=True))

    def mouseDown_(self, ev):
        self._d = ev.locationInWindow()

    def mouseDragged_(self, ev):
        if hasattr(self, "_d"):
            loc = ev.locationInWindow()
            f   = self.frame()
            self.setFrameOrigin_(AppKit.NSMakePoint(
                f.origin.x + loc.x - self._d.x,
                f.origin.y + loc.y - self._d.y,
            ))


class _DropPanel(AppKit.NSPanel):
    """Floating drop-down panel (history/settings). Drag from anywhere moves _win."""
    def canBecomeKeyWindow(self): return True

    def cancelOperation_(self, sender):
        if globals().get("_editing_scenario") and globals().get("_sc_editor_panel"):
            _main(lambda: _maybe_close_editor(pending_fn=None))
        else:
            _main(lambda: hide(force=True))

    def sendEvent_(self, event):
        _LD = 1; _LU = 2; _LDRAG = 6; _THRESH2 = 25.0
        t = event.type()
        w   = globals().get("_win")
        key = getattr(self, '_panel_key', None)
        is_attached = _magnet_on.get(key, True) if key else True
        if t == _LD:
            self._wd_s = AppKit.NSEvent.mouseLocation()
            if is_attached and w:
                self._wd_o = w.frame().origin
            else:
                self._wd_o = self.frame().origin
            self._wd_a = False
        elif t == _LDRAG and getattr(self, '_wd_s', None) is not None and self._wd_o is not None:
            cur = AppKit.NSEvent.mouseLocation()
            dx  = cur.x - self._wd_s.x
            dy  = cur.y - self._wd_s.y
            if self._wd_a or dx*dx + dy*dy > _THRESH2:
                self._wd_a = True
                if is_attached and w:
                    new_x = self._wd_o.x + dx
                    new_y = self._wd_o.y + dy
                    try: _snap_attached_panels_live(new_x, new_y)
                    except Exception: pass
                    w.setFrameOrigin_(AppKit.NSMakePoint(new_x, new_y))
                    _reposition_attached_panels()
                else:
                    self.setFrameOrigin_(AppKit.NSMakePoint(self._wd_o.x + dx, self._wd_o.y + dy))
        elif t == _LU:
            did_drag = getattr(self, '_wd_a', False)
            self._wd_s = None; self._wd_a = False
            if did_drag:
                if is_attached:
                    try: _magnet_save()
                    except Exception: pass
                else:
                    if key:
                        try: _update_panel_drag_end(key, self)
                        except Exception: pass
                    try: _repel_from_others(self)
                    except Exception: pass
        objc.super(_DropPanel, self).sendEvent_(event)


class _AboutPanel(AppKit.NSPanel):
    """Standalone About card panel. Intercepts mouseMoved to control cursor directly."""
    def canBecomeKeyWindow(self): return True

    def cancelOperation_(self, sender):
        _main(_hide_about_view)

    def sendEvent_(self, event):
        _MOUSE_MOVED = 5
        if event.type() == _MOUSE_MOVED:
            cv = self.contentView()
            if cv:
                loc  = event.locationInWindow()
                hit  = cv.hitTest_(loc)
                # Walk up the view hierarchy: if any ancestor is a link/wallet → hand
                v = hit
                cursor = AppKit.NSCursor.arrowCursor()
                while v is not None:
                    if isinstance(v, (_LinkButton, _WalletView)):
                        cursor = AppKit.NSCursor.pointingHandCursor()
                        break
                    v = v.superview()
                cursor.set()
        objc.super(_AboutPanel, self).sendEvent_(event)


class _EditorPanel(AppKit.NSPanel):
    """Floating editor panel that accepts key input (text fields work)."""
    def canBecomeKeyWindow(self): return True
    def canBecomeMainWindow(self): return False

    def becomeKeyWindow(self):
        """Re-activate app when panel regains focus (e.g. after user switches back)."""
        objc.super(_EditorPanel, self).becomeKeyWindow()
        AppKit.NSApp.activateIgnoringOtherApps_(True)

    def cancelOperation_(self, sender):
        _main(lambda: _maybe_close_editor(pending_fn=None))

    def sendEvent_(self, event):
        _LD = 1; _LU = 2; _LDRAG = 6; _THRESH2 = 25.0
        t = event.type()
        w   = globals().get("_win")
        key = getattr(self, '_panel_key', None)
        is_attached = _magnet_on.get(key, False) if key else False
        if t == _LD:
            self._wd_s = AppKit.NSEvent.mouseLocation()
            self._wd_o = w.frame().origin if (is_attached and w) else self.frame().origin
            self._wd_a = False
        elif t == _LDRAG and getattr(self, '_wd_s', None) is not None and self._wd_o is not None:
            cur = AppKit.NSEvent.mouseLocation()
            dx  = cur.x - self._wd_s.x
            dy  = cur.y - self._wd_s.y
            if self._wd_a or dx*dx + dy*dy > _THRESH2:
                self._wd_a = True
                if is_attached and w:
                    new_x = self._wd_o.x + dx
                    new_y = self._wd_o.y + dy
                    try: _snap_attached_panels_live(new_x, new_y)
                    except Exception: pass
                    w.setFrameOrigin_(AppKit.NSMakePoint(new_x, new_y))
                    _reposition_attached_panels()
                else:
                    self.setFrameOrigin_(AppKit.NSMakePoint(self._wd_o.x + dx, self._wd_o.y + dy))
        elif t == _LU:
            did_drag = getattr(self, '_wd_a', False)
            self._wd_s = None; self._wd_a = False
            if did_drag:
                if key:
                    try: _update_panel_drag_end(key, self)
                    except Exception: pass
                if not is_attached:
                    try: _repel_from_others(self)
                    except Exception: pass
        objc.super(_EditorPanel, self).sendEvent_(event)

    def performKeyEquivalent_(self, event):
        """Forward Cmd+C/V/X/A/Z directly to firstResponder actions."""
        CMD  = AppKit.NSEventModifierFlagCommand
        MASK = AppKit.NSEventModifierFlagDeviceIndependentFlagsMask
        if event.modifierFlags() & MASK == CMD:
            kc = event.keyCode()
            fr = self.firstResponder()
            if fr is not None:
                if kc == 9 and fr.respondsToSelector_("paste:"):    # v
                    fr.paste_(self); return True
                if kc == 8 and fr.respondsToSelector_("copy:"):     # c
                    fr.copy_(self); return True
                if kc == 7 and fr.respondsToSelector_("cut:"):      # x
                    fr.cut_(self); return True
                if kc == 0 and fr.respondsToSelector_("selectAll:"): # a
                    fr.selectAll_(self); return True
                if kc == 6:                                          # z — undo
                    um = fr.undoManager() if fr.respondsToSelector_("undoManager") else None
                    if um and um.canUndo():
                        um.undo(); return True
        return objc.super(_EditorPanel, self).performKeyEquivalent_(event)


def _copy_mod_flags():
    """Return NSEventModifierFlag mask for the configured copy-to-clipboard hotkey."""
    CTRL  = AppKit.NSEventModifierFlagControl
    CMD   = AppKit.NSEventModifierFlagCommand
    SHIFT = AppKit.NSEventModifierFlagShift
    return {
        "ctrl":       CTRL,
        "cmd":        CMD,
        "ctrl+shift": CTRL | SHIFT,
        "cmd+shift":  CMD | SHIFT,
    }.get(_st.get("hotkey_copy", "ctrl"), CTRL)


class TerminalTextView(AppKit.NSTextView):
    """NSTextView: Shift+Enter → immediate paste, terminal block cursor."""

    def keyDown_(self, event):
        ESC   = 53
        ENTER = 36
        SHIFT = AppKit.NSEventModifierFlagShift
        OPT   = AppKit.NSEventModifierFlagOption
        MASK  = AppKit.NSEventModifierFlagDeviceIndependentFlagsMask
        kc    = event.keyCode()
        mods  = event.modifierFlags() & MASK

        # Navigate up into last block when cursor is at start of _tv
        if kc in (51, 123, 126) and not mods:   # ⌫ ← ↑
            sel = self.selectedRange()
            if sel.location == 0 and sel.length == 0 and _rich_blocks:
                last = _rich_blocks[-1]
                if last._inner_tv and _win:
                    _win.makeFirstResponder_(last._inner_tv)
                    ln = len(str(last._inner_tv.string()))
                    last._inner_tv.setSelectedRange_(AppKit.NSMakeRange(ln, 0))
                    return

        copy_m = _copy_mod_flags()
        if kc == ENTER and mods == (SHIFT | OPT):
            # Opt+Shift+Enter → paste keeping MD formatting as-is
            if _on_paste_cb:
                _on_paste_cb(mode="md")
        elif kc == ENTER and copy_m and mods == (copy_m | OPT):
            # copy_mod+Opt+Enter → copy with MD formatting (no paste, overlay stays open)
            if _on_copy_cb:
                _on_copy_cb(mode="md")
        elif kc == ENTER and copy_m and mods == copy_m:
            # copy_mod+Enter → copy plain text (no paste, overlay stays open)
            if _on_copy_cb:
                _on_copy_cb()
        elif kc == ENTER and mods == SHIFT:
            if _on_paste_cb:
                _on_paste_cb()   # always raw, no full_default
        elif kc == ENTER and not mods:
            # Plain Enter → finalize manually typed text into a block
            if _tv and str(_tv.string()).strip():
                _finalize_tv_to_block(add_to_history=True)
            else:
                objc.super(TerminalTextView, self).keyDown_(event)
        elif kc == ESC:
            hide(force=True)
        else:
            objc.super(TerminalTextView, self).keyDown_(event)

    def paste_(self, sender):
        """RTF/HTML → convert to Markdown, insert at cursor; plain text → paste normally."""
        pb     = AppKit.NSPasteboard.generalPasteboard()
        tps    = list(pb.types() or [])
        RTF_T  = AppKit.NSPasteboardTypeRTF
        HTML_T = "public.html"
        has_rtf  = RTF_T  in tps
        has_html = HTML_T in tps

        # Check plain text first: if it already contains markdown code fences (```),
        # use it directly — RTF/HTML from editors (VS Code, Obsidian…) strips the
        # ``` markers during conversion, so we must not go through html2text in that case.
        if has_rtf or has_html:
            plain = pb.stringForType_(AppKit.NSPasteboardTypeString)
            if plain and re.search(r'^```', plain, re.MULTILINE):
                # Plain text is raw markdown with code fences — use it as-is
                _add_rich_block(plain.strip())
                return

            html_str = None
            # Prefer HTML directly from pasteboard
            if has_html:
                try:
                    html_data = pb.dataForType_(HTML_T)
                    if html_data:
                        html_str = bytes(html_data).decode('utf-8', errors='replace')
                except Exception:
                    pass
            # Fall back: RTF → NSAttributedString → HTML
            if html_str is None and has_rtf:
                try:
                    rtf_data = pb.dataForType_(RTF_T)
                    if rtf_data:
                        result = AppKit.NSAttributedString.alloc(
                            ).initWithRTF_documentAttributes_(rtf_data, None)
                        if result and result[0]:
                            ns_as = result[0]
                            out_data, _, _ = ns_as.dataFromRange_documentAttributes_error_(
                                AppKit.NSMakeRange(0, ns_as.length()),
                                {AppKit.NSDocumentTypeDocumentAttribute: AppKit.NSHTMLTextDocumentType},
                                None)
                            if out_data:
                                html_str = bytes(out_data).decode('utf-8', errors='replace')
                except Exception:
                    pass
            if html_str:
                md_text = _html_to_md(html_str)
                if md_text:
                    _add_rich_block(md_text)
                    return
        # Plain text (or failed extraction) — paste into _tv normally
        objc.super(TerminalTextView, self).paste_(sender)
        _main(_after_paste_plain)

    def performKeyEquivalent_(self, event):
        """Explicitly handle Cmd+C/V/X/A/Z so they always work."""
        CMD  = AppKit.NSEventModifierFlagCommand
        MASK = AppKit.NSEventModifierFlagDeviceIndependentFlagsMask
        mods = event.modifierFlags() & MASK
        if mods == CMD:
            kc = event.keyCode()
            if kc == 9:
                fr = _win.firstResponder() if _win else None
                with open('/tmp/vi_debug.log', 'a') as _dbg:
                    _dbg.write(f"[TermTV.performKeyEquivalent_] Cmd+V, fr={type(fr).__name__ if fr else None}\n")
        # If a block's inner TV is focused, let it handle key equivalents itself.
        if _win:
            fr = _win.firstResponder()
            if fr is not self and isinstance(fr, _BlockTV):
                return objc.super(TerminalTextView, self).performKeyEquivalent_(event)
        if mods == CMD:
            kc = event.keyCode()
            if kc == 8:   # c
                self.copy_(self); return True
            elif kc == 9: # v
                self.paste_(self); return True
            elif kc == 7: # x
                self.cut_(self); return True
            elif kc == 0: # a
                self.selectAll_(self); return True
            elif kc == 6: # z
                um = self.undoManager()
                if um and um.canUndo(): um.undo()
                return True
        return objc.super(TerminalTextView, self).performKeyEquivalent_(event)

    def didChangeText(self):
        """Sync _st['text'] on every edit so scenarios always see current content."""
        objc.super(TerminalTextView, self).didChangeText()
        _main(_update_cursor_pos)
        mode = _st.get("mode")
        if mode in ("ready", "history_open"):
            raw = str(self.string()).rstrip('\n')
            _st["text"] = raw
            if mode == "history_open" and raw.strip():
                _st["mode"] = "ready"
                _show_buttons(True)
                _refresh_scenario_colors()
                _show_target_app_header()
            else:
                # Update action row visibility when content appears or disappears
                _main(_update_action_visibility)
            # MD detection on manual edit (only when no rich blocks exist)
            if not _st.get("rich_fmt"):
                was = _st.get("is_md", False)
                _st["is_md"] = _is_markdown(raw) if raw.strip() else False
                if _st["is_md"] != was:
                    _main(_update_format_indicator)
            _main(_relayout_doc_view)

    def setSelectedRanges_affinity_stillSelecting_(self, ranges, affinity, still):
        objc.super(TerminalTextView, self).setSelectedRanges_affinity_stillSelecting_(
            ranges, affinity, still)
        _main(_update_cursor_pos)


class _BlockCursor(AppKit.NSView):
    """Fake terminal block cursor — overlay NSView, always visible, 400ms blink."""
    _on = True

    def drawRect_(self, rect):
        if not self._on:
            return
        b   = self.bounds()
        C_C = _rgba(0.290, 0.871, 0.502, 0.90)
        C_G = _rgba(0.290, 0.871, 0.502, 0.60)
        ctx = AppKit.NSGraphicsContext.currentContext()
        ctx.saveGraphicsState()
        sh  = AppKit.NSShadow.alloc().init()
        sh.setShadowColor_(C_G)
        sh.setShadowBlurRadius_(6.0)
        sh.setShadowOffset_(AppKit.NSMakeSize(0, 0))
        sh.set()
        C_C.setFill()
        AppKit.NSBezierPath.fillRect_(b)
        ctx.restoreGraphicsState()

    def tick_(self, timer):
        self._on = not self._on
        self.setNeedsDisplay_(True)


class _ThinGreenScroller(AppKit.NSScroller):
    """3px green knob, transparent track."""

    def drawKnob(self):
        r = self.rectForPart_(getattr(AppKit, 'NSScrollerKnob', 2))
        if r.size.width <= 0 or r.size.height <= 0:
            return
        bar_w = 3.0
        bx = r.origin.x + r.size.width - bar_w - 1   # 1px from right edge
        ir = AppKit.NSMakeRect(bx, r.origin.y + 4, bar_w, max(4, r.size.height - 8))
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ir, 1.5, 1.5)
        _rgba(0.00, 0.80, 0.00, 0.55).set()
        path.fill()

    def drawKnobSlotInRect_highlight_(self, slotRect, highlighted):
        pass  # transparent track — no white background


class _ThinAccentScroller(AppKit.NSScroller):
    """4px knob in current theme accent color (C_GREEN_BR), transparent track.
    Used for accumulation scroll view to match active color scheme."""

    def drawKnob(self):
        r = self.rectForPart_(getattr(AppKit, 'NSScrollerKnob', 2))
        if r.size.width <= 0 or r.size.height <= 0:
            return
        bar_w = 4.0
        bx = r.origin.x + r.size.width - bar_w - 2   # 2px from right edge
        ir = AppKit.NSMakeRect(bx, r.origin.y + 4, bar_w, max(6, r.size.height - 8))
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ir, 2.0, 2.0)
        C_GREEN_BR.colorWithAlphaComponent_(0.70).set()
        path.fill()

    def drawKnobSlotInRect_highlight_(self, slotRect, highlighted):
        pass  # transparent track


# ── Rich text block view ──────────────────────────────────────────────────────

_RICH_LIGHT_BG  = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.96, 0.97, 0.96, 1.0)
_RICH_DARK_BG   = _rgba(0.02, 0.06, 0.02, 0.0)   # transparent — dark window shows through
_RICH_LINE_C    = _rgba(0.00, 0.78, 0.55, 0.90)   # left border line colour

_on_add_history_cb      = None   # kept for silent mode / scenario paths
_on_update_session_cb   = None   # () → called when a block is added; main.py upserts session
_on_session_end_cb      = None   # () → called on hide; main.py clears current session ID


class _BlockTV(AppKit.NSTextView):
    """Editable NSTextView inside a block — handles cursor navigation between blocks."""

    def didChangeText(self):
        objc.super(_BlockTV, self).didChangeText()
        block = getattr(self, '_parent_block', None)
        if block:
            _main(block._resize_to_content)
            _main(block._check_edits)

    def paste_(self, sender):
        """Paste plain text into block. Preserves markdown markers, strips rich formatting."""
        pb = AppKit.NSPasteboard.generalPasteboard()
        plain = pb.stringForType_(AppKit.NSPasteboardTypeString)
        if plain:
            rng = self.selectedRange()
            if self.shouldChangeTextInRange_replacementString_(rng, plain):
                ts = self.textStorage()
                fs = _st.get("font_size", 12)
                replacement = AppKit.NSAttributedString.alloc().initWithString_attributes_(
                    plain, {
                        AppKit.NSForegroundColorAttributeName: C_TEXT,
                        AppKit.NSFontAttributeName: _mono(fs),
                    })
                ts.replaceCharactersInRange_withAttributedString_(rng, replacement)
                self.didChangeText()
                self.setSelectedRange_(AppKit.NSMakeRange(rng.location + len(plain), 0))
            return
        objc.super(_BlockTV, self).paste_(sender)

    def performKeyEquivalent_(self, event):
        """Handle Cmd+key when this block TV is the active first responder."""
        if not _win or _win.firstResponder() is not self:
            return False
        CMD  = AppKit.NSEventModifierFlagCommand
        MASK = AppKit.NSEventModifierFlagDeviceIndependentFlagsMask
        mods = event.modifierFlags() & MASK
        if mods == CMD:
            kc = event.keyCode()
            with open('/tmp/vi_debug.log', 'a') as _dbg:
                _dbg.write(f"[_BlockTV.performKeyEquivalent_] Cmd+{kc}\n")
            if kc == 9:  self.paste_(self);    return True  # v
            if kc == 8:  self.copy_(self);     return True  # c
            if kc == 7:  self.cut_(self);      return True  # x
            if kc == 0:  self.selectAll_(self); return True # a
            if kc == 6:
                um = self.undoManager()
                if um and um.canUndo(): um.undo()
                return True
        return False

    def keyDown_(self, event):
        ENTER = 36
        SHIFT = AppKit.NSEventModifierFlagShift
        OPT   = AppKit.NSEventModifierFlagOption
        MASK  = AppKit.NSEventModifierFlagDeviceIndependentFlagsMask
        kc    = event.keyCode()
        mods  = event.modifierFlags() & MASK

        sel  = self.selectedRange()
        txt  = str(self.string())
        at_start = (sel.location == 0 and sel.length == 0)
        at_end   = (sel.location == len(txt) and sel.length == 0)
        bidx = getattr(self, '_block_idx', -1)

        # At start → move to end of previous block (or stay if first)
        if at_start and kc in (51, 123, 126) and not mods:   # ⌫ ← ↑
            if bidx > 0 and bidx - 1 < len(_rich_blocks):
                prev = _rich_blocks[bidx - 1]
                if prev._inner_tv and _win:
                    _win.makeFirstResponder_(prev._inner_tv)
                    pln = len(str(prev._inner_tv.string()))
                    prev._inner_tv.setSelectedRange_(AppKit.NSMakeRange(pln, 0))
                    return
        # At end → move to start of next block, or to _tv
        if at_end and kc in (124, 125) and not mods:         # → ↓
            if bidx >= 0 and bidx < len(_rich_blocks) - 1:
                nxt = _rich_blocks[bidx + 1]
                if nxt._inner_tv and _win:
                    _win.makeFirstResponder_(nxt._inner_tv)
                    nxt._inner_tv.setSelectedRange_(AppKit.NSMakeRange(0, 0))
                    return
            elif _tv and _win:
                _win.makeFirstResponder_(_tv)
                _tv.setSelectedRange_(AppKit.NSMakeRange(0, 0))
                return

        if kc == 53:   # ESC
            hide(force=True)
            return

        # Copy/paste hotkeys — same as TerminalTextView, operate on full context
        copy_m = _copy_mod_flags()
        if kc == ENTER and mods == (SHIFT | OPT):
            if _on_paste_cb: _on_paste_cb(mode="md")
            return
        elif kc == ENTER and copy_m and mods == (copy_m | OPT):
            if _on_copy_cb: _on_copy_cb(mode="md")
            return
        elif kc == ENTER and copy_m and mods == copy_m:
            if _on_copy_cb: _on_copy_cb()
            return
        elif kc == ENTER and mods == SHIFT:
            if _on_paste_cb: _on_paste_cb()   # always raw
            return

        objc.super(_BlockTV, self).keyDown_(event)


class _RichBlockView(AppKit.NSView):
    """Markdown block: no inner scroll, adaptive height, hover-reveal buttons.

    Layout (NSView, y=0 at bottom):
      [bh-BTN_AREA..bh]          button strip: "md" + "▸" (hidden until hover)
      [V_PAD..bh-BTN_AREA]       NSTextView sized to content (no scroll)
      x=0..BLOCK_BORDER_W        left line (green on hover, cyan in MD mode)

    States:
      default – rendered text, no line, no buttons
      hovered – green left line, buttons appear top-right
      md_mode – cyan left line always, raw Markdown text
    """

    def _do_setup(self, md_text, idx):
        self._md_text  = md_text
        self._idx      = idx
        self._md_mode  = False
        self._hovered  = False
        self._is_md_block = _is_markdown(md_text)
        self._inner_tv = None
        self._md_btn          = None
        self._cpy_btn         = None
        self._del_btn         = None
        self._rev_btn         = None
        self._sc_undo_btn     = None   # scenario undo — shown when block is active scenario result
        self._line_v          = None
        self._hist_id         = None
        self._original_hist_id = None
        self._original_text   = None
        self._has_edits       = False
        try:
            self._rendered = _md_to_styled_attrs(md_text)
        except Exception:
            self._rendered = AppKit.NSAttributedString.alloc().initWithString_(md_text)
        self._setup_ui()

    def _setup_ui(self):
        bw = int(self.frame().size.width)
        bh = int(self.frame().size.height)

        # ── NSTextView (direct, no scroll wrapper) ────────────────────────────
        tv_x = BLOCK_L_PAD
        tv_y = BLOCK_V_PAD
        tv_w = bw - BLOCK_L_PAD - BLOCK_R_PAD
        tv_h = max(1, bh - BLOCK_BTN_AREA - BLOCK_V_PAD)

        inner = _BlockTV.alloc().initWithFrame_(
            AppKit.NSMakeRect(tv_x, tv_y, tv_w, tv_h))
        inner._block_idx    = self._idx
        inner._parent_block = self
        inner.setEditable_(True)
        inner.setSelectable_(True)
        inner.setRichText_(True)
        inner.setAllowsUndo_(True)
        inner.setUsesFontPanel_(False)
        inner.setUsesRuler_(False)
        inner.setSmartInsertDeleteEnabled_(False)
        inner.setDrawsBackground_(False)
        inner.setVerticallyResizable_(False)
        inner.setHorizontallyResizable_(False)
        inner.textContainer().setWidthTracksTextView_(True)
        inner.textContainer().setHeightTracksTextView_(False)
        inner.textStorage().setAttributedString_(self._rendered)
        self.addSubview_(inner)
        self._inner_tv = inner

        # ── Hover buttons ─────────────────────────────────────────────────────
        BTN_H = 12
        BTN_Y = bh - BLOCK_BTN_AREA + (BLOCK_BTN_AREA - BTN_H) // 2

        del_b = _BlockHoverBtn.alloc().init()
        del_b._chk_lbl    = "×"
        del_b._chk_sz     = 10
        del_b._normal_col = C_REC
        del_b.setBordered_(False)
        del_b.setAttributedTitle_(_atitle("×", size=10, color=C_REC))
        del_b.setFrame_(AppKit.NSMakeRect(BLOCK_L_PAD, BTN_Y, 14, BTN_H))
        del_b.setTag_(self._idx)
        del_b.setTarget_(_btn_t)
        del_b.setAction_(BtnTarget.richDelete_)
        del_b.setHidden_(True)
        self.addSubview_(del_b)
        self._del_btn = del_b

        md_b = _BlockHoverBtn.alloc().init()
        md_b._chk_lbl    = "md"
        md_b._chk_sz     = 9
        md_b._normal_col = C_GREEN_DIM
        md_b.setBordered_(False)
        md_b.setAttributedTitle_(_atitle("md", size=9, color=C_GREEN_DIM))
        md_b.setFrame_(AppKit.NSMakeRect(bw - 44, BTN_Y, 22, BTN_H))
        md_b.setTag_(self._idx)
        md_b.setTarget_(_btn_t)
        md_b.setAction_(BtnTarget.richToggle_)
        # Always visible for markdown blocks; hidden-until-hover for plain text
        md_b.setHidden_(not self._is_md_block)
        self.addSubview_(md_b)
        self._md_btn = md_b

        cpy_b = _BlockHoverBtn.alloc().init()
        cpy_b._chk_lbl    = "→"
        cpy_b._chk_sz     = 10
        cpy_b._normal_col = C_GREEN_DIM
        cpy_b.setBordered_(False)
        cpy_b.setAttributedTitle_(_atitle("→", size=10, color=C_GREEN_DIM))
        cpy_b.setFrame_(AppKit.NSMakeRect(bw - 20, BTN_Y, 16, BTN_H))
        cpy_b.setTag_(self._idx)
        cpy_b.setTarget_(_btn_t)
        cpy_b.setAction_(BtnTarget.richCopy_)
        cpy_b.setHidden_(True)
        self.addSubview_(cpy_b)
        self._cpy_btn = cpy_b

        rev_b = _BlockHoverBtn.alloc().init()
        rev_b._chk_lbl    = "↩"
        rev_b._chk_sz     = 10
        rev_b._normal_col = C_GREEN_DIM
        rev_b.setBordered_(False)
        rev_b.setAttributedTitle_(_atitle("↩", size=10, color=C_GREEN_DIM))
        rev_b.setFrame_(AppKit.NSMakeRect(bw - 68, BTN_Y, 20, BTN_H))
        rev_b.setTag_(self._idx)
        rev_b.setTarget_(_btn_t)
        rev_b.setAction_(BtnTarget.richRevert_)
        rev_b.setHidden_(True)
        self.addSubview_(rev_b)
        self._rev_btn = rev_b

        # Scenario undo — always visible (not hover-gated) while block is active scenario result
        sc_undo_b = _BlockHoverBtn.alloc().init()
        sc_undo_b._chk_lbl    = "[↩ сц]"
        sc_undo_b._chk_sz     = 9
        sc_undo_b._normal_col = C_CYAN
        sc_undo_b.setBordered_(False)
        sc_undo_b.setAttributedTitle_(_atitle("[↩ сц]", size=9, color=C_CYAN))
        sc_undo_b.setFrame_(AppKit.NSMakeRect(bw - 96, BTN_Y, 26, BTN_H))
        sc_undo_b.setTarget_(_btn_t)
        sc_undo_b.setAction_(BtnTarget.undoScenario_)
        sc_undo_b.setHidden_(True)
        self.addSubview_(sc_undo_b)
        self._sc_undo_btn = sc_undo_b

        # ── Left line indicator ────────────────────────────────────────────────
        line_v = AppKit.NSView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, BLOCK_BORDER_W, bh))
        line_v.setWantsLayer_(True)
        line_v.layer().setBackgroundColor_(AppKit.NSColor.clearColor().CGColor())
        self.addSubview_(line_v)
        self._line_v = line_v

        self._setup_tracking()

    def _setup_tracking(self):
        opts = (AppKit.NSTrackingMouseEnteredAndExited |
                AppKit.NSTrackingActiveAlways)
        ta = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            AppKit.NSMakeRect(0, 0,
                              self.frame().size.width,
                              self.frame().size.height),
            opts, self, None)
        self.addTrackingArea_(ta)

    def updateTrackingAreas(self):
        objc.super(_RichBlockView, self).updateTrackingAreas()
        for ta in list(self.trackingAreas()):
            self.removeTrackingArea_(ta)
        self._setup_tracking()

    def _set_line_color(self, ns_color):
        if self._line_v:
            try:
                self._line_v.layer().setBackgroundColor_(ns_color.CGColor())
            except Exception:
                pass

    def mouseEntered_(self, event):
        self._hovered = True
        if not self._md_mode:
            self._set_line_color(C_TEXT)
        for btn in (self._del_btn, self._md_btn, self._cpy_btn):
            if btn:
                btn.setHidden_(False)
                btn.refreshTracking()
        if self._rev_btn and self._has_edits:
            self._rev_btn.setHidden_(False)
            self._rev_btn.refreshTracking()

    def mouseExited_(self, event):
        self._hovered = False
        if not self._md_mode:
            self._set_line_color(AppKit.NSColor.clearColor())
            # Keep [md] visible for markdown blocks even when not hovered
            if self._md_btn and not self._is_md_block:
                self._md_btn.setHidden_(True)
            if self._cpy_btn: self._cpy_btn.setHidden_(True)
        else:
            if self._cpy_btn: self._cpy_btn.setHidden_(True)
        if self._del_btn: self._del_btn.setHidden_(True)
        if self._rev_btn: self._rev_btn.setHidden_(True)
        # sc_undo_btn stays visible on mouse-exit (always shown when scenario active)

    def set_sc_undo(self, visible: bool):
        """Show/hide scenario undo button on this block."""
        if self._sc_undo_btn:
            self._sc_undo_btn.setHidden_(not visible)
            if visible:
                self._sc_undo_btn.refreshTracking()

    def toggle_format(self):
        self._md_mode = not self._md_mode
        if self._inner_tv:
            if self._md_mode:
                # Rendered → raw: sync any edits made in rendered mode to _md_text first.
                # In rendered mode the user may have typed/pasted text directly into _inner_tv,
                # so capture that before we replace the content with the raw markdown source.
                current_in_tv = str(self._inner_tv.string()).strip()
                if current_in_tv and current_in_tv != str(self._rendered.string()).strip():
                    self._md_text = current_in_tv   # preserve edits made in rendered mode
                raw_attrs = _md_to_raw_attrs(self._md_text)
                self._inner_tv.textStorage().setAttributedString_(raw_attrs)
                self._set_line_color(C_CYAN)
            else:
                # Raw → rendered: re-render whatever markdown user has now in the editor
                current_raw = str(self._inner_tv.string()).strip() or self._md_text
                self._md_text = current_raw
                try:
                    self._rendered = _md_to_styled_attrs(current_raw)
                except Exception:
                    self._rendered = AppKit.NSAttributedString.alloc().initWithString_(current_raw)
                self._inner_tv.textStorage().setAttributedString_(self._rendered)
                color = C_TEXT if self._hovered else AppKit.NSColor.clearColor()
                self._set_line_color(color)
                # Mark as md block so float bar tracks it during scrolling
                self._is_md_block = True
        if self._md_btn:
            color = C_CYAN if self._md_mode else C_GREEN_DIM
            self._md_btn._normal_col = color
            self._md_btn.setAttributedTitle_(_atitle("md", size=9, color=color))
            self._md_btn.setHidden_(False)
        if self._cpy_btn:
            self._cpy_btn.setHidden_(not self._md_mode and not self._hovered)
        _main(self._resize_to_content)

    def copy_to_clipboard(self):
        import subprocess as _sp
        txt = str(self._inner_tv.string()) if self._inner_tv else self._md_text
        _sp.run(["pbcopy"], input=txt.encode("utf-8"), check=False)

    def _check_edits(self):
        """Update _has_edits flag and revert button visibility after text change."""
        if not self._inner_tv:
            return
        current = str(self._inner_tv.string()).strip()
        self._has_edits = bool(self._original_text and current != self._original_text)
        if self._rev_btn:
            should_show = self._has_edits and self._hovered and bool(self._original_text)
            self._rev_btn.setHidden_(not should_show)

    def _save_edit_to_history(self):
        """Called after debounce: save edited content to history (with parent link if available)."""
        if not self._inner_tv or not _on_add_history_cb:
            return
        text = str(self._inner_tv.string()).strip()
        if not text:
            return
        if text == (self._original_text or ""):
            return  # unchanged
        parent_id = self._original_hist_id  # may be None
        new_id = _on_add_history_cb(text, parent_id)
        self._hist_id   = new_id
        self._has_edits = True
        self._md_text   = text
        if self._rev_btn and parent_id:
            # Only show revert when we have an original to go back to
            self._rev_btn.setHidden_(not self._hovered)
            if self._hovered:
                self._rev_btn.refreshTracking()

    def _revert_to_original(self):
        """Restore block to its original (pre-edit) content."""
        orig = self._original_text
        if not orig or not self._inner_tv:
            return
        try:
            self._rendered = _md_to_styled_attrs(orig)
        except Exception:
            self._rendered = AppKit.NSAttributedString.alloc().initWithString_(orig)
        self._inner_tv.textStorage().setAttributedString_(self._rendered)
        self._md_text   = orig
        self._hist_id   = self._original_hist_id
        self._has_edits = False
        if self._rev_btn:
            self._rev_btn.setHidden_(True)
        _main(self._resize_to_content)

    def _resize_to_content(self):
        """Recalculate block height based on current inner TV content; delete if empty."""
        if not self._inner_tv:
            return
        current_text = str(self._inner_tv.string())
        if not current_text.strip():
            self._delete_self()
            return
        # Update stored markdown source ONLY in raw mode.
        # In rendered mode _inner_tv holds rendered HTML (no ## ** markers).
        if self._md_mode:
            self._md_text = current_text.strip()
        # Measure accurate content height with a temp view (avoids fixed-container clipping)
        dw   = int(self.frame().size.width)
        tv_w = max(40, dw - BLOCK_L_PAD - BLOCK_R_PAD)
        attrs = AppKit.NSAttributedString.alloc().initWithAttributedString_(
            self._inner_tv.textStorage())
        temp = AppKit.NSTextView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, tv_w, 50000))
        temp.setRichText_(True)
        temp.setVerticallyResizable_(True)
        temp.setHorizontallyResizable_(False)
        temp.textContainer().setWidthTracksTextView_(True)
        temp.textContainer().setHeightTracksTextView_(False)
        temp.textStorage().setAttributedString_(attrs)
        lm = temp.layoutManager()
        tc = temp.textContainer()
        lm.ensureLayoutForTextContainer_(tc)
        used      = lm.usedRectForTextContainer_(tc)
        content_h = max(16, int(used.size.height) + 4)
        bh        = content_h + BLOCK_BTN_AREA + BLOCK_V_PAD

        old_f = self.frame()
        if int(old_f.size.height) == bh:
            return

        self.setFrame_(AppKit.NSMakeRect(
            old_f.origin.x, old_f.origin.y, old_f.size.width, bh))

        tv_h = max(1, bh - BLOCK_BTN_AREA - BLOCK_V_PAD)
        old_tv = self._inner_tv.frame()
        self._inner_tv.setFrame_(AppKit.NSMakeRect(
            old_tv.origin.x, old_tv.origin.y, old_tv.size.width, tv_h))

        if self._line_v:
            self._line_v.setFrame_(AppKit.NSMakeRect(0, 0, BLOCK_BORDER_W, bh))

        # Reposition hover buttons to new top area
        BTN_H = 12
        BTN_Y = bh - BLOCK_BTN_AREA + (BLOCK_BTN_AREA - BTN_H) // 2
        for btn in (self._del_btn, self._rev_btn, self._md_btn, self._cpy_btn):
            if btn:
                of = btn.frame()
                btn.setFrame_(AppKit.NSMakeRect(of.origin.x, BTN_Y, of.size.width, BTN_H))

        _relayout_doc_view()

    def _sync_to_width(self, new_w):
        """Resize all internal subviews to new_w; remeasure + return new block height.
        Does NOT call _relayout_doc_view — safe to call from inside _relayout_doc_view."""
        if not self._inner_tv:
            return int(self.frame().size.height)
        tv_w = max(40, new_w - BLOCK_L_PAD - BLOCK_R_PAD)
        old_tv = self._inner_tv.frame()
        self._inner_tv.setFrame_(AppKit.NSMakeRect(
            old_tv.origin.x, old_tv.origin.y, tv_w, old_tv.size.height))
        # Remeasure height at new width
        attrs = AppKit.NSAttributedString.alloc().initWithAttributedString_(
            self._inner_tv.textStorage())
        temp = AppKit.NSTextView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, tv_w, 50000))
        temp.setRichText_(True)
        temp.setVerticallyResizable_(True)
        temp.setHorizontallyResizable_(False)
        temp.textContainer().setWidthTracksTextView_(True)
        temp.textContainer().setHeightTracksTextView_(False)
        temp.textStorage().setAttributedString_(attrs)
        lm = temp.layoutManager()
        tc = temp.textContainer()
        try:
            lm.ensureLayoutForTextContainer_(tc)
            used = lm.usedRectForTextContainer_(tc)
            content_h = max(16, int(used.size.height) + 4)
        except Exception:
            content_h = max(16, int(old_tv.size.height))
        bh = content_h + BLOCK_BTN_AREA + BLOCK_V_PAD
        tv_h = max(1, bh - BLOCK_BTN_AREA - BLOCK_V_PAD)
        self._inner_tv.setFrame_(AppKit.NSMakeRect(
            old_tv.origin.x, old_tv.origin.y, tv_w, tv_h))
        if self._line_v:
            self._line_v.setFrame_(AppKit.NSMakeRect(0, 0, BLOCK_BORDER_W, bh))
        BTN_H = 12
        BTN_Y = bh - BLOCK_BTN_AREA + (BLOCK_BTN_AREA - BTN_H) // 2
        btn_xs = {
            id(self._del_btn): BLOCK_L_PAD,
            id(self._md_btn):  new_w - 44,
            id(self._cpy_btn): new_w - 20,
            id(self._rev_btn): new_w - 68,
        }
        for btn in (self._del_btn, self._md_btn, self._cpy_btn, self._rev_btn):
            if btn:
                of  = btn.frame()
                bx  = btn_xs.get(id(btn), of.origin.x)
                btn.setFrame_(AppKit.NSMakeRect(bx, BTN_Y, of.size.width, BTN_H))
        return bh

    def _refresh_font(self):
        """Re-render block content with current font size."""
        if not self._inner_tv:
            return
        if self._md_mode:
            # Raw mode: _inner_tv has raw markdown (possibly user-edited)
            current_raw = str(self._inner_tv.string()).strip() or self._md_text
            self._md_text = current_raw
            raw_attrs = _md_to_raw_attrs(current_raw)
            self._inner_tv.textStorage().setAttributedString_(raw_attrs)
        else:
            # Rendered mode: always re-render from _md_text (original markdown)
            try:
                rendered = _md_to_styled_attrs(self._md_text)
            except Exception:
                rendered = AppKit.NSAttributedString.alloc().initWithString_(self._md_text)
            self._rendered = rendered
            self._inner_tv.textStorage().setAttributedString_(rendered)
        self._resize_to_content()

    def _delete_self(self):
        """Remove this block from the session and update focus."""
        if self not in _rich_blocks:
            return
        idx = _rich_blocks.index(self)
        _rich_blocks.pop(idx)
        # Update indices on remaining blocks
        for i, b in enumerate(_rich_blocks):
            b._idx = i
            if b._inner_tv:
                b._inner_tv._block_idx = i
            for btn in (b._del_btn, b._rev_btn, b._md_btn, b._cpy_btn):
                if btn:
                    btn.setTag_(i)
        self.removeFromSuperview()
        _relayout_doc_view()
        _update_action_visibility()   # hide buttons if last block was deleted
        # Move focus: previous block end, or _tv if none
        if _win:
            if idx > 0 and len(_rich_blocks) > 0:
                prev = _rich_blocks[idx - 1]
                if prev._inner_tv:
                    _win.makeFirstResponder_(prev._inner_tv)
                    end = prev._inner_tv.textStorage().length()
                    prev._inner_tv.setSelectedRange_(AppKit.NSMakeRange(end, 0))
                    return
            if _tv:
                _win.makeFirstResponder_(_tv)
                _tv.setSelectedRange_(AppKit.NSMakeRange(0, 0))


def _make_rich_block(md_text, idx):
    """Create a _RichBlockView sized to exact content height (no max)."""
    dw   = int(_doc_view.frame().size.width) if _doc_view else (W - 16)
    tv_w = max(40, dw - BLOCK_L_PAD - BLOCK_R_PAD)
    try:
        rendered = _md_to_styled_attrs(md_text)
        temp = AppKit.NSTextView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, tv_w, 50000))
        temp.setRichText_(True)
        temp.setVerticallyResizable_(True)
        temp.setHorizontallyResizable_(False)
        temp.textContainer().setWidthTracksTextView_(True)
        temp.textContainer().setHeightTracksTextView_(False)
        temp.textStorage().setAttributedString_(rendered)
        lm = temp.layoutManager()
        tc = temp.textContainer()
        lm.ensureLayoutForTextContainer_(tc)
        used  = lm.usedRectForTextContainer_(tc)
        content_h = max(16, int(used.size.height) + 4)
    except Exception as e:
        print(f"[overlay] _make_rich_block measure: {e}")
        content_h = 60
    bh    = content_h + BLOCK_BTN_AREA + BLOCK_V_PAD
    block = _RichBlockView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, dw, bh))
    block._do_setup(md_text, idx)
    return block


def _finalize_tv_to_block(add_to_history=False):
    """Convert text from _tv into a block; clear _tv for next entry.
    add_to_history=True for paste paths (dictation history is managed by main.py).
    """
    if not _tv or not _doc_view:
        return
    txt = str(_tv.string()).strip()
    if not txt:
        return
    idx   = len(_rich_blocks)
    block = _make_rich_block(txt, idx)
    _rich_blocks.append(block)
    _doc_view.addSubview_(block)
    # Inner cursor at END of block text so next click-in lands there
    if block._inner_tv:
        end = block._inner_tv.textStorage().length()
        block._inner_tv.setSelectedRange_(AppKit.NSMakeRange(end, 0))
    block._original_text    = txt
    hist_id = _on_add_history_cb(txt) if _on_add_history_cb else None
    block._hist_id          = hist_id
    block._original_hist_id = hist_id
    if _on_update_session_cb:
        _on_update_session_cb()
    _tv.setString_("")
    # Restore typing attributes — setString_("") can reset them to system defaults
    _tv.setTextColor_(C_TEXT)
    _tv.setTypingAttributes_({
        AppKit.NSFontAttributeName:            _mono(_st["font_size"]),
        AppKit.NSForegroundColorAttributeName: C_TEXT,
    })
    _st["text"] = ""
    _st["is_md"] = False
    _st["md_mode"] = False
    _update_format_indicator()
    _relayout_doc_view()
    _update_action_visibility()   # block now has content; ensure row stays visible
    if _win and _tv:
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        _win.makeKeyAndOrderFront_(None)
        _win.makeFirstResponder_(_tv)
        _tv.setSelectedRange_(AppKit.NSMakeRange(0, 0))
        _tv.scrollRangeToVisible_(AppKit.NSMakeRange(0, 0))


# ── ObjC action targets ───────────────────────────────────────────────────────

class BtnTarget(AppKit.NSObject):
    def scenario_(self, sender):
        idx = int(sender.tag())
        sc  = _st["scenarios"]
        if _on_scenario_cb and 0 <= idx < len(sc):
            _on_scenario_cb(sc[idx], idx)

    def close_(self, sender):
        hide(force=True)

    def actionCancel_(self, sender):
        """Smart cancel: undo scenario if active, stay in window after interrupt, else close."""
        if _st.get("active_sc") is not None:
            self.undoScenario_(sender)
        elif _st.get("post_interrupt"):
            _st["post_interrupt"] = False
            _refresh_scenario_colors()
        else:
            hide(force=True)

    def actionScene_(self, sender):
        """Toggle scenario picker in bottom row."""
        _st["sc_picker"] = not _st.get("sc_picker", False)
        def _():
            if _win:
                _relayout_buttons(int(_win.frame().size.width))
            _show_buttons(True)
        _main(_)

    def history_(self, sender):

        # Corner button (tag=9): distinguish single vs double click without opening
        # history on the first click of a double-click.
        if callable(getattr(sender, 'tag', None)) and sender.tag() == 9:
            ev = AppKit.NSApp.currentEvent()
            if ev and ev.clickCount() >= 2:
                # Double-click: cancel any pending single-click open, expand window
                AppKit.NSObject.cancelPreviousPerformRequestsWithTarget_selector_object_(
                    self, b'_openHistDelayed:', None)
                _main(_toggle_expand)
            else:
                # Single click: delay open so double-click can cancel it
                AppKit.NSObject.cancelPreviousPerformRequestsWithTarget_selector_object_(
                    self, b'_openHistDelayed:', None)
                self.performSelector_withObject_afterDelay_(
                    b'_openHistDelayed:', None, 0.30)
            return
        history = _on_history_cb() if _on_history_cb else []
        _main(lambda: _show_hist_panel(history))

    def _openHistDelayed_(self, _):
        history = _on_history_cb() if _on_history_cb else []
        _main(lambda: _show_hist_panel(history))

    def cfg_(self, sender):
        if _editing_scenario:
            return  # cfg panel stays visible; editor must be saved/cancelled first
        _main(_toggle_cfg_panel)

    def closePanel_(self, sender):
        if _hist_panel and _hist_panel.isVisible():
            _hist_panel.orderOut_(None)
        if _cfg_panel and _cfg_panel.isVisible():
            _close_cfg_panel()

    def histItem_(self, sender):
        obj = sender.representedObject()
        if not obj:
            return
        if not isinstance(obj, dict):
            _main(lambda: _restore_history_item(str(obj)))
            return
        if obj.get("type") == "session":
            blocks_text = obj.get("blocks_text") or []
            block_ids   = obj.get("blocks") or []
            sid = obj.get("id")
            if blocks_text:
                _main(lambda bt=blocks_text, bi=block_ids, s=sid: _restore_session(bt, bi, s))
        else:
            _main(lambda o=obj: _restore_history_item(o["full"], o.get("id")))

    def cfgOpacity_(self, sender):
        _st["opacity"] = round(float(sender.floatValue()), 2)
        _save_settings()
        _apply_all_panels_alpha()

    def cfgFontDec_(self, sender):
        _st["font_size"] = max(9.0, _st["font_size"] - 1.0)
        _save_settings()
        if _tv:
            _tv.setFont_(_mono(_st["font_size"]))
        _main(_update_blocks_font)
        _main(_update_cursor_pos)

    def cfgFontInc_(self, sender):
        _st["font_size"] = min(28.0, _st["font_size"] + 1.0)
        _save_settings()
        if _tv:
            _tv.setFont_(_mono(_st["font_size"]))
        _main(_update_blocks_font)
        _main(_update_cursor_pos)

    def cfgLang_(self, sender):
        idx = int(sender.tag())
        _st["lang"] = LANGS[idx] if 0 <= idx < len(LANGS) else "ru"
        _save_settings()
        _refresh_status_label()
        _refresh_scenario_colors()
        # Rebuild config panel with new lang (keep window position — immediately reopening)
        _close_cfg_panel_rebuild()
        _toggle_cfg_panel()
        # Rebuild scenario editor with new lang (preserves sc_idx, loses unsaved text edits)
        if _editing_scenario and _sc_editor_panel:
            sc_idx = (_sc_edit_refs or {}).get("sc_idx")
            _show_sc_editor(sc_idx)

    def cfgHotkeyCopy_(self, sender):
        options = ["ctrl", "cmd", "ctrl+shift", "cmd+shift"]
        tag = int(sender.tag())
        if 0 <= tag < len(options):
            _st["hotkey_copy"] = options[tag]
            _save_settings()
        # Rebuild panel to update button colors (keep window position — immediately reopening)
        _close_cfg_panel_rebuild()
        _toggle_cfg_panel()

    def cfgProviders_(self, sender):
        _toggle_providers_panel()

    def scProviderChanged_(self, sender):
        pop_prov  = (_sc_edit_refs or {}).get("pop_provider")
        pop_model = (_sc_edit_refs or {}).get("pop_model")
        if not pop_prov or not pop_model:
            return
        pid = pop_prov.titleOfSelectedItem() or ""
        _populate_model_popup(pop_model, pid)

    def hushResetPanels_(self, sender):
        _main(lambda: _reset_panels_layout())

    def hushDefaultCross_(self, sender):
        _main(lambda: _reset_to_cross_layout())

    def cfgInfo_(self, sender):
        """Toggle About card as standalone centered panel."""
        if _about_panel and _about_panel.isVisible():
            _hide_about_view()
        else:
            _main(_show_about_view)

    def aboutClose_(self, sender):
        _main(_hide_about_view)

    def aboutDonate_(self, sender):
        import subprocess
        subprocess.Popen(["open", "https://pay.alexbic.net/?mode=donate"])

    def aboutGithub_(self, sender):
        import subprocess
        subprocess.Popen(["open", "https://github.com/alexbic"])

    def aboutSite_(self, sender):
        import subprocess
        subprocess.Popen(["open", "https://alexbic.net"])

    def showAbout_(self, sender):
        """Status-bar menu: always show About card."""
        _main(_show_about_view)

    def openHush_(self, sender):
        """Status-bar menu: open main HUSH window (same as double Option press)."""
        show_recording()
        AppKit.NSApp.activateIgnoringOtherApps_(True)

    def toggleLaunchAtLogin_(self, sender):
        """Status-bar menu: toggle Launch at Login via LaunchAgent plist."""
        _toggle_launch_at_login()
        state = 1 if _is_launch_at_login() else 0
        sender.setState_(state)
        item = getattr(self, '_login_menu_item', None)
        if item:
            item.setState_(state)

    def menuNeedsUpdate_(self, menu):
        """NSMenuDelegate: refresh checkmark before menu opens."""
        item = getattr(self, '_login_menu_item', None)
        if item:
            item.setState_(1 if _is_launch_at_login() else 0)

    def quitApp_(self, sender):
        """Status-bar menu: quit the application."""
        AppKit.NSApp.terminate_(None)

    def retryStatusVisible_(self, timer):
        """No-op: kept for compatibility; status bar fix is in C launcher."""
        pass

    def cfgTheme_(self, sender):
        themes = [tm[0] for tm in _THEME_META]
        tag = int(sender.tag())
        if 0 <= tag < len(themes):
            _apply_theme(themes[tag])
        _close_cfg_panel_rebuild()
        _toggle_cfg_panel()

    def cfgQuit_(self, sender):
        _close_cfg_panel()
        _win_save_pos()
        AppKit.NSApplication.sharedApplication().terminate_(None)

    def provClose_(self, sender):
        _close_providers_panel()

    def panelMagnet_(self, sender):
        tag = sender.tag()
        if 0 <= tag < len(_MAGNET_KEYS):
            _main(lambda t=tag: _toggle_magnet(_MAGNET_KEYS[t]))

    def provSave_(self, sender):
        """Save all provider fields to providers.json and re-probe."""
        refs = _prov_field_refs or {}
        changed_ollama = False
        if "ollama_url" in refs:
            new_url = refs["ollama_url"].stringValue().strip()
            if new_url != _pc.get("ollama", "base_url"):
                _pc.set_field("ollama", "base_url", new_url)
                changed_ollama = True
        for pid in ("anthropic", "openai", "glm"):
            key = f"{pid}_key"
            if key in refs:
                _pc.set_field(pid, "api_key", refs[key].stringValue().strip())
        if "openai_base" in refs:
            _pc.set_field("openai", "base_url",
                          refs["openai_base"].stringValue().strip() or "https://api.openai.com/v1")
        if "glm_base" in refs:
            _pc.set_field("glm", "base_url",
                          refs["glm_base"].stringValue().strip() or "https://api.z.ai/api/paas/v4")
        _close_providers_panel()
        # Re-probe after UI closes (refs are cleared)
        if changed_ollama:
            _pc.probe_ollama()
        else:
            import threading as _th
            _th.Thread(target=_pc._probe_cloud, daemon=True).start()

    def cfgScResetOne_(self, sender):
        """Restore default scenario fields to factory defaults (without saving)."""
        sc_idx = int(sender.tag())
        if sc_idx >= len(DEFAULT_SCENARIOS):
            return
        default_sc = DEFAULT_SCENARIOS[sc_idx]
        refs  = _sc_edit_refs
        label = default_sc.get("label", {})
        if isinstance(label, dict):
            refs["tf_ru"].setStringValue_(label.get("ru", ""))
            refs["tf_en"].setStringValue_(label.get("en", ""))
            refs["tf_es"].setStringValue_(label.get("es", ""))
        else:
            refs["tf_en"].setStringValue_(str(label)[:6])
        _reset_sc_model_popups(default_sc.get("model", "") or "")
        refs["tv_prompt"].setString_(default_sc.get("prompt", ""))

    def cfgScEdit_(self, sender):
        sc_idx = int(sender.tag())
        if _editing_scenario:
            if _sc_edit_refs.get("sc_idx") == sc_idx:
                # Same card clicked again → toggle close (dirty check inside)
                _main(lambda: _maybe_close_editor(pending_fn=None))
                return
            # Different scenario → check unsaved changes first, then switch
            _main(lambda idx=sc_idx: _maybe_close_editor(
                pending_fn=lambda: _show_sc_editor(idx)))
        else:
            # Highlight this card immediately (bright = editor is open for it)
            scenarios = _st.get("scenarios", [])
            if sc_idx < len(scenarios):
                lbl = _sc_label_for(scenarios[sc_idx], _st.get("lang", "ru"))
                sc  = scenarios[sc_idx]
                is_fd  = bool(sc.get("full_default"))
                is_sil = bool(sc.get("silent"))
                if is_fd or is_sil:
                    ps = AppKit.NSMutableParagraphStyle.alloc().init()
                    ps.setAlignment_(AppKit.NSTextAlignmentCenter)
                    mstr = AppKit.NSMutableAttributedString.alloc().init()
                    a = {AppKit.NSFontAttributeName: _mono(9),
                         AppKit.NSForegroundColorAttributeName: C_CYAN,
                         AppKit.NSParagraphStyleAttributeName: ps}
                    parts = []
                    if is_fd:  parts.append("[")
                    if is_sil: parts.append("·")
                    parts.append(lbl)
                    if is_sil: parts.append("·")
                    if is_fd:  parts.append("]")
                    for part in parts:
                        mstr.appendAttributedString_(
                            AppKit.NSAttributedString.alloc().initWithString_attributes_(part, a))
                    sender.setAttributedTitle_(mstr)
                else:
                    sender.setAttributedTitle_(_atitle(lbl, size=9, color=C_CYAN))
            _main(lambda idx=sc_idx: _show_sc_editor(idx))

    def cfgScAdd_(self, sender):
        if _editing_scenario:
            _main(lambda: _maybe_close_editor(pending_fn=lambda: _show_sc_editor(None)))
        else:
            _main(lambda: _show_sc_editor(None))

    def cfgScSave_(self, sender):
        _main(_sc_editor_save)

    def cfgScCancel_(self, sender):
        _main(lambda: _maybe_close_editor(pending_fn=None))

    def cfgScDiscard_(self, sender):
        global _sc_edit_pending
        fn = _sc_edit_pending
        _main(lambda: _close_editor_now(fn))

    def cfgScCancelConfirm_(self, sender):
        """Dismiss unsaved-changes confirm overlay, stay in editor."""
        if _sc_editor_panel:
            cv = _sc_editor_panel.contentView()
            subs = list(cv.subviews())
            if subs:
                subs[-1].removeFromSuperview()

    def cfgScDelete_(self, sender):
        """Show delete-confirm overlay for a custom scenario."""
        sc_idx = int(sender.tag())
        _main(lambda: _show_sc_delete_confirm(sc_idx))

    def cfgScDeleteConfirmYes_(self, sender):
        sc_idx = int(sender.tag())
        _main(lambda: _do_delete_scenario(sc_idx))

    def cfgScDeleteNo_(self, sender):
        """Dismiss delete-confirm overlay."""
        if _sc_editor_panel:
            cv = _sc_editor_panel.contentView()
            subs = list(cv.subviews())
            if subs:
                subs[-1].removeFromSuperview()

    def mdToggle_(self, sender):
        _main(_toggle_md_mode)

    def richToggle_(self, sender):
        idx = int(sender.tag())
        if 0 <= idx < len(_rich_blocks):
            _main(_rich_blocks[idx].toggle_format)

    def richCopy_(self, sender):
        idx = int(sender.tag())
        if 0 <= idx < len(_rich_blocks):
            _main(_rich_blocks[idx].copy_to_clipboard)

    def richDelete_(self, sender):
        idx = int(sender.tag())
        if 0 <= idx < len(_rich_blocks):
            _main(_rich_blocks[idx]._delete_self)

    def richRevert_(self, sender):
        idx = int(sender.tag())
        if 0 <= idx < len(_rich_blocks):
            _main(_rich_blocks[idx]._revert_to_original)

    def floatMdToggle_(self, sender):
        if _float_target:
            _main(_float_target.toggle_format)
            _main(_update_float_bar)

    def floatCopy_(self, sender):
        if _float_target:
            _main(_float_target.copy_to_clipboard)

    def docScrolled_(self, notification):
        _main(_update_float_bar)

    def cfgScToggleSilent_(self, sender):
        """Toggle the 'silent mode' flag (state lives in _sc_edit_refs, not on the button)."""
        new_val = not _sc_edit_refs.get("silent", False)
        _sc_edit_refs["silent"] = new_val
        chk_prefix = "[✓] " if new_val else "[ ] "
        color = C_CYAN if new_val else C_GREEN_DIM
        sender.setAttributedTitle_(_atitle(
            chk_prefix + _T("sc_silent"), size=10, color=color,
            align=AppKit.NSTextAlignmentLeft))

    def cfgScToggleFullDefault_(self, sender):
        """Toggle 'default full mode scenario' (radio — only one allowed)."""
        new_val = not _sc_edit_refs.get("full_default", False)
        _sc_edit_refs["full_default"] = new_val
        chk_prefix = "[✓] " if new_val else "[ ] "
        color = C_GREEN_BR if new_val else C_GREEN_DIM
        sender.setAttributedTitle_(_atitle(
            chk_prefix + _T("sc_full_default"), size=10, color=color,
            align=AppKit.NSTextAlignmentLeft))

    def silentInterrupt_(self, sender):
        fn = _silent_interrupt_fn
        if fn:
            fn()

    def expand_(self, sender):
        _main(_toggle_expand)

    def send_(self, sender):
        if _on_paste_cb:
            _on_paste_cb(mode="shift_enter")   # [Отправить] — apply full_default if set

    def scPrev_(self, sender):
        global _sc_page
        if _sc_page > 0:
            _sc_page -= 1
            _main(lambda: _relayout_buttons(int(_win.frame().size.width)))
        # Also refresh labels in case language changed (label depends on _st lang)
        _main(_refresh_scenario_colors)

    def scNext_(self, sender):
        global _sc_page
        if (_sc_page + 1) * _sc_page_size < len(_st["scenarios"]):
            _sc_page += 1
            _main(lambda: _relayout_buttons(int(_win.frame().size.width)))

    def undoScenario_(self, sender):
        with open("/tmp/vi_undo_debug.log", "a") as _f:
            _f.write(f"[overlay] undoScenario_ called, cb={_on_undo_sc_cb is not None}, active_sc={_st.get('active_sc')}\n")
        if _on_undo_sc_cb:
            _on_undo_sc_cb()

_btn_t = None

# ── Helpers ───────────────────────────────────────────────────────────────────

def _mono(size=13.0, bold=False):
    w = AppKit.NSFontWeightMedium if bold else AppKit.NSFontWeightLight
    return AppKit.NSFont.monospacedSystemFontOfSize_weight_(size, w)

def _mklabel(text, size=12, bold=False, color=None):
    tf = AppKit.NSTextField.labelWithString_(text)
    tf.setEditable_(False)
    tf.setSelectable_(False)
    tf.setBezeled_(False)
    tf.setDrawsBackground_(False)
    tf.setFont_(_mono(size, bold))
    tf.setTextColor_(color or C_TEXT)
    return tf

def _atitle(title, size=11, color=None, align=AppKit.NSTextAlignmentCenter):
    ps = AppKit.NSMutableParagraphStyle.alloc().init()
    ps.setAlignment_(align)
    attrs = {
        AppKit.NSFontAttributeName:            _mono(size),
        AppKit.NSForegroundColorAttributeName: color or C_GREEN,
        AppKit.NSParagraphStyleAttributeName:  ps,
    }
    return AppKit.NSAttributedString.alloc().initWithString_attributes_(title, attrs)

class _LinkButton(AppKit.NSButton):
    """NSButton that shows a pointing-hand cursor on hover (for hyperlink-style buttons)."""
    def updateTrackingAreas(self):
        objc.super(_LinkButton, self).updateTrackingAreas()
        for a in list(self.trackingAreas()):
            self.removeTrackingArea_(a)
        opts = (AppKit.NSTrackingCursorUpdate |
                AppKit.NSTrackingActiveAlways |
                AppKit.NSTrackingInVisibleRect)
        self.addTrackingArea_(
            AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(), opts, self, None))

    def cursorUpdate_(self, event):
        AppKit.NSCursor.pointingHandCursor().set()

    def resetCursorRects(self):
        self.addCursorRect_cursor_(self.bounds(), AppKit.NSCursor.pointingHandCursor())


def _mkbtn(title, color=None, size=11, align=AppKit.NSTextAlignmentCenter):
    btn = AppKit.NSButton.alloc().init()
    btn.setBordered_(False)
    btn.setAttributedTitle_(_atitle(title, size=size, color=color, align=align))
    return btn


def _mklinkbtn(title, color=None, size=11, align=AppKit.NSTextAlignmentCenter):
    """Like _mkbtn but shows pointing-hand cursor on hover."""
    btn = _LinkButton.alloc().init()
    btn.setBordered_(False)
    btn.setAttributedTitle_(_atitle(title, size=size, color=color, align=align))
    return btn

def _sep_line(x, y, w, h=1, pin="bottom"):
    """pin='top' keeps line at top when window resizes; 'bottom' keeps at bottom."""
    box = AppKit.NSBox.alloc().initWithFrame_(AppKit.NSMakeRect(x, y, w, h))
    box.setBoxType_(AppKit.NSBoxSeparator)
    box.setBorderColor_(C_GREEN_BORD)
    vmask = AppKit.NSViewMinYMargin if pin == "top" else AppKit.NSViewMaxYMargin
    box.setAutoresizingMask_(AppKit.NSViewWidthSizable | vmask)
    return box

def _style_tf(tf, placeholder=""):
    """Style a terminal-look NSTextField: theme bg, thin border, vertically centered."""
    tf_bg = _rgba(*C_BG)
    cell = _CenteredTextFieldCell.alloc().init()
    cell.setFont_(_mono(10))
    cell.setTextColor_(C_TEXT)
    cell.setBackgroundColor_(tf_bg)
    cell.setDrawsBackground_(True)
    cell.setBezeled_(False)
    cell.setEditable_(True)
    cell.setSelectable_(True)
    cell.setFocusRingType_(AppKit.NSFocusRingTypeNone)
    tf.setCell_(cell)
    tf.setEditable_(True)
    tf.setSelectable_(True)
    tf.setWantsLayer_(True)
    lay = tf.layer()
    lay.setBackgroundColor_(tf_bg.CGColor())
    lay.setBorderColor_(C_GREEN_BORD.CGColor())
    lay.setBorderWidth_(0.5)
    lay.setCornerRadius_(2.0)
    if placeholder:
        pa = AppKit.NSMutableAttributedString.alloc().initWithString_(placeholder)
        rng = AppKit.NSMakeRange(0, len(placeholder))
        ph_col = AppKit.NSColor.colorWithRed_green_blue_alpha_(
            C_IDLE.redComponent(), C_IDLE.greenComponent(), C_IDLE.blueComponent(), 0.45)
        pa.addAttribute_value_range_(
            AppKit.NSForegroundColorAttributeName, ph_col, rng)
        pa.addAttribute_value_range_(
            AppKit.NSFontAttributeName, _mono(10), rng)
        cell.setPlaceholderAttributedString_(pa)

def _main(fn):
    def _safe():
        try:
            fn()
        except Exception:
            import traceback, time
            tb = traceback.format_exc()
            print(tb, flush=True)
            try:
                with open("/tmp/hush_main_err.log", "a") as _f:
                    _f.write(f"[{time.strftime('%H:%M:%S')}]\n{tb}\n")
            except Exception:
                pass
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_safe)

# ── Layout constants ──────────────────────────────────────────────────────────

W        = 440
W_EXP    = 640   # expanded width
H        = 358   # window height — same for main and all auxiliary panels
H_EXP    = 680   # expanded height
H_PANEL  = H     # alias: auxiliary panels use same height as main window

# Header (top): ONE line — status + waveform + [CFG][↵][□][×] all on same row
HDR_H    = 40
HDR_Y    = H - HDR_H                # = 300

HDR_ITEM_H  = 22
HDR_ITEM_Y  = HDR_Y + (HDR_H - HDR_ITEM_H) // 2   # = 309 (vertically centred)

# Status label [left]
STS_X    = 10
STS_W    = 70

# Right cluster in header: only ⚙ visible; □ and × permanently hidden
CLO_W    = 28
CLO_X    = W - CLO_W - 4           # = 408 (close btn slot, hidden permanently)
EXP_W    = 28
EXP_X    = CLO_X - EXP_W - 2       # = 378 (expand btn slot, hidden permanently — dbl-click icon)
CFG_H_W  = 24                       # ⚙ gear icon — small single char
CFG_H_X  = W - CFG_H_W - 6         # = 410 (far right corner)
HIST_H_W = 42                       # kept for reference; hist btn moved to bottom panel
HIST_H_X = CFG_H_X                  # waveform's right bound now extends to gear icon

# EQ/Waveform: fixed centered position, same for all modes
EQ_CTR_W = 200
EQ_CTR_X = (W - EQ_CTR_W) // 2    # = 120

# Aliases for legacy references (WF starts at centered position)
WF_X     = EQ_CTR_X                 # = 120
WF_W     = EQ_CTR_W                 # = 200

# Button row (bottom)
BTN_H    = 46
BTN_Y    = 0

# Text area (middle)
TXT_Y    = BTN_H + 4                # = 50
TXT_TOP  = HDR_Y - 2                # = 298
TXT_H    = TXT_TOP - TXT_Y         # = 248

# Drop panels — exact width of main window
DP_PANEL_W     = W                  # will update to current win width at show-time
DP_CFG_H       = 72                 # compact two-column layout
DP_HIST_ITEM_H = 26
DP_HIST_MAX_H  = 280

# Pre-allocated scenario button slots (max; actual per-page count computed dynamically)
SC_PAGE = 8

# Silent mode strip — pill with optional app icon
SILENT_H = 48    # strip height (all states); width computed dynamically

# Rich-text block layout
BLOCK_GAP      = 8    # gap between blocks
BLOCK_TV_GAP   = 2    # minimal gap between last block and cursor in _tv
BLOCK_L_PAD    = 8    # left padding (space for the hover border line)
BLOCK_R_PAD    = 4    # right padding
BLOCK_V_PAD    = 3    # bottom padding
BLOCK_BTN_AREA = 18   # top area reserved for hover buttons (not covered by content)
BLOCK_BORDER_W = 1.5  # left border line width (thin)

# ── Widget refs ───────────────────────────────────────────────────────────────

_win           = None
_pill          = None    # TerminalView (main background)
_wf            = None    # WaveformView (in header)
_tv            = None    # TerminalTextView (text area)
_lbl           = None    # status label (in header)
_app_icon_v    = None    # NSImageView с иконкой целевого приложения (в шапке)
_proc_app_lbl  = None    # NSTextField имя приложения — показывается только при обработке
_proc_sc_lbl   = None    # NSTextField имя сценария — справа от EQ во время обработки
_prev_app_name = ""      # локализованное имя prev_app (для _proc_app_lbl)
_undo_sc_btn   = None    # кнопка ↩ возврата к оригиналу (показывается когда сценарий активен)
_sc_action_v    = None   # 2-button panel: shown when scenario result is active
_sc_send_btn2   = None   # "Отправить" in 2-button panel
_sc_cancel_btn2 = None   # "Отменить" in 2-button panel
_action_row_v      = None   # 4-button panel: normal ready state (hidden when empty)
_action_hist_btn   = None   # [ИСТ] — history (replaces header history button)
_action_cancel_btn = None   # [ОТМЕНИТЬ] — close overlay (or undo scenario)
_action_scene_btn  = None   # [СЦЕНАРИЙ] — toggle scenario picker
_action_send_btn   = None   # [ОТПРАВИТЬ] — paste text as-is
_on_undo_sc_cb = None    # callback для отмены последнего сценария
_scroll        = None    # NSScrollView wrapping _tv
_sc_icons      = []      # scenario buttons (bottom row)
_sc_seps       = []      # separator labels between scenario buttons
_hist_btn      = None    # [HIST] button (bottom row)
_hist_corner_btn = None  # [⧖] history icon, always visible at bottom-right corner
_cfg_hdr_btn   = None    # [CFG] button (header, always visible)
_send_hdr_btn  = None    # [↵] button (header, visible when ready)
_close_btn     = None
_expand_btn    = None
_wf_timer      = None
_cur_view      = None    # _BlockCursor overlay
_cur_timer     = None    # NSTimer for cursor blink
_float_bar     = None    # sticky floating bar (md + copy) for large blocks
_float_bar_md  = None    # [md] button in float bar
_float_bar_cp  = None    # [→] button in float bar
_proc_eq_v        = None    # EqBarsView in header — shown during LLM scenario processing
_proc_sc_idx      = None    # scenario index currently being LLM-processed (yellow highlight)
_proc_hover_v     = None    # _ProcHoverView — hover cancel overlay for main window
_proc_interrupt_fn = None   # callable: called on cancel click during processing
_sc_avail         = {}      # {sc_idx: bool} cached model availability for main window buttons
_float_target     = None    # which _RichBlockView the float bar controls

_about_panel        = None   # standalone NSPanel for About card (centered on screen)
_prov_panel         = None   # drop panel for provider/API-key configuration
_prov_field_refs    = {}     # {"ollama_url": tf, "ollama_model": combo, "anthropic_key": tf, ...}
_prov_dot_refs      = {}     # {"ollama": NSTextField dot, ...}
_status_bar_item    = None   # NSStatusItem for macOS menu bar
_hist_panel       = None   # drop panel for history
_hist_panel_side  = None   # "below" | "right" | "left" — current placement
_hist_filter      = "blocks"   # "mixed" | "sessions" | "blocks" — active tab
_cfg_panel        = None   # drop panel for settings (stays open during editing)
_pre_cfg_win_y    = None   # (legacy, unused — panels no longer shift main window)
_panels_reset_open = False  # True after 🎯 press opens all; second press closes all
_sc_editor_panel  = None   # scenario editor panel (covers main window while editing)
_editing_scenario = False  # True while scenario editor is open
_sc_edit_refs    = {}     # {tf_ru/en/es, pop_provider, pop_model, tv_prompt, sc_idx, original}
_sc_edit_pending = None   # callable: what to do after editor is closed (save or discard)
_sc_cfg_buttons  = {}    # sc_idx → NSButton in current cfg panel (for color sync)

_sc_page      = 0    # current scenario page (0-based)
_sc_prev_btn  = None # [<] navigation button
_sc_next_btn  = None # [>] navigation button
_sc_active    = []   # _sc_active[i] = True if slot i has a scenario on current page
_sc_sep_active = []  # _sc_sep_active[i] = True if sep i is between two visible scenarios
_sc_page_size  = 5   # actual scenarios per page (computed dynamically from window width)

_md_btn      = None   # [md] format toggle for plain-text markdown
_doc_view    = None   # FlippedView container inside _scroll (holds blocks + _tv)
_rich_blocks = []     # list of _RichBlockView instances in insertion order

# Silent mode (floating center-bottom strip when full window is closed)
_silent_mode         = False
_silent_win          = None   # _SilentPanel instance
_silent_wf           = None   # _SilentWaveformView (recording state)
_silent_eq_v         = None   # EqBarsView (recognizing=scan / LLM=pulse)
_silent_target_app   = None   # NSRunningApplication for paste target
_silent_app_icon_v   = None   # _AppIconView (shown for non-Python apps)
_silent_hover_v      = None   # _HoverOverlayView (LLM state only)
_silent_interrupt_fn = None   # callable; called on interrupt click
_silent_text_v       = None   # NSTextField (accumulation) or NSTextField (processing card)
_silent_scroll_v     = None   # unused slot (kept for legacy reset in hide)
_silent_block_count  = 0      # unused slot (kept for legacy reset in hide)
_silent_strip_win_h  = 0      # initial window height (strip only) — set on first accumulation call
_silent_sep_y        = 0      # y-coordinate of separator (fixed relative to strip top)
_silent_saved_cx     = None   # saved window center X (persists across rebuilds)
_silent_saved_sy     = None   # saved window bottom Y (persists across rebuilds)

# ── Expand / collapse ─────────────────────────────────────────────────────────

_expanded        = False
_font_size_saved = None

# ── Markdown detection & rendering ────────────────────────────────────────────

_MD_PATTERNS = [
    re.compile(r'^#{1,6}\s', re.MULTILINE),       # headings
    re.compile(r'\*\*\S'),                          # bold
    re.compile(r'- \[[ xX]\]'),                    # checkboxes
    re.compile(r'`[^`]'),                           # inline code or code block
    re.compile(r'^[-*]{3,}\s*$', re.MULTILINE),    # horizontal rule
    re.compile(r'^\|.+\|', re.MULTILINE),          # table row
    re.compile(r'^\s*[-*+]\s+\S', re.MULTILINE),   # unordered list
    re.compile(r'^\s*\d+\.\s+\S', re.MULTILINE),   # ordered list
]

def _is_markdown(text: str) -> bool:
    """Return True if text matches ≥2 Markdown markers."""
    return sum(1 for p in _MD_PATTERNS if p.search(text)) >= 2


def _render_md_terminal(text: str) -> str:
    """Convert Markdown to terminal-style plain text for display."""

    def _inline(s: str) -> str:
        """Apply inline markdown to a string for terminal display."""
        # Escape sequences
        s = re.sub(r'\\([\\`*_{}\[\]()#+\-.!|])', r'\1', s)
        # Bold+italic *** (before ** and *)
        s = re.sub(r'\*{3}(.+?)\*{3}', lambda m: '_' + m.group(1).upper() + '_', s)
        # Bold **
        s = re.sub(r'\*\*(.+?)\*\*', lambda m: m.group(1).upper(), s)
        # Italic * or _
        s = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'_\1_', s)
        s = re.sub(r'(?<!_)\b_([^_\n]+)_\b(?!_)',  r'_\1_', s)
        # Strikethrough ~~
        s = re.sub(r'~~(.+?)~~', r'[\1]', s)
        # Inline code
        s = re.sub(r'`([^`]+)`', r'[\1]', s)
        # Images (before links)
        s = re.sub(r'!\[([^\]]*)\]\([^\)]*\)',
                   lambda m: f'[IMG: {m.group(1)}]' if m.group(1) else '[IMG]', s)
        # Links → show text only
        s = re.sub(r'\[([^\]]+)\]\([^\)]*\)', r'\1', s)
        # Auto links <url>
        s = re.sub(r'<(https?://[^>]+)>', r'\1', s)
        # Footnote refs [^n]
        s = re.sub(r'\[\^[^\]]+\]', '', s)
        return s

    def _is_table_sep(s: str) -> bool:
        return bool(re.match(r'^\|?[\s\-:|]+(\|[\s\-:|]+)+\|?$', s))

    def _render_table(tbl_lines: list) -> list:
        rows = []
        for tl in tbl_lines:
            if _is_table_sep(tl):
                continue
            cells = [_inline(c.strip()) for c in tl.strip('|').split('|')]
            rows.append(cells)
        if not rows:
            return []
        n_cols = max(len(r) for r in rows)
        widths = [0] * n_cols
        for row in rows:
            for ci, cell in enumerate(row):
                if ci < n_cols:
                    widths[ci] = max(widths[ci], len(cell))
        result = []
        for ri, row in enumerate(rows):
            padded = [row[ci].ljust(widths[ci]) if ci < len(row) else ' ' * widths[ci]
                      for ci in range(n_cols)]
            result.append('  '.join(padded))
            if ri == 0:
                result.append('  '.join('─' * widths[ci] for ci in range(n_cols)))
        return result

    lines = text.split('\n')
    out   = []
    in_code = False
    i = 0

    while i < len(lines):
        line    = lines[i]
        stripped = line.strip()

        # ── Code fences — preserve as-is ────────────────────────────────────
        if stripped.startswith('```'):
            in_code = not in_code
            out.append(line)
            i += 1; continue
        if in_code:
            out.append(line)
            i += 1; continue

        # ── GFM alerts > [!TYPE] ────────────────────────────────────────────
        alert_m = re.match(r'^>\s*\[!(NOTE|WARNING|TIP|IMPORTANT|CAUTION)\]',
                           stripped, re.IGNORECASE)
        if alert_m:
            atype = alert_m.group(1).upper()
            icons = {'NOTE': '📌', 'WARNING': '⚠', 'TIP': '💡',
                     'IMPORTANT': '❗', 'CAUTION': '🔶'}
            icon  = icons.get(atype, '▸')
            out.append(f'┌── {icon} {atype} ──')
            i += 1
            while i < len(lines):
                al = lines[i].strip()
                if al.startswith('>'):
                    body = re.sub(r'^>\s*', '', al)
                    if body:
                        out.append('│ ' + _inline(body))
                    i += 1
                else:
                    break
            out.append('└' + '─' * 18)
            continue

        # ── Blockquotes > ───────────────────────────────────────────────────
        if stripped.startswith('>'):
            level = 0
            s = stripped
            while s.startswith('>'):
                level += 1
                s = s[1:].lstrip()
            out.append('│ ' * level + _inline(s))
            i += 1; continue

        # ── Horizontal rule ─────────────────────────────────────────────────
        if re.match(r'^[-*_]{3,}\s*$', stripped):
            out.append('─' * 42)
            i += 1; continue

        # ── Headings ────────────────────────────────────────────────────────
        m = re.match(r'^(#{1,6})\s+(.*)', line)
        if m:
            level = len(m.group(1))
            title = _inline(m.group(2).strip())
            if level == 1:
                out.append(title.upper())
                out.append('═' * max(4, len(title)))
            elif level == 2:
                out.append(title.upper())
                out.append('─' * max(4, len(title)))
            elif level == 3:
                out.append('▸ ' + title)
            elif level == 4:
                out.append('▹ ' + title)
            else:
                out.append('  › ' + title)
            i += 1; continue

        # ── Tables ──────────────────────────────────────────────────────────
        if stripped.count('|') >= 2:
            tbl = []
            while i < len(lines):
                tl = lines[i].strip()
                if tl.count('|') >= 1 and re.match(r'^\|?[^#\n].*\|', tl):
                    tbl.append(tl)
                    i += 1
                else:
                    break
            out.extend(_render_table(tbl))
            continue

        # ── Footnote definitions [^n]: ───────────────────────────────────────
        if re.match(r'^\[\^[^\]]+\]:', stripped):
            i += 1; continue

        # ── Definition list `: definition` ───────────────────────────────────
        m_dl = re.match(r'^:\s+(.*)', line)
        if m_dl:
            out.append('  ' + _inline(m_dl.group(1)))
            i += 1; continue

        # ── Checkboxes, lists, inline ────────────────────────────────────────
        line = re.sub(r'^(\s*)- \[[xX]\]\s*', r'\1✓ ', line)
        line = re.sub(r'^(\s*)- \[ \]\s*',    r'\1○ ', line)
        line = re.sub(r'^(\s*)[-*+]\s+',      r'\1• ', line)
        line = re.sub(r'^(\s*)(\d+)\.\s+',    r'\g<1>\2. ', line)
        # Two-space hard break
        if line.endswith('  '):
            line = line.rstrip()
        line = _inline(line)
        out.append(line)
        i += 1

    return '\n'.join(out)


def _update_format_indicator():
    """Show/hide [md] button for plain-text markdown (rich blocks have own indicators)."""
    if _md_btn:
        is_md   = _st.get("is_md", False)
        md_mode = _st.get("md_mode", False)
        if is_md and not _st.get("rich_fmt"):
            color = C_GREEN_BR if md_mode else C_GREEN_DIM
            _md_btn.setAttributedTitle_(_atitle("[md]", size=9, color=color))
            _md_btn.setHidden_(False)
        else:
            _md_btn.setHidden_(True)

_update_md_indicator = _update_format_indicator


def _apply_terminal_style():
    """Apply uniform terminal color/font to all text in _tv."""
    if not _tv:
        return
    ts  = _tv.textStorage()
    rng = AppKit.NSMakeRange(0, ts.length())
    if rng.length == 0:
        return
    ts.beginEditing()
    ts.addAttribute_value_range_(
        AppKit.NSForegroundColorAttributeName, C_TEXT, rng)
    ts.addAttribute_value_range_(
        AppKit.NSFontAttributeName, _mono(_st["font_size"]), rng)
    ts.endEditing()


def _after_paste_plain():
    """Called after plain-text paste into _tv — convert to block and add to history."""
    if _tv:
        _st["text"] = str(_tv.string()).rstrip('\n')
    _apply_terminal_style()
    if not _st.get("rich_fmt"):
        _st["is_md"] = _is_markdown(_st["text"]) if _st["text"].strip() else False
    _update_format_indicator()
    _relayout_doc_view()
    _finalize_tv_to_block(add_to_history=True)


def _update_blocks_font():
    """Re-render all rich blocks with the current font size, then relayout."""
    for block in list(_rich_blocks):
        block._refresh_font()
    _relayout_doc_view()

def _relayout_doc_view():
    """Reposition rich blocks + _tv inside _doc_view (FlippedView, y=0 at top)."""
    if not _doc_view or not _tv or not _scroll:
        return
    cur_w = int(_doc_view.frame().size.width)
    if cur_w <= 0:
        return
    y = 0   # FlippedView: y=0 at top, grows downward

    for block in _rich_blocks:
        block_w = int(block.frame().size.width)
        if block_w != cur_w:
            bh = block._sync_to_width(cur_w)
        else:
            bh = int(block.frame().size.height)
        block.setFrame_(AppKit.NSMakeRect(0, y, cur_w, bh))
        y += bh + BLOCK_GAP

    if _rich_blocks:
        y += BLOCK_TV_GAP   # extra empty-line gap before _tv

    # _tv height from actual layout — guarded against layout manager issues
    vis_h = max(80, int(_scroll.frame().size.height) - y)
    try:
        lm = _tv.layoutManager()
        tc = _tv.textContainer()
        lm.ensureLayoutForTextContainer_(tc)
        used  = lm.usedRectForTextContainer_(tc)
        tv_h  = max(vis_h, int(used.size.height) + 40)
    except Exception as e:
        print(f"[overlay] _relayout_doc_view layout: {e}")
        tv_h = vis_h
    _tv.setFrame_(AppKit.NSMakeRect(0, y, cur_w, tv_h))

    _doc_view.setFrame_(AppKit.NSMakeRect(0, 0, cur_w, y + tv_h))
    _update_float_bar()


def _update_float_bar():
    """Show/hide/update the sticky floating [md]/[→] bar.
    The bar appears when a markdown block's header is scrolled above the viewport."""
    global _float_target
    if not _scroll or not _float_bar or not _doc_view:
        return
    vis = _doc_view.visibleRect()   # in FlippedView coords (y=0 at top)
    vis_top = vis.origin.y
    target = None
    for block in _rich_blocks:
        if not getattr(block, '_is_md_block', False):
            continue
        bf = block.frame()
        block_top = bf.origin.y
        block_bot = block_top + bf.size.height
        # Header above viewport, body still visible
        if block_top < vis_top and block_bot > vis_top + BLOCK_BTN_AREA:
            target = block
            break
    _float_target = target
    if target:
        # Position at top-right of scroll area (pill coords, y=0 at bottom)
        sf = _scroll.frame()
        bw, bh = 52, 16
        _float_bar.setFrame_(AppKit.NSMakeRect(
            sf.origin.x + sf.size.width - bw - 6,
            sf.origin.y + sf.size.height - bh - 2,
            bw, bh))
        # Sync [md] button state
        if _float_bar_md:
            is_raw = target._md_mode
            col = C_CYAN if is_raw else C_GREEN_DIM
            _float_bar_md._normal_col = col
            _float_bar_md.setAttributedTitle_(_atitle("md", size=9, color=col))
        _float_bar.setHidden_(False)
    else:
        _float_bar.setHidden_(True)


def _add_rich_block(md_text, hist_id=None):
    """Create a Markdown block and add it to the document view (above _tv).
    hist_id: if provided, use it (no new history entry created).
    """
    if not _doc_view:
        return
    try:
        idx   = len(_rich_blocks)
        block = _make_rich_block(md_text, idx)
        _rich_blocks.append(block)
        _doc_view.addSubview_(block)
        _st["rich_fmt"] = "md"
        # Inner cursor at end of block text
        if block._inner_tv:
            end = block._inner_tv.textStorage().length()
            block._inner_tv.setSelectedRange_(AppKit.NSMakeRange(end, 0))
        # Clear _tv so next speech starts fresh below the block
        if _tv:
            _tv.setString_("")
            _st["text"] = ""
            _st["is_md"] = False
            _st["md_mode"] = False
            _update_format_indicator()
        _relayout_doc_view()
        if _win and _tv:
            _win.orderFrontRegardless()
            _win.makeFirstResponder_(_tv)
            _tv.setSelectedRange_(AppKit.NSMakeRange(0, 0))
            _tv.scrollRangeToVisible_(AppKit.NSMakeRange(0, 0))
        block._original_text    = md_text
        if hist_id is None:
            hist_id = _on_add_history_cb(md_text) if _on_add_history_cb else None
        block._hist_id          = hist_id
        block._original_hist_id = hist_id
        if _on_update_session_cb:
            _on_update_session_cb()
    except Exception as e:
        print(f"[overlay] _add_rich_block error: {e}")


def _remove_all_rich_blocks():
    """Remove all rich blocks, clear rich state, relayout."""
    for block in _rich_blocks:
        block.removeFromSuperview()
    _rich_blocks.clear()
    _st["rich_fmt"]   = None
    _st["rich_mode"]  = False
    _st["rich_attrs"] = None
    _relayout_doc_view()


def _toggle_md_mode():
    """Toggle between raw Markdown text and terminal-rendered view."""
    md_mode = not _st.get("md_mode", False)
    _st["md_mode"] = md_mode
    if _tv:
        if md_mode:
            rendered = _render_md_terminal(_st["text"])
            _tv.setString_(rendered + '\n')
            _tv.setEditable_(False)
        else:
            raw = _st["text"]
            display = raw.rstrip('\n') + '\n'
            _tv.setString_(display)
            _tv.setEditable_(True)
            if _win:
                _win.makeFirstResponder_(_tv)
    _update_format_indicator()
    _relayout_doc_view()


def _update_cursor_pos():
    """Reposition the fake block cursor to the current insertion point (main thread)."""
    if not _tv or not _cur_view or not _win:
        return
    sel = _tv.selectedRange()
    r   = AppKit.NSMakeRange(sel.location, 0)
    # firstRectForCharacterRange gives screen-space rect for the insertion point
    screen_rect, _ = _tv.firstRectForCharacterRange_actualRange_(r, None)
    if screen_rect.size.height < 1:
        return
    # Convert screen → window → text-view coordinate space
    win_rect = _win.convertRectFromScreen_(screen_rect)
    tv_rect  = _tv.convertRect_fromView_(win_rect, None)
    h = max(tv_rect.size.height, 14.0)
    _cur_view.setFrame_(AppKit.NSMakeRect(tv_rect.origin.x, tv_rect.origin.y, 8.0, h))
    _cur_view.setHidden_(False)
    _cur_view.setNeedsDisplay_(True)


def _toggle_expand():
    global _expanded, _font_size_saved
    if not _expanded:
        _expanded = True
        _font_size_saved = _st["font_size"]
        new_sz = min(28.0, _st["font_size"] * 1.6)
        _st["font_size"] = new_sz
        _do_win_resize(H_EXP, W_EXP)
        _relayout_buttons(W_EXP)
        if _tv:
            _tv.setFont_(_mono(new_sz))
        _main(_update_blocks_font)
        if _expand_btn:
            _expand_btn.setAttributedTitle_(_atitle("[─]", size=12, color=C_GREEN_DIM))
        if _win:
            _win.setAlphaValue_(_st["opacity"])
    else:
        _expanded = False
        if _font_size_saved is not None:
            _st["font_size"] = _font_size_saved
        _do_win_resize(H, W)
        _relayout_buttons(W)
        if _tv:
            _tv.setFont_(_mono(_st["font_size"]))
        _main(_update_blocks_font)
        if _expand_btn:
            _expand_btn.setAttributedTitle_(_atitle("[□]", size=12, color=C_GREEN_DIM))
        if _win:
            _win.setAlphaValue_(_st["opacity"])
    # Reposition attached panels (their SIZE stays unchanged — 440×440)
    _reposition_attached_panels()


def _do_win_resize(new_h, new_w=None, animate=True):
    """Resize window keeping its TOP-RIGHT corner fixed."""
    if not _win:
        return
    f    = _win.frame()
    top  = f.origin.y + f.size.height
    right = f.origin.x + f.size.width
    nw   = new_w if new_w is not None else f.size.width
    ny   = top - new_h
    nx   = right - nw    # keep right edge fixed
    _win.setFrame_display_animate_(
        AppKit.NSMakeRect(nx, ny, nw, new_h), True, animate
    )
    # Scroll view: autoresizing handles width; we only need to fix height
    if _scroll:
        new_txt_top = new_h - HDR_H - 2
        new_txt_h   = new_txt_top - TXT_Y
        sw = nw - 16
        _scroll.setFrame_(
            AppKit.NSMakeRect(8, TXT_Y, sw, max(50, new_txt_h))
        )
    _relayout_doc_view()
    _main(_update_float_bar)


def _relayout_buttons(w):
    """Compute adaptive scenario grid: [<] always left, [>] always right, fill between.

    Computes _sc_page_size dynamically from window width so buttons fill available space.
    Nav arrows are always positioned at edges; enabled/disabled based on page position.
    """
    global _sc_page, _sc_page_size
    scs = _st["scenarios"]
    n   = len(scs)

    NAV_W   = 26   # nav arrow button width
    NAV_GAP = 4    # gap between arrow and first/last scenario
    SC_W    = 54   # scenario button width
    SEP_W   = 14   # · separator width
    MARGIN  = 8    # left/right window margin

    # Nav arrows always at fixed edge positions
    nav_y = BTN_Y + 11
    if _sc_prev_btn:
        _sc_prev_btn.setFrame_(AppKit.NSMakeRect(MARGIN, nav_y, NAV_W, 24))
    if _sc_next_btn:
        _sc_next_btn.setFrame_(AppKit.NSMakeRect(w - MARGIN - NAV_W, nav_y, NAV_W, 24))

    # Compute how many scenario slots fit between the two arrows
    inner_w = w - 2 * (MARGIN + NAV_W + NAV_GAP)
    # n slots: SC_W*n + SEP_W*(n-1) = (SC_W+SEP_W)*n - SEP_W ≤ inner_w
    _sc_page_size = max(1, (inner_w + SEP_W) // (SC_W + SEP_W))

    # Clamp current page
    max_page = max(0, (n - 1) // _sc_page_size) if n > 0 else 0
    if _sc_page > max_page:
        _sc_page = max_page

    page_start = _sc_page * _sc_page_size
    page_end   = min(page_start + _sc_page_size, n)
    page_count = page_end - page_start

    # Position scenario slots starting after left arrow
    col = MARGIN + NAV_W + NAV_GAP
    active_sc = _st.get("active_sc")
    for i, btn in enumerate(_sc_icons):
        if i < page_count:
            sc_idx = page_start + i
            sc     = scs[sc_idx]
            label  = _sc_label(sc)
            color  = C_GREEN_BR if sc_idx == active_sc else C_GREEN_DIM
            btn.setAttributedTitle_(_atitle(label, size=11, color=color))
            btn.setToolTip_(sc.get("name", ""))
            btn.setTag_(sc_idx)
            btn.setFrame_(AppKit.NSMakeRect(col, nav_y, SC_W, 24))
            _sc_active[i] = True
            col += SC_W
            if i < len(_sc_seps):
                if i < page_count - 1:
                    _sc_seps[i].setFrame_(AppKit.NSMakeRect(col, nav_y, SEP_W, 24))
                    _sc_sep_active[i] = True
                    col += SEP_W
                else:
                    _sc_sep_active[i] = False
        else:
            _sc_active[i] = False
            if i < len(_sc_sep_active):
                _sc_sep_active[i] = False

    # Enable/disable nav arrows based on current page position
    if _sc_prev_btn:
        _sc_prev_btn.setEnabled_(_sc_page > 0)
    if _sc_next_btn:
        _sc_next_btn.setEnabled_(page_end < n)


def _refresh_scenario_colors():
    """Update scenario button colors: processing=yellow, active=bright, unavailable=red, normal=dim."""
    active_sc = _st.get("active_sc")
    scs = _st["scenarios"]
    for i, btn in enumerate(_sc_icons):
        if not (_sc_active[i] if i < len(_sc_active) else False):
            continue
        sc_idx = int(btn.tag())
        sc     = scs[sc_idx] if 0 <= sc_idx < len(scs) else {}
        label  = _sc_label(sc)
        is_fd  = bool(sc.get("full_default"))
        is_sil = bool(sc.get("silent"))
        if is_fd and is_sil: label = "[·" + label + "·]"
        elif is_fd:           label = "[" + label + "]"
        avail  = _sc_avail.get(sc_idx, True)
        if sc_idx == _proc_sc_idx:
            color = C_YEL
        elif not avail:
            color = C_REC
        elif sc_idx == active_sc:
            color = C_GREEN_BR
        else:
            color = C_GREEN_DIM
        btn.setAttributedTitle_(_atitle(label, size=11, color=color))


def _start_sc_avail_check():
    """Check all scenario model availability in background, then refresh button colors."""
    global _sc_avail
    _sc_avail.clear()
    scs = list(_st.get("scenarios", []))

    def _run(scenarios=scs):
        avail = {}
        for i, sc in enumerate(scenarios):
            m = sc.get("model") or ""
            avail[i] = _model_available(m) if m else True
        _sc_avail.update(avail)
        _main(_refresh_scenario_colors)

    threading.Thread(target=_run, daemon=True).start()


def _end_processing():
    """Clear LLM-processing state; caller decides which header to show next."""
    global _proc_sc_idx, _proc_interrupt_fn
    _proc_sc_idx = None
    _proc_interrupt_fn = None
    if _proc_hover_v:
        _proc_hover_v._hover_active = False
        _proc_hover_v.setHidden_(True)

    # Restore gear icon (hidden by show_processing); expand/close remain hidden permanently
    if _cfg_hdr_btn: _cfg_hdr_btn.setHidden_(False)
    if _proc_sc_lbl: _proc_sc_lbl.setHidden_(True)

    # Restore waveform; recompute adaptive EQ width
    if _wf:
        _wf.setHidden_(False)
    if _proc_eq_v:
        _proc_eq_v.setHidden_(True)
    _layout_header_wf()

    _stop_timer()


def set_active_scenario(idx):
    """Highlight active scenario; show 2-button result panel or restore 3-button row."""
    _st["active_sc"] = idx
    _st["sc_picker"] = False   # close picker whenever scenario state changes
    def _():
        _refresh_scenario_colors()
        _show_buttons(True)
        for b in _rich_blocks:
            b.set_sc_undo(False)
    _main(_)


def is_editing_scenario() -> bool:
    """Return True while the scenario editor panel is open (hotkey should be suppressed)."""
    return _editing_scenario


def set_undo_scenario_callback(fn):
    global _on_undo_sc_cb
    _on_undo_sc_cb = fn


_HDR_GAP = 8          # gap between app name and EQ, and between EQ and gear icon

def _layout_header_wf():
    """Measure app name width, position label, then stretch EQ to fill remaining space.
    Must run on main thread."""
    ICON_END  = STS_X + 22 + 4    # icon right edge + inner gap = 36
    RIGHT_END = CFG_H_X - _HDR_GAP  # left edge of gear icon minus gap

    font  = _mono(11)
    d     = {AppKit.NSFontAttributeName: font}
    name  = _prev_app_name or ""
    raw_w = int(AppKit.NSString.stringWithString_(name).sizeWithAttributes_(d).width) if name else 0
    name_w = raw_w + 6

    # Name can take up to half the available space
    max_name = (RIGHT_END - ICON_END - _HDR_GAP) // 2
    name_w   = max(0, min(name_w, max_name))
    name_end = ICON_END + name_w

    if _proc_app_lbl:
        _proc_app_lbl.setFrame_(AppKit.NSMakeRect(ICON_END, HDR_ITEM_Y - 2, name_w, HDR_ITEM_H))

    # EQ fills all remaining space: from name_end+gap to gear icon
    eq_x = name_end + _HDR_GAP
    eq_w = max(40, RIGHT_END - eq_x)
    if _wf:
        _wf.setFrame_(AppKit.NSMakeRect(eq_x, HDR_ITEM_Y, eq_w, HDR_ITEM_H))
    if _proc_eq_v:
        _proc_eq_v.setFrame_(AppKit.NSMakeRect(eq_x, HDR_ITEM_Y, eq_w, HDR_ITEM_H))

    return name_end


def _show_target_app_header():
    """Show app icon + name in header. Call on main thread. No-op during active processing."""
    if _proc_sc_idx is not None:
        return   # show_processing() manages this state
    if _app_icon_v:
        _app_icon_v.setHidden_(False)
    if _proc_app_lbl:
        _proc_app_lbl.setStringValue_(_prev_app_name)
        _proc_app_lbl.setHidden_(False)
    if _lbl:
        _lbl.setHidden_(True)
    _layout_header_wf()


def _hide_target_app_header():
    """Hide icon + name, used only when overlay goes idle."""
    if _app_icon_v:
        _app_icon_v.setHidden_(True)
    if _proc_app_lbl:
        _proc_app_lbl.setHidden_(True)
    if _lbl:
        _lbl.setHidden_(False)
    # Reset waveform to fixed centered position
    if _wf:
        _layout_header_wf()


def set_prev_app_icon(app):
    """Обновить иконку и имя целевого приложения. app = NSRunningApplication или None."""
    global _prev_app_name
    try:
        _prev_app_name = str(app.localizedName() or "") if app else ""
    except Exception:
        _prev_app_name = ""

    def _():
        # Update icon image
        if _app_icon_v:
            if app:
                try:
                    img = app.icon()
                    if img:
                        _app_icon_v.setImage_(img)
                except Exception:
                    pass

        # In any active mode — refresh header icon + name
        mode = _st.get("mode", "idle")
        if mode != "idle":
            _show_target_app_header()
    _main(_)


# ── Drop panels ───────────────────────────────────────────────────────────────

def _close_cfg_panel():
    """Close the cfg panel and restore main window Y if it was shifted to make room."""
    global _cfg_panel, _pre_cfg_win_y
    if _cfg_panel:
        _cfg_panel.orderOut_(None)
        _cfg_panel.close()
        _cfg_panel = None
    if _pre_cfg_win_y is not None and _win:
        fr = _win.frame()
        _win.setFrameOrigin_(AppKit.NSMakePoint(fr.origin.x, _pre_cfg_win_y))
        _pre_cfg_win_y = None
        # History panel follows the main window back to its original Y
        if _hist_panel and _hist_panel.isVisible():
            _reposition_attached_panels()

def _close_cfg_panel_rebuild():
    """Close cfg panel only — keep window shifted; used when immediately reopening panel."""
    global _cfg_panel
    if _cfg_panel:
        _cfg_panel.orderOut_(None)
        _cfg_panel.close()
        _cfg_panel = None


def _make_drop_panel(w, h):
    """Create a terminal-styled borderless floating panel."""
    p = _DropPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        AppKit.NSMakeRect(0, 0, w, h),
        AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered, False,
    )
    p.setOpaque_(False)
    p.setBackgroundColor_(AppKit.NSColor.clearColor())
    p.setLevel_(AppKit.NSFloatingWindowLevel + 1)
    p.setHasShadow_(True)
    p.setHidesOnDeactivate_(False)   # stay visible when app loses focus
    bg = TerminalView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, w, h))
    bg.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
    p.setContentView_(bg)
    return p


def _panel_origin(pw, ph, align="right", prefer="below"):
    """Position a drop panel adjacent to the main window.
    prefer='below'  → opens below main window (fallback: above if off-screen)
    prefer='above'  → always opens above main window (cfg panel)
    """
    mf  = _win.frame()
    gap = 6
    if align == "right":
        px = mf.origin.x + mf.size.width - pw
    else:
        px = mf.origin.x

    py_below = mf.origin.y - ph - gap
    py_above = mf.origin.y + mf.size.height + gap

    if prefer == "above":
        py = py_above
    else:
        screen = AppKit.NSScreen.mainScreen()
        if screen:
            vis = screen.visibleFrame()
            py = py_below if py_below >= vis.origin.y else py_above
        else:
            py = py_below

    return AppKit.NSMakePoint(px, py)


def _hist_side_origin(pw, ph):
    """Return NSPoint to position history panel to the right or left of the main window.
    Returns None if there is no room on either side.
    """
    if not _win:
        return None
    mf  = _win.frame()
    gap = 6
    py  = mf.origin.y   # align bottom edges
    screen = AppKit.NSScreen.mainScreen()
    vis = screen.visibleFrame() if screen else None
    # Try right side first
    px_right = mf.origin.x + mf.size.width + gap
    if vis is None or px_right + pw <= vis.origin.x + vis.size.width:
        return AppKit.NSMakePoint(px_right, py)
    # Fall back to left side
    px_left = mf.origin.x - pw - gap
    if vis is None or px_left >= vis.origin.x:
        return AppKit.NSMakePoint(px_left, py)
    return None   # no room on either side


def _reposition_attached_panels():
    """Move magnetically-attached panels to track _win position."""
    win = globals().get("_win")
    if not win:
        return
    mf  = win.frame()
    wo  = mf.origin
    ww  = int(mf.size.width)
    wh  = int(mf.size.height)
    GAP = _SNAP_GAP
    _default_offset = {
        "cfg":       (0,        wh + 4),
        "hist":      (0,       -(H_PANEL + GAP)),
        "editor":    (ww + GAP, 0),
        "providers": (-(ww + GAP), 0),
    }
    for key, panel_name in [("cfg",       "_cfg_panel"),
                              ("hist",      "_hist_panel"),
                              ("editor",    "_sc_editor_panel"),
                              ("providers", "_prov_panel")]:
        if not _magnet_on.get(key, False):
            continue
        panel = globals().get(panel_name)
        if not panel:
            continue
        try:
            if not panel.isVisible():
                continue
            ph = int(panel.frame().size.height)
        except Exception:
            continue
        if key in _magnet_offset:
            dx, dy = _magnet_offset[key]
        else:
            dx, dy = _default_offset.get(key, (0, 0))
            _magnet_offset[key] = (dx, dy)
        nx, ny = int(wo.x + dx), int(wo.y + dy)
        # Safety: never let a "top" panel overlap the main window from above
        if dy > 0 and ny < int(wo.y) + wh + 2:
            ny = int(wo.y) + wh + GAP
            _magnet_offset[key] = (dx, wh + GAP)
        # Safety: never let a "bottom" panel overlap from below
        elif dy < 0 and ny + ph > int(wo.y) - 2:
            ny = int(wo.y) - ph - GAP
            _magnet_offset[key] = (dx, -(ph + GAP))
        try:
            panel.setFrameOrigin_(AppKit.NSMakePoint(nx, ny))
        except Exception:
            pass


def _reset_panels_layout():
    """🎯 toggle: first press shows all panels at their SAVED positions; second press hides all."""
    global _panels_reset_open

    if _panels_reset_open:
        _panels_reset_open = False
        _close_editor_now()
        _close_providers_panel()
        if _hist_panel and _hist_panel.isVisible():
            _hist_panel.orderOut_(None)
            _cfg_saved.setdefault("panels_open", {})["hist"] = False
            _save_settings()
        _close_cfg_panel()
        return

    _panels_reset_open = True
    win = globals().get("_win")
    if not win:
        return
    # Show panels at wherever their saved offsets put them (no reset of offsets)
    if not (_cfg_panel and _cfg_panel.isVisible()):
        _toggle_cfg_panel()
    if not (_prov_panel and _prov_panel.isVisible()):
        _toggle_providers_panel()
    sc_list = _st.get("scenarios", [])
    if sc_list and not (_sc_editor_panel and _sc_editor_panel.isVisible()):
        _show_sc_editor_impl(0)
    history = _on_history_cb() if _on_history_cb else []
    if not (_hist_panel and _hist_panel.isVisible()):
        _show_hist_panel(history)
    _reposition_attached_panels()


def _reset_to_cross_layout():
    """[✚] Hard reset to default cross: cfg top, hist bottom, providers left, editor right."""
    global _magnet_on, _magnet_offset, _magnet_free_pos, _panels_reset_open

    _panels_reset_open = True
    # All panels magneted for the default cross
    _magnet_on       = {k: True for k in _MAGNET_KEYS}
    _magnet_free_pos = {}
    for k in _MAGNET_KEYS:
        _update_magnet_btn(k)
    win = globals().get("_win")
    if not win:
        _magnet_save()
        return
    mf = win.frame()
    ww = int(mf.size.width)
    wh = int(mf.size.height)
    G  = _SNAP_GAP
    # Force default cross offsets
    _magnet_offset = {
        "cfg":       (0,        wh + 4),
        "hist":      (0,       -(H_PANEL + G)),
        "editor":    (ww + G,   0),
        "providers": (-(ww + G), 0),
    }
    _magnet_save()
    # Reopen cfg to pick up new offset (close→open for visual snap effect)
    if _cfg_panel and _cfg_panel.isVisible():
        _close_cfg_panel_rebuild()
    _toggle_cfg_panel()
    if not (_prov_panel and _prov_panel.isVisible()):
        _toggle_providers_panel()
    sc_list = _st.get("scenarios", [])
    if sc_list and not (_sc_editor_panel and _sc_editor_panel.isVisible()):
        _show_sc_editor_impl(0)
    history = _on_history_cb() if _on_history_cb else []
    if not (_hist_panel and _hist_panel.isVisible()):
        _show_hist_panel(history)
    _reposition_attached_panels()


def _restore_overlay_after_panel():
    """Restore overlay state + focus after a drop panel is closed via Escape."""
    if _st.get("mode") == "history_open":
        has_content = bool(_st.get("text", "").strip() or _rich_blocks)
        _st["mode"] = "ready" if has_content else "idle"
        if has_content:
            _show_target_app_header()
        else:
            _hide_target_app_header()
        _show_buttons(has_content)
    if _win and _tv:
        _win.makeKeyAndOrderFront_(None)
        _win.makeFirstResponder_(_tv)


def _close_all_panels():
    """Close any open drop panels."""
    if _hist_panel and _hist_panel.isVisible():
        _hist_panel.orderOut_(None)
    _close_cfg_panel()


def _hist_filter_items(items, mode):
    """Return items matching the active filter mode."""
    if mode == "sessions":
        return [i for i in items if i.get("type") == "session"]
    elif mode == "blocks":
        return [i for i in items if i.get("type") != "session"]
    return list(items)   # "mixed" — all


def _build_hist_docview(ctrl, scroll_w, scroll_h, CHK_W, CHK_R):
    """Build (or rebuild) the flipped docview for the history scroll area."""
    items = ctrl._items
    pw    = ctrl._pw
    n     = len(items)

    DP_HIST_ITEM_H = 26
    ITEM_X    = 20
    ITEM_RPAD = CHK_W + CHK_R + 6
    ITEM_W    = pw - ITEM_X - ITEM_RPAD
    CHAR_W    = 5.8
    TOTAL_CH  = max(10, int(ITEM_W / CHAR_W))
    C_DOTS    = _rgba(0.00, 0.45, 0.00, 0.22)
    C_SESSION = _rgba(0.20, 0.80, 0.80, 0.90)

    doc_h   = max(scroll_h, n * DP_HIST_ITEM_H) if n > 0 else scroll_h
    docview = _FlippedView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, pw, doc_h))

    if n == 0:
        empty = _mklabel(_T("hist_empty"), size=10, color=C_IDLE)
        empty.setFrame_(AppKit.NSMakeRect(12, 12, pw - 24, 16))
        docview.addSubview_(empty)
    else:
        for i, item in enumerate(items):
            short      = item["short"]
            full       = item["full"]
            ts         = item.get("created_at", "")[:16].replace("T", " ")
            row_y      = i * DP_HIST_ITEM_H
            is_session = item.get("type") == "session"

            if is_session:
                n_blocks = len(item.get("blocks", []))
                prefix   = "⊞ "
                display  = prefix + short
                ts      += f"  [{n_blocks} блоков]"
            else:
                prefix  = ""
                display = short

            dot_ch  = max(0, TOTAL_CH - len(display) - 1)
            raw_str = display + (" " + "·" * dot_ch if dot_ch > 0 else "")
            ns_str  = AppKit.NSMutableAttributedString.alloc().initWithString_(raw_str)
            ps = AppKit.NSMutableParagraphStyle.alloc().init()
            ps.setAlignment_(AppKit.NSTextAlignmentLeft)
            if is_session and prefix:
                ns_str.setAttributes_range_({
                    AppKit.NSFontAttributeName:            _mono(10),
                    AppKit.NSForegroundColorAttributeName: C_SESSION,
                    AppKit.NSParagraphStyleAttributeName:  ps,
                }, AppKit.NSMakeRange(0, len(prefix)))
            ns_str.setAttributes_range_({
                AppKit.NSFontAttributeName:            _mono(10),
                AppKit.NSForegroundColorAttributeName: C_TEXT,
                AppKit.NSParagraphStyleAttributeName:  ps,
            }, AppKit.NSMakeRange(len(prefix), len(display) - len(prefix)))
            if dot_ch > 0:
                ns_str.setAttributes_range_({
                    AppKit.NSFontAttributeName:            _mono(10),
                    AppKit.NSForegroundColorAttributeName: C_DOTS,
                    AppKit.NSParagraphStyleAttributeName:  ps,
                }, AppKit.NSMakeRange(len(display), len(raw_str) - len(display)))

            item_btn = AppKit.NSButton.alloc().init()
            item_btn.setBordered_(False)
            item_btn.setAttributedTitle_(ns_str)
            item_btn.setFrame_(AppKit.NSMakeRect(ITEM_X, row_y + 1, ITEM_W, DP_HIST_ITEM_H - 2))
            if is_session:
                rep = {"id": item["id"], "full": full,
                       "type": "session",
                       "blocks_text": item.get("blocks_text", [])}
            else:
                rep = {"id": item["id"], "full": full}
            item_btn.setRepresentedObject_(rep)
            item_btn.setToolTip_(ts)
            item_btn.setTarget_(_btn_t)
            item_btn.setAction_(BtnTarget.histItem_)
            docview.addSubview_(item_btn)

            chk = _HoverBtn.alloc().init()
            chk.setBordered_(False)
            chk._chk_lbl    = "[ ]"
            chk._chk_sz     = 9
            chk._normal_col = C_GREEN_DIM
            chk.setAttributedTitle_(_atitle("[ ]", size=9, color=C_GREEN_DIM))
            chk.setFrame_(AppKit.NSMakeRect(pw - CHK_W - CHK_R, row_y + 2,
                                            CHK_W, DP_HIST_ITEM_H - 4))
            chk.setTag_(i)
            chk.setTarget_(ctrl)
            chk.setAction_(_HistCtrl.toggle_)
            docview.addSubview_(chk)
            ctrl._chk[i] = chk

    return docview


def _rebuild_hist_scroll(ctrl):
    """Replace scroll document view after filter change; hide/show action buttons."""
    if not ctrl._scroll:
        return
    sf      = ctrl._scroll.frame()
    scroll_w = int(sf.size.width)
    scroll_h = int(sf.size.height)
    docview = _build_hist_docview(ctrl, scroll_w, scroll_h, ctrl._CHK_W, ctrl._CHK_R)
    ctrl._scroll.setDocumentView_(docview)
    # Reset action footer buttons visibility
    has = False
    for attr in ('_del_btn', '_merge_btn', '_append_btn', '_replace_btn'):
        b = getattr(ctrl, attr, None)
        if b:
            b.setHidden_(True)
    if ctrl._all_btn:
        ctrl._all_btn.setHidden_(len(ctrl._items) == 0)


_LAUNCH_AGENT_LABEL = "net.alexbic.hush"
_LAUNCH_AGENT_PLIST = os.path.expanduser(
    f"~/Library/LaunchAgents/{_LAUNCH_AGENT_LABEL}.plist")

def _is_launch_at_login():
    return os.path.exists(_LAUNCH_AGENT_PLIST)

def _toggle_launch_at_login():
    import subprocess
    if _is_launch_at_login():
        subprocess.call(["launchctl", "unload", _LAUNCH_AGENT_PLIST],
                        stderr=subprocess.DEVNULL)
        try:
            os.remove(_LAUNCH_AGENT_PLIST)
        except OSError:
            pass
    else:
        python = sys.executable
        script = os.path.abspath(os.path.join(os.path.dirname(__file__), "main.py"))
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_LAUNCH_AGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{script}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{os.path.expanduser("~/Library/Logs/hush.log")}</string>
    <key>StandardErrorPath</key>
    <string>{os.path.expanduser("~/Library/Logs/hush.log")}</string>
</dict>
</plist>
"""
        os.makedirs(os.path.dirname(_LAUNCH_AGENT_PLIST), exist_ok=True)
        with open(_LAUNCH_AGENT_PLIST, "w") as f:
            f.write(plist)


def _setup_status_bar():
    """Create macOS menu bar status item with About and Quit."""
    global _status_bar_item
    bar = AppKit.NSStatusBar.systemStatusBar()
    _status_bar_item = bar.statusItemWithLength_(AppKit.NSSquareStatusItemLength)
    btn = _status_bar_item.button()
    if btn:
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hush2.png")
        ns_img = AppKit.NSImage.alloc().initWithContentsOfFile_(icon_path)
        if ns_img:
            ns_img.setSize_(AppKit.NSMakeSize(18, 18))
            ns_img.setTemplate_(True)   # macOS auto-adapts to light/dark menu bar
            btn.setImage_(ns_img)
        else:
            btn.setTitle_("H")
            btn.setFont_(_mono(13, True))
        btn.setToolTip_("HUSH — голосовой ввод")
    menu = AppKit.NSMenu.alloc().init()
    open_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Открыть HUSH", "openHush:", "")
    open_item.setTarget_(_btn_t)
    # Show ⇧⌥ right-aligned on same line (native macOS shortcut display)
    # U+2325 = ⌥ option symbol as the "key"; Shift modifier adds ⇧ prefix → displays "⇧⌥"
    open_item.setKeyEquivalent_("⌥")
    open_item.setKeyEquivalentModifierMask_(AppKit.NSEventModifierFlagShift)
    menu.addItem_(open_item)
    menu.addItem_(AppKit.NSMenuItem.separatorItem())
    about_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "О приложении", "showAbout:", "")
    about_item.setTarget_(_btn_t)
    menu.addItem_(about_item)
    menu.addItem_(AppKit.NSMenuItem.separatorItem())
    login_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Запускать при входе в систему", "toggleLaunchAtLogin:", "")
    login_item.setTarget_(_btn_t)
    login_item.setState_(1 if _is_launch_at_login() else 0)
    menu.addItem_(login_item)
    _btn_t._login_menu_item = login_item   # keep reference for updates
    menu.addItem_(AppKit.NSMenuItem.separatorItem())
    quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Завершить HUSH", "quitApp:", "")
    quit_item.setTarget_(_btn_t)
    menu.addItem_(quit_item)
    menu.setDelegate_(_btn_t)   # menuNeedsUpdate_ refreshes checkmark on open
    _status_bar_item.setMenu_(menu)
    # macOS 14+: NSStatusItem.visible persists and may default to hidden in some contexts
    try:
        _status_bar_item.setVisible_(True)
    except Exception:
        pass
    pass


def _show_about_view():
    """Show About card as a standalone NSPanel centered on screen."""
    global _about_panel

    _hide_about_view()

    AW, AH = 560, 480

    # Center on main screen
    sf = AppKit.NSScreen.mainScreen().frame()
    px = sf.origin.x + (sf.size.width  - AW) / 2
    py = sf.origin.y + (sf.size.height - AH) / 2

    ap = _AboutPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        AppKit.NSMakeRect(px, py, AW, AH),
        AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered, False,
    )
    ap.setOpaque_(False)
    ap.setBackgroundColor_(AppKit.NSColor.clearColor())
    ap.setLevel_(AppKit.NSFloatingWindowLevel + 2)
    ap.setHasShadow_(True)
    ap.setHidesOnDeactivate_(False)

    # Click-anywhere-to-close background
    bg = _AboutBgView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, AW, AH))
    bg.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
    ap.setContentView_(bg)

    PAD_X  = 20    # horizontal padding
    PAD_B  = 12    # bottom padding
    GAP    = 6     # gap between footer rows
    ROW_H  = 20    # copyright / github row height

    lang = _st.get("lang", "ru")

    # ── Top-right corner: wallet donate (closed → open on hover) ─────────────
    W_W, W_H = _WalletView._VW, _WalletView._VH
    wallet_v = _WalletView.alloc().initWithFrame_(
        AppKit.NSMakeRect(AW - W_W - PAD_X, AH - W_H - 8, W_W, W_H))
    wallet_v.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMinYMargin)
    bg.addSubview_(wallet_v)

    # ── Second row from bottom: github link (centered) ────────────────────────
    GH_Y = PAD_B + ROW_H + GAP
    GH_W = 200
    gh_btn = _mklinkbtn("[ github.com/alexbic ]", color=C_TEXT, size=10)
    gh_btn.setFrame_(AppKit.NSMakeRect((AW - GH_W) / 2, GH_Y, GH_W, ROW_H))
    gh_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMaxXMargin | AppKit.NSViewMaxYMargin)
    gh_btn.setTarget_(_btn_t)
    gh_btn.setAction_(BtnTarget.aboutGithub_)
    bg.addSubview_(gh_btn)

    # ── Third row: copyright + author (centered) → link to site ──────────────
    CR_Y = GH_Y + ROW_H + GAP
    CR_W = 320
    cr_btn = _mklinkbtn("© 2026 Alexander Bikmukhametov", color=C_GREEN, size=10)
    cr_btn.setFrame_(AppKit.NSMakeRect((AW - CR_W) / 2, CR_Y, CR_W, ROW_H))
    cr_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMaxXMargin | AppKit.NSViewMaxYMargin)
    cr_btn.setTarget_(_btn_t)
    cr_btn.setAction_(BtnTarget.aboutSite_)
    bg.addSubview_(cr_btn)

    # ── Brand image (fills top area) ──────────────────────────────────────────
    IMG_BOT = CR_Y + ROW_H + 10
    IMG_TOP = 10
    img_y = IMG_BOT
    img_w = AW - PAD_X * 2
    img_h = AH - IMG_BOT - IMG_TOP

    img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hush_brand_full.png")
    ns_img   = AppKit.NSImage.alloc().initWithContentsOfFile_(img_path)
    if ns_img:
        iv = AppKit.NSImageView.alloc().initWithFrame_(
            AppKit.NSMakeRect(PAD_X, img_y, img_w, img_h))
        iv.setImage_(ns_img)
        iv.setImageScaling_(3)   # NSImageScaleProportionallyUpOrDown
        iv.setImageAlignment_(0) # NSImageAlignCenter
        iv.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        bg.addSubview_(iv)
    else:
        tf = AppKit.NSTextField.labelWithString_("[ HUSH ]")
        tf.setEditable_(False); tf.setBezeled_(False); tf.setDrawsBackground_(False)
        tf.setFont_(_mono(20, True)); tf.setTextColor_(C_GREEN_DIM)
        tf.setFrame_(AppKit.NSMakeRect(PAD_X, img_y + img_h // 2 - 14, img_w, 28))
        bg.addSubview_(tf)

    ap.setAcceptsMouseMovedEvents_(True)
    ap.makeKeyAndOrderFront_(None)
    _about_panel = ap


def _hide_about_view():
    """Close and release About card panel."""
    global _about_panel
    if _about_panel:
        _about_panel.close()
        _about_panel = None


def _show_hist_panel(history):
    global _hist_panel, _hist_ctrl, _hist_panel_side
    # Toggle: close if already open
    if _hist_panel and _hist_panel.isVisible():
        _hist_panel.orderOut_(None)
        _hist_panel_side = None
        # Reset history-browser mode if active
        if _st["mode"] == "history_open":
            _st["mode"] = "idle" if not _st["text"] else "ready"
        _cfg_saved.setdefault("panels_open", {})["hist"] = False
        _save_settings()
        return

    CHK_W   = 26
    CHK_R   = 16   # right margin — keeps checkboxes clear of the 3px overlay scrollbar
    HDR_H   = 32
    FOOT_H  = 30
    BOT_PAD = 10
    FIXED   = HDR_H + FOOT_H + BOT_PAD
    # Size panel by filtered item count (current active tab)
    n_filtered = len(_hist_filter_items(history, _hist_filter))
    pw      = int(_win.frame().size.width)
    mf      = _win.frame()
    gap     = 6
    screen  = AppKit.NSScreen.mainScreen()
    vis     = screen.visibleFrame() if screen else None

    # Fixed height same as providers; open to the LEFT of main window
    ph        = H_PANEL
    GAP_H     = 6
    if _magnet_on.get("hist", False) and "hist" in _magnet_offset:
        dx, dy = _magnet_offset["hist"]
        px = int(mf.origin.x + dx)
        py = int(mf.origin.y + dy)
    elif "hist" in _magnet_free_pos and not _magnet_on.get("hist", False):
        px, py = int(_magnet_free_pos["hist"][0]), int(_magnet_free_pos["hist"][1])
    else:
        px_left  = int(mf.origin.x - pw - GAP_H)
        px_right = int(mf.origin.x + mf.size.width + GAP_H)
        fits_left  = (vis is None or px_left >= vis.origin.x)
        if fits_left:
            px = px_left
        else:
            px = px_right
        py = int(mf.origin.y)
        if _magnet_on.get("hist", True):
            _magnet_offset["hist"] = (px - int(mf.origin.x), 0)
        else:
            _magnet_free_pos["hist"] = (px, py)
    if vis:
        py = min(py, int(vis.origin.y + vis.size.height) - ph)
        py = max(py, int(vis.origin.y))
    panel_origin  = AppKit.NSMakePoint(px, py)
    _hist_panel_side = "left"

    if _hist_panel:
        _hist_panel.orderOut_(None)
        _hist_panel.close()

    _hist_panel = _make_drop_panel(pw, ph)
    _hist_panel._panel_key = "hist"
    cv = _hist_panel.contentView()

    # ── Controller ───────────────────────────────────────────────────────────
    ctrl              = _HistCtrl.alloc().init()
    ctrl._all_items   = list(history)
    ctrl._items       = _hist_filter_items(history, _hist_filter)
    ctrl._sel         = set()
    ctrl._chk         = {}
    ctrl._all_btn     = None
    ctrl._del_btn     = None
    ctrl._merge_btn   = None
    ctrl._load_btn    = None
    ctrl._append_btn  = None
    ctrl._replace_btn = None
    ctrl._scroll      = None
    ctrl._tab_btns    = {}
    ctrl._tab_labels  = {}   # mode → display label (can't set attrs on NSButton)
    ctrl._pw          = pw
    ctrl._CHK_W       = CHK_W
    ctrl._CHK_R       = CHK_R
    ctrl._on_delete   = _on_history_delete_cb
    ctrl._on_merge    = _on_history_merge_cb
    _hist_ctrl        = ctrl

    # ── Header: three filter tabs + select-all checkbox ──────────────────────
    hdr_y   = ph - HDR_H
    TAB_GAP  = 6
    MAG_OFF  = 30   # left offset for magnet icon
    n_tabs   = 3
    tab_area_w = pw - CHK_W - CHK_R - 12 - MAG_OFF
    tab_w      = (tab_area_w - TAB_GAP * (n_tabs - 1)) // n_tabs
    tab_h      = 20
    tab_y      = hdr_y + (HDR_H - tab_h) // 2

    tab_specs = [
        ("mixed",    "hist_mixed"),
        ("sessions", "hist_sessions"),
        ("blocks",   "hist_blocks"),
    ]
    for ti, (mode, key) in enumerate(tab_specs):
        lbl = _T(key)
        col = C_CYAN if mode == _hist_filter else C_GREEN_DIM
        tx  = MAG_OFF + ti * (tab_w + TAB_GAP)
        tb  = _mkbtn(lbl, color=col, size=9)
        tb.setFrame_(AppKit.NSMakeRect(tx, tab_y, tab_w, tab_h))
        tb.setRepresentedObject_(mode)
        tb.setTarget_(ctrl)
        tb.setAction_(_HistCtrl.histFilter_)
        cv.addSubview_(tb)
        ctrl._tab_btns[mode]  = tb
        ctrl._tab_labels[mode] = lbl

    cv.addSubview_(_sep_line(0, hdr_y - 1, pw, pin="top"))

    all_btn = _HoverBtn.alloc().init()
    all_btn.setBordered_(False)
    all_btn._chk_lbl    = "[ ]"
    all_btn._chk_sz     = 9
    all_btn._normal_col = C_GREEN_DIM
    all_btn.setAttributedTitle_(_atitle("[ ]", size=9, color=C_GREEN_DIM))
    all_btn.setFrame_(AppKit.NSMakeRect(pw - CHK_W - CHK_R, hdr_y + 5, CHK_W, 22))
    all_btn.setTag_(-1)
    all_btn.setTarget_(ctrl)
    all_btn.setAction_(_HistCtrl.toggle_)
    all_btn.setHidden_(len(ctrl._items) == 0)
    cv.addSubview_(all_btn)
    ctrl._all_btn = all_btn

    _mkmagnet_btn("hist", cv, 6, hdr_y + (HDR_H - 22) // 2, 22, 22)

    # ── Scrollable list ───────────────────────────────────────────────────────
    scroll_y = BOT_PAD + FOOT_H
    scroll_h = ph - HDR_H - FOOT_H - BOT_PAD

    scroll = AppKit.NSScrollView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, scroll_y, pw, scroll_h))
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(AppKit.NSNoBorder)
    scroll.setDrawsBackground_(False)
    scroll.setBackgroundColor_(AppKit.NSColor.clearColor())
    scroll.setVerticalScroller_(_ThinGreenScroller.alloc().init())
    scroll.setScrollerStyle_(getattr(AppKit, 'NSScrollerStyleOverlay', 1))
    ctrl._scroll = scroll

    docview = _build_hist_docview(ctrl, pw, scroll_h, CHK_W, CHK_R)
    scroll.setDocumentView_(docview)
    cv.addSubview_(scroll)

    # ── Footer: delete / merge / append / replace ────────────────────────────
    cv.addSubview_(_sep_line(0, BOT_PAD + FOOT_H - 1, pw, pin="bottom"))

    btn_w  = 80
    f_gap  = 5
    bx0    = (pw - btn_w * 4 - f_gap * 3) // 2

    del_btn = _mkbtn(_T("btn_del"), color=C_REC, size=9)
    del_btn.setFrame_(AppKit.NSMakeRect(bx0, BOT_PAD + 4, btn_w, 22))
    del_btn.setHidden_(True)
    del_btn.setTarget_(ctrl)
    del_btn.setAction_(_HistCtrl.delete_)
    cv.addSubview_(del_btn)
    ctrl._del_btn = del_btn

    merge_btn = _mkbtn(_T("btn_merge"), color=C_CYAN, size=9)
    merge_btn.setFrame_(AppKit.NSMakeRect(bx0 + btn_w + f_gap, BOT_PAD + 4, btn_w, 22))
    merge_btn.setHidden_(True)
    merge_btn.setTarget_(ctrl)
    merge_btn.setAction_(_HistCtrl.histMerge_)
    cv.addSubview_(merge_btn)
    ctrl._merge_btn = merge_btn

    append_btn = _mkbtn(_T("btn_append"), color=C_GREEN, size=9)
    append_btn.setFrame_(AppKit.NSMakeRect(bx0 + (btn_w + f_gap) * 2, BOT_PAD + 4, btn_w, 22))
    append_btn.setHidden_(True)
    append_btn.setTarget_(ctrl)
    append_btn.setAction_(_HistCtrl.histAppend_)
    cv.addSubview_(append_btn)
    ctrl._append_btn = append_btn

    replace_btn = _mkbtn(_T("btn_replace"), color=C_GREEN_BR, size=9)
    replace_btn.setFrame_(AppKit.NSMakeRect(bx0 + (btn_w + f_gap) * 3, BOT_PAD + 4, btn_w, 22))
    replace_btn.setHidden_(True)
    replace_btn.setTarget_(ctrl)
    replace_btn.setAction_(_HistCtrl.histReplace_)
    cv.addSubview_(replace_btn)
    ctrl._replace_btn = replace_btn

    _hist_panel.setFrameOrigin_(panel_origin)
    AppKit.NSApp.activateIgnoringOtherApps_(True)
    _hist_panel.makeKeyAndOrderFront_(None)
    _cfg_saved.setdefault("panels_open", {})["hist"] = True
    _save_settings()


def _sc_model_from_refs() -> str:
    """Read provider + model popups → 'provider:model' or '' for auto."""
    pop_prov  = (_sc_edit_refs or {}).get("pop_provider")
    pop_model = (_sc_edit_refs or {}).get("pop_model")
    if not pop_prov:
        return ""
    pid = pop_prov.titleOfSelectedItem() or ""
    if not pid or pid == "авто":
        return ""
    mname = (pop_model.titleOfSelectedItem() or "") if pop_model else ""
    if not mname or mname == "—":
        return ""
    return f"{pid}:{mname}"


def _reset_sc_model_popups(model_str: str):
    """Reset provider + model popups from a model string like 'anthropic:claude-...'."""
    pop_prov  = (_sc_edit_refs or {}).get("pop_provider")
    pop_model = (_sc_edit_refs or {}).get("pop_model")
    if not pop_prov or not pop_model:
        return
    if ":" in (model_str or ""):
        pid, _, mname = model_str.partition(":")
    else:
        pid, mname = "", ""
    items = [pop_prov.itemTitleAtIndex_(i) for i in range(pop_prov.numberOfItems())]
    if pid in items:
        pop_prov.selectItemWithTitle_(pid)
    else:
        pop_prov.selectItemAtIndex_(0)
        pid = ""
    _populate_model_popup(pop_model, pid, mname)


def _sc_edit_dirty() -> bool:
    """True if editor fields differ from values when editor was opened."""
    o = _sc_edit_refs.get("original")
    if not o or not _sc_edit_refs.get("tf_en"):
        return False
    return (
        _sc_edit_refs["tf_ru"].stringValue().strip() != o["ru"] or
        _sc_edit_refs["tf_en"].stringValue().strip() != o["en"] or
        _sc_edit_refs["tf_es"].stringValue().strip() != o["es"] or
        _sc_model_from_refs() != o["model"] or
        _sc_edit_refs["tv_prompt"].string().strip() != o["prompt"] or
        _sc_edit_refs.get("silent", False) != o.get("silent", False) or
        _sc_edit_refs.get("full_default", False) != o.get("full_default", False)
    )


def _close_editor_now(pending_fn=None):
    """Close scenario editor panel."""
    global _sc_editor_panel, _sc_edit_pending, _editing_scenario
    _editing_scenario = False
    if _sc_editor_panel:
        _sc_editor_panel.orderOut_(None)
        _sc_editor_panel.close()
        _sc_editor_panel = None
    _sc_edit_pending = None
    # Rebuild cfg panel so the highlighted card resets to normal color
    if _cfg_panel and _cfg_panel.isVisible():
        _close_cfg_panel_rebuild()
        _toggle_cfg_panel()
    if pending_fn:
        pending_fn()


def _maybe_close_editor(pending_fn=None):
    """If editor has unsaved changes, show confirm overlay; otherwise close directly."""
    global _sc_edit_pending
    if not _editing_scenario or not _sc_editor_panel or not _sc_editor_panel.isVisible():
        if pending_fn:
            pending_fn()
        return
    if _sc_edit_dirty():
        _sc_edit_pending = pending_fn
        _show_sc_confirm()
    else:
        _close_editor_now(pending_fn)


def _show_sc_confirm():
    """Overlay centered in editor panel: save / discard / cancel."""
    if not _sc_editor_panel:
        return
    cv  = _sc_editor_panel.contentView()
    pw  = int(_sc_editor_panel.frame().size.width)
    ph  = int(_sc_editor_panel.frame().size.height)

    overlay = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, pw, ph))
    overlay.setWantsLayer_(True)
    overlay.layer().setBackgroundColor_(_rgba(*C_BG, 0.92).CGColor())

    DW = min(pw - 24, 220)
    DH = 60
    dx = (pw - DW) // 2
    dy = (ph - DH) // 2

    q = _mklabel(_T("edit_confirm"), size=10, color=C_TEXT)
    q.setAlignment_(AppKit.NSTextAlignmentCenter)
    q.setFrame_(AppKit.NSMakeRect(dx, dy + DH - 22, DW, 16))
    overlay.addSubview_(q)

    BW  = (DW - 20) // 3
    GAP = (DW - BW * 3) // 2
    specs = [
        (_T("btn_save"),    C_GREEN,     BtnTarget.cfgScSave_),
        (_T("btn_discard"), C_REC,       BtnTarget.cfgScDiscard_),
        (_T("btn_cancel"),  C_GREEN_DIM, BtnTarget.cfgScCancelConfirm_),
    ]
    for j, (txt, col, act) in enumerate(specs):
        b = _mkbtn(txt, color=col, size=9)
        b.setFrame_(AppKit.NSMakeRect(dx + j * (BW + GAP), dy + 6, BW, 22))
        b.setTarget_(_btn_t)
        b.setAction_(act)
        overlay.addSubview_(b)

    cv.addSubview_(overlay)


def _show_sc_delete_confirm(sc_idx: int):
    """Overlay in editor panel asking to confirm deletion of a custom scenario."""
    if not _sc_editor_panel:
        return
    cv  = _sc_editor_panel.contentView()
    pw  = int(_sc_editor_panel.frame().size.width)
    ph  = int(_sc_editor_panel.frame().size.height)

    overlay = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, pw, ph))
    overlay.setWantsLayer_(True)
    overlay.layer().setBackgroundColor_(_rgba(*C_BG, 0.92).CGColor())

    DW = min(pw - 24, 200)
    DH = 56
    dx = (pw - DW) // 2
    dy = (ph - DH) // 2

    q = _mklabel(_T("delete_confirm"), size=10, color=C_TEXT)
    q.setAlignment_(AppKit.NSTextAlignmentCenter)
    q.setFrame_(AppKit.NSMakeRect(dx, dy + DH - 20, DW, 14))
    overlay.addSubview_(q)

    BW  = (DW - 8) // 2
    for j, (txt, col, act) in enumerate([
        (_T("btn_sc_delete"), C_REC,       BtnTarget.cfgScDeleteConfirmYes_),
        (_T("btn_cancel"),    C_GREEN_DIM, BtnTarget.cfgScDeleteNo_),
    ]):
        b = _mkbtn(txt, color=col, size=9)
        b.setFrame_(AppKit.NSMakeRect(dx + j * (BW + 8), dy + 6, BW, 22))
        b.setTag_(sc_idx)
        b.setTarget_(_btn_t)
        b.setAction_(act)
        overlay.addSubview_(b)

    cv.addSubview_(overlay)


def _do_delete_scenario(sc_idx: int):
    """Delete a custom scenario, close editor, restore main window, refresh cfg panel."""
    global _sc_editor_panel, _editing_scenario, _cfg_panel
    scenarios = list(_st.get("scenarios", []))
    if 0 <= sc_idx < len(scenarios):
        scenarios.pop(sc_idx)
    _st["scenarios"] = scenarios
    save_scenarios(scenarios)
    _start_sc_avail_check()

    _editing_scenario = False
    if _sc_editor_panel:
        _sc_editor_panel.orderOut_(None)
        _sc_editor_panel.close()
        _sc_editor_panel = None

    if _win:
        _relayout_buttons(W if not _expanded else W_EXP)

    _close_cfg_panel_rebuild()
    _toggle_cfg_panel()


def _sc_label_for(sc: dict, lang: str) -> str:
    """Return scenario label for a given lang, max 6 chars. EN is the fallback."""
    label = sc.get("label", {})
    if isinstance(label, dict):
        txt = label.get(lang) or label.get("en") or label.get("ru") or label.get("es") or "?"
    else:
        txt = str(label)
    return txt[:6]


def _update_sc_cfg_colors():
    """Sync cfg panel scenario button colors with the currently open editor (if any)."""
    if not _cfg_panel or not _cfg_panel.isVisible():
        return
    editing_idx = _sc_edit_refs.get("sc_idx") if _editing_scenario else None
    scenarios   = _st.get("scenarios", [])
    lang        = _st.get("lang", "ru")
    for sc_idx, btn in _sc_cfg_buttons.items():
        if sc_idx >= len(scenarios):
            continue
        sc    = scenarios[sc_idx]
        label = _sc_label_for(sc, lang)
        color = C_CYAN if sc_idx == editing_idx else C_GREEN
        if sc.get("silent"):
            ps = AppKit.NSMutableParagraphStyle.alloc().init()
            ps.setAlignment_(AppKit.NSTextAlignmentCenter)
            mstr = AppKit.NSMutableAttributedString.alloc().init()
            if sc_idx == editing_idx:
                # Active editor: all parts cyan
                a = {AppKit.NSFontAttributeName: _mono(9),
                     AppKit.NSForegroundColorAttributeName: C_CYAN,
                     AppKit.NSParagraphStyleAttributeName: ps}
                for part in ("·", label, "·"):
                    mstr.appendAttributedString_(
                        AppKit.NSAttributedString.alloc().initWithString_attributes_(part, a))
            else:
                # Inactive: dots cyan, text green (original style)
                dot_a = {AppKit.NSFontAttributeName: _mono(9),
                         AppKit.NSForegroundColorAttributeName: C_CYAN,
                         AppKit.NSParagraphStyleAttributeName: ps}
                txt_a = {AppKit.NSFontAttributeName: _mono(9),
                         AppKit.NSForegroundColorAttributeName: C_GREEN,
                         AppKit.NSParagraphStyleAttributeName: ps}
                mstr.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_("·", dot_a))
                mstr.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(label, txt_a))
                mstr.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_("·", dot_a))
            btn.setAttributedTitle_(mstr)
        else:
            btn.setAttributedTitle_(_atitle(label, size=9, color=color))


def _show_sc_editor(sc_idx):
    """Open scenario editor covering the main window."""
    global _sc_editor_panel, _sc_edit_refs, _sc_edit_pending, _editing_scenario
    _sc_edit_pending = None
    try:
        return _show_sc_editor_impl(sc_idx)
    except Exception as _e:
        import traceback
        traceback.print_exc()
        with open("/tmp/hush_sc_editor.log", "a") as _f:
            traceback.print_exc(file=_f)


def _show_sc_editor_impl(sc_idx):
    global _sc_editor_panel, _sc_edit_refs, _editing_scenario

    if _sc_editor_panel:
        _sc_editor_panel.orderOut_(None)
        _sc_editor_panel.close()
        _sc_editor_panel = None

    scenarios = _st.get("scenarios", [])
    sc = scenarios[sc_idx] if sc_idx is not None and sc_idx < len(scenarios) else {}

    # Fixed size — same as all other auxiliary panels
    mf      = _win.frame()
    EDIT_W  = W
    EDIT_H  = H_PANEL
    MARGIN  = 12
    LABEL_H = 13
    TF_H    = 22
    GAP     = 3
    BTN_H   = 22
    BTN_W   = 72     # wide enough for Russian "[Сохранить]"

    # Use _EditorPanel so text fields can receive focus/keyboard input
    panel = _EditorPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        AppKit.NSMakeRect(0, 0, EDIT_W, EDIT_H),
        AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered, False,
    )
    panel.setOpaque_(False)
    panel.setBackgroundColor_(AppKit.NSColor.clearColor())
    panel.setLevel_(AppKit.NSFloatingWindowLevel + 2)
    panel.setHasShadow_(True)
    panel.setHidesOnDeactivate_(False)
    panel.setAppearance_(AppKit.NSAppearance.appearanceNamed_(
        AppKit.NSAppearanceNameDarkAqua))
    _bg = TerminalView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, EDIT_W, EDIT_H))
    panel.setContentView_(_bg)
    cv = _bg

    y = EDIT_H - 8

    # Header: 🧲 + title + right-side action button
    MAG_END = 30
    is_default_sc = sc_idx is not None and sc_idx < len(DEFAULT_SCENARIOS)
    is_custom_sc  = sc_idx is not None and sc_idx >= len(DEFAULT_SCENARIOS)
    RIGHT_BTN_W   = 56 if is_custom_sc else 34
    has_right_btn = is_default_sc or is_custom_sc
    _mkmagnet_btn("editor", cv, 6, y - LABEL_H - 2, 22, LABEL_H + 4)
    hdr_w = EDIT_W - MAG_END - MARGIN - (RIGHT_BTN_W + 4 if has_right_btn else 0)
    hdr = _mklabel("НАСТРОЙКИ СЦЕНАРИЯ", size=10, color=C_IDLE)
    hdr.setFrame_(AppKit.NSMakeRect(MAG_END, y - LABEL_H, hdr_w, LABEL_H))
    cv.addSubview_(hdr)
    if is_default_sc:
        btn_rst = _mkbtn("[↺]", color=C_GREEN_DIM, size=9)
        btn_rst.setFrame_(AppKit.NSMakeRect(EDIT_W - MARGIN - 34, y - LABEL_H, 34, LABEL_H))
        btn_rst.setTag_(sc_idx)
        btn_rst.setTarget_(_btn_t)
        btn_rst.setAction_(BtnTarget.cfgScResetOne_)
        cv.addSubview_(btn_rst)
    elif is_custom_sc:
        btn_del = _mkbtn(_T("btn_sc_delete"), color=C_REC, size=9)
        btn_del.setFrame_(AppKit.NSMakeRect(EDIT_W - MARGIN - RIGHT_BTN_W, y - LABEL_H, RIGHT_BTN_W, LABEL_H))
        btn_del.setTag_(sc_idx)
        btn_del.setTarget_(_btn_t)
        btn_del.setAction_(BtnTarget.cfgScDelete_)
        cv.addSubview_(btn_del)
    y -= LABEL_H + 6

    # Full-width fields with descriptive placeholders
    label_val = sc.get("label", {})
    if not isinstance(label_val, dict):
        label_val = {"ru": str(label_val), "en": "", "es": ""}

    FW = EDIT_W - MARGIN * 2

    def _make_tf(ph, value):
        tf = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(MARGIN, y - TF_H, FW, TF_H))
        _style_tf(tf, ph)
        tf.setStringValue_(value)
        cv.addSubview_(tf)
        return tf

    tf_ru    = _make_tf("RU — название кнопки, до 6 символов",   label_val.get("ru", ""))
    y       -= TF_H + GAP
    tf_en    = _make_tf("EN — название кнопки, до 6 символов *",  label_val.get("en", ""))
    y       -= TF_H + GAP
    tf_es    = _make_tf("ES — nombre del botón, hasta 6 símbolos", label_val.get("es", ""))
    y       -= TF_H + GAP
    # ── Cascading provider → model selector ──────────────────────────────────
    cur_model_str = sc.get("model", "") or ""
    if ":" in cur_model_str:
        cur_pid, _, cur_mname = cur_model_str.partition(":")
    else:
        cur_pid, cur_mname = "", ""

    HALF_W = (FW - 6) // 2

    # provider label + model label
    lbl_p = _mklabel("провайдер", size=9, color=C_IDLE)
    lbl_p.setFrame_(AppKit.NSMakeRect(MARGIN, y - LABEL_H, HALF_W, LABEL_H))
    cv.addSubview_(lbl_p)
    lbl_m = _mklabel("модель", size=9, color=C_IDLE)
    lbl_m.setFrame_(AppKit.NSMakeRect(MARGIN + HALF_W + 6, y - LABEL_H, HALF_W, LABEL_H))
    cv.addSubview_(lbl_m)
    y -= LABEL_H + 2

    # provider popup
    prov_items = ["авто"] + _pc.available_providers()
    pop_prov = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
        AppKit.NSMakeRect(MARGIN, y - TF_H, HALF_W, TF_H), False)
    pop_prov.addItemsWithTitles_(prov_items)
    pop_prov.setFont_(_mono(9))
    if cur_pid in prov_items:
        pop_prov.selectItemWithTitle_(cur_pid)
    else:
        pop_prov.selectItemAtIndex_(0)
    pop_prov.setTarget_(_btn_t)
    pop_prov.setAction_(BtnTarget.scProviderChanged_)
    cv.addSubview_(pop_prov)

    # model popup
    pop_model = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
        AppKit.NSMakeRect(MARGIN + HALF_W + 6, y - TF_H, HALF_W, TF_H), False)
    pop_model.setFont_(_mono(9))
    _populate_model_popup(pop_model, cur_pid, cur_mname)
    cv.addSubview_(pop_model)

    y -= TF_H + GAP

    # Silent mode checkbox row — separated by thin lines, looks like a setting option
    SIL_H   = 28
    SIL_SEP = 6
    y -= SIL_SEP
    cv.addSubview_(_sep_line(MARGIN, y, FW, pin="top"))
    y -= 1
    is_silent  = bool(sc.get("silent", False))
    chk_prefix = "[✓] " if is_silent else "[ ] "
    chk_color  = C_CYAN if is_silent else C_GREEN_DIM
    sil_btn    = _mkbtn(chk_prefix + _T("sc_silent"), color=chk_color,
                        size=10, align=AppKit.NSTextAlignmentLeft)
    sil_btn.setFrame_(AppKit.NSMakeRect(MARGIN, y - SIL_H, FW, SIL_H))
    sil_btn.setTarget_(_btn_t)
    sil_btn.setAction_(BtnTarget.cfgScToggleSilent_)
    cv.addSubview_(sil_btn)
    y -= SIL_H
    # Full mode default checkbox row
    is_full_default  = bool(sc.get("full_default", False))
    fd_prefix = "[✓] " if is_full_default else "[ ] "
    fd_color  = C_GREEN_BR if is_full_default else C_GREEN_DIM
    fd_btn    = _mkbtn(fd_prefix + _T("sc_full_default"), color=fd_color,
                       size=10, align=AppKit.NSTextAlignmentLeft)
    fd_btn.setFrame_(AppKit.NSMakeRect(MARGIN, y - SIL_H, FW, SIL_H))
    fd_btn.setTarget_(_btn_t)
    fd_btn.setAction_(BtnTarget.cfgScToggleFullDefault_)
    cv.addSubview_(fd_btn)
    y -= SIL_H
    cv.addSubview_(_sep_line(MARGIN, y, FW, pin="top"))
    y -= 1 + SIL_SEP

    # ── Footer: Cancel + Save buttons ─────────────────────────────────────────
    FOOT_H  = MARGIN + BTN_H + 6 + 1   # margin + btn + gap + sep
    cv.addSubview_(_sep_line(MARGIN, MARGIN + BTN_H + 6, FW, pin="top"))
    btn_cancel = _mkbtn("[Отмена]", color=C_GREEN_DIM, size=10)
    btn_cancel.setFrame_(AppKit.NSMakeRect(MARGIN, MARGIN, BTN_W, BTN_H))
    btn_cancel.setTarget_(_btn_t)
    btn_cancel.setAction_(BtnTarget.cfgScCancel_)
    cv.addSubview_(btn_cancel)
    btn_save = _mkbtn("[Сохранить]", color=C_GREEN_BR, size=10)
    btn_save.setFrame_(AppKit.NSMakeRect(EDIT_W - MARGIN - BTN_W, MARGIN, BTN_W, BTN_H))
    btn_save.setTarget_(_btn_t)
    btn_save.setAction_(BtnTarget.cfgScSave_)
    cv.addSubview_(btn_save)

    # Prompt textarea — expands to fill remaining space above footer
    BOT_PAD       = FOOT_H + 4
    prompt_area_h = max(y - BOT_PAD, 40)
    outer_frame   = AppKit.NSMakeRect(MARGIN, BOT_PAD, FW, prompt_area_h)

    prompt_wrapper = AppKit.NSView.alloc().initWithFrame_(outer_frame)
    prompt_wrapper.setWantsLayer_(True)
    prompt_wrapper.layer().setBackgroundColor_(_rgba(*C_BG).CGColor())
    prompt_wrapper.layer().setBorderColor_(C_GREEN_BORD.CGColor())
    prompt_wrapper.layer().setBorderWidth_(0.5)
    prompt_wrapper.layer().setCornerRadius_(2.0)
    cv.addSubview_(prompt_wrapper)

    PROMPT_PAD = 6
    B = 1   # border inset so scroll view doesn't cover the border line
    scroll = AppKit.NSScrollView.alloc().initWithFrame_(
        AppKit.NSMakeRect(B, B, FW - B * 2, prompt_area_h - B * 2))
    scroll.setBorderType_(AppKit.NSNoBorder)
    scroll.setHasVerticalScroller_(True)
    scroll.setHasHorizontalScroller_(False)
    scroll.setAutohidesScrollers_(True)
    scroll.setDrawsBackground_(False)
    scroll.setVerticalScroller_(_ThinGreenScroller.alloc().init())
    scroll.setScrollerStyle_(getattr(AppKit, 'NSScrollerStyleOverlay', 1))
    prompt_wrapper.addSubview_(scroll)

    cw = scroll.contentSize().width
    tv_prompt = _PlaceholderTextView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, cw, prompt_area_h))
    tv_prompt.setPlaceholder_("prompt — системная инструкция для Claude...")
    tv_prompt.setFont_(_mono(9))
    tv_prompt.setTextColor_(C_TEXT)
    tv_prompt.setBackgroundColor_(_rgba(*C_BG))
    tv_prompt.setTextContainerInset_(AppKit.NSMakeSize(PROMPT_PAD, PROMPT_PAD))
    tv_prompt.setSelectedTextAttributes_({
        AppKit.NSBackgroundColorAttributeName: _rgba(0.0, 0.45, 0.18, 0.55),
        AppKit.NSForegroundColorAttributeName: C_TEXT,
    })
    tv_prompt.setVerticallyResizable_(True)
    tv_prompt.setHorizontallyResizable_(False)
    tv_prompt.setMinSize_(AppKit.NSMakeSize(0, prompt_area_h))
    tv_prompt.setMaxSize_(AppKit.NSMakeSize(cw, 1_000_000))
    tv_prompt.textContainer().setContainerSize_(AppKit.NSMakeSize(cw - PROMPT_PAD * 2, 1_000_000))
    tv_prompt.textContainer().setWidthTracksTextView_(True)
    tv_prompt.setString_(sc.get("prompt", ""))

    scroll.setDocumentView_(tv_prompt)

    _sc_edit_refs = {
        "tf_ru":        tf_ru,
        "tf_en":        tf_en,
        "tf_es":        tf_es,
        "pop_provider": pop_prov,
        "pop_model":    pop_model,
        "sil_btn":      sil_btn,
        "silent":       is_silent,   # current toggle state (Python bool, toggled in action)
        "fd_btn":       fd_btn,
        "full_default": is_full_default,
        "tv_prompt":    tv_prompt,
        "sc_idx":       sc_idx,
        "original":     {
            "ru":           label_val.get("ru", ""),
            "en":           label_val.get("en", ""),
            "es":           label_val.get("es", ""),
            "model":        sc.get("model", "") or "",
            "prompt":       sc.get("prompt", ""),
            "silent":       is_silent,
            "full_default": is_full_default,
        },
    }
    _sc_editor_panel = panel
    _sc_editor_panel._panel_key = "editor"
    _editing_scenario = True

    # Position as floating auxiliary panel (magnet-aware), main window stays visible
    if _magnet_on.get("editor", True) and "editor" in _magnet_offset:
        dx, dy = _magnet_offset["editor"]
        ex, ey = int(mf.origin.x + dx), int(mf.origin.y + dy)
    elif "editor" in _magnet_free_pos and not _magnet_on.get("editor", True):
        ex, ey = int(_magnet_free_pos["editor"][0]), int(_magnet_free_pos["editor"][1])
    else:
        ex = int(mf.origin.x + mf.size.width + 6)
        ey = int(mf.origin.y)
        if _magnet_on.get("editor", True):
            _magnet_offset["editor"] = (mf.size.width + 6, 0)
    panel.setFrameOrigin_(AppKit.NSMakePoint(ex, ey))
    AppKit.NSApp.activateIgnoringOtherApps_(True)
    panel.makeKeyAndOrderFront_(None)
    panel.makeFirstResponder_(tf_en)

    # Sync cfg panel button colors — highlight whichever card is now active
    _update_sc_cfg_colors()


def _sc_editor_save():
    global _sc_editor_panel, _sc_edit_pending, _editing_scenario, _cfg_panel
    refs   = _sc_edit_refs
    lbl_ru = refs["tf_ru"].stringValue().strip().upper()[:6]
    lbl_en = refs["tf_en"].stringValue().strip().upper()[:6]
    lbl_es = refs["tf_es"].stringValue().strip().upper()[:6]
    model  = _sc_model_from_refs() or None
    prompt = refs["tv_prompt"].string()
    sc_idx = refs.get("sc_idx")

    if not lbl_en:
        return   # EN is required

    is_silent = refs.get("silent", False)
    is_full_default = refs.get("full_default", False)

    label = {
        "en": lbl_en,
        "ru": lbl_ru or lbl_en,
        "es": lbl_es or lbl_en,
    }
    sc = {"name": label.get("ru", lbl_en), "label": label, "model": model, "prompt": prompt}
    if is_silent:
        sc["silent"] = True
    if is_full_default:
        sc["full_default"] = True
    scenarios = list(_st.get("scenarios", []))
    if sc_idx is None:
        # New scenario: clear silent/full_default flags from all existing ones first
        if is_silent:
            for s in scenarios:
                s.pop("silent", None)
        if is_full_default:
            for s in scenarios:
                s.pop("full_default", None)
        scenarios.append(sc)
        new_idx = len(scenarios) - 1
    else:
        if 0 <= sc_idx < len(scenarios):
            # If marking silent, clear it from all others
            if is_silent:
                for i, s in enumerate(scenarios):
                    if i != sc_idx:
                        s.pop("silent", None)
            if is_full_default:
                for i, s in enumerate(scenarios):
                    if i != sc_idx:
                        s.pop("full_default", None)
            scenarios[sc_idx] = sc
    _st["scenarios"] = scenarios
    save_scenarios(scenarios)
    _start_sc_avail_check()

    pending_fn = _sc_edit_pending
    _sc_edit_pending = None
    _editing_scenario = False

    if _sc_editor_panel:
        _sc_editor_panel.orderOut_(None)
        _sc_editor_panel.close()
        _sc_editor_panel = None

    if _win:
        _relayout_buttons(W if not _expanded else W_EXP)

    if pending_fn:
        pending_fn()
    else:
        # Default: refresh config panel to reflect updated list
        _close_cfg_panel_rebuild()
        _toggle_cfg_panel()


def _populate_model_popup(popup, provider_id, selected=""):
    """Fill the model NSPopUpButton for the given provider_id."""
    popup.removeAllItems()
    models = _pc.models_for_provider(provider_id) if provider_id else []
    if models:
        popup.addItemsWithTitles_(models)
        popup.setEnabled_(True)
        if selected in models:
            popup.selectItemWithTitle_(selected)
        else:
            popup.selectItemAtIndex_(0)
    else:
        popup.addItemsWithTitles_(["—"])
        popup.setEnabled_(False)


def _toggle_cfg_panel():
    global _cfg_panel, _pre_cfg_win_y, _sc_cfg_buttons
    _sc_cfg_buttons = {}
    if _cfg_panel and _cfg_panel.isVisible():
        _close_cfg_panel()
        return

    pw = int(_win.frame().size.width)

    # ── Layout constants ─────────────────────────────────────────────────────────
    MARGIN    = 10        # panel left/right margin
    MARGIN_T  = 26        # top margin (room for [ⓘ] button)
    BOX_H     = 60        # top row: opacity | font | lang
    BOX_G     = 6         # horizontal gap between side-by-side boxes
    HK_TH_H   = 72        # middle row: hotkey (left) | themes 2×4 (right)
    CELL_H    = 22
    CELL_GAP  = 3
    COLS      = 6
    MAX_SC    = COLS * 3 - 1
    QUIT_H    = 46        # bottom quit section
    VGAP      = 8         # uniform vertical gap between every row

    inner_w = pw - 2 * MARGIN

    # Top row: opacity (narrow) | font | lang
    op_w = max(72, inner_w // 5)
    fn_w = max(90, inner_w // 3)
    la_w = inner_w - op_w - fn_w - 2 * BOX_G
    op_x = MARGIN
    fn_x = MARGIN + op_w + BOX_G
    la_x = fn_x + fn_w + BOX_G

    # Middle row: hotkey (left half) | themes 2×4 (right half)
    hk_w  = inner_w // 2 - BOX_G // 2
    th_w  = inner_w - hk_w - BOX_G
    hk_x  = MARGIN
    th_x  = MARGIN + hk_w + BOX_G

    scenarios = _st.get("scenarios", [])
    n_sc      = len(scenarios)
    n_cells   = min(n_sc + 1, MAX_SC + 1)
    n_rows    = max(1, (n_cells + COLS - 1) // COLS)

    SCEN_INNER = n_rows * (CELL_H + CELL_GAP) + 8
    SC_BOX_H   = 20 + SCEN_INNER

    # Bottom-up Y positions
    sc_box_y  = QUIT_H + VGAP
    hk_th_y   = sc_box_y + SC_BOX_H + VGAP
    box_y     = hk_th_y + HK_TH_H + VGAP
    ph        = max(box_y + BOX_H + MARGIN_T, H_PANEL)

    _close_cfg_panel_rebuild()
    _cfg_panel = _make_drop_panel(pw, ph)
    _cfg_panel._panel_key = "cfg"
    cv = _cfg_panel.contentView()

    # ── Fieldset box helper ───────────────────────────────────────────────────────
    def _fieldset(x, y, w, h, title):
        """Create a labeled fieldset (NSBox, NSAtTop title cuts through top border).
        Returns (contentView, content_w, content_h)."""
        box = AppKit.NSBox.alloc().initWithFrame_(AppKit.NSMakeRect(x, y, w, h))
        box.setBoxType_(AppKit.NSBoxCustom)
        box.setTitle_(f" {title} ")
        box.setTitlePosition_(AppKit.NSAtTop)
        box.setTitleFont_(_mono(7.5))
        box.setBorderColor_(C_GREEN_BORD)
        box.setFillColor_(_rgba(*C_BG, 0.35))
        box.setCornerRadius_(4)
        box.setContentViewMargins_(AppKit.NSMakeSize(5, 4))
        cv.addSubview_(box)
        box.titleCell().setTextColor_(C_GREEN_DIM)
        bcv = box.contentView()
        return bcv, int(bcv.frame().size.width), int(bcv.frame().size.height)

    # ── [ⓘ] INFO + [КЛЮЧИ] BUTTONS — top-right corner of panel ──────────────────
    INFO_SZ  = 18
    KEYS_W   = 46
    btn_info = _mkbtn("ⓘ", color=C_GREEN_DIM, size=12)
    btn_info.setFrame_(AppKit.NSMakeRect(pw - INFO_SZ - 8, ph - INFO_SZ - 4, INFO_SZ, INFO_SZ))
    btn_info.setTarget_(_btn_t)
    btn_info.setAction_(BtnTarget.cfgInfo_)
    btn_info.setToolTip_("О приложении")
    cv.addSubview_(btn_info)

    btn_keys = _mkbtn("[КЛЮЧИ]", color=C_GREEN_DIM, size=9)
    btn_keys.setFrame_(AppKit.NSMakeRect(pw - INFO_SZ - KEYS_W - 14, ph - INFO_SZ - 4, KEYS_W, INFO_SZ))
    btn_keys.setTarget_(_btn_t)
    btn_keys.setAction_(BtnTarget.cfgProviders_)
    btn_keys.setToolTip_("Настройка провайдеров и API ключей")
    cv.addSubview_(btn_keys)

    _mkmagnet_btn("cfg", cv, 6, ph - INFO_SZ - 4, 22, INFO_SZ)
    cfg_hdr = _mklabel("НАСТРОЙКИ", size=10, color=C_IDLE)
    cfg_hdr.setFrame_(AppKit.NSMakeRect(32, ph - INFO_SZ - 4, pw - 32 - INFO_SZ - KEYS_W - 20, INFO_SZ))
    cv.addSubview_(cfg_hdr)

    # ── OPACITY FIELDSET (narrow) ─────────────────────────────────────────────────
    op_cv, op_cw, op_ch = _fieldset(op_x, box_y, op_w, BOX_H, _T("cfg_opacity"))
    sl = TerminalSlider.alloc().initWithFrame_(
        AppKit.NSMakeRect(4, (op_ch - 20) // 2, op_cw - 8, 20))
    sl.setMinValue_(0.40)
    sl.setMaxValue_(1.00)
    sl.setFloatValue_(_st["opacity"])
    sl.setTarget_(_btn_t)
    sl.setAction_(BtnTarget.cfgOpacity_)
    op_cv.addSubview_(sl)

    # ── FONT FIELDSET ─────────────────────────────────────────────────────────────
    fn_cv, fn_cw, fn_ch = _fieldset(fn_x, box_y, fn_w, BOX_H, _T("cfg_font"))
    FBTN_W = max(28, (fn_cw - 8) // 2)
    fn_start_x = (fn_cw - 2 * FBTN_W - 4) // 2
    fn_btn_y   = (fn_ch - 22) // 2
    for j, (lbl_txt, act) in enumerate(
            [("[A-]", BtnTarget.cfgFontDec_), ("[A+]", BtnTarget.cfgFontInc_)]):
        b = _mkbtn(lbl_txt, color=C_GREEN, size=11)
        b.setFrame_(AppKit.NSMakeRect(fn_start_x + j * (FBTN_W + 4), fn_btn_y, FBTN_W, 22))
        b.setTarget_(_btn_t)
        b.setAction_(act)
        fn_cv.addSubview_(b)

    # ── LANGUAGE FIELDSET ─────────────────────────────────────────────────────────
    la_cv, la_cw, la_ch = _fieldset(la_x, box_y, la_w, BOX_H, _T("cfg_lang"))
    cur_lang = _st.get("lang", "ru")
    LANG_H   = 18
    LANG_G   = 2
    lang_total_h = 3 * LANG_H + 2 * LANG_G
    lang_start_y = (la_ch - lang_total_h) // 2
    for i, lbl_txt in enumerate(["[RU]", "[EN]", "[ES]"]):
        active = (LANGS[i] == cur_lang)
        color  = C_GREEN_BR if active else C_GREEN_DIM
        lb = _mkbtn(lbl_txt, color=color, size=9)
        lb.setFrame_(AppKit.NSMakeRect(2, lang_start_y + i * (LANG_H + LANG_G), la_cw - 4, LANG_H))
        lb.setTag_(i)
        lb.setTarget_(_btn_t)
        lb.setAction_(BtnTarget.cfgLang_)
        la_cv.addSubview_(lb)

    # ── HOTKEY FIELDSET (left half of middle row) ─────────────────────────────────
    hk_cv, hk_cw, hk_ch = _fieldset(hk_x, hk_th_y, hk_w, HK_TH_H, _T("cfg_hotkey"))
    HOT_COPY_OPTIONS = ["ctrl", "cmd", "ctrl+shift", "cmd+shift"]
    HOT_COPY_LABELS  = ["[^]", "[⌘]", "[^⇧]", "[⌘⇧]"]
    cur_hk    = _st.get("hotkey_copy", "ctrl")
    HK_GAP    = 5                                         # gap between buttons
    HK_EDGE   = 4                                         # left/right margin
    HK_BTN_H  = max(26, hk_ch - 10)                      # fill available height
    HK_BTN_W  = max(38, (hk_cw - 2 * HK_EDGE - 3 * HK_GAP) // 4)
    hk_start_x = (hk_cw - (4 * HK_BTN_W + 3 * HK_GAP)) // 2
    hk_btn_y   = (hk_ch - HK_BTN_H) // 2
    for i, (hk_val, hk_lbl) in enumerate(zip(HOT_COPY_OPTIONS, HOT_COPY_LABELS)):
        active = (hk_val == cur_hk)
        color  = C_CYAN if active else C_GREEN_DIM
        sz     = 12 if active else 10
        hb = _mkbtn(hk_lbl, color=color, size=sz)
        hb.setWantsLayer_(True)
        lay = hb.layer()
        lay.setCornerRadius_(4.0)
        if active:
            lay.setBorderWidth_(1.5)
            lay.setBorderColor_(C_CYAN.CGColor())
            lay.setBackgroundColor_(_rgba(0.45, 0.45, 0.45, 0.22).CGColor())
        else:
            lay.setBorderWidth_(0.5)
            lay.setBorderColor_(_rgba(0.50, 0.50, 0.50, 0.28).CGColor())
        hb.setFrame_(AppKit.NSMakeRect(hk_start_x + i * (HK_BTN_W + HK_GAP), hk_btn_y, HK_BTN_W, HK_BTN_H))
        hb.setTag_(i)
        hb.setTarget_(_btn_t)
        hb.setAction_(BtnTarget.cfgHotkeyCopy_)
        hk_cv.addSubview_(hb)

    # ── THEME FIELDSET (right half, 2 rows × 4 squares: светлые / тёмные) ─────────
    th_cv, th_cw, th_ch = _fieldset(th_x, hk_th_y, th_w, HK_TH_H, _T("cfg_theme"))
    cur_theme  = _st.get("theme", "emerald")
    n_light    = _N_LIGHT                      # 4 light themes (top row)
    n_dark     = len(_THEME_META) - n_light    # 4 dark themes (bottom row)
    n_per_row  = max(n_light, n_dark)          # = 4
    SQ_EDGE    = 5    # equal margin: left/right/top/bottom from fieldset edge
    SQ_PAD     = 5    # gap between cards in a row
    SQ_ROW_GAP = 5    # vertical gap between rows
    # Fill available space equally
    sq_w       = max(16, (th_cw - 2 * SQ_EDGE - (n_per_row - 1) * SQ_PAD) // n_per_row)
    sq_h       = max(14, (th_ch - 2 * SQ_EDGE - SQ_ROW_GAP) // 2)
    sq_y_dark  = SQ_EDGE
    sq_y_light = SQ_EDGE + sq_h + SQ_ROW_GAP
    try:
        _CALayer = objc.lookUpClass('CALayer')
    except Exception:
        _CALayer = None

    def _make_swatch(tname, tbg, tcolor, idx, sq_x, sq_y_val):
        active = (tname == cur_theme)
        tb = AppKit.NSButton.alloc().initWithFrame_(
            AppKit.NSMakeRect(sq_x, sq_y_val, sq_w, sq_h))
        tb.setBordered_(False)
        tb.setTitle_("")
        tb.setWantsLayer_(True)
        lay = tb.layer()
        lay.setCornerRadius_(3.0)
        lay.setMasksToBounds_(True)
        if _CALayer:
            top_h = int(sq_h * 0.55)
            bot_h = sq_h - top_h
            vr = min(1.0, tbg[0] * 4 + 0.05)
            vg = min(1.0, tbg[1] * 4 + 0.05)
            vb = min(1.0, tbg[2] * 4 + 0.05)
            bg_l = _CALayer.layer()
            bg_l.setFrame_(AppKit.NSMakeRect(0, bot_h, sq_w, top_h))
            bg_l.setBackgroundColor_(_rgba(vr, vg, vb, 1.0).CGColor())
            lay.addSublayer_(bg_l)
            ac_l = _CALayer.layer()
            ac_l.setFrame_(AppKit.NSMakeRect(0, 0, sq_w, bot_h))
            ac_l.setBackgroundColor_(tcolor.CGColor())
            lay.addSublayer_(ac_l)
        else:
            lay.setBackgroundColor_(tcolor.CGColor())
        if active:
            lay.setBorderWidth_(2.0)
            lay.setBorderColor_(AppKit.NSColor.whiteColor().CGColor())
        else:
            lay.setBorderWidth_(1.0)
            lay.setBorderColor_(_rgba(0.5, 0.5, 0.5, 0.30).CGColor())
        tb.setTag_(idx)
        tb.setTarget_(_btn_t)
        tb.setAction_(BtnTarget.cfgTheme_)
        th_cv.addSubview_(tb)

    # Ряд 1 (вверх): светлые темы — от края SQ_EDGE, равные промежутки
    for i, (tname, tbg, tcolor) in enumerate(_THEME_META[:n_light]):
        _make_swatch(tname, tbg, tcolor, i, SQ_EDGE + i * (sq_w + SQ_PAD), sq_y_light)

    # Ряд 2 (низ): тёмные темы — от края SQ_EDGE, равные промежутки
    for i, (tname, tbg, tcolor) in enumerate(_THEME_META[n_light:]):
        _make_swatch(tname, tbg, tcolor, n_light + i, SQ_EDGE + i * (sq_w + SQ_PAD), sq_y_dark)

    # ── SCENARIOS FIELDSET ────────────────────────────────────────────────────────
    sc_cv, sc_cw, sc_ch = _fieldset(MARGIN, sc_box_y, inner_w, SC_BOX_H, _T("cfg_scenes"))
    cur_lang = _st.get("lang", "ru")

    CELL_W        = (sc_cw - CELL_GAP * (COLS - 1)) // COLS
    total_grid_w  = COLS * CELL_W + CELL_GAP * (COLS - 1)
    grid_left     = (sc_cw - total_grid_w) // 2

    def _cell_rect(idx):
        row = idx // COLS
        col = idx % COLS
        x   = grid_left + col * (CELL_W + CELL_GAP)
        y   = sc_ch - 4 - (row + 1) * CELL_H - row * CELL_GAP
        return AppKit.NSMakeRect(x, y, CELL_W, CELL_H)

    sc_buttons = []
    for i, sc in enumerate(scenarios):
        label = _sc_label_for(sc, cur_lang)
        is_fd  = bool(sc.get("full_default"))
        is_sil = bool(sc.get("silent"))
        if is_fd or is_sil:
            # Build title: [·LABEL·] both, [LABEL] fd only, ·LABEL· sil only
            ps = AppKit.NSMutableParagraphStyle.alloc().init()
            ps.setAlignment_(AppKit.NSTextAlignmentCenter)
            title = AppKit.NSMutableAttributedString.alloc().init()
            mk = {AppKit.NSFontAttributeName:            _mono(9),
                  AppKit.NSForegroundColorAttributeName: C_CYAN,
                  AppKit.NSParagraphStyleAttributeName:  ps}
            tx = {AppKit.NSFontAttributeName:            _mono(9),
                  AppKit.NSForegroundColorAttributeName: C_GREEN if is_sil else C_CYAN,
                  AppKit.NSParagraphStyleAttributeName:  ps}
            parts = []
            if is_fd:  parts.append(("[", mk))
            if is_sil: parts.append(("·", mk))
            parts.append((label, tx))
            if is_sil: parts.append(("·", mk))
            if is_fd:  parts.append(("]", mk))
            for s, a in parts:
                title.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(s, a))
            btn = _mkbtn("", color=C_CYAN, size=9)
            btn.setAttributedTitle_(title)
        else:
            btn = _mkbtn(label, color=C_GREEN, size=9)
        btn.setFrame_(_cell_rect(i))
        btn.setTag_(i)
        btn.setTarget_(_btn_t)
        btn.setAction_(BtnTarget.cfgScEdit_)
        sc_cv.addSubview_(btn)
        _sc_cfg_buttons[i] = btn
        sc_buttons.append((btn, sc.get("model") or "", label,
                           bool(sc.get("silent")), bool(sc.get("full_default"))))

    # Background model availability check
    def _check_models(buttons):
        C_ERR = _rgba(0.9, 0.2, 0.2, 1.0)
        for btn, model_str, lbl, is_silent, is_fd in buttons:
            if model_str and not _model_available(model_str):
                def _paint(b=btn, label=lbl, sil=is_silent, fd=is_fd):
                    ps = AppKit.NSMutableParagraphStyle.alloc().init()
                    ps.setAlignment_(AppKit.NSTextAlignmentCenter)
                    a = {AppKit.NSFontAttributeName: _mono(9),
                         AppKit.NSForegroundColorAttributeName: C_ERR,
                         AppKit.NSParagraphStyleAttributeName: ps}
                    if sil or fd:
                        mstr = AppKit.NSMutableAttributedString.alloc().init()
                        parts = []
                        if fd:  parts.append("[")
                        if sil: parts.append("·")
                        parts.append(label)
                        if sil: parts.append("·")
                        if fd:  parts.append("]")
                        for p in parts:
                            mstr.appendAttributedString_(
                                AppKit.NSAttributedString.alloc().initWithString_attributes_(p, a))
                        b.setAttributedTitle_(mstr)
                    else:
                        b.setAttributedTitle_(
                            AppKit.NSAttributedString.alloc()
                                .initWithString_attributes_(label, a))
                AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_paint)
    threading.Thread(target=_check_models, args=(sc_buttons,), daemon=True).start()

    # [+] at next position
    if n_sc <= MAX_SC:
        btn_add = _mkbtn("[+]", color=C_GREEN_DIM, size=10)
        btn_add.setFrame_(_cell_rect(n_sc))
        btn_add.setTarget_(_btn_t)
        btn_add.setAction_(BtnTarget.cfgScAdd_)
        sc_cv.addSubview_(btn_add)

    # ── SEP between scenarios and quit ───────────────────────────────────────────
    cv.addSubview_(_sep_line(MARGIN, QUIT_H, pw - MARGIN * 2, pin="bottom"))

    # ── QUIT SECTION: [ВЫХОД] [🎯] [✚] ───────────────────────────────────────────
    SB_W = 30   # small button width
    SB_G = 4    # gap between small buttons
    BTN_Y = (QUIT_H - 22) // 2 + 4
    quit_w = pw - MARGIN * 2 - SB_W * 2 - SB_G - 6
    btn_quit = _mkbtn(_T("btn_quit"), color=C_REC, size=10)
    btn_quit.setFrame_(AppKit.NSMakeRect(MARGIN, BTN_Y, quit_w, 22))
    btn_quit.setTarget_(_btn_t)
    btn_quit.setAction_(BtnTarget.cfgQuit_)
    cv.addSubview_(btn_quit)

    # 🎯 — show/hide all panels at saved positions
    btn_rst = _mkbtn("🎯", color=C_GREEN_DIM, size=14)
    btn_rst.setFrame_(AppKit.NSMakeRect(pw - MARGIN - SB_W * 2 - SB_G, BTN_Y, SB_W, 22))
    btn_rst.setTarget_(_btn_t)
    btn_rst.setAction_(BtnTarget.hushResetPanels_)
    btn_rst.setToolTip_("Показать/скрыть все панели")
    cv.addSubview_(btn_rst)

    # ✚ — reset to default cross layout
    btn_cross = _mkbtn("✚", color=C_GREEN_DIM, size=12)
    btn_cross.setFrame_(AppKit.NSMakeRect(pw - MARGIN - SB_W, BTN_Y, SB_W, 22))
    btn_cross.setTarget_(_btn_t)
    btn_cross.setAction_(BtnTarget.hushDefaultCross_)
    btn_cross.setToolTip_("Сброс в крест: настройки↑  история↓  провайдеры←  сценарии→")
    cv.addSubview_(btn_cross)

    _cfg_panel.setAlphaValue_(_st.get("opacity", 0.88))

    # Position cfg panel: magnet-aware.
    CFG_GAP = 4
    mf = _win.frame()
    if _magnet_on.get("cfg", True):
        if "cfg" not in _magnet_offset:
            _magnet_offset["cfg"] = (0, int(mf.size.height) + CFG_GAP)
        dx, dy = _magnet_offset["cfg"]
        px = mf.origin.x + dx
        py = mf.origin.y + dy
        # If panel would go off-screen above, snap it below main window instead
        vis = AppKit.NSScreen.mainScreen().visibleFrame()
        top = py + ph
        if top > vis.origin.y + vis.size.height:
            py = int(mf.origin.y - ph - CFG_GAP)
            _magnet_offset["cfg"] = (dx, -(ph + CFG_GAP))
    else:
        if "cfg" in _magnet_free_pos:
            px, py = _magnet_free_pos["cfg"]
        else:
            px = int(mf.origin.x + mf.size.width - pw)
            py = int(mf.origin.y + mf.size.height + CFG_GAP)

    _cfg_panel.setFrameOrigin_(AppKit.NSMakePoint(px, py))
    AppKit.NSApp.activateIgnoringOtherApps_(True)
    _cfg_panel.makeKeyAndOrderFront_(None)


def _restore_history_item(full_text: str, item_id: str = None):
    """Load single history item into the text view."""
    if _hist_panel:
        _hist_panel.orderOut_(None)
    _load_history_combined(full_text, loaded_id=item_id)


def _restore_session(blocks_text: list, block_hist_ids: list = None, session_id: str = None):
    """Restore a session: recreate each block separately (preserving per-block structure)."""
    if not _doc_view:
        return
    if _hist_panel:
        _hist_panel.orderOut_(None)
    _st["text"]      = ""
    _st["mode"]      = "ready"
    _st["is_md"]     = False
    _st["md_mode"]   = False
    _st["active_sc"] = None
    _remove_all_rich_blocks()
    if _on_history_load_cb:
        _on_history_load_cb(session_id)
    if _tv:
        _tv.setString_("")
    for i, text in enumerate(blocks_text):
        if not text.strip():
            continue
        idx   = len(_rich_blocks)
        block = _make_rich_block(text, idx)
        hist_id = (block_hist_ids[i] if block_hist_ids and i < len(block_hist_ids) else None)
        block._original_text    = text.strip()
        block._original_hist_id = hist_id
        block._hist_id          = hist_id
        _rich_blocks.append(block)
        _doc_view.addSubview_(block)
        if block._inner_tv:
            end = block._inner_tv.textStorage().length()
            block._inner_tv.setSelectedRange_(AppKit.NSMakeRange(end, 0))
    _update_md_indicator()
    _update_format_indicator()
    _relayout_doc_view()
    _show_buttons(True)
    _refresh_scenario_colors()
    _show_target_app_header()
    if _win:
        _win.orderFrontRegardless()
        if _tv:
            _win.makeFirstResponder_(_tv)
            _tv.setSelectedRange_(AppKit.NSMakeRange(0, 0))
    _main(_update_cursor_pos)


def _get_all_text() -> str:
    """Return combined text from all blocks + _tv (used by scenarios and history append)."""
    parts = []
    for b in _rich_blocks:
        if b._inner_tv:
            # Always prefer live _inner_tv content — user may have edited in either mode
            t = str(b._inner_tv.string()).strip() or (b._md_text or "").strip()
        else:
            t = (b._md_text or "").strip()
        if t:
            parts.append(t)
    if _tv:
        tv_txt = str(_tv.string()).strip()
        if tv_txt:
            parts.append(tv_txt)
    return "\n\n".join(parts)


def _load_history_combined(text: str, loaded_id: str = None, keep_active: bool = False):
    """Load text (single or combined) into the main text area.
    loaded_id: UUID of the source history item (None if multiple combined).
    keep_active: if True, don't clear active_sc (used by show_scenario_result).
    """
    _st["text"]    = text
    _st["mode"]    = "ready"
    _st["is_md"]   = _is_markdown(text)
    _st["md_mode"] = False
    _remove_all_rich_blocks()
    if not keep_active:
        _st["active_sc"] = None   # loading from history clears any active filter
    if _on_history_load_cb:
        _on_history_load_cb(loaded_id)   # notify main.py which item was loaded
    _update_md_indicator()
    if _tv:
        display = text.rstrip('\n') + '\n'
        _tv.setString_(display)
        ln = len(display)
        _tv.setSelectedRange_(AppKit.NSMakeRange(ln, 0))
        _tv.scrollRangeToVisible_(AppKit.NSMakeRange(ln, 0))
    _end_processing()
    _show_target_app_header()
    _show_buttons(True)
    _refresh_scenario_colors()
    _win.orderFrontRegardless()
    if _tv:
        _win.makeFirstResponder_(_tv)
    # Convert loaded text to a block immediately
    _finalize_tv_to_block()
    _main(_update_cursor_pos)


# ── Public helpers ────────────────────────────────────────────────────────────

def refresh_hist_panel():
    """Reopen the history panel with fresh data (called after deletion)."""
    if _on_history_cb:
        history = _on_history_cb()
        _main(lambda h=history: _show_hist_panel(h))


def show_history_browser(history):
    """Open overlay in history-browser mode (double-tap hotkey)."""
    def _():
        _st["mode"] = "history_open"
        _show_target_app_header()   # keep showing target app, no status text
        _show_buttons(False)
        # [ИСТ] stays visible in header — no need to re-show
        _win.orderFrontRegardless()
        _show_hist_panel(history)
    _main(_)

# ── Silent mode ───────────────────────────────────────────────────────────────

def _build_silent_header(show_icon: bool):
    """Unified compact panel for recording + recognition states.
    Same layout as processing card header: [ICON] [App name]  [WF/EQ bars].
    Shares constants with _build_processing_card for visual consistency.
    """
    global _silent_win, _silent_wf, _silent_eq_v
    global _silent_app_icon_v, _silent_hover_v, _silent_text_v
    global _silent_saved_cx, _silent_saved_sy

    if _silent_win:
        fr = _silent_win.frame()
        _silent_saved_cx = fr.origin.x + fr.size.width / 2
        _silent_saved_sy = fr.origin.y
        _silent_save_pos(_silent_win)
        _silent_win.orderOut_(None)
        _silent_win.close()
        _silent_win = None

    _silent_app_icon_v = None
    _silent_hover_v    = None
    _silent_text_v     = None

    # ── shared constants (match _build_processing_card exactly) ──────────────
    CARD_W   = 320
    PAD      = 12
    ICON_SZ  = 28
    GAP      = 7
    ANIM_W   = 88
    ANIM_H   = 22
    HEADER_H = ICON_SZ + PAD * 2   # 52
    WIN_SIDE = 4

    win_w = CARD_W + WIN_SIDE * 2   # 328
    win_h = HEADER_H + WIN_SIDE * 2  # 60

    if _silent_saved_cx is not None:
        sx = int(_silent_saved_cx - win_w / 2)
        sy = int(_silent_saved_sy)
    else:
        sx, sy = _silent_load_pos(win_w, win_h)

    panel = _SilentPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        AppKit.NSMakeRect(sx, sy, win_w, win_h),
        AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered, False,
    )
    panel.setOpaque_(False)
    panel.setBackgroundColor_(AppKit.NSColor.clearColor())
    panel.setLevel_(AppKit.NSFloatingWindowLevel + 1)
    panel.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
        AppKit.NSWindowCollectionBehaviorStationary |
        AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
    )
    panel.setHasShadow_(False)
    panel.setHidesOnDeactivate_(False)
    panel.setMovable_(True)
    panel.setMovableByWindowBackground_(True)

    cv = _SilentContentView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, win_w, win_h))
    panel.setContentView_(cv)
    cv.updateTrackingAreas()

    bg = _SilentBgView.alloc().initWithFrame_(
        AppKit.NSMakeRect(WIN_SIDE, WIN_SIDE, CARD_W, HEADER_H))
    cv.addSubview_(bg)

    # Y centre for ANIM_H items inside header
    anim_y = WIN_SIDE + (HEADER_H - ANIM_H) // 2

    # Icon (left)
    if show_icon:
        icon_x = WIN_SIDE + PAD
        icon_y = WIN_SIDE + (HEADER_H - ICON_SZ) // 2
        iv = _AppIconView.alloc().initWithFrame_(
            AppKit.NSMakeRect(icon_x, icon_y, ICON_SZ, ICON_SZ))
        cv.addSubview_(iv)
        iv.applyRoundedMask_(ICON_SZ * 0.22)
        _silent_app_icon_v = iv
        name_x = icon_x + ICON_SZ + GAP
    else:
        name_x = WIN_SIDE + PAD

    # App name label — measure actual text width, then EQ takes the rest
    app_name = ""
    if _silent_target_app:
        try:
            app_name = str(_silent_target_app.localizedName() or "")
        except Exception:
            pass
    font_d   = {AppKit.NSFontAttributeName: _mono(10)}
    raw_name_w = int(AppKit.NSString.stringWithString_(app_name).sizeWithAttributes_(font_d).width) if app_name else 0
    right_edge = WIN_SIDE + CARD_W - PAD   # right boundary of card content
    max_name_w = (right_edge - name_x - GAP) // 2
    name_w     = min(raw_name_w + 4, max_name_w)
    eq_x       = name_x + name_w + GAP
    eq_w       = right_edge - eq_x

    NAME_LBL_H  = 14
    name_lbl_y  = WIN_SIDE + (HEADER_H - NAME_LBL_H) // 2
    name_lbl = _mklabel(app_name, size=10, color=C_TEXT)
    name_lbl.setFrame_(AppKit.NSMakeRect(name_x, name_lbl_y, name_w, NAME_LBL_H))
    cv.addSubview_(name_lbl)

    # Waveform — fills all space right of app name
    wv = _SilentWaveformView.alloc().initWithFrame_(
        AppKit.NSMakeRect(eq_x, anim_y, eq_w, ANIM_H))
    cv.addSubview_(wv)
    _silent_wf = wv

    # EQ bars — hidden initially; shown during recognition + LLM
    ev = EqBarsView.alloc().initWithFrame_(
        AppKit.NSMakeRect(eq_x, anim_y, eq_w, ANIM_H))
    ev.setHidden_(True)
    cv.addSubview_(ev)
    _silent_eq_v = ev

    _silent_win = panel


def _is_python_app(app) -> bool:
    """True for Python background processes — icon not meaningful to show."""
    if not app:
        return True
    try:
        name = str(app.localizedName() or "").lower()
        bid  = str(app.bundleIdentifier() or "").lower()
        exe  = ""
        if app.executableURL():
            exe = str(app.executableURL().lastPathComponent() or "").lower()
        return "python" in name or "python" in bid or "python" in exe
    except Exception:
        return True


def _populate_silent_app_icon():
    """Draw target app icon into _AppIconView if visible (main thread)."""
    if _silent_app_icon_v and _silent_target_app:
        try:
            icon = _silent_target_app.icon()
            if icon:
                _silent_app_icon_v.setImage_(icon)
        except Exception:
            pass


def show_recording_silent(prev_app=None):
    """Show floating waveform strip at screen center-bottom; call AFTER _silent_mode is set.
    If the silent window already exists (resume in same session), just switch state without rebuild.
    """
    global _silent_mode, _silent_target_app
    _silent_mode       = True
    _silent_target_app = prev_app
    def _():
        global _silent_interrupt_fn, _eq_t, _eq_dir, _eq_pulse_t
        _silent_interrupt_fn = None
        _st["mode"] = "recording"
        _clear_waveform()

        if _silent_win:
            # Window already exists — cancel countdown, switch to waveform
            global _eq_countdown_start
            _eq_countdown_start = 0.0
            if _silent_wf:
                _silent_wf.setHidden_(False)
            if _silent_eq_v:
                _silent_eq_v.setHidden_(True)
            _start_timer()
            _silent_win.orderFrontRegardless()
            return

        show_icon = not _is_python_app(_silent_target_app)
        _build_silent_header(show_icon=show_icon)

        _populate_silent_app_icon()

        if _silent_wf:
            _silent_wf.setHidden_(False)
        if _silent_eq_v:
            _silent_eq_v.setHidden_(True)

        _start_timer()
        _silent_win.orderFrontRegardless()
    _main(_)


def show_recognizing_silent():
    """Silent mode: recording done, Whisper transcribing — equalizer scan animation."""
    def _():
        global _eq_t, _eq_dir
        if _silent_wf:
            _silent_wf.setHidden_(True)
        if _silent_eq_v:
            # Pink bars, left→right→left scan
            _silent_eq_v.setMode_(0)
            _silent_eq_v.setCol_(C_PINK)
            _eq_t   = 0.0
            _eq_dir = 1
            _silent_eq_v.setHidden_(False)
    _main(_)


def show_countdown_silent(duration: float = 2.0):
    """Silent mode: grace period countdown — bars fill left→right (green→red)."""
    def _():
        global _eq_countdown_start, _eq_countdown_dur, _eq_countdown_t
        import time as _tm
        _eq_countdown_start = _tm.time()
        _eq_countdown_dur   = duration
        _eq_countdown_t     = 0.0
        if _silent_wf:
            _silent_wf.setHidden_(True)
        if _silent_eq_v:
            _silent_eq_v.setMode_(2)
            _silent_eq_v.setHidden_(False)
        _start_timer()
    _main(_)


def cancel_countdown_silent():
    """Cancel countdown and return to scan animation (user pressed Alt during grace)."""
    def _():
        global _eq_countdown_start
        _eq_countdown_start = 0.0
        if _silent_eq_v and not _silent_eq_v.isHidden() and _silent_eq_v._mode == 2:
            _silent_eq_v.setMode_(0)
            _silent_eq_v.setCol_(C_PINK)
    _main(_)


def show_transcribed_silent(text: str):
    """Silent mode: transcription done without LLM — overlay closes immediately after paste."""
    pass


def update_silent_accumulation(text: str):
    """Show accumulated transcribed text in the silent pill.

    Window grows upward based on actual text height.
    Max = 4 × initial strip height; after that — scrollable.
    NSScrollView + NSTextView with fully transparent background.
    """
    SEP_H    = 1
    PAD      = 12
    WIN_SIDE = 4
    TOP_PAD  = 10   # gap from separator to first line of text
    BOT_PAD  = 8    # gap from last line to window top edge

    def _():
        global _silent_text_v, _silent_scroll_v, _silent_strip_win_h, _silent_sep_y
        import time as _t
        with open("/tmp/vi_debug.log","a") as f:
            f.write(f"[{_t.strftime('%H:%M:%S')}] accum: win={_silent_win is not None} tv={_silent_text_v is not None} text={repr(text[:40])}\n")
        if not _silent_win:
            return

        fr    = _silent_win.frame()
        old_h = fr.size.height
        cw    = fr.size.width
        CARD_W = cw - WIN_SIDE * 2
        sv_w   = CARD_W - PAD * 2
        cv     = _silent_win.contentView()

        if _silent_text_v is None:
            # ── FIRST CALL: record strip height, create separator + scroll view ─
            _silent_strip_win_h = int(old_h)
            _silent_sep_y       = int(old_h - WIN_SIDE)

            # Separator
            sep = AppKit.NSView.alloc().initWithFrame_(
                AppKit.NSMakeRect(WIN_SIDE, _silent_sep_y, CARD_W, SEP_H))
            sep.setWantsLayer_(True)
            sep.layer().setBackgroundColor_(_rgba(0.15, 0.8, 0.15, 0.3).CGColor())
            cv.addSubview_(sep)

            # NSScrollView — starts 1px tall, grows below
            sv_y = _silent_sep_y + SEP_H + TOP_PAD
            scroll = AppKit.NSScrollView.alloc().initWithFrame_(
                AppKit.NSMakeRect(WIN_SIDE + PAD, sv_y, sv_w, 1))
            scroll.setBorderType_(AppKit.NSNoBorder)
            scroll.setHasVerticalScroller_(False)   # revealed when needed
            scroll.setHasHorizontalScroller_(False)
            scroll.setAutohidesScrollers_(True)
            # Full transparency: scroll view + clip view (NSClipView)
            scroll.setBackgroundColor_(AppKit.NSColor.clearColor())
            scroll.setDrawsBackground_(False)
            scroll.contentView().setDrawsBackground_(False)
            # Themed scroller: 4px accent-color knob, auto-hides via overlay style
            scroll.setVerticalScroller_(_ThinAccentScroller.alloc().init())
            scroll.setScrollerStyle_(getattr(AppKit, 'NSScrollerStyleOverlay', 1))

            # NSTextView — document view, auto-grows vertically
            tv = AppKit.NSTextView.alloc().initWithFrame_(
                AppKit.NSMakeRect(0, 0, sv_w, 1))
            tv.setEditable_(False)
            tv.setSelectable_(False)
            tv.setDrawsBackground_(False)
            tv.setBackgroundColor_(AppKit.NSColor.clearColor())
            tv.textContainer().setLineFragmentPadding_(0)
            tv.textContainer().setWidthTracksTextView_(True)
            tv.textContainer().setHeightTracksTextView_(False)
            tv.setHorizontallyResizable_(False)
            tv.setVerticallyResizable_(True)
            tv.setMinSize_(AppKit.NSMakeSize(sv_w, 0))
            tv.setMaxSize_(AppKit.NSMakeSize(sv_w, 100000))
            tv.setAutoresizingMask_(AppKit.NSViewWidthSizable)

            scroll.setDocumentView_(tv)
            cv.addSubview_(scroll)
            _silent_scroll_v = scroll
            _silent_text_v   = tv
            cv.updateTrackingAreas()

        # ── Set text via NSAttributedString (reliable font + color) ──────────
        attrs = {
            AppKit.NSFontAttributeName:            _mono(9.5),
            AppKit.NSForegroundColorAttributeName: C_TEXT,
        }
        astr = AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs)
        _silent_text_v.textStorage().setAttributedString_(astr)

        # ── Measure content height ────────────────────────────────────────────
        lm = _silent_text_v.layoutManager()
        lm.ensureLayoutForTextContainer_(_silent_text_v.textContainer())
        used = lm.usedRectForTextContainer_(_silent_text_v.textContainer())
        content_h = used.size.height

        # ── Grow or scroll? ───────────────────────────────────────────────────
        sv_y = _silent_sep_y + SEP_H + TOP_PAD
        MAX_WIN_H  = _silent_strip_win_h * 4
        MAX_SV_H   = MAX_WIN_H - sv_y - BOT_PAD - WIN_SIDE

        if content_h <= MAX_SV_H:
            # Content fits — grow window to show everything
            sv_h = max(content_h, 12)
            new_win_h = int(sv_y + sv_h + BOT_PAD + WIN_SIDE)
            needs_scroller = False
        else:
            # Content overflows — lock window at max, enable scroller
            sv_h = MAX_SV_H
            new_win_h = int(MAX_WIN_H)
            needs_scroller = True

        # Resize text view to match content (allows scroll to work)
        _silent_text_v.setFrameSize_(AppKit.NSMakeSize(sv_w, max(content_h, sv_h)))

        # Grow window if needed
        if new_win_h > int(old_h):
            _silent_win.setFrame_display_animate_(
                AppKit.NSMakeRect(fr.origin.x, fr.origin.y, cw, new_win_h),
                True, False)
            cv.setFrame_(AppKit.NSMakeRect(0, 0, cw, new_win_h))
            for sv in list(cv.subviews()):
                if isinstance(sv, _SilentBgView):
                    sv.setFrame_(AppKit.NSMakeRect(
                        WIN_SIDE, WIN_SIDE, CARD_W, new_win_h - WIN_SIDE * 2))
                    break
            cv.updateTrackingAreas()

        # Resize scroll view to fill text area
        _silent_scroll_v.setFrame_(
            AppKit.NSMakeRect(WIN_SIDE + PAD, sv_y, sv_w, max(sv_h, 12)))

        # Show/hide scrollbar
        _silent_scroll_v.setHasVerticalScroller_(needs_scroller)

        # Scroll to latest text (bottom of content view)
        _silent_text_v.scrollRangeToVisible_(
            AppKit.NSMakeRange(_silent_text_v.string().length(), 0))

        _silent_win.display()

    _main(_)


def _build_processing_card(raw_text: str, show_icon: bool):
    """Build the LLM-processing card (wider, taller than recording strip).

    Layout (dark rounded card, y=0 at bottom):
      ┌─────────────────────────────────────────┐  ← top
      │ распознанный текст...                   │  TEXT (high y)
      │ распознанный текст...                   │
      │─────────────────────────────────────────│  separator
      │ [ICON 28px] App name       [EQ BARS]    │  HEADER (low y)
      └─────────────────────────────────────────┘  ← bottom
    Hover over TEXT area: "Оставить без обработки" (header stays uncovered).
    """
    global _silent_win, _silent_wf, _silent_eq_v
    global _silent_app_icon_v, _silent_hover_v, _silent_text_v
    global _silent_saved_cx, _silent_saved_sy

    # Preserve the current window's height — don't shrink if text area grew during accumulation
    _prev_win_h = None
    if _silent_win:
        fr = _silent_win.frame()
        _silent_saved_cx = fr.origin.x + fr.size.width / 2
        _silent_saved_sy = fr.origin.y
        _silent_save_pos(_silent_win)
        _prev_win_h = int(fr.size.height)
        _silent_win.orderOut_(None)
        _silent_win.close()
        _silent_win = None

    _silent_app_icon_v = None
    _silent_hover_v    = None
    _silent_text_v     = None

    CARD_W   = 320
    PAD      = 12
    ICON_SZ  = 28
    GAP      = 7
    ANIM_H   = 22
    HEADER_H = ICON_SZ + PAD * 2        # 52 — strip at bottom
    SEP_H    = 1
    WIN_SIDE = 4
    MIN_TEXT_H = 72
    # Use accumulated window height if it's larger (don't shrink on LLM transition)
    default_win_h = HEADER_H + SEP_H + MIN_TEXT_H + PAD + WIN_SIDE * 2  # 145
    win_h   = max(_prev_win_h or 0, default_win_h)
    CARD_H  = win_h - WIN_SIDE * 2
    TEXT_H  = CARD_H - HEADER_H - SEP_H - PAD  # variable, fills card

    win_w = CARD_W + WIN_SIDE * 2

    scr = AppKit.NSScreen.mainScreen()
    if _silent_saved_cx is not None:
        sx = int(_silent_saved_cx - win_w / 2)
        sy = int(_silent_saved_sy)
    else:
        sx, sy = _silent_load_pos(win_w, win_h)

    panel = _SilentPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        AppKit.NSMakeRect(sx, sy, win_w, win_h),
        AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered, False,
    )
    panel.setOpaque_(False)
    panel.setBackgroundColor_(AppKit.NSColor.clearColor())
    panel.setLevel_(AppKit.NSFloatingWindowLevel + 1)
    panel.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
        AppKit.NSWindowCollectionBehaviorStationary |
        AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
    )
    panel.setHasShadow_(False)
    panel.setHidesOnDeactivate_(False)
    panel.setMovable_(True)
    panel.setMovableByWindowBackground_(True)

    cv = _SilentContentView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, win_w, win_h))
    panel.setContentView_(cv)
    cv.updateTrackingAreas()

    # ── dark card background ──────────────────────────────────────────────────
    bg = _SilentBgView.alloc().initWithFrame_(
        AppKit.NSMakeRect(WIN_SIDE, WIN_SIDE, CARD_W, CARD_H))
    cv.addSubview_(bg)

    # ── header row (BOTTOM of card, same position as recording strip) ─────────
    header_y = WIN_SIDE   # y of header bottom edge

    # app icon (left)
    if show_icon:
        iv = _AppIconView.alloc().initWithFrame_(
            AppKit.NSMakeRect(WIN_SIDE + PAD,
                              header_y + (HEADER_H - ICON_SZ) // 2,
                              ICON_SZ, ICON_SZ))
        cv.addSubview_(iv)
        iv.applyRoundedMask_(ICON_SZ * 0.22)
        _silent_app_icon_v = iv
        name_x = WIN_SIDE + PAD + ICON_SZ + GAP
    else:
        name_x = WIN_SIDE + PAD

    # app name label — measure text, EQ gets the rest
    app_name = ""
    if _silent_target_app:
        try:
            app_name = str(_silent_target_app.localizedName() or "")
        except Exception:
            pass
    font_d     = {AppKit.NSFontAttributeName: _mono(10)}
    raw_name_w = int(AppKit.NSString.stringWithString_(app_name).sizeWithAttributes_(font_d).width) if app_name else 0
    right_edge = WIN_SIDE + CARD_W - PAD
    max_name_w = (right_edge - name_x - GAP) // 2
    name_lbl_w = min(raw_name_w + 4, max_name_w)
    card_eq_x  = name_x + name_lbl_w + GAP
    card_eq_w  = right_edge - card_eq_x

    name_lbl = _mklabel(app_name, size=10, color=C_TEXT)
    name_lbl.setFrame_(AppKit.NSMakeRect(
        name_x, header_y + (HEADER_H - 14) // 2, name_lbl_w, 14))
    cv.addSubview_(name_lbl)

    # EQ bars — fills all space right of name
    eq_y = header_y + (HEADER_H - ANIM_H) // 2
    ev = EqBarsView.alloc().initWithFrame_(
        AppKit.NSMakeRect(card_eq_x, eq_y, card_eq_w, ANIM_H))
    ev.setMode_(1)
    ev.setCol_(C_YEL)
    cv.addSubview_(ev)
    _silent_eq_v = ev
    _silent_wf   = None

    # ── separator line ────────────────────────────────────────────────────────
    sep_y = WIN_SIDE + HEADER_H
    sep = AppKit.NSBox.alloc().initWithFrame_(
        AppKit.NSMakeRect(WIN_SIDE + PAD, sep_y, CARD_W - PAD * 2, SEP_H))
    sep.setBoxType_(AppKit.NSBoxSeparator)
    sep.setBorderColor_(C_GREEN_BORD)
    cv.addSubview_(sep)

    # ── recognized text (TOP of card, above separator) ────────────────────────
    TOP_INSET = 10   # breathing room between text view top and card top edge
    text_y = WIN_SIDE + HEADER_H + SEP_H + PAD // 2
    tv = AppKit.NSTextField.labelWithString_(raw_text or "")
    tv.setEditable_(False)
    tv.setSelectable_(False)
    tv.setBezeled_(False)
    tv.setDrawsBackground_(False)
    tv.setFont_(_mono(10))
    tv.setTextColor_(C_TEXT)
    tv.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
    tv.setMaximumNumberOfLines_(max(4, TEXT_H // 14))
    tv.setFrame_(AppKit.NSMakeRect(
        WIN_SIDE + PAD, text_y,
        CARD_W - PAD * 2, TEXT_H - TOP_INSET))
    cv.addSubview_(tv)
    _silent_text_v = tv

    # ── hover overlay (interrupt) — covers TEXT area only, not header ─────────
    hov_y = WIN_SIDE + HEADER_H + SEP_H
    hov_h = TEXT_H + PAD
    hov = _HoverOverlayView.alloc().initWithFrame_(
        AppKit.NSMakeRect(WIN_SIDE, hov_y, CARD_W, hov_h))
    hov._hint      = "Оставить без обработки"
    hov._hint_size = 16
    hov._hint_bold = True
    hov.setHidden_(True)
    cv.addSubview_(hov)
    _silent_hover_v = hov

    ev.setWantsLayer_(True)
    ev.layer().setOpacity_(1.0)

    _silent_win = panel


def show_processing_silent(interrupt_fn, raw_text=''):
    """Silent mode: LLM processing — large card with recognized text + interrupt overlay."""
    global _silent_interrupt_fn
    _silent_interrupt_fn = interrupt_fn
    def _():
        global _eq_pulse_t
        import time as _t
        try:
            n = str(_silent_target_app.localizedName()) if _silent_target_app else "None"
            b = str(_silent_target_app.bundleIdentifier()) if _silent_target_app else "None"
        except Exception as e:
            n, b = f"ERR:{e}", ""
        with open("/tmp/vi_debug.log","a") as f:
            f.write(f"[{_t.strftime('%H:%M:%S')}] processing_silent: target='{n}' bid='{b}'\n")
        show_icon = not _is_python_app(_silent_target_app)
        _build_processing_card(raw_text, show_icon)
        _populate_silent_app_icon()
        _eq_pulse_t = 0.0
        _silent_win.orderFrontRegardless()
    _main(_)


def is_idle() -> bool:
    """True when the window is completely closed (no active session)."""
    return _st.get("mode", "idle") == "idle"


def is_any_session_visible() -> bool:
    """True if silent strip OR full-mode recording card is currently showing."""
    return _silent_win is not None or not is_idle()


def get_silent_scenario():
    """Return the scenario dict configured for silent auto-paste, or None."""
    for sc in _st.get("scenarios", []):
        if sc.get("silent"):
            return sc
    return None


def get_full_default_scenario():
    """Return the scenario dict marked as full mode default, or None."""
    for sc in _st.get("scenarios", []):
        if sc.get("full_default"):
            return sc
    return None


def get_active_sc():
    """Return the index of the currently active scenario, or None."""
    return _st.get("active_sc")


def get_silent_interrupt_fn():
    """Return current interrupt callable (set during LLM processing), or None."""
    return _silent_interrupt_fn


# ── Init ──────────────────────────────────────────────────────────────────────

def init(on_scenario_callback, on_history_callback=None,
         on_paste_callback=None, on_copy_callback=None,
         on_history_delete_callback=None,
         on_history_load_callback=None, on_history_merge_callback=None,
         on_add_history_callback=None,
         on_update_session_callback=None, on_session_end_callback=None):
    global _win, _pill, _wf, _tv, _lbl, _sc_icons, _sc_seps, _sc_sep_active
    global _hist_btn, _hist_corner_btn, _cfg_hdr_btn, _send_hdr_btn, _close_btn, _expand_btn, _scroll, _btn_t
    global _md_btn, _doc_view, _rich_blocks
    global _on_scenario_cb, _on_history_cb, _on_paste_cb, _on_copy_cb, _on_history_delete_cb
    global _on_history_load_cb, _on_history_merge_cb, _on_add_history_cb
    global _on_update_session_cb, _on_session_end_cb
    global _sc_prev_btn, _sc_next_btn, _sc_page
    global _proc_eq_v, _proc_app_lbl, _proc_sc_lbl, _proc_hover_v
    global _app_icon_v, _undo_sc_btn, _on_undo_sc_cb

    _on_scenario_cb        = on_scenario_callback
    _on_history_cb         = on_history_callback
    _on_paste_cb           = on_paste_callback
    _on_copy_cb            = on_copy_callback
    _on_history_delete_cb  = on_history_delete_callback
    _on_history_load_cb    = on_history_load_callback
    _on_history_merge_cb   = on_history_merge_callback
    _on_add_history_cb     = on_add_history_callback
    _on_update_session_cb  = on_update_session_callback
    _on_session_end_cb     = on_session_end_callback
    _btn_t                 = BtnTarget.alloc().init()

    # Load magnet window state from saved settings
    _magnet_load()

    # One-time migration: silent_sc_idx (old setting) → scenario["silent"] flag.
    # After migration, settings.json is rewritten without silent_sc_idx so this
    # never runs again.
    legacy_idx = _st.pop("_silent_sc_idx_legacy", None)
    if legacy_idx is not None:
        scenarios = _st.get("scenarios", [])
        if 0 <= legacy_idx < len(scenarios):
            for i, s in enumerate(scenarios):
                if i == legacy_idx:
                    s["silent"] = True
                else:
                    s.pop("silent", None)
            save_scenarios(scenarios)
        _save_settings()   # rewrite settings.json without silent_sc_idx

    _init_sounds()

    x, y = _win_load_pos()

    _win = DragPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        AppKit.NSMakeRect(x, y, W, H),
        AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered, False,
    )
    _win.setOpaque_(False)
    _win.setBackgroundColor_(AppKit.NSColor.clearColor())
    _win.setLevel_(AppKit.NSFloatingWindowLevel)
    _win.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
        AppKit.NSWindowCollectionBehaviorStationary |
        AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
    )
    _win.setHasShadow_(True)
    _win.setHidesOnDeactivate_(False)
    _win.setAlphaValue_(_st.get("opacity", 0.88))

    cv    = _win.contentView()
    _pill = TerminalView.alloc().initWithFrame_(cv.bounds())
    _pill.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
    cv.addSubview_(_pill)

    # ── Header: ONE line — [status] [~waveform~] [□][×] ─────────────────────

    # Status label [left] — shifted right to make room for app icon
    ICON_SZ = 22
    _lbl = _mklabel(_T("ready"), size=10, color=C_IDLE)
    _lbl.setFrame_(AppKit.NSMakeRect(STS_X + ICON_SZ + 2, HDR_ITEM_Y, STS_W, HDR_ITEM_H))
    _lbl.setAutoresizingMask_(AppKit.NSViewMinYMargin)
    _pill.addSubview_(_lbl)

    # Иконка целевого приложения (слева, 22×22) — _AppIconView для double-click expand
    _app_icon_v = _AppIconView.alloc().initWithFrame_(
        AppKit.NSMakeRect(STS_X, HDR_ITEM_Y + 1, ICON_SZ - 2, ICON_SZ - 2))
    _app_icon_v.setHidden_(True)
    _app_icon_v.setAutoresizingMask_(AppKit.NSViewMinYMargin)
    _pill.addSubview_(_app_icon_v)

    # [↩] — возврат к оригиналу (скрыта пока сценарий не применён)
    _undo_sc_btn = _mkbtn("[↩]", color=C_CYAN, size=11)
    _undo_sc_btn.setFrame_(AppKit.NSMakeRect(STS_X + ICON_SZ, HDR_ITEM_Y, 34, HDR_ITEM_H))
    _undo_sc_btn.setToolTip_("Вернуть оригинал")
    _undo_sc_btn.setHidden_(True)
    _undo_sc_btn.setAutoresizingMask_(AppKit.NSViewMinYMargin)
    _undo_sc_btn.setTarget_(_btn_t)
    _undo_sc_btn.setAction_(BtnTarget.undoScenario_)
    _pill.addSubview_(_undo_sc_btn)

    # App name label — shown only during LLM processing (replaces status label + right buttons)
    _proc_app_lbl = _mklabel("", size=11, color=_rgba(0.7, 0.7, 0.7, 1.0))
    _proc_app_lbl.setFrame_(AppKit.NSMakeRect(STS_X + 26, HDR_ITEM_Y - 2, 130, HDR_ITEM_H))
    _proc_app_lbl.setHidden_(True)
    _proc_app_lbl.setAutoresizingMask_(AppKit.NSViewMinYMargin)
    _pill.addSubview_(_proc_app_lbl)

    # Scenario name label — symmetric gaps: STS_X (=10) from EQ end and from gear icon
    SC_LBL_H = 14                                    # tight height = font size, avoids top-clipping
    SC_LBL_Y = HDR_Y + (HDR_H - SC_LBL_H) // 2     # = 313 (true vertical center)
    SC_LBL_X = EQ_CTR_X + EQ_CTR_W + STS_X          # = 330 (same gap as icon from left)
    SC_LBL_W = CFG_H_X - SC_LBL_X - STS_X           # = 70  (same gap to gear on right)
    _proc_sc_lbl = _mklabel("", size=9, color=C_YEL)
    _proc_sc_lbl.setAlignment_(AppKit.NSTextAlignmentCenter)
    _proc_sc_lbl.setFrame_(AppKit.NSMakeRect(SC_LBL_X, SC_LBL_Y, SC_LBL_W, SC_LBL_H))
    _proc_sc_lbl.setHidden_(True)
    _proc_sc_lbl.setAutoresizingMask_(AppKit.NSViewMinYMargin)
    _pill.addSubview_(_proc_sc_lbl)

    # Waveform [fixed centered position, same size in all modes]
    _wf = WaveformView.alloc().initWithFrame_(
        AppKit.NSMakeRect(EQ_CTR_X, HDR_ITEM_Y, EQ_CTR_W, HDR_ITEM_H))
    _wf.setAutoresizingMask_(AppKit.NSViewMinYMargin)
    _pill.addSubview_(_wf)

    # Processing EQ bars — same fixed centered slot as waveform
    _proc_eq_v = EqBarsView.alloc().initWithFrame_(
        AppKit.NSMakeRect(EQ_CTR_X, HDR_ITEM_Y, EQ_CTR_W, HDR_ITEM_H))
    _proc_eq_v.setMode_(1)                          # pulse mode (like silent-LLM)
    _proc_eq_v.setCol_(C_YEL)                       # yellow pulse
    _proc_eq_v.setHidden_(True)
    _proc_eq_v.setAutoresizingMask_(AppKit.NSViewMinYMargin)
    _pill.addSubview_(_proc_eq_v)

    # Init mouse tracking for main pill
    _pill.updateTrackingAreas()

    # [⚙] — gear icon at far right corner, always visible
    _cfg_hdr_btn = _mkbtn("⚙", color=C_GREEN_DIM, size=18)
    _cfg_hdr_btn.setFrame_(AppKit.NSMakeRect(CFG_H_X, HDR_ITEM_Y, CFG_H_W, HDR_ITEM_H))
    _cfg_hdr_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMinYMargin)
    _cfg_hdr_btn.setTarget_(_btn_t)
    _cfg_hdr_btn.setAction_(BtnTarget.cfg_)
    _pill.addSubview_(_cfg_hdr_btn)

    # Expand [□] — permanently hidden; double-click on app icon activates expand
    _expand_btn = _mkbtn("[□]", color=C_GREEN_DIM, size=12)
    _expand_btn.setFrame_(AppKit.NSMakeRect(EXP_X, HDR_ITEM_Y, EXP_W, HDR_ITEM_H))
    _expand_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMinYMargin)
    _expand_btn.setTarget_(_btn_t)
    _expand_btn.setAction_(BtnTarget.expand_)
    _expand_btn.setHidden_(True)
    _pill.addSubview_(_expand_btn)

    # Close [×] — rightmost
    _close_btn = _mkbtn("[×]", color=C_REC, size=12)
    _close_btn.setFrame_(AppKit.NSMakeRect(CLO_X, HDR_ITEM_Y, CLO_W, HDR_ITEM_H))
    _close_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMinYMargin)
    _close_btn.setTarget_(_btn_t)
    _close_btn.setAction_(BtnTarget.close_)
    _close_btn.setHidden_(True)   # removed from header — action panel has [ОТМЕНИТЬ]
    _pill.addSubview_(_close_btn)

    # Header separator
    _pill.addSubview_(_sep_line(0, HDR_Y - 1, W))

    # ── Text area (middle) ────────────────────────────────────────────────────

    _scroll = AppKit.NSScrollView.alloc().initWithFrame_(
        AppKit.NSMakeRect(8, TXT_Y, W - 16, TXT_H))   # symmetric 8px margins
    _scroll.setHasVerticalScroller_(True)
    _scroll.setBorderType_(AppKit.NSNoBorder)
    _scroll.setDrawsBackground_(False)
    _scroll.setBackgroundColor_(AppKit.NSColor.clearColor())
    _scroll.setAutoresizingMask_(
        AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
    _scroll.setHidden_(False)
    _scroll.setWantsLayer_(True)
    _scroll.layer().setBorderColor_(C_GREEN_BORD.CGColor())
    _scroll.layer().setBorderWidth_(0.5)
    _scroll.layer().setCornerRadius_(3.0)

    # Document view container — FlippedView holds rich blocks + _tv
    _doc_view = _FlippedView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, W - 16, 800))
    _doc_view.setAutoresizingMask_(AppKit.NSViewWidthSizable)

    _tv = TerminalTextView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, W - 16, 800))
    _tv.setEditable_(True)
    _tv.setFont_(_mono(_st["font_size"]))
    _tv.setTextColor_(C_TEXT)
    _tv.setBackgroundColor_(AppKit.NSColor.clearColor())
    _tv.setDrawsBackground_(False)
    _tv.setTextContainerInset_(AppKit.NSMakeSize(8, 6))
    # Hide native thin cursor — we draw our own block cursor as an overlay view
    _tv.setInsertionPointColor_(AppKit.NSColor.clearColor())
    _tv.setSelectedTextAttributes_({
        AppKit.NSBackgroundColorAttributeName: _rgba(0.00, 0.40, 0.00, 0.38),
        AppKit.NSForegroundColorAttributeName: C_GREEN_BR,
    })
    # Ensure pasted plain text always uses terminal color/font
    _tv.setTypingAttributes_({
        AppKit.NSFontAttributeName:            _mono(_st["font_size"]),
        AppKit.NSForegroundColorAttributeName: C_TEXT,
    })
    _doc_view.addSubview_(_tv)
    _scroll.setVerticalScroller_(_ThinGreenScroller.alloc().init())
    _scroll.setScrollerStyle_(getattr(AppKit, 'NSScrollerStyleOverlay', 1))
    _scroll.setDocumentView_(_doc_view)
    _pill.addSubview_(_scroll)

    # [md] format toggle — top of text area, left side (away from scrollbar)
    # Rich blocks have their own per-block toggle; this is only for MD mode
    _md_btn = _mkbtn("[md]", color=C_GREEN_DIM, size=9)
    _md_btn.setFrame_(AppKit.NSMakeRect(W - 74, TXT_TOP - 18, 36, 14))
    _md_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMaxYMargin)
    _md_btn.setWantsLayer_(True)   # ensure renders above layer-backed _scroll
    _md_btn.setHidden_(True)
    _md_btn.setTarget_(_btn_t)
    _md_btn.setAction_(BtnTarget.mdToggle_)
    _pill.addSubview_(_md_btn)

    # ── Floating sticky bar for large markdown blocks ─────────────────────────
    global _float_bar, _float_bar_md, _float_bar_cp
    _float_bar = AppKit.NSView.alloc().initWithFrame_(
        AppKit.NSMakeRect(W - 58, TXT_Y + TXT_H - 18, 52, 16))
    _float_bar.setWantsLayer_(True)
    _float_bar.layer().setBackgroundColor_(_rgba(0.03, 0.08, 0.03, 0.88).CGColor())
    _float_bar.layer().setBorderColor_(C_GREEN_BORD.CGColor())
    _float_bar.layer().setBorderWidth_(0.5)
    _float_bar.layer().setCornerRadius_(3.0)
    _float_bar.setHidden_(True)
    _float_bar.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMinYMargin)

    _float_bar_md = _BlockHoverBtn.alloc().init()
    _float_bar_md._chk_lbl    = "md"
    _float_bar_md._chk_sz     = 9
    _float_bar_md._normal_col = C_GREEN_DIM
    _float_bar_md.setBordered_(False)
    _float_bar_md.setAttributedTitle_(_atitle("md", size=9, color=C_GREEN_DIM))
    _float_bar_md.setFrame_(AppKit.NSMakeRect(2, 2, 22, 12))
    _float_bar_md.setTarget_(_btn_t)
    _float_bar_md.setAction_(BtnTarget.floatMdToggle_)
    _float_bar.addSubview_(_float_bar_md)

    _float_bar_cp = _BlockHoverBtn.alloc().init()
    _float_bar_cp._chk_lbl    = "→"
    _float_bar_cp._chk_sz     = 10
    _float_bar_cp._normal_col = C_GREEN_DIM
    _float_bar_cp.setBordered_(False)
    _float_bar_cp.setAttributedTitle_(_atitle("→", size=10, color=C_GREEN_DIM))
    _float_bar_cp.setFrame_(AppKit.NSMakeRect(26, 2, 24, 12))
    _float_bar_cp.setTarget_(_btn_t)
    _float_bar_cp.setAction_(BtnTarget.floatCopy_)
    _float_bar.addSubview_(_float_bar_cp)

    _pill.addSubview_(_float_bar)

    # Register for scroll notifications to update float bar.
    # Use explicit bytes selectors (PyObjC requirement for addObserver).
    nc = AppKit.NSNotificationCenter.defaultCenter()
    # 1) NSClipView bounds change → fires when scroll position changes programmatically
    _scroll.contentView().setPostsBoundsChangedNotifications_(True)
    nc.addObserver_selector_name_object_(
        _btn_t, b"docScrolled:",
        AppKit.NSViewBoundsDidChangeNotification,
        _scroll.contentView())
    # 2) NSScrollView live-scroll → fires on trackpad/wheel user gestures
    nc.addObserver_selector_name_object_(
        _btn_t, b"docScrolled:",
        AppKit.NSScrollViewDidLiveScrollNotification,
        _scroll)

    # Fake block cursor (matches .term-cursor in admin.roclea.com)
    global _cur_view, _cur_timer
    _cur_view = _BlockCursor.alloc().initWithFrame_(AppKit.NSMakeRect(8, 6, 8, 16))
    _cur_view.setWantsLayer_(True)
    _tv.addSubview_(_cur_view)
    _cur_timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.4, _cur_view, _BlockCursor.tick_, None, True)
    AppKit.NSRunLoop.mainRunLoop().addTimer_forMode_(
        _cur_timer, AppKit.NSRunLoopCommonModes)

    # Bottom separator
    _pill.addSubview_(_sep_line(0, BTN_H + 2, W))

    # ── Button row (bottom) ───────────────────────────────────────────────────

    _sc_icons.clear()
    _sc_seps.clear()
    _sc_active.clear()
    _sc_sep_active.clear()
    _sc_page = 0

    # [<] prev-page nav button — always at left edge of picker, disabled on first page
    _sc_prev_btn = _mkbtn("[<]", color=C_GREEN_DIM, size=11)
    _sc_prev_btn.setHidden_(True)   # hidden outside picker mode
    _sc_prev_btn.setEnabled_(False)
    _sc_prev_btn.setAutoresizingMask_(AppKit.NSViewMaxYMargin)
    _sc_prev_btn.setTarget_(_btn_t)
    _sc_prev_btn.setAction_(BtnTarget.scPrev_)
    _pill.addSubview_(_sc_prev_btn)

    # SC_PAGE scenario button slots — labels/tags set by _relayout_buttons
    for i in range(SC_PAGE):
        btn = _mkbtn("", color=C_GREEN)
        btn.setTag_(i)
        btn.setEnabled_(False)
        btn.setHidden_(True)
        btn.setAutoresizingMask_(AppKit.NSViewMaxYMargin)
        btn.setTarget_(_btn_t)
        btn.setAction_(BtnTarget.scenario_)
        _pill.addSubview_(btn)
        _sc_icons.append(btn)
        _sc_active.append(False)

        # · separator (one per slot; last slot's sep will be hidden)
        sep = _DotSep.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 14, 24))
        sep.setHidden_(True)
        sep.setAutoresizingMask_(AppKit.NSViewMaxYMargin)
        _pill.addSubview_(sep)
        _sc_seps.append(sep)
        _sc_sep_active.append(False)

    # [>] next-page nav button (hidden until needed)
    _sc_next_btn = _mkbtn("[>]", color=C_GREEN_DIM, size=11)
    _sc_next_btn.setHidden_(True)
    _sc_next_btn.setEnabled_(False)
    _sc_next_btn.setAutoresizingMask_(AppKit.NSViewMaxYMargin)
    _sc_next_btn.setTarget_(_btn_t)
    _sc_next_btn.setAction_(BtnTarget.scNext_)
    _pill.addSubview_(_sc_next_btn)

    # [ИСТ/HIST] — header, always visible, next to [НСТ]
    _hist_btn = _mkbtn(_T("btn_hist"), color=C_CYAN, size=11)
    _hist_btn.setFrame_(AppKit.NSMakeRect(HIST_H_X, HDR_ITEM_Y, HIST_H_W, HDR_ITEM_H))
    _hist_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMinYMargin)
    _hist_btn.setToolTip_("История (двойное нажатие хоткея)")
    _hist_btn.setTarget_(_btn_t)
    _hist_btn.setAction_(BtnTarget.history_)
    _hist_btn.setHidden_(True)   # moved to action row — always hidden in header
    _pill.addSubview_(_hist_btn)

    # [↵] — bottom row, rightmost (separate from scenarios by visual gap)
    _send_hdr_btn = _mkbtn("[↵]", color=C_GREEN_BR, size=12)
    _send_hdr_btn.setHidden_(True)
    _send_hdr_btn.setToolTip_("Вставить (Shift+Enter)")
    _send_hdr_btn.setAutoresizingMask_(AppKit.NSViewMaxYMargin)
    _send_hdr_btn.setTarget_(_btn_t)
    _send_hdr_btn.setAction_(BtnTarget.send_)
    _pill.addSubview_(_send_hdr_btn)

    # Initial layout (assigns labels/positions to all slots)
    _relayout_buttons(W)

    # Check model availability in background — colors scenario buttons appropriately
    _start_sc_avail_check()

    # ── 3-button action row: Cancel | Scene | Send (hidden when empty) ───────────
    # History moved to permanent corner button — leaves right side clear
    global _action_row_v, _action_hist_btn, _action_cancel_btn, _action_scene_btn, _action_send_btn
    _action_row_v = AppKit.NSView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, W, BTN_H))
    _action_row_v.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMaxYMargin)
    _action_row_v.setHidden_(True)
    _action_hist_btn = None   # now permanently at bottom-right corner

    # Three buttons fit within x=0..404, leaving right 36px for the corner history icon
    ACT3_AVAIL = CFG_H_X - 4 * 8    # 410 - 32 = 378px for 3 buttons
    ACT3_W = ACT3_AVAIL // 3        # = 126px each
    ACT3_H = 28
    ACT3_Y = (BTN_H - ACT3_H) // 2

    _action_cancel_btn = _mkbtn(_T("btn_sc_undo"), color=C_IDLE, size=12)
    _action_cancel_btn.setFrame_(AppKit.NSMakeRect(8, ACT3_Y, ACT3_W, ACT3_H))
    _action_cancel_btn.setTarget_(_btn_t)
    _action_cancel_btn.setAction_(BtnTarget.actionCancel_)
    _action_row_v.addSubview_(_action_cancel_btn)

    _action_scene_btn = _mkbtn(_T("btn_scene"), color=C_YEL, size=12)
    _action_scene_btn.setFrame_(AppKit.NSMakeRect(8 + ACT3_W + 8, ACT3_Y, ACT3_W, ACT3_H))
    _action_scene_btn.setTarget_(_btn_t)
    _action_scene_btn.setAction_(BtnTarget.actionScene_)
    _action_row_v.addSubview_(_action_scene_btn)

    _action_send_btn = _mkbtn(_T("btn_sc_accept"), color=C_GREEN_BR, size=12)
    _action_send_btn.setFrame_(AppKit.NSMakeRect(8 + 2*(ACT3_W + 8), ACT3_Y, ACT3_W, ACT3_H))
    _action_send_btn.setTarget_(_btn_t)
    _action_send_btn.setAction_(BtnTarget.send_)
    _action_row_v.addSubview_(_action_send_btn)

    _pill.addSubview_(_action_row_v)

    # ── 2-button result panel (shown when scenario result is active) ──────────────
    global _sc_action_v, _sc_send_btn2, _sc_cancel_btn2
    _sc_action_v = AppKit.NSView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, W, BTN_H))
    _sc_action_v.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMaxYMargin)
    _sc_action_v.setHidden_(True)

    ACT_BTN_H = 28
    ACT_BTN_Y = (BTN_H - ACT_BTN_H) // 2
    ACT_BTN_W = (W - 3 * 8) // 2   # two equal buttons with 8px margins and gap

    _sc_cancel_btn2 = _mkbtn(_T("btn_sc_undo"), color=C_CYAN, size=12)
    _sc_cancel_btn2.setFrame_(AppKit.NSMakeRect(8, ACT_BTN_Y, ACT_BTN_W, ACT_BTN_H))
    _sc_cancel_btn2.setTarget_(_btn_t)
    _sc_cancel_btn2.setAction_(BtnTarget.undoScenario_)
    _sc_action_v.addSubview_(_sc_cancel_btn2)

    _sc_send_btn2 = _mkbtn(_T("btn_sc_accept"), color=C_GREEN_BR, size=12)
    _sc_send_btn2.setFrame_(AppKit.NSMakeRect(8 + ACT_BTN_W + 8, ACT_BTN_Y, ACT_BTN_W, ACT_BTN_H))
    _sc_send_btn2.setTarget_(_btn_t)
    _sc_send_btn2.setAction_(BtnTarget.send_)
    _sc_action_v.addSubview_(_sc_send_btn2)

    _pill.addSubview_(_sc_action_v)

    # [⧖] — history icon at bottom-right corner, symmetric to gear at top-right
    # Gear: x=CFG_H_X, gap-right=6, gap-top=9. History: same x, same gap from bottom.
    HIST_C_GAP_B = H - (HDR_ITEM_Y + HDR_ITEM_H)   # = same top-gap as gear = 9
    _hist_corner_btn = _mkbtn("☰", color=C_GREEN_DIM, size=14)
    _hist_corner_btn.setFrame_(AppKit.NSMakeRect(CFG_H_X, HIST_C_GAP_B, CFG_H_W, HDR_ITEM_H))
    _hist_corner_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMaxYMargin)
    _hist_corner_btn.setToolTip_("История (двойной клик — развернуть)")
    _hist_corner_btn.setTag_(9)   # used in history_ to detect double-click → expand
    _hist_corner_btn.setTarget_(_btn_t)
    _hist_corner_btn.setAction_(BtnTarget.history_)
    _pill.addSubview_(_hist_corner_btn)

    # Hover cancel overlay — added LAST so it's on top of all other subviews
    # Text drawn in drawRect_ (not as NSTextField subview — would swallow mouse events)
    _proc_hover_v = _HoverOverlayView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, W, H))
    _proc_hover_v._is_main  = True
    _proc_hover_v._hint     = "Оставить без обработки"
    _proc_hover_v._hint_size = 11
    _proc_hover_v.setHidden_(True)
    _proc_hover_v.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
    _pill.addSubview_(_proc_hover_v)

    _win.orderOut_(None)
    AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    _setup_status_bar()

# ── Button visibility helper ──────────────────────────────────────────────────

def _show_buttons(visible: bool, enabled: bool = None):
    """Manage bottom-row visibility.

    States (when visible=True):
      active_sc set  → 2-button panel: [ОТМЕНИТЬ] [ОТПРАВИТЬ]
      sc_picker      → scenario grid (existing _sc_icons)
      else           → 3-button action row: [ОТМЕНИТЬ] [СЦЕНАРИЙ] [ОТПРАВИТЬ]
    """
    if enabled is None:
        enabled = visible

    # Always-hidden legacy buttons (replaced by action panels)
    if _send_hdr_btn: _send_hdr_btn.setHidden_(True)

    def _hide_scenario_grid():
        for b in _sc_icons: b.setHidden_(True)
        for s in _sc_seps:  s.setHidden_(True)
        for nb in (_sc_prev_btn, _sc_next_btn):
            if nb: nb.setHidden_(True)

    if not visible:
        if _action_row_v: _action_row_v.setHidden_(True)
        if _sc_action_v:  _sc_action_v.setHidden_(True)
        _hide_scenario_grid()
        return

    active_sc = _st.get("active_sc")
    sc_picker  = _st.get("sc_picker", False)

    if active_sc is not None:
        # 2-button result panel
        if _sc_send_btn2:
            _sc_send_btn2.setAttributedTitle_(_atitle(_T("btn_sc_accept"), size=12, color=C_GREEN_BR))
        if _sc_cancel_btn2:
            _sc_cancel_btn2.setAttributedTitle_(_atitle(_T("btn_sc_undo"), size=12, color=C_CYAN))
        if _action_row_v: _action_row_v.setHidden_(True)
        if _sc_action_v:  _sc_action_v.setHidden_(False)
        _hide_scenario_grid()
    elif sc_picker:
        # Scenario picker grid — nav arrows always at edges, scenarios fill between
        if _action_row_v: _action_row_v.setHidden_(True)
        if _sc_action_v:  _sc_action_v.setHidden_(True)
        for i, btn in enumerate(_sc_icons):
            act = _sc_active[i] if i < len(_sc_active) else False
            btn.setHidden_(not act)
            btn.setEnabled_(enabled and act)
        for i, sep in enumerate(_sc_seps):
            act = _sc_sep_active[i] if i < len(_sc_sep_active) else False
            sep.setHidden_(not act)
        # Nav arrows always visible; enabled state set by _relayout_buttons
        if _sc_prev_btn: _sc_prev_btn.setHidden_(False)
        if _sc_next_btn: _sc_next_btn.setHidden_(False)
    else:
        # Normal 4-button action row — only show when there's content
        # Check both blocks/_tv (via _get_all_text) and _st["text"] (set before animation)
        has_content = bool(_get_all_text().strip() or _st.get("text", "").strip())
        if _sc_action_v:  _sc_action_v.setHidden_(True)
        _hide_scenario_grid()
        if not has_content:
            if _action_row_v: _action_row_v.setHidden_(True)
        else:
            if _action_hist_btn:
                _action_hist_btn.setAttributedTitle_(_atitle(_T("btn_hist"), size=12, color=C_CYAN))
            if _action_cancel_btn:
                _action_cancel_btn.setAttributedTitle_(_atitle(_T("btn_sc_undo"), size=12, color=C_IDLE))
            if _action_scene_btn:
                _action_scene_btn.setAttributedTitle_(_atitle(_T("btn_scene"), size=12, color=C_YEL))
            if _action_send_btn:
                _action_send_btn.setAttributedTitle_(_atitle(_T("btn_sc_accept"), size=12, color=C_GREEN_BR))
            if _action_row_v: _action_row_v.setHidden_(False)

def _update_action_visibility():
    """Refresh action row visibility based on content; call from text-change hooks."""
    mode = _st.get("mode")
    if mode not in ("ready", "history_open"):
        return
    _show_buttons(True)


# ── Public API ────────────────────────────────────────────────────────────────

def show_recording():
    """Start a recording session (or continue an existing one)."""
    def _():
        global _silent_mode
        # Cancel any pending delayed-history open
        if _btn_t:
            AppKit.NSObject.cancelPreviousPerformRequestsWithTarget_selector_object_(
                _btn_t, b'_openHistDelayed:', None)
        # Hide silent strip — full overlay takes over
        if _silent_win and _silent_win.isVisible():
            _silent_win.orderOut_(None)
        _silent_mode = False
        _st["mode"] = "recording"
        _st["active_sc"] = None
        _st["post_interrupt"] = False
        _st["sc_picker"] = False
        _clear_waveform()
        _show_target_app_header()     # always show icon + app name (no status text)
        _show_buttons(False)
        _start_timer()
        _win.orderFrontRegardless()
        # Restore hist panel if it was open on last session
        if _cfg_saved.get("panels_open", {}).get("hist") and not (_hist_panel and _hist_panel.isVisible()):
            history = _on_history_cb() if _on_history_cb else []
            _show_hist_panel(history)
    _main(_)


def show_transcribing():
    """Recording done — waiting for transcription."""
    def _():
        global _eq_t, _eq_dir
        _st["mode"] = "transcribing"
        if _silent_mode:
            _stop_timer()
            _clear_waveform()
            if _silent_wf:
                _silent_wf.setNeedsDisplay_(True)
        else:
            _show_target_app_header()   # keep icon + name during transcription
            _clear_waveform()
            if _wf:
                _wf.setHidden_(True)    # hide recording bars while EQ animates
            if _proc_eq_v:
                _eq_t   = 0.0
                _eq_dir = 1
                _proc_eq_v.setMode_(0)      # scan left→right
                _proc_eq_v.setCol_(C_PINK)  # pink for recognition phase
                _proc_eq_v.setHidden_(False)
            _start_timer()   # keep animating during recognition
    _main(_)


def show_result(text: str):
    """Transcription done — append new text as a block immediately."""
    def _():
        _st["mode"] = "ready"
        _st["active_sc"] = None
        _end_processing()
        _show_target_app_header()
        old = _st["text"]
        new_full = (old.rstrip() + "\n" + text).strip() if old else text
        _st["text"]    = new_full
        _st["is_md"]   = _is_markdown(new_full)
        _show_buttons(True)
        _refresh_scenario_colors()
        if not _editing_scenario:
            AppKit.NSApp.activateIgnoringOtherApps_(True)
            _win.makeKeyAndOrderFront_(None)
            if _tv:
                _win.makeFirstResponder_(_tv)
        _st["md_mode"] = False
        _update_format_indicator()
        # Show text immediately — no typewriter animation
        prefix = (old.rstrip() + "\n") if old else ""
        if _tv:
            display = prefix + text + '\n'
            _tv.setString_(display)
            ln = len(display)
            _tv.setSelectedRange_(AppKit.NSMakeRange(ln, 0))
            _tv.scrollRangeToVisible_(AppKit.NSMakeRange(ln, 0))
        _relayout_doc_view()
        _finalize_tv_to_block()
        _main(_update_cursor_pos)
    _main(_)


def show_scenario_result(text: str, hist_id: str = None):
    """Scenario processing done — REPLACE overlay text with result (instant, no animation)."""
    _load_history_combined(text, loaded_id=hist_id, keep_active=True)


# ── Providers panel ───────────────────────────────────────────────────────────

def _close_providers_panel():
    global _prov_panel, _prov_field_refs, _prov_dot_refs
    if _prov_panel:
        _prov_panel.orderOut_(None)
        _prov_panel.close()
        _prov_panel = None
    _prov_field_refs = {}
    _prov_dot_refs   = {}


def _toggle_providers_panel():
    global _prov_panel, _prov_field_refs, _prov_dot_refs
    if _prov_panel and _prov_panel.isVisible():
        _close_providers_panel()
        return

    _close_providers_panel()

    mf     = _win.frame()
    PW     = int(mf.size.width)
    MARGIN = 12
    FW     = PW - MARGIN * 2
    LBL_H  = 13
    TF_H   = 22
    GAP    = 4
    BTN_H  = 22

    # ── Fixed height (same across all panels) ─────────────────────────────────
    screen = _win.screen() or AppKit.NSScreen.mainScreen()
    vis    = screen.visibleFrame() if screen else AppKit.NSMakeRect(0, 0, 1440, 900)
    PH     = H_PANEL

    # ── Panel ─────────────────────────────────────────────────────────────────
    panel = _EditorPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        AppKit.NSMakeRect(0, 0, PW, PH),
        AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered, False,
    )
    panel.setOpaque_(False)
    panel.setBackgroundColor_(AppKit.NSColor.clearColor())
    panel.setLevel_(AppKit.NSFloatingWindowLevel + 2)
    panel.setHasShadow_(True)
    panel.setHidesOnDeactivate_(False)
    panel.setAppearance_(AppKit.NSAppearance.appearanceNamed_(
        AppKit.NSAppearanceNameDarkAqua))
    _bg = TerminalView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, PW, PH))
    panel.setContentView_(_bg)
    _prov_panel = panel
    _prov_panel._panel_key = "providers"

    cv = _bg

    def _tf(y_pos, placeholder, value):
        tf = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(MARGIN, y_pos, FW, TF_H))
        _style_tf(tf, placeholder)
        tf.setStringValue_(value)
        cv.addSubview_(tf)
        return tf

    def _dot_lbl(y_pos, pid, title):
        dot = _mklabel("●", size=9, color=C_GREEN_DIM)
        dot.setFrame_(AppKit.NSMakeRect(MARGIN, y_pos, 12, LBL_H))
        cv.addSubview_(dot)
        _prov_dot_refs[pid] = dot
        lbl = _mklabel(title, size=9, bold=True, color=C_GREEN_BR)
        lbl.setFrame_(AppKit.NSMakeRect(MARGIN + 15, y_pos, FW - 15, LBL_H))
        cv.addSubview_(lbl)

    y = PH - 8

    # ── Header ────────────────────────────────────────────────────────────────
    _mkmagnet_btn("providers", cv, 6, y - LBL_H - 2, 22, LBL_H + 4)
    hdr = _mklabel("ПРОВАЙДЕРЫ / API КЛЮЧИ", size=10, color=C_IDLE)
    hdr.setFrame_(AppKit.NSMakeRect(32, y - LBL_H, FW - 32, LBL_H))
    cv.addSubview_(hdr)
    y -= LBL_H + 5
    cv.addSubview_(_sep_line(MARGIN, y, FW, pin="top"))
    y -= 8

    # ── OLLAMA ────────────────────────────────────────────────────────────────
    _dot_lbl(y - LBL_H, "ollama", "OLLAMA")
    y -= LBL_H + GAP
    _prov_field_refs["ollama_url"] = _tf(y - TF_H, "http://localhost:11434",
        _pc.get("ollama", "base_url", "http://localhost:11434"))
    y -= TF_H + 10
    cv.addSubview_(_sep_line(MARGIN, y, FW, pin="top"))
    y -= 8

    # ── ANTHROPIC ─────────────────────────────────────────────────────────────
    _dot_lbl(y - LBL_H, "anthropic", "ANTHROPIC")
    y -= LBL_H + GAP
    _prov_field_refs["anthropic_key"] = _tf(y - TF_H, "sk-ant-api...",
        _pc.get("anthropic", "api_key"))
    y -= TF_H + 10
    cv.addSubview_(_sep_line(MARGIN, y, FW, pin="top"))
    y -= 8

    # ── OPENAI ────────────────────────────────────────────────────────────────
    _dot_lbl(y - LBL_H, "openai", "OPENAI")
    y -= LBL_H + GAP
    _prov_field_refs["openai_key"] = _tf(y - TF_H, "sk-proj-...",
        _pc.get("openai", "api_key"))
    y -= TF_H + GAP
    _prov_field_refs["openai_base"] = _tf(y - TF_H, "https://api.openai.com/v1",
        _pc.get("openai", "base_url", "https://api.openai.com/v1"))
    y -= TF_H + 10
    cv.addSubview_(_sep_line(MARGIN, y, FW, pin="top"))
    y -= 8

    # ── GLM ───────────────────────────────────────────────────────────────────
    _dot_lbl(y - LBL_H, "glm", "GLM (Z.ai)")
    y -= LBL_H + GAP
    _prov_field_refs["glm_key"] = _tf(y - TF_H, "API ключ GLM",
        _pc.get("glm", "api_key"))
    y -= TF_H + GAP
    _prov_field_refs["glm_base"] = _tf(y - TF_H, "https://api.z.ai/api/paas/v4",
        _pc.get("glm", "base_url", "https://api.z.ai/api/paas/v4"))

    # ── Buttons ───────────────────────────────────────────────────────────────
    cv.addSubview_(_sep_line(MARGIN, MARGIN + BTN_H + 6, FW, pin="top"))
    BTN_W = 80
    btn_cancel = _mkbtn("[Отмена]", color=C_GREEN_DIM, size=10)
    btn_cancel.setFrame_(AppKit.NSMakeRect(MARGIN, MARGIN, BTN_W, BTN_H))
    btn_cancel.setTarget_(_btn_t)
    btn_cancel.setAction_(BtnTarget.provClose_)
    cv.addSubview_(btn_cancel)
    btn_save = _mkbtn("[Сохранить]", color=C_GREEN_BR, size=10)
    btn_save.setFrame_(AppKit.NSMakeRect(PW - MARGIN - BTN_W, MARGIN, BTN_W, BTN_H))
    btn_save.setTarget_(_btn_t)
    btn_save.setAction_(BtnTarget.provSave_)
    cv.addSubview_(btn_save)

    # ── Position to the LEFT of main window ──────────────────────────────────
    GAP_X = 6
    if _magnet_on.get("providers", False) and "providers" in _magnet_offset:
        dx, dy = _magnet_offset["providers"]
        px = int(mf.origin.x + dx)
        py = int(mf.origin.y + dy)
    elif "providers" in _magnet_free_pos and not _magnet_on.get("providers", False):
        px, py = _magnet_free_pos["providers"]
        px, py = int(px), int(py)
    else:
        px = int(mf.origin.x - PW - GAP_X)
        py = int(mf.origin.y)
        if not _magnet_on.get("providers", False):
            _magnet_free_pos["providers"] = (px, py)
        else:
            _magnet_offset["providers"] = (-PW - GAP_X, 0)
    py = min(py, int(vis.origin.y + vis.size.height) - PH)
    py = max(py, int(vis.origin.y))
    panel.setFrameOrigin_(AppKit.NSMakePoint(px, py))
    panel.makeKeyAndOrderFront_(None)

    _refresh_prov_dots()


def _refresh_prov_dots():
    """Update status dot colors based on current provider_config probe results."""
    if not _prov_dot_refs:
        return
    for pid, dot in _prov_dot_refs.items():
        status = _pc.get_status(pid)
        if status is True:
            dot.setTextColor_(C_GREEN_BR)
        elif status is False:
            dot.setTextColor_(C_REC)
        else:
            dot.setTextColor_(C_GREEN_DIM)


def update_provider_status():
    """Called from main.py when provider probe completes — refresh UI dots."""
    _refresh_prov_dots()




def restore_ready():
    """After full_default interrupt: restore ready UI without touching existing text."""
    def _():
        _st["mode"] = "ready"
        _st["post_interrupt"] = True   # [Отменить] stays in window instead of closing
        _end_processing()
        _show_target_app_header()
        _show_buttons(True)
        _refresh_scenario_colors()
        if _tv:
            _win.makeFirstResponder_(_tv)
    _main(_)


def show_processing(name: str, sc_idx: int = None, interrupt_fn=None):
    """Scenario LLM processing: header = icon + app name + EQ, hide all header buttons."""
    def _():
        global _proc_sc_idx, _proc_interrupt_fn
        _proc_sc_idx = sc_idx
        _proc_interrupt_fn = interrupt_fn
        if _proc_hover_v and interrupt_fn:
            _proc_hover_v._hover_active = False
            _proc_hover_v.setHidden_(False)
            _proc_hover_v.setNeedsDisplay_(True)
            # Keep processing indicators (icon, label, EQ) on top of the cancel overlay
            for _ov in [_app_icon_v, _proc_app_lbl, _proc_eq_v]:
                if _ov:
                    _pill.addSubview_positioned_relativeTo_(
                        _ov, AppKit.NSWindowAbove, _proc_hover_v)

        # Hide normal header elements
        if _lbl:   _lbl.setHidden_(True)
        if _wf:    _wf.setHidden_(True)
        if _cfg_hdr_btn: _cfg_hdr_btn.setHidden_(True)

        # Show app icon (already in position STS_X)
        if _app_icon_v:     _app_icon_v.setHidden_(False)

        # Show app name label next to icon, measure its width dynamically
        if _proc_app_lbl:
            _proc_app_lbl.setStringValue_(_prev_app_name)
            _proc_app_lbl.setHidden_(False)

        # Position label only; EQ stays at fixed centered slot
        _layout_header_wf()
        if _proc_eq_v:
            _eq_pulse_t = 0.0
            _proc_eq_v.setMode_(1)      # pulse mode for LLM processing
            _proc_eq_v.setCol_(C_YEL)   # yellow for LLM phase
            _layout_header_wf()
            _proc_eq_v.setHidden_(False)

        # Show scenario name to the right of EQ (in square brackets)
        if _proc_sc_lbl:
            _proc_sc_lbl.setStringValue_(f"[{name}]" if name else "")
            _proc_sc_lbl.setHidden_(not name)

        _st["sc_picker"] = False   # close picker during processing
        _show_buttons(False)       # hide bottom row entirely during processing
        _refresh_scenario_colors()
        _start_timer()
    _main(_)


def hide_silent():
    """Hide only the silent strip (double-tap cancel). Does NOT touch the main overlay."""
    def _():
        global _silent_mode, _silent_wf, _silent_eq_v, _silent_hover_v, _silent_win
        global _silent_text_v, _silent_scroll_v, _silent_block_count, _eq_countdown_start
        global _silent_strip_win_h, _silent_sep_y
        _silent_mode        = False
        _silent_wf          = None
        _silent_eq_v        = None
        _silent_hover_v     = None
        _silent_text_v      = None
        _silent_scroll_v    = None
        _silent_block_count = 0
        _silent_strip_win_h = 0
        _silent_sep_y       = 0
        _eq_countdown_start = 0.0
        if _silent_win:
            _silent_win.orderOut_(None)
            _silent_win.close()
            _silent_win = None
    _main(_)


def hide(force: bool = False):
    """Close the session.
    force=True  — explicit user action (×, Escape): closes in any mode.
    force=False — background/auto close: skipped in history_open mode.
    """
    def _():
        global _expanded, _font_size_saved, _silent_mode, _silent_interrupt_fn
        global _silent_wf, _silent_eq_v, _silent_hover_v, _silent_win, _silent_text_v
        global _silent_scroll_v, _silent_block_count, _silent_strip_win_h, _silent_sep_y
        import time as _t, traceback as _tb
        _caller = "".join(_tb.format_stack()[-4:-1]).replace('\n',' ')[-200:]
        with open("/tmp/vi_debug.log","a") as f: f.write(f"[{_t.strftime('%H:%M:%S')}] hide(force={force}): mode={_st.get('mode')} | {_caller}\n")
        if not force and _st["mode"] == "history_open":
            return
        # Kill any running transcription subprocess
        try:
            import transcriber as _tc
            _tc.cancel()
        except Exception:
            pass
        _st["mode"]          = "idle"
        _silent_mode         = False
        _silent_interrupt_fn = None
        _silent_wf           = None
        _silent_eq_v         = None
        _silent_hover_v      = None
        _silent_text_v       = None
        _silent_scroll_v     = None
        _silent_block_count  = 0
        _silent_strip_win_h  = 0
        _silent_sep_y        = 0
        global _eq_countdown_start
        _eq_countdown_start  = 0.0
        global _silent_saved_cx, _silent_saved_sy
        # Save current pill/card position to disk BEFORE closing
        if _silent_win:
            _silent_save_pos(_silent_win)
            fr = _silent_win.frame()
            _silent_saved_cx = fr.origin.x + fr.size.width / 2
            _silent_saved_sy = fr.origin.y
        else:
            _silent_saved_cx = None
            _silent_saved_sy = None
        _end_processing()
        if _on_session_end_cb:
            _on_session_end_cb()
        if _silent_win:
            _silent_win.orderOut_(None)
            _silent_win.close()
            _silent_win = None
        _st["text"]    = ""
        _st["is_md"]   = False
        _st["md_mode"] = False
        _stop_timer()
        _remove_all_rich_blocks()
        _clear_waveform()
        _update_md_indicator()
        # Collapse if expanded (no animation — window is about to hide)
        if _expanded:
            _expanded = False
            if _font_size_saved is not None:
                _st["font_size"] = _font_size_saved
            _do_win_resize(H, W, animate=False)
            _relayout_buttons(W)
            if _tv:
                _tv.setFont_(_mono(_st["font_size"]))
            if _expand_btn:
                _expand_btn.setAttributedTitle_(_atitle("[□]", size=12, color=C_GREEN_DIM))
        if _tv:
            _tv.setEditable_(True)
            _tv.setString_("")
        _show_buttons(False)
        _hide_target_app_header()    # restore _lbl, hide icon+name
        if _lbl:
            _lbl.setStringValue_(_T("idle"))
            _lbl.setTextColor_(C_IDLE)
        if _hist_panel and _hist_panel.isVisible():
            _hist_panel.orderOut_(None)
        _close_editor_now()
        _close_cfg_panel()
        _close_providers_panel()
        _win_save_pos()
        _win.orderOut_(None)
        AppKit.NSApp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    _main(_)


def get_text() -> str:
    """Return session text for scenarios: blocks + _tv, falling back to _st['text']."""
    combined = _get_all_text()
    return combined or _st.get("text", "")


def get_block_texts() -> list:
    """Return source texts of all current blocks (for session upsert)."""
    result = []
    for b in _rich_blocks:
        if b._md_mode and b._inner_tv:
            text = str(b._inner_tv.string()).strip()
        else:
            text = (b._md_text or (str(b._inner_tv.string()) if b._inner_tv else "")).strip()
        if text:
            result.append(text)
    return result


def get_block_hist_ids() -> list:
    """Return list of hist_ids for all current blocks."""
    return [b._hist_id for b in _rich_blocks if b._hist_id]


def get_block_hist_data() -> list:
    """Return [{id, text}] for all blocks — used by main.py to build a session entry."""
    result = []
    for b in _rich_blocks:
        if b._md_mode and b._inner_tv:
            text = str(b._inner_tv.string()).strip()
        else:
            text = (b._md_text or (str(b._inner_tv.string()) if b._inner_tv else "")).strip()
        if text:
            result.append({"id": b._hist_id, "text": text})
    return result


def get_rich_state():
    """Return (fmt, rich_mode, rich_attrs) — always None now (blocks are self-contained)."""
    return (None, False, None)

# ── Text animation ────────────────────────────────────────────────────────────

def _animate_text(old_text: str, new_text: str):
    """Type new_text character by character after old_text, with typewriter sounds."""
    prefix = (old_text.rstrip() + "\n") if old_text else ""

    for i in range(len(new_text)):
        # Stop animation if session was closed
        if _st["mode"] == "idle":
            return

        current = prefix + new_text[:i + 1]
        char    = new_text[i]

        def ui_upd(s=current, c=char):
            if _tv and _st["mode"] in ("ready",):
                _tv.setString_(s)
                ln = len(s)
                _tv.setSelectedRange_(AppKit.NSMakeRange(ln, 0))   # cursor at end
                _tv.scrollRangeToVisible_(AppKit.NSMakeRange(ln, 0))
            if c not in (' ', '\n', '\t'):
                _click()

        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(ui_upd)

        if char in '.!?':
            time.sleep(0.055)
        elif char in ',;:':
            time.sleep(0.035)
        elif char == ' ':
            time.sleep(0.022)
        else:
            time.sleep(0.013)

    # Final state: convert text to a block, leave cursor on blank line below
    final = prefix + new_text
    def ui_final():
        if _tv:
            display = final + '\n'
            _tv.setString_(display)
            ln = len(display)
            _tv.setSelectedRange_(AppKit.NSMakeRange(ln, 0))
            _tv.scrollRangeToVisible_(AppKit.NSMakeRange(ln, 0))
        _relayout_doc_view()
        _finalize_tv_to_block()     # convert text → block, focus → _tv at pos 0
        _main(_update_cursor_pos)   # deferred: let AppKit settle layout first
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(ui_final)

# ── Timer: waveform redraw ────────────────────────────────────────────────────

class _TimerTarget(AppKit.NSObject):
    def tick_(self, t):
        import time as _t_mod
        global _eq_t, _eq_dir, _eq_pulse_t, _wf_t, _eq_countdown_t
        _wf_t = (_wf_t + 0.05) % (math.pi * 20)   # advance idle wave phase
        if _wf:
            _wf.setNeedsDisplay_(True)
        if _silent_wf and _silent_mode and not _silent_wf.isHidden():
            _silent_wf.setNeedsDisplay_(True)
        # advance equalizer animation
        if _silent_mode and _silent_eq_v and not _silent_eq_v.isHidden():
            m = _silent_eq_v._mode
            if m == 0:
                # scan: _eq_t bounces 0→1→0
                _eq_t += _eq_dir * 0.045
                if _eq_t >= 1.0:
                    _eq_t = 1.0; _eq_dir = -1
                elif _eq_t <= 0.0:
                    _eq_t = 0.0; _eq_dir = 1
            elif m == 1:
                # pulse: _eq_pulse_t cycles 0→1 repeatedly
                _eq_pulse_t = (_eq_pulse_t + 0.038) % 1.0
            else:
                # countdown: advance fill based on real elapsed time
                if _eq_countdown_start > 0 and _eq_countdown_dur > 0:
                    elapsed = _t_mod.time() - _eq_countdown_start
                    _eq_countdown_t = min(1.0, elapsed / _eq_countdown_dur)
            _silent_eq_v.setNeedsDisplay_(True)
        # main-window EQ: scan during transcription, pulse during LLM processing
        if _proc_eq_v and not _proc_eq_v.isHidden():
            if _proc_eq_v._mode == 0:
                _eq_t += _eq_dir * 0.045
                if _eq_t >= 1.0:
                    _eq_t = 1.0; _eq_dir = -1
                elif _eq_t <= 0.0:
                    _eq_t = 0.0; _eq_dir = 1
            else:
                _eq_pulse_t = (_eq_pulse_t + 0.038) % 1.0
            _proc_eq_v.setNeedsDisplay_(True)

_timer_target = None

def _start_timer():
    global _wf_timer, _timer_target
    _stop_timer()
    _timer_target = _TimerTarget.alloc().init()
    _wf_timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.05, _timer_target, _TimerTarget.tick_, None, True
    )

def _stop_timer():
    global _wf_timer
    if _wf_timer:
        _wf_timer.invalidate()
        _wf_timer = None
