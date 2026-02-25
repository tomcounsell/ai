---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-02-25
tracking: https://github.com/yudame/valor-agent/issues/168
---

# Stop Hook False Positive After PR Merge

## Problem

After merging a PR via `gh pr merge --squash --delete-branch`, the SDLC enforcement stop hook fires repeatedly in an infinite loop, detecting the squash-merged files as "code modified directly on main."

**Current behavior:**
1. Agent works on `session/foo`, modifying code files
2. `sdlc_state.json` records `code_modified: true` with file list
3. PR is merged via `gh pr merge --squash`, branch deleted, agent back on `main`
4. Stop hook fires: `code_modified=true + branch=main = VIOLATION`
5. Agent responds minimally, stop hook fires again — infinite loop
6. Session wastes context until manually terminated

**Desired outcome:**
After a successful PR merge, the stop hook recognizes the code arrived via a proper PR flow and does not flag a violation.

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

- **Branch tracking at write time**: Record the branch name when code is first modified, not just check it at stop time
- **Merge detection in stop hook**: Recognize when code on main arrived via PR merge, not direct push
- **State cleanup after merge**: Clear `code_modified` flag after successful `gh pr merge`

### Technical Approach

**Two complementary fixes** (defense in depth):

#### Fix 1: Record branch at modification time (root cause fix)

In `post_tool_use.py` → `update_sdlc_state_for_file_write()`, record the current git branch when `code_modified` is first set to `true`. Store as `modified_on_branch` in `sdlc_state.json`.

In `_check_no_direct_main_push()`, compare `modified_on_branch` against the current branch:
- If `modified_on_branch` is a `session/*` branch and current branch is `main` → code arrived via merge, not direct push → **pass**
- If `modified_on_branch` is `main` and current branch is `main` → genuine violation → **block**
- If `modified_on_branch` is absent (legacy state files) → fall back to current behavior

This eliminates the false positive at the source. The check now asks "where was the code *written*?" not "where is the code *now*?"

#### Fix 2: Clear state after `gh pr merge` (belt and suspenders)

In `post_tool_use.py` → `update_sdlc_state_for_bash()`, detect `gh pr merge` commands. When detected, reset `code_modified` to `false` in `sdlc_state.json`. This ensures the state file accurately reflects "the session's pending code has been properly merged."

### Updated sdlc_state.json Schema

```json
{
  "code_modified": true,
  "modified_on_branch": "session/my-feature",
  "files": ["bridge/telegram_bridge.py"],
  "quality_commands": {"pytest": true, "ruff": false, "black": false},
  "reminder_sent": true
}
```

## Rabbit Holes

- Over-engineering merge detection with `git log --merges` parsing — the branch-at-write-time approach is simpler and more reliable
- Adding escape hatches to the SDK stop hook — the issue is a false positive, not a missing override; fix the detection logic instead
- Reworking the entire SDLC state model — the current model works fine, it just needs one more field

## Risks

### Risk 1: Legacy state files without `modified_on_branch`
**Impact:** Old sessions that already have `code_modified: true` but no `modified_on_branch` field
**Mitigation:** Fall back to current behavior (check current branch). Only new sessions benefit from the fix. This is safe because the bug only manifests mid-session after a merge — old sessions are already done.

### Risk 2: `gh pr merge` detection false positive
**Impact:** Resetting `code_modified` when the merge command fails (e.g., merge conflicts)
**Mitigation:** Only detect the command pattern, not success — but since `code_modified` is already defensive (fail-open), this is a minor concern. A failed merge leaves the session in a state where the agent will retry anyway.

## No-Gos (Out of Scope)

- Adding `SKIP_SDLC` escape hatch to the SDK stop hook (separate concern)
- Reworking session state persistence model
- Changing the stop hook from blocking to advisory

## Update System

No update system changes required — this fix modifies hook behavior that is already deployed via the standard update process.

## Agent Integration

No agent integration required — this is a hook-internal change. The hooks already fire automatically on tool use and session stop events.

## Documentation

- [ ] Update `docs/features/sdlc-enforcement.md` — add `modified_on_branch` field to the state file schema, add troubleshooting entry for post-merge false positive
- [ ] Add entry to `docs/features/README.md` index table (if not already present)

### Inline Documentation
- [ ] Docstring updates for `_check_no_direct_main_push()` and `update_sdlc_state_for_file_write()`

## Success Criteria

- [ ] After `gh pr merge --squash`, stop hook does NOT fire false positive
- [ ] `sdlc_state.json` includes `modified_on_branch` when code is modified
- [ ] Direct code push to main still triggers violation (true positive preserved)
- [ ] Legacy state files without `modified_on_branch` don't crash or false-negative
- [ ] Existing unit tests in `test_sdk_client_sdlc.py` still pass
- [ ] New unit tests cover: merge scenario (code on session branch, now on main), legacy state fallback, `gh pr merge` state cleanup
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (hook-fix)**
  - Name: hook-builder
  - Role: Implement both fixes (branch tracking + merge cleanup)
  - Agent Type: builder
  - Resume: true

- **Validator (hook-fix)**
  - Name: hook-validator
  - Role: Verify fix correctness and edge cases
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add branch tracking to post_tool_use.py
- **Task ID**: build-branch-tracking
- **Depends On**: none
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- In `update_sdlc_state_for_file_write()`, get current git branch via `git rev-parse --abbrev-ref HEAD` when setting `code_modified=true` for the first time
- Store as `modified_on_branch` in the state dict
- Only set once (don't overwrite if already present — first write determines the branch)

### 2. Add merge detection to post_tool_use.py
- **Task ID**: build-merge-detection
- **Depends On**: none
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- In `update_sdlc_state_for_bash()`, detect `gh pr merge` commands using regex
- When detected, load state and set `code_modified=false`
- Only act if state file exists (same fast-path pattern as quality command tracking)

### 3. Update _check_no_direct_main_push logic
- **Task ID**: build-check-logic
- **Depends On**: build-branch-tracking
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- After confirming `code_modified=true` and `current_branch == "main"`, check `modified_on_branch`
- If `modified_on_branch` starts with `session/` → return None (code arrived via merge)
- If `modified_on_branch` is `main` or absent → return violation (preserve current behavior)

### 4. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-check-logic, build-merge-detection
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Add test: code modified on `session/foo`, current branch `main` → no violation (merge scenario)
- Add test: code modified on `main`, current branch `main` → violation (direct push)
- Add test: legacy state (no `modified_on_branch`), current branch `main` → violation (backward compat)
- Add test: `gh pr merge` in bash command → `code_modified` reset to false
- Add test: non-merge bash command → `code_modified` unchanged

### 5. Validate
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sdk_client_sdlc.py tests/unit/test_validate_sdlc_on_stop.py tests/unit/test_post_tool_use_sdlc.py -v`
- Run `ruff check agent/sdk_client.py agent/hooks/stop.py .claude/hooks/post_tool_use.py`
- Run `black --check agent/sdk_client.py agent/hooks/stop.py .claude/hooks/post_tool_use.py`
- Verify all success criteria

## Validation Commands

- `pytest tests/unit/test_sdk_client_sdlc.py -v` - verify branch check logic
- `pytest tests/unit/test_post_tool_use_sdlc.py -v` - verify state tracking
- `ruff check agent/sdk_client.py .claude/hooks/post_tool_use.py` - lint
- `black --check agent/sdk_client.py .claude/hooks/post_tool_use.py` - format
