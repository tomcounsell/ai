---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-10
tracking: https://github.com/tomcounsell/ai/issues/2716
last_comment_id: none
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

**Desired outcome:** a long-running session displays a visibly advancing liveness signal, emitted by an observer independent of the session, that cannot run indefinitely without the session producing substantive evidence of progress.

## Freshness Check

**Disposition: Unchanged.** Baseline `origin/main` = `1e3fdd6f5`.

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
   → **Informs approach:** one reaction per session per 10 minutes is negligible, but the watchdog scans *all* sessions in one pass and could emit a burst across many chats simultaneously. `bridge/telegram_relay.py` already re-raises `FloodWaitError` rather than swallowing it; ticks must be individually skippable so one flood-waited chat cannot stall the sweep.

## Spike Results

No spikes required — every assumption that would have justified one was already resolved empirically against the live API during `/do-issue` recon:

- **Are keycap digits legal standard reactions?** No. Server advertises exactly 74; no keycaps in either encoding. `messages.getAvailableReactions`.
- **Is the account Premium?** Yes (`premium: True`).
- **Do digit custom emoji exist and are they reachable?** Yes — `Birthday Collection` (id `1901206392136531984`) is already installed and carries all ten digits.
- **Does a custom-emoji reaction actually set and replace in place?** Yes. Verified in Saved Messages: set 1️⃣, read back `ReactionCustomEmoji(document_id=...)`, replaced with 2️⃣ in the same slot. Test message deleted.

## Data Flow

```
session_watchdog.watchdog_loop()            [bridge process, every WATCHDOG_INTERVAL=300s]
  └─ check_all_sessions()
       └─ for each active AgentSession:
            compute elapsed = now - liveness_anchor
            tick = elapsed // HEARTBEAT_TICK_INTERVAL_SECONDS
            if tick > last_published_tick and tick <= HEARTBEAT_MAX_TICKS:
              └─ queue reaction payload  →  redis LPUSH telegram:outbox:{session_id}
            if tick > HEARTBEAT_MAX_TICKS:
              └─ force progress message  →  steering queue (agent/steering.py)

bridge/telegram_relay._send_queued_reaction()   [drains outbox]
  └─ set_reaction(client, chat_id, reply_to, EmojiResult)
       ├─ ReactionCustomEmoji(document_id) attempt
       └─ on failure → ReactionEmoji(standard glyph) fallback
```

**Critical timing constraint:** `WATCHDOG_INTERVAL` is 300s but a tick is 600s. The tick number MUST be derived from elapsed wall clock (`elapsed // interval`), never incremented once per scan. An increment-per-scan implementation would advance at 2x the intended rate and would double-count on any loop restart.

## Why Previous Fixes Failed

#1313 failed for one reason and one reason only: **the emoji was never validated against the server's reaction set.** The repo already had the answer — `INVALID_REACTIONS` in `bridge/response.py` — and ⏳ was already in it when #1313 shipped. Nothing in the pipeline compared the new constant against that list.

Two secondary factors kept it invisible for months:
- The failure logs at WARNING in the *relay*, far from the watchdog code that caused it, so it never looked like a watchdog bug.
- The dedup key is set at enqueue time, so the system records "reaction applied" for a reaction that never landed. The bookkeeping asserts success independent of outcome.

Both are fixed structurally here: a test asserts every emittable glyph is legal, and the advance key reflects delivery.

## Architectural Impact

This plan's real weight is not the counter — it is establishing an **owner for the originating message's single reaction slot.**

Telegram permits one reaction per sender per message. Five writers currently target that slot:

| Writer | Glyph | Trigger |
|---|---|---|
| `bridge/telegram_bridge.py` | 👀 `REACTION_RECEIVED` | message received |
| `agent/worker_down_reactions.py` | ⚠ `REACTION_WORKER_DOWN` | no live worker at ingestion |
| `monitoring/session_watchdog.py` | ⏳ (dead) | stall observed |
| `agent/session_completion.py` | terminal reaction | session completes |
| `agent/output_handler.py` | RTR suppress reaction | read-the-room suppression |

They do not currently collide often because the dead one never fires and the others are roughly sequential in a session's life. A counter that mutates the slot every 10 minutes changes that: it will actively fight the completion and worker-down writers unless precedence is explicit.

**Required outcome:** a single documented precedence order, with the counter yielding to terminal states. Terminal (completion/error) must always win and must be final; worker-down outranks a tick; a tick outranks nothing.

## Appetite

**Medium.** The counter itself is small. The slot-precedence model is the real work, and it is the part that must not be rushed — an unarbitrated slot produces flickering reactions that read as system malfunction to the human.

Scope is held down by two decisions: delete the broken custom-emoji index rather than repair it, and pin digit ids statically.

## Prerequisites

- None blocking. The pre-requisite named in the issue (`build_custom_emoji_index` non-functional) is resolved by deletion — see No-Gos.

## Solution

### Key Elements

1. **A time-derived tick**, computed by the watchdog from elapsed wall clock against a liveness anchor, idempotent across scans and loop restarts.
2. **One reaction-slot owner** with documented precedence, replacing five independent writers racing one slot.
3. **A hard ceiling** that refuses to advance and forces a substantive progress message, with a fresh counter re-anchored to that new message.
4. **Deletion of the dead path** — `_apply_stall_reaction` and its machinery — in the same change.

### Flow

1. Watchdog scans an active session with a `chat_id` and `telegram_message_id`.
2. It computes `tick = elapsed // HEARTBEAT_TICK_INTERVAL_SECONDS` against the session's liveness anchor.
3. If `tick` exceeds the last published tick and the slot is not held by a higher-precedence state, it queues a reaction payload carrying the digit `document_id` plus its standard-emoji fallback.
4. The relay drains the payload; `set_reaction` attempts the custom emoji and falls back automatically.
5. On delivery success, the published-tick marker advances. On failure it does not, so the next scan retries.
6. When `tick` would exceed `HEARTBEAT_MAX_TICKS`, no reaction is queued. Instead the session is steered to publish a progress message, and the counter re-anchors to that message.

### Technical Approach

- **Anchor:** the counter measures time since the last *evidence of progress the watchdog can see itself* — not since session start, and never a self-report from the session. Candidate anchors in preference order: last observed turn transition, then `updated_at`, then session creation. The chosen anchor must be stated in the code comment, because [`bridge/liveness.py`](../../bridge/liveness.py) establishes the governing rule: *a handler that has stopped firing cannot testify to its own failure.*
- **Constants already exist** on branch `wip/session-heartbeat-ticker` (`78443c3f6`) in `bridge/response.py`: `PREMIUM_DIGIT_REACTIONS`, `HEARTBEAT_FALLBACK_ARC`, `HEARTBEAT_MAX_TICKS`, `HEARTBEAT_TICK_INTERVAL_SECONDS`, and `heartbeat_reaction(tick)` which raises at the ceiling rather than clamping. Adopt, relocate, or discard on the merits — it is temp progress, not a design commitment.
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
- `REACTION_ERROR` is pinned to 🤔 — see Open Questions, this collides with the proposed arc.

## Test Impact

- [ ] `tests/unit/test_stall_detection.py::test_payload_matches_build_reaction_payload` — REPLACE: asserts the dead payload's schema parity; would pin the removed design in place.
- [ ] `tests/unit/test_stall_detection.py` (stall-reaction cases: dedup, skip conditions, feature flag) — REPLACE: rewrite against counter behavior.
- [ ] `tests/integration/test_watchdog_to_bridge.py` — UPDATE: end-to-end watchdog→outbox→relay path now carries a custom-emoji payload.
- [ ] `tests/unit/test_custom_emoji_index.py` — DELETE: tests a function being removed. These tests pass only because they mock the client and feed it documents the real API never returns.
- [ ] `tests/unit/test_heartbeat_reactions.py` — UPDATE: exists on the wip branch; keep and extend to cover slot precedence.

## Rabbit Holes

- **Rebuilding the custom-emoji embedding index.** Tempting because the module exists. Out of scope — pin ids statically.
- **Probing chat reaction policy before each tick.** An extra API round trip per tick that still races policy changes. The fallback already covers it.
- **Making the counter semantically expressive** (different glyph per work type). The counter answers exactly one question: is time passing with something alive behind it.
- **Unifying all five reaction writers into one service.** Precedence must be defined; a full refactor of all five call sites is a separate change.

## Risks

### Risk 1: Sticker pack disappears
Digit `document_id`s belong to a third-party pack that could be uninstalled or withdrawn. **Mitigation:** every tick carries a standard-emoji fallback and `set_reaction` degrades automatically. The counter loses digits, not liveness.

### Risk 2: FLOOD_WAIT under a wide sweep
One scan touching many sessions across many distinct chats is the risky burst shape per the research. **Mitigation:** per-tick failures are isolated and retried on the next scan; never retried tightly in-loop.

### Risk 3: Reaction slot flicker
If precedence is wrong, the human sees the reaction change back and forth between a tick and a terminal state — reads as malfunction. **Mitigation:** terminal states are final and always win; this is the plan's primary test target.

## Race Conditions

### Race 1: Tick vs. session completion
A session completes while a tick payload is already queued in the outbox. The tick could land *after* the completion reaction and overwrite a terminal state with a liveness glyph.
**Prevention:** the relay (or the drain) must drop a queued tick whose session has since reached a terminal status. Ordering alone is insufficient — the payload is already in the list.

### Race 2: Double-tick across watchdog restarts
The watchdog restarts and rescans sessions mid-interval. **Prevention:** the tick is derived from elapsed wall clock, not incremented, so a rescan recomputes the same value. This is why increment-per-scan is prohibited.

### Race 3: Ceiling fires twice
Two scans both observe `tick > MAX` before the progress message is published. **Prevention:** the forced-progress steer must be guarded by an atomic `SET NX` marker, cleared only when the new anchor message exists.

## No-Gos (Out of Scope)

- **Not fixing `build_custom_emoji_index`.** Delete it, `rebuild_custom_emoji_index`, `_load_custom_embeddings`, `CUSTOM_CACHE_PATH`, and `tests/unit/test_custom_emoji_index.py`. It has no production caller, has never produced a cache file, and cannot work as written (it reads `result.documents` from `GetEmojiStickersRequest`, which returns only set descriptors — `sets=7, documents=0` on this account). Digit ids are pinned statically instead.
- **Not changing `WATCHDOG_INTERVAL`.** The 5-minute scan is fine for a 10-minute tick.
- **Not building the #2663 progress CLI.** Separate issue; this plan should consume the same progress truth when it exists, not pre-empt it.
- **Not refactoring the other four reaction writers.** Define precedence; do not rewrite their call sites.
- **Not keeping ⏳ or any parallel stall path.** No half-migration.

## Update System

No update-system changes required. This is bridge-internal: no new dependency, config file, service, or env var that must propagate to other machines. `HEARTBEAT_TICK_INTERVAL_SECONDS` is an optional local override with a working default and deliberately has no `.env.example` entry (the completeness check reports absent optional keys as warnings, and this one has no operational need to be set).

Note for the builder: `./scripts/valor-service.sh restart` is required after landing, since the watchdog runs inside the bridge process.

## Agent Integration

No agent integration required — this is a bridge-internal change. The watchdog already runs inside the bridge process and already reaches Telegram through the outbox relay. No new CLI entry point in `pyproject.toml [project.scripts]`, no new bridge import, no new agent-callable tool.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/session-liveness-tick-counter.md` covering the tick derivation, the reaction-slot precedence table, the ceiling and forced-progress behavior, and the Premium/fallback split.
- [ ] Add the entry to the `docs/features/README.md` index table.
- [ ] Update `docs/features/bridge-self-healing.md` if it references the ⏳ stall reaction.

### External Documentation Site
- [ ] No changes — internal mechanism, not user-facing product surface.

### Inline Documentation
- [ ] Update the `monitoring/session_watchdog.py` module docstring: it currently documents the ⏳ behavior being removed.
- [ ] Record in `bridge/response.py` why digits require the custom-emoji schema, so nobody re-adds a keycap to `VALIDATED_REACTIONS`.

## Success Criteria

- A session running past one tick interval shows an advancing counter in Telegram, verified by eye in a real chat.
- `grep -rn "STALL_REACTION_EMOJI\|_apply_stall_reaction" --include="*.py"` returns nothing outside git history.
- Zero `failed to set reaction` warnings from the watchdog path in `logs/bridge.log` over a session exercising the full counter.
- Every emittable glyph is asserted legal by a test.
- Advancing past the final tick raises rather than clamps.
- Reaching the ceiling produces a progress message with a fresh counter anchored to it.
- A completed session's terminal reaction is never overwritten by a tick.

## Step by Step Tasks

### 1. Establish reaction-slot precedence
- Document the precedence order across all five writers in `docs/features/session-liveness-tick-counter.md`.
- Implement the guard that prevents a tick from overwriting a terminal state, including the queued-payload case (Race 1).
- Tests first: terminal-wins and no-flicker are the highest-value assertions in this plan.

### 2. Land the reaction constants
- Adopt or rewrite the `wip/session-heartbeat-ticker` constants into their final home.
- Resolve the 🤔 collision with `REACTION_ERROR` (Open Question 1) before finalizing the arc.

### 3. Rewrite the watchdog path
- Delete `_apply_stall_reaction`, `_clear_stall_reaction_dedup`, `STALL_REACTION_EMOJI`, the `watchdog:stall_reaction_applied:` key, and the `WATCHDOG_STALL_REACTION_ENABLED` gate.
- Implement time-derived tick computation against the chosen anchor.
- Advance the published-tick marker on delivery outcome, not enqueue.

### 4. Implement the ceiling
- Refuse to tick past `HEARTBEAT_MAX_TICKS`; steer the session to publish progress, guarded by an atomic `SET NX` (Race 3).
- Re-anchor the counter to the new message.

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

| ID | Sev | Lands on | Finding | Disposition |
|---|---|---|---|---|
| B1 | Blocker | Data Flow, Task 3 | Counter is placed under `check_all_sessions()`, which is async and queries `status="active"` only (`session_watchdog.py:245,255`). The stall path actually lives in the **sync** `check_stalled_sessions()` (line 332), called without `await` at line 233. Worker-executed Telegram sessions run at `status="running"`, which `check_all_sessions` never sees. A builder following the diagram lands the feature in a function blind to its target sessions. **Verified.** | Rewrite Data Flow to `check_stalled_sessions()` or a new sync `_publish_liveness_ticks()`; status filter must include `running`; new code must be sync or get its own guarded `try` block. |
| B2 | Blocker | Problem, Technical Approach, Success Criteria, OQ2, OQ3 | The plan holds two incompatible semantics. Problem + Success Criterion 1 promise a signal that advances *while working*; Technical Approach anchors on "time since last observed progress", under which a healthy session refreshes continuously and **never shows a counter**. That is a stall meter, and it fails Success Criterion 1 by construction. OQ3 frames this as a field choice; it is a product choice. | Decide the semantics. Proposed: *gated duration* — `tick = elapsed_since_start // INTERVAL`, published only while independent evidence is fresh, and **frozen** (not advancing, not resetting) when it goes stale, so a frozen digit is itself the wedge signal. |
| B3 | Blocker | Architectural Impact | The writer inventory is wrong: **seven writers, not five**, and two rows are misattributed. `session_completion.py:564` queues a *suppress* 👀, not the terminal reaction — the real terminal writer is `session_executor.py:2499-2532`. The ⚠ setter is `response.py::react_if_worker_down:386`; `worker_down_reactions.py:126` is a separate writer queuing ✍ at pickup. **Missing entirely:** `tool_budget.py::_queue_budget_reaction:308` (🤯, same anchor message, same dedup pattern) and `tools/react_with_emoji.py:99` (agent-callable arbitrary reaction). **Verified.** | Correct the table to seven writers. Resolve the contradiction below before Task 1. |
| B3a | Blocker | No-Gos vs. Architectural Impact | Direct contradiction. Precedence across seven writers in two processes can only be enforced at the single drain point (`telegram_relay.py::process_outbox`, reaction branch line 922), which requires a `priority`/`kind` field every writer emits — i.e. touching the call sites the No-Gos forbid. | Either lift the No-Go for a minimal payload-field addition, or move slot ownership to its own issue. |
| B4 | Blocker | Race Conditions § Race 1 | The stated reasoning is wrong: *"the payload is already in the list"* assumes one queue. **There is no shared list.** `output_handler.react()` writes `telegram:outbox:{chat_id}` (`output_handler.py:1486`); ticks/stall/budget write `telegram:outbox:{session_id}`; `process_outbox` iterates `r.keys("telegram:outbox:*")` in unspecified order (`telegram_relay.py:891`). Ordering is undefined *across two queues*. The conclusion (drop stale ticks at the drain) is right; the reasoning would mislead a builder into thinking FIFO helps. | Restate the race. Guard at line 922, gated on a payload tick marker (no per-reaction Popoto query on a 100 ms loop), status read via `asyncio.to_thread`. Note: reactions exhausting `MAX_RELAY_RETRIES=3` are discarded, not dead-lettered. |
| C1 | Concern | Open Question 1 | The 🤔 collision is not a UX preference — `_assert_distinct()` **raises `ImportError` at module import** (`response.py:147,178`). Registering an arc that reuses 👀 or 🤔 in `_reaction_constants()` means **the bridge does not start**. **Verified.** | Adopt option (b); candidates 🥱 😴 🗿 🤓 👨‍💻 all confirmed in `VALIDATED_REACTIONS`. Keep the arc out of `_reaction_constants()` and add a test asserting the arc is disjoint from the constant registry. |
| C2 | Concern | Flow step 5, Task 3 | "Advance the marker on delivery outcome, not enqueue" has **no implementation path** — the watchdog enqueues, the relay delivers, nothing connects them. Since "Why Previous Fixes Failed" makes delivery-reflecting bookkeeping the structural fix, dropping it silently re-creates #1313's exact defect. | Either the relay writes `heartbeat:tick_published:{session_id}` at its `if success:` branch (`telegram_relay.py:978`), or explicitly retract the requirement on the grounds that a time-derived tick is self-correcting and a skipped digit is cosmetic. Decide in the plan. |
| C3 | Concern | Research 2, Risk 2 | FLOOD_WAIT analysis targets the wrong process. The watchdog makes **zero** Telegram calls (Redis only) and can never flood-wait, so Risk 2's mitigation is a no-op. Real exposure is `process_outbox`, which sleeps **inline** up to 300 s (`telegram_relay.py:943-955`), blocking the whole relay for all chats. | Rewrite Risk 2 against the relay. A per-chat skip is a change to shared delivery infrastructure and likely warrants its own issue. |
| C4 | Concern | Key Elements 3, Task 4, Race 3 | The ceiling may be unreachable. 9 × 600 s = 90 min, but `ABANDON_THRESHOLD = 1800` s and `DURATION_THRESHOLD = 7200` s (`session_watchdog.py:78-79`) abandon `active` sessions well before it. The ceiling only ever fires for `running` sessions. **Verified.** | State which status class the ceiling serves and reconcile the tick budget against 1800/7200 before building the `SET NX` guard for a possibly-dead branch. |
| C5 | Concern | Open Question 3 | The anchor candidates omit the independent evidence the same file already reads: `_check_transcript_liveness` on transcript mtime (line 176) and `read_recent_tool_calls` on `tool_use.jsonl` (line 1096), both written by the session subprocess's hooks rather than a probe. `bridge/liveness.py` resolved this class by *reaching over an independent path*, not by picking a better self-report field. | Anchor on `max(transcript mtime, tool_use.jsonl mtime)`; fall back to `updated_at` only if neither exists; log which source was used so a mirage is diagnosable. |
| C6 | Concern | No-Gos, Task 5 | "Simplify `find_best_emoji`'s custom branch, which becomes unreachable" is inaccurate — `_load_custom_embeddings()` is called unconditionally (`emoji_embedding.py:385`) and runs on every call, yielding nothing only because the cache never exists. Deleting it means `find_best_emoji` can never return `is_custom=True` again: a real behavior change, not dead-code removal. | Say so explicitly. Cut is `emoji_embedding.py:385-412`. |
| C7 | Concern | Appetite | Medium is not honest given B3/B3a/B4/C2: arbitration across seven writers in two processes, a guard on the relay's hot loop, a delivery-feedback channel that does not exist, and an unresolved product question. | Split into (A) kill ⏳ + land one working advancing signal, and (B) reaction-slot ownership as its own issue — or mark Large. |
| N1 | Nit | Task 3, Success Criteria | `tool_budget.py:311` names `_apply_stall_reaction` in prose; deleting the function orphans the reference and it survives Success Criterion 2's grep. | Update the comment. |
| N2 | Nit | Test Impact | Omits `tests/unit/test_bridge_relay.py` (reaction payload cases 412, 634, 1313-1411) and `tests/integration/test_worker_liveness_ingestion.py`. | Add both. |
| N3 | Nit | Test Impact | `tests/unit/test_heartbeat_reactions.py` is listed UPDATE but does not exist on `main`. | Disposition is ADD. |

**Upheld by the critique:** the diagnosis and its evidence; naming the reaction slot (not the counter) as the architectural weight; the increment-per-scan prohibition; refusing to probe chat policy; refusing to route ticks through `find_best_emoji`; deleting rather than repairing `build_custom_emoji_index`; the Rabbit Holes and No-Gos boundaries; the Freshness Check.

## Open Questions

1. **The proposed fallback arc collides with `REACTION_ERROR`.** The requested arc is 👀 then alternating 🤔/👨‍💻, but 🤔 is `REACTION_ERROR`'s pinned glyph (`agent/constants.py`), and ✍ (`REACTION_PROCESSING`), 👀 (`REACTION_RECEIVED`), and ⚠ (`REACTION_WORKER_DOWN`) are also taken. In the fallback case — exactly when digits are unavailable — every odd tick would show the same glyph the system uses for "error". Options: (a) accept it, since ticks and errors are temporally distinct; (b) swap 🤔 for an unclaimed validated glyph (🥱, 😴, 🗿, 🤓); (c) re-pin `REACTION_ERROR`. Recommendation: (b), with 🥱/😴 also reading as honest elapsed-time signals.
2. **How should a stalled session read on the counter?** The stall condition survives the rewrite but needs a representation: jump straight to the ceiling, use a distinct terminal glyph, or force the progress message immediately. Recommendation: jump to the ceiling, so a stall shortens the leash rather than adding a competing signal.
3. **Which liveness anchor?** Last observed turn transition is the most honest but may not be cheaply available in the watchdog's current session read. Falling back to `updated_at` risks the mirage documented in the `project_sdlc_liveness_mirage` memory, where probe refreshes make a hollow session look alive. Needs a decision before Task 3.
