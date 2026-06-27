# HUSH Windows — сборка исполняемого файла через PyInstaller
# Использование: .\build_win.ps1
# Требует: Python 3.11+, pip

param(
    [string]$WhisperModel = "base",          # tiny / base / small / medium
    [switch]$SkipModelDownload = $false,     # пропустить предзагрузку модели
    [switch]$SkipInstall = $false            # пропустить pip install
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== HUSH Windows Build ===" -ForegroundColor Green

# ── 1. Установка зависимостей ─────────────────────────────────────────────────

if (-not $SkipInstall) {
    Write-Host "`n[1/4] Установка зависимостей..." -ForegroundColor Cyan
    pip install -r requirements_win.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
    pip install pyinstaller>=6.0.0
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller install failed" }
}

# ── 2. Предзагрузка модели Whisper ───────────────────────────────────────────

$ModelDir = "$env:USERPROFILE\.local\share\hush\models\faster-whisper-$WhisperModel"

if (-not $SkipModelDownload) {
    Write-Host "`n[2/4] Загрузка модели Whisper '$WhisperModel'..." -ForegroundColor Cyan
    if (-not (Test-Path $ModelDir)) {
        python -c @"
from faster_whisper import WhisperModel
import os
model_dir = r'$ModelDir'
os.makedirs(os.path.dirname(model_dir), exist_ok=True)
print(f'Downloading faster-whisper-$WhisperModel ...')
m = WhisperModel('$WhisperModel', device='cpu', compute_type='int8', download_root=os.path.dirname(model_dir))
print('Model ready.')
"@
        if ($LASTEXITCODE -ne 0) { throw "Model download failed" }
    } else {
        Write-Host "  Модель уже есть: $ModelDir" -ForegroundColor DarkGreen
    }
}

# ── 3. Создание иконки (если нет hush.ico) ───────────────────────────────────

Write-Host "`n[3/4] Подготовка иконки..." -ForegroundColor Cyan

$IcoPath = "assets\hush.ico"
if (-not (Test-Path $IcoPath)) {
    # Ищем hush.icns и конвертируем, или создаём программно
    python -c @"
import os
from PIL import Image, ImageDraw, ImageFont

out = r'$IcoPath'
os.makedirs(os.path.dirname(out), exist_ok=True) if os.path.dirname(out) else None

# Создаём набор размеров для ICO
sizes = [16, 32, 48, 64, 128, 256]
frames = []
for s in sizes:
    img  = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([1, 1, s-1, s-1], fill='#22c55e')
    try:
        fnt = ImageFont.truetype('arial.ttf', int(s * 0.55))
    except Exception:
        fnt = ImageFont.load_default()
    draw.text((s//2, s//2), 'H', font=fnt, fill='#0d1f1a', anchor='mm')
    frames.append(img)

frames[0].save(out, format='ICO', sizes=[(s, s) for s in sizes], append_images=frames[1:])
print(f'Icon created: {out}')
"@
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Иконка не создана (продолжаем без неё)" -ForegroundColor Yellow
        $IcoPath = $null
    }
}

# ── 4. Сборка через PyInstaller ───────────────────────────────────────────────

Write-Host "`n[4/4] Сборка EXE через PyInstaller..." -ForegroundColor Cyan

$PyInstallerArgs = @(
    "main_win.py",
    "--name=HUSH",
    "--onedir",                         # --onedir быстрее стартует чем --onefile
    "--windowed",                       # без консольного окна
    "--noconfirm",                      # перезаписывать dist/ без вопросов
    "--add-data=src;src",               # включаем src/ в bundle
    "--paths=src",                      # добавляем src в путь поиска
    "--hidden-import=sounddevice",
    "--hidden-import=pynput.keyboard",
    "--hidden-import=pynput._util.win32",
    "--hidden-import=pystray._win32",
    "--hidden-import=faster_whisper",
    "--hidden-import=ctypes.wintypes",
    "--collect-all=faster_whisper",
    "--collect-all=ctranslate2",
    "--collect-all=tokenizers",
    "--collect-all=huggingface_hub",
    "--exclude-module=AppKit",
    "--exclude-module=objc",
    "--exclude-module=Foundation",
    "--exclude-module=Quartz"
)

if ($IcoPath -and (Test-Path $IcoPath)) {
    $PyInstallerArgs += "--icon=$IcoPath"
}

# Добавляем модели Whisper в bundle (опционально — большой размер)
# Раскомментируйте если хотите self-contained exe:
# if (Test-Path $ModelDir) {
#     $RelModelDir = "faster-whisper-$WhisperModel"
#     $PyInstallerArgs += "--add-data=$ModelDir;faster-whisper-$WhisperModel"
# }

& python -m PyInstaller @PyInstallerArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# ── Результат ─────────────────────────────────────────────────────────────────

Write-Host "`n=== Сборка завершена ===" -ForegroundColor Green
$ExePath = "dist\HUSH\HUSH.exe"
if (Test-Path $ExePath) {
    $Size = (Get-Item $ExePath).Length / 1MB
    Write-Host "  EXE: $PSScriptRoot\$ExePath ($([math]::Round($Size, 1)) MB)" -ForegroundColor Green
    Write-Host "  Папка: $PSScriptRoot\dist\HUSH\" -ForegroundColor Green
    Write-Host "`nДля запуска: .\dist\HUSH\HUSH.exe" -ForegroundColor Cyan
} else {
    Write-Host "  dist\HUSH\HUSH.exe не найден — проверьте ошибки выше" -ForegroundColor Red
}

Write-Host "`nПримечание: Модели Whisper (~150MB для 'base') хранятся в:" -ForegroundColor DarkGray
Write-Host "  $ModelDir" -ForegroundColor DarkGray
Write-Host "При первом запуске на новой машине модель скачается автоматически." -ForegroundColor DarkGray
