# Meet: Valor's chat posts reach the call again, and a failed one is said aloud

Incident, 2026-11-18, a group call on the Captain (sessions ce0af923f58b,
a759b668adc9): every `meet.send_message` failed, 18 of 18, "the call's chat
box was not there": the join line (the recording notice), the worker's
answers, a typed line's answer, and so their thread copies. Live was still
told "posted", so it stayed quiet. A bug fix (hard rule 9): commits with
tests; this page is the stage's record. No client detail in commits or docs.

## What Meet does now (observed, the Cowboy, its own session, 2026-11-18)

Three fresh Meets, joined from copies of the call profile:
- The box is still `textarea[aria-label="Send a message"]`, opened by
  `button[aria-label="Chat with everyone"]`, whose `aria-expanded` is
  "true" while the panel is open and "false" while shut. The box is not in
  the page until the panel has opened once; it stays (hidden) after it
  shuts, and a post into the hidden box reached a second device.
- The payload's join-time `openChatPanel` (awaited in `ready()`) raced:
  twice the box never came ("Failed to find chat input after 30 attempts",
  and it returns without its closing click), once it did. A click can also
  be eaten: with a "Gemini is available" pop-up showing, a click left
  `aria-expanded` "false" and the next one opened the panel.

## Change

1. **The post opens the panel when it is shut.** The vendored payload's
   `sendChatMessage` (`google_meet_chromedriver_payload.js:124`, marked
   "Valor's edit" as the 2026-11-17 one is) becomes async. Box in the page
   (shown or hidden): type and send as now, no click. No box: for up to
   10 s it waits for the box, clicking "Chat with everyone" only while its
   `aria-expanded` is "false", at most once a second (a click eaten by a
   pop-up is clicked again); never while it is "true" (the panel open or
   opening, as the failed join-time open may leave it). No button: false at
   once. The panel is left as it is. `Page.post` already awaits it.
2. **A failed post is said aloud.** `core/call.py` `_send`: the parts of a
   post that failed (ledger rows `failed`, as now; no copy if none went)
   are added, joined as one text, to `self.unposted`. The mix lane's tick
   (where `_answer_alone` runs) sends them to Live as one user turn,
   `turn_complete=True`, "[not posted] the call's chat failed, so the room
   has not seen this: say it aloud: ...", once a session is open and Valor
   is not mid-reply (`self.reply` and `self.out` both None), and clears
   `self.typed` so the reply is spoken. So a failure before Live opens
   (the join line, queued before the `live()` lane starts) waits for the
   session, as `read_note` does, and a failure while Valor speaks waits
   for that answer to end instead of cutting it. The join line takes this
   path: a room whose chat fails still hears it is being recorded.
3. **Nothing says "posted" before it is.** The worker's answer note moves
   from `work()` into `_send` (a `note` field on `Post`), sent only when
   every part posted; otherwise item 2's turn carries the answer. The
   `post` tool's result becomes "posting in the chat; you will be told if
   it fails". An `ask` repeated after its answer gets "already asked; the
   answer: ..." (no claim about the post).

No check, gate, guard, validator or new logging. No governance.

## Tests

- Real Chrome (`tests/test_meet_sign_in.py`, `meet.script(port)` as its
  other page tests), a local page whose button toggles `aria-expanded` and
  adds the box 1 s after it opens: shut, `Page.post` clicks once and the
  box gets the text and an Enter, True (fails before the fix: False);
  already clicked once (`aria-expanded` "true", box still coming), no
  click and True; a first click eaten (the page ignores it), clicked again
  and True; a hidden box present, no click and True; no button, False.
- `tests/test_call.py`: a failed post of Live's `post`, of a worker answer
  and of the join line each gives Live one completed `[not posted]` turn
  with its text and no "(posted in the chat)" note; ledger `failed`, no
  thread copy (existing). The join line failing before the session opens
  reaches the session once it opens. A failure while a reply plays is sent
  after it finishes, not during. A post split into two parts, both failed,
  gives one turn with both. A failed typed answer's next reply is spoken.
  A good post still notes the worker's answer. Tests whose wording this
  moves are updated (the `ask` note, the repeated `ask`, the `post` result).
- `tests/test_live_meet.py`: the asking device also sees the join line.

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
