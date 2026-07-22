# Plan: HUSH

> Decomposition of current project work. Keep this file synchronized as tasks
> move from review to implementation.

## Phases

### Phase 1: Project State Bootstrap
**Goal:** Review the repository and create the shared project-state structure.  
**ETA:** Complete in current session.  
**DoD:**
- [x] Inspect `ai-team` templates.
- [x] Inspect configured LightRAG and LightRAG MCP Connect examples.
- [x] Review HUSH repository layout, runtime flow, build script, and key risks.
- [x] Create `AGENTS.md`, `PROJECT.md`, `SPEC.md`, `PLAN.md`, `ROADMAP.md`,
      `BACKLOG.md`, `STATUS.md`, and `TECH_STACK.md`.

#### Tasks
- [x] @docs: Bootstrap project state files — DoD: files exist with
      repository-specific content and current review findings.

### Phase 2: Diagnose Current App Failure
**Goal:** Identify why the app currently does not work, without broad rewrites.  
**ETA:** Pending user-provided symptom and local verification.  
**DoD:**
- [ ] Failure mode is reproduced or narrowed to a concrete code/runtime path.
- [ ] Root cause is documented in `STATUS.md`.
- [ ] Minimal fix is implemented.
- [ ] Relevant verification from `TECH_STACK.md` passes or is explicitly blocked.

#### Tasks
- [ ] @desktop: Diagnose current HUSH runtime failure — DoD: reproduce or
      isolate the failure, patch the smallest responsible area, and verify.

### Phase 3: Verification and Packaging Hygiene
**Goal:** Improve confidence in local development and build behavior.  
**ETA:** After Phase 2.  
**DoD:**
- [ ] Compile check and build check are documented.
- [ ] Missing dependency or packaging assumptions are captured.
- [ ] Any new dependency policy is reflected in `TECH_STACK.md`.

#### Tasks
- [ ] @qa: Define repeatable local smoke checks — DoD: documented checks cover
      Python compilation, app packaging, and manual runtime smoke.
- [ ] @build: Review dependency manifest need — DoD: decide whether to add a
      tracked manifest and update docs accordingly.
