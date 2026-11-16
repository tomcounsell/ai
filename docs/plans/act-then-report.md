---
status: Planning
type: bug
appetite: Small
tracking: https://github.com/tomcounsell/ai/issues/3686
---

# A chat session sees what its action did before it writes the reply

Tom, 2026-11-11: "the session has to see the outcome before it writes the
reply"; call 2026-11-16 14:43 UTC: "I agree. We should build that."

## What is wrong (rechecked 2026-11-16, main at a9dc4078f)

A chat turn's `task.start|answer|steer|stop` are effect files the kernel
performs after the turn (`session.record`, `core/session.py:1009`), beside
its sends, so the reply is always written before the action exists.
Case: session `bcfdda8e1251`, 2026-11-11 16:04 +07, the reply "I've
restarted the work" went out, then the `task.start` failed with
`ValueError("… is not one of this project's chats")`; Tom waited an hour.
Again 2026-11-16 13:16 UTC: "passed to the build", then the steer was refused.

## What will be built

1. **`POST /t/<token>/record/act`** in `Gateway.record`
   (`core/gateway.py:500`), the route `valor-record` already uses. `Gateway`
   takes an optional `performers: router.PerformersFactory` (wired at its
   three constructions: `core/__main__.py:492`, `:536`, `core/serve.py:1442`,
   from `_performers`). The body is `{verb, target, payload, key}` with
   `verb` one of `start|answer|steer|stop`; the route builds
   `task.<verb>` itself, so a merge, push or send cannot be named through it
   (the route's definition, not a refusal: any other verb is the 404 an
   unknown record verb gets today). In its own connection and thread, as
   `record.answer` does: `broker.request(conn, performers(await
   tasks.brief(conn, grant.task_id)), grant.task_id, Action(f"task.{verb}",
   target, payload), request_id=f"{grant.turn_id}/act/{key}")`. Answers
   `kind`, `error`, `result`, `effect_id`. Same broker, intent and outcome
   rows, performers and ceilings; only the moment moves.
2. **`valor-record act start|answer|steer|stop`** (`tools/valor_record.py`),
   its own branch, not `fact_write`. Flags map to today's payloads
   (`--instruction`, `--by`, `--context`, `--project`, `--in`, `--text`, a
   task id target). `--key` defaults to a hash of type, target and payload,
   so a rerun in the same turn hits `_prior` (`core/broker.py:583`): a done
   start answers its task id again, no second task. Output: `done` and the
   result (a start prints the child's id), or the error and exit 1. The
   broker's `refused: in flight` and a timeout or dropped connection print
   "outcome not known yet (effect <effect_id> when the route answered);
   read `valor-record task <this session's id>` before you report it" and
   exit 1: an unsettled start has no child id, and the session's own
   ledger holds the intent and, later, its outcome. The session id is the
   Brief's `Task:` line.
3. **A killed kernel's in-turn intent settles.** `serve.recover`'s
   `kernel_types` (`core/serve.py:257`) gains the four `task.*` types;
   `chat_tasks`' `lookup` already reconciles each from the ledger. This also
   settles an end-of-turn `task.*` intent a killed collect left (recollect
   gets `in flight` from `_prior` and never settles it today), and runs on
   the live path too (`Kernel.regain`, `core/serve.py:815`).
4. **One home for the usage.** The four `chat_tasks` performers drop their
   `usage` attribute, so `Performers.offered` (`core/broker.py:145`) stops
   listing them in `{effects}`; the text, rewritten for the command, lives
   only in `skills/sdlc/converse.md`'s `valor-record` section. Every
   `task.*` effect mention a chat session reads becomes the command:
   `converse.md` (14), `roles/product.md:68`, `roles/teammate.md:24`,
   `skills/valor/skills/product-sheet/SKILL.md:135` (the approval step, the
   path of the 2026-11-16 case), `core/notices.py:59` (STALE's
   `task.stop`), `core/call.py:422` (`task.answer`); and the
   `core/intake.py:95-98` docstring for accuracy. Each
   with the order spelled out: act, read the outcome, then write the send
   that reports it; a failed action is reported as failed, in the same turn.
   The effect-file path itself is not refused or removed (no new refusal);
   no prompt offers it.
5. **Reactions.** `core/reactions.py` `COMMANDS` gains
   `valor-record act (start|answer|steer)` -> `"task"`, as `EFFECTS` maps
   the files today (`:142`).

Builder confirms: nothing holds `task:<session>` while a turn runs
(collect locks it after `turn.ended`), so a child start in-turn does not wait.

## Tests

- In-turn `act start` returns the child's id; its `task.started` row exists
  before the turn ends.
- The 2026-11-11 case replayed: `act start --in` a chat outside the
  project comes back `failed` with that `ValueError`; the CLI exits 1 and
  prints it; no `task.started` row.
- The same `act start` twice in a turn starts one task and prints one id.
- `act steer` on a task that does not report to the session: refused, error printed.
- `POST /record/act` with verb `merge` or a send type is not performed: no
  intent row, the 404 answer.
- A dangling in-turn `task.start` intent plus `serve.recover` writes its
  outcome; so does a dangling end-of-turn one (request id `turn/file`);
  and both settle with `live` set, as `Kernel.regain` calls it.
- A timed-out or in-flight act prints the not-known message naming the
  session's id, and exits 1.
- A chat session's `offered()` list has no `task.*` line; `converse.md`,
  `product.md`, `teammate.md`, product-sheet `SKILL.md`, `notices.STALE`
  and the call prompt name `valor-record act` and no `task.*` effect.
- `valor-record task ID` and `valor-record act start …` both parse.
- A turn whose Bash ran `valor-record act start` gets `task` activity.

## Out of scope / no-gos

- Sends stay end-of-turn effects. Nothing reads or checks the reply's text.
- No refusal of a stale `task.*` effect file; the broker performs it as
  today. A turn that both acts and writes the file starts two tasks;
  accepted, since no prompt offers the file. In-turn acts stay out of the
  next prompt's effects report: the turn read them when it acted.
- Not here: waking a session on a later outcome, call sessions' own paths.

## Governance

None added. An existing action runs earlier through an existing route,
broker and performer; recovery covers more types with the existing
`lookup`. Nothing is refused that is performed today.
