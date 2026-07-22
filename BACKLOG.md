# Backlog

## To do
- [ ] @privacy: Sanitize runtime diagnostic logs — DoD: avoid writing raw
      dictated text and full LLM result snippets to `/tmp/vi_*` and
      `/tmp/hush_processor.log` while preserving enough technical telemetry for
      hang diagnosis — priority: P1
- [ ] @desktop: Diagnose current HUSH runtime failure — DoD: reproduce or
      isolate the reported app problem, implement the smallest focused fix, and
      verify with the relevant checks from `TECH_STACK.md` — priority: P0
- [ ] @qa: Define repeatable local smoke checks — DoD: document compile,
      packaging, and manual runtime checks that agents can run before handoff —
      priority: P1
- [ ] @build: Decide dependency manifest policy — DoD: determine whether to add
      a tracked Python dependency manifest for AppKit/PyObjC, sounddevice, numpy,
      pynput, markdown, html2text, anthropic, and other runtime packages —
      priority: P1

## In progress
- None.

## Review
- [ ] @desktop: Review pre-existing `src/main.py` hotkey monitor changes — DoD:
      decide whether the recovery additions are part of the intended fix path or
      should be revised during the upcoming runtime diagnosis.
- [ ] @backend: Deferred OpenAI reasoning-model scenario requests — DoD:
      revisit only if maintainer asks; current direction is not to change
      OpenAI/model-selection behavior because the observed HTTP 400 may be a
      selected-model/configuration issue rather than the hang being debugged.

## Done
- [x] @desktop: Show action buttons for any loaded context text — DoD: when
      full-mode context contains text from history, manual typing, or rich
      blocks, the bottom action buttons appear consistently without requiring a
      new voice-input chunk — completed 2026-07-22; compile check passed,
      manual UI smoke still pending with rebuilt app/runtime.
- [x] @desktop: Harden PortAudio hang recovery — DoD: after
      `sd.InputStream().start()` timeout, reset recorder state, avoid blocking
      the hotkey worker/main AppKit path on PortAudio reinitialization, log the
      recovery result, and verify repeated hotkey presses still work — completed
      2026-07-22; compile check passed, manual repeated-hotkey verification
      still requires rebuilt app/runtime smoke.
- [x] @desktop: Analyze recent HUSH hangs from logs — DoD: inspected
      `/tmp` runtime logs, DiagnosticReports, current process state, and
      attempted a scoped macOS unified-log query; documented likely PortAudio
      and OpenAI scenario failure paths without intentionally copying private
      dictated content — completed 2026-07-22.
- [x] @docs: Inspect `ai-team`, LightRAG, and LightRAG MCP Connect examples —
      DoD: required filenames and operating conventions identified.
- [x] @docs: Bootstrap project state files — DoD: repository has the required
      project-state files populated from review and matching the `ai-team`
      operating protocol.

## Bugs from QA
- [ ] @qa: Current app does not work — repro: pending maintainer-provided exact
      symptom and local verification path.
