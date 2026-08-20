---
title: Live voice bridge — holding a call and answering live
slug: live-voice-bridge
type: feature
status: Draft
appetite: Large
tracking: none
created: 2026-08-20
---

# Live Voice Bridge

## The question

> Would that be a tool for the agent session — connecting to a call? Or would it
> be a bridge module that used agent sessions as a tool?

The second, with a layer the question doesn't have a name for yet. The first is
not merely worse — it is structurally impossible against this architecture.

## Why "a tool the session calls" cannot work

A session turn is one `claude -p --output-format stream-json` subprocess driven
by `agent/session_runner/runner.py`. Four properties of that shape each kill the
tool direction independently:

1. **Tool calls are blocking and synchronous.** A `join_call()` tool would hold
   the turn open for the duration of the call. The only bound on it is the turn
   ceiling — `TEAMMATE_TURN_TIMEOUT_S = 900`, `ENG_TURN_TIMEOUT_S = 7200`
   (`agent/session_runner/runner.py:106-108`). Nothing else in the turn runs
   while it blocks.
2. **There is no inbound channel into a running turn.** The caller's next
   sentence has to reach the agent *now*. The only injection point is the
   steering inbox, and the worker drains it at **turn boundaries**
   (`agent/session_runner/runner.py::_default_steering_pop`, line 587). Mid-turn,
   a session is deaf by construction.
3. **Latency is off by two to three orders of magnitude.** A call needs the
   first phoneme within roughly 300–500ms of end-of-speech. A turn is process
   spawn + prime + tool loop: seconds at best, minutes normally. The system is
   *designed* around that — ~1s worker pickup via `valor:sessions:new` pub/sub is
   considered fast here.
4. **Nothing in a session is a stable home for a socket.** The worker spawns and
   reaps a subprocess per turn; a call must survive turn boundaries. Whatever
   holds the WebRTC peer connection has to outlive every turn taken during the
   call.

## Why "a bridge module" is right but insufficient

Structurally a call is a third transport, and the seams for that already exist:

| Seam | Telegram | Email | Voice |
|------|----------|-------|-------|
| Long-lived process holding the medium | `bridge/telegram_bridge.py` | `bridge/email_bridge.py` | new |
| Inbound → session | `dispatch_telegram_session` | `enqueue_agent_session(transport="email")` | `transport="voice"` |
| Outbound queue | `telegram:outbox:{sid}` | `email:outbox:{sid}` | `voice:outbox:{sid}` |
| Room addressee (`models/room.py`) | `telegram:{chat_id}` | `email:{addr}` | `voice:{call_id}` |
| Drafter medium (`bridge/message_drafter.py`) | `telegram` | `email` | `voice` |
| Registered handler | `register_callbacks(..., transport=…)` | same | same |

But wiring only that gives you a very slow voicemail: utterance in → session
enqueued → 10–60s of silence on the line → TTS. Nobody stays on that call. The
transport seam is necessary and nowhere near sufficient.

## The shape that works: two tiers, split on latency

**Tier 1 — the call agent.** A bridge-side process that owns the audio socket
(LiveKit / Twilio / raw WebRTC) and runs a realtime speech-to-speech model. It
does turn-taking, barge-in, backchannel, and answers anything answerable from
the conversation and its primed context. It holds no tools that can block and it
never waits on Redis. This is the thing that "holds the call."

**Tier 2 — AgentSessions as the call agent's tool.** When the caller asks for
something that needs real work — read the repo, check CI, query the KB, write
code — tier 1 enqueues an AgentSession (`transport="voice"`) and *keeps talking*.
The session's `tools/send_message.py` output lands on `voice:outbox:{session_id}`;
tier 1 drains it and speaks it into the call as an interruption: "okay — the
build's failing on the linter, line 40."

So the inversion the question was circling: **the call is not a tool the session
picks up; the session is a tool the call picks up.**

### The tier boundary is a latency boundary, not a capability boundary

The rule that keeps this from rotting: anything that must answer inside one
conversational beat lives in tier 1 and gets **no tools**. Anything that needs a
tool call lives in tier 2 and is narrated asynchronously. Any tier-1 tool that
can take longer than a beat is a bug — it will produce dead air, and dead air is
how callers hang up.

## What this costs, honestly

- **Two models speak as Valor.** The realtime model has different priors than the
  composed persona system. Either accept the drift or feed the composed persona
  prompt in as its system prompt and keep them in sync — a real ongoing cost.
- **Single-machine ownership applies.** A phone number or call identifier is a
  bridge-contact identifier under the strict-ownership rule. It needs
  `projects.<key>.machine` resolution and a new arm in
  `bridge/config_validation.py::validate_projects_config`, or two machines answer
  the same call.
- **The drafter is text-shaped.** `draft_message(medium=…)` guards length, emits
  markdown, appends link footers, attaches files over a threshold. A `voice`
  medium needs the opposite: no markdown, no URLs, no code blocks, sentences a
  person can follow without seeing them.
- **Latency of tier 2 is user-visible.** A "still working on it" cadence is not
  polish, it is load-bearing. Without it a 40-second session read as a dropped
  call.
- **Durability.** The call transcript should append to the Room inbox, or a call
  leaves no trace in the memory/KB systems that every other medium feeds.
- **Cost.** A held call is a metered realtime connection for its full duration,
  independent of whether anyone is talking.

## Smallest first build

One number, one project, tier 1 only, exactly one tool: `ask_valor(question)` →
enqueue a teammate session → narrate the answer back into the call when it
lands. That proves the narration seam — the only genuinely new mechanism here —
before any work on call routing, multi-party, or transfer.
