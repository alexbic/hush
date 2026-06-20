# HUSH — Voice Input for macOS

A silent, keyboard-free dictation tool for macOS. Hold a hotkey, speak, release — your words appear in the active app, optionally polished by an LLM.

Built entirely with Python and native AppKit. No Electron, no subscriptions.

---

## How It Works

```
Hold Right ⌥  →  speak  →  release  →  text appears in active app
```

HUSH uses [Parakeet TDT 0.6B](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) — NVIDIA's CTC/TDT model running locally via Apple Neural Engine (CoreML). First run compiles the model (~2 min); subsequent runs start in seconds.

---

## Features

### Dictation

- **Hold Right ⌥** to start recording
- **Release** to stop and transcribe
- Audio processed **locally** — no data leaves your machine
- Recognized text is **pasted automatically** into the previously active app via Accessibility API

### Silent Pill

When you hold the hotkey, a small floating pill appears anchored to the screen edge:

- **Recording** — EQ bars animate in accent color
- **Transcribing** — recognized chunks appear as text accumulates inside the pill
- **Countdown** — 4-second grace period (green→red bars) before LLM processing
- **Processing** — recognized text at top, app icon + EQ indicator at bottom
- **Hover while processing** — overlay shows "Оставить без обработки"; click to paste raw text immediately, skipping LLM

The pill **remembers its screen position** across sessions. Drag it anywhere — next session it appears in the same spot.

### Scenarios (LLM Post-Processing)

Scenarios are configurable LLM prompts applied after transcription. Select a scenario before dictating; the transcribed text is sent to the LLM and the result is pasted.

Built-in scenarios (editable in `~/.config/hush/scenarios.json`):

| Name | What it does |
|------|-------------|
| **MAIN** | Smart formatter: detects context type (prompt, task list, letter, note), cleans filler words, formats accordingly |
| **ЧИСТКА** | Punctuation, capitalization, removes filler words, applies typographic quotes |
| **ПИСЬМО** | Formats as a business letter |
| **Задачи** | Converts to a checkbox task list (Markdown) |
| **MD** | Formats as Markdown with headers and lists |

**Supported LLM providers** (configured per scenario):

```json
{ "model": "ollama:qwen3:8b" }           // local via Ollama (default)
{ "model": "anthropic:claude-haiku-4-5-20251001" }  // Anthropic API
{ "model": "openai:gpt-4o-mini" }         // OpenAI-compatible API
{ "model": "glm:glm-4-flash" }            // GLM (Zhipu) API
{ "model": null }                          // auto: Ollama → Anthropic fallback
```

Scenarios without a prompt paste raw transcription directly (useful for silent mode without LLM).

### Interrupting LLM Processing

While the LLM is processing, hovering the pill shows a soft overlay. Clicking it:
- Cancels the LLM request
- Pastes the raw transcribed text immediately
- Releases the hotkey lock so you can dictate again right away

### History

- Last 50 transcriptions stored in `~/.config/hush/history.json`
- **Double-tap Right ⌥** to open the full history panel
- Browse, copy, re-paste, or delete entries
- Sessions group multi-chunk dictations into one entry
- Entries can be merged (continuation of an earlier session)

### Color Themes

8 built-in themes — switch from the pill's settings menu:

| Theme | Background | Accent |
|-------|-----------|--------|
| emerald | dark green | bright green |
| ocean | dark blue | cyan |
| neon | dark purple | magenta |
| gold | dark amber | yellow |
| paper | cream | dark green |
| sky | light blue | dark blue |
| sand | warm beige | brown |
| arctic | icy white | teal |

### Multi-language UI

Scenario labels support EN / RU / ES — the active language follows system locale.

---

## Requirements

- **macOS 13+** (Ventura or newer)
- **Python 3.14** (`brew install python@3.14`)
- **Accessibility permission** — required for automatic paste (Cmd+V injection)
- **Microphone permission** — requested on first run
- Parakeet TDT CoreML model (~400 MB, included)

Optional (for LLM scenarios):
- [Ollama](https://ollama.ai) running locally with a model loaded
- Anthropic / OpenAI / GLM API key

---

## Installation

### From source

```bash
git clone https://github.com/alexbic/hush.git
cd hush

# Install Python dependencies
pip3.14 install pyobjc sounddevice pynput anthropic openai

# Build the app bundle
bash build_app.sh

# Launch
open HUSH.app
```

On first launch HUSH copies `parakeet-cli` and the CoreML model to stable paths (`~/.local/bin/` and `~/.local/share/hush/`) so the model cache survives app updates.

### API keys (optional)

Create `~/.hush_env`:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export OLLAMA_BASE_URL="http://localhost:11434"   # default
```

HUSH loads this file at startup.

---

## Customizing Scenarios

Edit `~/.config/hush/scenarios.json`. Each scenario:

```json
{
  "name": "CLEAN",
  "label": { "en": "CLEAN", "ru": "ЧИСТКА", "es": "LIMPI" },
  "model": "ollama:qwen3:8b",
  "prompt": "Your prompt here. Input text is appended after ===",
  "silent": true
}
```

- `"silent": true` — scenario appears in silent pill mode only
- `"prompt": ""` — paste raw transcription without LLM

Changes take effect without restarting HUSH.

---

## Architecture

```
main.py          Hotkey listener, session lifecycle, paste logic
overlay.py       All UI: pill, processing card, history panel, themes
recorder.py      Audio capture via sounddevice
transcriber.py   Parakeet TDT subprocess wrapper + CoreML warmup
processor.py     LLM routing (Ollama / Anthropic / OpenAI / GLM / n8n)
injector.py      Accessibility-based text injection
config.py        Paths, API keys, constants
build_app.sh     Builds self-contained HUSH.app bundle
launcher.c       Thin C launcher (ensures correct NSBundle for status bar)
```

### Session lifecycle

```
hotkey press  →  recorder.start()  →  audio chunks
hotkey release →  recorder.stop()  →  wav file
                  transcriber.transcribe(wav)
                    └─ parakeet-cli [CoreML / ANE]
                  text accumulated in pill
                  4-second countdown
                  processor.process_with_prompt(text, scenario)
                  injector / subprocess paste
```

### CoreML model cache

Parakeet is a CoreML model (~400 MB, `parakeet-tdt-0.6b-v3-coreml`). Apple Neural Engine compiles device-specific execution plans on first run and caches them. Subsequent runs skip compilation and start in ~7 seconds.

HUSH preserves the cache by keeping the binary at a stable path (`~/.local/bin/parakeet-cli`). Rebuilding the app bundle does not invalidate the cache.

---

## Hotkeys

| Gesture | Action |
|---------|--------|
| Hold Right ⌥ | Start recording |
| Release Right ⌥ | Stop recording, transcribe, paste |
| Double-tap Right ⌥ (idle) | Open history panel |
| Double-tap Right ⌥ (counting down) | Force immediate paste (skip countdown) |
| Double-tap Right ⌥ (recording) | Cancel current session |
| Click pill (processing) | Paste raw text, skip LLM |

---

## Privacy

- **All speech processing is local** — Parakeet TDT runs on your Mac via CoreML/ANE
- Audio is never stored longer than a single session
- Text is sent to a remote LLM only if you configure a cloud scenario (Anthropic/OpenAI/GLM)
- With Ollama scenarios, everything stays on-device

---

## License

MIT
