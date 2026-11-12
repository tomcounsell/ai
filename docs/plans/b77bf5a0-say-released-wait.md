# The cut-off call tests wait for Valor's reply to be playing before anyone speaks over it

**Defect.** `tests/test_call.py::test_valor_answers_who_spoke_last_when_his_reply_begins_before_their_words_are_heard[False]`
failed once under the parallel suite at main 24c1cc31f (the message was lost: the JUnit report could not be
re-read). It passes alone (6 of 6), under ten `yes` loads (8 of 8), in two full `-n auto` suites, and in six
parallel runs of the call tests.

**Cause (reproduced).** A reply's audio plays only once its `meet.say` is released through the broker
(`Call._say`, `core/call.py:1381`, sets `self.out`), a DB round trip. The test pushes the reply, then
straight away has Tom talk over it (0.6 s) and Logan talk over it (0.6 s), and `_speakers` counts a barge only
while `self.out is not None`. When the release takes longer than about a second, Logan speaks before the reply
plays, nothing is cut, the fake page's 30 s of queued audio keeps the turn open, and `settle` fails. With
`_say` delayed by 1.0 s (a scratch edit, not committed) both parameters fail exactly so: `AssertionError: the
call never settled` at line 429. Production is not affected: someone speaking before Valor's audio plays is not
speaking over him. The defect is the test's timing.

**Fix (test only).** In `tests/test_call.py`, after the `push(... done())` in this test (line 425) and in
`test_the_person_answered_speaking_over_valor_cuts_him_off_and_side_talk_does_not` (line 386, the same race
with more slack; it passed at a 1.0 s delay), add `assert await until(lambda: c.the.out is not None)`, the
file's existing helper, the pattern of b8692f9c1 and cfa17d864. `core/call.py` is unchanged.

**Out of scope.** Other fixed waits in the file: none has failed. Making the release faster or playing before
release: it changes what the broker holds, for a race only the fake has.

**Tests.** With `_say` delayed by 1.0 s and by 2.0 s (scratch, not committed), the three tests pass where two
fail today. Then `tests/test_call.py` whole, and the full suite with `-n auto`, green. No governance is added.
