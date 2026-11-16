# Acknowledge a call request at once

Tom, priorities call, 2026-11-16: after someone asks for a call, the thread
shows nothing until Valor is in, which can take a minute or more. Approved
for build from #valor-product the same day. Appetite small. Wording plus a
test of documented behaviour; no new check, gate, state or code path. No
governance.

## Change (one commit)

1. `skills/sdlc/converse.md`, **Calls.** (amended after critique: a chat
   turn's sends leave only when it ends, so the line gets its own turn):
   before any reading, post one line in the asking thread, "Starting a call,
   link in a moment", with no link, write `.valor/wake.md` at `30s` on that
   send, and end the turn. The turn that finds the line sent and no call yet
   (the wake, or a thread message that dropped it) posts no second line,
   reads, and requests `meet.join` as before. "Say nothing yet" becomes "Say
   nothing more yet". The link still comes only from the `link` report.
2. `core/call.py:248`, the `joining` report: "Post nothing yet." becomes
   "Post nothing more yet: the thread already has your line that the call is
   starting." so a turn waking on that report does not post a second line.
3. `tools/meet.py` `MeetJoin.usage`: says the request comes a turn after the
   thread's "Starting a call" line. `docs/bridges/meet.md` "The pieces"
   gets the same one clause.

The invited call is unchanged: its top-level "Joining <title> at <time>"
post already acknowledges, and the `link` report needs no second post.

## Tests

- `tests/test_call.py`, beside the existing Calls-text test: the **Calls.**
  section holds the line with no link, before the reading and the
  `meet.join` request; the 30s wake and the turn's end; no second line;
  the link still only from the `link` report.
- `tests/test_meet.py:188`: the `joining` report says to post nothing more,
  and still carries neither the link nor the Meet code.
- Beyond the obvious: the ack line itself names no link (assert the Calls
  text says "no link" for it), and the invited-call section is untouched
  (it still says the `link` report needs no second post).
- Full suite and ruff.

## Out of scope

Posting the link early; a kernel-side automatic post (that would be a second
mechanism for the chat's send); Telegram-specific wording (the rule covers
any group chat's thread).

## Acceptance

After rollout, Tom asks for a call in a Slack thread and sees the line within
seconds, then the link once Valor is in. Checked on the first real call
request after rollout, from the thread and `valor-record call`.

**Stakes.** Prompt wording only: worst case a missing or doubled line in a
thread, reversible in one commit. Critique 0, review 0.
