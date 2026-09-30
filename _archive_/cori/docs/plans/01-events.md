# 01. Events

| | |
|---|---|
| Slug | `events` |
| Milestone | M0 |
| Status | built |
| Seams version | 3 (2026-09-20) |
| Owns | `schemas/ids.py`, `schemas/events.py`, `kernel/events.py`, the `events` table's indexes and the `reject_mutation()` message (one migration), single-flight locks |
| Depends on | seams |
| Written | 2026-09-19, against commit 1123f53; revised against 4cdd45c (seams v2); checked against seams Round two (2026-09-20, 5ebeff1) with the critique folded in; checked against seams v3 (`docs/reviews/2026-09-20-budget-ruling.md`), which retired the `standing:` lock namespace and changed nothing else here |
| Built | 2026-09-21, merge 3da1bf2 |

## Purpose

The event store is the durable half of "stateless compute over a stateful store." Every other kernel component appends to it and folds from it, so the M0 proof that "a stop leaves durable state intact" (tech stack §10) is a statement about this table: what is committed here survives, and nothing else is promised to. The two first objectives cannot run without it because the tree, the supervisor's turns, the cards, and the verdicts are all rows here, and the supervisor's single-flight lock is what keeps two triggers from rendering the same objective at once. This plan gives the rest of M0 five functions (`append`, `read`, `read_for`, `single_flight`, `upcast`), the `Event` model with its `EventType` names, the id aliases, and two indexes.

## What the documents say

- Tech stack §3, persistence rules, LOCKED: "Append-only by grant, not by convention. Event tables: the kernel role has INSERT and SELECT and nothing else. Migrations run under a separate role. A trigger rejecting UPDATE and DELETE is belt to the grant's suspenders."
- Tech stack §3, LOCKED: "Budget conservation in a transaction: `pg_advisory_xact_lock` keyed on the parent node id, check remaining, insert allocation event, commit." The advisory lock replaced `SELECT ... FOR UPDATE` because a row lock needs the UPDATE privilege (spike 01, folded in).
- Tech stack §3, LOCKED: "Row-level security on every space-partitioned table, keyed to a read token the kernel mints per render." Migration 0001 built it on `events`.
- Tech stack §3, LOCKED: "Driver: psycopg 3 async with hand-written SQL in the kernel, auditable line by line. Alembic for migrations using raw `op.execute`." (The summary table marks the driver PROVISIONAL; the sentence is what this plan builds to. See Finding 2.)
- Tech stack §3, LOCKED: "Vanilla SQL only, so the host stays swappable."
- Tech stack §10, "cheap now, expensive later, so done at M0", PROVISIONAL with scope COMMITTED: "Event versioning. Every event has `type` and `schema_version`; old events are never rewritten; readers upcast." and "Single-flight per thread and per objective via Postgres advisory locks, so two triggers cannot run concurrent supervisor turns on the same state."
- Tech stack §1, LOCKED: "Hypothesis is load-bearing. The kernel's invariants are properties, not examples."
- Architecture, five decisions, decision 1, KEEP: "The supervisor is a control loop, not a session. It is stateless compute over a stateful store. Its context is rendered each turn from the store and never accumulated."
- Architecture §1, REVISED: "Durable means events in the store, artifacts on a retained sandbox disk, and effects the broker confirmed." and, from spike 02, "a loop is only as lossless as its projection."
- Architecture §6, defenses, REVISED and ADDED: "Append-only event log; the belief table is a materialized view. Poisoning is a diff you can find."
- Architecture §9, ending a space, COMMITTED: the migrator removes rows and "appends a `space.destroyed` event with the counts"; tech stack §3 says that one migration disables the trigger inside its own transaction.
- Seams §0: ids are UUIDv7 strings from `uuid.uuid7()` minted by the kernel, with `new_id()` in `schemas/ids.py`; `EventId = int`; `schema_version` starts at 1 on every row; every model inherits `Strict` and is frozen; timestamps are timezone-aware.
- Seams §0, advisory locks (v2): "`pg_advisory_xact_lock(hashtextextended(key, 0))`, 64-bit, keyed by a namespaced string," with the namespaces `thread:`, `objective:`, `brief:`, `approval:`, `effect:` (v3; `standing:` was retired with the standing budget on 2026-09-20), and "lock order is thread before objective; nothing holding an objective lock takes a thread lock."
- Seams §1.8: the `Event` model's six fields, mirroring the `events` table.
- Seams §3.1 (v2): the five signatures of `kernel/events.py`; `single_flight` "refuses an autocommit connection"; `read` and `read_for` "always upcast."
- Seams §4 (v2): the thirty-six event types, `schema_version` 1, "every payload carries the ids named; nothing else is promised," and "the `EventType` Literal in `schemas/events.py` is exactly this table; a test asserts the two match."
- Seams, changes in version 2, ruling 11: `hashtextextended` everywhere. Seams Round two (2026-09-20): "`tests/test_grants.py` is retired by the tree plan, the first plan in the build order that adds a table; the spaces plan's `tests/test_space_partition.py` is the replacement and lands one step later." Round two also rules that `brief.issued`, `brief.failed`, `brief.stopped`, `brief.stop_confirmed`, `question.raised`, `question.answered`, and `snapshot.taken` carry `objective_id` at the top level, "so `read_for(key="objective_id")` reaches every event of a node."
- Plan findings record (`docs/reviews/2026-09-19-plan-findings.md`), finding 4: `reject_mutation()` names `events` in its message and is reused by every insert-only table; disposition "events plan uses `TG_TABLE_NAME`."
- Seams §6: `events` exists; "the plan adds `events_type_id_idx (type, id)` and `events_payload_gin (payload jsonb_path_ops)`."
- Seams §7: `schemas/ids.py`, `schemas/events.py`, `kernel/events.py` are this plan's; migration files are named by slug and numbered at reconcile.
- Spike 01, holds: advisory lock keyed on the parent id, 0 over-allocations in 960 racing delegations, p95 15 ms contended; "remaining budget derived by SUM over the ledger is fine at this scale."
- Spike 02, holds: 146 of 146 SIGKILLed runs converged when the loop took an advisory lock per turn and folded from seq 0; resume 35 ms median; the projection, never a counter, decides what is done.

## What exists

- `migrations/versions/0001_roles_and_events.py`: the three roles, the `events` table (`id bigint identity`, `space_id`, `type`, `schema_version default 1`, `occurred_at default now()`, `payload jsonb`), the index `events_space_id_idx (space_id, id)`, the grant (`kernel_rw` INSERT and SELECT), the `reject_mutation()` function and its triggers on UPDATE, DELETE, and TRUNCATE, `read_tokens`, `cori_current_space()`, and the two RLS policies. This plan edits none of it in place; its one migration adds two indexes on top and replaces the body of `reject_mutation()` so the message names the table it fires on.
- `tests/test_append_only.py`, `tests/test_rls.py`: the grant, trigger, and RLS proofs. They stay as they are and keep passing; the new tests sit beside them. `tests/test_grants.py` also exists and is retired by the tree plan, the first plan that adds a table, with the spaces plan's `tests/test_space_partition.py` as the replacement one step later (seams Round two); this plan neither extends nor depends on it, and it stays green through this plan because no grant changes here.
- `tests/conftest.py`: `kernel`, `context`, `migrator` connections and a fresh `space` id per test. This plan uses them and adds none.
- `schemas/space.py`: `Strict`, which `Event` inherits.
- `kernel/__init__.py`: the package docstring. `kernel/events.py` is new.
- Spike 01 `kernel.py`: the pattern `SELECT pg_advisory_xact_lock(hashtext(%s))` inside `conn.transaction()` and the Hypothesis stateful machine in `test_conservation.py` with a private event loop and a pure Python model beside the database. The pattern is lifted; the spike code stays in `spikes/`.
- Spike 02 `loop.py`: `turn()` takes the objective's advisory lock, folds every event from zero, appends, commits. The pattern (lock, fold, act from the fold) is what `single_flight` and `read` are for; the code stays in `spikes/`.

## Seams

**Consumed**

- §0 conventions: UUIDv7 ids, `EventId = int`, `Strict`, frozen records, `schema_version` from 1, timezone-aware datetimes.
- §1.1 `Strict` from `schemas/space.py`.
- §6 the `events` table as migration 0001 created it, the `kernel_rw` and `context_ro` roles, RLS through `cori_current_space()`.

**Provided**

- §0 `schemas/ids.py`: `SpaceId`, `ObjectiveId`, `BriefId`, `ConversationId`, `TurnId`, `CardId`, `ApprovalId`, `EffectId`, `QuestionId`, `SnapshotId`, `EpisodeId`, `EventId`, and `new_id()`.
- §1.8 `schemas/events.py`: `EventType`, `Event`, `CURRENT_VERSION`.
- §3.1 `kernel/events.py`: `append`, `read`, `read_for`, `single_flight`, `upcast`.
- §4 the `EventType` Literal is the thirty-six names of the table, no more and no fewer.
- §6 `events` indexes `events_type_id_idx (type, id)` and `events_payload_gin (payload jsonb_path_ops)`, and the table-naming `reject_mutation()` every insert-only table shares, in `migrations/versions/0002_events.py` (seams §7: events is first in the build order, so 0002 after 0001).

Nothing this plan exposes is absent from the seams document.

## Design

The index carries no spike requirement for this slug. Three modules, one migration, no new tables.

### `schemas/ids.py`

```python
SpaceId = str; ObjectiveId = str; BriefId = str; ConversationId = str; TurnId = str
CardId = str; ApprovalId = str; EffectId = str; QuestionId = str; SnapshotId = str
EpisodeId = str
EventId = int

def new_id() -> str:
    return str(uuid.uuid7())
```

Type aliases, not NewTypes, because no linter or type gate runs in CI (tech stack §1) and a NewType buys nothing a reader can see. `uuid.uuid7()` exists on the 3.14.6 in `uv.lock` (checked: it returns a version 7 id).

Decided here: `new_id()` lives in `schemas/ids.py` rather than in `kernel/`, so every kernel module mints an id the same way and a test can mint one without a database. Seams §0's rule that a worker or adapter never mints an id is enforced by the seams themselves (a `DelegateRequest`, `ToolLogRecord`, or `Reply` carries no id the kernel keeps), not by hiding the function.

### `schemas/events.py`

```python
EventType = Literal[
    # tree
    "objective.opened", "objective.contract_revised", "objective.approved", "objective.state_changed",
    "brief.issued", "brief.stopped", "brief.stop_confirmed", "brief.failed", "report.landed",
    "assumption.challenged", "budget.overrun",
    # spaces
    "inbound.routed", "inbound.unassigned", "space.destroyed",
    # worker
    "question.raised", "snapshot.taken",
    # supervisor
    "turn.started", "turn.rendered", "turn.completed", "message.received", "message.sent",
    "correction.recorded", "question.answered", "scribe.fired",
    # surface
    "session.opened", "conversation.opened", "space.switched", "card.issued", "card.expired",
    "approval.minted", "approval.consumed",
    # memory
    "episode.written", "belief.proposed",
    # verifier
    "verification.sampled", "checks.recorded", "verdict.recorded",
]

EVENT_TYPES: frozenset[str]              # get_args(EventType)
CURRENT_VERSION: dict[EventType, int]    # every type at 1 at M0

class Event(Strict, frozen=True):
    id: int
    space_id: SpaceId
    type: EventType
    schema_version: int = Field(ge=1)
    occurred_at: datetime
    payload: dict[str, Any]
```

Payloads stay `dict[str, Any]`. Seams §4 promises the ids each payload carries and nothing else, so the emitting plan validates its own model and hands `append` the result of `model_dump(mode="json")`. A per-type payload model arrives with the first type that reaches `schema_version` 2, because that is the first time a reader has two shapes to tell apart.

Decided here: a name is never removed from `EventType`. A retired type keeps its line with a comment, because a row of that type still exists and `read` must still parse it (tech stack §10: old events are never rewritten).

Seams §4 (v2) requires a test that the Literal and the table match. `tests/test_events_schema.py` parses the §4 table of `docs/plans/00-seams.md` and asserts set equality with `EVENT_TYPES`. The Literal is a copy of a contract, and a copy that drifts is found by a test rather than at the first refused `append`. When the seams change §4 again, this test fails until the Literal follows, which is the intended order. The grouping comments follow §4's emitter column; `question.raised` and `snapshot.taken` are the worker's (v2, ruling 7).

### `kernel/events.py`

```python
class UnknownEventType(ValueError): ...      # type not in EventType (append or read)
class StaleSchemaVersion(ValueError): ...    # append with a version other than CURRENT_VERSION[type]
class UnknownSchemaVersion(ValueError): ...  # read of a row with no upcast path to the current version
class NoTransaction(RuntimeError): ...       # single_flight on a connection with no open transaction

async def append(conn, *, space_id, type, payload, schema_version=1) -> int
async def read(conn, *, space_id, after=0, types=None, limit=1000) -> list[Event]
async def read_for(conn, *, space_id, key, value) -> list[Event]
@asynccontextmanager
async def single_flight(conn, key) -> AsyncIterator[None]
def upcast(event) -> Event
```

**`append`.** One `INSERT ... RETURNING id`, payload wrapped in `psycopg.types.json.Jsonb`. Refuses a `type` outside `EVENT_TYPES` with `UnknownEventType` and a `schema_version` other than `CURRENT_VERSION[type]` with `StaleSchemaVersion`, both before touching the connection. Never opens or commits a transaction; the caller's transaction is the unit of durability, so an emitter that writes two events and a ledger row in one turn commits them together or loses them together (spike 02). Returns the row's id.

Decided here: `append` never passes `occurred_at`; the column takes `now()`, which in Postgres is the transaction's start time, so every event committed in one transaction carries one timestamp and the order between them is `id`. Readers order by `id` and never by `occurred_at`. This is the spike 02 rule (fold by sequence) written into the seam's one ordering column.

Decided here: `append` takes a `dict` and not a model. It is the one place every payload passes, and the JSON encoder should see only what `model_dump(mode="json")` produced: strings for datetimes, the masked form for a `SecretStr`, nothing that needs a custom encoder.

**`read`.** `SELECT id, space_id, type, schema_version, occurred_at, payload FROM events WHERE space_id = %s AND id > %s [AND type = ANY(%s)] ORDER BY id LIMIT %s`. Every row is checked `row["type"] in EVENT_TYPES` first, and a miss raises `UnknownEventType` naming the row id and its type, the same order `append` uses; then `Event.model_validate`, then `upcast`, so a caller always sees the current shape. The check comes before validation because `Event.type` is the Literal and pydantic's `ValidationError` would otherwise fire first and hide the row id. `after` is exclusive; a caller reads the next page with `after=events[-1].id`.

Decided here: `read` raises on an unparseable row rather than skipping it. A reader that drops what it cannot parse is the projection bug spike 02 caught on its first run, in a quieter form.

Decided here: no cursor object. The page boundary is an event id, which is the only thing a caller needs to resume, and it is what the supervisor's trigger cursor will be.

`read` works under either role. Under `kernel_rw` the policy `events_kernel` passes every row of the named space; under `context_ro` with a live token the policy returns the token's space and an empty list for any other, which is the behaviour `tests/test_rls.py` already proves.

**`read_for`.** `WHERE space_id = %s AND payload @> jsonb_build_object(%s::text, %s::text) ORDER BY id`. Containment rather than `payload->>key = value` so the GIN index serves it. `value` is a string, which is what every id in seams §4 is. The result passes through the same check, validate, and upcast path as `read`. Containment matches top-level keys only; seams Round two put `objective_id` at the top level of every brief-scoped payload, so `read_for(key="objective_id")` reaches every event of a node, and a caller that needs a nested field reads the type and filters in Python.

**`single_flight`.** Executes `SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))` with the key and yields (seams §0 and §3.1, v2). The 64-bit hash means two of the namespaced keys share a lock number only by a coincidence of one in 2^64 per pair, and even then the cost is a needless wait, never a lost exclusion. The lock releases when the caller's transaction ends, which is the point of a transaction-scoped lock: a process killed mid-turn releases it with its connection (spike 02) and no unlock code has to run. Blocks rather than tries, because the second trigger's turn should run after the first and re-render from the store, never be dropped; whether that late turn still has work to do is decided from the fold, which is the supervisor's business (seams §3.3).

Seams §3.1 (v2) says `single_flight` refuses an autocommit connection; the property that matters is an open transaction, and `conn.autocommit` is a setting that stays true inside `async with conn.transaction()` on an autocommit connection, where psycopg has issued `BEGIN` and the lock is held. So the guard runs the lock statement and then raises `NoTransaction` when `conn.info.transaction_status != psycopg.pq.TransactionStatus.INTRANS`: a non-autocommit connection is `INTRANS` after the statement, an autocommit connection inside a transaction block is `INTRANS`, and a bare autocommit connection is back to `IDLE` and has already dropped the lock. The broker's per-step transactions on an autocommit connection (seams §3.10) pass; a caller that would hold nothing gets an error at the first call.

Seams §0 (v3) fixes the namespaces (`thread:`, `objective:`, `brief:`, `approval:`, `effect:`) and the order: thread before objective, and nothing holding an objective lock takes a thread lock. `single_flight` accepts any string key and enforces no namespace, because the callers are all kernel modules and the seam is the contract between them. The order rule is repeated in the module docstring so a reader of `kernel/events.py` sees it without opening the seams.

**`upcast`.** A registry `UPCASTERS: dict[tuple[EventType, int], Callable[[dict], dict]]` maps `(type, from_version)` to a function that returns the payload at `from_version + 1`. `upcast` walks from the row's version to `CURRENT_VERSION[type]` applying each step, and returns `event.model_copy(update={"schema_version": ..., "payload": ...})`. A version above the current one, or a missing step, raises `UnknownSchemaVersion`. At M0 the registry is empty and `upcast` is the identity; the test registers a synthetic two-step chain on a real type, proves the order and the result, and removes it.

Decided here: upcasters live in `kernel/events.py`'s registry and each is added by the plan that owns the type, when that type gains a version. The registry is the one place a reader has to look, and it stays inside the trust boundary with the readers.

Seams §3.1 (v2) says `read` and `read_for` always upcast. There is no raw read, because a caller that wants the stored shape is either a migration (which reads SQL) or a bug.

### Migration `migrations/versions/0002_events.py`

Raw `op.execute`, `revision = "0002"`, `down_revision = "0001"` (seams §7, v2 ruling 16: numbers follow the build order and events is first). Up:

```sql
CREATE INDEX events_type_id_idx ON events (type, id);
CREATE INDEX events_payload_gin ON events USING gin (payload jsonb_path_ops);
CREATE OR REPLACE FUNCTION reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % refused by trigger', TG_TABLE_NAME, TG_OP;
END
$$;
```

Down drops the two indexes and restores the 0001 body of the function. `CREATE OR REPLACE` keeps the function's oid, so the triggers on `events` (and on every table a later migration attaches it to) stay bound; the owner stays `migrator`. This is plan finding 4: the tree, spaces, gateway, worker, broker, surface, and verifier migrations all attach this one function, and a refused UPDATE on `budget_ledger` should say so rather than name `events`.

`events_type_id_idx` serves no query in this file: `read` is led by `space_id` on `events_space_id_idx` and `read_for` by the GIN. Its consumer is the supervisor's cross-space poll for trigger types (08-supervisor.md), which filters on `type` and `id > cursor` without a space, and the index is live from the day that poll runs.

Decided here: `jsonb_path_ops` rather than the default operator class, because `read_for` uses only `@>` and the path class is smaller and faster for it. Decided here: no `CONCURRENTLY`, because the table holds fifty-five rows and migrations run with the loop stopped; Alembic's per-migration transaction stays intact. Decided here: the function change rides in this plan's one migration rather than a second file, because seams §7 gives each plan one migration and the change is three lines.

### Control flow that matters

A supervisor turn, as the seams describe it and this plan supports it: open a transaction on a `kernel_rw` connection; `async with single_flight(conn, f"thread:{conversation_id}")`; `read(conn, space_id=..., after=0)` and fold; act; `append` each resulting event; commit. A SIGKILL anywhere before the commit leaves the store as it was and the lock free. That is the whole of what "lossless" means for durable state (architecture §1), and the chaos test in `99-integration.md` is the proof that runs it.

## Tasks

1. **`schemas/ids.py`.** The twelve aliases and `new_id()`. *Accept:* `uv run pytest tests/test_ids.py` green: `test_new_id_is_uuid7` (`uuid.UUID(new_id()).version == 7`) and `test_ids_sort_by_mint_order` (one thousand ids minted in a row compare ascending as strings).
2. **`schemas/events.py`.** `EventType`, `EVENT_TYPES`, `CURRENT_VERSION`, `Event`. *Accept:* `uv run pytest tests/test_events_schema.py` green: `test_event_types_match_seams_section_4` (parse the §4 table of `docs/plans/00-seams.md`, compare sets), `test_current_version_covers_every_type`, `test_event_is_frozen_and_strict` (assignment raises; an extra field is refused), `test_schema_version_below_one_is_refused`.
3. **`append`, `read`, `read_for`.** In `kernel/events.py`, with the exceptions, the type check before validation, and `upcast` in its identity form (empty `UPCASTERS`, `CURRENT_VERSION` walk of zero steps, a version above current raises `UnknownSchemaVersion`) so `read` and `read_for` can upcast from this task on; task 6 adds the registry walk. *Accept:* `uv run pytest tests/test_events.py` green: `test_append_returns_increasing_ids`, `test_read_after_is_exclusive`, `test_read_filters_by_types`, `test_read_for_matches_payload_key`, `test_append_refuses_unknown_type`, `test_append_refuses_stale_schema_version`, `test_read_raises_on_unknown_type_row` (a row inserted by raw SQL with type `x`; the exception is `UnknownEventType`, never `ValidationError`, and names the row id), `test_append_under_context_ro_is_refused` (`InsufficientPrivilege`), `test_context_ro_read_sees_token_space_only` (through `read`; the token row is inserted into `read_tokens` by SQL on the `kernel` connection and `cori.read_token` set on the `context` connection, as `tests/test_rls.py` does, since `spaces.mint_read_token` is built later).
4. **Properties of the store.** `tests/test_events_properties.py`: the stateful machine of the Properties section (P1, P2, P3). *Accept:* `uv run pytest tests/test_events_properties.py` green with the settings in the file; `uv run pytest tests/test_append_only.py tests/test_rls.py` still green.
5. **`single_flight`.** The context manager, the `NoTransaction` guard, the lock-order docstring. *Accept:* `uv run pytest tests/test_single_flight.py` green: `test_single_flight_serializes_per_key` (P4), `test_without_single_flight_counters_collide` (the control: sixteen connections, a 50 ms sleep between read and append, at least one duplicate counter), `test_single_flight_refuses_autocommit` (a bare autocommit connection raises `NoTransaction`), `test_single_flight_accepts_autocommit_inside_transaction_block` (the same connection inside `async with conn.transaction()` holds the lock, shown by a second connection blocking until the block exits), `test_lock_releases_when_connection_closes` (a second connection acquires the key within a second of the first being closed mid-transaction).
6. **`upcast`.** The registry and the multi-step walk over the identity form task 3 shipped. *Accept:* `uv run pytest tests/test_upcast.py` green: `test_upcast_is_identity_at_current_version`, `test_upcast_chains_registered_steps_in_order` (P5, with a synthetic chain on `message.sent` registered by a fixture and removed after), `test_upcast_refuses_missing_step`, `test_upcast_refuses_future_version`, `test_read_returns_upcast_events` (a raw row at version 1 while the fixture sets the current version to 3 comes back at 3).
7. **Migration `0002_events.py`.** The two indexes and the `reject_mutation()` body with `TG_TABLE_NAME`, `revision = "0002"`, `down_revision = "0001"`. *Accept:* `uv run alembic upgrade head` then `uv run alembic downgrade 0001` then `upgrade head` all succeed and `uv run alembic current` prints `0002`; `uv run pytest tests/test_events_migration.py` green: `test_events_indexes_exist` (`pg_indexes` lists `events_pkey`, `events_space_id_idx`, `events_type_id_idx`, `events_payload_gin`) and `test_reject_mutation_names_the_firing_table` (under `migrator`, a temporary table `tmp_append_only` gets the trigger, an UPDATE raises `RaiseException` whose message starts with `tmp_append_only is append-only`, and an UPDATE on `events` still raises one starting with `events is append-only`); `tests/test_append_only.py` unchanged and green.
8. **Format and contract.** *Accept:* `uv run black --check .` clean; `uv run lint-imports` reports the contract kept; `uv run pytest -q` green locally against the brew Postgres and in CI.

## Properties

All under `kernel/events.py`. Each is a Hypothesis test against the real database, in the manner of spike 01: a `RuleBasedStateMachine` with its own event loop, a pure Python model beside the store, `settings(max_examples=40, stateful_step_count=30, deadline=None)`, skipped when Postgres is unreachable (`requires_postgres`).

- **P1, the store is a log.** Operations: `append` with a space drawn from two fresh space ids, a type drawn from `EVENT_TYPES`, and a payload drawn from small JSON objects. Invariant: for each space, `read(space, after=0, limit=10_000)` equals the model's list for that space, in append order, with strictly increasing ids; ids are unique across both spaces; every returned event's `schema_version` equals `CURRENT_VERSION[type]`. `tests/test_events_properties.py::TestEventStore` (rule `append`, invariant `read_is_the_model`).
- **P2, paging is lossless.** Operation: `page(after, limit)` with `after` drawn from ids seen so far plus zero and `limit` from 1 to 50. Statement: concatenating pages from `after=0` until a short page equals the full read; a page never contains an id at or below its `after`; no page exceeds `limit`. Same machine, rule `page`.
- **P3, `read_for` is a filter.** Operation: `read_for(space, key, value)` with `key` and `value` drawn from the keys and string values the machine has appended plus one never-appended pair. Statement: the result equals `[e for e in read(space) if e.payload.get(key) == value]`. Same machine, rule `read_for`.
- **P4, single-flight serializes per key.** Operations: `n` in 2..16 connections, each assigned a key from one to three keys, each in its own transaction: `single_flight(key)`, count the events in a test space whose payload `key` matches, sleep 0 to 20 ms, `append` with `{"key": key, "n": count}`, commit. Statement: for every key, the appended `n` values are exactly `range(count_for_key)`, no gaps and no duplicates; without the lock the same schedule with a fixed 50 ms sleep produces at least one duplicate. `tests/test_single_flight.py::test_single_flight_serializes_per_key` (`@given(n, key assignment)`, `max_examples=20`) and the example control beside it.
- **P5, upcast composes.** Operations: a chain of 1 to 4 synthetic upcasters registered on one real type, each appending its step number to a list in the payload; an event at any version from 1 to the chain's length. Statement: `upcast` returns `schema_version == CURRENT_VERSION` and the payload list equals the step numbers from the event's version to the end, in order; `upcast(upcast(e)) == upcast(e)`; a version above current raises. `tests/test_upcast.py::test_upcast_chains_registered_steps_in_order` (`@given(chain length, start version)`; no database).

Append-only under the grant and the trigger is already a property of the database, proven by `tests/test_append_only.py`, and this plan leaves that test as the proof.

## Out of scope

- **Per-type payload models.** Seams §4 promises ids only. Arrives with the first `schema_version` 2.
- **A materialized fold or running totals.** Spike 01, surprise 4: SUM over the ledger is fine at this scale; a cache table arrives when a node has tens of thousands of events. Tech stack §3 adds that a materialized view is never granted to `context_ro`.
- **The Postgres outbox.** Tech stack §3 names it as the queue; no plan owns it and M0 is one process (tech stack §2). See Finding 1 for why it cannot be the `events` table under the `kernel_rw` grant and the assumption M0 proceeds under.
- **`UNASSIGNED_SPACE_ID`.** Seams §0 puts the constant in `schemas/space.py`, which the spaces plan owns.
- **Ending a space.** The `space.destroyed` event and the trigger-disabling migration are architecture §9 and the spaces plan's; the pattern already exists in `tests/test_append_only.py::test_owner_can_disable_trigger_to_destroy_a_space`.
- **The chaos test.** `tests/chaos/` is the integration plan's; this plan makes it possible and does not run it.
- **Retention or compaction of events.** No document asks for it; the `Retention` fields on the space manifest cover memory and artifacts.
- **Lock timeouts and deadlock detection beyond Postgres's own.** The namespaces of seams §0 with thread before objective need neither.
- **`tests/test_grants.py`.** Retired by the tree plan, the first to add a table, with the spaces plan's `tests/test_space_partition.py` as the replacement (seams Round two); this plan leaves it untouched.
- **TODOs from the refinement record.** None touches this component.

## Risks

- **The `EventType` Literal is a copy of seams §4.** Reconcile will change §4, and the copy is wrong until updated. Mitigated by `test_event_types_match_seams_section_4`, which makes the drift a red test rather than a refused write.
- **Fold cost grows with the log.** Spike 02 folded 27 events in under a millisecond; an objective with thousands will feel `read` from zero. Paging by `after` is in the seam; a snapshot is not, on purpose (spike 01, surprise 4). Measured on the real loop at M0 (tech stack §14, item 3).
- **Database tests skip without Postgres.** A local run can be green by skipping; CI runs them against the `pgvector/pgvector:pg18` service, which is where the acceptance checks count.
- **Rows outside the Literal already exist** (`tests/test_append_only.py` and `tests/test_rls.py` insert types `hello` and `x`). They live in throwaway space ids, so `read` never meets them; a full-table tool would. Kept as is, because those tests prove the grant and are cheaper to keep than to rewrite around a real type.

## Questions for the architect

None. The one item that came close is Finding 1, and the plan proceeds under the assumption stated there.

## Seam amendments

1. **§3.1, `single_flight`.** Change `pg_advisory_xact_lock(hashtext(key))` to `pg_advisory_xact_lock(hashtextextended(key, 0))`. Reason: `hashtext` returns an `int4`, so two of the keys `thread:*` and `objective:*` share a lock number with probability about one in four billion per pair; `hashtextextended` returns an `int8` and the single-argument form of `pg_advisory_xact_lock` takes a `bigint`, so the change is one token and removes the collision without a second argument or a key table. Spike 01 used `hashtext` because the spike had one key. Applied in seams v2 (§0, §3.1); the plan now builds against `hashtextextended(key, 0)`.

**Lead, reconcile (seams v2, 2026-09-19):** amendment 1 accepted; `hashtextextended(key, 0)` is now the rule in seams §0 and §3.1 for every advisory lock, including the broker's effect lock. Ruling from other plans that touches this file: `tests/test_grants.py` is retired by the spaces plan's catalog conformance test, so no task here extends it. Four event types were added in v2 (`brief.stop_confirmed`, `budget.overrun`, `snapshot.taken`, `session.opened`) and `question.raised` moved to emitter `worker`; the `EventType` Literal follows seams §4 as it now stands.

## Findings

Both carried into `docs/reviews/2026-09-19-plan-findings.md` as findings 1 and 2, each with disposition "edit" to tech stack §3.

1. **Tech stack §3, queue paragraph (line 91) against the grant rule (line 83) and spike 01, surprise 1.** The outbox is claimed with `FOR UPDATE SKIP LOCKED`, and a row lock needs the UPDATE privilege, which is exactly the incompatibility spike 01 found for `SELECT ... FOR UPDATE` on the parent node. So the outbox cannot be the `events` table, nor any table under the insert-only grant; it needs a kernel-owned mutable table, and the seams document assigns none. At M0 this changes nothing: one process (tech stack §2) means one consumer, so the supervisor can poll `read(after=cursor)` per space and `single_flight` serializes the turns. The plan proceeds on that assumption; the outbox table and its owner are for the lead when a second consumer process exists, and tech stack §3 should say the claim table is mutable and kernel-owned, never an event table.
2. **Tech stack §3 summary row (line 32) marks the driver and migrations PROVISIONAL while the §3 assessment marks "Postgres and the rules LOCKED."** The code from prereqs items 2 and 6 already commits to psycopg 3 and Alembic with raw `op.execute`, so the plan builds against the code and the assessment, which are the more recent. The summary row can be promoted.

### Critique, 2026-09-20

Three lenses (risk, scope, consistency) and a structural pass, against the seams as they stand with Round two. Line numbers are this file's before this section was added.

1. **The `single_flight` guard tests a setting, not a transaction.** Design, line 166 ("`NoTransaction` raised when `conn.autocommit` is true, before the lock statement") and task 5, line 205. `conn.autocommit` is a connection setting. psycopg 3 opens an explicit `BEGIN` for `async with conn.transaction()` on an autocommit connection, the flag stays `True`, `conn.info.transaction_status` is `INTRANS`, and the advisory lock is held (checked against the local Postgres 18). The guard as written refuses that caller, which is the shape seams §3.10 gives the broker: a connection with no open transaction that opens one per step. Fix: run the lock statement, then raise `NoTransaction` when `conn.info.transaction_status != psycopg.pq.TransactionStatus.INTRANS` (a non-autocommit connection is `INTRANS` after the statement; a bare autocommit connection is back to `IDLE` and has already dropped the lock), and add `test_single_flight_accepts_autocommit_inside_transaction_block` to task 5 beside the refusal test. Two lenses.
   *Folded (2026-09-20):* Design now runs the lock statement and raises `NoTransaction` on `conn.info.transaction_status != INTRANS`; the exception comment and task 5's acceptance carry both tests.
2. **`read` cannot raise `UnknownEventType` in the order written.** Design, line 154 ("Every row goes through `Event.model_validate` and then `upcast`" and "A row whose `type` is outside the Literal raises `UnknownEventType` naming the row id"). `Event.type` is the `EventType` Literal, so `model_validate` raises pydantic's `ValidationError` first, and task 3's `test_read_raises_on_unknown_type_row` is not met by the pipeline as described. Fix: `read` and `read_for` check `row["type"] in EVENT_TYPES` before validation and raise `UnknownEventType` with the row id, the same order `append` uses. Two lenses.
   *Folded (2026-09-20):* `read` and `read_for` check `row["type"] in EVENT_TYPES` before `model_validate`; task 3's test asserts `UnknownEventType`, never `ValidationError`.
3. **Task 3 depends on task 6.** Tasks, lines 203 and 206. `read` and `read_for` "always upcast" (line 174) and task 3's tests exercise them, while `upcast` and the `CURRENT_VERSION` walk are task 6. Fix: say in task 3 that it ships `upcast` in its identity form (empty `UPCASTERS`; a version above current raises `UnknownSchemaVersion`) and task 6 adds the registry walk and its tests, or move task 6 ahead of task 3.
   *Folded (2026-09-20):* task 3 ships `upcast` in its identity form; task 6 adds the walk. Order unchanged.
4. **The migration name is still a placeholder.** Seams, line 66; Design, lines 176 to 178; task 7, line 207 ("`NNNN_events.py`"). Seams §7 v2 fixed the number: 0002 events, after 0001. Fix: `migrations/versions/0002_events.py` with `revision = "0002"` and `down_revision = "0001"`, named in task 7's acceptance check.
   *Folded (2026-09-20):* the migration is `0002_events.py` in Seams, Design, and task 7, with `alembic current` printing `0002` as the check.
5. **A stale ruling on `tests/test_grants.py`, and a header that predates Round two.** Lines 35, 45, and 232 credit the retirement to the spaces plan under v2 ruling 12; seams Round two (2026-09-20, last bullet) moves it to the tree plan, with the spaces plan's `tests/test_space_partition.py` landing one step later. The header (line 8, "2 (2026-09-19)", and line 11) cites v2 alone. Nothing in Round two changes a seam this plan consumes or provides. Fix: reword the three sentences to the Round two ruling and add to the header that the plan was checked against Round two with no change. Three lenses.
   *Folded (2026-09-20):* the three sentences now credit the tree plan per Round two, and the header records the Round two check; no seam this plan consumes or provides changed.
6. **Seams §6 misquoted.** Line 37 quotes §6 as "the plan may add indexes on `(type, id)` and a GIN on `payload`"; §6 reads "the plan adds `events_type_id_idx (type, id)` and `events_payload_gin (payload jsonb_path_ops)`". The Design (lines 66 and 181 to 182) matches the seams; only the evidence line is soft. Fix: quote §6 as written.
   *Folded (2026-09-20):* the evidence line quotes §6 verbatim.
7. **The `context_ro` read test names a function built four steps later.** Task 3, line 203 ("`test_context_ro_read_sees_token_space_only` (through `read`, with a minted token)"). `spaces.mint_read_token` is the spaces plan's (seams §3.4), fourth in the build order. Fix: the test inserts the `read_tokens` row by SQL on the `kernel` connection and sets `cori.read_token` on the `context` connection, as `tests/test_rls.py` does.
   *Folded (2026-09-20):* task 3's acceptance says the token row is inserted by SQL; the test depends on nothing built later.
8. **Noted, seams win: `read_for` matches top-level keys, and two tree payloads carry `objective_id` elsewhere.** Design, line 162 (`payload @> jsonb_build_object(key, value)`), which is seams §3.1 ("payload[key] == value"). The tree plan's `project` (02-tree.md, line 148) reads a node's history with `read_for(key="objective_id")` and folds `brief.issued` and `brief.failed` from it, while seams §4 promises `brief.issued` as `brief (Brief minus gateway_token), gateway_token_sha256`, with `objective_id` nested inside `brief`, and `brief.failed` as `brief_id, error`. Containment stops at the top level, so those two types fall out of the fold unless the tree writes `objective_id` at the top of both payloads. This plan implements `read_for` as the seam says and changes nothing. For the lead: add `objective_id` to the top level of `brief.issued` and `brief.failed` in seams §4 (§4 promises the named ids and forbids nothing more, so the tree can write it today). The same reading applies to the surface plan's count of `approval.consumed` by `approval_id` (10-surface.md, line 171): `objective.approved` carries `approval_id` at the top level too, so the surface filters by type after the read.
   *Resolved (2026-09-20):* seams Round two puts `objective_id` at the top level of every brief-scoped payload, so `read_for(key="objective_id")` reaches every event of a node; `read_for` is unchanged and the Design now says containment matches top-level keys and why that is enough.
9. **Noted, seams win: no query in this plan uses `events_type_id_idx`.** Design, line 181. `read` filters on `space_id` first and `events_space_id_idx` serves it; `read_for` uses the GIN. The consumer is the supervisor's poll for trigger types (08-supervisor.md, line 146), which reads across spaces and so cannot go through `read`, whose `space_id` is required (seams §3.1). Seams §6 requires the index and the plan builds it; one sentence in the Design naming the consumer tells the builder the index is live.
   *Folded (2026-09-20):* the migration section names the supervisor's cross-space trigger poll as the consumer; the index stands as seams §6 requires.

Verdict: ready to build. No finding blocks a task; items 1 to 4 and 7 are wording and test additions the builder applies inside the task they name, 5 and 6 are citation fixes, and 8 and 9 are carried to the lead against seams §4 and the tree plan.
