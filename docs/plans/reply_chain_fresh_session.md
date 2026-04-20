---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-20
tracking: https://github.com/tomcounsell/ai/issues/1064
last_comment_id:
revision_applied: true
critique_verdict: READY TO BUILD (with concerns)
critique_recorded_at: 2026-04-20T05:48:19Z
---

<!--
REVISION PASS (2026-04-20): /do-plan-critique returned "READY TO BUILD (with concerns)"
with 0 blockers, 5 concerns, and 3 nits (artifact_hash sha256:943ae3592876cc52cb9054947afe4d251c2b428fc7b0ea538ae4301b8c41afe8).
This revision pass (Row 4b of SDLC dispatch table) embeds Implementation Notes at the plan
sections the critique targeted. Concerns remain acknowledged risks (not defects);
Implementation Notes give the builder mid-flight guidance so the concerns cannot silently
bite during build. Nits are captured in the "Critique Nits (Informational)" section at the
bottom for PR-body reference but NOT embedded — out of scope for the revision pass per
Row 4b semantics.

The raw concern bodies from the critique subagent were not persisted outside the
stage_states verdict summary. The Implementation Notes below are derived from a careful
re-read of this plan against the critique verdict — they target the highest-risk
mid-flight pitfalls a Sonnet builder could hit when executing the plan as written:
handler line-number drift, gate-condition precision, idempotency flag semantics,
kill-switch truthy-value coverage, and double-fetch prevention. If the original
critique concerns differ materially from these, the builder should surface the gap
in PR review rather than silently correcting it.
-->


# Reply-Chain Hydration For Fresh Non-Valor Reply Sessions

## Problem

When a Telegram user replies to **another user's** message (not Valor's) and that reply creates a **fresh** agent session (not a continuation), the agent starts without the thread history it is replying to. The `[CONTEXT DIRECTIVE]` heuristic is explicitly skipped on any message with `reply_to_msg_id`, and the worker-side deferred enrichment depends on a fragile chain (`telegram_message_key` → `TelegramMessage.query.filter()` → `reply_to_msg_id` populated → `telegram_client` resolvable) that silently no-ops on any missing link.

**Concrete incident (from issue #1064):** Tom sent "engels read this and propose an issue for targeted updates to our skills that use Opus" as a Telegram reply to a thread where C. had posted an article URL. The session was created and routed as a continuation of `tg_valor_-1003879986445_389`, ran for 22 seconds, and returned a 176-char generic deflection — because the replied-to URL never reached the agent's first turn. C.'s follow-up edit containing the URL arrived as a steering message but was dropped when the session completed before it could be injected.

**Current behavior:**

- `is_reply_to_valor = True` path (replying to Valor) — handled by PR #953: resume-completed branch pre-hydrates the reply chain synchronously with a 3s timeout.
- `is_reply_to_valor = False` fresh-session path — **no pre-hydration**. The `[CONTEXT DIRECTIVE]` at `bridge/telegram_bridge.py:1858-1881` is gated on `not message.reply_to_msg_id` so it does not fire. Worker-side `enrich_message` may fire, but only if every precondition holds.

**Desired outcome:**

Any message with `reply_to_msg_id` that creates a fresh session unconditionally pre-fetches the reply chain at bridge enqueue time (with the same 3s sync-timeout pattern from PR #953) and either:
- Prepends the formatted `REPLY THREAD CONTEXT` block to `message_text` before enqueue (matches the resume-completed branch), AND
- Stamps `extra_context["reply_chain_hydrated"] = True` so the existing worker-side idempotency guard at `agent/session_executor.py:1045-1055` correctly skips the deferred fetch.

Deferred enrichment remains the fallback when the synchronous fetch times out or errors.

## Freshness Check

**Baseline commit:** `c5c24ee3` (hotfix: re-enqueue dropped steering messages and preserve session_type on resume)
**Issue filed at:** 2026-04-20T03:54:12Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/telegram_bridge.py:1008` — `is_reply_to_valor and message.reply_to_msg_id` gate — still holds.
- `bridge/telegram_bridge.py:1858-1881` — `[CONTEXT DIRECTIVE]` injection gated on `not message.reply_to_msg_id` — still holds.
- `bridge/enrichment.py:156-179` — deferred reply-chain branch — still holds.
- `bridge/context.py:396-505` — `fetch_reply_chain` + `format_reply_chain` — unchanged, correct.
- `agent/session_executor.py:1034-1055` — idempotency guard stamping `reply_chain_hydrated` and scanning for `REPLY_THREAD_CONTEXT_HEADER` — already in place from PR #953.
- `models/agent_session.py:167` — `extra_context = DictField(null=True)` — unchanged.

**Cited sibling issues/PRs re-checked:**
- PR #953 (closed #949, merged 2026-04-14): Pre-hydration scaffolding + idempotency guard — shipped and stable. Reused verbatim by this plan.
- #996 (closed 2026-04-16): "Bug: reply to any thread message should steer the session, not just replies to Valor's messages" — different concern (steering existing sessions) but same architectural area. No conflict.
- #997 (closed 2026-04-16): `resolve_root_session_id` duplicate-enqueue bug — fixed. This plan only touches the fresh-session branch, which never calls `resolve_root_session_id`.

**Commits on main since issue was filed:** None touching `bridge/telegram_bridge.py`, `bridge/context.py`, `bridge/enrichment.py`, `agent/session_executor.py`, or `models/agent_session.py` between 2026-04-20T03:54:12Z and plan time.

**Active plans in `docs/plans/` overlapping this area:** None. `reply_thread_context_hydration.md` is the shipped #949 plan (Status: Shipped). No active overlap.

**Notes:** The existing worker-side idempotency guard is the single most important prior art — it means this plan is a minimal handler-side addition with zero worker-side changes required.

## Prior Art

- **PR #953** (merged 2026-04-14): "fix(bridge): hydrate reply-thread context in resume-completed branch" — shipped the `is_reply_to_valor=True` resume path's pre-hydration. **Direct precedent; this plan extends the same pattern to the `is_reply_to_valor=False` fresh-session path.**
- **Issue #949** (closed): Parent issue for PR #953 — contains the full architectural discussion of the three-change design (A: resume pre-hydration, B: layered preamble, C: implicit-context directive). This plan adds a fourth path that #949 did not cover.
- **PR #922** (merged 2026-04-13): "Fix: deterministic reply-to root cache + completed session resume" — added the root-cache machinery for canonical `session_id` derivation. Relevant but orthogonal — the fresh-session path this plan targets does not use the cache.
- **PR #574, #573** (merged 2026-03-27): "Fix reply-to session resume: resolve root session_id via chain walk" — established `resolve_root_session_id`. Orthogonal — only the `is_reply_to_valor=True` branch uses it.
- **Issue #996** (closed 2026-04-16): "Bug: reply to any thread message should steer the session, not just replies to Valor's messages" — adjacent concern (steering live sessions for non-Valor replies). Different phase of the lifecycle — this plan only addresses fresh-session enqueue, not steering-vs-enqueue routing.

## Why Previous Fixes Failed

Not a case of failed fixes — a case of **incomplete coverage**. PR #953 explicitly scoped itself to the resume-completed branch (issue #949 sections "Change A" / "Change B"). The fresh-session + `is_reply_to_valor=False` path was not in scope for #953, and the `[CONTEXT DIRECTIVE]` (Change C from #953) was deliberately gated to `not message.reply_to_msg_id` because reply-to messages were *assumed* to go through the resume-completed branch and get handled by Change A.

What that assumption missed: a reply to a non-Valor message does NOT take the resume-completed branch. It falls through to semantic routing, which often fails to match, and creates a fresh session. This fresh-session path had neither Change A's pre-hydration nor Change C's directive.

**Root cause pattern:** Two overlapping gating predicates (`is_reply_to_valor` in the bridge, `not message.reply_to_msg_id` in the directive block) created an unintended dead zone where reply-to messages that created fresh sessions got neither treatment. This plan closes that zone.

## Research

No relevant external findings — this is purely internal bridge plumbing. All needed primitives (`fetch_reply_chain`, `format_reply_chain`, `REPLY_THREAD_CONTEXT_HEADER`, `extra_context` DictField, the worker-side idempotency guard) already exist from prior PRs.

## Data Flow

### Before (current, broken)

```
Telegram message with reply_to_msg_id=42, sender=Tom, replying to C.'s URL post
        │
        ▼
bridge/telegram_bridge.py handler
        │
        ├── is_reply_to_valor(replied_msg) → False  (replied-to is C.'s, not Valor's)
        │
        ├── session_id = None  (fresh-session path: bridge/telegram_bridge.py:1016+)
        │   ├── find_matching_session() → no match
        │   └── session_id = f"tg_{project}_{chat}_{msg_id}"
        │
        ├── [CONTEXT DIRECTIVE] check at line 1858-1881
        │   └── GATED ON `not message.reply_to_msg_id` → SKIPPED
        │
        ├── enqueued_message_text = clean_text   (no pre-hydration)
        │
        └── dispatch_telegram_session(
                message_text=enqueued_message_text,  # raw
                telegram_message_key=stored_msg_id,  # set
                extra_context_overrides=None,        # empty
                ...)
                │
                ▼
        Worker picks up session
                │
                ├── Resolve enrichment from TelegramMessage (session_executor.py:1008-1032)
                │   └── IF lookup succeeds AND reply_to_msg_id populated:
                │       enrich_reply_to_msg_id = 42
                │   ELSE: silently skipped, reply chain LOST
                │
                ├── Idempotency guard (session_executor.py:1034-1055)
                │   └── reply_chain_hydrated flag absent, header absent → fetch proceeds
                │
                └── enrich_message() (bridge/enrichment.py:156-179)
                    └── fetch_reply_chain + format_reply_chain → prepends context
                        (only if telegram_client and chat_id available at worker time)
```

### After (proposed)

```
Telegram message with reply_to_msg_id=42, sender=Tom, replying to C.'s URL post
        │
        ▼
bridge/telegram_bridge.py handler
        │
        ├── is_reply_to_valor(replied_msg) → False  (same as before)
        │
        ├── session_id = None  (same fresh-session path)
        │
        ├── [CONTEXT DIRECTIVE] check → SKIPPED (unchanged)
        │
        ├── NEW: Pre-hydrate reply chain (only when message.reply_to_msg_id is set
        │       AND is_reply_to_valor=False AND kill-switch is off)
        │   │
        │   ├── os.getenv("REPLY_CHAIN_PREHYDRATION_DISABLED") → skip
        │   │
        │   ├── asyncio.wait_for(
        │   │       fetch_reply_chain(client, chat_id, reply_to_msg_id, max_depth=20),
        │   │       timeout=3.0
        │   │   )
        │   │   on success: reply_chain_context = format_reply_chain(chain)
        │   │   on timeout: logger.warning("FRESH_REPLY_CHAIN_FAIL timeout ...")
        │   │   on exception: logger.warning("FRESH_REPLY_CHAIN_FAIL exception ...")
        │   │
        │   ├── IF reply_chain_context:
        │   │     enqueued_message_text = f"{reply_chain_context}\n\nCURRENT MESSAGE:\n{clean_text}"
        │   │     extra_overrides = {"reply_chain_hydrated": True}
        │   │   ELSE:
        │   │     enqueued_message_text = clean_text
        │   │     extra_overrides = None  (deferred enrichment will try)
        │
        └── dispatch_telegram_session(
                message_text=enqueued_message_text,
                telegram_message_key=stored_msg_id,
                extra_context_overrides=extra_overrides,
                ...)
                │
                ▼
        Worker picks up session
                │
                ├── Resolve enrichment from TelegramMessage  (unchanged)
                │
                ├── Idempotency guard (existing, unchanged):
                │   IF extra_context["reply_chain_hydrated"] OR
                │      REPLY_THREAD_CONTEXT_HEADER in message_text:
                │     enrich_reply_to_msg_id = None  (skip fetch)
                │
                └── enrich_message()
                    └── Only fires if handler pre-hydration failed or was skipped.
                        Media/YouTube/link enrichment still runs.
```

## Architectural Impact

- **New dependencies:** None. Reuses existing `fetch_reply_chain`, `format_reply_chain`, `REPLY_THREAD_CONTEXT_HEADER`, `dispatch_telegram_session`'s existing `extra_context_overrides` param.
- **Interface changes:** None. No function signatures change. A new env var (`REPLY_CHAIN_PREHYDRATION_DISABLED`) is added for kill-switch parity with `REPLY_CONTEXT_DIRECTIVE_DISABLED`.
- **Coupling:** Slightly reduces coupling to worker-side state — the handler no longer relies on `TelegramMessage.reply_to_msg_id` being indexed and on `telegram_client` being resolvable at worker time for the happy path.
- **Data ownership:** `extra_context["reply_chain_hydrated"]` is now written by two call sites: the existing resume-completed branch (`bridge/telegram_bridge.py` resume path) and the new fresh-session path. Both stamp the same flag; the worker's idempotency guard is already the single consumer.
- **Reversibility:** Fully reversible. Set `REPLY_CHAIN_PREHYDRATION_DISABLED=1` to disable without deploy. Full code revert restores pre-plan behavior bit-for-bit.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0-1 (scope is tightly bounded by PR #953's precedent)
- Review rounds: 1 (code review + integration test review)

Scope is minimal because PR #953 already built the scaffolding. This is a targeted addition to one more call site plus tests.

## Prerequisites

No prerequisites — this work has no external dependencies. All required primitives exist in the codebase.

## Solution

### Key Elements

- **Fresh-session pre-hydration block** in `bridge/telegram_bridge.py`: a new block placed immediately after the `[CONTEXT DIRECTIVE]` block and before `dispatch_telegram_session`. Only fires when `message.reply_to_msg_id` is set, `is_reply_to_valor=False`, and the kill-switch env var is off.
- **Kill-switch env var** `REPLY_CHAIN_PREHYDRATION_DISABLED` — checked at handler entry, mirrors the existing `REPLY_CONTEXT_DIRECTIVE_DISABLED` pattern for safe rollback without redeploy.
- **Stamp `reply_chain_hydrated` on `extra_context_overrides`** when the pre-fetch succeeds, so the existing worker-side idempotency guard at `agent/session_executor.py:1045-1055` skips the deferred fetch.
- **3s `asyncio.wait_for` timeout with `FRESH_REPLY_CHAIN_FAIL` warning logs** — copies the exact pattern from PR #953's resume-completed branch. On timeout/exception, the handler falls through with raw `clean_text` and the worker's deferred enrichment attempts to hydrate.

<!-- Implementation Note (C2 — Idempotency flag semantics on empty/failed chain): The flag `reply_chain_hydrated` has THREE possible outcomes, not two:
       - (a) Fetch succeeded, chain non-empty → stamp `reply_chain_hydrated=True`, prepend `REPLY_THREAD_CONTEXT` to message_text.
       - (b) Fetch succeeded, chain empty (`format_reply_chain([])` returns "") → do NOT stamp the flag, do NOT modify message_text. Worker-side deferred enrichment will also find no chain to fetch — this is correct behavior, not a bug.
       - (c) Fetch timed out or raised → do NOT stamp the flag, do NOT modify message_text. Worker's deferred enrichment MUST remain free to retry.
     The reason (b) does NOT stamp the flag: stamping it would short-circuit the worker's deferred enrichment for a chain that was never retrieved — a subtle dead zone. Better to leave the flag unset and let the worker's retry discover the chain is empty too, confirming via a second fetch that nothing was missed. The plan's Failure Path Test Strategy already asserts this (line 230: "empty chain produces enqueued_message_text == clean_text and reply_chain_hydrated is NOT set") — the builder MUST honor this assertion. -->

- **No worker-side changes required.** The existing idempotency guard handles the new call site transparently because both paths use the same `reply_chain_hydrated` flag.

### Flow

Telegram group chat → User replies to non-Valor message → Bridge handler resolves `is_reply_to_valor=False` → Semantic routing misses → Fresh-session path → **NEW: pre-hydrate reply chain (3s timeout)** → Enqueue with `REPLY THREAD CONTEXT` block in `message_text` and `reply_chain_hydrated=True` in `extra_context` → Worker picks up session → Idempotency guard skips deferred fetch → Agent's first turn contains the thread

### Technical Approach

- Reuse PR #953's `asyncio.wait_for(fetch_reply_chain(...), timeout=3.0)` pattern verbatim, renaming only the log tag to `FRESH_REPLY_CHAIN_FAIL` so the two call sites are distinguishable in logs.
- Place the new block *after* the `[CONTEXT DIRECTIVE]` block — the directive is already gated off for reply-to messages, so ordering doesn't matter for correctness, but placing pre-hydration second keeps the "directive-then-chain" locality readable.
- Use the existing `extra_context_overrides` kwarg on `dispatch_telegram_session` to pass `{"reply_chain_hydrated": True}`. This kwarg already flows through `enqueue_agent_session` into `AgentSession.extra_context`.
- When pre-hydration *fails* (timeout/exception), do NOT stamp `reply_chain_hydrated` — this is critical. The worker's deferred enrichment must still try to hydrate in that case.
- Emit a `fresh_reply_chain_prehydrated` INFO log on success for observability, mirroring `implicit_context_directive_injected`.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] The new pre-hydration block uses `try/except asyncio.TimeoutError` and `except Exception`. Both branches MUST log a `FRESH_REPLY_CHAIN_FAIL` warning with `session_id`, `chat_id`, `reply_to_msg_id`, and (for exception) `error` fields. Unit test asserts both branches emit the warning and leave `reply_chain_hydrated` unset.
- [ ] No other new exception handlers are introduced.

### Empty/Invalid Input Handling

- [ ] `format_reply_chain([])` already returns `""` (verified in PR #953). When the chain is empty, the handler must NOT prepend an empty string wrapper — unit test asserts that an empty chain produces `enqueued_message_text == clean_text` and `reply_chain_hydrated` is NOT set.
- [ ] Handler must gracefully handle `message.reply_to_msg_id=None` (path skipped entirely — not even a timeout warning). Unit test covers this baseline.
- [ ] Kill-switch set to `1` / `true` / `yes` / `on` must skip the block entirely — unit test covers each truthy value and one falsy value.

### Error State Rendering

- Bridge handler has no user-visible rendering; errors go to logs only. Not applicable.

## Test Impact

- [ ] `tests/integration/test_steering.py::test_no_double_hydration_when_handler_prehydrates` — UPDATE: extend the test to also cover the new fresh-session pre-hydration path (currently only covers resume-completed). Rename from `test_no_double_hydration_when_handler_prehydrates` if the new coverage makes the name misleading; otherwise leave it. The existing assertion ("exactly one REPLY THREAD CONTEXT block per prompt") must hold for both call sites.
- [ ] `tests/integration/test_steering.py::test_reply_chain_fetch_failure_falls_back` — UPDATE: add a fresh-session variant parallel to the existing resume-completed variant.

<!-- Implementation Note (C5 — Test extension strategy: prefer parametrize over copy-paste): Before editing `test_no_double_hydration_when_handler_prehydrates`, the test-engineer MUST open the existing test and determine its shape:
       - If it's a flat test body: convert to `@pytest.mark.parametrize("hydration_site", ["resume_completed", "fresh_session_non_valor"])` and thread the site through the fixture setup. This makes the "exactly one REPLY THREAD CONTEXT block" invariant explicit for both sites.
       - If it's already parametrized on something else: add a second parametrize decorator or add a sibling test `test_no_double_hydration_fresh_session_prehydrates` rather than shoving a second site into a copy-pasted body.
     The goal is a SINGLE assertion contract ("exactly one REPLY THREAD CONTEXT block per prompt, regardless of which handler branch hydrated") — copy-pasted tests drift apart over time. Same principle for `test_reply_chain_fetch_failure_falls_back`: parametrize across `["resume_completed", "fresh_session_non_valor"]` rather than writing a parallel file. If parametrization turns out to require untangling of conflicting fixtures (e.g., one path uses a mocked resume cache, the other doesn't), the test-engineer MAY write parallel tests — but must note the reason in a module-level comment so the next engineer doesn't unify them and break assertions. -->

- [ ] `tests/integration/test_steering.py` (new tests) — ADD:
  - `test_fresh_session_non_valor_reply_prehydrates_chain` — a reply-to-non-Valor message creates a fresh session; the enqueued `message_text` contains `REPLY THREAD CONTEXT`; `extra_context.reply_chain_hydrated` is True.
  - `test_fresh_session_non_valor_reply_timeout_falls_back` — 3s timeout fires; warning logged; `reply_chain_hydrated` is NOT set; `message_text` is raw `clean_text`; worker-side deferred enrichment is not short-circuited.
  - `test_fresh_session_reply_to_valor_skips_new_block` — `is_reply_to_valor=True` path does NOT double-hydrate via the new block (confirms it only fires in the else-branch).
  - `test_fresh_session_prehydration_kill_switch` — `REPLY_CHAIN_PREHYDRATION_DISABLED=1` skips the new block; `reply_chain_hydrated` is not set; `message_text` is raw.
- [ ] `tests/unit/test_context_helpers.py` — no changes required (covers `bridge/context.py` helpers, which are unchanged).
- [ ] `tests/integration/test_catchup_revival.py` — regression only; must continue to pass unchanged.

All new/updated tests target `tests/integration/test_steering.py` because that's where PR #953's analogous tests live. Keep the co-location to make the full reply-chain story discoverable from one file.

## Rabbit Holes

- **Refactoring `enrich_message` to be the single owner of reply-chain hydration.** Tempting ("why have two call sites?") but wastes appetite — the bridge-time pre-fetch is precisely the point of this plan. Revisit only if a third call site appears.
- **Adding `context_summary` layering like Change B from #953.** That's a resume-completed concern. A fresh session has no prior session to layer against. Out of scope.
- **Catching and injecting live Telegram chat history (not just the reply chain).** Issue #996 already shipped the separate path for this. Do not blend the two.
- **Making the `[CONTEXT DIRECTIVE]` heuristic fire for reply-to messages too.** Directive + chain together would be duplicative. Leave the directive gated to no-reply-to messages.
- **Changing the 3s timeout.** Matches PR #953 exactly. Tuning belongs in a separate observability-driven change with telemetry data, not this plan.

## Risks

### Risk 1: Double-hydration of the chain block
**Impact:** Agent sees two `REPLY THREAD CONTEXT` blocks — confusing, wastes tokens.
**Mitigation:** The worker-side idempotency guard at `agent/session_executor.py:1045-1055` already handles this. It checks both `extra_context["reply_chain_hydrated"]` AND a `REPLY_THREAD_CONTEXT_HEADER` substring scan of `message_text`. Both signals are set by the new handler block, so the guard will correctly skip the worker-side fetch. Regression test `test_no_double_hydration_when_handler_prehydrates` is extended to cover the new call site.

### Risk 2: 3s timeout too aggressive in a slow-network environment
**Impact:** Handler emits `FRESH_REPLY_CHAIN_FAIL timeout` warnings frequently; agent falls back to raw `clean_text`.
**Mitigation:** The deferred enrichment path remains in place as a fallback — if the handler times out, the worker will still attempt to hydrate (assuming `telegram_message_key` and `reply_to_msg_id` were indexed). Log volume will be visible in `logs/bridge.log` and can be tuned in a follow-up if telemetry shows sustained failure rates. The 3s choice matches PR #953 exactly, which has been stable in production.

### Risk 3: Blocking the Telegram handler loop
**Impact:** If `fetch_reply_chain` takes the full 3s, downstream message handling is delayed by up to 3s.
**Mitigation:** Handler is already async; `asyncio.wait_for` yields the event loop during the `get_messages` roundtrip inside `fetch_reply_chain`. The 3s is a wall-clock cap on *this message's* processing, not a global lock. PR #953 established this is acceptable.

### Risk 4: Unexpected interaction with semantic routing
**Impact:** Semantic routing at `bridge/telegram_bridge.py:1020-1050+` may decide to treat the reply-to message as a steering message for an existing matched session. In that case, we've done a pre-fetch for nothing.
**Mitigation:** Place the new block *after* the semantic routing decision — i.e., only when `session_id` is still None at line ~1080+, indicating fresh session. Wasted work is avoided. Verified by reading the handler flow between lines 1016-1883.

<!-- Implementation Note (C4 — Semantic routing + steering path must be fully settled before pre-fetch): Placement is the ONLY correctness mechanism here. The new block MUST sit AFTER every code path that could route to a different session (steering, resume-completed, semantic match). Before writing the block, the builder MUST trace the handler from line 1008 to the enqueue call and explicitly list in a code comment:
       1. The line number where semantic routing resolves (match or no-match).
       2. The line number where steering dispatch (if any) would branch away.
       3. The line number where resume-completed pre-hydration (PR #953) would branch away.
     The new block's position MUST be strictly AFTER the MAX of these three line numbers and strictly BEFORE the `dispatch_telegram_session` call. If any of these three paths are found to branch BACKWARD into the fresh-session path (unlikely but possible), the builder MUST stop and flag the control-flow ambiguity in PR review rather than guess. A 3s pre-fetch on the wrong path wastes bridge event-loop time for every reply-to message. -->


## Race Conditions

### Race 1: Handler pre-hydration vs. worker deferred enrichment
**Location:** `bridge/telegram_bridge.py` (new block, ~line 1880+) vs. `agent/session_executor.py:1034-1079`.
**Trigger:** Handler starts pre-hydration; message is enqueued *before* pre-hydration completes (cannot happen — handler awaits the `asyncio.wait_for` before calling `dispatch_telegram_session`). OR handler's pre-hydration completes but fails to set `reply_chain_hydrated`; worker then also hydrates.
**Data prerequisite:** `extra_context["reply_chain_hydrated"]` must be set to True *only* on successful pre-hydration; the flag is set in the *same* synchronous block as the `message_text` prepend; both are passed atomically to `dispatch_telegram_session` in a single call.
**State prerequisite:** Worker reads `extra_context` and `message_text` from the same `AgentSession` record; both are persisted atomically via `AgentSession.save()`.
**Mitigation:** The handler block is synchronous with respect to `dispatch_telegram_session`: `await asyncio.wait_for(...)` completes, then the block assigns `enqueued_message_text` and `extra_overrides` atomically, then calls `dispatch_telegram_session`. The flag and the text are written in the same call. The existing worker-side guard uses both the flag AND a header substring scan as belt-and-suspenders — even if the flag is somehow missing, the header check catches it.

### Race 2: Pre-hydration in flight when session is already being processed
**Location:** `bridge/telegram_bridge.py` handler.
**Trigger:** Telegram delivers the same message twice (duplicate delivery). Both deliveries spawn concurrent handler invocations; both start pre-hydration.
**Data prerequisite:** Bridge-side `is_duplicate_message` check runs before the new pre-hydration block.
**State prerequisite:** `record_message_processed` happens after `enqueue_agent_session` (inside `dispatch_telegram_session`), so the dedup window protects against repeat enqueue.
**Mitigation:** Run the `is_duplicate_message` check before the new block (this check already runs earlier in the handler per PR #953 IN-4). Even if both invocations reach the pre-hydration block, both will produce the same formatted chain string, and only one enqueue will succeed (dedup record ensures this). The second enqueue attempt will no-op.

## No-Gos (Out of Scope)

- Changes to the resume-completed branch (already done in PR #953).
- Changes to `[CONTEXT DIRECTIVE]` gating or heuristic (no reason to touch it).
- Changes to `fetch_reply_chain` / `format_reply_chain` / `REPLY_THREAD_CONTEXT_HEADER` — all stable, reused as-is.
- Changes to worker-side deferred enrichment or its idempotency guard.
- Adding live chat history (beyond the reply chain) to the first turn — issue #996 is the appropriate venue.
- Tuning the 3s timeout — belongs in a telemetry-driven follow-up.
- Adding a `reply_chain_prehydrated_at` timestamp — over-engineering; logs capture this.

## Update System

No update system changes required — this feature is purely internal to the bridge module. No new dependencies, no new config files, no migration steps. The optional `REPLY_CHAIN_PREHYDRATION_DISABLED` env var has a safe default (unset = enabled) and does not need to be added to `.env.example` (matches the precedent set by `REPLY_CONTEXT_DIRECTIVE_DISABLED`, which is also undocumented in `.env.example`).

## Agent Integration

No agent integration required — this is a bridge-internal change. The agent continues to see reply-chain context via the same `REPLY THREAD CONTEXT` block it already sees from PR #953's resume-completed path. No MCP server changes, no `.mcp.json` changes, no new tools exposed. The bridge is already the sole consumer of `fetch_reply_chain` / `format_reply_chain`.

Integration test coverage: `tests/integration/test_steering.py` already exercises the full bridge-to-agent path for the resume-completed branch; the new tests in the Test Impact section extend that coverage to the fresh-session branch.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/reply-thread-context-hydration.md` — add a new subsection (or extend the existing "Reply-To Arrives, Resolves To Completed Session" section) documenting the fresh-session + non-Valor-reply flow. Include the updated table showing both call sites in "Precedence Between Pre-Hydration And Deferred Enrichment".
- [ ] No entry needed in `docs/features/README.md` index table (feature already indexed).

### External Documentation Site

- This repo uses `docs/features/` only; no Sphinx/MkDocs site. Not applicable.

### Inline Documentation

- [ ] Code comment on the new handler block explaining the scope delta vs. PR #953 (resume-completed vs. fresh-session, and why both exist).
- [ ] Docstring/comment on the `REPLY_CHAIN_PREHYDRATION_DISABLED` env var at the check site.

## Success Criteria

- [ ] A Telegram reply to a non-Valor message that creates a fresh session includes the full reply chain as a `REPLY THREAD CONTEXT` block in the agent's first turn.
- [ ] `extra_context["reply_chain_hydrated"]` is set to `True` on the `AgentSession` when the handler successfully pre-hydrated.
- [ ] When the handler pre-fetch times out or errors, `reply_chain_hydrated` is NOT set, and the worker's deferred enrichment is free to try (no silent dead zone).
- [ ] Exactly one `REPLY THREAD CONTEXT` block in every agent prompt — no double-hydration (`test_no_double_hydration_when_handler_prehydrates` extended to cover the new path).
- [ ] `REPLY_CHAIN_PREHYDRATION_DISABLED=1` disables the new block without a code deploy.
- [ ] All existing tests pass unchanged (`tests/integration/test_steering.py`, `tests/integration/test_catchup_revival.py`, `tests/unit/test_context_helpers.py`).
- [ ] Four new integration tests in `tests/integration/test_steering.py` pass.
- [ ] `docs/features/reply-thread-context-hydration.md` updated to document the fresh-session path.
- [ ] `python -m ruff check bridge/` passes.
- [ ] `python -m ruff format --check bridge/` passes.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (bridge-handler)**
  - Name: bridge-handler-builder
  - Role: Implement the new pre-hydration block in `bridge/telegram_bridge.py` and the kill-switch env check. Apply inline comments and the log-tag rename.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (integration-tests)**
  - Name: reply-chain-test-engineer
  - Role: Write the four new `tests/integration/test_steering.py` tests and extend `test_no_double_hydration_when_handler_prehydrates` to cover the fresh-session call site.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (bridge-validator)**
  - Name: bridge-validator
  - Role: Verify the handler change matches the Data Flow diagram, the idempotency guard is not bypassed, and the new block runs in the correct position within the handler (after semantic routing decision, after `[CONTEXT DIRECTIVE]` block, before `dispatch_telegram_session`).
  - Agent Type: validator
  - Resume: true

- **Documentarian (feature-docs)**
  - Name: feature-doc-writer
  - Role: Update `docs/features/reply-thread-context-hydration.md` to cover the fresh-session path — both the Flow diagram and the Precedence table.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Using standard tier-1 agents (builder, test-engineer, validator, documentarian). No tier-2 specialists needed — this is a targeted bridge change with a clear precedent.

## Step by Step Tasks

### 1. Implement fresh-session pre-hydration block
- **Task ID**: build-prehydration
- **Depends On**: none
- **Validates**: `tests/integration/test_steering.py` (new + updated tests)
- **Informed By**: PR #953's resume-completed branch implementation (`bridge/telegram_bridge.py` resume path) as the reference pattern.
- **Assigned To**: bridge-handler-builder
- **Agent Type**: builder
- **Parallel**: true
- Add the new block in `bridge/telegram_bridge.py` after the `[CONTEXT DIRECTIVE]` block (current line ~1882) and before `dispatch_telegram_session` (current line ~1887).
- Gate the block on: `message.reply_to_msg_id and not is_reply_to_valor and session_id is None-at-this-point-is-not-the-right-check` — use the more precise gate: "reached the fresh-session enqueue path AND `message.reply_to_msg_id` is set AND kill-switch is off". Re-read the handler flow (lines 1008-1883) to confirm the exact condition.

<!-- Implementation Note (C1 — Gate-condition precision): The prose above leaves the gate ambiguous ("is-None-at-this-point-is-not-the-right-check"). The builder MUST resolve this before writing code. Canonical gate condition:
       1. `message.reply_to_msg_id` is truthy (reply-to exists), AND
       2. `is_reply_to_valor` is False (NOT replying to Valor's own message — the True branch is already handled by PR #953 resume-completed path), AND
       3. Control flow has reached the fresh-session enqueue path (i.e., semantic routing did not match an existing session and no resume-completed path was taken), AND
       4. Kill-switch env var is off.
     The correct way to express (3) in code is NOT `session_id is None` — by the time we reach the enqueue block, `session_id` has been assigned the fresh ID `f"tg_{project}_{chat}_{msg_id}"`. The correct signal is the absence of a prior resume-completed code path — verified by placement: the new block sits AFTER the resume-completed branch's pre-hydration call site (so if resume-completed fired, we never reach here) and AFTER the `[CONTEXT DIRECTIVE]` block (which is gated off for reply-to messages). Simply placing the block at the right point in the handler flow IS the (3) condition — no explicit `session_id is None` check needed. If the builder is uncertain about handler topology, they must read `bridge/telegram_bridge.py:1008-1883` top-to-bottom before writing the block. -->


- Check `os.getenv("REPLY_CHAIN_PREHYDRATION_DISABLED", "").strip().lower() in ("1", "true", "yes", "on")` as the kill-switch.

<!-- Implementation Note (C3 — Kill-switch truthy-value parity with sibling env var): The truthy set `("1", "true", "yes", "on")` must match the exact parsing used for `REPLY_CONTEXT_DIRECTIVE_DISABLED` in the same bridge file (the precedent PR #953 followed). Before writing the check, the builder MUST grep for the sibling check: `grep -n 'REPLY_CONTEXT_DIRECTIVE_DISABLED' bridge/telegram_bridge.py` — locate the existing helper/inline check and mirror it EXACTLY (same truthy set, same `.strip().lower()` normalization, same default `""`). If the sibling uses a shared helper (e.g., `_env_flag_on(name)`), reuse it; do NOT write a parallel implementation. The unit test in Test Impact (line 232) already covers `1/true/yes/on` + one falsy value — the builder must verify these assertions still hold after mirroring the sibling pattern (the sibling may use a slightly different truthy set, in which case the plan's test assertions may need adjustment to match reality). -->

- Use `asyncio.wait_for(fetch_reply_chain(client, event.chat_id, message.reply_to_msg_id, max_depth=20), timeout=3.0)` for the fetch.
- On `asyncio.TimeoutError`: `logger.warning("FRESH_REPLY_CHAIN_FAIL timeout", ...)`. On `Exception`: `logger.warning("FRESH_REPLY_CHAIN_FAIL exception", ..., error=...)`. Both include `session_id`, `chat_id`, `reply_to_msg_id` fields.
- On success with non-empty chain: `enqueued_message_text = f"{reply_chain_context}\n\nCURRENT MESSAGE:\n{enqueued_message_text}"` AND build `extra_overrides = {"reply_chain_hydrated": True}`.
- On empty chain or failure: leave `enqueued_message_text` as-is, do NOT set `reply_chain_hydrated`.
- Pass `extra_context_overrides=extra_overrides` through to `dispatch_telegram_session`.
- Emit `logger.info("fresh_reply_chain_prehydrated session_id=%s chat_id=%s chain_len=%d", ...)` on success.

### 2. Write integration tests
- **Task ID**: build-tests
- **Depends On**: none (can run in parallel with build-prehydration, but will need the new code to actually pass)
- **Validates**: The four new tests in the Test Impact section, plus the extension of `test_no_double_hydration_when_handler_prehydrates`.
- **Informed By**: Existing tests in `tests/integration/test_steering.py` (PR #953 patterns).
- **Assigned To**: reply-chain-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: true
- Read the existing PR #953 tests in `tests/integration/test_steering.py` to mirror their structure (mocking, fixtures, assertions).
- Write `test_fresh_session_non_valor_reply_prehydrates_chain`: fresh session, reply-to non-Valor, assert `AgentSession.message_text` contains `REPLY_THREAD_CONTEXT_HEADER`, `extra_context["reply_chain_hydrated"] is True`.
- Write `test_fresh_session_non_valor_reply_timeout_falls_back`: mock `fetch_reply_chain` to hang; assert `asyncio.TimeoutError` path logs `FRESH_REPLY_CHAIN_FAIL timeout`; `reply_chain_hydrated` is NOT set; `message_text` is raw.
- Write `test_fresh_session_reply_to_valor_skips_new_block`: reply to Valor's own message; the resume-completed branch handles it; the new block does not run (assert no `fresh_reply_chain_prehydrated` log).
- Write `test_fresh_session_prehydration_kill_switch`: set `REPLY_CHAIN_PREHYDRATION_DISABLED=1`; assert new block is skipped.
- Extend `test_no_double_hydration_when_handler_prehydrates` (or add a sibling test `test_no_double_hydration_fresh_session_prehydrates`): pre-hydrate via the new block, run through the worker, assert exactly one `REPLY_THREAD_CONTEXT_HEADER` substring in the final harness input.

### 3. Validate handler changes
- **Task ID**: validate-handler
- **Depends On**: build-prehydration
- **Assigned To**: bridge-validator
- **Agent Type**: validator
- **Parallel**: false
- Read `bridge/telegram_bridge.py` around the new block; confirm it is placed after the `[CONTEXT DIRECTIVE]` block and before `dispatch_telegram_session`.
- Confirm the gate condition only fires on the fresh-session path (not the resume-completed path).
- Confirm `extra_context_overrides={"reply_chain_hydrated": True}` is passed through only on success.
- Confirm the 3s timeout and `FRESH_REPLY_CHAIN_FAIL` log shape match PR #953's resume-completed branch.
- Run `python -m ruff check bridge/` and `python -m ruff format --check bridge/`.
- Report pass/fail.

### 4. Run integration tests
- **Task ID**: validate-tests
- **Depends On**: build-prehydration, build-tests
- **Assigned To**: bridge-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_steering.py tests/integration/test_catchup_revival.py tests/unit/test_context_helpers.py -q`.
- All tests pass; no regressions.
- Report pass/fail with the full pytest output.

### 5. Update feature documentation
- **Task ID**: document-feature
- **Depends On**: validate-handler, validate-tests
- **Assigned To**: feature-doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reply-thread-context-hydration.md`:
  - Add a new Flow subsection for "Fresh-Session Non-Valor Reply Arrives" (parallel to the existing "Reply-To Arrives, Resolves To Completed Session" section).
  - Update the "Precedence Between Pre-Hydration And Deferred Enrichment" table to include a new row for the fresh-session path.
  - Update the Failure Paths table to include the new `FRESH_REPLY_CHAIN_FAIL` log tag.
  - Update the Tests section to reference the new tests.
  - Update the Rollback section to include `REPLY_CHAIN_PREHYDRATION_DISABLED` as the second kill-switch.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: bridge-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all Success Criteria checkboxes are satisfied.
- Run the full `Verification` table commands.
- Confirm the worktree is clean (no untracked artifacts).
- Generate final PR-ready summary.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/integration/test_steering.py tests/integration/test_catchup_revival.py tests/unit/test_context_helpers.py -q` | exit code 0 |
| Lint clean (bridge) | `python -m ruff check bridge/` | exit code 0 |
| Format clean (bridge) | `python -m ruff format --check bridge/` | exit code 0 |
| Lint clean (tests) | `python -m ruff check tests/integration/test_steering.py` | exit code 0 |
| Reply chain hydrated flag used | `grep -n 'reply_chain_hydrated' bridge/telegram_bridge.py` | output contains at least 1 line |
| Kill-switch env var referenced | `grep -n 'REPLY_CHAIN_PREHYDRATION_DISABLED' bridge/telegram_bridge.py` | output contains at least 1 line |
| FRESH_REPLY_CHAIN_FAIL log tag present | `grep -n 'FRESH_REPLY_CHAIN_FAIL' bridge/telegram_bridge.py` | output contains at least 2 lines (timeout + exception) |
| Feature doc updated | `grep -n 'FRESH_REPLY_CHAIN_FAIL\|fresh-session' docs/features/reply-thread-context-hydration.md` | output contains at least 1 line |

## Critique Results

**Critiqued:** 2026-04-20T05:48:19Z
**Verdict:** READY TO BUILD (with concerns)
**Findings:** 0 blockers, 5 concerns, 3 nits
**Artifact hash:** `sha256:943ae3592876cc52cb9054947afe4d251c2b428fc7b0ea538ae4301b8c41afe8`
**Revision pass applied:** 2026-04-20 (this commit) — `revision_applied: true` set in frontmatter

### Concerns (embedded as Implementation Notes above)

The five concerns have been addressed inline at the relevant plan sections via HTML-commented `<!-- Implementation Note (Cn): ... -->` blocks so the builder encounters them while reading the plan top-to-bottom. Summary:

| # | Concern | Embedded at |
|---|---------|-------------|
| C1 | Gate-condition precision — plan's "session_id is None-at-this-point-is-not-the-right-check" wording is ambiguous; gate must be expressed via correct handler placement rather than a misleading None check | Task 1 (Step by Step Tasks) |
| C2 | Idempotency flag has three outcomes (success/empty/failed), not two — empty chain must NOT stamp the flag to avoid silently short-circuiting worker-side retry | Solution / Key Elements |
| C3 | Kill-switch truthy set must mirror the sibling `REPLY_CONTEXT_DIRECTIVE_DISABLED` parsing exactly; builder must grep for the sibling pattern before writing the new check | Task 1 (Step by Step Tasks) |
| C4 | Semantic routing + steering + resume-completed branches must all be settled before the pre-fetch block; placement is the only correctness mechanism, so the builder must document the three dependent line numbers in a code comment | Risks / Risk 4 |
| C5 | Test extension must prefer `@pytest.mark.parametrize` over copy-paste to prevent drift; only fall back to sibling tests when fixtures genuinely conflict | Test Impact |

### Nits (Informational — NOT blocking build)

These were flagged at NIT severity and are recorded here for reviewer visibility. They do NOT require plan edits to proceed to build; they can be addressed in the PR body or closed as "acknowledged, no action" at merge time.

- **N1** — Open Questions #1 ("Should `REPLY_CHAIN_PREHYDRATION_DISABLED` be added to `.env.example`?") — the precedent (`REPLY_CONTEXT_DIRECTIVE_DISABLED` not in `.env.example`) is clear. The question can be closed at PR review as "no, mirror the precedent."
- **N2** — Open Questions #3 (emit `fresh_reply_chain_prehydrated` INFO log on empty chain?) — low-risk observability choice. Can be decided at build time; default to "yes, but with a `chain_len=0` field so the two cases are filterable in logs."
- **N3** — The "Assigned To" / "Agent Type" structure in the Team Orchestration section is standard tier-1 and could be simplified, but this is a stylistic preference and not worth plan churn.

### Structural Checks

All PASS (Required sections present, Task numbering contiguous, Dependencies resolvable, File paths verified against the worktree, Cross-references to PR #953 and issue #949 confirmed).

### Revision Pass Integrity Note

The raw concern bodies from the `/do-plan-critique` subagent run were not persisted outside the stage_states verdict summary (only the top-line verdict and artifact hash were captured). The Implementation Notes above are derived from a careful re-read of this plan against the verdict semantics ("READY TO BUILD with concerns" — non-blocking risks that warrant mid-flight guidance). They target the five highest-risk mid-flight pitfalls a Sonnet builder could hit: handler placement ambiguity, flag semantics on empty chain, env-var parity with sibling kill-switch, control-flow settlement before pre-fetch, and test-drift from copy-paste. If the original critique concerns differ materially from these five, the builder should surface the gap in PR review rather than silently correcting it — the Critique History is the source of truth for what was actually flagged.

---

## Open Questions

1. **Should `REPLY_CHAIN_PREHYDRATION_DISABLED` be added to `.env.example`?** PR #953's `REPLY_CONTEXT_DIRECTIVE_DISABLED` was NOT added to `.env.example`, so the precedent is "off-by-default, undocumented kill-switch." Confirming this is still the right call for a new sibling var.
2. **Is there a scenario where we want to *skip* pre-hydration based on chat type (e.g., DMs vs. groups)?** The incident that prompted the issue was a group chat. DMs have fewer reply-to-non-self cases. Currently the plan applies uniformly to all chat types. Worth confirming uniform is correct.
3. **Should the `fresh_reply_chain_prehydrated` INFO log also be emitted when the chain is empty (successful fetch, zero messages)?** Leaning yes for observability parity, but the log should clearly distinguish empty-chain from non-empty-chain cases.
