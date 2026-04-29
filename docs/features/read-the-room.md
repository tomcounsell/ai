# Read-the-Room Pre-Send Pass

## What it does

Between the message drafter's output and the Telegram relay outbox write, a
small Haiku call inspects the recent chat snapshot + the candidate draft and
returns one of `send`, `trim`, or `suppress`. This catches two failure modes
that the drafter alone cannot see:

1. **Conversation-moved-on** — the agent finishes drafting after another
   participant has already answered the same question. The agent's reply
   would land redundant.
2. **Crossing-streams** — while the agent was drafting, a participant has
   shifted topic. The agent's reply would land as a non-sequitur into a
   personal exchange.

The drafter has no view of the *room state* at send time; the drafter only
sees the agent's tool outputs. Read-the-Room (RTR) is the explicit catch-all.

Sources: issue [#1193](https://github.com/tomcounsell/ai/issues/1193) and the
plan at `docs/plans/sdlc-1193.md` (or `docs/plans/completed/` after merge).

## Where it lives

| File | Purpose |
|------|---------|
| `bridge/read_the_room.py` | The RTR module. `RoomVerdict` dataclass, `read_the_room()` entry point, system prompt, snapshot fetcher. |
| `agent/output_handler.py` | Call site. `TelegramRelayOutputHandler.send` calls RTR between the drafter and the outbox `rpush`. |
| `tests/unit/test_read_the_room.py` | Unit tests for verdict parsing, snapshot construction, fail-open paths. |
| `tests/unit/test_output_handler.py::TestReadTheRoomWiring` | Handler-level wiring tests (trim coercion, queue alignment). |
| `tests/integration/test_message_drafter_integration.py` | End-to-end Path A integration tests with RTR enabled. |

## Verdicts

`read_the_room(draft_text, chat_id, session) -> RoomVerdict` returns:

| Action | Effect |
|--------|--------|
| `send` | Write the original `delivery_text` to the outbox unchanged. |
| `trim` (`len(revised_text) >= 20`) | Substitute `verdict.revised_text` for `delivery_text` and write that. |
| `trim` (`len(revised_text) < 20`) | **Coerced to suppress** — too-short trims are exactly the failure mode the feature exists to prevent (a one-emoji message landing in a personal exchange). |
| `suppress` (with `reply_to_msg_id`) | Skip the text write. Queue a 👀 reaction (`RTR_SUPPRESS_EMOJI`) on the message we were replying to so the human still gets a "received" signal. |
| `suppress` (no `reply_to_msg_id`) | **Fall through to send the original text.** Without an anchor we cannot emit the reaction, and silent suppression breaks the I-heard-you contract — fall-through preserves the audit signal. |

## Snapshot defaults

* `K = 10` messages newest-first.
* `max_age_seconds = 300` (last 5 minutes).
* Whichever cap fires first applies.
* The snapshot is **passed through unfiltered**: entries from `sender ∈
  {Valor, system}` are the agent's own prior turns (Path A messages flow
  in through two recording sites, see Risk 3 of the plan); the prompt
  tells the model to treat them as agent-authored context, not as
  competing input.

Tune via the `DEFAULT_K` and `DEFAULT_MAX_AGE_SECONDS` module constants.

## Bypass conditions (no Haiku call)

RTR short-circuits to `send` without calling Haiku in any of:

* `READ_THE_ROOM_ENABLED` env var is unset/false (default for first
  rollout — opt in per machine).
* `draft_text` is empty / whitespace-only.
* `chat_id` is `None` (file-only delivery, etc.).
* `len(draft_text) < SHORT_OUTPUT_THRESHOLD` — aligns with the drafter's
  bypass band (200 chars) so we don't pay RTR latency in the same range
  the drafter already skipped.
* `session.sdlc_slug` is set — emits a `rtr.bypassed` event so SDLC
  pipeline status messages are observable but never blocked.
* The fetched snapshot is empty — no room to read.

## Failure modes

The RTR call is wrapped in a fail-open guard. Any error returns `send` with
`reason="rtr_error"` and emits a `rtr.failed` `session_event`:

* `anthropic.APITimeoutError` — SDK-level 3-second timeout fires.
* `anthropic.APIConnectionError` — httpx connection issue.
* `anthropic.APIError` — any other Anthropic API error.
* `ValueError` — malformed `room_verdict` tool_use response.
* Last-resort `Exception` — anything else.

The post-#1055 hotfix pattern is mandatory: `semaphore_slot()` for
concurrency gating + an inner `async with anthropic.AsyncAnthropic(timeout=3.0)`
for httpx-level cleanup on cancellation. **Do not** wrap with
`asyncio.wait_for` — that leaks httpx connections.

## Observability

All non-`send` outcomes append a `session_event` to
`session.session_events`. The schema is:

```json
{
  "type": "rtr.<outcome>",
  "ts": 1745923200.123,
  "chat_id": "-100123",
  "reason": "<short reason string>",
  "draft_preview": "<first 200 chars of draft>",
  "revised_preview": "<first 200 chars of revised, when applicable>",
  "error": "<exception class name, only for rtr.failed>"
}
```

Event types:

| Type | When |
|------|------|
| `rtr.suppressed` | Suppress verdict applied (with reaction emitted, OR coerced-from-short-trim). |
| `rtr.suppress_fallthrough` | Suppress verdict but no `reply_to_msg_id` anchor — original text was sent and this event records why. |
| `rtr.trimmed` | Long-form trim verdict applied. |
| `rtr.bypassed` | RTR short-circuited (currently emitted only for SDLC sessions). |
| `rtr.failed` | RTR raised an exception; fell open to `send`. |

Inspect via `valor-session inspect --id <session_id>` or
`/dashboard.json`.

## Path B (`valor-telegram send`) coverage

**Out of scope for this feature.** `valor-telegram send`
(`tools/valor_telegram.py`) writes directly to the Redis outbox and does
not pass through `agent/output_handler.py`. Adding RTR there requires a
sync→async bridge and changes the CLI's latency profile for every
invocation including human-invoked sends. The deferral is tracked in a
follow-up issue filed against this feature; see `docs/plans/sdlc-1193.md`
§ "Step by Step Tasks > Path B follow-up issue" for the rationale.

## Cost

One Haiku call per outbound Path A message that survives all bypass
conditions. At 50–100 outbound messages/day this is roughly $0.10/month
at Haiku rates. The `rtr.bypassed` event count makes the bypass rate
observable; if RTR is firing on >90% of sends, the heuristic is wrong.

## Rollout

1. Land the code with `READ_THE_ROOM_ENABLED=false` (default).
2. Flip the flag in `~/Desktop/Valor/.env` on the dev/test machine first.
3. Watch a low-stakes chat for one day; sample `session_events` for
   suppressed messages and judge the false-positive rate.
4. If false-positive rate > 10%, tighten the system prompt or raise the
   suppression bar. Otherwise flip the flag in production.

## Related documentation

* [`docs/features/bridge-worker-architecture.md`](bridge-worker-architecture.md) — Path A flow showing where RTR sits.
* [`docs/features/message-drafter.md`](message-drafter.md) — the upstream drafter whose output RTR inspects.
* [`docs/features/single-machine-ownership.md`](single-machine-ownership.md) — bridge ownership model RTR runs inside.
