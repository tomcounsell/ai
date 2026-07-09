---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-09
tracking: https://github.com/tomcounsell/ai/issues/1959
last_comment_id: none
---

# `_execute_agent_session` swallows exceptions silently without logging

## Problem

The executor-guard last-resort status save inside `_execute_agent_session`
catches every exception and does nothing with it. If the fallback
`session.save()` raises, the failure is completely invisible: no log line, no
metric, nothing to debug from later. A session that fails to persist its
`failed` status here vanishes without a trace.

**Current behavior:** `agent/session_executor.py` lines 1789-1791 (inside the
`empty_turn_input` executor-guard block of `_execute_agent_session`):
```python
                try:
                    session.status = "failed"
                    session.save(update_fields=["status", "updated_at"])
                except Exception:  # noqa: BLE001
                    pass
            return
```
This trips the guard test `tests/integration/test_silent_failures.py::TestNoSilentPassRemaining::test_no_bare_pass_in_critical_functions`, which uses `inspect.getsource` on the seven critical functions (including `_execute_agent_session`) and fails on any `except Exception:` immediately followed by a bare `pass` with no `logger` reference within the surrounding context window.

**Desired outcome:** The exception is logged (matching the `logger.warning`/`logger.error` `[executor-guard]` pattern already used two lines above) before the function returns, so a save failure at this point is observable. The guard test passes.

## Freshness Check

**Baseline commit:** `01214eac` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-09T04:41:58Z
**Disposition:** Minor drift

**File:line references re-verified:**
- Issue claims `agent/agent_session_queue.py` `_execute_agent_session` "around line 995". Drift: the function is *defined* in `agent/session_executor.py:805` and re-exported through `agent/agent_session_queue.py` (imported at line 64). `inspect.getsource` reads the real definition, which is why the guard test resolves the source to `session_executor.py`. The issue's "line 995" was the source-relative line number reported by the test, not a file line. The actual anti-pattern lives at `agent/session_executor.py:1789-1791`. Confirmed by hand: the `except Exception:  # noqa: BLE001` / `pass` block sits inside the executor-guard's `empty_turn_input` handler (issue #1741 "Fix B"), still inside `_execute_agent_session` (def at 805, no intervening `def`).

**Cited sibling issues/PRs re-checked:**
- None cited in the issue body.

**Commits on main since issue was filed (touching referenced files):**
- `bec97694` "Fix headless runner zombie wedge" — touched `agent/session_executor.py` but NOT the executor-guard block. Verified with `git show bec97694`: no changes near the `update_fields=["status", "updated_at"]` last-resort save. Block is unchanged.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Corrected the target file/line in Technical Approach below. No premise changed — the bare `except Exception: pass` still exists on current main and the guard test still fails against it.

## Prior Art

Skipped deep prior-art analysis (Small appetite, single-line observability fix). The guard test file `tests/integration/test_silent_failures.py` and its docstring ("The 7 critical locations are ...") show this is the last of a set of silent-failure sites that were previously instrumented with `logger.warning()` calls. This fix completes that set for the `_execute_agent_session` last-resort save. No prior *failed* attempt at this specific block exists.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. Module-level `logger = logging.getLogger(__name__)` already exists at `agent/session_executor.py:34`.

## Solution

### Key Elements

- **Log-before-return**: Replace the bare `pass` in the inner `except Exception:` block with a `logger.warning(...)` call that names the session and the operation attempted, matching the surrounding `[executor-guard]` log style.

### Flow

`_execute_agent_session` hits empty-turn-input guard → `finalize_session` raises a non-`StatusConflictError` exception → outer `except` logs `logger.error("[executor-guard] last-resort status save ...")` and attempts a direct `session.save()` → if that save *also* raises → **new behavior:** `logger.warning("[executor-guard] last-resort status save failed ...")` instead of silent `pass` → `return`.

### Technical Approach

- Edit `agent/session_executor.py`, the inner `try/except` at lines 1789-1791.
- Change:
  ```python
                except Exception:  # noqa: BLE001
                    pass
  ```
  to:
  ```python
                except Exception as save_exc:  # noqa: BLE001
                    logger.warning(
                        "[executor-guard] last-resort status save failed for "
                        "session %s: %s",
                        session.agent_session_id,
                        save_exc,
                    )
  ```
- Control flow is preserved: the `return` on the following line still executes; the exception is swallowed (intentional — this is a last-resort best-effort save), only now it is observable.
- `logger.warning` (not `logger.exception`) matches the adjacent outer-handler style, which uses `logger.error` with `exc` interpolated rather than a full traceback. A traceback here adds little signal for a best-effort save and would be noisier; `warning` correctly reflects that the session is already being finalized as `failed`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The single `except Exception: pass` block in scope is the exact target of this fix — it gains a `logger.warning`. Coverage is asserted by the existing guard test `TestNoSilentPassRemaining::test_no_bare_pass_in_critical_functions`, which mechanically detects `except Exception: pass` without a nearby `logger` reference.
- [ ] No other exception handlers are added or modified.

### Empty/Invalid Input Handling
- [ ] No new functions are introduced. The enclosing empty-turn-input guard (issue #1741) already handles the empty/`None`/`"None"` turn-input case; this fix only instruments its innermost fallback save.

### Error State Rendering
- [ ] No user-visible output surface. The observable output is a log line; the guard test asserts a `logger` reference is present in the block.

## Test Impact

- [ ] `tests/integration/test_silent_failures.py::TestNoSilentPassRemaining::test_no_bare_pass_in_critical_functions` — no change to the test; it currently FAILS against main and must PASS after the fix. This is the acceptance test.

No other existing tests affected — the change adds a log line inside a best-effort exception handler and does not alter control flow, return values, or any observable behavior other than emitting a warning when the fallback save raises. No test asserts the absence of that log line.

## Rabbit Holes

- Do NOT refactor the executor-guard block, restructure the nested try/except, or attempt to make the last-resort save "reliable" (retry, alternate persistence path). The save is intentionally best-effort; the only defect is that its failure is silent.
- Do NOT sweep the whole file for other `except ... pass` blocks. The guard test scopes exactly seven functions; only `_execute_agent_session` currently fails. Fixing unrelated blocks is scope creep.
- Do NOT switch to `logger.exception`/full traceback plumbing — it changes log volume characteristics for a path that fires only when a session is already failing.

## Risks

### Risk 1: `save_exc` variable name collision
**Impact:** A shadowed name could confuse a later reader or clash with an existing local.
**Mitigation:** `save_exc` is a fresh name scoped to the `except` clause; the outer handler already binds `exc`. No collision — verified the two are in disjoint `except` clauses.

### Risk 2: Guard-test context-window false pass
**Impact:** The guard test only scans a small window (`lines[i-2:i+3]`) for `logger`. If the fix placed the logger call outside that window it would still fail.
**Mitigation:** The `logger.warning` call *replaces* the `pass` line directly, so `stripped == "pass"` no longer matches at all — the block is no longer detected as a bare-pass. This is the strongest possible pass condition.

## Race Conditions

No race conditions identified — the change adds a synchronous `logger.warning` call inside an existing exception handler. No new shared state, no async ordering, no cross-process data flow is introduced.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

## Update System

No update system changes required — this is a one-line change to an existing exception handler in `agent/session_executor.py`. No new dependencies, config files, or migrations. `run.py`/`migrations.py` are untouched.

## Agent Integration

No agent integration required — this is a worker-internal change to the session executor's error-logging behavior. No MCP surface, no `.mcp.json` change, no new `tools/` entry, no bridge import. The executor already runs inside the worker session-execution path.

## Documentation

No documentation changes needed — this is an observability bug fix inside an existing internal function (`_execute_agent_session`), with no new capability, config, CLI, or user-facing surface. The behavioral contract ("critical functions must not swallow exceptions silently") is already documented by the guard test `tests/integration/test_silent_failures.py` and its module docstring, which enumerates the seven critical locations. The new `logger.warning` call is self-documenting inline and carries a `[executor-guard]` prefix consistent with the surrounding lines. No `docs/features/` page describes this internal error-handling path, so there is nothing to update.

- [ ] No `docs/features/` create/update required — justified above (internal observability fix, no user-facing surface).

## Success Criteria

- [ ] `agent/session_executor.py` inner `except Exception:` block (formerly lines 1789-1791) emits a `logger.warning` naming the session before returning.
- [ ] `pytest tests/integration/test_silent_failures.py::TestNoSilentPassRemaining::test_no_bare_pass_in_critical_functions -q -n0` passes.
- [ ] Control flow unchanged: the `return` after the block still executes; the exception is still swallowed (best-effort save).
- [ ] Tests pass (`/do-test`)
- [ ] `python -m ruff check .` and `python -m ruff format --check .` clean

## Team Orchestration

Single-task solo fix. No multi-agent orchestration needed.

### Team Members

- **Builder (logging-fix)**
  - Name: silent-pass-builder
  - Role: Replace the bare `pass` with a `logger.warning` call in the executor-guard block
  - Agent Type: builder
  - Resume: true

## Step by Step Tasks

### 1. Add warning log to the executor-guard last-resort save
- **Task ID**: build-silent-pass-logging
- **Depends On**: none
- **Validates**: tests/integration/test_silent_failures.py::TestNoSilentPassRemaining::test_no_bare_pass_in_critical_functions
- **Assigned To**: silent-pass-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/session_executor.py`, locate the inner `try/except` (around lines 1789-1791, inside the `empty_turn_input` executor-guard of `_execute_agent_session`).
- Replace `except Exception:  # noqa: BLE001` / `pass` with `except Exception as save_exc:  # noqa: BLE001` and a `logger.warning("[executor-guard] last-resort status save failed for session %s: %s", session.agent_session_id, save_exc)`.
- Preserve the `return` on the following line and the best-effort swallow semantics.
- Run `pytest tests/integration/test_silent_failures.py::TestNoSilentPassRemaining::test_no_bare_pass_in_critical_functions -q -n0` and confirm it passes.
- Run `python -m ruff check agent/session_executor.py` and `python -m ruff format agent/session_executor.py`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Guard test passes | `pytest tests/integration/test_silent_failures.py::TestNoSilentPassRemaining::test_no_bare_pass_in_critical_functions -q -n0` | exit code 0 |
| No bare pass remains in the block | `grep -A1 'last-resort status save failed' agent/session_executor.py \| grep -c 'pass'` | match count == 0 |
| Warning log present | `grep -c 'last-resort status save failed for' agent/session_executor.py` | output contains 1 |
| Lint clean | `python -m ruff check agent/session_executor.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/session_executor.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
