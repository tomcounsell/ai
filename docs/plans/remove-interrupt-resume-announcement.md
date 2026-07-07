---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-07
tracking: https://github.com/tomcounsell/ai/issues/1937
last_comment_id:
---

# Remove the "I was interrupted, will resume automatically" announcement

## Problem

When the worker restarts (e.g. a `/update`-driven redeploy) it cancels every in-flight
session's asyncio task. The `CancelledError` handler then sends the originating chat a
user-visible line: `"I was interrupted and will resume automatically. No action needed."`
This fires on routine, self-healing interruptions and lands as noise in client-facing
chats (observed in the Behring/cyndra chat, 2026-07-07 14:15 UTC, session
`tg_cyndra_8762685703_11064`). It is a promise about future runtime behavior, not an
outcome the user needs.

**Current behavior:**
On any cancel where the cancel-reason is absent or `"resume"`, both send sites emit
`INTERRUPT_RESUME` to the chat. A `/update` restart therefore produces a "will resume
automatically" line before the eventual real answer.

**Desired outcome:**
An interruption the machinery will recover from is **silent**. The user only ever sees one
of two terminal outcomes: **Finish** (the session's real work product) or **Fail**
(`FAILURE_NOTICE` on crash, `INTERRUPT_NO_RESUME` on a terminal, non-resumable stop). No
mid-flight lifecycle chatter. Grep-clean of "will resume automatically" across the codebase.

## Freshness Check

**Baseline commit:** `6b0518c9`
**Issue filed at:** 2026-07-07T07:36:18Z
**Disposition:** Unchanged

**File:line references re-verified (all Read against `6b0518c9`, all still hold):**
- `agent/notification_copy.py:24` — `INTERRUPT_RESUME` defined with the exact issue-quoted string — holds.
- `agent/messenger.py:335-361` — `CancelledError` handler dedup-winner branch; line 349 is `INTERRUPT_NO_RESUME if _reason == "no_resume" else INTERRUPT_RESUME` — holds.
- `agent/session_completion.py:1156-1184` — `_send_interrupted_message`; line 1180 is the same ternary — holds.
- `agent/cancel_reason.py` — docstring + `get/set_cancel_reason`; the "resume" default and `INTERRUPT_RESUME` references present — holds.
- `agent/session_health.py:2161` — sole writer of `"resume"` via `"no_resume" if _predicted_terminal else "resume"` — holds (verified; issue did not name this file).
- `agent/session_executor.py:708` — third `get_cancel_reason` consumer, checks `== "no_resume"` to suppress `FAILURE_NOTICE`; NOT a send site — holds (verified; issue did not name this file).

**Cited sibling issues/PRs re-checked:**
- #1877 — closed 2026-07-03. Built the reason-aware + deduped interrupt machinery this plan partially reverts (the resume-copy preservation). Its dedup + `no_resume` path stay. The referenced plan doc `docs/plans/session-lifecycle-notification-gaps.md` was archived (not present in `docs/plans/`).

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none. The recent SDLC-router / fork-worktree plans touch unrelated subsystems.

**Notes:** Two files load-bearing to this change were NOT named in the issue and were surfaced by blast-radius: `agent/session_health.py:2161` (the only `"resume"` writer) and `agent/session_executor.py:708` (the `FAILURE_NOTICE`-suppression reader that must be preserved). Both are folded into the plan below.

## Prior Art

- **#1877** — "Session lifecycle gaps: non-reason-aware interrupt message…" (closed 2026-07-03). Made the interrupt message reason-aware and deduped it across the two send sites, and introduced `agent/cancel_reason.py`. This plan reverts one specific decision from #1877 — preserving the resume copy "verbatim so the resume case behaves exactly as before" — while keeping the rest of its machinery for the terminal-fail path.
- **#1919** — "Idle notification swallowing the PM's real answer" (closed). Adjacent lifecycle-copy hygiene; no code overlap.

No prior *failed* fix exists for this exact behavior — #1877 succeeded at what it set to do; the product decision simply changed.

## Data Flow

1. **Entry point**: a killer/shutdown path cancels a running session's asyncio task
   (worker shutdown, health-check kill, deadline kill, recovery re-queue).
2. **Reason write (optional)**: a killer that knows the outcome writes
   `cancel-reason:{session_id}` via `set_cancel_reason` — `"no_resume"` for terminal,
   `"resume"` for re-queue. Absent when it's a plain worker shutdown.
3. **CancelledError propagates** into `agent/messenger.py`'s run loop (handler at 296-368)
   and/or the completion runner (`agent/session_completion.py::_send_interrupted_message`).
4. **Copy selection (the change point)**: today each site reads the reason and picks
   `INTERRUPT_NO_RESUME` (reason `"no_resume"`) or `INTERRUPT_RESUME` (everything else).
   After this change: send `INTERRUPT_NO_RESUME` only for `"no_resume"`; **send nothing**
   otherwise.
5. **Dedup**: both sites SET-NX `interrupted-sent:{session_id}` so only one wins the send.
   Preserved, but now only reached on the `no_resume` path.
6. **Output**: for `no_resume`, the winning site delivers `INTERRUPT_NO_RESUME`. For
   resume/absent, nothing is delivered; the session later resumes and delivers its real
   answer (Finish) — or, on crash, the separate `FAILURE_NOTICE` path in
   `agent/session_executor.py` delivers Fail.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none to public signatures. `set_cancel_reason`/`get_cancel_reason`
  keep their generic string API; only the values written narrow to `"no_resume"`.
- **Coupling**: decreases — one user-facing copy constant and one cancel-reason value
  (`"resume"`) are retired.
- **Data ownership**: unchanged.
- **Reversibility**: trivial — re-add the constant and the `else` branch. Low blast radius.

## Appetite

**Size:** Small

**Team:** Solo dev, plus a validator pass.

**Interactions:**
- PM check-ins: 0 (scope is fully specified by the issue + recon).
- Review rounds: 1 (code review to confirm no-legacy cleanup and no duplicate sends).

## Prerequisites

No prerequisites — this work is internal to `agent/` and has no external dependencies. Redis
(already required by the worker) backs the dedup/cancel-reason keys.

## Solution

### Key Elements

- **`agent/notification_copy.py`**: delete the `INTERRUPT_RESUME` constant and its docstring
  bullet. Keep `INTERRUPT_NO_RESUME` and `FAILURE_NOTICE`.
- **Both send sites** (`agent/messenger.py`, `agent/session_completion.py`): invert the
  gate so a send happens **only** when `get_cancel_reason == "no_resume"`. Absent/`"resume"`
  → no send, no dedup-key acquisition. Preserve the `interrupted-sent` dedup for the
  remaining `no_resume` send (two sites can still race it).
- **`agent/cancel_reason.py`**: the `"resume"` value is now dead everywhere it is read.
  Update the module/function docstrings to drop `INTERRUPT_RESUME` and the resume default.
  Keep the functions generic.
- **`agent/session_health.py:2161`**: stop writing `"resume"` — write `"no_resume"` only
  when the outcome is predicted terminal, otherwise write nothing.
- **`agent/session_executor.py:708`**: unchanged — it reads `== "no_resume"` to suppress a
  duplicate `FAILURE_NOTICE` and is not a send site.

### Flow

Worker restart → cancels session task → `CancelledError` in messenger/completion runner →
read cancel-reason → **absent/"resume": send nothing** → session re-queues → resumes →
delivers real answer.

Terminal kill → killer writes `cancel-reason=no_resume` → `CancelledError` → read reason →
**"no_resume": SET-NX dedup, winner sends `INTERRUPT_NO_RESUME`**.

### Technical Approach

- **Read the reason first, then decide.** At each send site, call `get_cancel_reason` before
  touching the dedup key. `get_cancel_reason` is non-destructive (180s TTL is the sole
  reclaimer), so both racing sites reading it is safe and cannot starve either. Only if the
  reason is `"no_resume"` do we acquire `interrupted-sent` SET-NX and send
  `INTERRUPT_NO_RESUME`. This makes the dedup key acquisition happen strictly on the send
  path, satisfying "remove the now-dead dedup plumbing around the resume send" while
  preserving single-winner semantics for the terminal-fail send.
- **`messenger.py` handler (296-368):** drop the `INTERRUPT_RESUME` import; import only
  `INTERRUPT_NO_RESUME`. Rewrite the body so the non-`no_resume` case falls straight through
  to the `finally` (cancel watchdog, re-raise) with no send. Update the block comment
  (302-311) to describe silent resume + `no_resume`-only send.
- **`session_completion.py::_send_interrupted_message` (1144-1184):** same inversion —
  early-return (silent) unless reason is `"no_resume"`; on `no_resume`, SET-NX then send
  `INTERRUPT_NO_RESUME`. Drop the `INTERRUPT_RESUME` import. Update the function docstring
  and the referencing docstring at ~604 to say "terminal no-resume interrupt" rather than a
  generic "I was interrupted" line.
- **`session_health.py:2161`:** replace the ternary write with a guarded write:
  `if _predicted_terminal: set_cancel_reason(entry.session_id, "no_resume")`. Update the
  2144-2154 comment to drop the resume-prediction rationale (only terminal is signalled now).
- **`cancel_reason.py`:** update docstrings (module 1-31, `set_cancel_reason` 45-58,
  `get_cancel_reason` 69-80) to remove `INTERRUPT_RESUME` and the "`resume` = re-queued"
  semantics. State the signal is now: `"no_resume"` present → terminal no-resume copy;
  absent → silence.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Both send sites keep their `try/except` around the send with a `logger.warning` on
  failure/timeout — assert the warning path still fires on the `no_resume` send (the
  existing `test_send_callback_timeout_swallowed` / `..._exception_swallowed` tests, updated
  to set `no_resume`).
- [ ] The `except Exception` swallow in `get_cancel_reason` / `set_cancel_reason` stays;
  covered by existing `tests/unit/test_cancel_reason.py` Redis-unavailable cases.

### Empty/Invalid Input Handling
- [ ] `get_cancel_reason("")` returns `None` → silent (no send). Existing coverage in
  `test_cancel_reason.py`; confirm the send sites treat `None` as silence.
- [ ] Absent cancel-reason key (plain worker shutdown) → silence. Add/adjust a test asserting
  `send_cb.assert_not_awaited()` at both sites.

### Error State Rendering
- [ ] Terminal-fail rendering preserved: `INTERRUPT_NO_RESUME` still delivered on
  `no_resume`, and `FAILURE_NOTICE` still delivered on crash (unchanged
  `session_executor.py` path). Assert both still reach the chat.
- [ ] Grep-clean check that no "will resume automatically" copy renders anywhere.

## Test Impact

- [ ] `tests/unit/test_messenger_cancelled_error.py::test_cancelled_error_delivers_interrupted_message` — REPLACE: default/no-reason cancel now sends nothing; assert `send_callback.assert_not_awaited()` instead of asserting `"resume automatically" in args[0]`.
- [ ] `tests/unit/test_messenger_cancelled_error.py::test_send_callback_timeout_swallowed` — UPDATE: set `cancel-reason=no_resume` so the send path (and its swallowed timeout) is still exercised, asserting the `INTERRUPT_NO_RESUME` send timed out gracefully.
- [ ] `tests/unit/test_messenger_cancelled_error.py::test_send_callback_exception_swallowed` — UPDATE: same, set `no_resume` to reach the send.
- [ ] `tests/unit/test_messenger_cancelled_error.py::test_duplicate_cancel_within_ttl_does_not_resend` — UPDATE: set `no_resume` so the dedup path is actually exercised (dedup is now reached only on the send path).
- [ ] `tests/unit/test_messenger_cancelled_error.py::test_redis_unavailable_still_sends` — UPDATE: set `no_resume`; confirm redis-unavailable still sends the `INTERRUPT_NO_RESUME` copy.
- [ ] `tests/unit/test_messenger_cancelled_error.py::test_cancelled_error_reraises_after_send` — UPDATE: re-raise must still occur on the silent (no-send) path; assert re-raise with no reason set (rename/retitle to reflect "reraises after handler").
- [ ] `tests/unit/test_deliver_pipeline_completion.py` (import line 24) — UPDATE: drop the `INTERRUPT_RESUME` import.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_cancelled_*` (asserts one `INTERRUPT_RESUME` send, ~L236) — REPLACE: assert `send_cb.assert_not_awaited()` for the default-reason cancel.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_cancelled_interrupted_dedup_suppresses_duplicate` (~L240) — UPDATE: configure redis `get` to return `b"no_resume"` so the dedup path is reached; assert single send + suppressed duplicate.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_unset_reason_sends_resume_copy` (~L305) — REPLACE: rename to `test_unset_reason_sends_nothing`; assert `send_cb.assert_not_awaited()`.
- [ ] `tests/unit/test_deliver_pipeline_completion.py::test_no_resume_reason_sends_no_resume_copy` / `test_dual_fire_winner_sends_no_resume_loser_silent` — KEEP (no_resume path unchanged).
- [ ] `tests/integration/test_pm_final_delivery.py` (import line 26) — UPDATE: drop the `INTERRUPT_RESUME` import.
- [ ] `tests/integration/test_pm_final_delivery.py` resume-copy test (~L247-249) — REPLACE: assert nothing delivered for the unset/resume-reason cancel.
- [ ] `tests/integration/test_pm_final_delivery.py::test_cancelled_error_no_resume_reason_delivers_no_resume_copy` (~L285 references `INTERRUPT_RESUME`) — UPDATE: keep the `no_resume` assertion; drop/replace the `!= INTERRUPT_RESUME` line that references the deleted constant.
- [ ] `tests/integration/test_pm_final_delivery.py::test_cancelled_then_second_cancel_does_not_duplicate_interrupted` — UPDATE: set `no_resume` so the flap-dedup path still produces exactly one send.
- [ ] `tests/unit/test_cancel_reason.py` (round-trips `"resume"`, L48-49) — UPDATE: `set/get` stay generic so the round-trip still passes; retitle the `"resume"` case to a neutral non-`no_resume` value (e.g. `"other"`) since `"resume"` is no longer a produced value, keeping the "non-`no_resume` reads back verbatim" contract.
- [ ] `tests/unit/test_session_executor_failure_notification.py` (L101 patches `get_cancel_reason` → `"resume"`) — UPDATE (cosmetic): the assertion (non-`no_resume` does not suppress `FAILURE_NOTICE`) still holds; swap the `"resume"` sentinel for a neutral value to avoid implying `"resume"` is still produced.

New coverage to add:
- [ ] `tests/unit/test_messenger_cancelled_error.py` — ADD: `test_resume_reason_sends_nothing` and `test_absent_reason_sends_nothing` (both assert `send_callback.assert_not_awaited()` and that the handler still re-raises).

## Rabbit Holes

- **Renaming `_send_interrupted_message` / the `cancel_reason` "kind" API.** Tempting for
  tidiness, but it ripples across imports and tests for no behavioral gain. Keep names;
  update docstrings only.
- **Collapsing the two send sites into one.** They live on genuinely different cancel paths
  (in-loop `CancelledError` vs. completion runner). Unifying them is a separate refactor, not
  this bug fix.
- **Removing the `interrupted-sent` dedup entirely.** It is still needed for the `no_resume`
  send that both sites can race. Do not delete it — only move its acquisition onto the send
  path.
- **Touching `session_executor.py:708`.** It reads `no_resume` to suppress a duplicate
  `FAILURE_NOTICE`; it is correct as-is. Leave it.

## Risks

### Risk 1: A genuine terminal stop goes silent
**Impact:** If the inversion is written so that `no_resume` also falls through to silence, a
user whose session was truly killed would get no notice.
**Mitigation:** Keep the explicit `no_resume` → `INTERRUPT_NO_RESUME` send at both sites; the
KEEP tests (`test_no_resume_reason_sends_no_resume_copy`, the integration `no_resume` test)
guard this. Verification grep confirms `INTERRUPT_NO_RESUME` is still referenced at both send
sites.

### Risk 2: `session_health.py:2161` guard change alters the FAILURE_NOTICE-suppression contract
**Impact:** `session_executor.py:708` suppresses `FAILURE_NOTICE` when reason is `no_resume`.
Previously a non-terminal cancel wrote `"resume"`; now it writes nothing. Both read back as
"not `no_resume`", so suppression behavior is identical — but a regression here would double-
message on the fail path.
**Mitigation:** `test_session_executor_failure_notification.py` covers "non-`no_resume` →
notice not suppressed"; keep it green with the neutral-sentinel update.

## Race Conditions

### Race 1: Two send sites race the terminal `no_resume` send
**Location:** `agent/messenger.py:296-368` and `agent/session_completion.py:1144-1184`.
**Trigger:** the same session is cancelled while both the in-loop handler and the completion
runner are live; both reach the `no_resume` branch.
**Data prerequisite:** `cancel-reason:{session_id}` must be written (`no_resume`) before
either site reads it — killers write it before cancelling (`session_health.py`,
`agent_session_queue.py`).
**State prerequisite:** exactly one `INTERRUPT_NO_RESUME` delivered per session.
**Mitigation:** the `interrupted-sent:{session_id}` SET-NX dedup (120s TTL) — moved onto the
send path — elects a single sender. The non-destructive cancel-reason read means the losing
site cannot starve the winner. Guarded by `test_dual_fire_winner_sends_no_resume_loser_silent`
and the flap-dedup integration test.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item (both send sites, the constant retirement, the dead
`"resume"` write in `session_health.py`, and the `cancel_reason.py` docstring cleanup) is in
scope for this plan.

## Update System

No update system changes required — this feature is purely internal to the worker's
`agent/` package. No new dependencies, config files, migrations, or `scripts/update/`
changes. No Popoto model changes (the cancel-reason/dedup keys are raw transient Redis keys,
not ORM models).

## Agent Integration

No agent integration required — this is a worker-internal change to session-lifecycle
messaging. No CLI entry point, no `mcp_servers/` / `.mcp.json` change, and no new
`bridge/telegram_bridge.py` import. The bridge still relays whatever the worker sends; it
simply receives one fewer lifecycle line.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-lifecycle.md` — remove the "will resume automatically"
  interrupt state from the lifecycle-notification description; state that auto-resuming
  interruptions are silent and only Finish / Fail (`FAILURE_NOTICE`, `INTERRUPT_NO_RESUME`)
  are surfaced.
- [ ] Grep `docs/` for "resume automatically" / `INTERRUPT_RESUME` and scrub any stale
  references (e.g. any lingering mention from the #1877 work).

### Inline Documentation
- [ ] Update module/handler docstrings in `agent/notification_copy.py`,
  `agent/cancel_reason.py`, `agent/messenger.py`, and `agent/session_completion.py` to match
  the silent-resume behavior (no dangling `INTERRUPT_RESUME` references).

No new `docs/features/*.md` file is needed — this modifies existing lifecycle behavior rather
than adding a capability.

## Success Criteria

- [ ] A `/update`-driven worker restart (cancel with absent/`resume` reason) sends **nothing**
  to the chat — asserted by the new silence tests at both send sites.
- [ ] A terminal kill (`cancel-reason=no_resume`) still delivers `INTERRUPT_NO_RESUME`, and a
  crash still delivers `FAILURE_NOTICE` — asserted by the KEEP tests.
- [ ] No "will resume automatically" copy and no `INTERRUPT_RESUME` symbol remain anywhere
  (grep-clean over `agent/`, `tests/`, `docs/`).
- [ ] No duplicate sends on the terminal-fail path (dedup preserved; dual-fire test green).
- [ ] `"resume"` is no longer written by any `set_cancel_reason` call site.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (interrupt-copy-removal)**
  - Name: interrupt-builder
  - Role: Delete `INTERRUPT_RESUME`, invert both send sites to `no_resume`-only, simplify the
    `session_health.py` write, update `cancel_reason.py` docstrings, and update all affected
    tests.
  - Agent Type: builder
  - Domain: async (cancellation/`CancelledError` semantics, Redis dedup race)
  - Resume: true

- **Validator (interrupt-copy-removal)**
  - Name: interrupt-validator
  - Role: Verify silence on resume/absent, `INTERRUPT_NO_RESUME` on `no_resume`,
    `FAILURE_NOTICE` on crash, grep-clean, and no duplicate sends.
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Remove the constant and invert the send sites
- **Task ID**: build-interrupt-removal
- **Depends On**: none
- **Validates**: tests/unit/test_messenger_cancelled_error.py, tests/unit/test_deliver_pipeline_completion.py, tests/integration/test_pm_final_delivery.py, tests/unit/test_cancel_reason.py, tests/unit/test_session_executor_failure_notification.py
- **Assigned To**: interrupt-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `INTERRUPT_RESUME` (constant + docstring bullet) from `agent/notification_copy.py`.
- In `agent/messenger.py:296-368`: read cancel-reason first; send `INTERRUPT_NO_RESUME`
  (behind `interrupted-sent` SET-NX) only when reason is `"no_resume"`; otherwise fall
  through silently to `finally` (watchdog cancel + re-raise). Drop the `INTERRUPT_RESUME`
  import; update comments.
- In `agent/session_completion.py::_send_interrupted_message`: early-return (silent) unless
  reason is `"no_resume"`; on `no_resume`, SET-NX then send `INTERRUPT_NO_RESUME`. Drop the
  `INTERRUPT_RESUME` import; update this + the ~L604 docstring.
- In `agent/session_health.py:2161`: write `"no_resume"` only when predicted terminal, else
  no write; update the 2144-2154 comment.
- In `agent/cancel_reason.py`: update module + function docstrings to drop `INTERRUPT_RESUME`
  and the `"resume"` semantics.
- Update all tests per the Test Impact section and add the two new silence tests.
- Run `python -m ruff format .` (no lint per repo rule).

### 2. Validate
- **Task ID**: validate-interrupt-removal
- **Depends On**: build-interrupt-removal
- **Assigned To**: interrupt-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table below; confirm all Success Criteria; report pass/fail.

### 3. Documentation
- **Task ID**: document-interrupt-removal
- **Depends On**: build-interrupt-removal
- **Assigned To**: interrupt-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-lifecycle.md` and scrub stale "resume automatically"
  references across `docs/`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_messenger_cancelled_error.py tests/unit/test_deliver_pipeline_completion.py tests/unit/test_cancel_reason.py tests/unit/test_session_executor_failure_notification.py tests/integration/test_pm_final_delivery.py -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| INTERRUPT_RESUME retired (code) | `grep -rn "INTERRUPT_RESUME" agent/` | exit code 1 |
| No resume-copy anywhere | `grep -rn "will resume automatically" agent/ tests/ docs/` | exit code 1 |
| No "resume" cancel-reason written | `grep -rn 'set_cancel_reason([^)]*"resume"' agent/` | exit code 1 |
| no_resume send preserved (messenger) | `grep -c "INTERRUPT_NO_RESUME" agent/messenger.py` | output > 0 |
| no_resume send preserved (completion) | `grep -c "INTERRUPT_NO_RESUME" agent/session_completion.py` | output > 0 |
| FAILURE_NOTICE suppression preserved | `grep -c 'get_cancel_reason(session_id) == "no_resume"' agent/session_executor.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None blocking. The issue plus recon fully specify scope. Two derived decisions are recorded
as defaults (raise if the supervisor disagrees):

1. **Silence == absent reason.** Both an absent cancel-reason and the retired `"resume"`
   value map to silence; only `"no_resume"` sends. This is the plan's core interpretation of
   "an interruption that will auto-resume must be silent."
2. **Keep `cancel_reason.py`'s generic string API.** `set/get_cancel_reason` stay generic
   (docstrings updated) rather than being hard-narrowed to a boolean, to minimize churn and
   keep `session_executor.py:708`'s reader untouched.
