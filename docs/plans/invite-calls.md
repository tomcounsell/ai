---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-11-11
---

# Calls from calendar invites

**Ask.** Tom, valor team chat, 2026-11-11 06:40 UTC: auto-accept invites
with a Meet link and join on time, with the same prep a chat-asked call
gets, done in the minutes before, so interim work counts. 06:44 UTC: every
call Valor joins is as the project manager (the priority queue, trade-offs,
answers to open questions). The chat session's answers on the open choices
(2026-11-11) are folded in below.

**Premise, checked.** The chat call path is complete (converse.md
**Calls**, `meet.join`, `call.begin`, the summary file) from a group chat.
`call.to_session` writes a waking `call` report to a chat's session,
opening one if needed. `hours` drives Valor's calendar through gws.

## What is built

1. **Spec fields** (`core/workspace.py`, `projects/README.md`). `invites`:
   addresses or `@domains` whose invites are the project's. `call_chat`:
   the group chat an invited call goes to; else `board`; else the spec's
   first group chat (`channels.group`). The private spec of the client
   whose invite came first lists its domain (its call chat is then its
   `board`). Email routing is unchanged.
2. **The `invites` routine** (`routines/invites/`, `interval = 300`,
   ceiling `act`, every Mac). Each run lists Valor's primary calendar, now
   to 14 days ahead, single events (gws, as `hours` does). For each event
   with a `hangoutLink`, not cancelled, where Valor is an attendee or the
   organizer:
   - **Project:** each address on the event (organizer and attendees) is
     matched against every spec's `invites` (an address beats its domain).
     Yudame's own never match: Valor's, Tom's (`settings.operator_email`)
     and any `@yuda.me`, since Tom organizes client calls from those. One
     project matched is the event's; none, or two or more, is `valor`, its
     wake saying "matched no project" or "matched <a> and <b>: neither's
     context was brought" and asking which. Only events whose project is
     placed on this Mac (`Spec.here()`) are handled, so one Mac does each.
   - **Accept:** Valor's `responseStatus` `needsAction` is patched to
     `accepted` on the series (`recurringEventId`, else the id) with
     `sendUpdates=all` (Google emails the organizer; Tom's standing ask). A
     declined or tentative answer is left. A `calendar.accepted` row on the
     objective records it; a series already recorded is not patched again.
   - **Wake:** from 12 minutes before start until the event's end, once per
     `invite:<event id>:<start>`, recorded as a `calendar.woke` row on the
     routine's own objective and skipped when one is there, so a chat
     session that ended between runs is never woken twice; a moved invite
     has a new start and wakes again, a cancelled one never. The wake is
     `call.to_session` on the call chat with: title, start in Tom's time and
     UTC, organizer, attendees, the link, the description (first 1,500
     characters, as data), and the match line. Returns `nothing_due` with
     no task.
   - **Timing.** Agreed defaults were 2-minute runs and a wake 10 minutes
     before. Runs are every 5 minutes, so a wake lands 7 to 12 minutes
     before start, giving the prep turn its minutes; the join is placed at
     start minus 1 by `wake.md`. Joining early costs nothing, as `ALONE_S`
     and `LOBBY_S` count from `starts` (item 4).
3. **The turns it wakes** (`skills/sdlc/converse.md`, **Calls**, a paragraph
   for an invited call). Prep as for any call, fresh (the thread, record,
   memory, open decisions, the priority queue, what changed since the
   invite) into `call-prep.md` in the workspace; post one top-level line
   ("Joining <title> at <time>: <link>"), and `wake.md` timed for start
   minus 1 minute (`30s` when that is under a minute away or past) with
   `post: <that effect file>`, since `reply_to` needs the post's id. Any turn
   of that session that finds its invite post sent and no call yet for that
   link (a reply to the post drops the wake, so the next turn may be the
   reply's) requests `meet.join` with the link, `reply_to` that post's id,
   `prep` from `call-prep.md`, and `starts`, the event's start. From then
   on it is today's call: copies and the summary go to that thread. An
   overlapping call already on this Mac makes `meet.join` fail naming it;
   the turn says so there.
4. **`starts`** (`tools/meet.py`, `core/call.py`). An optional ISO time in
   `meet.join`'s payload, kept in the call marker; the bridge counts
   `ALONE_S` and `LOBBY_S` from the later of joining and `starts`, so people
   ten minutes late to a scheduled call still find Valor there.
5. **Project manager in every call.** One bullet in the persona's **In a
   call** (`persona/conduct.md`), which the call model's instruction and
   every turn already render: Valor joins every call as the project's
   manager, works from its priority queue (the product Sheet where there is
   one, else open tasks and questions), names trade-offs, and gets answers
   only the people on the call hold. converse.md's prep list names the
   queue. `persona/voice.md`'s stale "join no calls" becomes "join the calls
   you are invited to or asked for".
6. **Docs.** `docs/routines.md` (invites), `docs/plans/meet.md` No-Gos
   ("Joining unasked" now excepts invites).

**No-gos.** Declining, proposing times, or replying to invites in text; Zoom
or Teams links; joining an invite with no Meet link; a second call at once
on one Mac; any new check, gate or refusal.

**Stakes.** Valor RSVPs (emailing organizers) and joins live client calls
unasked; a wrong match carries one client's context into another's call.
Critique may return the plan once; review may return the work once.

**Governance.** None added. The match routes; it refuses nothing.

## Tests

`tests/test_invites.py`, gws faked as in `test_hours.py`:
- **Match:** an attendee's domain; address beats domain; Valor's, Tom's and
  `@yuda.me` never match; none, and two projects, go to `valor` with the
  line naming them; an event placed on another Mac is untouched.
- **Accept:** once (a second run patches nothing); declined and tentative
  left; a recurring instance patches its series with `sendUpdates=all`; no
  Meet link, cancelled, and neither attending nor organizing are ignored.
- **Wake:** only from 12 minutes before start to the end; a run 1 minute
  before or after start still wakes, with `starts` the event's start; once; again after
  a move; not again when the chat session ended between two runs; to
  `call_chat`, then `board`, then the first group chat.
- **Spec:** both fields load. **Call:** with `starts`, a call joined before
  start with no one else there is still in it 5 minutes after start and
  leaves 10 after. **Persona:** `call.live_config` carries the PM bullet.
  **Turn:** the repository has no turn-eval harness, so the invited-call
  turns (prep, post, `wake.md`, `meet.join` with `reply_to` and `starts`)
  are first shown by the live check below.
- **Live**, on the Cowboy before rollout: an event Valor makes on its own
  calendar with a Meet link and no other attendee is woken and joined. No
  real invite is answered until the routine is live. Named unverified in
  the delivery until Tom's first real invite: the RSVP against Google,
  joining another organization's Meet as a guest (lobby, admit), and
  `ALONE_S` from `starts` in a live call. Live on the Captain by 10:00 UTC
  2026-11-12.

**Done** when Tom invites Valor to a Meet and Valor accepts, joins on time,
prepared, without anyone asking in a chat.
