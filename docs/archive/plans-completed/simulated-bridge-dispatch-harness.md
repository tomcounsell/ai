---
status: Ready
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-07-18
tracking: https://github.com/tomcounsell/ai/issues/2159
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-07-27T04:10:55Z
---

# Simulated Bridge Dispatch Harness

Extract the transport-agnostic dispatch decision (steer running / steer pending / resume completed with context / new session) out of the Telethon handler closure into an importable function driven by a plain dataclass, and add a simulated-bridge integration harness that drives multi-turn reply-to scenarios end-to-end against test Redis.

## Problem

The bridge's dispatch decision has regressed repeatedly (#567 → #919 → #949 → #1064 → #1836 → #2136) because it lives as inline control flow inside `async def handler(event)` (`bridge/telegram_bridge.py:1156`), a ~900-line closure over the live Telethon client. No test can import it, so every fix is verified only in production, and every refactor silently re-breaks one of the multi-turn invariants.

**Current behavior:**
- The steer/pending-steer/resume-completed decision (`bridge/telegram_bridge.py:1782–2035`) and the coalescing guards (`:1650`, `:2043+`) are unreachable from tests. Only their leaf primitives (dedup, steering push, enqueue, context builders) have coverage.
- Four multi-turn invariants have zero end-to-end tests: reply resumes the original session; reply mid-run becomes steering; reply after completion resumes with prior context (including the live-session re-check race at `:1852`); rapid follow-ups coalesce.

**Desired outcome:**
- The decision is an importable function taking a plain inbound-message dataclass plus injected transport ports — no Telethon types in its signature or module.
- A simulated-bridge harness in `tests/integration/` scripts multi-turn message sequences through the real decision + real Redis (isolated test DB) and asserts the four invariants.
- The Telegram handler shrinks to parsing + one call; behavior is bit-for-bit preserved and pinned by the new scenario tests.

## Freshness Check

**Original baseline commit (plan time, 2026-07-18):** `ab6e517374519c8d2379df95dd10a9d6f4660d5e`
**Re-verified baseline commit (2026-07-27):** `66a433bd9be9c46b965aeb01434f81e9888e3d84`
**Issue filed at:** 2026-07-18T15:02:36Z
**Disposition:** Minor drift — line numbers shifted, all claims still hold. Structure of the dispatch decision is intact; no root-cause change.

**File:line references re-verified (2026-07-27, corrected below):**
- `bridge/telegram_bridge.py:1156` (was :1152) — `async def handler(event)` closure start — still holds
- `bridge/telegram_bridge.py:1782–2035` (was :1755–2000) — reply-to steer/pending/resume-completed branch — still holds (all landmarks present)
- `bridge/telegram_bridge.py:1852–1900` (was :1826–1871) — live-session re-check guard (`:1852`) + dedup short-circuit (`:1890–1892`) inside resume-completed — still holds
- `bridge/telegram_bridge.py:1985` — `dispatch_telegram_session` call in resume path; `:2003` `_steering_session_enqueued = True` sentinel; `:2010`/`:2021` the two broad exception handlers — still holds
- `bridge/telegram_bridge.py:791` (was :787) — `_build_completed_resume_text` module-level — still holds
- `bridge/telegram_bridge.py:894` (was :890) — `_ack_steering_routed` — still holds
- `bridge/telegram_bridge.py:1650` (was :1617–1623) / `:2043–2050` (was :2002+) — `_recent_session_by_chat` coalescing guard set/read — still holds
- `bridge/context.py:536` — `resolve_root_session_id(client, chat_id, reply_to_msg_id, project_key)` — **unchanged**
- `bridge/dispatch.py:84` — `dispatch_telegram_session` claim→enqueue→dedup wrapper — **unchanged**

**Cited sibling issues/PRs re-checked:** #2136 closed 2026-07-17 (goal re-injection on resume — merged; its `_build_completed_resume_text` path is part of what this harness pins). #2147 (test/live notify isolation) open, plan on main — adjacent, not blocking.

**Commits on main since issue was filed (touching referenced files):** four, all behavior-preserving relative to the dispatch decision.
- `af68a92bb` (Fix: durable stale-replay guard in live Telegram handler) — **inserted 29 lines at `:1189`**, an intake pre-filter using the `LastProcessedRecord` high-water cursor. This is upstream of the reply-to branch in the handler preamble; it is what shifted the branch down ~27 lines. It does **not** touch the steer/resume/new decision. Note for the builder: this guard stays in the handler (it is transport/intake plumbing, not the extracted decision).
- `b88d38aee` (Fix catchup/reconciler re-enqueuing #2204/#2298) — added ~25 lines at `:3080` (edit-handler region, downstream of the reply-to branch) — irrelevant to the extracted decision.
- `7ed56b8bd` (Redis MISCONF Sentry fanout) and `f69d243ad` (⚠ reaction when no worker alive #1312/#2196) — Sentry-noise/reaction changes, no impact on the dispatch decision.

**Active plans in `docs/plans/` overlapping this area:** `test-suite notify isolation` (#2147) touches test-suite/worker isolation, not bridge dispatch — coordination signal only: our harness must follow whatever db-scoped notify convention it lands.

## Prior Art

- **#567**: Reply-to should resume original AgentSession — introduced reply-based session continuity.
- **#919 / #949 / #1064**: split sessions, missing thread history on reply-to — added `resolve_root_session_id` chain walking and reply-chain context.
- **#997**: duplicate enqueue on reply-chain timeout — added the `_steering_session_enqueued` sentinel (`bridge/telegram_bridge.py:1786`).
- **#318 / #705 / #449**: semantic routing into active sessions; in-memory coalescing guard for rapid-fire messages (`_recent_session_by_chat`).
- **#730**: terminal-status guard on intake path (prevents completed→superseded cycling).
- **#1836 / #2136**: reply-to drops and goal-less resumes — latest recurrences, both fixed inline in the closure.
- **#948**: a reply-chain branch that missed its `record_message_processed` call produced a duplicate AgentSession — **the origin of the AST contract test** (`tests/unit/test_bridge_dispatch_contract.py`, docstring). The banned-enqueue/dedup-ordering guards Task 2 extends exist because of this bug; the extraction must not let them go vacuous (Risk 2).
- **#963**: reconciler re-dispatched steered messages as duplicates — the origin of the `TestSteeringPathsRecordDedup` push→dedup-before-return invariant that now moves into `steering_push_and_dedup` (Test Impact).
- **#1215**: media-in-steering — added the `process_incoming_media` rewrite + eyes-reaction + attachment-ingest inside `_ack_steering_routed`; this is the fourth Telethon touch spike-1 keeps wrapper-side (Option B).
- **#1574**: `--await-reply` live E2E probe — tests the real bridge over a live Telegram connection; not CI-runnable, complements (does not replace) this harness.
- **`tests/unit/test_bridge_dispatch_contract.py`**: AST-level guards (origin #948/#963) asserting the handler contains no direct enqueue/dedup calls outside `dispatch_telegram_session`, and every `push_steering_message` is followed by dedup before return — static shape checks this plan must keep passing (and keep non-vacuous — Risk 2).

## Research

No relevant external findings — purely internal refactor + test harness; proceeding with codebase context. (Phase 0.7 skipped per skill rule: no external libraries, APIs, or ecosystem patterns involved.)

## Spike Results

### spike-1: Extraction boundary of the reply-to decision branch
- **Assumption**: "The steer/pending/resume-completed decision reads only dataclass-expressible inputs plus Redis, with Telethon needed solely for side effects."
- **Method**: code-read (`bridge/telegram_bridge.py:1490–2020` read in full at plan time)
- **Finding**: Confirmed. The branch reads scalars already computed by the handler (`is_reply_to_valor`, `message.reply_to_msg_id`, `message.id`, `message.date`, `event.chat_id`, `session_id`, `project_key`, `project` dict, `chat_title`, `sender_name`, `sender_id`, `clean_text`, `safe_clean_text`, `stored_msg_id`) plus `AgentSession.query` and `is_duplicate_message`. The Telethon `client`/`event` objects are touched by **THREE** effects the branch invokes directly, all transport side-effects with no return value the decision reads:
  1. `_ack_steering_routed(client, event, message, …)` at `:1804`/`:1837`/`:1870` — the steering-routed ack. Its steering-push + dedup + chat-log tail is transport-agnostic; the reaction is Telethon; **and it contains a FOURTH Telethon touch nested one level down — see below.**
  2. `fetch_reply_chain(client, event.chat_id, message.reply_to_msg_id)` at `:1920` — reply-thread hydration in the resume path.
  3. `react_if_worker_down(client, event.chat_id, message.id, session_id)` at `:1984` (`bridge/response.py:386`) — the #1312 ⚠ reaction fired immediately before the resume dispatch when this machine's worker is not alive. **This is the effect a two-port design would silently drop.** A verbatim move that left this as a direct `react_if_worker_down(client, …)` call would drag the Telethon client into the "Telethon-free" module and break the `grep -ci telethon == 0` criterion.
  These three are injectable ports (`ack_steer`, `fetch_reply_chain`, `notify_worker_down`).
- **FOURTH Telethon touch (media rewrite, #1215) — inside `_ack_steering_routed`, not the branch.** `_ack_steering_routed` (`bridge/telegram_bridge.py:894–997`) is NOT a pure transport tail: when `message.media` is set (`:926`) it calls `await process_incoming_media(client, message)` at `:941` to rewrite the steering `text` (media → extracted description, or `description + caption`) BEFORE `push_steering_message` at `:969`, fires the pre-download "eyes" reaction (`:931`), and schedules fire-and-forget attachment ingest (`:962`). So the "steering push + dedup + chat-log core" the plan moves into the Telethon-free module is *downstream* of a client-typed call. Moving that core verbatim would either drag `process_incoming_media(client, …)` into `bridge/intake_decision.py` (failing `grep -ci telethon == 0`) or silently drop media→text rewriting for steered media messages. **Resolution (Option B — media stays wrapper-side): the media rewrite, eyes reaction, and ingest scheduling stay entirely inside the Telethon wrapper `_ack_steering_routed`; the wrapper computes the already-enriched `text` and `is_abort`, then delegates ONLY the transport-agnostic tail (`push_steering_message` + `record_telegram_message_handled` + chat-log) to a moved module-level `steering_push_and_dedup(...)` core in `bridge/intake_decision.py`.** The fourth touch is resolved by *never crossing the module boundary* — `process_incoming_media` is referenced only in `telegram_bridge.py`, so IntakePorts stays at three effect ports + enqueue and no media-enrichment port is added. `route_reply_intake` reaches this whole sequence through the single `ack_steer` port (Telegram binds it to `_ack_steering_routed`; tests bind a recorder).
- **Confidence**: high
- **Impact on plan**: the decision extracts cleanly behind an `InboundMessage` dataclass (which must carry `session_id`, the resolved session identifier the whole branch keys on) + **three** injected callables (`ack_steer`, `fetch_reply_chain`, `notify_worker_down`) plus the `enqueue` port; the media rewrite is kept wrapper-side (Option B) so no fourth port and no fake Telethon layer are needed. The transport-agnostic `steering_push_and_dedup` core moves into the module and is the new home of the #963 push→dedup invariant.

### spike-2: `resolve_root_session_id` cache path works without a client
- **Assumption**: "The Redis-cache walk (Steps 0–1) never touches the Telethon client; only the API fallback does."
- **Method**: code-read (`bridge/context.py:536`+, corroborated by recon for #2159)
- **Finding**: Confirmed — the client is used only in the Step-2 API fallback via `fetch_reply_chain`. An optional `client=None` short-circuit (cache-only mode) is a minimal additive seam.
- **Confidence**: high
- **Impact on plan**: harness drives the cache path with seeded `TelegramMessage` records; no fake client required.

## Data Flow

1. **Entry point**: Telegram message arrives → Telethon `handler(event)` parses text, media flags, sender, computes `is_reply_to_valor`, resolves `session_id` (reply chain → `resolve_root_session_id`; else semantic routing → fresh ID).
2. **Intake decision (extracted by this plan)**: `route_reply_intake(msg: InboundMessage, ports: IntakePorts)` — checks `AgentSession` by (session_id, status) in order running/active → pending → completed; applies the live re-check guard, dedup short-circuit, and #997 sentinel.
3. **Steering path**: `route_reply_intake` calls the `ack_steer` port. Telegram binds it to the `_ack_steering_routed` wrapper, which does media rewrite (`process_incoming_media`, #1215) + eyes/received/abort reactions + ingest scheduling entirely wrapper-side, then delegates the transport-agnostic tail — `push_steering_message` (`agent/steering.py:37`) + `record_telegram_message_handled` dedup + chat-log — to the moved module-level `steering_push_and_dedup(...)` core in `bridge/intake_decision.py`. Tests bind `ack_steer` to a recorder. No media-enrichment port: `process_incoming_media` never enters the Telethon-free module (Option B, spike-1).
4. **Resume path**: `fetch_reply_chain` port → `format_reply_chain` → `_build_completed_resume_text` → `notify_worker_down` port (#1312 ⚠ if this machine's worker is down; wrapped in its own fail-quiet try/except at the call site so a raising port can never divert the resume to the new-session fall-through) → `dispatch_telegram_session` (`bridge/dispatch.py:84`: claim → enqueue → dedup record).
5. **New-session path**: terminal-status guard (#730) → in-memory coalescing guard (#705) → `dispatch_telegram_session`.
6. **Output**: `AgentSession` in Redis queue → standalone worker executes; steering messages drained at turn boundary.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #567 | Made reply-to resume the original session | Logic landed inline in the closure; next routing refactor (#919) split sessions again |
| #919/#949/#1064 | Root-ID resolution + thread history on resume | Primitives got unit tests but the decision consuming them stayed untested; #1836 re-broke delivery |
| #1836 | Fixed silent drops + resume for granite sessions | Fixed at the classifier/session layer; the closure's decision remained unpinned |
| #2136 | Re-injected goal/context on resume | Correct fix, again inline; nothing prevents the next refactor from dropping it |

**Root cause pattern:** every fix patches inline closure logic that no test can import. The fix layer is right; the missing piece is an importable seam plus scenario tests that make regressions loud at PR time. This plan adds the seam and the tests rather than another behavior patch.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: new module `bridge/intake_decision.py` (dataclass + ports + decision function + the transport-agnostic `steering_push_and_dedup` core); `resolve_root_session_id` gains an optional cache-only mode (`client=None`) — additive, default behavior unchanged; `_ack_steering_routed` keeps its current signature and its media-rewrite/reaction body (Option B) but delegates its push→dedup→chat-log tail to `steering_push_and_dedup`.
- **Coupling**: decreases — the decision no longer closes over the Telethon client; email convergence (#2160) becomes possible later.
- **Data ownership**: unchanged — AgentSession/steering/dedup Redis keys keep their owners.
- **Reversibility**: high — behavior-preserving extraction; reverting restores the inline branch.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (open-questions round, pre-build confirmation)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running locally | `redis-cli ping` | Integration tests use the isolated test DB via `redis_test_db` fixture |

## Solution

### Key Elements

- **`InboundMessage` dataclass** (`bridge/intake_decision.py`): the transport-agnostic projection of an inbound message — text, safe_text, chat_id, message_id, reply_to_msg_id, is_reply_to_valor, sender_name, sender_id, chat_title, message_ts, project_key, project config, stored_msg_id, and **`session_id`** (the resolved session identifier the whole reply-to branch keys on — resolved upstream by the handler via reply-chain walk or semantic routing; the decision never re-resolves it). The `project_name` label used only in log strings is derived from `project`, not a separate field.
- **`IntakePorts`**: the **three** injected transport effects plus the enqueue seam —
  - `ack_steer(...)` — the whole steering-routed ack (Telegram binds it to `_ack_steering_routed`; tests supply a recorder). Under Option B (spike-1) this port owns the *entire* Telethon side of steering: media rewrite (`process_incoming_media`, #1215), eyes/received/abort reactions, and attachment-ingest scheduling — none of which cross into the module. The wrapper computes the enriched `text` + `is_abort` and then calls the transport-agnostic `steering_push_and_dedup(...)` core (see below); `route_reply_intake` reaches all of it through this one port.
  - `fetch_reply_chain(chat_id, reply_to_msg_id)` — Telegram wraps the Telethon call; tests return canned chains (or raise, to exercise `RESUME_REPLY_CHAIN_FAIL`).
  - `notify_worker_down(chat_id, message_id, session_id)` — the #1312 ⚠ worker-down reaction (Telegram binds `react_if_worker_down`, which is itself fail-quiet — `bridge/response.py:401`; tests record the call). Fired unconditionally before the resume dispatch; **must not** be dropped, or the operator loses the "no worker alive" signal on resume. **`route_reply_intake` wraps the `notify_worker_down` call in its own try/except** so that even a raising port (or a non-fail-quiet future binding) logs-and-continues to the resume dispatch rather than propagating to the branch's broad `except`, which would divert the message to the new-session fall-through (a resume silently downgraded to a fresh session — the CONCERN a bare "still enqueued" assertion would miss). A no-op default keeps non-Telegram callers simple.
  - `enqueue(...)` — defaults to `dispatch_telegram_session`.
- **`steering_push_and_dedup(session_id, text, sender_name, is_abort, chat_id, message_id)`** (`bridge/intake_decision.py`): the transport-agnostic tail moved out of `_ack_steering_routed` — `push_steering_message` then `record_telegram_message_handled` (dedup) then chat-log, in that order. This is the new lexical home of the #963 push→dedup invariant, so the AST steering-dedup contract repoints here (see Test Impact). It receives already-enriched `text` from the wrapper; it never touches Telethon.
- **`route_reply_intake(msg, ports) -> RouteResult`**: the extracted decision — running/active steer → pending steer → completed resume (live re-check guard, dedup short-circuit, chain hydration, `_build_completed_resume_text`, dispatch, #997 sentinel semantics) → signal fall-through. Returns a small result enum (`STEERED_LIVE | STEERED_PENDING | STEERED_LIVE_GUARD | RESUMED_COMPLETED | DUPLICATE_SKIPPED | FALL_THROUGH`) for logging and test assertions.
- **Coalescing guard extraction**: the `_recent_session_by_chat` in-memory guard becomes a small `RecentSessionGuard` class with an injectable clock. **Critical invariant: coalescing works only if one single guard instance backs every access site.** Today the module-level dict is written at `bridge/telegram_bridge.py:1650` and `:2345` (two SET sites, after a session is created for a chat) and read at `:2043–2050` (the coalescing check before a new-session dispatch). The extraction must replace all three with method calls on **one shared module-level `RecentSessionGuard` instance** — not a fresh instance per call site and not one instance in `route_reply_intake` and another in the handler. If the read and the sets end up on different instances the guard silently never coalesces (the exact #705 regression). Tests inject a frozen clock into that same instance. **Because it is a module-level singleton, its dict persists across tests and would leak coalescing state between them; the harness ships a per-test `autouse` fixture that clears the shared `RecentSessionGuard` instance (and resets its injected clock) before every test** so no test inherits another's recent-session entries.
- **Simulated-bridge harness** (`tests/integration/test_simulated_bridge_dispatch.py` + a `SimulatedBridge` helper): constructs `InboundMessage` sequences, seeds `AgentSession`/`TelegramMessage` records in the test Redis, runs the real decision with recording ports, and asserts session counts, steering-queue contents, and resume-text content. **The helper exposes a deferred-write hook** (`SimulatedBridge.defer_session_write()` context / an `enqueue` recording-port mode that records the intended write but withholds the actual `AgentSession` Redis save until released) so Race 3 can reproduce the real write-in-flight window — message 1's session is enqueued-but-not-yet-queryable while message 2 runs its status lookup (see Race 3).

### Flow

**Inbound message (any transport)** → handler parses into `InboundMessage` → `route_reply_intake` consults AgentSession state → **steer** (push + dedup + ack port) / **resume** (chain port + context build + dispatch) / **fall through** → new-session path with terminal + coalescing guards → **AgentSession enqueued for worker**.

### Technical Approach

- Extraction is **behavior-preserving**: move the branch at `bridge/telegram_bridge.py:1782–2035` verbatim into `route_reply_intake`, replacing **all three** direct Telethon touches with port calls — `_ack_steering_routed(client, event, message, …)` → `ports.ack_steer(...)`, `fetch_reply_chain(client, …)` → `ports.fetch_reply_chain(...)`, and `react_if_worker_down(client, …)` at `:1984` → `ports.notify_worker_down(...)` (wrapped in a call-site try/except, see below). Missing the third leaves a `client`-typed call in the module and fails the Telethon-free criterion. Preserve the #997 sentinel semantics (on port/Redis exceptions after dispatch, do not fall through), status-check order, the `max(created_at)` completed-record selection, and the `reply_chain_hydrated` extra-context flag. Note: the durable stale-replay guard added at `:1189` (commit `af68a92bb`) is intake plumbing upstream of this branch — leave it in the handler.
- **`notify_worker_down` call-site try/except:** wrap `ports.notify_worker_down(...)` so a raising port logs-and-continues to the resume dispatch. In production `react_if_worker_down` is already fail-quiet (`bridge/response.py:401`), so this is a no-op for the live binding; it exists to guarantee the module never lets a signal-only port divert a resume to the new-session fall-through — the difference a bare "still enqueued" test can't see (Failure Path Test Strategy asserts the result is `RESUMED_COMPLETED`, not merely enqueued).
- **Split `_ack_steering_routed` per Option B (spike-1):** the media rewrite (`process_incoming_media`, #1215), the eyes/received/abort reactions, and attachment-ingest scheduling ALL stay inside the Telethon wrapper `_ack_steering_routed` (`bridge/telegram_bridge.py:894–997`). The wrapper computes the enriched `text` + `is_abort`, then calls the transport-agnostic `steering_push_and_dedup(session_id, text, sender_name, is_abort, chat_id, message_id)` core (moved into `bridge/intake_decision.py`: `push_steering_message` → dedup → chat-log). `route_reply_intake` reaches steering only through `ports.ack_steer(...)` (Telegram binds `_ack_steering_routed`). `process_incoming_media` is referenced only in `telegram_bridge.py`, so the module stays Telethon-free (`grep -c process_incoming_media bridge/intake_decision.py == 0`); no media-enrichment port is added and IntakePorts stays at three effect ports + enqueue.
- `resolve_root_session_id` gains `client: TelegramClient | None` — `None` skips the Step-2 API fallback (cache-only). Harness seeds the Redis message cache instead of faking Telethon.
- Handler keeps: event parsing, media enrichment, reactions, revival replies, semantic routing (#318) — all Telegram-specific, out of scope.
- Harness respects the #2147 notify-isolation conventions (db-scoped fixtures already standard in `tests/integration/`).
- Follow the transport-keyed callback convention (`docs/sdlc/do-plan.md`) for any port registration.
- Blast radius (hand-traced; `tools.code_impact_finder` timed out at plan time): modify `bridge/telegram_bridge.py`, `bridge/context.py`, `bridge/dispatch.py` (or new `bridge/intake_decision.py`); tests `tests/unit/test_bridge_dispatch_contract.py`; add `tests/integration/test_simulated_bridge_dispatch.py`; docs `docs/features/`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The extracted branch keeps its two broad handlers (`ConnectionError/OSError` at `:2010` and `Exception` at `:2021`, `bridge/telegram_bridge.py`) — each gets a scenario test asserting the observable behavior: post-dispatch exception → no second enqueue (#997 sentinel); pre-dispatch exception → fall-through to the new-session path with an ERROR log.
- [ ] Reply-chain fetch timeout/exception (`RESUME_REPLY_CHAIN_FAIL`) → test asserts resume still dispatches with summary-only preamble and a WARNING.

### Empty/Invalid Input Handling
- [ ] `InboundMessage` with empty/whitespace text follows the existing `--empty message--` normalization (asserted in harness setup, not re-implemented).
- [ ] Reply to a session_id with no AgentSession records in any status → falls through to new-session path (test).

### Error State Rendering
- [ ] Steering-ack port failure does not lose the steering message (push happens before ack; test asserts queue contents when ack port raises).
- [ ] `notify_worker_down` port fires exactly once before the resume dispatch and does not gate or divert the resume: test with a recording port asserts (a) the port was called with `(chat_id, message_id, session_id)`, and (b) with a port that **raises**, `route_reply_intake` returns **`RESUMED_COMPLETED`** and the resumed session (not a new one) is enqueued exactly once — proving the call-site try/except swallows the raise and continues to the resume dispatch rather than propagating to the branch's broad `except` and falling through to the new-session path. A bare "still enqueued" assertion is insufficient: it passes even when a resume is silently downgraded to a fresh session, which is the exact regression this test must catch. A companion case with a no-op port asserts the same `RESUMED_COMPLETED` result (#1312 semantics preserved, no work dropped).

## Test Impact

- [ ] `tests/unit/test_bridge_dispatch_contract.py` — UPDATE (load-bearing, not cosmetic): today `TestSteeringPathsRecordDedup.test_all_steering_paths_have_dedup_in_handler` and `TestBridgeDispatchContract.test_handler_contains_no_direct_banned_calls` locate their target via `_find_telethon_handler` (an `AsyncFunctionDef` named `handler` decorated `@<client>.on(...)`) and walk only that function. (Note: today the push→dedup invariant already lives one level down in `_ack_steering_routed`, not lexically in `handler`; the handler-scoped steering-dedup walk is *already* near-vacuous — the extraction makes this explicit and must not leave it so.) **After the extraction, `dispatch_telegram_session` moves into the plain module-level `route_reply_intake` and the `push_steering_message`→dedup pair moves into the plain module-level `steering_push_and_dedup` (`bridge/intake_decision.py`), so the handler-scoped walks match zero relevant calls and both guards pass VACUOUSLY — a false green.** The fix has three required parts, all in Task 2:
  1. **Add a `_find_module_function(tree, name)` locator** that resolves a plain (undecorated, non-`@on`) `FunctionDef`/`AsyncFunctionDef` by name at module top level of `bridge/intake_decision.py`.
  2. **Repoint the two walks at their new homes:** point the banned-enqueue walk at `route_reply_intake` (in addition to `handler`, which retains only the coalescing/new-session dispatch), and point the steering push→dedup-ordering walk at `steering_push_and_dedup` (the moved core — where `push_steering_message` and `record_telegram_message_handled` now co-locate). Option B keeps the media rewrite wrapper-side, so `steering_push_and_dedup` is Telethon-free and its push→dedup ordering is directly walkable.
  3. **Add positive-control assertions** so a mislocated or emptied target fails LOUDLY instead of passing on zero calls: assert the located `route_reply_intake` contains **≥1** `dispatch_telegram_session` call AND **≥1** `ack_steer` call (its steer branches), and the located `steering_push_and_dedup` contains **≥1** `push_steering_message` call AND **≥1** `record_telegram_message_handled` call, before running the dedup-ordering walk. If any locator returns None or a count is zero, the test must `assert`-fail with a message naming the extraction as the likely cause — never silently pass.
- [ ] `tests/integration/test_steering.py` — UPDATE (minor): `resolve_root_session_id` tests gain one case for `client=None` cache-only mode; existing cases unchanged (param is additive with a default).

No other existing tests affected — the extraction is behavior-preserving and all other coverage targets leaf primitives whose signatures do not change.

## Rabbit Holes

- **Faking Telethon** — do not build a FakeTelegramClient or fabricate Telethon event objects; the port seam + cache-only resolver mode makes them unnecessary. (Issue Recon explicitly dropped this.)
- **Email-bridge convergence** — tempting while touching the seam; it is #2160, not this plan.
- **Edit-handler steering** (`@client.on(events.MessageEdited)` at `bridge/telegram_bridge.py:2548`) — independently re-implements steer; Telegram-specific, leave untouched.
- **Semantic routing extraction** (#318 branch at `:1526–1585`) — depends on `find_matching_session` LLM calls; pulling it into the harness drags in model mocking. Leave in the handler.
- **Refactoring the rest of the closure** — the handler has ~2,000 more lines of enrichment/reaction/revival logic; extract only the decision branch.

## Risks

### Risk 1: Extraction subtly changes behavior (the exact bug class this plan exists to stop)
**Impact:** A fifth regression in the reply-to lineage, self-inflicted.
**Mitigation — and an honest statement of what pins behavior.** The scenario tests are NOT the preservation gate, and the plan must not pretend otherwise. Most of them are `xfail` until `route_reply_intake` exists (they call a function that isn't there yet), so they cannot run against the current inline branch and cannot catch a change introduced *by* the extraction — a green post-extraction scenario test only proves "the new code satisfies the new test," not "the new code matches the old code." The actual behavior-preservation gate is **Task 3's line-by-line verbatim diff of the moved block against the original `:1782–2035`** (see Task 3): the reviewer/validator confirms every statement, branch order, and the three port substitutions (`ack_steer`, `fetch_reply_chain`, `notify_worker_down`) are a mechanical rename of the original — no reordered status checks, no dropped `notify_worker_down`, no altered `max(created_at)` selection or `_steering_session_enqueued` sentinel handling. The scenario tests are the *regression tripwire going forward* (they make the next refactor loud); the diff is the *equivalence proof for this refactor*. Both are required; neither substitutes for the other.

### Risk 2: AST contract guards silently stop guarding the moved code (false green)
**Impact:** The greater danger is not a failing test but a **vacuously passing** one — once `push_steering_message`/`dispatch_telegram_session` move into `route_reply_intake`, the handler-scoped walks find nothing and the steering-dedup contract passes on zero calls, so the invariant it exists to protect is no longer enforced anywhere.
**Mitigation:** Task 2 repoints the walks via a new `_find_module_function` locator — banned-enqueue at `route_reply_intake`, push→dedup-ordering at `steering_push_and_dedup` (its Option-B home) — AND adds positive-control assertions (≥1 `dispatch_telegram_session` + ≥1 `ack_steer` in `route_reply_intake`; ≥1 `push_steering_message` + ≥1 `record_telegram_message_handled` in `steering_push_and_dedup`) so a mislocated/emptied target fails loudly. Verification includes both the contract test file and a `grep` that the `_find_module_function` locator is present.

### Risk 3: Harness couples to Popoto/Redis internals and rots
**Impact:** Tests break on unrelated model changes, get skipped, blind spot returns.
**Mitigation:** Harness only uses public seams: Popoto ORM models, `push_steering_message`/`pop_all_steering_messages`, `dispatch_telegram_session`, and the new ports. No raw Redis (enforced repo-wide by the no-raw-redis hook).

## Race Conditions

### Race 1: Completed-resume vs concurrently created live session
**Location:** `bridge/telegram_bridge.py:1852` (guard re-check; moves into `route_reply_intake`)
**Trigger:** Two rapid replies to a completed session; the first re-enqueues (pending) while the second is between its status checks.
**Data prerequisite:** Live-guard re-check must run against current Redis state after the completed lookup.
**State prerequisite:** Guard order pending→running→active preserved.
**Mitigation:** Preserved verbatim; scenario test injects a live record via a port hook between resolution and dispatch and asserts the result flips to `STEERED_LIVE_GUARD`.

### Race 2: Rapid-fire duplicate replies to the same completed session
**Location:** dedup short-circuit `bridge/telegram_bridge.py:1890–1900`
**Trigger:** Same (chat_id, message_id) processed twice before Redis dedup write completes.
**Data prerequisite:** `is_duplicate_message` checked before reply-chain fetch.
**Mitigation:** Preserved; test asserts second identical message returns `DUPLICATE_SKIPPED` with exactly one enqueue.

### Race 3: In-memory coalescing window vs Redis visibility (#705)
**Location:** `_recent_session_by_chat` set at `:1650` and `:2345`, read at `:2043–2050`
**Trigger:** Two non-reply messages <200ms apart; the second must see the first's session before the first's Redis write lands.
**Data prerequisite:** Guard dict entry set before any await on the enqueue path.
**Mitigation:** single shared `RecentSessionGuard` with injectable clock, plus a per-test `autouse` reset fixture (Solution) so no test inherits another's guard entries.
**Test must exercise the real first-empty-then-populated window, not just clock arithmetic.** The whole point of the in-memory guard is to bridge the gap where the AgentSession Redis write from message 1 is not yet visible when message 2 runs its status lookup. A test that seeds Redis with message 1's session and then fires message 2 proves nothing — the Redis lookup would succeed on its own and the in-memory guard is never load-bearing.
**Mechanism (required — the deferred-write hook):** the SimulatedBridge helper exposes a deferred-write mode (`SimulatedBridge.defer_session_write()` / an `enqueue` recording port that captures the intended write but withholds the actual `AgentSession` save until explicitly released). The test uses it to reproduce the gap: fire message 1 under a deferred write (the guard is set at `:1650`/`:2345` before the enqueue, but message 1's AgentSession is NOT yet persisted, so it is not queryable), then fire message 2 and assert its `AgentSession.query` for the chat returns **empty on the first look** while the shared guard still causes it to coalesce onto message 1's session — net exactly one session created. Drive this with the frozen clock keeping both messages inside the merge window; assert the second message produces zero new AgentSession rows and no second enqueue. Then release the deferred write to confirm message 1's session lands. A companion assertion advances the frozen clock past the window and confirms the guard correctly lets a third message create a fresh session. Without the deferred-write hook there is no way to open the write-in-flight window in a single-threaded test, so the hook is a required part of Task 1's harness.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2160] Email-bridge convergence on the extracted decision (steering support + shared dedup wrapper for `_process_inbound_email`) — filed as #2160, blocked on this plan merging. Anti-criterion in Verification: this PR must not touch `bridge/email_bridge.py`.

Everything else raised during recon (Telethon failure modes, edit-handler, semantic-routing extraction, full closure refactor) is a permanent anti-goal or rabbit hole documented above, not deferred work.

## Update System

No update system changes required — this is an internal refactor plus tests: no new dependencies, config files, launchd services, or migrations. No Popoto model changes (no `scripts/update/migrations.py` entry needed). Deployed machines pick it up via the normal `/update` git pull; the bridge must be restarted after deploy per the standard restart rule (already part of `/update`).

## Agent Integration

No agent integration required — this is a bridge-internal refactor and test harness. No new CLI entry point in `pyproject.toml [project.scripts]`, no MCP server or `.mcp.json` changes. The bridge continues to call the extracted function via direct Python import (`bridge/telegram_bridge.py` → `bridge/intake_decision.py`), which is one of the two sanctioned integration paths.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/simulated-bridge-dispatch-harness.md` — the decision seam (dataclass + ports), the RouteResult vocabulary, how to add a scenario test, and the boundary with the #1574 live probe
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/session-steering.md` — note the steering-routing core's new home
- [ ] Update `tests/README.md` — move `bridge/telegram_bridge.py` blind-spot entry to "partially covered" with a pointer to the harness

### Inline Documentation
- [ ] Module docstring on `bridge/intake_decision.py` mapping each branch to its origin issue (#567, #997, #730, #705, #2136)

## Success Criteria

- [ ] `route_reply_intake` importable with no Telethon types in `bridge/intake_decision.py`; all three transport effects are ports (`ack_steer`, `fetch_reply_chain`, `notify_worker_down`) and `InboundMessage` carries `session_id`; the media rewrite (`process_incoming_media`, #1215) stays wrapper-side (`grep -c process_incoming_media bridge/intake_decision.py == 0`) and `notify_worker_down` is wrapped in a call-site try/except so a raising port resumes rather than falling through to a new session
- [ ] Scenario: reply to a prior Valor message resolves to the original session and creates no second AgentSession
- [ ] Scenario: reply while session is running/active lands in the steering queue, not the session queue
- [ ] Scenario: reply after completion dispatches a resume whose message_text contains the prior goal/context; flips to steer when a live session appears mid-decision
- [ ] Scenario: two rapid messages coalesce into one session
- [ ] `test_bridge_dispatch_contract.py` guards extended via `_find_module_function` (banned-enqueue → `route_reply_intake`, push→dedup-ordering → `steering_push_and_dedup`), with positive-control assertions (≥1 `dispatch_telegram_session` + ≥1 `ack_steer` in `route_reply_intake`; ≥1 `push_steering_message` + ≥1 `record_telegram_message_handled` in `steering_push_and_dedup`) so neither guard can pass vacuously — all passing
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

- **Builder (intake-decision)**
  - Name: intake-builder
  - Role: Extract the decision module, port seams, resolver cache-only mode, guard class; keep contract tests green
  - Agent Type: builder
  - Resume: true

- **Test Engineer (harness)**
  - Name: harness-tester
  - Role: SimulatedBridge helper + scenario/race tests
  - Agent Type: test-engineer
  - Resume: true

- **Validator (dispatch)**
  - Name: dispatch-validator
  - Role: Verify behavior preservation, run full bridge test subset, check success criteria
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: harness-documentarian
  - Role: Feature doc, index, tests/README blind-spot update
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Pin current behavior
- **Task ID**: build-baseline-tests
- **Depends On**: none
- **Validates**: tests/integration/test_simulated_bridge_dispatch.py (create)
- **Informed By**: spike-1 (extraction boundary confirmed), spike-2 (cache-only resolver)
- **Assigned To**: harness-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Write the SimulatedBridge helper and the four scenario tests + three race tests against the CURRENT code, driving the smallest reachable seams (they will call the extracted function once it exists; initially mark the not-yet-reachable ones xfail with `# pending extraction` runtime-free decorators only)
- Seed helpers: AgentSession factory per status, TelegramMessage cache seeding for `resolve_root_session_id`
- **Deferred-write hook** (`SimulatedBridge.defer_session_write()` / an `enqueue` recording port that withholds the actual `AgentSession` save until released) — required for the Race 3 write-in-flight window (see Race 3)
- **Per-test `autouse` reset fixture** that clears the shared module-level `RecentSessionGuard` instance and resets its injected clock before every test, so guard state never leaks between tests (see Race 3 mitigation / Solution)

### 2. Extract the decision
- **Task ID**: build-intake-decision
- **Depends On**: build-baseline-tests
- **Validates**: tests/integration/test_simulated_bridge_dispatch.py, tests/unit/test_bridge_dispatch_contract.py, tests/integration/test_steering.py
- **Informed By**: spike-1, spike-2
- **Assigned To**: intake-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `bridge/intake_decision.py`: `InboundMessage` (incl. `session_id`), `IntakePorts` (`ack_steer`, `fetch_reply_chain`, `notify_worker_down`, `enqueue`), `RouteResult`, `route_reply_intake` (verbatim move of `:1782–2035`), replacing all three direct Telethon touches — `_ack_steering_routed`, `fetch_reply_chain`, `react_if_worker_down` — with port calls. Wrap the `ports.notify_worker_down(...)` call in its own try/except so a raising port continues to the resume dispatch instead of diverting to the new-session fall-through (CONCERN 1).
- **Option B for the media rewrite (spike-1 BLOCKER):** move ONLY the transport-agnostic tail of `_ack_steering_routed` into `bridge/intake_decision.py` as `steering_push_and_dedup(session_id, text, sender_name, is_abort, chat_id, message_id)` (`push_steering_message` → `record_telegram_message_handled` → chat-log). Keep the media rewrite (`process_incoming_media`, #1215), the eyes/received/abort reactions, and attachment-ingest scheduling inside the Telethon wrapper `_ack_steering_routed`, which computes the enriched `text` + `is_abort` and calls `steering_push_and_dedup(...)`. `route_reply_intake` reaches steering only via `ports.ack_steer(...)`; `process_incoming_media` must NOT appear in `bridge/intake_decision.py`.
- Add `client=None` mode to `resolve_root_session_id`; extract `RecentSessionGuard` as a single shared module-level instance backing every set AND read site
- Replace the handler branch with the single call; remove all xfail markers from Task 1 tests (convert to hard assertions)
- Extend AST contract guards to the new module: add `_find_module_function(tree, name)`; repoint the banned-enqueue walk at `route_reply_intake` and the steering push→dedup-ordering walk at `steering_push_and_dedup`; add positive-control assertions (≥1 `dispatch_telegram_session` AND ≥1 `ack_steer` in `route_reply_intake`; ≥1 `push_steering_message` AND ≥1 `record_telegram_message_handled` in `steering_push_and_dedup`) so extraction that mislocates or empties either target fails loudly instead of passing vacuously

### 3. Validate behavior preservation
- **Task ID**: validate-dispatch
- **Depends On**: build-intake-decision
- **Assigned To**: dispatch-validator
- **Agent Type**: validator
- **Parallel**: false
- **The verbatim diff IS the preservation gate (not the scenario tests — see Risk 1). It has TWO parts:**
  1. **The branch diff.** Extract the pre-extraction `:1782–2035` block from the parent commit (`git show <pre-extraction-sha>:bridge/telegram_bridge.py`) and diff it statement-by-statement against `route_reply_intake` in `bridge/intake_decision.py`. Confirm the ONLY differences are the three mechanical port substitutions — `_ack_steering_routed(client, event, message, …)` → `ports.ack_steer(...)`, `fetch_reply_chain(client, …)` → `ports.fetch_reply_chain(...)`, `react_if_worker_down(client, …)` → `ports.notify_worker_down(...)` (now inside a call-site try/except, CONCERN 1) — plus the `msg.`/`ports.` attribute rename. Explicitly verify NONE of the following drifted: status-check order (running→active→pending→completed), the `max(created_at)` completed-record selection, the `_steering_session_enqueued` #997 sentinel and both broad except handlers, the `reply_chain_hydrated` extra-context flag, and the unconditional `notify_worker_down`-before-enqueue ordering.
  2. **The `_ack_steering_routed` split diff (Option B).** Diff the pre-extraction `_ack_steering_routed` body (`:894–997`) against its post-extraction form. Confirm the media rewrite (`process_incoming_media`, #1215), the eyes/received/abort reactions, and ingest scheduling stayed wrapper-side byte-for-byte, and the ONLY change is that its `push_steering_message` → dedup → chat-log tail was replaced by a single call to `steering_push_and_dedup(...)` receiving the already-enriched `text` + `is_abort`. Confirm `steering_push_and_dedup` in `bridge/intake_decision.py` is a verbatim move of that tail with no Telethon reference (`grep -c process_incoming_media bridge/intake_decision.py == 0`).
  Record both diff verdicts (equivalent / drift found) in the report as the primary pass/fail signal.
- Then run `scripts/pytest-clean.sh tests/unit tests/integration -q`; confirm zero remaining xfails in the new test file; confirm the contract test's positive-control assertions are present and passing (both `route_reply_intake` and `steering_push_and_dedup` guards are non-vacuous); report pass/fail per success criterion

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-dispatch
- **Assigned To**: harness-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Feature doc, README index row, session-steering doc note, tests/README blind-spot update

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: dispatch-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows; verify all success criteria including docs; generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Scenario tests pass | `scripts/pytest-clean.sh tests/integration/test_simulated_bridge_dispatch.py -q` | exit code 0 |
| Contract guards pass | `scripts/pytest-clean.sh tests/unit/test_bridge_dispatch_contract.py -q` | exit code 0 |
| Full suite | `scripts/pytest-clean.sh tests/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No Telethon in decision module | `grep -ci "telethon" bridge/intake_decision.py` | match count == 0 |
| No raw client calls leaked into module | `grep -cE "react_if_worker_down\(client\|fetch_reply_chain\(client\|_ack_steering_routed\(client" bridge/intake_decision.py` | match count == 0 |
| Media rewrite stays wrapper-side (#1215) | `grep -c "process_incoming_media" bridge/intake_decision.py` | match count == 0 |
| Handler delegates to extracted fn | `grep -c "route_reply_intake" bridge/telegram_bridge.py` | output > 0 |
| Contract guard is not vacuous (positive control present) | `grep -c "_find_module_function" tests/unit/test_bridge_dispatch_contract.py` | output > 0 |
| No stale pending-extraction xfails | `grep -rn "pending extraction" tests/integration/test_simulated_bridge_dispatch.py \| wc -l` | match count == 0 |
| Anti-criterion: email bridge untouched (#2160 stays separate) | `git diff --name-only origin/main...HEAD \| grep -c "bridge/email_bridge.py"` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER 1 | critique | InboundMessage/IntakePorts under-enumerate the branch's inputs/effects — missing `session_id` and a `notify_worker_down` port for `react_if_worker_down` (THREE client effects, not two) | spike-1 finding, Solution (dataclass + ports), Data Flow step 4, Technical Approach | `InboundMessage` gains `session_id`; `IntakePorts` gains `notify_worker_down(chat_id, message_id, session_id)`; all three Telethon touches (`:1804/:1837/:1870`, `:1920`, `:1984`) become ports |
| BLOCKER 2 | critique | "Contract guards extended and passing" is false-green — handler-scoped walks match zero calls after the move and pass vacuously | Test Impact contract row, Risk 2, Task 2, Success Criteria, Verification | Add `_find_module_function` locator for `route_reply_intake`; repoint walks; add positive-control assertion (≥1 `push_steering_message` + ≥1 `dispatch_telegram_session`) |
| CONCERN 3 | critique | `RecentSessionGuard` must own both the SET (`:1650`, `:2345`) and READ (`:2043–2050`) on one instance or coalescing breaks | Solution (coalescing guard bullet), Task 2, Race 3 | Single shared module-level instance backs every set/read site |
| CONCERN 4 | critique | Frozen-clock Race 3 test doesn't exercise the real first-empty-then-populated Redis retry | Race 3 | Test reproduces the visibility gap: message 2's `AgentSession.query` returns empty on first look while the shared guard coalesces onto message 1 |
| CONCERN 5 | critique | Baseline-first pinning is hollow — invariants only turn green post-extraction; name Task 3's verbatim diff as the preservation gate | Risk 1, Task 3, Resolved Decision #3 | Verbatim `git show <pre-extraction-sha>` diff of `:1782–2035` vs `route_reply_intake` is the named equivalence gate; scenario tests are the forward tripwire |
| BLOCKER (2nd) | critique | `_ack_steering_routed` has a FOURTH Telethon touch — `process_incoming_media(client, message)` at `:941` (#1215) rewrites steering text BEFORE the push core the plan moves; a verbatim move drags a client call into the module or drops media→text rewrite | spike-1 (fourth-touch finding + Option B), Solution (`ack_steer` + `steering_push_and_dedup`), Data Flow step 3, Technical Approach (Option-B split), Task 2, Task 3 (split diff), Success Criteria, Verification (`process_incoming_media == 0`) | Option B: media rewrite + reactions + ingest stay inside the Telethon wrapper `_ack_steering_routed`; only the enriched-text push→dedup→chat-log tail moves into module-level `steering_push_and_dedup`. No fourth port; `process_incoming_media` never enters `bridge/intake_decision.py` |
| CONCERN (2nd) 1 | critique | A raising `notify_worker_down` port falls through to a NEW session (not resume); "still enqueued" assertion is ambiguous | Solution (`notify_worker_down` bullet), Data Flow step 4, Technical Approach (call-site try/except), Failure Path Test Strategy, Success Criteria | `route_reply_intake` wraps `notify_worker_down` in its own try/except (log-and-continue to resume); test asserts result is `RESUMED_COMPLETED` with one enqueue, not merely "enqueued" |
| CONCERN (2nd) 2 | critique | Race 3 test has no mechanism to open the write-in-flight window | Solution (SimulatedBridge deferred-write hook), Race 3 (Mechanism), Task 1 | SimulatedBridge `defer_session_write()` withholds message 1's AgentSession save so message 2's `AgentSession.query` returns empty on first look while the shared guard coalesces |
| CONCERN (2nd) 3 | critique | Prior Art omits #948 (origin of the AST contract test Task 2 extends) | Prior Art (#948, #963, #1215 added) | #948 (duplicate-session-from-missed-dedup) named as the contract test's origin; #963 (steering re-dispatch) and #1215 (media-in-steering) added for completeness |
| NIT (2nd) | critique | Module-level `RecentSessionGuard` singleton needs a per-test autouse reset fixture | Solution (coalescing guard bullet), Race 3 mitigation, Task 1 | Per-test `autouse` fixture clears the shared guard instance + resets its clock before every test |

---

## Resolved Decisions

The three planning open-questions are resolved with their recommended defaults (all engineering-judgment calls the plan body already builds on; no blocking business decision). Revisit at critique if any warrants challenge.

1. **Module home** → new `bridge/intake_decision.py`; `bridge/dispatch.py` stays the thin enqueue+dedup wrapper. (Recommended default; keeps the decision module Telethon-free and independently importable.)
2. **Fall-through scope** → keep minimal: extract the reply-to branch + coalescing guard only; the non-reply new-session assembly stays in the handler calling shared helpers. (Recommended default; halves the moved surface and the extraction risk, and does not preclude a later full-intake pass or email convergence #2160.)
3. **Baseline-first ordering** → keep baseline-first, but with an honest division of labor (see Risk 1 and Task 3): Task 1 writes the scenario tests against current behavior — the ones reachable through today's leaf seams run immediately; the ones that need `route_reply_intake` are `xfail # pending extraction` and turn green only after Task 2. These scenario tests are the **forward regression tripwire**, not the equivalence proof for this extraction (a green post-extraction test only shows the new code matches the new test). The **equivalence proof for this refactor is Task 3's verbatim diff** of the moved block against the original. Baseline-first still earns its keep: the reachable tests exercise real seams now, and writing all scenarios before the move forces the invariants to be named up front.
