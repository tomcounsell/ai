---
tracking: none
slug: m2-1-resident-kernel
type: build
status: planned
critique_rounds: 2
review_rounds: 2
---

# 2.1 The resident kernel and the bridge port

Task 2.1 of [valor-rebuild.md](valor-rebuild.md), milestone 2. It lands a
kernel process that runs under launchd and never exits, a supervisor that
advances each task one step per event, steering by message, and the port
that 2.2 (Telegram) and 2.3 (email) build against in parallel. The port
section below is the contract those two tasks read; its names, arguments,
rows, payloads, and indexes are exact.

Built on the rebuild branch after 1.4b and 1.4d merge. It takes 1.4d's
shape as given: the composition root builds a `broker.Performers` per
process and passes it to `request`, `release`, `reconcile`, and
`tasks.dispatch`; no module global holds performers; a performer's
`perform` and `lookup` are `async`. Where this plan names a signature that
1.4d also touches, 1.4d's merged form is the base and this plan adds only
what it says.

## Stakes

`critique_rounds: 2`, `review_rounds: 2`. The task changes the kernel's
process model, the broker's idempotency key, the release path, and the
schema. A mistake sends a message twice under Valor's name, sends one Tom
did not approve, loses a task when the process dies, or leaves a turn
running and spending with no process watching it.

## The Done items, as evidence

| Done item (valor-rebuild.md 2.1) | Evidence |
| --- | --- |
| A launchd `KeepAlive` process holds the gateway, the broker, the turn runner, and one turn slot held in Postgres | `python -m core serve` (`core/serve.py`); the plist `python -m core service install` writes; `test_serve.py::test_one_turn_slot` (two ready tasks, one turn at a time, a second `serve` waits on the kernel lock) |
| Killing it mid-task loses nothing; on restart it resumes from the ledger | `test_serve.py::test_kill_mid_turn` (SIGKILL during a scripted turn, restart, the task reaches the same final state with `tasks.audit` empty); rollout step 5 on the build Mac |
| The supervisor advances a task one state per event | `router.step` runs one runner once; `test_serve.py::test_one_step_per_event` |
| Same store in, byte-identical context out | `test_serve.py::test_same_store_same_context` (the Brief and prompt rendered twice from one ledger, and after a restart, are equal bytes) |
| Steering: a message for a task mid-turn is a row delivered at the next turn's start | `message.steered`; `test_intake.py::test_steer_mid_turn` |
| The port: `intake.receive`, `message.received` with its unique index, `notice.requested` and `notice.sent`, a release-requested row the owning bridge performs, a sweep that reconciles dangling intents on restart | The Port section; `test_intake.py`, `test_port.py` |
| Absorbs the idempotency key and the tech-stack line | `test_port.py::test_two_identical_sends`; `docs/tech-stack.md` row and section |

## Threat model

- Someone posing as Tom: only a `verified` record from the operator in
  settings answers, approves, stops, or starts work; the rest is inert.
- A send Tom did not approve: a bridge performs only through
  `broker.release`, which checks the stop fence, refusal, and an unused
  approval of the same digest in the intent's transaction.
- A send performed twice: the platform id derives from a key ending in the
  effect id; reconcile runs only on a free effect lock, `lookup` first.
- A turn outliving a killed kernel: restart reaps it by marker and sandbox
  name, ends it, and charges its open calls before anything else.
- A duplicated inbound message: the unique index records and binds it once.
- Two machines on one chat: a bridge receives only chats its machine owns.

## Design

### The resident kernel (`core/serve.py`, new)

`python -m core serve` runs until killed. In order:

1. Connect and take the session advisory lock `kernel:<settings.machine>`,
   blocking. A second `serve` waits there, and a restart after a kill
   waits only until Postgres ends the dead session, which it does as the
   socket closes.
2. Recover (`serve.recover(conn, performers)`):
   - For every `turn.started` with no `turn.ended` whose task's
     `run:<task>` lock is free: `runs.reap(turn_id)` and
     `runs.reap_sandboxed` for its name, then `turn.reaped` and
     `turn.ended` with `outcome: "interrupted"`, `result: {}`, and
     `reason: "kernel restarted"`.
   - For every `gateway.opened` with no `gateway.charged`:
     `spending.charge` with the call's `estimate_usd_micros` and
     `{"estimated": true, "reason": "kernel restarted"}`. Metered, never a
     stop.
   - For every dangling intent (an `effect.intent` with no
     `effect.outcome`) whose action type this process performs:
     `broker.reconcile(conn, effect_id, performers)`.
   - The services sweep `router._Services.sweep` runs today, for every
     task.
3. Start the gateway once (`Gateway(credential=ClaudeLogin())`), LISTEN on
   `valor_events` on a connection of its own, and loop. Each wake (a
   notification, or `settings.serve_tick_s` with none) runs, in order:
   `intake.bind` over every unbound `message.received`,
   `notices.owe` for every active task, then `schedule`.

`schedule` folds every task whose fold is not `merged` or `stopped` and
that has no job running in this process, and starts at most one job per
task:

- `JUDGE` and `MERGE`: a job runs at once, beside any turn. A judgement
  call or a merge request is not a turn.
- A state whose runner starts a harness (`CLARIFY`, `PLAN`, `CRITIQUE`,
  `BUILD`, `CHECKS`, `PATCH`): the job needs the turn slot. Ready tasks
  are taken in order of the id of their latest row, oldest first.
- `WAITING`, `MERGED`, `STOPPED`, a legacy or calibration task: nothing.

A job is `router.step(gateway, task_id, runners, dsn, performers)`: under
the task's `run:<task>` lock (as today), fold, run the state's runner
once, and return. The rows it writes notify, which wakes the loop for the
next step. `router.run`, the command line's loop, becomes `step` repeated
until a settled state, so `python -m core run` behaves as it does today.

The turn slot is the session advisory lock `turn-slot:<settings.machine>`,
held by the job's own connection for the runner's life. `python -m core
run` takes the same lock for a harness state, waiting for it, so a command
line run and the resident kernel never run two turns at once. While a job
holds the slot, the kernel runs `caffeinate -i -w <kernel pid>` so the Mac
does not sleep mid-turn, and stops it when the slot is released.

A task's services start before its first harness step and stop when it
settles or the kernel exits, so a build, its checks, and a patch share
one start. Stop is `python -m core stop TASK`, as today; `launchctl`
stops the whole kernel, never one task.

### The schema (`core/schema.sql`)

One notification trigger and these indexes, each making a fold or a
binding total:

```sql
CREATE UNIQUE INDEX events_one_message ON events
  (task_id, (payload->>'chat_id'), (payload->>'message_id'))
  WHERE type = 'message.received';
CREATE UNIQUE INDEX events_one_binding ON events ((payload->>'received_id'))
  WHERE type = 'message.bound';
CREATE UNIQUE INDEX events_one_notice ON events (task_id, (payload->>'about_key'))
  WHERE type = 'notice.requested';
CREATE UNIQUE INDEX events_one_notice_sent ON events ((payload->>'notice_id'))
  WHERE type = 'notice.sent';
CREATE UNIQUE INDEX events_one_release ON events ((payload->>'effect_id'))
  WHERE type = 'release.requested';
CREATE INDEX events_sent_messages ON events USING gin ((payload->'sent'))
  WHERE type IN ('notice.sent', 'effect.outcome');

CREATE FUNCTION events_notify() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_notify('valor_events', json_build_object(
    'id', NEW.id, 'task_id', NEW.task_id, 'type', NEW.type)::text);
  RETURN NULL;
END $$;
CREATE TRIGGER events_notify AFTER INSERT ON events
  FOR EACH ROW EXECUTE FUNCTION events_notify();
```

A notification is delivered at commit. The trigger refuses nothing.

### The broker (`core/broker.py`)

- **The key gains the effect id.** `Action.key(effect_id)` is
  `f"{action_type}:{target}:{digest(payload)[:16]}:{effect_id}"`, written
  as `idempotency_key` on the effect's rows and passed to `perform` and
  `lookup`. Two identical sends in one task are two effects.
- **A repeated request is matched by `request_id`, not by the key.**
  `request(conn, task_id, action, performers, *, request_id=None)`. When
  `request_id` is given, the effect's rows carry it and a prior effect on
  the task with the same `request_id` and payload digest is the answer
  (`_prior` matches on it). The session passes
  `f"{turn_id}/{entry['file']}"`, one signal file of one turn, so
  collecting a turn twice never requests twice. The merge request passes
  none (`verdicts.ensure_merge` reads the merge effect's state before it
  asks). A row with no `request_id` matches nothing.
- **A declared performer.** `broker.Declared(action_type, effect_class,
  usage, owner, refuse=None)` is a performer with no `perform` in this
  process. `request` holds or refuses it like any other. `release` on a
  declared type runs every check `release` runs today (stop fence,
  `refuse`, the merge predicate for a merge, an unused approval whose
  digest matches), writes no intent, and appends `release.requested`
  `{"effect_id", "approval_id", "owner"}`. It returns
  `Outcome(effect_id, "pending")`. A second release of the same effect
  returns the same pending outcome; the unique index keeps one row.
- **The owner performs.** In the owning bridge's process the same
  `release(conn, effect_id, performers)` finds a real performer, repeats
  the checks, writes `effect.intent` with the `approval_id`, performs,
  and writes `effect.outcome`, under the per-effect session lock
  `effect:<id>` as today.
- **`dangling(conn, action_types) -> list[str]`**: effect ids with an
  intent and no outcome, of those types, oldest first. The kernel's
  recover and each bridge's start pass them to `reconcile`.

### Intake and binding (`core/intake.py`, new)

`intake.receive` records and returns; the kernel binds. `intake.bind(conn,
performers)` takes each `message.received` with no `message.bound`, in id
order, and for the first row below that matches writes, in one
transaction under `task:<task>`, the task row and `message.bound`
`{"received_id", "task_id", "as"}` on the channel stream:

| The record | Bound as | Task row |
| --- | --- | --- |
| Not from the operator, or not `verified` | `none` | none |
| Reply to any notice or sent message of a task, text exactly `stop` | `stop` | `task.stopped` (`tasks.stop`) |
| Reply to a `question` notice whose question is open | `answer` | `question.answered` (`session.answer`) |
| Reply to a `delivered` notice, task in `merge` or `merged` | `feedback` | `feedback.given` (`session.feedback`) |
| Reply to an `effect` notice, text exactly `approve` (trimmed, casefolded) | `approve` | `approval.granted`, then `broker.release` |
| Any other reply to a notice or sent message of a task | `steer` | `message.steered` |
| Not a reply, in a chat a project spec lists | `start` | `task.started` for that project, the text as the instruction |
| Not a reply, in a chat no spec lists | `none` | none |

A reply finds its task through `events_sent_messages`: the `sent` list on
`notice.sent` and on a bridge send's `effect.outcome`. Every task row
carries provenance `by: "tom"`, `via` the channel, `role_played: false`.
`tasks.stop`, `session.answer`, and `session.feedback` take `via` and
`role_played` (stop gains them).

### Steering (`core/machine.py`, `core/session.py`)

`message.steered` `{"received_id", "channel", "chat_id", "message_id",
"text", "attachments", "provenance"}` on the task stream. The fold keeps
`f.steering`: the steered rows written after the start of the last turn
that finished. A finished turn spends them, as it spends an answer; an
interrupted or failed turn does not. `session.next_prompt` renders them
after the entry prompt and before the errors report:

```
Tom wrote while you worked:
- <text> (attachments: <path>, ...)
```

A steered message does not change the state.

### Operator notices (`core/notices.py`, new)

`notices.owe(conn, task_id)` writes the notices a task's fold owes and
has not requested. Each is `notice.requested` on the task stream:

```json
{"notice_id": "<new_id>", "channel": "telegram", "chat_id": "<operator chat>",
 "kind": "question | effect | delivered",
 "about_key": "question:<question_id> | effect:<effect_id> | delivered:<sha>",
 "text": "<plain text>", "reply_to": null}
```

- `question`: an open question; the text is the question.
- `effect`: a held effect. The text names the action type, the target,
  the effect id, and the payload in full (a message's text as it will be
  sent), then "Reply approve to send it."
- `delivered`: `task.delivered`. The text names the candidate sha and
  each check's outcome.

`owe` is idempotent by `about_key`. Notices are not held (Q9 A). The
channel and chat are `settings.operator_channel` and `operator_chat`.

## Port

What 2.2 and 2.3 build against. Every name below is in `core/`; a bridge
imports `core.port`, `core.intake`, and `core.broker` and nothing else
from the kernel.

### The inbound record (`core/intake.py`)

```python
@dataclass(frozen=True)
class Attachment:
    path: str          # under settings.inbound_dir/<channel>/, written before receive
    sha256: str
    mime: str
    name: str
    bytes: int

@dataclass(frozen=True)
class Inbound:
    channel: str                 # "telegram" or "email"
    chat_id: str                 # Telegram peer id as text; email: the sender address, lowercased
    chat_kind: str               # "dm", "group", or "email"
    message_id: str              # Telegram message id as text; email: the Message-ID header
    sender_id: str               # Telegram user id as text; email: the From address, lowercased
    sender_name: str
    sent_at: str                 # ISO 8601, UTC
    verified: bool               # Telegram: always true; email: DMARC pass for the From domain
    kind: str = "message"        # "message" only; polls are not carried
    text: str = ""
    reply_to: str | None = None  # Telegram: replied message id; email: In-Reply-To
    thread: list[str] = field(default_factory=list)       # email References, oldest first
    topic_id: str | None = None  # Telegram forum topic
    attachments: list[Attachment] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)  # email only; UID and UIDVALIDITY go here
```

```python
@dataclass(frozen=True)
class Received:
    received_id: str   # the message.received row's payload id
    duplicate: bool    # true when this (channel, chat_id, message_id) is already recorded

class Intake:
    async def receive(self, inbound: Inbound) -> Received: ...
    async def highest(self, chat_id: str) -> int | None: ...
    def owns(self, chat_id: str) -> bool: ...
```

- `receive` appends `message.received` with `task_id` = the channel and
  payload `{"received_id": <new_id>, **asdict(inbound)}`, in its own
  transaction, and returns after commit. A unique violation on
  `events_one_message` returns the first row's `received_id` with
  `duplicate: true`. The bridge acknowledges to the platform (Telegram
  read state, IMAP `\Seen`) only after `receive` returns.
- `highest(chat_id)` is the largest `message_id` recorded for the chat,
  compared as an integer, or None. Telegram gap fill starts there.
- `owns(chat_id)` is true for the operator chat and for a chat a project
  spec lists under `chats` whose `machine` is `settings.machine` or
  absent. A bridge does not receive a chat it does not own.

### The bridge (`core/port.py`, new)

```python
@dataclass(frozen=True)
class ChannelLimits:
    max_text: int        # characters per message
    max_file_bytes: int

PerformFn = Callable[[broker.Action, str], Awaitable[dict[str, Any]]]
LookupFn = Callable[[broker.Action, str], Awaitable[dict[str, Any] | None]]

class Bridge(Protocol):
    channel: str
    limits: ChannelLimits
    def performers(self) -> dict[str, tuple[PerformFn, LookupFn]]: ...
    async def run(self, intake: Intake, outbox: Outbox) -> None: ...

async def serve(bridge: Bridge) -> None: ...
```

A bridge module's `__main__` is `asyncio.run(port.serve(bridge()))`.
`serve`:

1. Takes the session lock `bridge:<channel>:<settings.machine>`.
2. Builds `broker.Performers` from the declarations in
   `port.DECLARED` for the bridge's channel, each joined with the
   `(perform, lookup)` the bridge returns for that type. The class,
   usage, and refusal are the kernel's; the bridge supplies only how.
3. Reconciles `broker.dangling(conn, <its types>)`.
4. Runs `bridge.run(intake, outbox)` until it returns or raises; then
   exits nonzero, and launchd restarts it.

`perform(action, key)` returns the result and raises on failure.
`lookup(action, key)` returns the result of a send that happened, None
when the platform holds none, and raises `broker.Unknown` when it cannot
tell. Both results carry
`{"sent": [{"channel": str, "chat_id": str, "message_id": str}, ...]}`,
one entry per platform message (a long text split in two is two), which
is how a reply binds to its task.

### The declared actions (`core/port.py`)

```python
DECLARED = {
    "telegram.send_message": broker.Declared(
        "telegram.send_message", "act", <usage text>, owner="telegram", refuse=_telegram_refuse),
    "email.send": broker.Declared(
        "email.send", "act", <usage text>, owner="email", refuse=_email_refuse),
}
```

- `telegram.send_message`: target is the chat id as text. Payload
  `{"text": str, "reply_to": str | None, "topic_id": str | None,
  "files": [{"path": str, "sha256": str}]}`. Refused when the chat is not
  owned, when text and files are both empty, or when a file is missing or
  its sha256 differs.
- `email.send`: target is the `to` addresses, lowercased, sorted, joined
  by commas. Payload `{"to": [str], "cc": [str], "subject": str, "body":
  str, "in_reply_to": str | None, "references": [str], "files": [{"path":
  str, "sha256": str}]}`. Refused when `to` is empty or a file is missing
  or differs.

A `random_id` or Message-ID derived from the key differs between two
identical sends and repeats for a retry of one.

### The outbox (`core/port.py`)

```python
@dataclass(frozen=True)
class Release:
    effect_id: str

@dataclass(frozen=True)
class NoticeDue:
    notice_id: str
    task_id: str
    chat_id: str
    text: str
    reply_to: str | None

class Outbox:
    def __aiter__(self) -> AsyncIterator[Release | NoticeDue]: ...
    async def perform(self, item: Release) -> broker.Outcome: ...
    async def sent(self, item: NoticeDue, sent: list[dict[str, str]]) -> None: ...
```

- Iterating yields, oldest first, every `release.requested` whose owner
  is this channel and whose effect has no intent, then every
  `notice.requested` on this channel with no `notice.sent`; then waits on
  `valor_events` for a row of either type and yields again. A
  notification missed while reconnecting is caught by the next full read
  at `settings.serve_tick_s`.
- `perform(item)` calls `broker.release(conn, item.effect_id,
  performers)`: the checks, the intent, the bridge's `perform`, the
  outcome. A release the checks refuse here (task stopped, file changed)
  appends `effect.refused` with the reason, which ends the effect.
- `sent(item, sent)` appends `notice.sent` `{"notice_id", "sent"}` on
  the notice's task stream. A notice sent but not marked (a crash between)
  is yielded again; a Telegram notice's `random_id` is derived from
  `notice_id`, so the platform drops the second send.

A bridge never writes an approval, an intent, or an outcome itself, and
never sends a message the outbox did not yield.

### Settings (`core/settings.py`)

| Field | Env | Default |
| --- | --- | --- |
| `machine` | `VALOR_MACHINE` | the short host name |
| `operator_telegram_id` | `VALOR_OPERATOR_TELEGRAM_ID` | none (no Telegram sender is the operator) |
| `operator_email` | `VALOR_OPERATOR_EMAIL` | none |
| `operator_channel` | `VALOR_OPERATOR_CHANNEL` | `telegram` |
| `operator_chat` | `VALOR_OPERATOR_CHAT` | none (notices wait unsent) |
| `inbound_dir` | `VALOR_INBOUND` | `~/valor-inbound` |
| `serve_tick_s` | `VALOR_SERVE_TICK_S` | 60 |

The operator is a sender whose `sender_id` equals `operator_telegram_id`
on Telegram or `operator_email` on email, on a `verified` record.

Project specs (`core/workspace.py` `Spec`) gain `chats: tuple[str, ...]`
(`"telegram:<chat id>"`, `"email:<address>"`) and `machine: str | None`.
A message that starts a task starts it in the project whose spec lists
the chat.

## Tech debt absorbed

- The broker key collapsing two identical sends in one task: the key ends
  in the effect id, and a repeated request is matched by `request_id`.
- `docs/tech-stack.md` marking the resident kernel open: the kernel
  process row and section describe `serve` under launchd, in use.

## Left out

- The Telegram and email performers, clients, and processes: 2.2 and 2.3.
- Polls, approvals or stops by email, notices by email, standing grants.
- Binding by judgement (use shapes 2 and 3).
- More than one turn at a time, and more than one machine.
- A database role of the bridges' own.

## Tests

All against the worktree's test database, with the scripted harness
(`tests/scripted.py`) and fake bridge performers (`tests/performers.py`).

`tests/test_serve.py` (new):
- `test_kill_mid_turn`: `serve` in a subprocess; a scripted turn that
  starts a marked child and opens a gateway call, then sleeps. SIGKILL the
  kernel by its own pid. The child still runs. Restart `serve`: the child
  is reaped, `turn.reaped` and `turn.ended` (`interrupted`) are written,
  the open call is charged with `estimated: true`, the next turn resumes
  the same entry, the task reaches the state an unkilled run reaches, and
  `tasks.audit` is empty.
- `test_kill_between_intent_and_outcome`: a kernel performer (push) that
  hangs after its intent; SIGKILL; restart: `reconcile` asks `lookup`,
  writes `done` when the target holds the commit, writes nothing while
  `lookup` raises `Unknown`, and `failed` only after `reconcile_after_s`.
- `test_one_turn_slot`: of two ready tasks in `build`, the one with the
  older latest row runs first and the other only after it ends; a `judge`
  task runs beside a turn; `core run` on a third waits for the slot; a
  second `serve` waits on `kernel:<machine>` until the first is killed.
- `test_one_step_per_event`: each `router.step` writes the rows of one
  runner; a step's own rows wake the next.
- `test_same_store_same_context`: the Brief and prompt dispatched from one
  ledger, rendered twice and again in a restarted process, are equal
  bytes; the interrupted turn and the turn after the restart carry the
  same `brief_sha256`.
- `test_stop_mid_turn_under_serve`: `core stop` kills the turn; the task
  folds `stopped`; the slot is free.
- `test_missed_notification`: a row written while the LISTEN connection is
  down is acted on at the next tick.

`tests/test_intake.py` (new):
- `test_duplicate_inbound`: the same record received twice, and twice
  concurrently from two connections, writes one `message.received`; both
  calls return the same `received_id`; the second says `duplicate`; it
  binds once.
- `test_binding_table`: one case per row of the binding table, including
  an unverified record from the operator's address and a verified one
  from someone else, both bound `none`.
  An answer to a closed question and feedback outside `merge` steer.
- `test_approve_by_reply`: `approve` on an effect notice writes the
  approval with `via: telegram` and releases: a kernel push performs, a
  Telegram send writes `release.requested`. `Approve.` and `approve it`
  bind as `steer`.
- `test_steer_mid_turn`: a steer bound while a turn runs is in the next
  turn's prompt, not the running one's; an interrupted turn does not
  spend it; a finished turn does.
- `test_start_from_chat`: a plain message in a listed chat starts a task
  in that project; in an unlisted chat it binds `none`.
- `test_owns`: the operator chat, a chat on this machine, a chat on
  another machine.

`tests/test_port.py` (new):
- `test_release_requested_then_performed`: approve, kernel release writes
  `release.requested` and no intent; the bridge's outbox yields it; its
  `perform` runs once; intent and outcome carry the approval.
- `test_release_requested_alone_sends_nothing`: a `release.requested` row
  for an effect whose approval is used, or whose task is stopped, yields a
  refused outcome and no `perform` call.
- `test_bridge_crash_between_intent_and_outcome`: the fake bridge dies
  after its intent; on start, `serve` reconciles through `lookup`; no
  second `perform` while the first's lock is held.
- `test_two_identical_sends`: two equal requests from two signal files are
  two effects with keys ending in different effect ids; the same file
  collected twice is one effect.
- `test_notice_crash_before_sent`: a notice sent but not marked is yielded
  again with the same `notice_id`.
- `test_declared_class_is_the_kernels`: a bridge's performers take class
  and refusal from `DECLARED`.

## Files

| File | Change | Also changed by |
| --- | --- | --- |
| `core/serve.py` | new: the resident kernel, recover, schedule | |
| `core/intake.py` | new: `Inbound`, `Intake`, `bind` | |
| `core/notices.py` | new: `owe`, notice text | |
| `core/port.py` | new: `Bridge`, `ChannelLimits`, `DECLARED`, `Outbox`, `serve` | |
| `core/broker.py` | key with effect id, `request_id`, `Declared`, `release.requested`, `dangling` | 1.4d |
| `core/router.py` | `step`, the turn slot, services per stretch | 1.4b, 1.4d |
| `core/session.py` | `request_id`, steering in `next_prompt` | 1.4d |
| `core/machine.py` | `f.steering` | 1.4b |
| `core/tasks.py` | `stop` takes `via` and `role_played` | 1.4d, 4.1 |
| `core/settings.py` | the fields above | 1.4b, 1.5 |
| `core/workspace.py` | `Spec.chats`, `Spec.machine` | 1.4b |
| `core/schema.sql` | indexes, trigger | 1.4b, 1.4d, 1.5, 4.1 |
| `core/__main__.py` | `serve`, `service install`, `service remove`; `release` of a declared type | 1.4b, 1.4d |
| `core/README.md` | the resident kernel, the port | 1.4b, 1.4d |
| `projects/valor.toml`, `projects/README.md` | `chats`, `machine` | |
| `docs/tech-stack.md` | kernel process row and section | |
| `docs/architecture.md` | the supervisor and steering, as built | 4.2 |
| `docs/machine.md` | the kernel's plist and log | |
| `docs/bridges/telegram.md` | its port section points to this one's names | 2.2 |
| `tests/test_serve.py`, `tests/test_intake.py`, `tests/test_port.py` | new | |
| `tests/test_kernel.py`, `tests/performers.py` | the key; fake bridge performers | 1.4d |

The schema and `core/__main__.py` are touched by four other tasks; 2.1
merges after 1.4d and rebases on whatever has merged by then.

## Rollout

1. Merge. The lead runs `python -m core migrate` on the machine cluster;
   the indexes are partial on new row types, so existing rows are
   untouched.
2. Set `operator_telegram_id`, `operator_email`, and `operator_chat` in
   the service's environment, and `chats` and `machine` in each project
   spec.
3. `python -m core service install` writes
   `~/Library/LaunchAgents/com.valor.kernel.plist`: `ProgramArguments`
   the repo's `.venv/bin/python -m core serve`, `WorkingDirectory` the
   repo, `KeepAlive` true, `RunAtLoad` true, `ProcessType` `Interactive`,
   standard out and error to `<log_dir>/kernel.log`, the environment from
   step 2 and no secret. It then runs `launchctl bootstrap gui/<uid>` on
   it. The gateway reads Claude's login from the login Keychain, which a
   LaunchAgent in the GUI domain reaches. `python -m core service remove`
   runs `launchctl bootout` and deletes the plist.
4. In the test window, with `main`'s bridge and worker off on the build
   Mac, start a task from the command line; it advances with no `run`.
5. Mid-turn, kill the kernel by the pid `launchctl print
   gui/<uid>/com.valor.kernel` names; launchd restarts it; `core status`
   shows the turn interrupted and resumed, and `tasks.audit` is empty.

## Decided by default

- The bridges are processes of their own, so a platform library stays
  out of the kernel's memory and a bridge's connection has one owner.
- Binding runs in the kernel, so `receive` is one insert.
- One notification channel, `valor_events`, from a trigger.
- A repeated request matches by `request_id` (turn and signal file).
- Bridges connect as `valor_kernel`.
- An interrupted turn's open calls are charged at worst case, marked
  estimated.
- The turn slot is a session advisory lock; ready order is each task's
  latest row id.
- `approve` and `stop` match only as the whole trimmed, casefolded text.
- A plain message starts a task only in a chat a project spec lists.
- Services stay up for a working stretch; `caffeinate -i` for a turn.
- Telegram is the operator channel; email notices are left out.
- `serve_tick_s` is 60 seconds, a wake interval, not a limit.

## Questions for Tom

1. **Who is the operator.** Which Telegram account and which email address
   the kernel treats as Tom, and which chat receives notices. Assumed: the
   Telegram user id and address `main`'s bridge configuration names for
   Tom, and the direct chat between Tom and Valor's account as the
   operator chat.
2. **Where a plain message from Tom starts work.** Assumed: in the project
   whose spec lists that chat, with the direct chat listed by the `valor`
   project; a message in a chat no spec lists is recorded and starts
   nothing.
