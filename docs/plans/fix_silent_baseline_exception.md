---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-10
tracking: https://github.com/tomcounsell/ai/issues/326
---

# Fix Silent Exception Swallowing in pre_tool_use.py capture_git_baseline_once

## Problem

In `.claude/hooks/pre_tool_use.py`, the `capture_git_baseline_once()` function uses a bare `except Exception: pass` that silently swallows all errors during git baseline capture.

**Current behavior:**
When baseline capture fails (e.g., filesystem permission error, subprocess timeout, JSON serialization failure), the exception is silently discarded. No log output, no warning, no indication of failure. The stop hook (`validate_sdlc_on_stop.py`) then has no baseline to compare against, potentially flagging pre-existing dirty files as uncommitted session work.

**Desired outcome:**
Failures in baseline capture produce a visible warning on stderr, matching the established `HOOK WARNING` pattern used throughout `post_tool_use.py`. The function remains fire-and-forget (no crash), but failures are observable in hook logs.

## Prior Art

- **PR #224**: "Fix top 5 bridge error log issues" -- Addressed error logging gaps in the bridge. Established the pattern of replacing silent failures with warning logs. Related but focused on bridge code, not hooks.
- **PR #321**: While patching bare `except Exception: pass` blocks in `models/agent_session.py` (adding warning logs per review feedback), this same anti-pattern was noticed in the hook file but was out of scope.

## Data Flow

1. **Entry point**: Claude Code invokes a tool, triggering `pre_tool_use.py` hook
2. **capture_git_baseline_once()**: Runs `git diff --name-only` (staged + unstaged), filters for code extensions, writes JSON to `data/sessions/{session_id}/git_baseline.json`
3. **Stop hook**: `validate_sdlc_on_stop.py` reads `git_baseline.json` to distinguish pre-existing dirty files from session modifications
4. **Output**: If baseline capture fails silently, stop hook has no baseline and may produce false positive warnings about uncommitted work

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Solo dev work -- single line change with established pattern to follow.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Warning log on exception**: Replace `pass` with a `print(..., file=sys.stderr)` call that logs the session ID and exception message

### Flow

**Hook fires** -> capture_git_baseline_once() -> exception occurs -> **warning printed to stderr** -> function returns (no crash)

### Technical Approach

- Replace `except Exception: pass` with `except Exception as e:` plus a `print(f"HOOK WARNING: ...", file=sys.stderr)` call
- Match the exact pattern from `post_tool_use.py` lines 143-147, 187-191, 235-239
- No behavior change beyond adding visibility -- function still does not raise

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] Identify `except Exception: pass` blocks in touched files -- the one at line 54 of `pre_tool_use.py` is the target of this fix
- [ ] Add test asserting that when an exception occurs, stderr output contains the warning message

### Empty/Invalid Input Handling
- [ ] Test with empty session_id (already handled -- returns early on line 28-29)
- [ ] Test with missing session_id key in hook_input (already handled via `.get()`)

### Error State Rendering
- No user-visible output -- this is hook stderr logging only

## Rabbit Holes

- Refactoring all bare except blocks across the entire codebase -- out of scope, each should be its own issue
- Adding structured logging (JSON) to hooks -- hooks use simple stderr prints, not the bridge's logging system
- Adding retry logic for baseline capture -- the function is fire-and-forget by design

## Risks

### Risk 1: None significant
**Impact:** This is a one-line change adding a warning print. The function already catches all exceptions; we are just making the catch visible.
**Mitigation:** Match the exact established pattern from post_tool_use.py.

## Race Conditions

No race conditions identified -- `capture_git_baseline_once()` is synchronous and single-threaded. The `baseline_path.exists()` guard on line 36 prevents duplicate writes, and each session gets its own directory.

## No-Gos (Out of Scope)

- Refactoring other bare except blocks elsewhere in the codebase
- Adding structured logging to hook scripts
- Changing the fire-and-forget behavior of baseline capture
- Adding retry logic or fallback mechanisms

## Update System

No update system changes required -- this is a hook-internal change with no new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required -- this is a hook-internal change. No MCP server changes, no bridge changes, no new tools.

## Documentation

### Inline Documentation
- [ ] The new except clause is self-documenting (warning message describes the failure context)

No feature documentation changes needed -- this is a bugfix that adds observability to an existing internal mechanism.

## Success Criteria

- [ ] `except Exception: pass` in `capture_git_baseline_once()` is replaced with a warning log
- [ ] Warning format matches `HOOK WARNING: Failed to capture git baseline for {session_id}: {e}`
- [ ] Warning is printed to `sys.stderr`
- [ ] Function still does not raise (fire-and-forget behavior preserved)
- [ ] Tests pass (`/do-test`)
- [ ] Ruff format and lint pass

## Team Orchestration

### Team Members

- **Builder (hook-fix)**
  - Name: hook-fixer
  - Role: Replace bare except with warning log
  - Agent Type: builder
  - Resume: true

- **Validator (hook-fix)**
  - Name: hook-validator
  - Role: Verify the fix matches the established pattern
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix the bare except block
- **Task ID**: build-hook-fix
- **Depends On**: none
- **Assigned To**: hook-fixer
- **Agent Type**: builder
- **Parallel**: false
- Replace `except Exception: pass` with warning log in `.claude/hooks/pre_tool_use.py` line 54-55
- Use pattern: `except Exception as e:` + `print(f"HOOK WARNING: Failed to capture git baseline for {session_id}: {e}", file=sys.stderr)`

### 2. Add test for warning output
- **Task ID**: build-test
- **Depends On**: build-hook-fix
- **Assigned To**: hook-fixer
- **Agent Type**: builder
- **Parallel**: false
- Add a test that exercises the exception path and asserts stderr contains the warning

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-hook-fix, build-test
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify ruff format and lint pass
- Verify the warning pattern matches post_tool_use.py
- Run tests

## Validation Commands

- `python -m ruff check .claude/hooks/pre_tool_use.py` - Lint passes
- `python -m ruff format --check .claude/hooks/pre_tool_use.py` - Format passes
- `grep -n "HOOK WARNING" .claude/hooks/pre_tool_use.py` - Warning message exists
- `grep -c "except Exception:" .claude/hooks/pre_tool_use.py` - No bare pass (should show the except line)
