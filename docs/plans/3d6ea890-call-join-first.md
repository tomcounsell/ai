# Call link sooner: join first, read up after (P-033)

Tom, #valor-product, 2026-11-16 13:00-13:10 UTC: the Meet link takes 2-3
minutes after a call is asked for; join first, and be nearly ready.
Mandatory (Tom's ask), value medium, appetite small. No governance: no
check, gate or guard is added; the link still goes out only once Valor is in.

## Where the time goes (observed, ledger of the asking chat session)

| step | 05:28 call (before the ack line) | 08:09 call |
|---|---|---|
| ask to turn start | 23s (reaction 19s) | 11s |
| ack turn, line sent | - | 15s turn, line at +29s |
| 30s wake to reading turn start | - | **85s** (fired 55s late) |
| reading turn | **77s** | 31s |
| `meet.join` to call session | 7s | 7s |
| browser join (`call.started` to `call.joined`) | 6s | 6s |
| link report to link posted (a chat turn) | 18s | 24s |
| ask to link | 132s | 185s |

Joining is the smallest share. The time is the wake, the reading turn and
the turn that posts the link: three chat turns where one is needed.

## Change

1. **One turn joins.** `skills/sdlc/converse.md` **Calls.**: the turn that
   sees the ask sends "Starting a call, link in a moment" *and* requests
   `meet.join` in the same turn, no reading first and no wake. Its `prep` is
   written from what it already holds: the project's summary page in its
   Brief (P-024), the open decisions, the asking thread and this chat, kept
   short. The wake and the "finds that line sent and no call yet" paragraph
   go. Expected: line and join leave together about 25-35s after the ask.
2. **The call reads up, after Valor is in.** `core/call.py` `Call`: for a
   call asked for in a chat (no `starts`; invited calls unchanged), the first
   item on the worker lane (`work`, the one worker, so its turns stay serial)
   is a read-up: the thread, the project's record and memory on the topic,
   its open decisions and queue, returning short notes with cases. The answer
   is not posted in the call's chat. It is appended to `info["prep"]` (so a
   fresh Live session's `live_config` carries it, and the worker's later turns
   resume a session that read it) and given to the open Live session once, as
   a `note` (`turn_complete=False`: no reply asked, no restart). Notes in
   before the session opens are in its config and not noted again.
3. **Live knows it is still reading.** While the read-up is pending the
   instruction carries one line: asked what the notes would hold, say you are
   still reading up, and answer from `backlog`, `person` and
   `open_decisions` meanwhile; a note opening "Your reading for this call is
   in" brings them; do not speak about the note itself.
4. **The call posts its own link.** At `entered`, for a call asked for in a
   chat, the call copies "I'm in: <link>" to the asking thread through its
   existing thread copy (`_copy`, `_effect`), which already sends every line
   Valor writes in the call. This removes the chat turn that posted it (one
   mechanism, moved, not added). The `link` report then says the link is
   posted and to post nothing. A failed join posts nothing, as today.
5. `tools/meet.py` `MeetJoin.usage`, the module docstrings of `core/call.py`
   and `tools/meet.py`, and `docs/bridges/meet.md` "The pieces" say the same.

## Out of scope

Invited calls (they prep ahead, post "Joining <title>", and get no extra
link line). Intake and reaction latency (11-23s). The late wake (a `30s`
wake started 85s later, 2026-11-16 08:10:20 to 08:11:45): a defect for its
own fix, named in the delivery. No cache of prep.

## Tests

- Calls text (`tests/test_call.py`, the existing Calls test, rewritten): one
  turn sends the line and requests `meet.join`; no wake, no reading before;
  prep from the Brief's summary, decisions and thread; the link not posted by
  the chat session. The invited-call section unchanged.
- `Call` with the test fakes: an asked-for call copies the link to the thread
  with its `reply_to` only after `call.joined`; a failed join copies nothing;
  an invited call (`starts`) copies nothing and runs no read-up.
- Read-up: the worker's first turn is the read-up; its answer is not posted
  in the call's chat; it is noted once with `turn_complete=False`; a session
  opened after it gets it in `live_config` and no note; a question asked
  meanwhile is answered after it; the pending line is in the instruction only
  while pending.
- `tests/test_meet.py`: the `link` report says the link is posted and to post
  nothing; the `joining` report still carries no link.
- Full suite and ruff.

## Acceptance

After rollout, a real call asked for in a group chat: the link in the thread
within 60s of the ask (ask and link times from the ledger), and the call
answering a question only the read-up notes hold. Recorded with the date.

**Stakes.** The live call path: a wrong build doubles a line, speaks twice,
or posts a link for a call Valor is not in; reversible in one commit.
Critique 1, review 0.
