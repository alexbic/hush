#!/bin/bash
# build_app.sh — собирает самодостаточный HUSH.app.
# Структура проекта:
#   src/      — Python-исходники + launcher.c
#   assets/   — PNG/SVG/ICNS ресурсы
#   defaults/ — шаблон scenarios.json
set -e
cd "$(dirname "$0")"

APP_NAME="HUSH"
APP="$APP_NAME.app"
ROOT="$(pwd)"
SRC="$ROOT/src"
ASSETS="$ROOT/assets"
DEFAULTS="$ROOT/defaults"

# Ищем python3.14, потом python3
PYTHON="$(command -v python3.14 2>/dev/null || command -v python3 2>/dev/null)"
if [ -z "$PYTHON" ]; then
    echo "❌ Python не найден. Установи python3.14 через Homebrew: brew install python@3.14"
    exit 1
fi

echo "=== Сборка $APP ==="
echo "Python : $PYTHON"
echo ""

# ── Иконка ─────────────────────────────────────────────────────────────────
if [ ! -f "$ASSETS/hush.icns" ]; then
    echo "⚠️  hush.icns не найден, сборка невозможна"
    exit 1
else
    echo "  ✓ hush.icns уже существует"
fi

# ── Снимаем хеш parakeet-cli ДО удаления старого бандла ────────────────────
_OLD_PARAKEET_HASH=$(md5 -q "$APP/Contents/Resources/parakeet-cli" 2>/dev/null || echo "")

# ── Структура bundle ────────────────────────────────────────────────────────
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources"

# ── C-лаунчер ──────────────────────────────────────────────────────────────
if [ -f "$SRC/launcher.c" ]; then
    echo "Компилируем C лаунчер..."
    if clang -framework Foundation -o "$APP/Contents/MacOS/$APP_NAME" "$SRC/launcher.c" 2>&1; then
        chmod +x "$APP/Contents/MacOS/$APP_NAME"
        echo "  ✓ C лаунчер скомпилирован"
    else
        echo "  ⚠ Ошибка компиляции C лаунчера, используем bash fallback"
        _use_bash_launcher=1
    fi
else
    echo "  ⚠ src/launcher.c не найден, используем bash fallback"
    _use_bash_launcher=1
fi

if [ "${_use_bash_launcher}" = "1" ]; then
    cat > "$APP/Contents/MacOS/$APP_NAME" << 'LAUNCHER'
#!/bin/bash
MACOS_DIR="$(cd "$(dirname "$0")" && pwd)"
RESOURCES="$(cd "$MACOS_DIR/../Resources" && pwd)"
export RESOURCEPATH="$RESOURCES"
PYTHON="$(command -v python3.14 2>/dev/null || command -v python3 2>/dev/null || echo python3)"
exec "$PYTHON" "$RESOURCES/main.py" "$@"
LAUNCHER
    chmod +x "$APP/Contents/MacOS/$APP_NAME"
fi

# ── Python-исходники из src/ ────────────────────────────────────────────────
echo "Копируем исходники..."
for f in main.py overlay.py recorder.py transcriber.py injector.py processor.py config.py provider_config.py; do
    [ -f "$SRC/$f" ] && cp "$SRC/$f" "$APP/Contents/Resources/" && echo "  + $f"
done

# ── Сценарии (шаблон из defaults/, пользовательские в ~/.config/hush/) ──────
HUSH_CFG="$HOME/.config/hush"
mkdir -p "$HUSH_CFG"
if [ ! -f "$HUSH_CFG/scenarios.json" ] && [ -f "$DEFAULTS/scenarios.json" ]; then
    cp "$DEFAULTS/scenarios.json" "$HUSH_CFG/scenarios.json"
    echo "  + scenarios.json → ~/.config/hush/"
elif [ -f "$DEFAULTS/scenarios.json" ]; then
    echo "  ✓ ~/.config/hush/scenarios.json уже существует (не перезаписываем)"
fi

# ── Ресурсы из assets/ ─────────────────────────────────────────────────────
echo "Копируем ресурсы..."
for f in "$ASSETS"/*.png; do
    [ -f "$f" ] && cp "$f" "$APP/Contents/Resources/" && echo "  + $(basename "$f")"
done
for f in "$ASSETS"/*.svg; do
    [ -f "$f" ] && cp "$f" "$APP/Contents/Resources/" && echo "  + $(basename "$f")"
done

# ── parakeet-cli ────────────────────────────────────────────────────────────
PARAKEET_BIN=""
if [ -f "$ROOT/parakeet-cli" ]; then
    PARAKEET_BIN="$ROOT/parakeet-cli"
elif [ -d "$ROOT/parakeet-cli" ]; then
    PARAKEET_BIN="$ROOT/parakeet-cli"
elif [ -f "$HOME/.local/bin/parakeet-cli" ]; then
    PARAKEET_BIN="$HOME/.local/bin/parakeet-cli"
fi
if [ -n "$PARAKEET_BIN" ]; then
    DST="$APP/Contents/Resources/parakeet-cli"
    if [ -d "$PARAKEET_BIN" ]; then
        # parakeet-cli — директория с бинарниками
        cp -rp "$PARAKEET_BIN" "$DST"
        echo "  ✓ parakeet-cli (директория) скопирован"
    else
        SRC_HASH=$(md5 -q "$PARAKEET_BIN")
        if [ "$SRC_HASH" != "$_OLD_PARAKEET_HASH" ]; then
            cp "$PARAKEET_BIN" "$DST"
            chmod +x "$DST"
            echo "  + parakeet-cli обновлён (CoreML перекомпилирует модель)"
        else
            cp -p "$PARAKEET_BIN" "$DST"
            echo "  ✓ parakeet-cli без изменений (CoreML-кеш сохранён)"
        fi
    fi
else
    echo "  ⚠  parakeet-cli не найден ни в проекте, ни в ~/.local/bin/"
fi

# ── Модели (CoreML) ─────────────────────────────────────────────────────────
# Модели НЕ входят в бандл — они скачиваются с Google Drive при первом запуске
# приложения (см. src/main.py::_first_run_setup).
echo "  ✓ Модели не входят в бандл — скачиваются при первом запуске"

# ── Info.plist ──────────────────────────────────────────────────────────────
cat > "$APP/Contents/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>             <string>HUSH</string>
    <key>CFBundleDisplayName</key>      <string>HUSH</string>
    <key>CFBundleIdentifier</key>       <string>net.alexbic.hush</string>
    <key>LSUIElement</key>              <true/>
    <key>CFBundleVersion</key>          <string>2.1.0</string>
    <key>CFBundleShortVersionString</key><string>2.1</string>
    <key>CFBundleExecutable</key>       <string>HUSH</string>
    <key>CFBundlePackageType</key>      <string>APPL</string>
    <key>CFBundleIconFile</key>         <string>hush</string>
    <key>NSHighResolutionCapable</key>  <true/>
    <key>NSMicrophoneUsageDescription</key>
        <string>HUSH использует микрофон для голосового ввода текста.</string>
    <key>NSAppleEventsUsageDescription</key>
        <string>HUSH использует Apple Events для вставки распознанного текста.</string>
</dict>
</plist>
PLIST

# ── PkgInfo ─────────────────────────────────────────────────────────────────
printf "APPL????" > "$APP/Contents/PkgInfo"

# ── Иконка в bundle ─────────────────────────────────────────────────────────
cp "$ASSETS/hush.icns" "$APP/Contents/Resources/hush.icns"
cp "$ASSETS/hush.icns" "$APP/Contents/Resources/hush"

# Примечание про подпись: НЕ подписываем бандл явно. macOS автоматически
# ставит ad-hoc/linker-signed подпись при запуске. Явный codesign --identifier
# не помогает сохранить Accessibility между переустановками, потому что для
# ad-hoc подписи macOS всё равно использует cdhash, который меняется при
# каждой сборке. Корневое решение — в v3.0 (архитектура "матрёшка": wrapper
# ставится один раз, обновляется только codebase).

echo ""
echo "✓ Готово: $APP"
echo ""
echo "Структура bundle:"
find "$APP" -not -path "*/models/*" | head -35
echo ""
echo "Запуск:  open \"$APP\""
echo "Или перенеси в /Applications и запускай оттуда."
echo ""
echo "Примечание: требует python3.14 из Homebrew."
echo "API ключи: кнопка [КЛЮЧИ] в меню приложения → ~/.config/hush/providers.json"
