---
tracking: none
slug: m2-2f-flood-wait-flake
type: bug
status: built
---

# The flood wait test under load

## What the test measures

`test_a_flood_wait_fails_the_send_and_the_next_waits_it_out` starts its clock
before the failing send. The bridge holds a flood wait until the moment
Telegram gave it plus Telegram's seconds, on the monotonic clock, so the wait
starts inside the failing send. Recording the outcome and releasing the next
send use up part of the wait before the next send begins. On an idle machine
that is a few milliseconds; under load it can reach 0.1 s. A clock started
after the failing send would read less than the full second under load and
fail a correct bridge.

## The assertion

The wait cannot end before the flood moment plus one second, and the flood
moment is after the test's start, so the elapsed time is at least 1.0 s at any
load. The assertion fails if the bridge waits less than Telegram asked. The
test for a flood wait after the first part of a split send asserts the same
1.0 s.

## Evidence

With five copies of the test running at once beside twelve CPU hogs, a clock
started after the failing send failed once in 15 runs (measured 0.78 to 0.88 s
against a 0.9 s floor); the clock started before it passed 25 of 25.
