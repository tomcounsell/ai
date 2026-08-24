---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-07
tracking: https://github.com/tomcounsell/ai/issues/1937
last_comment_id:
revision_applied: true
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
2. **Reason write (optional, post-change contract)**: after this change the **only** value any
   caller writes is `"no_resume"`, and only when the outcome is predicted terminal
   (`session_health.py:2161` writes it iff `_predicted_terminal`; other killers write it for a
   known-terminal kill). Every non-terminal path — re-queue, plain worker shutdown, an
   unpredicted-terminal escalation before its re-stamp — writes **nothing**, leaving the
   `cancel-reason:{session_id}` key **absent**. The retired `"resume"` value is never written by
   any call site. So the reader's world shrinks to two states: `"no_resume"` present (a killer
   owns a terminal narrative) or absent (silence).
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
7. **Late-terminal escalation (the silent-terminal gap this inversion introduces)**: when the
   pre-cancel prediction was non-terminal, step 2 wrote no reason, so the send sites in steps
   4-6 stayed silent and never took the dedup key. If the subprocess then survives
   cancel+SIGTERM+SIGKILL, `session_health.py`'s escalation branch (2330-2346) finalizes the
   session to the terminal `failed` status *after* that window. This branch now re-stamps
   `no_resume` **and may deliver `INTERRUPT_NO_RESUME` itself** — but only as a **last-resort
   voice**, because the same branch already runs two other user-facing deliveries on **disjoint**
   dedup keys: `_deliver_deferred_self_draft_fallback` (delivers the real answer;
   `deferred_self_draft_pending` gate) and, for `reason_kind == "tool_timeout"`,
   `_deliver_tool_timeout_degraded_notice` (key `tool_timeout:degraded_sent`). Those keys do
   **not** dedup against `interrupted-sent`, so an unconditional terminal send here would
   double-message. Two guards make the terminal notice fire **exactly once, and only when the
   branch would otherwise be silent**:
   - **Code-level gate** against the sibling deliveries: send the terminal notice only when
     `not _has_deferred and not _degraded_sent` (no real answer went out, and no degraded notice
     went out). This is the guard the shared key cannot provide.
   - **Shared `interrupted-sent:{session_id}` SET-NX** (120s TTL — the same key and TTL the two
     send sites use) against the messenger/completion send sites: if a predicted-terminal cancel
     already fired `INTERRUPT_NO_RESUME` from a send site, that site holds the key and the helper
     skips. If Redis is unreachable during acquisition, the helper **sends anyway** (fail-safe,
     matching both send sites and `_deliver_tool_timeout_degraded_notice`).
   The helper is correct whether or not an earlier send fired: the shared key dedups the
   send-site case, the code gate dedups the sibling-helper case, so a genuinely terminal failure
   is neither silently dropped nor double-announced.

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
- **`agent/session_health.py:2330-2346` (subprocess-survived escalation branch)**: this
  branch escalates a session to the terminal `failed` status *after* the cancel/send window
  has already passed. Under the old copy, the pre-cancel prediction wrote `"resume"`, so the
  send sites already fired the resume line and the branch merely "degraded safely." Under the
  new copy, the pre-cancel prediction writes **nothing** for a non-terminal prediction, so the
  send sites stayed **silent** and never acquired the `interrupted-sent` dedup key. If this
  branch only re-stamps `no_resume` (as today), a genuinely terminal failure could be delivered
  to the user as **complete silence** — a regression the inversion introduces. But this same
  branch **already runs two other user-facing deliveries** before finalizing:
  `_deliver_deferred_self_draft_fallback(entry)` (unconditional — delivers the real answer when
  `deferred_self_draft_pending` is set) and, when `not _has_deferred and reason_kind ==
  "tool_timeout"`, `_deliver_tool_timeout_degraded_notice(entry, tool_name)`. Those helpers use
  **disjoint** dedup keys (`deferred_self_draft` state and `tool_timeout:degraded_sent`
  respectively) that do **not** dedup against `interrupted-sent`. Dropping an **unconditional**
  terminal send into this branch therefore double-messages on essentially every
  subprocess-survived escalation (the real answer / degraded notice already spoke). **Fix
  (critique BLOCKER):** after re-stamping `no_resume`, capture whether the degraded notice fired
  (`_degraded_sent`) and gate the terminal send so it fires **only when the branch would
  otherwise be silent**:

  ```python
  set_cancel_reason(entry.session_id, "no_resume")
  _has_deferred = (getattr(entry, "extra_context", None) or {}).get("deferred_self_draft_pending")
  await _deliver_deferred_self_draft_fallback(entry)
  _degraded_sent = False
  if not _has_deferred and reason_kind == "tool_timeout":
      await _deliver_tool_timeout_degraded_notice(entry, tool_name)
      _degraded_sent = True
  # Last-resort terminal voice: only when neither the real answer nor the
  # degraded notice already spoke, so the branch never double-messages.
  if not _has_deferred and not _degraded_sent:
      await _deliver_terminal_interrupt_notice(entry)
  ```

  The `interrupted-sent:{session_id}` SET-NX inside the helper still dedups against the two
  send sites (if a predicted-terminal cancel already fired from a send site, the helper skips);
  the **code-level gate** above is what dedups against the two sibling helper deliveries the
  shared key cannot see. Together they guarantee **exactly one** user-facing send from this
  branch. Add a small helper `_deliver_terminal_interrupt_notice(entry)` — see the Technical
  Approach for its exact dedup key/TTL and fail-safe contract, and the NIT-consolidation note
  that factors its delivery mechanics out of `_deliver_tool_timeout_degraded_notice`.
- **`agent/session_executor.py:703-714`**: the `== "no_resume"` read stays (it suppresses a
  duplicate `FAILURE_NOTICE`), **but its surrounding comment (703-707) and the docstring at
  690-694 must be corrected** — both currently narrate a "stale `resume` reason from an
  interrupt-and-requeue" scenario that can no longer occur (`"resume"` is never written after
  this change). Rewrite the prose to describe the new signal set only (`"no_resume"` present →
  a killer owns the terminal narrative; absent → no killer narrative). The code logic is
  unchanged; only the comment/docstring text changes so it stops contradicting reality.

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
  **2144-2154 pre-cancel comment** to drop the resume-prediction rationale (only terminal is
  signalled now), and specifically rewrite the stale sentence at **2150-2152** —
  "…degrades safely to the resume copy (pre-#1877 behavior) if the send already fired" —
  since there is no longer any resume copy to degrade to. The escalation branch now delivers
  the terminal notice explicitly (below), so the comment must describe *that*, not a silent
  degradation.
- **`session_health.py:2330-2346` (subprocess-survived escalation branch):** keep the
  `set_cancel_reason(entry.session_id, "no_resume")` re-stamp, then apply the gated-send block
  shown in the Solution (`_degraded_sent` capture + `if not _has_deferred and not
  _degraded_sent: await _deliver_terminal_interrupt_notice(entry)`). Rewrite the branch's stale
  comment at **2338-2343** ("if the interrupt send already fired it degrades safely to the
  resume copy") to describe the new gated last-resort send.

  The new `_deliver_terminal_interrupt_notice(entry)` helper's **delivery mechanics** (transport
  resolution via `_resolve_callbacks` from `extra_context`, `FileOutputHandler` fallback, WARNING
  swallow so finalization never blocks) mirror `_deliver_tool_timeout_degraded_notice`, but the
  **dedup key, TTL, and message differ** — this is the reconciliation the critique flagged
  (Concern 2): do **not** copy the model helper's `tool_timeout:degraded_sent:{session_id}` key
  or its 3600s TTL. The terminal helper MUST use:
  - **Dedup key:** `interrupted-sent:{session_id}` — the exact shared key the two send sites use,
    so it dedups against a hypothetical earlier send-site delivery.
  - **TTL:** `ex=120` — matching the send sites (`messenger.py:319`,
    `session_completion.py:1156`), **not** the degraded helper's `ex=3600`.
  - **Message:** the `INTERRUPT_NO_RESUME` copy (not the degraded-service string).

  **Fail-safe on Redis outage (Concern 1):** the SET-NX must fail *open*, exactly like the two
  send sites (`"…lock failed…; sending anyway"` / `"…lock unavailable…; sending anyway"`) and the
  model helper (`"…lock failed…; proceeding anyway"`). Only a **successful** `acquired is False`
  (key legitimately already held) suppresses the send. A Redis **exception** during acquisition
  is caught, logged at WARNING as a dedup failure, and the helper **still sends** the notice —
  never swallow the terminal send just because the dedup key could not be reached. Structure:

  ```python
  try:
      acquired = POPOTO_REDIS_DB.set(f"interrupted-sent:{session_id}", "1", nx=True, ex=120)
      if acquired is False:            # legitimately already held -> the one send happened elsewhere
          return
  except Exception as _lock_err:       # Redis unreachable -> fail open, still send
      logger.warning("[session-health] interrupted-sent lock failed for %s: %s; sending anyway",
                     session_id, _lock_err)
  # ...resolve send_cb, await send_cb(chat_id, INTERRUPT_NO_RESUME, telegram_message_id, entry)
  ```

  **NIT — helper duplication (eliminate, don't note-and-leave):** the terminal helper and
  `_deliver_tool_timeout_degraded_notice` would otherwise be ~90% identical (same callback
  resolution, fallback, telemetry-swallow, fail-open lock). Factor the shared mechanics into one
  private `_deliver_oneshot_dedup_notice(entry, *, dedup_key, ttl, message)` that both call —
  the degraded helper passes `(dedup_key="tool_timeout:degraded_sent:{sid}", ttl=3600,
  message=<degraded copy>)`, the terminal helper passes `(dedup_key="interrupted-sent:{sid}",
  ttl=120, message=INTERRUPT_NO_RESUME)`. This keeps the differing key/TTL/message as the only
  parameters and prevents a second near-duplicate helper (repo NO-LEGACY rule). Refactoring the
  existing degraded helper to route through the shared core is in scope; its behavior and its
  own dedup key/TTL are unchanged (covered by existing degraded-notice tests).
- **`session_executor.py:703-714` + docstring 690-694:** the `== "no_resume"` guard is correct
  and stays. Update only the prose: delete the "stale `resume` reason from an
  interrupt-and-requeue" narrative (that state is now impossible) and restate the contract as
  "`no_resume` present → a killer owns the terminal exit narrative, suppress; absent → no
  killer narrative, send the failure notice." This is a comment/docstring-only edit — no logic
  change.
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
- [ ] Grep-clean check that the deleted literal `"I was interrupted and will resume
  automatically"` renders nowhere. (Scope the grep to the exact literal — the retained
  `INTERRUPT_NO_RESUME` docstring legitimately contains "…Nothing will resume automatically",
  so a broad `"will resume automatically"` grep would false-positive.)

### Real-Answer-Survives-Resume Coverage
- [ ] **Interrupt→auto-resume→real answer delivered (integration).** Add a test to
  `tests/integration/test_pm_final_delivery.py` that: (1) interrupts a session with an
  absent/non-terminal cancel-reason, (2) asserts **zero** interim lifecycle sends
  (`send_cb.assert_not_awaited()` across the interrupt window), (3) drives the resumed session
  to completion, and (4) asserts the real work-product message is delivered exactly once. This
  is the guardrail that silencing the interrupt copy did not swallow the answer (Success
  Criterion "End-to-end resume still delivers the real answer"). If a full resume cannot be
  simulated in-process, split into (a) the in-process "zero interim sends" assertion above and
  (b) a documented manual verification: trigger a `/update` restart mid-session in a test chat
  and confirm the chat shows no interrupt line but does receive the eventual answer — record
  the observation in the PR.

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

- [ ] `tests/unit/test_session_health_subprocess_kill.py` — UPDATE: the subprocess-survived escalation branch now delivers `INTERRUPT_NO_RESUME`; any existing assertion that the branch sends nothing must be updated. Existing kill/finalize assertions (status → `failed`, orphan-reaper ownership) stay.

New coverage to add:
- [ ] `tests/unit/test_messenger_cancelled_error.py` — ADD: `test_resume_reason_sends_nothing` and `test_absent_reason_sends_nothing` (both assert `send_callback.assert_not_awaited()` and that the handler still re-raises).
- [ ] `tests/unit/test_session_health_subprocess_kill.py` — ADD: `test_subprocess_survived_escalation_delivers_no_resume_when_no_earlier_send` (pre-cancel prediction non-terminal → send sites silent → subprocess survives → escalation branch delivers exactly one `INTERRUPT_NO_RESUME`) and `test_escalation_send_deduped_when_interrupted_sent_already_held` (if the shared `interrupted-sent` key is already held, the escalation branch sends nothing — no double message).
- [ ] `tests/unit/test_session_health_subprocess_kill.py` — ADD (critique BLOCKER guard): `test_escalation_branch_emits_exactly_one_user_facing_send`. Assert the subprocess-survived escalation branch produces **exactly one** user-facing delivery across all three sends by spying `_deliver_deferred_self_draft_fallback`, `_deliver_tool_timeout_degraded_notice`, and the terminal helper. Three cases: (i) deferred self-draft pending → real answer sent, `_degraded_sent` False, terminal notice **not** sent (gated by `not _has_deferred`); (ii) `reason_kind == "tool_timeout"`, no deferred → degraded notice sent, terminal notice **not** sent (gated by `not _degraded_sent`); (iii) no deferred, non-`tool_timeout` kind → terminal notice **is** the only send. In every case total user-facing sends from the branch == 1 (no double message).
- [ ] `tests/unit/test_session_health_subprocess_kill.py` — ADD (Concern 1 fail-safe): `test_terminal_interrupt_notice_sends_when_dedup_redis_errors`. Patch the `interrupted-sent` SET-NX to raise → assert the terminal helper logs the dedup failure at WARNING and **still** awaits the send (fail-open), and that a legitimate `acquired is False` (key already held) instead suppresses the send.
- [ ] `tests/integration/test_pm_final_delivery.py` — ADD: `test_interrupt_resume_delivers_real_answer_with_zero_interim_sends` (absent-reason interrupt → zero interim lifecycle sends → resumed session's real work-product still delivered exactly once). Backs the Success Criterion for Concern 4.

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
- **Changing `session_executor.py:708` *logic*.** The `== "no_resume"` read that suppresses a
  duplicate `FAILURE_NOTICE` is correct as-is — leave the logic. (The comment/docstring prose
  around it, 690-694 and 703-707, *does* get corrected — see Technical Approach — because it
  narrates a now-impossible `"resume"` state, but that is a text-only edit, not a logic touch.)

## Risks

### Risk 1: A genuine terminal stop goes silent
**Impact:** Two ways this could happen. (a) If the inversion is written so that `no_resume`
also falls through to silence, a user whose session was truly killed would get no notice.
(b) **The late-terminal escalation gap:** the subprocess-survived branch in
`session_health.py` predicts non-terminal *before* the cancel (so writes nothing → the send
sites stay silent), then escalates to `failed` *after* the cancel. Without an explicit send
there, a genuinely terminal failure would be delivered as complete silence — the exact
regression this inversion could introduce.
**Mitigation:** For (a), keep the explicit `no_resume` → `INTERRUPT_NO_RESUME` send at both
sites; the KEEP tests (`test_no_resume_reason_sends_no_resume_copy`, the integration
`no_resume` test) guard this. For (b), the escalation branch now owns a **gated** last-resort
terminal send via `_deliver_terminal_interrupt_notice` — fired only when
`not _has_deferred and not _degraded_sent` (so it never double-messages the sibling
`_deliver_deferred_self_draft_fallback` / `_deliver_tool_timeout_degraded_notice` deliveries),
and additionally deduped against the two send sites by the shared `interrupted-sent` SET-NX.
This introduces its own hazard — a **double message** — if the gate is dropped; that is covered
by two new unit tests: one asserting the escalation path delivers exactly **one**
`INTERRUPT_NO_RESUME` when the branch is otherwise silent, and one asserting **zero** terminal
sends when a deferred self-draft or degraded notice already fired (the sibling-delivery gate).
Verification grep confirms `INTERRUPT_NO_RESUME` is referenced at both send sites and in
`session_health.py`.

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

Nothing deferred — every relevant item is in scope for this plan: both send sites, the
constant retirement, the dead `"resume"` write in `session_health.py`, the new
escalation-branch terminal send that closes the silent-terminal gap, the stale-comment
corrections in `session_health.py` (2150-2152, 2338-2343) and `session_executor.py`
(690-694, 703-707), and the `cancel_reason.py` docstring cleanup.

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
- [ ] Scrub `docs/features/session-isolation.md:222` — the watchdog paragraph states the
  `CancelledError` handler "sends 'I was interrupted and will resume automatically' to the
  user." Rewrite it to reflect the silent-resume behavior (auto-resuming interruptions are
  silent; only terminal `INTERRUPT_NO_RESUME` / `FAILURE_NOTICE` surface).
- [ ] Grep `docs/features/` for "resume automatically" / `INTERRUPT_RESUME` and scrub any
  remaining stale references from the #1877 work. (Leave `docs/plans/completed/*.md` and this
  plan doc untouched — they are historical record and legitimately quote the retired copy.)

### Inline Documentation
- [ ] Update module/handler docstrings in `agent/notification_copy.py`,
  `agent/cancel_reason.py`, `agent/messenger.py`, and `agent/session_completion.py` to match
  the silent-resume behavior (no dangling `INTERRUPT_RESUME` references).

No new `docs/features/*.md` file is needed — this modifies existing lifecycle behavior rather
than adding a capability.

## Success Criteria

- [ ] A `/update`-driven worker restart (cancel with absent/`resume` reason) sends **nothing**
  to the chat — asserted by the new silence tests at both send sites.
- [ ] **End-to-end resume still delivers the real answer.** When a session is interrupted with
  an absent/non-terminal reason and then auto-resumes, the chat receives **zero** interim
  lifecycle messages *and* the eventual real work-product message is still delivered. Silencing
  the interrupt copy must not swallow the actual answer. Verified by an integration test
  (below) — the point of this whole change is fewer noise lines, not a lost answer.
- [ ] A terminal kill (`cancel-reason=no_resume`) still delivers `INTERRUPT_NO_RESUME`, and a
  crash still delivers `FAILURE_NOTICE` — asserted by the KEEP tests.
- [ ] The deleted literal `"I was interrupted and will resume automatically"` and the
  `INTERRUPT_RESUME` symbol remain nowhere in **code or active docs** — grep-clean over
  `agent/`, `tests/`, and `docs/features/`, scoped to the exact literal so the retained
  `INTERRUPT_NO_RESUME` docstring does not false-fail it. (Archived `docs/plans/completed/*`
  and this plan doc keep the historical quote by design.)
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
- **Validates**: tests/unit/test_messenger_cancelled_error.py, tests/unit/test_deliver_pipeline_completion.py, tests/integration/test_pm_final_delivery.py, tests/unit/test_cancel_reason.py, tests/unit/test_session_executor_failure_notification.py, tests/unit/test_session_health_subprocess_kill.py
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
  no write; update the 2144-2154 pre-cancel comment, including the stale 2150-2152
  "degrades safely to the resume copy" sentence.
- In `agent/session_health.py:2330-2346` (subprocess-survived escalation branch): after the
  `no_resume` re-stamp, capture `_degraded_sent` and add the **gated** terminal send
  `if not _has_deferred and not _degraded_sent: await _deliver_terminal_interrupt_notice(entry)`
  (per the Solution block) so the branch never double-messages the sibling
  `_deliver_deferred_self_draft_fallback` / `_deliver_tool_timeout_degraded_notice` deliveries.
  Add the new `_deliver_terminal_interrupt_notice(entry)` helper using dedup key
  `interrupted-sent:{session_id}` with `ex=120` (NOT the degraded helper's key/TTL), delivering
  `INTERRUPT_NO_RESUME` via `_resolve_callbacks`, **failing open** on a Redis SET-NX error
  (log WARNING, still send), never raising. Factor the shared delivery mechanics into
  `_deliver_oneshot_dedup_notice(entry, *, dedup_key, ttl, message)` and route both the terminal
  helper and `_deliver_tool_timeout_degraded_notice` through it (NIT — eliminate duplication).
  Rewrite the stale 2338-2343 comment to describe the gated last-resort send.
- In `agent/session_executor.py` (docstring 690-694, comment 703-707): correct the prose to
  drop the now-impossible "stale `resume` reason" narrative; leave the `== "no_resume"` logic.
- In `agent/cancel_reason.py`: update module + function docstrings to drop `INTERRUPT_RESUME`
  and the `"resume"` semantics.
- Update all tests per the Test Impact section and add the new silence + escalation-send tests.
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
| Deleted resume-copy literal gone (code) | `grep -rn "I was interrupted and will resume automatically" agent/ tests/` | exit code 1 |
| Active feature docs scrubbed | `grep -rn "I was interrupted and will resume automatically" docs/features/` | exit code 1 |
| No "resume" cancel-reason written | `grep -rn 'set_cancel_reason([^)]*"resume"' agent/` | exit code 1 |
| no_resume send preserved (messenger) | `grep -c "INTERRUPT_NO_RESUME" agent/messenger.py` | output > 0 |
| no_resume send preserved (completion) | `grep -c "INTERRUPT_NO_RESUME" agent/session_completion.py` | output > 0 |
| FAILURE_NOTICE suppression preserved | `grep -c 'get_cancel_reason(session_id) == "no_resume"' agent/session_executor.py` | output > 0 |
| Terminal-interrupt notice sent from escalation branch | `grep -c "INTERRUPT_NO_RESUME" agent/session_health.py` | output > 0 |

> **Grep-gate note (critique BLOCKER fix).** The retained `INTERRUPT_NO_RESUME`
> docstring in `agent/notification_copy.py` legitimately contains the substring
> "…Nothing will resume automatically." A broad `grep -rn "will resume
> automatically"` would therefore false-fail this plan's own gate (exit 0 instead
> of 1) and could bait a builder into deleting correct retained copy just to force
> exit 1. The gate is scoped to the **exact deleted literal** — `"I was interrupted
> and will resume automatically"` — which is unique to the retired
> `INTERRUPT_RESUME` constant and its copy-asserting tests. That literal must be
> gone; the `INTERRUPT_NO_RESUME` docstring wording is deliberately untouched.
>
> The automated gate is scoped to `agent/ tests/` (code) and `docs/features/` (active docs),
> **not** the whole `docs/` tree. Archived `docs/plans/completed/*.md` and this plan document
> itself legitimately quote the deleted literal as historical record; a `grep -rn … docs/`
> gate would false-fail on those. `docs/features/session-isolation.md:222` actively describes
> the old copy and **is** in scope for the Documentation scrub.

## Critique Results

**Critique verdict (2026-07-07): NEEDS REVISION.** Revision pass addressed all findings:

- **BLOCKER — grep-gate collision.** The acceptance grep is narrowed from the broad
  `"will resume automatically"` to the exact deleted literal
  `"I was interrupted and will resume automatically"`, so the retained `INTERRUPT_NO_RESUME`
  docstring ("…Nothing will resume automatically") no longer false-fails the gate. See
  Verification table + grep-gate note, and the matching wording in Failure Path Test Strategy
  and Success Criteria.
- **Concern 1 — silent terminal via subprocess-survived escalation.** The escalation branch
  (`session_health.py:2330-2346`) now owns acquiring the shared `interrupted-sent` dedup key
  and sending `INTERRUPT_NO_RESUME` via a new `_deliver_terminal_interrupt_notice` helper. See
  Solution, Data Flow step 7, Technical Approach, Risk 1(b), task 1, and the new escalation
  unit tests.
- **Concern 2 — stale "degrades to resume copy" comment (2150-2152).** Explicitly listed for
  update in the file-level task list (Solution, Technical Approach `session_health.py:2161`
  bullet, task 1), alongside the escalation-branch comment at 2338-2343.
- **Concern 3 — stale "resume" narrative in `session_executor.py:703-707` (+ docstring
  690-694).** Reclassified from "unchanged" to a comment/docstring-only correction (logic
  stays). See Solution, Technical Approach, Rabbit Holes, and task 1.
- **Concern 4 — no criterion for real answer surviving resume.** Added Success Criterion
  "End-to-end resume still delivers the real answer" plus an integration test
  (`test_interrupt_resume_delivers_real_answer_with_zero_interim_sends`) and a documented
  manual-verification fallback. See Success Criteria, Failure Path Test Strategy, Test Impact.

**Critique verdict (2026-07-08, second pass): NEEDS REVISION.** Second revision pass addressed
all findings:

- **BLOCKER — escalation branch double-messages the user.** The prior revision dropped an
  *unconditional* `_deliver_terminal_interrupt_notice(entry)` into the
  `elif not _subprocess_confirmed_dead:` branch, which already fires
  `_deliver_deferred_self_draft_fallback` (the real answer) and, on `tool_timeout`,
  `_deliver_tool_timeout_degraded_notice` — both on disjoint dedup keys that never dedup against
  `interrupted-sent`, so the new send fired on essentially every escalation → guaranteed double
  message. Fixed: the send is now gated `if not _has_deferred and not _degraded_sent:
  await _deliver_terminal_interrupt_notice(entry)`, with `_degraded_sent` captured when the
  degraded notice fires. See Solution (`session_health.py:2330-2346` bullet, code block), Data
  Flow step 7, Technical Approach, Risk 1(b), task 1, and the new
  `test_escalation_branch_emits_exactly_one_user_facing_send` unit test asserting exactly one
  user-facing send from this branch.
- **Concern 1 — helper goes silent on Redis outage.** Reconciled: the terminal helper now
  **fails open** — a Redis exception during `interrupted-sent` SET-NX is logged at WARNING and the
  send proceeds anyway (matching both send sites and the model helper); only a successful
  `acquired is False` suppresses. See Technical Approach (fail-safe code block), task 1, and the
  new `test_terminal_interrupt_notice_sends_when_dedup_redis_errors` test.
- **Concern 2 — "follow exact structure" vs required key/TTL.** Reconciled: the plan now
  separates the helper's *delivery mechanics* (mirror `_deliver_tool_timeout_degraded_notice`)
  from its *dedup identity*, which MUST be `interrupted-sent:{session_id}` at `ex=120` — NOT the
  degraded helper's `tool_timeout:degraded_sent` / `ex=3600`. See Technical Approach
  (`session_health.py:2330-2346` bullet).
- **Concern 3 — stale write-contract narrative in Data Flow step 2.** Rewritten: step 2 now
  states the post-change contract (`"no_resume"` is the only value ever written, and only when
  predicted terminal; all other paths leave the key absent; `"resume"` is never written).
- **NIT — helper duplication.** Eliminated (not just noted): the shared delivery mechanics are
  factored into `_deliver_oneshot_dedup_notice(entry, *, dedup_key, ttl, message)`, called by
  both the terminal helper and `_deliver_tool_timeout_degraded_notice`. See Technical Approach
  and task 1.

<!-- Above populated during the revision pass; /do-plan-critique may append its next verdict. -->

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
