---
status: Planning
type: chore
appetite: Small
owner: Valor
created: 2026-03-23
tracking: https://github.com/tomcounsell/ai/issues/474
last_comment_id:
---

# PM Persona: Finish Dev-Session Dispatch and Cleanup

## Problem

The PM persona refactor shipped most of its work directly on main, but left behind dead playlist code and stale references. Items #2 (routing fix) and #3 (dev-session dispatch injection) already shipped in commits `166fb0a0` and `38aee0f4` respectively, so the remaining work is cleanup.

**Current behavior:**
- ~182 lines of dead playlist code in `tools/job_scheduler.py` (functions, CLI subcommands, Redis key constants)
- PM persona file references playlist commands that no longer work
- `docs/features/README.md` lists playlist as "Shipped" instead of "Deprecated"
- Stale playlist comments in `agent/job_queue.py`
- Test file `tests/unit/test_job_scheduler_persona.py` tests playlist persona gates for dead code

**Desired outcome:**
- All playlist code, references, and tests removed
- Documentation reflects deprecation
- Codebase has zero references to the removed feature

## Prior Art

- **Issue #450**: Original playlist feature (shipped in PR #456) — introduced the code being removed
- **Issue #459**: SDLC Redesign — deprecated playlist, removed Observer hook, but left dead code behind
- **PR #464**: SDLC Redesign implementation — removed Observer hooks but not the playlist functions themselves

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

Pure dead code removal — no design decisions needed.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Dead code removal**: Delete all playlist functions, constants, and CLI subcommands from `job_scheduler.py`
- **Reference cleanup**: Remove playlist mentions from PM persona, job_queue.py comments, and feature index
- **Test cleanup**: Remove playlist-specific persona tests, keep schedule-related tests

### Technical Approach

- Delete lines 160-368 in `tools/job_scheduler.py` (playlist constants, `_get_redis()`, helper functions, `cmd_playlist`, `cmd_playlist_status`). Note: `_get_redis()` is only called by playlist functions and should be deleted with them.
- Remove `"playlist"` from `PERSONA_RESTRICTED_ACTIONS` dict
- Remove `playlist` and `playlist-status` from argparse subparsers and command dispatch dict
- Remove playlist references from `~/Desktop/Valor/personas/project-manager.md` (lines 97, 167, 169, 173)
- Remove playlist-specific tests from `tests/unit/test_job_scheduler_persona.py`
- Update stale comments in `agent/job_queue.py` (lines 468-470)
- Update `docs/features/README.md` to mark playlist as "Deprecated" instead of "Shipped"

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope — this is pure deletion

### Empty/Invalid Input Handling
- Not applicable — removing code, not adding

### Error State Rendering
- Not applicable

## Test Impact

- [ ] `tests/unit/test_job_scheduler_persona.py::test_developer_can_playlist` — DELETE: tests removed feature
- [ ] `tests/unit/test_job_scheduler_persona.py::test_project_manager_can_playlist` — DELETE: tests removed feature
- [ ] `tests/unit/test_job_scheduler_persona.py::test_teammate_blocked_from_playlist` — DELETE: tests removed feature

Remaining tests in `test_job_scheduler_persona.py` (schedule-related) should be kept and must still pass.

## Rabbit Holes

- Don't refactor other shared utilities (e.g., `_get_project_key()`) — `_get_redis()` itself is playlist-only and should be deleted
- Don't touch the pipeline state machine integration (item #4) — that's a separate issue
- Don't verify dev-session dispatch end-to-end (item #3) — needs live Telegram, not code changes

## Risks

### Risk 1: Accidentally removing shared code
**Impact:** Job scheduler breaks for schedule/status/push/bump/pop/cancel commands
**Mitigation:** Only delete clearly playlist-scoped functions. Run existing tests after removal to verify.

## Race Conditions

No race conditions identified — all operations are synchronous deletions of dead code.

## No-Gos (Out of Scope)

- Pipeline state machine integration (issue body item #4 — separate issue)
- Live verification of dev-session dispatch (item #3 — manual testing, not code changes)
- Routing fix verification (item #2 — already shipped and tested)
- Refactoring job_scheduler.py beyond playlist removal

## Update System

No update system changes required — this removes dead code that was never invoked in production.

## Agent Integration

No agent integration required — the playlist CLI was registered as an MCP tool but never actually used by the agent. Removing it has no functional impact.

## Documentation

- [ ] Update `docs/features/README.md` — change playlist status from "Shipped" to "Deprecated"
- [ ] Verify `docs/features/sdlc-job-playlist.md` deprecation notice is sufficient (no changes expected)

## Success Criteria

- [ ] Zero grep hits for "playlist" in `tools/job_scheduler.py`
- [ ] Zero grep hits for "playlist" in `~/Desktop/Valor/personas/project-manager.md`
- [ ] Zero grep hits for "playlist" in `agent/job_queue.py`
- [ ] `pytest tests/unit/test_job_scheduler_persona.py -x -q` passes (schedule tests intact)
- [ ] `python -m ruff check tools/job_scheduler.py` passes
- [ ] `docs/features/README.md` shows playlist as "Deprecated"
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Remove all playlist dead code and references
  - Agent Type: builder
  - Resume: true

- **Validator (cleanup)**
  - Name: cleanup-validator
  - Role: Verify zero playlist references and tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Remove playlist code from job_scheduler.py
- **Task ID**: build-playlist-removal
- **Depends On**: none
- **Validates**: tests/unit/test_job_scheduler_persona.py
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete playlist constants (`PLAYLIST_KEY_PREFIX`, `PLAYLIST_RETRIES_KEY_PREFIX`)
- Delete playlist helper functions (`_playlist_key`, `_retries_key`)
- Delete playlist operations (`playlist_push`, `playlist_pop`, `playlist_status`, `playlist_requeue`, `playlist_clear`)
- Delete CLI handlers (`cmd_playlist`, `cmd_playlist_status`)
- Remove `"playlist"` from `PERSONA_RESTRICTED_ACTIONS`
- Remove playlist argparse subparsers and dispatch entries
- Delete playlist-specific tests from `test_job_scheduler_persona.py`
- Remove stale playlist comments from `agent/job_queue.py` (lines 468-470)

### 2. Clean up external references
- **Task ID**: build-reference-cleanup
- **Depends On**: none
- **Validates**: grep confirms zero playlist references
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove playlist references from `~/Desktop/Valor/personas/project-manager.md`
- Update `docs/features/README.md` playlist status to "Deprecated"

### 3. Validation
- **Task ID**: validate-all
- **Depends On**: build-playlist-removal, build-reference-cleanup
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_job_scheduler_persona.py -x -q`
- Run `python -m ruff check tools/job_scheduler.py`
- Grep for "playlist" across codebase to verify zero references in modified files
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No playlist in scheduler | `grep -c playlist tools/job_scheduler.py` | exit code 1 |
| No playlist in job_queue | `grep -c playlist agent/job_queue.py` | exit code 1 |
| Persona tests pass | `pytest tests/unit/test_job_scheduler_persona.py -x -q` | exit code 0 |
