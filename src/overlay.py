"""
Голосовой ввод — зелёный терминальный интерфейс.
Окно сессии: открывается по нажатию хоткея, накапливает текст, закрывается по [×] или PASTE.
Ветка: ui/terminal-green
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

# ── Сценарии ───────────────────────────────────────────────────────────────────

SCENARIOS_FILE = os.path.expanduser("~/.config/hush/scenarios.json")

def _html_to_md(html: str) -> str:
    h = _html2text.HTML2Text()
    h.ignore_links    = False
    h.ignore_images   = True
    h.body_width      = 0       # без переноса строк
    h.unicode_snob    = True
    h.protect_links   = False
    h.mark_code       = True
    return h.handle(html).strip()


_C_YELLOW = None  # ленивая инициализация, чтобы не вызывать _rgba слишком рано
def _md_yellow():
    global _C_YELLOW
    if _C_YELLOW is None:
        _C_YELLOW = _rgba(1.0, 1.0, 0.33)
    return _C_YELLOW

def _md_to_raw_attrs(md_text: str):
    """Подсветка синтаксиса сырого Markdown-источника. Возвращает NSMutableAttributedString."""
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
    """Рендеринг Markdown в NSAttributedString с цветами текущей темы."""
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
            # initWithHTML всегда добавляет \n в конце — убираем, чтобы блоки не получали лишнюю пустую строку
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
    """Быстрая проверка доступности провайдера по строке provider:model. Выполняется в фоновом потоке."""
    if not model_str:
        return True
    provider, _, model_name = model_str.partition(":")
    provider = provider.lower()
    if provider == "ollama":
        try:
            base = _pc.get("ollama", "base_url", "http://localhost:11434").rstrip("/")
            req = urllib.request.Request(f"{base}/api/tags")
            with urllib.request.urlopen(req, timeout=2) as r:
                data = json.loads(r.read())
            names = [m["name"] for m in data.get("models", [])]
            return any(n == model_name or n.startswith(model_name + ":") for n in names)
        except Exception:
            return False
    # Облачные провайдеры: используем статус от последнего probe (реальная HTTP-проверка ключа)
    status = _pc.get_status(provider)
    if status is None:
        return True   # ещё не проверяли — не красим
    return bool(status)


# ── Сохранение настроек ────────────────────────────────────────────────────────

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
    """Стабильный идентификатор текущей конфигурации экранов (разрешение + количество)."""
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
        "btn_close":     "[ЗАКРЫТЬ]",
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
        "btn_copy":       "[КОПИРОВАТЬ]",
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
        "menu_open":    "Открыть HUSH",
        "menu_about":   "О приложении",
        "menu_login":   "Запускать при входе в систему",
        "menu_quit":    "Завершить HUSH",
        "menu_tooltip": "HUSH — голосовой ввод",
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
        "btn_close":     "[CLOSE]",
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
        "btn_copy":       "[COPY]",
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
        "menu_open":    "Open HUSH",
        "menu_about":   "About HUSH",
        "menu_login":   "Launch at Login",
        "menu_quit":    "Quit HUSH",
        "menu_tooltip": "HUSH — voice input",
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
        "btn_close":     "[CERRAR]",
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
        "btn_copy":       "[COPIAR]",
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
        "menu_open":    "Abrir HUSH",
        "menu_about":   "Acerca de HUSH",
        "menu_login":   "Iniciar al arrancar",
        "menu_quit":    "Salir de HUSH",
        "menu_tooltip": "HUSH — entrada de voz",
    },
}

LANGS = ["ru", "en", "es"]   # порядок в панели настроек (сверху вниз)


def _detect_system_lang() -> str:
    """Сопоставить предпочтительный язык macOS с одним из поддерживаемых LANGS."""
    try:
        pref = AppKit.NSLocale.preferredLanguages()
        if pref:
            code = str(pref[0]).split("-")[0].lower()
            if code == "ru":
                return "ru"
            if code in ("es", "ca", "gl", "eu"):
                return "es"
    except Exception:
        pass
    return "en"


def _T(key: str) -> str:
    return STRINGS.get(_st.get("lang", "ru"), STRINGS["ru"]).get(key, key)

def _sc_label(sc: dict) -> str:
    """Вернуть метку сценария на текущем языке (EN как запасной вариант), максимум 6 символов."""
    return _sc_label_for(sc, _st.get("lang", "ru"))

def _refresh_status_label():
    """Синхронизировать метку статуса главного окна и подписи кнопок HIST/CFG с текущим языком."""
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

# ── Состояние ──────────────────────────────────────────────────────────────────

_cfg_saved = _load_settings()

def _silent_default_pos(win_w: int, win_h: int):
    """Позиция по умолчанию: по центру горизонтально, 6% от нижнего края видимой области."""
    scr = AppKit.NSScreen.mainScreen()
    vis = scr.visibleFrame()
    sx = int(vis.origin.x + (vis.size.width - win_w) / 2)
    sy = int(vis.origin.y + max(20, int(vis.size.height * 0.06)))
    return sx, sy

def _silent_load_pos(win_w: int, win_h: int):
    """Загрузить сохранённую позицию для текущей конфигурации экранов; откат на позицию по умолчанию, если выходит за границы."""
    key = _screen_key()
    saved = _cfg_saved.get("silent_pos", {}).get(key)
    if saved:
        cx, sy = saved["cx"], saved["sy"]
        scr = AppKit.NSScreen.mainScreen()
        vis = scr.visibleFrame()
        sx = int(cx - win_w / 2)
        # Проверка: окно должно помещаться в видимой области
        in_x = vis.origin.x <= sx and sx + win_w <= vis.origin.x + vis.size.width
        in_y = vis.origin.y <= sy and sy + win_h <= vis.origin.y + vis.size.height
        if in_x and in_y:
            return sx, int(sy)
    return _silent_default_pos(win_w, win_h)

def _silent_save_pos(win):
    """Сохранить центр X и нижнюю Y текущего окна для данной конфигурации экранов."""
    fr  = win.frame()
    cx  = fr.origin.x + fr.size.width / 2
    sy  = fr.origin.y
    key = _screen_key()
    pos = _cfg_saved.setdefault("silent_pos", {})
    pos[key] = {"cx": cx, "sy": sy}
    _save_settings()


def _win_default_pos():
    """Позиция главного окна по умолчанию: правый верхний угол основного экрана."""
    scr = AppKit.NSScreen.mainScreen().frame()
    x   = scr.origin.x + scr.size.width - W - 20
    y   = scr.origin.y + scr.size.height - H - 60
    return int(x), int(y)

def _win_load_pos():
    """Загрузить сохранённую позицию главного окна; проверить на всех экранах; откат на позицию по умолчанию."""
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
    """Сохранить начало координат главного окна (в свёрнутом размере) в настройки."""
    if not _win:
        return
    fr  = _win.frame()
    key = _screen_key()
    _cfg_saved.setdefault("win_pos", {})[key] = {"x": fr.origin.x, "y": fr.origin.y}
    _save_settings()


# ── Система магнитных окон ─────────────────────────────────────────────────────

_PANEL_GAP = 10

def _repel_from_others(panel):
    """Оттолкнуть панель от перекрывающихся окон (защита от наложений)."""
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


_MAGNET_KEYS    = ["cfg", "hist", "editor", "providers"]  # tag=индекс
_MAGNET_DEFAULT = {"cfg": True, "hist": True, "editor": True, "providers": True}
_magnet_on      = dict(_MAGNET_DEFAULT)
_magnet_offset  = {}   # {key: (dx, dy)} от начала координат _win когда магнит включён
_magnet_free_pos = {}  # {key: (x, y)} сохранённая свободная позиция когда магнит выключен
_snap_ts        = {}   # {key: monotonic()} — время последней привязки (защита от осцилляций)
_magnet_btns    = {}   # {key: NSButton} для обновления интерфейса

# ── Режим кластера (расширенное окно) ─────────────────────────────────────────
_cluster_mode     = False   # True пока главное окно в развёрнутом состоянии
_cluster_offsets  = {}      # {key: (dx, dy)} от начала координат _cfg_panel
_cluster_was_open = set()   # ключи панелей, видимых ДО разворачивания (для восстановления при сворачивании)
_cluster_cfg_auto = False   # True если cfg была открыта автоматически как якорь кластера


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
    """Вернуть сторону ("left","right","top","bottom"), на которой находится примагниченная панель."""
    if key not in _magnet_offset:
        return None
    dx, dy = _magnet_offset[key]
    if abs(dx) >= abs(dy):
        return "left" if dx <= 0 else "right"
    return "top" if dy >= 0 else "bottom"


_OPP = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}


def _all_screens_bounds():
    """Объединение видимых областей всех подключённых экранов → (x, y, x2, y2).
    Используется ТОЛЬКО для ограничения перетаскивания главного окна между экранами."""
    scrs = AppKit.NSScreen.screens() or [AppKit.NSScreen.mainScreen()]
    x  = min(int(s.visibleFrame().origin.x) for s in scrs)
    y  = min(int(s.visibleFrame().origin.y) for s in scrs)
    x2 = max(int(s.visibleFrame().origin.x + s.visibleFrame().size.width)  for s in scrs)
    y2 = max(int(s.visibleFrame().origin.y + s.visibleFrame().size.height) for s in scrs)
    return x, y, x2, y2


def _screen_bounds_at(cx, cy):
    """Видимые границы единственного экрана, содержащего точку (cx, cy).
    Панели используют это — их мир ограничен текущим экраном, а не объединением всех.
    Откат: экран с наибольшим перекрытием, затем mainScreen."""
    for s in (AppKit.NSScreen.screens() or [AppKit.NSScreen.mainScreen()]):
        f = s.visibleFrame()
        if (f.origin.x <= cx < f.origin.x + f.size.width and
                f.origin.y <= cy < f.origin.y + f.size.height):
            return (int(f.origin.x), int(f.origin.y),
                    int(f.origin.x + f.size.width), int(f.origin.y + f.size.height))
    # Центр находится между экранами — macOS выберет экран с наибольшим перекрытием
    win = globals().get("_win")
    s = (win.screen() if win else None) or AppKit.NSScreen.mainScreen()
    f = s.visibleFrame()
    return (int(f.origin.x), int(f.origin.y),
            int(f.origin.x + f.size.width), int(f.origin.y + f.size.height))


_MAGNET_PANEL_GLOBALS = {
    "cfg":       "_cfg_panel",
    "hist":      "_hist_panel",
    "editor":    "_sc_editor_panel",
    "providers": "_prov_panel",
}

# ── Сеточное размещение панелей ──────────────────────────────────────────────
#
# Каждая ячейка идентифицируется по (col, row) относительно главного окна:
#   col=0, row=0  — позиция самого главного окна (всегда зарезервирована)
#   col < 0  → левые колонки;   col > 0  → правые колонки
#   row > 0  → строки выше;     row < 0  → строки ниже
#
# Предпочтительные ячейки по умолчанию:
#   cfg       → (0, +1)  выше по центру
#   hist      → (0, -1)  ниже по центру
#   providers → (-1, 0)  слева
#   editor    → (+1, 0)  справа
#
# Когда предпочтительная ячейка занята или выходит за экран, назначаем ближайшую
# свободную ячейку по евклидову расстоянию от (pref_col, pref_row). Без наложений,
# без выхода за границы экрана, полностью адаптивно к любому разрешению / ориентации.

_PANEL_PREF_CELL = {
    "cfg":       (0,  1),
    "hist":      (0, -1),
    "providers": (-1, 0),
    "editor":    ( 1, 0),
}


def _cell_to_pos(col, row, wx, wy, ww, wh, pw, ph):
    """Абсолютные координаты (nx, ny) ячейки сетки (col, row) относительно главного окна."""
    G = _SNAP_GAP
    if col == 0:
        nx = wx
    elif col < 0:
        nx = wx + col * (pw + G)           # col=-1 → wx-(pw+G)
    else:
        nx = wx + ww + G + (col - 1) * (pw + G)  # col=+1 → wx+ww+G
    if row == 0:
        ny = wy
    elif row < 0:
        ny = wy + row * (ph + G)           # row=-1 → wy-(ph+G)
    else:
        ny = wy + wh + G + (row - 1) * (ph + G)  # row=+1 → wy+wh+G
    return int(nx), int(ny)


def _pos_to_cell(nx, ny, wx, wy, ww, wh, pw, ph):
    """Ближайшая ячейка сетки (col, row) для абсолютной позиции (nx, ny)."""
    G = _SNAP_GAP
    half_cw = (pw + G) / 2
    half_ch = (ph + G) / 2
    # Горизонталь
    if abs(nx - wx) < half_cw:
        col = 0
    elif nx < wx:
        col = -max(1, round((wx - nx) / (pw + G)))
    else:
        col = max(1, round((nx - wx - ww - G) / (pw + G)) + 1)
    # Вертикаль
    if abs(ny - wy) < half_ch:
        row = 0
    elif ny < wy:
        row = -max(1, round((wy - ny) / (ph + G)))
    else:
        row = max(1, round((ny - wy - wh - G) / (ph + G)) + 1)
    return col, row


def _valid_grid_cells(wx, wy, ww, wh, pw, ph, vx, vy, vx2, vy2):
    """Все ячейки (col, row) вокруг главного окна.

    Всегда включает минимум 1 ячейку с каждой стороны, чтобы панели можно было
    разместить — даже если экран слишком мал для идеального крестообразного расположения.
    Ячейки, не помещающиеся полностью на экране, тоже возвращаются; _assign_cell
    оценивает их по степени выхода за границы, поэтому экранные ячейки выигрывают первыми.
    """
    G = _SNAP_GAP
    max_left  = max(1, int((wx - vx        - G) // (pw + G)))
    max_right = max(1, int((vx2 - wx - ww  - G) // (pw + G)))
    max_above = max(1, int((vy2 - wy - wh  - G) // (ph + G)))
    max_below = max(1, int((wy - vy        - G) // (ph + G)))
    cells = []
    for col in range(-max_left, max_right + 1):
        for row in range(-max_below, max_above + 1):
            if col == 0 and row == 0:
                continue
            cells.append((col, row))
    return cells


def _occupied_cells(excl_key, wx, wy, ww, wh, pw, ph):
    """Множество ячеек (col, row), занятых видимыми магнитными панелями (excl_key исключён)."""
    occ = set()
    for k in _MAGNET_KEYS:
        if k == excl_key or not _magnet_on.get(k, False) or k not in _magnet_offset:
            continue
        p = globals().get(_MAGNET_PANEL_GLOBALS.get(k, ""))
        if p is None or not p.isVisible():
            continue
        dx, dy = _magnet_offset[k]
        occ.add(_pos_to_cell(wx + dx, wy + dy, wx, wy, ww, wh, pw, ph))
    return occ


def _assign_cell(key, wx, wy, ww, wh, pw, ph, vx, vy, vx2, vy2):
    """Найти лучшую свободную ячейку для `key`. Возвращает (col, row, nx, ny) или None.

    Оценка (меньше = лучше):
      0. axis_group  — предпочитать ту же ось, что у pref (горизонтальная панель остаётся
                       в row=0; вертикальная — в col=0). Ячейки поперечной оси — крайний случай.
      1. off_screen  — суммарные пиксели, на которые панель выходит за границы экрана.
                       Полностью экранные ячейки всегда выигрывают у обрезанных.
      2. dist        — квадрат евклидова расстояния от предпочтительной ячейки (разбивка ничьих).
    """
    if key in _magnet_offset:
        dx, dy = _magnet_offset[key]
        pref = _pos_to_cell(wx + dx, wy + dy, wx, wy, ww, wh, pw, ph)
    else:
        pref = _PANEL_PREF_CELL.get(key, (0, -1))

    valid    = _valid_grid_cells(wx, wy, ww, wh, pw, ph, vx, vy, vx2, vy2)
    occupied = _occupied_cells(key, wx, wy, ww, wh, pw, ph)
    free     = [c for c in valid if c not in occupied]

    if not free:
        return None

    pc, pr = pref

    def _score(c):
        col, row = c
        nx, ny = _cell_to_pos(col, row, wx, wy, ww, wh, pw, ph)
        # Пиксели за пределами экрана
        off = (max(0, vx - nx) + max(0, nx + pw - vx2) +
               max(0, vy - ny) + max(0, ny + ph - vy2))
        # Штраф за ось: поперечные ячейки стоят ~1/4 размера панели.
        # Если ячейка по оси выходит больше этого, поперечная ось выигрывает.
        if pr == 0 and pc != 0:           # pref горизонтальная (левая/правая строка)
            axis_pen = 0 if row == 0 else pw // 4
        elif pc == 0 and pr != 0:         # pref вертикальная (строка выше/ниже)
            axis_pen = 0 if col == 0 else ph // 4
        else:
            axis_pen = 0
        dist = (col - pc) ** 2 + (row - pr) ** 2
        return (off + axis_pen, dist)

    best = min(free, key=_score)
    nx, ny = _cell_to_pos(best[0], best[1], wx, wy, ww, wh, pw, ph)
    return best[0], best[1], nx, ny


# Оставлено для вызовов, которые ещё передают perp (теперь игнорируется — все панели одной высоты)
def _first_free_slot_on_side(side, excl_key, wx, wy, ww, wh, pw, ph, perp=0):
    """Устаревшая заглушка: возвращает первый свободный слот на `side` с использованием сеточной системы."""
    G = _SNAP_GAP
    vx, vy, vx2, vy2 = _screen_bounds_at(wx + ww / 2, wy + wh / 2)
    result = _assign_cell(excl_key, wx, wy, ww, wh, pw, ph, vx, vy, vx2, vy2)
    if result is None:
        # Абсолютный откат: рядом с предпочтительной стороной, с зажимом
        if side == "top":    return wx, wy + wh + G
        if side == "bottom": return wx, wy - ph - G
        if side == "right":  return wx + ww + G, wy
        return wx - pw - G, wy
    _, _, nx, ny = result
    return nx, ny


def _snap_attached_panels_live(new_wx, new_wy):
    """При перетаскивании главного окна: переназначить каждую видимую магнитную панель
    в лучшую свободную ячейку сетки на текущем экране. Использует ячеечную сетку, чтобы
    панели никогда не перекрывались и всегда помещались в границах экрана."""
    if _cluster_mode:
        return   # панели собраны вокруг cfg, а не прикреплены к _win
    win = globals().get("_win")
    if not win:
        return
    mf = win.frame()
    ww, wh = mf.size.width, mf.size.height
    vx, vy, vx2, vy2 = _screen_bounds_at(new_wx + ww / 2, new_wy + wh / 2)
    MARGIN = 20
    import time as _time
    now = _time.monotonic()
    _SNAP_COOLDOWN_S = 0.4

    # Собрать панели, выходящие за экран при новых new_wx/new_wy
    candidates = []
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
        # Выход за экран по любой оси
        off = (px < vx - MARGIN or px + pw > vx2 + MARGIN or
               py < vy - MARGIN or py + ph > vy2 + MARGIN)
        if not off:
            continue
        dist = abs(dx) + abs(dy)
        candidates.append((dist, key, pw, ph))

    # Переназначать начиная с ближайших, чтобы более близкие панели первыми занимали предпочтительные ячейки
    candidates.sort()
    for _, key, pw, ph in candidates:
        if now - _snap_ts.get(key, 0.0) < _SNAP_COOLDOWN_S:
            continue
        result = _assign_cell(key, new_wx, new_wy, ww, wh, pw, ph, vx, vy, vx2, vy2)
        if result is None:
            continue
        _col, _row, nx, ny = result
        _snap_ts[key] = now
        _magnet_offset[key] = (nx - new_wx, ny - new_wy)


def _smart_snap_panel(key, panel):
    """По mouseUp: обработать позицию панели после перетаскивания.

    Магнитные панели (магнит ВКЛ): если вышла за экран, переназначить в ближайшую свободную
    ячейку сетки без наложений.
    Свободные панели (магнит ВЫКЛ): только зажать в экране — без назначения в ячейку,
    пользователь разместил её намеренно.
    """
    win = globals().get("_win")
    if not win or not panel:
        return
    pf = panel.frame()
    mf = win.frame()
    px, py = int(pf.origin.x), int(pf.origin.y)
    pw, ph = int(pf.size.width), int(pf.size.height)
    wx, wy = int(mf.origin.x), int(mf.origin.y)
    ww, wh = int(mf.size.width), int(mf.size.height)
    vx, vy, vx2, vy2 = _screen_bounds_at(wx + ww / 2, wy + wh / 2)
    MARGIN = 20

    off = (px < vx - MARGIN or px + pw > vx2 + MARGIN or
           py < vy - MARGIN or py + ph > vy2 + MARGIN)
    if not off:
        return

    if not _magnet_on.get(key, False):
        # Свободная панель: только зажать в экране, уважаем позицию пользователя
        nx = max(vx, min(px, vx2 - pw))
        ny = max(vy, min(py, vy2 - ph))
        _magnet_free_pos[key] = (nx, ny)
        panel.setFrameOrigin_(AppKit.NSMakePoint(nx, ny))
        _magnet_save()
        return

    # Магнитная панель: найти ближайшую свободную ячейку сетки
    result = _assign_cell(key, wx, wy, ww, wh, pw, ph, vx, vy, vx2, vy2)
    if result is None:
        return
    _col, _row, nx, ny = result
    nx = max(vx, min(nx, vx2 - pw))
    ny = max(vy, min(ny, vy2 - ph))
    _magnet_offset[key] = (nx - wx, ny - wy)
    panel.setFrameOrigin_(AppKit.NSMakePoint(nx, ny))
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
    # Привязать панель к противоположной стороне, если вышла за экран
    try:
        _smart_snap_panel(key, panel)
    except Exception:
        pass

def _calc_panel_pos(key, wx, wy, ww, wh, pw, ph):
    """Вернуть (px, py) для размещения панели.

    Магнитные панели: вызывает _assign_cell, который всегда возвращает ячейку
    (_valid_grid_cells теперь гарантирует ≥1 ячейку с каждой стороны). Панели могут
    немного выходить за экран — это лучше, чем перекрываться друг с другом.

    Свободные панели (магнит ВЫКЛ): используют сохранённый _magnet_free_pos, зажатый в экране.
    """
    vx, vy, vx2, vy2 = _screen_bounds_at(wx + ww / 2, wy + wh / 2)
    if _magnet_on.get(key, True):
        result = _assign_cell(key, wx, wy, ww, wh, pw, ph, vx, vy, vx2, vy2)
        if result:
            _, _, nx, ny = result
            _magnet_offset[key] = (nx - wx, ny - wy)
            return int(nx), int(ny)
        # Крайне редкий случай (все ячейки заняты другими панелями этого ключа?)
        # Просто вернуть текущее сохранённое смещение, зажатое в экране
        dx, dy = _magnet_offset.get(key, (0, 0))
        return int(max(vx, min(wx + dx, vx2 - pw))), int(max(vy, min(wy + dy, vy2 - ph)))
    else:
        if key in _magnet_free_pos:
            px, py = _magnet_free_pos[key]
            px = max(vx, min(int(px), vx2 - pw))
            py = max(vy, min(int(py), vy2 - ph))
        else:
            # Первое размещение в свободном режиме: попробовать через сетку, сохранить как свободную позицию
            result = _assign_cell(key, wx, wy, ww, wh, pw, ph, vx, vy, vx2, vy2)
            if result:
                _, _, px, py = result
            else:
                px, py = int(wx), int(wy)
            _magnet_free_pos[key] = (px, py)
        return int(px), int(py)


def _mkmagnet_btn(key, cv, x, y, w=22, h=22):
    is_on = _magnet_on.get(key, False)
    btn   = _mkbtn("🧲", color=AppKit.NSColor.whiteColor(), size=13)
    btn.setFrame_(AppKit.NSMakeRect(x, y, w, h))
    btn.setAlphaValue_(1.0 if is_on else 0.35)
    btn.setTag_(_MAGNET_KEYS.index(key))
    btn.setTarget_(_btn_t)
    btn.setAction_(BtnTarget.panelMagnet_)
    btn.setToolTip_("Прикрепить к главному окну" if not is_on else "Открепить от главного окна")
    cv.addSubview_(btn)
    _magnet_btns[key] = btn
    return btn


_WIN_ALPHA = 1.0    # pill (silent mode) is always fully opaque

_st = {
    "mode":          "idle",   # idle | recording | transcribing | ready | history_open
    "text":          "",       # накопленный текст сессии
    "opacity":       _cfg_saved.get("opacity",   0.88),  # прозрачность развёрнутого окна
    "font_size":     _cfg_saved.get("font_size", 13.0),
    "lang":          _cfg_saved.get("lang",      _detect_system_lang()),
    "theme":         _cfg_saved.get("theme",     "emerald"),
    "scenarios":     load_scenarios(),
    "active_sc":     None,     # индекс текущего применённого сценария, или None
    "is_md":         False,    # True если текущий текст похож на Markdown
    "md_mode":       False,    # True = отображение в терминальном виде, False = исходный текст
    "rich_fmt":      None,     # None | "rtf" | "html" — устанавливается при вставке форматированного текста
    "rich_mode":     False,    # True = отображение оригинального форматирования в текстовом поле
    "rich_attrs":    None,     # снимок NSAttributedString из буфера обмена (при форматированной вставке)
    "_silent_sc_idx_legacy": _cfg_saved.get("silent_sc_idx"),  # только для миграции, не используется
}

_on_scenario_cb        = None  # (scenario_dict, idx) → None
_on_history_cb         = None  # () → [dict, ...]
_on_paste_cb           = None  # () → None  (Shift+Enter / [↵])
_on_copy_cb            = None  # () → None  (Ctrl+Enter — копировать в буфер, без вставки)
_on_history_delete_cb  = None  # ([str]) → None  (удалить по UUID)
_on_history_load_cb    = None  # (item_id_or_None) → None  (элемент загружен в редактор)
_on_history_merge_cb   = None  # (text: str, source_ids: list) → str  (объединить и удалить источники)

_hist_ctrl = None  # сильная ссылка на _HistCtrl — предотвращает GC пока панель открыта

# ── Волновая форма ─────────────────────────────────────────────────────────────

_wf_lock  = threading.Lock()
_WF_N     = 24
_wf_bars  = [0.0] * _WF_N   # текущая высота отображения (сглаженная)
_wf_peaks = [0.0] * _WF_N   # удержание пика на столбец
_wf_t     = 0.0              # фаза анимации в состоянии ожидания (увеличивается таймером)

def update_waveform(chunk_float32):
    """Вызывается из аудио-коллбэка с сырым PCM float32."""
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
            # Быстрая атака, медленный гравитационный спад
            _wf_bars[i]  = nv if nv > ov else ov * 0.60
            # Удержание пика: мгновенный подъём, медленное падение
            if nv >= _wf_peaks[i]:
                _wf_peaks[i] = nv
            else:
                _wf_peaks[i] = max(0.0, _wf_peaks[i] - 0.018)

def _clear_waveform():
    with _wf_lock:
        _wf_bars[:]  = [0.0] * _WF_N
        _wf_peaks[:] = [0.0] * _WF_N

# ── Пул звуков ─────────────────────────────────────────────────────────────────

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
    """Воспроизвести короткий щелчок печатной машинки (с ограничением частоты)."""
    global _snd_idx, _last_snd
    t = time.time()
    if t - _last_snd < 0.03 or not _snd_pool:
        return
    _last_snd = t
    s = _snd_pool[_snd_idx % len(_snd_pool)]
    _snd_idx += 1
    if not s.isPlaying():
        s.play()

# ── Цвета терминала ────────────────────────────────────────────────────────────

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

# ── Цветовые темы ─────────────────────────────────────────────────────────────
# Цветовые палитры из панели администратора Roclea (набор цветов TC терминала)
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
# (name, bg_tuple, accent_NSColor) — первые _N_LIGHT = светлые темы (верхний ряд), остальные — тёмные
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
    # Синхронизировать цвет текста _tv, чтобы ввод и setString_() всегда соответствовали теме
    tv = globals().get("_tv")
    if tv:
        tv.setTextColor_(C_TEXT)
        tv.setTypingAttributes_({
            AppKit.NSFontAttributeName:            _mono(_st.get("font_size", 12)),
            AppKit.NSForegroundColorAttributeName: C_TEXT,
        })
    # Обновить все кнопки с цветами темы — только после построения UI
    if globals().get("_pill"):
        _refresh_status_label()
        _apply_theme_to_all_windows()
    exp_btn = globals().get("_expand_btn")
    if exp_btn:
        lbl = "[─]" if globals().get("_expanded") else "[□]"
        exp_btn.setAttributedTitle_(_atitle(lbl, size=12, color=C_GREEN_DIM))
    _apply_all_panels_alpha()


def _apply_theme_to_all_windows():
    """Перерисовать / пересобрать все видимые вторичные окна для применения новых цветов C_*."""
    # Панель истории — пересобрать если открыта (использует фон TerminalView + много цветных кнопок)
    hist = globals().get("_hist_panel")
    if hist and hasattr(hist, "isVisible") and hist.isVisible():
        hist.orderOut_(None)
        hist.close()
        on_hist = globals().get("_on_history_cb")
        if on_hist:
            history = on_hist()
            _show_hist_panel(history)

    # Панель провайдеров — пересобрать если открыта (текстовые поля + кнопки с жёсткими цветами)
    prov = globals().get("_prov_panel")
    if prov and hasattr(prov, "isVisible") and prov.isVisible():
        prov.orderOut_(None)
        _toggle_providers_panel()

    # Рекурсивно перерисовать все подвиды — необходимо для панелей с глубокой иерархией видов
    def _redisplay_tree(v):
        v.setNeedsDisplay_(True)
        for sub in list(v.subviews()):
            _redisplay_tree(sub)

    # Все вторичные панели — drawRect_ читает глобальные C_*; рекурсия для вложенных видов
    for key in ("_cfg_panel", "_hist_panel", "_sc_editor_panel", "_about_panel"):
        p = globals().get(key)
        if p and hasattr(p, "isVisible") and p.isVisible():
            cv = p.contentView()
            if cv:
                _redisplay_tree(cv)

    # Окна тихого режима
    for key in ("_silent_win",):
        sw = globals().get(key)
        if sw and hasattr(sw, "contentView"):
            cv = sw.contentView()
            if cv:
                _redisplay_tree(cv)

    # Повторно отрендерить rich-блоки с новыми цветами темы
    for _b in list(globals().get("_rich_blocks", [])):
        try:
            _b._rendered = _md_to_styled_attrs(_b._md_text)
            if _b._inner_tv and not getattr(_b, "_md_mode", False):
                _b._inner_tv.textStorage().setAttributedString_(_b._rendered)
        except Exception:
            pass
    # Обновить цвета кнопок магнита
    for _mkey in list(_magnet_btns):
        _update_magnet_btn(_mkey)

    # Редактор сценариев — обновить NSTextField/NSTextView/NSButton явно (CGColor — снимок)
    _refresh_sc_editor_colors()


def _refresh_sc_editor_colors():
    """Обновить цвета интерактивных элементов редактора сценариев при смене темы."""
    refs  = globals().get("_sc_edit_refs")
    panel = globals().get("_sc_editor_panel")
    if not refs or not panel:
        return
    if not (hasattr(panel, "isVisible") and panel.isVisible()):
        return

    panel.setAppearance_(_panel_appearance())

    # NSTextField для имён (RU / EN / ES) — обновляем layer-фон и цвет текста в ячейке
    tf_bg_cg = _rgba(*C_BG).CGColor()
    bord_cg  = C_GREEN_BORD.CGColor()
    for key in ("tf_ru", "tf_en", "tf_es"):
        tf = refs.get(key)
        if not tf:
            continue
        cell = tf.cell()
        if cell:
            cell.setTextColor_(C_TEXT)
        lay = tf.layer()
        if lay:
            lay.setBackgroundColor_(tf_bg_cg)
            lay.setBorderColor_(bord_cg)
        tf.setNeedsDisplay_(True)

    # NSTextView промпта
    tv = refs.get("tv_prompt")
    if tv:
        tv.setBackgroundColor_(_rgba(*C_BG))
        tv.setTextColor_(C_TEXT)
        tv.setInsertionPointColor_(C_TEXT)
        stor = tv.textStorage()
        if stor and stor.length() > 0:
            stor.setAttributes_range_(
                {AppKit.NSFontAttributeName: _mono(9),
                 AppKit.NSForegroundColorAttributeName: C_TEXT},
                AppKit.NSMakeRange(0, stor.length()))
        tv.setNeedsDisplay_(True)
        # Обновить layer-фон обёртки (NSView-контейнер вокруг scroll view)
        sv = tv.enclosingScrollView()
        if sv:
            outer = sv.superview()
            if outer:
                lay = outer.layer()
                if lay:
                    lay.setBackgroundColor_(tf_bg_cg)
                    lay.setBorderColor_(bord_cg)

    # Кнопки-чекбоксы — атрибутированный заголовок хранит снимок цвета
    is_silent = bool(refs.get("silent", False))
    sil_btn   = refs.get("sil_btn")
    if sil_btn:
        sil_btn.setAttributedTitle_(_atitle(
            ("[✓] " if is_silent else "[ ] ") + _T("sc_silent"),
            color=C_CYAN if is_silent else C_GREEN_DIM,
            size=10, align=AppKit.NSTextAlignmentLeft))

    is_fd  = bool(refs.get("full_default", False))
    fd_btn = refs.get("fd_btn")
    if fd_btn:
        fd_btn.setAttributedTitle_(_atitle(
            ("[✓] " if is_fd else "[ ] ") + _T("sc_full_default"),
            color=C_GREEN_BR if is_fd else C_GREEN_DIM,
            size=10, align=AppKit.NSTextAlignmentLeft))


def _apply_all_panels_alpha():
    """Применить _st['opacity'] к _win и всем открытым панелям. Тихий режим (_silent_win) исключён."""
    alpha = _st.get("opacity", 0.88)
    win = globals().get("_win")
    if win:
        win.setAlphaValue_(alpha)
    for key in ("_cfg_panel", "_hist_panel", "_sc_editor_panel", "_about_panel", "_prov_panel"):
        p = globals().get(key)
        if p and hasattr(p, "isVisible") and p.isVisible():
            p.setAlphaValue_(alpha)

# Применить сохранённую тему при запуске (обновляет глобальные переменные до построения UI)
_apply_theme(_cfg_saved.get("theme", "emerald"), _save=False)

# ── Представления ──────────────────────────────────────────────────────────────

def _tf_cell_adj(cell, frame):
    """Вернуть frame, скорректированный для вертикального выравнивания + отступа слева (для NSTextFieldCell)."""
    sz = cell.cellSizeForBounds_(frame)
    dy = max(0.0, (frame.size.height - sz.height) / 2.0)
    return AppKit.NSMakeRect(
        frame.origin.x + 5,
        frame.origin.y + dy,
        frame.size.width - 10,
        sz.height)


class _CenteredTextFieldCell(AppKit.NSTextFieldCell):
    """NSTextFieldCell с вертикальным центрированием текста и левым отступом."""

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
    """NSTextView с плейсхолдером, отображаемым когда поле пустое."""
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
    """Горизонтальный слайдер в стиле терминала: зелёная дорожка + прямоугольный бегунок."""

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
        # Без заливки фона — прозрачный, наследует тёмный фон панели
        # Дорожка (тонкая линия)
        th = 2.0
        ty = (h - th) / 2
        # Незаполненная дорожка
        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.00, 0.30, 0.00, 1.0).set()
        AppKit.NSRectFill(AppKit.NSMakeRect(0, ty, w, th))
        # Бегунок
        tw = 10.0
        th2 = h * 0.65
        ty2 = (h - th2) / 2
        t   = (self._val - self._min) / max(0.001, self._max - self._min)
        tx  = t * (w - tw)
        # Заполненная дорожка
        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.10, 0.60, 0.10, 1.0).set()
        AppKit.NSRectFill(AppKit.NSMakeRect(0, ty, tx + tw / 2, 2.0))
        # Прямоугольник бегунка
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
    """Тёмный фон + CRT-сканлайны + зелёная рамка."""

    def drawRect_(self, rect):
        b    = self.bounds()
        a    = 1.0
        r, g, bb = C_BG
        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, bb, a).set()
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, 4, 4)
        path.fill()
        # CRT-сканлайны — почти невидимы на светлых темах, тонкие на тёмных
        bg_lum = (C_BG[0] + C_BG[1] + C_BG[2]) / 3
        sl_factor = 0.995 if bg_lum > 0.5 else 0.80  # light: 0.5% / dark: 20%
        sr, sg, sb = C_BG[0] * sl_factor, C_BG[1] * sl_factor, C_BG[2] * sl_factor
        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(sr, sg, sb, a).set()
        yy = 0.0
        while yy < b.size.height:
            AppKit.NSRectFill(AppKit.NSMakeRect(0, yy, b.size.width, 2))
            yy += 4
        # Рамка
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
    """Фон карточки «О приложении» — клик в любом месте (не на кнопке подвида) закрывает её."""

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
    """Кнопка-кошелёк для пожертвований.
    Изображение всегда заполняет весь frame (без сжатия).
    Белый фон — отдельный слой ПОД изображением:
      - высота фиксирована (обрезает купюры сверху ~22%, монету снизу ~8%)
      - ширина анимируется от _BG_W_CLOSED до _BG_W_OPEN при наведении
    Изображение переключается closed→open в середине анимации."""

    _VW          = 90     # ширина вида (pt)
    _VH          = 68     # высота вида (pt)
    _TOP_CROP    = 0.14   # доля обрезки сверху (область купюр)
    _BOT_CROP    = 0.02   # доля обрезки снизу (область монеты)
    _BG_W_CLOSED = 62     # ширина белого фона в закрытом состоянии (ширина корпуса кошелька)
    _BG_W_OPEN   = 90     # ширина белого фона в открытом состоянии (полная ширина вида)

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
        self._img_open  = _prescale(os.path.join(_dir, "wallet-open.png"))
        self._frac   = 0.0
        self._target = 0.0
        self._timer  = None
        return self

    def updateTrackingAreas(self):
        for a in list(self.trackingAreas()):
            self.removeTrackingArea_(a)
        opts = (AppKit.NSTrackingMouseEnteredAndExited |
                AppKit.NSTrackingCursorUpdate |
                AppKit.NSTrackingActiveAlways |
                AppKit.NSTrackingInVisibleRect)
        self.addTrackingArea_(
            AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(), opts, self, None))
        # super вызываем ПОСЛЕДНИМ — он добавляет tooltip tracking area поверх наших
        objc.super(_WalletView, self).updateTrackingAreas()

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
        tt = getattr(self, '_tt_text', '')
        if tt:
            _about_tt_show(tt)

    def mouseExited_(self, event):
        self._start_timer(0.0)
        _about_tt_hide()

    def mouseDown_(self, event):
        import subprocess
        subprocess.Popen(["open", "https://pay.alexbic.net/?mode=donate"])

    def acceptsFirstMouse_(self, event):
        return True

    def resetCursorRects(self):
        self.addCursorRect_cursor_(self.bounds(), AppKit.NSCursor.pointingHandCursor())

    def drawRect_(self, rect):
        f  = self._frac
        W  = float(self.bounds().size.width)   # реальный размер frame
        H  = float(self.bounds().size.height)
        full = AppKit.NSMakeRect(0, 0, W, H)

        # ── Слой 1: белый фон — только на тёмных темах, только он анимируется ───
        bg_lum = (C_BG[0] + C_BG[1] + C_BG[2]) / 3
        if bg_lum < 0.5:   # тёмная тема — кошельку нужен белый фон для контраста
            bg_y = H * self._BOT_CROP
            bg_h = H * (1.0 - self._TOP_CROP - self._BOT_CROP)
            _rc = self._BG_W_CLOSED / self._VW   # пропорция закрытого фона
            bg_w = W * _rc + (W - W * _rc) * f   # масштабируем под текущую ширину
            bg_x = W - bg_w   # выравнивание по правому краю
            radius = 8 * W / self._VW
            bg_path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                AppKit.NSMakeRect(bg_x, bg_y, bg_w, bg_h), radius, radius)
            AppKit.NSColor.whiteColor().setFill()
            bg_path.fill()

        # ── Слой 2: закрытый кошелёк — статичный, полный вид, затухает ───────
        if self._img_close:
            self._img_close.drawInRect_fromRect_operation_fraction_(
                full, AppKit.NSZeroRect, AppKit.NSCompositeSourceOver, 1.0 - f)

        # ── Слой 3: открытый кошелёк — статичный, полный вид, появляется ─────
        if self._img_open:
            self._img_open.drawInRect_fromRect_operation_fraction_(
                full, AppKit.NSZeroRect, AppKit.NSCompositeSourceOver, f)


def _draw_wf_bars(bars, peaks, bounds, bar_w=3.0, gap=2.5):
    """Рисовать столбцы эквалайзера при активности; плавную синусоиду в режиме ожидания."""
    b     = bounds
    w, h  = b.size.width, b.size.height
    n_src = len(bars)

    # Суммарная энергия сигнала — определяет вид: ожидание или активность
    energy = sum(bars) / max(1, n_src)
    active = energy > 0.015

    if not active:
        # Ожидание: непрерывная синусоида, скользящая слева направо
        cy     = h / 2.0
        amp_px = h * 0.38          # 38% высоты — хорошо видно
        STEPS  = max(80, int(w))   # достаточно плавно при любой ширине
        path   = AppKit.NSBezierPath.bezierPath()
        path.setLineWidth_(1.5)
        path.setLineCapStyle_(AppKit.NSRoundLineCapStyle)
        for s in range(STEPS + 1):
            px    = s * w / STEPS
            phase = (px / w) * 2.0 * math.pi * 4.0 - _wf_t * 1.0
            py    = cy + amp_px * math.sin(phase)
            pt    = AppKit.NSMakePoint(px, py)
            if s == 0:
                path.moveToPoint_(pt)
            else:
                path.lineToPoint_(pt)
        C_IDLE.set()
        path.stroke()
        return

    # Активность (запись): столбчатый эквалайзер с точками удержания пиков
    n       = max(4, int((w + gap) / (bar_w + gap)))
    r       = bar_w / 2
    total_w = n * bar_w + (n - 1) * gap
    x0      = (w - total_w) / 2

    for i in range(n):
        src_i = int(i * n_src / n)
        amp   = bars[src_i]
        peak  = peaks[src_i] if peaks else 0.0

        bh    = max(2.0, amp * h * 0.90)
        color = C_BAR_ON if amp > 0.05 else C_BAR_OFF

        x = x0 + i * (bar_w + gap)
        y = (h - bh) / 2
        p = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSMakeRect(x, y, bar_w, bh), r, r)
        color.set()
        p.fill()

        # Точка удержания пика: яркая метка 2px над столбиком
        if peak > 0.08:
            dot_h = max(1.5, bar_w * 0.5)
            dot_y = (h - peak * h * 0.90) / 2 - dot_h - 1
            if dot_y > 0:
                dp = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    AppKit.NSMakeRect(x, dot_y, bar_w, dot_h), r * 0.5, r * 0.5)
                C_BAR_ON.colorWithAlphaComponent_(0.85).set()
                dp.fill()


class WaveformView(AppKit.NSView):
    """Реактивные столбцы эквалайзера с удержанием пиков и анимацией дыхания в режиме ожидания."""

    def drawRect_(self, rect):
        with _wf_lock:
            bars  = list(_wf_bars)
            peaks = list(_wf_peaks)
        _draw_wf_bars(bars, peaks, self.bounds(), bar_w=3.0, gap=2.5)


class _SilentWaveformView(AppKit.NSView):
    """Компактный эквалайзер для тихой полосы — тот же размер столбиков, меньше помещается."""

    def drawRect_(self, rect):
        with _wf_lock:
            bars  = list(_wf_bars)
            peaks = list(_wf_peaks)
        _draw_wf_bars(bars, peaks, self.bounds(), bar_w=3.5, gap=2.5)


# Состояние анимации эквалайзера на уровне модуля
_eq_t             = 0.0   # позиция сканирования 0→1→0 (для режима сканирования)
_eq_dir           = 1     # направление сканирования: +1 или -1
_eq_pulse_t       = 0.0   # позиция импульса 0→1 повторяющаяся (для режима импульса от центра)
_eq_countdown_t   = 0.0   # заполнение обратного отсчёта 0→1 (для режима обратного отсчёта)
_eq_countdown_start = 0.0 # time.time() начала обратного отсчёта (0 = неактивно)
_eq_countdown_dur   = 2.0 # длительность обратного отсчёта в секундах

class EqBarsView(AppKit.NSView):
    """Столбцы эквалайзера равной высоты с анимированной подсветкой.

    Режим 0 (сканирование): яркое окно скользит слева направо и обратно.
    Режим 1 (импульс): два ярких фронта расходятся от центра к краям, затем сбрасываются.
    """
    _N    = 18   # количество столбиков
    _mode = 0    # 0=сканирование, 1=импульс
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
                # Гауссовый пик скользит слева направо и обратно
                peak_pos = 0.12 + 0.76 * _eq_t
                sigma    = 0.18
                dist     = fi - peak_pos
                h_factor = 0.06 + 0.94 * math.exp(-(dist * dist) / (2 * sigma * sigma))
                alpha    = 0.25 + 0.75 * h_factor
                bar_col  = col
            elif self._mode == 1:
                # Рябь распространяется от центра к краям
                center    = 0.5
                dc        = abs(fi - center) * 2.0
                sigma_env = 0.65
                envelope  = math.exp(-(dc * dc) / (2 * sigma_env * sigma_env))
                ripple    = 0.5 + 0.5 * math.sin(PI2 * (dc * 1.5 - _eq_pulse_t))
                h_factor  = max(0.05, envelope * (0.20 + 0.80 * ripple))
                alpha     = 0.20 + 0.80 * envelope
                bar_col   = col
            else:
                # Заполнение обратного отсчёта: столбики заполняются слева направо с градиентом зелёный→красный
                filled   = fi <= _eq_countdown_t
                rr       = min(1.0, fi * 2.0)          # 0→1 при заполнении
                gg       = max(0.0, 1.0 - fi * 1.4)    # 1→0 затухает до красного
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
    """Непрозрачный фон-таблетка для тихой полосы — использует цвет C_BG текущей темы."""
    def drawRect_(self, rect):
        b = self.bounds()
        r = min(b.size.height / 2, 14.0)
        p = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, r, r)
        br, bg, bb = C_BG
        _rgba(br, bg, bb, 0.96).set()
        p.fill()


class _AppIconView(AppKit.NSView):
    """Рисует иконку приложения напрямую — без рамки NSImageView, без обводки, чисто.
    Вызвать applyRoundedMask_() после initWithFrame_ для обрезки в стиле iOS."""
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
    """Полупрозрачный оверлей при наведении во время обработки LLM; клик = прерывание.
    Работает для тихой полосы (_silent_interrupt_fn) и главного окна (_proc_interrupt_fn).
    Текст рисуется напрямую в drawRect_, чтобы NSTextField не поглощал события мыши."""
    def drawRect_(self, rect):
        # Сначала очистить грязный прямоугольник — предотвращает артефакт двойной отрисовки при изменении alpha
        AppKit.NSColor.clearColor().set()
        AppKit.NSRectFill(rect)
        b = self.bounds()
        TOP_PAD  = 8   # отступ сверху, чтобы оверлей не касался разделителя
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
                    AppKit.NSFontAttributeName:            _mono(sz),    # лёгкий моноширинный
                    AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithWhite_alpha_(0.92, 1.0),
                    AppKit.NSParagraphStyleAttributeName:  ps,
                })
            text_h = sz + 4
            # центрировать в прямоугольнике фона с отступом
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
    """Прозрачный content view для тихой полосы; обеспечивает отслеживание мыши для наведения LLM."""
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
    """NSView с y=0 вверху (для списков с прокруткой)."""
    def isFlipped(self): return True


class _DotSep(AppKit.NSView):
    """Рисует идеально отцентрированную зелёную точку для разделителей сценариев."""
    def drawRect_(self, rect):
        b  = self.bounds()
        cx = b.size.width  / 2
        cy = b.size.height / 2
        r  = 1.8
        path = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
            AppKit.NSMakeRect(cx - r, cy - r, r * 2, r * 2))
        _rgba(0.33, 1.00, 0.33, 0.88).set()
        path.fill()


class _HistItemView(AppKit.NSView):
    """Строка истории: рисует текст, клик запускает action, перетаскивание перемещает кластер.
    В отличие от NSButton, не запускает блокирующий цикл nextEventMatchingMask_ в mouseDown_,
    поэтому события mouseDragged_ доходят до _DropPanel.sendEvent_ в обычном порядке."""

    _astr   = None   # NSAttributedString для отрисовки
    _rep    = None   # representedObject (словарь элемента)
    _target = None
    _action = None
    _ds     = None   # начало перетаскивания (NSPoint), устанавливается в mouseDown_
    _da     = False  # превысило ли перетаскивание порог

    def isOpaque(self): return False
    def acceptsFirstMouse_(self, event): return True

    def drawRect_(self, rect):
        if self._astr:
            b = self.bounds()
            self._astr.drawInRect_(b)

    # Заглушки совместимости, имитирующие API NSButton, используемый в _build_hist_docview
    def setAttributedTitle_(self, s):
        self._astr = s
        self.setNeedsDisplay_(True)

    def setRepresentedObject_(self, o): self._rep = o
    def representedObject(self):        return self._rep
    def setTarget_(self, t):            self._target = t
    def setAction_(self, a):            self._action = a
    def setBordered_(self, v):          pass  # заглушка; у NSView нет рамки

    def mouseDown_(self, event):
        self._ds = AppKit.NSEvent.mouseLocation()
        self._da = False
        # Нет вызова super() → нет блокирующего цикла отслеживания → события drag доходят до окна

    def mouseDragged_(self, event):
        if self._ds is None:
            return
        cur = AppKit.NSEvent.mouseLocation()
        dx  = cur.x - self._ds.x
        dy  = cur.y - self._ds.y
        if self._da or dx * dx + dy * dy > 100.0:
            self._da = True

    def mouseUp_(self, event):
        was_drag = self._da
        self._ds = None
        self._da = False
        if not was_drag and self._target and self._action:
            self._target.performSelector_withObject_(self._action, self)


class _HoverBtn(AppKit.NSButton):
    """Кнопка без рамки, которая светлеет при наведении — для терминальных чекбоксов."""

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
    """Кнопка наведения внутри rich-блоков — использует фиксированные границы отслеживания, работает когда скрыта."""

    def refreshTracking(self):
        """Пересобрать область отслеживания; вызывать после setHidden_(False) чтобы наведение срабатывало корректно."""
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
    """Состояние панели + ObjC-действия для чекбоксов истории."""

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
        if ids and self._on_delete:
            self._on_delete(ids)   # main.py удаляет по UUID, вызывает refresh_hist_panel

    def _selected_items(self):
        """Вернуть выбранные элементы от старых к новым."""
        indices = sorted(self._sel, reverse=True)
        return [self._items[i] for i in indices if 0 <= i < len(self._items)]

    def histReplace_(self, sender):
        """Заменить текущий текст оверлея выбранными элементами (без новой записи в историю)."""
        items_sel = self._selected_items()
        combined  = "\n\n".join(item["full"] for item in items_sel)
        loaded_id = items_sel[0]["id"] if len(items_sel) == 1 else None
        if combined:
            _load_history_combined(combined, loaded_id=loaded_id)

    def histMerge_(self, sender):
        """Объединить выбранные элементы в ОДИН новый блок текущей сессии, мягко удалить источники."""
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
        _add_rich_block(combined, hist_id=new_id)

    def histAppend_(self, sender):
        """Добавить выбранные элементы в текущую сессию как отдельные блоки.
        Сессии раскрываются в отдельные блоки.
        """
        items_sel = self._selected_items()
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
        # Подсвечиваем активную вкладку
        for m, btn in (self._tab_btns or {}).items():
            col = C_CYAN if m == mode else C_GREEN_DIM
            btn.setAttributedTitle_(_atitle(self._tab_labels.get(m, m), size=9, color=col))
        # Перефильтруем и перестраиваем контент скролла
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
    """Панель без рамки с перетаскиванием — drag из любого места через перехват sendEvent_."""
    def canBecomeKeyWindow(self): return True

    def cancelOperation_(self, sender):
        hide(force=True)

    def sendEvent_(self, event):
        _LD = 1; _LU = 2; _LDRAG = 6; _THRESH2 = 25.0   # порог 5 px
        t = event.type()
        if t == _LD:
            # Двойной клик в любом месте строки заголовка → переключить разворачивание
            if event.clickCount() == 2:
                loc = event.locationInWindow()
                wh  = self.frame().size.height
                if loc.y >= wh - HDR_H:
                    _main(_toggle_expand)
                    return
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
                # Жёстко ограничить главное окно в объединении всех экранов — не выходить за края
                try:
                    wf          = self.frame()
                    ww_, wh_    = wf.size.width, wf.size.height
                    x1, y1, x2, y2 = _all_screens_bounds()
                    new_x = max(x1, min(new_x, x2 - ww_))
                    new_y = max(y1, min(new_y, y2 - wh_))
                except Exception:
                    pass
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
    """Тонкая панель-полоска заголовка для тихого режима — перетаскиваемая, Escape или клик для скрытия."""
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
        _LD = 1; _LU = 2; _LDRAG = 6; _THRESH2 = 100.0
        t = event.type()
        w   = globals().get("_win")
        key = getattr(self, '_panel_key', None)
        is_attached = _magnet_on.get(key, True) if key else True
        is_cluster_anchor = (_cluster_mode and
                             key == "cfg" and
                             globals().get("_cfg_panel") is self)
        in_cluster = _cluster_mode and not is_cluster_anchor
        if t == _LD:
            self._wd_s  = AppKit.NSEvent.mouseLocation()
            self._wd_a  = False
            if is_cluster_anchor:
                self._wd_o = self.frame().origin
            elif in_cluster:
                # Отслеживать позицию cfg — перетаскивание любой панели кластера перемещает весь кластер
                cfg_ref = globals().get("_cfg_panel")
                self._wd_o = cfg_ref.frame().origin if cfg_ref else self.frame().origin
            elif is_attached and w:
                self._wd_o = w.frame().origin
            else:
                self._wd_o = self.frame().origin
            cv = self.contentView()
            hit = cv.hitTest_(event.locationInWindow()) if cv else None
            self._wd_on_btn = isinstance(hit, AppKit.NSButton)
        elif t == _LDRAG and getattr(self, '_wd_s', None) is not None and self._wd_o is not None:
            cur = AppKit.NSEvent.mouseLocation()
            dx  = cur.x - self._wd_s.x
            dy  = cur.y - self._wd_s.y
            if self._wd_a or dx*dx + dy*dy > _THRESH2:
                self._wd_a = True
                if is_cluster_anchor:
                    self.setFrameOrigin_(AppKit.NSMakePoint(self._wd_o.x + dx, self._wd_o.y + dy))
                    _reposition_cluster()
                elif in_cluster:
                    # Переместить cfg, затем перепозиционировать все панели кластера вместе
                    cfg_ref = globals().get("_cfg_panel")
                    if cfg_ref:
                        cfg_ref.setFrameOrigin_(AppKit.NSMakePoint(self._wd_o.x + dx, self._wd_o.y + dy))
                        _reposition_cluster()
                elif is_attached and w:
                    new_x = self._wd_o.x + dx
                    new_y = self._wd_o.y + dy
                    try: _snap_attached_panels_live(new_x, new_y)
                    except Exception: pass
                    w.setFrameOrigin_(AppKit.NSMakePoint(new_x, new_y))
                    _reposition_attached_panels()
                else:
                    self.setFrameOrigin_(AppKit.NSMakePoint(self._wd_o.x + dx, self._wd_o.y + dy))
        elif t == _LU:
            did_drag   = getattr(self, '_wd_a', False)
            on_btn     = getattr(self, '_wd_on_btn', False)
            self._wd_s = None; self._wd_a = False; self._wd_on_btn = False
            if did_drag:
                if is_cluster_anchor or in_cluster:
                    _update_cluster_offsets()
                elif is_attached:
                    try: _magnet_save()
                    except Exception: pass
                else:
                    if key:
                        try: _update_panel_drag_end(key, self)
                        except Exception: pass
                    try: _repel_from_others(self)
                    except Exception: pass
                if on_btn:
                    return
        objc.super(_DropPanel, self).sendEvent_(event)


class _TTView(AppKit.NSView):
    """Кастомная всплывающая подсказка."""
    _text = ""
    def isOpaque(self): return False
    def drawRect_(self, rect):
        w = self.bounds().size.width
        h = self.bounds().size.height
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSMakeRect(0, 0, w, h), 4, 4)
        AppKit.NSColor.colorWithWhite_alpha_(0.12, 0.93).setFill()
        path.fill()
        AppKit.NSString.stringWithString_(self._text).drawInRect_withAttributes_(
            AppKit.NSMakeRect(8, 3, w - 16, h - 6),
            {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(11.5),
             AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor()})


class _AboutPanel(AppKit.NSPanel):
    """Отдельная панель-карточка «О программе». Перехватывает mouseMoved для прямого управления курсором."""
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
                # Пройти вверх по иерархии: если какой-либо предок — ссылка/кошелёк → рука
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
    """Плавающая панель редактора, принимающая ввод с клавиатуры (текстовые поля работают)."""
    def canBecomeKeyWindow(self): return True
    def canBecomeMainWindow(self): return False

    def becomeKeyWindow(self):
        """Повторно активировать приложение когда панель снова получает фокус (напр. после переключения)."""
        objc.super(_EditorPanel, self).becomeKeyWindow()
        AppKit.NSApp.activateIgnoringOtherApps_(True)

    def cancelOperation_(self, sender):
        _main(lambda: _maybe_close_editor(pending_fn=None))

    def sendEvent_(self, event):
        _LD = 1; _LU = 2; _LDRAG = 6; _THRESH2 = 100.0
        t = event.type()
        w   = globals().get("_win")
        key = getattr(self, '_panel_key', None)
        is_attached   = _magnet_on.get(key, False) if key else False
        in_cluster    = _cluster_mode  # providers/editor are never the anchor
        if t == _LD:
            self._wd_s  = AppKit.NSEvent.mouseLocation()
            self._wd_a  = False
            if in_cluster:
                cfg_ref = globals().get("_cfg_panel")
                self._wd_o = cfg_ref.frame().origin if cfg_ref else self.frame().origin
            elif is_attached and w:
                self._wd_o = w.frame().origin
            else:
                self._wd_o = self.frame().origin
            cv = self.contentView()
            hit = cv.hitTest_(event.locationInWindow()) if cv else None
            self._wd_on_btn = isinstance(hit, AppKit.NSButton)
        elif t == _LDRAG and getattr(self, '_wd_s', None) is not None and self._wd_o is not None:
            cur = AppKit.NSEvent.mouseLocation()
            dx  = cur.x - self._wd_s.x
            dy  = cur.y - self._wd_s.y
            if self._wd_a or dx*dx + dy*dy > _THRESH2:
                self._wd_a = True
                if in_cluster:
                    cfg_ref = globals().get("_cfg_panel")
                    if cfg_ref:
                        cfg_ref.setFrameOrigin_(AppKit.NSMakePoint(self._wd_o.x + dx, self._wd_o.y + dy))
                        _reposition_cluster()
                elif is_attached and w:
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
            on_btn   = getattr(self, '_wd_on_btn', False)
            self._wd_s = None; self._wd_a = False; self._wd_on_btn = False
            if did_drag:
                if in_cluster:
                    _update_cluster_offsets()
                else:
                    if key:
                        try: _update_panel_drag_end(key, self)
                        except Exception: pass
                    if not is_attached:
                        try: _repel_from_others(self)
                        except Exception: pass
                if on_btn:
                    return
        objc.super(_EditorPanel, self).sendEvent_(event)

    def performKeyEquivalent_(self, event):
        """Перенаправить Cmd+C/V/X/A/Z напрямую в действия firstResponder."""
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
    """Cmd+Enter копирует в буфер обмена (жёстко задано)."""
    return AppKit.NSEventModifierFlagCommand


class TerminalTextView(AppKit.NSTextView):
    """NSTextView: Shift+Enter → немедленная вставка, терминальный блочный курсор."""

    def keyDown_(self, event):
        ESC   = 53
        ENTER = 36
        SHIFT = AppKit.NSEventModifierFlagShift
        OPT   = AppKit.NSEventModifierFlagOption
        MASK  = AppKit.NSEventModifierFlagDeviceIndependentFlagsMask
        kc    = event.keyCode()
        mods  = event.modifierFlags() & MASK

        # Перейти вверх в последний блок когда курсор в начале _tv
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
            # Opt+Shift+Enter → вставить сохраняя MD-форматирование как есть
            if _on_paste_cb:
                _on_paste_cb(mode="md")
        elif kc == ENTER and copy_m and mods == (copy_m | OPT):
            # copy_mod+Opt+Enter → копировать с MD-форматированием (без вставки, оверлей остаётся)
            if _on_copy_cb:
                _on_copy_cb(mode="md")
        elif kc == ENTER and copy_m and mods == copy_m:
            # copy_mod+Enter → копировать простой текст (без вставки, оверлей остаётся)
            if _on_copy_cb:
                _on_copy_cb()
        elif kc == ENTER and mods == SHIFT:
            if _on_paste_cb:
                _on_paste_cb()   # всегда сырой, без full_default
        elif kc == ENTER and not mods:
            # Простой Enter → завершить введённый вручную текст в блок
            if _tv and str(_tv.string()).strip():
                _finalize_tv_to_block(add_to_history=True)
            else:
                objc.super(TerminalTextView, self).keyDown_(event)
        elif kc == ESC:
            hide(force=True)
        else:
            objc.super(TerminalTextView, self).keyDown_(event)

    def paste_(self, sender):
        """RTF/HTML → преобразовать в Markdown, вставить в позицию курсора; простой текст → вставить обычно."""
        pb     = AppKit.NSPasteboard.generalPasteboard()
        tps    = list(pb.types() or [])
        RTF_T  = AppKit.NSPasteboardTypeRTF
        HTML_T = "public.html"
        has_rtf  = RTF_T  in tps
        has_html = HTML_T in tps

        # Сначала проверить простой текст: если уже содержит markdown code fences (```),
        # использовать напрямую — RTF/HTML из редакторов (VS Code, Obsidian…) удаляет
        # маркеры ``` при конвертации, поэтому не нужно проходить через html2text.
        if has_rtf or has_html:
            plain = pb.stringForType_(AppKit.NSPasteboardTypeString)
            if plain and re.search(r'^```', plain, re.MULTILINE):
                # Простой текст — сырой markdown с code fences — использовать как есть
                _add_rich_block(plain.strip())
                return

            html_str = None
            # Предпочесть HTML напрямую из буфера обмена
            if has_html:
                try:
                    html_data = pb.dataForType_(HTML_T)
                    if html_data:
                        html_str = bytes(html_data).decode('utf-8', errors='replace')
                except Exception:
                    pass
            # Запасной вариант: RTF → NSAttributedString → HTML
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
        # Простой текст (или неудачное извлечение) — вставить в _tv обычным образом
        objc.super(TerminalTextView, self).paste_(sender)
        _main(_after_paste_plain)

    def performKeyEquivalent_(self, event):
        """Явно обработать Cmd+C/V/X/A/Z чтобы они всегда работали."""
        CMD  = AppKit.NSEventModifierFlagCommand
        MASK = AppKit.NSEventModifierFlagDeviceIndependentFlagsMask
        mods = event.modifierFlags() & MASK
        if mods == CMD:
            kc = event.keyCode()
            if kc == 9:
                fr = _win.firstResponder() if _win else None
                with open('/tmp/vi_debug.log', 'a') as _dbg:
                    _dbg.write(f"[TermTV.performKeyEquivalent_] Cmd+V, fr={type(fr).__name__ if fr else None}\n")
        # Если фокус внутри TV блока — передаём ему обработку клавиш
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
        """Синхронизировать _st['text'] при каждом редактировании чтобы сценарии видели актуальный контент."""
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
                # Обновить видимость строки действий когда контент появляется или исчезает
                _main(_update_action_visibility)
            # Определение MD при ручном редактировании (только когда нет rich-блоков)
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
    """Фиктивный терминальный блочный курсор — оверлей NSView, всегда виден, мигает 400мс."""
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
    """Зелёный ползунок 3px, прозрачный трек."""

    def drawKnob(self):
        r = self.rectForPart_(getattr(AppKit, 'NSScrollerKnob', 2))
        if r.size.width <= 0 or r.size.height <= 0:
            return
        bar_w = 3.0
        bx = r.origin.x + r.size.width - bar_w - 1   # 1px от правого края
        ir = AppKit.NSMakeRect(bx, r.origin.y + 4, bar_w, max(4, r.size.height - 8))
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ir, 1.5, 1.5)
        _rgba(0.00, 0.80, 0.00, 0.55).set()
        path.fill()

    def drawKnobSlotInRect_highlight_(self, slotRect, highlighted):
        pass  # прозрачный трек — без белого фона


class _ThinAccentScroller(AppKit.NSScroller):
    """Ползунок 4px в акцентном цвете текущей темы (C_GREEN_BR), прозрачный трек.
    Используется для scroll view накопления чтобы соответствовать активной цветовой схеме."""

    def drawKnob(self):
        r = self.rectForPart_(getattr(AppKit, 'NSScrollerKnob', 2))
        if r.size.width <= 0 or r.size.height <= 0:
            return
        bar_w = 4.0
        bx = r.origin.x + r.size.width - bar_w - 2   # 2px от правого края
        ir = AppKit.NSMakeRect(bx, r.origin.y + 4, bar_w, max(6, r.size.height - 8))
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ir, 2.0, 2.0)
        C_GREEN_BR.colorWithAlphaComponent_(0.70).set()
        path.fill()

    def drawKnobSlotInRect_highlight_(self, slotRect, highlighted):
        pass  # прозрачный трек


# ── Представление блока с текстом ─────────────────────────────────────────────

_RICH_LIGHT_BG  = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.96, 0.97, 0.96, 1.0)
_RICH_DARK_BG   = _rgba(0.02, 0.06, 0.02, 0.0)   # прозрачный — тёмное окно просвечивает
_RICH_LINE_C    = _rgba(0.00, 0.78, 0.55, 0.90)   # цвет левой границы

_on_add_history_cb      = None   # сохранено для тихого режима / путей сценариев
_on_update_session_cb   = None   # () → вызывается при добавлении блока; main.py обновляет сессию
_on_session_end_cb      = None   # () → вызывается при скрытии; main.py очищает ID текущей сессии


class _BlockTV(AppKit.NSTextView):
    """Редактируемый NSTextView внутри блока — обрабатывает навигацию курсора между блоками."""

    def didChangeText(self):
        objc.super(_BlockTV, self).didChangeText()
        block = getattr(self, '_parent_block', None)
        if block:
            _main(block._resize_to_content)
            _main(block._check_edits)

    def paste_(self, sender):
        """Вставить простой текст в блок. Сохраняет markdown-маркеры, убирает rich-форматирование."""
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
        """Обработать Cmd+key когда этот block TV является активным firstResponder."""
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

        # В начале → перейти в конец предыдущего блока (или остаться если первый)
        if at_start and kc in (51, 123, 126) and not mods:   # ⌫ ← ↑
            if bidx > 0 and bidx - 1 < len(_rich_blocks):
                prev = _rich_blocks[bidx - 1]
                if prev._inner_tv and _win:
                    _win.makeFirstResponder_(prev._inner_tv)
                    pln = len(str(prev._inner_tv.string()))
                    prev._inner_tv.setSelectedRange_(AppKit.NSMakeRange(pln, 0))
                    return
        # В конце → перейти в начало следующего блока или в _tv
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

        # Горячие клавиши копирования/вставки — как в TerminalTextView, работают с полным контекстом
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
            if _on_paste_cb: _on_paste_cb()   # всегда сырой
            return

        objc.super(_BlockTV, self).keyDown_(event)


class _RichBlockView(AppKit.NSView):
    """Markdown-блок: без внутренней прокрутки, адаптивная высота, кнопки по наведению.

    Макет (NSView, y=0 снизу):
      [bh-BTN_AREA..bh]          панель кнопок: "md" + "▸" (скрыты до наведения)
      [V_PAD..bh-BTN_AREA]       NSTextView по размеру контента (без прокрутки)
      x=0..BLOCK_BORDER_W        левая линия (зелёная при наведении, голубая в MD-режиме)

    Состояния:
      default – отрисованный текст, нет линии, нет кнопок
      hovered – зелёная левая линия, кнопки появляются вверху справа
      md_mode – голубая левая линия всегда, сырой Markdown-текст
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
        self._sc_undo_btn     = None   # отмена сценария — показывается когда блок является результатом активного сценария
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

        # ── NSTextView (прямой, без обёртки прокрутки) ────────────────────────────
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

        # ── Кнопки при наведении ─────────────────────────────────────────────────────
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
        # Всегда виден для markdown-блоков; скрыт до наведения для простого текста
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

        # Отмена сценария — всегда видна (не по наведению) пока блок является результатом активного сценария
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

        # ── Индикатор левой линии ────────────────────────────────────────────────
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
            # Сохранить [md] видимым для markdown-блоков даже без наведения
            if self._md_btn and not self._is_md_block:
                self._md_btn.setHidden_(True)
            if self._cpy_btn: self._cpy_btn.setHidden_(True)
        else:
            if self._cpy_btn: self._cpy_btn.setHidden_(True)
        if self._del_btn: self._del_btn.setHidden_(True)
        if self._rev_btn: self._rev_btn.setHidden_(True)
        # sc_undo_btn остаётся видимой при выходе мыши (всегда видна когда сценарий активен)

    def set_sc_undo(self, visible: bool):
        """Показать/скрыть кнопку отмены сценария на этом блоке."""
        if self._sc_undo_btn:
            self._sc_undo_btn.setHidden_(not visible)
            if visible:
                self._sc_undo_btn.refreshTracking()

    def toggle_format(self):
        self._md_mode = not self._md_mode
        if self._inner_tv:
            if self._md_mode:
                # Отрисованный → сырой: сначала синхронизировать правки в отрисованном режиме в _md_text.
                # В отрисованном режиме пользователь мог вводить/вставлять текст напрямую в _inner_tv,
                # поэтому нужно захватить это перед заменой контента сырым markdown-источником.
                current_in_tv = str(self._inner_tv.string()).strip()
                if current_in_tv and current_in_tv != str(self._rendered.string()).strip():
                    self._md_text = current_in_tv   # сохранить правки сделанные в отрисованном режиме
                raw_attrs = _md_to_raw_attrs(self._md_text)
                self._inner_tv.textStorage().setAttributedString_(raw_attrs)
                self._set_line_color(C_CYAN)
            else:
                # Сырой → отрисованный: перерисовать markdown который теперь в редакторе
                current_raw = str(self._inner_tv.string()).strip() or self._md_text
                self._md_text = current_raw
                try:
                    self._rendered = _md_to_styled_attrs(current_raw)
                except Exception:
                    self._rendered = AppKit.NSAttributedString.alloc().initWithString_(current_raw)
                self._inner_tv.textStorage().setAttributedString_(self._rendered)
                color = C_TEXT if self._hovered else AppKit.NSColor.clearColor()
                self._set_line_color(color)
                # Пометить как md-блок чтобы float bar отслеживал при прокрутке
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
        """Обновить флаг _has_edits и видимость кнопки отката после изменения текста."""
        if not self._inner_tv:
            return
        current = str(self._inner_tv.string()).strip()
        self._has_edits = bool(self._original_text and current != self._original_text)
        if self._rev_btn:
            should_show = self._has_edits and self._hovered and bool(self._original_text)
            self._rev_btn.setHidden_(not should_show)

    def _save_edit_to_history(self):
        """Вызывается после дебаунса: сохранить отредактированный контент в историю (со ссылкой на родителя если есть)."""
        if not self._inner_tv or not _on_add_history_cb:
            return
        text = str(self._inner_tv.string()).strip()
        if not text:
            return
        if text == (self._original_text or ""):
            return  # не изменено
        parent_id = self._original_hist_id  # может быть None
        new_id = _on_add_history_cb(text, parent_id)
        self._hist_id   = new_id
        self._has_edits = True
        self._md_text   = text
        if self._rev_btn and parent_id:
            # Показывать откат только когда есть оригинал для возврата
            self._rev_btn.setHidden_(not self._hovered)
            if self._hovered:
                self._rev_btn.refreshTracking()

    def _revert_to_original(self):
        """Восстановить блок до исходного (до правок) содержимого."""
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
        """Пересчитать высоту блока на основе текущего контента inner TV; удалить если пусто."""
        if not self._inner_tv:
            return
        current_text = str(self._inner_tv.string())
        if not current_text.strip():
            self._delete_self()
            return
        # Обновлять хранимый markdown-источник ТОЛЬКО в сыром режиме.
        # В отрисованном режиме _inner_tv содержит отрисованный HTML (без маркеров ## **).
        if self._md_mode:
            self._md_text = current_text.strip()
        # Точное измерение высоты контента через временное представление (избегаем обрезания)
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

        # Перепозиционировать кнопки наведения в новую верхнюю область
        BTN_H = 12
        BTN_Y = bh - BLOCK_BTN_AREA + (BLOCK_BTN_AREA - BTN_H) // 2
        for btn in (self._del_btn, self._rev_btn, self._md_btn, self._cpy_btn):
            if btn:
                of = btn.frame()
                btn.setFrame_(AppKit.NSMakeRect(of.origin.x, BTN_Y, of.size.width, BTN_H))

        _relayout_doc_view()

    def _sync_to_width(self, new_w):
        """Изменить размер всех внутренних subview до new_w; перемерить + вернуть новую высоту блока.
        НЕ вызывает _relayout_doc_view — безопасно вызывать из _relayout_doc_view."""
        if not self._inner_tv:
            return int(self.frame().size.height)
        tv_w = max(40, new_w - BLOCK_L_PAD - BLOCK_R_PAD)
        old_tv = self._inner_tv.frame()
        self._inner_tv.setFrame_(AppKit.NSMakeRect(
            old_tv.origin.x, old_tv.origin.y, tv_w, old_tv.size.height))
        # Перемерить высоту при новой ширине
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
        """Перерисовать контент блока с текущим размером шрифта."""
        if not self._inner_tv:
            return
        if self._md_mode:
            # Сырой режим: _inner_tv содержит сырой markdown (возможно, отредактированный)
            current_raw = str(self._inner_tv.string()).strip() or self._md_text
            self._md_text = current_raw
            raw_attrs = _md_to_raw_attrs(current_raw)
            self._inner_tv.textStorage().setAttributedString_(raw_attrs)
        else:
            # Отрисованный режим: всегда перерисовывать из _md_text (оригинального markdown)
            try:
                rendered = _md_to_styled_attrs(self._md_text)
            except Exception:
                rendered = AppKit.NSAttributedString.alloc().initWithString_(self._md_text)
            self._rendered = rendered
            self._inner_tv.textStorage().setAttributedString_(rendered)
        self._resize_to_content()

    def _delete_self(self):
        """Удалить этот блок из сессии и обновить фокус."""
        if self not in _rich_blocks:
            return
        idx = _rich_blocks.index(self)
        _rich_blocks.pop(idx)
        # Обновить индексы оставшихся блоков
        for i, b in enumerate(_rich_blocks):
            b._idx = i
            if b._inner_tv:
                b._inner_tv._block_idx = i
            for btn in (b._del_btn, b._rev_btn, b._md_btn, b._cpy_btn):
                if btn:
                    btn.setTag_(i)
        self.removeFromSuperview()
        _relayout_doc_view()
        _update_action_visibility()   # скрыть кнопки если удалён последний блок
        # Переместить фокус: конец предыдущего блока или _tv если нет
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
    """Создать _RichBlockView точно по высоте контента (без максимума)."""
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
    """Преобразовать текст из _tv в блок; очистить _tv для следующего ввода.
    add_to_history=True для путей вставки (история диктовки управляется main.py).
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
    # Внутренний курсор в КОНЦЕ текста блока чтобы следующий клик попал туда
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
    # Восстановить атрибуты ввода — setString_("") может сбросить их к системным значениям по умолчанию
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
    _update_action_visibility()   # блок теперь имеет контент; строка должна оставаться видимой
    if _win and _tv:
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        _win.makeKeyAndOrderFront_(None)
        _win.makeFirstResponder_(_tv)
        _tv.setSelectedRange_(AppKit.NSMakeRange(0, 0))
        _tv.scrollRangeToVisible_(AppKit.NSMakeRange(0, 0))


# ── Цели ObjC-действий ─────────────────────────────────────────────────────────

class BtnTarget(AppKit.NSObject):
    def scenario_(self, sender):
        idx = int(sender.tag())
        sc  = _st["scenarios"]
        if _on_scenario_cb and 0 <= idx < len(sc):
            _on_scenario_cb(sc[idx], idx)

    def close_(self, sender):
        hide(force=True)

    def actionCancel_(self, sender):
        """Умная отмена: откатить сценарий если активен, остаться в окне после прерывания, иначе закрыть."""
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

        # Угловая кнопка (tag=9): различать одиночный и двойной клик без открытия
        # истории на первом клике двойного.
        if callable(getattr(sender, 'tag', None)) and sender.tag() == 9:
            ev = AppKit.NSApp.currentEvent()
            if ev and ev.clickCount() >= 2:
                # Двойной клик: отменить ожидающее одиночное открытие, развернуть окно
                AppKit.NSObject.cancelPreviousPerformRequestsWithTarget_selector_object_(
                    self, b'_openHistDelayed:', None)
                _main(_toggle_expand)
            else:
                # Одиночный клик: отложить открытие чтобы двойной клик мог его отменить
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
            return  # панель cfg остаётся видимой; редактор должен быть сохранён/отменён сначала
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
        _refresh_menu_titles()
        # Пересобрать панель config с новым языком (сохранить позицию окна — немедленно переоткрыть)
        _close_cfg_panel_rebuild()
        _toggle_cfg_panel()
        # Пересобрать редактор сценариев с новым языком (сохраняет sc_idx, теряет несохранённые правки)
        if _editing_scenario and _sc_editor_panel:
            sc_idx = (_sc_edit_refs or {}).get("sc_idx")
            _show_sc_editor(sc_idx)


    def hushCopyText_(self, sender):
        if _on_copy_cb:
            _on_copy_cb()

    def histClose_(self, sender):
        if _hist_panel and _hist_panel.isVisible():
            _hist_panel.orderOut_(None)

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
        """Переключить карточку «О программе» как отдельную центрированную панель."""
        if _about_panel and _about_panel.isVisible():
            _hide_about_view()
        else:
            _main(_show_about_view)

    def aboutClose_(self, sender):
        _main(_hide_about_view)

    def aboutDonate_(self, sender):
        import subprocess
        subprocess.Popen(["open", "https://pay.alexbic.net/?mode=donate"])

    def aboutDocs_(self, sender):
        import subprocess
        lang = _st.get("lang", "ru")
        urls = {
            "ru": "https://github.com/alexbic/hush/blob/main/README_RU.md",
            "es": "https://github.com/alexbic/hush/blob/main/README_ES.md",
        }
        url = urls.get(lang, "https://github.com/alexbic/hush/blob/main/README.md")
        subprocess.Popen(["open", url])

    def aboutGithub_(self, sender):
        import subprocess
        subprocess.Popen(["open", "https://github.com/alexbic/hush"])

    def aboutSite_(self, sender):
        import subprocess
        subprocess.Popen(["open", "https://alexbic.net"])

    def showAbout_(self, sender):
        """Меню статус-бара: всегда показывать карточку «О программе»."""
        _main(_show_about_view)

    def openHush_(self, sender):
        """Меню статус-бара: открыть главное окно HUSH (то же что двойное нажатие Option)."""
        show_recording()
        AppKit.NSApp.activateIgnoringOtherApps_(True)

    def toggleLaunchAtLogin_(self, sender):
        """Меню статус-бара: переключить автозапуск при входе через LaunchAgent plist."""
        _toggle_launch_at_login()
        state = 1 if _is_launch_at_login() else 0
        sender.setState_(state)
        item = getattr(self, '_login_menu_item', None)
        if item:
            item.setState_(state)

    def menuNeedsUpdate_(self, menu):
        """NSMenuDelegate: обновить галочку перед открытием меню."""
        item = getattr(self, '_login_menu_item', None)
        if item:
            item.setState_(1 if _is_launch_at_login() else 0)

    def quitApp_(self, sender):
        """Меню статус-бара: завершить приложение."""
        AppKit.NSApp.terminate_(None)

    def retryStatusVisible_(self, timer):
        """Заглушка: сохранена для совместимости; исправление статус-бара в C-лаунчере."""
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
        """Сохранить все поля провайдеров в providers.json и повторно проверить."""
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
        # Повторно проверить всех провайдеров после закрытия UI (ссылки очищены)
        _pc.probe_all()

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
                # Та же карточка нажата снова → переключить закрытие (проверка изменений внутри)
                _main(lambda: _maybe_close_editor(pending_fn=None))
                return
            # Другой сценарий → сначала проверить несохранённые изменения, затем переключить
            _main(lambda idx=sc_idx: _maybe_close_editor(
                pending_fn=lambda: _show_sc_editor(idx)))
        else:
            # Подсветить эту карточку немедленно (яркая = редактор для неё открыт)
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
        """Закрыть оверлей подтверждения несохранённых изменений, остаться в редакторе."""
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
        """Закрыть оверлей подтверждения удаления."""
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
        """Переключить флаг «тихий режим» (состояние хранится в _sc_edit_refs, не на кнопке)."""
        new_val = not _sc_edit_refs.get("silent", False)
        _sc_edit_refs["silent"] = new_val
        chk_prefix = "[✓] " if new_val else "[ ] "
        color = C_CYAN if new_val else C_GREEN_DIM
        sender.setAttributedTitle_(_atitle(
            chk_prefix + _T("sc_silent"), size=10, color=color,
            align=AppKit.NSTextAlignmentLeft))

    def cfgScToggleFullDefault_(self, sender):
        """Переключить «сценарий по умолчанию в полном режиме» (радио — допустим только один)."""
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
        # Также обновить метки если изменился язык (метка зависит от _st lang)
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

# ── Вспомогательные функции ────────────────────────────────────────────────────

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
    """NSButton показывающий курсор-указатель при наведении (для кнопок в стиле гиперссылок)."""
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
    """Как _mkbtn но показывает курсор-указатель при наведении."""
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
    """Стилизовать NSTextField в терминальном виде: фон через CALayer, граница, вертикально по центру."""
    tf_bg = _rgba(*C_BG)
    cell = _CenteredTextFieldCell.alloc().init()
    cell.setFont_(_mono(10))
    cell.setTextColor_(C_TEXT)
    cell.setBackgroundColor_(AppKit.NSColor.clearColor())
    cell.setDrawsBackground_(False)   # фон — через CALayer, не через ячейку
    cell.setBezeled_(False)
    cell.setEditable_(True)
    cell.setSelectable_(True)
    cell.setFocusRingType_(AppKit.NSFocusRingTypeNone)
    tf.setCell_(cell)
    tf.setEditable_(True)
    tf.setSelectable_(True)
    tf.setDrawsBackground_(False)
    tf.setWantsLayer_(True)
    lay = tf.layer()
    if lay:
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

# ── Константы компоновки ──────────────────────────────────────────────────────

W        = 440
W_EXP    = 640   # ширина в развёрнутом виде
H        = 358   # высота окна — одинакова для главного и всех вспомогательных панелей
H_EXP    = 680   # высота в развёрнутом виде
H_PANEL  = H     # псевдоним: вспомогательные панели используют ту же высоту что главное окно

# Заголовок (вверху): ОДНА строка — статус + волноформа + [CFG][↵][□][×] на одной строке
HDR_H    = 40
HDR_Y    = H - HDR_H                # = 300

HDR_ITEM_H  = 22
HDR_ITEM_Y  = HDR_Y + (HDR_H - HDR_ITEM_H) // 2   # = 309 (вертикально по центру)

# Метка статуса [слева]
STS_X    = 10
STS_W    = 70

# Правый кластер в заголовке: видна только ⚙; □ и × всегда скрыты
CLO_W    = 28
CLO_X    = W - CLO_W - 4           # = 408 (слот кнопки закрытия, скрыта постоянно)
EXP_W    = 28
EXP_X    = CLO_X - EXP_W - 2       # = 378 (слот кнопки разворачивания, скрыта постоянно — двойной клик на иконку)
CFG_H_W  = 24                       # ⚙ иконка шестерёнки — один маленький символ
CFG_H_X  = W - CFG_H_W - 6         # = 410 (дальний правый угол)
HIST_H_W = 42                       # сохранено для справки; кнопка истории перенесена на нижнюю панель
HIST_H_X = CFG_H_X                  # правая граница волноформы теперь доходит до иконки шестерёнки

# EQ/Волноформа: фиксированная центрированная позиция, одинакова для всех режимов
EQ_CTR_W = 200
EQ_CTR_X = (W - EQ_CTR_W) // 2    # = 120

# Псевдонимы для устаревших ссылок (WF начинается с центрированной позиции)
WF_X     = EQ_CTR_X                 # = 120
WF_W     = EQ_CTR_W                 # = 200

# Строка кнопок (снизу)
BTN_H    = 46
BTN_Y    = 0

# Текстовая область (середина)
TXT_Y    = BTN_H + 4                # = 50
TXT_TOP  = HDR_Y - 2                # = 298
TXT_H    = TXT_TOP - TXT_Y         # = 248

# Drop panels — точная ширина главного окна
DP_PANEL_W     = W                  # обновится до текущей ширины окна при показе
DP_CFG_H       = 72                 # компактная двухколоночная компоновка
DP_HIST_ITEM_H = 26
DP_HIST_MAX_H  = 280

# Заранее выделенные слоты кнопок сценариев (максимум; фактическое кол-во на страницу вычисляется динамически)
SC_PAGE = 8

# Полоска тихого режима — таблетка с опциональной иконкой приложения
SILENT_H = 48    # высота полоски (все состояния); ширина вычисляется динамически

# Компоновка rich-text блоков
BLOCK_GAP      = 8    # отступ между блоками
BLOCK_TV_GAP   = 2    # минимальный отступ между последним блоком и курсором в _tv
BLOCK_L_PAD    = 8    # левый отступ (место для линии границы при наведении)
BLOCK_R_PAD    = 4    # правый отступ
BLOCK_V_PAD    = 3    # нижний отступ
BLOCK_BTN_AREA = 18   # верхняя область зарезервированная для кнопок наведения (не перекрывается контентом)
BLOCK_BORDER_W = 1.5  # ширина левой граничной линии (тонкая)

# ── Ссылки на виджеты ───────────────────────────────────────────────────────────────────────────────────────

_win           = None
_pill          = None    # TerminalView (главный фон)
_wf            = None    # WaveformView (в заголовке)
_tv            = None    # TerminalTextView (текстовая область)
_lbl           = None    # метка статуса (в заголовке)
_app_icon_v    = None    # NSImageView с иконкой целевого приложения (в шапке)
_proc_app_lbl  = None    # NSTextField имя приложения — показывается только при обработке
_proc_sc_lbl   = None    # NSTextField имя сценария — справа от EQ во время обработки
_prev_app_name = ""      # локализованное имя prev_app (для _proc_app_lbl)
_undo_sc_btn   = None    # кнопка ↩ возврата к оригиналу (показывается когда сценарий активен)
_sc_action_v    = None   # 2-кнопочная панель: показывается когда активен результат сценария
_sc_send_btn2   = None   # «Отправить» в 2-кнопочной панели
_sc_copy_btn2   = None   # «Копировать» в 2-кнопочной панели
_sc_cancel_btn2 = None   # «Отменить» в 2-кнопочной панели
_action_row_v      = None   # 4-кнопочная панель: нормальное состояние ready (скрыта когда пусто)
_action_copy_btn   = None   # [КОПИРОВАТЬ] в 4-кнопочной строке действий
_action_hist_btn   = None   # [ИСТ] — история (заменяет кнопку истории в заголовке)
_action_cancel_btn = None   # [ОТМЕНИТЬ] — закрыть оверлей (или отменить сценарий)
_action_scene_btn  = None   # [СЦЕНАРИЙ] — переключить выбор сценария
_action_send_btn   = None   # [ОТПРАВИТЬ] — вставить текст как есть
_on_undo_sc_cb = None    # callback для отмены последнего сценария
_scroll        = None    # NSScrollView обёртывающий _tv
_sc_icons      = []      # кнопки сценариев (нижняя строка)
_sc_seps       = []      # разделители между кнопками сценариев
_hist_btn      = None    # кнопка [HIST] (нижняя строка)
_hist_corner_btn = None  # иконка [⧖] истории, всегда видна в правом нижнем углу
_cfg_hdr_btn   = None    # кнопка [CFG] (заголовок, всегда видна)
_send_hdr_btn  = None    # кнопка [↵] (заголовок, видна когда ready)
_close_btn     = None
_expand_btn    = None
_wf_timer      = None
_cur_view      = None    # оверлей _BlockCursor
_cur_timer     = None    # NSTimer для мигания курсора
_float_bar     = None    # прилипающая плавающая панель (md + copy) для больших блоков
_float_bar_md  = None    # кнопка [md] в плавающей панели
_float_bar_cp  = None    # кнопка [→] в плавающей панели
_proc_eq_v        = None    # EqBarsView в заголовке — показывается во время LLM-обработки сценария
_proc_sc_idx      = None    # индекс сценария в процессе LLM-обработки (жёлтая подсветка)
_proc_hover_v     = None    # _ProcHoverView — оверлей отмены по наведению для главного окна
_proc_interrupt_fn = None   # callable: вызывается при клике отмены во время обработки
_sc_avail         = {}      # {sc_idx: bool} кешированная доступность модели для кнопок главного окна
_float_target     = None    # какой _RichBlockView управляет плавающей панелью

_about_panel        = None   # отдельный NSPanel для карточки «О программе» (центрирован на экране)
_tt_panel           = None   # всплывающая подсказка для About-панели
_tt_timer           = None   # таймер задержки перед показом подсказки
_prov_panel         = None   # drop panel для настройки провайдеров/API-ключей
_prov_field_refs    = {}     # {"ollama_url": tf, "ollama_model": combo, "anthropic_key": tf, ...}
_prov_dot_refs      = {}     # {"ollama": NSTextField точка, ...}
_status_bar_item    = None   # NSStatusItem для строки меню macOS
_hist_panel       = None   # drop panel для истории
_hist_panel_side  = None   # "below" | "right" | "left" — текущее размещение
_hist_filter      = "blocks"   # "mixed" | "sessions" | "blocks" — активная вкладка
_cfg_panel        = None   # drop panel для настроек (остаётся открытым во время редактирования)
_pre_cfg_win_y    = None   # (устаревший, не используется — панели больше не сдвигают главное окно)
_panels_reset_open = False  # True после нажатия 🎯 открывает все; второе нажатие закрывает все
_sc_editor_panel  = None   # панель редактора сценариев (перекрывает главное окно при редактировании)
_editing_scenario = False  # True пока открыт редактор сценариев
_sc_edit_refs    = {}     # {tf_ru/en/es, pop_provider, pop_model, tv_prompt, sc_idx, original}
_sc_edit_pending = None   # callable: что делать после закрытия редактора (сохранить или отменить)
_sc_cfg_buttons  = {}    # sc_idx → NSButton в текущей панели cfg (для синхронизации цветов)

_sc_page      = 0    # текущая страница сценариев (с нуля)
_sc_prev_btn  = None # кнопка навигации [<]
_sc_next_btn  = None # кнопка навигации [>]
_sc_active    = []   # _sc_active[i] = True если слот i содержит сценарий на текущей странице
_sc_sep_active = []  # _sc_sep_active[i] = True если разделитель i между двумя видимыми сценариями
_sc_page_size  = 5   # фактическое кол-во сценариев на странице (вычисляется динамически из ширины окна)

_md_btn      = None   # переключатель формата [md] для простого текста в markdown
_doc_view    = None   # FlippedView контейнер внутри _scroll (содержит блоки + _tv)
_rich_blocks = []     # список экземпляров _RichBlockView в порядке вставки

# Тихий режим (плавающая полоска внизу по центру когда полное окно закрыто)
_silent_mode         = False
_silent_win          = None   # экземпляр _SilentPanel
_silent_wf           = None   # _SilentWaveformView (состояние записи)
_silent_eq_v         = None   # EqBarsView (распознавание=сканирование / LLM=импульс)
_silent_target_app   = None   # NSRunningApplication для цели вставки
_silent_app_icon_v   = None   # _AppIconView (показывается для не-Python приложений)
_silent_hover_v      = None   # _HoverOverlayView (только в состоянии LLM)
_silent_interrupt_fn = None   # callable; called on interrupt click
_silent_text_v       = None   # NSTextField (accumulation) or NSTextField (processing card)
_silent_scroll_v     = None   # unused slot (kept for legacy reset in hide)
_silent_block_count  = 0      # неиспользуемый слот (сохранён для обратной совместимости при сбросе в hide)
_silent_strip_win_h  = 0      # начальная высота окна (только полоска) — устанавливается при первом вызове накопления
_silent_sep_y        = 0      # y-координата разделителя (фиксирована относительно верха полоски)
_silent_saved_cx     = None   # сохранённый центр X окна (сохраняется между пересборками)
_silent_saved_sy     = None   # сохранённый нижний Y окна (сохраняется между пересборками)

# ── Разворачивание / сворачивание ─────────────────────────────────────────────────────────

_expanded        = False
_font_size_saved = None

# ── Определение и отрисовка Markdown ────────────────────────────────────────────

_MD_PATTERNS = [
    re.compile(r'^#{1,6}\s', re.MULTILINE),       # заголовки
    re.compile(r'\*\*\S'),                          # жирный
    re.compile(r'- \[[ xX]\]'),                    # чекбоксы
    re.compile(r'`[^`]'),                           # инлайн-код или блок кода
    re.compile(r'^[-*]{3,}\s*$', re.MULTILINE),    # горизонтальная линия
    re.compile(r'^\|.+\|', re.MULTILINE),          # строка таблицы
    re.compile(r'^\s*[-*+]\s+\S', re.MULTILINE),   # неупорядоченный список
    re.compile(r'^\s*\d+\.\s+\S', re.MULTILINE),   # упорядоченный список
]

def _is_markdown(text: str) -> bool:
    """Вернуть True если текст соответствует ≥2 Markdown-маркерам."""
    return sum(1 for p in _MD_PATTERNS if p.search(text)) >= 2


def _render_md_terminal(text: str) -> str:
    """Преобразовать Markdown в терминальный простой текст для отображения."""

    def _inline(s: str) -> str:
        """Применить инлайн-markdown к строке для терминального отображения."""
        # Управляющие последовательности
        s = re.sub(r'\\([\\`*_{}\[\]()#+\-.!|])', r'\1', s)
        # Жирный+курсив *** (перед ** и *)
        s = re.sub(r'\*{3}(.+?)\*{3}', lambda m: '_' + m.group(1).upper() + '_', s)
        # Жирный **
        s = re.sub(r'\*\*(.+?)\*\*', lambda m: m.group(1).upper(), s)
        # Курсив * или _
        s = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'_\1_', s)
        s = re.sub(r'(?<!_)\b_([^_\n]+)_\b(?!_)',  r'_\1_', s)
        # Зачёркивание ~~
        s = re.sub(r'~~(.+?)~~', r'[\1]', s)
        # Инлайн-код
        s = re.sub(r'`([^`]+)`', r'[\1]', s)
        # Изображения (перед ссылками)
        s = re.sub(r'!\[([^\]]*)\]\([^\)]*\)',
                   lambda m: f'[IMG: {m.group(1)}]' if m.group(1) else '[IMG]', s)
        # Ссылки → показать только текст
        s = re.sub(r'\[([^\]]+)\]\([^\)]*\)', r'\1', s)
        # Авто-ссылки <url>
        s = re.sub(r'<(https?://[^>]+)>', r'\1', s)
        # Сноски [^n]
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

        # ── Блоки кода — сохранять как есть ────────────────────────────────────
        if stripped.startswith('```'):
            in_code = not in_code
            out.append(line)
            i += 1; continue
        if in_code:
            out.append(line)
            i += 1; continue

        # ── GFM оповещения > [!TYPE] ────────────────────────────────────────────
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

        # ── Цитаты > ───────────────────────────────────────────────────
        if stripped.startswith('>'):
            level = 0
            s = stripped
            while s.startswith('>'):
                level += 1
                s = s[1:].lstrip()
            out.append('│ ' * level + _inline(s))
            i += 1; continue

        # ── Горизонтальная линия ─────────────────────────────────────────────────
        if re.match(r'^[-*_]{3,}\s*$', stripped):
            out.append('─' * 42)
            i += 1; continue

        # ── Заголовки ────────────────────────────────────────────────────────
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

        # ── Таблицы ──────────────────────────────────────────────────────────
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

        # ── Определения сносок [^n]: ───────────────────────────────────────
        if re.match(r'^\[\^[^\]]+\]:', stripped):
            i += 1; continue

        # ── Список определений `: definition` ───────────────────────────────────
        m_dl = re.match(r'^:\s+(.*)', line)
        if m_dl:
            out.append('  ' + _inline(m_dl.group(1)))
            i += 1; continue

        # ── Чекбоксы, списки, инлайн ────────────────────────────────────────
        line = re.sub(r'^(\s*)- \[[xX]\]\s*', r'\1✓ ', line)
        line = re.sub(r'^(\s*)- \[ \]\s*',    r'\1○ ', line)
        line = re.sub(r'^(\s*)[-*+]\s+',      r'\1• ', line)
        line = re.sub(r'^(\s*)(\d+)\.\s+',    r'\g<1>\2. ', line)
        # Жёсткий перенос двумя пробелами
        if line.endswith('  '):
            line = line.rstrip()
        line = _inline(line)
        out.append(line)
        i += 1

    return '\n'.join(out)


def _update_format_indicator():
    """Показать/скрыть кнопку [md] для простого текста markdown (rich-блоки имеют собственные индикаторы)."""
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
    """Применить единый терминальный цвет/шрифт ко всему тексту в _tv."""
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
    """Вызывается после вставки простого текста в _tv — конвертировать в блок и добавить в историю."""
    if _tv:
        _st["text"] = str(_tv.string()).rstrip('\n')
    _apply_terminal_style()
    if not _st.get("rich_fmt"):
        _st["is_md"] = _is_markdown(_st["text"]) if _st["text"].strip() else False
    _update_format_indicator()
    _relayout_doc_view()
    _finalize_tv_to_block(add_to_history=True)


def _update_blocks_font():
    """Перерисовать все rich-блоки с текущим размером шрифта, затем перекомпоновать."""
    for block in list(_rich_blocks):
        block._refresh_font()
    _relayout_doc_view()

def _relayout_doc_view():
    """Перепозиционировать rich-блоки + _tv внутри _doc_view (FlippedView, y=0 сверху)."""
    if not _doc_view or not _tv or not _scroll:
        return
    cur_w = int(_doc_view.frame().size.width)
    if cur_w <= 0:
        return
    y = 0   # FlippedView: y=0 вверху, растёт вниз

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

    # Высота _tv из реальной компоновки — защита от проблем менеджера компоновки
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
    """Показать/скрыть/обновить прилипающую плавающую панель [md]/[→].
    Панель появляется когда заголовок markdown-блока прокручен выше viewport."""
    global _float_target
    if not _scroll or not _float_bar or not _doc_view:
        return
    vis = _doc_view.visibleRect()   # в координатах FlippedView (y=0 сверху)
    vis_top = vis.origin.y
    target = None
    for block in _rich_blocks:
        if not getattr(block, '_is_md_block', False):
            continue
        bf = block.frame()
        block_top = bf.origin.y
        block_bot = block_top + bf.size.height
        # Заголовок выше viewport, тело ещё видно
        if block_top < vis_top and block_bot > vis_top + BLOCK_BTN_AREA:
            target = block
            break
    _float_target = target
    if target:
        # Позиция в правом верхнем углу скролла (координаты пилюли, y=0 внизу)
        sf = _scroll.frame()
        bw, bh = 52, 16
        _float_bar.setFrame_(AppKit.NSMakeRect(
            sf.origin.x + sf.size.width - bw - 6,
            sf.origin.y + sf.size.height - bh - 2,
            bw, bh))
        # Синхронизировать состояние кнопки [md]
        if _float_bar_md:
            is_raw = target._md_mode
            col = C_CYAN if is_raw else C_GREEN_DIM
            _float_bar_md._normal_col = col
            _float_bar_md.setAttributedTitle_(_atitle("md", size=9, color=col))
        _float_bar.setHidden_(False)
    else:
        _float_bar.setHidden_(True)


def _add_rich_block(md_text, hist_id=None):
    """Создать Markdown-блок и добавить в document view (выше _tv).
    hist_id: если передан, использовать его (новая запись в историю не создаётся).
    """
    if not _doc_view:
        return
    try:
        idx   = len(_rich_blocks)
        block = _make_rich_block(md_text, idx)
        _rich_blocks.append(block)
        _doc_view.addSubview_(block)
        _st["rich_fmt"] = "md"
        # Внутренний курсор в конце текста блока
        if block._inner_tv:
            end = block._inner_tv.textStorage().length()
            block._inner_tv.setSelectedRange_(AppKit.NSMakeRange(end, 0))
        # Очистить _tv чтобы следующая речь начиналась свежей ниже блока
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
    """Удалить все rich-блоки, очистить rich-состояние, перекомпоновать."""
    for block in _rich_blocks:
        block.removeFromSuperview()
    _rich_blocks.clear()
    _st["rich_fmt"]   = None
    _st["rich_mode"]  = False
    _st["rich_attrs"] = None
    _relayout_doc_view()


def _toggle_md_mode():
    """Переключить между сырым Markdown-текстом и терминальным отрисованным видом."""
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
    """Перепозиционировать фиктивный блочный курсор в текущую точку вставки (главный поток)."""
    if not _tv or not _cur_view or not _win:
        return
    sel = _tv.selectedRange()
    r   = AppKit.NSMakeRange(sel.location, 0)
    # firstRectForCharacterRange даёт прямоугольник точки вставки в экранных координатах
    screen_rect, _ = _tv.firstRectForCharacterRange_actualRange_(r, None)
    if screen_rect.size.height < 1:
        return
    # Преобразовать экран → окно → пространство координат text-view
    win_rect = _win.convertRectFromScreen_(screen_rect)
    tv_rect  = _tv.convertRect_fromView_(win_rect, None)
    h = max(tv_rect.size.height, 14.0)
    _cur_view.setFrame_(AppKit.NSMakeRect(tv_rect.origin.x, tv_rect.origin.y, 8.0, h))
    _cur_view.setHidden_(False)
    _cur_view.setNeedsDisplay_(True)


def _reposition_cluster():
    """Переместить панели кластера для отслеживания _cfg_panel (якорь развёрнутого режима)."""
    cfg = globals().get("_cfg_panel")
    if not cfg or not cfg.isVisible():
        return
    cf  = cfg.frame()
    cx, cy = int(cf.origin.x), int(cf.origin.y)
    for key, pname in [("hist", "_hist_panel"),
                       ("providers", "_prov_panel"),
                       ("editor", "_sc_editor_panel")]:
        if key not in _cluster_offsets:
            continue
        panel = globals().get(pname)
        if not panel or not panel.isVisible():
            continue
        dx, dy = _cluster_offsets[key]
        try:
            panel.setFrameOrigin_(AppKit.NSMakePoint(cx + dx, cy + dy))
        except Exception:
            pass


def _update_cluster_offsets():
    """Пересчитать смещения кластера после индивидуального перетаскивания панели кластера."""
    cfg = globals().get("_cfg_panel")
    if not cfg or not cfg.isVisible():
        return
    cf  = cfg.frame()
    cx, cy = int(cf.origin.x), int(cf.origin.y)
    for key, pname in [("hist", "_hist_panel"),
                       ("providers", "_prov_panel"),
                       ("editor", "_sc_editor_panel")]:
        panel = globals().get(pname)
        if panel and panel.isVisible():
            pf = panel.frame()
            _cluster_offsets[key] = (int(pf.origin.x) - cx, int(pf.origin.y) - cy)


def _cluster_anchor_pos():
    """Вычислить позицию якоря cfg чтобы кластер 2×2 не перекрывал развёрнутое окно.

    Кластер занимает 2 колонки: cfg/providers слева, hist/editor справа.
    Общая ширина кластера = 2*W + GAP. Нужно освободить ПОЛНУЮ ширину кластера
    от края главного окна, а не только ширину одной панели.
    Использует собственный экран _win чтобы оставаться на том же мониторе.
    """
    GAP       = _SNAP_GAP
    CW        = 2 * W + GAP   # total cluster width (2 columns of 440px + gap)
    if not _win:
        return 50, 400
    wf = _win.frame()
    wx, wy = int(wf.origin.x), int(wf.origin.y)
    ww, wh = int(wf.size.width), int(wf.size.height)

    # Выровнять верх кластера с верхом главного окна
    cy = wy + wh - H_PANEL

    screen = _win.screen() or AppKit.NSScreen.mainScreen()
    sf     = screen.visibleFrame() if screen else None
    if sf:
        sx, sy = int(sf.origin.x), int(sf.origin.y)
        sw, sh = int(sf.size.width), int(sf.size.height)

        cx_left  = wx - CW - GAP          # fully left of main window
        cx_right = wx + ww + GAP          # fully right of main window

        left_ok  = cx_left  >= sx
        right_ok = cx_right + CW <= sx + sw

        if left_ok:
            cx = cx_left
        elif right_ok:
            cx = cx_right
        else:
            # Ни одна сторона не помещается идеально — выбрать ту что меньше выходит за экран
            left_over  = max(0, sx - cx_left)
            right_over = max(0, cx_right + CW - (sx + sw))
            cx = cx_left if left_over <= right_over else cx_right

        # Ограничить чтобы хотя бы cfg (левая колонка) оставалась на экране
        cx = max(sx, min(cx, sx + sw - W))
        # Ограничить вертикально чтобы кластер не выходил за края экрана
        cy = max(sy + H_PANEL + GAP, min(cy, sy + sh - H_PANEL))
    else:
        cx = wx - CW - GAP

    return cx, cy


def _apply_cluster_offsets(cfg_panel, offsets):
    """Переместить cfg в якорную позицию, затем переместить каждую панель на (dx, dy) от cfg."""
    cx, cy = _cluster_anchor_pos()
    cf = cfg_panel.frame()
    cfg_panel.setFrameOrigin_(AppKit.NSMakePoint(cx, cy))
    panel_map = {"hist": "_hist_panel", "providers": "_prov_panel", "editor": "_sc_editor_panel"}
    for key, (dx, dy) in offsets.items():
        p = globals().get(panel_map.get(key, ""))
        if p and p.isVisible():
            p.setFrameOrigin_(AppKit.NSMakePoint(cx + dx, cy + dy))


def _cluster_grid_offsets(visible_keys):
    """Вычислить плотные смещения сетки 2×2 (относительно cfg в 0,0) для видимых панелей.

    Grid:
        [cfg ][hist ]   ← верхний ряд (dy=0)
        [prov][edit ]   ← нижний ряд (dy = -(H+GAP))
    cfg всегда вверху слева; другие панели заполняют оставшиеся слоты слева направо, сверху вниз.
    """
    GAP = _SNAP_GAP
    # Позиции слотов (col, row) в сетке — col 0/1, row 0/1
    SLOTS = [
        ("cfg",       0, 0),
        ("hist",      1, 0),
        ("providers", 0, 1),
        ("editor",    1, 1),
    ]
    offsets = {}
    for key, col, row in SLOTS:
        if key == "cfg":
            continue
        if key not in visible_keys:
            continue
        dx =  col * (W + GAP)
        dy = -row * (H_PANEL + GAP)   # macOS: вниз = меньший y
        offsets[key] = (dx, dy)
    return offsets


def _enter_cluster_mode():
    """Собрать все открытые панели в компактную сетку 2×2 слева от развёрнутого окна."""
    global _cluster_mode, _cluster_offsets, _cluster_was_open, _cluster_cfg_auto

    _cluster_was_open = set()
    for key, pname in [("hist", "_hist_panel"),
                       ("providers", "_prov_panel"),
                       ("editor", "_sc_editor_panel")]:
        p = globals().get(pname)
        if p and p.isVisible():
            _cluster_was_open.add(key)

    # Cfg всегда является якорем кластера — открыть если нужно
    _cluster_cfg_auto = not (globals().get("_cfg_panel") and _cfg_panel.isVisible())
    if _cluster_cfg_auto:
        _toggle_cfg_panel()

    cfg = globals().get("_cfg_panel")
    if not cfg or not cfg.isVisible():
        return

    _cluster_offsets = _cluster_grid_offsets(_cluster_was_open | {"cfg"})
    _apply_cluster_offsets(cfg, _cluster_offsets)
    _cluster_mode = True


def _exit_cluster_mode():
    """Восстановить панели кластера обратно в крепление к _win."""
    global _cluster_mode, _cluster_offsets

    _cluster_mode = False
    _cluster_offsets = {}

    # Закрыть панели которые не были открыты до разворачивания
    for key, pname, close_fn in [
        ("hist",      "_hist_panel",      lambda: _hist_panel.orderOut_(None) if _hist_panel else None),
        ("providers", "_prov_panel",      _close_providers_panel),
        ("editor",    "_sc_editor_panel", _close_editor_now),
    ]:
        if key not in _cluster_was_open:
            panel = globals().get(pname)
            if panel and panel.isVisible():
                try:
                    close_fn()
                except Exception:
                    pass

    # Закрыть cfg если он был автоматически открыт как якорь
    if _cluster_cfg_auto:
        _close_cfg_panel()

    # Восстановить позиционирование относительно _win для панелей что были открыты
    _reposition_attached_panels()


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
        _main(_enter_cluster_mode)
    else:
        _exit_cluster_mode()
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


def _do_win_resize(new_h, new_w=None, animate=True):
    """Изменить размер окна сохраняя фиксированным его ВЕРХНИЙ ПРАВЫЙ угол."""
    if not _win:
        return
    f    = _win.frame()
    top  = f.origin.y + f.size.height
    right = f.origin.x + f.size.width
    nw   = new_w if new_w is not None else f.size.width
    ny   = top - new_h
    nx   = right - nw    # сохранить фиксированным правый край
    _win.setFrame_display_animate_(
        AppKit.NSMakeRect(nx, ny, nw, new_h), True, animate
    )
    # Scroll view: autoresizing обрабатывает ширину; нужно только исправить высоту
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
    """Вычислить адаптивную сетку сценариев: [<] всегда слева, [>] всегда справа, заполнить между.

    Вычисляет _sc_page_size динамически из ширины окна чтобы кнопки заполняли доступное пространство.
    Стрелки навигации всегда на фиксированных крайних позициях; активны/неактивны в зависимости от позиции страницы.
    """
    global _sc_page, _sc_page_size
    scs = _st["scenarios"]
    n   = len(scs)

    NAV_W   = 26   # ширина кнопки навигации
    NAV_GAP = 4    # отступ между стрелкой и первым/последним сценарием
    SC_W    = 54   # ширина кнопки сценария
    SEP_W   = 14   # ширина разделителя ·
    MARGIN  = 8    # отступ окна слева/справа

    # Стрелки навигации всегда на фиксированных краевых позициях
    nav_y = BTN_Y + 11
    if _sc_prev_btn:
        _sc_prev_btn.setFrame_(AppKit.NSMakeRect(MARGIN, nav_y, NAV_W, 24))
    if _sc_next_btn:
        _sc_next_btn.setFrame_(AppKit.NSMakeRect(w - MARGIN - NAV_W, nav_y, NAV_W, 24))

    # Вычисляем количество слотов сценариев между двумя стрелками
    inner_w = w - 2 * (MARGIN + NAV_W + NAV_GAP)
    # n слотов: SC_W*n + SEP_W*(n-1) = (SC_W+SEP_W)*n - SEP_W ≤ inner_w
    _sc_page_size = max(1, (inner_w + SEP_W) // (SC_W + SEP_W))

    # Ограничиваем текущую страницу
    max_page = max(0, (n - 1) // _sc_page_size) if n > 0 else 0
    if _sc_page > max_page:
        _sc_page = max_page

    page_start = _sc_page * _sc_page_size
    page_end   = min(page_start + _sc_page_size, n)
    page_count = page_end - page_start

    # Позиционировать слоты сценариев начиная после левой стрелки
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

    # Включить/отключить стрелки навигации в зависимости от текущей позиции страницы
    if _sc_prev_btn:
        _sc_prev_btn.setEnabled_(_sc_page > 0)
    if _sc_next_btn:
        _sc_next_btn.setEnabled_(page_end < n)


def _refresh_scenario_colors():
    """Обновить цвета кнопок сценариев: обработка=жёлтый, активный=яркий, недоступный=красный, обычный=тёмный."""
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
    """Проверить доступность модели для всех сценариев в фоне, затем обновить цвета кнопок."""
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

    # Восстановить иконку шестерёнки (скрыта show_processing); expand/close остаются скрытыми постоянно
    if _cfg_hdr_btn: _cfg_hdr_btn.setHidden_(False)
    if _proc_sc_lbl: _proc_sc_lbl.setHidden_(True)

    # Восстановить волноформу; пересчитать адаптивную ширину EQ
    if _wf:
        _wf.setHidden_(False)
    if _proc_eq_v:
        _proc_eq_v.setHidden_(True)
    _layout_header_wf()

    _stop_timer()


def set_active_scenario(idx):
    """Подсветить активный сценарий; показать 2-кнопочную панель результата или восстановить 3-кнопочную строку."""
    _st["active_sc"] = idx
    _st["sc_picker"] = False   # close picker whenever scenario state changes
    def _():
        _refresh_scenario_colors()
        _show_buttons(True)
        for b in _rich_blocks:
            b.set_sc_undo(False)
    _main(_)


def is_editing_scenario() -> bool:
    """Вернуть True пока открыта панель редактора сценариев (горячая клавиша должна быть заблокирована)."""
    return _editing_scenario


def set_undo_scenario_callback(fn):
    global _on_undo_sc_cb
    _on_undo_sc_cb = fn


_HDR_GAP = 8          # gap between app name and EQ, and between EQ and gear icon


def _live_hdr_item_y() -> int:
    """Вернуть HDR_ITEM_Y на основе реальной текущей высоты окна (корректно в развёрнутом режиме)."""
    h = int(_win.frame().size.height) if _win else H
    return (h - HDR_H) + (HDR_H - HDR_ITEM_H) // 2


def _layout_header_wf():
    """Измерить ширину имени приложения, позиционировать метку, затем растянуть EQ заполняя оставшееся место.
    Должен выполняться в главном потоке."""
    ICON_END  = STS_X + 22 + 4    # правый край иконки + внутренний отступ = 36
    # Вычислить левый край иконки шестерёнки из реальной ширины окна (учитывает развёрнутый режим)
    cw        = int(_win.frame().size.width) if _win else W
    RIGHT_END = cw - CFG_H_W - 6 - _HDR_GAP

    font  = _mono(11)
    d     = {AppKit.NSFontAttributeName: font}
    name  = _prev_app_name or ""
    raw_w = int(AppKit.NSString.stringWithString_(name).sizeWithAttributes_(d).width) if name else 0
    name_w = raw_w + 6

    # Имя может занимать до половины доступного пространства
    max_name = (RIGHT_END - ICON_END - _HDR_GAP) // 2
    name_w   = max(0, min(name_w, max_name))
    name_end = ICON_END + name_w

    iy = _live_hdr_item_y()
    if _proc_app_lbl:
        _proc_app_lbl.setFrame_(AppKit.NSMakeRect(ICON_END, iy - 2, name_w, HDR_ITEM_H))

    # EQ заполняет всё оставшееся пространство: от name_end+gap до иконки шестерёнки
    eq_x = name_end + _HDR_GAP
    eq_w = max(40, RIGHT_END - eq_x)
    if _wf:
        _wf.setFrame_(AppKit.NSMakeRect(eq_x, iy, eq_w, HDR_ITEM_H))
    if _proc_eq_v:
        _proc_eq_v.setFrame_(AppKit.NSMakeRect(eq_x, iy, eq_w, HDR_ITEM_H))

    return name_end


def _show_target_app_header():
    """Показать иконку + имя приложения в заголовке. Вызывать в главном потоке. Нет-оп во время активной обработки."""
    if _proc_sc_idx is not None:
        return   # show_processing() управляет этим состоянием
    if _app_icon_v:
        _app_icon_v.setHidden_(False)
    if _proc_app_lbl:
        _proc_app_lbl.setStringValue_(_prev_app_name)
        _proc_app_lbl.setHidden_(False)
    if _lbl:
        _lbl.setHidden_(True)
    _layout_header_wf()


def _hide_target_app_header():
    """Скрыть иконку + имя, используется только когда оверлей переходит в idle."""
    if _app_icon_v:
        _app_icon_v.setHidden_(True)
    if _proc_app_lbl:
        _proc_app_lbl.setHidden_(True)
    if _lbl:
        _lbl.setHidden_(False)
    # Сбросить волноформу в фиксированную центрированную позицию
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
        # Обновить иконку
        if _app_icon_v:
            if app:
                try:
                    img = app.icon()
                    if img:
                        _app_icon_v.setImage_(img)
                except Exception:
                    pass

        # В любом активном режиме — обновить иконку + имя в заголовке
        mode = _st.get("mode", "idle")
        if mode != "idle":
            _show_target_app_header()
    _main(_)


# ── Выпадающие панели ──────────────────────────────────────────────────────────

def _close_cfg_panel():
    """Закрыть панель cfg и восстановить Y главного окна если оно было сдвинуто для освобождения места."""
    global _cfg_panel, _pre_cfg_win_y
    if _cfg_panel:
        _cfg_panel.orderOut_(None)
        _cfg_panel.close()
        _cfg_panel = None
    if _pre_cfg_win_y is not None and _win:
        fr = _win.frame()
        _win.setFrameOrigin_(AppKit.NSMakePoint(fr.origin.x, _pre_cfg_win_y))
        _pre_cfg_win_y = None
        # Панель истории возвращается к исходному Y вместе с главным окном
        if _hist_panel and _hist_panel.isVisible():
            _reposition_attached_panels()

def _close_cfg_panel_rebuild():
    """Закрыть только панель cfg — не восстанавливать позицию окна; используется при немедленном переоткрытии панели."""
    global _cfg_panel
    if _cfg_panel:
        _cfg_panel.orderOut_(None)
        _cfg_panel.close()
        _cfg_panel = None


def _make_drop_panel(w, h):
    """Создать терминально-стилизованную плавающую панель без рамки."""
    p = _DropPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        AppKit.NSMakeRect(0, 0, w, h),
        AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered, False,
    )
    p.setOpaque_(False)
    p.setBackgroundColor_(AppKit.NSColor.clearColor())
    p.setLevel_(AppKit.NSFloatingWindowLevel + 1)
    p.setHasShadow_(True)
    p.setHidesOnDeactivate_(False)   # оставаться видимой когда приложение теряет фокус
    bg = TerminalView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, w, h))
    bg.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
    p.setContentView_(bg)
    return p


def _panel_origin(pw, ph, align="right", prefer="below"):
    """Позиционировать drop panel смежно с главным окном.
    prefer='below'  → открывается ниже главного окна (запасной: выше если за экраном)
    prefer='above'  → всегда открывается выше главного окна (панель cfg)
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
    """Вернуть NSPoint для позиционирования панели истории справа или слева от главного окна.
    Вернуть None если нет места ни с одной стороны.
    """
    if not _win:
        return None
    mf  = _win.frame()
    gap = 6
    py  = mf.origin.y   # выровнять нижние края
    screen = AppKit.NSScreen.mainScreen()
    vis = screen.visibleFrame() if screen else None
    # Попробовать правую сторону сначала
    px_right = mf.origin.x + mf.size.width + gap
    if vis is None or px_right + pw <= vis.origin.x + vis.size.width:
        return AppKit.NSMakePoint(px_right, py)
    # Откат на левую сторону
    px_left = mf.origin.x - pw - gap
    if vis is None or px_left >= vis.origin.x:
        return AppKit.NSMakePoint(px_left, py)
    return None   # no room on either side


def _reposition_attached_panels():
    """Переместить магнитно-прикреплённые панели для отслеживания позиции _win."""
    if _cluster_mode:
        return   # развёрнутый режим: панели кластеризуются вокруг cfg, не _win
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
        try:
            panel.setFrameOrigin_(AppKit.NSMakePoint(nx, ny))
        except Exception:
            pass


def _reset_panels_layout():
    """🎯 переключение: первое нажатие показывает все панели на их СОХРАНЁННЫХ позициях; второе — скрывает все."""
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
    # Показать панели там где их сохранённые смещения их помещают (без сброса смещений)
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
    """[🔄] Reset panel layout.
    In expanded mode → 2×2 compact grid (panels gather as a square).
    In normal mode   → classic cross around main window, all magnets ON.
    """
    global _magnet_on, _magnet_offset, _magnet_free_pos, _panels_reset_open, _cluster_mode, _cluster_offsets

    if globals().get("_expanded"):
        # ── Развёрнутый / кластерный режим: собрать панели в сетку 2×2 ──────────────
        # Убедиться что cfg открыт (якорь кластера)
        if not (_cfg_panel and _cfg_panel.isVisible()):
            _toggle_cfg_panel()
        # Открыть все панели которые имеет смысл показать
        if not (_hist_panel and _hist_panel.isVisible()):
            history = _on_history_cb() if _on_history_cb else []
            _show_hist_panel(history)
        if not (_prov_panel and _prov_panel.isVisible()):
            _toggle_providers_panel()
        sc_list = _st.get("scenarios", [])
        if sc_list and not (_sc_editor_panel and _sc_editor_panel.isVisible()):
            _show_sc_editor_impl(0)

        cfg = globals().get("_cfg_panel")
        if cfg and cfg.isVisible():
            visible_keys = set()
            for k, pn in [("hist","_hist_panel"),("providers","_prov_panel"),("editor","_sc_editor_panel")]:
                p = globals().get(pn)
                if p and p.isVisible():
                    visible_keys.add(k)
            _cluster_offsets = _cluster_grid_offsets(visible_keys | {"cfg"})
            _apply_cluster_offsets(cfg, _cluster_offsets)
            _cluster_mode = True
        return

    # ── Нормальный режим: классический крест, все магниты ВКЛ ─────────────────────────────
    _cluster_mode    = False
    _cluster_offsets = {}
    _panels_reset_open = True
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
    _magnet_offset = {
        "cfg":       (0,         wh + 4),
        "hist":      (0,        -(H_PANEL + G)),
        "editor":    (ww + G,    0),
        "providers": (-(ww + G), 0),
    }
    _magnet_save()
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
    """Восстановить состояние оверлея + фокус после закрытия drop panel через Escape."""
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
    """Закрыть все открытые drop panels."""
    if _hist_panel and _hist_panel.isVisible():
        _hist_panel.orderOut_(None)
    _close_cfg_panel()


def _hist_filter_items(items, mode):
    """Вернуть элементы соответствующие активному режиму фильтра."""
    if mode == "sessions":
        return [i for i in items if i.get("type") == "session"]
    elif mode == "blocks":
        return [i for i in items if i.get("type") != "session"]
    return list(items)   # "mixed" — все


def _build_hist_docview(ctrl, scroll_w, scroll_h, CHK_W, CHK_R):
    """Собрать (или пересобрать) перевёрнутый docview для области прокрутки истории."""
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

            item_btn = _HistItemView.alloc().initWithFrame_(
                AppKit.NSMakeRect(ITEM_X, row_y + 1, ITEM_W, DP_HIST_ITEM_H - 2))
            item_btn.setAttributedTitle_(ns_str)
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
    # Сбросить видимость кнопок нижнего раздела действий
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


_menu_items = {}   # {key: NSMenuItem} для живого обновления языка


def _refresh_menu_titles():
    """Обновить заголовки пунктов меню статус-бара в соответствии с текущим языком."""
    for key, item in _menu_items.items():
        item.setTitle_(_T(key))
    btn = _status_bar_item.button() if _status_bar_item else None
    if btn:
        btn.setToolTip_(_T("menu_tooltip"))


def _setup_status_bar():
    """Создать пункт статус-бара macOS с локализованным меню."""
    global _status_bar_item
    bar = AppKit.NSStatusBar.systemStatusBar()
    _status_bar_item = bar.statusItemWithLength_(AppKit.NSSquareStatusItemLength)
    btn = _status_bar_item.button()
    if btn:
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hush2.png")
        ns_img = AppKit.NSImage.alloc().initWithContentsOfFile_(icon_path)
        if ns_img:
            ns_img.setSize_(AppKit.NSMakeSize(18, 18))
            ns_img.setTemplate_(True)   # macOS автоматически адаптирует к светлому/тёмному меню-бару
            btn.setImage_(ns_img)
        else:
            btn.setTitle_("H")
            btn.setFont_(_mono(13, True))
        btn.setToolTip_(_T("menu_tooltip"))
    menu = AppKit.NSMenu.alloc().init()

    open_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        _T("menu_open"), "openHush:", "")
    open_item.setTarget_(_btn_t)
    # Показать ⇧⌥ выравненным справа на той же строке (нативное отображение горячей клавиши macOS)
    open_item.setKeyEquivalent_("⌥")
    open_item.setKeyEquivalentModifierMask_(AppKit.NSEventModifierFlagShift)
    menu.addItem_(open_item)
    _menu_items["menu_open"] = open_item

    menu.addItem_(AppKit.NSMenuItem.separatorItem())

    about_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        _T("menu_about"), "showAbout:", "")
    about_item.setTarget_(_btn_t)
    menu.addItem_(about_item)
    _menu_items["menu_about"] = about_item

    menu.addItem_(AppKit.NSMenuItem.separatorItem())

    login_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        _T("menu_login"), "toggleLaunchAtLogin:", "")
    login_item.setTarget_(_btn_t)
    login_item.setState_(1 if _is_launch_at_login() else 0)
    menu.addItem_(login_item)
    _menu_items["menu_login"] = login_item
    _btn_t._login_menu_item = login_item

    menu.addItem_(AppKit.NSMenuItem.separatorItem())

    quit_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        _T("menu_quit"), "quitApp:", "")
    quit_item.setTarget_(_btn_t)
    menu.addItem_(quit_item)
    _menu_items["menu_quit"] = quit_item

    menu.setDelegate_(_btn_t)   # menuNeedsUpdate_ обновляет галочку при открытии
    _status_bar_item.setMenu_(menu)
    try:
        _status_bar_item.setVisible_(True)
    except Exception:
        pass


_LIGHT_THEMES = {"paper", "sky", "sand", "arctic"}

class _GithubIconView(AppKit.NSView):
    """Кнопка-иконка ссылки GitHub для панели «О программе», тот же размер что _WalletView."""
    _URL       = "https://github.com/alexbic/hush"
    _img_dark  = None   # тёмный знак — для тем со светлым фоном
    _img_light = None   # светлый знак — для тем с тёмным фоном

    def acceptsFirstMouse_(self, _e): return True
    def isOpaque(self): return False

    def mouseDown_(self, _event): pass  # поглощаем, чтобы _AboutBgView не закрыл панель

    def mouseUp_(self, _event):
        import subprocess
        subprocess.Popen(["open", self._URL])

    def drawRect_(self, _rect):
        w = self.bounds().size.width
        h = self.bounds().size.height
        _dir = os.path.dirname(os.path.abspath(__file__))

        if self.__class__._img_dark is None:
            self.__class__._img_dark = AppKit.NSImage.alloc().initWithContentsOfFile_(
                os.path.join(_dir, "github-mark-dark.png"))
        if self.__class__._img_light is None:
            self.__class__._img_light = AppKit.NSImage.alloc().initWithContentsOfFile_(
                os.path.join(_dir, "github-mark-light.png"))

        is_light_theme = _st.get("theme", "emerald") in _LIGHT_THEMES
        ns_img = self.__class__._img_dark if is_light_theme else self.__class__._img_light

        if ns_img:
            icon_pt = min(w, h) * 0.78
            ix = (w - icon_pt) / 2
            iy = (h - icon_pt) / 2
            ns_img.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                AppKit.NSMakeRect(ix, iy, icon_pt, icon_pt),
                AppKit.NSZeroRect,
                AppKit.NSCompositingOperationSourceOver,
                1.0, True, None,
            )

    def updateTrackingAreas(self):
        for a in list(self.trackingAreas()):
            self.removeTrackingArea_(a)
        opts = (AppKit.NSTrackingMouseEnteredAndExited |
                AppKit.NSTrackingActiveAlways |
                AppKit.NSTrackingInVisibleRect)
        self.addTrackingArea_(
            AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(), opts, self, None))
        objc.super(_GithubIconView, self).updateTrackingAreas()

    def mouseEntered_(self, _event):
        tt = getattr(self, '_tt_text', '')
        if tt:
            _about_tt_show(tt)

    def mouseExited_(self, _event):
        _about_tt_hide()


def _about_tt_show(text):
    global _tt_timer
    _about_tt_hide()
    import threading
    def _show():
        _main(lambda: _about_tt_create(text))
    t = threading.Timer(0.6, _show)
    t.daemon = True
    t.start()
    _tt_timer = t


def _about_tt_hide():
    global _tt_panel, _tt_timer
    if _tt_timer:
        _tt_timer.cancel()
        _tt_timer = None
    if _tt_panel:
        try:
            _tt_panel.close()
        except Exception:
            pass
        _tt_panel = None


def _about_tt_create(text):
    global _tt_panel
    if _tt_panel:
        try:
            _tt_panel.close()
        except Exception:
            pass
    tw = max(int(len(text) * 6.8) + 24, 80)
    th = 22
    loc = AppKit.NSEvent.mouseLocation()
    tx  = loc.x - tw / 2
    ty  = loc.y - th - 18
    scr = AppKit.NSScreen.mainScreen().frame()
    tx = max(scr.origin.x + 4, min(tx, scr.origin.x + scr.size.width  - tw - 4))
    ty = max(scr.origin.y + 4, min(ty, scr.origin.y + scr.size.height - th - 4))
    panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        AppKit.NSMakeRect(tx, ty, tw, th),
        AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered, False,
    )
    panel.setOpaque_(False)
    panel.setBackgroundColor_(AppKit.NSColor.clearColor())
    panel.setLevel_(AppKit.NSFloatingWindowLevel + 4)
    panel.setIgnoresMouseEvents_(True)
    panel.setHidesOnDeactivate_(False)
    panel.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
        AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
    v = _TTView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, tw, th))
    v._text = text
    panel.contentView().addSubview_(v)
    panel.orderFront_(None)
    _tt_panel = panel


def _show_about_view():
    """Показать карточку «О программе» как отдельный NSPanel центрированный на экране."""
    global _about_panel

    _hide_about_view()

    AW, AH = 560, 480
    PAD    = 16

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

    bg = _AboutBgView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, AW, AH))
    bg.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
    ap.setContentView_(bg)

    lang = _st.get("lang", "ru")

    # ── Размеры иконок ────────────────────────────────────────────────────────
    ICON_W = _WalletView._VW   # 90
    ICON_H = _WalletView._VH   # 68
    LBL_H  = 16

    # ── Верхний левый угол: версия ───────────────────────────────────────────
    ver_tf = AppKit.NSTextField.labelWithString_("v1.0")
    ver_tf.setEditable_(False); ver_tf.setBezeled_(False); ver_tf.setDrawsBackground_(False)
    ver_tf.setFont_(_mono(9, False))
    ver_tf.setTextColor_(C_GREEN_DIM)
    ver_tf.setAlignment_(AppKit.NSTextAlignmentCenter)
    ver_tf.setFrame_(AppKit.NSMakeRect(PAD, AH - PAD - LBL_H, ICON_W, LBL_H))
    ver_tf.setAutoresizingMask_(AppKit.NSViewMaxXMargin | AppKit.NSViewMinYMargin)
    bg.addSubview_(ver_tf)

    # ── Верхний правый угол: Инструкция ──────────────────────────────────────
    doc_labels = {"ru": "Инструкция", "es": "Instrucciones"}
    doc_label  = doc_labels.get(lang, "Documentation")
    dc_btn = _mklinkbtn(doc_label, color=C_GREEN, size=10)
    dc_btn.setFrame_(AppKit.NSMakeRect(AW - PAD - ICON_W, AH - PAD - LBL_H, ICON_W, LBL_H))
    dc_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMinYMargin)
    dc_btn.setTarget_(_btn_t)
    dc_btn.setAction_(BtnTarget.aboutDocs_)
    bg.addSubview_(dc_btn)

    # ── Нижний левый угол: GitHub ─────────────────────────────────────────────
    gh_icon = _GithubIconView.alloc().initWithFrame_(
        AppKit.NSMakeRect(PAD, PAD, ICON_W, ICON_H))
    gh_icon.setAutoresizingMask_(AppKit.NSViewMaxXMargin | AppKit.NSViewMaxYMargin)
    gh_icon._tt_text = {"ru": "Открыть репозиторий на GitHub", "en": "Open GitHub repository", "es": "Abrir repositorio en GitHub"}.get(lang, "GitHub")
    bg.addSubview_(gh_icon)

    # ── Нижний правый угол: Donation / Wallet (чуть меньше, чуть левее) ────
    _WW = 68      # уменьшенная ширина кошелька в About
    _WH = 51      # уменьшенная высота кошелька в About
    _WX = AW - PAD - _WW - 22          # правый отступ + сдвиг влево
    _WY = PAD + (ICON_H - _WH) // 2   # вертикальный центр в ряду иконок
    wallet_v = _WalletView.alloc().initWithFrame_(
        AppKit.NSMakeRect(_WX, _WY, _WW, _WH))
    wallet_v.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMaxYMargin)
    wallet_v._tt_text = {"ru": "Поддержать проект", "en": "Support the project", "es": "Apoyar el proyecto"}.get(lang, "Donate")
    bg.addSubview_(wallet_v)

    # ── Copyright — по центру между нижними иконками ──────────────────────────
    CX = PAD + ICON_W + 10
    CW = AW - 2 * (PAD + ICON_W + 10)
    CR_Y = PAD + (ICON_H - 12) // 2
    cr_btn = _mklinkbtn("© 2026 Alexander Bikmukhametov", color=C_GREEN_DIM, size=9)
    cr_btn.setFrame_(AppKit.NSMakeRect(CX, CR_Y, CW, 12))
    cr_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMaxXMargin | AppKit.NSViewMaxYMargin)
    cr_btn.setTarget_(_btn_t)
    cr_btn.setAction_(BtnTarget.aboutSite_)
    bg.addSubview_(cr_btn)

    # ── "ГОЛОСОВОЙ НАБОР" — сразу над нижними иконками ───────────────────────
    title_texts = {"ru": "ГОЛОСОВОЙ НАБОР", "es": "DICTADO DE VOZ"}
    title_text  = title_texts.get(lang, "VOICE INPUT")
    TITLE_H = 34
    TITLE_Y = PAD + ICON_H + 6
    title_font = (AppKit.NSFont.fontWithName_size_("Futura", 20)
               or AppKit.NSFont.fontWithName_size_("Avenir Next", 20)
               or AppKit.NSFont.fontWithName_size_("Avenir", 20)
               or AppKit.NSFont.fontWithName_size_("Helvetica Neue", 20)
               or AppKit.NSFont.systemFontOfSize_(20))
    title_tf = AppKit.NSTextField.labelWithString_(title_text)
    title_tf.setEditable_(False); title_tf.setBezeled_(False); title_tf.setDrawsBackground_(False)
    title_tf.setFont_(title_font)
    title_tf.setTextColor_(C_GREEN)
    title_tf.setAlignment_(AppKit.NSTextAlignmentCenter)
    title_tf.setFrame_(AppKit.NSMakeRect(0, TITLE_Y, AW, TITLE_H))
    title_tf.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMaxYMargin)
    bg.addSubview_(title_tf)

    # ── Картинка бренда — центральная область ────────────────────────────────
    IMG_Y = TITLE_Y + TITLE_H + 8
    img_w = AW - PAD * 2
    img_h = AH - PAD - LBL_H - 8 - IMG_Y

    img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hush_brand_full.png")
    ns_img   = AppKit.NSImage.alloc().initWithContentsOfFile_(img_path)
    if ns_img:
        iv = AppKit.NSImageView.alloc().initWithFrame_(
            AppKit.NSMakeRect(PAD, IMG_Y, img_w, img_h))
        iv.setImage_(ns_img)
        iv.setImageScaling_(3)   # NSImageScaleProportionallyUpOrDown
        iv.setImageAlignment_(0) # NSImageAlignCenter
        iv.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        bg.addSubview_(iv)
    else:
        tf = AppKit.NSTextField.labelWithString_("[ HUSH ]")
        tf.setEditable_(False); tf.setBezeled_(False); tf.setDrawsBackground_(False)
        tf.setFont_(_mono(20, True)); tf.setTextColor_(C_GREEN_DIM)
        tf.setFrame_(AppKit.NSMakeRect(PAD, IMG_Y + img_h // 2 - 14, img_w, 28))
        bg.addSubview_(tf)

    ap.setAcceptsMouseMovedEvents_(True)
    ap.makeKeyAndOrderFront_(None)
    _about_panel = ap


def _hide_about_view():
    """Закрыть и освободить панель карточки «О программе»."""
    global _about_panel
    _about_tt_hide()
    if _about_panel:
        _about_panel.close()
        _about_panel = None


def _show_hist_panel(history):
    global _hist_panel, _hist_ctrl, _hist_panel_side
    # Переключение: закрыть если уже открыто
    if _hist_panel and _hist_panel.isVisible():
        _hist_panel.orderOut_(None)
        _hist_panel_side = None
        # Сбрасываем режим браузера истории если активен
        if _st["mode"] == "history_open":
            _st["mode"] = "idle" if not _st["text"] else "ready"
        _cfg_saved.setdefault("panels_open", {})["hist"] = False
        _save_settings()
        return

    CHK_W   = 26
    CHK_R   = 16   # правый отступ — оставляет чекбоксы чистыми от 3px полосы прокрутки
    HDR_H   = 32
    FOOT_H  = 30
    BOT_PAD = 10
    FIXED   = HDR_H + FOOT_H + BOT_PAD
    # Размер панели по количеству отфильтрованных элементов (текущая активная вкладка)
    n_filtered = len(_hist_filter_items(history, _hist_filter))
    pw      = W    # panel always normal width regardless of expanded mode
    mf      = _win.frame()
    gap     = 6
    screen  = AppKit.NSScreen.mainScreen()
    vis     = screen.visibleFrame() if screen else None

    ph = H_PANEL
    wx, wy = int(mf.origin.x), int(mf.origin.y)
    ww, wh = int(mf.size.width), int(mf.size.height)
    px, py = _calc_panel_pos("hist", wx, wy, ww, wh, pw, ph)
    panel_origin  = AppKit.NSMakePoint(px, py)
    _hist_panel_side = "left"

    if _hist_panel:
        _hist_panel.orderOut_(None)
        _hist_panel.close()

    _hist_panel = _make_drop_panel(pw, ph)
    _hist_panel._panel_key = "hist"
    cv = _hist_panel.contentView()

    # ── Контроллер ───────────────────────────────────────────────────────────
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
    ctrl._tab_labels  = {}   # mode → отображаемая метка (нельзя задать атрибуты у NSButton)
    ctrl._pw          = pw
    ctrl._CHK_W       = CHK_W
    ctrl._CHK_R       = CHK_R
    ctrl._on_delete   = _on_history_delete_cb
    ctrl._on_merge    = _on_history_merge_cb
    _hist_ctrl        = ctrl

    # ── Заголовок: три вкладки фильтра + чекбокс «выбрать все» ──────────────────────
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

    # ── Прокручиваемый список ───────────────────────────────────────────────────────
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

    # ── Нижняя строка: [ЗАКРЫТЬ] всегда видна + удалить / объединить / добавить / заменить ──
    cv.addSubview_(_sep_line(0, BOT_PAD + FOOT_H - 1, pw, pin="bottom"))

    CLS_W  = 90
    btn_w  = 72
    f_gap  = 5
    # кнопки действий центрированы в пространстве правее кнопки закрытия
    action_x0 = CLS_W + 10
    bx0       = action_x0 + (pw - action_x0 - btn_w * 4 - f_gap * 3) // 2

    close_btn = _mkbtn(_T("btn_close"), color=C_GREEN_DIM, size=9)
    close_btn.setFrame_(AppKit.NSMakeRect(8, BOT_PAD + 4, CLS_W, 22))
    close_btn.setTarget_(_btn_t)
    close_btn.setAction_(BtnTarget.histClose_)
    cv.addSubview_(close_btn)

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

    # В режиме кластера: переопределяем panel_origin для следования смещению кластера
    if _cluster_mode:
        cfg = globals().get("_cfg_panel")
        if cfg and cfg.isVisible():
            cf = cfg.frame()
            cx, cy = int(cf.origin.x), int(cf.origin.y)
            if "hist" in _cluster_offsets:
                dx, dy = _cluster_offsets["hist"]
            else:
                # Hist ещё не был в кластере — назначить слот и добавить смещение
                dx, dy = W + _SNAP_GAP, 0   # по умолчанию: справа от cfg (сетка col 1)
                _cluster_offsets["hist"] = (dx, dy)
            panel_origin = AppKit.NSMakePoint(cx + dx, cy + dy)

    _hist_panel.setFrameOrigin_(panel_origin)
    AppKit.NSApp.activateIgnoringOtherApps_(True)
    _hist_panel.makeKeyAndOrderFront_(None)
    _cfg_saved.setdefault("panels_open", {})["hist"] = True
    _save_settings()


def _sc_model_from_refs() -> str:
    """Прочитать попапы провайдера + модели → 'provider:model' или '' для авто."""
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
    """Сбросить попапы провайдера + модели из строки вида 'anthropic:claude-...'."""
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
    """True если поля редактора отличаются от значений при открытии редактора."""
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
    """Закрыть панель редактора сценариев."""
    global _sc_editor_panel, _sc_edit_pending, _editing_scenario
    _editing_scenario = False
    if _sc_editor_panel:
        _sc_editor_panel.orderOut_(None)
        _sc_editor_panel.close()
        _sc_editor_panel = None
    _sc_edit_pending = None
    # Пересобрать панель cfg чтобы подсвеченная карточка сбросила цвет к обычному
    if _cfg_panel and _cfg_panel.isVisible():
        _close_cfg_panel_rebuild()
        _toggle_cfg_panel()
        # В режиме кластера: перемещаем все панели для восстановления сетки кластера
        if _cluster_mode:
            cfg = globals().get("_cfg_panel")
            if cfg and cfg.isVisible():
                _apply_cluster_offsets(cfg, _cluster_offsets)
    if pending_fn:
        pending_fn()


def _maybe_close_editor(pending_fn=None):
    """Если редактор имеет несохранённые изменения, показать оверлей подтверждения; иначе закрыть напрямую."""
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
    """Оверлей в центре панели редактора: сохранить / отменить / закрыть."""
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
    """Оверлей в панели редактора запрашивающий подтверждение удаления кастомного сценария."""
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
    """Вернуть метку сценария для указанного языка, максимум 6 символов. EN — запасной вариант."""
    label = sc.get("label", {})
    if isinstance(label, dict):
        txt = label.get(lang) or label.get("en") or label.get("ru") or label.get("es") or "?"
    else:
        txt = str(label)
    return txt[:6]


def _update_sc_cfg_colors():
    """Синхронизировать цвета кнопок сценариев на панели cfg с текущим открытым редактором (если есть)."""
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
                # Активный редактор: все части голубые
                a = {AppKit.NSFontAttributeName: _mono(9),
                     AppKit.NSForegroundColorAttributeName: C_CYAN,
                     AppKit.NSParagraphStyleAttributeName: ps}
                for part in ("·", label, "·"):
                    mstr.appendAttributedString_(
                        AppKit.NSAttributedString.alloc().initWithString_attributes_(part, a))
            else:
                # Неактивный: точки голубые, текст зелёный (исходный стиль)
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


def _panel_appearance():
    """NSAppearance для панелей: светлая/тёмная по текущей теме."""
    name = (AppKit.NSAppearanceNameAqua
            if _st.get("theme", "emerald") in _LIGHT_THEMES
            else AppKit.NSAppearanceNameDarkAqua)
    return AppKit.NSAppearance.appearanceNamed_(name)

# ── Кастомный дропдаун (замена NSPopUpButton) ─────────────────────────────────
_open_dropdown_panel = None
_open_dropdown_ref   = None   # _TerminalPopup, открывший дропдаун


class _TerminalPopup(AppKit.NSView):
    """Кастомный дропдаун в стиле терминала — совместим с NSPopUpButton API."""
    _items    = []
    _selected = 0
    _target   = None
    _action   = None
    _hovered  = False
    _enabled  = True

    def initWithFrame_(self, frame):
        self = objc.super(_TerminalPopup, self).initWithFrame_(frame)
        if self is None:
            return None
        self._items    = []
        self._selected = 0
        self._target   = None
        self._action   = None
        self._hovered  = False
        self._enabled  = True
        return self

    # ── NSPopUpButton-совместимый API ─────────────────────────────────────────
    def addItemsWithTitles_(self, titles):
        self._items.extend(list(titles))
        self.setNeedsDisplay_(True)

    def addItemWithTitle_(self, title):
        self._items.append(str(title))
        self.setNeedsDisplay_(True)

    def removeAllItems(self):
        self._items    = []
        self._selected = 0
        self.setNeedsDisplay_(True)

    def setFont_(self, font):
        pass

    def selectItemWithTitle_(self, title):
        if title in self._items:
            self._selected = self._items.index(title)
            self.setNeedsDisplay_(True)

    def selectItemAtIndex_(self, idx):
        self._selected = max(0, min(idx, len(self._items) - 1))
        self.setNeedsDisplay_(True)

    def titleOfSelectedItem(self):
        if self._items and 0 <= self._selected < len(self._items):
            return self._items[self._selected]
        return ""

    def itemTitleAtIndex_(self, i):
        return self._items[i] if 0 <= i < len(self._items) else ""

    def numberOfItems(self):
        return len(self._items)

    def setTarget_(self, t):
        self._target = t

    def setAction_(self, a):
        self._action = a

    def setEnabled_(self, flag):
        self._enabled = bool(flag)
        self.setNeedsDisplay_(True)

    # ── Рисование ─────────────────────────────────────────────────────────────
    def isOpaque(self):
        return False

    def acceptsFirstMouse_(self, _e):
        return True

    def drawRect_(self, rect):
        b = self.bounds()
        w = b.size.width
        h = b.size.height
        r, g, b_ = C_BG
        f = 2.2 if (self._hovered and self._enabled) else 1.5
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSMakeRect(0, 0, w, h), 3, 3)
        _rgba(min(r * f, 1.0), min(g * f, 1.0), min(b_ * f, 1.0)).setFill()
        path.fill()
        bord = C_GREEN_BORD if self._enabled else C_GREEN_DIM
        bord.setStroke()
        path.setLineWidth_(0.7)
        path.stroke()
        ARROW_W = 14
        title   = self.titleOfSelectedItem() or "—"
        txt_col = C_TEXT if self._enabled else C_IDLE
        AppKit.NSString.stringWithString_(title).drawInRect_withAttributes_(
            AppKit.NSMakeRect(6, (h - 12) / 2, w - ARROW_W - 8, 13),
            {AppKit.NSFontAttributeName: _mono(10),
             AppKit.NSForegroundColorAttributeName: txt_col})
        AppKit.NSString.stringWithString_("▾").drawInRect_withAttributes_(
            AppKit.NSMakeRect(w - ARROW_W, (h - 11) / 2, ARROW_W - 2, 13),
            {AppKit.NSFontAttributeName: _mono(9),
             AppKit.NSForegroundColorAttributeName: C_GREEN_DIM})

    # ── Мышь ──────────────────────────────────────────────────────────────────
    def updateTrackingAreas(self):
        for a in list(self.trackingAreas()):
            self.removeTrackingArea_(a)
        self.addTrackingArea_(AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(),
            AppKit.NSTrackingMouseEnteredAndExited |
            AppKit.NSTrackingActiveAlways |
            AppKit.NSTrackingInVisibleRect,
            self, None))
        objc.super(_TerminalPopup, self).updateTrackingAreas()

    def mouseEntered_(self, _e):
        self._hovered = True
        self.setNeedsDisplay_(True)

    def mouseExited_(self, _e):
        self._hovered = False
        self.setNeedsDisplay_(True)

    def mouseDown_(self, _e):
        if self._enabled:
            _toggle_terminal_dropdown(self)


class _DropdownListView(AppKit.NSView):
    """Список строк кастомного дропдауна."""
    ROW_H      = 20
    PAD        = 4
    _items     = []
    _selected  = 0
    _hovered   = -1
    _on_select = None   # callable(index)

    def isOpaque(self):
        return False

    def acceptsFirstMouse_(self, _e):
        return True

    def _row_at(self, pt):
        h = self.bounds().size.height
        for i in range(len(self._items)):
            row_y = h - self.PAD - (i + 1) * self.ROW_H
            if row_y <= pt.y < row_y + self.ROW_H:
                return i
        return -1

    def drawRect_(self, rect):
        b = self.bounds()
        w = b.size.width
        h = b.size.height
        r, g, b_ = C_BG
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            AppKit.NSMakeRect(0, 0, w, h), 4, 4)
        _rgba(min(r * 1.6, 1.0), min(g * 1.6, 1.0), min(b_ * 1.6, 1.0)).setFill()
        path.fill()
        C_GREEN_BORD.setStroke()
        path.setLineWidth_(0.8)
        path.stroke()
        for i, item in enumerate(self._items):
            row_y  = h - self.PAD - (i + 1) * self.ROW_H
            is_sel = (i == self._selected)
            is_hov = (i == self._hovered)
            if is_sel or is_hov:
                f2 = 4.5 if is_sel else 2.8
                rp = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    AppKit.NSMakeRect(3, row_y + 1, w - 6, self.ROW_H - 2), 3, 3)
                _rgba(min(r * f2, 1.0), min(g * f2, 1.0), min(b_ * f2, 1.0)).setFill()
                rp.fill()
            color = C_GREEN_BR if is_sel else (C_GREEN if is_hov else C_TEXT)
            AppKit.NSString.stringWithString_(item).drawInRect_withAttributes_(
                AppKit.NSMakeRect(10, row_y + 4, w - 20, self.ROW_H - 4),
                {AppKit.NSFontAttributeName: _mono(10),
                 AppKit.NSForegroundColorAttributeName: color})

    def updateTrackingAreas(self):
        for a in list(self.trackingAreas()):
            self.removeTrackingArea_(a)
        self.addTrackingArea_(AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(),
            AppKit.NSTrackingMouseMoved |
            AppKit.NSTrackingMouseEnteredAndExited |
            AppKit.NSTrackingActiveAlways |
            AppKit.NSTrackingInVisibleRect,
            self, None))
        objc.super(_DropdownListView, self).updateTrackingAreas()

    def mouseMoved_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        self._hovered = self._row_at(pt)
        self.setNeedsDisplay_(True)

    def mouseExited_(self, _e):
        self._hovered = -1
        self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        pt  = self.convertPoint_fromView_(event.locationInWindow(), None)
        row = self._row_at(pt)
        _close_open_dropdown()
        if row >= 0 and self._on_select:
            self._on_select(row)


def _close_open_dropdown():
    global _open_dropdown_panel, _open_dropdown_ref
    if _open_dropdown_panel:
        try:
            _open_dropdown_panel.close()
        except Exception:
            pass
        _open_dropdown_panel = None
    _open_dropdown_ref = None


def _toggle_terminal_dropdown(popup):
    global _open_dropdown_panel, _open_dropdown_ref
    if _open_dropdown_panel:
        same = (_open_dropdown_ref is popup)
        _close_open_dropdown()
        if same:
            return
    if not popup._items:
        return
    win = popup.window()
    if not win:
        return
    view_in_win = popup.convertRect_toView_(popup.bounds(), None)
    scr         = win.convertRectToScreen_(view_in_win)
    ROW_H  = 20
    PAD    = 4
    n      = len(popup._items)
    LIST_W = max(int(popup.bounds().size.width), 120)
    LIST_H = min(n * ROW_H + PAD * 2, 260)
    px = scr.origin.x
    py = scr.origin.y - LIST_H
    screen = AppKit.NSScreen.mainScreen().frame()
    if py < screen.origin.y + 4:
        py = scr.origin.y + scr.size.height
    panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        AppKit.NSMakeRect(px, py, LIST_W, LIST_H),
        AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered, False,
    )
    panel.setOpaque_(False)
    panel.setBackgroundColor_(AppKit.NSColor.clearColor())
    panel.setLevel_(AppKit.NSFloatingWindowLevel + 5)
    panel.setHidesOnDeactivate_(False)
    panel.setIgnoresMouseEvents_(False)

    def on_select(idx):
        popup._selected = idx
        popup.setNeedsDisplay_(True)
        if popup._target and popup._action:
            try:
                popup._action(popup._target, popup)
            except Exception as _err:
                print(f"dropdown action: {_err}")

    lv = _DropdownListView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, LIST_W, LIST_H))
    lv._items     = list(popup._items)
    lv._selected  = popup._selected
    lv._hovered   = -1
    lv._on_select = on_select
    panel.setContentView_(lv)
    panel.orderFront_(None)
    _open_dropdown_panel = panel
    _open_dropdown_ref   = popup


def _show_sc_editor(sc_idx):
    """Открыть редактор сценариев поверх главного окна."""
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

    # Фиксированный размер — такой же как у всех вспомогательных панелей
    mf      = _win.frame()
    EDIT_W  = W
    EDIT_H  = H_PANEL
    MARGIN  = 12
    LABEL_H = 13
    TF_H    = 22
    GAP     = 3
    BTN_H   = 22
    BTN_W   = 72     # wide enough for Russian "[Сохранить]"

    # Использовать _EditorPanel чтобы текстовые поля могли получать фокус/клавиатурный ввод
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
    panel.setAppearance_(_panel_appearance())   # DarkAqua / Aqua — NSButton берёт цвет атрибутированной строки корректно
    _bg = TerminalView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, EDIT_W, EDIT_H))
    panel.setContentView_(_bg)
    cv = _bg

    y = EDIT_H - 8

    # Заголовок: 🧲 + название + кнопка действия справа
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

    # Полноширинные поля с описательными плейсхолдерами
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
        # Слой гарантированно существует после addSubview_ — переприменяем цвета
        lay = tf.layer()
        if lay:
            lay.setBackgroundColor_(_rgba(*C_BG).CGColor())
            lay.setBorderColor_(C_GREEN_BORD.CGColor())
            lay.setBorderWidth_(0.5)
            lay.setCornerRadius_(2.0)
        return tf

    tf_ru    = _make_tf("RU — название кнопки, до 6 символов",   label_val.get("ru", ""))
    y       -= TF_H + GAP
    tf_en    = _make_tf("EN — название кнопки, до 6 символов *",  label_val.get("en", ""))
    y       -= TF_H + GAP
    tf_es    = _make_tf("ES — nombre del botón, hasta 6 símbolos", label_val.get("es", ""))
    y       -= TF_H + GAP
    # ── Каскадный выбор провайдер → модель ──────────────────────────────────
    cur_model_str = sc.get("model", "") or ""
    if ":" in cur_model_str:
        cur_pid, _, cur_mname = cur_model_str.partition(":")
    else:
        cur_pid, cur_mname = "", ""

    HALF_W = (FW - 6) // 2

    # метка провайдера + метка модели
    lbl_p = _mklabel("провайдер", size=9, color=C_IDLE)
    lbl_p.setFrame_(AppKit.NSMakeRect(MARGIN, y - LABEL_H, HALF_W, LABEL_H))
    cv.addSubview_(lbl_p)
    lbl_m = _mklabel("модель", size=9, color=C_IDLE)
    lbl_m.setFrame_(AppKit.NSMakeRect(MARGIN + HALF_W + 6, y - LABEL_H, HALF_W, LABEL_H))
    cv.addSubview_(lbl_m)
    y -= LABEL_H + 2

    # попап провайдера
    prov_items = ["авто"] + _pc.available_providers()
    pop_prov = _TerminalPopup.alloc().initWithFrame_(
        AppKit.NSMakeRect(MARGIN, y - TF_H, HALF_W, TF_H))
    pop_prov.addItemsWithTitles_(prov_items)
    if cur_pid in prov_items:
        pop_prov.selectItemWithTitle_(cur_pid)
    else:
        pop_prov.selectItemAtIndex_(0)
    pop_prov.setTarget_(_btn_t)
    pop_prov.setAction_(BtnTarget.scProviderChanged_)
    cv.addSubview_(pop_prov)

    # попап модели
    pop_model = _TerminalPopup.alloc().initWithFrame_(
        AppKit.NSMakeRect(MARGIN + HALF_W + 6, y - TF_H, HALF_W, TF_H))
    _populate_model_popup(pop_model, cur_pid, cur_mname)
    cv.addSubview_(pop_model)

    y -= TF_H + GAP

    # Тихий режим + Full режим по умолчанию — одна строка, рядом
    SIL_H   = 26
    SIL_SEP = 5
    CHK_W   = FW // 2 - 2
    y -= SIL_SEP
    cv.addSubview_(_sep_line(MARGIN, y, FW, pin="top"))
    y -= 1
    is_silent  = bool(sc.get("silent", False))
    chk_prefix = "[✓] " if is_silent else "[ ] "
    chk_color  = C_CYAN if is_silent else C_GREEN_DIM
    sil_btn    = _mkbtn(chk_prefix + _T("sc_silent"), color=chk_color,
                        size=10, align=AppKit.NSTextAlignmentLeft)
    sil_btn.setFrame_(AppKit.NSMakeRect(MARGIN, y - SIL_H, CHK_W, SIL_H))
    sil_btn.setTarget_(_btn_t)
    sil_btn.setAction_(BtnTarget.cfgScToggleSilent_)
    cv.addSubview_(sil_btn)
    is_full_default = bool(sc.get("full_default", False))
    fd_prefix = "[✓] " if is_full_default else "[ ] "
    fd_color  = C_GREEN_BR if is_full_default else C_GREEN_DIM
    fd_btn    = _mkbtn(fd_prefix + _T("sc_full_default"), color=fd_color,
                       size=10, align=AppKit.NSTextAlignmentLeft)
    fd_btn.setFrame_(AppKit.NSMakeRect(MARGIN + CHK_W + 4, y - SIL_H, CHK_W, SIL_H))
    fd_btn.setTarget_(_btn_t)
    fd_btn.setAction_(BtnTarget.cfgScToggleFullDefault_)
    cv.addSubview_(fd_btn)
    y -= SIL_H
    cv.addSubview_(_sep_line(MARGIN, y, FW, pin="top"))
    y -= 1 + SIL_SEP

    # ── Нижняя строка: кнопки Отмена + Сохранить ─────────────────────────────────────────
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

    # Текстовое поле промпта — расширяется чтобы заполнить оставшееся место над нижней строкой
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
    tv_prompt.setInsertionPointColor_(C_TEXT)
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
        "silent":       is_silent,   # текущее состояние переключателя (Python bool, меняется в action)
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

    # Позиционирование с использованием назначения ячейки сетки
    _ewx, _ewy = int(mf.origin.x), int(mf.origin.y)
    _eww, _ewh = int(mf.size.width), int(mf.size.height)
    ex, ey = _calc_panel_pos("editor", _ewx, _ewy, _eww, _ewh, int(mf.size.width), H_PANEL)
    if _cluster_mode:
        cfg = globals().get("_cfg_panel")
        if cfg and cfg.isVisible():
            cf = cfg.frame()
            cx, cy = int(cf.origin.x), int(cf.origin.y)
            if "editor" in _cluster_offsets:
                dx, dy = _cluster_offsets["editor"]
            else:
                dx, dy = W + _SNAP_GAP, -(H_PANEL + _SNAP_GAP)   # по умолчанию: слот внизу справа
                _cluster_offsets["editor"] = (dx, dy)
            ex, ey = cx + dx, cy + dy
    panel.setFrameOrigin_(AppKit.NSMakePoint(ex, ey))
    AppKit.NSApp.activateIgnoringOtherApps_(True)
    panel.makeKeyAndOrderFront_(None)
    panel.makeFirstResponder_(tf_en)

    # Синхронизировать цвета кнопок на панели cfg — подсветить активную карточку
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
        return   # EN обязателен

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
        # Новый сценарий: сначала очистить флаги silent/full_default у всех существующих
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
            # Если помечаем silent, убрать его у всех остальных
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
        # По умолчанию: обновить панель конфигурации чтобы отразить обновлённый список
        _close_cfg_panel_rebuild()
        _toggle_cfg_panel()


def _populate_model_popup(popup, provider_id, selected=""):
    """Заполнить _TerminalPopup модели для указанного provider_id."""
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

    pw = W   # панели всегда используют обычную (не расширенную) ширину

    # ── Константы разметки ─────────────────────────────────────────────────────────
    MARGIN    = 10        # левый/правый отступ панели
    MARGIN_T  = 22        # верхний отступ (место для кнопки [ⓘ])
    BOX_H     = 52        # верхний ряд: прозрачность | шрифт | язык
    BOX_G     = 6         # горизонтальный отступ между соседними блоками
    HK_TH_H   = 120       # ряд тем: высокие образцы (горячая клавиша удалена — Cmd+Enter захардкожен)
    CELL_H    = 22
    CELL_GAP  = 3
    COLS      = 6
    MAX_SC    = COLS * 3 - 1
    QUIT_H    = 46        # нижняя секция выхода
    VGAP      = 6         # одинаковый вертикальный отступ между всеми рядами

    inner_w = pw - 2 * MARGIN

    # Верхний ряд: три равные колонки — прозрачность | шрифт | язык
    eq_col_w  = (inner_w - 2 * BOX_G) // 3
    op_w = fn_w = la_w = eq_col_w
    op_x = MARGIN
    fn_x = MARGIN + op_w + BOX_G
    la_x = MARGIN + 2 * (op_w + BOX_G)

    # Ряд тем: полная ширина
    th_w  = inner_w
    th_x  = MARGIN

    scenarios = _st.get("scenarios", [])
    n_sc      = len(scenarios)
    n_cells   = min(n_sc + 1, MAX_SC + 1)
    n_rows    = max(1, (n_cells + COLS - 1) // COLS)

    SCEN_INNER = n_rows * (CELL_H + CELL_GAP) + 8
    SC_BOX_H   = 20 + SCEN_INNER

    # Позиции Y снизу вверх
    sc_box_y  = QUIT_H + VGAP
    hk_th_y   = sc_box_y + SC_BOX_H + VGAP
    box_y     = hk_th_y + HK_TH_H + VGAP
    ph        = max(box_y + BOX_H + MARGIN_T, H_PANEL)

    _close_cfg_panel_rebuild()
    _cfg_panel = _make_drop_panel(pw, ph)
    _cfg_panel._panel_key = "cfg"
    cv = _cfg_panel.contentView()

    # ── Вспомогательная функция блока fieldset ───────────────────────────────────────────────────────
    def _fieldset(x, y, w, h, title):
        """Создать именованный fieldset (NSBox, заголовок NSAtTop прорезает верхнюю границу).
        Возвращает (contentView, content_w, content_h)."""
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

    # ── Кнопки [ⓘ] INFO + [КЛЮЧИ] — верхний правый угол панели ──────────────────
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

    # ── FIELDSET ПРОЗРАЧНОСТИ (узкий) ─────────────────────────────────────────────────
    op_cv, op_cw, op_ch = _fieldset(op_x, box_y, op_w, BOX_H, _T("cfg_opacity"))
    sl = TerminalSlider.alloc().initWithFrame_(
        AppKit.NSMakeRect(4, (op_ch - 20) // 2, op_cw - 8, 20))
    sl.setMinValue_(0.40)
    sl.setMaxValue_(1.00)
    sl.setFloatValue_(_st["opacity"])
    sl.setTarget_(_btn_t)
    sl.setAction_(BtnTarget.cfgOpacity_)
    op_cv.addSubview_(sl)

    # ── FIELDSET ШРИФТА ─────────────────────────────────────────────────────────────
    fn_cv, fn_cw, fn_ch = _fieldset(fn_x, box_y, fn_w, BOX_H, _T("cfg_font"))
    FBTN_W = max(28, (fn_cw - 8) // 2)
    fn_start_x = (fn_cw - 2 * FBTN_W - 4) // 2
    fn_btn_y   = (fn_ch - 22) // 2
    for j, (lbl_txt, act, tip) in enumerate(
            [("[A-]", BtnTarget.cfgFontDec_, "Уменьшить шрифт"),
             ("[A+]", BtnTarget.cfgFontInc_, "Увеличить шрифт")]):
        b = _mkbtn(lbl_txt, color=C_GREEN, size=11)
        b.setFrame_(AppKit.NSMakeRect(fn_start_x + j * (FBTN_W + 4), fn_btn_y, FBTN_W, 22))
        b.setTarget_(_btn_t)
        b.setAction_(act)
        b.setToolTip_(tip)
        fn_cv.addSubview_(b)

    # ── FIELDSET ЯЗЫКА ─────────────────────────────────────────────────────────
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

    # ── FIELDSET ТЕМ (полная ширина, 2 ряда × 4 квадрата: светлые / тёмные) ───────────
    th_cv, th_cw, th_ch = _fieldset(th_x, hk_th_y, th_w, HK_TH_H, _T("cfg_theme"))
    cur_theme  = _st.get("theme", "emerald")
    n_light    = _N_LIGHT                      # 4 светлые темы (верхний ряд)
    n_dark     = len(_THEME_META) - n_light    # 4 тёмные темы (нижний ряд)
    n_per_row  = max(n_light, n_dark)          # = 4
    SQ_EDGE    = 5    # равный отступ: лево/право/верх/низ от края fieldset
    SQ_PAD     = 5    # отступ между карточками в ряду
    SQ_ROW_GAP = 5    # вертикальный отступ между рядами
    # Равномерно заполнить доступное пространство
    sq_w       = max(16, (th_cw - 2 * SQ_EDGE - (n_per_row - 1) * SQ_PAD) // n_per_row)
    sq_h       = max(14, (th_ch - 2 * SQ_EDGE - SQ_ROW_GAP) // 2)
    sq_y_dark  = SQ_EDGE
    sq_y_light = SQ_EDGE + sq_h + SQ_ROW_GAP
    try:
        _CALayer = objc.lookUpClass('CALayer')
    except Exception:
        _CALayer = None

    _THEME_LABELS = {
        "paper": "Paper (светлая)", "sky": "Sky (светлая)",
        "sand": "Sand (светлая)", "arctic": "Arctic (светлая)",
        "emerald": "Emerald (тёмная)", "ocean": "Ocean (тёмная)",
        "neon": "Neon (тёмная)", "gold": "Gold (тёмная)",
    }

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
        tb.setToolTip_(_THEME_LABELS.get(tname, tname))
        th_cv.addSubview_(tb)

    # Ряд 1 (вверх): светлые темы — от края SQ_EDGE, равные промежутки
    for i, (tname, tbg, tcolor) in enumerate(_THEME_META[:n_light]):
        _make_swatch(tname, tbg, tcolor, i, SQ_EDGE + i * (sq_w + SQ_PAD), sq_y_light)

    # Ряд 2 (низ): тёмные темы — от края SQ_EDGE, равные промежутки
    for i, (tname, tbg, tcolor) in enumerate(_THEME_META[n_light:]):
        _make_swatch(tname, tbg, tcolor, n_light + i, SQ_EDGE + i * (sq_w + SQ_PAD), sq_y_dark)

    # ── FIELDSET СЦЕНАРИЕВ ────────────────────────────────────────────────────────
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
            # Собрать заголовок: [·LABEL·] оба, [LABEL] только fd, ·LABEL· только sil
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

    # Фоновая проверка доступности модели
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

    # [+] в следующей позиции
    if n_sc <= MAX_SC:
        btn_add = _mkbtn("[+]", color=C_GREEN_DIM, size=10)
        btn_add.setFrame_(_cell_rect(n_sc))
        btn_add.setTarget_(_btn_t)
        btn_add.setAction_(BtnTarget.cfgScAdd_)
        sc_cv.addSubview_(btn_add)

    # ── Разделитель между сценариями и выходом ───────────────────────────────────────────
    cv.addSubview_(_sep_line(MARGIN, QUIT_H, pw - MARGIN * 2, pin="bottom"))

    # ── СЕКЦИЯ ВЫХОДА: [🔄 сброс] | [ВЫХОД центр] | [🎯 панели] ───────────────────
    SB_W  = 34   # ширина боковой кнопки
    SB_G  = 6    # отступ между боковыми кнопками и кнопкой выхода
    BTN_Y = (QUIT_H - 22) // 2 + 4
    quit_w = pw - MARGIN * 2 - SB_W * 2 - SB_G * 2
    quit_x = MARGIN + SB_W + SB_G

    # 🔄 — сброс к крестообразному расположению по умолчанию (слева)
    btn_cross = _mkbtn("🔄", color=C_GREEN_DIM, size=14)
    btn_cross.setFrame_(AppKit.NSMakeRect(MARGIN, BTN_Y, SB_W, 22))
    btn_cross.setTarget_(_btn_t)
    btn_cross.setAction_(BtnTarget.hushDefaultCross_)
    btn_cross.setToolTip_("Сброс: настройки↑  история↓  провайдеры←  сценарии→")
    cv.addSubview_(btn_cross)

    # [ВЫХОД] — центр
    btn_quit = _mkbtn(_T("btn_quit"), color=C_REC, size=10)
    btn_quit.setFrame_(AppKit.NSMakeRect(quit_x, BTN_Y, quit_w, 22))
    btn_quit.setTarget_(_btn_t)
    btn_quit.setAction_(BtnTarget.cfgQuit_)
    cv.addSubview_(btn_quit)

    # 🎯 — показать/скрыть все панели (справа)
    btn_rst = _mkbtn("🎯", color=C_GREEN_DIM, size=14)
    btn_rst.setFrame_(AppKit.NSMakeRect(pw - MARGIN - SB_W, BTN_Y, SB_W, 22))
    btn_rst.setTarget_(_btn_t)
    btn_rst.setAction_(BtnTarget.hushResetPanels_)
    btn_rst.setToolTip_("Показать/скрыть все панели")
    cv.addSubview_(btn_rst)

    _cfg_panel.setAlphaValue_(_st.get("opacity", 0.88))

    mf  = _win.frame()
    wx, wy = int(mf.origin.x), int(mf.origin.y)
    ww, wh = int(mf.size.width), int(mf.size.height)
    px, py = _calc_panel_pos("cfg", wx, wy, ww, wh, pw, ph)
    # В режиме кластера: восстановить cfg на якорную позицию кластера
    if _cluster_mode:
        cx, cy = _cluster_anchor_pos()
        px, py = cx, cy
    _cfg_panel.setFrameOrigin_(AppKit.NSMakePoint(px, py))
    AppKit.NSApp.activateIgnoringOtherApps_(True)
    _cfg_panel.makeKeyAndOrderFront_(None)


def _restore_history_item(full_text: str, item_id: str = None):
    """Добавить элемент истории как новый блок (дополнить, не заменить)."""
    _add_rich_block(full_text, hist_id=item_id)


def _restore_session(blocks_text: list, block_hist_ids: list = None, session_id: str = None):
    """Добавить блоки сессии к текущему контенту (каждый блок добавляется отдельно)."""
    if not _doc_view:
        return
    for i, text in enumerate(blocks_text):
        if not text.strip():
            continue
        hist_id = (block_hist_ids[i] if block_hist_ids and i < len(block_hist_ids) else None)
        _add_rich_block(text.strip(), hist_id=hist_id)


def _get_all_text() -> str:
    """Вернуть объединённый текст из всех блоков + _tv (используется сценариями и добавлением в историю)."""
    parts = []
    for b in _rich_blocks:
        if b._inner_tv:
            # Всегда предпочитать живой контент _inner_tv — пользователь мог редактировать в любом режиме
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
    """Загрузить текст (одиночный или объединённый) в основную текстовую область.
    loaded_id: UUID исходного элемента истории (None если объединено несколько).
    keep_active: если True, не очищать active_sc (используется show_scenario_result).
    """
    _st["text"]    = text
    _st["mode"]    = "ready"
    _st["is_md"]   = _is_markdown(text)
    _st["md_mode"] = False
    _remove_all_rich_blocks()
    if not keep_active:
        _st["active_sc"] = None   # загрузка из истории сбрасывает любой активный фильтр
    if _on_history_load_cb:
        _on_history_load_cb(loaded_id)   # уведомить main.py какой элемент был загружен
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
    # Сразу преобразовать загруженный текст в блок
    _finalize_tv_to_block()
    _main(_update_cursor_pos)


# ── Публичные хелперы ──────────────────────────────────────────────────────────

def refresh_hist_panel():
    """Переоткрыть панель истории с новыми данными (вызывается после удаления)."""
    if _on_history_cb:
        history = _on_history_cb()
        _main(lambda h=history: _show_hist_panel(h))


def show_history_browser(history):
    """Открыть оверлей в режиме просмотра истории (двойное нажатие горячей клавиши)."""
    def _():
        _st["mode"] = "history_open"
        _show_target_app_header()   # показывать целевое приложение, без текста статуса
        _show_buttons(False)
        # [ИСТ] остаётся видимым в заголовке — нет необходимости показывать снова
        _win.orderFrontRegardless()
        _show_hist_panel(history)
    _main(_)

# ── Тихий режим ────────────────────────────────────────────────────────────────

def _build_silent_header(show_icon: bool):
    """Единая компактная панель для состояний записи + распознавания.
    Такая же разметка как у заголовка карточки обработки: [ICON] [Имя приложения]  [WF/EQ полосы].
    Использует общие константы с _build_processing_card для визуальной согласованности.
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

    # ── общие константы (точно совпадают с _build_processing_card) ──────────────
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

    # Центр Y для элементов ANIM_H внутри заголовка
    anim_y = WIN_SIDE + (HEADER_H - ANIM_H) // 2

    # Иконка (слева)
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

    # Метка имени приложения — измерить фактическую ширину текста, EQ занимает остаток
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

    # Форма волны — заполняет всё пространство правее имени приложения
    wv = _SilentWaveformView.alloc().initWithFrame_(
        AppKit.NSMakeRect(eq_x, anim_y, eq_w, ANIM_H))
    cv.addSubview_(wv)
    _silent_wf = wv

    # Полосы эквалайзера — изначально скрыты; показываются при распознавании + LLM
    ev = EqBarsView.alloc().initWithFrame_(
        AppKit.NSMakeRect(eq_x, anim_y, eq_w, ANIM_H))
    ev.setHidden_(True)
    cv.addSubview_(ev)
    _silent_eq_v = ev

    _silent_win = panel


def _is_python_app(app) -> bool:
    """True для фоновых процессов Python — иконка не несёт смысла для отображения."""
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
    """Нарисовать иконку целевого приложения в _AppIconView если видима (главный поток)."""
    if _silent_app_icon_v and _silent_target_app:
        try:
            icon = _silent_target_app.icon()
            if icon:
                _silent_app_icon_v.setImage_(icon)
        except Exception:
            pass


def show_recording_silent(prev_app=None):
    """Показать плавающую полосу формы волны внизу по центру экрана; вызывать ПОСЛЕ установки _silent_mode.
    Если тихое окно уже существует (возобновление в той же сессии), просто сменить состояние без пересборки.
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
            # Окно уже существует — отменить обратный отсчёт, перейти на форму волны
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
    """Тихий режим: запись завершена, Whisper транскрибирует — анимация сканирования эквалайзера."""
    def _():
        global _eq_t, _eq_dir
        if _silent_wf:
            _silent_wf.setHidden_(True)
        if _silent_eq_v:
            # Розовые полосы, скан слева→направо→влево
            _silent_eq_v.setMode_(0)
            _silent_eq_v.setCol_(C_PINK)
            _eq_t   = 0.0
            _eq_dir = 1
            _silent_eq_v.setHidden_(False)
    _main(_)


def show_countdown_silent(duration: float = 2.0):
    """Тихий режим: обратный отсчёт периода ожидания — полосы заполняются слева→направо (зелёный→красный)."""
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
    """Отменить обратный отсчёт и вернуться к анимации сканирования (пользователь нажал Alt во время ожидания)."""
    def _():
        global _eq_countdown_start
        _eq_countdown_start = 0.0
        if _silent_eq_v and not _silent_eq_v.isHidden() and _silent_eq_v._mode == 2:
            _silent_eq_v.setMode_(0)
            _silent_eq_v.setCol_(C_PINK)
    _main(_)


def show_transcribed_silent(text: str):
    """Тихий режим: транскрипция завершена без LLM — оверлей закрывается немедленно после вставки."""
    pass


def update_silent_accumulation(text: str):
    """Показать накопленный транскрибированный текст в тихом пилле.

    Окно растёт вверх в зависимости от фактической высоты текста.
    Max = 4 × initial strip height; after that — scrollable.
    NSScrollView + NSTextView with fully transparent background.
    """
    SEP_H    = 1
    PAD      = 12
    WIN_SIDE = 4
    TOP_PAD  = 10   # отступ от разделителя до первой строки текста
    BOT_PAD  = 8    # отступ от последней строки до верхнего края окна

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
            # ── ПЕРВЫЙ ВЫЗОВ: запомнить высоту полосы, создать разделитель + scroll view ─
            _silent_strip_win_h = int(old_h)
            _silent_sep_y       = int(old_h - WIN_SIDE)

            # Разделитель
            sep = AppKit.NSView.alloc().initWithFrame_(
                AppKit.NSMakeRect(WIN_SIDE, _silent_sep_y, CARD_W, SEP_H))
            sep.setWantsLayer_(True)
            sep.layer().setBackgroundColor_(_rgba(0.15, 0.8, 0.15, 0.3).CGColor())
            cv.addSubview_(sep)

            # NSScrollView — начинает с высотой 1px, растёт вниз
            sv_y = _silent_sep_y + SEP_H + TOP_PAD
            scroll = AppKit.NSScrollView.alloc().initWithFrame_(
                AppKit.NSMakeRect(WIN_SIDE + PAD, sv_y, sv_w, 1))
            scroll.setBorderType_(AppKit.NSNoBorder)
            scroll.setHasVerticalScroller_(False)   # показывается при необходимости
            scroll.setHasHorizontalScroller_(False)
            scroll.setAutohidesScrollers_(True)
            # Полная прозрачность: scroll view + clip view (NSClipView)
            scroll.setBackgroundColor_(AppKit.NSColor.clearColor())
            scroll.setDrawsBackground_(False)
            scroll.contentView().setDrawsBackground_(False)
            # Тематический скроллер: 4px ручка цвета акцента, авто-скрывается через overlay стиль
            scroll.setVerticalScroller_(_ThinAccentScroller.alloc().init())
            scroll.setScrollerStyle_(getattr(AppKit, 'NSScrollerStyleOverlay', 1))

            # NSTextView — document view, автоматически растёт по вертикали
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

        # ── Установить текст через NSAttributedString (надёжный шрифт + цвет) ──────────
        attrs = {
            AppKit.NSFontAttributeName:            _mono(9.5),
            AppKit.NSForegroundColorAttributeName: C_TEXT,
        }
        astr = AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs)
        _silent_text_v.textStorage().setAttributedString_(astr)

        # ── Измерить высоту контента ────────────────────────────────────────────
        lm = _silent_text_v.layoutManager()
        lm.ensureLayoutForTextContainer_(_silent_text_v.textContainer())
        used = lm.usedRectForTextContainer_(_silent_text_v.textContainer())
        content_h = used.size.height

        # ── Рост или прокрутка? ───────────────────────────────────────────────────
        sv_y = _silent_sep_y + SEP_H + TOP_PAD
        MAX_WIN_H  = _silent_strip_win_h * 4
        MAX_SV_H   = MAX_WIN_H - sv_y - BOT_PAD - WIN_SIDE

        if content_h <= MAX_SV_H:
            # Контент умещается — расширить окно чтобы показать всё
            sv_h = max(content_h, 12)
            new_win_h = int(sv_y + sv_h + BOT_PAD + WIN_SIDE)
            needs_scroller = False
        else:
            # Контент переполнен — фиксируем максимум, включаем скроллер
            sv_h = MAX_SV_H
            new_win_h = int(MAX_WIN_H)
            needs_scroller = True

        # Изменить размер text view под контент (позволяет прокрутке работать)
        _silent_text_v.setFrameSize_(AppKit.NSMakeSize(sv_w, max(content_h, sv_h)))

        # Расширить окно при необходимости
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

        # Изменить размер scroll view для заполнения текстовой области
        _silent_scroll_v.setFrame_(
            AppKit.NSMakeRect(WIN_SIDE + PAD, sv_y, sv_w, max(sv_h, 12)))

        # Показать/скрыть полосу прокрутки
        _silent_scroll_v.setHasVerticalScroller_(needs_scroller)

        # Прокрутить к последнему тексту (низ content view)
        _silent_text_v.scrollRangeToVisible_(
            AppKit.NSMakeRange(_silent_text_v.string().length(), 0))

        _silent_win.display()

    _main(_)


def _build_processing_card(raw_text: str, show_icon: bool):
    """Построить карточку обработки LLM (шире и выше чем полоса записи).

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

    # Сохранить текущую высоту окна — не уменьшать если текстовая область выросла при накоплении
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
    # Использовать накопленную высоту окна если она больше (не уменьшать при переходе к LLM)
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

    # ── тёмный фон карточки ──────────────────────────────────────────────────
    bg = _SilentBgView.alloc().initWithFrame_(
        AppKit.NSMakeRect(WIN_SIDE, WIN_SIDE, CARD_W, CARD_H))
    cv.addSubview_(bg)

    # ── строка заголовка (НИЗ карточки, та же позиция что у полосы записи) ─────────
    header_y = WIN_SIDE   # y нижнего края заголовка

    # иконка приложения (слева)
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

    # метка имени приложения — измерить текст, EQ занимает остаток
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

    # Полосы EQ — заполняют всё пространство правее имени
    eq_y = header_y + (HEADER_H - ANIM_H) // 2
    ev = EqBarsView.alloc().initWithFrame_(
        AppKit.NSMakeRect(card_eq_x, eq_y, card_eq_w, ANIM_H))
    ev.setMode_(1)
    ev.setCol_(C_YEL)
    cv.addSubview_(ev)
    _silent_eq_v = ev
    _silent_wf   = None

    # ── разделительная линия ────────────────────────────────────────────────────────
    sep_y = WIN_SIDE + HEADER_H
    sep = AppKit.NSBox.alloc().initWithFrame_(
        AppKit.NSMakeRect(WIN_SIDE + PAD, sep_y, CARD_W - PAD * 2, SEP_H))
    sep.setBoxType_(AppKit.NSBoxSeparator)
    sep.setBorderColor_(C_GREEN_BORD)
    cv.addSubview_(sep)

    # ── распознанный текст (ВЕРХ карточки, над разделителем) ────────────────────────
    TOP_INSET = 10   # пространство между верхом text view и верхним краем карточки
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

    # ── оверлей наведения (прерывание) — покрывает только TEXT область, не заголовок ─────────
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
    """Тихий режим: обработка LLM — большая карточка с распознанным текстом + оверлей прерывания."""
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
    """True когда окно полностью закрыто (нет активной сессии)."""
    return _st.get("mode", "idle") == "idle"


def is_any_session_visible() -> bool:
    """True если тихая полоса ИЛИ карточка записи в полном режиме сейчас отображается."""
    return _silent_win is not None or not is_idle()


def get_silent_scenario():
    """Вернуть словарь сценария настроенного для тихой авто-вставки, или None."""
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
    """Вернуть индекс текущего активного сценария, или None."""
    return _st.get("active_sc")


def get_silent_interrupt_fn():
    """Вернуть текущий вызываемый объект прерывания (устанавливается при обработке LLM), или None."""
    return _silent_interrupt_fn


# ── Инициализация ──────────────────────────────────────────────────────────────

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

    # Загрузить состояние магнитного окна из сохранённых настроек
    _magnet_load()

    # Одноразовая миграция: silent_sc_idx (старая настройка) → флаг scenario["silent"].
    # После миграции settings.json перезаписывается без silent_sc_idx,
    # и это больше никогда не выполняется.
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
        _save_settings()   # перезаписать settings.json без silent_sc_idx

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

    # ── Заголовок: ОДНА строка — [статус] [~форма волны~] [□][×] ─────────────────────

    # Метка статуса [слева] — сдвинута вправо чтобы освободить место для иконки приложения
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

    # Метка имени приложения — показывается только при обработке LLM (заменяет метку статуса + правые кнопки)
    _proc_app_lbl = _mklabel("", size=11, color=_rgba(0.7, 0.7, 0.7, 1.0))
    _proc_app_lbl.setFrame_(AppKit.NSMakeRect(STS_X + 26, HDR_ITEM_Y - 2, 130, HDR_ITEM_H))
    _proc_app_lbl.setHidden_(True)
    _proc_app_lbl.setAutoresizingMask_(AppKit.NSViewMinYMargin)
    _pill.addSubview_(_proc_app_lbl)

    # Метка имени сценария — симметричные отступы: STS_X (=10) от края EQ и от иконки шестерни
    SC_LBL_H = 14                                    # точная высота = размер шрифта, избегает обрезки сверху
    SC_LBL_Y = HDR_Y + (HDR_H - SC_LBL_H) // 2     # = 313 (true vertical center)
    SC_LBL_X = EQ_CTR_X + EQ_CTR_W + STS_X          # = 330 (same gap as icon from left)
    SC_LBL_W = CFG_H_X - SC_LBL_X - STS_X           # = 70  (same gap to gear on right)
    _proc_sc_lbl = _mklabel("", size=9, color=C_YEL)
    _proc_sc_lbl.setAlignment_(AppKit.NSTextAlignmentCenter)
    _proc_sc_lbl.setFrame_(AppKit.NSMakeRect(SC_LBL_X, SC_LBL_Y, SC_LBL_W, SC_LBL_H))
    _proc_sc_lbl.setHidden_(True)
    _proc_sc_lbl.setAutoresizingMask_(AppKit.NSViewMinYMargin)
    _pill.addSubview_(_proc_sc_lbl)

    # Форма волны [фиксированная центральная позиция, одинаковый размер во всех режимах]
    _wf = WaveformView.alloc().initWithFrame_(
        AppKit.NSMakeRect(EQ_CTR_X, HDR_ITEM_Y, EQ_CTR_W, HDR_ITEM_H))
    _wf.setAutoresizingMask_(AppKit.NSViewMinYMargin)
    _pill.addSubview_(_wf)

    # Полосы EQ обработки — та же фиксированная центральная позиция что у формы волны
    _proc_eq_v = EqBarsView.alloc().initWithFrame_(
        AppKit.NSMakeRect(EQ_CTR_X, HDR_ITEM_Y, EQ_CTR_W, HDR_ITEM_H))
    _proc_eq_v.setMode_(1)                          # режим импульса (как silent-LLM)
    _proc_eq_v.setCol_(C_YEL)                       # жёлтый импульс
    _proc_eq_v.setHidden_(True)
    _proc_eq_v.setAutoresizingMask_(AppKit.NSViewMinYMargin)
    _pill.addSubview_(_proc_eq_v)

    # Инициализировать отслеживание мыши для главного пилла
    _pill.updateTrackingAreas()

    # [⚙] — иконка шестерни в дальнем правом углу, всегда видна
    _cfg_hdr_btn = _mkbtn("⚙", color=C_GREEN_DIM, size=18)
    _cfg_hdr_btn.setFrame_(AppKit.NSMakeRect(CFG_H_X, HDR_ITEM_Y, CFG_H_W, HDR_ITEM_H))
    _cfg_hdr_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMinYMargin)
    _cfg_hdr_btn.setTarget_(_btn_t)
    _cfg_hdr_btn.setAction_(BtnTarget.cfg_)
    _cfg_hdr_btn.setToolTip_("Настройки")
    _pill.addSubview_(_cfg_hdr_btn)

    # Expand [□] — постоянно скрыта; двойной клик на иконке приложения активирует расширение
    _expand_btn = _mkbtn("[□]", color=C_GREEN_DIM, size=12)
    _expand_btn.setFrame_(AppKit.NSMakeRect(EXP_X, HDR_ITEM_Y, EXP_W, HDR_ITEM_H))
    _expand_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMinYMargin)
    _expand_btn.setTarget_(_btn_t)
    _expand_btn.setAction_(BtnTarget.expand_)
    _expand_btn.setHidden_(True)
    _pill.addSubview_(_expand_btn)

    # Close [×] — самый правый
    _close_btn = _mkbtn("[×]", color=C_REC, size=12)
    _close_btn.setFrame_(AppKit.NSMakeRect(CLO_X, HDR_ITEM_Y, CLO_W, HDR_ITEM_H))
    _close_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMinYMargin)
    _close_btn.setTarget_(_btn_t)
    _close_btn.setAction_(BtnTarget.close_)
    _close_btn.setHidden_(True)   # удалена из заголовка — панель действий имеет [ОТМЕНИТЬ]
    _pill.addSubview_(_close_btn)

    # Разделитель заголовка
    _pill.addSubview_(_sep_line(0, HDR_Y - 1, W, pin="top"))

    # ── Текстовая область (середина) ────────────────────────────────────────────────────

    _scroll = AppKit.NSScrollView.alloc().initWithFrame_(
        AppKit.NSMakeRect(8, TXT_Y, W - 16, TXT_H))   # симметричные отступы 8px
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

    # Контейнер document view — FlippedView содержит rich блоки + _tv
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
    # Скрыть нативный тонкий курсор — рисуем собственный блочный курсор как overlay view
    _tv.setInsertionPointColor_(AppKit.NSColor.clearColor())
    _tv.setSelectedTextAttributes_({
        AppKit.NSBackgroundColorAttributeName: _rgba(0.00, 0.40, 0.00, 0.38),
        AppKit.NSForegroundColorAttributeName: C_GREEN_BR,
    })
    # Убедиться что вставленный простой текст всегда использует цвет/шрифт терминала
    _tv.setTypingAttributes_({
        AppKit.NSFontAttributeName:            _mono(_st["font_size"]),
        AppKit.NSForegroundColorAttributeName: C_TEXT,
    })
    _doc_view.addSubview_(_tv)
    _scroll.setVerticalScroller_(_ThinGreenScroller.alloc().init())
    _scroll.setScrollerStyle_(getattr(AppKit, 'NSScrollerStyleOverlay', 1))
    _scroll.setDocumentView_(_doc_view)
    _pill.addSubview_(_scroll)

    # [md] переключатель формата — верх текстовой области, левая сторона (подальше от полосы прокрутки)
    # Rich блоки имеют собственные переключатели на блок; это только для режима MD
    _md_btn = _mkbtn("[md]", color=C_GREEN_DIM, size=9)
    _md_btn.setFrame_(AppKit.NSMakeRect(W - 74, TXT_TOP - 18, 36, 14))
    _md_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMaxYMargin)
    _md_btn.setWantsLayer_(True)   # убедиться что рендерится поверх _scroll с layer
    _md_btn.setHidden_(True)
    _md_btn.setTarget_(_btn_t)
    _md_btn.setAction_(BtnTarget.mdToggle_)
    _pill.addSubview_(_md_btn)

    # ── Плавающая закреплённая панель для больших markdown блоков ─────────────────────────
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

    # Зарегистрироваться для уведомлений прокрутки чтобы обновлять float bar.
    # Использовать явные bytes-селекторы (требование PyObjC для addObserver).
    nc = AppKit.NSNotificationCenter.defaultCenter()
    # 1) Изменение bounds NSClipView → срабатывает при программном изменении позиции прокрутки
    _scroll.contentView().setPostsBoundsChangedNotifications_(True)
    nc.addObserver_selector_name_object_(
        _btn_t, b"docScrolled:",
        AppKit.NSViewBoundsDidChangeNotification,
        _scroll.contentView())
    # 2) NSScrollView live-scroll → срабатывает при жестах трекпада/колеса пользователя
    nc.addObserver_selector_name_object_(
        _btn_t, b"docScrolled:",
        AppKit.NSScrollViewDidLiveScrollNotification,
        _scroll)

    # Поддельный блочный курсор (совпадает с .term-cursor в admin.roclea.com)
    global _cur_view, _cur_timer
    _cur_view = _BlockCursor.alloc().initWithFrame_(AppKit.NSMakeRect(8, 6, 8, 16))
    _cur_view.setWantsLayer_(True)
    _tv.addSubview_(_cur_view)
    _cur_timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.4, _cur_view, _BlockCursor.tick_, None, True)
    AppKit.NSRunLoop.mainRunLoop().addTimer_forMode_(
        _cur_timer, AppKit.NSRunLoopCommonModes)

    # Нижний разделитель
    _pill.addSubview_(_sep_line(0, BTN_H + 2, W))

    # ── Строка кнопок (внизу) ───────────────────────────────────────────────────

    _sc_icons.clear()
    _sc_seps.clear()
    _sc_active.clear()
    _sc_sep_active.clear()
    _sc_page = 0

    # [<] кнопка навигации на предыдущую страницу — всегда у левого края пикера, отключена на первой странице
    _sc_prev_btn = _mkbtn("[<]", color=C_GREEN_DIM, size=11)
    _sc_prev_btn.setHidden_(True)   # скрыта вне режима пикера
    _sc_prev_btn.setEnabled_(False)
    _sc_prev_btn.setAutoresizingMask_(AppKit.NSViewMaxYMargin)
    _sc_prev_btn.setTarget_(_btn_t)
    _sc_prev_btn.setAction_(BtnTarget.scPrev_)
    _pill.addSubview_(_sc_prev_btn)

    # Слоты кнопок сценариев SC_PAGE — метки/теги устанавливаются в _relayout_buttons
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

        # · разделитель (один на слот; разделитель последнего слота будет скрыт)
        sep = _DotSep.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 14, 24))
        sep.setHidden_(True)
        sep.setAutoresizingMask_(AppKit.NSViewMaxYMargin)
        _pill.addSubview_(sep)
        _sc_seps.append(sep)
        _sc_sep_active.append(False)

    # [>] кнопка навигации на следующую страницу (скрыта пока не нужна)
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
    _hist_btn.setHidden_(True)   # перенесена в строку действий — всегда скрыта в заголовке
    _pill.addSubview_(_hist_btn)

    # [↵] — нижний ряд, крайний справа (отделён от сценариев визуальным отступом)
    _send_hdr_btn = _mkbtn("[↵]", color=C_GREEN_BR, size=12)
    _send_hdr_btn.setHidden_(True)
    _send_hdr_btn.setToolTip_("Вставить (Shift+Enter)")
    _send_hdr_btn.setAutoresizingMask_(AppKit.NSViewMaxYMargin)
    _send_hdr_btn.setTarget_(_btn_t)
    _send_hdr_btn.setAction_(BtnTarget.send_)
    _pill.addSubview_(_send_hdr_btn)

    # Начальная разметка (назначает метки/позиции всем слотам)
    _relayout_buttons(W)

    # Проверить доступность модели в фоне — красит кнопки сценариев соответственно
    _start_sc_avail_check()

    # ── Строка из 4 кнопок действий: Отмена | Сцена | Копировать | Отправить (скрыта когда пусто) ────────
    global _action_row_v, _action_hist_btn, _action_cancel_btn, _action_scene_btn, _action_send_btn, _action_copy_btn
    _action_row_v = AppKit.NSView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, W, BTN_H))
    _action_row_v.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMaxYMargin)
    _action_row_v.setHidden_(True)
    _action_hist_btn = None   # теперь постоянно в правом нижнем углу

    # Четыре кнопки в x=0..CFG_H_X (410), оставляя правые 36px для иконки истории в углу
    ACT4_AVAIL = CFG_H_X - 5 * 8    # 410 - 40 = 370px for 4 buttons
    ACT4_W = ACT4_AVAIL // 4        # = 92px each
    ACT4_H = 28
    ACT4_Y = (BTN_H - ACT4_H) // 2

    _action_cancel_btn = _mkbtn(_T("btn_sc_undo"), color=C_IDLE, size=11)
    _action_cancel_btn.setFrame_(AppKit.NSMakeRect(8, ACT4_Y, ACT4_W, ACT4_H))
    _action_cancel_btn.setTarget_(_btn_t)
    _action_cancel_btn.setAction_(BtnTarget.actionCancel_)
    _action_row_v.addSubview_(_action_cancel_btn)

    _action_scene_btn = _mkbtn(_T("btn_scene"), color=C_YEL, size=11)
    _action_scene_btn.setFrame_(AppKit.NSMakeRect(8 + ACT4_W + 8, ACT4_Y, ACT4_W, ACT4_H))
    _action_scene_btn.setTarget_(_btn_t)
    _action_scene_btn.setAction_(BtnTarget.actionScene_)
    _action_row_v.addSubview_(_action_scene_btn)

    _action_copy_btn = _mkbtn(_T("btn_copy"), color=C_CYAN, size=11)
    _action_copy_btn.setFrame_(AppKit.NSMakeRect(8 + 2*(ACT4_W + 8), ACT4_Y, ACT4_W, ACT4_H))
    _action_copy_btn.setTarget_(_btn_t)
    _action_copy_btn.setAction_(BtnTarget.hushCopyText_)
    _action_copy_btn.setToolTip_("⌘+Enter — скопировать в буфер")
    _action_row_v.addSubview_(_action_copy_btn)

    _action_send_btn = _mkbtn(_T("btn_sc_accept"), color=C_GREEN_BR, size=11)
    _action_send_btn.setFrame_(AppKit.NSMakeRect(8 + 3*(ACT4_W + 8), ACT4_Y, ACT4_W, ACT4_H))
    _action_send_btn.setTarget_(_btn_t)
    _action_send_btn.setAction_(BtnTarget.send_)
    _action_row_v.addSubview_(_action_send_btn)

    _pill.addSubview_(_action_row_v)

    # ── Панель с 3 кнопками результата: Отмена | Копировать | Отправить (показывается при активном результате сценария) ──
    global _sc_action_v, _sc_send_btn2, _sc_cancel_btn2, _sc_copy_btn2
    _sc_action_v = AppKit.NSView.alloc().initWithFrame_(
        AppKit.NSMakeRect(0, 0, W, BTN_H))
    _sc_action_v.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMaxYMargin)
    _sc_action_v.setHidden_(True)

    ACT_BTN_H = 28
    ACT_BTN_Y = (BTN_H - ACT_BTN_H) // 2
    ACT_BTN_W = (W - 4 * 8) // 3   # три равные кнопки с отступами 8px и зазорами

    _sc_cancel_btn2 = _mkbtn(_T("btn_sc_undo"), color=C_CYAN, size=12)
    _sc_cancel_btn2.setFrame_(AppKit.NSMakeRect(8, ACT_BTN_Y, ACT_BTN_W, ACT_BTN_H))
    _sc_cancel_btn2.setTarget_(_btn_t)
    _sc_cancel_btn2.setAction_(BtnTarget.undoScenario_)
    _sc_action_v.addSubview_(_sc_cancel_btn2)

    _sc_copy_btn2 = _mkbtn(_T("btn_copy"), color=C_CYAN, size=12)
    _sc_copy_btn2.setFrame_(AppKit.NSMakeRect(8 + ACT_BTN_W + 8, ACT_BTN_Y, ACT_BTN_W, ACT_BTN_H))
    _sc_copy_btn2.setTarget_(_btn_t)
    _sc_copy_btn2.setAction_(BtnTarget.hushCopyText_)
    _sc_copy_btn2.setToolTip_("⌘+Enter — скопировать в буфер")
    _sc_action_v.addSubview_(_sc_copy_btn2)

    _sc_send_btn2 = _mkbtn(_T("btn_sc_accept"), color=C_GREEN_BR, size=12)
    _sc_send_btn2.setFrame_(AppKit.NSMakeRect(8 + 2*(ACT_BTN_W + 8), ACT_BTN_Y, ACT_BTN_W, ACT_BTN_H))
    _sc_send_btn2.setTarget_(_btn_t)
    _sc_send_btn2.setAction_(BtnTarget.send_)
    _sc_action_v.addSubview_(_sc_send_btn2)

    _pill.addSubview_(_sc_action_v)

    # [⧖] — иконка истории в правом нижнем углу, симметрично шестерне в правом верхнем
    # Шестерня: x=CFG_H_X, отступ-право=6, отступ-верх=9. История: тот же x, тот же отступ снизу.
    HIST_C_GAP_B = H - (HDR_ITEM_Y + HDR_ITEM_H)   # = same top-gap as gear = 9
    _hist_corner_btn = _mkbtn("☰", color=C_GREEN_DIM, size=14)
    _hist_corner_btn.setFrame_(AppKit.NSMakeRect(CFG_H_X, HIST_C_GAP_B, CFG_H_W, HDR_ITEM_H))
    _hist_corner_btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMaxYMargin)
    _hist_corner_btn.setToolTip_("История (двойной клик — развернуть)")
    _hist_corner_btn.setTag_(9)   # используется в history_ для определения двойного клика → развернуть
    _hist_corner_btn.setTarget_(_btn_t)
    _hist_corner_btn.setAction_(BtnTarget.history_)
    _pill.addSubview_(_hist_corner_btn)

    # Оверлей отмены при наведении — добавляется ПОСЛЕДНИМ чтобы быть поверх всех других subview
    # Текст рисуется в drawRect_ (не как подвид NSTextField — это проглотило бы события мыши)
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

# ── Вспомогательная функция видимости кнопок ───────────────────────────────────

def _show_buttons(visible: bool, enabled: bool = None):
    """Управлять видимостью нижнего ряда.

    Состояния (когда visible=True):
      active_sc установлен  → панель из 2 кнопок: [ОТМЕНИТЬ] [ОТПРАВИТЬ]
      sc_picker             → сетка сценариев (существующие _sc_icons)
      иначе                 → строка из 3 кнопок действий: [ОТМЕНИТЬ] [СЦЕНАРИЙ] [ОТПРАВИТЬ]
    """
    if enabled is None:
        enabled = visible

    # Всегда скрытые устаревшие кнопки (заменены панелями действий)
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
        # Панель с 2 кнопками результата
        if _sc_send_btn2:
            _sc_send_btn2.setAttributedTitle_(_atitle(_T("btn_sc_accept"), size=12, color=C_GREEN_BR))
        if _sc_cancel_btn2:
            _sc_cancel_btn2.setAttributedTitle_(_atitle(_T("btn_sc_undo"), size=12, color=C_CYAN))
        if _action_row_v: _action_row_v.setHidden_(True)
        if _sc_action_v:  _sc_action_v.setHidden_(False)
        _hide_scenario_grid()
    elif sc_picker:
        # Сетка пикера сценариев — навигационные стрелки всегда на краях, сценарии заполняют между
        if _action_row_v: _action_row_v.setHidden_(True)
        if _sc_action_v:  _sc_action_v.setHidden_(True)
        for i, btn in enumerate(_sc_icons):
            act = _sc_active[i] if i < len(_sc_active) else False
            btn.setHidden_(not act)
            btn.setEnabled_(enabled and act)
        for i, sep in enumerate(_sc_seps):
            act = _sc_sep_active[i] if i < len(_sc_sep_active) else False
            sep.setHidden_(not act)
        # Навигационные стрелки всегда видны; состояние enabled устанавливается в _relayout_buttons
        if _sc_prev_btn: _sc_prev_btn.setHidden_(False)
        if _sc_next_btn: _sc_next_btn.setHidden_(False)
    else:
        # Обычная строка из 4 кнопок действий — показывать только когда есть контент
        # Проверить и blocks/_tv (через _get_all_text) и _st["text"] (установлено до анимации)
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
    """Обновить видимость строки действий на основе контента; вызывать из хуков изменения текста."""
    mode = _st.get("mode")
    if mode not in ("ready", "history_open"):
        return
    _show_buttons(True)


# ── Публичный API ──────────────────────────────────────────────────────────────

def show_recording():
    """Начать сессию записи (или продолжить существующую)."""
    def _():
        global _silent_mode
        # Отменить любое отложенное открытие истории
        if _btn_t:
            AppKit.NSObject.cancelPreviousPerformRequestsWithTarget_selector_object_(
                _btn_t, b'_openHistDelayed:', None)
        # Скрыть тихую полосу — полный оверлей берёт управление
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
        # Восстановить панель истории если она была открыта в прошлой сессии
        if _cfg_saved.get("panels_open", {}).get("hist") and not (_hist_panel and _hist_panel.isVisible()):
            history = _on_history_cb() if _on_history_cb else []
            _show_hist_panel(history)
    _main(_)


def show_transcribing():
    """Запись завершена — ожидание транскрипции."""
    def _():
        global _eq_t, _eq_dir
        _st["mode"] = "transcribing"
        if _silent_mode:
            _stop_timer()
            _clear_waveform()
            if _silent_wf:
                _silent_wf.setNeedsDisplay_(True)
        else:
            _show_target_app_header()   # показывать иконку + имя во время транскрипции
            _clear_waveform()
            if _wf:
                _wf.setHidden_(True)    # скрыть полосы записи пока анимирует EQ
            if _proc_eq_v:
                _eq_t   = 0.0
                _eq_dir = 1
                _proc_eq_v.setMode_(0)      # скан слева→направо
                _proc_eq_v.setCol_(C_PINK)  # розовый для фазы распознавания
                _proc_eq_v.setHidden_(False)
            _start_timer()   # продолжать анимацию во время распознавания
    _main(_)


def show_result(text: str):
    """Транскрипция завершена — добавить новый текст как блок немедленно."""
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
        # Показать текст немедленно — без анимации печати
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
    """Обработка сценария завершена — ЗАМЕНИТЬ текст оверлея результатом (мгновенно, без анимации)."""
    _load_history_combined(text, loaded_id=hist_id, keep_active=True)


# ── Панель провайдеров ─────────────────────────────────────────────────────────

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
    # Повторный пробинг при каждом открытии — точки статуса отражают актуальный статус
    try:
        import provider_config as _pc_mod
        _pc_mod.probe_all()
    except Exception:
        pass

    PW     = W   # panel always normal width regardless of expanded mode
    mf     = _win.frame()   # main window frame — for grid position only
    MARGIN = 12
    FW     = PW - MARGIN * 2
    LBL_H  = 13
    TF_H   = 22
    GAP    = 4
    BTN_H  = 22

    # ── Фиксированная высота (одинакова для всех панелей) ─────────────────────
    screen = _win.screen() or AppKit.NSScreen.mainScreen()
    vis    = screen.visibleFrame() if screen else AppKit.NSMakeRect(0, 0, 1440, 900)
    PH     = H_PANEL

    # ── Панель ─────────────────────────────────────────────────────────────────
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
    panel.setAppearance_(_panel_appearance())
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

    # ── Заголовок ──────────────────────────────────────────────────────────────
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

    # ── Кнопки ─────────────────────────────────────────────────────────────────
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

    # ── Позиционирование через ячейки сетки ────────────────────────────────────
    wx2, wy2 = int(mf.origin.x), int(mf.origin.y)
    ww2, wh2 = int(mf.size.width), int(mf.size.height)
    px, py = _calc_panel_pos("providers", wx2, wy2, ww2, wh2, PW, PH)
    if _cluster_mode:
        cfg = globals().get("_cfg_panel")
        if cfg and cfg.isVisible():
            cf = cfg.frame()
            cx, cy = int(cf.origin.x), int(cf.origin.y)
            if "providers" in _cluster_offsets:
                dx, dy = _cluster_offsets["providers"]
            else:
                dx, dy = 0, -(H_PANEL + _SNAP_GAP)   # default: below cfg (grid row 1)
                _cluster_offsets["providers"] = (dx, dy)
            px, py = cx + dx, cy + dy
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
            # Держим индикаторы обработки (иконка, метка, EQ) поверх оверлея отмены
            for _ov in [_app_icon_v, _proc_app_lbl, _proc_eq_v]:
                if _ov:
                    _pill.addSubview_positioned_relativeTo_(
                        _ov, AppKit.NSWindowAbove, _proc_hover_v)

        # Скрываем обычные элементы заголовка
        if _lbl:   _lbl.setHidden_(True)
        if _wf:    _wf.setHidden_(True)
        if _cfg_hdr_btn: _cfg_hdr_btn.setHidden_(True)

        # Показываем иконку приложения (уже на позиции STS_X)
        if _app_icon_v:     _app_icon_v.setHidden_(False)

        # Показываем метку имени приложения рядом с иконкой, измеряем ширину динамически
        if _proc_app_lbl:
            _proc_app_lbl.setStringValue_(_prev_app_name)
            _proc_app_lbl.setHidden_(False)

        # Позиционируем только метку; EQ остаётся на фиксированном центральном слоте
        _layout_header_wf()
        if _proc_eq_v:
            _eq_pulse_t = 0.0
            _proc_eq_v.setMode_(1)      # pulse mode for LLM processing
            _proc_eq_v.setCol_(C_YEL)   # yellow for LLM phase
            _layout_header_wf()
            _proc_eq_v.setHidden_(False)

        # Показываем название сценария справа от EQ (в квадратных скобках)
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
        # Завершаем запущенный подпроцесс транскрипции
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
        # Сохраняем позицию пилюли/карточки на диск ДО закрытия
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
        # Сворачиваем если расширено (без анимации — окно будет скрыто)
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
        global _panels_reset_open
        _panels_reset_open = False   # sync toggle state: panels are now all closed
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

# ── Анимация текста ────────────────────────────────────────────────────────────

def _animate_text(old_text: str, new_text: str):
    """Type new_text character by character after old_text, with typewriter sounds."""
    prefix = (old_text.rstrip() + "\n") if old_text else ""

    for i in range(len(new_text)):
        # Останавливаем анимацию если сессия закрыта
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

    # Финальное состояние: конвертируем текст в блок, курсор на пустой строке ниже
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

# ── Таймер: перерисовка волновой формы ─────────────────────────────────────────

class _TimerTarget(AppKit.NSObject):
    def tick_(self, t):
        import time as _t_mod
        global _eq_t, _eq_dir, _eq_pulse_t, _wf_t, _eq_countdown_t
        _wf_t = (_wf_t + 0.05) % (math.pi * 20)   # advance idle wave phase
        if _wf:
            _wf.setNeedsDisplay_(True)
        if _silent_wf and _silent_mode and not _silent_wf.isHidden():
            _silent_wf.setNeedsDisplay_(True)
        # Продвигаем анимацию эквалайзера
        if _silent_mode and _silent_eq_v and not _silent_eq_v.isHidden():
            m = _silent_eq_v._mode
            if m == 0:
                # scan: _eq_t осциллирует 0→1→0
                _eq_t += _eq_dir * 0.045
                if _eq_t >= 1.0:
                    _eq_t = 1.0; _eq_dir = -1
                elif _eq_t <= 0.0:
                    _eq_t = 0.0; _eq_dir = 1
            elif m == 1:
                # pulse: _eq_pulse_t циклически 0→1
                _eq_pulse_t = (_eq_pulse_t + 0.038) % 1.0
            else:
                # countdown: продвигаем заполнение на основе реального времени
                if _eq_countdown_start > 0 and _eq_countdown_dur > 0:
                    elapsed = _t_mod.time() - _eq_countdown_start
                    _eq_countdown_t = min(1.0, elapsed / _eq_countdown_dur)
            _silent_eq_v.setNeedsDisplay_(True)
        # EQ главного окна: scan при транскрипции, pulse при обработке LLM
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
