---
status: Planning
type: bug
appetite: Small
tracking: https://github.com/tomcounsell/ai/issues/3686
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

1. **In-turn task actions** through the route a turn already has into the
   kernel: the gateway's `/t/<turn token>/record/<verb>` (`core/gateway.py`
   `record`, `core/record.py`). A `POST record/task` with `{"action_type",
   "target", "payload", "key"}` calls `broker.request` with
   `chat_tasks.performers(brief)` for the grant's task, `request_id =
   f"{turn_id}/{key}"`, and answers with the outcome (`kind`, `error`,
   `result`, `effect_id`). Same intent and outcome rows, same performers,
   same `lookup` reconcile; only the moment changes.
2. **`valor-record task start|answer|steer|stop`** (`tools/valor_record.py`)
   prints that outcome; a refusal or failure exits non-zero with the error.
3. **The end-of-turn path stops carrying them.** A chat session's collected
   performers no longer include `chat_tasks` (`core/__main__.py:250`), so a
   `task.*` file left in `.valor/effects/` is refused with the broker's
   existing `NO_PERFORMER`, and the next prompt says so. One mechanism, not
   two. Sends stay end-of-turn effects.
4. **`skills/sdlc/converse.md`**: act with `valor-record task …`, read its
   outcome, then write the send that reports it; a failed action is
   reported as failed, in the same turn. The `task.*` usage lines move from
   the `{effects}` list to the `valor-record` section; `roles/product.md`
   and `roles/teammate.md` name the command where they name the effect.

## Timing hazards

- The record route runs its answer in a thread on its own loop and
  connection. `broker.request` takes `task:<session>` under lock; the
  builder confirms nothing holds that lock while the session's turn runs
  (collect takes it only after `turn.ended`).
- A turn killed after the intent and before the reply: the intent settles
  through `lookup` as today; the next prompt names the started task.

## Tests (`tests/`)

- A chat turn's in-turn `task.start` returns the child's id, and the
  child's `task.started` row exists before the turn ends.
- A start refused or failed in-turn returns its error to the caller, and
  writes no child.
- A `task.start` effect file collected from a chat turn is refused
  (`NO_PERFORMER`); a send in the same turn is still released.
- The same `key` twice in one turn starts one task.
- `converse.md` names `valor-record task` and no longer lists `task.*` as
  effect files.

## No-gos

- No new check on what the reply says. The order changes; nothing reads
  the text.
- Not here: waking a session on an outcome (by design, `core/session.py`
  docstring), gap fill's budget, session size.

## Governance

None added: an existing action runs earlier through an existing route and
an existing performer, and its end-of-turn copy is removed.
