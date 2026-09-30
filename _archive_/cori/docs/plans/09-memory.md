# 09. Memory

| | |
|---|---|
| Slug | `memory` |
| Milestone | M0 |
| Status | built |
| Seams version | 3 (2026-09-20) |
| Owns | `kernel/memory.py`, `schemas/memory.py`, `schemas/belief.py`; the Redis models `Episode` and `Belief`; no Postgres table |
| Depends on | `events` (append, the event table), `spaces` (manifests, the set of space ids) |
| Written | 2026-09-19, against commit `1123f53`; revised the same day against seams v2 at `4cdd45c`; checked against seams v3 (`docs/reviews/2026-09-20-budget-ruling.md`), which names no class and no budget axis this plan uses, so nothing changed |
| Built | 2026-09-21, merge 7d3d648 |

## Purpose

M0 proves "one objective end to end at `propose` inside one client space's sandbox, in code and in a drafted message" (tech stack §10, COMMITTED on scope). Memory is the part of that proof where what happened in the space is kept as evidence: raw turns and reports go in as they are, the Scribe adds summaries with provenance, and an Executor's Brief carries the relevant hits for its space and nothing from any other. The two first objectives need this in three places: the `memory` block of the Executor's context slice, the Scribe's write path after every turn, and the record of belief proposals that the operator record will read at M1. Everything here is evidence with no authority (architecture, decision 5, KEEP), so the plan is a write path, a read path, and the rules that keep both inside one space.

## What the documents say

- Architecture, decision 5, KEEP: "Memory is evidence, not instruction. Three stores with three trust models."
- Architecture, decision 6, ADDED: "Every conversation, objective, sandbox, and retrieval is scoped to exactly one space."
- Architecture §1, REVISED: "When a thread exceeds its cap, the Scribe compresses the older half into episodic memory and the thread is rehydrated from summary plus recent turns." And: the render is deterministic, "same store state in, byte-identical context out."
- Architecture §3.1, KEEP: an Executor's `context_slice` is "node + compressed path to root + relevant memory hits for this space", and Planner, Executor, Verifier are "capped at PROJECT".
- Architecture §3.5, KEEP: the Scribe "writes episodic memory directly, as raw turns and summaries with provenance links and never as extracted facts. It only proposes to the operator record. A proposal inherits the space of the turn it came from and can never carry global scope; the kernel derives its source class ceiling from who authored the evidence it cites." Division of labor: "The kernel writes structured records deterministically ... The Scribe writes what needs language."
- Architecture §6, operator record, REVISED and ADDED: the `Belief` fields; source classes `direct | correction | decision | inferred`; "In v1 a belief carries its source class, its supporting events, and its status."
- Architecture §6, who assigns the source class, PATCHED: "A belief whose supporting events are not person-authored is `inferred` regardless of what the proposer wrote."
- Architecture §6, who sets the scope, PATCHED: "A belief inherits the space of the turn it came from ... No agent writes global scope."
- Architecture §6, defenses: "Append-only event log; the belief table is a materialized view. Poisoning is a diff you can find."
- Architecture §6, episodic memory, KEEP with the no-extraction rule ADDED: "Queryable by structure first and vector second. Retention by importance decay. Nothing here has authority." "Episodic memory ingests raw turns and reports. There is no LLM extraction step in front of it." Summaries "are additions with provenance links, never replacements for the turns they compress."
- Architecture §6, popoto, ADDED, backend COMMITTED: "Every model is partitioned by space ... every retrieval names one space. Global beliefs live in a reserved global partition ... There is no query that spans partitions." "Supersession is popoto's." "Confidence is observed, never authoritative, in v1."
- Architecture §9, PATCHED: the `unassigned` space, where Cori "may read headers and ask which space, and may do nothing else"; `retention` on the manifest.
- Tech stack §2, LOCKED: `schemas/` holds `Belief`; `workers/` and `adapters/` never import `kernel/`.
- Tech stack §3, LOCKED: hand-written SQL in the kernel; events are append-only under `kernel_rw`.
- Tech stack §3.1, COMMITTED: "`space` is a key field; decaying and ranked fields partition by it; every retrieval names exactly one space." "Confidence recorded, never read for authority." "Redis is the backend ... local, bound to localhost, and reachable only from the kernel process." The two stores "never need a joint transaction."
- Tech stack §8, PROVISIONAL: "Summaries are events with provenance links to what they compress, so a bad summary is a diff you can find." "Episodic retrieval is by structure first, vector second." Embeddings are Voyage-4 with `embedding_model` stored per row.
- Tech stack §10, PROVISIONAL, COMMITTED on scope: M0 ships the Scribe on the PydanticAI loop; M1 ships "operator record with goals and scope, the supervisor framing against it". Every event has `type` and `schema_version`.
- Tech stack §12, PROVISIONAL: "The audit log is Postgres, append-only, and the only record with evidentiary standing."
- Tech stack §1, LOCKED: "The kernel's invariants are properties, not examples."
- Tech stack, selection rule, LOCKED: "no vendor is integrated ahead of a need."
- Seams §0: UUIDv7 ids minted by the kernel; `Strict` models; `*_sha256` hashes; `UNASSIGNED_SPACE_ID`.
- Seams §1.13 and §1.14 (v2): `Belief`, `EpisodeWrite`, `MemoryHit`, `SliceQuery`, `BeliefProposal` as fields, including `regards`, `data_class`, `text_sha256`, and `max_data_class = "PROJECT"`.
- Seams §2.4 (v2): `KernelAPI.read_slice`, `write_episode`, `propose_belief` are the worker's door; the door checks generation, space, and capability, and "`read_slice` replaces `query.max_data_class` with the Brief's `max_data_class`", so memory checks none of them. `latest_summary` is absent from the door.
- Seams §3.8 (v2): `write` (event first, then the Redis row), `ingest`, `retrieve` ("structure first, then lexical rank; deterministic for identical state"), `latest_summary` ("the newest summary episode for a conversation or objective"), `propose` ("nothing reads it at M0"), `derive_ceiling`.
- Seams §4 (v2): memory emits `episode.written` with `episode_id, space, kind, provenance, data_class, regards, text, text_sha256`, and `belief.proposed` with `proposal, proposal_id, derived_ceiling`.
- Seams §6 (v2): "Redis: `Episode` model (kinds `turn`, `report`, `summary`, `decision`) and `Belief` model"; "`Belief` is defined and unread at M0."
- Prereqs item 3, done: `kernel/memory.py::Episode` with `space = KeyField()`, and `tests/test_memory.py` showing popoto refusing a ranked query with no space.

## What exists

- `kernel/memory.py`: the prereq `Episode` model (`space`, `episode_id = AutoKeyField()`, `text`, `salience`, `confidence`). This plan rewrites it; the partition proof carries over, the fields change (Findings 1 and 2).
- `tests/test_memory.py`: the two-space round trip and the refused unpartitioned ranked query. Kept and extended.
- `tests/conftest.py`: the `kernel` connection fixture (`kernel_rw`) and the per-test `space` fixture.
- `migrations/versions/0001_roles_and_events.py`: the `events` table this plan appends to through `kernel/events.py`.
- `schemas/space.py`: `Strict`, `DataClass`, `Space.retention`.
- Redis 8 under `brew services` on localhost; popoto 1.9.0 on Python 3.14 (prereqs items 3 and 5).
- No spike code is lifted. Spike 04 bears on the read path: a retrieval that changes order between two renders of the same state breaks the cache and the replay guarantee of tech stack §8.

## Seams

**Consumed**

- §0: `EpisodeId`, `SpaceId` from `schemas/ids.py`; `UNASSIGNED_SPACE_ID`; the hash and id conventions.
- §1.1: `Strict`, `DataClass`.
- §1.8: `Event`, for `ingest` and for the provenance check.
- §2.4: the door. `kernel/api.py` (worker plan) implements `read_slice`, `write_episode`, `propose_belief` by calling §3.8 after its own checks.
- §3.1: `kernel/events.append`.
- §3.3: `render_brief_slice` (supervisor plan) calls `retrieve` for the `memory` block.
- §3.4: `kernel/spaces.load_all`, to refuse a write to a space with no manifest.
- §4: the payload shapes of `message.received`, `message.sent`, `report.landed`, which `ingest` maps.

**Provided**

- §1.13 `schemas/belief.py::Belief` and §1.14 `schemas/memory.py` (`EpisodeKind`, `EpisodeWrite`, `MemoryHit`, `SliceQuery`, `BeliefProposal`) as written in v2.
- §3.8 `kernel/memory.py`: `write`, `ingest`, `retrieve`, `latest_summary`, `propose`, `derive_ceiling`.
- §4: the events `episode.written` and `belief.proposed`.
- §6: the Redis models `Episode` and `Belief`.

## Design

The index carries no line for `memory` under "Requirements carried from the spikes". Nothing from the spikes is owed here.

### Modules

- `schemas/memory.py`: the five models of seams §1.14 (v2), fields as listed there. A validator refuses an `EpisodeWrite` of kind `summary` or `decision` with empty provenance (seams §1.14 comment).
- `schemas/belief.py`: `Belief` exactly as seams §1.13.
- `kernel/memory.py`: sets `REDIS_URL` from `CORI_REDIS_URL` before importing popoto, binds `load_all` from `kernel.spaces` as a module name, defines the two popoto models, and holds the six functions below. Nothing under `workers/` or `adapters/` imports it; workers reach it through the door (tech stack §2, LOCKED).

Decided here: `REDIS_URL` is set with `os.environ.setdefault` at the top of `kernel/memory.py`, default `redis://localhost:6379/0`, because popoto connects at import time and reads only that variable. Tests bind db 1 (CLAUDE.md) in one place: the first lines of `tests/conftest.py`, before any import, set `REDIS_URL` to `CORI_REDIS_URL` or `redis://localhost:6379/1`. The per-file `setdefault` in `tests/test_memory.py` is removed, because whichever test module imports `kernel.memory` first binds the whole session, and `tests/test_context.py` collects before it.

Decided here: `load_all` is looked up as `kernel.memory.load_all` at call time, never captured at import, so a test can `monkeypatch.setattr(kernel.memory, "load_all", lambda: mapping)` with a mapping that covers the fixture's space ids. This is the tree plan's pattern for `check_may_open`; seams §3.8 gives `write` and `propose` no `spaces` argument, so the module name is the seam-neutral injection point.

### The Redis models

```python
class Episode(Model):
    space = KeyField()                                   # tech stack §3.1: the partition
    kind = KeyField()                                    # turn | report | summary | decision
    data_class = KeyField()                              # PROJECT | OPERATOR
    regards = KeyField(null=True)                        # conversation, objective, or brief id
    episode_id = UniqueKeyField()                        # UUIDv7, minted by write()
    text = StringField()
    text_sha256 = StringField()
    text_bm25 = BM25Field(source="text")                 # lexical rank, scoped per query by allowed_keys
    provenance = ListField()                             # event ids
    event_id = IntField()                                # the episode.written row; read by property 3 and task 3's test, by primary key
    written_at = SortedField(type=datetime, partition_by="space")   # the event's occurred_at

class Belief(Model):                                     # defined, unwritten, unread at M0 (seams §6)
    scope = KeyField()                                   # a space id or "global"
    id = UniqueKeyField()                                # seams §1.13 names it id
    statement, kind, domain, source_class, status, test = StringField() ...
    supersedes = StringField(null=True)
    supporting_events = ListField()
    last_confirmed_at = DatetimeField(null=True)
```

One model holds all four episode kinds (seams §6, v2). The reason, recorded when v1 named a separate `Summary` model: `EpisodeKind` already names `summary`, and one model means one retrieval path and one partition rule (Finding 4).

Decided here: `episode_id` is a `UniqueKeyField` set by `write()` from `schemas.ids.new_id()`, replacing the prereq `AutoKeyField`, because seams §0 makes every id a kernel-minted UUIDv7 (Finding 1). A v7 id sorts by time, so recency order is `episode_id` descending and needs no second column; `written_at` stays as the partitioned sorted field that keeps popoto refusing an unpartitioned ranked query (prereqs item 3).

Decided here: `salience` and `confidence` leave the `Episode` model. No document gives an episode either, `EpisodeWrite` carries neither, and "retention by importance decay" (architecture §6, KEEP) is built with retention at M1 (Out of scope). The `Belief` model carries exactly the fields of seams §1.13; popoto's `ValidityField` for supersession and `ConfidenceField` for observed confidence (architecture §6, popoto rules) arrive with the M1 write path, since a field with no writer is a hook.

Decided here: `kind`, `data_class`, and `regards` are key fields so structure-first retrieval is a Redis set intersection rather than a scan, and so the Redis key itself names the partition and class of every row.

### Functions

```python
async def write(conn, write: EpisodeWrite) -> EpisodeId
async def ingest(conn, event: Event) -> EpisodeId | None
async def retrieve(query: SliceQuery) -> list[MemoryHit]
async def latest_summary(space: SpaceId, regards: str) -> MemoryHit | None
async def propose(conn, proposal: BeliefProposal) -> str
def derive_ceiling(event_types: Sequence[EventType], proposed: SourceClass) -> SourceClass
```

These are seams §3.8 (v2). `conn` is the caller's open psycopg transaction, as in every other §3 signature. All popoto calls run through `asyncio.to_thread`. Decided here: one rule for every popoto call rather than a mix of popoto's sync and async methods, because `BM25Field.search` has only a sync form and the prereq test proved the sync path on 3.14.

**`write`.** In order:

1. Refuse a space absent from `load_all()` (the module-level name above) and refuse `UNASSIGNED_SPACE_ID` (architecture §9, PATCHED: the unassigned space permits header reads and nothing else). `"global"` has no manifest, so this step is also what refuses a global-scope proposal.
2. Load `id, space_id, type` for every id in `provenance` with one `SELECT ... WHERE id = ANY(%s)` under `kernel_rw`. Refuse if any is missing or carries another `space_id`. Decided here: the check is memory's own SQL rather than a new `events` function, because it reads three columns and never the payload, and tech stack §3 (LOCKED) puts hand-written SQL in the kernel.
3. Mint `episode_id = new_id()` (seams §0) and `text_sha256`.
4. `events.append(conn, space_id=..., type="episode.written", payload=...)` with `episode_id, space, kind, provenance, data_class, regards, text, text_sha256` (seams §3.1, §4, v2). Decided here: the payload carries the text, because tech stack §8 (PROVISIONAL) says summaries are events, and architecture §6 says poisoning is a diff you find in the log; with the text in the event, a Redis row can be checked against, and rebuilt from, the record that has standing.
5. Read back `occurred_at` of the appended row by id in the same transaction, then `Episode.create(...)` with `event_id` set to that id and `written_at` set to that `occurred_at`. A failure here raises inside the caller's transaction, so the event is never committed without its row. Decided here: `written_at` is the event's timestamp rather than a second clock reading, so a row is rebuildable from the log and property 3 can check the field; the read-back is one primary-key `SELECT`.

Decided here: event first, row second, because the event is the record with standing (tech stack §12) and the row is the index over it. The one gap this leaves is named under Risks.

**`ingest`.** Builds an `EpisodeWrite` from an appended event, with `provenance=[event.id]`, and hands it to `write`, so an ingested episode gets its own `episode.written` event like any other. Returns `None` for any other type, and returns `None` when `event.space_id == UNASSIGNED_SPACE_ID`, because the unassigned space reads headers and does nothing else (architecture §9) and so remembers nothing; the supervisor calls `ingest` inside the same transaction as the utterance's event, and a refusal there would unwind the person's own message. The mapping:

| Event | kind | text | regards | data_class |
|---|---|---|---|---|
| `message.received` | `turn` | `payload.text` | `conversation_id` | OPERATOR |
| `message.sent` | `turn` | `payload.text` | `conversation_id` | OPERATOR |
| `report.landed` | `report` | `payload.report.summary` | `objective_id` | PROJECT |

Decided here: the kernel ingests raw turns and reports deterministically and the Scribe writes only summaries and decisions. Architecture §6 says episodic memory ingests raw turns with no extraction step, and architecture §3.5 gives structured records to the kernel and language to the Scribe; a copy of a turn needs no language. The supervisor plan decides where in the turn it calls `ingest`; this plan provides the mapping (seams §3.8, v2).

Decided here: conversation turns ingest as OPERATOR and reports as PROJECT. Architecture §3.1 makes the Commit card the release act for anything the supervisor derived from OPERATOR slices, and the person's conversation with the supervisor is where those slices are in play; a report is written by a worker already capped at PROJECT. The consequence at M0 is that an Executor's `memory` block holds reports and any summary the Scribe wrote at PROJECT, and never a conversation turn. Widening that is a person's decision, taken with the operator record at M1.

**`retrieve`.** Names one space and returns at most `k` hits:

1. Candidates are the set intersection of `space`, the requested `kinds` (all four when `None`), `regards` when given, and the data classes at or below `max_data_class` (PROJECT admits PROJECT; OPERATOR admits both).
2. When `query` has a token, rank with `BM25Field.search(Episode, "text_bm25", query, limit=k, allowed_keys=candidates)`; the score is the BM25 score. `allowed_keys` is popoto's own scoping argument, so a hit outside the space is impossible by construction and the limit counts in-space hits.
3. When `query` is blank, order candidates by `episode_id` descending and take `k`; the score is `0.0`.
4. Every hit is built from the row: `space, episode_id, kind, text, provenance, written_at, score, regards, data_class, text_sha256`.

`retrieve` never calls `Episode.query.all()` and never issues a filter without `space`; the property tests hold that. The ordering is deterministic for identical Redis state: BM25 ties break on the redis key inside popoto's script, and v7 ids are total. Decided here: lexical rank through popoto's `BM25Field` at M0 and no vector stage. Tech stack §8 (PROVISIONAL) names Voyage for embeddings, and the selection rule (LOCKED) forbids integrating a vendor ahead of a need; the two first objectives have no need the space's own recent episodes and a keyword rank cannot meet. The vector stage is listed under Out of scope. Finding 3 records what BM25 shares across partitions.

**`latest_summary`.** The set intersection of `space`, `kind="summary"`, and `regards`, ordered by `episode_id` descending; the first row as a `MemoryHit` with `score=0.0`, or `None` when the set is empty. Three key fields, one intersection, no scan and no rank. Decided here: no data class cap on this call, because its one caller is the supervisor's render (seams §3.3), which holds OPERATOR, and the door (seams §2.4) does not expose it; a worker reaches summaries only through `retrieve`, where the cap applies. Decided here: `regards` is required, because a summary with no `regards` belongs to no thread or objective and nothing rehydrates from it; the supervisor writes thread summaries with `regards=conversation_id` (architecture §1) and objective summaries with the objective id.

**`propose`.** In order: refuse an unknown or unassigned space; load the supporting events as in `write` step 2, reading also `payload->'approval'->>'kind'` and `payload->'approval'->>'session_id'` for `approval.minted` rows, and refuse a missing or foreign one; set `kernel_minted = True` when any `approval.minted` row has kind `self_approved` or `expired` or session id `kernel`; `derived_ceiling = "inferred" if kernel_minted else derive_ceiling(types, proposal.proposed_source_class)`; mint `proposal_id = new_id()`; append `belief.proposed` with `proposal, proposal_id, derived_ceiling`; return `proposal_id`. No Redis row: the belief table is a materialized view of the log (architecture §6) and the view is built at M1 (seams §3.8, §6). Global scope cannot be proposed because `"global"` has no manifest and step 1 refuses it (architecture §6, who sets the scope, PATCHED); `SpaceId` is a `str` alias and does no refusing of its own. Decided here: the self-approval check lives in `propose` rather than in `derive_ceiling`, because the seam gives `derive_ceiling` types only, and a kernel self-approval (seams §1.12) shares its type with the person's approval; a proposal citing one must land `inferred`, since no person was involved and M1 reads this log.

**`derive_ceiling`.** Pure. Returns `proposed` when every supporting event's type is person-authored and `proposed` is one of `direct`, `correction`, `decision`; returns `inferred` otherwise. This is architecture §6's rule as written: the proposer chooses among the three person classes when the evidence is the person's, and an inference is `inferred` whatever the proposer wrote. Decided here: the person-authored types at M0 are `message.received`, `correction.recorded`, and `approval.minted`, the three seams §4 events that carry a surface `session_id` or are a kernel-typed utterance. M0 records the ceiling and reads nothing (seams §3.8).

### Space isolation, stated once

Redis has no row-level security. What keeps spaces apart here is: `space` in every key; every filter naming a space; `allowed_keys` on every ranked search; the provenance check refusing evidence from another space; and the door (seams §2.4) refusing a query for a space other than the Brief's. Properties 1, 2, and 5 test the first four; the fifth belongs to the worker plan.

## Tasks

1. **Schemas.** `schemas/memory.py` and `schemas/belief.py` as in seams §1.13 and §1.14 with the amended fields; validators for provenance by kind and `k` in 1..20. *Accept:* `uv run pytest tests/test_schemas_memory.py` green: `test_summary_without_provenance_refused`, `test_extra_field_refused`, `test_slice_query_defaults_to_project`, `test_belief_fields_match_seams`.
2. **Models.** Rewrite `kernel/memory.py`: the `REDIS_URL` line, the `load_all` binding, `Episode` and `Belief` as above, no functions yet. Move the `REDIS_URL` test binding to the top of `tests/conftest.py` and delete it from `tests/test_memory.py`; update that file to the new fields and keep its two existing tests. *Accept:* `uv run pytest tests/test_memory.py` green, including `test_ranked_query_without_space_is_refused` on `written_at` and a new `test_belief_model_round_trips_on_314`; `redis-cli -n 0 dbsize` reads the same before and after the run.
3. **`write` and the provenance check.** Add a `spaces_for(*ids)` fixture in `tests/conftest.py` that monkeypatches `kernel.memory.load_all` to a mapping of `Space` manifests for the given ids (kind `client`, one root, ceiling 1), and use it in every store-backed test. *Accept:* `tests/test_memory.py::test_write_appends_event_then_row` (the event's `text_sha256` equals the row's, `event_id` points at it, `written_at` equals its `occurred_at`), `::test_write_refuses_foreign_provenance` (no row, and after the caller's rollback no event), `::test_write_refuses_unassigned_and_unknown_space` (the unpatched fixture id is the unknown space).
4. **`retrieve`.** *Accept:* `tests/test_memory.py::test_query_ranks_within_space`, `::test_blank_query_returns_most_recent_first`, `::test_kinds_and_regards_filter`, `::test_project_caller_never_sees_operator_episode`.
5. **`latest_summary`.** *Accept:* `tests/test_memory.py::test_latest_summary_returns_newest_for_regards` (three summaries written in order for one conversation, the third comes back), `::test_latest_summary_none_when_absent`, `::test_latest_summary_ignores_other_kinds_regards_and_spaces` (a newer `turn` with the same `regards`, a newer summary for another `regards`, and a newer summary in another space all leave the answer unchanged).
6. **`ingest`.** *Accept:* `tests/test_memory.py::test_ingest_maps_three_event_types` (kind, regards, data class, and provenance per the table, and an `episode.written` event per ingested episode), `::test_ingest_ignores_other_types` returns `None` and writes nothing, `::test_ingest_returns_none_in_unassigned` (a `message.received` in `unassigned` returns `None`, writes nothing, and the caller's transaction still commits its event).
7. **`propose` and `derive_ceiling`.** *Accept:* `tests/test_memory.py::test_propose_records_event_with_ceiling`, `::test_propose_writes_no_redis_row` (`Belief` count unchanged), `::test_propose_refuses_foreign_evidence`, `::test_propose_refuses_global_scope`, `::test_self_approval_never_raises_the_ceiling` (a proposal at `decision` citing an `approval.minted` of kind `self_approved` lands `inferred`; the same citing a person's `approved` record lands `decision`).
8. **Properties.** `tests/test_memory_properties.py` with the seven properties below. Each store-backed example draws its space ids from a fixed set of three that `spaces_for` has patched into `load_all` for the module, prefixed per example so rows never collide, and cleans up after. *Accept:* `uv run pytest tests/test_memory_properties.py` green; the pure properties at 2,000 examples, the store-backed ones at 100 with `deadline=None`.
9. **Format and contract.** *Accept:* `uv run black --check .` and `uv run lint-imports` clean with the new modules present.

## Properties

The pattern is spike 05: a strategy over operations, a statement that must hold, a named test. Store-backed properties write through `write` and `ingest` against local Postgres and Redis db 1.

1. **Hits never cross spaces.** Over any sequence of `EpisodeWrite`s across two or three spaces and any `SliceQuery`, every hit's `space` equals the query's, and every hit's `episode_id` was written under that space. `test_memory_properties.py::test_hits_never_cross_spaces`.
2. **Hits never exceed the caller's data class.** Over the same writes and any query with `max_data_class="PROJECT"`, no hit has `data_class="OPERATOR"`. And over a blank query with `k=20` and fewer than 20 writes, so the cap never binds, every hit returned at `"PROJECT"` is also returned at `"OPERATOR"`: the cap narrows the candidate set and does nothing else. `::test_hits_never_exceed_data_class`.
3. **Memory says nothing the log did not record.** For every hit, the row's `event_id` resolves by primary key to an `episode.written` event whose `episode_id`, `text_sha256`, and `occurred_at` equal the hit's `episode_id`, `text_sha256`, and `written_at`. `::test_every_hit_has_its_event`.
4. **Retrieval is a function of store state.** Two calls of `retrieve` with the same query and no write between them return identical lists, ids and order. `::test_retrieve_is_deterministic`.
5. **Foreign or missing evidence is refused whole.** Over any provenance list drawn from events in the query's space, another space, and unused ids, `write` and `propose` succeed exactly when every cited id exists in the query's space (an empty list is allowed for `turn` and `report` writes and refused by the schema for `summary`, `decision`, and every proposal), and a refusal leaves no row and no event. `::test_foreign_provenance_refused_whole`.
6. **The ceiling is derived from the authors.** Pure. Over any list of event types and any proposed class, `derive_ceiling` returns the proposed class exactly when every type is person-authored and the proposed class is one of the three person classes; `inferred` otherwise. `::test_ceiling_is_inferred_unless_person_authored`.
7. **The latest summary is the newest one, and only that.** Over any sequence of writes with kinds and `regards` drawn from small sets across two spaces, `latest_summary(space, regards)` returns the episode with the greatest `episode_id` among the `summary` writes with that `space` and `regards`, and `None` when there were none. `::test_latest_summary_is_newest_matching`.

## Out of scope

- **The operator record's read and write path**: the belief materialized view, quarantine and promotion, retirement, the goal trail through popoto's supersession, `ConfidenceField` recorded and unread, the standing digest of belief changes, and the global partition every render reads. Tech stack §10 places the operator record at M1; seams §3.8 and §6 say the `Belief` model is unread at M0.
- **Corrections as beliefs.** At M0 a correction is the event `correction.recorded` and the supervisor renders it from events (seams §1.12, decided there); it becomes a `Belief` of source class `correction` at M1.
- **Vector retrieval and embeddings.** Tech stack §8 (PROVISIONAL) names Voyage-4 and `embedding_model` per row; no embedding provider exists at M0 and the selection rule forbids adding one ahead of a need. Arrives with the operator record, whose retrieval needs it first.
- **Retention.** "Retention by importance decay" (architecture §6, KEEP) and `Space.retention.memory_days` (architecture §9). Nothing reaches its retention age inside M0. The sweep that enforces it is also where a Redis row without an event is removed (Risks).
- **Ending a space** removes memory rows (architecture §9, COMMITTED). Built with the space-destroy migration, which is after M0.
- **Thread compression.** Deciding when a thread exceeds its cap and firing the Scribe is the supervisor's (architecture §1); memory stores the summary and hands it back through `latest_summary`.
- **Cross-space views** composed from per-space Scribe summaries (architecture §9): M1 with the morning brief.
- **Token-budgeted packing** of hits (popoto's `ContextAssembler`): the supervisor's render enforces the slice caps; memory returns `k` hits.
- **The understanding metrics** over episodic and belief events (architecture §6): computed at volume.
- TODOs from the refinement record seen and left alone: classification propagation by rule (architecture §3.1), which would touch the data class of derived text.

## Risks

- **Two stores drift.** A caller that rolls back after `write` returns leaves a Redis row with no event. Event-first ordering closes the other direction, property 3 detects the drift, and the M1 retention sweep removes such rows. Tech stack §3.1 accepts the two stores without a joint transaction because memory has no authority; the plan keeps to that.
- **popoto on 3.14.** Prereqs item 3 proved `KeyField`, `AutoKeyField`, `SortedField`, and `ConfidenceField`. `UniqueKeyField`, `BM25Field`, `ListField`, and `DatetimeField` are first exercised by task 2, which is why it comes before any function.
- **Scoped BM25 has a fetch ceiling and loads candidates.** popoto's `allowed_keys` widening stops at `SCOPED_SEARCH_FETCH_CAP = 4096` rows of the whole `Episode` corpus, so once every space together passes that, a space whose hits rank below that position comes back short; and step 1 loads every candidate row in the space to hand their keys to the search. Both are far from M0 volumes; the cap is the number to watch when episodic memory reaches thousands of rows.
- **Shared BM25 statistics.** Document frequency and average length are per model, so a write in one space can shift the rank, never the membership, of another space's hits (Finding 3). For M0 volumes the effect is unmeasurable; the architect owns popoto and can partition the statistics if the property matters.
- **Property tests against two live stores** are slower than spike 05's pure ones. The example counts in task 8 are set for a run under a minute; if they are not, the store-backed strategies shrink before the pure ones do.

## Questions for the architect

1. **Should BM25 statistics partition by space in popoto?** Assumption while it waits: shared statistics are acceptable at M0, since hits never cross and the rank shift is small. If the answer is yes, it is a popoto change and this plan's `retrieve` is unchanged.
2. **Is an embedding provider part of M0?** Assumption: no. Retrieval is structure then lexical; the vector stage arrives with the operator record.

**Answered 2026-09-19 (lead, from the documents), question 2:** no embedding provider at M0 (tech stack, selection rule). **Question 1** stays with the architect; shared statistics are the assumption.

## Seam amendments

All five were applied in seams v2 and the plan above cites v2 directly; the list stays as the record of what was asked and why.

1. **§3.8, signatures.** Replace with:
   ```python
   async def write(conn, write: EpisodeWrite) -> EpisodeId
   async def ingest(conn, event: Event) -> EpisodeId | None    # message.received, message.sent, report.landed; None otherwise
   async def retrieve(query: SliceQuery) -> list[MemoryHit]
   async def propose(conn, proposal: BeliefProposal) -> str
   ```
   Reason: the two writers append events and must do so inside the caller's transaction, as every other §3 function does; `ingest` is the deterministic raw-turn path of architecture §6 and the supervisor calls it.
2. **§1.14, fields.** `EpisodeWrite` gains `regards: str | None = None`. `MemoryHit` gains `regards: str | None`, `data_class: DataClass`, `text_sha256: str`. `SliceQuery` gains `regards: str | None = None` and `max_data_class: DataClass = "PROJECT"`. Reason: the supervisor rehydrates a thread from its summary (architecture §1) and needs a structural handle to find it; the `memory` block's `ContextBlock.data_class` needs each hit's class; a caller that omits the cap must get the narrower slice.
3. **§4, `episode.written` payload.** `episode_id, space, kind, provenance, data_class, regards, text, text_sha256`. Reason: summaries are events (tech stack §8) and the log is what a poisoning diff is found in (architecture §6).
4. **§6, Redis row.** "Redis: `Episode` model (kinds `turn`, `report`, `summary`, `decision`) and `Belief` model (defined and unread at M0)." Reason: `EpisodeKind` already names `summary`; one model, one retrieval path.
5. **§2.4, `read_slice`.** Add: "the door replaces `query.max_data_class` with the Brief's `max_data_class` before calling `retrieve`, so a worker cannot widen its own slice." Reason: the cap is a Brief fact (architecture §3.1) and the door is where Brief facts are enforced.

**Lead, reconcile (seams v2, 2026-09-19):** amendments 1 to 5 accepted as written (seams §1.14, §2.4, §3.8, §4, §6). One addition from the supervisor plan: `async def latest_summary(space: SpaceId, regards: str) -> MemoryHit | None` joins §3.8, the newest `summary` episode for a conversation or objective, which `regards` already makes a set lookup.

## Findings

1. `kernel/memory.py` line 19 mints `episode_id` with `AutoKeyField` (uuid4 hex); seams §0 requires a UUIDv7 minted by the kernel. Built against the seams; the model is rewritten.
2. `kernel/memory.py` lines 21 and 22 give an episode `salience` and `confidence`; no section of architecture §6 or tech stack §3.1 gives an episode either, and seams §1.14 `EpisodeWrite` carries neither. Built against the seams; both fields leave.
3. Architecture §6 (popoto rules, ADDED) says "There is no query that spans partitions." popoto 1.9.0's `BM25Field` keeps document frequency and average length per model class, so a ranked query is scoped by `allowed_keys` but scored with corpus statistics from every space. Membership holds; rank independence across spaces does not. Recorded for the architect (Question 1).
4. Seams v1 §6 listed `Episode` and `Summary` as two Redis models while §1.14 made `summary` an `EpisodeKind` written through the same `EpisodeWrite`. Resolved in v2: one `Episode` model.
5. Architecture §3.5 says the Scribe "writes episodic memory directly, as raw turns and summaries", while architecture §6 says episodic memory "ingests raw turns and reports" with no extraction step and §3.5's division of labor gives deterministic records to the kernel. Built against §6 and the division of labor: the kernel ingests turns and reports through `ingest`, the Scribe writes summaries and decisions through `write`.
6. Tech stack §8 says "Store `embedding_model` and dimensions on every row regardless", and the M0 table in §10 ships no embedding provider; a column with no writer at M0 would be a hook. Left to M1 with the vector stage (Out of scope).

### Critique, 2026-09-20

Read against seams v2 with its Round two section (nothing in Round two touches this plan; the header's seams line stands), popoto 1.9.0 as installed in `.venv`, and the sibling plans that call §3.8. Three critics ran independently; where two reached the same defect it says so.

1. **Every store-backed test writes into a space with no manifest.** `write` step 1 (line 136) refuses "a space absent from `spaces.load_all()`", and `propose` repeats it (line 167). Every test the plan names runs under `tests/conftest.py::space`, which mints `space-<random>` with no manifest anywhere, and the property tests (line 184) put "each example under its own space prefix". `load_all(directory=SPACES_DIR)` only resolves against `infra/spaces/*.yaml`, and seams §3.8 gives `write` and `propose` no `spaces` argument, so tasks 3, 6, 7, and 8 and properties 1, 3, 5, and 7 fail on their happy paths as written. Fix: bind `load_all` as a module name in `kernel/memory.py` (the tree plan does this for `check_may_open`) and have the memory tests `monkeypatch` it to a mapping that covers the fixture's ids and every id a property example draws; say so in tasks 3 and 8. Corroborated by the Risk critic.
   **Response:** changed. `load_all` is a module-level name in `kernel/memory.py`, looked up at call time; `tests/conftest.py` gains `spaces_for(*ids)`, which monkeypatches it; tasks 3 and 8 name the fixture and property examples draw from three patched ids. The seams signatures are unchanged.
2. **`ingest` in the `unassigned` space rolls back the person's own message.** The surface plan (line 175) makes `unassigned` a legal conversation target so `route_inbound` cards can be seen there; the supervisor plan (line 145) turns every `Utterance` into `message.received` "followed by `memory.ingest` of that event in the same transaction", before any `check_may_open`. This plan's `ingest` (line 144) maps `message.received` and hands it to `write`, whose step 1 refuses `UNASSIGNED_SPACE_ID`, so the exception unwinds the transaction and the utterance's event is lost. Fix: `ingest` returns `None` when `event.space_id == UNASSIGNED_SPACE_ID`, with the reason (architecture §9: the space reads headers and does nothing else, so it remembers nothing), and task 6 gains `test_ingest_returns_none_in_unassigned`. While there, say in one sentence that `ingest` builds an `EpisodeWrite` and calls `write`, so an ingested episode gets its own `episode.written` event; property 3 depends on it and the text only shows the field table. Corroborated by the Consistency and Risk critics.
   **Response:** changed. `ingest` returns `None` for an event in `unassigned`, and the text now says it builds an `EpisodeWrite` and calls `write`; task 6 gains `test_ingest_returns_none_in_unassigned`, which also checks the caller's event still commits.
3. **A kernel self-approval counts as person-authored.** Line 169 lists `approval.minted` among "the person-authored types at M0", but seams §1.12 and the surface plan (line 167) mint a `self_approved` record with `session_id = "kernel"` and an empty `raw_message` as the same event type, and `derive_ceiling` sees only the type. A proposal citing a self-approval would be recorded at `decision` with no person involved, in an append-only log that M1 reads. `derive_ceiling(event_types, proposed)` is the seam and stays as it is. Fix in `propose`: the step-2 `SELECT` also reads `payload->'approval'->>'kind'` for `approval.minted` rows, and a `self_approved` or `expired` kind is passed to `derive_ceiling` as not person-authored (drop it from the sequence and pass a flag, or substitute a non-person type; the plan picks one); add `test_self_approval_never_raises_the_ceiling` to task 7. Dropping `approval.minted` from the list altogether is the smaller change and loses nothing at M0. Corroborated by the Consistency critic.
   **Response:** changed, keeping `approval.minted` rather than dropping it, per the lead: `propose` reads the approval's kind and session id and forces `inferred` when the record is `self_approved`, `expired`, or minted under session `kernel`; `derive_ceiling` stays as the seam; task 7 gains `test_self_approval_never_raises_the_ceiling`.
4. **Property 2's superset clause is false under top-k.** Line 192: with `"OPERATOR"` "the hit set is a superset of the PROJECT hit set". `retrieve` returns at most `k` hits ranked by BM25, so admitting OPERATOR candidates can push a PROJECT hit out of the top `k`; Hypothesis will find it with two spaces and `k=1`. Fix: restate as "every PROJECT hit is a candidate under OPERATOR" (membership, checked with a blank query and `k=20` over fewer than 20 writes), and keep the first clause as it is.
   **Response:** changed. Property 2 keeps the first clause and restates the second as membership over a blank query with `k=20` and fewer than 20 writes, so the cap never binds.
5. **`REDIS_URL` binds on first import, and the first importer is not `tests/test_memory.py`.** Line 87 sets the default to db 0 in `kernel/memory.py`; `tests/test_memory.py` overrides it at module top, which works only while it is the first module in the session to import popoto. `tests/test_context.py` (supervisor) collects before it alphabetically and imports `kernel.context`, which imports `kernel.memory`, so the whole session binds db 0, task 2's acceptance "`redis-cli -n 0 dbsize` reads the same" (line 178) fails, and popoto's guard refuses the cleanup flush. Fix: set `REDIS_URL` at the top of `tests/conftest.py` before any import (or `[tool.pytest.ini_options] env`), remove the per-file `setdefault`, and name this in task 2.
   **Response:** changed. `REDIS_URL` is bound at the top of `tests/conftest.py` before any import; the per-file `setdefault` in `tests/test_memory.py` is removed; task 2 names both. `kernel/memory.py` keeps its db 0 default for the process.
6. **`written_at` has no stated source.** Lines 103 and 161 write and return it; nothing says what value `Episode.create` receives, and the `episode.written` payload (line 139) carries no timestamp, so the row cannot be rebuilt from the log and property 3 cannot check the field. Fix: one sentence, either "the appended event's `occurred_at`, read back in the same transaction" or "`datetime.now(UTC)`, index-only, never rendered". Related, and for the lead rather than the plan since the seams win: §3.8's `ingest(conn, event: Event)` needs a full `Event` while `append` returns an `int`; the supervisor must read the row back (`events.read(conn, space_id=..., after=id - 1, limit=1)`) or hand-build one with an approximate `occurred_at`. Raised by the Consistency critic.
   **Response:** changed. `written_at` is the appended event's `occurred_at`, read back by primary key in the same transaction, so the row is rebuildable and property 3 checks it. The `Event` argument of `ingest` stands as the seam; `ingest` reads only `id`, `space_id`, `type`, and `payload`, so a hand-built `Event` from the supervisor is as good as a read-back.
7. **The global-scope guard is misattributed.** Line 167: "`BeliefProposal.space` is a `SpaceId`, so global scope cannot be proposed." `SpaceId` is a `str` alias (events plan, line 86), so `space="global"` validates; what refuses it is step 1's manifest check. Fix: say so, and add `test_propose_refuses_global_scope` to task 7.
   **Response:** changed. The guard is the manifest check in step 1, said so in `write` and `propose`; task 7 gains `test_propose_refuses_global_scope`.
8. **The scoped BM25 search has a ceiling the plan does not name.** Line 159 says "the limit counts in-space hits". True until the `Episode` corpus across all spaces passes `SCOPED_SEARCH_FETCH_CAP = 4096` rows (`popoto/fields/bm25_field.py`), after which a space whose hits rank below that position is returned short by design. Also, step 1's candidates are popoto instances, so every candidate row in the space is loaded before ranking to obtain the keys `allowed_keys` wants. Both fine at M0 volumes; add one line under Risks beside the shared-statistics entry.
   **Response:** changed. A Risks entry names `SCOPED_SEARCH_FETCH_CAP` and the candidate load beside the shared-statistics entry.
9. **`event_id` on `Episode` (line 102) has no M0 reader** beyond `test_write_appends_event_then_row`; the Risks section gives it to the M1 retention sweep, and the plan's own rule (line 117, "a field with no writer is a hook") cuts the same way for a field with no reader. Property 3 uses `episode_id` through `events.read_for`. Either drop the column or name its M0 reader. Raised by the Scope critic.
   **Response:** stands, with the reader named. `event_id` is what property 3 and task 3's test resolve to the log by primary key, which `read_for` on a payload key cannot do as cheaply; the comment on the field says so.
10. **Small text defects.** (a) Line 139 calls `events.append(conn, space_id, "episode.written", payload)` positionally; seams §3.1 is keyword-only (`space_id=`, `type=`, `payload=`). (b) Line 138 mints with `str(uuid.uuid7())`; seams §0 and the events plan give `schemas.ids.new_id()`. (c) The popoto `Belief` model (line 106) names `belief_id` where seams §1.13 names `id`, and `supersedes` and `last_confirmed_at` need `null=True`. (d) Property 5 (line 195) says `write` succeeds "exactly when every id exists in the query's space"; a `turn` or `report` with empty provenance also succeeds, so qualify it. (e) Line 144 "Maps a committed event": the supervisor calls it before commit, in the same transaction; "appended" is the true word.
   **Response:** changed, all five: keyword arguments to `append`; `new_id()` for both ids; `Belief.id`, and `null=True` on `supersedes` and `last_confirmed_at`; property 5 qualified by kind; "appended" in `ingest`.
11. **The index disagrees with the header.** `docs/plans/README.md` row 09 lists "Depends on: spaces"; this header (line 10) and the build order say events and spaces. For the lead. Raised by the Scope critic.
   **Response:** stands; for the lead. The header and the build order are right, since `write` appends through `kernel/events.py`; the index row is the lead's to edit.

Verdict: return to author. Findings 1 to 3 change behavior and tests a builder cannot infer from the plan, and 4 and 5 are acceptance checks that fail as written; each fix is a few sentences.
