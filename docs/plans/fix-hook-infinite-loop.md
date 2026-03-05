---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-06
tracking: https://github.com/yudame/valor-agent/issues/261
---

# Stop Hook Infinite Loop When Code Edited on Main Then Moved to Branch

## Problem

The SDLC stop hook creates an unrecoverable infinite loop when code is edited on `main` during a session, then moved to a feature branch. Three bugs compound into the loop:

1. `modified_on_branch` is write-once and permanently records `main` if the first edit happens before `git checkout -b session/...`
2. `_check_no_direct_main_push` has no escape hatch (unlike project-level hooks which support `SKIP_SDLC=1`)
3. No state update occurs when the branch changes via `git checkout -b` or when a PR merge clears the `modified_on_branch` field

**Current behavior:**
Agent edits code on `main`, SDLC state records `modified_on_branch: "main"`. Agent creates a feature branch, commits, opens a PR, and merges. When the session tries to complete, the stop hook sees `code_modified: true` + `modified_on_branch: "main"` and hard-blocks. The agent responds, the hook fires again, creating an infinite loop that burns context for 8+ rounds until manually killed.

**Desired outcome:**
The stop hook correctly recognizes when code has been moved off `main` onto a feature branch (or merged via PR), and does not falsely trigger a violation. When genuinely stuck, the `SKIP_SDLC` escape hatch allows recovery.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Solo dev work is fast -- the bottleneck is alignment and review. Appetite measures communication overhead, not coding time.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Branch switch detection**: Update `modified_on_branch` when `git checkout -b session/*` is detected in bash commands
- **SKIP_SDLC escape hatch**: Add `SKIP_SDLC=1` support to the SDK stop hook, matching project-level hooks
- **Live git diff verification**: Before reporting a violation, verify files actually have uncommitted changes on `main`
- **Full merge cleanup**: Clear both `code_modified` and `modified_on_branch` on `gh pr merge`

### Flow

**Code edited on main** -> Agent runs `git checkout -b session/slug` -> `modified_on_branch` updated to `session/slug` -> Stop hook sees `session/*` -> **No violation**

**Code edited on main, no branch switch** -> Stop hook checks live git diff -> No uncommitted changes on main -> **No violation (stale state)**

**Genuine violation** -> Code actually uncommitted on main -> **Hard-block with remediation instructions**

### Technical Approach

#### Fix 1: Update `modified_on_branch` on branch switch

In `update_sdlc_state_for_bash()` in `.claude/hooks/post_tool_use.py`, detect `git checkout -b session/*` and `git switch -c session/*` commands. When detected and `code_modified` is `true`, update `modified_on_branch` to the new branch name.

```python
# In update_sdlc_state_for_bash(), before the early return
branch_match = re.search(
    r'\bgit\s+(?:checkout\s+-b|switch\s+-c)\s+(session/\S+)', command
)
if branch_match:
    session_id = hook_input.get("session_id", "unknown")
    state_path = get_sdlc_state_path(session_id)
    if state_path.exists():
        state = load_sdlc_state(session_id)
        if state.get("code_modified"):
            state["modified_on_branch"] = branch_match.group(1)
            save_sdlc_state(session_id, state)
    return  # Branch switch is not a quality command or merge
```

#### Fix 2: Add SKIP_SDLC escape hatch to SDK stop hook

In `_check_no_direct_main_push()` in `agent/sdk_client.py`, add early return when `SKIP_SDLC=1`:

```python
if os.environ.get("SKIP_SDLC") == "1":
    logger.warning(
        f"[sdlc-main-check] SKIP_SDLC=1 -- bypassing main branch check for {session_id}"
    )
    return None
```

#### Fix 3: Verify against live git state

In `_check_no_direct_main_push()`, before reporting a violation, check whether the listed files actually have uncommitted changes on `main`:

```python
# Cross-check: are there actually uncommitted code changes on main?
diff_result = subprocess.run(
    ["git", "diff", "--name-only"], capture_output=True, text=True,
    cwd=str(repo_root), timeout=5,
)
staged_result = subprocess.run(
    ["git", "diff", "--name-only", "--cached"], capture_output=True, text=True,
    cwd=str(repo_root), timeout=5,
)
all_changed = set(diff_result.stdout.strip().split("\n") + staged_result.stdout.strip().split("\n"))
all_changed.discard("")
if not any(is_code_file(f) for f in all_changed):
    logger.info(
        f"[sdlc-main-check] State says code modified on main but no actual uncommitted "
        f"code changes found -- stale state, no violation."
    )
    return None
```

This requires importing `is_code_file` from the hooks module or inlining the check.

#### Fix 4: Clear `modified_on_branch` on `gh pr merge`

In `update_sdlc_state_for_bash()`, the existing merge detection already clears `code_modified`. Extend it to also clear `modified_on_branch`:

```python
if is_merge:
    state["code_modified"] = False
    state.pop("modified_on_branch", None)  # Clear stale branch tracking
```

## Rabbit Holes

- Parsing git reflog to reconstruct full branch history -- too complex, branch switch detection in bash commands is sufficient
- Making the stop hook advisory instead of blocking -- the hard-block is correct behavior; the issue is false positives, not the enforcement model
- Adding complex state machine for branch lifecycle -- the four targeted fixes address all known failure modes without architectural changes

## Risks

### Risk 1: Branch switch regex misses edge cases
**Impact:** `modified_on_branch` not updated for unusual `git checkout` invocations (e.g., with `--` separator or quoted branch names)
**Mitigation:** The regex covers the standard patterns used by the agent. Fix 3 (live git diff) provides a second safety net for any missed patterns.

### Risk 2: `is_code_file` import in `sdk_client.py`
**Impact:** Importing from `.claude/hooks/post_tool_use.py` into `agent/sdk_client.py` creates a cross-layer dependency
**Mitigation:** Inline the code file extension check in `sdk_client.py` (it is a 3-line function). Keep both copies in sync via a comment referencing the canonical version.

### Risk 3: Live git diff check adds subprocess overhead to stop hook
**Impact:** Slight latency increase on session completion
**Mitigation:** The subprocess only runs in the violation path (code_modified=true + on main + not session/* branch). Normal sessions (on feature branches or docs-only) skip it entirely.

## No-Gos (Out of Scope)

- Refactoring the entire SDLC state model or schema
- Changing the stop hook from blocking to advisory
- Adding UI/notification for SDLC violations (current stderr logging is sufficient)
- Reworking session state persistence to a database

## Update System

No update system changes required -- these fixes modify hook behavior that is already deployed via the standard update process. The `SKIP_SDLC` env var is a local override, not a config file change.

## Agent Integration

No agent integration required -- this is a hook-internal change. The hooks already fire automatically on tool use and session stop events. The `SKIP_SDLC` env var can be set by the operator when needed for recovery.

## Documentation

- [ ] Update `docs/features/sdlc-enforcement.md` -- add `modified_on_branch` update-on-branch-switch behavior, document `SKIP_SDLC=1` escape hatch, add troubleshooting entry for the infinite loop scenario
- [ ] Add entry to `docs/features/README.md` index table if not already present

### Inline Documentation
- [ ] Update docstrings for `_check_no_direct_main_push()` and `update_sdlc_state_for_bash()`
- [ ] Code comments on the live git diff verification logic

## Success Criteria

- [ ] Branch switch (`git checkout -b session/*`) updates `modified_on_branch` in `sdlc_state.json`
- [ ] `SKIP_SDLC=1` bypasses the SDK stop hook main branch check
- [ ] Stop hook verifies live git diff before reporting violation (stale state = no violation)
- [ ] `gh pr merge` clears both `code_modified` and `modified_on_branch`
- [ ] Existing unit tests in `test_sdk_client_sdlc.py` and `test_post_tool_use_sdlc.py` still pass
- [ ] New unit tests cover: branch switch detection, SKIP_SDLC bypass, live git diff check, merge cleanup of `modified_on_branch`
- [ ] Direct code push to main still triggers violation (true positive preserved)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (hook-fix)**
  - Name: hook-builder
  - Role: Implement all four fixes across `post_tool_use.py` and `sdk_client.py`
  - Agent Type: builder
  - Resume: true

- **Validator (hook-fix)**
  - Name: hook-validator
  - Role: Verify fix correctness, edge cases, and no regressions
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add branch switch detection to post_tool_use.py
- **Task ID**: build-branch-switch
- **Depends On**: none
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- In `update_sdlc_state_for_bash()`, detect `git checkout -b session/*` and `git switch -c session/*` commands
- When detected and `code_modified` is true, update `modified_on_branch` to the new branch name
- Handle early return correctly so the change does not interfere with quality command or merge detection

### 2. Clear `modified_on_branch` on `gh pr merge`
- **Task ID**: build-merge-cleanup
- **Depends On**: none
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- In `update_sdlc_state_for_bash()`, extend the existing merge detection to also clear `modified_on_branch`
- Single line: `state.pop("modified_on_branch", None)`

### 3. Add SKIP_SDLC escape hatch to `_check_no_direct_main_push`
- **Task ID**: build-skip-sdlc
- **Depends On**: none
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `os.environ.get("SKIP_SDLC") == "1"` check at the top of `_check_no_direct_main_push()`
- Log a warning when bypassed
- Update the function docstring to document the escape hatch

### 4. Add live git diff verification to `_check_no_direct_main_push`
- **Task ID**: build-live-diff
- **Depends On**: build-skip-sdlc
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Before returning a violation, check `git diff --name-only` and `git diff --name-only --cached`
- If no code files have uncommitted changes, return None (stale state)
- Inline `is_code_file` logic (3-line function) to avoid cross-layer import

### 5. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-branch-switch, build-merge-cleanup, build-skip-sdlc, build-live-diff
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Test: `git checkout -b session/foo` bash command updates `modified_on_branch`
- Test: `git switch -c session/bar` bash command updates `modified_on_branch`
- Test: non-session branch switch does not update `modified_on_branch`
- Test: branch switch without pre-existing state is no-op
- Test: `SKIP_SDLC=1` returns None from `_check_no_direct_main_push`
- Test: no uncommitted code changes on main returns None (stale state)
- Test: `gh pr merge` clears `modified_on_branch`
- Test: existing tests still pass (no regressions)

### 6. Validate
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sdk_client_sdlc.py tests/unit/test_post_tool_use_sdlc.py -v`
- Run `ruff check agent/sdk_client.py .claude/hooks/post_tool_use.py`
- Run `ruff format --check agent/sdk_client.py .claude/hooks/post_tool_use.py`
- Verify all success criteria

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: hook-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-enforcement.md` with new behavior
- Add troubleshooting section for the infinite loop scenario

### 8. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify documentation exists and is accurate
- Generate final report

## Validation Commands

- `pytest tests/unit/test_sdk_client_sdlc.py -v` - verify branch check logic and SKIP_SDLC
- `pytest tests/unit/test_post_tool_use_sdlc.py -v` - verify state tracking and branch switch detection
- `ruff check agent/sdk_client.py .claude/hooks/post_tool_use.py` - lint
- `ruff format --check agent/sdk_client.py .claude/hooks/post_tool_use.py` - format
