# Agent Instructions

This macOS desktop application repository is operated by AI agents and humans.
Minimize rediscovery: read the project state files before changing code,
runtime assumptions, packaging, or user-data behavior.

## Required Reading Order
1. `PROJECT.md` — identity, goal, and layout.
2. `STATUS.md` — current state, review findings, and next action.
3. `SPEC.md` — source of truth for expected behavior.
4. `PLAN.md` — current implementation phases.
5. `TECH_STACK.md` — tools, stack, commands, and runtime assumptions.
6. `BACKLOG.md` — task list.
7. `ROADMAP.md` — milestone context.

Read only the implementation files needed after this orientation.

## Privacy and Secret Handling
- HUSH stores user configuration in `~/.config/hush/`.
- Never commit real API keys, provider tokens, passwords, private keys, or
  personal history/transcription data.
- Never print raw provider configuration, user dictation history, or full
  contents of local config files in handoffs.
- Use variable names and placeholders when documenting configuration.
- Treat `/tmp/hush_*`, `/tmp/vi_*`, and clipboard contents as potentially
  private runtime data.

## New Repository Bootstrap
When starting work in another repository, check whether it has this project-state structure:

- `AGENTS.md`
- `PROJECT.md`
- `SPEC.md`
- `PLAN.md`
- `ROADMAP.md`
- `BACKLOG.md`
- `STATUS.md`
- `TECH_STACK.md`

If the structure is missing, review the repository first, create the files with
repository-appropriate content, and then continue implementation work using
those files as the shared operating context.

## Task State Protocol
Before starting any non-trivial repository work, make the task recoverable:

1. Add or update a `BACKLOG.md` item with:
   - owner lane such as `@backend`, `@desktop`, `@qa`, `@docs`, or `@build`;
   - short task title;
   - clear Definition of Done.
2. Move the item to `In Progress` when implementation starts.
3. Update `STATUS.md` with:
   - what is being worked on;
   - current assumptions;
   - known blockers or risks;
   - next action if the session stops.

## Task Sizing and Decomposition
Before implementation, assess task size and decompose when needed:

- If the task can be completed and verified in one focused pass, keep it as one
  `BACKLOG.md` item.
- If the task has multiple components, uncertain dependencies, macOS permission
  risk, packaging risk, or could overflow the context window, split it into
  independently testable items.
- Each decomposed item must have its own Definition of Done and verification path.
- Prefer a sequence where each completed item leaves the repository in a
  working, reviewable state.
- Keep the parent goal visible in `PLAN.md` or `STATUS.md`.
- Do not start broad implementation until the decomposition is written down.

## Skills and Tools Check
Before specialized work, check whether an applicable skill, tool, or repository
script already exists.

- Prefer repository scripts and documented commands over ad-hoc commands.
- For macOS UI/runtime checks, inspect code first, then use the smallest safe
  local verification path.
- Do not use tools as a shortcut around privacy, secret-handling, or project
  state updates.

## Fix and Verification Loop
When implementation or verification fails, do not stop at the first failure
unless the next step would require new authority or unsafe action.

1. Classify the failure:
   - code/config defect;
   - missing local dependency or model artifact;
   - macOS permission/runtime state problem;
   - sandbox/tooling problem;
   - unclear requirement or external blocker.
2. If it is a code/config defect, make the smallest focused fix and rerun the
   relevant check.
3. If it is an environment/tooling problem, try a safe documented workaround.
4. Repeat `fix -> verify -> record result` until:
   - all relevant checks pass;
   - the same blocker remains after reasonable attempts;
   - continuing would require secrets, destructive action, production/user-data
     mutation, or user approval.
5. Record meaningful loop results in `STATUS.md` or `BACKLOG.md`.

Never mark a task complete while required verification is still blocked unless
the user explicitly waives that verification.

## Decision Gate
When a task is blocked by product, security, architecture, packaging, or
macOS-permission direction, do not choose silently if the change could affect
user data, privacy expectations, or core input behavior.

1. Stop before irreversible work such as deleting user config/history, changing
   provider secret handling, changing app bundle identity, or replacing the
   transcription/runtime model path.
2. Record the decision point in `STATUS.md` and move or add a `BACKLOG.md` item
   to `Review`.
3. Present 2-3 concrete options with risks and rollback implications.
4. If the maintainer gives a clear decision, record it, then continue from the
   last safe checkpoint.

## Keep Project Files Current
Any non-trivial change must update the project state files in the same work session.

- If behavior, scope, architecture, or acceptance criteria change, update `SPEC.md`.
- If implementation steps, sequencing, or phase ownership change, update `PLAN.md`.
- If milestone status changes, update `ROADMAP.md`.
- If tasks are added, started, blocked, reviewed, or completed, update `BACKLOG.md`.
- If current status, verification, blockers, or next actions change, update `STATUS.md`.
- If dependencies, tools, commands, env vars, packaging, or runtime versions
  change, update `TECH_STACK.md`.
- If repository purpose, layout, ownership, or operating model changes, update
  `PROJECT.md`.
- If agent workflow rules change, update this `AGENTS.md`.

## Verification
Before claiming code or packaging changes are done, run the relevant checks from
`TECH_STACK.md` or explain why they were not run.
