# A call's front model keeps its token while the worker's turns end

Bug fix (CLAUDE.md rule 9). This file is removed in the fix commit, so
`main` gets no plan file, as with `meet-front-home.md`.

## What was wrong (checked on the Captain, call 2ab504d318ae)

`_call` (`core/__main__.py`) uses one `Gateway` for both the front model,
`gateway.issue(session_id, "front")`, and the worker, `runs.run_turn(gateway,
session_id, ...)`. At the end of any turn, `runs` calls `gateway.retire(task_id)`
and `gateway.cut(task_id)`. Those drop **every** token of the task and cut
**every** in-flight call of the task, including the front's. In the ledger the
worker's one turn ended at 06:03:28 UTC (`turn.ended`). Every front draft after
that got `403 turn token revoked or unknown`, starting with the next one at
06:04:05. The call started at 06:00:30 and ended at 06:07:43, after Tom left.
`gateway.drain(task_id)` has the same task-wide reach: a worker turn's end
waits on the front's calls.

## The fix

The end of a turn retires, cuts and drains **its own** grant, not the
whole task's. A stop (`revoke`) still ends everything the task has.

- `core/gateway.py`: `Grant` becomes frozen (hashable). `calls` and
  `upstream_calls` are keyed by `Grant` instead of `task_id`.
  `retire`, `cut` and `drain` take `(task_id, turn_id=None)`: given a turn,
  they act only on that turn's grants. With no turn they act on the whole
  task, so `revoke`, the hours runner and `test_record` behave as they do
  now. `revoked` stays per task. Update the docstrings.
- `core/runs.py`: the three `retire`/`cut` sites and the two `drain` sites
  pass `turn_id`.
- No change to `core/call.py`'s front-failure path. Its documented job is
  "a draft that fails is silence, and the call goes on". Making it speak or
  post would be a new behaviour, not the existing path's job, so it is out
  of scope per the request. The delivery names it for Tom.

## Tests (`tests/test_gateway_meter.py` or `tests/test_kernel.py`, with the fake upstream)

1. **The incident.** One gateway, one task. Issue a `front` grant, run a worker
   turn through `runs.run_turn` to its end, then call `/v1/messages` on the
   front's token. It answers 200 and is charged to the task. This fails on
   the current code.
2. **In flight.** While a front stream is open, a worker turn of the same task
   ends. The stream is not cut and runs to its end. The worker's `drain` does
   not wait for it.
3. **A stop still ends everything.** `revoke(task)` (a stop) refuses the front's
   token and cuts its in-flight stream.
4. **The turn's own token** is still refused after its turn ends. This covers
   the existing behaviour and is kept.
5. **Whole-task retire** (no turn) still drops every grant: the existing
   `test_record` and hours paths.

Full suite under 5 min; ruff clean.

**Live (`tests/test_live_meet.py`, the `_call` test).** Extend it so the
call outlives the incident:
- The session's workspace is provisioned, so the worker really runs a turn
  through `runs.run_turn`. Provision it the way the kernel does, or by a
  `workspace.provisioned` row, whichever the test database allows.
- The second device asks a question the front sends to the worker, then
  waits for the worker's `turn.ended`.
- It then asks aloud again and must hear Valor answer.
- The call runs at least 4 min, past the 3 min 35 s at which this call died.

## Rollout and confirmation

- Merge, then update all four Macs over ssh with each `com.valor.kernel`
  job's launchctl environment, as earlier rollouts did.
- On the Captain, run the live `_call` test above in its login session, with
  Valor's second device and no Tom.
- Evidence: a `turn.ended` for the worker, then a later `meet.say` done, and
  no 403 in meet.log.

## Delay measurement (report only, no code)

For each exchange in 2ab504d318ae, from the ledger and meet.log/voice.log:
- `call.heard` (after `PAUSE_S`/`GIVE_UP_S` endpointing and Parakeet)
- then `effect.held` (front's first sentence plus `meet.speak`)
- then `effect.intent`
- then playback start (`effect.outcome` time minus `played_s`).

The time between end of speech and `call.heard` comes from the endpointing
constants and voice.log's transcription times, where they are logged. Where
a stage isn't logged, say so rather than add logging.

First read: heard to sound took about 3.3 s, 5.8 s and 6.7 s, plus up to 1.6 s
of endpointing. Then compare a realtime speech-to-speech model with
tightening the current pipeline (stream TTS per clause, a shorter endpoint,
skip the judge with one other person, a faster front seat), with the cost to
build each and a recommendation. The duplex change is not built.

## Out of scope

- The duplex voice change.
- Changing the front-failure path to speak or post.
- Any new check or guard.

## Rounds

Critique 0, review 0, by rule 9 and Tom's "a bug fix: a commit with a test".
The gateway change is kernel code. It is undone with one revert, and tests
1–5 pin both directions: what a turn's end leaves alone, and what a stop
still ends.
