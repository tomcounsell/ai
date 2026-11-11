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

**Premise, checked.** A chat session's call path is complete (converse.md
**Calls**, `meet.join`, `call.begin`, the `call` reports, the summary file);
it needs a group chat. `call.to_session` writes a `call` report to a chat's
session once per `about_key`, opening it if needed, and a report wakes it.
The `hours` routine already drives Valor's calendar through gws per Mac. A
turn's `wake.md` with `post:` gives the next turn that post's message id.

## What is built

1. **Spec fields** (`core/workspace.py`, `projects/README.md`). `invites`:
   addresses or `@domains` whose invites are the project's. `call_chat`:
   the group chat an invited call goes to; else `board`; else the spec's
   first group chat (`meet.group_chat`). Mastery's private spec gets
   `invites = ["@pursue-mastery.com"]` (its call chat is then `board`,
   #mastery-dev). Email routing is unchanged.
2. **The `invites` routine** (`routines/invites/`, `interval = 300`,
   ceiling `act`, every Mac). Each run lists Valor's primary calendar, now
   to 14 days ahead, single events (gws, as `hours` does). For each event
   with a `hangoutLink`, not cancelled, where Valor is an attendee or the organizer:
   - **Project:** the organizer's address, then each attendee's in order
     (Valor's own skipped), matched against every spec's `invites` (an
     address beats its domain; specs in name order); none matched is
     `valor`. Only events whose project is placed on this Mac
     (`Spec.here()`) are handled, so one Mac handles each.
   - **Accept:** Valor's `responseStatus` `needsAction` is patched to
     `accepted` on the series (`recurringEventId`, else the id) with
     `sendUpdates=all` (Google emails the organizer; Tom's standing ask). A
     declined or tentative answer is left as set. A `calendar.accepted` row
     on the objective records it.
   - **Wake:** from 13 minutes before start until the event's end,
     `call.to_session` on the call chat, `about_key`
     `invite:<event id>:<start>` (so a moved invite wakes again at its new
     time, and a cancelled one never), with: title, start in Tom's time,
     organizer, attendees, the link, the description (first 1,500
     characters, as data), and when none matched, "matched no project: ask
     for the domain to map". Returns `nothing_due` with no task.
3. **The turn it wakes** (`skills/sdlc/converse.md`, **Calls**, a paragraph
   for an invited call). Do the prep as for any call, fresh (the thread,
   record, memory, open decisions, the priority queue, what changed since
   the invite) into `call-prep.md` in the workspace; post one top-level line
   ("Joining <title> at <time>: <link>"), and `wake.md` `60s` with
   `post: <that effect file>`. Woken, request `meet.join` with the link,
   `reply_to` that post's id and `prep` from the file. From then on it is
   today's call: the link report needs no second post, copies and the
   summary go to that thread. An overlapping call already on this Mac makes
   `meet.join` fail naming it; the turn says so in that thread.
4. **Project manager in every call.** One bullet in the persona's **In a
   call** (`persona/conduct.md`), which the call model's instruction and
   every turn already render: Valor joins every call as the project's
   manager, works from its priority queue (the product Sheet where there is
   one, else open tasks and questions), names trade-offs, and gets answers
   only the people on the call hold. converse.md's prep list names the
   queue. `persona/voice.md`'s stale "join no calls" becomes "join the calls
   you are invited to or asked for".
5. **Docs.** `docs/routines.md` (invites), `docs/plans/meet.md` No-Gos
   ("Joining unasked" now excepts invites).

**No-gos.** Declining, proposing times, or replying to invites in text; Zoom
or Teams links; joining an invite with no Meet link; a second call at once
on one Mac; any new check, gate or refusal.

**Stakes.** Valor RSVPs (emailing organizers) and joins live client calls
unasked; a wrong match carries one client's context into another's call.
Critique may return the plan once; review may return the work once.

**Governance.** None added. The match routes; it refuses nothing.

## Tests

`tests/test_invites.py`, gws faked as in `test_hours.py`: organizer match;
attendee match when the organizer's domain is unlisted; address beats
domain; Valor's own address ignored; no match goes to `valor` with the
no-project line; an event placed on another Mac is untouched; accepted once
(a second run patches nothing), declined and tentative left, a recurring
instance patches its series with `sendUpdates=all`; no Meet link, cancelled,
and neither attending nor organizing are ignored; the wake comes only inside 13 minutes
before start to the end, once, and again after a move; `call_chat`, then
`board`, then the first group chat. Spec: both fields load. A render of
**In a call** carries the PM bullet into `call.live_config`. Live: an event
Valor makes on its own calendar with a Meet link and no other attendee is
joined on the Cowboy before rollout (accepting is shown by the tests; no
real invite is answered until the routine is live).

**Done** when Tom invites Valor to a Meet and Valor accepts, joins on time,
prepared, without anyone asking in a chat.
