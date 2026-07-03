---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-07-03
tracking: https://github.com/tomcounsell/ai/issues/1877
last_comment_id:
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
- **Interface changes:** minimal. Defect #1 introduces a `cancel-reason:{session_id}` Redis convention (raw string, short TTL) written by killers and read by the two send sites. No function signature changes required if we use the Redis-keyed reason; a thin helper (e.g. `agent/cancel_reason.py`) encapsulates set/get so callers don't hand-roll keys.
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

- **Cancel-reason signal (defect #1):** a transient `cancel-reason:{session_id}` Redis key (raw `POPOTO_REDIS_DB`, short TTL, values e.g. `no_resume` / `resume`) written by each killer at the moment it decides the session's fate, read by the two send sites. Default (absent) = genuine worker shutdown = "will resume". Keeps `agent/messenger.py` ORM-free.
- **Reason-aware message selection:** both `agent/messenger.py` and `agent/session_completion.py` pick the message from the reason: "interrupted, will resume automatically" vs. a no-resume line ("stopped and will not resume automatically" — exact copy in Open Questions).
- **Failure notification (defect #2):** best-effort `messenger._send_callback(...)` on the running→failed branch in `session_executor.py`, guarded by `not chat_state.defer_reaction`, with an optional `failed-sent:{session_id}` dedup key mirroring the interrupted pattern (multiple finalize paths must not double-send).
- **Worker Sentry (defect #3):** gated `sentry_sdk.init(...)` in `worker/__main__.py:main()` reusing the bridge's DSN/release/`before_send`/environment gating — ideally via a shared `configure_sentry()` helper.
- **Dedup narrowing (defect #4):** `_cleanup_duplicate_sessions` terminal scan → `("completed",)`.

### Flow

Session interrupted → killer writes `cancel-reason` (or leaves it unset on shutdown) → CancelledError reaches messenger/completion send site → site reads reason → user sees an accurate "will resume" / "will not resume" line.

Session crashes → executor finalize computes `failed` → best-effort Telegram notification → user learns the request didn't complete.

### Technical Approach

- **Defect #1 (chosen approach — Redis-keyed reason):** introduce `agent/cancel_reason.py` with `set_cancel_reason(session_id, kind, ttl=180)` and `pop_cancel_reason(session_id) -> str | None` over raw `POPOTO_REDIS_DB` (same access pattern the dedup key already uses). Wire `set_cancel_reason(..., "no_resume")` into: Branch 1 deadline kill (before finalizing `cancelled`), and every out-of-band killer that finalizes to a terminal non-resume status (supersede, health-check kill, `_apply_recovery_transition` when it does NOT re-queue). Recovery paths that re-queue to `pending` set `"resume"`. Branch 3 (worker shutdown) writes nothing → messenger default "will resume" (correct). Both send sites call `pop_cancel_reason` and select copy; absent → resume. **Why this over alternatives:** `agent/messenger.py` is deliberately ORM-free (`session_executor.py:1894` comment), so it cannot `get_authoritative_session` inside the handler — ruling out "read status in handler". Threading a param through `cancel()` is impossible for Branch 3 (asyncio cancels the worker-loop task; there is no call site to pass a reason). A raw-Redis reason key threads from every killer, needs no signature changes, respects the ORM-free invariant, and degrades safely (missing key → current behavior).
- **Defect #2:** in `session_executor.py` failure finalize block, when `task.error` and `not chat_state.defer_reaction`, `await asyncio.wait_for(messenger._send_callback(<failure copy>), timeout=2.0)` inside try/except that swallows send errors and never blocks finalization. Dedup via `failed-sent:{session_id}` SET NX EX so the three finalize paths don't double-send.
- **Defect #3:** extract the bridge's init block (55-85) into `configure_sentry(component: str)` (new module, e.g. `monitoring/sentry_config.py`) preserving `before_send=_sentry_before_send`, `release`, `traces_sample_rate`, `environment`. Bridge and `worker/__main__.py:main()` both call it. **Sequencing:** #1834 is landing environment-gating on the bridge init — see Open Questions for whether to wait for #1834 or land the shared helper here (absorbing the gating). Do not initialize worker Sentry ungated before the gating exists, or worker dev/test runs report as production.
- **Defect #4:** one-line change of the status tuple at `scripts/update/run.py:300` to `("completed",)`; update the docstring (272-279) which currently lists all four statuses.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new best-effort sends (defects #1, #2) are wrapped in `except (TimeoutError, Exception)` that logs a warning — add tests asserting a send-callback that raises does NOT prevent finalization (session still reaches terminal status).
- [ ] `pop_cancel_reason` on Redis failure must not raise into the CancelledError handler — test with Redis unavailable → handler falls back to "will resume" and re-raises cleanly.

### Empty/Invalid Input Handling
- [ ] `pop_cancel_reason` with no key set returns `None` → both send sites default to the resume message (test the unset path explicitly).
- [ ] Invalid/unknown reason value → treat as resume (safe default); test an unexpected string.

### Error State Rendering
- [ ] Defect #2: force `task._task` to raise, assert a `_send_callback` invocation occurred with failure copy and the session finalized to `failed`.
- [ ] Defect #1: assert no-resume copy is sent for `cancel-reason=no_resume` and resume copy for unset/`resume`.

## Test Impact

- [ ] `tests/unit/test_deliver_pipeline_completion.py` — UPDATE: covers the completion-runner interrupted send (`session_completion.py`); update assertions to expect reason-aware copy and add a no-resume case.
- [ ] `tests/integration/test_pm_final_delivery.py` — UPDATE: exercises the interrupted best-effort send end-to-end; add a no-resume-reason path and keep the default-resume path passing.
- [ ] `scripts/update/run.py` dedup — no dedicated existing test found (`grep` found only the source file). ADD a new unit test `tests/unit/test_cleanup_duplicate_sessions.py` (REPLACE-not-applicable; net-new) asserting a `failed` terminal + matching `pending` → pending survives; a `completed` terminal + matching `pending` → pending killed.
- [ ] session_executor failure-path notification — net-new test asserting the running→failed send; likely in a new `tests/unit/test_session_executor_failure_notification.py` (no existing test covers this path).

No other existing tests assert the specific literal interrupt string beyond the two files above (verified via `grep -rln "will resume automatically" tests/`).

## Rabbit Holes

- **Do not** attempt to unify all three cancellation branches in `agent_session_queue.py` into one message-owning code path — the ORM-free messenger invariant and the asyncio-driven Branch 3 make a single choke point infeasible. The Redis-keyed reason is deliberately decoupled.
- **Do not** rewrite `_sentry_before_send` / hibernation logic — reuse it verbatim via the shared helper.
- **Do not** try to make defect #3 also fix #1834's bridge gating unless the Open Question resolves that way — respect #1834's scope boundary.
- **Do not** expand defect #4 into a general dedup-policy redesign — it is a one-line status-tuple narrowing plus docstring.

## Risks

### Risk 1: Killer coverage for the cancel-reason is incomplete
**Impact:** A no-resume killer that forgets to set the reason → user still sees "will resume" (regression to current behavior for that path, not worse).
**Mitigation:** Enumerate every terminal-non-resume finalize call in the queue/recovery/health-check paths during build and set the reason at each. Default-resume degradation means a miss is silent-safe, not misleading beyond today. Add a test per killer path.

### Risk 2: #1834 conflict on the bridge Sentry init
**Impact:** If defect #3 extracts a shared helper while #1834 is editing the same init block, one PR must rebase.
**Mitigation:** Sequence per Open Question resolution — either wait for #1834 then reuse, or land the shared helper here and have #1834 rebase onto it. Coordinate before touching `bridge/telegram_bridge.py:55-85`.

### Risk 3: Double-send across the three failure finalize paths (defect #2)
**Impact:** User gets two failure messages.
**Mitigation:** `failed-sent:{session_id}` SET NX EX dedup, mirroring `interrupted-sent`.

## Race Conditions

### Race 1: Reason key read before killer writes it
**Location:** `agent/messenger.py:296-355`, `agent/session_completion.py:1145-1176` vs. killer write sites.
**Trigger:** Killer cancels the task before its `set_cancel_reason` write commits; the CancelledError handler runs and `pop_cancel_reason` returns `None`.
**Data prerequisite:** The reason key must be written before `.cancel()` is called (or before the finalize that triggers cancellation).
**State prerequisite:** For Branches 1 & 2 the killer owns the ordering — write reason, then finalize/cancel.
**Mitigation:** Write the reason immediately before the cancel/finalize in each killer. If the race still loses, `None` → resume copy = current behavior (safe default, no crash). Documented as acceptable degradation, not a correctness bug.

### Race 2: Concurrent failure finalize paths (defect #2)
**Location:** `session_executor.py:1991-2079`.
**Trigger:** Two of the three finalize paths execute for the same session under a CAS-conflict retry.
**Data prerequisite:** Only one user notification per failure.
**State prerequisite:** `failed-sent` key established atomically.
**Mitigation:** SET NX EX dedup guarantees single send.

## No-Gos (Out of Scope)

- [ORDERED] Landing worker-side Sentry init *before* #1834's environment-gating exists — must wait for #1834 to merge OR land the shared gated helper in this plan (Open Question 2 decides). Named gated event: #1834 merge. Defect #3 itself stays in scope; only the ungated-early-init ordering is forbidden.
- Nothing else deferred — defects #1, #2, #4 and the in-scope form of #3 are all built in this plan.

## Update System

`scripts/update/run.py::_cleanup_duplicate_sessions` is itself part of the `/update` flow — defect #4 modifies update-time behavior directly. No new deps, no migration, no `migrations.py` entry (no Popoto schema change — the cancel-reason and failed-sent keys are transient raw-Redis keys, not ORM fields). No propagation changes for the shared Sentry helper beyond the code itself. Otherwise: no update system changes required.

## Agent Integration

No agent integration required — all four fixes are internal to the worker/bridge/update runtime. No new CLI entry point, no `.mcp.json` change, no new tool surface. The bridge/worker already import their respective modules; the shared `configure_sentry()` helper (if adopted) is called at process startup, not exposed to the agent.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/pm-final-delivery.md` — document reason-aware interrupt messaging (defect #1) and the running→failed notification (defect #2), including the `cancel-reason:{session_id}` and `failed-sent:{session_id}` key conventions and TTLs.
- [ ] Update `docs/features/bridge-worker-architecture.md` — note worker-process Sentry initialization (defect #3) as part of worker startup responsibilities.
- [ ] Update `docs/features/session-lifecycle.md` — clarify which terminal statuses count as "handled" for dedup (defect #4: only `completed`).

### Inline Documentation
- [ ] Docstring for `agent/cancel_reason.py` helpers describing the signalling convention and safe-default semantics.
- [ ] Update `_cleanup_duplicate_sessions` docstring (`scripts/update/run.py:272-279`) to say only `completed` suppresses a pending retry.

## Success Criteria

- [ ] A session cancelled via deadline (Branch 1) or superseded/killed (Branch 2, non-resume) no longer sends "will resume automatically".
- [ ] A session cancelled via genuine worker shutdown (Branch 3) still sends an accurate resume message.
- [ ] A running→failed transition triggers a best-effort Telegram notification, verified by a test that forces a task exception and asserts a send occurred and the session finalized `failed`.
- [ ] `worker/__main__.py` calls `sentry_sdk.init` at startup when `SENTRY_DSN` is set (and honors the same gating as the bridge), verified by a startup test/assert.
- [ ] `_cleanup_duplicate_sessions` only treats `completed` as handled — a `failed` terminal + matching `pending` leaves the pending session alive (test).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `worker/__main__.py` references `sentry_sdk` (or the shared `configure_sentry` helper).

## Team Orchestration

The lead orchestrates; builder+validator pairs per defect. Defects #2 and #4 are the most mechanical (build first / in parallel). Defect #1 waits on the design confirmation. Defect #3 waits on the #1834 sequencing decision.

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
- **Depends On**: none
- **Validates**: tests/unit/test_session_executor_failure_notification.py (create)
- **Assigned To**: failnotify-builder
- **Agent Type**: builder — Domain: async/concurrency
- **Parallel**: true
- Add best-effort `messenger._send_callback(<failure copy>)` on the `task.error` finalize branch (guarded `not chat_state.defer_reaction`), `asyncio.wait_for` timeout 2.0, swallow send errors.
- Add `failed-sent:{session_id}` SET NX EX dedup.
- Test: force `task._task` to raise → assert send occurred + session finalized `failed`; assert a raising send-callback does not block finalization.

### 3. Defect #1 — reason-aware interrupt message
- **Task ID**: build-reason
- **Depends On**: none (but gated on Open Question 1 confirmation)
- **Validates**: tests/unit/test_deliver_pipeline_completion.py (update), tests/integration/test_pm_final_delivery.py (update)
- **Assigned To**: reason-builder
- **Agent Type**: builder — Domain: async/concurrency
- **Parallel**: true
- Create `agent/cancel_reason.py` (`set_cancel_reason`, `pop_cancel_reason` over raw POPOTO_REDIS_DB, short TTL).
- Wire `set_cancel_reason(...,"no_resume")` into deadline kill (Branch 1) and every terminal-non-resume out-of-band killer; `"resume"` on re-queue-to-pending recovery paths.
- Update `agent/messenger.py:339` and `agent/session_completion.py:1172` to select copy from `pop_cancel_reason` (absent → resume copy).
- Tests: no-resume path, resume path, unset-default path, Redis-unavailable fallback.

### 4. Defect #3 — worker Sentry init
- **Task ID**: build-worker-sentry
- **Depends On**: #1834 merge OR Open Question 2 resolution
- **Validates**: tests/unit/test_worker_sentry_init.py (create)
- **Assigned To**: sentry-builder
- **Agent Type**: builder
- **Parallel**: false (sequenced per Open Question 2)
- Extract bridge init (55-85) into `configure_sentry(component)` reusing `_sentry_before_send`; call from bridge + `worker/__main__.py:main()`.
- Honor the environment gating #1834 lands (do not init ungated).
- Test: with `SENTRY_DSN` set and gating satisfied, `sentry_sdk.init` is invoked during worker startup.

### 5. Validation
- **Task ID**: validate-all
- **Depends On**: build-dedup, build-failnotify, build-reason, build-worker-sentry, document-feature
- **Assigned To**: lifecycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all tests + lint + format; verify every Success Criterion.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-dedup, build-failnotify, build-reason, build-worker-sentry
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
| Reason helper exists | `python -c "import agent.cancel_reason"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Defect #1 approach + copy.** The plan chooses a raw-Redis `cancel-reason:{session_id}` signal (justified by the ORM-free messenger invariant and asyncio-driven Branch 3). Confirm this over the alternatives (thread a param through cancel — infeasible for Branch 3; read authoritative status in handler — violates the messenger's ORM-free invariant). Also confirm the exact no-resume user copy — proposed: **"I was stopped and won't resume automatically. Send a new message if you'd like me to continue."** vs. the existing resume copy.
2. **Defect #3 sequencing vs #1834.** #1834 is open with no PR filed. Choose: (a) **wait** for #1834 to merge, then reuse its bridge gating for the worker (safest, but blocks defect #3 on external progress); or (b) **land the shared `configure_sentry()` helper here** including the environment gating, and have #1834 rebase onto it (unblocks this plan, absorbs part of #1834's scope). Recommendation: (b) — the shared helper is the correct end-state and lets all four defects ship together; coordinate with whoever owns #1834.
3. **Failure-notify copy (defect #2).** Confirm the user-facing failure message — proposed: **"Something went wrong and I couldn't finish that. I've logged the error."** Should it invite a retry, or stay neutral?
