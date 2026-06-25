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
   - On `failed`/`abandoned` via the health monitor: `await _deliver_deferred_self_draft_fallback(entry)` is called *before* `finalize_session`. ✅ This call is **retained** — but the helper is now **EMAIL-ONLY**: a one-line gate is added right after it resolves `transport = extra_ctx.get("transport")` (`session_health.py:1411` — anchor by symbol) — `if transport in (None, "telegram"): return` — so the async helper early-returns for telegram and ONLY ever delivers for email. Telegram on every terminal path (`completed`/`failed`/`abandoned`) is owned exclusively by the sync chokepoint flush. The two paths use distinct SETNX keys (sync telegram → `self_draft_completed_flush_sent`; async email → `self_draft_fallback_sent`) and — because the async helper now structurally early-returns for telegram — they target **disjoint transports**, so a double-send is structurally impossible.
   - On `completed` via the normal executor path (`complete_transcript` → `finalize_session`, both sync, no running loop): **no fallback runs today** — held text is lost. ❌ (the bug). After this change the sync chokepoint flush covers the **telegram** transport here; email on the `completed` path is a documented known-gap (see Technical Approach BLOCKER 2 scope note).
5. **Output (target state)**: the **synchronous** flush (TELEGRAM transport) fires once at the chokepoint on every **legitimate first-time** terminal transition, placed **after the idempotency early-return** (`session_lifecycle.py:337` — anchor by symbol) **AND after the `reject_from_terminal` guard** (`StatusConflictError` raise at `:347` — anchor by symbol), but **before the status `save()`** (around the CAS re-read region) so it runs only on a real running→terminal transition and never on a rejected illegal terminal→different-terminal re-transition; it reads the deferral flag from a fresh authoritative session (`get_authoritative_session`), applies the narration gate, and `rpush`es the held text directly to the telegram outbox (`telegram:outbox:{session_id}`) → bridge delivers to Telegram. The real-world delivery case (`running`→`completed`/`failed`/`abandoned`) has `current_status` non-terminal, so it sails past both `:337` and `:347` and reaches the flush. A redundant re-finalize of an already-`completed` session correctly hits the `:337` idempotency return and does NOT re-flush — which is right, because the first (genuine) finalize already flushed. For `transport == "email"` the sync flush early-returns and the async helper carries email coverage on `failed`/`abandoned`. No async send-callback is awaited at the chokepoint — it is sync and has no running loop on the `completed` path. **Dedup is PER-PATH-PER-TRANSPORT, not a single shared key:** the telegram sync flush dedups on SETNX `self_draft_completed_flush_sent:{session_id}` (1h); the async email helper dedups on its own `self_draft_fallback_sent:{session_id}` (1h). The two keys are distinct so a telegram completed-flush can never acquire the email helper's lock (and silently drop an email) or vice versa — and since the keys cover **disjoint transports** (telegram exclusively the sync flush's domain, email exclusively the async helper's), exactly-once holds within each transport and cross-transport double-send is structurally impossible. The telegram completed-flush key guarantees exactly-once across the first genuine terminal finalize and any later telegram recovery that *itself* reaches a genuine terminal transition through the chokepoint; a redundant re-finalize of an already-terminal session short-circuits at the `:337` idempotency return (the first finalize already flushed), so the SETNX is a belt-and-suspenders guard for the recovery-after-completion case, not the double-finalize case.

## Architectural Impact

- **New dependencies**: none. The flush mechanism already exists; this adds a new telegram sync flush at the chokepoint and retains the existing async helper for email.
- **Interface changes**: `finalize_session()` gains the responsibility of flushing a pending deferral for the **telegram** transport. The flush is implemented as a **new fully-synchronous helper** (`flush_deferred_self_draft_sync`) that writes directly to the Redis telegram outbox via `rpush` — see Technical Approach. The pre-existing `async def _deliver_deferred_self_draft_fallback` is **retained but EDITED to be email-only**: a one-line transport gate (`if transport in (None, "telegram"): return`) is added right after it resolves `transport` so it never delivers telegram (replicating the email outbox payload synchronously is out of appetite — see BLOCKER 2 scope note in Technical Approach). The new sync helper and `finalize_session` are both sync, so there is no sync/async boundary to bridge at the chokepoint.
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

**Q2 continuation cleanup is SPLIT OUT to a follow-on slug by default.** The continuation re-enqueue lives in `session_executor.py:1893-1944` (anchor by symbol) — independent code from the lifecycle chokepoint. Because the chokepoint flush guarantees delivery, the self-draft continuation is **cosmetic** (no longer a delivery path), so Q2 is NOT required for the bug fix and does NOT ship in this Small slug. Disposition: **Q2 splits to a follow-on slug `deferred_self_draft_continuation_cleanup`** (a tracked follow-up referencing this plan); it is removed from the in-scope build tasks here. The core fix (sync chokepoint flush + email-only async gate) does not depend on it.

The mechanism already exists; the core is a new telegram sync chokepoint flush (the async helper stays for email) with one regression test. The risk surface is the no-double-send guarantee — reviewable in a focused pass.

## Prerequisites

No prerequisites — this work has no external dependencies (no new secrets, services, or config).

## Solution

### Key Elements

- **Centralized telegram flush at the chokepoint**: `finalize_session()` (`models/session_lifecycle.py:221` — anchor by symbol) becomes the single place that flushes a pending deferred self-draft **for the telegram transport** on **any** terminal status (`completed`, `failed`, `abandoned`, and by construction `killed`/`cancelled`). Email coverage stays on the retained async helper (`failed`/`abandoned` only).
- **Sync helper for telegram transport; async helper edited to be email-only**: a new fully-synchronous `flush_deferred_self_draft_sync(session)` is invoked once at the chokepoint and handles the **TELEGRAM** transport (the production incident and all acceptance criteria are telegram). For `transport == "email"` the sync flush early-returns; the existing async `_deliver_deferred_self_draft_fallback` (`session_health.py:1338` — anchor by symbol) remains wired on the three `failed`/`abandoned` call sites (`:1736/:1759/:1787`) but is **EDITED to early-return for telegram** (`if transport in (None, "telegram"): return`, added right after it resolves `transport = extra_ctx.get("transport")` at `:1411`) so it ONLY delivers for email. The async helper is therefore **NOT deleted** — it is the email-only fallback. See Technical Approach for the email known-gap on the `completed` path.
- **Q2 — continuation cleanup (SPLIT OUT — follow-on slug)**: the re-enqueued self-draft "continuation" (`session_executor.py:1893-1944` — anchor by symbol) is the unreliable path that died in 0.2s. With the terminal-path flush in place, the self-draft text is guaranteed delivered at completion, so the continuation re-enqueue for the *self-draft* case is redundant (cosmetic, not a delivery loss). This cleanup is **NOT in scope for this slug** — it splits to a tracked follow-on slug `deferred_self_draft_continuation_cleanup` referencing this plan (see Appetite / No-Gos). The core fix does not depend on it.
- **Per-path-per-transport dedup (two distinct keys)**: the telegram sync completed-flush dedups on a **new** SETNX `self_draft_completed_flush_sent:{session_id}` (1h); the async email helper keeps its **existing** SETNX `self_draft_fallback_sent:{session_id}` (1h) untouched. The keys are distinct on purpose — a shared key would couple the two transports and could let a telegram completed-flush acquire the lock the async email helper later needs, silently dropping an email. Within the telegram completed-flush path the new key is the exactly-once guarantee: the first telegram finalize-caller (completion or a later telegram recovery routing through the chokepoint) wins the SETNX; the second is a no-op. Because the two keys never target the same transport, there is no cross-path double-send.

### Flow

Agent reply flagged → delivery deferred + `extra_context` persisted → session reaches **any** terminal transition → `finalize_session()` → (flag set?) → `flush_deferred_self_draft_sync()` rpushes held text to the outbox once (synchronously) → bridge delivers to Telegram.

### Technical Approach

**Decided sync/async shape (closes Open Question 3 — the BLOCKER).** The earlier draft told the build to "mirror how `finalize_session`'s existing async side effects are dispatched." That instruction was wrong: `finalize_session()` (`models/session_lifecycle.py:221`) is a plain **sync `def`** with NO `await`, NO `asyncio.create_task`, and NO event-loop dispatch anywhere — every side effect it runs (telemetry, auto-tag, checkpoint, parent finalization, save) is synchronous. The `completed` path reaches it via `complete_transcript()` (`bridge/session_transcript.py:252`, also a sync `def`) with **no ambient running event loop**, so a naive `asyncio.create_task(...)` at the chokepoint would raise `RuntimeError: no running event loop`. There is no async dispatch pattern to mirror — so the build does **not** reuse `_deliver_deferred_self_draft_fallback`'s async `await send_cb(...)` shape at the chokepoint.

Instead, **make the flush synchronous up to the outbox `rpush`**:

- The actual telegram delivery commit is already synchronous. The async `send_cb` chain (`TelegramRelayOutputHandler.send`) does redundancy/RTR/narration processing, but the terminal act is a sync `r.rpush(f"telegram:outbox:{session_id}", json.dumps(payload))` (`agent/output_handler.py:705-720` — anchor by symbol). The chokepoint flush enqueues the held text **directly to the Redis telegram outbox synchronously**, bypassing the async send-callback entirely (see DECISION on redundancy-filter/RTR bypass below).
- **CONCERN 3 — DECISION (not an open question): the sync flush intentionally bypasses the redundancy filter and RTR** that `send_cb` / `TelegramRelayOutputHandler.send` apply (the drafter → redundancy → RTR → outbox sequencing at `output_handler.py:476` and below — anchor by symbol). Justification: (a) the held `deferred_self_draft_text` was **already drafter-validated at defer time** — the drafter ran before deferral, so re-running it adds nothing; (b) at a terminal flush there is **no live SDLC session** for RTR to bypass-to, so RTR is a no-op there; (c) redundancy suppression is a **nice-to-have, not a correctness guard** — sending one possibly-redundant terminal reply is strictly better than the current silent loss. This is a deliberate, bounded trade-off — see Rabbit Holes and Risk 4. It must not be re-flagged as an open question.
- Add a sync helper — `flush_deferred_self_draft_sync(session)` in `agent/session_health.py` (next to the retained async helper) — that performs, all synchronously:
  1. Read `extra_context["deferred_self_draft_pending"]`; early-return if falsy.
  2. **Transport gate — TELEGRAM only — resolved from `extra_context`, NOT a top-level field.** `AgentSession` has **no** top-level `transport` field (confirmed at HEAD: the async helper resolves it as `transport = extra_ctx.get("transport")`, `session_health.py:1411`). The sync helper MUST resolve transport from the **same fresh authoritative session it reads the deferral flag from** (the `get_authoritative_session(session_id)` object recovered in step 1), i.e. `transport = (fresh_session.extra_context or {}).get("transport")`, mirroring the async helper. A builder that reads `session.transport` / `getattr(session, "transport", None)` gets `None`, which makes the email gate **dead code** — an email session would then receive an undeliverable telegram payload AND double-coverage. The telegram path runs **only** when `transport` is `None` or `"telegram"`; if `transport == "email"`, **early-return** (the retained async helper covers email on `failed`/`abandoned`; the `completed`-path email case is a documented known-gap — see scope note below).
  2b. SETNX the **completed-path dedup key** `self_draft_completed_flush_sent:{session_id}` (`nx=True, ex=3600`); early-return if not acquired (preserves exactly-once for the telegram completed-flush path). This is a **distinct key** from the async helper's `self_draft_fallback_sent:{session_id}` — see CONCERN (shared-key decoupling) below.
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
- **Insertion point — gate the flush AFTER the idempotency early-return AND the `reject_from_terminal` guard, before the status save (closes BLOCKER 2a / CONCERN 2).** (Anchor by symbol — line numbers are HEAD-of-writing hints; grep `if current_status == status`, `reject_from_terminal`, and `def finalize_session`.) `finalize_session()` (`models/session_lifecycle.py:221`) has, in order: (1) an idempotency early-return — `current_status = getattr(session, "status", None); if current_status == status: ... return` (HEAD-verified at **`:337`**); (2) a `reject_from_terminal` guard that `raise StatusConflictError(...)` for an illegal terminal→different-terminal re-transition (HEAD-verified at **`:347`**); then (3) the CAS re-read and side effects. The flush MUST run **after both `:337` and `:347`** (i.e. only on a LEGITIMATE first-time terminal transition where `current_status` is non-terminal), in the CAS re-read region but **before the status `save()`** so it still precedes the terminal write. **Why not above `:337`:** placing it above the idempotency return would also place it above the `:347` reject raise, so a *rejected* illegal terminal→different-terminal re-transition would deliver the held text and *then* raise — an incorrect double/spurious delivery. **Why this is still correct for the real case:** the only path that actually carries a pending deferral to a terminal transition is `running`→`completed`/`failed`/`abandoned`, where `current_status` is non-terminal — it passes the `:337` check (not yet terminal) and the `:347` guard (not currently terminal) and reaches the flush. A redundant re-finalize of an already-`completed` session (e.g. `complete_transcript` then `_complete_agent_session`) hits the `:337` idempotency return and is short-circuited — which is **correct**, because the first genuine finalize already flushed. The SETNX dedup remains the exactly-once guard for the recovery-after-completion case (a later genuine terminal transition that routes through the chokepoint), not for the double-finalize case (which `:337` already handles).
- **Authoritative read — read the flag from a FRESH session, not the caller's object (closes BLOCKER 2b).** The caller's in-memory `session` object may be stale: its `extra_context` can still read `deferred_self_draft_pending=False` if the defer-time `save(update_fields=["extra_context"])` happened on a *different* object instance than the one passed to `finalize_session`. Note the existing CAS block (`:365`) reads `fresh = get_authoritative_session(session_id)` purely for the status comparison and then **discards `fresh`** — it does NOT re-read `extra_context`, and it lives *below* the idempotency early-return anyway. So the flush must do its own authoritative read: call `get_authoritative_session(session_id)` (defined in the same module, `models/session_lifecycle.py:100` — no import needed) inside `flush_deferred_self_draft_sync`, read `deferred_self_draft_pending` / `deferred_self_draft_text` from that fresh object, and fall back to the caller's `session.extra_context` only if the fresh read returns `None`. This is the authoritative source for the deferral flag.
- `finalize_session()` calls `flush_deferred_self_draft_sync(session)` exactly once, **after** the idempotency early-return at `:337` and the `reject_from_terminal` guard at `:347`, in the CAS re-read region but **before** the status `save()`. Because it is fully synchronous, it works identically whether the caller has a running loop or not — no loop handling, no `create_task`, no `run_until_complete`.
- **The existing `async def _deliver_deferred_self_draft_fallback` (`session_health.py:1338` — anchor by symbol) is RETAINED but EDITED to be EMAIL-ONLY** (closes BLOCKER — telegram double-send). It remains wired on its three `failed`/`abandoned` call sites (`:1736/:1759/:1787` — anchor by symbol) to carry **email-transport** deferred self-drafts, because the email outbox payload (`to`/`subject`/`in_reply_to`/`from_addr`) is derived inside `TelegramRelayOutputHandler._send_via_email_outbox` (`output_handler.py:184-291` — anchor by symbol) from `extra_context` + project config, and replicating that synchronously is **out of appetite**. **The required edit (Build Task 1 explicitly permits editing the async helper):** right after the helper resolves `transport = extra_ctx.get("transport")` (`session_health.py:1411` — HEAD-verified; it then unconditionally `await send_cb(...)` at `:1426` for ANY transport today), add the gate `if transport in (None, "telegram"): return`. With this gate the async helper structurally cannot deliver telegram — it ONLY ever sends for email. **Why this is the clean design and closes the double-send:** the sync chokepoint flush fires on ALL terminal paths (`completed`/`failed`/`abandoned`) for telegram; without the gate, a TELEGRAM `failed`/`abandoned` session would be delivered TWICE (once by the sync flush, once by the unconditional async helper), and the two DISTINCT dedup keys (`self_draft_completed_flush_sent` vs `self_draft_fallback_sent`) deliberately cannot cross-dedup it. The gate makes the sync flush the sole owner of telegram on every terminal path and the async helper the sole owner of email — the two paths target **disjoint transports**, so a double-send is structurally impossible regardless of the keys. The new sync flush and the gated async helper use **DISTINCT** SETNX keys (`self_draft_completed_flush_sent:{session_id}` for the sync telegram flush; `self_draft_fallback_sent:{session_id}` for the async email helper — left untouched): the keys are per-transport-disjoint, so exactly-once holds within each transport and cross-transport double-send cannot occur. **DEDUP KEY DECISION:** the two keys are KEPT distinct as-is (no merge into a single shared key) — a shared key would re-introduce the earlier lock-stealing concern (one path acquiring the lock the other needs); since the keys now cover disjoint transports, distinctness is free and the simplest safe choice. The narration/canned-notice substitution logic is **inlined** directly in `flush_deferred_self_draft_sync` (it is ~6 lines — narration gate + empty-text canned-notice substitution); the async helper's copy is left untouched (NO shared-helper extraction — that refactor is gold-plating for a Small fix and would touch the async email path needlessly).

**Email-transport scope / known-gap (BLOCKER 2).** The sync completed-path flush handles **TELEGRAM transport only**. If `transport == "email"`, the sync flush early-returns; the retained async helper remains wired on `failed`/`abandoned` for email. **Known gap:** an email-transport deferred self-draft that reaches `completed` (not `failed`/`abandoned`) without redrafting is NOT flushed by this slug — it falls through. This is an explicit, accepted follow-on gap because the production incident and **all** acceptance criteria are telegram. It is recorded in No-Gos. (Preferred-simpler option per BLOCKER 2: keep the sync flush telegram-only, keep the async helper for email; do not attempt synchronous email-payload replication.)

**Delivery ordering on `failed`/`abandoned` (resolves CONCERN 2).** The three health-monitor branches `await _deliver_deferred_self_draft_fallback(entry)` *before* their `finalize_session(entry, ...)` call, so the email flush is ordered before the terminal save. With the new telegram chokepoint flush inside `finalize_session` (after the `:337`/`:347` guards, before the status `save()`), both run before the terminal write commits — ordering is preserved on every path. The async helper (email, after the `if transport in (None, "telegram"): return` gate) and the chokepoint flush (telegram) operate on **disjoint transports and different SETNX keys**, so they cannot double-send the same reply: an email session early-returns from the telegram chokepoint flush at the transport gate, and a telegram session early-returns from the async helper at its new gate. Build must confirm each of the three branches routes through `finalize_session` immediately after the retained explicit call so no `failed`/`abandoned` telegram flush is dropped (the chokepoint owns telegram there).

- **Idempotency is already correct.** The sync helper early-returns if `deferred_self_draft_pending` is falsy and SETNX-guards delivery. No new dedup logic; the test must *prove* no double-send when both a completion and a later recovery observe the flag.
- **Keep the three explicit `session_health.py` calls AND the (now email-only) async helper** — they are the email-transport fallback on `failed`/`abandoned` (see BLOCKER reconciliation above). Confirm the new telegram chokepoint flush covers the `failed`/`abandoned` telegram case (those branches call `finalize_session` immediately after the retained explicit call — verify each does), so telegram is delivered exactly once by the sync flush and the email-only async helper never touches telegram (its new `if transport in (None, "telegram"): return` gate). Email is never dropped (retained helper, distinct key).
- **Q2 continuation cleanup — SPLIT OUT to a follow-on slug, NOT in scope here.** The re-enqueued self-draft "continuation" (`session_executor.py:1893-1944` — anchor by symbol; grep `leftover`) no-ops in 0.2s but, because the chokepoint flush guarantees telegram delivery, is **cosmetic** — not a delivery loss. The plan therefore does NOT ship the continuation cleanup in this Small slug; it is tracked as the follow-on slug `deferred_self_draft_continuation_cleanup` referencing this plan (see No-Gos). The core fix (sync chokepoint flush + email-only async gate) does NOT depend on it. (Implementation note carried to the follow-on slug, not done here: filter the `drafter-fallback` sender out of the continuation re-enqueue, compute a `filtered` list, guard `if not filtered: skip` before any `filtered[0]` access, replace `leftover[0]` with `filtered[0]`, and add a `tests/unit/test_steering.py` regression for the empty-after-filter and mixed-list cases.)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `flush_deferred_self_draft_sync` wraps its body in `try/except` and logs at WARNING on failure (`session_health.py`) — the regression test must assert that an outbox/Redis failure is logged and swallowed (never raises out of `finalize_session`, which must not be made fallible by this change).
- [ ] The chokepoint invocation must itself be exception-isolated: a flush failure must NOT prevent `finalize_session` from completing the status write. Test: stub the flush to raise, assert the session still reaches its terminal status.

### Empty/Invalid Input Handling
- [ ] `deferred_self_draft_text` empty/whitespace → helper already substitutes "I couldn't finish responding to that — please try again." Add a test asserting the canned notice is sent when `_text` is empty but `_pending` is True.
- [ ] `deferred_self_draft_pending` absent/falsy → helper early-returns; chokepoint must not send anything for ordinary completions. Test: a normal `completed` session with no deferral triggers zero outbox writes.
- [ ] Agent-output processing: the continuation no-op (empty turn input, `session_executor.py:1548`) is now harmless because the chokepoint flush already delivered the held text before the continuation runs — the continuation is cosmetic. Removing the self-draft continuation entirely is the SPLIT-OUT follow-on slug `deferred_self_draft_continuation_cleanup` (out of scope here); this slug does not depend on it.

### Error State Rendering
- [ ] User-visible: the flushed reply (or canned notice) must reach the outbox. Test asserts an `rpush` to the project outbox with the held text on the `completed` path.
- [ ] Verify the narration gate (`is_narration_only`) substitution still applies on the `completed` path (parity with the recovery path).

## Test Impact

- [ ] `tests/unit/test_session_health_tool_timeout.py` — VERIFY (likely no change): this file exercises the existing `failed`/`abandoned` fallback (the async helper), which is **retained** for email coverage. Its assertions on `failed`/`abandoned` delivery should stay green. Confirm they assert *observable delivery* (outbox write / SETNX), not brittle call-site line numbers; if any test pins a specific `session_health.py` line, REPLACE it to assert delivery-on-finalize instead. The retained async path means these tests are not regressed by this change.
- [ ] `tests/unit/test_output_handler.py` — UPDATE if it asserts defer-time behavior; confirm the persisted `extra_context` keys are unchanged (they are). Likely no change.
- [ ] `tests/unit/test_steering.py` — NO CHANGE in this slug: the Q2 continuation cleanup is split out to the follow-on slug `deferred_self_draft_continuation_cleanup`, which carries the `test_steering.py` regression. No `test_steering.py` edits land here.
- [ ] New: `tests/unit/test_deferred_self_draft_completed.py` (create) — the primary regression: deferral → clean `completed` → the **original reply body** delivered exactly once (assert payload content, exercising both `complete_transcript` and `_complete_agent_session` against the single completed-flush SETNX `self_draft_completed_flush_sent:{session_id}`); plus the no-double-send case (telegram completion-flush + later telegram failed-recovery → exactly one telegram outbox write via the new key), the telegram `failed`/`abandoned` exactly-once case (sync flush delivers; async helper early-returns at its telegram gate), the re-finalize-idempotency case (second `finalize_session` of an already-`completed` session short-circuits at the `:337` idempotency return → still exactly one total write from the first genuine finalize), exception isolation, empty-text canned notice, the no-deferral zero-write case, and the email-transport case (`extra_context['transport']=='email'` reaching `completed` → ZERO `telegram:outbox:{session_id}` writes).

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

### Risk 2: Double-send when a telegram completion and a later telegram recovery both observe the flag
**Impact:** Human receives the telegram reply twice.
**Mitigation:** The new SETNX `self_draft_completed_flush_sent:{session_id}` (1h) guarantees exactly-once for the telegram completed-flush path across all telegram finalize-callers that route through the chokepoint. Regression test simulates a telegram `completed` flush followed by a later telegram `failed`-recovery (both routing through `finalize_session`) on the same `session_id` and asserts **exactly one** telegram outbox write — proving the new key dedups telegram-on-telegram. (The async email helper uses its own distinct key `self_draft_fallback_sent:{session_id}`; the two keys never collide because the sync flush early-returns for email at the transport gate.)

### Risk 3: Telegram chokepoint flush double-sends with the async helper on `failed`/`abandoned`
**Impact:** On a `failed`/`abandoned` telegram session, both the async helper and the new telegram chokepoint flush could fire — human gets the reply twice. This is the BLOCKER this revision closes.
**Mitigation (structural, not key-based):** The two paths are decoupled by **transport via an explicit code gate**, so they target disjoint transports and cannot double-send. The async helper is EDITED to early-return for telegram (`if transport in (None, "telegram"): return`, added right after it resolves `transport = extra_ctx.get("transport")` at `:1411`); it therefore delivers ONLY for email. The sync chokepoint flush early-returns for `transport == "email"` at its own transport gate; it delivers ONLY for telegram. For a `failed`/`abandoned` telegram session the async helper now returns immediately (never reaches `await send_cb(...)`), and the telegram delivery flows solely through the chokepoint flush (which owns the `self_draft_completed_flush_sent:{session_id}` key) — exactly once. The two DISTINCT keys never cross-dedup, but they don't need to, because the gates already guarantee disjoint transports. The async helper is **retained** for email and is NOT deleted, so there is no email recovery-path regression. A unit test asserts a TELEGRAM `failed`/`abandoned` session delivers the held reply EXACTLY ONCE (via the sync flush; the async helper early-returns on telegram). Keep the existing `failed`/`abandoned` email tests green as proof. Verify each of the three branches still routes through `finalize_session` immediately after its retained explicit call.

### Risk 4: Sync flush bypasses redundancy filter + RTR (accepted, bounded trade-off — DECISION)
**Impact:** A flushed terminal reply is not redundancy-suppressed and does not pass through RTR; in a pathological case the human could receive a reply that a live redundancy check would have suppressed.
**Mitigation / justification (this is a DECISION, not an open question):** (a) the held `deferred_self_draft_text` was already drafter-validated at defer time — the drafter ran before deferral; (b) at a terminal flush there is no live SDLC session for RTR to bypass-to, so RTR is a no-op; (c) redundancy suppression is a nice-to-have, not a correctness guard — one possibly-redundant terminal reply is strictly better than the current silent loss this slug fixes. The trade-off is bounded to terminal flushes only. Do not re-flag as an open question.

## Race Conditions

### Race 1: Completion flush vs. health-monitor recovery flush on the same session
**Location:** `models/session_lifecycle.py:221` (chokepoint) and `agent/session_health.py:1736/1759/1787` (recovery branches — anchor by symbol, line numbers are HEAD-of-writing hints).
**Trigger:** A session completes (flush A at the chokepoint) and, before the 1h dedup window, the health monitor independently observes the same `deferred_self_draft_pending` flag and attempts flush B.
**Data prerequisite:** `extra_context["deferred_self_draft_pending"]` is True and `deferred_self_draft_text` is populated (written at defer time, `output_handler.py:453-456`, before any terminal transition).
**State prerequisite:** The telegram completed-flush SETNX key `self_draft_completed_flush_sent:{session_id}` must be checked-and-set atomically before delivery.
**Mitigation:** Atomic SETNX with `nx=True, ex=3600` on the completed-flush key. First telegram caller wins; second early-returns. Test proves single delivery. (The async email helper's distinct `self_draft_fallback_sent:{session_id}` key is not involved here — the sync flush handles telegram only.)

### Race 2: Caller's in-memory object predates the defer-time save
**Location:** `output_handler.py:446-456` (defer-time save of `deferred_self_draft_pending`/`deferred_self_draft_text` to the **authoritative record**) vs. `session_lifecycle.py:221` (chokepoint read).
**Trigger:** This is NOT a durability race — the defer-time `save(update_fields=["extra_context"])` at `output_handler.py:446-456` writes the deferral flags straight to the authoritative record, so they ARE persisted before finalization. The actual hazard is **object staleness**: the `session` *object* the caller passes into `finalize_session()` may be a different in-memory instance that was loaded **before** the defer-time save, so reading `session.extra_context` directly would observe `deferred_self_draft_pending` as still-False (the flag was written to the record, not to this object). The fresh read matters because finalize must re-read the authoritative record to observe `pending=True`.
**Data prerequisite:** The defer happens *inside* the agent's turn (before the turn returns) and its save commits to the authoritative record; the terminal transition happens *after* the turn returns, but the caller's `session` object may have been instantiated before that save. The record is correct; the in-memory copy may be stale.
**State prerequisite:** The chokepoint must read the **authoritative record** via `get_authoritative_session(session_id)`, not the caller's possibly-pre-save in-memory object. The existing CAS re-read at `session_lifecycle.py:365` (`fresh = get_authoritative_session(session_id)`) is used **only** for the status comparison and then discarded — it does NOT re-read `extra_context`, and it lives below the idempotency early-return at `:337`. So the flush cannot piggyback on it.
**Mitigation:** `flush_deferred_self_draft_sync` performs its **own** authoritative read: it calls `get_authoritative_session(session_id)` (same module, `session_lifecycle.py:100`) and reads `deferred_self_draft_pending` / `deferred_self_draft_text` from that fresh object, falling back to the caller's `session.extra_context` only if the fresh read returns `None`. The caller's possibly-stale `extra_context` is never the sole source of truth. Build verifies the helper reads the fresh object, not just the passed-in `session`. (This precondition is stated in the helper's docstring — see Documentation → Inline Documentation.)

### Race 3: Re-finalize of an already-terminal session and the flush placement
**Location:** `models/session_lifecycle.py:337` (idempotency early-return), `:347` (`reject_from_terminal` guard) vs. the flush invocation.
**Trigger:** `complete_transcript` finalizes a session to `completed`; then `_complete_agent_session` (or a health-monitor recovery) calls `finalize_session(session, "completed", ...)` again on the same `session_id`. The second call hits `if current_status == status: ... return` at `:337` and runs **no** side effects.
**Data prerequisite:** `deferred_self_draft_pending` is True; the first (genuine running→completed) finalize already ran the flush and fired the SETNX.
**State prerequisite:** The flush must run on the genuine first-time terminal transition (`current_status` non-terminal), and a redundant re-finalize must NOT re-flush (the first already delivered).
**Mitigation:** Insert the flush **after** the idempotency early-return at `:337` AND the `reject_from_terminal` raise at `:347`, in the CAS re-read region before the status `save()` (see Technical Approach). This means: (a) the genuine `running`→terminal transition (non-terminal `current_status`) passes both guards and reaches the flush — delivery happens once; (b) a redundant re-finalize of an already-`completed` session short-circuits at `:337` and does NOT re-flush — correct, because the first finalize already delivered; (c) a rejected illegal terminal→different-terminal re-transition raises at `:347` and never reaches the flush — so no spurious delivery on a rejected transition. Placing the flush ABOVE the guards would have caused (c) to deliver-then-raise. The SETNX `self_draft_completed_flush_sent:{session_id}` remains the exactly-once guard for the recovery-after-completion case (a *later genuine* terminal transition that reaches the chokepoint), not for the double-finalize case (which `:337` handles). The regression test exercises BOTH `complete_transcript` and `_complete_agent_session` (including the double-finalize) and asserts exactly-once (see Success Criteria).

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1794] General continuation-lifecycle hardening (guarding *all* re-enqueued continuations against already-terminal parents) is broader than this fix. This plan scopes Q2 to the self-draft case only.
- [SPLIT-OUT FOLLOW-ON SLUG] Q2 (the self-draft continuation re-enqueue cleanup) is SPLIT OUT to the follow-on slug `deferred_self_draft_continuation_cleanup` and is NOT shipped in this slug. Q2 is NOT a delivery-correctness dependency — the chokepoint flush is authoritative for delivery, so the self-draft continuation is only a cosmetic 0.2s no-op, not a lost reply. The follow-on slug carries the `drafter-fallback` filter + `filtered`-list guard + `tests/unit/test_steering.py` regression.
- [KNOWN-GAP — email completed-path, TRACKED FOLLOW-UP ISSUE] The sync completed-path flush handles **telegram transport only**. An email-transport deferred self-draft that reaches `completed` (not `failed`/`abandoned`) without redrafting is NOT flushed by this slug (the async helper covers email only on `failed`/`abandoned`). This is explicitly out-of-scope-by-design because the production incident and all acceptance criteria are telegram; synchronously replicating the email outbox payload (`_send_via_email_outbox`) is out of appetite. **A tracking issue WILL be filed** — title: *"deferred self-draft on email transport is not flushed on the completed path"* — so the gap is tracked rather than silently accepted. Filing that follow-up issue is a closing task of this slug (see Step by Step Tasks → task 4). It is referenced here as the canonical out-of-scope record for this telegram-focused slug.
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
- [ ] The async `_deliver_deferred_self_draft_fallback` is **retained but EDITED to be email-only** for coverage on `failed`/`abandoned` — update its docstring to state it now handles the **email transport** fallback specifically (it early-returns for telegram via `if transport in (None, "telegram"): return`; telegram is covered by the new sync chokepoint flush), and that it keeps its own SETNX dedup key `self_draft_fallback_sent:{session_id}` (distinct from the sync flush's `self_draft_completed_flush_sent:{session_id}`). The new `flush_deferred_self_draft_sync` helper carries a fresh docstring stating it is the **telegram** chokepoint flush covering all terminal statuses (`completed`, `failed`, `abandoned`), that it is fully synchronous (direct telegram-outbox `rpush`, no event loop), that it dedups on its **own** key `self_draft_completed_flush_sent:{session_id}`, that it early-returns for `transport == "email"` (resolved from `extra_context`, not a top-level field), and that it reads the deferral flag from a **fresh authoritative session** (`get_authoritative_session`), not the caller's possibly-stale `extra_context`.
- [ ] Comment the chokepoint invocation in `finalize_session` explaining the deferred-flush invariant, why it is placed **after the idempotency early-return (`:337`) and the `reject_from_terminal` guard (`:347`), before the status save** (so it runs only on a legitimate first-time terminal transition and never on a rejected illegal re-transition), and why it is synchronous (the `completed` path has no running loop).

## Success Criteria

**Behavioral / user-facing (the bug actually fixed):**
- [ ] **The human receives the ORIGINAL reply BODY, exactly once, on the `completed` path (TELEGRAM transport).** A telegram-transport session that defers a reply for self-draft and then reaches `completed` without redrafting produces exactly **one** outbox `rpush` to `telegram:outbox:{session_id}` whose payload body is the **non-empty original `deferred_self_draft_text`** (verbatim, not a placeholder) — or the narration/canned equivalent only when the text is genuinely narration-only/empty. The regression test asserts the **payload body content** (the actual reply text the human reads), not merely that "an rpush happened," and asserts the exact rpush count (1) — proving the human receives the reply they would otherwise have silently lost.
- [ ] **Exactly-once holds across BOTH completion entry points.** The test exercises the `completed` path through **both** `complete_transcript` (`bridge/session_transcript.py:317`) **and** `_complete_agent_session` (`agent/session_completion.py:167`) finalizing the same `session_id` — including the double-finalize where both run — and asserts the original reply body is delivered exactly **one** time total. The first genuine `running`→`completed` finalize passes the `:337`/`:347` guards and flushes; the second (re-finalize of an already-`completed` session) short-circuits at the `:337` idempotency return and does NOT re-flush — so exactly one delivery results. (The SETNX `self_draft_completed_flush_sent:{session_id}` additionally guards the recovery-after-completion case where a later genuine terminal transition routes through the chokepoint.)
- [ ] **No silent loss and no double-send.** Completion-flush followed by a later `failed`/`abandoned` recovery on the same `session_id` yields exactly **one** outbox write total (SETNX dedup preserved) — the human is never messaged twice.
- [ ] A normal `completed` session with no pending deferral produces **zero** flush-originated outbox writes (no spurious sends on the happy path).

**Structural:**
- [ ] The **telegram** flush fires on all terminal paths (`completed`, `failed`, `abandoned`) via the single `finalize_session` chokepoint. The three explicit `session_health.py` call sites AND the `async def _deliver_deferred_self_draft_fallback` are **retained** for email coverage (NOT removed) — verify each branch routes through `finalize_session` immediately after, so telegram is never double-sent and email is never dropped.
- [ ] The async `_deliver_deferred_self_draft_fallback` is EMAIL-ONLY: it contains the gate `if transport in (None, "telegram"): return` right after it resolves `transport = extra_ctx.get("transport")` — verified by inspection. The async helper therefore never reaches `await send_cb(...)` for telegram.
- [ ] **A TELEGRAM `failed`/`abandoned` session delivers the held reply EXACTLY ONCE** — via the sync chokepoint flush; the async helper early-returns at its telegram gate. Proven by a unit test (telegram `failed` or `abandoned` deferral → exactly one `telegram:outbox:{session_id}` write).
- [ ] grep confirms exactly one `flush_deferred_self_draft_sync` invocation exists at the `finalize_session` chokepoint, AND the three `_deliver_deferred_self_draft_fallback(entry)` calls remain in `session_health.py` (retained for email).
- [ ] The new `flush_deferred_self_draft_sync` helper is fully synchronous (no `await`/`create_task`/`run_until_complete`/`get_event_loop`) — verified by grep/inspection — so it cannot raise "no running event loop" on the `completed` path.
- [ ] The flush invocation is placed **after the idempotency early-return at `:337` AND the `reject_from_terminal` guard at `:347`**, in the CAS re-read region before the status `save()` (verified by inspection: the `flush_deferred_self_draft_sync(...)` call follows both the `if current_status == status: ... return` line and the `raise StatusConflictError(...)` reject line, and precedes the status save), so the flush runs only on a legitimate first-time terminal transition and never on a rejected illegal re-transition.
- [ ] The helper reads `deferred_self_draft_pending` from a fresh authoritative session via `get_authoritative_session(session_id)`, falling back to the caller's `extra_context` only on a `None` fresh read — verified by inspection. The caller's possibly-stale object is never the sole source of the flag.
- [ ] The telegram outbox payload includes the `reply_to` key AND the flush calls `r.expire(queue_key, OUTBOX_TTL)` (both MANDATORY) — verified by inspection of the helper against the `output_handler.py` recipe (`chat_id`/`reply_to`/`text`/`session_id`/`timestamp`).
- [ ] For `transport == "email"` (resolved as `(fresh_session.extra_context or {}).get("transport")`, NOT a top-level field) the sync flush early-returns — proven by a unit test asserting an email-transport session (`extra_context['transport']=='email'`) reaching `completed` produces **ZERO** `telegram:outbox:{session_id}` entries. Email coverage on `failed`/`abandoned` stays on the retained async helper. The email completed-path gap is recorded in No-Gos and tracked as a follow-up issue (see No-Gos).

**Q2 (SPLIT OUT — follow-on slug, NOT in scope here):**
- [ ] Q2 (the self-draft continuation re-enqueue cleanup) does NOT ship in this slug — it is split to the follow-on slug `deferred_self_draft_continuation_cleanup` and recorded in No-Gos. This criterion is satisfied by the absence of continuation-cleanup changes in this slug's diff; the cleanup is not a delivery-correctness dependency (the chokepoint flush is authoritative).

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
  - Role: Add the telegram sync flush at the `finalize_session` chokepoint (dedup key `self_draft_completed_flush_sent:{session_id}`); EDIT `_deliver_deferred_self_draft_fallback` to be email-only (add `if transport in (None, "telegram"): return` after it resolves `transport`); RETAIN the async helper + its three `session_health.py` call sites for email coverage (keeping its distinct key `self_draft_fallback_sent:{session_id}` untouched).
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
- Add `flush_deferred_self_draft_sync(session)` to `agent/session_health.py`: fully synchronous (no `await`/`create_task`/`run_until_complete`/`get_event_loop`), implemented as plain sync code (no loop calls), performing: authoritative read via `get_authoritative_session(session_id)` (fall back to the caller's `session.extra_context` only if the fresh read is `None`) → pending-check (early-return if falsy) → **transport gate (read transport from the SAME fresh authoritative session: `transport = (fresh_session.extra_context or {}).get("transport")` — NOT `getattr(session, "transport", None)`, which is always `None` since `AgentSession` has no top-level transport field): if `transport == "email"`, early-return** (email stays on the retained async helper); proceed only when transport is `None`/`"telegram"` → SETNX dedup on the **completed-path key** (`self_draft_completed_flush_sent:{session_id}`, `nx=True, ex=3600`; early-return if not acquired — this is DISTINCT from the async helper's `self_draft_fallback_sent:{session_id}`, which the sync flush must NOT touch) → **inline** narration/canned-notice substitution (~6 lines; do NOT extract a shared helper — leave the async helper's copy untouched) → build the **exact telegram payload** (`chat_id`/`reply_to`/`text`/`session_id`/`timestamp` per the Technical Approach recipe; `reply_to` MANDATORY) → `r.rpush(queue_key, json.dumps(payload))` then `r.expire(queue_key, OUTBOX_TTL)` (the `r.expire` call is MANDATORY). Wrap the whole body in `try/except`, log at WARNING, never raise. (NOTE: the sync flush deliberately bypasses the redundancy filter + RTR — DECISION, see Technical Approach / Risk 4.)
- Invoke `flush_deferred_self_draft_sync(session)` (lazy import to avoid cycle) from `finalize_session` (grep `def finalize_session`; HEAD hint `:221`) exactly once, placed **after the idempotency early-return** (grep `if current_status == status`; HEAD hint `:337`) **AND after the `reject_from_terminal` guard** (grep `reject_from_terminal`; HEAD hint `:347`), in the CAS re-read region but **before the status `save()`** — so it runs only on a legitimate first-time terminal transition (where `current_status` is non-terminal) and never on a rejected illegal terminal→different-terminal re-transition. The helper does its own authoritative read of `deferred_self_draft_pending`, so the chokepoint does not gate on the caller's possibly-stale `extra_context`.
- Exception-isolate the invocation: a flush failure must not prevent the status write.
- **EDIT the async helper to be email-only (this task explicitly permits editing it):** in `async def _deliver_deferred_self_draft_fallback` (grep the symbol; HEAD hint `:1338`), right after it resolves `transport = extra_ctx.get("transport")` (HEAD hint `:1411`) and before its unconditional `await send_cb(...)` (HEAD hint `:1426`), add the gate `if transport in (None, "telegram"): return`. After this edit the async helper delivers ONLY for email — telegram is owned exclusively by the sync chokepoint flush on every terminal path.
- Confirm each of the three `session_health.py` `_deliver_deferred_self_draft_fallback(entry)` call sites (grep the symbol; HEAD hints `:1736/:1759/:1787`) calls `finalize_session` immediately after; **RETAIN these three calls and the (now email-only) `async def _deliver_deferred_self_draft_fallback` for email coverage — do NOT delete.** Because the async helper now structurally early-returns for telegram and the sync flush early-returns for email, the two paths target disjoint transports → telegram is never double-sent.
- (Docstrings for the new helper + chokepoint comment are covered by the Documentation → Inline Documentation section — do not re-author here.)

### 2. Regression + failure-path tests
- **Task ID**: build-tests
- **Depends On**: build-chokepoint
- **Validates**: tests/unit/test_deferred_self_draft_completed.py
- **Assigned To**: regression-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Deferral → clean `completed` → assert exactly one outbox write whose **payload body equals the original non-empty `deferred_self_draft_text` verbatim** (assert content, not just that an rpush occurred).
- Both completion entry points: exercise the `completed` path through **both** `complete_transcript` and `_complete_agent_session` (including both finalizing the same `session_id`) → assert the original reply body is delivered exactly **one** time total against the single completed-flush SETNX `self_draft_completed_flush_sent:{session_id}`.
- No-double-send: telegram completion-flush + later telegram `failed`-recovery on same `session_id` → exactly one telegram outbox write (via the new completed-flush key).
- Email-transport gate: a session with `extra_context['transport']=='email'` reaching `completed` → ZERO `telegram:outbox:{session_id}` writes (the sync flush early-returns at the transport gate).
- Exception isolation: flush stubbed to raise → status still set terminal.
- Empty `deferred_self_draft_text` but pending True → canned notice sent.
- Normal `completed` with no deferral → zero outbox writes.
- Re-finalize idempotency: finalize an already-`completed` session a second time → the second finalize short-circuits at the `:337` idempotency return (the flush is below it) and does NOT re-flush → exactly one write total, from the first genuine finalize.
- Telegram `failed`/`abandoned` exactly-once: a telegram deferral reaching `failed` (or `abandoned`) → exactly one `telegram:outbox:{session_id}` write (the sync chokepoint flush delivers; the email-only async helper early-returns at its telegram gate `if transport in (None, "telegram"): return`).

### 3. Final validation
- **Task ID**: validate-all
- **Depends On**: build-chokepoint, build-tests
- **Assigned To**: delivery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full unit suite; confirm existing `failed`/`abandoned` email fallback tests stay green.
- grep-confirm: the three `_deliver_deferred_self_draft_fallback` calls are RETAINED (email coverage), the async helper contains the email-only gate `if transport in (None, "telegram"): return`, exactly one `flush_deferred_self_draft_sync` chokepoint invocation, and the sync helper has no event-loop calls.
- grep-confirm the two dedup keys are DISTINCT: `self_draft_completed_flush_sent` (sync telegram flush) and `self_draft_fallback_sent` (async email helper) each appear, and the sync helper does NOT reference `self_draft_fallback_sent`.
- Verify dedup and exception-isolation criteria.
- **File the email-transport follow-up issue** (title: *"deferred self-draft on email transport is not flushed on the completed path"*) via `gh issue create --label bug` referencing this slug, so the known-gap recorded in No-Gos is tracked rather than silently accepted.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_deferred_self_draft_completed.py tests/unit/test_session_health_tool_timeout.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Async helper RETAINED (email) | `grep -c "_deliver_deferred_self_draft_fallback" agent/session_health.py` | match count >= 4 (1 def + 3 call sites) |
| Async helper is email-only (telegram gate) | `awk '/async def _deliver_deferred_self_draft_fallback/{f=1} f&&/^async def /&&!/_deliver_deferred_self_draft_fallback/{f=0} f' agent/session_health.py \| grep -cE 'transport in \(None, "telegram"\)'` | output >= 1 |
| Sync helper defined once | `grep -c "def flush_deferred_self_draft_sync" agent/session_health.py` | output contains 1 |
| Telegram payload includes reply_to | `awk '/def flush_deferred_self_draft_sync/{f=1} f&&/^def /&&!/flush_deferred_self_draft_sync/{f=0} f' agent/session_health.py \| grep -c "reply_to"` | output >= 1 |
| Flush calls r.expire (OUTBOX_TTL) | `awk '/def flush_deferred_self_draft_sync/{f=1} f&&/^def /&&!/flush_deferred_self_draft_sync/{f=0} f' agent/session_health.py \| grep -cE "\.expire\("` | output >= 1 |
| Chokepoint invokes the sync flush | `grep -c "flush_deferred_self_draft_sync" models/session_lifecycle.py` | output > 0 |
| Sync helper has no loop calls | `awk '/def flush_deferred_self_draft_sync/{f=1} f&&/^def /&&!/flush_deferred_self_draft_sync/{f=0} f' agent/session_health.py | grep -nE "await \|create_task\|run_until_complete"` | no matches (the helper body contains no event-loop calls) |

---

## Resolved Questions

All open questions from the initial draft were resolved during plan revision (critique pass, 2026-06-25). They are recorded here as durable decisions, not open items.

1. **Q2 disposition (continuation) — RESOLVED: SPLIT OUT to a follow-on slug.** The self-draft continuation re-enqueue cleanup is NOT shipped in this slug — it is split to the follow-on slug `deferred_self_draft_continuation_cleanup` (tracked under No-Gos), because the chokepoint flush makes the continuation cosmetic (delivery is already guaranteed) and the core fix does not depend on it. The narrow `drafter-fallback` filter + `filtered`-list guard + `test_steering.py` regression travel to that follow-on slug. The broader general continuation guard remains separately tracked under No-Gos `[SEPARATE-SLUG #1794]`.
2. **Completion gating (issue Q4) — RESOLVED: flush-then-complete.** A deferred-self-draft session is allowed to reach `completed`; the chokepoint flushes the held text at `finalize_session` rather than blocking completion until the redraft resolves. Flush-at-finalize is the intended invariant, not a hard gate.
3. **Sync/async dispatch (the BLOCKER) — RESOLVED: fully synchronous flush.** `finalize_session` is a plain sync `def` with no event-loop dispatch, and the `completed` path reaches it with no ambient running loop. The flush is therefore a fully synchronous helper (`flush_deferred_self_draft_sync`) ending in a direct outbox `r.rpush(...)` — NO `await`/`create_task`/`run_until_complete`. There is no fire-and-forget task and no moving the chokepoint up to an async caller. See Technical Approach, Rabbit Holes, and Risk 1.
4. **Flush insertion point + authoritative read (BLOCKER 2, superseded by 5th-critique CONCERN) — RESOLVED.** (a) The flush is placed **below** the idempotency early-return at `models/session_lifecycle.py:337` (`if current_status == status: return`) **AND below the `reject_from_terminal` guard at `:347`** (`raise StatusConflictError`), in the CAS re-read region before the status `save()`. It runs only on a legitimate first-time terminal transition (where `current_status` is non-terminal); a redundant re-finalize correctly short-circuits at `:337` (the first genuine finalize already flushed), and a rejected illegal terminal→different-terminal re-transition raises at `:347` *without* a spurious delivery. (The earlier draft placed it above `:337`; that was wrong — it would also sit above the `:347` reject and deliver-then-raise on a rejected transition.) The SETNX guards the recovery-after-completion case (a later genuine terminal transition through the chokepoint), not the double-finalize case. (b) The helper reads `deferred_self_draft_pending`/`deferred_self_draft_text` from a fresh authoritative session via `get_authoritative_session(session_id)` (the existing CAS `fresh` object at `:365` is discarded and never re-reads `extra_context`), falling back to the caller's `session.extra_context` only on a `None` fresh read. See Technical Approach, Race 2, and Race 3.
5. **Payload construction + transport scope (BLOCKER 2, 2nd-round) — RESOLVED: telegram-only sync flush, async helper retained for email.** The async helper delegates to `send_cb` and builds no payload itself, so the sync flush constructs the telegram payload explicitly per the verified recipe (`chat_id`/`reply_to`/`text`/`session_id`/`timestamp` + `r.expire(queue_key, OUTBOX_TTL)`; `reply_to` and `r.expire` are MANDATORY). Email-transport replication is out of appetite, so the async `_deliver_deferred_self_draft_fallback` is **retained** (NOT deleted) for email coverage on `failed`/`abandoned`; the email completed-path is a documented known-gap (No-Gos). See Technical Approach payload recipe + Email-transport scope note.
6. **Redundancy filter / RTR bypass (CONCERN 3, 2nd-round) — RESOLVED: deliberate bypass.** The sync flush intentionally bypasses the redundancy filter and RTR (`output_handler.py:476` sequencing): the held text was already drafter-validated at defer time, a terminal flush has no live SDLC session for RTR, and redundancy suppression is a nice-to-have not a correctness guard. Bounded, accepted trade-off — see Rabbit Holes and Risk 4.
7. **Telegram double-send on `failed`/`abandoned` (5th-critique BLOCKER) — RESOLVED: async helper is now EMAIL-ONLY.** Because the sync chokepoint flush fires on ALL terminal paths (`completed`/`failed`/`abandoned`) for telegram, and the async helper (`session_health.py:1338`) today resolves `transport = extra_ctx.get("transport")` (`:1411`) then unconditionally `await send_cb(...)` (`:1426`) for ANY transport, a TELEGRAM `failed`/`abandoned` session would deliver TWICE — once via the sync flush, once via the async helper — and the two DISTINCT dedup keys deliberately cannot cross-dedup it. **Resolution:** add `if transport in (None, "telegram"): return` to the async helper right after it resolves `transport`, making it email-only. The sync flush now exclusively owns telegram on every terminal path and the async helper exclusively owns email; the two paths target disjoint transports, so a double-send is structurally impossible. The keys stay distinct (per-transport-disjoint) — exactly-once holds within each transport. See Technical Approach (async-helper-edited bullet), Risk 3, and Build Task 1.
