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
- Live goes deaf while its turn runs (observed in a live Gemini run today, script kept at `../exp/deaf.py`: with
  `NO_INTERRUPTION` 8 s of speech sent during Valor's reply was neither
  transcribed nor answered, with or without the cut note; with Live's default
  `START_OF_ACTIVITY_INTERRUPTS` it was interrupted in 0.2 s, heard everything
  and answered it). After the call cuts Valor off locally, Live keeps sending
  the dropped turn for up to 13 s. In the calls there was no heard row in A
  10:08:48–55, B 10:31:09–22 or B 10:35:59–36:07. The row after B 10:33:21–28
  is a sentence tail. B 10:31:22 and 10:36:09 then asked Tom to repeat himself
  (observed). In B, Tom's settling words for an item fell in that window
  (inferred).
- Settled items reopened from the worker's prep note (observed). A 10:15:42:
  the newest prep note led with an item settled at 10:10:36 and listed
  another item as "unreached". Asked for the next item at 10:16:27, Valor
  raised exactly those two, in that order, and Tom said both were covered. B 10:34:40: the
  note "read back" an item settled at 10:33:01–21 ("couldn't tell from the call");
  Valor raised it at 10:35:30 and Tom said "we just covered this".
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
2. *Live hears while it speaks.* Drop `NO_INTERRUPTION` so Live uses its default
   start-of-activity interrupt. While Live's turn is in progress (`self.reply`),
   the frame sent to Live carries only the person being answered
   (`self.answering`; everyone when it is None). Side talk can't stop him (the
   2026-11-05 reason for `NO_INTERRUPTION`). On `interrupted`, a playing reply
   is cut as a barge-in is (`_cut_off`: stop, settle with what played, one cut
   note), and a held one is dropped. The local barge stays for the tail that
   plays after Live's turn is complete. A reply is cut once, and noted once.
   The `live_config` docstring and docs/bridges/meet.md say so.
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
- test_call.py: `interrupted` while a reply plays stops the page and settles
  the say as cut with what played, with one cut note. `interrupted` before
  release plays nothing. A local barge in the tail still cuts. Barge and
  interrupt on one reply give one note. While a turn is in progress, a
  non-answered person's audio is not in Live's frame and the answered
  person's is. Outside a turn, everyone's is.
- test_live_call_voice.py (`VALOR_LIVE=1`): speech during Valor's long reply
  is heard and answered, 3 of 3. A prep note that leads with an item settled
  in the conversation, then "what's next?", gets the other item, 3 of 3. The
  existing voice tests still pass (pause waited out, 1:1 opens the next item,
  group waits).
- test_live_meet.py: a lookup answer arriving while Valor speaks and while the
  other device speaks gives one voice at a time and no spoken answer turn. A
  settled item is not reopened.
- Full suite under 5 minutes; then merge, roll out to the four Macs (never
  while `pgrep -f meet-chrome`), confirm each head, and run the live Meet test.

**Out of scope.** The raised hand. The 1:1 nudge and `UNPOSTED` turns: no
recorded overlap. The 40-line window prep reads. The Captain also plays the
call through its own speaker (volume 56): unverified as a factor, with no case.
Others' talk during Valor's turn in a group is not heard by Live, as before.

**Governance.** None added: no check, gate or step. Item 2 removes a setting
and item 1 removes code.

Stakes: Valor's voice in live client calls; every change is reversible by a
revert. Critique rounds 0, review rounds 0 (bug fixes, rule 9).
