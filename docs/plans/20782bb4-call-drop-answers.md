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
file or directory" (server still down), and the process exited without writing
its end. The locks were on `holder` in `core/__main__.py` `_call`, whose
session the restart also dropped, so the kernel's `sweep` found
`meet:call:<session>` free and wrote "Valor's call process stopped" at 10:40.
Commit 52ac19204 fixed the same restart for a running turn (`runs._heard`);
the call process was missed.

**Fix.**
- **The locks.** `router.alive()`'s pattern (`core/router.py:139`: probe the
  holder; dropped, take the lock again on a fresh session; refused, another
  holder has it) moves to `core/db.py` as `db.Held(dsn, keys)`, with `take()`
  and `alive()`. `router.run` uses it for `run:<task>` (behaviour unchanged);
  `_call` uses it for `meet:call:<session>` and `run:<session>` in place of
  its bare `holder`, and passes it to `Call` as `held` (default `None`, so
  tests that build `Call` without locks still can; the new tests pass one).
  The holder lives in `Call.held`; after a restart `alive()` replaces its
  connection.
- **The listener.** `orders` relistens on a dropped session (`runs._relisten`,
  moved to `db.listen(dsn, channels)`, one helper for both). After
  relistening: `held.alive()`; refused (the sweep won, or another process),
  the call leaves with "another process took over the call" and its end is
  not written twice (`end` already returns False once `call.ended` exists).
  Then it catches up a stop (`tasks.is_stopped`) and each `message.steered`
  row of the call after the last one it posted.
- **The short connections.** `_heard`, `watch`'s participant row,
  `_open`/`_charge`, `open_decisions`, `_effect`, `summarise` and the end in
  `run` connect through `Call._connect()`, which retries a refused connection
  every `db.RELISTEN_S` for at most `DB_WAIT_S = 180` (the restart took 61 s;
  `health.py` restarts at most every ten minutes), then raises as now. This
  stalls the awaiting lane for the outage, by intent: `_heard` sits in the
  Live receive loop, so Valor neither hears new lines into the record nor
  speaks for up to that minute, and `watch` stops checking who left. Bounded,
  a database that stays down still ends the call (and the sweep writes it),
  as today; unbounded would leave a call no one can see. Live keeps its own
  socket, so the session resumes where it was.
- Out: retrying a statement whose connection drops mid-flight (only
  `_effect` already does, by request id); the worker's turn (52ac19204).

## 2. Vague spoken answers

**Cause.** The Live instruction (`core/call.py` `INSTRUCTIONS`) carries the
persona's "In a call" section and the asking session's `prep`; the worker's
prompt (`core/__main__.py` `_call` `worker`) is "Asked in the call: …", the
last 40 lines and the prep. None tells the voice or the worker to answer the
question asked with the specific. The prep guidance in
`skills/sdlc/converse.md` **Calls** asks for "what you would recommend, kept
short", with no case behind it and no ranking rule (`tools/meet.py`
`MeetJoin.usage` says "your notes on the topic or project, … kept short").
So, asked for an example or the criteria, the voice had only the
recommendation and repeated it, with the prep's unevidenced "breaks real work".

**Fix, wording only.**
- `persona/conduct.md` "In a call": a bullet **Answer what was asked**: give
  the specific asked for (the example, the number, the rule) from your notes
  or the call; a claim names its evidence (the case and its date); when your
  notes don't hold it, say plainly you don't know and `ask` your worker; never
  answer by restating the goal or a recommendation.
- The worker's prompt in `_call`: one sentence: answer exactly what was asked,
  with the specific and where it is recorded, or say it isn't known.
- `skills/sdlc/converse.md` **Calls**, the one place for prep: for each
  recommendation, the concrete case behind it (what happened, when, where
  recorded) or "no case yet"; and the order of the queue with the ranking
  rule it follows, cited from the persona's **One ordered queue** and **Value
  for effort**, not restated. `MeetJoin.usage` points to converse's Calls.

## Tests

- `tests/test_call.py`, a call with a `db.Held`, under way: `terminate_all`
  (from `tests/test_health.py`) drops every session, and `core.call.db.connect`
  is stubbed to raise `OperationalError` for its next N calls (the server
  down). The call keeps running; afterwards `meet:call:<session>` is held again
  (a fresh `pg_try_advisory_lock` on it fails) and `call.sweep` ends nothing;
  a stop written after the restart ends it with "the call was stopped"; a
  steer written during the outage is posted; one `call.ended`, with the call's
  own reason.
- Same, another session takes `meet:call:<session>` during the outage: the
  call leaves and the session has one `call.ended`.
- `tests/test_health.py`'s turn test and router tests pass on the moved helpers.
- Documented behaviour: `live_config(...)`'s instruction carries "Answer what
  was asked"; converse.md's Calls asks prep for a case per recommendation and
  the ranking rule.
- Full suite under 5 minutes.

Stakes: a live call's process and its database connections; reversible.
