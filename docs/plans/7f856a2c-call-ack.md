# Acknowledge a call request at once

Tom, priorities call, 2026-11-16: after someone asks for a call, the thread
shows nothing until Valor is in, which can take a minute or more. Approved
for build from #valor-product the same day. Appetite small. Wording plus a
test of documented behaviour; no new check, gate, state or code path. No
governance.

## Change (one commit)

1. `skills/sdlc/converse.md`, **Calls.**: "Say nothing yet" becomes: in the
   same turn as the `meet.join` request, post one line in that thread,
   "Starting a call, link in a moment" (the chat's own send, as the reply
   section says, with the same `reply_to`/`topic_id` as the join), with no
   link and no Meet code; then say nothing more until the `link` report. The
   rest stands: the link only from the `link` report, one line; a
   could-not-join report gets one line why, with no link.
2. `core/call.py:248`, the `joining` report: "Post nothing yet." becomes
   "Post nothing more yet: the thread already has your line that the call is
   starting." so a turn waking on that report does not post a second line.
3. `tools/meet.py` `MeetJoin.usage`: add "With this request, post one line
   in the thread that the call is starting, with no link." Module docstring
   and `docs/bridges/meet.md` "The pieces" get the same one clause.

The invited call is unchanged: its top-level "Joining <title> at <time>"
post already acknowledges, and the `link` report needs no second post.

## Tests

- `tests/test_call.py`, beside the existing Calls-text test: the **Calls.**
  section (sliced to **An invited call.**) holds "Starting a call, link in a
  moment", says to post it in the same turn as the `meet.join` request, and
  still says the link is posted only from the `link` report.
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
