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
`_deliver_deferred_self_draft_fallback()` (`agent/session_health.py:1338` —
anchor by symbol; line numbers are HEAD-of-writing hints), is
meant to flush that held text if the session dies before redrafting.

But the fallback is wired **only into the health-monitor `failed`/`abandoned`
recovery branches** (`session_health.py:1736`, `:1759`, `:1787`). The normal
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

**File:line references re-verified (anchor by SYMBOL; line numbers are HEAD-of-writing hints — Builder MUST grep for these symbols to confirm current locations before editing):**
- `agent/output_handler.py:453-456` — defer-time persistence of `deferred_self_draft_pending`/`deferred_self_draft_text` into `extra_context` — **still holds.**
- `agent/output_handler.py:749` `_inject_self_draft_steering` — pushes `SELF_DRAFT_INSTRUCTION` to the steering queue, returns True to defer delivery — **still holds** (issue cited `:752`; the def line is `:749`, body unchanged).
- `agent/session_health.py:1338` `_deliver_deferred_self_draft_fallback(entry)` — defined here; docstring says "Called on every terminal recovery branch (`failed` and `abandoned`)" — **still holds** (HEAD-verified at `:1338`; re-anchor by symbol).
- `agent/session_health.py:1736` (abandoned, local), `:1759` (failed, max recovery), `:1787` (failed, subprocess not confirmed dead) — the three existing call sites, all `await _deliver_deferred_self_draft_fallback(entry)` — **still holds** (HEAD-verified at `:1736/:1759/:1787`; re-anchor by symbol).
- `models/session_lifecycle.py:221` `finalize_session(session, status, ...)` — the single centralized terminal-transition handler — **confirmed present.**
- `bridge/session_transcript.py:317` — `complete_transcript()` calls `finalize_session(s, status, reason="transcript completed: ...")` on the normal path — **confirmed.** This is the `completed` write that currently bypasses the fallback.
- `agent/session_completion.py:167` `_complete_agent_session` → `finalize_session(...)` — **confirmed.**
- `agent/session_executor.py:1893-1944` — re-enqueue of unconsumed steering as a continuation, reusing the same `session_id` (`leftover` list at `:1897`, `if leftover:` gate at `:1898`, `leftover[0]` index at `:1921`) — **confirmed** (Q2 root cause area; HEAD-verified region).
- `agent/session_executor.py:1548-1579` — empty-turn-input guard finalizing `failed` with `reason="empty_container_message"` — **confirmed** (matches the 0.2s death).

**Builder MUST grep for these symbols to confirm current locations before editing — line numbers in this plan are hints only.** (`_deliver_deferred_self_draft_fallback`, `flush_deferred_self_draft_sync`, `finalize_session`, `if current_status == status`, `get_authoritative_session`, `leftover`.) `get_authoritative_session` is importable as `from models.session_lifecycle import get_authoritative_session` (already imported at `session_health.py:35`).

**Cited sibling issues/PRs re-checked:**
- #1730 / PR #1739 (merged 2026-06-18) — added the original `failed`/`abandoned` deferred fallback. This is the direct predecessor; it wired only the recovery branches, leaving the `completed` gap this issue closes.

**Commits on main since issue was filed (touching referenced files):** none — the issue was filed today (2026-06-25T08:59Z) and no commits have touched `session_health.py`, `session_lifecycle.py`, `session_transcript.py`, or `output_handler.py` since.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Line-number drift only; all claims hold against the HEAD-verified locations above (helper `:1338`; call sites `:1736`/`:1759`/`:1787`; Q2 region `:1893-1944`). Line numbers throughout this plan are HEAD-of-writing hints — anchor by symbol.

## Prior Art

- **Issue #1730 / PR #1739** (merged 2026-06-18): "deferred delivery lost when tool_timeout kills session: no fallback when self-draft steering is pending." Introduced `_deliver_deferred_self_draft_fallback()`, the defer-time `extra_context` persistence, and the 1-hour SETNX dedup. Wired the fallback into the health-monitor `failed`/`abandoned` branches **only**. This is the direct predecessor — the present issue is the missed `completed` path of the same mechanism.
- **Issue #1219 / PR #1685**: Repositioned the message drafter to a verbatim pass-through + validation filter, establishing the `needs_self_draft` signal path. Context only — not a fix attempt for this gap.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|-----------------------|
| PR #1739 (#1730) | Added the deferred-self-draft fallback + defer-time persistence + 1h dedup; wired it into the three health-monitor recovery branches (`session_health.py:1736/1759/1787` — anchor by symbol). | It targeted **only** the recovery branches because the original symptom was a tool-timeout *kill*. It never wired the fallback into the normal `completed` path (`finalize_session` via `complete_transcript`), so a clean completion after a deferral silently drops the held text. The fix addressed the kill-path symptom, not the general invariant "any terminal transition must flush a pending deferral." |

**Root cause pattern:** The deferred-flush invariant was attached to *specific terminal branches* rather than to the *single chokepoint* every terminal transition funnels through (`finalize_session`). Per-branch wiring is fragile: each new terminal path must remember to call the fallback. This plan moves the invariant to the chokepoint so it holds for `completed`, `failed`, `abandoned`, and any future terminal status by construction.

## Data Flow

1. **Entry point**: Agent produces a reply → `TelegramRelayOutputHandler.send()` (`agent/output_handler.py`).
2. **Validation**: drafter flags `needs_self_draft=True` (wire-format violation / empty promise).
3. **Defer**: handler calls `_inject_self_draft_steering()` (push `SELF_DRAFT_INSTRUCTION` to steering queue) AND persists `deferred_self_draft_pending=True` + `deferred_self_draft_text=<text>` into `extra_context` (`output_handler.py:453-456`). No outbox write happens.
4. **Session ends**: worker reaches a terminal transition. All terminal writes funnel through `finalize_session(session, status, ...)` (`models/session_lifecycle.py:221`).
   - On `failed`/`abandoned` via the health monitor: `await _deliver_deferred_self_draft_fallback(entry)` is called *before* `finalize_session`, flushing the held text for **both** transports. ✅ This call is **retained** — it is the email-transport fallback (the sync chokepoint flush handles telegram only; on these branches the SETNX dedup ensures the telegram chokepoint flush and the async helper never double-send).
   - On `completed` via the normal executor path (`complete_transcript` → `finalize_session`, both sync, no running loop): **no fallback runs today** — held text is lost. ❌ (the bug). After this change the sync chokepoint flush covers the **telegram** transport here; email on the `completed` path is a documented known-gap (see Technical Approach BLOCKER 2 scope note).
5. **Output (target state)**: the **synchronous** flush (TELEGRAM transport) fires once at the chokepoint regardless of terminal status, gated **above the idempotency early-return** (`session_lifecycle.py:337` — anchor by symbol) so a re-finalize of an already-terminal session still flushes; it reads the deferral flag from a fresh authoritative session (`get_authoritative_session`), applies the narration gate, and `rpush`es the held text directly to the telegram outbox (`telegram:outbox:{session_id}`) → bridge delivers to Telegram. For `transport == "email"` the sync flush early-returns and the async helper carries email coverage on `failed`/`abandoned`. No async send-callback is awaited at the chokepoint — it is sync and has no running loop on the `completed` path. Dedup via SETNX `self_draft_fallback_sent:{session_id}` (1h) guarantees exactly-once across both completion entry points, any later recovery, and the retained async helper.

## Architectural Impact

- **New dependencies**: none. The flush mechanism already exists; this adds a new telegram sync flush at the chokepoint and retains the existing async helper for email.
- **Interface changes**: `finalize_session()` gains the responsibility of flushing a pending deferral for the **telegram** transport. The flush is implemented as a **new fully-synchronous helper** (`flush_deferred_self_draft_sync`) that writes directly to the Redis telegram outbox via `rpush` — see Technical Approach. The pre-existing `async def _deliver_deferred_self_draft_fallback` is **retained** as the email-transport fallback on the three `failed`/`abandoned` call sites (replicating the email outbox payload synchronously is out of appetite — see BLOCKER 2 scope note in Technical Approach). The new sync helper and `finalize_session` are both sync, so there is no sync/async boundary to bridge at the chokepoint.
- **Coupling**: slightly increases coupling from `models/session_lifecycle.py` → `agent/session_health.py` (the new sync flush helper). Mitigated by a lazy import inside the call to avoid an import cycle (`session_lifecycle` is imported very early).
- **Data ownership**: unchanged. The fallback reads `extra_context` and writes to the outbox, same as today.
- **Reversibility**: high — the change is one invocation added at the chokepoint (telegram flush) plus a new sync helper; the existing async helper and its three call sites are left intact for email. Trivially revertible.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the chokepoint placement and Q2 disposition)
- Review rounds: 1 (verify dedup correctness and no double-send)

**Core scope (fits Small):** the sync chokepoint flush in `finalize_session` (telegram transport), and the regression test. The async helper + its three call sites are **retained** for email coverage. This is the delivery-loss fix and is self-contained.

**Q2 continuation cleanup is CONDITIONAL.** The continuation re-enqueue lives in `session_executor.py:1893-1944` (anchor by symbol) — independent code from the lifecycle chokepoint. Because the chokepoint flush makes the self-draft continuation cosmetic (no longer a delivery path), Q2 is NOT required for the bug fix. Disposition: **ship Q2 in this slug only if the core fix lands cleanly within the Small appetite**; if the chokepoint work consumes the appetite or Q2 reveals coupling, split Q2 to a follow-on slug (`deferred_self_draft_continuation_cleanup`) referencing this plan. The Success Criteria mark the core flush as required and Q2 as conditional accordingly.

The mechanism already exists; the core is a new telegram sync chokepoint flush (the async helper stays for email) with one regression test. The risk surface is the no-double-send guarantee — reviewable in a focused pass.

## Prerequisites

No prerequisites — this work has no external dependencies (no new secrets, services, or config).

## Solution

### Key Elements

- **Centralized telegram flush at the chokepoint**: `finalize_session()` (`models/session_lifecycle.py:221` — anchor by symbol) becomes the single place that flushes a pending deferred self-draft **for the telegram transport** on **any** terminal status (`completed`, `failed`, `abandoned`, and by construction `killed`/`cancelled`). Email coverage stays on the retained async helper (`failed`/`abandoned` only).
- **Sync helper for telegram transport; async helper retained for email**: a new fully-synchronous `flush_deferred_self_draft_sync(session)` is invoked once at the chokepoint and handles the **TELEGRAM** transport (the production incident and all acceptance criteria are telegram). For `transport == "email"` the sync flush early-returns; the existing async `_deliver_deferred_self_draft_fallback` (`session_health.py:1338` — anchor by symbol) remains wired on the three `failed`/`abandoned` call sites (`:1736/:1759/:1787`) to preserve email coverage on those paths. The async helper is therefore **NOT deleted** — it is the email fallback. See Technical Approach for the email known-gap on the `completed` path.
- **Q2 — continuation cleanup (CONDITIONAL)**: the re-enqueued self-draft "continuation" (`session_executor.py:1893-1944` — anchor by symbol) is the unreliable path that died in 0.2s. With the terminal-path flush in place, the self-draft text is guaranteed delivered at completion, so the continuation re-enqueue for the *self-draft* case is redundant (cosmetic, not a delivery loss). Resolve by filtering the `drafter-fallback` self-draft steering out of the continuation re-enqueue. **Ships in this slug only if the core fix fits the Small appetite; otherwise splits to a follow-on slug** (see Appetite).
- **Preserved dedup**: the existing SETNX `self_draft_fallback_sent:{session_id}` (1h) remains the exactly-once guarantee. Moving the call to the chokepoint cannot double-send: the first caller (completion or a later recovery) wins the SETNX; the second is a no-op.

### Flow

Agent reply flagged → delivery deferred + `extra_context` persisted → session reaches **any** terminal transition → `finalize_session()` → (flag set?) → `flush_deferred_self_draft_sync()` rpushes held text to the outbox once (synchronously) → bridge delivers to Telegram.

### Technical Approach

**Decided sync/async shape (closes Open Question 3 — the BLOCKER).** The earlier draft told the build to "mirror how `finalize_session`'s existing async side effects are dispatched." That instruction was wrong: `finalize_session()` (`models/session_lifecycle.py:221`) is a plain **sync `def`** with NO `await`, NO `asyncio.create_task`, and NO event-loop dispatch anywhere — every side effect it runs (telemetry, auto-tag, checkpoint, parent finalization, save) is synchronous. The `completed` path reaches it via `complete_transcript()` (`bridge/session_transcript.py:252`, also a sync `def`) with **no ambient running event loop**, so a naive `asyncio.create_task(...)` at the chokepoint would raise `RuntimeError: no running event loop`. There is no async dispatch pattern to mirror — so the build does **not** reuse `_deliver_deferred_self_draft_fallback`'s async `await send_cb(...)` shape at the chokepoint.

Instead, **make the flush synchronous up to the outbox `rpush`**:

- The actual telegram delivery commit is already synchronous. The async `send_cb` chain (`TelegramRelayOutputHandler.send`) does redundancy/RTR/narration processing, but the terminal act is a sync `r.rpush(f"telegram:outbox:{session_id}", json.dumps(payload))` (`agent/output_handler.py:705-720` — anchor by symbol). The chokepoint flush enqueues the held text **directly to the Redis telegram outbox synchronously**, bypassing the async send-callback entirely (see DECISION on redundancy-filter/RTR bypass below).
- **CONCERN 3 — DECISION (not an open question): the sync flush intentionally bypasses the redundancy filter and RTR** that `send_cb` / `TelegramRelayOutputHandler.send` apply (the drafter → redundancy → RTR → outbox sequencing at `output_handler.py:476` and below — anchor by symbol). Justification: (a) the held `deferred_self_draft_text` was **already drafter-validated at defer time** — the drafter ran before deferral, so re-running it adds nothing; (b) at a terminal flush there is **no live SDLC session** for RTR to bypass-to, so RTR is a no-op there; (c) redundancy suppression is a **nice-to-have, not a correctness guard** — sending one possibly-redundant terminal reply is strictly better than the current silent loss. This is a deliberate, bounded trade-off — see Rabbit Holes and Risk 4. It must not be re-flagged as an open question.
- Add a sync helper — `flush_deferred_self_draft_sync(session)` in `agent/session_health.py` (next to the retained async helper) — that performs, all synchronously:
  1. Read `extra_context["deferred_self_draft_pending"]`; early-return if falsy.
  2. SETNX `self_draft_fallback_sent:{session_id}` (`nx=True, ex=3600`); early-return if not acquired (preserves exactly-once across all callers).
  2a. **Transport gate — TELEGRAM only.** If the resolved transport is `email`, **early-return** (the retained async helper covers email on `failed`/`abandoned`; the `completed`-path email case is a documented known-gap — see scope note below). Proceed only for telegram.
  3. Recover `deferred_self_draft_text`; apply the narration gate (`is_narration_only` → `NARRATION_FALLBACK_MESSAGE`) and the empty-text canned-notice substitution.
  4. **Build the telegram outbox payload with this EXACT recipe** (verified against `agent/output_handler.py:705-720` — anchor by symbol). Note the async helper at `session_health.py:1338` delegates to `send_cb` and never builds a payload itself, so there is no single payload object to "copy" — build it explicitly:
     ```python
     # Inside flush_deferred_self_draft_sync, telegram transport:
     chat_id = getattr(session, "chat_id", None) or ""
     reply_to = int(getattr(session, "telegram_message_id", None) or 0) or None
     payload = {
         "chat_id": chat_id,
         "reply_to": reply_to,
         "text": message,            # narration/canned-substituted held text
         "session_id": session_id,
         "timestamp": time.time(),
     }
     queue_key = f"telegram:outbox:{session_id}"
     r.rpush(queue_key, json.dumps(payload))
     r.expire(queue_key, 3600)       # OUTBOX_TTL — REQUIRED; matches output_handler.OUTBOX_TTL
     ```
     The `reply_to` key **and** the `r.expire(queue_key, OUTBOX_TTL)` call are **MANDATORY** — the prior wording omitted both. Reuse `output_handler.OUTBOX_TTL` for the TTL value rather than a bare literal where importable.
  5. Wrap the whole body in `try/except`, log at WARNING, never raise.
- **Insertion point — gate the flush EARLY, before the idempotency early-return (closes BLOCKER 2a).** (Anchor by symbol — line numbers are HEAD-of-writing hints; grep `if current_status == status` and `def finalize_session`.) `finalize_session()` (`models/session_lifecycle.py:221`) has an idempotency early-return — `current_status = getattr(session, "status", None); if current_status == status: ... return` (HEAD-verified at **`:337`**) — which fires *before* the terminal-state guard and the CAS re-read (`fresh = get_authoritative_session(...)`) and short-circuits **all** side effects. A re-finalize of an already-`completed` session (e.g. `complete_transcript` runs, then `_complete_agent_session` runs on the same session, or a health-monitor recovery re-finalizes) hits that `return` and would skip a flush placed anywhere lower in the function. Therefore the flush call must be inserted **before line 337's `if current_status == status` early-return** (after the telemetry tap / AC4 reset block at `:320-333`, before the idempotency check) so a redundant re-finalize of an already-terminal session *still* flushes if the SETNX hasn't fired yet. The SETNX dedup (not the idempotency early-return) is what guarantees exactly-once; placing the flush above the early-return makes the two re-finalize callers (`complete_transcript` and `_complete_agent_session`) race into the same SETNX rather than one silently short-circuiting.
- **Authoritative read — read the flag from a FRESH session, not the caller's object (closes BLOCKER 2b).** The caller's in-memory `session` object may be stale: its `extra_context` can still read `deferred_self_draft_pending=False` if the defer-time `save(update_fields=["extra_context"])` happened on a *different* object instance than the one passed to `finalize_session`. Note the existing CAS block (`:365`) reads `fresh = get_authoritative_session(session_id)` purely for the status comparison and then **discards `fresh`** — it does NOT re-read `extra_context`, and it lives *below* the idempotency early-return anyway. So the flush must do its own authoritative read: call `get_authoritative_session(session_id)` (defined in the same module, `models/session_lifecycle.py:100` — no import needed) inside `flush_deferred_self_draft_sync`, read `deferred_self_draft_pending` / `deferred_self_draft_text` from that fresh object, and fall back to the caller's `session.extra_context` only if the fresh read returns `None`. This is the authoritative source for the deferral flag.
- `finalize_session()` calls `flush_deferred_self_draft_sync(session)` exactly once, before the idempotency early-return at `:337` (and therefore before the status `save()`). Because it is fully synchronous, it works identically whether the caller has a running loop or not — no loop handling, no `create_task`, no `run_until_complete`.
- **The existing `async def _deliver_deferred_self_draft_fallback` (`session_health.py:1338` — anchor by symbol) is RETAINED, NOT deleted** (closes BLOCKER 2 email-coverage reconciliation). It remains wired on its three `failed`/`abandoned` call sites (`:1736/:1759/:1787` — anchor by symbol) to carry **email-transport** deferred self-drafts, because the email outbox payload (`to`/`subject`/`in_reply_to`/`from_addr`) is derived inside `TelegramRelayOutputHandler._send_via_email_outbox` (`output_handler.py:184-291` — anchor by symbol) from `extra_context` + project config, and replicating that synchronously is **out of appetite**. The new sync flush and the retained async helper share the SETNX `self_draft_fallback_sent:{session_id}` key, so a given session is flushed by **exactly one** of them (whichever wins the SETNX) — never both. The narration/canned-notice substitution logic is shared via a small sync helper that both call (no duplication); the async helper keeps its email-payload `send_cb` delegation.

**Email-transport scope / known-gap (BLOCKER 2).** The sync completed-path flush handles **TELEGRAM transport only**. If `transport == "email"`, the sync flush early-returns; the retained async helper remains wired on `failed`/`abandoned` for email. **Known gap:** an email-transport deferred self-draft that reaches `completed` (not `failed`/`abandoned`) without redrafting is NOT flushed by this slug — it falls through. This is an explicit, accepted follow-on gap because the production incident and **all** acceptance criteria are telegram. It is recorded in No-Gos. (Preferred-simpler option per BLOCKER 2: keep the sync flush telegram-only, keep the async helper for email; do not attempt synchronous email-payload replication.)

**Delivery ordering on `failed`/`abandoned` (resolves CONCERN 2).** The three health-monitor branches `await _deliver_deferred_self_draft_fallback(entry)` *before* their `finalize_session(entry, ...)` call, so the flush is ordered before the terminal save. With the new telegram chokepoint flush inside `finalize_session` (before the status `save()`), both run before the terminal write commits — ordering is preserved on every path. The SETNX guard means the async helper (email) and the chokepoint flush (telegram) never double-send for the same session: the first to win the SETNX delivers, the other early-returns. Build must confirm each of the three branches routes through `finalize_session` immediately after the retained explicit call so no `failed`/`abandoned` flush is dropped.

- **Idempotency is already correct.** The sync helper early-returns if `deferred_self_draft_pending` is falsy and SETNX-guards delivery. No new dedup logic; the test must *prove* no double-send when both a completion and a later recovery observe the flag.
- **Keep the three explicit `session_health.py` calls AND the async helper** — they are the email-transport fallback on `failed`/`abandoned` (see BLOCKER 2 reconciliation above). Confirm the new telegram chokepoint flush covers the `failed`/`abandoned` telegram case (those branches call `finalize_session` immediately after the retained explicit call — verify each does), so telegram is never double-sent (SETNX) and email is never dropped (retained helper).
- **Q2 continuation (CONDITIONAL — see Appetite).** Read `session_executor.py:1893-1944` (anchor by symbol; grep `leftover`) and confirm whether the *self-draft* steering message is among the "leftover" re-enqueued messages. If it is, the continuation attempts a redraft that (per the production log) no-ops in 0.2s because the parent is already terminal / turn input strips empty. With the chokepoint flush guaranteeing telegram delivery, the self-draft continuation re-enqueue is redundant. Resolve by filtering the self-draft steering sender (`drafter-fallback`) out of the continuation re-enqueue — targeted, leaves the general continuation mechanism intact. **The filter must recompute the `leftover` list and guard the empty case**: after dropping `drafter-fallback`, if the list is empty, skip the re-enqueue entirely — there must be no `leftover[0]` index access (`session_executor.py:1921` — anchor by symbol) on an empty list, and the `if leftover:` gate (`:1898` — anchor by symbol) must see the *filtered* list. If Q2 ships, it must include a `tests/unit/test_steering.py` regression asserting the empty-after-filter and mixed-list cases. **This sub-task is CONDITIONAL**: it ships only if the chokepoint flush lands cleanly within the Small appetite; otherwise it splits to a follow-on slug (see Appetite) along with its regression test. The core fix (chokepoint flush) does NOT depend on Q2 — once the held text is flushed at finalize, the no-op continuation is cosmetic, not a delivery loss.

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

- [ ] `tests/unit/test_session_health_tool_timeout.py` — VERIFY (likely no change): this file exercises the existing `failed`/`abandoned` fallback (the async helper), which is **retained** for email coverage. Its assertions on `failed`/`abandoned` delivery should stay green. Confirm they assert *observable delivery* (outbox write / SETNX), not brittle call-site line numbers; if any test pins a specific `session_health.py` line, REPLACE it to assert delivery-on-finalize instead. The retained async path means these tests are not regressed by this change.
- [ ] `tests/unit/test_output_handler.py` — UPDATE if it asserts defer-time behavior; confirm the persisted `extra_context` keys are unchanged (they are). Likely no change.
- [ ] `tests/unit/test_steering.py` — UPDATE only if it asserts the self-draft steering message is re-enqueued as a continuation; the Q2 cleanup (filtering `drafter-fallback` sender out of the continuation) may change that expectation. Audit during build.
- [ ] New: `tests/unit/test_deferred_self_draft_completed.py` (create) — the primary regression: deferral → clean `completed` → the **original reply body** delivered exactly once (assert payload content, exercising both `complete_transcript` and `_complete_agent_session` against the single SETNX); plus the no-double-send case (completion + later recovery), the re-finalize-idempotency case (second finalize above the `:337` early-return still flushes once), exception isolation, empty-text canned notice, and the no-deferral zero-write case.

## Rabbit Holes

- **Rewriting the continuation re-enqueue mechanism wholesale.** Q2's general fix (guarding all continuations against terminal parents) is a larger change. Scope this plan to the *self-draft* case only (filter the `drafter-fallback` sender, or the narrow parent-terminal guard for that one path). A general continuation-lifecycle overhaul is a separate issue.
- **Making `finalize_session` fully async (or scheduling async work from it).** Do not convert the chokepoint and its dozens of sync callers to async, and do not introduce `create_task`/`run_until_complete` to call the old async helper — there is no running loop on the `completed` path. The flush must be plain synchronous code that ends in a direct outbox `rpush`.
- **Per-run dedup scoping.** The helper's docstring notes the 1h TTL is intentionally not per-run. Do not add `started_at` to the dedup key unless a concrete resume double-send is demonstrated — out of scope.
- **Touching the drafter / validator logic.** The `needs_self_draft` decision and `SELF_DRAFT_INSTRUCTION` are upstream and correct; this plan only changes *when the held text is flushed*.
- **Re-implementing the redundancy filter / RTR in the sync flush (DECISION: don't).** The sync flush deliberately bypasses the redundancy filter and RTR that `send_cb`/`TelegramRelayOutputHandler.send` apply (`output_handler.py:476` sequencing — anchor by symbol). This is intentional and bounded, NOT an oversight: the held `deferred_self_draft_text` was already drafter-validated at defer time, a terminal flush has no live SDLC session for RTR to bypass-to, and redundancy suppression is a nice-to-have not a correctness guard. Do not port these stages into the sync path. See Risk 4.
- **Synchronously replicating the email outbox payload.** `_send_via_email_outbox` derives `to`/`subject`/`in_reply_to`/`from_addr` from `extra_context` + project config (`output_handler.py:184-291` — anchor by symbol). Reproducing that in a sync helper is out of appetite. Email coverage stays on the retained async helper (`failed`/`abandoned`); completed-path email is a documented known-gap (No-Gos).

## Risks

### Risk 1: Loop-handling error breaks finalization (eliminated by the sync design)
**Impact (the hazard this design removes):** `finalize_session` is sync and called from many contexts (worker loop, executor guards, health monitor) with no guaranteed running event loop. A naive `await`/`create_task`/`run_until_complete` would raise "no running event loop" or "loop already running," breaking finalization for *every* session — this is the BLOCKER the revision closes.
**Mitigation:** The flush is implemented as a **fully synchronous** helper that writes the held text directly to the Redis outbox via `rpush` — no `await`, no `create_task`, no loop handling. It therefore behaves identically with or without an ambient loop. Additionally the invocation is exception-isolated so any error (e.g. a Redis failure) degrades to "flush skipped, status still written," never "finalize crashes." A unit test stubs the flush to raise and asserts the terminal status is still set.

### Risk 2: Double-send when completion and a later recovery both observe the flag
**Impact:** Human receives the reply twice.
**Mitigation:** The existing SETNX `self_draft_fallback_sent:{session_id}` (1h) already guarantees exactly-once across all callers. Regression test simulates a `completed` flush followed by a `failed` recovery on the same `session_id` and asserts exactly one outbox write.

### Risk 3: Telegram chokepoint flush double-sends with the retained async helper on `failed`/`abandoned`
**Impact:** On a `failed`/`abandoned` telegram session, both the retained async helper and the new telegram chokepoint flush could fire — human gets the reply twice.
**Mitigation:** Both share the SETNX `self_draft_fallback_sent:{session_id}` (`nx=True, ex=3600`). Whichever runs first wins; the other early-returns. The async helper is **retained** (not removed) specifically for email coverage — it is NOT deleted, so there is no recovery-path regression. Keep the existing `failed`/`abandoned` tests green as proof. Verify each of the three branches still routes through `finalize_session` immediately after its retained explicit call.

### Risk 4: Sync flush bypasses redundancy filter + RTR (accepted, bounded trade-off — DECISION)
**Impact:** A flushed terminal reply is not redundancy-suppressed and does not pass through RTR; in a pathological case the human could receive a reply that a live redundancy check would have suppressed.
**Mitigation / justification (this is a DECISION, not an open question):** (a) the held `deferred_self_draft_text` was already drafter-validated at defer time — the drafter ran before deferral; (b) at a terminal flush there is no live SDLC session for RTR to bypass-to, so RTR is a no-op; (c) redundancy suppression is a nice-to-have, not a correctness guard — one possibly-redundant terminal reply is strictly better than the current silent loss this slug fixes. The trade-off is bounded to terminal flushes only. Do not re-flag as an open question.

## Race Conditions

### Race 1: Completion flush vs. health-monitor recovery flush on the same session
**Location:** `models/session_lifecycle.py:221` (chokepoint) and `agent/session_health.py:1736/1759/1787` (recovery branches — anchor by symbol, line numbers are HEAD-of-writing hints).
**Trigger:** A session completes (flush A at the chokepoint) and, before the 1h dedup window, the health monitor independently observes the same `deferred_self_draft_pending` flag and attempts flush B.
**Data prerequisite:** `extra_context["deferred_self_draft_pending"]` is True and `deferred_self_draft_text` is populated (written at defer time, `output_handler.py:453-456`, before any terminal transition).
**State prerequisite:** The SETNX key `self_draft_fallback_sent:{session_id}` must be checked-and-set atomically before delivery.
**Mitigation:** Existing atomic SETNX with `nx=True, ex=3600`. First caller wins; second early-returns. Test proves single delivery.

### Race 2: Defer-time persist not yet visible at finalization
**Location:** `output_handler.py:453-456` (persist) vs. `session_lifecycle.py:221` (chokepoint read).
**Trigger:** finalization reads `extra_context` before the defer-time `save(update_fields=["extra_context"])` is durable.
**Data prerequisite:** The defer happens *inside* the agent's turn (before the turn returns); the terminal transition happens *after* the turn returns. The persist is therefore strictly ordered before finalization within a single session's lifecycle.
**State prerequisite:** The chokepoint must read the authoritative session, not a stale in-memory copy that predates the persist. The existing CAS re-read at `session_lifecycle.py:365` (`fresh = get_authoritative_session(session_id)`) is used **only** for the status comparison and then discarded — it does NOT re-read `extra_context`, and it lives below the idempotency early-return at `:337`. So the flush cannot piggyback on it.
**Mitigation:** `flush_deferred_self_draft_sync` performs its **own** authoritative read: it calls `get_authoritative_session(session_id)` (same module, `session_lifecycle.py:100`) and reads `deferred_self_draft_pending` / `deferred_self_draft_text` from that fresh object, falling back to the caller's `session.extra_context` only if the fresh read returns `None`. The caller's possibly-stale `extra_context` is never the sole source of truth. Build verifies the helper reads the fresh object, not just the passed-in `session`. (This precondition is stated in the helper's docstring — see Documentation → Inline Documentation.)

### Race 3: Re-finalize of an already-terminal session short-circuits before the flush
**Location:** `models/session_lifecycle.py:337` (idempotency early-return) vs. the flush invocation.
**Trigger:** `complete_transcript` finalizes a session to `completed`; then `_complete_agent_session` (or a health-monitor recovery) calls `finalize_session(session, "completed", ...)` again on the same `session_id`. The second call hits `if current_status == status: ... return` at `:337` and runs **no** side effects.
**Data prerequisite:** `deferred_self_draft_pending` is True but the first finalize's flush (if it ran) may have already fired the SETNX — or, in a path where the first object was stale, may NOT have fired it.
**State prerequisite:** The flush must execute on the *second* (idempotent) call too, so that if the first call's object was stale and skipped the flush, the second authoritative-read call still delivers exactly once.
**Mitigation:** Insert the flush call **above** the idempotency early-return at `:337` (see Technical Approach), so it runs on every `finalize_session` invocation regardless of whether the status is already terminal. The SETNX `self_draft_fallback_sent:{session_id}` (not the idempotency `return`) is the sole exactly-once guard: whichever invocation wins the SETNX delivers; the other early-returns inside the helper. The regression test exercises BOTH `complete_transcript` and `_complete_agent_session` against the single SETNX (see Success Criteria) to prove exactly-once across the double-finalize.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1794] General continuation-lifecycle hardening (guarding *all* re-enqueued continuations against already-terminal parents) is broader than this fix. This plan scopes Q2 to the self-draft case only.
- [CONDITIONAL-SPLIT] Q2 (the self-draft continuation re-enqueue cleanup) is itself conditional: if the core chokepoint flush consumes the Small appetite, Q2 splits to a follow-on slug `deferred_self_draft_continuation_cleanup`. Q2 is NOT a delivery-correctness dependency — the chokepoint flush is authoritative for delivery, so deferring Q2 leaves only a cosmetic 0.2s no-op continuation, not a lost reply.
- [KNOWN-GAP — email completed-path, follow-on] The sync completed-path flush handles **telegram transport only**. An email-transport deferred self-draft that reaches `completed` (not `failed`/`abandoned`) without redrafting is NOT flushed by this slug (the async helper covers email only on `failed`/`abandoned`). This is explicitly accepted because the production incident and all acceptance criteria are telegram; synchronously replicating the email outbox payload (`_send_via_email_outbox`) is out of appetite. Track as a follow-on if email completed-path loss is ever observed.
- Core in-scope for this plan regardless: the new telegram sync chokepoint flush, the **retained** async helper + its three call sites (email coverage on `failed`/`abandoned`), dedup preservation, and the regression test. The async helper is NOT deleted.

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
- [ ] The async `_deliver_deferred_self_draft_fallback` is **retained** for email coverage on `failed`/`abandoned` — update its docstring to state it now handles the **email transport** fallback specifically (telegram is covered by the new sync chokepoint flush), and that it shares the SETNX dedup with the sync flush. The new `flush_deferred_self_draft_sync` helper carries a fresh docstring stating it is the **telegram** chokepoint flush covering all terminal statuses (`completed`, `failed`, `abandoned`), that it is fully synchronous (direct telegram-outbox `rpush`, no event loop), that it early-returns for `transport == "email"`, and that it reads the deferral flag from a **fresh authoritative session** (`get_authoritative_session`), not the caller's possibly-stale `extra_context`.
- [ ] Comment the chokepoint invocation in `finalize_session` explaining the deferred-flush invariant, why it is placed **above the idempotency early-return** (so a re-finalize of an already-terminal session still flushes), and why it is synchronous (the `completed` path has no running loop).

## Success Criteria

**Behavioral / user-facing (the bug actually fixed):**
- [ ] **The human receives the ORIGINAL reply BODY, exactly once, on the `completed` path (TELEGRAM transport).** A telegram-transport session that defers a reply for self-draft and then reaches `completed` without redrafting produces exactly **one** outbox `rpush` to `telegram:outbox:{session_id}` whose payload body is the **non-empty original `deferred_self_draft_text`** (verbatim, not a placeholder) — or the narration/canned equivalent only when the text is genuinely narration-only/empty. The regression test asserts the **payload body content** (the actual reply text the human reads), not merely that "an rpush happened," and asserts the exact rpush count (1) — proving the human receives the reply they would otherwise have silently lost.
- [ ] **Exactly-once holds across BOTH completion entry points sharing the single SETNX.** The test exercises the `completed` path through **both** `complete_transcript` (`bridge/session_transcript.py:317`) **and** `_complete_agent_session` (`agent/session_completion.py:167`) finalizing the same `session_id` — including the double-finalize where both run — and asserts the original reply body is delivered exactly **one** time total (the single SETNX `self_draft_fallback_sent:{session_id}` dedups across both, and the flush sits above the idempotency early-return so the second finalize is not silently short-circuited).
- [ ] **No silent loss and no double-send.** Completion-flush followed by a later `failed`/`abandoned` recovery on the same `session_id` yields exactly **one** outbox write total (SETNX dedup preserved) — the human is never messaged twice.
- [ ] A normal `completed` session with no pending deferral produces **zero** flush-originated outbox writes (no spurious sends on the happy path).

**Structural:**
- [ ] The **telegram** flush fires on all terminal paths (`completed`, `failed`, `abandoned`) via the single `finalize_session` chokepoint. The three explicit `session_health.py` call sites AND the `async def _deliver_deferred_self_draft_fallback` are **retained** for email coverage (NOT removed) — verify each branch routes through `finalize_session` immediately after, so telegram is never double-sent (SETNX) and email is never dropped.
- [ ] grep confirms exactly one `flush_deferred_self_draft_sync` invocation exists at the `finalize_session` chokepoint, AND the three `_deliver_deferred_self_draft_fallback(entry)` calls remain in `session_health.py` (retained for email).
- [ ] The new `flush_deferred_self_draft_sync` helper is fully synchronous (no `await`/`create_task`/`run_until_complete`/`get_event_loop`) — verified by grep/inspection — so it cannot raise "no running event loop" on the `completed` path.
- [ ] The flush invocation is placed **above the idempotency early-return** at `models/session_lifecycle.py:337` (verified by inspection: the `flush_deferred_self_draft_sync(...)` call precedes the `if current_status == status: ... return` line), so a re-finalize of an already-terminal session is not silently short-circuited before the flush.
- [ ] The helper reads `deferred_self_draft_pending` from a fresh authoritative session via `get_authoritative_session(session_id)`, falling back to the caller's `extra_context` only on a `None` fresh read — verified by inspection. The caller's possibly-stale object is never the sole source of the flag.
- [ ] The telegram outbox payload includes the `reply_to` key AND the flush calls `r.expire(queue_key, OUTBOX_TTL)` (both MANDATORY) — verified by inspection of the helper against the `output_handler.py` recipe (`chat_id`/`reply_to`/`text`/`session_id`/`timestamp`).
- [ ] For `transport == "email"` the sync flush early-returns (verified by inspection); email coverage on `failed`/`abandoned` stays on the retained async helper. The email completed-path gap is recorded in No-Gos.

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
  - Role: Add the telegram sync flush at the `finalize_session` chokepoint; RETAIN the async helper + its three `session_health.py` call sites for email coverage; share the SETNX dedup between them.
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
- **Anchor by symbol — grep before editing; the line numbers below are HEAD-of-writing hints only.**
- Add `flush_deferred_self_draft_sync(session)` to `agent/session_health.py`: fully synchronous (no `await`/`create_task`/`run_until_complete`/`get_event_loop`), implemented as plain sync code (no loop calls), performing: authoritative read via `get_authoritative_session(session_id)` (fall back to the caller's `session.extra_context` only if the fresh read is `None`) → pending-check (early-return if falsy) → **transport gate: if `transport == "email"`, early-return** (email stays on the retained async helper) → SETNX dedup (`self_draft_fallback_sent:{session_id}`, `nx=True, ex=3600`; early-return if not acquired) → narration/canned-notice substitution → build the **exact telegram payload** (`chat_id`/`reply_to`/`text`/`session_id`/`timestamp` per the Technical Approach recipe; `reply_to` MANDATORY) → `r.rpush(queue_key, json.dumps(payload))` then `r.expire(queue_key, OUTBOX_TTL)` (the `r.expire` call is MANDATORY). Factor the narration/canned substitution into a small shared sync helper that the retained async helper also calls (no duplication). Wrap the whole body in `try/except`, log at WARNING, never raise. (NOTE: the sync flush deliberately bypasses the redundancy filter + RTR — DECISION, see Technical Approach / Risk 4.)
- Invoke `flush_deferred_self_draft_sync(session)` (lazy import to avoid cycle) from `finalize_session` (grep `def finalize_session`; HEAD hint `:221`) exactly once, placed **before the idempotency early-return** (grep `if current_status == status`; HEAD hint `:337`) so a re-finalize of an already-terminal session still flushes. The helper does its own authoritative read of `deferred_self_draft_pending`, so the chokepoint does not gate on the caller's possibly-stale `extra_context`.
- Exception-isolate the invocation: a flush failure must not prevent the status write.
- Confirm each of the three `session_health.py` `_deliver_deferred_self_draft_fallback(entry)` call sites (grep the symbol; HEAD hints `:1736/:1759/:1787`) calls `finalize_session` immediately after; **RETAIN these calls and the `async def _deliver_deferred_self_draft_fallback` (HEAD hint `:1338`) for email coverage — do NOT delete.** The SETNX dedup ensures telegram is not double-sent.
- (Docstrings for the new helper + chokepoint comment are covered by the Documentation → Inline Documentation section — do not re-author here.)

### 2. Resolve Q2 — continuation cleanup (CONDITIONAL)
- **Task ID**: build-continuation
- **Depends On**: build-chokepoint (Q2 is only meaningful once the flush is authoritative)
- **Condition**: Execute only if the core chokepoint fix (task 1 + task 3) lands within the Small appetite. If it does not, split this task to a follow-on slug `deferred_self_draft_continuation_cleanup` and record the deferral in No-Gos — do NOT block the core delivery fix on it.
- **Validates**: tests/unit/test_steering.py
- **Assigned To**: continuation-builder
- **Agent Type**: builder
- **Parallel**: false
- **Anchor by symbol — grep `leftover` before editing; HEAD-of-writing region hint is `session_executor.py:1893-1944`.**
- Read the continuation re-enqueue region (grep `leftover`; HEAD hint `:1893-1944`); confirm whether the `drafter-fallback` self-draft steering is in the re-enqueued "leftover".
- Filter the `drafter-fallback` sender out of the continuation re-enqueue.
- **Recompute `leftover` after filtering and guard the empty case.** After dropping `drafter-fallback` messages, `leftover` may be empty. The current code indexes `leftover[0].get("sender", ...)` (HEAD hint `:1921`) and gates the re-enqueue on `if leftover:` (HEAD hint `:1898`). The filter MUST recompute the filtered list and short-circuit (skip the re-enqueue entirely) when it is empty — there must be no `leftover[0]` access on an empty list. If the only unconsumed message was the self-draft, no continuation is enqueued at all (correct — the chokepoint flush already delivered it).
- Ensure no genuine (non-self-draft) unconsumed steering is lost by the change.
- **Regression test (required if Q2 ships):** add a case to `tests/unit/test_steering.py` asserting (1) a leftover list containing only `drafter-fallback` produces zero continuation re-enqueues and no IndexError, and (2) a mixed list re-enqueues only the non-`drafter-fallback` messages. If Q2 splits to a follow-on slug, this test moves there.

### 3. Regression + failure-path tests
- **Task ID**: build-tests
- **Depends On**: build-chokepoint
- **Validates**: tests/unit/test_deferred_self_draft_completed.py
- **Assigned To**: regression-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Deferral → clean `completed` → assert exactly one outbox write whose **payload body equals the original non-empty `deferred_self_draft_text` verbatim** (assert content, not just that an rpush occurred).
- Both completion entry points: exercise the `completed` path through **both** `complete_transcript` and `_complete_agent_session` (including both finalizing the same `session_id`) → assert the original reply body is delivered exactly **one** time total against the single SETNX.
- No-double-send: completion-flush + later `failed` recovery on same `session_id` → exactly one write.
- Exception isolation: flush stubbed to raise → status still set terminal.
- Empty `deferred_self_draft_text` but pending True → canned notice sent.
- Normal `completed` with no deferral → zero outbox writes.
- Re-finalize idempotency: finalize an already-`completed` session a second time → the flush still runs (above the `:337` early-return) and SETNX still dedups → exactly one write across both finalizes.

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-chokepoint, build-continuation, build-tests
- **Assigned To**: delivery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full unit suite; confirm existing `failed`/`abandoned` fallback tests stay green.
- grep-confirm: the three `_deliver_deferred_self_draft_fallback` calls are RETAINED (email coverage), exactly one `flush_deferred_self_draft_sync` chokepoint invocation, and the sync helper has no event-loop calls.
- Verify dedup and exception-isolation criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_deferred_self_draft_completed.py tests/unit/test_session_health_tool_timeout.py tests/unit/test_steering.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Async helper RETAINED (email) | `grep -c "_deliver_deferred_self_draft_fallback" agent/session_health.py` | match count >= 4 (1 def + 3 call sites) |
| Sync helper defined once | `grep -c "def flush_deferred_self_draft_sync" agent/session_health.py` | output contains 1 |
| Telegram payload includes reply_to | `awk '/def flush_deferred_self_draft_sync/{f=1} f&&/^def /&&!/flush_deferred_self_draft_sync/{f=0} f' agent/session_health.py \| grep -c "reply_to"` | output >= 1 |
| Flush calls r.expire (OUTBOX_TTL) | `awk '/def flush_deferred_self_draft_sync/{f=1} f&&/^def /&&!/flush_deferred_self_draft_sync/{f=0} f' agent/session_health.py \| grep -cE "\.expire\("` | output >= 1 |
| Chokepoint invokes the sync flush | `grep -c "flush_deferred_self_draft_sync" models/session_lifecycle.py` | output > 0 |
| Sync helper has no loop calls | `awk '/def flush_deferred_self_draft_sync/{f=1} f&&/^def /&&!/flush_deferred_self_draft_sync/{f=0} f' agent/session_health.py | grep -nE "await \|create_task\|run_until_complete"` | no matches (the helper body contains no event-loop calls) |

---

## Resolved Questions

All open questions from the initial draft were resolved during plan revision (critique pass, 2026-06-25). They are recorded here as durable decisions, not open items.

1. **Q2 disposition (continuation) — RESOLVED: narrow filter.** Filter the `drafter-fallback` self-draft steering out of the continuation re-enqueue (targeted, lower-risk), rather than a broad "drop any continuation whose parent is terminal" guard. The general guard is tracked separately under No-Gos `[SEPARATE-SLUG #1794]`. Q2 itself is CONDITIONAL — see Appetite / No-Gos.
2. **Completion gating (issue Q4) — RESOLVED: flush-then-complete.** A deferred-self-draft session is allowed to reach `completed`; the chokepoint flushes the held text at `finalize_session` rather than blocking completion until the redraft resolves. Flush-at-finalize is the intended invariant, not a hard gate.
3. **Sync/async dispatch (the BLOCKER) — RESOLVED: fully synchronous flush.** `finalize_session` is a plain sync `def` with no event-loop dispatch, and the `completed` path reaches it with no ambient running loop. The flush is therefore a fully synchronous helper (`flush_deferred_self_draft_sync`) ending in a direct outbox `r.rpush(...)` — NO `await`/`create_task`/`run_until_complete`. There is no fire-and-forget task and no moving the chokepoint up to an async caller. See Technical Approach, Rabbit Holes, and Risk 1.
4. **Flush insertion point + authoritative read (BLOCKER 2) — RESOLVED.** (a) The flush is gated **above** the idempotency early-return at `models/session_lifecycle.py:337` (`if current_status == status: return`), not below it — otherwise a re-finalize of an already-`completed` session (e.g. `complete_transcript` then `_complete_agent_session`) would short-circuit before flushing. The SETNX, not the early-return, is the exactly-once guard. (b) The helper reads `deferred_self_draft_pending`/`deferred_self_draft_text` from a fresh authoritative session via `get_authoritative_session(session_id)` (the existing CAS `fresh` object at `:365` is discarded and never re-reads `extra_context`), falling back to the caller's `session.extra_context` only on a `None` fresh read. See Technical Approach, Race 2, and Race 3.
5. **Payload construction + transport scope (BLOCKER 2, 2nd-round) — RESOLVED: telegram-only sync flush, async helper retained for email.** The async helper delegates to `send_cb` and builds no payload itself, so the sync flush constructs the telegram payload explicitly per the verified recipe (`chat_id`/`reply_to`/`text`/`session_id`/`timestamp` + `r.expire(queue_key, OUTBOX_TTL)`; `reply_to` and `r.expire` are MANDATORY). Email-transport replication is out of appetite, so the async `_deliver_deferred_self_draft_fallback` is **retained** (NOT deleted) for email coverage on `failed`/`abandoned`; the email completed-path is a documented known-gap (No-Gos). See Technical Approach payload recipe + Email-transport scope note.
6. **Redundancy filter / RTR bypass (CONCERN 3, 2nd-round) — RESOLVED: deliberate bypass.** The sync flush intentionally bypasses the redundancy filter and RTR (`output_handler.py:476` sequencing): the held text was already drafter-validated at defer time, a terminal flush has no live SDLC session for RTR, and redundancy suppression is a nice-to-have not a correctness guard. Bounded, accepted trade-off — see Rabbit Holes and Risk 4.
