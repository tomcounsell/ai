---
status: completed
type: bug
appetite: Large
owner: Valor Engels
created: 2026-08-10
tracking: https://github.com/tomcounsell/ai/issues/2716
last_comment_id: 5312083426
revised: 2026-08-17
revision_applied: true
revision_applied_at: 2026-08-17T05:24:00Z
---

# Session liveness tick counter (rewrite of the dead stall reaction)

## Problem

A human watching a long-running session cannot tell "still working" from "wedged" without asking. The one mechanism built to close that gap has never worked.

[#1313](https://github.com/tomcounsell/ai/issues/1313) added `_apply_stall_reaction` in [`monitoring/session_watchdog.py`](../../monitoring/session_watchdog.py), which queues a ⏳ reaction on the user's originating Telegram message when a session stalls. ⏳ is not a legal Telegram reaction — it is absent from the 74 the server advertises and is already listed in this repo's own `INVALID_REACTIONS`. Every attempt fails at the API:

```
2026-08-09T15:47:55Z WARNING bridge.telegram_relay _send_queued_reaction
Relay: failed to set reaction ⏳ on msg 1308 in chat -1003449100931
```

`logs/bridge.log` shows 11 `Stall reaction queued` events and zero landings. The dedup key is set on *enqueue*, not delivery, so a failure is never retried within that stall period.

Even repaired, a single static reaction is a one-shot signal: it cannot show time passing, and it never escalates — a session can sit behind it forever.

**Desired outcome:** a long-running session displays a visibly advancing counter, emitted by the watchdog, that guarantees a progress message from the PM at least every 100 minutes for any session still executing tool calls (see the conditionality note below).

### What the counter means (decided 2026-08-10)

This is the load-bearing semantic decision, and it is narrower than "liveness" in the colloquial sense. Read it before implementing anything.

- **The counter asserts that the watchdog has eyes on this session.** That is the whole claim. The watchdog is a process independent of the session, so a ticking counter is proof *the observer* is alive and watching — which is exactly the guarantee `bridge/liveness.py` says a self-report cannot provide.
- **The number is duration.** Nothing more. Tick 4 means "roughly 40 minutes since this counter started," not "4 units of progress" and not "4 checks passed."
- **It does NOT guarantee the session is unstalled.** A wedged session and a busy session tick identically. Any wording that implies stall detection is wrong and must not appear in this document or in the code comments.
- **The PM may take as long as it wants** working with eng sessions. Duration is not a failure condition.
- **The forcing function is the guarantee, for a session still executing tool calls.** At the ceiling the PM must publish a progress message — success, failure, or still-working — and a fresh counter anchors to that message. So the human is promised a substantive human-readable update at least every 100 minutes, and the counter is the visible countdown toward that promise.
- **The guarantee is conditional, and the condition is worth stating plainly.** Delivery is via steering, which `agent/steering.py:5` serves on a PostToolUse hook and drains at turn boundaries. A genuinely wedged session — no tool calls, no turn boundary — never receives the steer, and Race 3's `SET NX` marker then stays latched, so the human sees a frozen digit and no message. That is acceptable and arguably correct (a frozen counter is itself the wedge signal), but it is the one case the 100-minute promise does not cover. Task 4 must give it an explicit disposition rather than leaving the prose absolute.

This is why no evidence-freshness gating is required: the counter is not trying to infer the session's internal state. It measures wall-clock time and forces a periodic answer. The answer, not the digit, is where stall information actually surfaces.

## Freshness Check

**Disposition: Unchanged.** Re-verified 2026-08-17 against `origin/main` = `c23fb0e00`, seven days and ~10 SDLC-substrate merges after the original 2026-08-10 pass at `1e3fdd6f5`. Full evidence table in [issue comment 5312083426](https://github.com/tomcounsell/ai/issues/2716#issuecomment-5312083426). Every load-bearing claim still holds: `STALL_REACTION_EMOJI = "⏳"` (`session_watchdog.py:112`) still contradicts `INVALID_REACTIONS` (`response.py:86`); `_apply_stall_reaction` (`:578`), `_clear_stall_reaction_dedup` (`:674`), the `watchdog:stall_reaction_applied:` key (`:636`) and the `WATCHDOG_STALL_REACTION_ENABLED` gate (`:622`) are all intact; `build_custom_emoji_index`'s only non-test caller is `rebuild_custom_emoji_index`, itself test-only; `data/custom_emoji_embeddings.json` still does not exist. The single watchdog change since filing (`ac190fb26`, the #2642 steering flip) is 5 lines and touches no stall-reaction symbol. One detail went stale — branch `wip/session-heartbeat-ticker` (`78443c3f6`) is gone, unreachable, with no commit anywhere containing `PREMIUM_DIGIT_REACTIONS`; corrected in `10ebf7bbd` and reflected in Technical Approach / Task 2.

Original 2026-08-10 pass, still valid:

- Issue #2716 was filed 2026-08-10T08:55:49Z; `git log origin/main --since=<that>` returns no commits. All issue claims were verified against this exact tree during `/do-issue` recon, minutes before planning.
- [#2710](https://github.com/tomcounsell/ai/pull/2710) (`sdlc-stall-auto-resume`) merged shortly before filing and sounded adjacent. Verified with `git show --stat 6a9e2b66c`: it touches no watchdog, reaction, `response.py`, or emoji file. It handles stalled *SDLC runs*; this plan handles the *user-visible* signal. No collision.
- `monitoring/session_watchdog.py` last changed in `f69d243ad` (#1312, the ⚠ worker-down reaction) — the same reaction-slot surface this plan reorganizes, and it predates the issue. No drift.
- **Overlap surfaced:** [#2643](https://github.com/tomcounsell/ai/issues/2643) (`docs/plans/watchdog-import-time-log-handler.md`, status Ready, unbuilt) modifies the same file's logging setup. Disjoint concern (logging handler vs. reaction logic) but the same file — coordinate on merge order rather than treating it as a blocker.
- Bug reproduction: confirmed still present. `STALL_REACTION_EMOJI == "⏳"`, and `"⏳" in INVALID_REACTIONS` is `True` on current main.

## Prior Art

- [#1313](https://github.com/tomcounsell/ai/issues/1313) — added the ⏳ stall reaction. Shipped non-functional; this plan rewrites it.
- [#1312](https://github.com/tomcounsell/ai/issues/1312) / [#2196](https://github.com/tomcounsell/ai/pull/2196) — added the ⚠ worker-down reaction on the same originating message. Established the precedent of a second writer to the same slot, which this plan must now arbitrate.
- [#2663](https://github.com/tomcounsell/ai/issues/2663) — read-only session-progress CLI. Complementary pull interface to this push signal; both should read the same progress truth.
- [#2494](https://github.com/tomcounsell/ai/issues/2494) — durability refactor keying recovery on work owed rather than process status. Same principle, different surface.

## Research

Queries: Telegram `messages.sendReaction` rate limits / FLOOD_WAIT; chat-level `available_reactions` / `allow_custom` restrictions.

1. **Chat policy can reject custom emoji regardless of sender Premium.** `ChatReactions` has three constructors: `chatReactionsNone` (reactions off), `chatReactionsAll` with an `allow_custom` flag, and `chatReactionsSome` (explicit whitelist). Exposed as `available_reactions` on `chatFull`/`channelFull`. ([core.telegram.org/type/ChatReactions](https://core.telegram.org/type/ChatReactions))
   → **Informs approach:** do not probe chat policy before reacting. Attempt the custom digit and let the existing `set_reaction` fallback handle rejection. A pre-flight check adds an API round trip per tick and still races policy changes.

2. **No published per-method rate limit for `sendReaction`.** FLOOD_WAIT_X is the only feedback; it must be honored by sleeping X seconds. Bursts across many distinct chats are riskier than repeated reactions in one chat. ([MadelineProto FLOOD_WAIT](https://docs.madelineproto.xyz/docs/FLOOD_WAIT.html), [grammY flood limits](https://grammy.dev/advanced/flood))
   → **Informs approach:** the risk lands on the *relay*, not the watchdog — the watchdog writes only Redis and cannot flood-wait. See Risk 2 for the corrected analysis: `process_outbox` sleeps inline up to 300 s on FLOOD_WAIT, blocking all chats. One reaction per session per 10 minutes is negligible traffic, and a skipped tick is self-correcting, so this plan adds load without adding a mitigation and files the relay fix separately.

## Spike Results

No spikes required — every assumption that would have justified one was already resolved empirically against the live API during `/do-issue` recon:

- **Are keycap digits legal standard reactions?** No. Server advertises exactly 74; no keycaps in either encoding. `messages.getAvailableReactions`.
- **Is the account Premium?** Yes (`premium: True`).
- **Do digit custom emoji exist and are they reachable?** Yes — `Birthday Collection` (id `1901206392136531984`) is already installed and carries all ten digits.
- **Does a custom-emoji reaction actually set and replace in place?** Yes. Verified in Saved Messages: set 1️⃣, read back `ReactionCustomEmoji(document_id=...)`, replaced with 2️⃣ in the same slot. Test message deleted.

## Data Flow

```
session_watchdog.watchdog_loop()            [bridge process, every WATCHDOG_INTERVAL=300s]
  ├─ check_all_sessions()          [async; status="active" only — NOT our host]
  └─ _publish_liveness_ticks()     [NEW, sync — called like check_stalled_sessions()]
       └─ for each session in status in {running, active} with chat_id + telegram_message_id:
            elapsed = now - counter_anchor          # anchor = counter start, pure duration
            tick    = elapsed // HEARTBEAT_TICK_INTERVAL_SECONDS
            if tick > last_published_tick and tick <= HEARTBEAT_MAX_TICKS:
              └─ queue reaction payload (+priority field)
                   → redis LPUSH telegram:outbox:{session_id}
            elif tick > HEARTBEAT_MAX_TICKS and not ceiling_fired (SET NX):
              └─ steer PM to publish progress → agent/steering.py
                   → on new message: re-anchor counter to it

bridge/telegram_relay.process_outbox()          [drains ALL telegram:outbox:* keys]
  └─ reaction branch (line 922)
       ├─ DROP if session reached terminal status  (Race 1 guard)
       ├─ DROP if a higher-priority reaction owns the slot
       └─ set_reaction(client, chat_id, reply_to, EmojiResult)
            ├─ ReactionCustomEmoji(document_id) attempt
            └─ on failure → ReactionEmoji(standard glyph) fallback
```

**Host function (corrected).** The tick publisher does NOT belong in `check_all_sessions()`: that function is `async` and queries `status="active"` only (`session_watchdog.py:245,255`), while worker-executed Telegram sessions run at `status="running"`. Placing it there would blind the feature to exactly the sessions it exists for. The new publisher is **synchronous**, called from `watchdog_loop()` alongside `check_stalled_sessions()` (line 233, called without `await`), and needs its own guarded `try` block so a tick failure cannot take down the loop.

**Anchor (decided).** The anchor is *when the counter started* — session start, then re-anchored to each forced progress message. It is not "last observed progress evidence." Per the semantics above, the counter measures duration and makes no claim about the session's internal state, so no transcript-mtime or `updated_at` freshness probe is needed. This also sidesteps the hollow-session mirage entirely: there is no liveness inference to be fooled.

**Critical timing constraint:** `WATCHDOG_INTERVAL` is 300s but a tick is 600s. The tick number MUST be derived from elapsed wall clock (`elapsed // interval`), never incremented once per scan. An increment-per-scan implementation would advance at 2x the intended rate and would double-count on any loop restart.

## Why Previous Fixes Failed

#1313 failed for one reason and one reason only: **the emoji was never validated against the server's reaction set.** The repo already had the answer — `INVALID_REACTIONS` in `bridge/response.py` — and ⏳ was already in it when #1313 shipped. Nothing in the pipeline compared the new constant against that list.

Two secondary factors kept it invisible for months:
- The failure logs at WARNING in the *relay*, far from the watchdog code that caused it, so it never looked like a watchdog bug.
- The dedup key is set at enqueue time, so the system records "reaction applied" for a reaction that never landed. The bookkeeping asserts success independent of outcome.

Both are fixed structurally here: a test asserts every emittable glyph is legal, and the advance key reflects delivery.

## Architectural Impact

This plan's real weight is not the counter — it is establishing an **owner for the originating message's single reaction slot.**

Telegram permits one reaction per sender per message. **Seven writers** currently target that slot — the inventory below was corrected during critique after the first draft got the count and two attributions wrong.

| # | Writer | Glyph | Trigger | Proposed rank |
|---|---|---|---|---|
| 1 | `agent/session_executor.py:2545-2580` | `REACTION_COMPLETE` / `REACTION_SUCCESS` / `REACTION_ERROR`, or `None` to clear | session reaches terminal state | **1 — terminal, always wins, final** |
| 2 | `bridge/response.py::react_if_worker_down:386` | ⚠ `REACTION_WORKER_DOWN` | no live worker at ingestion (direct in-process `set_reaction`, not the outbox) | 2 |
| 3 | `agent/tool_budget.py::_queue_budget_reaction:308` | 🤯 `BUDGET_REACTION_EMOJI` | tool budget exhausted → `paused_budget` | 2 |
| 4 | `agent/worker_down_reactions.py:126` | ✍ `REACTION_PROCESSING` | worker picks the session up (overwrites the ⚠) | 3 |
| 5 | `agent/output_handler.py:1574,1578` + `tools/valor_telegram.py:977,1022` | RTR suppress reaction | read-the-room suppression | 3 |
| 6 | `agent/session_completion.py:564` | 👀 (suppress, default arg) | child-session completion suppress | 4 |
| 7 | `monitoring/session_watchdog.py:112,444` | ⏳ (dead — being deleted) | stall observed | — |
| → | **`_publish_liveness_ticks()` (new)** | digit 1-9 / fallback arc | every `HEARTBEAT_TICK_INTERVAL_SECONDS` | **5 — lowest; yields to everything** |

Corrections from the first draft, recorded so nobody re-derives the wrong list: `session_completion.py` queues a *suppress* 👀 and is **not** the terminal writer (that is `session_executor.py`); the ⚠ setter and the ✍ pickup writer are two distinct writers, not one; and `tool_budget.py` and `tools/react_with_emoji.py` were missed entirely.

`tools/react_with_emoji.py:99` is an eighth path — an agent-callable arbitrary reaction that *can* target the anchor message. It is deliberately unranked: it is a human/agent acting intentionally, and the precedence model should not silently override a deliberate act. It simply wins whenever it runs, and the next tick will overwrite it. State this explicitly rather than leaving it undefined.

They rarely collide today because the dead one never fires and the rest are roughly sequential in a session's life. A counter mutating the slot every 10 minutes ends that.

**Required outcome:** one documented precedence order, enforced at the single drain point. The tick ranks lowest and yields to everything; terminal is final and can never be overwritten by a tick.

**Enforcement point and the No-Go it breaks.** With seven writers across two processes, precedence can only be enforced where all outbox traffic converges: `bridge/telegram_relay.py::process_outbox`, reaction branch (line 922). That requires a `priority` (or `kind`) field on the reaction payload, which every outbox writer must emit — which means touching their call sites. The first draft's No-Go forbidding call-site changes directly contradicted this. **Resolved: the No-Go is lifted for the minimal payload-field addition only.** Adding a field is not refactoring; rewriting their trigger logic remains out of scope. Writer #2 sets reactions in-process rather than via the outbox and so bypasses the drain guard entirely — note it as a known gap rather than pretending the guard is total.

## Appetite

**Large.** Revised up from Medium during critique, and kept as one issue by explicit decision rather than split.

The counter itself is small. The real work is arbitrating seven writers across two processes, adding a drop guard to the relay's hot delivery loop, and the forcing function. Calling that Medium was not honest.

Scope is still held down by three decisions: delete the broken custom-emoji index rather than repair it, pin digit ids statically, and use a pure-duration anchor that requires no evidence-freshness machinery.

## Prerequisites

- None blocking. The pre-requisite named in the issue (`build_custom_emoji_index` non-functional) is resolved by deletion — see No-Gos.

## Solution

### Key Elements

1. **A time-derived tick**, computed by the watchdog from elapsed wall clock against a liveness anchor, idempotent across scans and loop restarts.
2. **One reaction-slot owner** with documented precedence, replacing five independent writers racing one slot.
3. **A hard ceiling** that refuses to advance and forces a substantive progress message, with a fresh counter re-anchored to that new message.

   **Reachability (checked during critique).** 9 ticks × 600 s = 90 min of ticking, ceiling at 100 min counting the 👀 slot. Against that, `check_all_sessions` abandons `status="active"` sessions at `ABANDON_THRESHOLD = 1800` s of `updated_at` silence and `DURATION_THRESHOLD = 7200` s total (`session_watchdog.py:78-79`). **The ceiling therefore serves `status="running"` sessions** — worker-executed Telegram sessions, which `check_all_sessions` never abandons because it never queries them. That is the population this feature exists for, so the ceiling is live code, not dead. For `active` sessions the 30-minute abandon can pre-empt the ceiling around tick 3; that is correct behavior (an abandoned session should stop ticking) and must not be "fixed" by raising the threshold.
4. **Deletion of the dead path** — `_apply_stall_reaction` and its machinery — in the same change.

### Flow

1. Watchdog scans an active session with a `chat_id` and `telegram_message_id`.
2. It computes `tick = elapsed // HEARTBEAT_TICK_INTERVAL_SECONDS` against the session's liveness anchor.
3. If `tick` exceeds the last published tick and the slot is not held by a higher-precedence state, it queues a reaction payload carrying the digit `document_id` plus its standard-emoji fallback.
4. The relay drains the payload; `set_reaction` attempts the custom emoji and falls back automatically.
5. The published-tick marker advances at **enqueue**, not delivery — see the decision below.
6. When `tick` would exceed `HEARTBEAT_MAX_TICKS`, no reaction is queued. Instead the session is steered to publish a progress message, and the counter re-anchors to that message.

**Decision: the marker advances on enqueue, and the "delivery outcome" requirement from the first draft is explicitly retracted.** The critique correctly flagged that no channel connects the relay's delivery result back to the watchdog, and that "Why Previous Fixes Failed" makes delivery-reflecting bookkeeping the structural fix — so dropping it silently would re-create #1313's defect.

It is not being dropped silently; it is being retracted with an argument that only holds because of the pure-duration semantics. A time-derived tick is **self-correcting**: if tick 4 fails to deliver, tick 5 fires ten minutes later from the same wall-clock derivation and overwrites the slot correctly. The counter converges without any feedback channel. A missed digit is cosmetic, not a stuck state.

This is materially different from #1313, where the dedup key was a **one-shot latch**: a single failed ⏳ meant no reaction for that entire stall period, with nothing to retry it. The defect there was a permanent false "applied" record, not a transient gap. Building a relay→watchdog feedback channel would stack a second retry layer on a payload the relay already retries three times, for a digit that repairs itself on the next tick.

What replaces it as the structural defense against #1313's defect class: a test asserting every emittable glyph is legal (so the delivery failure cannot happen for the original reason), and the relay's existing WARNING on failure as the diagnostic.

### Technical Approach

- **Anchor:** counter start — session start, re-anchored to each forced progress message. Pure duration, per the semantics decided in the Problem section and restated under Data Flow. There is deliberately **no** evidence-freshness probe (no transcript mtime, no `tool_use.jsonl` mtime, no `updated_at` staleness gate): the counter makes no claim about the session's internal state, so there is no liveness inference to defend. State the anchor in the code comment, and state alongside it *why* no self-report is consulted — [`bridge/liveness.py`](../../bridge/liveness.py) establishes the governing rule that *a handler that has stopped firing cannot testify to its own failure*, which is why the tick is emitted by the watchdog rather than by the session.
- **Constants must be written from scratch.** An earlier draft of this plan pointed at branch `wip/session-heartbeat-ticker` (`78443c3f6`) for `PREMIUM_DIGIT_REACTIONS`, `HEARTBEAT_FALLBACK_ARC`, `HEARTBEAT_MAX_TICKS`, `HEARTBEAT_TICK_INTERVAL_SECONDS`, and a ceiling-raising `heartbeat_reaction(tick)`. **That branch and commit no longer exist** — verified 2026-08-17: the object is unreachable and no commit in the repo contains `PREMIUM_DIGIT_REACTIONS`. It was temp progress, never a design commitment, so nothing is lost but the typing. Write these fresh in `bridge/response.py` against the behavior this plan specifies; do not spend time recovering the branch.
- **Payload:** the outbox already supports `custom_emoji_document_id` (`bridge/telegram_relay.py`), so no transport change is needed. The payload literal must stay schema-compatible with `_build_reaction_payload`.
- **Do not route ticks through `find_best_emoji`.** It uses cosine similarity plus `_softmax_sample` at a temperature — deliberately random among top-K. Correct for "pick a feeling", wrong for "this is tick 4".

## Failure Path Test Strategy

### Exception Handling Coverage

- Custom emoji rejected (non-Premium, pack removed, `chatReactionsNone`/`chatReactionsSome` policy) → falls back to the standard glyph; no exception escapes the watchdog loop.
- `FloodWaitError` on one chat → that tick is skipped and retried next scan; the sweep continues for other sessions.
- Redis unavailable when recording the published tick → tick is not marked delivered; next scan retries. No crash.

### Empty/Invalid Input Handling

- Session with no `chat_id` or no `telegram_message_id` (local sessions, non-Telegram origins) → skipped silently, no Redis writes. Existing skip semantics preserved.
- Negative or out-of-range tick → `ValueError`, never a clamp.

### Error State Rendering

- Terminal error must win the slot and stay. A tick must never overwrite a completed or errored session's reaction.
- `REACTION_ERROR` is pinned to 🤔, which is why the fallback arc avoids that glyph (Resolved Question 4) — a healthy session must never wear the error face.

## Test Impact

- [ ] `tests/unit/test_stall_detection.py::test_payload_matches_build_reaction_payload` — REPLACE: asserts the dead payload's schema parity; would pin the removed design in place.
- [ ] `tests/unit/test_stall_detection.py` (stall-reaction cases: dedup, skip conditions, feature flag) — REPLACE: rewrite against counter behavior.
- [ ] `tests/integration/test_watchdog_to_bridge.py` — UPDATE: end-to-end watchdog→outbox→relay path now carries a custom-emoji payload plus a priority field.
- [ ] `tests/unit/test_custom_emoji_index.py` — DELETE: tests a function being removed. These tests pass only because they mock the client and feed it documents the real API never returns.
- [ ] `tests/unit/test_heartbeat_reactions.py` — ADD: does not exist anywhere; write it new alongside the constants. Cover slot precedence, arc **non-registration** in `_reaction_constants()` (not glyph disjointness — see Task 2), and the ceiling raising rather than clamping.
- [ ] `tests/unit/test_bridge_relay.py` (reaction payload cases at 412, 634, 1313-1411) — UPDATE: the drain-side precedence guard changes this path.
- [ ] `tests/integration/test_worker_liveness_ingestion.py` — UPDATE: touches the ingestion-reaction sequence the precedence table now orders.
- [ ] `tests/integration/test_reply_delivery.py::TestReactionEmojiSelection` — UPDATE: shares `_assert_distinct()` with `bridge/response.py`; adding arc constants must not break the distinctness invariant.

## Rabbit Holes

- **Rebuilding the custom-emoji embedding index.** Tempting because the module exists. Out of scope — pin ids statically.
- **Probing chat reaction policy before each tick.** An extra API round trip per tick that still races policy changes. The fallback already covers it.
- **Making the counter semantically expressive** (different glyph per work type). The counter answers exactly one question: is time passing with something alive behind it.
- **Unifying all five reaction writers into one service.** Precedence must be defined; a full refactor of all five call sites is a separate change.

## Risks

### Risk 1: Sticker pack disappears
Digit `document_id`s belong to a third-party pack that could be uninstalled or withdrawn. **Mitigation:** every tick carries a standard-emoji fallback and `set_reaction` degrades automatically. The counter loses digits, not liveness.

### Risk 2: FLOOD_WAIT stalls the shared relay
Corrected during critique — the first draft aimed this at the wrong process. **The watchdog makes zero Telegram calls** (it writes Redis only) and therefore can never flood-wait; the original mitigation was a no-op against a nonexistent risk.

The real exposure is `process_outbox`, whose flood handler sleeps **inline** for up to 300 s (`telegram_relay.py:943-955`), blocking the entire relay for every chat and every message type, not just reactions. Adding a per-session reaction every 10 minutes raises the odds of reaching that handler.

**Mitigation:** none in this plan, deliberately. A per-chat skip in the relay is a change to shared delivery infrastructure with blast radius well beyond this feature and deserves its own issue. What this plan owes is not making it worse: ticks are low-frequency (one per session per 10 min), and a skipped tick is cosmetic and self-correcting. File the relay fix separately if tick traffic makes it bite.

### Risk 3: Reaction slot flicker
If precedence is wrong, the human sees the reaction change back and forth between a tick and a terminal state — reads as malfunction. **Mitigation:** terminal states are final and always win; this is the plan's primary test target.

## Race Conditions

### Race 1: Tick vs. session completion
A session completes while a tick payload is already queued. The tick could land *after* the terminal reaction and overwrite it with a duration glyph.

**There is no shared queue** — this is the part the first draft got wrong. `output_handler.react()` sets `session_id = chat_id` and writes `telegram:outbox:{chat_id}` (`output_handler.py:1574,1578`), while ticks, stall, and budget reactions write `telegram:outbox:{session_id}`. `process_outbox` iterates `r.keys("telegram:outbox:*")` in **unspecified order** (`telegram_relay.py:891`), draining up to `RELAY_BATCH_SIZE=10` per key per cycle. So ordering is undefined *across two independent queues*, not merely unguaranteed within one. A builder assuming FIFO gets them partway there would be wrong.

**Prevention:** drop the stale tick at the drain (`telegram_relay.py:922`), gated on a tick marker in the payload so the guard costs nothing for other reaction types. Constraints: no per-reaction Popoto status query on a 100 ms poll loop, and any status read goes through `asyncio.to_thread` like every other Redis call in that loop. Note that reactions exhausting `MAX_RELAY_RETRIES=3` are **discarded, not dead-lettered** (`telegram_relay.py:827-831`).

**Latent today:** `tool_budget.py`'s 🤯 has this same bug right now — nothing stops it landing after a terminal reaction. It is rarer only because budget exhaustion is rarer, and the ⏳ path never exhibited it only because it never landed at all. The guard fixes all three.

### Race 2: Double-tick across watchdog restarts
The watchdog restarts and rescans sessions mid-interval. **Prevention:** the tick is derived from elapsed wall clock, not incremented, so a rescan recomputes the same value. This is why increment-per-scan is prohibited.

### Race 3: Ceiling fires twice
Two scans both observe `tick > MAX` before the progress message is published. **Prevention:** the forced-progress steer must be guarded by an atomic `SET NX` marker, cleared only when the new anchor message exists.

## No-Gos (Out of Scope)

- **Not fixing `build_custom_emoji_index`.** Delete it, `rebuild_custom_emoji_index`, `_load_custom_embeddings`, `CUSTOM_CACHE_PATH`, and `tests/unit/test_custom_emoji_index.py`. It has no production caller, has never produced a cache file, and cannot work as written (it reads `result.documents` from `GetEmojiStickersRequest`, which returns only set descriptors — `sets=7, documents=0` on this account). Digit ids are pinned statically instead.
  **This is a behavior change, not dead-code removal — say so in the commit.** `_load_custom_embeddings()` is called unconditionally at `emoji_embedding.py:385` and runs on *every* `find_best_emoji` call; it yields nothing only because the cache file never exists. Removing it (cut: `emoji_embedding.py:385-412`) means `find_best_emoji` can never again return `is_custom=True`. Keep `set_reaction`'s `ReactionCustomEmoji` branch (`response.py:360-370`) and `EmojiResult`'s `document_id`/`is_custom` fields — those are proven working with statically pinned ids and are what the counter rides on. Only the embedding-index half goes.
- **Not changing `WATCHDOG_INTERVAL`.** The 5-minute scan is fine for a 10-minute tick.
- **Not building the #2663 progress CLI.** Separate issue; this plan should consume the same progress truth when it exists, not pre-empt it.
- **Not rewriting the other reaction writers' trigger logic.** Adding a `priority` field to their payloads IS in scope (see Architectural Impact — precedence cannot be enforced without it). Changing when or why they fire is not.
- **Not fixing the relay's inline FLOOD_WAIT sleep.** Real bug, shared infrastructure, its own issue (see Risk 2).
- **Not keeping ⏳ or any parallel stall path.** No half-migration.
- **Not claiming stall detection.** The counter measures duration and asserts watchdog attention. Any wording implying it detects a wedge is out of scope and wrong.

## Update System

No update-system changes required. This is bridge-internal: no new dependency, config file, service, or env var that must propagate to other machines. `HEARTBEAT_TICK_INTERVAL_SECONDS` is an optional local override with a working default and deliberately has no `.env.example` entry (the completeness check reports absent optional keys as warnings, and this one has no operational need to be set).

Note for the builder: `./scripts/valor-service.sh restart` is required after landing, since the watchdog runs inside the bridge process.

## Agent Integration

No agent integration required — this is a bridge-internal change. The watchdog already runs inside the bridge process and already reaches Telegram through the outbox relay. No new CLI entry point in `pyproject.toml [project.scripts]`, no new bridge import, no new agent-callable tool.

## Documentation

### Feature Documentation
- [x] Create `docs/features/session-liveness-tick-counter.md` covering the tick derivation, the reaction-slot precedence table, the ceiling and forced-progress behavior, and the Premium/fallback split.
- [x] Add the entry to the `docs/features/README.md` index table.
- [x] Update `docs/features/bridge-self-healing.md` if it references the ⏳ stall reaction.

### External Documentation Site
- [x] No changes — internal mechanism, not user-facing product surface.

### Inline Documentation
- [x] Update the `monitoring/session_watchdog.py` module docstring: it currently documents the ⏳ behavior being removed.
- [x] Record in `bridge/response.py` why digits require the custom-emoji schema, so nobody re-adds a keycap to `VALIDATED_REACTIONS`.

## Success Criteria

- A `status="running"` session past one tick interval shows an advancing counter in Telegram, verified by eye in a real chat.
- `grep -rn "STALL_REACTION_EMOJI\|_apply_stall_reaction" --include="*.py"` returns nothing outside git history — including the prose reference at `tool_budget.py:311`.
- The bridge starts. (Non-trivial: an arc glyph registered in `_reaction_constants()` raises `ImportError` at import.)
- Zero `failed to set reaction` warnings from the watchdog path in `logs/bridge.log` over a session exercising the full counter.
- Every emittable glyph is asserted legal by a test.
- Advancing past the final tick raises rather than clamps.
- Reaching the ceiling produces a progress message with a fresh counter anchored to it.
- A completed session's terminal reaction is never overwritten by a tick.

## Step by Step Tasks

### 1. Establish reaction-slot precedence
- Document the seven-writer precedence order in `docs/features/session-liveness-tick-counter.md`, including the unranked `react_with_emoji.py` path and the in-process `react_if_worker_down` gap.
- Add the `priority` field to the reaction payload. **Do it inside `_build_reaction_payload` (`agent/output_handler.py:1501`), as a keyword-only `priority: int | None = None` that falls back to a glyph→rank derivation when the caller passes nothing.** This is the decision the plan owes the builder, because "emit it from every outbox writer" is not a payload-literal edit for the writer that matters most: the terminal reaction (rank 1) never builds its own payload — `agent/session_executor.py:2576` calls `react_cb(...)`, which resolves to `TelegramRelayOutputHandler.react` (`:1535`) and builds at `:1576` through this one shared static. The alternative — threading `priority` through all four `react()` signatures (`output_handler.py:91,150,1535`, `bridge/email_bridge.py:1012`) — is rejected as a wider blast radius for no gain.
- **The tick publisher must pass `priority` explicitly; it must not lean on the glyph→rank fallback.** 👀 is genuinely ambiguous under that derivation — it is both the child-completion suppress (rank 4, `agent/session_completion.py:564`) and the tick's slot-0 arc entry (rank 5). The fallback exists for writers that predate the field, not for new code that has the parameter in hand.
- **Preserve schema parity after the replacement.** The payload schema is hand-mirrored in five more places (`agent/session_completion.py:591-602`, `agent/tool_budget.py:332-339`, `tools/react_with_emoji.py:98-105`, `monitoring/session_watchdog.py:605-608`, and imported directly at `agent/worker_down_reactions.py:137`). Task 8 REPLACEs `test_payload_matches_build_reaction_payload`, which is currently the test keeping one of those mirrors honest — so the replacement test must assert the *new* schema against `_build_reaction_payload` for every mirror, or the mirrors drift silently the first time the schema moves again.
- Implement the drain guard at `telegram_relay.py:922`: drop a tick whose session reached terminal status, and drop a lower-priority reaction when a higher one owns the slot. Status read via `asyncio.to_thread`.
- **Give the slot-ownership clause a state store**, or it is unimplementable. Terminal status is queryable, but "who owns this slot right now" is not derivable from anything that exists: record it per `(chat_id, message_id)` under `heartbeat:slot_owner:{chat_id}:{message_id}` holding the winning rank, written at the drain's `if success:` branch, TTL-bounded, and reset when a rank-1 terminal reaction lands. Without this the guard silently degrades to the terminal check alone — which is the only clause the drain can otherwise enforce.
- Tests first: terminal-wins and no-flicker are the highest-value assertions in this plan.

### 2. Land the reaction constants
- Write the reaction constants fresh in `bridge/response.py` (the `wip/session-heartbeat-ticker` draft is gone — see Technical Approach): the pinned digit table, the fallback arc (👀 then alternating 🥱/👨‍💻 — see Resolved Question 4), `HEARTBEAT_MAX_TICKS`, `HEARTBEAT_TICK_INTERVAL_SECONDS`, and a `heartbeat_reaction(tick)` that raises past the ceiling.
- **Keep the fallback arc OUT of `_reaction_constants()`.** `_assert_distinct()` raises `ImportError` at module import (`response.py:147,178`), so registering an arc that reuses an already-registered glyph (👀, 🤔, ✍, ⚠) stops the bridge from starting. The arc is a sequence, not a constant registry entry.
- Add a test asserting **the arc registers no entry in `_reaction_constants()`**, so a future edit cannot reintroduce the import-time crash. Word it that way, not as glyph-disjointness: the arc deliberately leads with 👀, which *is* `REACTION_RECEIVED` (`bridge/response.py:111,137`), so a literal disjointness assertion would fail against the arc this task specifies. The invariant that actually prevents the crash is non-registration — `_assert_distinct()` (`response.py:147`, run at import at `:178`) only ever inspects the registry dict, so a glyph merely *reused* by an unregistered sequence cannot trip it.

### 3. Rewrite the watchdog path
- Delete `_apply_stall_reaction`, `_clear_stall_reaction_dedup`, `STALL_REACTION_EMOJI`, the `watchdog:stall_reaction_applied:` key, and the `WATCHDOG_STALL_REACTION_ENABLED` gate.
- Update the `agent/tool_budget.py:311` comment, which names `_apply_stall_reaction` as its model — deleting the function orphans that reference and it would survive the Success Criteria grep.
- Add the sync `_publish_liveness_ticks()` called from `watchdog_loop()` with its own guarded `try`; status filter must include `running`.
- Implement wall-clock tick derivation against the counter-start anchor. Marker advances at enqueue (see Flow step 5).

### 4. Implement the ceiling
- Refuse to tick past `HEARTBEAT_MAX_TICKS`; steer the session to publish progress, guarded by an atomic `SET NX` (Race 3).
- **Use the legacy session-scoped steering leg (`room_id=None`), not the Room leg.** `agent/steering.py:18-33` makes key selection selective (#2642): conversation-level writes go to the Room key and are served to whichever session next drains that Room, while session-scoped diagnostics — explicitly including the watchdog loop-break steer — target the legacy key. "Publish your progress" is a diagnostic about *this* session; sent to the Room leg a sibling session can consume it and the wedged session stays silent.
- **Re-anchor on a named signal, not on inference.** The counter's anchor is cleared and re-established when the forced progress message actually exists. Use the relay's own record of sent message ids (`bridge/telegram_relay.py:984`, `_record_sent_message` on the `AgentSession`) as the truth source: the new anchor is that message id. **Mind the `DELIVERED_NO_ID` hole:** `telegram_relay.py:937-939` sets `success=True` with `msg_id=None`, and `_record_sent_message` only fires when an id exists — so a delivered-but-idless progress message would leave the Race 3 marker latched and the counter frozen despite the human having been answered. Treat a `DELIVERED_NO_ID` outcome as clearing the marker without re-anchoring (the counter stops; the next inbound message starts a fresh one). Re-anchoring on *any* subsequent PM message, rather than only a ceiling-forced one, is intended. This is the signal Race 3 depends on — its `SET NX` marker is cleared only when the new anchor message exists, so without naming it the marker has no specified clearer and latches forever.
- **Disposition for "steer never honored"** (see the conditionality note in *What the counter means*): a wedged session never drains the steer, so no progress message appears and no re-anchor occurs. Do **not** add a retry or a timeout escalation in this issue. The counter stays frozen at the ceiling digit, which is the intended terminal display for this case. Assert it in a test so a later reader does not "fix" the freeze.
- **Redis keys, named with TTLs so they cannot leak.** The counter anchor (`heartbeat:anchor:{session_id}`) and the last-published tick (`heartbeat:tick:{session_id}`) both carry a TTL comfortably exceeding the full ceiling window; the Race 3 forced-progress marker keeps its own `SET NX` TTL. An anchor key outliving its session is a leak, and the keys being *deleted* by Task 3 are named precisely, so these should be too.

### 5. Delete the dead custom-emoji index
- Remove `build_custom_emoji_index`, `rebuild_custom_emoji_index`, `_load_custom_embeddings`, `CUSTOM_CACHE_PATH`, and `tests/unit/test_custom_emoji_index.py`.
- Simplify `find_best_emoji`'s custom branch, which becomes unreachable.

### 6. Validation
- Run `scripts/pytest-clean.sh` over the affected suites.
- Restart services and verify a real session in Telegram.

### 7. Documentation
- Per the Documentation section.

### 8. Final Validation
- `python -m ruff check` / `format`; full success criteria sweep.

## Verification

1. Create a test AgentSession with a `test-` prefixed `project_key` and a real `chat_id`/`telegram_message_id`; let it run past two tick intervals; observe the reaction advance in Telegram; delete the session via the ORM.
2. Force the ceiling with a lowered `HEARTBEAT_TICK_INTERVAL_SECONDS`; confirm the progress message is published and a fresh counter attaches to it.
3. Complete a session mid-tick; confirm the terminal reaction wins and is not overwritten.
4. Simulate custom-emoji rejection; confirm the standard fallback lands and nothing raises.

## Critique Results

**Verdict: NEEDS REVISION** — 4 blockers, 6 concerns, 3 nits. Single-reviewer pass (no war-room roster; the reviewer's configuration forbids spawning sub-agents). Every claim below was independently spot-verified against the tree before acceptance.

| Sev | ID | Finding | Lands on | Disposition |
|---|---|---|---|---|
| Blocker | B1 | Counter is placed under `check_all_sessions()`, which is async and queries `status="active"` only (`session_watchdog.py:245,255`). The stall path actually lives in the **sync** `check_stalled_sessions()` (line 332), called without `await` at line 233. Worker-executed Telegram sessions run at `status="running"`, which `check_all_sessions` never sees. A builder following the diagram lands the feature in a function blind to its target sessions. **Verified.** | Data Flow, Task 3 | Rewrite Data Flow to `check_stalled_sessions()` or a new sync `_publish_liveness_ticks()`; status filter must include `running`; new code must be sync or get its own guarded `try` block. |
| Blocker | B2 | The plan holds two incompatible semantics. Problem + Success Criterion 1 promise a signal that advances *while working*; Technical Approach anchors on "time since last observed progress", under which a healthy session refreshes continuously and **never shows a counter**. That is a stall meter, and it fails Success Criterion 1 by construction. OQ3 frames this as a field choice; it is a product choice. | Problem, Technical Approach, Success Criteria, OQ2, OQ3 | Decide the semantics. Proposed: *gated duration* — `tick = elapsed_since_start // INTERVAL`, published only while independent evidence is fresh, and **frozen** (not advancing, not resetting) when it goes stale, so a frozen digit is itself the wedge signal. |
| Blocker | B3 | The writer inventory is wrong: **seven writers, not five**, and two rows are misattributed. `session_completion.py:564` queues a *suppress* 👀, not the terminal reaction — the real terminal writer is `session_executor.py:2499-2532`. The ⚠ setter is `response.py::react_if_worker_down:386`; `worker_down_reactions.py:126` is a separate writer queuing ✍ at pickup. **Missing entirely:** `tool_budget.py::_queue_budget_reaction:308` (🤯, same anchor message, same dedup pattern) and `tools/react_with_emoji.py:99` (agent-callable arbitrary reaction). **Verified.** | Architectural Impact | Correct the table to seven writers. Resolve the contradiction below before Task 1. |
| Blocker | B3a | Direct contradiction. Precedence across seven writers in two processes can only be enforced at the single drain point (`telegram_relay.py::process_outbox`, reaction branch line 922), which requires a `priority`/`kind` field every writer emits — i.e. touching the call sites the No-Gos forbid. | No-Gos vs. Architectural Impact | Either lift the No-Go for a minimal payload-field addition, or move slot ownership to its own issue. |
| Blocker | B4 | The stated reasoning is wrong: *"the payload is already in the list"* assumes one queue. **There is no shared list.** `output_handler.react()` writes `telegram:outbox:{chat_id}` (`output_handler.py:1486`); ticks/stall/budget write `telegram:outbox:{session_id}`; `process_outbox` iterates `r.keys("telegram:outbox:*")` in unspecified order (`telegram_relay.py:891`). Ordering is undefined *across two queues*. The conclusion (drop stale ticks at the drain) is right; the reasoning would mislead a builder into thinking FIFO helps. | Race Conditions § Race 1 | Restate the race. Guard at line 922, gated on a payload tick marker (no per-reaction Popoto query on a 100 ms loop), status read via `asyncio.to_thread`. Note: reactions exhausting `MAX_RELAY_RETRIES=3` are discarded, not dead-lettered. |
| Concern | C1 | The 🤔 collision is not a UX preference — `_assert_distinct()` **raises `ImportError` at module import** (`response.py:147,178`). Registering an arc that reuses 👀 or 🤔 in `_reaction_constants()` means **the bridge does not start**. **Verified.** | Open Question 1 | Adopt option (b); candidates 🥱 😴 🗿 🤓 👨‍💻 all confirmed in `VALIDATED_REACTIONS`. Keep the arc out of `_reaction_constants()` and add a test asserting the arc is disjoint from the constant registry. |
| Concern | C2 | "Advance the marker on delivery outcome, not enqueue" has **no implementation path** — the watchdog enqueues, the relay delivers, nothing connects them. Since "Why Previous Fixes Failed" makes delivery-reflecting bookkeeping the structural fix, dropping it silently re-creates #1313's exact defect. | Flow step 5, Task 3 | Either the relay writes `heartbeat:tick_published:{session_id}` at its `if success:` branch (`telegram_relay.py:978`), or explicitly retract the requirement on the grounds that a time-derived tick is self-correcting and a skipped digit is cosmetic. Decide in the plan. |
| Concern | C3 | FLOOD_WAIT analysis targets the wrong process. The watchdog makes **zero** Telegram calls (Redis only) and can never flood-wait, so Risk 2's mitigation is a no-op. Real exposure is `process_outbox`, which sleeps **inline** up to 300 s (`telegram_relay.py:943-955`), blocking the whole relay for all chats. | Research 2, Risk 2 | Rewrite Risk 2 against the relay. A per-chat skip is a change to shared delivery infrastructure and likely warrants its own issue. |
| Concern | C4 | The ceiling may be unreachable. 9 × 600 s = 90 min, but `ABANDON_THRESHOLD = 1800` s and `DURATION_THRESHOLD = 7200` s (`session_watchdog.py:78-79`) abandon `active` sessions well before it. The ceiling only ever fires for `running` sessions. **Verified.** | Key Elements 3, Task 4, Race 3 | State which status class the ceiling serves and reconcile the tick budget against 1800/7200 before building the `SET NX` guard for a possibly-dead branch. |
| Concern | C5 | The anchor candidates omit the independent evidence the same file already reads: `_check_transcript_liveness` on transcript mtime (line 176) and `read_recent_tool_calls` on `tool_use.jsonl` (line 1096), both written by the session subprocess's hooks rather than a probe. `bridge/liveness.py` resolved this class by *reaching over an independent path*, not by picking a better self-report field. | Open Question 3 | Anchor on `max(transcript mtime, tool_use.jsonl mtime)`; fall back to `updated_at` only if neither exists; log which source was used so a mirage is diagnosable. |
| Concern | C6 | "Simplify `find_best_emoji`'s custom branch, which becomes unreachable" is inaccurate — `_load_custom_embeddings()` is called unconditionally (`emoji_embedding.py:385`) and runs on every call, yielding nothing only because the cache never exists. Deleting it means `find_best_emoji` can never return `is_custom=True` again: a real behavior change, not dead-code removal. | No-Gos, Task 5 | Say so explicitly. Cut is `emoji_embedding.py:385-412`. |
| Concern | C7 | Medium is not honest given B3/B3a/B4/C2: arbitration across seven writers in two processes, a guard on the relay's hot loop, a delivery-feedback channel that does not exist, and an unresolved product question. | Appetite | Split into (A) kill ⏳ + land one working advancing signal, and (B) reaction-slot ownership as its own issue — or mark Large. |
| Nit | N1 | `tool_budget.py:311` names `_apply_stall_reaction` in prose; deleting the function orphans the reference and it survives Success Criterion 2's grep. | Task 3, Success Criteria | Update the comment. |
| Nit | N2 | Omits `tests/unit/test_bridge_relay.py` (reaction payload cases 412, 634, 1313-1411) and `tests/integration/test_worker_liveness_ingestion.py`. | Test Impact | Add both. |
| Nit | N3 | `tests/unit/test_heartbeat_reactions.py` is listed UPDATE but does not exist on `main`. | Test Impact | Disposition is ADD. |

**Upheld by the critique:** the diagnosis and its evidence; naming the reaction slot (not the counter) as the architectural weight; the increment-per-scan prohibition; refusing to probe chat policy; refusing to route ticks through `find_best_emoji`; deleting rather than repairing `build_custom_emoji_index`; the Rabbit Holes and No-Gos boundaries; the Freshness Check.

## Resolved Questions

All four questions are decided. Kept here rather than deleted, because each rules out an approach a future reader would otherwise re-propose.

1. **Counter semantics — DECIDED (owner, 2026-08-10).** The counter asserts watchdog attention; the number is duration; it makes no stall claim; the ceiling forces a PM progress message at minimum every 100 minutes. Full statement in the Problem section. This supersedes both the "advancing liveness signal" framing of the first draft and the critique's "gated duration" counter-proposal — no evidence-freshness gating is needed, because the counter never infers session state.
2. **Scope — DECIDED (owner, 2026-08-10).** Kept whole, appetite raised to Large. Not split into a separate reaction-slot-ownership issue: precedence is a prerequisite for the counter, and shipping the counter without it produces exactly the flicker the plan exists to avoid.
3. **Anchor — DECIDED, follows from (1).** Anchor is counter start (session start, re-anchored at each forced progress message). Pure duration. The transcript-mtime / `updated_at` / turn-transition debate is moot: with no liveness inference there is no mirage to defend against.

4. **Fallback arc glyph — DECIDED 2026-08-17.** The arc is **👀 then alternating 🥱/👨‍💻**. The original 🤔 is dropped: it is `REACTION_ERROR`'s pinned glyph (`bridge/response.py:143`), so in fallback mode every odd tick would show the system's "error" face on a healthy session. 🥱 is in `VALIDATED_REACTIONS` (`response.py:68`), unclaimed by any constant, and reads as honest elapsed time. There is no crash risk on either choice — the arc stays out of `_reaction_constants()` per Task 2, and Task 2's non-registration test enforces that. Note the arc still *leads with* 👀, which is `REACTION_RECEIVED`; that is deliberate and safe, because `_assert_distinct()` inspects only the registry, never a reused glyph.

## Open Questions

None — all four questions above are decided. The plan is ready for critique.
