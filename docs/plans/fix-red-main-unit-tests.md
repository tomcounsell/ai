---
status: Ready
type: bug
appetite: Small
owner: valor
created: 2026-08-21
tracking: https://github.com/yudame/ai/issues/2904
last_comment_id:
revision_applied: true
revision_applied_at: 2026-08-21T11:48:04Z
---

# Fix red main: two unit tests broken by the #2803 run-identity anchor

## Problem

`main` is red at `0f261bf0d`: three unit tests fail. Two of them are genuine,
unowned defects; the third is already owned by #2805.

**Current behavior:**
- `tests/unit/test_sdlc_dispatch.py::TestDispatchRecordLease::test_lease_lost_between_peek_and_write_refuses` fails: `mock_get_or_create.assert_not_called()` fires because `PipelineLedger.get_or_create` was called once.
- `tests/unit/test_sdlc_meta_set.py::TestMetaSetWriteMeta::test_pr_number_writes_ledger_field_not_meta_key` fails: `update_mock.assert_not_called()` fires because `update_stage_states` was called once.
- `tests/unit/test_subprocess_test_db_isolation.py::test_every_test_subprocess_inherits_the_claimed_test_db` fails — owned by #2805 (the subprocess test-db guard's line-number-keyed `ALLOWLIST`). Not in scope here.

**Desired outcome:**
- The two unowned tests pass on `main`, restoring a green suite for the #1 and #2 rows.
- The tests still assert their original intent: the dispatch write is refused when the lease is lost between peek and write, and `pr_number` is written as a ledger FIELD with no `_pr_number` meta key.

## Freshness Check

**Baseline commit:** `54a679dbb` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-08-20T08:32:25Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tests/unit/test_sdlc_dispatch.py:120` (`test_lease_lost_between_peek_and_write_refuses`) — still present; reproduced failing on current main.
- `tests/unit/test_sdlc_meta_set.py:254` (`test_pr_number_writes_ledger_field_not_meta_key`) — still present; reproduced failing on current main.
- `tools/_sdlc_utils.py:691` (`_anchor_confirmed_run_identity`) and `:731` (`resolve_ledger_lease`) — the anchor runs on the confirmed-owner path; confirmed as the source of the extra `get_or_create` / `update_stage_states` calls.
- `agent/pipeline_ledger.py:423` (`record_run_identity`) — calls `PipelineLedger.get_or_create` and `update_stage_states`; confirmed.

**Cited sibling issues/PRs re-checked:**
- #2805 — OPEN. Owns the #3 row (subprocess test-db guard ALLOWLIST). Not re-diagnosed here.
- #2803 — MERGED 2026-08-14 (`ec585ceb9`). The run-identity anchor; root cause of both #1 and #2. Landed AFTER the two tests were last touched (`0b65389ee`, 2026-08-13).

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since=2026-08-20T08:32:25Z` over the referenced files is empty.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** The issue frames "three isolated defects," but #1 and #2 share a single root cause (the #2803 anchor's side-effect calls on the confirmed-owner path). The plan treats them as one fix. #3 is a separate pre-owned defect.

## Prior Art

- **[PR #2803]** — "Anchor run-identity history on the ledger so a re-ensure rebinds instead of re-minting" (MERGED 2026-08-14). Introduced `_anchor_confirmed_run_identity` → `record_run_identity`, which calls `PipelineLedger.get_or_create` and `update_stage_states` on the confirmed-owner path inside `resolve_ledger_lease`. This is the root cause of both failing tests; it did not update the two tests' strict `assert_not_called()` assertions.
- **[Issue #2805]** — OPEN. Owns the #3 row. Not in scope.

No prior attempts to fix these two specific test failures were found.

## Research

No relevant external findings — proceeding with codebase context and training data. This is a purely internal test-assertion fix with no external libraries or ecosystem patterns involved.

## Spike Results

No spikes needed — the root cause was confirmed directly by reproducing both failures and tracing the call path through `resolve_ledger_lease` → `_anchor_confirmed_run_identity` → `record_run_identity`.

## Data Flow

The change is confined to two test files; no production data flow is modified. The relevant flow (for context) is:

1. **Entry point**: `_cli_record` / `write_meta` call `_sdlc_utils.resolve_ledger_lease(issue_number, run_id)`.
2. **`resolve_ledger_lease`**: peeks `touch_issue_lock`. On the confirmed-owner path (`acquired` and `owner_run_id == run_id`), calls `_anchor_confirmed_run_identity` → `record_run_identity`, which calls `PipelineLedger.get_or_create` and `update_stage_states` (the #2803 anchor side effects).
3. **Revalidate gate**: `revalidate_ledger_lease` re-touches the lock non-peek; a foreign owner returns `False` and the write is refused.
4. **Output**: `ISSUE_LOCKED` (dispatch) / field write via `ledger.save()` (pr_number).

The tests' `assert_not_called()` assertions predate step 2's side effects and are now stale.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2803 | Added the run-identity anchor on the confirmed-owner path | Did not update the two existing tests' strict `assert_not_called()` assertions, which now see the anchor's legitimate `get_or_create` / `update_stage_states` calls. The source behavior is correct; only the test assertions are stale. |

**Root cause pattern:** A production change added a legitimate side-effect call on a path the tests asserted was side-effect-free, and the tests were not updated in the same change.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none — no production code changes.
- **Coupling**: none.
- **Data ownership**: none.
- **Reversibility**: trivial — two test-assertion edits.

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

- **Fix test #1** (`test_sdlc_dispatch.py::test_lease_lost_between_peek_and_write_refuses`): the test's intent is that the dispatch WRITE is refused when the lease is lost between peek and revalidate. The source already returns `ISSUE_LOCKED` and never reaches the write-path `get_or_create` at `sdlc_dispatch.py:256`. The stale `mock_get_or_create.assert_not_called()` must be replaced with `assert mock_get_or_create.call_count == 1` — the anchor's single `get_or_create` is legitimate, and a second call would mean the write path ran.
- **Fix test #2** (`test_sdlc_meta_set.py::test_pr_number_writes_ledger_field_not_meta_key`): the test's intent is that `pr_number` is written as a ledger FIELD and no `_pr_number` meta key is ever written to `stage_states_json`. The source already does this via `ledger.save()`. The stale `update_mock.assert_not_called()` must be replaced with a real-dict membership check: initialize `mock_ledger.stage_states_json = {}` and assert `"_pr_number" not in mock_ledger.stage_states_json` (the anchor's `_run_identities` write is unrelated and legitimate).

### Flow

Red test → update assertion to target the real guarantee → green test → suite green.

### Technical Approach

- **Test #1** (`test_lease_lost_between_peek_and_write_refuses`): replace the stale `mock_get_or_create.assert_not_called()` with a load-bearing `assert mock_get_or_create.call_count == 1` AND add an additive `assert_not_called()` on the write-path entrypoint `record_dispatch_for_ledger`. The lease-lost write path would perform a second `get_or_create` after revalidation (the write-path call at `sdlc_dispatch.py:256`) and would call `record_dispatch_for_ledger`; since the write is refused, `record_dispatch_for_ledger` is never called. Patch it as `patch("tools.sdlc_dispatch.record_dispatch_for_ledger") as mock_record_dispatch` and assert `mock_record_dispatch.assert_not_called()`. This is purely additive and does not disturb the existing `result["reason"] == "ISSUE_LOCKED"` and `result["ok"] is False` assertions. The `record_dispatch_for_ledger` guard is the load-bearing one (robust to anchor refactors that add a second `get_or_create`); `call_count == 1` is retained as a secondary pin.
- **Test #2** (`test_pr_number_writes_ledger_field_not_meta_key`): the real-dict assignment alone does NOT resolve the vacuousness — `update_stage_states` is patched with a bare MagicMock that never applies the captured updater, so `stage_states_json` stays `{}` regardless and a buggy `write_meta` writing `_pr_number` via the helper passes green. Replace the bare patch with a real `side_effect` that applies the updater to a shared real dict, then assert membership after `write_meta`:
  ```python
  mock_ledger = MagicMock()
  mock_ledger.pr_number = None
  mock_ledger.stage_states_json = {}   # real dict, not a MagicMock

  def _apply(_, update_fn, **kwargs):
      mock_ledger.stage_states_json = update_fn(mock_ledger.stage_states_json) or {}

  with (
      patch("models.session_lifecycle.touch_issue_lock", return_value=_lock_result()),
      patch("agent.pipeline_ledger.PipelineLedger.get_or_create", return_value=mock_ledger),
      patch("tools.stage_states_helpers.update_stage_states", side_effect=_apply),
  ):
      result = write_meta(key="pr_number", value="42", issue_number=1, run_id="run-test")

  assert result == {"key": "pr_number", "value": 42}
  assert mock_ledger.pr_number == 42
  assert isinstance(mock_ledger.pr_number, int)
  mock_ledger.save.assert_called_once()
  assert "_pr_number" not in mock_ledger.stage_states_json
  ```
  The `_apply` side_effect signature is `(session, update_fn, **kwargs)` — both `write_meta` (`sdlc_meta_set.py:229`) and the #2803 anchor (`pipeline_ledger.py:472`) call `update_stage_states(ledger, update_fn, field="stage_states_json")`, i.e. two positional args plus the `field` kwarg. This still passes today (the anchor's `append_run_identity` writes only `_run_identities`) while catching a future `_pr_number`-writing updater. Drop the `update_mock.call_args_list` loop — it guards nothing about the single-writer contract because `write_meta` writes `pr_number` as a field via `ledger.save()`, not via `update_stage_states`.
- **Docstring (test #2)**: amend the test #2 docstring to the anchor-aware contract — it currently states "update_stage_states must not be called at all" (`test_sdlc_meta_set.py:254`), which the #2803 anchor now legitimately violates. Reword to e.g. "no `_pr_number` meta key is ever written to stage_states_json; the run-identity anchor may write `_run_identities`." Ship the docstring edit in the same commit as the assertion edit.
- **Integration points**: none — both edits are local to the two test files.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No exception handlers in scope — this is a test-assertion fix; no production exception paths are touched.

### Empty/Invalid Input Handling
- [ ] Not applicable — no new or modified functions; the tests exercise existing behavior.

### Error State Rendering
- [ ] Not applicable — no user-visible output.

## Test Impact

- [ ] `tests/unit/test_sdlc_dispatch.py::TestDispatchRecordLease::test_lease_lost_between_peek_and_write_refuses` — UPDATE: replace `mock_get_or_create.assert_not_called()` with `assert mock_get_or_create.call_count == 1` plus an additive `record_dispatch_for_ledger` `assert_not_called()` (the dispatch write is refused; the anchor's single `get_or_create` is legitimate).
- [ ] `tests/unit/test_sdlc_meta_set.py::TestMetaSetWriteMeta::test_pr_number_writes_ledger_field_not_meta_key` — UPDATE: initialize `mock_ledger.stage_states_json = {}` (real dict), replace the bare `update_stage_states` patch with a real `side_effect` that applies the updater, replace `update_mock.assert_not_called()` with `assert "_pr_number" not in mock_ledger.stage_states_json`, and amend the docstring to the anchor-aware contract.
- [ ] `tests/unit/test_subprocess_test_db_isolation.py::test_every_test_subprocess_inherits_the_claimed_test_db` — NOT in scope; owned by #2805.

## Rabbit Holes

- **Re-diagnosing #3** — the subprocess test-db guard failure is owned by #2805. Do not touch it here; fixing #2805 closes that row.
- **"Fixing" the production code** — the source is correct. The dispatch write IS refused and pr_number IS field-backed. Do not change `_sdlc_utils.py` / `sdlc_dispatch.py` / `sdlc_meta_set.py` to avoid the anchor's side effects; that would break the #2803 continuity feature.
- **Tightening the anchor** — making `record_run_identity` skip `get_or_create`/`update_stage_states` is a separate concern with its own blast radius; out of scope.

## Risks

### Risk 1: Over-broad assertion weakens the test's intent
**Impact:** The test stops guarding the actual contract (write refused / no `_pr_number` meta key).
**Mitigation:** Anchor the new assertions to the real write path (`record_dispatch_for_ledger` not called; no `_pr_number` key written), not to a blanket "no side effects" check.

### Risk 2: The anchor's behavior changes again
**Impact:** A future change to `record_run_identity` could re-break these tests.
**Mitigation:** The new assertions target the contract, not the anchor's incidental calls, so they are robust to anchor refactors.

## Race Conditions

No race conditions identified — the change is synchronous, single-threaded test-assertion edits with no concurrency concerns.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2805] Fixing the #3 subprocess test-db guard failure — owned by #2805; do not re-diagnose or touch it here.
- [SEPARATE-SLUG #2803] Modifying the run-identity anchor's side-effect behavior — a separate concern with its own blast radius; the source is correct as-is.

## Update System

No update system changes required — this is a test-assertion fix with no runtime, dependency, or config impact. The `/update` skill and `scripts/update/` are unaffected.

## Agent Integration

No agent integration required — this is a test-only change. No new CLI entry point, MCP surface, or bridge wiring is involved.

## Documentation

- [ ] Amend the in-code docstring of `test_pr_number_writes_ledger_field_not_meta_key` (`tests/unit/test_sdlc_meta_set.py:254`) to the anchor-aware contract (see Technical Approach) so it stays accurate.
- [ ] No `docs/features/` file is needed — this is a test-only fix with no new capability, interface, or user-facing surface to document. The only doc change is the in-code docstring above.

## Success Criteria

- [ ] `tests/unit/test_sdlc_dispatch.py::TestDispatchRecordLease::test_lease_lost_between_peek_and_write_refuses` passes.
- [ ] `tests/unit/test_sdlc_meta_set.py::TestMetaSetWriteMeta::test_pr_number_writes_ledger_field_not_meta_key` passes.
- [ ] The two tests still assert their original intent (write refused / no `_pr_number` meta key).
- [ ] No production code changed.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`) — no changes needed.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly.

### Team Members

- **Builder (test-fix)**
  - Name: test-fix-builder
  - Role: Apply both test-assertion updates (and the test #2 docstring amendment) in one task, then self-verify both tests pass
  - Agent Type: builder
  - Resume: true

### Available Agent Types

A single Tier 1 core agent (`builder`) is sufficient for this Small-appetite fix. The two edits are small, tightly coupled, and share one root cause, so a single builder task with a self-verifying validation step replaces the earlier two-builder / two-validator orchestration — avoiding disproportionate machinery and the rival-incarnation hazard of two parallel builders on the same named agent.

## Step by Step Tasks

### 1. Apply both test-assertion fixes (single builder task)
- **Task ID**: build-testfix
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_dispatch.py, tests/unit/test_sdlc_meta_set.py
- **Assigned To**: test-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- **Test #1** (`test_lease_lost_between_peek_and_write_refuses`): replace `mock_get_or_create.assert_not_called()` with `assert mock_get_or_create.call_count == 1` AND add an additive `patch("tools.sdlc_dispatch.record_dispatch_for_ledger") as mock_record_dispatch` with `mock_record_dispatch.assert_not_called()`. Keep `result["ok"] is False` and `result["reason"] == "ISSUE_LOCKED"`. The lease-lost write path would perform a second `get_or_create` after revalidation and would call `record_dispatch_for_ledger`; since the write is refused, neither happens. The `record_dispatch_for_ledger` guard is load-bearing (robust to anchor refactors); `call_count == 1` is a secondary pin.
- **Test #2** (`test_pr_number_writes_ledger_field_not_meta_key`): initialize `mock_ledger.stage_states_json = {}` (a real dict) before `write_meta`; keep `mock_ledger.pr_number == 42` and `mock_ledger.save.assert_called_once()`; replace the bare `patch("tools.stage_states_helpers.update_stage_states")` with a real `side_effect` that applies the updater to the shared dict, then replace `update_mock.assert_not_called()` with `assert "_pr_number" not in mock_ledger.stage_states_json`. The side_effect is `def _apply(_, update_fn, **kwargs): mock_ledger.stage_states_json = update_fn(mock_ledger.stage_states_json) or {}` (both `write_meta` and the anchor call `update_stage_states(ledger, update_fn, field="stage_states_json")`). Do NOT use a loop over `update_mock.call_args_list` — it guards nothing about the single-writer contract (write_meta writes pr_number as a field via `ledger.save()`).
- **Docstring (test #2)**: amend the test #2 docstring from "update_stage_states must not be called at all" to the anchor-aware contract (e.g. "no `_pr_number` meta key is ever written to stage_states_json; the run-identity anchor may write `_run_identities`"). Ship in the same commit as the assertion edit.

### 2. Self-verify (same builder task)
- **Task ID**: verify-testfix
- **Depends On**: build-testfix
- **Assigned To**: test-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Run both tests individually and confirm they pass.
- Confirm no production code changed (`git diff --stat` shows only the two test files).
- Run `python -m ruff check` and `python -m ruff format --check` on the two changed test files.
- Confirm the tests still assert their original intent (write refused / no `_pr_number` meta key).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Test #1 passes | `scripts/pytest-clean.sh tests/unit/test_sdlc_dispatch.py::TestDispatchRecordLease::test_lease_lost_between_peek_and_write_refuses -q` | exit code 0 |
| Test #2 passes | `scripts/pytest-clean.sh tests/unit/test_sdlc_meta_set.py::TestMetaSetWriteMeta::test_pr_number_writes_ledger_field_not_meta_key -q` | exit code 0 |
| Lint clean | `python -m ruff check tests/unit/test_sdlc_dispatch.py tests/unit/test_sdlc_meta_set.py` | exit code 0 |
| Format clean | `python -m ruff format --check tests/unit/test_sdlc_dispatch.py tests/unit/test_sdlc_meta_set.py` | exit code 0 |
| No production code changed | `git diff --stat -- tools/ agent/ models/` | output does not contain `tools/` |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| NIT | Consolidated Critic | The Verification table's "No production code changed" check (`git diff --stat -- tools/ agent/ models/`) only greps for `tools/` in the output; an accidental change under `agent/` or `models/` would pass the check despite being a production-code change. | pending | N/A (NIT) |

---
