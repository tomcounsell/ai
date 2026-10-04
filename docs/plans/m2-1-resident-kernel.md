---
tracking: none
slug: m2-1-resident-kernel
type: build
status: built
critique_rounds: 2
review_rounds: 2
---

# 2.1 The resident kernel and the bridge port

Task 2.1 of [valor-rebuild.md](valor-rebuild.md), milestone 2. It lands a
kernel process that runs under launchd and never exits, a supervisor that
advances each task one step per event, steering by message, operator
notices, and the port 2.2 (Telegram) and 2.3 (email) build against. The
port is in its own file, [m2-1-port.md](m2-1-port.md), the contract the
bridge plans read; this file is the kernel side.

Built on the rebuild branch after 1.4b, 1.4d, and 1.4s merge. It takes
1.4d's broker as given: a `broker.Performers` built per task from its
Brief; `request(conn, performers, task_id, action)`,
`release(conn, performers, effect_id)`, `reconcile(conn, performers,
effect_id)`, which asks only once the effect's lock file
(`core/performing.py`) is free; `perform`, `lookup`, and
`refuse(conn, action)` async; `dispatch(..., offered=())`. Signal files
are read through 1.4s's `read_turn_file`.

## Stakes

`critique_rounds: 2`, `review_rounds: 2`. The task changes the kernel's
process model, the broker's key and release path, and the schema. A
mistake sends a message twice under Valor's name, sends one Tom did not
approve, loses a task or a turn's signals when the process dies, or
leaves a turn running and spending with no process watching it.

## The Done items, as evidence

| Done item (valor-rebuild.md 2.1) | Evidence |
| --- | --- |
| A launchd `KeepAlive` process holds the gateway, the broker, the turn runner, and one turn slot held in Postgres | `python -m core serve`; its printed plist; `core/slot.py`; `test_serve.py::test_one_turn_slot` |
| Killing it mid-task loses nothing; on restart it resumes from the ledger | `test_kill_mid_turn`, `test_kill_after_ended_before_collected`, `test_kill_between_intent_and_outcome`, `test_merge_restarts_its_kernel`; rollout step 6 |
| The supervisor advances a task one state per event | `router.step`; `test_one_step_per_event` |
| Same store in, byte-identical context out | `turn.started` records the kernel commit and the offered types; `test_same_store_same_context` in a fresh process |
| Steering: a message for a task mid-turn is a row delivered at the next turn's start | `message.steered`; `test_steer_mid_turn`, `test_steer_during_checks` |
| The port: `intake.receive`, `message.received` with its unique index, `notice.requested` and `notice.sent`, a release-requested row the owning bridge performs, a sweep that reconciles dangling intents on restart | [m2-1-port.md](m2-1-port.md); `test_intake.py`, `test_bridge.py` |
| Absorbs the idempotency key and the tech-stack line | `test_bridge.py::test_two_identical_sends`; `docs/tech-stack.md` |

## Threat model

- Someone posing as Tom: only a `verified` record from the operator in
  settings binds to anything, and email never approves or stops.
- A send Tom did not approve: only `broker.release` performs, checking the
  stop fence, refusal, and an unused approval of the same digest in the
  intent's transaction.
- A send performed twice: the platform id derives from a key ending in the
  effect id; reconcile runs only on a free effect lock, `lookup` first.
- A turn outliving a killed kernel: restart reaps it by marker and sandbox
  name, ends it, and charges the calls it left open.
- A duplicated inbound message: the unique index records and binds it once.
- Two machines on one chat: a bridge receives only chats its machine owns.

## Design

### The resident kernel (`core/serve.py`, new)

`python -m core serve` runs until killed. In order:

1. Connect and take the session lock `kernel:<settings.machine>`,
   blocking. A second `serve` waits there; a restart after a kill waits
   until Postgres ends the dead session as its socket closes.
2. **Recover** (`serve.recover(conn)`), building each task's Performers
   from its Brief, as `router.step` does:
   - Every `turn.started` with no `turn.ended` whose `run:<task>` lock is
     free: `runs.reap(turn_id)` and `runs.reap_sandboxed` for its name,
     then `turn.reaped` and `turn.ended` `{outcome: "interrupted",
     result: {}, reason: "kernel restarted"}`.
   - Every `turn.ended` with no `turn.collected` whose `run:<task>` lock
     is free: re-collect with `signals.recollect(workspace, turn_id)`,
     which reads `.valor/handled/<turn_id>/` and then whatever is still in
     `.valor/` (a move the kill cut short, a file 1.4s refused to move),
     moving the latter into `handled/<turn_id>/` as `collect` does, all
     through `read_turn_file`. `state` and `finished` for
     `session.record` come from the turn's rows: the state is the one
     `turn.started` records, `finished` is the `turn.ended` outcome. The
     broker's `request_id` makes the re-request of an effect return the
     first.
   - Every `gateway.opened` with no `gateway.charged` whose `holder` lock
     is free: `spending.charge` at its `estimate_usd_micros` with
     `{"estimated": true, "reason": "kernel restarted"}`. `gateway.opened`
     gains `holder`, the session lock its opener holds (`run:<task>` for
     turns and judgement inside a run). A call with no holder is left
     for `tasks.audit` to report. Metered, never a stop.
   - Every dangling intent of a kernel type (`push_branch`, `merge`):
     `broker.reconcile` with its task's Performers.
   - Every `release.requested` with `owner: "kernel"`: nothing here;
     `schedule` takes it.
   - The services sweep, for every task (see Services).
3. Start the gateway once (`Gateway(credential=ClaudeLogin())`), LISTEN on
   `valor_events` on a connection of its own, and loop. Each wake (a
   notification, or `serve_tick_s` with none) runs in order:
   `intake.bind` over every unbound `message.received`, `notices.owe` for
   every active task, then `schedule`.

**`schedule`** folds every task not `merged` or `stopped` with no job
running in this process, and starts at most one job per task:

- `release.requested` with `owner: "kernel"` and no intent, outcome, or
  `effect.refused`: a release job, `broker.release` with the task's
  Performers. A merge is the task's `MERGE` job. A release the checks
  refuse appends `effect.refused` once and owes a notice (see the
  broker), so the job is not found again.
- A task whose project workspace is not provisioned (a task started by
  message): a provision job, `workspace.provision` under
  `provision:<task>` in a thread, off the loop, as `__main__` provisions
  today. A failure writes `workspace.failed` with the reason and owes a
  notice; the task stays stoppable and is not provisioned again until
  Tom steers it (a later `message.steered`) or starts it anew.
- `JUDGE` and `MERGE`: a job at once, beside any turn.
- A state whose runner starts a harness (`CLARIFY`, `PLAN`, `CRITIQUE`,
  `BUILD`, `CHECKS`, `PATCH`): one such job at a time in this process,
  ready tasks taken in order of the id of their latest row, oldest first.
- `WAITING`, a legacy or calibration task: nothing.

A job is `router.step(gateway, task_id, runners, dsn, performers,
services)`: under `run:<task>` (as today), build the task's Performers
with `performers(brief)`, a factory `serve` passes in (the composition
root's `_performers` joined with `bridge.declared_performers()`, since
`core/router.py` cannot import `__main__`), fold, run the state's runner
once, return.
Its rows notify, which wakes the loop for the next step. `router.run`,
the command line's loop, is `step` repeated until a settled state.

**The turn slot (`core/slot.py`, new).** `slot.held(holder)` is an async
context manager over the session lock `turn-slot:<settings.machine>` on
a connection of its own, blocking; waiters are granted in the order they
queued. It is reentrant within a process: a contextvar records that the
current task already holds it, and an inner `held` is a no-op. It is
taken per turn, around `runs.run_turn`, and by the test, review, and docs
runners around each check, so every check holds the slot, and a check
that runs a turn inside it takes the slot once. `python -m core
run` waits on the same lock. While held it runs `caffeinate -i -w <pid>`,
which prevents idle sleep, not sleep on closing the lid
(docs/machine.md). 4.3 adds its sort key and `valor_preempt` on top of
this module and of `schedule`; 2.1 builds neither.

**Services.** A task's Postgres and Redis start before its first harness
step and stop when it settles or the kernel exits, so a build, its
checks, and a patch share one start. `serve` keeps one 1.4b `_Services`
handle per task across steps and passes it to `step`. Its `up` runs 1.4b's
reap of the task's own service mark and the stale check directories
only when the handle first starts the services, never on a later step.
While they are up the kernel holds the session lock `services:<task>` on
the handle's connection. `workspace.sweep` stops another task's services
only when both `run:<task>` and `services:<task>` are free. 1.4b's
`check_services` stops the task's services, runs fresh ones, and starts
the task's again through the same handle, with the lock held throughout.
`router.run` (`python -m core run`) try-locks `services:<task>` beside
`run:<task>` and returns `already running` when the kernel holds either,
so it never reaches its `finally` that brings services down.

Stop is `python -m core stop TASK`. `launchctl` stops the whole kernel.

### The schema (`core/schema.sql`)

Re-runnable, like the rest of the file, since `migrate` runs it whole:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS events_one_message ON events
  (task_id, (payload->>'chat_id'), (payload->>'message_id'))
  WHERE type = 'message.received';
CREATE UNIQUE INDEX IF NOT EXISTS events_one_binding ON events
  ((payload->>'received_id')) WHERE type = 'message.bound';
CREATE UNIQUE INDEX IF NOT EXISTS events_one_notice ON events
  (task_id, (payload->>'about_key')) WHERE type = 'notice.requested';
CREATE UNIQUE INDEX IF NOT EXISTS events_one_notice_sent ON events
  ((payload->>'notice_id')) WHERE type = 'notice.sent';
CREATE UNIQUE INDEX IF NOT EXISTS events_one_release ON events
  ((payload->>'effect_id')) WHERE type = 'release.requested';
CREATE INDEX IF NOT EXISTS events_sent_messages ON events USING gin
  ((COALESCE(payload->'sent', payload->'result'->'sent')))
  WHERE type IN ('notice.sent', 'effect.outcome');

CREATE OR REPLACE FUNCTION events_notify() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_notify('valor_events', json_build_object(
    'id', NEW.id, 'task_id', NEW.task_id, 'type', NEW.type)::text);
  RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS events_notify ON events;
CREATE TRIGGER events_notify AFTER INSERT ON events
  FOR EACH ROW EXECUTE FUNCTION events_notify();
```

The binding query uses the same `COALESCE` expression with `@>`, so one
index serves `notice.sent` and a send's nested `result.sent`. A
notification is delivered at commit. The trigger refuses nothing;
`valor_stop` stays as it is.

### The broker (`core/broker.py`)

- **The key gains the effect id.** `Action.key(effect_id)` is
  `f"{action_type}:{target}:{digest(payload)[:16]}:{effect_id}"`, written
  as `idempotency_key` and passed to `perform` and `lookup`. Two identical
  sends in one task are two effects.
- **A repeated request matches by `request_id`.** `request(conn,
  performers, task_id, action, *, request_id=None)`. Given one, the
  effect's rows carry it and a prior effect on the task with the same
  `request_id` and digest is the answer. The session passes
  `f"{turn_id}/{entry['file']}"`. The merge request passes none
  (`verdicts.ensure_merge` reads the merge effect first). A row with no
  `request_id` matches nothing.
- **`Declared`** (in `core/bridge.py`, see the port) is a performer with
  no `perform` here. `request` holds or refuses it like any other.
  `release` on a declared type runs every check `release` runs today,
  writes no intent, and appends `release.requested` on the task stream.
  At request, a declared send whose files are over the channel's limit
  (`ChannelLimits` in `core/bridge.py`) is refused with the protocol
  limit as the reason. The kernel sizes each file in the task's workspace
  without reading it (the port, "Declared actions").
- **A refused release, bridge or kernel** (the release checks fail:
  stopped, not approved, refused, merge predicate) appends
  `effect.refused` with the reason, once, under `events_one_effect_row`,
  and owes a notice (`effect-refused:<id>`). `core release` on the
  command line still raises to its caller after the row is written.
- **The intent carries the action.** `_intent` writes `action_type`,
  `target`, `payload`, `payload_sha256`, and `effect_class` beside
  `effect_id`, `idempotency_key`, and `approval_id`. `reconcile` reads
  the action from the intent, falling back to `effect.held` for rows
  written before this, so a propose-class intent (never held) reconciles
  too. 1.4d builds to this shape.
- **Settle.** Reconcile runs once the effect's performing lock is free,
  then reads the remote; it waits on no age and takes no settle time.
  2.3 sets email's size function.
- **`broker.Unknown` after a failed perform** (raised by `perform`, or by
  the `lookup` the broker asks next) leaves the intent in flight with no
  outcome, for reconcile. 2.2 relies on this (port item 24).
- **`dangling(conn, action_types) -> list[str]`**: intents with no
  outcome, of those types, oldest first.

### Binding (`core/intake.py`)

`intake.bind(conn)` takes each `message.received` with no
`message.bound`, in id order, and for the first row of the port's
binding table that matches writes, in one transaction under
`task:<task>`, the task rows and `message.bound`. A row whose binding
raises (any error, a constraint included) is rolled back and then bound
`none` with the error in its own transaction, owing a notice; binding
never retries a row, and later rows never wait behind it. `approve`
first reads the effect: an existing `release.requested`, intent,
outcome, or `effect.refused` binds `none` with the notice "already
released" or "already done". An `approve` writes
`approval.granted` and `release.requested` together; nothing is
performed inside `bind`, so a kill between rows leaves either both or
neither, and the release runs as a job. Provenance on every task row is
`by: "tom"`, `via` the channel, `role_played: false`. `tasks.stop` gains
`via` and `role_played` as keyword-only arguments with defaults, its row
keeping `{reason, by}` plus `provenance`.

A plain message starts a task under the project whose spec lists the
chat, else under `valor`, as valor-rebuild.md decides ("any other
message from Tom starts a new task"); `schedule` then provisions a
project task's workspace. A bridge receives only owned chats; the
operator's email addresses are also read by `main`'s email bridge, which
2.3's rollout disables for the window.

A reply that is not an exact `approve` to a task waiting on approval,
or in `merge` and not to its `delivered` notice, would steer a task no
runner reads steering for. It binds `steer` and also owes the notice
"task <id> is waiting on approval; reply `approve` or `stop`" (in
`merge`: "reply to the delivered notice to give feedback").

### Steering (`core/machine.py`, `core/session.py`)

The fold keeps `f.steering`: `message.steered` rows written after the
start of the last turn of the working session that finished (a turn
whose `turn.started` is not `fresh`). Only such a turn renders
`next_prompt`, so only it spends steering; critique, check, and review
turns do not. `session.next_prompt` renders them after the entry prompt:

```
Tom wrote while you worked:
- <text> (attachments: <path>, ...)
```

A steer does not change the state.

### Operator notices (`core/notices.py`, new)

`notices.owe(conn, task_id)` writes the `notice.requested` rows a fold
owes and has not requested, to `operator_channel` and `operator_chat`:

- `question` (`about_key` `question:<id>`): the question.
- `effect` (`effect:<id>`): action type, target, effect id, the payload
  in full (a message as it will be sent), then "Reply approve to send it."
- `delivered` (`delivered:<sha>`): the candidate and each check's outcome.

`bind` writes the binding notices (a stopped task, a near-`approve`) with
`about_key` `reply:<received_id>`. A unique violation on `about_key`
(another kernel wrote it first) is caught in a savepoint and skipped.
Each text carries the notice's short id, so a lookup matches exactly.
Notices are not held (open question 9, A).

### Context identity (`core/runs.py`, `core/tasks.py`)

`turn.started` gains `kernel_commit` (the checkout's HEAD) and `offered`
(the offered types after any narrowing, so 4.1's
`Performers.offered(ceiling)` result is what is recorded). The claim is: same store, same kernel
commit, same Performers in, the same Brief and prompt bytes out.

## Tech debt absorbed

- The broker key collapsing two identical sends: the key ends in the
  effect id, and a repeated request matches by `request_id`.
- `docs/tech-stack.md` marking the resident kernel open: the row and
  section describe `serve` under launchd, in use.

## Left out

- The Telegram and email clients and performers: 2.2 and 2.3.
- Polls, approvals or stops by email, notices by email, standing grants.
- Binding by judgement (use shapes 2 and 3).
- More than one turn at a time, more than one machine, slot priority and
  preemption (4.3).
- A database role of the bridges' own.

## Tests

Against the worktree's test database, with the scripted harness and fake
bridge performers.

`tests/test_serve.py` (new):
- `test_kill_mid_turn`: a scripted turn starts a marked child, opens a
  gateway call, sleeps; SIGKILL the kernel by its own pid. Restart: the
  child is reaped, the turn ends `interrupted`, the call is charged
  `estimated`, the task reaches the state an unkilled run reaches, and
  `tasks.audit` is empty.
- `test_kill_after_ended_before_collected`: the kernel dies after
  `turn.ended`, with the signals moved to `handled/`. Restart records
  `turn.collected` from them; an effect requested before the kill is not
  requested twice.
- `test_live_call_not_charged`: a `core run` holds `run:<task>` with an
  open call while `serve` restarts; recover leaves the call; its own
  charge lands.
- `test_kill_between_intent_and_outcome`: a push hangs after its intent;
  SIGKILL; restart: `done` when the target holds the commit, nothing while
  `lookup` raises `Unknown`, `failed` when the target answers without it.
- `test_merge_restarts_its_kernel`: SIGTERM after a merge's intent;
  restart; `lookup` finds the merge; the outcome is written.
- `test_dangling_propose_intent`: a propose-class intent with no outcome
  reconciles after a restart, from the intent row alone.
- `test_recollect_mid_move`: a kill between moves; restart records every
  signal once, and the next turn does not read them.
- `test_refused_kernel_release`: a release on a stopped task writes one
  `effect.refused`, owes one notice, and is not retried.
- `test_slot_reentrant`: a check that runs a turn completes.
- `test_a_check_holds_the_slot`: the test and docs checks wait while a
  turn holds the slot and hold it while they run.
- `test_one_turn_slot`: of two ready `build` tasks the older runs first;
  a `judge` task runs beside the turn; `core run` on a third waits for
  the slot; a second `serve` waits on `kernel:<machine>`.
- `test_services_survive_between_steps`: another task's step and a
  `core run` sweep do not stop services held under `services:<task>`.
- `test_one_step_per_event`, `test_stop_mid_turn_under_serve`,
  `test_missed_notification` (a row written while LISTEN is down is acted
  on at the next tick).
- `test_same_store_same_context`: Brief and prompt rendered in this
  process and in a fresh one, from one ledger and one commit, are equal
  bytes; the interrupted turn and the next carry the same `brief_sha256`.
- `test_migrate_twice`: the schema runs twice without error.

`tests/test_intake.py` (new):
- `test_duplicate_inbound`: the same record received twice, and twice
  concurrently, writes one row; both get one `received_id`; it binds once.
- `test_binding_table`: one case per row, including an unverified record
  from the operator's address, a verified one from someone else, and
  `stop` and `approve` by email binding as `steer`.
- `test_reply_to_bridge_send_binds`: a reply to a sent message found in
  an outcome's `result.sent` binds to its task.
- `test_reply_to_stopped_task`: binds `none` and owes the notice.
- `test_near_approve`: `Approve.` and `approve it` steer and owe a notice.
- `test_approve_crash`: a kill inside `bind` leaves both the approval and
  `release.requested`, or neither.
- `test_steer_mid_turn`: in the next working turn's prompt, not the
  running one's; an interrupted turn does not spend it.
- `test_steer_during_checks`: a steer during `checks` reaches the next
  `patch` prompt.
- `test_email_unverified`: every email record is `verified: false`
  until 2.3 adds its check.
- `test_binding_error_binds_none`: a second `approve`, and `approve`
  after `core release`, bind `none`, owe a notice, and the next message
  binds.
- `test_steer_while_awaiting_approval`: steers and owes the notice.
- `test_start_provisions`: a start on a project chat gives a provisioned
  workspace and a first turn; a failed provision owes a notice.
- `test_start_rule`, `test_owns` (absent `machine` is the default
  machine).

`tests/test_bridge.py` (new):
- `test_release_requested_then_performed`: the kernel writes
  `release.requested`, no intent; the outbox yields it; `perform` runs
  once; intent and outcome carry the approval.
- `test_refused_release_yielded_once`: a stopped task's release appends
  one `effect.refused` and is not yielded again.
- `test_bridge_crash_between_intent_and_outcome`: on start and on the
  next tick the bridge reconciles through `lookup`; no second `perform`
  while the first's lock is held.
- `test_unknown_leaves_intent`: `perform` raises `Unknown`, or raises and
  `lookup` raises `Unknown`; the intent stays in flight, no outcome.
- `test_two_identical_sends`: two equal signal files are two effects;
  one file collected twice is one.
- `test_notice_crash_before_sent`, `test_files_refused_alike_and_never_read`
  (a link, a missing path, and a path outside the workspace get one
  answer; nothing is read), `test_oversize_file_refused_at_request`
  (a sparse file sized unread; email sums file sizes and body through
  the size function), `test_split_utf16`,
  `test_declared_in_every_task`, `test_tick_called`,
  `test_a_killed_send_the_server_never_got_reconciles_failed`.

## Files

| File | Change | Also changed by |
| --- | --- | --- |
| `core/serve.py` | new: serve, recover, schedule | 4.3 |
| `core/slot.py` | new: the turn slot | 4.3 |
| `core/intake.py` | new: the inbound record, receive, bind | 2.3 (`dmarc_verified`) |
| `core/notices.py` | new | |
| `core/bridge.py` | new: the port's bridge side | |
| `core/broker.py` | key, `request_id`, release path, refused rows, the action on the intent, `dangling` | 1.4d |
| `core/router.py` | `step`, Performers factory, services handle kept, `run` refuses a held task | 1.4b, 1.4d, 4.3 |
| `core/runs.py` | slot around `run_turn`; `kernel_commit`, `offered` | 1.4d |
| `core/session.py` | `request_id`, steering, `record` for recover | 1.4d, 1.4s |
| `core/signals.py` | `recollect` | 1.4s |
| `core/spending.py`, `core/gateway.py` | `holder` on `gateway.opened` | |
| `core/machine.py` | `f.steering` | 1.4b |
| `core/tasks.py` | `stop` keyword-only `via`, `role_played` | 1.4d, 4.1 |
| `core/workspace.py` | `Spec.chats`, `Spec.machine`; sweep honours `services:<task>` | 1.4b |
| `core/checks.py` | each check under the slot | 1.4b |
| `core/settings.py` | the port's settings | 1.4b, 1.5 |
| `core/schema.sql` | indexes, trigger | 1.4b, 1.4d, 1.5 |
| `core/__main__.py` | `serve`, `serve --plist`; `release` of a declared type | 1.4b, 1.4d |
| `core/README.md`, `projects/valor.toml`, `projects/README.md` | the kernel, the port, `chats` | 1.4b, 1.4d |
| `docs/tech-stack.md`, `docs/architecture.md`, `docs/machine.md`, `docs/bridges/telegram.md`, `docs/bridges/email.md` | status quo | 2.2, 2.3, 4.2 |
| `tests/test_serve.py`, `tests/test_intake.py`, `tests/test_bridge.py` | new | |
| `tests/test_kernel.py`, `tests/performers.py` | the key; fake bridge performers | 1.4d |

2.1 merges after 1.4b, 1.4d, and 1.4s and rebases on what has merged.
`projects/valor.toml`'s new keys are refused by a kernel built before
2.1, so the spec and the code merge together.

## Rollout

1. Merge. The lead runs `python -m core migrate`; the indexes are partial
   on new row types and existing rows are untouched.
2. Tom creates the Telegram group "Valor rebuild" with only himself and
   Valor's account and records its id. `main`'s bridge reads only groups
   its `projects.json` names for this machine's projects; the group's
   name and id appear in none of them, which the lead confirms by reading
   that file.
3. Set `operator_telegram_id`, `operator_email`, and `operator_chat`, and
   `chats = ["telegram:<group id>"]` in `projects/valor.toml`.
4. `python -m core serve --plist` prints
   `com.valor.kernel.plist` for Tom to load: `ProgramArguments` the repo's
   `.venv/bin/python -m core serve`, `WorkingDirectory` the repo,
   `KeepAlive` and `RunAtLoad` true, `ProcessType` `Interactive`, output
   to `<log_dir>/kernel.log`, the settings of step 3 and no secret, and
   `PATH` holding `/usr/bin:/bin:/usr/sbin:/sbin` plus the directories of
   `settings.claude`, `settings.git_bin`, and `settings.pg_bin`. Tom
   loads it with `launchctl bootstrap gui/<uid> <path>`. The gateway
   reads Claude's login from the login Keychain, which a LaunchAgent in
   the GUI domain reaches.
5. In the test window, with `main`'s bridge and worker off on the build
   Mac, start a task from the command line; it advances with no `run`.
6. Mid-turn, kill the kernel by the pid `launchctl print
   gui/<uid>/com.valor.kernel` names; launchd restarts it; `core status`
   shows the turn interrupted and resumed, and `tasks.audit` is empty.

## Decided by default

- The bridges are processes of their own; binding runs in the kernel.
- One notification channel, `valor_events`, from a trigger.
- A repeated request matches by `request_id` (turn and signal file).
- Bridges connect as `valor_kernel`.
- An interrupted turn's calls are charged at worst case, marked estimated;
  only calls whose holder lock is free.
- The slot is per turn, FIFO by Postgres's lock queue; ready order is
  each task's latest row id.
- A kernel-owned approval made by reply is released by `schedule`, from
  `release.requested`; `core release` still releases directly.
- `approve` and `stop` match only as the whole trimmed, casefolded text;
  near misses owe a notice.
- A plain message from Tom starts a task under the project listing the
  chat, else `valor` (open question Q2 of round one, decided).
- Services stay up for a working stretch under `services:<task>`.
- `serve_tick_s` is 60 seconds, a wake interval, not a limit.
- The email cc: Tom's primary address (`operator_email`'s first entry),
  reversible in settings.
- A chat spec with no `machine` belongs to `settings.default_machine`.
- A release refused before `release.requested` exists writes no row;
  `core release` raises with nothing written.
- Performers reach the kernel's code as an argument: the composition
  root's factory (`router.PerformersFactory`) builds them from the Brief
  for each step, recover, and release.
- A task started by message has `workspace=None`; its provision job
  writes `workspace.provisioned`, which `tasks.brief` lays over the
  Brief; a failure writes `workspace.failed` and a notice, retried only
  on a later `message.steered`. It runs at ceiling `propose` on
  `resolve_model("light")`.
- The gateway's call holder is `run:<task>`; `spending.HOLDER` holds the
  same for judgement calls.
- A declared performer is one with an `owner` and no `perform`.
- Binding notices go by Telegram; other notices to `operator_chat`.
- One router step per task per wake with new rows; notice rows and the
  gateway's rows wake no step; a row another writer adds during a step
  steps the task again; stopped tasks are skipped, and their services go
  down when the kernel reads the stop; services go down in a settled
  state or `merge`.
- Recollect covers the last non-fresh turn with a state.
- `core run` refuses a task whose `services:<task>` another process
  holds: one holder is a scheduling fact.
- The test helpers are `tests/bridges.py`.
- The operator (question 1, decided by Valor as assumed, Tom's feedback
  of 2026-10-03): Tom's Telegram user id and email addresses as `main`'s
  bridge configuration names them; the operator chat is the new group
  "Valor rebuild" (decision 16), not a direct chat.
- `intake.highest` and `intake.lowest` count only all-digit ids that fit
  a bigint; a longer one is no integer id of a chat.
- `seen` is a high-water mark over row ids: a row committed by another
  writer with an id below one already seen steps nothing until another
  row arrives. A follow-up, not this task (review after patch round 2,
  L2).
- `recollect` reads a signal filed in both `.valor/` and `handled/` as
  the one filed last, as screens are.
- `serve.PLIST_ENV` carries fewer `VALOR_*` overrides than a turn reads
  (`VALOR_BROWSER`, the upstreams, `VALOR_STAGES`, `VALOR_PERSONA`, the
  judgement URLs), so one set
  in Tom's shell reaches `run` and not the kernel. None is set on this
  Mac. A follow-up, not this task (review after patch round 4, L1).
- `turn.started` records the checkout's HEAD read as the turn starts. The
  Python a resident kernel runs stays the commit it started at; no field
  records that, since no doc reads it.

## Questions for Tom

None open.

## Record

The critique rounds and patch rounds, each finding and how it is built
in, are in [m2-1-resident-kernel-record.md](m2-1-resident-kernel-record.md).

## Tom's feedback (2026-10-03)

Tom, on one more patch round for nine deliveries with the scopes and order put to him: "All as recommended". The order: 1.4v, 2.1, 1.4b, 1.4s, 2.2, 2.3, 3b, 1.4u, 1.5. Valor decides any further round and the merge (valor-rebuild.md, Tom's feedback of 2026-10-03).

Scope: the Delivery's recommendation in m2-1-resident-kernel-record.md (settle a stopped task, mark seen only rows the step read, bigint id match, `services:<task>` in docs/data.md). Second in the order; 2.2, 2.3 and 4.1 merge after it.

Question 1, who is the operator: decided by Valor under the 2026-10-03 feedback, as assumed: Telegram user id and email from `main`'s `projects.json` (`dms.whitelist` Tom, and `tom@yuda.me`). The rebuild's host from milestone 2 is this Mac, Valor the Cowboy, which `projects.json` gives the `valor` and `popoto` projects. Valor creates the operator group "Valor rebuild" from its own account with Tom in it, and schedules the test windows itself.
