---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-20
tracking: https://github.com/tomcounsell/ai/issues/457
last_comment_id:
---

# Observer calls non-existent `is_sdlc_job()` method

## Problem

The Observer agent crashes silently on every invocation because `observer.py` calls `self.session.is_sdlc_job()` in three places, but `AgentSession` only exposes `is_sdlc` as a **property** — there is no `is_sdlc_job()` method.

This `AttributeError` causes:
1. **Observer constructor fails** (line 365) → state machine never initializes for SDLC sessions
2. **`_handle_read_session` fails** (line 399) → `read_session` tool returns an error to the LLM, so the Observer has no session context to make routing decisions
3. **`run()` fails** (line 526) → the `is_sdlc` check that determines max auto-continues is broken

The fallback in `job_queue.py:1728-1734` catches the crash and delivers raw worker output directly to Telegram — bypassing the entire Observer routing and summarization pipeline. That's why SDLC sessions lose their stage progress lines and link footers: the Observer never runs successfully, so the output goes through the raw fallback path instead of the formatting pipeline.

**Evidence:** `job_queue.py:1646` correctly uses `agent_session.is_sdlc` (the property). `response.py:396` correctly uses `session.is_sdlc`. Only `observer.py` uses the non-existent `is_sdlc_job()`.

**Why tests didn't catch this:** Tests use `MagicMock` sessions where `session.is_sdlc_job.return_value = True` works because MagicMock creates attributes on access.

## Solution

Replace `self.session.is_sdlc_job()` with `self.session.is_sdlc` in three locations in `bridge/observer.py`. That's it.

The formatting pipeline (`response.py` → `summarizer.py` → `_compose_structured_summary()`) already works correctly for SDLC sessions. It checks `session.is_sdlc`, forces summarization, and adds stage progress lines and link footers. The only reason it wasn't working is the Observer was crashing before it could route the output through that pipeline.

No new logic. No safety nets. No double-formatting guards. No prompt changes. Just fix the API call.

### Changes

**`bridge/observer.py`** — 3 lines changed:
- Line 365: `if session.is_sdlc_job():` → `if session.is_sdlc:`
- Line 399: `is_sdlc = self.session.is_sdlc_job()` → `is_sdlc = self.session.is_sdlc`
- Line 526: `is_sdlc = self.session.is_sdlc_job()` → `is_sdlc = self.session.is_sdlc`

**`tests/unit/test_observer.py`** — Update mock setup:
- Change `session.is_sdlc_job.return_value = X` to use `is_sdlc` property on mock (via `PropertyMock` or direct attribute)

### Why this is cleaner than the original plan

The original plan proposed 3 interconnected fixes across 4 files:
1. Harden Observer prompt with `is_sdlc` authority instructions
2. Add `_compose_structured_summary()` call in `job_queue.py` as a "safety net"
3. Add double-formatting prevention in `response.py`

That was treating symptoms. The Observer LLM wasn't "misclassifying" SDLC sessions — it was never receiving session data at all because `read_session` was crashing. The formatting wasn't missing because of a pipeline gap — the pipeline was never reached because the Observer fallback delivered raw output.

Fix the root cause (wrong method name), and the existing pipeline handles everything correctly.

## Appetite

**Size:** Small (< 1 hour)

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Verify Observer constructs successfully with a real SDLC session (no AttributeError)
- [ ] Verify `_handle_read_session` returns valid data including `is_sdlc: true`

### Empty/Invalid Input Handling
- [ ] Verify Observer handles session with `is_sdlc = False` (non-SDLC path)
- [ ] Verify Observer handles session with no stage_states (fresh SDLC session)

### Error State Rendering
- [ ] Existing Observer fallback in `job_queue.py:1728-1734` still works if Observer fails for other reasons

## Test Impact

- [ ] `tests/unit/test_observer.py` — UPDATE: change `session.is_sdlc_job.return_value` to use `session.is_sdlc` as a property/attribute on mock objects

## Rabbit Holes

- Adding "safety net" formatting in `job_queue.py` — unnecessary, the pipeline already works
- Prompt hardening for `is_sdlc` authority — the Observer wasn't misclassifying, it was crashing
- Double-formatting prevention — not a real risk since there's only one formatting path

## Risks

### Risk 1: Other callers of `is_sdlc_job()`
**Impact:** Other code might also use the wrong method name
**Mitigation:** Grep confirmed only `observer.py` uses `is_sdlc_job()`. All other callers (`job_queue.py`, `response.py`, `summarizer.py`) correctly use `is_sdlc`.

## Race Conditions

None — this is a method name fix, no concurrency changes.

## No-Gos (Out of Scope)

- Refactoring Observer routing logic
- Changing the summarizer pipeline
- Adding new formatting paths in `job_queue.py`
- Modifying Observer prompt

## Update System

No update system changes required.

## Agent Integration

No agent integration required.

## Documentation

- [ ] Update `docs/features/observer-agent.md` to note `is_sdlc` property usage (not method)

## Success Criteria

- [ ] Observer constructs without `AttributeError` for SDLC sessions
- [ ] `read_session` tool returns `is_sdlc: true` for SDLC sessions
- [ ] SDLC session deliveries include stage progress line and link footers
- [ ] Non-SDLC message delivery is unaffected
- [ ] All existing tests pass
- [ ] Lint and format clean

## Team Orchestration

### Team Members

- **Builder (is-sdlc-fix)**
  - Name: fix-builder
  - Role: Fix the 3 method calls and update test mocks
  - Agent Type: builder
  - Resume: true

- **Validator (is-sdlc-fix)**
  - Name: fix-validator
  - Role: Verify Observer runs correctly for SDLC sessions
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix `is_sdlc_job()` → `is_sdlc` in observer.py
- **Task ID**: fix-observer-calls
- **Depends On**: none
- **Validates**: tests/unit/test_observer.py
- **Assigned To**: fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace 3 occurrences of `self.session.is_sdlc_job()` / `session.is_sdlc_job()` with `self.session.is_sdlc` / `session.is_sdlc` in `bridge/observer.py`

### 2. Update test mocks
- **Task ID**: fix-test-mocks
- **Depends On**: fix-observer-calls
- **Validates**: tests/unit/test_observer.py
- **Assigned To**: fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Update mock session objects in `tests/unit/test_observer.py` to use `session.is_sdlc` attribute instead of `session.is_sdlc_job.return_value`

### 3. Validate
- **Task ID**: validate-all
- **Depends On**: fix-test-mocks
- **Assigned To**: fix-validator
- **Agent Type**: validator
- **Parallel**: false
- Run tests, lint, format checks

### N-1. Documentation
- **Task ID**: document-fix
- **Depends On**: validate-all
- **Assigned To**: fix-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/observer-agent.md`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_observer.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/observer.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/observer.py` | exit code 0 |
| No is_sdlc_job in observer | `grep -c 'is_sdlc_job' bridge/observer.py` | output = 0 |
| is_sdlc used correctly | `grep -c 'is_sdlc' bridge/observer.py` | output > 0 |

## Open Questions

No open questions.
