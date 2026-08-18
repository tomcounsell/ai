# Session Liveness Tick Counter

**Issue:** [#2716](https://github.com/tomcounsell/ai/issues/2716) ·
**Code:** `monitoring/session_watchdog.py::_publish_liveness_ticks`,
`bridge/liveness_ticks.py`, `bridge/response.py::heartbeat_reaction`,
`agent/reaction_priority.py`, `bridge/telegram_relay.py::_reaction_yields_slot`

The session watchdog advances a counter on the Telegram message that started a
session, so a human watching a long-running session can see that something
independent of that session is still watching it. At a hard ceiling the counter
refuses to advance and forces the session to publish a progress message
instead.

## What the counter means

This is the load-bearing semantic, and it is narrower than "liveness" in the
colloquial sense.

- **It asserts that the watchdog has eyes on this session.** That is the whole
  claim. The watchdog is a process independent of the session, so a ticking
  counter is proof that *the observer* is alive and watching — the guarantee
  [`bridge/liveness.py`](../../bridge/liveness.py) says a self-report cannot
  provide, because a handler that has stopped firing cannot testify to its own
  failure.
- **The number is duration.** Tick 4 means "roughly forty minutes since this
  counter started". Not four units of progress, not four checks passed.
- **It does not detect stalls.** A wedged session and a busy session tick
  identically. Any wording that implies stall detection is wrong.
- **The forcing function is the guarantee.** At the ceiling the session must
  publish a progress message — success, failure, or still-working — and a fresh
  counter anchors to that message. The human is promised a substantive update
  at least every 100 minutes, and the counter is the visible countdown toward
  that promise.

Because the counter infers nothing about the session's internal state, it needs
no evidence-freshness gating: no transcript mtime, no `tool_use.jsonl` mtime,
no `updated_at` staleness probe. There is no liveness inference to be fooled.

## Mechanism

```
watchdog_loop()                     [bridge process, every WATCHDOG_INTERVAL=300s]
  └─ _publish_liveness_ticks()      [sync, own guarded try]
       └─ for each session in status {running, active} with chat_id + telegram_message_id:
            elapsed = now - anchor_ts
            tick    = elapsed // HEARTBEAT_TICK_INTERVAL_SECONDS
            tick <= last_published        → skip
            tick <= HEARTBEAT_MAX_TICKS   → queue reaction payload (priority=5)
                                              → LPUSH telegram:outbox:{session_id}
            tick >  HEARTBEAT_MAX_TICKS   → SET NX ceiling marker, steer for progress

process_outbox()                    [bridge relay, 100 ms poll]
  ├─ _reaction_yields_slot()        → drop when precedence says so
  ├─ set_reaction()                 → ReactionCustomEmoji, falling back to the standard glyph
  ├─ _record_reaction_slot_owner()  → remember the rank that landed
  └─ on a delivered message: _reanchor_liveness_counter()
```

**The publisher is synchronous and sweeps `running`.** It deliberately does not
live inside `check_all_sessions()`: that function is async and queries
`status="active"` only, while worker-executed Telegram sessions — the exact
population this feature exists for — run at `status="running"`. Placing it
there would blind the feature to its own target.

**The tick is derived, never incremented.** `WATCHDOG_INTERVAL` is 300s against
a 600s tick, so incrementing once per scan would advance at double rate and
double-count across a loop restart. A rescan recomputes the same value from
wall clock, which is what makes the counter idempotent.

**The anchor is the counter's start**, not "last observed progress evidence":
session start initially, then re-anchored to each message the session
publishes.

**The marker advances at enqueue, not delivery.** A time-derived tick is
self-correcting — if tick 4 never lands, tick 5 fires one interval later from
the same derivation and overwrites the slot correctly, so a missed digit is
cosmetic. This is materially different from the ⏳ path it replaced, whose
dedup key was a one-shot latch: one failed reaction meant no reaction for the
entire stall period with nothing to retry it.

## Glyphs and degradation

Keycap digits (1️⃣ … 9️⃣) are **not** legal Telegram reactions in either
encoding — they are absent from the 74 the server advertises. Do not add one to
`VALIDATED_REACTIONS`; it will be rejected at the API exactly as ⏳ was. A digit
can only be rendered as `ReactionCustomEmoji` with a sticker-pack
`document_id`, which requires a Premium sender.

Every tick therefore carries both: a pinned `document_id` (when one exists) and
a standard-glyph fallback that `set_reaction` degrades to automatically when
the custom emoji is rejected — non-Premium sender, pack uninstalled, or a chat
whose `available_reactions` policy (`chatReactionsNone` / `chatReactionsSome` /
`allow_custom=false`) forbids it. Chat policy is deliberately **not** probed
before reacting: that costs an API round trip per tick and still races a policy
change.

The fallback arc is `HEARTBEAT_FALLBACK_ARC = ("👀", "🥱", "👨‍💻")` — slot 0 is
the acknowledgement, later ticks alternate. 🤔 is deliberately absent: it is
`REACTION_ERROR`'s pinned glyph, and a healthy session must never wear the
error face.

**`PREMIUM_DIGIT_REACTIONS` is currently empty.** The digit glyphs live in the
`Birthday Collection` pack (sticker set id `1901206392136531984`), installed on
this account, but the per-digit document ids were never recorded in the repo
and can only be read from a live authenticated Telethon client. Until the table
is pinned the counter runs entirely on the fallback arc — the same degradation
path taken when the pack disappears, so the feature is correct either way; it
simply shows an alternating arc instead of digits.

### The arc is not a reaction constant

`HEARTBEAT_FALLBACK_ARC` must **never** be registered in
`bridge/response.py::_reaction_constants()`. `_assert_distinct()` runs at module
import and raises `ImportError` on any duplicated glyph, and the arc leads with
👀, which *is* `REACTION_RECEIVED`. Registering it stops the bridge from
starting. The reuse is safe precisely because `_assert_distinct()` inspects only
the registry dict, never a glyph reused by an unregistered sequence.
`tests/unit/test_heartbeat_reactions.py` and
`tests/integration/test_reply_delivery.py` both assert the non-registration.

## The ceiling

`heartbeat_reaction(tick)` **raises** past `HEARTBEAT_MAX_TICKS` rather than
clamping. A clamped counter would sit at the last digit forever, which is
exactly the "runs forever" failure the ceiling exists to prevent.

At the ceiling the publisher queues no reaction. Instead it claims
`heartbeat:ceiling:{session_id}` with an atomic `SET NX` — two scans can both
observe a tick past the ceiling before a progress message exists, and only one
may steer — and pushes a steering message asking the session to publish
progress.

The steer targets the **legacy session-scoped leg** (`room_id=None`), not the
Room key. "Publish your progress" is a diagnostic about *this* session; sent to
the Room leg a sibling session could consume it and the session the human is
waiting on would stay silent. See [session steering](session-steering.md) and
`agent/steering.py`.

**Which sessions actually reach the ceiling.** 9 ticks × 600 s is 90 minutes of
ticking, ceiling at 100 minutes counting the slot-0 👀. `check_all_sessions`
abandons `status="active"` sessions at `ABANDON_THRESHOLD = 1800` s of silence,
so for those the abandon usually pre-empts the ceiling around tick 3 — correct
behavior, and not to be "fixed" by raising the threshold. The ceiling serves
`status="running"` sessions, which `check_all_sessions` never queries.

**When the steer is never honored.** Delivery is via steering, which the
PostToolUse hook serves and the worker drains at turn boundaries. A genuinely
wedged session — no tool calls, no turn boundary — never receives it, the
`SET NX` marker stays claimed, and the counter freezes at the ceiling digit.
That is the intended terminal display for this case (a frozen counter is itself
the wedge signal) and the one case the 100-minute promise does not cover. Do
not add a retry or timeout escalation; a test pins the freeze so a later reader
does not "fix" it.

## Re-anchoring

The counter re-anchors on a **named signal**, not on inference: the relay's own
record that a message was sent. `_reanchor_liveness_counter` runs at the
relay's success branch for any non-reaction message, sets the new anchor to
that message id, resets the tick, and releases the ceiling marker.

Re-anchoring on *any* message the session publishes, not only a ceiling-forced
one, is intended — the human has been answered either way.

**The `DELIVERED_NO_ID` hole.** A send can reach Telegram and return no message
id (`success=True`, `msg_id=None`). There is nothing to anchor to, so that
outcome **stops** the counter — clearing the anchor, tick, and ceiling marker
without re-anchoring. Left unhandled, the marker would stay latched and the
counter would freeze at the ceiling digit despite the human having been
answered. The next inbound message starts a fresh counter.

## Reaction-slot precedence

Telegram permits exactly one reaction per sender per message. **Seven writers**
target a session's originating message. Ranks live in
`agent/reaction_priority.py`; **lower wins**.

| Rank | Writer | Glyph | Trigger |
|---|---|---|---|
| 1 | `agent/session_executor.py` | `REACTION_COMPLETE` / `REACTION_SUCCESS` / `REACTION_ERROR`, or `None` to clear | session reaches terminal state — **final, always wins** |
| 2 | `bridge/response.py::react_if_worker_down` | ⚠ `REACTION_WORKER_DOWN` | no live worker at ingestion |
| 2 | `agent/tool_budget.py::_queue_budget_reaction` | 🤯 `BUDGET_REACTION_EMOJI` | tool budget exhausted |
| 3 | `agent/worker_down_reactions.py` | ✍ `REACTION_PROCESSING` | worker picks the session up (overwrites the ⚠) |
| 3 | `agent/output_handler.py` RTR branch | 👀 | read-the-room suppression |
| 4 | `agent/session_completion.py` | 👀 | child-session completion suppress |
| 5 | `monitoring/session_watchdog.py::_publish_liveness_ticks` | digit / fallback arc | every `HEARTBEAT_TICK_INTERVAL_SECONDS` — **lowest, yields to everything** |

Two writers sit outside this model, deliberately:

- **`tools/react_with_emoji.py` is unranked.** It is an agent-callable
  arbitrary reaction — a human or agent acting on purpose — and the precedence
  model should not silently override a deliberate act. It takes the glyph
  derivation, which lands on rank 1 for any emoji the model picks, so it is
  never dropped; it simply wins whenever it runs and the next tick overwrites
  it. That second half only holds because its payload carries
  `priority_ranked: False`, so it delivers **without claiming the slot**.
  Recording it would pin the message at the fallback's terminal rank for the
  owner key's full TTL and silently suppress the budget, pickup, and tick
  writers behind it — the exact opposite of "the next tick overwrites it".
  `priority_ranked` exists to separate the two questions a single `priority`
  value cannot answer at once: may this payload be dropped, and may it claim
  the slot.
- **`react_if_worker_down` bypasses the guard entirely.** It calls
  `set_reaction` in-process at ingestion rather than writing to the outbox, so
  the drain never sees it. The guard is not total, and this is the gap. It has
  not caused an observed problem because ⚠ fires at ingestion, before any
  counter exists for that message.

### How the rank reaches the drain

The rank travels on the payload as `priority`, added inside
`TelegramRelayOutputHandler._build_reaction_payload` as a keyword-only argument
that falls back to a glyph→rank derivation when the caller passes nothing. That
placement is what makes the terminal writer work: rank 1 never builds its own
payload — `agent/session_executor.py` calls `react_cb`, which resolves to
`TelegramRelayOutputHandler.react`, which builds here. Threading `priority`
through all four `react()` signatures would have been a wider blast radius for
no gain.

Unmapped glyphs derive to rank 1. That is the fail-safe direction: an
unrecognized reaction wins its slot exactly as it does today, and only the tick
ever yields.

**The tick publisher passes `priority` explicitly** and must never lean on the
derivation — 👀 is genuinely ambiguous under it, mapping to the rank-4
child-completion suppress rather than the tick's rank 5.

The payload schema is hand-mirrored in five places
(`agent/session_completion.py`, `agent/tool_budget.py`,
`tools/react_with_emoji.py`, `monitoring/session_watchdog.py`, and imported
directly by `agent/worker_down_reactions.py`). `TestReactionPayloadSchemaParity`
in `tests/unit/test_stall_detection.py` asserts every mirror against
`_build_reaction_payload`; without it they drift silently the first time the
schema moves.

### The drain guard

Precedence can only be enforced where all outbox traffic converges:
`bridge/telegram_relay.py::process_outbox`, reaction branch. There is no single
queue whose FIFO order could substitute — `output_handler.react()` writes
`telegram:outbox:{chat_id}` while ticks, budget, and completion reactions write
`telegram:outbox:{session_id}`, and `process_outbox` iterates those keys in
unspecified order, so ordering is undefined *across two independent queues*.

`_reaction_yields_slot` applies two rules against
`heartbeat:slot_owner:{chat_id}:{message_id}`:

1. **Terminal is final.** Once a rank-1 reaction lands, nothing lower may
   overwrite it. This also closes a bug that predates the counter: nothing
   stopped `tool_budget`'s 🤯 landing after a terminal reaction.
2. **The tick yields to everything.** A rank-5 tick is dropped whenever any
   higher-ranked writer owns the slot, and additionally whenever its session
   has already reached a terminal status.

Everything else is left alone — in particular the normal ⚠ → ✍ progression at
pickup, where a later, lower-ranked reaction is supposed to win. The
terminal-status Popoto query runs only for tick payloads, so ordinary reaction
traffic costs nothing extra on the 100 ms poll loop, and every Redis read goes
through `asyncio.to_thread` like the rest of that loop.

Note that reactions exhausting `MAX_RELAY_RETRIES=3` are discarded, not
dead-lettered.

## Redis keys

| Key | Contents | TTL |
|---|---|---|
| `heartbeat:anchor:{session_id}` | JSON `{ts, message_id}` — counter start and anchor message | 6 h |
| `heartbeat:tick:{session_id}` | Last tick number published | 6 h |
| `heartbeat:ceiling:{session_id}` | `SET NX` forced-progress marker (Race 3) | 6 h |
| `heartbeat:slot_owner:{chat_id}:{message_id}` | Precedence rank owning the slot | 24 h |

Every TTL comfortably exceeds the full ceiling window without outliving its
session; an anchor key that survives its session is a leak.

## Configuration

| Name | Default | Meaning |
|---|---|---|
| `HEARTBEAT_TICK_INTERVAL_SECONDS` | 600 | Seconds per tick. Provisional and tunable; the smallest interval that keeps reaction traffic negligible against the relay's shared FLOOD_WAIT exposure. Lower it locally to exercise the ceiling without waiting 100 minutes. |
| `HEARTBEAT_MAX_TICKS` | 9 | Ceiling. Code constant, not env-tunable. |

No `.env.example` entry: the interval is an optional local override with a
working default and no operational need to be set.

## Known limitations

- **`PREMIUM_DIGIT_REACTIONS` is empty**, so the counter currently shows the
  fallback arc rather than digits. Pinning the ids requires a live Telethon
  read.
- **`react_if_worker_down` bypasses the drain guard** (in-process
  `set_reaction`), so the guard is not total.
- **The first sweep after deploy steers a burst.** A missing anchor is seeded
  from `session.started_at`, so any session already running longer than the
  full ceiling window computes a past-ceiling tick on the very first sweep,
  claims the ceiling, and gets a forced-progress steer. That is a one-time
  burst across the whole `running`+`active` population, it is self-correcting,
  and each session's `SET NX` marker fires once — but the first post-deploy
  hour will look unusually chatty and that is expected, not a bug.
- **FLOOD_WAIT is a shared-relay exposure.** The watchdog makes zero Telegram
  calls and can never flood-wait; the relay's handler sleeps inline for up to
  300 s, blocking every chat and message type. Ticks add roughly one reaction
  per session per 10 minutes, and a skipped tick is cosmetic and
  self-correcting, so this change adds load without adding a mitigation. A
  per-chat skip is a change to shared delivery infrastructure and belongs in
  its own issue.

## What this replaced

The ⏳ stall reaction from
[#1313](https://github.com/tomcounsell/ai/issues/1313) — `STALL_REACTION_EMOJI`,
`_apply_stall_reaction`, `_clear_stall_reaction_dedup`, the
`watchdog:stall_reaction_applied:` key, and the
`WATCHDOG_STALL_REACTION_ENABLED` gate — all deleted. ⏳ is not a legal Telegram
reaction: it was in this repo's own `INVALID_REACTIONS` when #1313 shipped, and
nothing in the pipeline compared the new constant against that list. Eleven
enqueues, zero landings. A test now asserts every emittable glyph is legal.

It was a rewrite rather than an addition for a hard reason: one reaction slot
means one writer. A surviving stall reaction and the counter would have
clobbered each other with the winner decided by drain order.

The dead custom-emoji embedding index went with it —
`build_custom_emoji_index`, `rebuild_custom_emoji_index`,
`_load_custom_embeddings`, `CUSTOM_CACHE_PATH`. It had no production caller and
could not work as written: it read `result.documents` from
`GetEmojiStickersRequest`, which returns only set descriptors. **This was a
behavior change, not dead-code removal** — `find_best_emoji` can no longer
return `is_custom=True`. The transport half (`EmojiResult.document_id`,
`set_reaction`'s `ReactionCustomEmoji` branch) is proven working with
statically pinned ids and stays.
