---
title: Meet listener — join a Google Meet, listen, comment in chat
slug: live-voice-bridge
type: feature
status: Draft
appetite: Medium
tracking: none
created: 2026-08-20
---

# Meet Listener

Join a Google Meet by link, listen, and post useful answers into the meeting
chat. Text out only — never speaks.

## What the listen-only constraint buys

The full-duplex voice case (hold a call, talk back) is the hard superset: it
needs sub-second turn-taking, barge-in, and a realtime speech-to-speech model
that can never block. Listening and typing removes all of it.

| | Voice reply | Chat comment |
|---|---|---|
| Latency budget | ~300–500ms to first phoneme | ~5–20s and still reads as live |
| Turn-taking | barge-in, VAD, interruption | none — chat has no floor to hold |
| Model in the loop | realtime speech-to-speech | transcript in, text out |
| Hard problem | latency | **knowing when to speak at all** |

That last row is the whole design. A 15-second-old comment is fine. A comment
nobody needed is not. The center of this feature is a relevance gate, not a
pipeline.

## Structure

Still two tiers split on latency, but tier 1 collapses from a realtime voice
agent to a cheap classifier:

**Tier 1 — the meeting watcher** (bridge-side, continuous, no tools). Holds the
Meet session, maintains a rolling transcript window, and on each speaker-turn
boundary asks a Haiku-class classifier one question: *is there an open question,
a checkable claim, or a named artifact here that I can usefully answer?* Default
verdict is silence. Answers available from primed context alone go out directly.

**Tier 2 — AgentSessions as the watcher's tool.** Anything needing a lookup —
the repo, the KB, memory, an issue, prior meetings — enqueues an AgentSession
(`transport="meet"`). Output lands on `meet:outbox:{session_id}`; the bridge
types it into the chat panel.

The rule from the voice design survives intact, only the clock is kinder:
**anything needing a tool call goes to tier 2.**

## Joining: three options, one viable today

| Option | Listens | Posts to chat | Blocker |
|---|---|---|---|
| [Meet Media API](https://developers.google.com/workspace/meet/media-api/guides/overview) | yes, native streams | **no** | Developer Preview; *every participant* must be enrolled; blocked by encryption/watermark |
| Headless-browser participant | via live captions | yes | Meet DOM fragility; needs a real Google account admitted to the call |
| Bot vendor ([Recall.ai](https://www.recall.ai/product/meeting-bot-api/google-meet) ~$0.50/hr, [MeetingBaaS](https://www.meetingbaas.com/en/meeting-bot-api-for-google-meet) ~$0.35–0.69/hr) | yes, ~200ms WebSocket | yes | per-hour cost; third party in the room |

The Media API is out: it cannot post to chat, and the all-participants
enrollment requirement kills any meeting with outside attendees.

**Start with the browser participant.** The infra is already here — Chromium is
installed, Playwright is configured against it — and Meet's own **live captions
give a speaker-attributed transcript for free**, so no ASR is needed at all.
Move to a vendor if DOM fragility becomes a real maintenance tax.

### Do not use BYOB for this

BYOB drives the user's *real* Chrome and is serialized at the scheduler by
`requires_real_chrome` (`agent/session_pickup.py`) because real Chrome has one
DOM tree. A 45-minute meeting held through BYOB would block every other
real-Chrome session for its full duration. The Meet listener needs its own
dedicated Chromium instance, its own profile and Google account, owned by the
bridge process — not by an agent session.

## What this repo already has for the hard part

The "don't be noisy" problem is one this codebase has solved twice:

- `bridge/redundancy_filter.py::should_suppress` — bigram-Jaccard duplicate
  guard, already used to stop the agent repeating itself.
- `bridge/read_the_room.py::read_the_room` — a `send | trim | suppress` verdict
  pass with a fail-open contract.

Both are drop-in for the comment gate. On top of them this medium needs a hard
budget: a cap on comments per meeting and a cooldown, with silence as the
default and breaking it deliberately expensive.

## The one genuinely new mechanism: staleness

Telegram and email have no moving present tense — a late reply is still a reply.
A meeting does. If tier 2 takes 40 seconds and the discussion has moved on two
topics, the comment is worse than useless.

So the `meet:outbox` drain cannot just post what it finds. Before typing, it
re-checks the answer against the current transcript window and either posts,
reframes with an explicit back-reference ("earlier, on the migration — …"), or
drops it. Nothing in the existing outbox path does this.

## Costs and constraints

- **The bot is a participant.** It appears in the roster under whatever account
  it uses. Name it plainly and let attendees know it is there — a listener in a
  meeting is a recording device and should not be a surprise.
- **Single-machine ownership.** A meeting identifier is a bridge-contact
  identifier under the strict rule; it needs `projects.<key>.machine` resolution
  and an arm in `bridge/config_validation.py::validate_projects_config`, or two
  machines join the same call.
- **The drafter is chat-shaped already**, but Meet chat is not Telegram: no
  markdown rendering, short lines, no file attachments. A `meet` medium on
  `draft_message` handles this.
- **Priming decides quality.** Joining cold produces generic comments. The
  watcher should prime on the calendar event, attendees, any agenda doc, and
  linked issues before the meeting starts.
- **Durability.** The transcript should append to the Room inbox so a meeting
  feeds memory and the KB the way every other medium does.

## Smallest first build

One recurring internal meeting, joined by link, captions-only transcript, no
tier 2 at all: post nothing, and instead write the comments it *would* have made
to a log. Read that log after three meetings. If the gate is right, wire the
chat panel; if it is not, no one in the meeting ever had to see it being wrong.
