#!/bin/bash
# build_app.sh — собирает самодостаточный HUSH.app.
# Все Python-файлы и ресурсы копируются в Contents/Resources/.
# Лаунчер находит пути относительно себя — app можно перемещать куда угодно.
set -e
cd "$(dirname "$0")"

APP_NAME="HUSH"
APP="$APP_NAME.app"
SRC_DIR="$(pwd)"

# Ищем python3.14, потом python3
PYTHON="$(command -v python3.14 2>/dev/null || command -v python3 2>/dev/null)"
if [ -z "$PYTHON" ]; then
    echo "❌ Python не найден. Установи python3.14 через Homebrew: brew install python@3.14"
    exit 1
fi

echo "=== Сборка $APP ==="
echo "Python : $PYTHON"
echo "Исходники: $SRC_DIR"
echo ""

# ── Иконка ─────────────────────────────────────────────────────────────────
if [ ! -f hush.icns ]; then
    echo "Создаём hush.icns из hush_icon.svg..."
    ICONSET=$(mktemp -d)
    qlmanage -t -s 1024 -o "$ICONSET" hush_icon.svg >/dev/null 2>&1
    SRC_PNG="$ICONSET/hush_icon.svg.png"
    mkdir -p "$ICONSET/hush.iconset"
    for SIZE in 16 32 128 256 512; do
        sips -z $SIZE $SIZE "$SRC_PNG" --out "$ICONSET/hush.iconset/icon_${SIZE}x${SIZE}.png" >/dev/null
    done
    sips -z 32   32   "$SRC_PNG" --out "$ICONSET/hush.iconset/icon_16x16@2x.png"   >/dev/null
    sips -z 64   64   "$SRC_PNG" --out "$ICONSET/hush.iconset/icon_32x32@2x.png"   >/dev/null
    sips -z 256  256  "$SRC_PNG" --out "$ICONSET/hush.iconset/icon_128x128@2x.png" >/dev/null
    sips -z 512  512  "$SRC_PNG" --out "$ICONSET/hush.iconset/icon_256x256@2x.png" >/dev/null
    sips -z 1024 1024 "$SRC_PNG" --out "$ICONSET/hush.iconset/icon_512x512@2x.png" >/dev/null
    iconutil -c icns "$ICONSET/hush.iconset" -o hush.icns
    rm -rf "$ICONSET"
    echo "  ✓ hush.icns создан"
fi

# ── Снимаем хеш parakeet-cli ДО удаления старого бандла ────────────────────
_OLD_PARAKEET_HASH=$(md5 -q "$APP/Contents/Resources/parakeet-cli" 2>/dev/null || echo "")

# ── Структура bundle ────────────────────────────────────────────────────────
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources"

# ── C-лаунчер — собираем бинарный launcher из launcher.c ──────────────────
# Бинарный лаунчер делает NSBundle.mainBundle() = HUSH.app, что необходимо
# для корректного позиционирования NSStatusItem в macOS 14+ (height > 0).
if [ -f "$SRC_DIR/launcher.c" ]; then
    echo "Компилируем C лаунчер..."
    if clang -framework Foundation -o "$APP/Contents/MacOS/$APP_NAME" "$SRC_DIR/launcher.c" 2>&1; then
        chmod +x "$APP/Contents/MacOS/$APP_NAME"
        echo "  ✓ C лаунчер скомпилирован"
    else
        echo "  ⚠ Ошибка компиляции C лаунчера, используем bash fallback"
        _use_bash_launcher=1
    fi
else
    echo "  ⚠ launcher.c не найден, используем bash fallback"
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

# ── Python-файлы приложения ─────────────────────────────────────────────────
echo "Копируем исходники..."
for f in main.py overlay.py recorder.py transcriber.py injector.py processor.py config.py provider_config.py; do
    [ -f "$SRC_DIR/$f" ] && cp "$SRC_DIR/$f" "$APP/Contents/Resources/" && echo "  + $f"
done

# ── Сценарии (хранятся в ~/.config/hush/, не в бандле) ──────────────────────
HUSH_CFG="$HOME/.config/hush"
mkdir -p "$HUSH_CFG"
if [ ! -f "$HUSH_CFG/scenarios.json" ] && [ -f "$SRC_DIR/scenarios.json" ]; then
    cp "$SRC_DIR/scenarios.json" "$HUSH_CFG/scenarios.json"
    echo "  + scenarios.json → ~/.config/hush/"
elif [ -f "$SRC_DIR/scenarios.json" ]; then
    echo "  ✓ ~/.config/hush/scenarios.json уже существует (не перезаписываем)"
fi

# ── Ресурсы (иконки, изображения) ──────────────────────────────────────────
echo "Копируем ресурсы..."
for f in "$SRC_DIR"/*.png; do
    [ -f "$f" ] && cp "$f" "$APP/Contents/Resources/" && echo "  + $(basename "$f")"
done
for f in "$SRC_DIR"/*.svg; do
    [ -f "$f" ] && cp "$f" "$APP/Contents/Resources/" && echo "  + $(basename "$f")"
done

# ── parakeet-cli (бинарник распознавания речи) ─────────────────────────────
PARAKEET_BIN=""
if [ -f "$SRC_DIR/parakeet-cli" ]; then
    PARAKEET_BIN="$SRC_DIR/parakeet-cli"
elif [ -f "$HOME/.local/bin/parakeet-cli" ]; then
    PARAKEET_BIN="$HOME/.local/bin/parakeet-cli"
fi
if [ -n "$PARAKEET_BIN" ]; then
    DST="$APP/Contents/Resources/parakeet-cli"
    SRC_HASH=$(md5 -q "$PARAKEET_BIN")
    if [ "$SRC_HASH" != "$_OLD_PARAKEET_HASH" ]; then
        cp "$PARAKEET_BIN" "$DST"
        chmod +x "$DST"
        echo "  + parakeet-cli обновлён (хеш изменился — CoreML перекомпилирует модель)"
    else
        cp -p "$PARAKEET_BIN" "$DST"   # preserve timestamps to keep CoreML cache
        echo "  ✓ parakeet-cli без изменений (CoreML-кеш сохранён)"
    fi
else
    echo "  ⚠  parakeet-cli не найден ни в проекте, ни в ~/.local/bin/"
fi

# ── Модели (CoreML, ~400MB) — копируем только если изменились ───────────────
if [ -d "$SRC_DIR/models" ]; then
    rsync -a --checksum "$SRC_DIR/models" "$APP/Contents/Resources/" 2>/dev/null \
        || { echo "  (rsync недоступен, копируем через cp -rp)"; cp -rp "$SRC_DIR/models" "$APP/Contents/Resources/"; }
    echo "  ✓ models/ синхронизированы"
else
    echo "  ⚠  Модели не найдены в $SRC_DIR/models"
    echo "     Убедись что ~/.local/bin/parakeet-cli доступен системно."
fi

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
    <key>CFBundleVersion</key>          <string>0.1.0</string>
    <key>CFBundleShortVersionString</key><string>0.1</string>
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
cp hush.icns "$APP/Contents/Resources/hush.icns"
cp hush.icns "$APP/Contents/Resources/hush"  # без расширения для CFBundleIconFile

echo ""
echo "✓ Готово: $SRC_DIR/$APP"
echo ""
echo "Структура bundle:"
find "$APP" -not -path "*/models/*" | head -30
echo ""
echo "Запуск:  open \"$SRC_DIR/$APP\""
echo "Или перенеси в /Applications и запускай оттуда."
echo ""
echo "Примечание: требует python3.14 из Homebrew."
echo "API ключи: кнопка [КЛЮЧИ] в меню приложения → ~/.config/hush/providers.json"
