---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-02
tracking: https://github.com/tomcounsell/ai/issues/1838
last_comment_id:
---

# deliver_sync no-loop return contract

## Problem

`tests/unit/granite_container/test_bridge_adapter.py::TestDeliverSyncReturnsBool::test_no_loop_returns_false`
fails on `main`:

```
AssertionError: True is not false
WARNING agent.granite_container.bridge_adapter:bridge_adapter.py:1107
  [bridge-adapter] no captured event loop for send_cb; re-enqueueing to outbox (loop=None)
```

When `_deliver_sync` finds no live event loop, it re-enqueues the payload to the
outbox and returns the recovery result (`True` when the outbox re-enqueue
succeeds). The test asserts the no-loop path returns `False`.

**Current behavior:** The granite unit suite does not exit 0 on `main`
(`pytest tests/unit/granite_container/ -n0`) solely because of this one test.
The outbox re-enqueue itself works. It is a stale test assertion, not a
functional defect.

**Desired outcome:** The test asserts the correct return contract, the granite
unit suite is green on `main`, and the `_deliver_sync` return semantics are
documented so the mismatch cannot recur.

## Freshness Check

**Baseline commit:** `f8eac988` (working tree on branch
`chore/security-bumps-msgpack-pydantic-settings`; `bridge_adapter.py` is clean
vs `main`)
**Issue filed at:** 2026-07-01T17:48:22Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/granite_container/bridge_adapter.py:~928` (issue's cited line) — the
  no-loop re-enqueue-and-`return recovered` block — **drifted to lines
  1100-1114**. Claim still holds exactly: `loop is None or loop.is_closed()` →
  `recovered = self._enqueue_to_outbox(...)` → `self._record_delivery_event(...,
  recovered=recovered)` → `return recovered`.
- `tests/unit/granite_container/test_bridge_adapter.py:786-795` —
  `test_no_loop_returns_false` sets `adapter._loop = None` and asserts
  `assertFalse(result)`. Confirmed present and still failing (reproduced:
  `1 failed`, `AssertionError: True is not false`).

**Cited sibling issues/PRs re-checked:**
- #1837 — surfacing harness (granite failure-simulation, Wave 1). Left this
  pre-existing failure untouched by design (test-only constraint). Still the
  correct framing.
- #1647 / #1644 (PR d005aaa2) — introduced the `_deliver_sync` return-bool
  contract and this test, with the no-loop path returning `False`.
- #1805 / #1812 (PR 8ef8beaa) — **intentionally** replaced the no-loop
  `return False` with `recovered = self._enqueue_to_outbox(...); return
  recovered` (and swapped `_record_delivery_failure` → `_record_delivery_event`
  and the log wording). This is the change that made the test stale. The test
  was not updated in that PR.

**Commits on main since issue was filed (touching bridge_adapter.py):**
- `b624607b` per-role transport hedge (#1842) — did not touch the no-loop return
  path.
- `0297da0d` hook-driven turn returns (#1688) — did not touch the no-loop return
  path.

Neither commit altered the `_deliver_sync` return contract; only line numbers
drifted.

**Active plans in `docs/plans/` overlapping this area:** None modify the
`_deliver_sync` return contract or this test.
`correctness-delivery-integrity.md` (status: Ready) covers delivery integrity
broadly but does not reference `_deliver_sync`, the no-loop path, or this test.

**Notes:** Root cause unchanged since #1805 landed. The corrected line reference
(1100-1114, not ~928) is used throughout this plan.

## Prior Art

- **PR #1651 (issues #1644, #1647)** — Added the `_deliver_sync` return-bool
  contract ("True on confirmed delivery, False on failure") and the
  `TestDeliverSyncReturnsBool` suite, including `test_no_loop_returns_false`. At
  that time the no-loop path returned `False`, so the assertion was correct.
- **PR #1812 (issue #1805)** — "re-enqueue timed-out deliveries." Changed the
  no-loop path (and the loop-closed and timeout paths) to re-enqueue to the
  outbox and `return recovered`, so a reply is never silently lost. The intent
  was explicit and correct; the companion test assertion was not updated, which
  is the whole of issue #1838.
- **Issue #1837** — Granite failure-simulation harness (Wave 1) surfaced this
  pre-existing failure and filed #1838 rather than fixing it in-place.

## Why Previous Fixes Failed

Not a repeated-fix situation. There is exactly one relevant change (#1805) and
it did the right thing to the production code. The only omission was updating
the test assertion to match the deliberately-changed contract.

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| PR #1812 (#1805) | Made no-loop/loop-closed/timeout paths re-enqueue to outbox and `return recovered` | Correct production change; left `test_no_loop_returns_false` asserting the pre-#1805 `False`, creating the test-vs-code mismatch |

**Root cause pattern:** A behavior change to a return contract was shipped
without updating the one unit test that pinned the old contract.

## Architectural Impact

- **New dependencies:** None.
- **Interface changes:** None to the runtime signature. The `_deliver_sync`
  docstring is clarified to state the precise return contract.
- **Coupling:** Unchanged.
- **Data ownership:** Unchanged.
- **Reversibility:** Trivially reversible (test edit plus a docstring comment).

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (the investigation resolves the direction unambiguously)
- Review rounds: 1 (single reviewer confirms the contract decision)

## Prerequisites

No prerequisites — this work has no external dependencies. The failing test runs
against local Redis, which the granite unit suite already assumes.

## Solution

### Decision (resolves the issue's investigation)

**The test is stale; the production code is correct.** Update the test to assert
the recovery contract. Do NOT change the `_deliver_sync` return value.

Evidence:

1. **Intent is documented in git history.** PR #1812 (#1805) deliberately
   replaced `return False` with `recovered = self._enqueue_to_outbox(...);
   return recovered` on the no-loop path (and the loop-closed and timeout
   paths), rewording the log line from "delivery skipped" to "re-enqueueing to
   outbox." The change exists precisely so a reply is never silently lost.

2. **The contract is coherent as "delivered OR durably queued at return time."**
   All three paths that call `_enqueue_to_outbox` synchronously before returning
   (no-loop 1100-1114, loop-closed 1178-1187, timeout 1188-1213) return
   `recovered`. The one path that returns `False` despite eventual outbox
   recovery — same-thread fire-and-forget (line 1161) — is consistent, because
   there the enqueue is DEFERRED to a `task.add_done_callback`; nothing is in the
   outbox at return time. So `True` == "the payload is delivered or already sits
   in the outbox," `False` == "neither happened yet."

3. **No caller depends on `False` meaning "not synchronously delivered."** The
   only consumers of the return value are `_make_user_callback` /
   `_make_complete_callback` (lines 1008-1054), which set
   `self._user_facing_routed = True` and emit a dashboard event with
   `"delivered": delivered`. Treating an outbox-recovered reply as routed is
   correct: the outbox drain will deliver it, and the nuance is separately
   recorded by `_record_delivery_event(..., recovered=True)` as a
   `recovered_via_outbox` event. Marking `user_facing_routed` selects the
   `REACTION_COMPLETE` emoji over the bare `REACTION_SUCCESS`, which is the right
   signal for a reply that will reach the user.

### Key Elements

- **Test update (`test_bridge_adapter.py`)**: Replace the hardcoded
  `assertFalse` with assertions that pin the real contract — the no-loop path
  returns whatever `_enqueue_to_outbox` returns.
- **Docstring clarification (`bridge_adapter.py`)**: State the return contract
  precisely so the `False`-on-same-thread vs `recovered`-on-no-loop distinction
  is legible and cannot drift into another stale-test mismatch.

### Flow

Container thread emits `[/user]`/`[/complete]` → `_deliver_sync` → no live loop
→ `_enqueue_to_outbox` (sync Redis) → returns `recovered` → caller sets
`_user_facing_routed` and emits `granite_user_routed`/`granite_complete_routed`
event → outbox relay drains the payload to Telegram later.

### Technical Approach

- Rename `test_no_loop_returns_false` →
  `test_no_loop_returns_outbox_recovery_result` (name must not lie about the
  contract).
- Use `unittest.mock.patch` (already imported) to patch
  `adapter._enqueue_to_outbox`:
  - patched to return `True` → assert `_deliver_sync(...) is True` and that
    `_enqueue_to_outbox` was called once with `(chat_id, payload, reply_to)`.
  - patched to return `False` (outbox enqueue failed, e.g. missing session_id)
    → assert `_deliver_sync(...) is False`.
- This tests the actual behavior on both branches rather than depending on
  ambient Redis availability (the current test only "passes as True" because
  local Redis happens to accept the rpush).
- Clarify the `_deliver_sync` docstring: "Returns True when the payload is
  delivered synchronously OR re-enqueued to the outbox before returning; False
  when neither happened (sync send_cb raised, a pre-scheduling error occurred,
  or the same-thread fire-and-forget path where outbox recovery is deferred to a
  done-callback)."
- No change to production control flow or return values.

## Failure Path Test Strategy

### Exception Handling Coverage
- The touched `_deliver_sync` block already routes its failure/no-loop paths
  through `_record_delivery_event(..., recovered=...)`; no `except Exception:
  pass` blocks are introduced or modified. The existing `TestDeliverSyncReturnsBool`
  cases already cover the sync-raises path (`test_sync_send_cb_raises_returns_false`).

### Empty/Invalid Input Handling
- The `_enqueue_to_outbox` false branch (no `session_id` → returns `False`) is
  now explicitly asserted via the mocked-`False` case, proving the no-loop path
  propagates a genuine "not queued" outcome as `False`.

### Error State Rendering
- The return value flows to the dashboard `"delivered"` event field. The
  mocked-`True` and mocked-`False` cases pin both renderings. No user-facing UI
  is added.

## Test Impact

- [ ] `tests/unit/granite_container/test_bridge_adapter.py::TestDeliverSyncReturnsBool::test_no_loop_returns_false`
  — REPLACE: rename to `test_no_loop_returns_outbox_recovery_result` and assert
  the return value equals the (mocked) `_enqueue_to_outbox` result on both the
  `True` and `False` branches, instead of a hardcoded `False`.
- The sibling cases in the same class
  (`test_sync_send_cb_success_returns_true`, `test_sync_send_cb_raises_returns_false`)
  are unaffected — their contract is unchanged.

## Rabbit Holes

- **Do not "fix" the code to return `False` on the no-loop path.** That would
  re-introduce the silent-reply-loss #1805 fixed and mislabel a recovered reply
  as un-routed. The investigation resolves in favor of the code.
- **Do not unify the same-thread fire-and-forget path (line 1161) to also return
  `recovered`.** Its enqueue is deferred to a done-callback; nothing is queued at
  return time, so `False` is correct there. Touching it is scope creep with
  concurrency risk.
- **Do not refactor `_deliver_sync`'s structure.** The only code edit is the
  docstring; behavior stays byte-for-byte identical.

## Risks

### Risk 1: Test made to pass without pinning the real contract
**Impact:** A future contract drift slips through again.
**Mitigation:** Assert BOTH branches (recovered True → True, recovered False →
False) by mocking `_enqueue_to_outbox`, so the test encodes the actual
"return == recovery result" contract rather than a single ambient outcome.

### Risk 2: Docstring wording drifts from behavior later
**Impact:** Confusion recurs.
**Mitigation:** The docstring states the contract in terms of the four concrete
return sites; the test now enforces the no-loop site, so a behavior change would
turn the test red.

## Race Conditions

No race conditions identified. The change is a test edit plus a docstring
comment; no production concurrency behavior is altered. The mocked test runs the
no-loop branch synchronously with `_loop = None` (no scheduling, no threads).

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

## Update System

No update system changes required — this is a test assertion fix plus a docstring
comment, purely internal to the granite container module. No new dependencies,
config, or migrations.

## Agent Integration

No agent integration required — this is a bridge/container-internal change with
no new tool surface, MCP registration, or entry-point wiring. The
`_deliver_sync` return value is consumed only inside `bridge_adapter.py`.

## Documentation

No documentation changes needed — this fix corrects a stale unit-test assertion
and clarifies an existing private-method docstring. There is no user-facing or
feature-level behavior change to document, so no `docs/features/` file is created
or updated. The only doc-shaped deliverable is inline (the `_deliver_sync`
docstring), captured as a task below.

### Inline Documentation
- [ ] Clarify the `_deliver_sync` docstring in
  `agent/granite_container/bridge_adapter.py` to state the precise return
  contract (delivered-or-queued → True; neither → False).

## Success Criteria

- [ ] `test_no_loop_returns_false` is replaced by
  `test_no_loop_returns_outbox_recovery_result`, asserting the return value
  equals the mocked `_enqueue_to_outbox` result on both branches.
- [ ] `pytest tests/unit/granite_container/test_bridge_adapter.py -n0` exits 0.
- [ ] `pytest tests/unit/granite_container/ -n0` exits 0 (the baseline the issue
  says this test alone was breaking).
- [ ] `_deliver_sync` production return values are unchanged (git diff shows no
  change to any `return` statement in the method body).
- [ ] `_deliver_sync` docstring states the delivered-or-queued contract.
- [ ] Tests pass (`/do-test`)
- [ ] Format clean (`python -m ruff format --check .`)

## Team Orchestration

### Team Members

- **Builder (test-and-docstring)**
  - Name: deliver-sync-fixer
  - Role: Rename/rewrite the no-loop test to assert the recovery contract and
    clarify the `_deliver_sync` docstring. No production control-flow change.
  - Agent Type: builder
  - Domain: async (outbox recovery / event-loop absence semantics)
  - Resume: true

- **Validator (contract)**
  - Name: deliver-sync-validator
  - Role: Verify the granite unit suite is green, the production return
    statements are unchanged, and both recovery branches are asserted.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Rewrite the no-loop test + clarify docstring
- **Task ID**: build-test-contract
- **Depends On**: none
- **Validates**: `tests/unit/granite_container/test_bridge_adapter.py::TestDeliverSyncReturnsBool`
- **Informed By**: Solution decision (test is stale; code is correct)
- **Assigned To**: deliver-sync-fixer
- **Agent Type**: builder
- **Parallel**: false
- Rename `test_no_loop_returns_false` to
  `test_no_loop_returns_outbox_recovery_result`.
- Patch `adapter._enqueue_to_outbox` to return `True`; set `adapter._loop = None`;
  assert `_deliver_sync(...) is True` and `_enqueue_to_outbox` was called once
  with `(1, "hello", None)`.
- Add an assertion (same test or a sibling) patching `_enqueue_to_outbox` to
  return `False` and asserting `_deliver_sync(...) is False`.
- Update the class docstring line if it still says only "False on failure" so it
  reads "…or the outbox recovery result on deferred-delivery paths."
- Clarify the `_deliver_sync` docstring in `bridge_adapter.py` per the Technical
  Approach. Do NOT change any `return` statement in the method body.
- Run `python -m ruff format .` on touched files.

### 2. Validate the contract and baseline
- **Task ID**: validate-contract
- **Depends On**: build-test-contract
- **Assigned To**: deliver-sync-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/granite_container/test_bridge_adapter.py -n0` → expect 0.
- Run `pytest tests/unit/granite_container/ -n0` → expect 0.
- Run `git diff agent/granite_container/bridge_adapter.py` and confirm no
  `return` statement changed (docstring-only edit).
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No-loop test passes | `pytest "tests/unit/granite_container/test_bridge_adapter.py::TestDeliverSyncReturnsBool" -n0 -q` | exit code 0 |
| Granite unit baseline green | `pytest tests/unit/granite_container/ -n0 -q` | exit code 0 |
| Stale test name gone | `grep -rn "test_no_loop_returns_false" tests/unit/granite_container/` | exit code 1 |
| New contract test present | `grep -rn "test_no_loop_returns_outbox_recovery_result" tests/unit/granite_container/` | exit code 0 |
| Production returns unchanged | `git diff main -- agent/granite_container/bridge_adapter.py \| grep -E "^[-+].*return (recovered\|False\|True)"` | match count == 0 |
| Format clean | `python -m ruff format --check agent/granite_container/bridge_adapter.py tests/unit/granite_container/test_bridge_adapter.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None. The investigation resolves the direction unambiguously (test is stale;
production code is correct per the intentional #1805 change), and the fix is a
test rewrite plus a docstring clarification with no production behavior change.
