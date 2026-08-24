---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-07-03
tracking: https://github.com/tomcounsell/ai/issues/1877
last_comment_id:
revision_applied: true
---

# Session Lifecycle Notification Gaps

## Problem

The worker is the sole session-execution engine. When a session's asyncio task is cancelled or crashes, several code paths decide what (if anything) the user sees in Telegram and what observability tooling captures. Four independently-verified defects live on that "how does the user find out what happened to their session" surface.

**Current behavior:**

1. **Cancellation message is not reason-aware.** `agent/messenger.py:339` and `agent/session_completion.py:1172` both hardcode `"I was interrupted and will resume automatically. No action needed."` and send it unconditionally on any `asyncio.CancelledError`. But `agent/agent_session_queue.py:1899-1979` already distinguishes three cancellation causes — deadline kill (finalized `cancelled`, no resume), out-of-band cleanup (already finalized elsewhere, or re-queued to `pending`), and genuine worker shutdown (the one case startup recovery re-queues). A user whose session was killed/superseded is told "will resume automatically" when nothing will.
2. **Silent running→failed.** In `agent/session_executor.py:1991-2079`, three finalize-on-failure paths persist `final_status = "failed"` but none call `messenger._send_callback(...)`. A session that dies from an uncaught exception fails silently — no Telegram message at all.
3. **No worker-process Sentry.** `sentry_sdk.init()` exists only in `bridge/telegram_bridge.py:74-85`; `grep -rl sentry_sdk worker/*.py` is empty. Since execution happens in the worker, every SDK/tool/lifecycle exception (including the defect #2 crash) is invisible to Sentry.
4. **Over-eager dedup kill.** `scripts/update/run.py::_cleanup_duplicate_sessions` (line 300) treats `("completed", "killed", "abandoned", "failed")` as "already handled", killing any matching `pending` retry. Only `completed` means the message was actually handled — a legitimate retry after a `failed`/`killed`/`abandoned` attempt gets silently dropped.

**Desired outcome:**
- The interrupt message accurately distinguishes "will resume" from "will not resume".
- running→failed always produces a best-effort user-facing Telegram notification, mirroring the CancelledError best-effort pattern.
- Worker-process exceptions reach Sentry with the same fidelity as bridge exceptions, reusing (not duplicating) whatever environment-gating #1834 lands.
- `_cleanup_duplicate_sessions` treats only `completed` as "already handled".

## Freshness Check

**Baseline commit:** ebd017c24
**Issue filed at:** 2026-07-03T05:16:27Z
**Disposition:** Unchanged

**File:line references re-verified (all still hold at baseline):**
- `agent/messenger.py:339` — hardcoded interrupted string inside `except asyncio.CancelledError` — confirmed (handler at 296-355, raw-Redis dedup key `interrupted-sent:{session_id}` 120s TTL).
- `agent/session_completion.py:1172` — identical string in completion-runner interrupted send — confirmed.
- `agent/agent_session_queue.py:1899-1979` — three-branch cancellation disambiguation via `current_task().cancelling()` (Branch 1 deadline, Branch 2 cleanup-artifact, Branch 3 worker shutdown) — confirmed, none threaded to the message.
- `agent/session_executor.py:1991-2079` — three finalize-on-failure paths (`complete_transcript`, `finalize_session` fallback, second `complete_transcript` fallback), zero `messenger`/`send_callback` calls — confirmed. `messenger` is in scope (built at :1477 with `_send_callback=send_to_chat`).
- `worker/__main__.py` — no `sentry_sdk` reference; `main()` at 1340, `asyncio.run(_run_worker(...))` at 1386 — confirmed.
- `scripts/update/run.py:272-329` — terminal-status tuple `("completed", "killed", "abandoned", "failed")` at line 300 — confirmed.

**Cited sibling issues/PRs re-checked:**
- #1834 — **still OPEN, no PR filed** (searched open/all PRs, none found). This is a live sequencing dependency for defect #3.
- #1058, #1767, #986/#989 — historical context, already merged; establish the patterns this plan refines (not blockers).

**Commits on main since issue was filed (touching referenced files):** none (issue filed <2 min before plan; `git log --since` empty).

**Active plans in `docs/plans/` overlapping this area:** none touching `session_executor` failure path / `messenger` interrupt / `_cleanup_duplicate_sessions` / worker Sentry. `docs/plans/sentry_hibernation_filter.md` is the origin of the bridge's `_sentry_before_send`/`is_hibernating` filter — reference, not overlap.

**Notes:** No drift; all line numbers exact at baseline.

## Prior Art

No prior issues/PRs attempted these specific fixes (searched closed issues + merged PRs for "interrupted resume message sentry worker" / "cleanup duplicate sessions failed" — no matches). Relevant established patterns:

- **#1058** — introduced the CancelledError best-effort interrupted-message pattern and its `interrupted-sent:{session_id}` dedup key. Defect #1 refines this pattern; it does not replace it.
- **#1834 (open)** — will gate the *existing bridge-side* `sentry_sdk.init()` on environment (`PYTEST_CURRENT_TEST`, machine gating). Defect #3 must reuse whatever gating it lands rather than duplicate it.
- **#1767** — post-restart dead-worker sweep finalizes dead-worker sessions as `killed`; a `killed` session there is legitimately unhandled work — the same class defect #4 protects (a `killed`/`failed` attempt must not suppress a `pending` retry).
- **`sentry_hibernation_filter.md`** — the bridge's `_sentry_before_send` drops events while hibernating; the worker init should reuse the same `before_send` so worker events honor hibernation too.

## Why Previous Fixes Failed

No prior failed fixes — these are first-time defect fixes, not repeated attempts. Section retained only to record that #1058 (the pattern defect #1 refines) did not fail; it simply predates the need for reason-awareness.

## Data Flow

**Cancellation path (defects #1):**
1. A killer decides to cancel: `agent_session_queue.py` deadline check (Branch 1), an out-of-band killer (`_apply_recovery_transition`, health-check kill, supersede — Branch 2), or asyncio cancelling the worker-loop task on shutdown (Branch 3).
2. `CancelledError` propagates down into the executor's inner task → `agent/messenger.py` shutdown handler (296-355) fires → best-effort `_send_callback("I was interrupted...")` → re-raises.
3. On the completion-runner path, `agent/session_completion.py` (1145-1176) sends the same string via `send_cb`.
4. Status finalize ordering: Branch 1 finalizes to `cancelled` *before* `exec_task.cancel()`; Branch 2 killers finalize (or re-queue to `pending`) before/around the cancel; Branch 3 leaves status `running` (startup recovery re-queues later). **The reason must reach the ORM-free messenger without an ORM read.**

**Failure path (defect #2):**
1. `session_executor.py:1976` `await task._task` raises → `task._error` set (1980-1986).
2. `finally` (1992+) computes `final_status = "failed"` and persists via one of three paths. No user message today. Add a best-effort `messenger._send_callback(...)` on the failure branch, guarded by `not chat_state.defer_reaction`.

**Worker Sentry (defect #3):**
1. `worker/__main__.py:main()` (1340) runs before `asyncio.run(_run_worker(...))` (1386). Add gated `sentry_sdk.init(...)` here so any exception in `_run_worker` / session execution is captured.

**Dedup cleanup (defect #4):**
1. `_cleanup_duplicate_sessions` scans terminal statuses (line 300) → builds `terminal_keys` → kills matching `pending`. Narrow the scan tuple to `("completed",)`.

## Architectural Impact

- **New dependencies:** none. `sentry_sdk` already a dependency; raw `POPOTO_REDIS_DB` already used in the messenger for dedup.
- **Interface changes:** minimal. Defect #1 introduces a `cancel-reason:{session_id}` Redis convention (raw string, 180s TTL) written by killers and read non-destructively by whichever send site wins the `interrupted-sent` dedup. No function signature changes required if we use the Redis-keyed reason; a thin helper (e.g. `agent/cancel_reason.py`) encapsulates set/non-destructive-get so callers don't hand-roll keys.
- **Coupling:** defect #3 preferably extracts a shared `configure_sentry(component)` helper reused by bridge + worker (reduces duplication). If sequenced after #1834, it reuses #1834's gating directly.
- **Data ownership:** unchanged — status remains ORM-owned; the reason key is transient signalling, not authoritative state.
- **Reversibility:** all four are small, independently revertible. Defect #4 is a one-line tuple change.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM check-in on the defect #1 design decision and the #1834 sequencing call, code reviewer.

**Interactions:**
- PM check-ins: 1-2 (confirm defect #1 approach; confirm #1834 sequencing)
- Review rounds: 1

Four small independent fixes; the communication overhead is the design confirmation for #1 and the #1834 ordering, not coding time.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | cancel-reason key + dedup key + AgentSession ORM |
| `sentry_sdk` importable | `python -c "import sentry_sdk"` | worker Sentry init (defect #3) |

`SENTRY_DSN` is optional — defect #3's init is DSN-gated, so absence is a valid state (init is skipped), not a build blocker.

## Solution

### Key Elements

- **Cancel-reason signal (defect #1):** a transient `cancel-reason:{session_id}` Redis key (raw `POPOTO_REDIS_DB`, 180s TTL, values e.g. `no_resume` / `resume`) written by each killer at the moment it decides the session's fate, read **non-destructively** (never popped) by whichever send site actually wins the `interrupted-sent:{session_id}` dedup. Default (absent) = genuine worker shutdown = "will resume". Keeps `agent/messenger.py` ORM-free. The read must be non-destructive and gated behind the dedup win — see the BLOCKER resolution in Technical Approach for why a destructive pop reintroduces the very bug.
- **Reason-aware message selection:** both `agent/messenger.py` and `agent/session_completion.py` pick the message from the reason: "interrupted, will resume automatically" vs. a no-resume line ("stopped and will not resume automatically" — exact copy in Open Questions).
- **Failure notification (defect #2):** best-effort `messenger._send_callback(...)` on the running→failed branch in `session_executor.py`, guarded by `not chat_state.defer_reaction`, with an optional `failed-sent:{session_id}` dedup key mirroring the interrupted pattern (multiple finalize paths must not double-send).
- **Worker Sentry (defect #3):** gated `sentry_sdk.init(...)` in `worker/__main__.py:main()` via a shared `configure_sentry(component, before_send=None)` helper that preserves the bridge's DSN/release/environment gating verbatim, takes `before_send` as a parameter (worker passes `None`, avoiding bridge-hibernation coupling), and adds its own minimal `PYTEST_CURRENT_TEST`/`CI` + machine guard so worker test runs never tag `production`. A startup log records enabled/disabled state.
- **Dedup narrowing (defect #4):** `_cleanup_duplicate_sessions` terminal scan → `("completed",)`.

### Flow

Session interrupted → killer writes `cancel-reason` (or leaves it unset on shutdown) → CancelledError reaches messenger/completion send site → site reads reason → user sees an accurate "will resume" / "will not resume" line.

Session crashes → executor finalize computes `failed` → best-effort Telegram notification → user learns the request didn't complete.

### Technical Approach

- **Defect #1 (chosen approach — Redis-keyed reason):** introduce `agent/cancel_reason.py` with `set_cancel_reason(session_id, kind, ttl=180)` and a **non-destructive** `get_cancel_reason(session_id) -> str | None` over raw `POPOTO_REDIS_DB` (same access pattern the dedup key already uses). There is deliberately **no** destructive pop — the 180s TTL is the sole cleanup mechanism. Wire `set_cancel_reason(..., "no_resume")` into: Branch 1 deadline kill (before finalizing `cancelled`), and every out-of-band killer that finalizes to a terminal non-resume status (supersede, health-check kill, `_apply_recovery_transition` when it does NOT re-queue). Recovery paths that re-queue to `pending` set `"resume"`. Branch 3 (worker shutdown) writes nothing → messenger default "will resume" (correct). **Why this over alternatives:** `agent/messenger.py` is deliberately ORM-free (`session_executor.py:1894` comment), so it cannot `get_authoritative_session` inside the handler — ruling out "read status in handler". Threading a param through `cancel()` is impossible for Branch 3 (asyncio cancels the worker-loop task; there is no call site to pass a reason). A raw-Redis reason key threads from every killer, needs no signature changes, respects the ORM-free invariant, and degrades safely (missing key → current behavior).
- **Defect #1 BLOCKER resolution (dedup/read race).** `agent/messenger.py:296-355` and `agent/session_completion.py:1144-1176` are BOTH gated by a single-winner `interrupted-sent:{session_id}` SET-NX dedup key — exactly one of them actually sends the message. The critique's blocker: if the reason read were a **destructive pop**, the *losing* send site (the one whose SET-NX lost, so it will NOT send) could pop the reason first; then the *winning* sender reads `None` and emits the "will resume" copy for a `no_resume` session — reproducing the original bug. **Fix (both parts):** (a) `get_cancel_reason` is non-destructive — no site ever deletes the key, so a non-sending site cannot starve the sender; the 180s TTL reclaims it. (b) The reason read is moved to *after* the `interrupted-sent` SET-NX succeeds — i.e. only the actual sender reads the reason, inside the won-dedup branch. Belt-and-suspenders: non-destructive read means even out-of-order execution is safe, and the read-inside-winner placement means the read only happens on the path that sends. A stale key can linger at most 180s and only for the same (unique) `session_id`, so there is no cross-session contamination. **Required test:** messenger wins the `interrupted-sent` SET-NX while the completion-runner ALSO fires for the same `session_id` with `cancel-reason=no_resume` → assert the winning site sends the **no-resume** copy and the losing site sends nothing.
- **Defect #2:** in `session_executor.py` failure finalize block, when `task.error` and `not chat_state.defer_reaction`, `await asyncio.wait_for(messenger._send_callback(<failure copy>), timeout=2.0)` inside try/except that swallows send errors and never blocks finalization. Dedup via `failed-sent:{session_id}` SET NX EX so the three finalize paths don't double-send.
- **Defect #3 (chosen approach — mechanical extraction + minimal own gate, NOT #1834 absorption):** extract the bridge's init block into `configure_sentry(component: str, before_send=None)` (new module `monitoring/sentry_config.py`) preserving the bridge's *current* gating verbatim — DSN-gated (`if not os.getenv("SENTRY_DSN"): return`), plus `release`, `traces_sample_rate`, `environment`.
  - **`before_send` is a parameter, not hardcoded (Concern: hibernation coupling).** The bridge's `_sentry_before_send` calls `bridge.hibernation.is_hibernating()` — a bridge-only concept. If the worker reused it verbatim, worker Sentry events would be silently dropped whenever the *bridge* is hibernating even though the worker is healthy, defeating defect #3 during a degraded state. So the signature takes `before_send=None`: **bridge** passes `_sentry_before_send`; **worker** passes `None` (a worker-owned filter can be added later if needed). The shared helper never imports `bridge.hibernation`.
  - **Own the minimal pytest/CI gate (Concern: baseline bridge has no dev/test gate).** The current bridge init has NO dev/test guard — only `environment` defaulting to `"production"`. The bridge never runs under worker pytest, so it never mis-tags; but the worker init *does* execute in worker tests, so if `SENTRY_DSN` is present a worker pytest run would report as `production` — the exact No-Go this plan forbids. Therefore `configure_sentry()` includes an explicit early return `if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("CI"): return` plus the machine gate, INSIDE the helper. This is the *minimal* guard needed to avoid mis-tagging — it does NOT absorb #1834's full environment-gating scope (dev-vs-prod distinction), which #1834 still layers on top of the shared helper later. This keeps the plan consistent with its own first No-Go.
  - **Worker env sourcing is already handled (Concern: worker is a separate launchd process).** Verified: `scripts/install_worker.sh:107-166` (and the mirrored injection in `scripts/remote-update.sh:168-196`) inject **all** `.env` values — including `SENTRY_DSN`/`SENTRY_ENVIRONMENT` — into the worker plist's `EnvironmentVariables` via `dotenv_values`, exactly as the bridge plist does. So `os.getenv("SENTRY_DSN")` resolves in the launchd worker; the "no propagation changes" claim holds. To make a missing-env no-op observable, the worker logs at startup: `logger.info("worker sentry: %s", "enabled" if os.getenv("SENTRY_DSN") else "disabled (no DSN in worker env)")`.
  - Bridge and `worker/__main__.py:main()` both call `configure_sentry(...)`.
  - **Sequencing (decoupled from #1834).** This shared helper is landed here and does not block on #1834; #1834 rebases onto `configure_sentry()` to add its richer environment gating. The task DAG no longer holds the three independent fixes hostage to #1834 — see the revised `Depends On` on `validate-all`/`document-feature`.
- **Defect #4:** one-line change of the status tuple at `scripts/update/run.py:300` to `("completed",)`; update the docstring (272-279) which currently lists all four statuses.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new best-effort sends (defects #1, #2) are wrapped in `except (TimeoutError, Exception)` that logs a warning — add tests asserting a send-callback that raises does NOT prevent finalization (session still reaches terminal status).
- [ ] `get_cancel_reason` on Redis failure must not raise into the CancelledError handler — test with Redis unavailable → handler falls back to "will resume" and re-raises cleanly.

### Empty/Invalid Input Handling
- [ ] `get_cancel_reason` with no key set returns `None` → the winning send site defaults to the resume message (test the unset path explicitly).
- [ ] Invalid/unknown reason value → treat as resume (safe default); test an unexpected string.

### Error State Rendering
- [ ] Defect #2: force `task._task` to raise, assert a `_send_callback` invocation occurred with failure copy and the session finalized to `failed`.
- [ ] Defect #1: assert no-resume copy is sent for `cancel-reason=no_resume` and resume copy for unset/`resume`.
- [ ] Defect #1 (BLOCKER regression guard): both send sites race the `interrupted-sent` SET-NX for the same `session_id` with `cancel-reason=no_resume` set — assert the SET-NX **winner** sends the no-resume copy and the loser sends nothing, proving the non-destructive read + read-inside-winner placement prevents the winner from reading `None`.

## Test Impact

- [ ] `tests/unit/test_deliver_pipeline_completion.py` — UPDATE: covers the completion-runner interrupted send (`session_completion.py`); update assertions to expect reason-aware copy (asserted via the shared `agent/notification_copy.py` constants, not literals) and add a no-resume case plus the BLOCKER dual-fire regression test (both sites race the `interrupted-sent` dedup with `no_resume` set → winner sends no-resume copy, loser silent).
- [ ] `tests/integration/test_pm_final_delivery.py` — UPDATE: exercises the interrupted best-effort send end-to-end; add a no-resume-reason path and keep the default-resume path passing.
- [ ] `scripts/update/run.py` dedup — no dedicated existing test found (`grep` found only the source file). ADD a new unit test `tests/unit/test_cleanup_duplicate_sessions.py` (REPLACE-not-applicable; net-new) asserting a `failed` terminal + matching `pending` → pending survives; a `completed` terminal + matching `pending` → pending killed.
- [ ] session_executor failure-path notification — net-new test asserting the running→failed send (asserting the `FAILURE_NOTICE` constant); likely in a new `tests/unit/test_session_executor_failure_notification.py` (no existing test covers this path).
- [ ] worker Sentry init — net-new `tests/unit/test_worker_sentry_init.py` asserting (a) `configure_sentry("worker")` invokes `sentry_sdk.init` when `SENTRY_DSN` is set and no guard trips, and (b) init is **skipped** under `PYTEST_CURRENT_TEST` even with `SENTRY_DSN` present.

No other existing tests assert the specific literal interrupt string beyond the two files above (verified via `grep -rln "will resume automatically" tests/`). Once `agent/notification_copy.py` lands (build-copy), all copy assertions reference its constants, so a future copy change is a single-file edit and does not ripple across test files.

## Rabbit Holes

- **Do not** attempt to unify all three cancellation branches in `agent_session_queue.py` into one message-owning code path — the ORM-free messenger invariant and the asyncio-driven Branch 3 make a single choke point infeasible. The Redis-keyed reason is deliberately decoupled.
- **Do not** rewrite `_sentry_before_send` / hibernation logic. The **bridge** keeps passing `_sentry_before_send` into `configure_sentry` verbatim; the **worker** passes `before_send=None` (it must NOT import `bridge.hibernation` — bridge hibernation is not a worker health signal). "Reuse verbatim" applies to the bridge caller only.
- **Do not** make defect #3 absorb #1834's full environment (dev-vs-prod) gating. The shared helper carries only the *minimal* pytest/CI + machine guard needed to stop worker test runs mis-tagging `production`; #1834 layers its richer environment gating onto the shared helper afterward.
- **Do not** expand defect #4 into a general dedup-policy redesign — it is a one-line status-tuple narrowing plus docstring.

## Risks

### Risk 1: Killer coverage for the cancel-reason is incomplete
**Impact:** A no-resume killer that forgets to set the reason → user still sees "will resume" (regression to current behavior for that path, not worse).
**Mitigation:** Enumerate every terminal-non-resume finalize call in the queue/recovery/health-check paths during build and set the reason at each. Default-resume degradation means a miss is silent-safe, not misleading beyond today. Add a test per killer path.

### Risk 2: #1834 conflict on the bridge Sentry init
**Impact:** This plan and #1834 both edit the bridge init block; one PR must rebase.
**Decision (was Open Question 2):** Land the shared `configure_sentry()` helper here; #1834 rebases onto it to add its richer dev-vs-prod environment gating. This plan does NOT wait on #1834 and does NOT absorb its full scope — it carries only the minimal pytest/CI + machine guard needed to avoid mis-tagging worker test runs.
**Mitigation:** Coordinate with whoever owns #1834 before touching `bridge/telegram_bridge.py` init; the extraction is mechanical, so the rebase is a call-site swap.

### Risk 3: Double-send across the three failure finalize paths (defect #2)
**Impact:** User gets two failure messages.
**Mitigation:** `failed-sent:{session_id}` SET NX EX dedup, mirroring `interrupted-sent`.

## Race Conditions

### Race 1: Reason key read before killer writes it
**Location:** `agent/messenger.py:296-355`, `agent/session_completion.py:1145-1176` vs. killer write sites.
**Trigger:** Killer cancels the task before its `set_cancel_reason` write commits; the CancelledError handler runs and `get_cancel_reason` returns `None`.
**Data prerequisite:** The reason key must be written before `.cancel()` is called (or before the finalize that triggers cancellation).
**State prerequisite:** For Branches 1 & 2 the killer owns the ordering — write reason, then finalize/cancel.
**Mitigation:** Write the reason immediately before the cancel/finalize in each killer. If the race still loses, `None` → resume copy = current behavior (safe default, no crash). Documented as acceptable degradation, not a correctness bug.

### Race 1b: Two send sites, one dedup winner (the BLOCKER)
**Location:** `agent/messenger.py:296-355` and `agent/session_completion.py:1144-1176`, both gated by the `interrupted-sent:{session_id}` SET-NX dedup.
**Trigger:** Both send sites fire for the same `session_id`; one wins the SET-NX and sends, the other loses and must stay silent.
**Data prerequisite:** The winning (sending) site must be the one that reads the reason.
**State prerequisite:** The reason read is non-destructive (no pop) AND placed *after* the SET-NX win, so only the sender reads the reason and no site can starve another.
**Mitigation:** `get_cancel_reason` never deletes the key; 180s TTL is the only reclaimer. Read-inside-winner ensures the losing site never consumes the reason. Covered by the BLOCKER regression-guard test above.

### Race 2: Concurrent failure finalize paths (defect #2)
**Location:** `session_executor.py:1991-2079`.
**Trigger:** Two of the three finalize paths execute for the same session under a CAS-conflict retry.
**Data prerequisite:** Only one user notification per failure.
**State prerequisite:** `failed-sent` key established atomically.
**Mitigation:** SET NX EX dedup guarantees single send.

## No-Gos (Out of Scope)

- Landing worker-side Sentry init with **no** gate against `PYTEST_CURRENT_TEST`/`CI`/machine — forbidden, because a `SENTRY_DSN`-present worker test run would tag `production`. The shared helper's own minimal guard (defect #3, Technical Approach) satisfies this; defect #3 no longer waits on #1834. #1834's richer dev-vs-prod environment gating is layered on later and is out of scope here.
- Nothing else deferred — defects #1, #2, #3, and #4 are all built in this plan; #3 does not block on #1834.

## Update System

`scripts/update/run.py::_cleanup_duplicate_sessions` is itself part of the `/update` flow — defect #4 modifies update-time behavior directly. No new deps, no migration, no `migrations.py` entry (no Popoto schema change — the cancel-reason and failed-sent keys are transient raw-Redis keys, not ORM fields).

**Worker Sentry env propagation (defect #3) — verified, no new work.** The worker is a separate launchd process, but `scripts/install_worker.sh:107-166` and the mirrored injection in `scripts/remote-update.sh:168-196` already inject **all** `.env` values (including `SENTRY_DSN`/`SENTRY_ENVIRONMENT`) into the worker plist's `EnvironmentVariables` via `dotenv_values`, exactly as the bridge plist does. So the launchd worker resolves `os.getenv("SENTRY_DSN")` and the "no propagation changes" claim holds. The build adds a worker startup log (`worker sentry: enabled|disabled (no DSN in worker env)`) so a missing-env no-op is observable in `logs/worker.log` — no update-script change required, but re-verify the injection still carries `SENTRY_DSN` after any future plist-template edit. Otherwise: no update system changes required.

## Agent Integration

No agent integration required — all four fixes are internal to the worker/bridge/update runtime. No new CLI entry point, no `.mcp.json` change, no new tool surface. The bridge/worker already import their respective modules; the shared `configure_sentry()` helper (if adopted) is called at process startup, not exposed to the agent.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/pm-final-delivery.md` — document reason-aware interrupt messaging (defect #1) and the running→failed notification (defect #2), including the `cancel-reason:{session_id}` and `failed-sent:{session_id}` key conventions and TTLs.
- [ ] Update `docs/features/bridge-worker-architecture.md` — note worker-process Sentry initialization (defect #3) as part of worker startup responsibilities, including the shared `configure_sentry(component, before_send=None)` helper, why the worker passes `before_send=None` (no bridge-hibernation coupling), the minimal pytest/CI/machine guard, and that `SENTRY_DSN` reaches the worker via the existing plist env injection.
- [ ] Update `docs/features/session-lifecycle.md` — clarify which terminal statuses count as "handled" for dedup (defect #4: only `completed`).

### Inline Documentation
- [ ] Docstring for `agent/cancel_reason.py` helpers describing the signalling convention, the **non-destructive** read + read-inside-dedup-winner rule, the 180s-TTL-only cleanup, and safe-default semantics.
- [ ] Docstring for `agent/notification_copy.py` constants (`INTERRUPT_RESUME`, `INTERRUPT_NO_RESUME`, `FAILURE_NOTICE`) noting they are the single source of truth for user-facing lifecycle copy.
- [ ] Docstring for `monitoring/sentry_config.py::configure_sentry` documenting the `before_send` parameter contract (bridge passes `_sentry_before_send`, worker passes `None`) and the pytest/CI/machine guard.
- [ ] Update `_cleanup_duplicate_sessions` docstring (`scripts/update/run.py:272-279`) to say only `completed` suppresses a pending retry.

## Success Criteria

- [ ] **Per-killer-path coverage (not a runtime absolute).** For each enumerated non-resume killer, a test asserts `set_cancel_reason(session_id, "no_resume")` is written *before* the finalize/cancel that triggers the send: (a) Branch 1 deadline kill; (b) supersede; (c) health-check kill; (d) `_apply_recovery_transition` terminal (non-re-queue) path. Coverage of these four paths is a **build gate** — because the safe-default failure mode (absent key → resume copy) is itself the old bug, an un-enumerated or racing killer would silently satisfy a lifecycle-wide "never sends resume" absolute while still being wrong. Per-path write-before-finalize assertions catch that; a global absolute cannot.
- [ ] Given `cancel-reason=no_resume`, the winning send site emits the confirmed `INTERRUPT_NO_RESUME` copy (shared constant), verified by the BLOCKER dual-fire test.
- [ ] A session cancelled via genuine worker shutdown (Branch 3) writes no reason key and still sends the accurate `INTERRUPT_RESUME` copy (unset-default path test).
- [ ] A running→failed transition triggers a best-effort Telegram notification with the confirmed `FAILURE_NOTICE` copy (shared constant), verified by a test that forces a task exception and asserts a send occurred and the session finalized `failed`.
- [ ] `worker/__main__.py` calls `configure_sentry("worker")` at startup; `sentry_sdk.init` fires when `SENTRY_DSN` is set AND the pytest/CI/machine guard is not tripped. A test asserts init is **skipped** under `PYTEST_CURRENT_TEST` even with `SENTRY_DSN` present (no `production` mis-tag), and the worker logs its enabled/disabled state.
- [ ] `_cleanup_duplicate_sessions` only treats `completed` as handled — a `failed` terminal + matching `pending` leaves the pending session alive (test).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `worker/__main__.py` references `sentry_sdk` (or the shared `configure_sentry` helper).

## Team Orchestration

The lead orchestrates; builder+validator pairs per defect. Defects #2, #3, and #4 are independent and can build in parallel — none blocks on #1834 (Risk 2 decision). Defect #4 is the most mechanical. Copy-asserting tests for #1/#2 wait on the human copy sign-off (build-copy → `agent/notification_copy.py`); the mechanism code can build immediately. Defect #1's design approach is confirmed (see Open Question 1 resolution below); only the exact strings need sign-off.

### Team Members

- **Builder (cancel-reason)** — Name: `reason-builder` — Role: defect #1 (cancel_reason helper + killer wiring + both send sites) — Agent Type: builder (Domain: async/concurrency) — Resume: true
- **Builder (failure-notify)** — Name: `failnotify-builder` — Role: defect #2 (executor failure send + dedup) — Agent Type: builder (Domain: async/concurrency) — Resume: true
- **Builder (worker-sentry)** — Name: `sentry-builder` — Role: defect #3 (shared helper + worker init) — Agent Type: builder — Resume: true
- **Builder (dedup-narrow)** — Name: `dedup-builder` — Role: defect #4 (status tuple + docstring + test) — Agent Type: builder — Resume: true
- **Validator** — Name: `lifecycle-validator` — Role: verify all four defects' criteria + tests — Agent Type: validator — Resume: true
- **Documentarian** — Name: `lifecycle-docs` — Role: docs updates — Agent Type: documentarian — Resume: true

## Step by Step Tasks

### 1. Defect #4 — narrow dedup terminal scan
- **Task ID**: build-dedup
- **Depends On**: none
- **Validates**: tests/unit/test_cleanup_duplicate_sessions.py (create)
- **Assigned To**: dedup-builder
- **Agent Type**: builder
- **Parallel**: true
- Change `scripts/update/run.py:300` tuple to `("completed",)`; update docstring (272-279).
- Add unit test: `failed` terminal + matching `pending` → pending survives; `completed` + matching `pending` → pending killed.

### 2. Defect #2 — failure-path notification
- **Task ID**: build-failnotify
- **Depends On**: build-copy (copy sign-off, OQ3 — copy-asserting tests wait on the shared constant); mechanism build can start immediately
- **Validates**: tests/unit/test_session_executor_failure_notification.py (create)
- **Assigned To**: failnotify-builder
- **Agent Type**: builder — Domain: async/concurrency
- **Parallel**: true
- Add best-effort `messenger._send_callback(FAILURE_NOTICE)` on the `task.error` finalize branch (guarded `not chat_state.defer_reaction`), `asyncio.wait_for` timeout 2.0, swallow send errors. `FAILURE_NOTICE` comes from the shared `agent/notification_copy.py` (build-copy) — do NOT inline a literal.
- Add `failed-sent:{session_id}` SET NX EX dedup.
- Test (after build-copy lands): force `task._task` to raise → assert send occurred with the confirmed `FAILURE_NOTICE` constant + session finalized `failed`; assert a raising send-callback does not block finalization.
- **Copy assertions gated on build-copy** (Open Question 3 sign-off) — write the mechanism test first, add the copy assertion once the constant exists.

### 3. Defect #1 — reason-aware interrupt message
- **Task ID**: build-reason
- **Depends On**: build-copy (copy sign-off, OQ1 — copy-asserting tests wait on the shared constant)
- **Validates**: tests/unit/test_deliver_pipeline_completion.py (update), tests/integration/test_pm_final_delivery.py (update)
- **Assigned To**: reason-builder
- **Agent Type**: builder — Domain: async/concurrency
- **Parallel**: true
- Create `agent/cancel_reason.py` (`set_cancel_reason`, non-destructive `get_cancel_reason` over raw POPOTO_REDIS_DB, 180s TTL — no pop/delete).
- Wire `set_cancel_reason(...,"no_resume")` into deadline kill (Branch 1) and every terminal-non-resume out-of-band killer; `"resume"` on re-queue-to-pending recovery paths.
- Update `agent/messenger.py:339` and `agent/session_completion.py:1172` to select copy from `get_cancel_reason`, reading **only inside the branch that won the `interrupted-sent` SET-NX** (absent → resume copy).
- Assert the confirmed no-resume string via the shared `agent/notification_copy.py` constant (see build-copy), not a literal.
- Tests: no-resume path, resume path, unset-default path, Redis-unavailable fallback, **and the BLOCKER dual-fire test** (both sites race the dedup with `no_resume` set → winner sends no-resume copy, loser silent).

### 4. Defect #3 — worker Sentry init
- **Task ID**: build-worker-sentry
- **Depends On**: none (decoupled from #1834 — see Risk 2 decision)
- **Validates**: tests/unit/test_worker_sentry_init.py (create)
- **Assigned To**: sentry-builder
- **Agent Type**: builder
- **Parallel**: true
- Extract bridge init into `configure_sentry(component, before_send=None)` (new `monitoring/sentry_config.py`), preserving DSN-gating verbatim; bridge passes `_sentry_before_send`, worker passes `None`; call from bridge + `worker/__main__.py:main()`.
- Add the helper's own minimal guard: `if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("CI"): return` + machine gate (do NOT absorb #1834's dev-vs-prod scope).
- Add worker startup log: `logger.info("worker sentry: %s", "enabled" if os.getenv("SENTRY_DSN") else "disabled (no DSN in worker env)")`.
- Tests: (a) with `SENTRY_DSN` set and gating satisfied, `sentry_sdk.init` is invoked during worker startup; (b) under `PYTEST_CURRENT_TEST` with `SENTRY_DSN` present, `configure_sentry` returns WITHOUT calling `sentry_sdk.init` (no `production` mis-tag).

### 4b. Copy sign-off + shared constants (Concern: copy before tests)
- **Task ID**: build-copy
- **Depends On**: human copy sign-off (Open Questions 1 & 3)
- **Assigned To**: reason-builder
- **Agent Type**: builder
- **Parallel**: false (blocks build-reason and build-failnotify test assertions)
- After the human confirms the no-resume copy (OQ1) and failure copy (OQ3), create `agent/notification_copy.py` with the three constants (`INTERRUPT_RESUME`, `INTERRUPT_NO_RESUME`, `FAILURE_NOTICE`).
- All send sites and all tests reference these constants — copy changes are one edit, not three. Do NOT write copy-asserting tests until this task lands.

### 5. Validation
- **Task ID**: validate-all
- **Depends On**: build-dedup, build-failnotify, build-reason (build-worker-sentry validates via its own test tail — not a gate on the three independent fixes)
- **Assigned To**: lifecycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all tests + lint + format; verify every Success Criterion. If build-worker-sentry has landed, include its criteria; otherwise it ships/validates independently.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-dedup, build-failnotify, build-reason (worker-Sentry docs land with build-worker-sentry's own doc tail)
- **Assigned To**: lifecycle-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update the three feature docs + inline docstrings per the Documentation section.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Worker inits Sentry | `grep -rl 'sentry_sdk\|configure_sentry' worker/*.py` | exit code 0 |
| Dedup only completed | `grep -n '"completed", "killed", "abandoned", "failed"' scripts/update/run.py` | exit code 1 |
| Failure notify wired | `grep -n 'failed-sent' agent/session_executor.py` | exit code 0 |
| Reason helper exists | `python -c "from agent.cancel_reason import set_cancel_reason, get_cancel_reason"` | exit code 0 |
| Reason read non-destructive | `grep -n 'pop_cancel_reason\|delete.*cancel-reason' agent/cancel_reason.py agent/messenger.py agent/session_completion.py` | exit code 1 (no destructive pop) |

## Critique Results

<!-- Populated by /do-plan-critique (war room) 2026-07-03. Verdict: NEEDS REVISION (1 blocker). FULL depth, 3 critics. Revision applied 2026-07-03 — all 7 findings resolved in-body. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk&Robustness + History&Consistency (cross-validated) | Destructive `pop_cancel_reason` at BOTH send sites (`messenger.py:296-355`, `session_completion.py:1144-1176`) races the single-winner `interrupted-sent:{session_id}` dedup: the losing site can pop the reason first, so the winning (sending) site reads `None` and emits the "will resume" copy for a `no_resume` session — reproducing the exact bug. | **RESOLVED** — Technical Approach "Defect #1 BLOCKER resolution"; Solution Key Elements; Race 1b; Success Criteria; build-reason task; Verification "Reason read non-destructive" row | Adopted BOTH fixes: non-destructive `get_cancel_reason` (no pop; 180s TTL is the only reclaimer) AND the read is placed inside the `interrupted-sent` SET-NX winner branch. Added the required dual-fire regression test (both sites race dedup with `no_resume` → winner sends no-resume copy, loser silent). |
| CONCERN | Risk&Robustness(Operator) + Scope&Value(Simplifier) | Open Question 2 recommends option (b) "reuse the bridge gating," but the baseline bridge init has NO dev/test gate — only `environment` default "production." So (b) reuses nothing and worker pytest runs with `SENTRY_DSN` set get tagged `production` — the exact No-Go outcome — while also absorbing #1834's scope, contradicting the plan's own first No-Go. | **RESOLVED** — Technical Approach defect #3; Risk 2; No-Gos; OQ2 [RESOLVED] | Chose mechanical extraction preserving DSN-gating verbatim PLUS the helper's own minimal `PYTEST_CURRENT_TEST`/`CI`+machine guard (NOT #1834's dev-vs-prod scope). Added a test asserting init is skipped under pytest even with `SENTRY_DSN` present. |
| CONCERN | Risk&Robustness(Skeptic) | Reusing `_sentry_before_send` verbatim in the worker couples worker→`bridge.hibernation.is_hibernating()` (a bridge-only concept): worker Sentry events silently dropped whenever the BRIDGE is hibernating even if the worker is healthy. | **RESOLVED** — Technical Approach defect #3; Rabbit Holes | Signature is `configure_sentry(component, before_send=None)`; bridge passes `_sentry_before_send`, worker passes `None`; helper never imports `bridge.hibernation`. Narrowed the "reuse verbatim" Rabbit Hole to the bridge caller only. |
| CONCERN | History&Consistency(Archaeologist) | The worker is a SEPARATE launchd process. If the worker plist doesn't source the same `.env`, `os.getenv("SENTRY_DSN")` is `None` and defect #3 is a silent production no-op. Plan claims "no propagation changes." | **RESOLVED** — Technical Approach defect #3; Update System (Worker Sentry env propagation) | Verified: `install_worker.sh:107-166` + `remote-update.sh:168-196` inject all `.env` values (incl. `SENTRY_DSN`) into the worker plist via `dotenv_values` — the claim holds. Added the enabled/disabled startup log for observability and documented it in Update System. |
| CONCERN | Scope&Value(Simplifier) | Task DAG makes `validate-all`/`document-feature` depend on `build-worker-sentry`, which was gated on #1834 (OPEN, no PR) — holding three independent shippable fixes hostage. | **RESOLVED** — Step by Step Tasks 4/5/6; Team Orchestration | `build-worker-sentry` decoupled from #1834 (`Depends On: none`); `validate-all`/`document-feature` now depend on `[build-dedup, build-failnotify, build-reason]`; worker-Sentry carries its own validation/doc tail. |
| CONCERN | Scope&Value(User) | Success Criteria validate mechanism ("a send occurred"), never the copy; Test Impact pins tests to literal strings while the strings are unconfirmed — baking in to-be-revised copy. | **RESOLVED** — new build-copy task; OQ1/OQ3 kept open for human sign-off; Test Impact; Success Criteria | Added `agent/notification_copy.py` shared-constant task (`build-copy`) gated on human copy sign-off; all send sites + tests reference the constants (one edit, not three); `build-reason`/`build-failnotify` copy-asserting tests gate on `build-copy`. |
| CONCERN | History&Consistency(ConsistencyAuditor) | Success Criterion 1 was a lifecycle-wide absolute; the safe-default failure mode (absent key → resume copy) IS the old bug, so an un-enumerated/racing killer silently satisfies it falsely. | **RESOLVED** — Success Criteria (per-killer-path bullet) | Reworded Criterion 1 to per-killer-path build gate: each enumerated non-resume killer (Branch 1 deadline, supersede, health-check kill, `_apply_recovery_transition` terminal) has a test asserting `set_cancel_reason(..., "no_resume")` is written before finalize/cancel — not a runtime absolute. |

---

## Open Questions

The BLOCKER and all six CONCERNS from the critique are resolved in the plan body above. The two remaining open items are **user-facing copy sign-off** (deliberately kept open per the Scope&Value(User) concern — copy must be confirmed by a human BEFORE copy-asserting tests are written; `build-copy` gates on these answers). Open Question 2's design decision is now **resolved** (see below).

1. **Defect #1 no-resume copy (approach already confirmed).** The raw-Redis `cancel-reason:{session_id}` signal is confirmed over the alternatives (thread a param through cancel — infeasible for Branch 3; read authoritative status in handler — violates the ORM-free invariant). **Still needs human sign-off:** the exact no-resume string, proposed **"I was stopped and won't resume automatically. Send a new message if you'd like me to continue."** This becomes `INTERRUPT_NO_RESUME` in `agent/notification_copy.py`.
2. **[RESOLVED] Defect #3 sequencing vs #1834.** Decision: land the shared `configure_sentry(component, before_send=None)` helper here with the *minimal* pytest/CI/machine guard (NOT #1834's full dev-vs-prod environment gating); #1834 rebases onto the helper. Defect #3 no longer blocks on #1834, and the three other fixes no longer wait on it. See Risk 2 and Technical Approach defect #3.
3. **Failure-notify copy (defect #2).** Confirm the user-facing failure message — proposed: **"Something went wrong and I couldn't finish that. I've logged the error."** Should it invite a retry, or stay neutral? This becomes `FAILURE_NOTICE` in `agent/notification_copy.py`.
