# Project: HUSH

**Slug:** `hush`  
**Created:** 2026-07-22  
**Version:** 2.0  
**Status:** active local macOS desktop app

## Goal
Provide a local-first macOS voice input tool that records speech with a hotkey,
transcribes it on-device with Parakeet/CoreML, optionally post-processes it with
an LLM scenario, and inserts the result into the previously active app.

## Stakeholders
- Maintainer: repository maintainer
- Users: macOS users who want fast dictation with optional LLM formatting
- Operators: agents or humans maintaining local builds and runtime behavior

## Current Focus
Model distribution overhaul and CI/CD pipeline optimization:

- Remove CoreML models from application bundle to reduce size
- Implement automatic model downloading from Google Drive with fallback
- Update CI/CD pipeline for DMG creation and automated releases
- Change distribution format from .app bundle to DMG for better user experience

## Repository Layout
- `src/main.py` — app lifecycle, hotkey event handling, session state, history,
  paste flow, provider monitoring, sleep/wake handling.
- `src/overlay.py` — AppKit overlay UI, panels, scenario editor, history UI,
  markdown rendering, settings, provider status UI.
- `src/recorder.py` — microphone capture through `sounddevice`, audio
  normalization, WAV chunk creation.
- `src/transcriber.py` — `parakeet-cli` process management, warm-up, CoreML
  model env, transcription cleanup.
- `src/processor.py` — LLM post-processing through Ollama, Anthropic,
  OpenAI-compatible APIs, GLM, and optional n8n webhook.
- `src/provider_config.py` — provider config persistence and availability/model
  probing.
- `src/config.py` — runtime paths, language/model defaults, provider getters.
- `src/injector.py` — clipboard/AppKit paste helpers.
- `src/launcher.c` — native launcher that embeds Python from Homebrew framework
  and keeps `NSBundle.mainBundle()` pointed at `HUSH.app`.
- `build_app.sh` — local packaging script for `HUSH.app`.
- `assets/` — app icons, brand assets, UI images.
- `defaults/scenarios.json` — default scenario definitions copied to user config
  on first build/setup when absent.
- `models/` — ignored local CoreML model artifacts.
- `parakeet-cli/` — ignored local transcription CLI artifact.
- `.github/workflows/` — GitHub Actions workflows for automated app building and
  releases (in progress).
- Project state files — `AGENTS.md`, `PROJECT.md`, `SPEC.md`, `PLAN.md`,
  `ROADMAP.md`, `BACKLOG.md`, `STATUS.md`, `TECH_STACK.md`.

## Runtime Data
- User settings: `~/.config/hush/settings.json`
- Provider config: `~/.config/hush/providers.json`
- Scenarios: `~/.config/hush/scenarios.json`
- History: `~/.config/hush/history.json`
- Stable model path: `~/.local/share/hush/models/parakeet-tdt-0.6b-v3-coreml`
- Stable CLI path: `~/.local/bin/parakeet-cli`
- Runtime lock/log/temp files: `/tmp/hush*`, `/tmp/vi_*`
- Built app bundle: `HUSH.app` (local build or GitHub release)

## Linked Projects

| Project | Relationship | Source of Truth | What changes here |
| --- | --- | --- | --- |
| `ai-team` | Project-state template and operating protocol reference | `ai-team/projects/_template` and configured examples | Adopt matching project state files and workflow |
| NVIDIA Parakeet / local CoreML artifact | Speech transcription runtime | Bundled/local model and `parakeet-cli` artifacts | Paths, launch env, warm-up/cancel behavior |
| Ollama / Anthropic / OpenAI / GLM | Optional LLM post-processing providers | User provider configuration | Provider routing, UI status, model selection |
| GitHub Actions | Automated app building and distribution | `.github/workflows/` directory | Build scripts, release automation, CI/CD |

Dependency rule: if a task changes private user config, provider secret handling,
model paths, clipboard/history behavior, or multi-monitor UI behavior, document 
the boundary in `SPEC.md` and update `STATUS.md` with verification and rollback notes.
