# Status: HUSH

**Updated:** 2026-07-26T00:40:00Z by AI Agent

## Brief
HUSH runtime fully verified end-to-end on 2026-07-25/26. All three reported
issues (auto-paste, About overlay centering, Enter during countdown) were the
**same root cause**: the app bundle was ad-hoc signed without a stable
identifier, so macOS TCC treated every reinstall as a new app and silently
dropped the Accessibility grant. Without Accessibility, `_commit_and_paste`
skipped Cmd+V and the global KeyDown monitor that powers Enter-during-countdown
(`_force_paste_raw_now`) stayed mute. Fixed by signing the bundle with
`--identifier net.alexbic.hush` in `build_app.sh`. About overlay also fixed to
always center on screen. Accessibility re-granted on the maintainer's Mac after
removing the old TCC entry; `AX trusted: True` confirmed in logs and auto-paste
confirmed firing. Ready for v2.0 tag.

## Progress
- M1: ██████████ 100%
- M2: ██████████ 100%
- M3: ██████████ 100%
- v2.0 release: ████░░░░░░ 40% (code ready, awaiting Accessibility verify + tag)

## Completed
- Cleaned up git tags, keeping only v1.0 and v1.1 as stable releases
- Returned to version 1.1 as last stable version and prepared for v1.2 release
- Removed CoreML models from application bundle to reduce size from 500MB+ to ~50MB
- Implemented automatic model downloading from Google Drive with fallback mechanism
- Updated CI/CD pipeline for DMG creation and automated releases
- Changed distribution format to DMG for better user experience
- Created v1.2 tag and tested complete build process
- Updated model loading logic in src/main.py to use external URL
- Fixed CI/CD pipeline with proper DMG creation workflow
- Committed and pushed all changes to GitHub

## Completed (this session)
- Diagnosed real root cause: ad-hoc signature without stable identifier → TCC
  drops Accessibility on every reinstall → auto-paste AND Enter-during-countdown
  both break (they share the same Accessibility dependency).
- Fixed About overlay centering in `src/overlay.py::_show_about_view` — now
  always centered on `NSScreen.mainScreen().visibleFrame()`, no longer follows
  main window position.
- Added stable code signature step to `build_app.sh`:
  `codesign --force --deep --sign - --identifier net.alexbic.hush`.
- Rebuilt, reinstalled to `/Applications/HUSH.app`, verified
  `Identifier=net.alexbic.hush` via `codesign -dv`.
- Maintainer re-granted Accessibility (removed stale TCC entry, re-added app).
- Verified in `/tmp/vi_debug.log`:
  `[23:58:00] paste: AX trusted=True, firing Cmd+V` (no "ПРОПУСК" skip).
  `[23:58:20] AX trusted: True` / `All 4 hotkey monitors created successfully`.
- Maintainer confirmed live: "Да, сейчас работает." (auto-paste + About + Enter
  during countdown all working).
- Committed (`53c1983`) and pushed to `origin/main`.

## In Progress
- v2.0 release: bump version refs, tag, push, let GitHub Actions build DMG.

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
- Repository review shows no tracked `.github/workflows/*` automation for
  building or publishing `HUSH.app`; current public-distribution evidence in
  the repo points to tagged releases plus README links, while local builds are
  still performed through `build_app.sh`.
- The active runtime paste path is in `src/main.py::_commit_and_paste`, not in
  `src/injector.py`. It currently relies on: hiding the overlay, reactivating
  the previous app, writing clipboard text with `pbcopy`, and firing synthetic
  `Cmd+V` through `pynput`.
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
- Distribution-path review: in progress; repository files show manual local
  build/install instructions and release tags `v1.0`, `v1.1`, but no checked-in
  GitHub Actions workflow for producing release binaries.
- Paste-path review: in progress; inspecting `_commit_and_paste`,
  `_activate_prev_app`, Accessibility checks, and related clipboard timing.
- App build/runtime smoke: not run during bootstrap; defer until failure
  diagnosis unless explicitly requested.
- Recent-log analysis: completed from `/private/tmp/vi_debug.log`,
  `/private/tmp/vi_transcribe.log`, `/private/tmp/vi_recorder.log`,
  `/private/tmp/hush_processor.log`, session-directory metadata, and current
  process state. No code verification was run because no runtime code was
  changed in this analysis step.
- PortAudio recovery source check: `python3 -m compileall -q src` passed after
  changes to `src/main.py` and `src/recorder.py`.
- Intensive PortAudio stress test completed: 10 minutes, 20 cycles, 0 PortAudio hangs,
  14 recorder.start() failures detected during testing

## Next
- Maintainer grants Accessibility to HUSH in System Settings → Privacy &
  Security → Accessibility, then verifies auto-paste on live voice input.
- After paste verification: bump version to 2.0, tag `v2.0`, push, let GitHub
  Actions build the DMG, install, final smoke test, publish release.
- Optional follow-ups (not blocking v2.0): sanitize raw text from
  `/tmp/vi_*` and `/tmp/hush_processor.log`; add dependency manifest.
