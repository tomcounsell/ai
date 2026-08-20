---
title: Meet listener — join by calendar invite, listen, comment in chat
slug: live-voice-bridge
type: feature
status: Draft
appetite: Medium
tracking: none
created: 2026-08-20
---

# Meet Listener

Valor holds a dedicated Google account. It gets invited to a meeting like any
other member, joins, listens, and posts useful answers into the meeting chat.
Text out only — it never speaks.

## The invite model is the right one, and it solves three problems at once

Inviting the bot account to the calendar event is not just a nicer UX than
pasting a link. It is load-bearing:

1. **It is the trigger.** The bridge polls the bot account's own calendar for
   events carrying a `hangoutLink` and schedules a join. No per-meeting config,
   no link handoff.
2. **It is the access control.** Under Meet's default *Trusted* access type,
   anyone in the host's org joins without knocking, **and so does anyone outside
   the org who was invited through a Google Calendar event**
   ([Workspace Updates](https://workspaceupdates.googleblog.com/2023/06/simplified-access-controls-for-google-meet.html)).
   The invite is what clears the lobby, cross-org included. Opt-in is per
   meeting, granted by whoever runs it, using Google's model rather than one we
   invent. Uninviting is the off switch.
3. **It is the priming payload.** The event carries title, description,
   attendee list, and attached docs — everything needed to join warm instead of
   generic. A cold join produces generic comments; this is where quality comes
   from.

RSVP is a real affordance too: the bot can decline a meeting it judges itself
useless for, and that decline is visible to the organizer.

## The API question, answered plainly

Google publishes exactly three Meet surfaces
([SDK and API overview](https://developers.google.com/workspace/meet/overview)):

| Surface | Status | What it does | Joins as a participant? | Posts to chat? |
|---|---|---|---|---|
| Meet **add-ons SDK** | GA | Embeds an app panel inside Meet for participants who install it | no | no |
| Meet **REST API** | GA | Meeting spaces, conferences, participants, artifacts — post-hoc records | no | no |
| Meet **Media API** | Developer Preview | Real-time audio, video, participant metadata | no | no |

**None of the three joins a meeting as a participant, and none can send a
message to the in-meeting chat.** That is not a gap in the docs; it is the
current shape of the product.

The Media API is also gated hard: the Cloud project, the OAuth principal, **and
every participant in the conference** must be enrolled in the Developer Preview
Program, and it refuses meetings with encryption or a watermark
([overview](https://developers.google.com/workspace/meet/media-api/guides/overview)).
For any meeting with an outside attendee that is a non-starter.

### What the third-party notetakers actually do

Both patterns exist in the market, and they split exactly along this line:

- **Bot-free** — [Fireflies' Google Meet SDK mode](https://guide.fireflies.ai/articles/3309351579-integrate-google-meet-sdk-with-fireflies-for-bot-free-meeting-recording)
  and Read.ai's native integration ride the official media path. They listen
  well and **post nothing into the meeting** — they hand you notes afterward.
  That is not a product choice; it is the API's ceiling.
- **Browser participant** — Recall.ai, MeetingBaaS, Attendee, and Fireflies'
  own `fred@fireflies.ai` bot join as a real participant. These are the ones
  that can type in chat.

So "use the same API the third-party apps use" gets us the listening half and
stops dead at the commenting half. The vendors we would be imitating cannot do
the thing we actually want.

## The fork

**A. Official Media API, output elsewhere.** Listen via the sanctioned path;
send the comment to Telegram or email instead of meeting chat. Clean, no
automation risk, no visible bot — but it is a different feature. The remark
lands where Valor already lives, not where the conversation is happening, and it
misses everyone else in the room.

**B. Browser participant** (recommended). The dedicated account signs in to a
persistent Chromium profile, joins from the calendar event, mutes mic and
camera, reads Meet's own live captions for a speaker-attributed transcript, and
types into the chat panel. This is the only option that satisfies both halves of
the ask, and it is literally "joins the same way other members do."

Costs, stated honestly: it is browser automation against a UI Google can change
without notice, so the caption and chat selectors are a standing maintenance
tax; Google discourages automated meeting clients, so the account carries some
risk of being flagged; and it appears in the roster, which is a feature, not a
bug — name it so it self-discloses.

## Structure

Two tiers, split on latency — listening removes the realtime constraint, so
tier 1 collapses from a voice model to a cheap classifier.

**Tier 1 — the meeting watcher** (bridge-side, continuous, no tools). Holds the
Meet tab, maintains a rolling caption window, and on each speaker-turn boundary
asks a Haiku-class classifier one question: *is there an open question, a
checkable claim, or a named artifact I can usefully answer?* Default verdict is
silence. Anything answerable from the primed event context goes out directly.

**Tier 2 — AgentSessions as the watcher's tool.** Anything needing a lookup —
repo, KB, memory, an issue, prior meetings — enqueues an AgentSession
(`transport="meet"`). Output lands on `meet:outbox:{session_id}`; the watcher
types it into the chat panel.

The rule holds from the voice design, only the clock is kinder: **anything
needing a tool call goes to tier 2.**

## What the repo already has

- `tools/google_workspace/auth.py` — OAuth plumbing and `get_service("calendar",
  "v3")`, already consumed by `tools/valor_calendar.py`. **But it holds one
  identity per machine** (`google_token.{machine}.json`, scope
  `.../auth/calendar`). The bot account is a *second identity on the same
  machine*, so it needs an account-scoped token path, not a second machine.
- `bridge/redundancy_filter.py::should_suppress` — bigram-Jaccard duplicate
  guard.
- `bridge/read_the_room.py::read_the_room` — a `send | trim | suppress` verdict
  pass, fail-open.

The last two are close to drop-in for the comment gate; this codebase has
already solved "don't be noisy" twice. On top of them: a hard cap on comments
per meeting and a cooldown, with silence as the default.

## Two constraints specific to this medium

**Staleness.** Telegram and email have no moving present tense — a late reply is
still a reply. A meeting does. If tier 2 takes 40 seconds and the room has moved
on two topics, the comment is worse than useless. Before typing, the outbox
drain re-checks the answer against the current caption window and either posts,
reframes with an explicit back-reference ("earlier, on the migration — …"), or
drops it. Nothing in the existing outbox path does this.

**One meeting at a time.** Calendars overlap; a single signed-in account cannot
cleanly attend two conferences. v1 takes the first and logs the skip.

Ownership follows the strict rule: the bot's Google account is a bridge-contact
identifier and resolves to exactly one machine via `projects.<key>.machine`,
enforced in `bridge/config_validation.py::validate_projects_config`. Two
machines joining as the same account is a live failure mode, not a theoretical
one.

## Smallest first build

One recurring internal meeting. Calendar-triggered join, captions-only
transcript, no tier 2, and **post nothing** — log the comments it would have
made. Read that log after three meetings. If the gate is right, wire the chat
panel; if it is wrong, nobody in the meeting had to watch it be wrong.

---

A platform- and architecture-neutral PRD for this feature — goals, principles, capabilities,
measurement, and rollout, written to be implementable by any sufficiently capable agent system —
lives alongside this doc at [`quiet-participant-prd.html`](quiet-participant-prd.html). This file
is the architecture-coupled counterpart: it records what the shape costs *here*.
