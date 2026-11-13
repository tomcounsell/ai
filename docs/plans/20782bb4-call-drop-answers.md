# A call survives its database restarting; spoken answers answer what was asked

Two defects from the 2026-11-13 call (eed-fiog-nve). Bug fixes: commits with
tests, no new check, gate or guard.

## 1. The drop at 10:40

**Cause, from the logs.** At 10:38:14 UTC the Cowboy's Postgres took a fast
shutdown (the controlled restart onto 18.7 for the plpgsql load failure) and
was back at 10:39:15. `~/Library/Logs/Valor/meet.log`: the call's `orders`
lane raised `psycopg.OperationalError: server closed the connection
unexpectedly` from `listener.notifies()` (`core/call.py` `orders`), `_lane`
ended the call, the summary's and the end's `db.connect` failed with "No such
file or directory" (server still down), so the process exited without writing
its end. The kernel's `sweep` found `meet:call:<session>` free (the holder
connection was gone too) and wrote "Valor's call process stopped" at 10:40.
Commit 52ac19204 fixed the same restart for a running turn (`runs._heard`);
the call process was missed.

**Fix, in `core/call.py`, reusing 52ac19204's pattern.**
- `orders`: a listening session the database drops is listened again on a new
  one once the database answers (`runs._relisten`, moved to `core/db.py` as
  `db.listen(dsn, channels)` so both use one helper). After relistening it
  takes `meet:call:<session>` and `run:<session>` again on that connection and
  catches up a stop (`tasks.is_stopped`) and any `message.steered` row of the
  call newer than the last it posted. If the locks are taken or `call.ended`
  is already written (the sweep won the race), the call leaves with that as
  its reason, and writes no second end.
- The call's short-lived connections (`_heard`, `watch`, `_open`/`_charge`,
  `open_decisions`, `_effect`, `summarise`, the end in `run`) go through one
  `Call._connect()` that waits for the database while it refuses connections
  (sleep `db.RELISTEN_S`, the constant moved with the helper). So no lane dies
  on a restart and the end is always written with its real reason.
- Out: retrying a statement whose connection drops mid-flight (only
  `_effect` already does, by request id); the Gemini Live session (not on the
  database); the worker's turn (already covered by 52ac19204).

## 2. Vague spoken answers

**Cause.** The Live instruction (`core/call.py` `INSTRUCTIONS`) carries the
persona's "In a call" section and the `prep` the asking chat session wrote.
Neither tells the voice to answer the question asked with the specific, and
the prep guidance (`skills/sdlc/converse.md` **Calls**, `tools/meet.py`
`MeetJoin.usage`: "what you would recommend, kept short") asks for
recommendations without the evidence or the ranking rule. So asked for an
example or the criteria, the voice had only the recommendation and repeated
it, and repeated the prep's unevidenced "breaks real work".

**Fix, wording only.**
- `persona/conduct.md` "In a call": a bullet **Answer what was asked**: give
  the specific it asks for (the example, the number, the rule) from your notes
  or the call; a claim names its evidence (the case and its date); when your
  notes don't hold it, say plainly you don't know and `ask` your worker; never
  answer by restating the goal or a recommendation.
- `skills/sdlc/converse.md` **Calls** (the one place for prep): `prep` carries,
  for each recommendation, the concrete case behind it (what happened, when,
  where it is recorded) or "no case yet"; and the ranking rule that orders the
  queue (mandatory work first: a bug that breaks a job, security, a dated
  commitment; then value high/medium/low; then the smaller appetite).
  `MeetJoin.usage` points to it instead of describing prep itself.

## Tests

- `tests/test_call.py`: a call under way when every database session is
  terminated (`terminate_all`, as in 52ac19204's test) and connections are
  refused for a few seconds keeps running; `meet:call:<session>` is held
  again afterwards, so `call.sweep` ends nothing; a stop written after the
  restart still ends it with "the call was stopped"; a steer written after is
  posted; the end is written with the call's own reason.
- Same, the sweep wins first: the call leaves and the session has one
  `call.ended`.
- `tests/test_health.py`'s turn test keeps passing on the moved helper.
- `tests/test_call.py`, documented behaviour: `live_config(...)`'s instruction
  tells the voice to answer what was asked with the specific and to say when
  it doesn't know; converse.md's Calls asks prep for a case per
  recommendation and states the ranking rule.
- Full suite under 5 minutes.

Stakes: a live call's process and its database connections; reversible.
