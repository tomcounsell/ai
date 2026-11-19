# Calls: one voice at a time, and what was settled stays settled

Tom's two 1:1 calls on the Captain, 2026-11-19 (call A 10:03–10:19 UTC, call B
10:30–10:37 UTC; the only calls on any Mac that day). Evidence was read from
each call's ledger rows, meet.log and its two-channel recording, and kept
outside the repo. Cases are given by time and kind only.

**What the record shows**

- No two Valor replies ever played at once (observed: every `meet.say`
  interval is disjoint in both calls). The "answer voice" Tom heard
  talking over is the spoken lookup answer (`Call._say_answer`). At A 10:07:05
  it began 0.45 s after Valor's own sentence, which had ended on a question to
  Tom. Tom started answering and cut it at 1.2 s. At A 10:08:56 it took the
  floor after Tom had cut a reply on another item. `QUIET_S` counts only
  others' voices, so Valor's own last sentence counts as a pause.
- Live goes deaf while its turn runs (observed in live Gemini runs today,
  script at `../exp/deaf.py`). With `NO_INTERRUPTION`, 8 s of speech sent
  during Valor's reply, before Live's `turn_complete`, was neither transcribed
  nor answered: 3 of 3, with or without the cut note. When the call cuts Valor
  off locally, Live keeps sending the dropped turn for up to 13 s in the calls
  and 20-30 s in the runs. In the calls no heard row exists for A
  10:08:48-55, B 10:31:09-22 or B 10:35:59-36:07, and the row after B
  10:33:21-28 is a sentence tail. B 10:31:22 and 10:36:09 then asked Tom to
  repeat himself (observed).
- Live's default `START_OF_ACTIVITY_INTERRUPTS` is no fix (observed, same
  script). It hears the barge, but a 0.6 s "Mm-hm." interrupted Valor 2 of 2,
  and Live then restarted its answer from the top. A note sent mid-reply did
  not interrupt, 2 of 2. Sending the kept speech in one burst after Live's
  `turn_complete` was transcribed and answered, 2 of 2. A third run sent it
  before `turn_complete` and it was lost, which confirms the window.
- No `interrupted` line appears in either call's log (observed), as expected
  under `NO_INTERRUPTION`.
- Settled items reopened from the worker's prep note (observed). A 10:15:42:
  the newest prep note led with a catch on an item settled at 10:10:36 and
  listed another settled item as not yet reached. Asked for the next item at 10:16:27, Valor
  raised exactly those two, in that order, and Tom said both were covered. B
  10:34:40: the note led with a read-back of an item settled at 10:33:01-21,
  saying it could not tell whether it was settled. Valor raised it at
  10:35:30 and Tom said it was already covered. In both, the settling lines were inside the 40 lines the prep
  read (`self.lines[-40:]`). For A the window began at 10:08:47 and for B it
  covered the whole call, and both settling rows are heard rows (observed). So
  the 40-line window is not the cause; the prompt is.
- Not causes (observed): no session reopened or resumed in either call. Tom's
  channel is about 0.05–0.16 RMS while he talks and 0 otherwise, against
  `SPEAKING` 0.01, so "heard only while speaking" dropped nothing.

**Build** (one commit per fix, each with its tests)

1. *The spoken lookup answer comes out* (Tom, 2026-11-19 07:55). It can't wait
   cleanly: a pause after Valor's own question is the other person's turn, and
   a reply Live is about to start can't be seen. Remove `_say_answer`,
   `Call.answers` and `Post.say`, and turn `SAY_ANSWER` into a kept note (no
   reply asked). The note says the answer is in and posted in the chat, and to
   give it in one short sentence at the start of the next reply. It stays
   posted in the chat as now. A failed post is still said as `UNPOSTED`.
2. *What is said while Live's turn runs reaches it.* Keep `NO_INTERRUPTION`.
   When the call cuts Valor off (`_cut_off`) while Live's turn is still open
   (`self.reply` set), keep the frames sent to Live from that moment, plus the
   `BARGE_S` that cut him. Keep each frame sent until Live's `turn_complete`,
   then send the kept speech before the next frame. A cut after
   `turn_complete`, while the tail plays, keeps nothing, since Live is already
   listening. A lost session drops what was kept. A sound shorter than
   `BARGE_S` cuts nothing and keeps nothing, so a backchannel neither stops
   Valor nor is replayed. Side talk during an uncut reply stays as before. The
   `live_config` docstring and docs/bridges/meet.md say this, and why the
   default interrupt was refused.
3. *Prep never relists the settled.* `PREPARE`: leave out what the call has
   settled or said is covered, even where it adds work; no read-backs of it.
   `PREPARED` and the instruction line on prep notes: an item the call has
   settled stays settled; never raise it again.

**Tests**

- test_call.py (fake Live): a lookup answer to a question asked aloud arrives
  while Valor's reply plays, while the other person speaks, and at a quiet
  pause. In each case no turn is asked, one note carries the answer, and it is
  kept for a fresh session. A failed post is still said once as unposted.
  Update the tests built on the spoken answer (lines ~953, ~998).
- test_call.py: the answered person cuts Valor while Live's turn is open.
  Nothing is sent early, and at `turn_complete` their kept speech is sent
  before the next frame. Cut after `turn_complete`: nothing kept. A sound
  under `BARGE_S`: no cut, nothing kept. Session lost mid-keep: nothing sent
  to the next session.
- test_live_call_voice.py (`VALOR_LIVE=1`): speech during Valor's long reply,
  sent again after `turn_complete` as the call does, is heard and answered,
  3 of 3. A note sent mid-reply leaves it whole, 3 of 3. A prep note that
  leads with an item settled in the conversation, then "what's next?", gets
  the other item, 3 of 3. The existing voice tests still pass.
- test_live_meet.py: a lookup answer arriving while Valor speaks and while the
  other device speaks gives one voice at a time and no spoken answer turn.
  The settled-item case is tested against Live directly
  (test_live_call_voice.py), not in Meet: it needs a prep stub that relists
  an item, which the Meet test's stub worker does not model. A noted answer
  is given in Valor's next reply: a live voice test, 3 of 3.
- Full suite under 5 minutes; then merge, roll out to the four Macs (never
  while `pgrep -f meet-chrome`), confirm each head, and run the live Meet test.

**Not built, from the critique.** Wiring Live's `interrupted` to a cut, and
clearing `self.reply` on it: with `NO_INTERRUPTION` kept, Live sends none
(observed). The reopened items in both calls are item 3's. Item 2 is for the
words lost while Valor was cut off, and the two "say that again" turns.

**Out of scope.** The raised hand. The 1:1 nudge and `UNPOSTED` turns: no
recorded overlap. The 40-line window prep reads (it held both settlings). The Captain also plays the
call through its own speaker (volume 56): unverified as a factor, with no case.
Others' talk during Valor's turn in a group is not heard by Live, as before.

**Governance.** None added: no check, gate or step. Item 1 removes code;
item 2 sends speech that was being lost.

Stakes: Valor's voice in live client calls; every change is reversible by a
revert. Critique rounds 0, review rounds 0 (bug fixes, rule 9).
