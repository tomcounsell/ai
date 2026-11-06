# Plan: the Meet call speech tests wait for the reply they check

## The defect

`tests/test_call.py::test_a_reply_is_one_say_its_audio_held_until_released_and_settled_with_what_was_said`
went red at 52a101ca2 under `-n auto`: no `meet.say` was held.

Cause (reproduced): the test waits with
`until(lambda: c.the.out is None and not c.the.replies)`. That holds before
the reply exists too. The reply is registered in `Call._begin`
(`core/call.py`) only after `_close_stretch` writes `call.heard` to Postgres.
Under load, that write outlasts `push`'s 0.1 s sleep, so the wait passes at
once, `settle` finds nothing busy, and `finish` cancels the Live lane before
the turn's first audio requests its say. Wrapping `_close_stretch` in a 0.3 s
sleep fails the test every time, with the same `ValueError`. `until` returns
False on a timeout, so a wait that never held would pass silently too.

`test_lives_no_speech_turn_is_silence_and_its_markup_is_never_in_what_was_said`
has the same vacuous wait (`until(lambda: not c.the.replies)` before
"Sure, Monday.") and can fail the same way.

## The fix (test-only; `core/call.py` is right)

- In the first test, wait for the reply to have played and be over:
  `assert await until(lambda: c.page.streams and c.the.out is None and not c.the.replies)`.
- In the no-speech test, wait for the spoken turn's audio as well:
  `assert await until(lambda: len(c.page.streams) > len(quiet) and not c.the.replies)`.
- Both waits are asserted, so a timeout fails at the wait, not at a later unpack.

Out of scope: any change to `core/call.py`, `settle()` or the other tests'
waits, all of which wait on something that only becomes true after the work.

## Tests

- Both tests pass alone and in the full suite (`uv run pytest -q -n auto -p no:cacheprovider`).
- Both still pass with `_close_stretch` delayed 0.3 s (done by hand, not
  committed). Before the fix, the first test fails every time under that delay.
- The full suite stays under 5 minutes.
