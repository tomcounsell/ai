---
status: merged
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-13
tracking: https://github.com/tomcounsell/ai/issues/929
last_comment_id:
revision_applied: true
---

# Fix: AgentSession.response_delivered_at DatetimeField coercion error stalls PM session pipeline

## Problem

When a PM session's child dev session completes, the worker calls `session.append_event("lifecycle", ...)` to record the transition. This triggers `_append_event_dict()` → `self.save(update_fields=["session_events", "updated_at"])`. Popoto's `is_valid()` runs on **all** fields during save — not just the listed `update_fields`. If `response_delivered_at` holds a value that Popoto cannot coerce to `datetime`, `is_valid()` logs an error and `save()` silently fails.

The failure is silent to the worker: `_append_event_dict` catches the exception and logs a warning, but does not re-raise. The PM session's status transition (`running→waiting_for_children`) is never persisted to Redis. The PM session ends up split-state: `status=running` in Redis, `waiting_for_children` in the worker's in-memory view. The worker will not re-dispatch a `running` session it already handed off, so **the SDLC pipeline stalls permanently after the first stage** with no visible error.

**Current behavior:** `append_event` logs `'DatetimeField' object has no attribute 'strftime'` and the save silently fails, leaving the PM session in `running` state in Redis while the worker thinks it is `waiting_for_children`.

**Desired outcome:** `append_event("lifecycle", ...)` succeeds on any `AgentSession` regardless of `response_delivered_at`'s stored state. PM sessions complete their lifecycle transitions reliably, and the SDLC pipeline progresses through all stages.

## Freshness Check

**Baseline commit:** `a0a55d775dc42350c48e93c52ce4258c10d842df`
**Issue filed at:** 2026-04-13T05:25:37Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `models/agent_session.py:154` — `response_delivered_at = DatetimeField(null=True)` — still holds
- `models/agent_session.py:1228` — `_append_event_dict` partial save — still holds at line 1228
- `models/agent_session.py:453` — `int | float` → `datetime` coercion for `response_delivered_at` — confirmed present at lines 453-458; coercion only fires if the value is `int | float`, not for other bad types
- `agent/agent_session_queue.py:3249` — `response_delivered_at` stamp — confirmed present at line 3249

**Cited sibling issues/PRs re-checked:**
- PR #923 — merged 2026-04-12T15:15:42Z — introduced `response_delivered_at` stamping; this is the PR that introduced the field and triggered the bug

**Commits on main since issue was filed (touching referenced files):**
- None — `git log --oneline --since=2026-04-13T05:25:37Z -- models/agent_session.py agent/agent_session_queue.py` returned empty

**Active plans in `docs/plans/` overlapping this area:** none

**Notes:** The `_normalize_kwargs` coercion path (lines 453-458) only handles `int | float` values. The error occurs with a `DatetimeField` descriptor object — this arises when Popoto's `encode_popoto_model_obj` calls `getattr(obj, field_name)` and the instance attribute was not set (Python returns the class-level descriptor). Confirmed by tracing Popoto's `__init__` → `is_valid()` → `encode_popoto_model_obj` flow.

## Prior Art

- **PR #923** (merged 2026-04-12): "fix: prevent duplicate session execution after health-check recovery" — introduced `response_delivered_at` field on `AgentSession`. The field was stamped in `agent_session_queue.py:3249` using `agent_session.save()` (full save). This PR is the proximate cause of the coercion bug: sessions created before this PR have no `response_delivered_at` value in their Redis hash, so when `_normalize_kwargs` doesn't populate it and the field's `default` is `None`, Popoto's `__init__` sets it to `None` — but only if the field name reaches the default-setting loop. Code inspection reveals that the coercion error reaches `encode_popoto_model_obj` when the value is a non-datetime, non-None type.

No prior closed issues found for this specific coercion pattern.

## Data Flow

1. **Entry point**: Worker detects child dev session completion; calls `session.append_event("lifecycle", "waiting_for_children→running")`
2. **`append_event`** (`models/agent_session.py:1211`): Creates `SessionEvent`, calls `_append_event_dict(event.model_dump())`
3. **`_append_event_dict`** (`models/agent_session.py:1228`): Appends event dict to `self.session_events`, calls `self.save(update_fields=["session_events", "updated_at"])`
4. **`AgentSession.save`** (`models/agent_session.py:296`): Calls `super().save(update_fields=...)` → Popoto `Model.save()`
5. **Popoto `Model.save()`**: Calls `self.is_valid()` — validates **all** fields, not just `update_fields`
6. **`is_valid()` coercion loop** (`popoto/models/base.py:~836`): For each field, if `value is not None and not isinstance(value, field.type)`, tries `field.type(value)` → `datetime(bad_value)` → `TypeError` → returns `False`
7. **`is_valid()` returns `False`** → `save()` aborts without writing to Redis
8. **`_append_event_dict` catch block** catches exception, logs warning — status transition is never persisted
9. **Result**: PM session stuck at `status=running` in Redis; worker cannot re-dispatch

**The coercion trigger**: `encode_popoto_model_obj` calls `getattr(obj, field_name)` for `response_delivered_at`. If the field was populated with a non-`datetime`, non-`None` value (e.g., an old integer timestamp not converted by `_normalize_kwargs`, or the field descriptor itself due to a Popoto `__init__` edge case), `is_valid()` attempts `datetime(value)` which fails.

## Architectural Impact

- **No new dependencies**: Pure Python stdlib / existing Popoto API
- **Interface changes**: None — `append_event`, `_append_event_dict`, and `save` signatures unchanged
- **Coupling**: Decreases fragility — normalizing `response_delivered_at` in `_normalize_kwargs` makes the model more defensive
- **Data ownership**: Unchanged
- **Reversibility**: Trivial — the fix is additive defensive coercion; removing it returns to current behavior

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

- **Extend `__setattr__` coercion**: `AgentSession.__setattr__` (line 305) already handles `int | float` → `datetime`; extend it to also convert `str` ISO timestamps and reset unknown types to `None` — this covers all assignment paths including Popoto's own init loops
- **Extend `_normalize_kwargs` for defence-in-depth**: Add the same `str` and unknown-type handling to the existing coercion block at lines 453-458
- **Add DEBUG log on coercion**: Log at `DEBUG` level when `__setattr__` normalizes a bad `response_delivered_at` value (observability)
- **New unit tests**: Verify `append_event` succeeds when `response_delivered_at` is `None`, an integer timestamp, a `datetime`, a string ISO timestamp, and an unknown bad value

### Technical Approach

**Primary fix — extend `__setattr__`** (`models/agent_session.py`):

`AgentSession.__setattr__` (line 305) is the live guard for **all** attribute assignments, including those made by Popoto's own `__init__` kwargs-apply loop (`setattr(self, field_name, kwargs[field_name])`). The existing guard only converts `int | float` to `datetime`. Extend it to also handle:
- `str` values in `_DATETIME_FIELDS`: attempt `datetime.fromisoformat(value)`, normalize to UTC-aware if naive, fall through to `None` on parse failure
- Any other non-`datetime` type in `_DATETIME_FIELDS`: reset to `None` (field is `null=True`, so `None` is always valid)
- Add `logger.debug(f"AgentSession: coerced {name}={value!r} → None (bad type {type(value).__name__})")` when resetting to `None`

This ensures that **any assignment path** — construction, Redis load, or direct set — always leaves `response_delivered_at` (and all other `_DATETIME_FIELDS`) as a `datetime` or `None`.

**Why `__setattr__` is the right fix location**: Popoto's `Model.__init__` always:
1. Calls `setattr(self, field_name, default_value)` for all fields (defaults loop) — invokes `AgentSession.__setattr__`
2. Calls `setattr(self, field_name, kwargs[field_name])` for provided kwargs (kwargs-apply loop) — also invokes `AgentSession.__setattr__`

Since both loops go through `__setattr__`, fixing `__setattr__` covers all construction and load paths. `_normalize_kwargs` is called only during `AgentSession.__init__` and `AgentSession.create()` and does not cover post-construction assignments.

**Secondary fix — extend `_normalize_kwargs`** (`models/agent_session.py`):

Extend the `response_delivered_at` branch at lines 453-458 to also handle `str` ISO timestamps and unknown types (reset to `None`), for defence-in-depth at the construction callsite. Same logic as `__setattr__` extension.

**`__init__` post-init guard — NOT NEEDED** (critique concern resolved):

> **Critique concern:** The `__init__` post-init guard using `object.__getattribute__` may be solving a non-reachable code path.

**Verified unreachable.** Code inspection of `popoto/models/base.py:Model.__init__` confirms: the defaults loop sets `None` via `setattr`, then the kwargs-apply loop re-sets the Redis-decoded value via `setattr` — both pass through `AgentSession.__setattr__`. Since `__setattr__` is the fix location, no non-datetime, non-None value can survive construction after the fix. The `object.__getattribute__` / `object.__setattr__` post-init guard described in the original plan sketch is **removed from scope** — it is unnecessary and would bypass Popoto's descriptor protocol for no benefit.

**`__setattr__` scope clarification** (critique nit resolved):

The fix extends the **existing** `AgentSession.__setattr__` override (line 305). No new `__setattr__` class is introduced — only the method body is extended to handle `str` and unknown types for all `_DATETIME_FIELDS`. No descriptor-protocol bypasses needed.

**No Popoto upstream change required**: the fix lives entirely in `AgentSession.__setattr__` and `_normalize_kwargs`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_append_event_dict` catches all exceptions at `models/agent_session.py:1250` — existing test `test_nudge_stomp_regression.py` covers this path but does not exercise the coercion failure scenario. New test will assert the save succeeds (no warning logged) when `response_delivered_at` is in a bad state.

### Empty/Invalid Input Handling
- [ ] `response_delivered_at=None` must not raise — already passes with current code
- [ ] `response_delivered_at=<int>` (Unix timestamp) — handled by existing `__setattr__` coercion; new test asserts no coercion error
- [ ] `response_delivered_at=<str "bad">` (invalid string) — new `__setattr__` path; new test asserts reset to `None`
- [ ] `response_delivered_at=<str ISO timestamp>` (valid string) — new `__setattr__` path; new test asserts conversion to UTC-aware `datetime`
- [ ] `response_delivered_at=<DatetimeField descriptor>` — covered by the "unknown type → None" branch in `__setattr__`; test asserts reset to `None`

### Error State Rendering
- No user-visible output changes — this is a persistence fix
- Worker logs will no longer emit `append_event save failed` warnings for this scenario

## Test Impact

- [ ] `tests/unit/test_agent_session_queue.py` — UPDATE: existing `response_delivered_at` health-check tests (lines 379-446) are not affected by this fix (they test health-check logic, not coercion). No changes needed to existing assertions.
- [ ] `tests/integration/test_nudge_stomp_regression.py::test_stale_append_event_preserves_status_and_auto_continue_count` — UPDATE: this test creates a session with default `response_delivered_at=None`. After the fix, the test should still pass. Verify no regression.
- [ ] `tests/integration/test_lifecycle_transition.py::test_append_event_dict_uses_partial_save` — UPDATE: verify this test still passes after the fix.

## Rabbit Holes

- **Fixing Popoto's `is_valid()` to only validate `update_fields`**: This would require an upstream Popoto change and is out of scope. The fix in `AgentSession` is sufficient and safer.
- **Scanning all sessions in Redis and cleaning up bad `response_delivered_at` values**: A migration script is not necessary because the defensive coercion in `_normalize_kwargs` / `__init__` handles it at load time.
- **Adding `update_fields` filtering to `is_valid()`**: Popoto doesn't support this; the correct fix is normalizing the field value before save.

## Risks

### Risk 1: `object.__getattribute__` bypasses AgentSession's own descriptor
**Impact:** If `AgentSession` has any custom `__getattribute__` magic for `response_delivered_at`, the guard could bypass it.
**Mitigation:** `AgentSession` has no custom `__getattribute__`. The guard is a one-line defensive check; using `object.__getattribute__` directly ensures we see the raw stored value, not a lazily-decoded one.

### Risk 2: Coercion of string ISO timestamps could introduce wrong timezone
**Impact:** If a string timestamp lacks timezone info, `datetime.fromisoformat()` returns a naive datetime; Popoto may then store it as-is. Downstream comparisons with UTC-aware datetimes could break.
**Mitigation:** After parsing, normalize to UTC-aware: `dt.replace(tzinfo=UTC)` if `dt.tzinfo is None`. Fall through to `None` on any parse failure.

## Race Conditions

No race conditions identified — the fix is applied in `__init__` and `_normalize_kwargs`, which run single-threaded during object construction and before any concurrent access.

## No-Gos (Out of Scope)

- Fixing all other DatetimeField coercion hazards system-wide — only `response_delivered_at` is broken today
- Adding a Redis migration script to clean up old records
- Changing Popoto's `is_valid()` behavior upstream

## Update System

No update system changes required — this feature is purely internal. No new dependencies, config files, or deployment steps.

## Agent Integration

No agent integration required — this is a model-layer bug fix. No MCP servers, `.mcp.json`, or bridge changes needed.

## Documentation

- [ ] Update `docs/features/agent-session-model.md` to document the `response_delivered_at` coercion guard in `_normalize_kwargs` / `__init__` — what it protects against (descriptor-leak on old Redis records lacking the field) and the normalization rules (int/float → datetime, str ISO → datetime, other non-datetime → None)
- [ ] Add an inline docstring to `AgentSession._normalize_kwargs` explaining why `response_delivered_at` gets extra coercion beyond `int | float`

## Success Criteria

- [ ] `append_event("lifecycle", ...)` succeeds on sessions with `None`, integer, or datetime values for `response_delivered_at`
- [ ] PM session lifecycle transitions (`running→waiting_for_children`, `waiting_for_children→running`) persist to Redis correctly
- [ ] New unit test: `test_append_event_succeeds_with_bad_response_delivered_at` in `tests/unit/test_agent_session_queue.py` — passes with `response_delivered_at` set to `None`, int, datetime, and field descriptor
- [ ] No regressions in `tests/unit/test_agent_session_*.py` and `tests/integration/test_nudge_stomp_regression.py`
- [ ] Tests pass (`/do-test`)
- [ ] `python -m ruff check .` clean

## Team Orchestration

### Team Members

- **Builder (coercion-fix)**
  - Name: coercion-builder
  - Role: Implement defensive coercion in `_normalize_kwargs` and `__init__`, plus new unit tests
  - Agent Type: builder
  - Resume: true

- **Validator (coercion-fix)**
  - Name: coercion-validator
  - Role: Verify all acceptance criteria, run full test suite
  - Agent Type: validator
  - Resume: true

### Available Agent Types

See plan template for full list.

## Step by Step Tasks

### 1. Implement defensive coercion in AgentSession
- **Task ID**: build-coercion
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_queue.py, tests/integration/test_nudge_stomp_regression.py, tests/integration/test_lifecycle_transition.py
- **Assigned To**: coercion-builder
- **Agent Type**: builder
- **Parallel**: true
- Extend `AgentSession.__setattr__` in `models/agent_session.py` (line 305): after the existing `int | float` branch for `_DATETIME_FIELDS`, add handling for `str` (attempt `datetime.fromisoformat`, normalize to UTC-aware if naive, fall to `None` on failure) and any other non-`datetime` type (reset to `None`); add `logger.debug(...)` when normalizing a bad value
- Extend `_normalize_kwargs` in `models/agent_session.py` (lines 453-458): add the same `str` and unknown-type handling for `response_delivered_at` as defence-in-depth at the construction callsite
- Do NOT add a post-init guard to `__init__` — verified unreachable; see Technical Approach for rationale
- Add unit test `test_append_event_succeeds_with_bad_response_delivered_at` in `tests/unit/test_agent_session_queue.py` covering: `None`, int Unix timestamp, a `datetime`, a string ISO timestamp (valid), and a non-datetime/non-None value (e.g., the string `"bad"`)
- Verify no regressions in `tests/unit/test_agent_session_queue.py` (health-check tests), `tests/integration/test_nudge_stomp_regression.py`, and `tests/integration/test_lifecycle_transition.py`

### 2. Validate fix
- **Task ID**: validate-coercion
- **Depends On**: build-coercion
- **Assigned To**: coercion-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_agent_session_queue.py tests/integration/test_nudge_stomp_regression.py tests/integration/test_lifecycle_transition.py -x -q`
- Verify `python -m ruff check . && python -m ruff format --check .` passes
- Confirm new test covers all four `response_delivered_at` states (None, int, datetime, bad value)
- Confirm no `append_event save failed` warning is logged in any of the new test scenarios

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-coercion
- **Assigned To**: coercion-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-session-model.md` to document the `response_delivered_at` coercion guard: what it protects against and the normalization rules
- Add an inline docstring to `AgentSession._normalize_kwargs` explaining the extended coercion

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: coercion-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` (full suite)
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Coercion guard present | `grep -n "not isinstance.*datetime" models/agent_session.py` | output > 0 |
| New test exists | `grep -n "test_append_event_succeeds_with_bad_response_delivered_at" tests/unit/test_agent_session_queue.py` | output > 0 |
| No stale worker log | `grep "append_event save failed" logs/worker.log` | no matches after fix deployed |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Archaeologist | `__init__` post-init guard using `object.__getattribute__` may be solving an unreachable code path — Popoto `__init__` always sets defaults, so descriptor-leak cannot survive construction | Technical Approach: verified unreachable; guard removed from scope; `__setattr__` extension handles all paths | Verified by inspecting `popoto/models/base.py:Model.__init__` — defaults loop + kwargs-apply loop both go through `AgentSession.__setattr__` |
| NIT | Operator | No DEBUG log when `_normalize_kwargs` coerces a bad value — makes silent normalization hard to observe | Technical Approach + Task 1: `logger.debug(...)` added to `__setattr__` coercion path | Log emitted when any `_DATETIME_FIELDS` value is reset to `None` due to bad type |
| NIT | Operator | Worker log success criterion missing from Verification table | Verification table: added "No stale worker log" row | `grep "append_event save failed" logs/worker.log` should return no matches after deploy |
| NIT | Simplifier | `__setattr__` override scope unclear — plan mentioned a new override class | Technical Approach: clarified the fix extends the **existing** `__setattr__` at line 305 | No new `__setattr__` class; only method body extended |

---

## Open Questions

None — root cause is confirmed by code inspection. The fix approach is straightforward and does not require human input.
