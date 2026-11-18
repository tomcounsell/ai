# Meet: Valor's chat posts reach the call again, and a failed one is said aloud

Incident, 2026-11-18, a group call on the Captain (sessions ce0af923f58b,
a759b668adc9): every `meet.send_message` failed, 18 of 18, "the call's chat
box was not there": the join line (the recording notice), the worker's
answers, a typed line's answer, and so their thread copies. Live was still
told "posted", so it stayed quiet. A bug fix (hard rule 9): commits with
tests; this page is the stage's record. No client detail in commits or docs.

## What Meet does now (observed, the Cowboy, its own session, 2026-11-18)

Two fresh Meets, joined from copies of the call profile:
- The box is still `textarea[aria-label="Send a message"]`, opened by
  `button[aria-label="Chat with everyone"]`. But it is not in the page
  until the chat panel has been opened once; it stays (hidden) after the
  panel closes, and a post into the hidden box reached a second device.
- The payload's join-time `openChatPanel` (via `ready()`) gave up after its
  3 s ("Failed to find chat input after 30 attempts") on the first device
  in; the box never appeared, so every post failed. A device joining a call
  already going got it. So the join-time open is a race Meet now loses.

## Change

1. **The post opens the panel when it needs to.** The vendored payload's
   `sendChatMessage` (`google_meet_chromedriver_payload.js:124`, marked
   "Valor's edit" as the 2026-11-17 one is) becomes async: no box in the
   page, it clicks "Chat with everyone" once and waits for the box (up to
   10 s), then types and sends as now; the panel is left as it is. A box
   already there (hidden or shown): no click. `Page.post` already awaits
   the evaluate. `openChatPanel` at join stays.
2. **A failed post is told to Live as a turn to answer aloud.**
   `core/call.py` `_send`: when a part fails (ledger row `failed`, as now),
   Live gets a user turn with `turn_complete=True`: `[chat post failed]`,
   the text, and "the room has not seen it: say it aloud", and
   `self.typed` is cleared so that reply is spoken, not posted again. The
   join line takes the same path, so a room whose chat fails still hears it
   is being recorded.
3. **Nothing says "posted" before it is.** The worker's answer note moves
   from `work()` into `_send` (a `note` field on `Post`), sent after the
   post and only if it went (a failure is item 2's turn, carrying the
   answer). The `post` tool's result becomes "posting in the chat; you
   will be told if it fails". An `ask` repeated after the answer came gets
   "already asked; the answer: ..." (it no longer claims the post).

No check, gate, guard, validator, retry loop or new logging. No governance.

## Tests

- Real Chrome (`tests/test_meet_sign_in.py`, with `meet.script(port)` as
  its other page tests): a local page whose "Chat with everyone" button
  adds the box 1 s after a click (as Meet does): `Page.post` clicks once,
  the box gets the text and an Enter, returns True (fails before the fix:
  False). A hidden box already present: posted, no click. No chat button:
  False, at once.
- `tests/test_call.py`: a failed post (`post_ok = False`) of Live's `post`,
  of a worker answer and of the join line each gives Live one completed
  turn naming the text and saying it failed, and no "(posted in the chat)"
  note; the ledger row is `failed` and no thread copy goes (existing). A
  failed typed answer's next reply is spoken (`typed` cleared). A good
  post still notes the worker's answer as posted. Update the tests whose
  order or wording this moves (the `ask` note test, the repeated-`ask`
  result, the `post` result).
- `tests/test_live_meet.py`: the asking device also sees the join line in
  the chat (the lookup and the typed answer are already checked there).

## Rollout and proof

Full suite and ruff; merge; `core update --wait` in each kernel job's
launchctl environment, detached (the Cowboy via `ssh localhost`,
PATH=/opt/homebrew/bin), never while a call runs on a Mac; each head =
main. Then the live Meet test on the Cowboy: the second device sees the
join line, the lookup and the typed answer in the chat.

## Out of scope

Copying a failed post to the thread anyway (no copy on failure is
documented and tested); the payload's join-time panel open; Meet's other
UI changes; any other finding of task cf854dfed7ef.
