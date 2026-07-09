---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-09
tracking: https://github.com/tomcounsell/ai/issues/1980
last_comment_id:
---

# Wrap-up Guard Reply-Text Loss (stale-UUID fallback clobbers a valid result)

## Problem

A PM turn (including the wrap-up guard's bounded extra turn) can produce a fully valid,
complete `[/complete]`/`[/user]` final message — persisted in the raw Claude Code
transcript with `stop_reason: "end_turn"` — and the human still receives the canned
`OPERATOR_TERMINAL_MESSAGE` ("I wasn't able to produce a response to this. Please rephrase
or follow up.") instead of the real ~1,300-character completion.

**Current behavior:**
Inside `get_response_via_harness` (`agent/sdk_client.py`), a resumed (`--resume`) subprocess
can emit a valid `result` event carrying the completion text and *then* exit non-zero (a
post-turn/cleanup non-zero exit, not an external kill). The stale-UUID fallback at
`agent/sdk_client.py:2607` fires on `prior_uuid and returncode is not None and returncode != 0`
**without checking whether a `result` event already fired**. It re-runs a fresh session
(no `--resume`, using `full_context_message`); that retry's empty/different output overwrites
the good `result_text`, so `get_response_via_harness` returns `""`.
`HeadlessRoleDriver.run_turn`'s `if not reply:` empty-output guard
(`agent/session_runner/role_driver.py:433-436`) then sets `outcome.reply_text = ""` /
`exit_reason = "empty_output"`, and `_run_wrapup_guard` (`agent/session_runner/runner.py:1178-1180`)
delivers `OPERATOR_TERMINAL_MESSAGE`.

This defeats a contract the role driver *already documents* but cannot enforce, because the
clobber happens one layer below it. `role_driver.py:453-454`: *"A nonzero exit AFTER a
result event keeps the result: the event is the protocol's completion signal."* The role
driver's residual-#1916 guard (`role_driver.py:457-468`) only keeps a result on a nonzero
exit when a result event fired — but by the time it runs, `get_response_via_harness` has
already discarded the text via the fallback retry.

**Desired outcome:**
When the underlying harness subprocess produces a valid `result` event, that text propagates
to `HeadlessRoleDriver.run_turn`'s return value and is delivered to the human — even on a
non-zero exit, even inside the wrap-up guard's bounded turn. `OPERATOR_TERMINAL_MESSAGE`
fires only when the PM turn genuinely produced no result event and no accumulated text.

## Freshness Check

**Baseline commit:** d875ae226fed1e066e47eed00b4aad2c792e7bc1
**Issue filed at:** 2026-07-09T10:45:25Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/sdk_client.py:2607` — stale-UUID fallback gate `if prior_uuid and returncode is not None and returncode != 0:` — still holds verbatim.
- `agent/sdk_client.py:2949-2996` — `result` event handler `result_text = data.get("result", "")` then `break` — still holds.
- `agent/sdk_client.py:2751-2753` — `if result_text is not None: return result_text` / `return ""` — still holds.
- `agent/session_runner/role_driver.py:433-436` — `if not reply:` empty-output guard — still holds.
- `agent/session_runner/role_driver.py:451-468` — residual-#1916 "nonzero exit WITHOUT a result event is a failed turn" guard, comment at 453-454 documents the intended "result event is the completion signal" contract — still holds.
- `agent/session_runner/runner.py:1149-1183` — `_run_wrapup_guard`, OPERATOR_TERMINAL_MESSAGE fallback at 1178-1180 — still holds.

**Cited sibling issues/PRs re-checked:**
- #1979 — sticky `response_delivered_at` premature finalization; issue explicitly ruled it out as the same root cause (different timing window). Not a blocker.
- #1916 (residual) — the role-driver guard this bug defeats one layer up. Confirms the intended contract; our fix enforces it at the correct layer.

**Commits on main since issue was filed (touching referenced files):** none (`git log --since=<createdAt> -- agent/sdk_client.py agent/session_runner/role_driver.py agent/session_runner/runner.py` returned empty).

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** No drift. All line references cited in the issue are exact against baseline.

## Prior Art

- **#1063**: "sdk_client --resume returns image-dimension error text (exit 0) — fallback never fires" — established the image-dimension sentinel path that sits *above* the stale-UUID fallback (`agent/sdk_client.py:2559`). Confirms the fallback ladder shape; our change touches only the stale-UUID gate, not the image path.
- **#1916**: added the role-driver residual guard (`role_driver.py:451-468`) that keeps a result on a nonzero exit iff a result event fired. This bug is the missing counterpart *one layer down* — `get_response_via_harness` must not clobber the result before that guard runs.
- **#1058**: "replace [PIPELINE_COMPLETE] marker with reliable PM final-delivery protocol" — same delivery-reliability theme; not the same root cause.
- No prior issue/PR found for "stale-UUID fallback discards a valid result event." This is a first fix for this specific path.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1916 (role_driver residual guard) | On a nonzero exit, keep the result iff a `result` event fired; else classify as failed turn. | Correct policy, wrong layer for THIS path. It runs in `HeadlessRoleDriver.run_turn` *after* `get_response_via_harness` returns. The stale-UUID fallback inside `get_response_via_harness` already overwrote `result_text` with the fresh-retry's empty output, so the guard never sees the completion. |

**Root cause pattern:** the "a `result` event is the protocol's completion signal" invariant
is enforced at the role-driver layer but violated at the harness layer. The fix moves the
invariant down to where the clobber happens.

## Data Flow

1. **Entry point**: `SessionRunner._run_wrapup_guard` (`runner.py:1159`) calls `self._driver.run_turn(PM_WRAPUP_PROMPT...)`.
2. **`HeadlessRoleDriver.run_turn`** (`role_driver.py:381`): `reply = await asyncio.wait_for(harness_fn(...), timeout=turn_timeout_s)` where `harness_fn` is `get_response_via_harness`. This turn rides `--resume` (`prior_uuid` set from earlier turns).
3. **`get_response_via_harness`** (`sdk_client.py`): primary `_run_harness_subprocess` call (site 1, line 2526) parses stream-json; the `result` event sets `result_text` = the completion (line 2950); subprocess then exits non-zero → `returncode != 0`.
4. **Clobber (the bug)**: `sdk_client.py:2607` `if prior_uuid and returncode != 0:` → fallback re-runs `_run_harness_subprocess` (site 3, line 2635) with `full_context_message`, no `--resume`; that retry returns empty `result_text`, overwriting the good value.
5. **Return**: `if result_text is not None: return result_text` (line 2751) — now `""` (or `None` → `""`).
6. **`run_turn`**: `if not reply:` (line 433) → `outcome.reply_text=""`, `exit_reason="empty_output"`.
7. **Output**: `_run_wrapup_guard` sees empty text → `on_user_payload(OPERATOR_TERMINAL_MESSAGE)` (`runner.py:1179`). Real completion lost.

The fix intercepts at step 4: skip the fallback when step 3 already produced a `result` event
(`result_text is not None`).

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none. `get_response_via_harness` signature and return type unchanged.
- **Coupling**: unchanged. One conditional gains a clause using a local already in scope (`result_text`).
- **Data ownership**: unchanged.
- **Reversibility**: trivial — the change is one added boolean clause; revert is a one-line diff.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (root cause pinned via code recon; no scope ambiguity)
- Review rounds: 1 (code review before merge)

## Prerequisites

No prerequisites — this work has no external dependencies. The regression tests mock
`_run_harness_subprocess`, so no live `claude` binary or network is required.

## Solution

### Key Elements

- **Stale-UUID fallback gate (`agent/sdk_client.py:2607`)**: add a `result_text is None`
  clause so the destructive fresh-session retry fires only when the resumed subprocess
  produced no `result` event at all (the genuine stale/invalid-UUID case, where `claude
  --resume` errors out before emitting any result). A resumed turn that emitted a `result`
  event is a successful resume; a subsequent non-zero exit must not discard the completion.
- **Comment update**: document that the fallback is gated on "no result event fired," and
  that this enforces at the harness layer the same "result event is the completion signal"
  invariant that `role_driver.py:453-454` documents at the driver layer.

### Flow

Resumed wrap-up turn → subprocess emits valid `result` event (completion text captured)
→ subprocess exits non-zero → **fallback gate now sees `result_text is not None` and skips
the retry** → `get_response_via_harness` returns the completion → `run_turn` returns it
(residual-#1916 guard keeps it) → `_run_wrapup_guard` classifies `[/complete]` and delivers
the real text.

### Technical Approach

- Change the gate at `agent/sdk_client.py:2607` from:
  ```python
  if prior_uuid and returncode is not None and returncode != 0:
  ```
  to:
  ```python
  if prior_uuid and returncode is not None and returncode != 0 and result_text is None:
  ```
  `result_text is None` iff no `result` event fired on the primary invocation (the
  `_run_harness_subprocess` return contract: `result_text` is non-None exactly when a
  result event fired — see `sdk_client.py:3085-3129` and the `on_exit_status(returncode,
  result_text is not None)` call at line 3089). An empty-but-present result (`""`) is a
  genuinely empty turn and correctly does NOT trigger the fallback, satisfying acceptance
  criterion #3.
- Leave the image-dimension fallback (`sdk_client.py:2559-2600`) untouched — it is gated
  independently on `IMAGE_DIMENSION_SENTINEL` and a truthy `result_text`, and does not
  interact with this change (that path runs on `returncode == 0`).
- No change to `role_driver.py` or `runner.py`: once the harness stops clobbering, the
  existing residual-#1916 guard and wrap-up-guard classification deliver the text correctly.

## Failure Path Test Strategy

### Exception Handling Coverage
- No new `except Exception: pass` blocks introduced. The touched function already has
  fail-quiet Popoto/token side effects guarded elsewhere; this change adds no handlers.
- State: "No exception handlers added in scope."

### Empty/Invalid Input Handling
- The core behavior under test IS empty-output handling. New tests assert:
  - Non-empty `result` event + non-zero exit + `prior_uuid` → real text returned (fallback skipped).
  - `result_text is None` (no result event) + non-zero exit + `prior_uuid` → fallback still fires (stale-UUID recovery preserved).
  - Empty-string `result` event (`""`) + non-zero exit → fallback NOT fired; returns `""` (genuinely empty; OPERATOR_TERMINAL_MESSAGE is then correct downstream).
- Verifies empty output does not trigger a silent loop: the fallback is bounded to one retry and now only fires when there is genuinely nothing to preserve.

### Error State Rendering
- End-to-end `run_turn` test asserts that a nonzero-exit-with-result turn yields a non-empty
  `outcome.reply_text` and `exit_reason != "empty_output"` — i.e. the real content reaches
  the delivery path rather than being swallowed into the canned message.

## Test Impact

- [ ] `tests/unit/test_harness_retry.py::TestHarnessRetry::test_first_retry_increments_counter_and_returns_empty` — REVIEW/no change expected: this covers the AgentSession-level agent retry (different mechanism), not the stale-UUID subprocess fallback. Verify it still passes; no edit anticipated.
- [ ] `tests/integration/test_harness_resume.py::test_stale_uuid_triggers_fallback` — REVIEW/no change expected: a genuinely stale UUID produces NO result event on the primary, so `result_text is None` and the fallback still fires. Confirm this integration test still passes unchanged.
- [ ] `tests/unit/test_sdk_client_harness_counters.py` — REVIEW/no change expected: counter accumulation across primary+fallback is unaffected when the fallback is legitimately skipped. Confirm green.

No existing test asserts the buggy behavior (clobber-on-nonzero-exit-with-result), so no
test needs DELETE/REPLACE — the fix is additive to the gate and the regression coverage is new.

## Rabbit Holes

- **Reading the transcript `.jsonl` as a recovery source** when the stdout `result` event is
  missing/null. Tempting (the transcript had the text) but a much larger change and NOT this
  bug — here the `result` event DID fire; the problem is discarding it. Out of scope.
- **Reworking `content_block_start` full_text reset** (`sdk_client.py:3014-3015` resets
  `full_text` per content block). A latent hardening concern for the no-result-event fallback
  path, but unrelated to this bug (which has a result event). Do not touch.
- **Turn-timeout tuning** for the wrap-up guard vs. main loop. The issue floated a timeout
  race as an open question; recon rules it out (no kill/timeout log line; the clobber fully
  explains the symptom). Do not chase timeout tuning.

## Risks

### Risk 1: A genuinely stale UUID that somehow emits a result event before erroring
**Impact:** If `claude --resume <bad-uuid>` could emit a `result` event and *then* fail, the
gated fallback would be skipped and we'd return that (possibly partial) result instead of
retrying fresh.
**Mitigation:** By protocol, `claude --resume` on an unrecognized UUID errors during startup
*before* producing any `result` event — `result_text` stays `None`, so the fallback still
fires. The integration test `test_stale_uuid_triggers_fallback` guards this empirically. If a
result event fired, the resume demonstrably succeeded and its output is authoritative
(consistent with the role-driver's documented contract).

### Risk 2: Masking a real failure by delivering a result from a subprocess that exited non-zero
**Impact:** Delivering content from a subprocess that ultimately exited non-zero.
**Mitigation:** This is the *intended* contract, already documented at `role_driver.py:453-454`
("a nonzero exit AFTER a result event keeps the result"). A `result` event with
`stop_reason: end_turn` is a clean model completion; a subsequent non-zero exit is a
post-turn/cleanup artifact, not a reason to discard a user's answer. The residual-#1916 guard
still classifies a nonzero exit *without* a result event as a failed turn.

## Race Conditions

No race conditions identified. `get_response_via_harness` is a single async coroutine; the
primary and fallback subprocess calls are sequential `await`s within one turn. `result_text`
is a local variable read and written on the same coroutine with no concurrent access. The
change adds a read of an already-materialized local to a synchronous conditional.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1979] Sticky `response_delivered_at` premature-finalization fix — a distinct
  root cause on the same session, tracked separately.

## Update System

No update system changes required — this is a purely internal behavioral fix to a single
conditional in `agent/sdk_client.py`. No new dependencies, config files, migrations, or
`scripts/update/` changes. No Popoto schema changes.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change to the headless
session runner's harness call path. No new CLI entry point, no `.mcp.json`/`mcp_servers/`
change, and the bridge (`bridge/telegram_bridge.py`) does not need to import anything new.
The fix restores correct delivery through the *existing* agent output path; the regression
tests exercise `get_response_via_harness` and `HeadlessRoleDriver.run_turn` directly.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/headless-session-runner.md` — add a subsection under the harness
  fallback / turn-outcome discussion documenting the "a `result` event is the completion
  signal; the stale-UUID fallback only fires when no result event fired" invariant, and how it
  pairs with the role-driver residual-#1916 guard.

### External Documentation Site
- No external docs site in this repo. N/A.

### Inline Documentation
- [ ] Update the comment block at `agent/sdk_client.py:2602-2606` to state the fallback is
  gated on "no result event fired (`result_text is None`)" and why (don't discard a valid
  completion on a post-turn non-zero exit).
- [ ] Ensure the function docstring's fallback description (`sdk_client.py:2386-2388`) reflects
  the `result_text is None` condition.

## Success Criteria

- [ ] Root cause documented: stale-UUID fallback at `sdk_client.py:2607` clobbers a valid
  `result_text` on a non-zero exit that followed a fired `result` event. (Acceptance #1)
- [ ] Regression test: a turn whose primary subprocess emits valid final text and exits
  non-zero returns that text (fallback skipped), and `HeadlessRoleDriver.run_turn` propagates
  it (non-empty `reply_text`, `exit_reason != "empty_output"`) — real text delivered, not
  `OPERATOR_TERMINAL_MESSAGE`. (Acceptance #2)
- [ ] Test: `result_text is None` + non-zero exit + `prior_uuid` still triggers the fallback
  (stale-UUID recovery preserved).
- [ ] Test: empty-string result event does not spuriously trigger the fallback and yields
  `""` — OPERATOR_TERMINAL_MESSAGE remains reserved for a genuinely empty PM turn. (Acceptance #3)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -n "result_text is None" agent/sdk_client.py` confirms the gate change is present.

## Team Orchestration

Small single-file fix; solo builder + code reviewer.

### Team Members

- **Builder (harness-fallback-gate)**
  - Name: harness-fix-builder
  - Role: Apply the one-clause gate change, update comments/docstring, write the regression tests.
  - Agent Type: builder
  - Domain: async/concurrency
  - Resume: true

- **Code Reviewer (harness-fallback-gate)**
  - Name: harness-fix-reviewer
  - Role: Verify the gate change preserves stale-UUID recovery and does not mask real failures; verify tests genuinely reproduce the failure.
  - Agent Type: code-reviewer
  - Resume: true

### Step by Step Tasks

### 1. Implement fallback gate + tests
- **Task ID**: build-harness-gate
- **Depends On**: none
- **Validates**: tests/unit/test_harness_stale_uuid_result_preservation.py (create), tests/unit/test_sdk_client_harness_counters.py, tests/integration/test_harness_resume.py
- **Assigned To**: harness-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `and result_text is None` to the stale-UUID fallback gate at `agent/sdk_client.py:2607`.
- Update the comment at `sdk_client.py:2602-2606` and the docstring fallback note.
- Create `tests/unit/test_harness_stale_uuid_result_preservation.py` with three unit tests
  (result-event-preserved, no-result-event-still-fallbacks, empty-result-not-fallbacked) plus
  one `HeadlessRoleDriver.run_turn` end-to-end test (nonzero exit + result event → non-empty
  reply_text, `exit_reason != "empty_output"`).

### 2. Documentation
- **Task ID**: document-feature
- **Depends On**: build-harness-gate
- **Assigned To**: harness-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `docs/features/headless-session-runner.md` per the Documentation section.

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-harness-gate, document-feature
- **Assigned To**: harness-fix-reviewer
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm all success criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Gate change present | `grep -n "returncode != 0 and result_text is None" agent/sdk_client.py` | exit code 0 |
| Regression tests pass | `pytest tests/unit/test_harness_stale_uuid_result_preservation.py -q` | exit code 0 |
| Existing harness tests pass | `pytest tests/unit/test_harness_retry.py tests/unit/test_sdk_client_harness_counters.py tests/unit/test_sdk_client.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/sdk_client.py tests/unit/test_harness_stale_uuid_result_preservation.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/sdk_client.py tests/unit/test_harness_stale_uuid_result_preservation.py` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_harness_stale_uuid_result_preservation.py` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
