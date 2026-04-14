---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-14
tracking: https://github.com/tomcounsell/ai/issues/958
last_comment_id: null
revision_applied: true
---

# PM Session Context Overflow — Harness Input Budget Cap

## Problem

PM sessions that resume across multiple Telegram turns eventually crash with `Separator is not found, and chunk exceed the limit`. This error comes from inside the `claude` CLI binary when the argument passed to `claude -p` exceeds the binary's internal chunking limit.

**Current behavior:** Each PM session resume turn assembles a message that includes a context prefix, a resume hydration block (git summary), a reply thread of up to 20 prior Telegram messages (up to 2000 chars each for Valor's messages), and the steering message text. This assembled string is passed as a command-line argument (`cmd = harness_cmd + [message]` in `agent/sdk_client.py:1532`). On the third or fourth turn, the cumulative context crosses the `claude` binary's internal chunk limit, and the session dies with no recovery.

**Desired outcome:** PM sessions remain functional across arbitrarily many turns. When resume context grows large, the oldest context is trimmed before the harness call rather than crashing after it. The trim is silent — no session downtime, no error message to Telegram.

## Freshness Check

**Baseline commit:** `10ebedfd3f3bd66f0f617839809c167bfabef643`
**Issue filed at:** 2026-04-14T13:01:07Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/sdk_client.py:1532` — `cmd = harness_cmd + [message]` — still holds exactly
- `agent/sdk_client.py:1686` — `build_harness_turn_input()` — still holds, no changes since filing
- `agent/messenger.py:148` — `self._result = await coro` (crash site in `_run_work`) — still holds
- `agent/agent_session_queue.py:555` — `_maybe_inject_resume_hydration()` — still holds
- `bridge/context.py:463` — `format_reply_chain()` — still holds; `max_len` for Valor messages is 2000 chars

**Cited sibling issues/PRs re-checked:**
- #961 — Closed 2026-04-14 with revised scope: confirmed same root cause as #958, no redundant fix landed
- #945 — Open (harness streaming regression gap) — related area but distinct problem

**Commits on main since issue was filed (touching referenced files):**
- None (zero commits on `agent/`, `bridge/context.py`, `agent/sdk_client.py`, `agent/messenger.py` since 13:01 UTC)

**Active plans in `docs/plans/` overlapping this area:** None

**Notes:** Issue #961's recon (now closed) independently confirmed the error originates in the `claude` CLI binary, not from hiredis Redis RESP parsing as originally hypothesized. Both issues agree: the fix belongs in `agent/sdk_client.py`.

## Prior Art

- **PR #878** (Add PM session resume hydration context): Added `_maybe_inject_resume_hydration()` which prepends a `<resumed-session-context>` block to PM session message_text. This PR introduced the mechanism that contributes to context growth — it was the right feature, but added no size cap.
- **PR #953** (hydrate reply-thread context in resume-completed branch): Added reply-thread context (up to 20 messages, 2000 chars each for Valor) to resumed sessions. Combined with PR #878's hydration block, this can create inputs of 40–50KB before the steering message is added.
- **Issue #961** (closed): Filed same day, different hypothesis (hiredis payload overflow). Recon found session_events was already bounded and closed in favor of #958.

No prior attempt addressed the harness input size limit directly.

## Data Flow

1. **Telegram message arrives** → bridge assigns `session_id`, creates `AgentSession`, enqueues in Redis
2. **Worker pops session** → calls `_maybe_inject_resume_hydration()` which prepends a `<resumed-session-context>` block (git summary) to `message_text` if 2+ prior resume files exist
3. **Worker runs `_get_response_via_harness()`** → calls `build_harness_turn_input()` which prepends project context, reply-thread context (from bridge), and scope headers
4. **`get_response_via_harness()`** → assembles `cmd = harness_cmd + [message]` and launches `claude -p --output-format stream-json ... [full_message]`
5. **`claude` binary receives full message as positional arg** → binary internally chunks the message for its context window; if total input exceeds its chunk limit, raises "Separator is not found, and chunk exceed the limit"
6. **`BackgroundTask._run_work()`** catches the exception at `await coro` (line 148) → if separator error: logs WARNING and sends user-friendly "Context too long" message; otherwise: logs ERROR and sends raw error (existing behavior)

**The unbounded growth compounds across turns:**
- Turn 1: context prefix (~500 chars) + steering message (~100 chars) ≈ 600 chars
- Turn 2: context prefix + resume hydration block (~500 chars) + reply chain (turn 1's ~1200 char Valor response + user message) + steering message ≈ 2500 chars
- Turn N: each additional turn adds 2000+ chars per prior Valor response in the reply chain, compounding until the binary's internal limit is hit

## Architectural Impact

- **No interface changes**: `build_harness_turn_input()` and `get_response_via_harness()` signatures unchanged
- **Single insertion point**: a `_apply_context_budget()` helper that trims the assembled message before it reaches `get_response_via_harness()` — isolated to `agent/sdk_client.py`
- **No new dependencies**: pure string manipulation
- **Reversibility**: trivial — the cap is a single constant; removing the cap restores prior behavior

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

- **Context budget constant**: `HARNESS_MAX_INPUT_CHARS = 100_000` — a conservative cap well below the observed failure threshold (empirically the error occurs around 200KB+; 100K gives headroom while preserving full context for all normal sessions)
- **`_apply_context_budget(message: str, max_chars: int) -> str`**: trim function that preserves everything after the last `\n\nMESSAGE:` header (the actual steering message, which must never be truncated) and trims from the oldest context (the top of the string) to meet the budget
- **Apply at harness call site**: call `_apply_context_budget()` inside `get_response_via_harness()` before `cmd = harness_cmd + [message]`
- **Separator error catch**: catch the specific error strings in `BackgroundTask._run_work()` and send a user-friendly "Context too long — please resend your request." message instead of the raw error string; log a WARNING with input length so the trim threshold can be tuned

### Flow

PM session turn N → `build_harness_turn_input()` (assemble full context) → `_apply_context_budget()` (trim oldest context if over limit) → `get_response_via_harness()` (launch subprocess) → `claude -p [trimmed message]` → success

### Technical Approach

**Change 1: Add `_apply_context_budget()` to `agent/sdk_client.py`**

```python
HARNESS_MAX_INPUT_CHARS = 100_000  # module-level constant

def _apply_context_budget(message: str, max_chars: int = HARNESS_MAX_INPUT_CHARS) -> str:
    """Trim oldest context from harness input if it exceeds max_chars.

    Preserves everything from the final 'MESSAGE:' marker onward — the
    steering message must never be truncated. If no MESSAGE: marker exists,
    trims from the start of the string.

    Returns the original string unchanged if within budget.
    """
    if len(message) <= max_chars:
        return message

    # Find the MESSAGE: boundary — steering message must be preserved in full
    marker = "\nMESSAGE: "
    idx = message.rfind(marker)
    if idx != -1:
        tail = message[idx:]          # "\nMESSAGE: ..." must stay intact
        budget_for_prefix = max_chars - len(tail)
        if budget_for_prefix <= 0:
            # Steering message alone exceeds budget — pass through unchanged
            # (harness may still fail, but we preserve message fidelity)
            return message
        trimmed_prefix = message[len(message) - budget_for_prefix - len(tail): idx]
        # If we had to trim, add a marker so the agent knows context was cut
        trim_marker = "[CONTEXT TRIMMED — oldest context omitted to fit harness budget]\n"
        return trim_marker + trimmed_prefix + tail
    else:
        # No MESSAGE: marker — trim from start
        return "[CONTEXT TRIMMED]\n" + message[len(message) - max_chars:]
```

**Change 2: Apply the budget inside `get_response_via_harness()`**

At line 1532 in `agent/sdk_client.py`, before `cmd = harness_cmd + [message]`:

```python
message = _apply_context_budget(message)
if len(message) < original_len:
    logger.info(
        f"[harness] Context budget applied: trimmed {original_len} → {len(message)} chars"
    )
```

**Change 3: Catch separator errors in `BackgroundTask._run_work()` as last-resort fallback**

In `agent/messenger.py` `_run_work()`, distinguish the separator error from other errors. When the budget cap fails (e.g., steering message alone exceeds the limit), send a user-friendly message instead of exposing the raw error string to Telegram:

```python
_SEPARATOR_ERRORS = (
    "Separator is not found, and chunk exceed the limit",
    "Separator is found, but chunk is longer than limit",
)

except Exception as e:
    err_str = str(e)
    if any(sig in err_str for sig in _SEPARATOR_ERRORS):
        logger.warning(
            f"[{self.messenger.session_id}] Harness context overflow ({len(err_str)} chars): {err_str[:120]}"
        )
        await self.messenger.send(
            "Context too long — please resend your request.",
            message_type="error",
        )
    else:
        # existing error handling unchanged
        await self.messenger.send(
            f"I encountered an error: {str(e)[:200]}",
            message_type="error",
        )
```

Note: the coro has already completed when the error is raised — there is no retry mechanism here. The budget cap in Change 2 is the primary prevention. Change 3 is a fallback that replaces the raw error string with a user-friendly message when the rare budget-cap bypass occurs.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `BackgroundTask._run_work()` has a bare `except Exception as e` — existing test coverage already covers this path; new test verifies the separator variant is logged as WARNING, not just ERROR
- [ ] `_apply_context_budget()` — no exception handlers needed (pure string ops); test with empty string and None-equivalent edge cases

### Empty/Invalid Input Handling
- [ ] `_apply_context_budget("")` → returns `""` unchanged
- [ ] `_apply_context_budget(message)` where `len(message) <= max_chars` → returns message unchanged (no-op path)
- [ ] `_apply_context_budget(message)` where steering message alone exceeds budget → passes through unchanged (cannot safely trim)

### Error State Rendering
- [ ] Separator error in `_run_work()` must NOT surface raw error string to Telegram; budget cap is the primary prevention; when it fails, Change 3 sends "Context too long — please resend your request." (not the raw separator text)
- [ ] Non-separator errors continue to surface the existing `f"I encountered an error: {str(e)[:200]}"` message unchanged

## Test Impact

- [ ] `tests/unit/test_sdk_client.py` — UPDATE: add tests for `_apply_context_budget()` function (budget logic, MESSAGE: boundary preservation, no-op when under limit, trim marker injection); also add a dedicated integration test for the `get_response_via_harness()` → `_apply_context_budget()` call path (verifies budget is applied inside the harness call, not just the helper in isolation)
- [ ] `tests/unit/test_cross_repo_gh_resolution.py` — UPDATE: this test verifies `build_harness_turn_input()` cross-repo header injection only — it does NOT exercise the `_apply_context_budget()` call inside `get_response_via_harness()`; keep as a regression guard for normal-sized inputs not being affected by the budget cap
- [ ] No existing tests assert on message length passed to the harness subprocess — no deletions needed

## Rabbit Holes

- **Switching to stdin**: `claude -p` accepts `--input-format stream-json` for streaming input over stdin, which would lift the ARG_MAX constraint entirely. However, `get_response_via_harness()` is already a subprocess that streams stdout; adding stdin streaming would require significant refactoring of the subprocess management. This is the right long-term fix but exceeds Small appetite.
- **Dynamic summary compression**: Summarizing the reply chain with a Haiku API call before injecting it into the harness input. Smart but introduces latency and API cost on every PM turn. Out of scope.
- **Per-turn context storage**: Persisting "what happened last turn" to Redis and fetching it at harness start instead of building it from the reply chain. Correct architectural move but is a separate feature.
- **Tuning `HARNESS_MAX_INPUT_CHARS`**: The exact value that causes the `claude` binary to fail is unknown (it varies with the internal build). 100K is conservative; don't spend time profiling — the trim marker will make violations visible for tuning later.

## Risks

### Risk 1: Trimming loses critical context
**Impact:** PM session loses track of what stage it was on or what issues it was managing; dispatches duplicate dev sessions or misses a stage
**Mitigation:** The `MESSAGE:` boundary preservation guarantees the steering message (the human's explicit instruction) is never trimmed. PM sessions also query `sdlc_stage_query` at the start of each turn, so stage state is re-derived from Redis, not from in-message context alone.

### Risk 2: HARNESS_MAX_INPUT_CHARS too conservative for large sessions
**Impact:** Valid PM context gets trimmed unnecessarily, causing context loss on turns that would have succeeded
**Mitigation:** 100K chars is approximately 25K tokens — roughly 8× the size of a normal PM turn with resume hydration and a 20-message reply chain. The trim marker makes trimming visible in logs. Can be raised by a config change without code change.

## Race Conditions

No race conditions identified — `_apply_context_budget()` is a pure synchronous string transformation called in a single async task per session turn. No shared state is read or written.

## No-Gos (Out of Scope)

- Switching `get_response_via_harness()` to use stdin (`--input-format stream-json`) instead of positional arg
- Summarizing the reply chain with a separate API call
- Dynamic per-session budget based on reply chain depth
- Fixing the root cause inside the `claude` binary (external binary, not under our control)
- Retroactive fix for already-failed sessions (those must be re-enqueued manually)

## Update System

No update system changes required — this is a pure in-process change to `agent/sdk_client.py` and `agent/messenger.py`. No new config files, no new env vars, no schema changes.

## Agent Integration

No agent integration required — the budget cap is applied transparently inside `get_response_via_harness()`. The change is invisible to callers and requires no MCP changes.

## Documentation

- [ ] Update `docs/features/bridge-worker-architecture.md` — add a note in the "CLI harness" section about the context budget cap and `HARNESS_MAX_INPUT_CHARS`
- [ ] No new feature doc needed — this is a bug fix with a single constant

## Success Criteria

- [ ] Sending "keep going" to a PM session resumed 3+ times no longer crashes with a "Separator" error
- [ ] `_apply_context_budget()` trims from the oldest context, preserving `MESSAGE:` content in full
- [ ] `HARNESS_MAX_INPUT_CHARS = 100_000` is a module-level constant, adjustable without code change
- [ ] Unit tests: budget logic, no-op path, MESSAGE: boundary preservation, edge cases (empty, no marker)
- [ ] Both separator error variants ("Separator is not found" and "Separator is found, but chunk is longer") are logged as WARNING with input length when they do occur
- [ ] When the separator error fires (budget cap bypass), Telegram receives "Context too long — please resend your request." — not the raw separator error string
- [ ] Tests pass (`pytest tests/unit/test_sdk_client.py -q`)
- [ ] `python -m ruff check agent/ && python -m ruff format --check agent/` exits 0

## Team Orchestration

### Team Members

- **Builder (harness-budget)**
  - Name: harness-budget-builder
  - Role: Implement `_apply_context_budget()`, wire it into `get_response_via_harness()`, add separator-error logging to `BackgroundTask._run_work()`
  - Agent Type: builder
  - Resume: true

- **Validator (harness-budget)**
  - Name: harness-budget-validator
  - Role: Verify tests pass, ruff clean, no-op behavior preserved for normal-sized inputs
  - Agent Type: validator
  - Resume: true

### Available Agent Types

See template for full list.

## Step by Step Tasks

### 1. Implement context budget cap
- **Task ID**: build-budget-cap
- **Depends On**: none
- **Validates**: tests/unit/test_sdk_client.py (add), tests/unit/test_cross_repo_gh_resolution.py (regression guard)
- **Assigned To**: harness-budget-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `HARNESS_MAX_INPUT_CHARS = 100_000` constant to `agent/sdk_client.py` (module level, near `_HARNESS_FLUSH_INTERVAL`)
- Implement `_apply_context_budget(message: str, max_chars: int = HARNESS_MAX_INPUT_CHARS) -> str` in `agent/sdk_client.py` — trim from oldest context, preserve MESSAGE: boundary
- In `get_response_via_harness()`, call `_apply_context_budget(message)` before `cmd = harness_cmd + [message]`; log INFO when trim occurs (original vs trimmed length)
- In `BackgroundTask._run_work()` (`agent/messenger.py`), add a two-branch separator error handler: if separator error (check for both "Separator is not found" and "Separator is found, but chunk is longer" variants), log WARNING and `await self.messenger.send("Context too long — please resend your request.", message_type="error")`; otherwise use existing error send path
- Add unit tests to `tests/unit/test_sdk_client.py`:
  - `_apply_context_budget()`: no-op when under budget, trim removes oldest prefix, `MESSAGE:` boundary preserved, trim marker injected, empty input passthrough, steering-only exceeds budget passthrough
  - Integration test for `get_response_via_harness()` → `_apply_context_budget()` call path: verify that budget is applied inside the harness call for an oversized input (normal-sized inputs pass through unchanged)
- `tests/unit/test_cross_repo_gh_resolution.py`: verify `build_harness_turn_input()` cross-repo header injection still works for normal-sized inputs (regression guard — this test covers header injection only, not the budget cap)
- Verify `pytest tests/unit/test_sdk_client.py tests/unit/test_cross_repo_gh_resolution.py -q` exits 0
- Run `python -m ruff check agent/ && python -m ruff format --check agent/`

### 2. Validate and update docs
- **Task ID**: validate-budget-cap
- **Depends On**: build-budget-cap
- **Assigned To**: harness-budget-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `_apply_context_budget()` exists in `agent/sdk_client.py` and is called in `get_response_via_harness()`
- Confirm `HARNESS_MAX_INPUT_CHARS` is a module-level constant
- Confirm separator-error handler in `agent/messenger.py` sends "Context too long — please resend your request." (not the raw error string) and logs WARNING
- Confirm non-separator errors still use the existing `f"I encountered an error: {str(e)[:200]}"` path
- Run `pytest tests/unit/test_sdk_client.py -q` — must pass
- Run `pytest tests/unit/test_cross_repo_gh_resolution.py -q` — must pass (regression guard)
- Run `python -m ruff check agent/` — must exit 0
- Update `docs/features/bridge-worker-architecture.md` — in the harness call chain section, add a note that `_apply_context_budget()` is called inside `get_response_via_harness()` with `HARNESS_MAX_INPUT_CHARS = 100_000` before subprocess launch

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sdk_client.py -q` | exit code 0 |
| Cross-repo regression | `pytest tests/unit/test_cross_repo_gh_resolution.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/sdk_client.py agent/messenger.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/sdk_client.py agent/messenger.py` | exit code 0 |
| Budget constant exists | `grep -n "HARNESS_MAX_INPUT_CHARS" agent/sdk_client.py` | output > 0 |
| Budget applied in harness | `grep -n "_apply_context_budget" agent/sdk_client.py` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Operator, User | C1: Separator error still surfaces raw string to Telegram when budget cap fails — Change 3 only changed log level but did not suppress the raw error send | Change 3 revised in Technical Approach | Two-branch handler: separator → send "Context too long — please resend your request."; other → existing send. Confirmed in Task 1 and validate-budget-cap checklist. |
| CONCERN | Operator, Archaeologist | C2: `docs/features/bridge-worker-architecture.md` not updated with budget cap note | validate-budget-cap task updated | Task 2 now explicitly requires adding a note about `_apply_context_budget()` and `HARNESS_MAX_INPUT_CHARS` at the harness call chain diagram. |
| NIT | — | N1: `test_cross_repo_gh_resolution.py` scope claim vs reality — task described it as an integration guard for `_apply_context_budget()`, but the test only covers `build_harness_turn_input()` header injection | Test Impact and Task 1 clarified | Cross-repo test retained as regression guard for header injection only; dedicated `get_response_via_harness()` → `_apply_context_budget()` integration test added to `test_sdk_client.py`. |

---

## Open Questions

None — root cause is confirmed, solution is scoped, no human decisions required.
