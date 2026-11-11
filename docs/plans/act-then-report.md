---
status: Planning
type: bug
appetite: Small
tracking: https://github.com/tomcounsell/ai/issues/3686
revision_applied: true
revision_applied_at: 2026-11-11T12:00:00Z
---

# A chat session sees what its action did before it writes the reply

Request: Tom, 2026-11-11, after three yudame/#valor-dev threads went
unanswered or wrong: "the session has to see the outcome before it writes
the reply." The rule is already in the persona (`persona/voice.md:44`, no
promises: "true only when the change is already made in this turn"); a
chat session cannot keep it for work it hands to a task.

## What is wrong (checked 2026-11-11, main at 2db190e23)

- A chat turn's `task.start`, `task.answer`, `task.steer` and `task.stop`
  are files in `.valor/effects/`. The kernel performs them after the turn
  ends (`core/session.py` collect, ~1043), in file-name order
  (`core/signals.py:213`), mixed with the turn's sends. The reply is
  always written before the action exists; whether it leaves first is the
  alphabet's call.
- Incident, Cowboy, session `bcfdda8e1251`, 16:04 +07: `op-status.json`
  (Slack: "I've restarted the work from #mastery-dev") released, then
  `prs-task-captain.json` (`task.start`, `in` a chat of another project)
  failed: `ValueError("slack:T0B6E8JPE3C/C0CBHMR5FHV is not one of this
  project's chats")`. Tom waited an hour on a task that never existed.
  Same order, luckily harmless, at 13:41 (`invite-ack` before
  `invite-task`) and 14:44 (`ron-ack` before `ron-task`).

## What will be built

1. **`POST /t/<token>/record/act`, answered in the gateway.** `record.answer`
   gives a WRITES handler no turn id and no performers (`core/record.py:542`),
   and core/ may not import tools/ (`core/__main__.py:165`), so the route
   lives in `Gateway.record` (`core/gateway.py:471`): `Gateway` takes
   `performers: router.PerformersFactory`, passed at `core/serve.py:1321`
   and `core/__main__.py:497`. For `act` it opens its own connection in a
   thread (as `record.answer` does), builds
   `performers(await tasks.brief(conn, grant.task_id))`, and calls
   `broker.request(conn, built, grant.task_id, Action(action_type, target,
   payload), request_id=f"{grant.turn_id}/{key}")`. It answers `kind`,
   `error`, `result`, `effect_id`. Same intent and outcome rows, performers
   and `lookup` reconcile; only the moment changes.
2. **`valor-record act start|answer|steer|stop`** (`tools/valor_record.py`),
   its own subcommand and its own write branch (not `fact_write`, which
   sends every write to `/facts`, `:242`). `act`, not `task`: `valor-record
   task ID` is the ledger read `converse.md` uses. `--key` defaults to a hash
   of action type, target and payload, so a rerun is `_prior`'s
   (`core/broker.py:583`) same request id and digest: the standing answer,
   no second task. A refusal or failure prints the error and exits
   non-zero; a timeout or dropped connection prints "outcome unknown; rerun
   the same command" and exits non-zero (today it says "the kernel is not
   answering", `:260`, though the gateway thread may still commit).
3. **The end-of-turn path stops carrying them.** `chat_tasks` stays in
   `_performers` (`core/__main__.py:250`): recovery reconciles and
   recollects through that factory (`core/serve.py:242`, `:358`), and an
   in-turn intent left by a killed turn must still settle. Instead
   `session.record` (`core/session.py:1043`) answers a chat session's
   `task.*` effect file with `{**entry, "error": "act with valor-record
   act"}` before `broker.request`, beside the merge refusal at `:1045`; the
   next prompt reports it as it reports that one. The `{effects}` list
   (`offered()` at `core/session.py:179`, `:226`) drops the `task.*` usage
   lines. One mechanism per concern: sends stay end-of-turn effects.
4. **Prompts.** `skills/sdlc/converse.md`: act with `valor-record act …`,
   read its outcome, then write the send that reports it; a failed action
   is reported as failed, in the same turn. The `task.*` usage text moves
   to the `valor-record` section, and `TaskStart.usage`'s "Your next prompt
   names it" (`tools/chat_tasks.py:89`) becomes "the command prints its
   id". `skills/sdlc/roles/product.md` and `skills/sdlc/roles/teammate.md`
   name the command where they name the effect.
5. **Reactions.** `core/reactions.py` `COMMANDS` gains
   `valor-record act (start|answer|steer)` -> `"task"`, so the handed-to-task
   reaction still fires (`EFFECTS` saw only effect files, `:142`). The
   effects report (`_effects_report`, `core/session.py:1658`) stays
   end-of-turn only: the turn already read its in-turn outcomes.

## Timing hazards

- `broker.request` takes `task:<session>` under lock in its own connection;
  the builder confirms nothing holds that lock while the turn runs
  (collect takes it only after `turn.ended`).
- A turn killed after the intent and before the reply: `serve.recover`
  reconciles it through `chat_tasks`' `lookup`, still in the factory.

## Tests (`tests/`)

- In-turn `act start` returns the child's id; the child's `task.started`
  row exists before the turn ends.
- The incident: `act start` with `in` a chat outside the project's chats
  (`ValueError`, `tools/chat_tasks.py:121`) comes back `kind="failed"`
  with that error; `valor-record` exits non-zero; no `task.started` row.
- The same command twice in one turn starts one task.
- A dangling in-turn `task.start` intent plus `serve.recover` writes an
  outcome.
- A chat turn's `task.start` effect file is answered with the error and
  not performed; a send in the same turn is still released.
- `valor-record task ID` and `valor-record act start …` both parse.
- A chat turn whose Bash ran `valor-record act start` gets `task` activity.
- `converse.md` names `valor-record act` and its `{effects}` list has no
  `task.*` line.

## No-gos

- No new check on what the reply says. The order changes; nothing reads
  the text. Left as is: a stale `task.*` file written after a reply that
  claimed the work started is refused after that reply has gone; the next
  prompt says so.
- Not here: waking a session on an outcome (by design, `core/session.py`
  docstring), gap fill's budget, session size.

## Governance

None added: an existing action runs earlier through an existing route and
an existing performer. The collect-time refusal is the old path's removal
(rule 4), written as the merge refusal already is, not a check over a path
that stays.

## Critique Results

Round 1 (2026-11-11): NEEDS REVISION, 3 blockers, 3 concerns; all six
verified against the code and adopted above (performers kept for recovery,
route in the gateway with its factory, `act` verb, reaction pattern,
timeout and default key, incident test).
