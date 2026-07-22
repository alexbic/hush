# Spec: HUSH

> Source of truth for expected product behavior. Update through explicit project
> state changes when behavior or scope changes.

## Goal
HUSH must let a macOS user dictate text from any app with minimal friction,
transcribe speech locally, optionally format it through a selected LLM scenario,
and paste the result into the previously active application.

## Users and Scenarios
- User holds Right Option in silent mode, speaks, releases, and receives pasted
  text after transcription and optional default scenario processing.
- User opens full mode with Shift + Right Option, dictates multiple chunks,
  reviews or edits them, applies scenarios, and pastes or copies the result.
- User manages scenario prompts, default scenario flags, provider configuration,
  language, theme, font size, history, and auxiliary panels from the overlay UI.
- User can use local Ollama or cloud providers while retaining local speech
  capture/transcription by default.

## Functional Requirements
- F1: Run as a macOS accessory app without a Dock icon and with a menu bar item.
- F2: Prevent multiple concurrent HUSH instances through a process lock.
- F3: Capture microphone audio while the hotkey is held and stop capture on release.
- F4: Transcribe audio chunks through Parakeet/CoreML using the current UI language.
- F5: Support Russian, English, and Spanish UI/transcription language IDs.
- F6: Support silent mode accumulation with a 4-second finalize grace period.
- F7: Support immediate raw paste during silent-mode countdown.
- F8: Support full mode with multiple blocks, scenario application, undo, history
  loading, and copy/paste actions.
- F9: Store scenario, provider, settings, and history JSON under `~/.config/hush`.
- F10: Copy bundled `parakeet-cli` and models to stable user paths on first run
  when missing.
- F11: Probe LLM providers and expose availability/model choices in the UI.
- F12: Route scenario processing to Ollama, Anthropic, OpenAI-compatible, GLM, or
  optional n8n webhook according to scenario config.
- F13: Insert text into the previously active app through clipboard plus
  Accessibility-driven paste.
- F14: Preserve history entries, session groupings, soft deletion references, and
  parent links used for scenario undo.
- F15: Handle system sleep/wake by cancelling transcription, reinitializing
  PortAudio, recreating hotkey monitors, and warming Parakeet.

## Non-Functional Requirements
- NF1: Speech capture/transcription remains local unless the user chooses an LLM
  scenario/provider that sends text externally.
- NF2: The repository must not commit user secrets, provider configs, local
  history, generated app bundles, logs, audio chunks, or large model artifacts.
- NF3: Hotkey handlers must not block the AppKit main run loop.
- NF4: Long operations such as recording stop, transcription, provider probes,
  LLM calls, paste sleeps, and warm-up must run off the main UI path when needed.
- NF5: Runtime failures should degrade gracefully: failed provider calls return
  raw text, failed transcription returns empty text, missing permissions leave
  text in clipboard when paste cannot be fired.
- NF6: Build and runtime instructions must be reproducible from documented local
  dependencies.
- NF7: Project state files must stay current after substantial work.

## Out of Scope
- Hosted server operation for speech capture.
- Storing real provider secrets or user dictation history in git.
- Replacing the local Parakeet/CoreML transcription path without an explicit
  architecture decision.
- Changing app bundle identity, user config paths, or privacy boundaries as an
  incidental fix.

## Acceptance Criteria
- [ ] Fresh repository orientation is possible by reading the project state files
      before implementation files.
- [ ] Python sources compile successfully in the supported local Python version.
- [ ] `build_app.sh` can produce `HUSH.app` when local Python, model artifacts,
      assets, and `parakeet-cli` are present.
- [ ] On macOS with Accessibility and Microphone permissions, Right Option
      records and release triggers transcription.
- [ ] Silent mode can paste raw text or scenario-processed text into the previous
      active app.
- [ ] Full mode supports accumulating blocks, applying scenarios, undoing a
      scenario result, copying, and pasting.
- [ ] User provider keys are read only from local user config or environment and
      are never committed or printed.

## Open Questions
- What exact runtime failure should be fixed next after project structure setup?
- Should this repository add a dependency manifest for the Python packages
  currently implied by imports and README?
- Should `build_app.sh` avoid mutating `~/.config/hush/scenarios.json` during
  build, or is that intentional local setup behavior?
- Should active development target only Python 3.14, or keep launcher fallback
  support for Python 3.13/3.12?
