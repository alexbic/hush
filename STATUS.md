# Status: HUSH

**Updated:** 2026-07-22T21:31:00Z by Codex

## Brief
HUSH now has a targeted full-mode UI fix for a regression where loading context
from history could leave the bottom action buttons hidden until a new voice
chunk was recorded. The visibility rule now follows any non-empty context,
including rich blocks restored from history.

## Progress
- M1: ██████████ 100%
- M2: ░░░░░░░░░░ 0%
- M3: ░░░░░░░░░░ 0%

## Completed
- Reviewed `ai-team/projects/_template`.
- Reviewed configured examples in `lightrag` and `lightrag-mcp-connect`.
- Reviewed HUSH README files, build script, runtime modules, ignored artifacts,
  and current working-tree state.
- Created the required project state files.

## In Progress
- None.

## Review Findings
- Repository has no tracked dependency manifest. Runtime imports imply PyObjC
  (`AppKit`, `objc`, `ApplicationServices`), `sounddevice`, `numpy`, `pynput`,
  `markdown`, `html2text`, and `anthropic`, while packaging depends on Homebrew
  Python and macOS tools.
- Repository has no automated tests. The safest current baseline check is Python
  compilation; real end-to-end verification needs macOS permissions, microphone,
  `parakeet-cli`, CoreML model artifacts, and an active UI session.
- `build_app.sh` is both a packager and local setup helper: it removes/rebuilds
  `HUSH.app`, copies resources, may create `assets/hush.icns`, and may copy
  default scenarios into `~/.config/hush/scenarios.json` when absent.
- Runtime writes diagnostic logs and audio/temp artifacts under `/tmp`; these
  may contain private user text or operational details and should not be copied
  into project docs.
- Speech transcription is local-first, but scenario processing can send dictated
  text to Ollama, Anthropic, OpenAI-compatible providers, GLM, or n8n depending
  on user scenario config.
- `src/main.py` already had uncommitted changes before this bootstrap. They add
  hotkey monitor validation/recovery and sleep/wake logging. Treat these as
  pre-existing user/worktree changes unless the maintainer asks to revise them.
- `overlay.py` is very large and owns many responsibilities; fixes touching UI
  state should be tightly scoped and verified manually.
- Current UI regression: in full mode, loading history can end with content in
  rich blocks while `_tv` is empty, and one visibility path checks only `_tv`
  text before switching out of `history_open`. That leaves bottom action
  buttons hidden even though the context already contains text.
- The fix centralizes content detection in `src/overlay.py` so history-loaded
  blocks, manual typing, and text already present in `_st["text"]` all drive
  the same bottom-action visibility behavior.
- Runtime log review on 2026-07-22 found fresh `/private/tmp` logs but no HUSH
  `.ips` crash report in `~/Library/Logs/DiagnosticReports` for the last two
  days. A scoped macOS unified-log query was attempted but RunningBoard/TCC
  noise made it impractical without a narrower export pipeline.
- Current process check found `/Applications/HUSH.app/Contents/MacOS/HUSH`
  running as PID 39069. The installed app resources differ from current
  `src/main.py`, so rebuilding/reinstalling matters before runtime verification.
- `/private/tmp/vi_debug.log` contains 969 `recorder.start(): OK` and matching
  969 `_stop_and_queue` completions, suggesting normal recording stop is not the
  common stuck path.
- The same log contains 7 `recorder.start() FAILED:
  sd.InputStream()/start() hung (PortAudio)` events. Around these events,
  low-level key events continue but user-visible recording does not always
  recover promptly. This points to PortAudio start/recovery as a likely hang
  source.
- `/private/tmp/vi_transcribe.log` contains 963 completed Parakeet runs with
  `rc=0`, 2 killed Parakeet processes (`rc=-15`), and no invalid-file skips.
  Transcription itself looks mostly healthy in the sampled logs.
- `/private/tmp/hush_processor.log` shows 481 scenario-processing starts and
  476 OpenAI HTTP 400 errors. The captured API error says `temperature` is not
  supported by the selected model, which matches `openai:o3-mini`/reasoning
  model request handling in `src/processor.py`.
- Privacy issue found during review: runtime logs currently store raw dictated
  text snippets and LLM result snippets. Future diagnostics should redact these
  by default.
- PortAudio recovery patch completed in source: `recorder.start()` now marks a
  timed-out start attempt as abandoned, resets recorder state, and closes any
  late-created stream; `main.py` now schedules PortAudio recovery outside the
  hotkey/UI path and logs only `ok=True/False`.
- OpenAI/model handling is intentionally deferred per maintainer direction; the
  current fix scope is PortAudio recovery only.

## Blockers
- Full app verification may require local macOS Accessibility/Microphone
  permissions and GUI execution approval.
- Exact forced-quit timestamps cannot be recovered from `hush_launcher.log`
  because launcher entries have no timestamps and no normal/abnormal exit
  marker.

## Verification
- Repository review: completed by reading project files and examples.
- Python compile check: passed with `python3 -m compileall -q src`.
- UI visibility bug fix: completed in `src/overlay.py` by routing action-row
  visibility through a shared "has context content" check used by
  `history_open` transitions, panel restore, history close, and rich-block
  restore paths.
- App build/runtime smoke: not run during bootstrap; defer until failure
  diagnosis unless explicitly requested.
- Recent-log analysis: completed from `/private/tmp/vi_debug.log`,
  `/private/tmp/vi_transcribe.log`, `/private/tmp/vi_recorder.log`,
  `/private/tmp/hush_processor.log`, session-directory metadata, and current
  process state. No code verification was run because no runtime code was
  changed in this analysis step.
- PortAudio recovery source check: `python3 -m compileall -q src` passed after
  changes to `src/main.py` and `src/recorder.py`.

## Next
- Rebuild/reinstall HUSH before a focused manual UI smoke so the app bundle
  matches repository sources.
- Verify the fixed repro path manually: open full mode, load history content,
  confirm bottom action buttons appear without a new dictation chunk.
