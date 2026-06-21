# HUSH — Voice Input for macOS

A silent, keyboard-free dictation tool for macOS. Hold a hotkey, speak, release — your words appear in the active app, optionally polished by an LLM.

Built entirely with Python and native AppKit. No Electron, no subscriptions.

---

## How It Works

HUSH has two recording modes:

**Silent Mode** — lightweight floating pill, auto-pastes after a short pause:
```
Hold Right ⌥  →  speak  →  release  →  text pasted into active app
```

**Full Mode** — full overlay card, accumulate multiple blocks, paste on demand:
```
⇧⌥  →  window opens  →  hold ⌥ to record chunks  →  Shift+Enter to paste
```

HUSH uses [Parakeet TDT 0.6B](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) — NVIDIA's CTC/TDT model running locally via Apple Neural Engine (CoreML). First run compiles the model (~2 min); subsequent runs start in seconds.

---

## Modes

### Silent Mode

Activated by holding **Right ⌥**. A small floating pill appears anchored to the screen edge.

**States:**
- **Recording** — EQ bars animate in accent color
- **Transcribing** — recognized chunks appear as text accumulates inside the pill
- **Countdown** — 4-second grace period (bars shift green→red) before LLM processing
- **Processing** — recognized text at top, app icon + EQ indicator at bottom
- **Hover while processing** — overlay shows interrupt button; click to paste raw text immediately, skipping LLM

**Multi-chunk recording:** release ⌥ between phrases to transcribe each chunk separately — they accumulate in the pill and are processed together when the countdown fires.

The pill **remembers its screen position** across sessions. Drag it anywhere.

---

### Full Mode

Activated by **⇧⌥** (Shift + Right Option). Opens the main overlay card without starting recording.

**Workflow:**
1. **⇧⌥** — open the full-mode window
2. **Hold ⌥** — record a block; **release** — transcribe; block appears in the window
3. Repeat to add more blocks — they accumulate without auto-pasting
4. Optionally click a **scenario button** to process the text with an LLM
5. **Shift+Enter** or **[↵]** button — paste result into the previously active app, close window

**Default scenario for Full Mode:** in scenario settings you can mark one scenario as *full mode default* (★). When set, pressing Shift+Enter without having manually applied a scenario will automatically run that LLM first, then paste the result.

**Cancel:** double-tap ⌥ at any time to discard everything and close both overlays.

---

## Hotkeys

| Gesture | Action |
|---------|--------|
| Hold Right ⌥ | Start recording (silent mode) |
| Release Right ⌥ | Stop recording, transcribe |
| ⇧⌥ | Open full-mode window |
| ⌥ (in full-mode window) | Record next block |
| Double-tap ⌥ | Cancel session, close all overlays |
| Enter (during countdown) | Force immediate raw paste, skip scenario |
| Shift+Enter (full mode) | Paste accumulated text (applies default scenario if set) |
| ⌥ (during LLM processing) | Interrupt LLM, paste raw text instead |

---

## Scenarios (LLM Post-Processing)

Scenarios are configurable LLM prompts applied after transcription.

Built-in scenarios (editable in `~/.config/hush/scenarios.json`):

| Name | What it does |
|------|-------------|
| **MAIN** | Smart formatter: detects context type (prompt, task list, letter, note), cleans filler words |
| **ЧИСТКА** | Punctuation, capitalization, removes filler words, typographic quotes |
| **ПИСЬМО** | Formats as a business letter |
| **Задачи** | Converts to a checkbox task list (Markdown) |
| **MD** | Formats as Markdown with headers and lists |

**Supported LLM providers** (configured per scenario):

```json
{ "model": "ollama:qwen3:8b" }                      // local via Ollama (default)
{ "model": "anthropic:claude-haiku-4-5-20251001" }  // Anthropic API
{ "model": "openai:gpt-4o-mini" }                   // OpenAI-compatible API
{ "model": "glm:glm-4-flash" }                      // GLM (Zhipu) API
{ "model": null }                                    // auto: Ollama → Anthropic fallback
```

**Scenario flags** (set in the settings UI):

| Flag | Effect |
|------|--------|
| `silent` | Scenario appears in silent pill only |
| `full_default` ★ | Auto-applied before paste in full mode (one per list) |

Scenarios without a prompt paste raw transcription directly.

---

## Scenario Settings

Open settings via the **⚙** button. Click any scenario to edit it.

- **Label fields** (RU / EN / ES) — button label, up to 6 characters
- **Model** — override the LLM for this scenario (empty = auto)
- **Prompt** — system instruction; transcribed text is appended
- **silent mode** toggle — restrict scenario to silent pill
- **full mode по умолч.** toggle ★ — mark as the default for full mode (radio — only one allowed)

Unsaved changes are detected when switching scenarios or closing — a save/discard prompt appears.

---

## History

- Last 50 transcriptions stored in `~/.config/hush/history.json`
- Browse, copy, re-paste, or delete entries from the history panel
- Sessions group multi-chunk dictations into one entry
- Entries can be merged (continuation of an earlier session)

---

## Color Themes

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
  "silent": true,
  "full_default": false
}
```

Changes take effect without restarting HUSH.

---

## Architecture

```
main.py          Hotkey listener, session lifecycle, paste logic
overlay.py       All UI: pill, full-mode card, history panel, themes
recorder.py      Audio capture via sounddevice
transcriber.py   Parakeet TDT subprocess wrapper + CoreML warmup
processor.py     LLM routing (Ollama / Anthropic / OpenAI / GLM / n8n)
injector.py      Accessibility-based text injection
config.py        Paths, API keys, constants
build_app.sh     Builds self-contained HUSH.app bundle
launcher.c       Thin C launcher (ensures correct NSBundle for status bar)
```

### Silent mode session lifecycle

```
Hold ⌥  →  recorder.start()  →  audio stream
Release ⌥  →  recorder.stop()  →  wav file queued
              transcriber.transcribe(wav)  [CoreML / ANE]
              text appended to pill accumulation
              4-second countdown
              processor.process_with_prompt(text, scenario)
              injector paste  →  prev app receives text
```

### Full mode session lifecycle

```
⇧⌥  →  overlay opens (standby, no recording)
⌥ held  →  recorder.start()
⌥ released  →  recorder.stop()  →  transcribe  →  block shown in window
(repeat for more blocks)
Shift+Enter  →  [optional: default scenario LLM]  →  paste  →  window closes
```

### CoreML model cache

Parakeet is a CoreML model (~400 MB, `parakeet-tdt-0.6b-v3-coreml`). Apple Neural Engine compiles device-specific execution plans on first run and caches them. Subsequent runs skip compilation and start in ~7 seconds.

HUSH preserves the cache by keeping the binary at a stable path (`~/.local/bin/parakeet-cli`). Rebuilding the app bundle does not invalidate the cache.

---

## Privacy

- **All speech processing is local** — Parakeet TDT runs on your Mac via CoreML/ANE
- Audio is never stored longer than a single session
- Text is sent to a remote LLM only if you configure a cloud scenario (Anthropic/OpenAI/GLM)
- With Ollama scenarios, everything stays on-device

---

## License

MIT
