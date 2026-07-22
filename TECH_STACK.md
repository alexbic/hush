# Tech Stack

## Runtime
- macOS accessory app (`LSUIElement`) built as `HUSH.app`.
- Python runtime loaded from Homebrew Python framework by `src/launcher.c`;
  packaging script prefers `python3.14` and falls back to `python3`.
- AppKit/PyObjC for UI, menu bar, windows, event monitors, pasteboard, and
  workspace/sleep notifications.
- `pynput` for firing Cmd+V and trailing-space keystrokes after clipboard setup.
- `sounddevice` and PortAudio for microphone capture.
- `numpy` and `wave` for audio buffering, normalization, and WAV output.
- `parakeet-cli` with NVIDIA Parakeet TDT 0.6B CoreML artifacts for local
  transcription.

## UI and App Flow
- `src/main.py` owns lifecycle, hotkey monitors, session state, history, paste
  orchestration, provider monitoring, and sleep/wake recovery.
- `src/overlay.py` owns AppKit UI, scenario editor, settings, history panels,
  markdown rendering, provider status, and visual recording/transcription states.
- Right Option starts/stops silent recording.
- Shift + Right Option opens/closes full mode.
- Enter during silent countdown pastes raw accumulated text.
- Shift+Enter in full mode can apply the full-default scenario before paste.

## Transcription and Audio
- Sample rate: 16 kHz mono.
- Audio chunks are normalized by peak and written as temporary WAV files.
- Transcriber uses `PARAKEET_LANG_ID` and `PARAKEET_MODEL_DIR` env values.
- Stable CLI path: `~/.local/bin/parakeet-cli`.
- Stable model path:
  `~/.local/share/hush/models/parakeet-tdt-0.6b-v3-coreml`.
- Bundle fallback paths live under `HUSH.app/Contents/Resources/`.
- Warm-up runs on launch, periodically, and after some cancellation/wake paths
  to keep CoreML/ANE startup latency down.

## LLM Providers
- Ollama local API: `http://localhost:11434` by default.
- Anthropic SDK for Anthropic scenarios.
- OpenAI-compatible HTTP API for OpenAI and GLM/Z.ai scenarios.
- Optional n8n webhook when a scenario prompt starts with `n8n:`.

## Key Environment Variables
Document variable names only. Do not commit or print real values.

- `VOICE_LANG_ID`: optional fallback language token id.
- `VOICE_LLM_MODEL`: fallback Anthropic model name for scenarios without an
  explicit model.
- `VOICE_N8N_WEBHOOK`: optional webhook URL for n8n-backed processing.
- `RESOURCEPATH`: set by app launcher/build fallback to locate bundled resources.
- Provider keys and base URLs are persisted in `~/.config/hush/providers.json`,
  not committed to the repository.

## User Data Paths
- `~/.config/hush/settings.json`
- `~/.config/hush/providers.json`
- `~/.config/hush/scenarios.json`
- `~/.config/hush/history.json`
- `/tmp/hush.lock`
- `/tmp/hush_*`, `/tmp/vi_*` logs/audio/session files

## Ignored / Generated Artifacts
- `models/`
- `parakeet-cli/`
- `HUSH.app/`
- `dist/`
- `build/`
- `__pycache__/`
- `*.pyc`
- `*.wav`
- `*.log`
- `.env`
- `.hush_env`
- `.claude/`
- root-level generated image/document copies listed in `.gitignore`

## Local Commands
```bash
python3 -m compileall -q src
./build_app.sh
open /Users/bic/dev/hush/HUSH.app
```

Use `./build_app.sh` only when local packaging side effects are acceptable:
it rebuilds `HUSH.app` and may initialize `~/.config/hush/scenarios.json` if
that file is missing.

## Manual Runtime Smoke
- Confirm macOS Accessibility and Microphone permissions for `HUSH.app` or the
  active Python runtime.
- Launch `HUSH.app`.
- Press and hold Right Option, speak briefly, release, and confirm transcription
  starts.
- Confirm either raw paste or scenario-processed paste reaches the previously
  active app.
- Open full mode with Shift + Right Option, add a block, apply or skip a
  scenario, and paste/copy.

## Notes for Agents
- Prefer `rg` for search.
- Do not print provider keys, user history, or raw dictated text from user config.
- Do not delete local model artifacts or user config/history unless the
  maintainer explicitly asks.
- Keep project state files current when behavior, plans, verification, runtime
  status, or dependencies change.
