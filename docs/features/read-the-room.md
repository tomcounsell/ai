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

Sources:
- Path A — issue [#1193](https://github.com/tomcounsell/ai/issues/1193), implementation in
  PR [#1204](https://github.com/tomcounsell/ai/pull/1204) at commit `531e8f4e`.
- Path B (`valor-telegram send`) — issue [#1203](https://github.com/tomcounsell/ai/issues/1203).

## Where it lives

| File | Purpose |
|------|---------|
| `bridge/read_the_room.py` | The RTR module. `RoomVerdict` dataclass, `read_the_room()` entry point, system prompt, snapshot fetcher. |
| `agent/output_handler.py` | **Path A** call site. `TelegramRelayOutputHandler.send` calls RTR between the drafter and the outbox `rpush`. |
| `tools/valor_telegram.py` | **Path B** call site. `cmd_send` calls RTR after linkify+truncate, before the outbox `rpush` (issue #1203). Caller-type gate auto-detects agent vs. human invocations. |
| `tests/unit/test_read_the_room.py` | Unit tests for verdict parsing, snapshot construction, fail-open paths. |
| `tests/unit/test_output_handler.py::TestReadTheRoomWiring` | Path A handler-level wiring tests (trim coercion, queue alignment). |
| `tests/unit/test_valor_telegram.py::TestCmdSendRTR` | Path B CLI wiring tests (caller-type gate, flag overrides, verdict branching, fail-open). |
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

## Coverage (Path A + Path B)

RTR runs on **two** independent message paths into the Telegram outbox.
Both share the same public `read_the_room()` entry point, the same
`READ_THE_ROOM_ENABLED` machine-wide gate, and the same fail-open
semantics. They differ only in *who is invoking the path* and therefore
in the gating logic that decides whether to call RTR at all.

| Path | Entry point | Default behavior |
|------|-------------|------------------|
| **Path A** | `agent/output_handler.py::TelegramRelayOutputHandler.send` (the worker's drafter→outbox flow) | Always agent-driven. RTR runs whenever `READ_THE_ROOM_ENABLED=true`. |
| **Path B** | `tools/valor_telegram.py::cmd_send` (the `valor-telegram send` CLI registered in `pyproject.toml`) | RTR runs only for agent-invoked sends; human-invoked sends bypass RTR by default. |

### Caller-type gate (Path B only)

`cmd_send` distinguishes agent invocations from human invocations using
`VALOR_SESSION_ID`, the canonical "we are inside an AgentSession" env
var injected by the worker via
`agent/sdk_client.py:_extract_sdlc_env_vars`. The decision rule:

1. `--no-read-the-room` flag → **off**, regardless of env.
2. `--read-the-room` flag → **on**, regardless of env.
3. Otherwise: **on** iff `VALOR_SESSION_ID` is set + non-empty.

The two flags are mutually exclusive at argparse level (passing both
exits non-zero with a usage error).

### Opt-in / opt-out matrix

| Caller | `VALOR_SESSION_ID` | Flag | RTR runs? |
|--------|--------------------|------|-----------|
| Worker drafter (Path A) | (n/a) | (n/a) | **Yes** when `READ_THE_ROOM_ENABLED=true`. |
| Agent Bash → `valor-telegram send` (Path B) | set | none | **Yes** when `READ_THE_ROOM_ENABLED=true`. |
| Agent Bash → `valor-telegram send` (Path B) | set | `--no-read-the-room` | **No**. |
| Human shell → `valor-telegram send` (Path B) | unset | none | **No**. |
| Human shell → `valor-telegram send` (Path B) | unset | `--read-the-room` | **Yes** when `READ_THE_ROOM_ENABLED=true`. |
| Human shell with inherited env (sub-shell, `tmux`, `claude --resume`) | set (inherited) | none | **Yes** (false positive). Use `--no-read-the-room` to opt out. |

### Activation diagnostic (Path B)

Immediately before the `asyncio.run(read_the_room(...))` call, `cmd_send`
prints `(RTR active — running pre-send pass)` to stderr. This makes
accidental env inheritance visible — a human who pops a fresh terminal
*from inside* a running agent context will silently inherit
`VALOR_SESSION_ID`, and the diagnostic gives them a chance to `Ctrl-C`
and retry with `--no-read-the-room`.

Path A doesn't need this signal because it's always agent-driven.

### Why Path B has its own caller-type gate

Path B is also used by humans (ad-hoc `valor-telegram send` invocations
from a shell). RTR running on a deliberate human-authored message would
be both wasteful (a Haiku call on every manual send) and surprising
(suppressing a human's intentional send with a 👀 reaction). The
auto-detection rule keeps the default sensible without plumbing a new
flag through every agent prompt.

Path A has no such concern — it only runs when the worker is drafting an
outbound message, which is always agent-authored by definition.

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

## Adjacent layers

**Drafter Redundancy Suppression** (`bridge/redundancy_filter.py`, issue #1205) runs before RTR in the Path A funnel, but only for SDLC sessions. It uses deterministic bigram-Jaccard similarity (no LLM call) to suppress near-verbatim PM status repeats within a configurable time window. When it suppresses, RTR is not called (early return). When it passes, RTR runs as normal.

The two layers compose cleanly:
- SDLC sessions: redundancy filter first → RTR after (though RTR's SDLC bypass means RTR is effectively a no-op for SDLC sessions today).
- Non-SDLC sessions: redundancy filter skipped → RTR runs.

See [Drafter Redundancy Suppression](drafter-redundancy-suppression.md) for full details.

**PM Completion Runner Haiku Judge** (`agent/session_completion.py::_judge_completion_novelty`, issue #1262) is a *separate* Haiku call site distinct from RTR. It runs only inside `_deliver_pipeline_completion` for the borderline band of the post-draft suppression check (Jaccard `[0.55, 0.75)`), with its own tool schema (`completion_novelty_verdict` with `restate`/`new` enum) and 3-second timeout. It deliberately does NOT share code with RTR: RTR judges room context against a candidate draft; the completion-novelty judge compares two specific message strings (prior mid-session send vs. drafted final summary). Both follow the same fail-open pattern (`semaphore_slot()` + inline `anthropic.AsyncAnthropic(timeout=3.0)`); if the RTR Haiku model identifier or timeout changes, audit `_judge_completion_novelty` for parallel updates. See [PM Final Delivery: mid-session-send-aware completion suppression](pm-final-delivery.md#mid-session-send-aware-completion-suppression).

## Related documentation

* [`docs/features/bridge-worker-architecture.md`](bridge-worker-architecture.md) — Path A flow showing where RTR sits.
* [`docs/features/drafter-redundancy-suppression.md`](drafter-redundancy-suppression.md) — the deterministic SDLC-specific guard that runs before RTR.
* [`docs/features/message-drafter.md`](message-drafter.md) — the upstream drafter whose output RTR inspects.
* [`docs/features/single-machine-ownership.md`](single-machine-ownership.md) — bridge ownership model RTR runs inside.
