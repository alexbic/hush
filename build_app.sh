#!/bin/bash
# build_app.sh — собирает HUSH.app для локального тестирования.
# Лаунчер ссылается на исходники напрямую: модели не копируются, ребилд не нужен.
set -e
cd "$(dirname "$0")"

APP_NAME="HUSH"
APP="$APP_NAME.app"
SRC_DIR="$(pwd)"
PYTHON="$(command -v python3.14 || command -v python3)"

echo "=== Сборка $APP ==="
echo "Python : $PYTHON"
echo "Исходники: $SRC_DIR"

# ── Иконка ─────────────────────────────────────────────────────────────────
if [ ! -f hush.icns ]; then
    echo "Создаём hush.icns из hush_icon.svg..."
    ICONSET=$(mktemp -d)
    # SVG → 1024px PNG через QuickLook
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

# ── Структура bundle ────────────────────────────────────────────────────────
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources"

# ── Лаунчер ────────────────────────────────────────────────────────────────
cat > "$APP/Contents/MacOS/$APP_NAME" << LAUNCHER
#!/bin/bash
# Загружаем переменные окружения из ~/.hush_env (API ключи и т.п.)
[ -f "\$HOME/.hush_env" ] && source "\$HOME/.hush_env"
exec "$PYTHON" "$SRC_DIR/main.py" "\$@"
LAUNCHER
chmod +x "$APP/Contents/MacOS/$APP_NAME"

# ── Info.plist ──────────────────────────────────────────────────────────────
cat > "$APP/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>             <string>HUSH</string>
    <key>CFBundleDisplayName</key>      <string>HUSH</string>
    <key>CFBundleIdentifier</key>       <string>net.alexbic.hush</string>
    <key>CFBundleVersion</key>          <string>0.1.0</string>
    <key>CFBundleShortVersionString</key><string>0.1</string>
    <key>CFBundleExecutable</key>       <string>HUSH</string>
    <key>CFBundlePackageType</key>      <string>APPL</string>
    <key>CFBundleIconFile</key>         <string>hush</string>
    <key>LSUIElement</key>              <true/>
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

echo ""
echo "✓ Готово: $SRC_DIR/$APP"
echo ""
echo "Запуск:  open \"$SRC_DIR/$APP\""
echo ""
echo "API ключи (опционально) — создай ~/.hush_env:"
echo "  export ANTHROPIC_API_KEY=sk-ant-..."
echo "  export OPENAI_API_KEY=sk-..."
