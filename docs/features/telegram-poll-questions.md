# Telegram poll questions

When an engineering session gets blocked on a judgment call in a Telegram **group**, `/ask-me`
renders the question as a **native Telegram poll**. The human glances, taps once, and the session
resumes with the choice in hand — instead of reading a paragraph on a phone and composing prose.

Issue [#2701](https://github.com/tomcounsell/ai/issues/2701).
Plan: [`docs/plans/ask-me-telegram-polls.md`](../plans/ask-me-telegram-polls.md).

## The degradation matrix, and why it is what it is

A poll ships only when the chat is a **group** AND the session is an **eng** session. Everything
else gets today's numbered-prose behavior.

| Surface | Result |
|---|---|
| Telegram group + `eng` session | **Native poll** |
| Telegram 1:1 DM | Numbered prose |
| `teammate` session, even in an eligible group | Numbered prose |
| Email, local, system | Numbered prose |

### The capability matrix is settled — do not re-probe it

Verified by live MTProto probe and recorded on the issue. These are facts of the protocol and of
an owner decision, not defaults to work around:

| Sender | Chat type | Polls | Inline keyboard buttons |
|---|---|---|---|
| User account (the bridge, Telethon/MTProto) | 1:1 DM | ❌ `MediaInvalidError` | ❌ bot-only primitive |
| User account | Group | ✅ verified | ❌ bot-only primitive |
| Bot account (Bot API) | 1:1 DM | ✅ verified | ✅ verified |

**The bot path was rejected on identity, not capability.** A bot cannot post into a user-to-user
chat, so its question would land in a separate `@bot` conversation and break the thread scoping
sessions depend on. It would also add a second Telegram identity, a new inbound transport, and
`projects.json` contact-ownership churn.

**`teammate` sessions get prose by owner decision** — polls are an engineering affordance.

Do not send test polls to re-confirm any of this.

### `PollAnswer.option` accepts 8 bytes, not 100

Measured empirically against live MTProto during the build gate:

```
1, 2, 4, 8 bytes: accepted
9, 12 bytes:      REJECTED — "A poll option used invalid data (the data may be too long)"
```

The rejection happens **at the wire with no local signal**, so an over-long option ships a poll that
simply never sends. The TL schema declares `option:bytes` with no visible bound and every published
reference says 100, so this is only knowable by probing. Treat 8 as a hard protocol constant.

This is why the correlation id below travels as packed binary rather than the obvious
`f"{index}:{hex}"` text form, which is 34 bytes and cannot be sent at all.

## Outbound: question → poll on screen

```
/ask-me (headless bridge branch)
  → valor-ask-poll                      tools/ask_poll.py
  → poll_eligible(chat_id, session_id)  bridge/poll_gating.py     ← degrade to prose here, once
  → build_telegram_poll_outbox_payload  agent/output_handler.py   ← mints poll_id_hint
  → telegram:outbox:{session_id}
  → _send_queued_poll                   bridge/telegram_relay.py  ← re-checks eligibility
  → send_poll                           bridge/response.py        ← raw InputMediaPoll
  → telegram:poll:{server_poll_id}      bridge/poll_registry.py
  → then AskUserQuestion                                          ← ends the turn (see below)
```

### `bridge/poll_gating.py` — one predicate, evaluated twice, failing closed

`poll_eligible(chat_id, session_id) -> PollEligibility(ok, reason)`:

- `is_group_chat(chat_id)` — the negative-id discriminator. **It stays in
  `bridge/read_the_room.py`**, merely promoted from the private `_is_group_chat`; poll gating
  *imports* it. One definition, no alias, and the dependency arrow points from the feature module
  at the generic predicate rather than the reverse.
- `session_type == SessionType.ENG`, compared against the enum, never a bare literal in two places.
- **Every ambiguity is ineligible**: a missing `AgentSession` record, a `null` `session_type`
  (the field is `null=True`), an unparseable chat id, or any exception. It never raises.

The asymmetry justifying fail-closed: a question rendered as prose to an eng session is a cosmetic
loss, while a poll rendered into a teammate chat is a scope violation and a poll into a DM is a hard
rejection that consumes retries while an agent stays blocked.

Read **twice on purpose** — at ask time by the CLI (so degradation happens once, at a single
decision point, before a poll payload exists) and again at send time by the relay (the last writer
before the wire; a payload can sit in the outbox across a session-type change). The relay's
ineligible branch **converts to plain text rather than dropping**.

It is **synchronous**, and the relay must reach it through `asyncio.to_thread`: the `AgentSession`
lookup is on an unindexed field, and calling it inline would stall every other bridge coroutine for
seconds per poll send.

### `poll_id_hint` — the Race-6 correlation key

Minted in `build_telegram_poll_outbox_payload` and **nowhere else**, once per payload rather than
per send attempt. It is carried on the outbox payload, used as the provisional registry key, and
embedded in the poll's option bytes so a poll discovered on the wire can be matched back to the
payload that produced it.

Because the ceiling is 8 bytes, only a **7-byte prefix** travels: `bytes([index])` plus the first 7
bytes of the 32-hex hint. `correlation_matches(decoded_prefix, poll_id_hint)` owns the resulting
prefix-vs-full-hint comparison in one place, so no call site has to get the asymmetry right by hand.
56 bits disambiguates a bounded window of recent outbound polls in one chat with room to spare, and
the adoption rule's "more than one candidate → adopt nothing" bail covers a collision.

`session_type` is deliberately **not** stamped into the payload: a queued payload would outlive a
session's real type.

### Drafter: validate, never compose

The poll question goes through the public `validate_poll_question()` seam
(`bridge/message_drafter.py`), which wraps `_validate_for_medium(question, "telegram_poll")`.

It does **not** go through `draft_message`, which runs `_compose_structured_draft` *before*
validating and would return the question wearing an emoji prefix, a stage line and a link footer. A
stage line inside a poll question is not a message with a header; it is a broken question.

This is a deliberate, narrow departure from the "drafter is the load-bearing comms layer" doctrine:
a poll question is a structured artifact rendered by Telegram's own UI. The comms layer is not
bypassed overall — the escape-hatch followup, which is ordinary prose, still goes through the full
drafter.

Option count and length validation lives in `tools/ask_poll.py`, because `_validate_for_medium`
takes text only and physically cannot see the options.

### Failure → the human still sees the question

Two mechanisms, and the distinction matters:

1. **The user-visible path is a plain-text re-enqueue.** On terminal relay failure the poll branch
   rpushes a `type: None` payload onto the same `telegram:outbox:{session_id}` key with
   `_relay_attempts` reset. It cannot loop: `process_outbox` is `while processed < RELAY_BATCH_SIZE`
   over `r.lpop` of that key, so the rpush is consumed on a later cycle, and plain text never
   re-enters the poll branch.
2. **Dead-lettering is the durability backstop, and is explicitly NOT prompt delivery.**
   `replay_dead_letters` runs from exactly one site — the bridge connect sequence — i.e. the next
   bridge restart, which for a blocked agent is hours away or never. Two guards both matter:
   `"poll"` must stay out of the ephemeral discard tuple, **and** the question must be supplied as
   the dead-letter `text`, because a poll payload has no `text` key and would otherwise fall through
   the `if chat_id and text` gate into silence.

## The asking session has to actually stop

Rendering the poll is only half a feature. If the asker keeps running it answers its own question by
guessing before anyone taps. Two mechanisms have to line up, and **neither existed before this
feature**.

### 1. `/ask-me` calls `AskUserQuestion` after `valor-ask-poll`

This looks like a redundant double-ask. It is not, and it must not be "cleaned up":

- `valor-ask-poll` **renders** the question.
- `AskUserQuestion` **ends the turn**.

The `needs_human` edge fires only on a `PreToolUse` tool-name match against `AskUserQuestion`
(`agent/session_runner/hook_edge.py`, `_ASK_USER_MATCHER`). A Bash call to `valor-ask-poll` has tool
name `Bash`, which never matches. Without the second call the turn does not end on
`PM_NEEDS_HUMAN` and the whole `COMPLETED`-is-the-mainline chain below is inoperative.

Under `claude -p` the second call does not prompt anyone — it fires the edge and ends the turn.

**Rejected alternative, do not revive:** teaching `_ASK_USER_MATCHER` about Bash calls. It would
fire `needs_human` on arbitrary Bash invocations and couple a generic turn-edge classifier to one
CLI's name.

### 2. `agent/output_router.py` pauses the nudge loop

**This fixes a defect that pre-dates the poll feature.** `determine_delivery_action`'s

```python
if session_type == "eng" and classification_type == "sdlc":
    return "nudge_continue"
```

is unconditional and sits ahead of every `stop_reason` branch, so an sdlc eng session posing *any*
question — a poll or plain prose — is re-enqueued with `NUDGE_MESSAGE` and proceeds on a guess.

A `"pause_open_question"` branch now sits **immediately ahead** of that line, guarded by a
`has_open_question` keyword. Placement is load-bearing: it is *after* the terminal-status,
`completion_sent`, post-compaction, watchdog, rate-limit, empty-output and nudge-cap guards (a
session that is dying, wedged, rate-limited or capped must still take those paths) and *before* the
eng+sdlc line, which is the only thing it overrides.

**The condition is the poll registry's existing unanswered row — no new state.**
`session_has_open_poll(session_id)` is that read, and the **executor** performs it (thread-offloaded,
fail-quiet) and passes the result in. `determine_delivery_action` stays a **pure function**, exactly
as it performs no `AgentSession` read for `last_compaction_ts`.

**Blast radius is bounded by the default.** `has_open_question` defaults to `False`, so every
session with no outstanding poll keeps today's behavior — asserted by the pre-existing
`tests/unit/test_output_router.py` cases passing unmodified.

Pausing at a turn boundary is consistent with the "no mid-turn blocking" decision: nothing holds a
turn open; the turn has already ended and the router simply stops re-enqueuing.

## Inbound: tap → agent resumes

A tap produces **no Telegram message** — only an `updateMessagePoll` broadcast with aggregate counts.

### The registry is mandatory, not an optimisation

`UpdateMessagePoll(poll_id, results, poll)` has **no peer and no message id**. There is no way to
route a vote back to a chat or session from the update alone. `bridge/poll_registry.py` closes that
gap with a row written at send time.

Plain Redis strings following `bridge/job_router.py`'s deliberate non-Popoto posture — which keeps
this outside index drift and `rebuild_indexes()`, and means **no `scripts/update/migrations.py`
entry**. Rows are created on demand and expire on their own.

**The descriptor row is immutable; every mutable marker is its own atomic key.**

| Key | Holds | Why separate |
|---|---|---|
| `telegram:poll:{id}` | `chat_id`, `msg_id`, `session_id`, `question`, `options`, `created_at` | Written once with `SET NX`, never rewritten |
| `telegram:poll:answered:{id}` | **the ISO-8601 claim timestamp** | The one-shot lock. A timestamp, never the constant `1`, so staleness is readable |
| `telegram:poll:dispatched:{id}` | ISO timestamp | The steer/re-enqueue happened and must never repeat. Bounds the claim release |
| `telegram:poll:steered_at:{id}` | ISO timestamp | The translation completed cleanly. What `iter_unanswered_polls` and the operator signal key on |
| `telegram:poll:warned:{id}` | `1` | Deduplicates `poll_expired_unanswered` |

Storing these as fields inside one JSON value would make every marker write a read-modify-write on a
shared key. The reconciliation loop's warn write races the fast-path translator's marker writes, and
**the claim serializes *translation*, not the loop's own scan-side write**. A lost update drops
`steered_at` (the row is re-yielded forever) or drops `dispatched` (the double-enqueue the marker
exists to prevent).

**Enumeration is index-backed** via the `POLL_OPEN_INDEX` / `POLL_PENDING_INDEX` SETs. No `SCAN
MATCH`: it filters server-side but still walks every key in a db shared with production Popoto keys,
once per fast reconcile tick. The index is a *hint* and the row stays authoritative — a lost `SREM`
costs one wasted `GET`, never a missed poll, which is why both `SADD`s live in the same helper as
their write.

### Reconciliation is primary; `events.Raw` is a latency win

Both call the same idempotent `translate_poll_vote`, so adding the fast path cannot introduce a
second behavior. Making `GetPollResultsRequest` reconciliation the guaranteed mechanism buys
restart-survivability and tolerance of a dropped update for free.

`poll_update_observed` is logged from inside the Raw handler on every `UpdateMessagePoll`. That is
how the un-gated question — *does the push update reach a user account at all?* — gets answered in
production, rather than by opening a second updates-enabled client on the bridge's auth key, which
would consume updates the live bridge needs. **If that signal never appears, the Raw handler is dead
weight and should be deleted in a follow-up. That is a scope reduction, not a bug.**

### Group semantics: first vote wins, deterministically

Anyone in the room can tap. `PollResults` gives no ordering, so "the first vote" is **not** derivable
from the aggregate. The rule:

1. Filter to options with `voters >= 1`.
2. Zero → return **without claiming**. A spurious update must not burn the one-shot claim.
3. Exactly one → use it.
4. More than one → warn naming the tied options, then highest `voters`, ties broken by **lowest
   decoded option index**.

Closing the poll on first translation makes this first-voter-wins in practice, and that is the
intent: **the poll exists to unblock one agent, not to take a vote of the room.** Quorum, weighting
and voter allowlists are deliberately not built. The polls only go into machine-owned eng groups
whose members can *already* steer a session by typing, so a tap grants no authority a message does
not, and a disagreement is correctable with an ordinary reply.

### Attribution is not built

`GetPollVotesRequest` **cannot** resolve a voter here: polls are sent with `public_voters=False`, and
per-voter detail is only retrievable for a public poll. Verified by the build gate. Building the
call anyway would be dead weight on the inbound fast path, so `sender_name` resolves through the
target session's `initial_telegram_message["sender_name"]` and then the literal `"Telegram poll"`.

Making attribution work would mean flipping `public_voters=True`, publishing every voter's identity
to the whole group in exchange for a name in a steer line. **Not taken.**

### `COMPLETED` is the normal outcome, not an edge case

`/ask-me` ends its turn on the `needs_human` edge, and `PM_NEEDS_HUMAN` is clean and
wrap-up-eligible, so `_runner_final_status` finalizes the AgentSession `"completed"` at turn end —
while the human has up to `POLL_PROBE_TAP_WAIT_S` (default 1800s) to decide.

**By the time nearly every real tap lands, the asking session is already `completed`.** So
`resume_completed_session` → `dispatch_telegram_session` is the shipped mainline, and dropping that
branch loses **every** vote, not a rare one. `LIVE` / `PENDING` / `LIVE_GUARD` are the "tap beat the
turn end" exceptions.

`bridge/answer_routing.py` holds the two seams a vote and a typed reply share:

- `resolve_answer_target(session_id) -> AnswerTarget` — a pure state read returning
  `LIVE | PENDING | LIVE_GUARD | COMPLETED | NONE`, carrying the **session object** so the caller
  can derive `room_id` from it.
- `resume_completed_session(...)` — the completed-session re-enqueue, with `project` /
  `project_key` / `working_dir` / `session_type` each falling back to the field on the completed
  record, which is why a caller with no project dict can still use it.

**The module is poll-independent on purpose.** `translate_poll_vote` lives in `bridge/poll_vote.py`
and imports from it. The extraction restructures the primary inbound path for every typed Telegram
reply on every machine, and reverting the poll feature does not revert that — so it landed as its
own commit and `git revert` of it stays a real option.

`room_id` is **mandatory** on every steer: `push_steering_message` selects the Room key only when
handed one and deliberately never looks a session up itself (`session_id` is unindexed). Omitting it
silently writes the legacy `steering:{session_id}` key while every peer caller writes the Room leg —
a regression, not a crash.

The poll's own `msg_id` is passed as `telegram_message_id` on the completed path. That is safe and
load-bearing: `dispatch_telegram_session` claims `(chat_id, telegram_message_id)` via
`claim_message`, which is **inbound-only** — an outbound send never claims it — so a poll's message
id is an unused, unique, stable dedup key for exactly this re-enqueue.

## Failure recovery

### The claim must not permanently swallow a question

The claim is taken before closing the poll and before steering. Any failure after it would otherwise
leave the claim alive for its TTL, and `iter_unanswered_polls` skips a claimed row — an invisible,
permanently blocked agent. Four mechanisms:

1. Everything after the claim runs in `try/except`, and the handler **deletes the claim** so the
   next reconciliation tick retries.
2. Completion is recorded **separately from the claim** as `steered_at`, written only after the
   steer returns. `poll_expired_unanswered` keys on the **absent steered marker, never on a missing
   claim** — otherwise the operator signal would be blind to precisely this state.
3. `mark_poll_dispatched` runs immediately after the steer and **before anything else that can
   throw**, and the exception handler releases the claim **only when it is absent**. A blanket
   release re-opens the mirror failure: one vote, two enqueues on the `COMPLETED` mainline. The
   stated second guard cannot cover it — `claim_message`'s `CLAIM_TTL_SECONDS` is deliberately short
   and the slow reconcile interval outlives it.
4. **A stale-claim takeover**, without which 1–3 do not cover the failure this names first. Item 1
   only runs if the process survived; on a bridge death after the claim nothing releases it. So on a
   lost claim the translator reads the claim's **age**: younger than one slow interval is a genuine
   concurrent peer (return); older with `dispatched` present means re-attempt only `steered_at`;
   older with nothing dispatched means **take the claim over and proceed**.

**One residual is accepted and named.** `mark_poll_dispatched` is a separate Redis command issued
*after* the steer returns, so a bridge death inside that one-command window leaves a claim with no
marker over a side effect that already happened — which the takeover reads as "nothing dispatched"
and steers a second time. Accepted deliberately: the alternative is never taking over, which is the
permanent swallow item 4 exists to fix, and a duplicate resume of a completed session is
degraded-but-safe while a swallowed question is not. **Do not move `mark_poll_dispatched` ahead of
the steer** — that re-opens the swallow. `takeover_poll_claim` emits `poll_claim_takeover` with the
poll id, claim age and dispatched state, so a production double-dispatch is diagnosable from logs
rather than inferred.

### Race 6: a poll on screen with no registry row

The server assigns the poll id only after the send returns, so a complete row genuinely cannot
precede the send. A restart in that window leaves a poll visibly on screen that nothing can route —
permanent loss, distinct from a vote arriving before the write (self-healing) and from a restart
after it (the registry survives).

Both halves are required:

1. **A provisional row first**, and "first" means the **first statement** of `_send_queued_poll` —
   not merely "before `send_poll`". `process_outbox` already consumed the work item with an atomic
   LPOP, so the window between that LPOP and the provisional write is a silent *total* loss with
   nothing to adopt and no row to warn on. Every branch that then declines to send deletes the
   provisional row on its way to prose.
2. **Orphan adoption in the reconciliation loop**, matched on the correlation id embedded in the
   option bytes — **never on question text**, which is not unique (an agent re-asking after an
   expired poll, or two sessions asking the same standard question, produce two candidates with no
   tie-break). **More than one match adopts nothing and warns**: an ambiguous adoption steers a
   session with someone else's answer, which is worse than a dropped question.

The same lookup runs on a relay **retry** before re-sending. Telegram accepting `SendMediaRequest`
and the client then raising is an ordinary MTProto outcome, and because `poll_id_hint` is minted per
payload rather than per attempt, a naive retry would put two polls on screen decoding to the same
hint — turning the ambiguity bail from a safety guard into the systematic outcome of a routine retry.

## Observability

| Signal | Where | Means |
|---|---|---|
| `poll_expired_unanswered` | reconcile loop | A question is past its warn age with no `steered_at`. **The** operator signal for a failing inbound half. One warning per poll |
| `telegram:poll:reconcile:heartbeat` | written every tick, read by `ui/app.py`'s `/dashboard.json` | The loop itself is alive. Absence → `poll_reconcile: degraded` |
| `poll_update_observed` | `events.Raw` handler | The push update reaches a user account. Permanent absence → delete the handler |
| `poll_claim_takeover` | `takeover_poll_claim` | A stale claim was taken over; a following double-dispatch is possible |
| `poll_adoption_ambiguous` | adoption scan | Two candidates matched one hint; nothing adopted |
| `poll_sent_without_correlation_id` | `send_poll` | A caller did not thread `poll_id_hint`; Race-6 mitigation is silently off |

**The heartbeat exists because `poll_expired_unanswered` cannot cover the loop's own death**: it is
emitted from *inside* the loop's scan, so if the loop is what died the signal cannot fire. A detector
that lives inside the thing it detects is not a detector. This is one Redis key and one read at an
existing surface — not a new service and not a second watchdog.

## Module map

| Module | Owns | Why here |
|---|---|---|
| `tools/ask_poll.py` | The `valor-ask-poll` CLI, option validation, degradation | The single entry point the agent reaches through Bash |
| `bridge/poll_gating.py` | `poll_eligible` | The one answer to "does this surface take a poll" |
| `bridge/read_the_room.py` | `is_group_chat` | A **generic** Telegram peer-type predicate. Not moved into a feature module, which would make read-the-room import from a poll module |
| `bridge/response.py` | `send_poll`, `close_poll`, `encode_option` / `decode_option` / `correlation_matches` | Already the raw-MTProto home |
| `agent/output_handler.py` | payload builder, `send_poll` handler method, `render_poll_as_text` | Beside its text sibling |
| `bridge/poll_registry.py` | keys, markers, index SETs, every registry helper | The index-set ownership boundary |
| `bridge/poll_vote.py` | `translate_poll_vote` | Kept **out** of `answer_routing.py` so that module stays poll-independent and revertible |
| `bridge/poll_reconcile.py` | the loop, heartbeat, orphan adoption | Out of `telegram_bridge.py`, which only imports and starts it |
| `bridge/answer_routing.py` | `resolve_answer_target`, `resume_completed_session` | The **poll-independent** seam shared with the reply-to ladder |
| `agent/output_router.py` | `pause_open_question` | The nudge-loop decision, which is where the pre-existing defect lived |

The `events.Raw` handler stays in `bridge/telegram_bridge.py` — only the loop body moved out.

## Configuration

All tunables are named, env-overridable constants in `bridge/poll_registry.py`, each provisional and
adjustable: `POLL_REGISTRY_TTL_S`, `POLL_ANSWER_CLAIM_TTL_S`, `POLL_RECONCILE_FAST_INTERVAL_S`,
`POLL_RECONCILE_SLOW_INTERVAL_S`, `POLL_RECONCILE_FAST_WINDOW_S`,
`POLL_RECONCILE_HEARTBEAT_TTL_S` (2× the slow interval), `POLL_EXPIRY_WARN_AGE_S`,
`POLL_PROBE_TAP_WAIT_S`, plus `POLL_ADOPTION_SCAN_LIMIT` in `bridge/telegram_relay.py`.

No new secrets, no `.env` edit, no migration.

## Deploying

The bridge gains a new Telethon handler and a new background loop, so
`./scripts/valor-service.sh restart` is **required** after merge — already part of `/update`. Until
that restart, a bridge running older code discards `type: "poll"` payloads as an unknown message
type. Verify with `tail -5 logs/bridge.log` showing "Connected to Telegram".

`valor-ask-poll` only materializes after the `uv sync` / editable reinstall that
`scripts/update/run.py` already performs.

## Deliberately not built

- **1:1 DMs**, a bot identity, a `getUpdates`/webhook transport, inline keyboard buttons.
- **`teammate` sessions.**
- **Reaction-based answering.** Telethon exposes no high-level reaction event, and confirming push
  delivery would require a second client on the bridge's auth key.
- **Quorum, weighting, voter allowlists.**
- **Quiz polls, `correct_answers`, multiple-choice, free-text capture inside the poll.**
- **Attribution via `GetPollVotesRequest`** — impossible for an anonymous poll (above).
- **Auto-rendering a question posed at turn end.** `/ask-me` is the only poll trigger. A question
  surfaced through the bare `needs_human` edge is not inspected for options and still delivers text.
