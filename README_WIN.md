# HUSH — Windows 11

Голосовая диктовка для Windows с транскрипцией через faster-whisper и опциональной постобработкой через LLM.

## Требования

- Windows 11 (или Windows 10 22H2+)
- Python 3.11 или выше
- Микрофон
- *(Опционально)* NVIDIA GPU с CUDA для ускоренной транскрипции

## Установка

### 1. Клонировать репозиторий

```powershell
git clone https://github.com/alexbic/hush.git
cd hush
git checkout windows
```

### 2. Установить зависимости

```powershell
pip install -r requirements_win.txt
```

При наличии CUDA (опционально, для ускорения в 3–5x):

```powershell
pip install torch --index-url https://download.pytorch.org/whl/cu121
```

### 3. Первый запуск

```powershell
python main_win.py
```

При первом запуске faster-whisper автоматически скачает модель `base` (~150 MB) в `~/.local/share/hush/models/`. Последующие запуски загружают модель за ~2 секунды.

## Использование

| Действие | Результат |
|---|---|
| **Зажать Right Alt** | Начать запись |
| **Отпустить Right Alt** | Остановить запись → транскрипция → вставка в активное приложение |
| **Shift + Right Alt** | Открыть главное окно HUSH |
| **Кнопка [ВСТАВИТЬ]** | Вставить текст из окна в предыдущее приложение |
| **Кнопка [КОПИРОВАТЬ]** | Скопировать текст в буфер обмена |

Значок HUSH появляется в системном трее (область уведомлений). Правый клик — меню с пунктами «Открыть», «История», «Настройки», «Выход».

## Настройка провайдеров LLM

Откройте настройки через трей или кнопку ⚙ в главном окне:

- **Anthropic API Key** — для Claude (haiku, sonnet, opus)
- **OpenAI API Key** — для GPT-4o-mini, GPT-4o
- **GLM API Key** — для GLM-4 Flash (Z.ai)
- **Ollama URL** — для локальных моделей (по умолчанию `http://localhost:11434`)

Если ни один провайдер не настроен — текст вставляется без LLM обработки.

## Смена модели Whisper

Размер модели настраивается через переменную окружения или в настройках:

```powershell
$env:HUSH_WHISPER_MODEL = "small"   # tiny | base | small | medium
python main_win.py
```

| Модель | Размер | Скорость (CPU) | Точность |
|---|---|---|---|
| `tiny` | ~75 MB | очень быстро | низкая |
| `base` | ~150 MB | быстро | хорошая |
| `small` | ~480 MB | средне | высокая |
| `medium` | ~1.5 GB | медленно | очень высокая |

## Смена языка

В настройках (поле «Язык распознавания»): `ru`, `en`, `es`.

Или через переменную окружения:

```powershell
$env:VOICE_LANG = "en"
python main_win.py
```

## Сборка EXE

Для создания автономного исполняемого файла (не требует установленного Python):

```powershell
.\build_win.ps1
```

Результат: `dist\HUSH\HUSH.exe`

Параметры сборки:

```powershell
.\build_win.ps1 -WhisperModel small          # использовать модель small
.\build_win.ps1 -SkipModelDownload           # не скачивать модель при сборке
.\build_win.ps1 -SkipInstall                 # не переустанавливать зависимости
```

## Устранение проблем

### Нет звука / микрофон не найден

```powershell
python -c "import sounddevice as sd; print(sd.query_devices())"
```

Установите нужное устройство:

```powershell
$env:HUSH_INPUT_DEVICE = "0"   # номер из query_devices()
```

### faster-whisper не устанавливается

На некоторых системах требуется Visual C++ Redistributable:

```
https://aka.ms/vs/17/release/vc_redist.x64.exe
```

### Ctrl+V не работает в некоторых приложениях

Некоторые приложения (игры, терминалы) блокируют SendInput. В этом случае HUSH помещает текст в буфер обмена — вставьте вручную через Ctrl+V.

### Логи

Все логи пишутся во временную папку:

```
%TEMP%\hush_win_debug.log
%TEMP%\hush_transcribe_win.log
```

## Файловая структура

```
hush/
├── main_win.py               ← Точка входа (Windows)
├── requirements_win.txt      ← Зависимости
├── build_win.ps1             ← Сборка EXE
├── src/
│   ├── config_win.py         ← Конфигурация Windows
│   ├── transcriber_win.py    ← faster-whisper транскрипция
│   ├── injector_win.py       ← Вставка через clipboard + SendInput
│   ├── overlay_win.py        ← UI (tkinter + pystray)
│   ├── recorder.py           ← Запись звука (кроссплатформенный)
│   ├── processor.py          ← LLM постобработка (кроссплатформенный)
│   └── provider_config.py    ← Конфиг провайдеров (кроссплатформенный)
└── README_WIN.md             ← Эта инструкция
```

Общий конфиг (`~/.config/hush/providers.json`, `scenarios.json`, `history.json`) совместим между macOS и Windows.
