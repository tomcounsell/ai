# Meet: Valor's microphone on in the call, and again when it speaks

Incident, 2026-11-18 13:38 UTC, call ce0af923f58b on the Captain (7
invited): Valor joined, heard the room and answered, but Meet had its
microphone off (Meet mutes a joiner of a large call, or a profile last left
muted), so nobody heard it. Nothing in the call code turns the microphone
on. A bug fix (hard rule 9): one commit with its tests; this page is the
stage's record, not a design. No client detail goes in the commit or docs.

The Captain's live checkout carries an uncommitted hot patch (read over
ssh, `git diff`): `Page.ready()` clicks a visible
`button[aria-label="Turn on microphone"]` through a Playwright locator.
It covers the join only, not a mute later in the call. meet.log shows
call a759b668adc9 opened Live at 13:43:23 on it; the log does not say
whether the room heard Valor (no microphone line is logged), so the patch
is unproven, not disproven.

## Change (one commit)

Reuse the vendored payload: `turnOnMic()` (google_meet_chromedriver_payload.js
:2492) clicks `button[aria-label="Turn on microphone"]` if present, and
`window.botOutputManager.ensureMicOn()` (shared payload :662) calls it.

1. `bridges/meet/__init__.py` `Page.ready()`: after
   `enableMediaSending()`, call `window.botOutputManager.ensureMicOn()` in
   the same evaluate. Docstring says why (Meet joins muted).
2. `bridges/meet/page.js` `valorStream`: when `fresh` (the first chunk of
   a turn Valor speaks, `core/call.py` `_play(fresh=True)`), call
   `out.ensureMicOn()` before queueing. Absent the button it does nothing.
   Chunks after the first do not click.

No check, gate, guard, validator or new logging. No governance. The
Captain's hot patch is replaced, not kept.

## Tests (real Chrome, as `tests/test_meet_sign_in.py`'s page-socket test)

A local page loaded with `meet.script(port)` as the init script:
- A "Turn on microphone" button present: `Page.ready()` (a `Page` whose
  `.page` is that Playwright page) clicks it once; a fresh `valorStream`
  chunk clicks it again (Meet muted Valor mid-call); a non-fresh chunk does
  not.
- No such button (a "Turn off microphone" one, Valor already live):
  `ready()` and a fresh chunk click nothing and raise nothing, and the
  chunk still queues (`valorStream` resolves to a number).

Existing tests that drive `ready()`/`valorStream` on fakes keep passing.

## Rollout and proof

- Full suite (`uv run pytest -q -n auto -p no:cacheprovider`) and ruff.
- After merge: `core update --wait` in each kernel job's launchctl
  environment, detached; the Cowboy via `ssh localhost` with
  PATH=/opt/homebrew/bin. The Captain: only once no call runs there,
  `git checkout bridges/meet/__init__.py`, then update. Each head = main.
- Live Meet test (`VALOR_LIVE=1 VALOR_MEET_LIVE=1 tests/test_live_meet.py`)
  on one Mac with no real call: Valor's page clicks "Turn off microphone"
  after joining and before `ready()`, and again before a later question;
  the asking device must still hear each answer (the test's existing
  first-sound measurement). That mute step is added to the live test.

## Out of scope

The Pirate's and the Bald's call-profile sign-ins; camera; any mute
detection or retry loop beyond the two moments above.
