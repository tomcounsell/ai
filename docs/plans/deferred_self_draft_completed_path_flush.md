---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-25
tracking: https://github.com/tomcounsell/ai/issues/1794
last_comment_id:
revision_applied: true
---

# Deferred self-draft flush on the `completed` terminal path

## Problem

When an autonomous session produces a reply that the delivery validator
(`bridge/message_drafter.py`) flags for a wire-format violation, the output
handler **defers delivery**: it injects a self-draft steering message asking the
agent to rewrite next turn, and persists `deferred_self_draft_pending=True` +
`deferred_self_draft_text=<original text>` into the session's `extra_context`
(`agent/output_handler.py:453-456`). A fallback,
`_deliver_deferred_self_draft_fallback()` (`agent/session_health.py:1519`), is
meant to flush that held text if the session dies before redrafting.

But the fallback is wired **only into the health-monitor `failed`/`abandoned`
recovery branches** (`session_health.py:1917`, `:1940`, `:1968`). The normal
worker **`completed`** path performs no such check. So a session that defers a
reply for self-draft and then cleanly completes before redrafting **silently
loses its reply** — the human gets nothing even though the work succeeded.

**Current behavior:**
Production, 2026-06-25 — session `tg_psyoptimal_-1003743854645_263` committed a
card, opened a PR, and produced a 1164-char confirmation reply. The reply was
deferred for self-draft, the session went `running→completed` via the normal
executor path (not health-monitor recovery), and the held text was never sent.
The re-enqueued self-draft "continuation" was picked up 8 minutes later and died
in 0.2s in the "worker finally block" without redrafting. The human only got the
reply after manual recovery from the log.

**Desired outcome:**
A deferred self-draft that is never redrafted is flushed to the human on **every**
terminal path — `completed`, `failed`, and `abandoned` — via a single shared
chokepoint, with the existing 1-hour dedup preserved (never double-send). A
successful session must never silently swallow its own reply.

## Freshness Check

**Baseline commit:** `872d77c7` (`fix(watchdog): deterministic U-state worker recovery (#1767) (#1795)`)
**Issue filed at:** 2026-06-25T08:59:21Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/output_handler.py:453-456` — defer-time persistence of `deferred_self_draft_pending`/`deferred_self_draft_text` into `extra_context` — **still holds.**
- `agent/output_handler.py:749` `_inject_self_draft_steering` — pushes `SELF_DRAFT_INSTRUCTION` to the steering queue, returns True to defer delivery — **still holds** (issue cited `:752`; the def line is `:749`, body unchanged).
- `agent/session_health.py:1519` `_deliver_deferred_self_draft_fallback(entry)` — defined here; docstring says "Called on every terminal recovery branch (`failed` and `abandoned`)" — **still holds** (issue cited `:1418`; minor drift to `:1519`).
- `agent/session_health.py:1917` (abandoned, local), `:1940` (failed, max recovery), `:1968` (failed, subprocess not confirmed dead) — the three existing call sites, all `await _deliver_deferred_self_draft_fallback(entry)` — **still holds** (issue cited `:1836`/`:1864`; drifted to the three sites listed).
- `models/session_lifecycle.py:221` `finalize_session(session, status, ...)` — the single centralized terminal-transition handler — **confirmed present.**
- `bridge/session_transcript.py:317` — `complete_transcript()` calls `finalize_session(s, status, reason="transcript completed: ...")` on the normal path — **confirmed.** This is the `completed` write that currently bypasses the fallback.
- `agent/session_completion.py:167` `_complete_agent_session` → `finalize_session(...)` — **confirmed.**
- `agent/session_executor.py:1935-1976` — re-enqueue of unconsumed steering as a continuation, reusing the same `session_id` — **confirmed** (Q2 root cause area).
- `agent/session_executor.py:1548-1579` — empty-turn-input guard finalizing `failed` with `reason="empty_container_message"` — **confirmed** (matches the 0.2s death).

**Cited sibling issues/PRs re-checked:**
- #1730 / PR #1739 (merged 2026-06-18) — added the original `failed`/`abandoned` deferred fallback. This is the direct predecessor; it wired only the recovery branches, leaving the `completed` gap this issue closes.

**Commits on main since issue was filed (touching referenced files):** none — the issue was filed today (2026-06-25T08:59Z) and no commits have touched `session_health.py`, `session_lifecycle.py`, `session_transcript.py`, or `output_handler.py` since.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Line-number drift only (issue cited stale line numbers `1418`/`1836`/`1864`/`752`); all claims hold against the corrected locations above.

## Prior Art

- **Issue #1730 / PR #1739** (merged 2026-06-18): "deferred delivery lost when tool_timeout kills session: no fallback when self-draft steering is pending." Introduced `_deliver_deferred_self_draft_fallback()`, the defer-time `extra_context` persistence, and the 1-hour SETNX dedup. Wired the fallback into the health-monitor `failed`/`abandoned` branches **only**. This is the direct predecessor — the present issue is the missed `completed` path of the same mechanism.
- **Issue #1219 / PR #1685**: Repositioned the message drafter to a verbatim pass-through + validation filter, establishing the `needs_self_draft` signal path. Context only — not a fix attempt for this gap.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| PR #1739 (#1730) | Added the deferred-self-draft fallback + defer-time persistence + 1h dedup; wired it into the three health-monitor recovery branches (`session_health.py:1917/1940/1968`). | It targeted **only** the recovery branches because the original symptom was a tool-timeout *kill*. It never wired the fallback into the normal `completed` path (`finalize_session` via `complete_transcript`), so a clean completion after a deferral silently drops the held text. The fix addressed the kill-path symptom, not the general invariant "any terminal transition must flush a pending deferral." |

**Root cause pattern:** The deferred-flush invariant was attached to *specific terminal branches* rather than to the *single chokepoint* every terminal transition funnels through (`finalize_session`). Per-branch wiring is fragile: each new terminal path must remember to call the fallback. This plan moves the invariant to the chokepoint so it holds for `completed`, `failed`, `abandoned`, and any future terminal status by construction.

## Data Flow

1. **Entry point**: Agent produces a reply → `TelegramRelayOutputHandler.send()` (`agent/output_handler.py`).
2. **Validation**: drafter flags `needs_self_draft=True` (wire-format violation / empty promise).
3. **Defer**: handler calls `_inject_self_draft_steering()` (push `SELF_DRAFT_INSTRUCTION` to steering queue) AND persists `deferred_self_draft_pending=True` + `deferred_self_draft_text=<text>` into `extra_context` (`output_handler.py:453-456`). No outbox write happens.
4. **Session ends**: worker reaches a terminal transition. All terminal writes funnel through `finalize_session(session, status, ...)` (`models/session_lifecycle.py:221`).
   - On `failed`/`abandoned` via the health monitor (today): `await _deliver_deferred_self_draft_fallback(entry)` is called *before* `finalize_session`, so the held text is flushed. ✅ (after this change, the sync chokepoint flush inside `finalize_session` covers it instead — ordering preserved, still before the terminal save.)
   - On `completed` via the normal executor path (`complete_transcript` → `finalize_session`, both sync, no running loop): **no fallback runs** — held text is lost. ❌ (the bug)
5. **Output (target state)**: the **synchronous** flush fires once at the chokepoint regardless of terminal status; applies the narration gate and `rpush`es the held text directly to the outbox (`telegram:outbox:{session_id}` / email equivalent) → bridge delivers to Telegram. No async send-callback is awaited — the chokepoint is sync and has no running loop on the `completed` path. Dedup via SETNX `self_draft_fallback_sent:{session_id}` (1h) guarantees exactly-once.

## Architectural Impact

- **New dependencies**: none. The fallback helper already exists; this relocates *when* it is invoked.
- **Interface changes**: `finalize_session()` gains the responsibility of flushing a pending deferral. The flush is implemented as a **new fully-synchronous helper** (`flush_deferred_self_draft_sync`) that writes directly to the Redis outbox via `rpush` — see Technical Approach. The pre-existing `async def _deliver_deferred_self_draft_fallback` is deleted; there is no sync/async boundary to bridge because `finalize_session` and the new helper are both sync.
- **Coupling**: slightly increases coupling from `models/session_lifecycle.py` → `agent/session_health.py` (the fallback helper). Mitigated by a lazy import inside the call to avoid an import cycle (`session_lifecycle` is imported very early).
- **Data ownership**: unchanged. The fallback reads `extra_context` and writes to the outbox, same as today.
- **Reversibility**: high — the change is one invocation added at the chokepoint plus deletion of three now-redundant call sites; trivially revertible.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the chokepoint placement and Q2 disposition)
- Review rounds: 1 (verify dedup correctness and no double-send)

**Core scope (fits Small):** the sync chokepoint flush in `finalize_session`, deletion of the async helper + its three call sites, and the regression test. This is the delivery-loss fix and is self-contained.

**Q2 continuation cleanup is CONDITIONAL.** The continuation re-enqueue lives in `session_executor.py:1935-1976` — independent code from the lifecycle chokepoint. Because the chokepoint flush makes the self-draft continuation cosmetic (no longer a delivery path), Q2 is NOT required for the bug fix. Disposition: **ship Q2 in this slug only if the core fix lands cleanly within the Small appetite**; if the chokepoint work consumes the appetite or Q2 reveals coupling, split Q2 to a follow-on slug (`deferred_self_draft_continuation_cleanup`) referencing this plan. The Success Criteria mark the core flush as required and Q2 as conditional accordingly.

The mechanism already exists; the core is a relocation (async helper → sync chokepoint flush) with one regression test. The risk surface is the no-double-send guarantee — reviewable in a focused pass.

## Prerequisites

No prerequisites — this work has no external dependencies (no new secrets, services, or config).

## Solution

### Key Elements

- **Centralized flush at the chokepoint**: `finalize_session()` (`models/session_lifecycle.py:221`) becomes the single place that flushes a pending deferred self-draft on **any** terminal status (`completed`, `failed`, `abandoned`, and by construction `killed`/`cancelled`).
- **Sync helper replaces the async one**: the async `_deliver_deferred_self_draft_fallback` and its three explicit call sites in `session_health.py` (`:1917/1940/1968`) are **deleted**; a new fully-synchronous `flush_deferred_self_draft_sync(session)` is invoked once at the chokepoint, covering all terminal branches. No per-branch wiring remains.
- **Q2 — continuation cleanup (CONDITIONAL)**: the re-enqueued self-draft "continuation" (`session_executor.py:1935-1976`) is the unreliable path that died in 0.2s. With the terminal-path flush in place, the self-draft text is guaranteed delivered at completion, so the continuation re-enqueue for the *self-draft* case is redundant (cosmetic, not a delivery loss). Resolve by filtering the `drafter-fallback` self-draft steering out of the continuation re-enqueue. **Ships in this slug only if the core fix fits the Small appetite; otherwise splits to a follow-on slug** (see Appetite).
- **Preserved dedup**: the existing SETNX `self_draft_fallback_sent:{session_id}` (1h) remains the exactly-once guarantee. Moving the call to the chokepoint cannot double-send: the first caller (completion or a later recovery) wins the SETNX; the second is a no-op.

### Flow

Agent reply flagged → delivery deferred + `extra_context` persisted → session reaches **any** terminal transition → `finalize_session()` → (flag set?) → `flush_deferred_self_draft_sync()` rpushes held text to the outbox once (synchronously) → bridge delivers to Telegram.

### Technical Approach

**Decided sync/async shape (closes Open Question 3 — the BLOCKER).** The earlier draft told the build to "mirror how `finalize_session`'s existing async side effects are dispatched." That instruction was wrong: `finalize_session()` (`models/session_lifecycle.py:221`) is a plain **sync `def`** with NO `await`, NO `asyncio.create_task`, and NO event-loop dispatch anywhere — every side effect it runs (telemetry, auto-tag, checkpoint, parent finalization, save) is synchronous. The `completed` path reaches it via `complete_transcript()` (`bridge/session_transcript.py:252`, also a sync `def`) with **no ambient running event loop**, so a naive `asyncio.create_task(...)` at the chokepoint would raise `RuntimeError: no running event loop`. There is no async dispatch pattern to mirror — so the build does **not** reuse `_deliver_deferred_self_draft_fallback`'s async `await send_cb(...)` shape at the chokepoint.

Instead, **make the flush synchronous up to the outbox `rpush`**:

- The actual delivery commit is already synchronous. The async `send_cb` chain (`TelegramRelayOutputHandler.send`) does redundancy/RTR/narration processing, but the terminal act is a sync `r.rpush(f"telegram:outbox:{session_id}", json.dumps(payload))` (`agent/output_handler.py:715-719`; email path `:280-283`). The chokepoint flush enqueues the held text **directly to the Redis outbox synchronously**, bypassing the async send-callback entirely.
- Extract a sync helper — `flush_deferred_self_draft_sync(session)` in `agent/session_health.py` (next to the existing async helper) — that performs, all synchronously:
  1. Read `extra_context["deferred_self_draft_pending"]`; early-return if falsy.
  2. SETNX `self_draft_fallback_sent:{session_id}` (`nx=True, ex=3600`); early-return if not acquired (preserves exactly-once across all callers).
  3. Recover `deferred_self_draft_text`; apply the narration gate (`is_narration_only` → `NARRATION_FALLBACK_MESSAGE`) and the empty-text canned-notice substitution — identical logic to the async helper, lifted into a shared sync function both call.
  4. Build the same outbox payload the async handler builds and `r.rpush(queue_key, json.dumps(payload))` for the resolved transport (telegram vs email outbox key).
  5. Wrap the whole body in `try/except`, log at WARNING, never raise.
- `finalize_session()` calls `flush_deferred_self_draft_sync(session)` exactly once, on the CAS-re-read session object, before the status `save()`. Because it is fully synchronous, it works identically whether the caller has a running loop or not — no loop handling, no `create_task`, no `run_until_complete`.
- The existing `async def _deliver_deferred_self_draft_fallback` is **deleted** along with its three call sites (it is fully replaced by the sync chokepoint flush — NO LEGACY CODE TOLERANCE). The shared narration/canned-notice/dedup/payload logic moves into the sync helper so nothing is duplicated.

**Delivery ordering on `failed`/`abandoned` (resolves CONCERN 2).** The three health-monitor branches today `await _deliver_deferred_self_draft_fallback(entry)` *before* their `finalize_session(entry, ...)` call, so the flush is ordered before the terminal save. After this change the flush moves *inside* `finalize_session` (before the status `save()`), so it still happens before the terminal write commits — ordering is preserved on every path. The SETNX guard means even if a branch's removed explicit call and the chokepoint ever both ran, only the first wins. Build must confirm each of the three branches routes through `finalize_session` immediately after the (removed) explicit call (verified below) so no `failed`/`abandoned` flush is dropped.

- **Idempotency is already correct.** The sync helper early-returns if `deferred_self_draft_pending` is falsy and SETNX-guards delivery. No new dedup logic; the test must *prove* no double-send when both a completion and a later recovery observe the flag.
- **Remove the three explicit `session_health.py` calls** (and the now-unused async helper) only after confirming the chokepoint covers the `failed`/`abandoned` branches they sit in (those branches call `finalize_session` immediately after — verify each does).
- **Q2 continuation (CONDITIONAL — see Appetite).** Read `session_executor.py:1935-1976` and confirm whether the *self-draft* steering message is among the "leftover" re-enqueued messages. If it is, the continuation attempts a redraft that (per the production log) no-ops in 0.2s because the parent is already terminal / turn input strips empty. With the chokepoint flush guaranteeing delivery, the self-draft continuation re-enqueue is redundant. Resolve by filtering the self-draft steering sender (`drafter-fallback`) out of the continuation re-enqueue — targeted, leaves the general continuation mechanism intact. **This sub-task is CONDITIONAL**: it ships only if the chokepoint flush lands cleanly within the Small appetite; otherwise it splits to a follow-on slug (see Appetite). The core fix (chokepoint flush) does NOT depend on Q2 — once the held text is flushed at finalize, the no-op continuation is cosmetic, not a delivery loss.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `flush_deferred_self_draft_sync` wraps its body in `try/except` and logs at WARNING on failure (`session_health.py`) — the regression test must assert that an outbox/Redis failure is logged and swallowed (never raises out of `finalize_session`, which must not be made fallible by this change).
- [ ] The chokepoint invocation must itself be exception-isolated: a flush failure must NOT prevent `finalize_session` from completing the status write. Test: stub the flush to raise, assert the session still reaches its terminal status.

### Empty/Invalid Input Handling
- [ ] `deferred_self_draft_text` empty/whitespace → helper already substitutes "I couldn't finish responding to that — please try again." Add a test asserting the canned notice is sent when `_text` is empty but `_pending` is True.
- [ ] `deferred_self_draft_pending` absent/falsy → helper early-returns; chokepoint must not send anything for ordinary completions. Test: a normal `completed` session with no deferral triggers zero outbox writes.
- [ ] Agent-output processing: confirm the continuation no-op (empty turn input, `session_executor.py:1548`) cannot silently loop — Q2 cleanup removes the self-draft continuation so this path is not re-entered.

### Error State Rendering
- [ ] User-visible: the flushed reply (or canned notice) must reach the outbox. Test asserts an `rpush` to the project outbox with the held text on the `completed` path.
- [ ] Verify the narration gate (`is_narration_only`) substitution still applies on the `completed` path (parity with the recovery path).

## Test Impact

- [ ] `tests/unit/test_session_health_tool_timeout.py` — UPDATE: this file exercises the existing `failed`/`abandoned` fallback. After the call sites move to the chokepoint, assertions that the fallback fires on `failed`/`abandoned` must still pass — verify they assert *observable delivery* (outbox write / SETNX), not the specific call site in `session_health.py`. If any test asserts the helper is called from a specific `session_health.py` line, REPLACE it to assert delivery-on-finalize instead.
- [ ] `tests/unit/test_output_handler.py` — UPDATE if it asserts defer-time behavior; confirm the persisted `extra_context` keys are unchanged (they are). Likely no change.
- [ ] `tests/unit/test_steering.py` — UPDATE only if it asserts the self-draft steering message is re-enqueued as a continuation; the Q2 cleanup (filtering `drafter-fallback` sender out of the continuation) may change that expectation. Audit during build.
- [ ] New: `tests/unit/test_deferred_self_draft_completed.py` (create) — the primary regression: deferral → clean `completed` → held text delivered exactly once; plus the no-double-send case (completion + later recovery).

## Rabbit Holes

- **Rewriting the continuation re-enqueue mechanism wholesale.** Q2's general fix (guarding all continuations against terminal parents) is a larger change. Scope this plan to the *self-draft* case only (filter the `drafter-fallback` sender, or the narrow parent-terminal guard for that one path). A general continuation-lifecycle overhaul is a separate issue.
- **Making `finalize_session` fully async (or scheduling async work from it).** Do not convert the chokepoint and its dozens of sync callers to async, and do not introduce `create_task`/`run_until_complete` to call the old async helper — there is no running loop on the `completed` path. The flush must be plain synchronous code that ends in a direct outbox `rpush`.
- **Per-run dedup scoping.** The helper's docstring notes the 1h TTL is intentionally not per-run. Do not add `started_at` to the dedup key unless a concrete resume double-send is demonstrated — out of scope.
- **Touching the drafter / validator logic.** The `needs_self_draft` decision and `SELF_DRAFT_INSTRUCTION` are upstream and correct; this plan only changes *when the held text is flushed*.

## Risks

### Risk 1: Loop-handling error breaks finalization (eliminated by the sync design)
**Impact (the hazard this design removes):** `finalize_session` is sync and called from many contexts (worker loop, executor guards, health monitor) with no guaranteed running event loop. A naive `await`/`create_task`/`run_until_complete` would raise "no running event loop" or "loop already running," breaking finalization for *every* session — this is the BLOCKER the revision closes.
**Mitigation:** The flush is implemented as a **fully synchronous** helper that writes the held text directly to the Redis outbox via `rpush` — no `await`, no `create_task`, no loop handling. It therefore behaves identically with or without an ambient loop. Additionally the invocation is exception-isolated so any error (e.g. a Redis failure) degrades to "flush skipped, status still written," never "finalize crashes." A unit test stubs the flush to raise and asserts the terminal status is still set.

### Risk 2: Double-send when completion and a later recovery both observe the flag
**Impact:** Human receives the reply twice.
**Mitigation:** The existing SETNX `self_draft_fallback_sent:{session_id}` (1h) already guarantees exactly-once across all callers. Regression test simulates a `completed` flush followed by a `failed` recovery on the same `session_id` and asserts exactly one outbox write.

### Risk 3: Removing the three `session_health.py` call sites regresses the recovery path
**Impact:** `failed`/`abandoned` sessions stop flushing if the chokepoint doesn't actually cover those branches.
**Mitigation:** Verify each of the three branches calls `finalize_session` immediately after the (removed) explicit call. Keep the existing `failed`/`abandoned` tests green as the proof. If any branch does NOT route through `finalize_session`, leave its explicit call in place.

## Race Conditions

### Race 1: Completion flush vs. health-monitor recovery flush on the same session
**Location:** `models/session_lifecycle.py:221` (chokepoint) and `agent/session_health.py:1917/1940/1968` (recovery branches).
**Trigger:** A session completes (flush A at the chokepoint) and, before the 1h dedup window, the health monitor independently observes the same `deferred_self_draft_pending` flag and attempts flush B.
**Data prerequisite:** `extra_context["deferred_self_draft_pending"]` is True and `deferred_self_draft_text` is populated (written at defer time, `output_handler.py:453-456`, before any terminal transition).
**State prerequisite:** The SETNX key `self_draft_fallback_sent:{session_id}` must be checked-and-set atomically before delivery.
**Mitigation:** Existing atomic SETNX with `nx=True, ex=3600`. First caller wins; second early-returns. Test proves single delivery.

### Race 2: Defer-time persist not yet visible at finalization
**Location:** `output_handler.py:453-456` (persist) vs. `session_lifecycle.py:221` (chokepoint read).
**Trigger:** finalization reads `extra_context` before the defer-time `save(update_fields=["extra_context"])` is durable.
**Data prerequisite:** The defer happens *inside* the agent's turn (before the turn returns); the terminal transition happens *after* the turn returns. The persist is therefore strictly ordered before finalization within a single session's lifecycle.
**State prerequisite:** The chokepoint must read the authoritative session (re-read), not a stale in-memory copy that predates the persist. `finalize_session` already does a CAS re-read; the flush must use the re-read object's `extra_context`.
**Mitigation:** Use the CAS-re-read session for the `extra_context` read at the chokepoint (the new sync helper accepts the `session` object and reads `getattr(session, "extra_context", ...)`). Build verifies the chokepoint passes the re-read object, not the caller's possibly-stale one.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1794] General continuation-lifecycle hardening (guarding *all* re-enqueued continuations against already-terminal parents) is broader than this fix. This plan scopes Q2 to the self-draft case only.
- [CONDITIONAL-SPLIT] Q2 (the self-draft continuation re-enqueue cleanup) is itself conditional: if the core chokepoint flush consumes the Small appetite, Q2 splits to a follow-on slug `deferred_self_draft_continuation_cleanup`. Q2 is NOT a delivery-correctness dependency — the chokepoint flush is authoritative for delivery, so deferring Q2 leaves only a cosmetic 0.2s no-op continuation, not a lost reply.
- Core in-scope for this plan regardless: the sync chokepoint flush, deletion of the async helper + its three call sites, dedup preservation, and the regression test.

## Update System

No update system changes required — this is a purely internal bug fix to the worker/session-lifecycle code. No new dependencies, config files, secrets, or migration steps. The change propagates to all machines via the normal `/update` git pull + service restart.

## Agent Integration

No agent integration required — this is a bridge/worker-internal delivery-path fix. No new CLI entry point in `pyproject.toml [project.scripts]`, no new MCP server, no `.mcp.json` change. The bridge already delivers from the outbox; this change only ensures the held text reaches the outbox on the `completed` path. The behavior is verified by the regression test (a `completed` deferral produces an outbox write), not by an agent-invoked tool.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/` doc that covers the deferred self-draft / delivery fallback (locate the doc that documents PR #1739 / issue #1730 — likely under message-drafter or session-lifecycle features) to state that the fallback now fires on **all** terminal paths via the `finalize_session` chokepoint, not only `failed`/`abandoned`.
- [ ] If no such doc exists, add a short section to the session-lifecycle feature doc describing the deferred-self-draft flush invariant and its chokepoint.

### Inline Documentation
**Single source for the docstring work — the build-chokepoint task references this; it is NOT duplicated as a separate task bullet there.**
- [ ] The async `_deliver_deferred_self_draft_fallback` is deleted, so its stale "Called on every terminal recovery branch" / "Terminal-branch-only precondition" docstring goes with it. The replacement `flush_deferred_self_draft_sync` helper carries a fresh docstring stating it is the single chokepoint flush covering **all** terminal statuses (`completed`, `failed`, `abandoned`) and that it is fully synchronous (direct outbox `rpush`, no event loop).
- [ ] Comment the chokepoint invocation in `finalize_session` explaining the deferred-flush invariant and why it is synchronous (the `completed` path has no running loop).

## Success Criteria

**Behavioral / user-facing (the bug actually fixed):**
- [ ] **Held text is delivered exactly once on the `completed` path.** A session that defers a reply for self-draft and then reaches `completed` without redrafting produces exactly **one** outbox `rpush` (`telegram:outbox:{session_id}` or the email equivalent) carrying the held `deferred_self_draft_text` — or the narration/canned equivalent when the text is narration-only/empty. The regression test asserts the payload content and the exact rpush count (1), proving the human receives the reply they would otherwise have silently lost.
- [ ] **No silent loss and no double-send.** Completion-flush followed by a later `failed`/`abandoned` recovery on the same `session_id` yields exactly **one** outbox write total (SETNX dedup preserved) — the human is never messaged twice.
- [ ] A normal `completed` session with no pending deferral produces **zero** flush-originated outbox writes (no spurious sends on the happy path).

**Structural:**
- [ ] The flush fires on all terminal paths (`completed`, `failed`, `abandoned`) via the single `finalize_session` chokepoint — the three explicit `session_health.py` call sites AND the now-unused `async def _deliver_deferred_self_draft_fallback` are removed (or a branch bypassing `finalize_session` is explicitly justified).
- [ ] grep confirms zero explicit `_deliver_deferred_self_draft_fallback(entry)` calls remain in `session_health.py` and exactly one `flush_deferred_self_draft_sync` invocation exists at the `finalize_session` chokepoint.
- [ ] The new `flush_deferred_self_draft_sync` helper is fully synchronous (no `await`/`create_task`/`run_until_complete`) — verified by grep/inspection — so it cannot raise "no running event loop" on the `completed` path.

**Q2 (CONDITIONAL — see Appetite):**
- [ ] If shipped in this slug: the re-enqueued self-draft continuation no longer no-op-fails — the `drafter-fallback` steering is filtered out of the continuation re-enqueue. The redraft path is no longer relied upon for delivery (the chokepoint flush is authoritative). If split to a follow-on slug, this criterion moves there and is recorded as deferred in No-Gos.

**Regression / no-regression:**
- [ ] Regression test exists (`tests/unit/test_deferred_self_draft_completed.py`): `needs_self_draft` deferral → immediate clean `completed` → held text delivered exactly once.
- [ ] No regression to the existing `failed`/`abandoned` fallback behavior or its 1-hour dedup (existing tests stay green).
- [ ] The stale `_deliver_deferred_self_draft_fallback` / chokepoint docstrings are corrected (single source — see Documentation; covered by the inline-docs task, not duplicated elsewhere).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (chokepoint-flush)**
  - Name: flush-builder
  - Role: Relocate the deferred-self-draft flush to the `finalize_session` chokepoint; remove the three `session_health.py` call sites; isolate the sync/async boundary.
  - Agent Type: builder
  - Resume: true

- **Builder (continuation-cleanup) — CONDITIONAL**
  - Name: continuation-builder
  - Role: Resolve Q2 (only if core fix fits the Small appetite) — filter the `drafter-fallback` self-draft steering out of the continuation re-enqueue. If split to a follow-on slug, this member is not staffed in this run.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (regression)**
  - Name: regression-tester
  - Role: Write `test_deferred_self_draft_completed.py` (completed-path delivery + no-double-send + exception isolation + empty-text canned notice).
  - Agent Type: test-engineer
  - Resume: true

- **Validator (delivery)**
  - Name: delivery-validator
  - Role: Verify all success criteria, dedup correctness, no regression to `failed`/`abandoned`.
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Map and place the chokepoint flush
- **Task ID**: build-chokepoint
- **Depends On**: none
- **Validates**: tests/unit/test_session_health_tool_timeout.py, tests/unit/test_deferred_self_draft_completed.py (create)
- **Assigned To**: flush-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `flush_deferred_self_draft_sync(session)` to `agent/session_health.py`: fully synchronous (no `await`/`create_task`), performing pending-check → SETNX dedup → narration/canned-notice substitution → direct outbox `r.rpush(queue_key, json.dumps(payload))` for the resolved transport. Lift the shared narration/canned/dedup/payload logic out of the deleted async helper so nothing is duplicated.
- Invoke `flush_deferred_self_draft_sync` (lazy import to avoid cycle) from `finalize_session` (`models/session_lifecycle.py:221`) once, gated on `extra_context["deferred_self_draft_pending"]`, reading the CAS-re-read session object, before the status `save()`.
- Exception-isolate the invocation: a flush failure must not prevent the status write.
- Confirm each of the three `session_health.py` branches (`:1917/1940/1968`) calls `finalize_session` immediately after; remove the now-redundant explicit calls AND delete the `async def _deliver_deferred_self_draft_fallback` (fully replaced).
- (Docstrings for the new helper + chokepoint comment are covered by the Documentation → Inline Documentation section — do not re-author here.)

### 2. Resolve Q2 — continuation cleanup (CONDITIONAL)
- **Task ID**: build-continuation
- **Depends On**: build-chokepoint (Q2 is only meaningful once the flush is authoritative)
- **Condition**: Execute only if the core chokepoint fix (task 1 + task 3) lands within the Small appetite. If it does not, split this task to a follow-on slug `deferred_self_draft_continuation_cleanup` and record the deferral in No-Gos — do NOT block the core delivery fix on it.
- **Validates**: tests/unit/test_steering.py
- **Assigned To**: continuation-builder
- **Agent Type**: builder
- **Parallel**: false
- Read `session_executor.py:1935-1976`; confirm whether the `drafter-fallback` self-draft steering is in the re-enqueued "leftover".
- Filter the `drafter-fallback` sender out of the continuation re-enqueue.
- Ensure no genuine (non-self-draft) unconsumed steering is lost by the change.

### 3. Regression + failure-path tests
- **Task ID**: build-tests
- **Depends On**: build-chokepoint
- **Validates**: tests/unit/test_deferred_self_draft_completed.py
- **Assigned To**: regression-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Deferral → clean `completed` → assert exactly one outbox write of the held text.
- No-double-send: completion-flush + later `failed` recovery on same `session_id` → exactly one write.
- Exception isolation: flush stubbed to raise → status still set terminal.
- Empty `deferred_self_draft_text` but pending True → canned notice sent.
- Normal `completed` with no deferral → zero outbox writes.

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-chokepoint, build-continuation, build-tests
- **Assigned To**: delivery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full unit suite; confirm existing `failed`/`abandoned` fallback tests stay green.
- grep-confirm: three explicit calls removed, exactly one chokepoint invocation.
- Verify dedup and exception-isolation criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_deferred_self_draft_completed.py tests/unit/test_session_health_tool_timeout.py tests/unit/test_steering.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Async helper + calls removed | `grep -c "_deliver_deferred_self_draft_fallback" agent/session_health.py` | match count == 0 |
| Sync helper defined once | `grep -c "def flush_deferred_self_draft_sync" agent/session_health.py` | output contains 1 |
| Chokepoint invokes the sync flush | `grep -c "flush_deferred_self_draft_sync" models/session_lifecycle.py` | output > 0 |
| Sync helper has no loop calls | `awk '/def flush_deferred_self_draft_sync/{f=1} f&&/^def /&&!/flush_deferred_self_draft_sync/{f=0} f' agent/session_health.py | grep -nE "await \|create_task\|run_until_complete"` | no matches (the helper body contains no event-loop calls) |

---

## Resolved Questions

All open questions from the initial draft were resolved during plan revision (critique pass, 2026-06-25). They are recorded here as durable decisions, not open items.

1. **Q2 disposition (continuation) — RESOLVED: narrow filter.** Filter the `drafter-fallback` self-draft steering out of the continuation re-enqueue (targeted, lower-risk), rather than a broad "drop any continuation whose parent is terminal" guard. The general guard is tracked separately under No-Gos `[SEPARATE-SLUG #1794]`. Q2 itself is CONDITIONAL — see Appetite / No-Gos.
2. **Completion gating (issue Q4) — RESOLVED: flush-then-complete.** A deferred-self-draft session is allowed to reach `completed`; the chokepoint flushes the held text at `finalize_session` rather than blocking completion until the redraft resolves. Flush-at-finalize is the intended invariant, not a hard gate.
3. **Sync/async dispatch (the BLOCKER) — RESOLVED: fully synchronous flush.** `finalize_session` is a plain sync `def` with no event-loop dispatch, and the `completed` path reaches it with no ambient running loop. The flush is therefore a fully synchronous helper (`flush_deferred_self_draft_sync`) ending in a direct outbox `r.rpush(...)` — NO `await`/`create_task`/`run_until_complete`. There is no fire-and-forget task and no moving the chokepoint up to an async caller. See Technical Approach (lines ~137-149), Rabbit Holes, and Risk 1.
