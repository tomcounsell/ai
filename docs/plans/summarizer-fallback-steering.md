---
slug: summarizer-fallback-steering
status: Planning
type: bug
appetite: Small
tracking: https://github.com/tomcounsell/ai/issues/891
created: 2026-04-10
---

# Summarizer Fallback: Agent Self-Summary via Session Steering

## Problem

When both summarizer backends (Haiku and OpenRouter) fail, the delivery path at `bridge/summarizer.py:1495-1506` truncates raw agent output and sends it verbatim to Telegram. This raw output contains narrated train-of-thought text ("Let me investigate...", "Let me check...") that violates PM-voice communication standards.

**Current behavior:**

Raw internal monologue reaches the Telegram chat:

> "Let me investigate recent errors in the system. Found the error. Let me get more details on it. No existing issue. The bug is clear: `SortedField` is for a single numeric value... Let me create the issue. Let me research the context first..."

Bridge logs confirm `"All summarization backends failed, truncating"` at the time of delivery.

**Desired outcome:**

When summarizer backends fail, the system injects a steering message asking the agent to self-summarize its own output using the same quality standards the summarizer enforces. Raw truncated text is never delivered unless the self-summary also fails AND `is_narration_only()` confirms the text is safe.

## Freshness Check

**Baseline commit:** `bdf1e2a9`
**Issue filed at:** 2026-04-10T13:54:11Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/summarizer.py:1495-1506` -- fallback truncation block -- still holds, code unchanged
- `bridge/summarizer.py:1444` -- `_strip_process_narration()` call site -- still holds
- `bridge/summarizer.py:1006` -- `SUMMARIZER_SYSTEM_PROMPT` definition -- still holds
- `bridge/message_quality.py:42` -- `is_narration_only()` definition -- still holds, still dead code (no callers in delivery path)
- `agent/steering.py:37` -- `push_steering_message()` -- still holds

**Cited sibling issues/PRs re-checked:**
- #186 -- closed, addressed threshold and stage line issues but not the fallback narration path
- PR #408 -- merged, fixed observer reason leak (different vector)

**Commits on main since issue was filed (touching referenced files):** None

**Active plans in `docs/plans/` overlapping this area:** None

**Notes:** All file:line references from the issue are accurate against current main.

## Prior Art

- **Issue #676**: Summarizer integration audit -- closed, identified coverage gaps but did not address the fallback truncation path
- **Issue #401**: Observer leaks internal reasoning to Telegram -- closed, addressed a different leak vector (observer reasons, not summarizer fallback)
- **PR #228**: SDLC-first architecture with summarizer reliability -- merged, established the current summarizer pipeline but the fallback truncation has always been blind

## Data Flow

Current (broken) fallback path:

1. **Agent produces output** -- raw text with potential narration
2. **`send_to_chat()` callback** in `agent/agent_session_queue.py:2843` routes via nudge loop, action resolves to `"deliver"`
3. **`send_cb()`** calls `bridge/response.py:send_response_with_files()` which calls `summarize_response()`
4. **`summarize_response()`** at `bridge/summarizer.py:1404`: strips narration (line 1444), tries Haiku (line 1450), tries OpenRouter (line 1453)
5. **Both fail** -- falls through to line 1495: blind truncation at `SAFETY_TRUNCATE` (4096 chars)
6. **Returns `SummarizedResponse(was_summarized=False)`** with raw truncated text
7. **Raw text delivered to Telegram** -- narration and all

New fallback path (this plan):

1. Steps 1-4 same as above
2. **Both fail** -- instead of truncating, `summarize_response()` returns a new signal: `needs_self_summary=True`
3. **`send_response_with_files()`** detects the signal, pushes a steering message to the session's queue via `push_steering_message()`
4. **Steering message content**: distilled self-summary instruction derived from `SUMMARIZER_SYSTEM_PROMPT` quality requirements
5. **Worker picks up steering message** at next turn boundary (line 3106-3127 in `agent_session_queue.py`), agent produces a clean self-summary
6. **Self-summary goes through normal delivery path** -- if summarizer works this time, great; if not, apply `is_narration_only()` gate before delivering
7. **Last resort**: if `is_narration_only()` returns True, deliver `NARRATION_FALLBACK_MESSAGE` instead of raw text

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. All changes are to existing bridge and agent modules.

## Solution

### Key Elements

- **Self-summary signal**: `SummarizedResponse` gains a `needs_self_summary` boolean field so the caller can distinguish "summarization failed" from "text was short enough to skip"
- **Steering message injection**: `send_response_with_files()` pushes a self-summary instruction to the session's steering queue when the signal is set
- **Self-summary prompt**: A compact instruction derived from `SUMMARIZER_SYSTEM_PROMPT` that tells the agent to re-state its output as a concise PM-facing update
- **Narration gate**: `is_narration_only()` wired into the delivery path as the final safety net before raw text reaches Telegram

### Flow

**Agent output** --> `send_response_with_files()` --> `summarize_response()` --> [backends fail] --> returns `needs_self_summary=True` --> steering message pushed --> agent self-summarizes on next turn --> normal delivery

**Last resort**: if self-summary also fails summarization AND `is_narration_only()` is True --> deliver `NARRATION_FALLBACK_MESSAGE` instead of raw narration

### Technical Approach

1. **Add `needs_self_summary` field to `SummarizedResponse`** (default `False`). When all backends fail, set it to `True` instead of truncating. Still populate `full_output_file` and `artifacts` so they are not lost.

2. **Create `SELF_SUMMARY_INSTRUCTION` constant** in `bridge/summarizer.py`. This is a compact prompt distilled from `SUMMARIZER_SYSTEM_PROMPT` that the agent can follow to produce a clean summary. It does not include the full system prompt -- just the essential rules (outcome-focused bullets, no process narration, PM voice).

3. **Wire steering injection in `send_response_with_files()`** (`bridge/response.py`). After calling `summarize_response()`, if `needs_self_summary` is True and `session` is available, call `push_steering_message()` from `agent/steering.py` to inject the self-summary instruction. Return early without sending to Telegram -- the agent will produce the summary on its next turn, which will flow through the normal delivery path.

4. **Wire `is_narration_only()` as last-resort gate** in `send_response_with_files()`. When `was_summarized` is False (meaning no self-summary signal either -- this is the ultimate fallback), check `is_narration_only(text)`. If True, replace with `NARRATION_FALLBACK_MESSAGE`. This eliminates the dead-code status of `is_narration_only()`.

5. **Handle the "no session" case**: If `summarize_response()` returns `needs_self_summary=True` but no session is available (e.g., called without session context), fall back to the existing truncation behavior with the narration gate applied.

## Step by Step Tasks

### 1. Add self-summary signal and instruction

- **Task ID**: build-signal
- **Depends On**: none
- **Validates**: `tests/unit/test_summarizer.py`
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true
- Add `needs_self_summary: bool = False` field to `SummarizedResponse` dataclass in `bridge/summarizer.py`
- Create `SELF_SUMMARY_INSTRUCTION` constant in `bridge/summarizer.py` -- a compact prompt derived from `SUMMARIZER_SYSTEM_PROMPT` rules (outcome-focused bullets, no process narration, PM voice, preserve artifacts)
- Modify the fallback block at line 1495-1506: set `needs_self_summary=True`, keep artifacts and `full_output_file`, set `text` to empty string (text will come from the agent's self-summary)

### 2. Wire steering injection in response.py

- **Task ID**: build-steering
- **Depends On**: build-signal
- **Validates**: `tests/unit/test_delivery_execution.py`, `tests/integration/test_summarizer_integration.py`
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: false
- In `send_response_with_files()` after calling `summarize_response()`, check `summarized.needs_self_summary`
- If True and session is available with a `session_id`: call `push_steering_message(session.session_id, SELF_SUMMARY_INSTRUCTION, sender="summarizer-fallback")` and return early (do not send to Telegram)
- If True but no session: fall through to existing truncation with narration gate (step 3)
- Import `push_steering_message` from `agent.steering` and `SELF_SUMMARY_INSTRUCTION` from `bridge.summarizer`

### 3. Wire narration gate as last resort

- **Task ID**: build-gate
- **Depends On**: build-signal
- **Validates**: `tests/unit/test_message_quality.py`
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: true (with build-steering)
- In `send_response_with_files()`, after the summarizer call, when `was_summarized` is False and `needs_self_summary` is False (i.e., text was short or the no-session fallback): check `is_narration_only(text)`
- If True, replace `text` with `NARRATION_FALLBACK_MESSAGE` from `bridge/message_quality.py`
- Import `is_narration_only` and `NARRATION_FALLBACK_MESSAGE` from `bridge.message_quality`

### 4. Add tests

- **Task ID**: build-tests
- **Depends On**: build-signal, build-steering, build-gate
- **Validates**: all new test cases
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add test in `tests/unit/test_summarizer.py`: when both backends fail, `summarize_response()` returns `needs_self_summary=True` and empty text
- Add test in `tests/unit/test_summarizer.py`: `SELF_SUMMARY_INSTRUCTION` contains key quality markers (no process narration, outcome-focused)
- Add test in `tests/unit/test_delivery_execution.py`: when `needs_self_summary=True` and session available, `push_steering_message` is called and no Telegram message is sent
- Add test in `tests/unit/test_delivery_execution.py`: when `needs_self_summary=True` and no session, text falls through with narration gate applied
- Add test: `is_narration_only()` gate replaces narration text with `NARRATION_FALLBACK_MESSAGE` in the delivery path

### 5. Documentation

- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/summarizer-format.md` to document the self-summary fallback path
- Update `docs/features/session-steering.md` to mention summarizer fallback as a steering use case

### 6. Final Validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_summarizer.py tests/unit/test_message_quality.py tests/unit/test_delivery_execution.py -x -q`
- Run `python -m ruff check bridge/summarizer.py bridge/response.py bridge/message_quality.py`
- Verify `is_narration_only` is imported in `bridge/response.py` (no longer dead code)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `push_steering_message()` call in `response.py` must be wrapped in try/except so a Redis failure does not block delivery -- fall through to truncation + narration gate
- [ ] If `is_narration_only()` raises, the text should be delivered as-is (non-fatal)

### Empty/Invalid Input Handling
- [ ] `summarize_response("")` still returns empty `SummarizedResponse` with `needs_self_summary=False` -- the empty check at line 1425 returns before reaching the fallback
- [ ] `is_narration_only("")` returns `False` (already tested, no change needed)
- [ ] `is_narration_only(None)` returns `False` (already handled)

### Error State Rendering
- [ ] When self-summary steering fails (Redis down), raw text is still delivered with narration gate applied -- not silently dropped
- [ ] `NARRATION_FALLBACK_MESSAGE` is a user-friendly message, not a stack trace or empty string

## Test Impact

- [ ] `tests/unit/test_summarizer.py::TestSummarizeResponse::test_all_backends_fail_truncates` -- UPDATE: assert `needs_self_summary=True` instead of truncated raw text
- [ ] `tests/unit/test_summarizer.py::TestSummarizeResponse::test_all_backends_fail_long_text` -- UPDATE: assert `needs_self_summary=True` and `text` is empty string
- [ ] `tests/unit/test_delivery_execution.py` -- UPDATE: mock `summarize_response` to return `needs_self_summary` scenarios

## Rabbit Holes

- Implementing a retry loop within `summarize_response()` to re-call Haiku/OpenRouter. The steering approach is simpler and leverages existing infrastructure.
- Making the self-summary synchronous (Option C from the issue). This would block the delivery path and add latency. Steering is async and non-blocking.
- Refactoring the entire summarizer fallback chain. The change is surgical: one new field, one new constant, two wiring points in `response.py`.
- Passing the full `SUMMARIZER_SYSTEM_PROMPT` as the steering message. It is too long (over 3KB). A distilled instruction is sufficient.

## Risks

### Risk 1: Self-summary produces another unsummarizable response
**Impact:** Infinite steering loop -- agent keeps being asked to self-summarize
**Mitigation:** The steering message is injected only once per delivery attempt. The `needs_self_summary` flag is set by `summarize_response()` only when backends fail, not when the text is already a self-summary. On the second pass through `send_response_with_files()`, if backends fail again, the narration gate catches it. No loop is possible because steering is a one-shot injection, not a retry mechanism.

### Risk 2: Steering message arrives after session completes
**Impact:** The steering message is never consumed; next session picks it up incorrectly
**Mitigation:** `clear_steering_queue()` is already called at session cleanup. Steering messages have session-scoped keys (`steering:{session_id}`), so cross-session contamination cannot occur.

### Risk 3: Redis unavailable when pushing steering message
**Impact:** `push_steering_message()` fails, no self-summary is produced
**Mitigation:** Wrap the push in try/except, fall through to truncation + narration gate. This is the existing behavior today, just with the narration gate added.

## Race Conditions

No race conditions identified. The steering message push is a single Redis RPUSH (atomic). The worker consumes steering messages sequentially at turn boundaries. There is no shared mutable state between the summarizer fallback and the steering consumer -- they run in different execution contexts (bridge callback vs. worker loop).

## No-Gos (Out of Scope)

- Replacing or upgrading the summarizer backends (Haiku, OpenRouter). This plan only addresses the fallback when they fail.
- Adding a third summarizer backend. Out of scope -- the self-summary approach makes a third backend unnecessary.
- Changing the happy path (when summarizer succeeds). The fix is isolated to the fallback.
- Modifying `_strip_process_narration()` behavior. It continues to run pre-summarizer as before.
- Making `is_narration_only()` block delivery in the happy path. It is only wired as a last-resort gate in the fallback path.

## Update System

No update system changes required -- this is a bridge-internal change that modifies existing modules. No new dependencies, no new config files, no migration steps.

## Agent Integration

No agent integration required -- this is a bridge-internal change. The steering message is pushed programmatically from `bridge/response.py`, not from an MCP tool or agent action. The agent is the *recipient* of the steering message, not the sender.

## Documentation

- [ ] Update `docs/features/summarizer-format.md` to document the self-summary fallback path and `is_narration_only()` gate
- [ ] Update `docs/features/session-steering.md` to add summarizer fallback as a steering use case
- [ ] Add inline docstring to `SELF_SUMMARY_INSTRUCTION` constant explaining its purpose and derivation

## Success Criteria

- [ ] When both summarizer backends fail, `summarize_response()` returns `needs_self_summary=True`
- [ ] `send_response_with_files()` pushes a steering message when `needs_self_summary=True` and session is available
- [ ] Raw truncated text is never delivered directly to Telegram (unless self-summary also fails AND `is_narration_only()` passes)
- [ ] `is_narration_only()` is wired into the delivery path (no longer dead code)
- [ ] Existing `tests/unit/test_message_quality.py` tests still pass
- [ ] New tests cover: self-summary fallback trigger, steering injection, narration gate
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (summarizer-fallback)**
  - Name: fallback-builder
  - Role: Implement signal, steering wiring, and narration gate
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: fallback-tester
  - Role: Write tests for all new paths
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: fallback-docs
  - Role: Update feature docs
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: fallback-validator
  - Role: Final validation of all criteria
  - Agent Type: validator
  - Resume: true

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_summarizer.py tests/unit/test_message_quality.py tests/unit/test_delivery_execution.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/summarizer.py bridge/response.py bridge/message_quality.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/summarizer.py bridge/response.py bridge/message_quality.py` | exit code 0 |
| Narration gate wired | `grep -n 'is_narration_only' bridge/response.py` | exit code 0 |
| Dead code eliminated | `grep -rn 'is_narration_only' bridge/response.py` | output contains is_narration_only |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

No open questions -- the stakeholder confirmed Option B (session steering) as the implementation approach, and the technical path is clear from the existing steering infrastructure.
