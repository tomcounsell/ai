---
status: docs_complete
type: bug
appetite: Small
owner: Valor
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/761
last_comment_id:
---

# Fix _pop_agent_session tests and extraction helper docstrings

## Problem

The `_pop_agent_session()` function in `agent/agent_session_queue.py` was refactored
to use in-place mutation via `transition_status()` (line 524) instead of the old
delete-and-recreate pattern. Integration tests and module docstrings were not
updated, so ~13 tests still assert delete-and-recreate semantics and fail.

**Current behavior:**
- `tests/integration/test_agent_session_queue_race.py::test_pop_agent_session_preserves_fields`
  (and peer tests across 5 files) asserts `session.agent_session_id != original.agent_session_id`,
  which only holds if a new record was created. With in-place mutation the ID is stable,
  so the assertion fails.
- Several test docstrings describe a "delete-and-recreate pattern used by
  `_pop_agent_session`" that no longer exists, creating drift between code and
  test intent.
- Separately, `_extract_agent_session_fields` is used by three callers that still
  use delete-and-recreate (retry L762, orphan fix L1219, continuation fallback L1916).
  Issue #761 flags `message_text` and `scheduling_depth` as missing from
  `_AGENT_SESSION_FIELDS`.

**Desired outcome:**
- All 13 currently-failing integration tests pass.
- Tests assert current in-place mutation semantics (same `agent_session_id`,
  status changed to `running`).
- Test/module docstrings no longer refer to "delete-and-recreate" for the pop path.
- Remaining delete-and-recreate callers preserve `message_text` data correctly.

## Prior Art

- **Issue #700**: Completed sessions revert to pending, causing infinite execution
  loop — fixed the zombie bug that motivated the lifecycle refactor, but left
  tests asserting the pre-refactor delete-and-recreate contract for
  `_pop_agent_session`.
- **Issue #716 / #715**: Follow-up quality stages for the zombie fix — addressed
  some test gaps but did not catch the `_pop_agent_session` contract drift.
- **Issue #714 / #713**: Follow-up integration tests and retrospective review for
  the session zombie fix — same scope, did not reach the pop-path tests.

No prior PR has attempted this specific fix. This is a straightforward test
alignment + docstring cleanup.

## Data Flow

1. **Worker loop** calls `_pop_agent_session(chat_id)`.
2. **Filter pending sessions** for the chat, drop future-scheduled ones.
3. **Pick highest priority FIFO** — the chosen `AgentSession` instance.
4. **Mutate in place**: set `started_at`, call `transition_status(chosen, "running")`.
   Popoto saves the record; the `status` index is updated because it is an
   `IndexedField`, not a `KeyField`. **`agent_session_id` is unchanged.**
5. **Steering drain**: pop steering messages and prepend into `chosen.message_text`
   (which writes through to `initial_telegram_message["message_text"]`), then
   `async_save`.
6. **Return** the same instance the caller can act on.

Separately, the three delete-and-recreate callers still in the codebase
(retry, orphan fix, continuation) use `_extract_agent_session_fields` to copy
field values before deleting and recreating. Because
`_AGENT_SESSION_FIELDS` already includes `initial_telegram_message`, and
`message_text` is a virtual property backed by that dict, `message_text` is
preserved automatically by copying the dict. The issue's framing in Bug 2 is
a misread — the field is in fact preserved today. What is missing is a clarifying
comment/test asserting this behavior.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none. `_pop_agent_session` runtime behavior must NOT change.
- **Coupling**: unchanged.
- **Data ownership**: unchanged.
- **Reversibility**: trivial — this is test and docstring work.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Pure test alignment with no production-code behavior change. Appetite is dominated
by the number of affected test files (5) and the need to not accidentally regress
any other behavior they cover.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Test rewrite**: Update tests that assert delete-and-recreate semantics of
  `_pop_agent_session` to assert in-place mutation semantics.
- **Docstring cleanup**: Fix module/class docstrings in affected test files that
  claim `_pop_agent_session` uses delete-and-recreate.
- **Field-extraction clarification**: Add a comment on `_AGENT_SESSION_FIELDS`
  noting that `message_text` is preserved via `initial_telegram_message`, and add
  a focused unit test asserting round-trip preservation of `message_text` through
  `_extract_agent_session_fields`.
- **Scheduling_depth**: Do NOT add to `_AGENT_SESSION_FIELDS` — it is a computed
  property derived by walking the parent chain and is explicitly marked
  "ignored, now derived" in `_push_agent_session` kwargs. Tests asserting
  `scheduling_depth` appears in the extracted dict should be removed or adjusted
  to walk the parent chain instead.

### Flow

Failing test run → update assertions and docstrings → re-run → green.

### Technical Approach

For each failing test in the 5 target files:
1. Change assertions of the form `new.agent_session_id != original.agent_session_id`
   to `new.agent_session_id == original.agent_session_id`.
2. Change any lookups that re-query by the "new ID" to query by the original ID
   and expect a single record with `status == "running"`.
3. Update docstrings that describe the pop path as "delete-and-recreate" to
   describe it as "in-place mutation via `transition_status()`".
4. Leave docstrings and tests referring to delete-and-recreate for OTHER paths
   (retry, orphan fix, continuation, kill, complete_transcript) untouched — those
   paths still use the pattern legitimately.

For `_extract_agent_session_fields`:
1. Add an inline comment on `_AGENT_SESSION_FIELDS` explaining that
   `message_text` is preserved via the `initial_telegram_message` dict.
2. Add a unit test round-tripping a session with a non-empty `message_text`
   through `_extract_agent_session_fields` and `AgentSession.create(**fields)`,
   asserting the new record's `.message_text` matches.

## Failure Path Test Strategy

### Exception Handling Coverage
- No new exception handlers are added. The existing `try/except` around
  steering drain in `_pop_agent_session` (L530-550) is not in scope for this
  work — it already logs on failure.

### Empty/Invalid Input Handling
- `_pop_agent_session(chat_id)` already returns `None` for empty pending queues.
  A test asserting this branch already exists — verify it still passes.
- Round-trip test for `_extract_agent_session_fields` will include a case where
  `message_text` is `None` (no initial_telegram_message dict) to confirm the
  extraction does not raise.

### Error State Rendering
- Not applicable — no user-visible output.

## Test Impact

- [ ] `tests/integration/test_agent_session_queue_race.py::test_pop_agent_session_preserves_fields` — UPDATE: assert same `agent_session_id` and `status == "running"` after pop.
- [ ] `tests/integration/test_agent_session_queue_race.py` line 88 (`assert new_job.agent_session_id != original.agent_session_id`) — UPDATE: flip assertion.
- [ ] `tests/integration/test_agent_session_queue_race.py` module docstring (line 3: "delete-and-recreate pattern used by _pop_agent_session") — UPDATE: describe in-place mutation.
- [ ] `tests/integration/test_agent_session_queue_race.py` class docstring (line 94) — UPDATE: remove delete-and-recreate language for the pop path.
- [ ] `tests/integration/test_agent_session_scheduler.py` — UPDATE: any assertions on `_pop_agent_session` behavior that expect a new ID.
- [ ] `tests/integration/test_lifecycle_transition.py` — UPDATE: pop-path assertions only; leave L176/L191 (`complete_transcript` delete-and-recreate) as-is.
- [ ] `tests/integration/test_agent_session_health_monitor.py` — UPDATE: pop-path assertions.
- [ ] `tests/integration/test_silent_failures.py` — UPDATE: pop-path assertions.
- [ ] `tests/unit/test_agent_session_queue.py` (create if missing) — ADD: round-trip unit test for `_extract_agent_session_fields` preserving `message_text` via `initial_telegram_message`.
- [ ] Any test asserting `scheduling_depth` appears in the extracted field dict — DELETE or adjust to derive via parent walk.

## Rabbit Holes

- **Do NOT refactor `_pop_agent_session` itself.** The production behavior is
  correct; only tests and docstrings are wrong.
- **Do NOT remove the `_extract_agent_session_fields` helper or the
  delete-and-recreate pattern from retry/orphan-fix/continuation paths.** Those
  paths legitimately need to change KeyFields (`parent_agent_session_id`) or
  create fresh records with new auto IDs.
- **Do NOT add `message_text` as a top-level field on the model.** It is
  intentionally a virtual property over `initial_telegram_message`; flattening
  it would require a schema migration and is out of scope.
- **Do NOT attempt to add `scheduling_depth` to `_AGENT_SESSION_FIELDS`.** It is
  derived by walking the parent chain and cannot be set at create time.

## Risks

### Risk 1: Test updates mask a real production bug
**Impact:** If a test was failing because it caught a real bug (not because it
asserted stale semantics), flipping the assertion would hide that bug.
**Mitigation:** Before flipping each assertion, read the test body and verify it
is testing the contract described in the current `_pop_agent_session` docstring.
If in doubt, write a separate sibling test for the OLD assertion and verify it
fails for the right reason before deleting it.

### Risk 2: Delete-and-recreate callers silently lose `message_text`
**Impact:** If the retry path (L762) reconstructs a session without
`initial_telegram_message` populated, the agent loses the user's original text.
**Mitigation:** The round-trip unit test added in this plan will cover that the
dict is in fact preserved. Verify the retry path by reading L762-790 during build
and asserting no field is dropped.

## Race Conditions

No new race conditions introduced. The existing race coverage in
`test_agent_session_queue_race.py` must continue to pass after the test updates —
the property being tested (concurrent pops don't double-assign) is independent
of whether mutation is in-place or delete-and-recreate.

## No-Gos (Out of Scope)

- Refactoring or removing the delete-and-recreate pattern in retry / orphan fix /
  continuation paths.
- Flattening `message_text` into a top-level model field.
- Adding `scheduling_depth` to the extraction helper.
- Any changes to `_pop_agent_session`'s runtime behavior.

## Update System

No update system changes required — this is a test and docstring cleanup,
internal to the repo.

## Agent Integration

No agent integration required — this is a worker/queue internal change.
No MCP tools, bridge imports, or `.mcp.json` changes are affected.

## Documentation

- [ ] Update the module docstring of `agent/agent_session_queue.py` around
  `_AGENT_SESSION_FIELDS` (lines 71-123) to clarify which callers use
  delete-and-recreate (retry, orphan fix, continuation) and why
  `_pop_agent_session` does NOT.
- [ ] Update `docs/features/popoto-index-hygiene.md` (if it references
  `_pop_agent_session`) to reflect the in-place mutation pattern. If it does not
  mention `_pop_agent_session`, no change is needed.
- [ ] No new feature doc needed — this is a bugfix, not a new capability.
- [ ] No external docs site changes — internal bugfix.

## Success Criteria

- [ ] All 13 previously-failing integration tests in `test_agent_session_queue_race.py`,
  `test_agent_session_scheduler.py`, `test_lifecycle_transition.py`,
  `test_agent_session_health_monitor.py`, and `test_silent_failures.py` pass.
- [ ] New unit test asserts `_extract_agent_session_fields` round-trips
  `message_text` via `initial_telegram_message`.
- [ ] No remaining test docstring or module docstring describes
  `_pop_agent_session` as "delete-and-recreate".
- [ ] No Redis warnings of the form "one or more redis keys points to missing
  objects" appear in the test log.
- [ ] `_pop_agent_session` production behavior is byte-identical before and
  after the change (diff touches only tests, docstrings, and the comment on
  `_AGENT_SESSION_FIELDS`).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (tests-and-docstrings)**
  - Name: pop-test-builder
  - Role: Update the 5 test files and module docstrings; add the round-trip unit test.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (tests-and-docstrings)**
  - Name: pop-test-validator
  - Role: Run the affected test files and assert all previously-failing tests pass; grep for leftover "delete-and-recreate" references to `_pop_agent_session`.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update test assertions in 5 files
- **Task ID**: build-update-pop-tests
- **Depends On**: none
- **Validates**: `tests/integration/test_agent_session_queue_race.py`, `tests/integration/test_agent_session_scheduler.py`, `tests/integration/test_lifecycle_transition.py`, `tests/integration/test_agent_session_health_monitor.py`, `tests/integration/test_silent_failures.py`
- **Assigned To**: pop-test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Read each file, identify assertions that expect a new `agent_session_id`
  after `_pop_agent_session` (or that query by a stale ID).
- Flip the assertions to expect the same `agent_session_id` and
  `status == "running"`.
- Leave tests covering the OTHER delete-and-recreate paths (retry, orphan fix,
  continuation, kill, complete_transcript) untouched.

### 2. Update docstrings that mention `_pop_agent_session` delete-and-recreate
- **Task ID**: build-update-docstrings
- **Depends On**: none
- **Validates**: `grep -rn 'delete-and-recreate.*_pop_agent_session\|_pop_agent_session.*delete-and-recreate' tests/` returns nothing.
- **Assigned To**: pop-test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Update the module docstring in `test_agent_session_queue_race.py` (L3) and
  class docstring (L94) to describe in-place mutation.
- Grep for any other test docstring claiming `_pop_agent_session` uses
  delete-and-recreate and update.

### 3. Clarify `_AGENT_SESSION_FIELDS` comment and add round-trip test
- **Task ID**: build-field-comment-and-test
- **Depends On**: none
- **Validates**: new test in `tests/unit/test_agent_session_queue.py` (create if missing).
- **Assigned To**: pop-test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Add a comment on `_AGENT_SESSION_FIELDS` (L73 in `agent/agent_session_queue.py`)
  explicitly noting that `message_text` is preserved via `initial_telegram_message`
  and that `scheduling_depth` is intentionally omitted because it is derived.
- Add a unit test that: creates an `AgentSession` with `message_text="hello"`,
  extracts fields, creates a new record from the extracted dict, and asserts the
  new record's `.message_text == "hello"`. Include a `message_text=None` case.

### 4. Validate
- **Task ID**: validate-all
- **Depends On**: build-update-pop-tests, build-update-docstrings, build-field-comment-and-test
- **Assigned To**: pop-test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_agent_session_queue_race.py tests/integration/test_agent_session_scheduler.py tests/integration/test_lifecycle_transition.py tests/integration/test_agent_session_health_monitor.py tests/integration/test_silent_failures.py -q`.
- Run `pytest tests/unit/test_agent_session_queue.py -q`.
- Run `git diff agent/agent_session_queue.py` and confirm the only non-comment
  change is the comment on `_AGENT_SESSION_FIELDS` — no runtime behavior change.
- Grep for remaining `_pop_agent_session.*delete-and-recreate` references.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Affected integration tests pass | `pytest tests/integration/test_agent_session_queue_race.py tests/integration/test_agent_session_scheduler.py tests/integration/test_lifecycle_transition.py tests/integration/test_agent_session_health_monitor.py tests/integration/test_silent_failures.py -q` | exit code 0 |
| Round-trip unit test passes | `pytest tests/unit/test_agent_session_queue.py -q` | exit code 0 |
| No stale pop-path docstrings | `grep -rn '_pop_agent_session.*delete-and-recreate\|delete-and-recreate.*_pop_agent_session' tests/` | exit code 1 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| `_pop_agent_session` runtime unchanged | `git diff agent/agent_session_queue.py -- :!agent/agent_session_queue.py` shows only comment changes | manual review |

## Critique Results

**Verdict:** READY TO BUILD
**Findings:** 4 total (0 blockers, 3 concerns, 1 nit)

### Concerns

#### C1. Plan contradicts issue framing of Bug 2 without updating the issue
- **Critics:** Archaeologist, User
- **Location:** Data Flow (L70-74), No-Gos
- **Finding:** Issue #761 frames Bug 2 as `_extract_agent_session_fields` losing `message_text` for retry/orphan/continuation paths. The plan (correctly, verified against `models/agent_session.py:462` where `message_text` is a virtual property over `initial_telegram_message`) calls this a misread and scopes the fix down to a clarifying comment + round-trip unit test. This is the right call technically, but the plan does not propose updating issue #761 or commenting on it to explain the scope reduction — future readers auditing the issue will think Bug 2 was silently dropped.
- **Suggestion:** Add a task (or note under "Documentation") to post a comment on #761 stating that the field-extraction helper already preserves `message_text` via `initial_telegram_message` and link the round-trip unit test as evidence.

#### C2. "13 failing tests" is unverified and never enumerated
- **Critics:** Skeptic, Operator
- **Location:** Problem, Success Criteria (L244-246)
- **Finding:** The plan repeatedly cites "all 13 currently-failing integration tests" but never lists them by node ID, and no prerequisite task runs `pytest --collect-only` or a failure-capture command to baseline the set. If the actual failing count is different (12, 15), the validator task has no ground truth, and a test that was failing for an unrelated reason could be "fixed" by flipping an assertion it shouldn't flip. Risk 1 mentions this concern but the mitigation ("read the test body and verify") is manual and easy to skip under pressure.
- **Suggestion:** Add a pre-flight task that runs the 5 affected test files, captures the failing node IDs to a scratch file, and makes the validator task assert that exact set (no more, no less) is now green. This turns Risk 1 mitigation into a mechanical check.

#### C3. Validator's diff check is too narrow
- **Critics:** Adversary, Operator
- **Location:** Verification table, Task 4 validate-all
- **Finding:** The verification row `git diff agent/agent_session_queue.py` only checks for comment-only changes to that one file. But the plan also touches `agent/agent_session_queue.py` module docstring (per Documentation L232-235), and nothing verifies that NO production file outside `agent/agent_session_queue.py` was modified. A test-engineer builder could unintentionally "fix" a test by editing, say, `models/session_lifecycle.py` or `agent/output_router.py`, and the validator would not catch it.
- **Suggestion:** Tighten to `git diff --stat` on the full working tree and whitelist expected paths: the 5 test files, `tests/unit/test_agent_session_queue.py`, and `agent/agent_session_queue.py` (comment-only). Fail validation if any other path appears.

### Nits

#### N1. Open Question 2 should be resolved before build, not during
- **Critics:** Skeptic
- **Location:** Open Questions (L351-355)
- **Finding:** The "redis keys points to missing objects" warning is listed as a success criterion (L251-252) but is also an open question ("is that a separate bug, or will it disappear?"). If it's a separate bug, the success criterion is unachievable via this plan alone.
- **Suggestion:** Either (a) downgrade the success criterion to "no NEW warnings appear" with a baseline capture, or (b) resolve the open question before starting the build by grepping recent test logs for that warning string.

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and non-empty |
| Task numbering | PASS | Tasks 1-4 sequential |
| Dependencies valid | PASS | Task 4 depends on tasks 1-3 (all defined) |
| File paths exist | PASS | All 5 integration test files + `agent/agent_session_queue.py` verified; `tests/unit/test_agent_session_queue.py` correctly marked "create if missing" |
| Prerequisites met | N/A | Plan declares no prerequisites |
| Cross-references | PASS | Success criteria map to tasks; No-Gos do not appear in Solution |

**Verified source citations:**
- `agent/agent_session_queue.py:524` — `transition_status(chosen, "running", ...)` confirmed as in-place mutation (not delete-and-recreate)
- `agent/agent_session_queue.py:73-123` — `_AGENT_SESSION_FIELDS` includes `initial_telegram_message` (L85), confirming plan's claim that `message_text` is preserved transitively
- `models/agent_session.py:462` — `message_text` is a `@property` reading from `initial_telegram_message`, confirming plan's correction of Bug 2's framing
- `tests/integration/test_agent_session_queue_race.py:3` — Module docstring references "delete-and-recreate pattern used by _pop_agent_session" (stale, as plan claims)
- `tests/integration/test_agent_session_queue_race.py:88, 138` — Stale `agent_session_id !=` assertions confirmed

**Verdict rationale:** No blockers found. The plan is technically sound, correctly scopes down an issue-level misread, and has a well-defined rollback profile (test/docstring-only changes). The three concerns are hygiene improvements that should be folded into the build but do not prevent it. Proceed to `/do-build`.

---

## Open Questions

1. Should the clarifying comment on `_AGENT_SESSION_FIELDS` also explicitly list
   which call sites still use delete-and-recreate (retry L762, orphan fix L1219,
   continuation L1916)? This would help future readers but risks staleness as
   line numbers drift.
2. The issue mentions "Redis logs show 'one or more redis keys points to missing
   objects' in some paths where delete-and-recreate is still used with incomplete
   field extraction." Is that warning a symptom of a separate bug in the retry
   or orphan-fix path that should be diagnosed here, or is it expected noise that
   will disappear once the pop-path tests stop triggering the wrong code path?
