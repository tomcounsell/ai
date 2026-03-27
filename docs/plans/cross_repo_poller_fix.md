---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-03-27
tracking: https://github.com/tomcounsell/ai/issues/564
last_comment_id: null
---

# Cross-Repo Issue Poller Fix

## Problem

The issue poller auto-dispatches plan creation for every configured project, but when the issue belongs to a **foreign repo** (any project other than `tomcounsell/ai`), the spawned `claude -p` subprocess runs in the ai repo's working directory with no `GH_REPO` or `SDLC_TARGET_REPO` context. The agent then creates a branch for the foreign issue inside the ai repo, checking out foreign repo commits and wiping the ai repo's files.

**Current behavior:**
1. Issue poller detects a new issue on `tomcounsell/popoto` (or another of the 10 non-ai projects)
2. `dispatch_plan_creation()` spawns `claude -p` with `cwd=_project_root` (hardcoded to ai repo, line 302)
3. No `GH_REPO` or `SDLC_TARGET_REPO` env vars are set
4. Agent creates branch `session/{slug}` inside the ai repo, checking out foreign commits
5. All ai repo files disappear; hooks break, Redis sessions corrupt, dashboard goes blank

**Desired outcome:**
- Foreign-repo issues are planned in their own working directory
- `GH_REPO` and `SDLC_TARGET_REPO` are set so the spawned agent targets the correct repo
- The ai repo is never affected by cross-repo issue dispatch
- The fix is invisible to the single-repo (ai) polling path

## Prior Art

- **PR #396**: "Fix cross-repo gh resolution via GH_REPO env var" — Added `GH_REPO` and `SDLC_TARGET_REPO` injection to the SDK client for Telegram-routed SDLC work. Established the exact pattern this fix replicates. Did not cover the issue poller path since the poller bypasses the SDK client.
- **PR #250**: "Fix /do-build cross-repo dispatch (#249)" — Earlier cross-repo fix for build dispatch; same root cause pattern.
- **Issue #237**: Stale worktrees block branch checkout — addressed worktree cleanup but not cross-repo dispatch validation.

No prior attempts touched `dispatch_plan_creation()` or the issue poller's `cwd` behavior.

## Data Flow

**Current (broken) flow:**
1. **Entry**: `run_polling_cycle()` loads projects from `projects.json` (each has `org`, `repo`, `working_directory`)
2. `poll_project(project)` — passes `org` and `repo` strings but drops `working_directory`
3. `process_issue(org, repo, issue, ...)` — receives no directory context
4. `dispatch_plan_creation(org, repo, issue_number)` — spawns `claude -p` with `cwd=_project_root` (ai repo always)
5. **Output**: Agent branches inside ai repo, catastrophic git state

**Fixed flow:**
1. **Entry**: `run_polling_cycle()` loads projects (unchanged)
2. `poll_project(project)` — forwards `working_directory` (and `org`/`repo`) to `process_issue`
3. `process_issue(org, repo, issue, ..., working_directory)` — passes directory through
4. `dispatch_plan_creation(org, repo, issue_number, ..., working_directory)` — sets:
   - `cwd=working_directory` (target project, not ai repo)
   - `env["GH_REPO"] = f"{org}/{repo}"` for foreign repos
   - `env["SDLC_TARGET_REPO"] = working_directory` for foreign repos
5. **Output**: Agent branches inside the foreign repo's working directory

## Architectural Impact

- **Interface changes**: `dispatch_plan_creation()` and `process_issue()` get a new `working_directory` parameter
- **New dependencies**: None — reuses existing env var patterns already defined in SDK client
- **Coupling**: No new coupling; the fix follows the pattern established in `sdk_client.py`
- **Data ownership**: No change; `working_directory` already lives in the project dict from `load_projects()`
- **Reversibility**: Trivial to revert — isolated to 3 function signatures + subprocess call

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **`dispatch_plan_creation()` cwd fix**: Pass `working_directory` instead of hardcoding `_project_root` as the `cwd`
- **Env var injection**: For foreign repos (where `working_directory != _project_root`), inject `GH_REPO={org}/{repo}` and `SDLC_TARGET_REPO={working_directory}` into the subprocess environment
- **`process_issue()` threading**: Accept `working_directory` param and forward it to `dispatch_plan_creation()`
- **`poll_project()` threading**: Extract `working_directory` from project dict and pass to `process_issue()`

### Technical Approach

- Mirror the pattern in `sdk_client.py` lines 1650-1681 + lines 909-915
- Detect foreign repo by comparing `working_directory` to `_project_root` (same logic as SDK client comparing to `AI_REPO_ROOT`)
- Keep the ai-repo path unchanged: when `working_directory == _project_root`, no new env vars are added
- `os.environ.copy()` as the subprocess env base, then add cross-repo vars on top

### Flow

Issue detected → `poll_project` extracts `working_directory` → `process_issue(working_directory)` → `dispatch_plan_creation(working_directory)` → `claude -p` runs in target dir with `GH_REPO` + `SDLC_TARGET_REPO` set

## Failure Path Test Strategy

### Exception Handling Coverage
- `dispatch_plan_creation()` already has `try/except` around `subprocess.run` — existing tests cover the error path
- The new `working_directory` parameter can be an empty string or missing directory; add guard: if `working_directory` is empty or doesn't exist, fall back to `_project_root` and log a warning

### Empty/Invalid Input Handling
- If `working_directory` is an empty string (old project config without the field), default to `_project_root` — same as current behavior
- If `working_directory` doesn't exist as a path, log a warning and fall back to `_project_root`

### Error State Rendering
- Subprocess failure is already logged at WARNING level — no new silent failure paths added

## Test Impact

- [ ] `tests/test_issue_poller.py::TestProcessIssue::test_plans_new_unique_issue` — UPDATE: assert `dispatch_plan_creation` is called with `working_directory` argument
- [ ] `tests/test_issue_poller.py::TestProcessIssue::test_flags_insufficient_context` — UPDATE: `process_issue` signature change (add `working_directory` with default)
- [ ] `tests/test_issue_poller.py::TestProcessIssue::test_skips_existing_plan` — UPDATE: signature change

New tests to add:
- `test_dispatch_uses_target_working_directory` — verify subprocess `cwd` is `working_directory` for foreign repos
- `test_dispatch_injects_gh_repo_env_for_foreign_repo` — verify `GH_REPO` env var is set for foreign repos
- `test_dispatch_injects_sdlc_target_repo_for_foreign_repo` — verify `SDLC_TARGET_REPO` is set for foreign repos
- `test_dispatch_no_cross_repo_env_for_ai_repo` — verify env vars are NOT set when `working_directory == _project_root`
- `test_dispatch_falls_back_to_project_root_when_working_dir_empty` — safety fallback

## Rabbit Holes

- **Worktree manager repo-ownership validation**: Adding a guard in `worktree_manager.py` to refuse branches for mismatched repos would be defense-in-depth but is a separate concern. Skip for this fix.
- **Rewriting the subprocess dispatch as async**: Not needed; the poller runs on a cron schedule and 5-minute timeouts are acceptable.
- **Validating that `working_directory` is an actual git repo**: Nice-to-have but adds complexity; the existing behavior (subprocess failure logged as warning) already handles this gracefully.

## Risks

### Risk 1: projects.json `working_directory` missing or stale
**Impact:** Empty `working_directory` causes `cwd` to be an empty string, crashing subprocess
**Mitigation:** Guard with fallback to `_project_root` + warning log (explicitly handled above)

### Risk 2: Breaking the ai-repo polling path
**Impact:** Plans for ai-repo issues stop working
**Mitigation:** Only inject cross-repo env vars when `working_directory != _project_root`; add explicit test for the ai-repo case

## Race Conditions

No race conditions identified — all changes are synchronous and single-process.

## No-Gos (Out of Scope)

- Worktree manager cross-repo validation (follow-up issue if needed)
- Recon validator fix for `**Confirmed:**` bucket format (separate issue)
- Any changes to how the poller fetches issues or marks them seen

## Update System

No update system changes required — this feature is purely internal code change to `scripts/issue_poller.py` with no new config files or dependencies.

## Agent Integration

No agent integration required — the issue poller is a standalone cron script; the fix does not add new MCP tools or bridge changes.

## Documentation

- [ ] Update `docs/features/issue-poller.md` (if it exists) or create it, describing the cross-repo dispatch behavior
- [ ] Add note to `CLAUDE.md` System Architecture section that the issue poller now correctly handles cross-repo dispatch

## Success Criteria

- [ ] `dispatch_plan_creation()` runs `claude -p` in the target project's `working_directory`, not the ai repo
- [ ] `GH_REPO` and `SDLC_TARGET_REPO` env vars are set for foreign-repo dispatches
- [ ] Filing an issue on `tomcounsell/popoto` does not affect the ai repo's git state
- [ ] The ai repo's branch remains on `main` after the poller processes a foreign-repo issue
- [ ] Existing ai-repo issue polling continues to work unchanged (no env vars injected)
- [ ] All 5 new tests pass
- [ ] All updated existing tests pass
- [ ] `python -m ruff check scripts/issue_poller.py tests/test_issue_poller.py` clean

## Team Orchestration

### Team Members

- **Builder (poller-fix)**
  - Name: poller-builder
  - Role: Modify `dispatch_plan_creation()`, `process_issue()`, and `poll_project()` in `scripts/issue_poller.py`
  - Agent Type: builder
  - Resume: true

- **Test Engineer (poller-tests)**
  - Name: poller-test-engineer
  - Role: Write the 5 new tests and update the 3 existing tests in `tests/test_issue_poller.py`
  - Agent Type: test-engineer
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Run full test suite and lint; verify no ai-repo pollution
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix `dispatch_plan_creation()` and callers
- **Task ID**: build-poller-fix
- **Depends On**: none
- **Validates**: `tests/test_issue_poller.py`
- **Assigned To**: poller-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `working_directory: str | None = None` parameter to `dispatch_plan_creation()`
- Set `cwd = working_directory or _project_root` in `subprocess.run`
- Build `env = os.environ.copy()`, inject `GH_REPO` and `SDLC_TARGET_REPO` when `working_directory` is set and differs from `_project_root`
- Add `working_directory: str | None = None` parameter to `process_issue()`, forwarded to `dispatch_plan_creation()`
- Update `poll_project()` to extract `working_directory` from project dict and pass to `process_issue()`
- Add fallback guard: if `working_directory` is empty or path doesn't exist, warn and fall back to `_project_root`

### 2. Update and extend tests
- **Task ID**: build-tests
- **Depends On**: build-poller-fix
- **Validates**: `tests/test_issue_poller.py`
- **Assigned To**: poller-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Update 3 existing `TestProcessIssue` tests for new signature (add `working_directory` parameter with default)
- Add `test_dispatch_uses_target_working_directory`
- Add `test_dispatch_injects_gh_repo_env_for_foreign_repo`
- Add `test_dispatch_injects_sdlc_target_repo_for_foreign_repo`
- Add `test_dispatch_no_cross_repo_env_for_ai_repo`
- Add `test_dispatch_falls_back_to_project_root_when_working_dir_empty`

### 3. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/test_issue_poller.py -v`
- Run `python -m ruff check scripts/issue_poller.py tests/test_issue_poller.py`
- Run `python -m ruff format --check scripts/issue_poller.py tests/test_issue_poller.py`
- Verify no ai-repo git state changes

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Issue poller tests pass | `pytest tests/test_issue_poller.py -v` | exit code 0 |
| Lint clean | `python -m ruff check scripts/issue_poller.py tests/test_issue_poller.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/issue_poller.py tests/test_issue_poller.py` | exit code 0 |
| No new cross-repo env in ai path | `pytest tests/test_issue_poller.py -k "no_cross_repo_env"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — the solution approach is clear and mirrors an existing working pattern in the SDK client.
