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

### Phase 2: PortAudio Recovery and Multi-Monitor Support
**Goal:** Implement PortAudio hang recovery and automatic window relocation.  
**Date:** 2026-07-23  
**DoD:**
- [x] PortAudio hang recovery mechanism implemented with timeout handling.
- [x] Automatic window relocation to target app screen implemented.
- [x] Screen configuration change notification handler added.
- [x] Multi-monitor functionality tested and working in all modes.
- [x] App rebuilt and installed with new features.

#### Tasks
- [x] @desktop: Implement PortAudio hang recovery — DoD: added timeout handling
      and recovery mechanism in recorder.py and main.py.
- [x] @desktop: Implement multi-monitor window relocation — DoD: added automatic
      window relocation functions and screen change notification handler.

### Phase 3: GitHub Distribution Preparation
**Goal:** Prepare for automated app building and GitHub distribution.  
**ETA:** Current session.  
**DoD:**
- [ ] GitHub Actions workflow for automated app building created.
- [ ] Documentation updated for users downloading releases.
- [ ] Version bumped to 1.3.0 with changelog.
- [ ] All project state files updated with current status.

#### Tasks
- [ ] @build: Create GitHub Actions workflow — DoD: automated build script for
      macOS app bundle creation and release.
- [ ] @docs: Update user documentation — DoD: installation and usage instructions
      for GitHub releases.
- [ ] @desktop: Bump version to 1.3.0 — DoD: update version references and
      changelog with completed features.
- [ ] @build: Commit and push all changes — DoD: all new features and
      documentation committed with proper version bump.
