---
tracking: none
slug: m2-2f-flood-wait-flake
type: bug
status: built
---

# The flood wait test that failed under load

## Cause

`test_a_flood_wait_fails_the_send_and_the_next_waits_it_out` took its start
time after the first send had already failed. The bridge holds a flood wait
until the moment Telegram gave it plus Telegram's seconds, on the monotonic
clock, so the wait starts inside the failing send. Everything between that
moment and the test's start (recording the outcome, releasing the next send)
had already used up part of the wait. On an idle machine that gap is a few
milliseconds; under load it reached 0.1 s, so the measured time came in at
0.78 to 0.88 s against a 0.9 s floor. The code was right; the test measured
from the wrong moment.

## Fix

The test starts its clock before the failing send and asserts the full
second. The wait cannot end before the flood moment plus one second, and the
flood moment is after the test's start, so the assertion holds at any load
and still fails if the bridge waits less than Telegram asked.

## Shown

With five copies of the test running at once beside twelve CPU hogs, the old
test failed once in 15 runs (`assert ... >= 0.9`); the fixed test passed 25 of
25.
