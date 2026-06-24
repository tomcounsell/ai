---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-06-24
tracking: https://github.com/tomcounsell/ai/issues/1346
last_comment_id:
---

# Remove pm-briefings re-export shim

## Problem

PR #1340 ("Finish pm-briefings cutover", merged 2026-05-08) renamed the reflection
package from `reflections/pm_audio_briefing/` to `reflections/pm_briefings/`. Because
the vault `reflections.yaml` (iCloud-synced) and the code rename propagate to bridge
machines on independent schedules, PR #1340 added a transient single-file re-export
shim at `reflections/pm_audio_briefing.py` so the registry callable
`reflections.pm_audio_briefing.run` would still resolve during the mixed-state deploy
window. A guard test pinned the shim so it could not silently rot.

**Current behavior:**
- `reflections/pm_audio_briefing.py` exists purely as a `from reflections.pm_briefings import *` re-export shim — dead code now that every machine has the rename and the vault edit propagated (~47 days elapsed).
- `tests/unit/reflections/test_pm_audio_briefing_reexport_shim.py` exists solely to guard the shim.
- The vault `reflections.yaml` already points the `pm-briefings` registry entry at the canonical `callable: "reflections.pm_briefings.run"` — nothing in production resolves the old path anymore.

**Desired outcome:**
- The shim file and its guard test are deleted.
- All imports/registry callables reference `reflections.pm_briefings` (already true).
- Test suite, lint, and format remain green.

## Freshness Check

**Baseline commit:** 95023a72887a42451155c653f46fdb8cce5945d5
**Issue filed at:** 2026-05-08T17:59:00Z
**Disposition:** Unchanged (with one clarification — see Notes)

**File:line references re-verified:**
- `reflections/pm_audio_briefing.py` — issue claims it is the transient shim — **still holds**, git-tracked, 23-line re-export of `reflections.pm_briefings`.
- `tests/unit/reflections/test_pm_audio_briefing_reexport_shim.py` — issue claims it is the guard test — **still holds**, git-tracked, 3 tests asserting identity-equal re-export.
- `reflections/pm_briefings/__init__.py` — canonical target — **present**; `import reflections.pm_briefings; callable(run) == True` confirmed. (Note: the canonical target is now a *package* directory, not the flat `pm_briefings.py` file the issue title implies.)

**Cited sibling issues/PRs re-checked:**
- PR #1340 — **MERGED 2026-05-08T17:57:48Z**. The ≥1-day deploy-window precondition is satisfied by ~47 days.
- #1306 ("Finish pm-briefings cutover...") — **closed 2026-05-08** by PR #1340 (the cutover this shim was created during).
- #1292 ("Cut over from legacy reflections to pm-briefings dispatcher") — **closed 2026-05-06**, predecessor cutover. No bearing on the cleanup.

**Commits on main since issue was filed (touching referenced files):**
- `cd98272f` "refactor(reflections): one file per reflection under reflections/{group}/" — reorganized `reflections/`; did NOT touch the shim or its guard test (both unchanged since May 9). Irrelevant to this cleanup.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** The vault `~/Desktop/Valor/reflections.yaml` (`config/reflections.yaml` is a symlink to it) already uses `callable: "reflections.pm_briefings.run"` (line 384). No live code or config references `reflections.pm_audio_briefing` — the only non-historical reference in the repo is the guard test itself. This confirms the shim is fully discharged and safe to delete. One clarification on the issue's task list: "grep production logs for ImportError on the shim" is moot — the vault already resolves the canonical path everywhere, so the shim is unreferenced dead code, not a live fallback.

## Prior Art

- **Issue #1306 / PR #1340**: "Finish pm-briefings cutover" — performed the package rename and *introduced* this shim as the deploy-window guard. Its own comments scoped the removal as a deliberate follow-up after the propagation window — that follow-up is this issue.
- **Issue #1292**: "Cut over from legacy reflections to pm-briefings dispatcher" — earlier cutover step; established the dispatcher the package feeds. No code overlap with this cleanup.

## Data Flow

The reflection scheduler resolves the registry `callable` string via `importlib.import_module` + `getattr` (`agent/reflection_scheduler.py::_resolve_callable`). The `pm-briefings` entry in `reflections.yaml` resolves `reflections.pm_briefings.run` directly. The shim sits entirely off this live path — it was only reachable when a stale vault entry still named `reflections.pm_audio_briefing.run`, which no longer occurs. Deleting the shim removes an unreachable module; no live data flow changes.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a delete-two-files chore with all preconditions externally verified at plan time. The only communication overhead is a single review round on the deletion PR.

## Prerequisites

No prerequisites — this work has no external dependencies. The deploy-window precondition (≥1 day post-#1340-merge) is satisfied by ~47 days and was verified during the Freshness Check.

## Solution

### Key Elements

- **Delete the shim**: remove `reflections/pm_audio_briefing.py`.
- **Delete the guard test**: remove `tests/unit/reflections/test_pm_audio_briefing_reexport_shim.py` (it tests only the shim; with the shim gone it would import-error).
- **Verify no live references remain**: confirm nothing outside historical plan docs references `reflections.pm_audio_briefing`.

### Flow

Current state (shim present, unreferenced) → delete shim + guard test → grep confirms zero live references to `pm_audio_briefing` → test/lint/format green → PR merged

### Technical Approach

- `git rm reflections/pm_audio_briefing.py tests/unit/reflections/test_pm_audio_briefing_reexport_shim.py`.
- Run the reflections unit tests plus a repo-wide grep to confirm the canonical path (`reflections.pm_briefings`) is the only one in use.
- No code edits to `reflections/pm_briefings/` — it is already the sole canonical module and the vault registry already targets it.
- The local untracked `reflections/pm_audio_briefing/` directory (pycache-only, not in git) is out of scope for the PR; optional local `rm -rf` is a housekeeping nicety, not a deliverable.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope. This change only deletes a re-export module and its guard test; it adds no new code paths.

### Empty/Invalid Input Handling
- Not applicable — no functions are added or modified.

### Error State Rendering
- No user-visible output. The only observable post-condition is that `import reflections.pm_audio_briefing` now raises `ModuleNotFoundError`, which is the intended end state (the module is gone). Verified by the Verification table's import-failure check.

## Test Impact

- [ ] `tests/unit/reflections/test_pm_audio_briefing_reexport_shim.py` (all 3 tests) — DELETE: these tests guard the shim's existence and identity-equal re-export. With the shim removed they would `ModuleNotFoundError` on import; the entire file is removed alongside the shim.

No other existing tests reference `pm_audio_briefing` (verified by `grep -rln pm_audio_briefing tests/` returning only the guard test).

## Rabbit Holes

- **Do not** refactor or touch `reflections/pm_briefings/` internals — the cutover is complete and stable; this chore is deletion only.
- **Do not** chase a "ImportError in production logs" investigation — the vault already migrated to the canonical path, so there is no live fallback to log against. The grep-and-delete is sufficient evidence.
- **Do not** attempt to clean stale untracked artifacts on other machines via this PR — local pycache directories are not version-controlled and propagate naturally via `/update`.

## Risks

### Risk 1: A bridge machine still has a stale vault entry naming the old callable path
**Impact:** That machine's `pm-briefings` reflection would `ImportError` on resolution after the shim is gone.
**Mitigation:** The vault `reflections.yaml` is a single iCloud-synced file (`config/reflections.yaml` symlinks to it on every machine), and it already reads `callable: "reflections.pm_briefings.run"`. There is no per-machine divergence in the callable path. The ~47-day window since #1340 far exceeds the ≥1-day propagation requirement.

### Risk 2: Some unscanned code path imports the shim
**Impact:** Deletion would break that import.
**Mitigation:** Repo-wide grep (`grep -rn pm_audio_briefing --include=*.py --include=*.yaml --include=*.json`) confirms the only non-historical reference is the guard test being deleted. The Verification table re-runs this grep as a gate.

## Race Conditions

No race conditions identified — this change deletes two static files and runs no concurrent or async operations. The propagation race the shim originally guarded has fully settled (deploy window elapsed; verified at plan time).

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1306] The package rename and dispatcher cutover itself — already completed and closed by PR #1340; this plan only removes the leftover deploy-window shim.

Nothing else deferred — the shim deletion, guard-test deletion, and reference verification are all in scope and completable within this plan.

## Update System

No update system changes required — this chore removes a dead module that the `/update` flow already stopped relying on once the vault propagated. The deletion propagates to every machine through the normal `git pull` step of `/update`; no script or skill changes needed.

## Agent Integration

No agent integration required — this is an internal reflection-module cleanup. The shim was never an agent-facing tool, CLI entry point, or bridge import; it was only a registry-callable target reachable by the reflection scheduler, which already resolves the canonical `reflections.pm_briefings.run` path.

## Documentation

No documentation changes needed. The shim is undocumented transient deploy-window scaffolding — no `docs/features/` page describes it, and `docs/features/README.md` has no entry for it. The pm-briefings feature documentation (if any) already describes the canonical `reflections.pm_briefings` package and is unaffected by removing the dead alias.

### Inline Documentation
- No inline doc changes — the deleted files take their own docstrings with them.

## Success Criteria

- [ ] `reflections/pm_audio_briefing.py` no longer exists (`git rm`'d).
- [ ] `tests/unit/reflections/test_pm_audio_briefing_reexport_shim.py` no longer exists (`git rm`'d).
- [ ] Repo-wide grep finds zero live (non-plan-doc, non-pycache) references to `pm_audio_briefing`.
- [ ] `import reflections.pm_briefings` still resolves and `run` is callable.
- [ ] Tests pass (`/do-test`).
- [ ] Lint and format clean.

## Team Orchestration

Single-builder chore. No validator pairing needed beyond the lead's own verification; the Verification table provides machine-checkable gates that `/do-build` runs automatically.

### Team Members

- **Builder (shim-removal)**
  - Name: shim-remover
  - Role: Delete the shim file and guard test, verify no live references remain, confirm canonical import resolves.
  - Agent Type: builder
  - Resume: true

## Step by Step Tasks

### 1. Remove shim and guard test
- **Task ID**: build-remove-shim
- **Depends On**: none
- **Validates**: full unit suite (`pytest tests/unit/reflections/ -q`), and absence of `tests/unit/reflections/test_pm_audio_briefing_reexport_shim.py`
- **Assigned To**: shim-remover
- **Agent Type**: builder
- **Parallel**: false
- `git rm reflections/pm_audio_briefing.py`
- `git rm tests/unit/reflections/test_pm_audio_briefing_reexport_shim.py`
- Run `grep -rn "pm_audio_briefing" --include="*.py" --include="*.yaml" --include="*.json" .` and confirm the only matches are historical plan docs under `docs/plans/` (zero live code/config references).
- Confirm `python -c "import reflections.pm_briefings as c; assert callable(c.run)"` succeeds.
- Run `pytest tests/unit/reflections/ -q`, `python -m ruff check .`, `python -m ruff format --check .`.

### 2. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-remove-shim
- **Assigned To**: shim-remover (self-verify) or lead
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification-table commands.
- Confirm all Success Criteria met.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Shim deleted | `test ! -e reflections/pm_audio_briefing.py` | exit code 0 |
| Guard test deleted | `test ! -e tests/unit/reflections/test_pm_audio_briefing_reexport_shim.py` | exit code 0 |
| Canonical import resolves | `python -c "import reflections.pm_briefings as c; assert callable(c.run)"` | exit code 0 |
| Shim no longer importable | `python -c "import reflections.pm_audio_briefing" 2>&1` | output contains ModuleNotFoundError |
| No live code refs to old path | `grep -rn "pm_audio_briefing" --include="*.py" --include="*.yaml" --include="*.json" . \| grep -v "docs/plans/" \| grep -v "__pycache__"` | exit code 1 |
| Reflections unit tests pass | `pytest tests/unit/reflections/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — all preconditions (deploy window elapsed, vault on canonical path, zero live references) were verified at plan time. This is a clean delete-two-files chore.
