# Data

Valor keeps its state in one Postgres database, used as a document store:
JSONB documents written once, an append-only events table, and no foreign
keys. Every budget movement, turn, effect, approval, question, answer,
piece of feedback, correction, and stop is a row in the events table, and
everything the kernel knows about a task is computed from those rows when it
is needed. Postgres is the one store; memory joins it last.

This doc owns the storage: tables, roles, the integrity mechanisms, how
state is read, how a turn's Brief is rendered from the store, test
databases, and where memory will live. `docs/architecture.md` owns what the
kernel does with the rows (budgets, the broker, approvals, stop, steering).

## What serves what

| Mechanism | Serves |
|---|---|
| Append-only events table, locked by grant and trigger | Constraint "Reliable stop, recovery, and correction": a ledger the system cannot edit records every effect |
| Write-once JSONB documents, no foreign keys | Tom's decision: Postgres only, document strategies |
| Partial unique indexes | Constraint "Bounded authority and spend" (one charge per call, one tap per effect) and lossless stop (one stop per task) |
| Per-task advisory locks | Constraint "Bounded authority and spend": two calls racing on one task never spend the same money |
| State as a fold over events | Lossless stop: nothing to reconcile between a stored status and the record |
| Brief and corrections rendered at turn time | Constraint: corrections reach every session and agent |
| Questions, answers, feedback, and approvals as rows with provenance | Mission item 6 and the Evidence item "Attention spent" |
| Kernel role with select and insert only | Least privilege [11]; the ledger constraint |
| Kernel database out of a turn's reach | The ledger constraint; the demonstration's first incident |
| A fresh test database per test session | `tests/README.md`: real Postgres, no mocks |

## The tables

`core/schema.sql` holds the schema: two tables, nine partial unique
indexes, one trigger function, and the grants. One `CHECK` constraint, the
verdict enum, is generated from `core/machine.py` (`VERDICTS`) and applied by
`db.migrate` (Verdicts refused at write, below).

### events

| Column | Type | Meaning |
|---|---|---|
| `id` | `bigint` identity | The order of the ledger. Every reader orders by it |
| `task_id` | `text` | The stream the row belongs to: a task's id, or a named stream (`corrections`, `guards`, `judgement`) |
| `type` | `text` | The event type, `noun.verb` (`turn.started`, `effect.held`) |
| `payload` | `jsonb` | The event's fields. Ids of what the row regards live here |
| `at` | `timestamptz` | `clock_timestamp()`, the wall time of the insert |

`at` is for people reading the ledger. Two rows written in one transaction
get distinct times, but order comes from `id` alone.

Two indexes serve reads: `(task_id, id)` for reading one stream in order,
and a GIN index over `payload` (`jsonb_path_ops`) for containment queries
(`payload @> '{...}'`).

A **stream** is the set of rows sharing a `task_id`. A task's stream holds
everything that happened to it. The `corrections` stream holds every
correction Tom has given, numbered from one, and applies to every task.
The `guards` stream holds the seeded guards, and the `judgement` stream
every calibration record.

### documents

| Column | Type | Meaning |
|---|---|---|
| `kind` | `text` | What the document is. The current kernel writes one kind, `task` |
| `id` | `text` | Its id within the kind |
| `body` | `jsonb` | The document |
| `created_at` | `timestamptz` | When it was written |

The primary key is `(kind, id)`. A document is what the kernel commits to
once and never changes. The `task` document is the Brief as the task
started: instruction, budget in micro-dollars, effect ceiling,
`governance_grant`, workspace, model, harness settings, and where a merge
goes: the target branch, origin's push URL as an absolute path, and the
workspace's head at start (`core/tasks.py`, `Brief`). `Brief.load` reads
only the fields the Brief has, so a stored document carrying an older
field (`mode`) still loads. The kernel role cannot update a document, so
anything that changes about a task after it starts is an event on its
stream, never an edit to its Brief. Tom's feedback that widened the
demonstration's scope from one profile item to seven was two
`feedback.given` rows; the Brief stayed as written
(rebuild-demonstration.md, Attention log).

A new kind of document is a new value of `kind` and needs no migration. The
objective tree's nodes are the next kind the design names
(`docs/architecture.md`); the current kernel holds a single task record per
task.

## Event types

Every type the current kernel writes, by the module that writes it. Each
payload carries the ids listed; a reader relies on nothing else.

| Writer | Type | Payload | Serves |
|---|---|---|---|
| `core/tasks.py` | `task.started` | `sdlc: 1`, instruction, `budget_usd_micros`, `max_effect_class`, `governance_grant`, `target_branch`, `origin_url`, `base_sha`, provenance. A calibration task's carries `calibration` (the site) instead of `sdlc`, with instruction, budget, `max_effect_class: read`, and provenance. A row with neither is a legacy task (State is a fold, below) | Bounded authority and spend |
| `core/verdicts.py` | `judge.decided` | verdict (`precise`, `thin`), `leg: judgement`, `judgement_id`, `answered`, `p_precise`, label, `abstained`, model, `usd_micros`, `guard_id` when thin. Rows from before the judge ran carry `leg: manual` and provenance | Mission items 3 and 6 |
| `core/judgement.py` | `judgement.answered` | `judgement_id`, site, `task_sha256`, `calibrated_sha256`, `inputs_sha256`, `ref`, `usd_micros`, attempts (per leg: model, endpoint host, outcome, `call_id`, charge, latency, and on failure a reason, status, and fixed sentence), answers (per question: label, probabilities, `p_proceed`, decision, the provider's pick, leg, model), action, `abstained`, leg, model | Mission item 6; the routing a judgement causes is legible |
| `core/judgement.py` | `judgement.failed` | as `judgement.answered` without answers, action, leg, or model; plus `on_failure` and `too_large` | As above |
| `core/judgement_sites.py` | `judgement.calibrated` | on the `judgement` stream: site, `run`, `task_sha256`, both pinned models, floors, `at`, the calibration task's id, `n`, label sources, per leg the Brier score with its n, confusion counts, abstain rate, accuracy, error rate, cost per call, `all_correct`; `entry_check`; every case | Calibration discipline (`docs/judgement-layer.md`) |
| `core/tasks.py` | `task.stopped` | reason, by | Lossless stop |
| `core/budget.py` | `gateway.reserved` | `call_id`, `turn_id`, model, `usd_micros` (worst case), estimated input, `max_tokens`. A judgement call's (written through `core/judgement.py`) has `turn_id` null and adds `route: judgement`, `judgement_id`, site, leg | Money conserved per call |
| `core/budget.py` | `gateway.refused` | the call's fields plus reason | Bounded spend; the refusal is itself recorded |
| `core/budget.py` | `gateway.charged` | `call_id`, `usd_micros` (actual), `turn_id`, model, `price_checked` (the day the price used was checked), provider status, cut, usage. A judgement call's adds the judgement fields above and `unused`, `unsent`, or `usage_missing` when they apply | Money conserved per call |
| `core/runs.py` | `turn.started` | `turn_id`, the state the turn runs in, harness, argv, the dispatched Brief whole, `brief_sha256`, correction numbers | Corrections reach every turn; legibility |
| `core/runs.py` | `turn.ended` | `turn_id`, outcome (`done`, `failed`, `stopped`), return code, parsed result (including the harness session id), stderr tail, metered spend | Lossless stop |
| `core/runs.py` | `turn.reaped` | `turn_id`, the processes stopped after the turn | Lossless stop |
| `core/session.py` | `turn.collected` | `turn_id`, the state it ran in and its verdict, what the turn left under `.valor/` (question, no-question statement, plan signal, delivery note, effect requests), the candidate (head sha and turn id) when the verdict is `candidate`, and `errors`: the signals that did not count and why | Legibility; the state machine |
| `core/session.py` | `question.asked` | `question_id`, `turn_id`, text, the state the answer returns to | Mission item 6 |
| `core/session.py` | `plan.written` | `turn_id`, path, commit, `sha256` of the file at that commit, stakes, `critique_rounds`, `review_rounds`, scope additions | Mission items 1 and 3 |
| `core/verdicts.py` | `critique.decided` | `plan_sha256`, verdict, findings, raised counts, leg, model, `usd_micros`, `guard_id` when it sends the plan back, provenance when manual | Mission item 1 |
| `core/verdicts.py` | `test.decided`, `review.decided`, `docs.decided` | the candidate, verdict, findings, leg, model, `usd_micros`, provenance when manual; test adds command, failures, untested behaviors, the breadth `guard_id`, and, when a breadth judgement was given, `breadth` (its `judgement_id`, actions, abstained, model, `usd_micros`, `guard_id`); review and docs add the governance answer and its instances (id, path, line, function context, summary, incident, mission item), and, when governance judgements were given, their ids, `abstain_instances`, and `unjudged_hunks`; docs adds its head and the paths it changed; the verdict that completes a join sending work to `patch` names `review.loop` | Mission item 1; Evidence "Independent checks" |
| `core/session.py` | `question.answered` | `question_id`, text, provenance | Mission item 6 |
| `core/session.py` | `feedback.given` | `feedback_id`, `on_delivery`, the candidate, text, provenance | Mission item 1; Evidence "Tom's feedback, both directions" |
| `core/verdicts.py` | `task.delivered` | the candidate, outcome (`passed`, `gaps`, `did_not_pass`, `governance_refused`), the join's row, summary (the candidate turn's `done.md`), the three verdicts, every finding, the gaps, the plan's scope additions, the instances awaiting Tom's tap. Written with the verdict that completes a join to `merge`. Legacy rows hold `turn_id` and summary only | Mission item 1 |
| `core/guards.py` | `guard.granted` | `guard_id`, name, incident, mission items, `granted_at`, `expires` (ninety days on), Tom's note, provenance; on a task also `instance_id`, path, and the candidate it was granted on. The seeded guards sit on the `guards` stream | The governing constraint |
| `core/broker.py` | `effect.held` | `effect_id`, action type, effect class, target, payload, `payload_sha256`, idempotency key, `adds_governance` (computed by the broker; for a `merge`, from the candidate's review and docs verdicts) | Nothing `act`-class leaves without Tom's tap |
| `core/broker.py` | `effect.refused` | as `effect.held`, plus reason | Bounded authority |
| `core/broker.py` | `approval.granted` | `approval_id`, `effect_id`, `payload_sha256`, note (Tom's literal message), provenance (`by`, `via`, `at`, `role_played`) | One tap, one effect |
| `core/budget.py` | `budget.raised` | `raise_id`, `usd_micros`, note, provenance | Only Tom raises a committed budget |
| `core/broker.py` | `effect.intent` | `effect_id`, idempotency key, `approval_id` | Recovery: a kill between intent and outcome leaves a findable row |
| `core/broker.py` | `effect.outcome` | `effect_id`, idempotency key, kind (`done`, `failed`), result, error | Legibility |
| `core/corrections.py` | `correction.recorded` | number, scope, source class, text, provenance | Corrections are first-class and carry provenance |

One table holds every execution record. Gateway calls, turns, and effects
are rows of the types above, with no separate log table for each. What a
turn did inside its harness, tool call by tool call, is the harness's
transcript (`docs/harnesses.md`); the ledger records the turn's Brief, its
model calls, its outcome, and every effect it requested. A gateway row holds
the provider's usage, never the request or response body.

Steering a running task is the architecture's mechanism. Its rows are
events on the task's stream, read at the next turn boundary. Answers and
feedback are the two forms the current kernel builds.

## Append-only, enforced twice

The events table cannot be edited by anything that runs as the kernel, and
cannot be edited by accident by anyone.

1. **Grants, the first lock.** `REVOKE ALL ... FROM PUBLIC`, then
   `GRANT SELECT, INSERT` on `events` and on `documents` to `valor_kernel`.
   The kernel role has no update, delete, or truncate privilege on either
   table. An attempt fails with `InsufficientPrivilege`.
2. **Triggers, the second lock.** `reject_mutation()` raises on any
   `UPDATE` or `DELETE` of an `events` row and on `TRUNCATE` of `events`,
   naming the table and the operation. Triggers fire for the owner too, so
   the role that applies migrations cannot rewrite the ledger by a stray
   statement. `documents` carries the grant and no trigger: the kernel
   cannot change a Brief, and the owner can.

`tests/test_kernel.py` proves both: the kernel role is refused by the grant
on `events` and on `documents`, and the owner is refused by the trigger.

The owner is Tom's role and can drop a trigger. The triggers guard against
mistakes, and the ledger's protection against the system itself rests on
the grant and on the kernel database staying out of a turn's reach (below).

The ledger cannot be emptied, which is why tests drop the whole database
rather than its rows (Test databases, below).

## Integrity without foreign keys

A row names what it belongs to by id inside its payload. Four mechanisms
keep the ledger consistent in place of a foreign-key lattice.

### Partial unique indexes

Each one makes a class of double-write impossible at the database, whatever
the kernel code does.

| Index | Unique on | Rows | What it prevents |
|---|---|---|---|
| `events_one_call_row` | `(type, payload->>'call_id')` | `gateway.reserved`, `gateway.charged` | A model call reserved or charged twice |
| `events_one_effect_row` | `(type, payload->>'effect_id')` | `effect.held`, `effect.intent`, `effect.outcome`, `effect.refused` | Two intents or two outcomes for one effect |
| `events_approval_used_once` | `payload->>'approval_id'` | `effect.intent` with an approval | One approval releasing two effects: one tap, one effect |
| `events_one_correction_number` | `payload->>'number'` | `correction.recorded` | Two corrections sharing a number |
| `events_one_stop` | `task_id` | `task.stopped` | A task stopped twice |
| `events_one_judge` | `task_id` | `judge.decided` | Two judge verdicts for one task |
| `events_one_judgement` | `payload->>'judgement_id'` | `judgement.answered`, `judgement.failed` | Two outcomes for one judgement |
| `events_one_turn_row` | `(type, payload->>'turn_id')` | `turn.started`, `turn.ended`, `turn.collected`, `turn.reaped` | One turn collected twice, so two candidates from one turn |
| `events_one_guard` | `payload->>'guard_id'` | `guard.granted` | A guard granted twice |
| `events_one_instance_grant` | `(task_id, payload->>'instance_id')` | `guard.granted` with an instance | One governance instance granted twice on a task |

### Verdicts refused at write

`events_verdict_in_enum_<digest>` is a `CHECK` constraint built from
`machine.VERDICTS` (`machine.constraint_sql`): every verdict row's verdict,
a `turn.collected` row's verdict for the state it names (required whenever
a state is present), plan counts and critique raises in 0 to 2. So the fold
never meets a verdict outside its enum, whatever code wrote the row. The
name carries a digest of the generated SQL: `db.migrate` leaves a current
one alone, and otherwise drops the older one and adds the current one in
one transaction. It is added `NOT VALID`, so rows already written are
never rechecked: a value may leave `VERDICTS` without history refusing the
change, and every new row is still checked.

These are what make every fold over the ledger total: a reader never meets
two charges for one call or two outcomes for one effect and has to choose.

### Advisory locks

`core/ledger.py`, `lock`, takes `pg_advisory_xact_lock` on a namespaced key
inside the caller's transaction: `task:<id>` for anything that reads a
task's state and then appends to it (reserving money, recording a stop,
answering, recording feedback, requesting an effect, collecting a turn),
and `corrections` for numbering the next correction. The lock is released
when the transaction ends, including when the process holding it is
killed, so no unlock code has to run. An advisory lock needs no table
privilege; a row lock would need `UPDATE`, which the kernel role does not
have.

Reserving money is the case that matters. Remaining budget is the Brief's
budget plus every `budget.raised`, minus every charge, minus every open
reservation, computed by one fold (`core/tasks.py`, `money`) that
`tasks.status` and `budget.reserve` both call, the latter under the task's
lock, so two calls racing on one task cannot both reserve the last of it.
`test_racing_reservations_never_exceed_the_budget` runs that race on real
Postgres.

### Transactions as the unit of durability

`ledger.append` never opens or commits a transaction. The caller's
transaction decides which rows land together: `tasks.start` writes the task
document and `task.started` together, and a turn's collected signals and
its question or delivery commit together. Connections are autocommit
(`core/db.py`), so a read holds no transaction open and every
`conn.transaction()` block is a real one.

### Intent before outcome

An effect's `effect.intent` row commits before its performer runs, and its
`effect.outcome` row after. A kill between the two leaves an intent with no
outcome. The process performing an effect holds a session advisory lock on
it from before the intent to the outcome, so the lock is free only when
that process died. `broker.reconcile`
then asks the target through the performer's `lookup`, which answers
present (the target branch holds the commit, at its tip or below it),
absent, or unknown (the target did not answer). Present is written `done`.
Absent is written `failed` only once the intent is older than
`reconcile_after_s` (at least twice `git_timeout_s`, the one deadline on a
perform's git calls; settings refuse less), since a
performer whose database connection dropped frees the lock while its push
may still run. Unknown writes nothing, and the effect stays in flight. Either outcome is marked `reconciled`. The router does this for a
task's merge on its next run; other dangling intents stay listed by
`tasks.audit` (`docs/architecture.md`, the broker).

## State is a fold over events

The kernel stores no status field. A task's state is computed by reading
its stream in `id` order and folding: `core/machine.py`, `fold`, gives the
state machine's state (one of eleven; `docs/sdlc-state-machine.md`), and
`core/tasks.py`, `status`, returns it with the plan, loop counts, the
current candidate and its checks, the governance instances, committed
and charged money, open reservations, remaining money, every turn and its
outcome, every effect and its state, the latest delivery, and the
attention log. The harness session a turn resumes is folded the same way,
from the last `turn.ended` result that carried a session id. The fold is
total: a row that is not a transition from the state the task is in is
ignored and listed, never an error, and a property test folds every prefix
of generated ledgers to exactly one state (`tests/test_machine.py`).

This is what makes stop lossless at the storage level. A stop at any
instant leaves rows that each landed whole or not at all, and no stored
status that could disagree with them. `tasks.audit` states what a stop
must leave true, and it is a check over the fold: every reservation
charged, every turn ended, no effect between intent and outcome, nothing
charged past the committed budget.

**Shape changes.** A row is never rewritten, so when an event's payload
gains a field, readers handle both shapes. A task whose `task.started` has
no `sdlc` predates the state machine: it folds read-only by the old
kernel's precedence (stopped; a delivery not reopened by feedback is
`merge`; an unanswered question is `waiting`; feedback after a delivery is
`patch`; any turn is `build`; else `judge`), and nothing but stop, budget
raise, approve, and release writes to it. A calibration task folds with
`calibration` set and nothing else, and every SDLC writer, stop, budget
raise, and turn refuses it. The instance so far is
provenance (`core/tasks.py`, `provenance`): a field a row never recorded
reads as null, never as a default. Answers and feedback recorded before
`role_played` existed read `role_played: null`, and approvals recorded
before approvals carried provenance hold only a top-level `by` and read
`via` and `role_played` as null, with `at` from the row's own column. The
rule is that the reader carries the old shape forward, never a migration
over the ledger. A table change is additive (a nullable column, a new
index) and rewrites no row; `tests/test_migrate_history.py` applies one to
a copy of the kernel database and checks every row's `xmin` and each
table's file node.

**Scale.** Folding a whole stream on every read assumes a task's stream
stays small. The demonstration's task, four turns and three deliveries,
sits in ledger rows 88 to 255 (rebuild-demonstration.md). This is a gap: the
assumption has no published source and has not been measured at a size
where it would fail. The answer, when one is needed, is a snapshot
document written at a known event id and folded forward from there, not a
mutable status column.

## Rendering at turn time

The Brief a turn receives is rendered from the store as the turn starts,
never carried over from an earlier turn. `core/tasks.py`, `dispatch`,
reads the task document and every correction in force from the
`corrections` stream and renders one text: the task's commitments, the
corrections in the order Tom gave them with their provenance, and the
`.valor/` protocol a turn uses to reach Tom. `core/runs.py` records that
text whole in `turn.started`, with its SHA-256 and the correction numbers
it carried, under the task's lock, before the harness starts.

Serves the constraint that corrections reach every session and agent. A
correction recorded now reaches the next turn of every task, including
tasks that started before it, because no turn reads a copy.

Evidence: every `turn.started` row of the demonstration task carries
`"corrections": [1]` and one `brief_sha256`, and correction 1 appears in
the recorded Brief and in the system prompt argument that reached
`claude -p` (rebuild-demonstration.md, Correction 1 rendering, verified).
The same store state renders a byte-identical Brief, which is why the four
turns shared one digest and why the prompt cache can hold across turns.
Subagents a harness starts inside a turn were not checked; that gap is
`docs/architecture.md`'s.

Rendering is the one place stored data becomes prompt text. Memory, when
it exists, enters the same way: read through the port at render time,
recorded in the rendered text, never accumulated in a session.

## Attention as ledger rows

Mission item 6 counts attention on the same footing as money, and the
Evidence section counts decisions escalated to Tom per finished task. Both
are folds over rows the kernel already writes:

- `question.asked` and `question.answered`: what Valor asked and Tom's
  answer.
- `feedback.given`: Tom's feedback on a delivery, bound to the delivery it
  answers (`on_delivery`).
- `approval.granted`: Tom's tap on a held effect, with his literal message.
- `budget.raised`: Tom's raise of the task's committed budget.

Answers and feedback carry **provenance**: `by` (who wrote it), `via`
(the channel), `at`, and `role_played` (true when someone stood in for
Tom). The demonstration's second feedback round was written by the
orchestrator from Tom's recorded answers, and before `role_played` existed
the ledger could not say so (rebuild-demonstration.md, Kernel findings 5).
`tasks.status` returns the attention log as one list, each entry labelled
by kind with its provenance.

Approvals and raises carry the same provenance as an answer,
`role_played` included, because two of the demonstration's three pushes
(rows 204 and 253) were approved under Tom's standing permission for local
copies rather than by a tap each (rebuild-demonstration.md, Where Tom acted
as project manager); the replay driver approves its pushes with
`role_played: true`. `tasks.status` counts each kind apart in
`attention_counts` (total, role-played, unknown), so approvals never add to
the questions and feedback counted as interruptions. The `by` on rows
written before this shape is unreliable: every earlier approval says
`tom`, the replay driver's included.

The attention budget a task carries is a field of its Brief in the design
(`docs/architecture.md`, The attention log); the attention it spent is the
fold above.

## Why no relational lattice

Tom's decision is Postgres only, with document strategies instead of
relational modelling. The kernel's shape makes that the natural fit, for
four reasons that follow from the constraints:

1. **The kernel cannot update.** A lattice of rows that point at each other
   and change in place needs `UPDATE`, and an `UPDATE` grant on the ledger
   is the thing the constraint forbids. Facts that change are new events;
   facts that never change are documents.
2. **Integrity lives where the decision is made.** Whether an answer has an
   open question to answer, whether a release has a matching unused
   approval, whether a task can take feedback: the kernel checks each
   against the fold, under the task's lock, in the transaction that writes
   the row. A foreign key could check only that an id exists, which is the
   least of it. The checks a key would carry that do matter, uniqueness of
   calls, effects, approvals, corrections, and stops, are the partial
   unique indexes.
3. **New facts need no migration.** A new event type or document kind is
   a new string. Feedback, provenance, and `role_played` needed no schema
   change; the one index beyond the first schema is correction numbering,
   and the demonstration and thirteen replay runs ran on it
   (rebuild-baseline.md).
4. **Nothing cascades.** There is no delete to cascade and no row whose
   removal could orphan another.

This is a design assumption with evidence (the demonstration and the
baseline ran on it) and no published source; it is a gap in the
REFERENCES.md sense.

## Roles

| Role | Login | Privileges | Used by |
|---|---|---|---|
| `valor_kernel` | yes, with its password on the kernel databases | `SELECT, INSERT` on `events` and `documents`; nothing else | Every kernel process: the CLI, the gateway, the runner |
| Owner | Tom's macOS user by default (`VALOR_PG_OWNER`), with its password on the kernel databases | Owns the database and the schema | `python -m core migrate`, `backup`, and the test fixtures, nothing else |

`python -m core migrate` (`core/db.py`, `migrate`) connects as the owner,
creates `valor_kernel` if missing, creates the database if missing,
applies `core/schema.sql`, and records correction 1 (the governance
paragraph, from `CLAUDE.md`) if the ledger has no correction 1; it then
runs `secure-login` (below). Every other connection is `valor_kernel`
(`core/settings.py`, `dsn`). Least privilege [11]: the role that runs the
kernel holds exactly what appending and reading need. `LISTEN`,
`pg_notify`, and advisory locks need no table privilege, so the stop
channel and the per-task locks work under the same grant.

Connection settings come from one typed module, `core/settings.py`, each
with a default for this Mac and an environment override: host
(`VALOR_PGHOST`, default the `/tmp` socket), port (`VALOR_PGPORT`, 5432),
database (`VALOR_DB`, `valor_rebuild`), test database (`VALOR_TEST_DB`,
`valor_rebuild_test`), owner (`VALOR_PG_OWNER`), and the password file
(`VALOR_PG_PASSFILE`). Every connection string names the password file and
never holds a password.

## The kernel database is out of a turn's reach

The ledger is only uneditable if nothing a turn runs can connect as a role
that can write it. The control stance [4] assumes the model in a turn may
try.

**The incident.** Before the demonstration's first turn, the machine's
Postgres cluster trusted every loopback connection, so a turn could have
connected as `valor_kernel`, or as a superuser, and written ledger rows
(rebuild-demonstration.md, Kernel findings 1). The constraint at stake:
"A ledger the system cannot edit records every effect."

**The rule.** The kernel's cluster and any database a turn's work uses are
different clusters, and a turn cannot reach the kernel's.

- The kernel's database lives on the machine cluster (port 5432 and its
  socket in `/tmp`), and every role logging into a kernel database needs
  its password (`core/credentials.py`, `secure_login`: `scram-sha-256`
  rules for the socket, `127.0.0.1`, and `::1`, first in `pg_hba.conf`).
  The passwords live in a libpq password file only the kernel's user reads
  (`settings.pg_passfile`; [machine.md](machine.md), Keychain).
- A workspace that needs Postgres gets a cluster of its own on another
  port, with password authentication on every login, a superuser password
  generated and discarded once the app's role exists, and an app role with
  only what the app's tests need (`CREATEDB` for Django's test runner).
  The demonstration's ran on `127.0.0.1:5439`; the replays gave each
  concurrent run its own databases (rebuild-baseline.md, Infrastructure
  fixed during the series).
- The turn's sandbox denies port 5432 and the machine socket, the password
  file's directory, the machine cluster's data directory, and the backup
  disk; it allows the workspace cluster's port and denies the workspace
  cluster's data directory. `tests/test_demo_sandbox.py` probes it: the
  gateway reachable at any port, 5432 and its socket refused, the
  password file, `pg_hba.conf`, and the dumps unreadable and unwritable.
  `tests/test_credentials.py` shows a login without the password refused
  on a scratch cluster secured the same way.

How the sandbox is built, and which work runs under `sandbox-exec` and
which in an Apple container, is `docs/architecture.md`'s.

**What is met, and what still rests on the sandbox.** Met: no role logs
into a kernel database without the password, and a workspace turn can read
neither the password file, the cluster's data directory, nor the dumps,
because every workspace turn runs under a sandbox profile
(`harnesses/claude_code.py`, `workspace_turn`, refuses a task without
one). A bare `turn` runs under no profile and has no tools; it must not be
given tools without one. Still on
the sandbox: other databases on the cluster (`postgres`, `psyoptimal`)
trust local logins, so a turn that escaped its profile could log in as the
superuser to one of them and change a role's password or the server's
settings; and Tom's macOS user, outside any turn, can always edit the data
directory. If a deceptively aligned model [5, 6]
finds a side channel, the ledger records only what it chose to show; the
effect ceiling keeps the damage survivable while that is discovered.

## Test databases

`tests/conftest.py` runs every test session against `valor_rebuild_test`
(the `test_database` setting) on the same cluster, created fresh: `migrate` is called
with `fresh=True`, which drops the database as the owner and recreates it.
The ledger cannot be emptied, so dropping the database is the only reset,
and the owner is the only role that can do it. Tests connect as
`valor_kernel` like the kernel does, plus an owner connection for the
test that proves the trigger refuses the owner too. `db.migrate` touches no
role password, password file, or `pg_hba.conf`, so a test run leaves the
machine cluster's credentials as it found them; the credential tests run
`secure_login` only on scratch clusters.

The rule from `tests/README.md` applies: real Postgres, no mocks, and every
test marks its live spend (`@pytest.mark.spend(usd=...)`, declared in
`pyproject.toml`). The ledger tests spend nothing.

The test database shares the kernel's cluster, so it is out of a turn's
reach by the same sandbox rule. `tests/test_live_turn.py` and
`tests/test_live_session.py` run real `claude -p` turns through the
gateway, declare their spend, and run only with `VALOR_LIVE=1`, so a plain
test run spends nothing; `tests/test_gateway_meter.py` meters a successful
call in every run by replaying a recorded provider response.

## Memory, last

Memory is built last, on popoto [20] over Postgres. Popoto's Postgres
backend is tracked in its issue 631; until it ships, `memory/` holds its
README and the port `core/` reads memory through, and nothing in the
kernel depends on popoto.

Memory goes into the same Postgres as everything else. The design rules it
must keep:

- **Memory grants nothing.** Retrieved content can act as instructions to
  a model [7], so nothing read from memory changes a budget, a ceiling, or
  an approval. Memory's tables are its own, under a role with no privilege
  on `events` or `documents`.
- **Memory never writes the ledger.** The kernel reads memory through the
  port at render time (Rendering at turn time, above), and the rendered
  text is recorded in `turn.started` like the rest of the Brief.
- **Episodic memory ingests raw turns.** Popoto's measurement found raw
  turn ingestion beat LLM extraction on judged accuracy [20]; the turns
  memory ingests are the ones the harness transcripts already hold.

Tom's corrections are rows in the kernel's ledger, on the `corrections`
stream (`core/corrections.py`), and the exemplar ledger shares that store
as source class `exemplar`. `core/` owns both streams: they render into
every Brief, which is why they sit beside the effects rather than in
memory. Memory, when built, reads and curates them through the port; it
never writes the streams or owns them.

## Gaps

1. **Event-store scale.** No measured ceiling on stream size for a full
   fold, and no source for the assumption that folding suffices. Snapshot
   documents are the planned answer.
2. **Document store without a lattice.** Backed by the demonstration and
   the baseline, not by a published source.
3. **The rest of the cluster.** The kernel databases need a password from
   every role, but other databases on the machine cluster trust local
   logins, so a superuser login from an escaped turn is kept out by the
   sandbox alone.
4. **One backup disk.** The ledger is kept forever and dumped to one
   external disk, 30 dumps deep (`docs/machine.md`, Backups). There is no
   second copy elsewhere.
5. **Memory's schema.** Waits on popoto's Postgres backend [20].
