# A handed-on message gets no usage-limit notice in the room

Bug fix (hard rule 9): one commit, a fix and its tests. The build removes this
file, since a bug fix keeps no plan file.

## The case (observed, 2026-11-24, on the Captain)

A chat session on another Mac handed a message on to a project's team-room
session (`valor-record act steer`, recorded there as a `message.steered` whose
payload carries `handed_on`). The receiving session's turn failed on the model
usage limit. `limit.notify` took the handed-on steer as a person's message
waiting in that room (`notices.unanswered` returns the latest
`message.steered`). It requested `slack.send_message` "I'm offline until 1am
Bangkok time tomorrow…" into the room as a reply to the hand-on's
`handon:…` id. Slack refused it (`invalid_thread_ts`). The room still should
not have been posted in: a hand-on is never posted in either room
(`core/intake.py`, the hand-on binding: "it was never posted here").

## Fix

`core/notices.py` `unanswered` gains a keyword, `people=False`. With
`people=True`, a `message.steered` carrying `handed_on` is skipped and the
walk goes on back to the latest steer without one. The keyword is passed into
its `ended`-report recursion, so an earlier session that ended holding only a
hand-on yields None. Every other caller is unchanged. `hears` and `owe`'s
ended report still carry hand-ons, and rightly so. `core/limit.py` `notify`
calls `unanswered(..., people=True)`. A person's message is found even when a
hand-on came after it, and a session holding only hand-ons sends no notice.
The limit wait and retry are untouched. The module docstring gets one clause:
a handed-on message is never a person waiting in that chat.

## Out of scope

- Sending the notice back to the session that handed the message on, across
  Macs. Answered: no notice anywhere for a hand-on.
- The email own-address rule (`intake.takes_email`). Answered: mail between
  Valor's own addresses stays unreceived. No bridge change.
- An effect outcome stepping a chat session. Named as a product note in the
  report, not fixed here.

## Tests (`tests/test_usage_limit.py`)

1. **The 24 Nov case.** A Slack chat session holds only a `message.steered`
   with `handed_on` (by tom, from another chat, `message_id` `handon:…`). A
   limit step is taken. No `limit/` effect is held. The task still waits for
   the reset (`step.failed` with `until`, no `task.ended`).
2. **A person, then a hand-on.** A person's own message, then a hand-on, then
   a limit step. Exactly one notice is held, replying to the person's
   `message_id`. This fails under a check on `unanswered`'s single result.
3. **A hand-on, then a person.** The same in the other order. Exactly one
   notice is held, replying to the person.
4. **An earlier session ended holding only a hand-on.** The next session of
   the chat (`child.reported` kind `ended`) hits the limit. No notice is held.
5. The existing suite passes unchanged, including the ended-session hand-off
   of a person's message and each chat kind's send.

## Rollout

Merge. The Captain's kernel rolls forward to its own merge (`core/rollout.py`).
Confirm the Captain's checkout and kernel lock run the merge commit
(`python -m core health`, `git log -1`). No bridge restart, since no bridge
code changes.

## Stakes

A wrong fix would either post into a room (client-visible) or silence a limit
notice someone is waiting for. The change is small, reversible, and covered by
tests.

Governance: none added. The change removes a send the system should never
have made; it adds no check, gate or review step.
