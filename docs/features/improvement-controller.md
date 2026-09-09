# Improvement Controller

The loop that lets the system find its own weaknesses, gather what it needs to
know, test candidate changes, and explain with evidence why the next version
deserves to exist.

Tracking issue: [#3177](https://github.com/tomcounsell/ai/issues/3177).
Plan: [`docs/plans/recursive-self-improvement.md`](../plans/recursive-self-improvement.md).
Current state: [capability matrix](../plans/critiques/recursive-self-improvement-capability-matrix.md).
North star: [`docs/improvement-charter.md`](../improvement-charter.md), which Tom owns and only Tom edits.

## What exists today

Lanes 1 and 2. The records, the settings, the evidence collection tick, the
verifying artifact store, and two dashboard panels. The control journal, the
research sessions, the evaluation harness, and releases arrive with lanes 3
through 6, each as its own child issue.

Read the capability matrix before believing anything is working. It grades each
component on four separate axes — implemented, deployed, measured, effect — and
"implemented" supports exactly one claim: the code exists.

## Three layers

**Research reasoning.** Bounded sessions that read evidence, revise the system's
model of itself, generate hypotheses with a mechanism and a falsifier, and
propose actions. Natural-language research instructions live in one narrowly
scoped skill; authority lives in code. Not built yet (lane 5).

**Control journal.** A small non-Popoto Redis namespace, `improve:{project}:{case}:*`,
whose Lua transition script is the sole authority for research-state
transitions, leases, dispatch intents, and budget reservations. The flat Popoto
records below are its queryable projection, updated after the journal commits.
Decisions read the journal head, never the projection. Not built yet (lane 3).

**Existing execution infrastructure.** Jobs, AgentSessions, the reflection
scheduler, worktree and venv isolation, the SDLC pipeline, the content store,
and `tools/memory_eval/` statistics. Reused through public APIs and extended at
named seams, never forked.

## Three authorities

| Authority | May | May not |
|---|---|---|
| Research | Read scoped evidence, author hypotheses, request investigations, propose experiments within the charter | Enqueue sessions, contact anyone, amend the charter |
| Candidate | Modify declared candidate surfaces in isolation | Read holdout answers, alter release policy, touch production state, contact stakeholders |
| Evaluation and release | Run frozen contracts, write access-controlled verdicts under a separate process identity | Promote anything automatically |

**A worktree is not a security boundary.** Until evaluator secrets and
production credentials are separated from candidate execution, automated
promotion stays disabled. That separation is an event outside this plan.

## Records

Eight flat modules in `models/`, each exported from `models/__init__.py`, each
carrying a schema-gate docstring, a `project_key` partition, a recency
`SortedField` partitioned by `project_key`, and only low-cardinality
`IndexedField`s.

| Model | Holds | TTL |
|---|---|---|
| `ImprovementCharter` | Immutable authorization versions. The controller cannot write this model at all | Immortal |
| `ImprovementEvidence` | One observation the loop reasons from | 30 days |
| `ImprovementModelRevision` | Revisions of the system's model of itself | Immortal |
| `ImprovementCase` | A weakness the system decided to pursue | Immortal |
| `ImprovementInvestigation` | One bounded act of finding something out | 30 days |
| `ImprovementExperiment` | A candidate under a frozen, preregistered contract | Immortal |
| `ImprovementEvaluation` | A paired, blinded measurement | Immortal |
| `ImprovementRelease` | A qualified candidate awaiting human review | Immortal |

Evidence and investigations expire on `ReflectionRun`'s 30-day horizon: they are
high-volume observation rows and retrieval-dated external claims respectively,
and a cached claim outliving its retrieval date is worse than no claim. What
must outlive the window is distilled onto the case, which is immortal. The rest
are lineage: an evaluation nobody can trace back to what was actually run is not
a verdict, so expiring any of them would erase the comparison the system exists
to make.

Every one records its TTL decision in prose in its module docstring, and
`tests/unit/test_improvement_models.py` fails if a docstring stops saying so.

## Evidence collection

`reflections/improvement_collect.py` runs three observer adapters on a 900-second
tick. Each is independently fail-soft; one broken source degrades the tick
rather than ending it.

| Adapter | Reads | Writes |
|---|---|---|
| `collect_corrections` | Inbound `AgentSession.chat_message_log` turns and Tom-sourced `Memory` rows, via `reflections.utilities.CORRECTION_PATTERNS` | One `correction` row per session that needed correcting, plus one per correcting memory |
| `collect_inspirations` | `Memory` rows with `source="human"` — the links Tom sends | One `inspiration` row per memory, preserving the text, its reference, and its date |
| `collect_expectation_coverage` | Open outbound expectations on Jobs | One `owner_liveness` row per gone owner, plus one coverage row per tick |

`expectation_reconciler` additionally records its shipped-work signal at the
point it computes it, so "how often did a lane ship without discharging its
expectation?" becomes answerable.

**Both correction inputs have a named production writer.** That is the whole
point: the retired delegation aggregate was structurally always zero because the
session flag it averaged had nobody writing it, and retiring that shape is why
this system exists. So the detector reads only fields something fills in:

| Input | Production writer | Dedup identity |
|---|---|---|
| `AgentSession.chat_message_log` entries with `direction="in"` | `bridge/dispatch.py::_append_inbound_chat_log`, on every inbound Telegram message | `source_session_id` |
| `Memory` rows with `source="human"` | `bridge/telegram_bridge.py` (`Memory.safe_save(..., source="human")`), on every inbound human message with a resolved project | `source_ref="memory:{id}"` |

`AgentSession.log_path` is deliberately not read. Its only assigner,
`bridge/session_transcript.py::start_transcript`, has no production caller, so a
detector keyed on it can never fire — measured at 0 of 58 rows on the machine
that owns `valor`. `tests/unit/test_improvement_evidence.py::TestDetectorInputsHaveProductionWriters`
pins all three facts.

The memory partition is enumerated **once per tick** and shared by the correction
detector and the inspiration adapter (`human_memories`), so the tick's one
expensive read stays one read.

**Classification is uncertain evidence, never a verdict.** `classify_correction`
answers `unknown` for the common case and only claims `architectural` when the
human named an approach or a missed end-to-end journey. A regex cannot tell a
rescue from a preference most of the time, and a confident wrong label is worse
than an honest absent one.

**Enumeration, not search.** The inspiration adapter reads
`tools.memory_search.fetch_all_records`, not `search()`. `search()` is a
relevance-ranked top-N with no `source` parameter that early-returns on a blank
query; an adapter built on it would silently under-read, which is exactly the
failure this system exists to prevent.

### Registration

The tick is registered through `scripts/update/reflection_register.py::register_improvement_collect`,
called from `scripts/update/run.py` at Step 1.6585 — **before** Step 1.66's
vault→config copy, so the entry propagates into this machine's
`config/reflections.yaml` on the same cycle. Registration writes the vault file
`~/Desktop/Valor/reflections.yaml`; writing the config copy instead would be
erased by the very next `/update`.

Machine pinning is inherited from `register_reflection`'s existing
`_this_machine_owns_valor` guard, not re-implemented, so only the machine that
owns the `valor` project schedules the tick.

### The kill switch

`ImprovementSettings.enabled` (`IMPROVEMENT__ENABLED`) gates every write in
`reflections/improvement_collect.py`. False — the default — means
`run_improvement_collect` returns `status="skipped"` and writes nothing, which
is exactly what `config/settings.py` and `.env.example` promise it means. So an
`/update` that registers the reflection does not, by itself, start a
15-minute writer against production Redis.

Registration is independent of the switch, so turning collection on is a single
`IMPROVEMENT__ENABLED=true` in the vault `.env` on the machine that owns the
project — no re-registration, and evidence starts accumulating from that moment.
Turning it back off is the same edit in reverse; no hand edit of the vault
`reflections.yaml` is needed to stop the writes.

## Settings

`config/settings.py::ImprovementSettings`, disabled by default.

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `False` | Master switch. `IMPROVEMENT__ENABLED` |
| `max_concurrent_research_sessions` | `1` | Claude work is budgeted as SDLC lane concurrency, not dollars: the subscription is the constraint |
| `daily_paid_inference_usd` | `10.00` | Daily pool for paid inference on non-Claude models through OpenRouter; controller and evaluator draw separate reservations |
| `weekly_infrastructure_usd` | `50.00` | Weekly pool for sandboxes, storage, and Cloudflare, charter §8's second spending category |
| `budget_day_boundary` | `UTC` | The timezone whose midnight ends a budget day |
| `budget_week_start` | `monday` | The weekday an infrastructure budget week begins on |
| `controller_tick_seconds` | `900` | Cadence, matching the registered reflection |

Three budget units, reserved separately, and one of them is not money: Claude
work runs on the subscription and is budgeted as lane concurrency. The window
boundaries are settings rather than assumptions because charter §8 requires them
disclosed — a reservation that resets on an undisclosed boundary cannot be
audited against what was actually spent.

**Nothing meters dollars today.** Both dollar figures are declared limits with
no meter behind them; `tools/paid_inference_meter.py` arrives with lane 3.
Uncertain metering is not zero cost.

**There is no fixed allocation across areas.** Ranking is by expected
contribution to the north star (charter §3), recorded per case as
`ranking_rationale` beside the `priority_area` and the charter digest it was
ranked under. The eleven-value `PRIORITY_AREAS` vocabulary is a classification,
not a quota.

**There is no `daily_question_ceiling`.** Charter §9 forbids routine research
questions: the controller resolves uncertainty from Tom-sourced memories and
online research, and records what it cannot resolve as a provisional assumption
with its evidence, shown on the dashboard as an assumption rather than a fact.
One message class is permitted, the evidence-backed charter amendment request,
and it arrives with lane 3. A Verification row fails the build if a routine
question path reappears.

## Charter

`docs/improvement-charter.md` is the north star. Tom owns it and only Tom edits
it; nothing in `models/`, `tools/`, `reflections/`, or `ui/` writes that file,
and it is on the candidate-surface denylist.

`ImprovementCharter.load_from_file` projects the file into a record. It digests
the bytes with `tools/sdlc_verdict.py::compute_plan_hash`, which normalizes CRLF
to LF, so the same charter digests identically on any checkout. It **refuses**
any file whose frontmatter names an owner other than `Tom Counsell`, returning
None and writing nothing.

The record is append-only. One immutable row per unseen digest; an amended
charter adds a row and leaves the earlier one exactly as it was, because an
evaluation or release that cites a charter must still resolve it years later.
The loader never calls `save()` on an existing row, never flips a prior row to
`superseded`, and never deletes. `ImprovementCharter.pinned(project_key)` is the
charter in force: the newest row by `created_at`.

`digest` is a plain field rather than an index. The schema gate rejects an
indexed field whose name marks it unbounded, so the loader matches the digest in
Python over the project's charter rows, which number one per version.

`ImprovementCase`, `ImprovementInvestigation`, and `ImprovementRelease` each
carry `charter_digest`, so a decision records the exact charter text it was
admitted under. The version is the human-readable name; the digest is the
identity.

## Provider eligibility and resources

`tools/improvement_eligibility.py::is_open_source(project_key)` is the charter
§7 guard: any provider may see open-source work, and client work stays on the
Claude and Codex subscriptions. It **fails closed to client** on every
uncertainty, and it passes the repository to `gh` positionally, because
`GH_REPO` is set process-wide and `gh` reads it before cwd. Its cache is
process-local rather than a Redis key, and caches only determinate answers.

`tools/improvement_resources.py::probe()` is the charter §8 verification: each
named resource is reported `verified`, `absent`, or `unknown`. `unknown` is the
default on any uncertainty, never `absent`, because reporting a resource absent
when it exists sends the next lane out to acquire something already in the
vault. Presence comes from `op item list` titles, which carry no field values;
where a fingerprint is wanted the credential is hashed immediately and reported
as `sha256:<hex>`. The probe never raises and never emits a credential.

## Control namespace contract (lane 3)

`improve:{project_key}:{case_id}:*`, with an explicit schema version, holding
only what is research-specific: the case head, its revision, action IDs, and
reservations.

- `head` holds `{revision, state, epoch, owner, updated_at}`; `journal` is a
  bounded list of `{revision, action_id, event, payload_digest, ts}`.
- One Lua script `transition(expected_revision, epoch, action_id, event, payload_digest)`
  verifies ownership and epoch, checks the expected revision, advances the head,
  and appends. Rejections return a reason code; they never raise.
- Leases, including the monotonic fencing generation and Redis-server-time
  expiry, come from **#3183's lease module**. This system writes no second lease.
  The accept rule at effect boundaries is `generation >= highest_accepted`,
  because a strictly-greater rule refuses the holder's own second write.
- Dispatch, budget authorization, and release each re-check the generation
  inside the same script call that records the effect. A lease check followed by
  an unguarded effect is not fencing.
- Payloads are written to the content store and hashed before the transition
  references their digest. An artifact with no committed event is an orphan.

**The raw-Redis guard is a text heuristic, not a namespace check.** It fires when
one command string contains both a Popoto-context substring and a block pattern,
so it can misfire on a plain `improve:*` key. Two consequences, both binding: the
control journal binds its client under a private alias in its own module, never
`from popoto.redis_db import POPOTO_REDIS_DB as _R` in a file where an operator
would type a debug one-liner; and compare-and-delete on release is exercised
through a pytest file, never an inline `python -c`. The exemption for raw Redis
on a non-Popoto key is already stated in this repo at
`models/session_lifecycle.py`, with a working Lua CAS precedent in the same file.

## Dispatch (lane 3)

The child session gate fires only when `parent_agent_session_id` is set, and the
reflection scheduler already creates top-level sessions with no parent. Research
sessions take that same path: **the gate is never engaged and never lifted**.
Ownership belongs to the `ImprovementCase` and its Job, not to a parent executor
session; sessions carry `research_case_id`, `experiment_id`, and `action_id` as
provenance.

Only the scheduler adapter submits research sessions, after validating charter,
journal authorization, reservation, and allowed action type. A planning session
proposes an action by writing a journal event; it cannot enqueue.

Lane 3 consumes #3183's idempotent create-or-bind seam on `_push_agent_session`
and changes no queue signature.

## Artifacts

`models/verifying_artifact_store.py`. Popoto's `FilesystemStore.load()` verifies
the hash on the live path and then, on a mismatch or a missing live file,
returns the **archived bytes without re-hashing them**. For a document chunk
that is harmless. For evaluation evidence it turns a corrupted file into a
scored result.

`VerifyingArtifactStore` re-hashes on every load path, archive included, and
raises `ArtifactIntegrityError` instead. Callers treat that as "this evaluation
cannot be scored", which is the honest outcome: an unverifiable artifact is not
weak evidence, it is no evidence. `exists()` is overridden to match, so
`exists()` returning True still implies `load()` succeeds.

Improvement artifacts live under their own retention root
(`POPOTO_IMPROVEMENT_CONTENT_PATH`, defaulting to `data/improvement_content`) so
retention and export policy for evaluation evidence can differ from ordinary
content without a path heuristic.

## Dashboard

Three panels on the root dashboard.

**Goals** is the charter §11 readable record: which charter version and digest
the work is ranked under, the §3 early priorities marked as starting hypotheses
rather than an allocation, open cases with their `priority_area` and
`ranking_rationale`, and unresolved assumptions. Every heading no lane writes
yet says so and names the lane that will fill it — acquired abilities and
resource use come with lane 3, evaluations with lane 4, rejected approaches with
lane 5. Each section renders one of three distinguishable states: content,
"nothing yet, written by lane N", or "unavailable" when the read failed. A bare
zero would claim a measurement was taken.

The other two panels are backed by `ImprovementEvidence`.

**Coverage** comes first and is the denominator. A falling correction count with
a falling scan count is not an improvement, and coverage is what makes the two
distinguishable. When the collection tick has gone quiet the panel says every
count below it is a count of nothing, rather than showing a comforting zero.

**Intervention burden** splits corrections by classification and publishes the
raw count beside every share. `unknown` is expected to be the largest bucket.
An empty window reads as "nothing observed", never "nothing happened".

`ui/data/improvement.py` exports exactly four getters, and a test pins that
list as an exact list. **Experiment count and merged-patch count are activity, not improvement**,
and there is deliberately no function here that returns them.

Cases, hypotheses, rejected experiments, spend, release lineage, and the
paused/inconclusive/reconciliation-required renderings arrive with the lane that
first writes each one. Six permanently empty tiles is not a dashboard.

## Break-glass

The CLI (`valor-improve`) arrives with lane 3. Until then there is no controller
to unwedge: the only moving part is the evidence tick, and it fails soft.

Once lane 3 lands, the procedure is:

**Is it a namespace outage or a wedged case?** Run `valor-improve doctor`. It
prints every paused head, every stale intent, and every outstanding reservation,
so you see the blast radius before touching anything.

- **Every case paused at once, `doctor` cannot read the namespace** — that is a
  Redis or connectivity problem, not a wedged case. Fix the substrate. The
  controller pauses and reports rather than falling back to projection state,
  by design: a decision made from a stale projection is worse than no decision.
- **One case paused, others progressing** — that case is wedged. Read its head
  and its journal tail before resuming; the journal is the record of what it was
  trying to do.

**Commands.**

| Command | Effect |
|---|---|
| `valor-improve doctor` | Print paused heads, stale intents, outstanding reservations |
| `valor-improve case show --case ID` | The head, its revision, and the journal tail |
| `valor-improve pause [--case ID] [--reason TEXT]` | Pause explicitly. A pause is never self-clearing |
| `valor-improve resume [--case ID]` | Re-read the head and clear the pause. Refuses a case whose intents are still `reconciliation_required` unless `--force` |

**Evidence to keep before resuming:** the journal tail, the `doctor` output, and
the reservation state. `resume` advances the head; the pre-resume state is not
recoverable from the projection.

**Stale intents recover on their own cadence.** The `improvement-intent-reconcile`
reflection scans for intents in `admitted` or `materialized` older than a
lease-derived staleness threshold, CAS-transitions them to
`reconciliation_required`, releases the reservation, and calls the existing
`finalize_session()` to force the orphaned row terminal. It never mints a second
identity, and it reads intents rather than a status map, so it does not depend
on `RECOVERY_OWNERSHIP` — which is explicitly an informational constant, not
used for runtime routing.

## Dependency on #3183

[#3183 (ETL-grade pipeline hardening)](https://github.com/tomcounsell/ai/issues/3183)
owns three primitives this system consumes rather than redesigns:

| Primitive | Owner | Consumed by |
|---|---|---|
| Idempotent create-or-bind seam on `_push_agent_session` | #3183's current build | Lane 3 dispatch. `status="admitted"` is a caller argument, not a seam change |
| Renewed execution lease with a fencing generation (`models/redis_lease.py`) | **#3183's lane 6, itself a child issue of #3183** | The control journal imports it wherever it needs a lease |
| Generalized `DeadLetter` record with a `stage` field | #3183's current build | Exhausted improvement intents write `stage="improvement-intent"` |

Lane 3 blocks on each until it lands. It opens no PR against
`agent/agent_session_queue.py` and defines no second dead-letter sink.

## Retired

`models/task_type_profile.py` was deleted whole in this work, along with the
`finalize_session` block that called it, the `rework_triggered` branch in
`tools/session_tags.py`, and the writerless `AgentSession.rework_triggered`
field. `rework_rate` was structurally always zero because its input had no
production writer; the delegation recommendation derived from it was derived
from nothing; and that recommendation's only reader had no callers. Its keyspace
is retired by the registered `retire_task_type_profile` migration.

**No baseline may be drawn from it.** Its historical zeros were an absent
writer, not observations of low rework. Rework is now derived from
`ImprovementEvidence` rows carrying `classification="architectural"`, which have
a real writer.

`scripts/autoexperiment.py` and its installer, plist, tests, and feature doc were
also removed. It committed to whatever branch happened to be checked out, its
`--dry-run` overwrote tracked source and spent API money, and its installer
defaulted to a target whose module was deleted in #466. It was never installed
or run on any machine in this fleet: no log, no dated result, no run history.
Its evaluation corpora are retained under `data/experiments/` as marked-legacy
evidence.

## Claim levels

The plan's yardstick, restated so nobody grades on a curve:

1. **Loop operational** — one complete autonomous investigation-to-measurement cycle.
2. **System improvement demonstrated** — held-out and production gains over incumbent.
3. **Recursive improvement demonstrated** — a changed research process produces
   greater validated gains per comparable total budget on fresh opportunities.

Repeated edits satisfy none of the latter two. This is system-level recursive
improvement with externally supplied models; model-weight training and claims of
unbounded acceleration are out of scope.

## See also

- [Improvement Evaluation](improvement-evaluation.md) — contract fields, manifest, judge envelope, statistics, holdout policy
- [`docs/plans/recursive-self-improvement.md`](../plans/recursive-self-improvement.md) — the full design
- [Capability matrix](../plans/critiques/recursive-self-improvement-capability-matrix.md) — what is implemented, deployed, measured, and unknown
- [Adding Reflection Tasks](adding-reflection-tasks.md) — how the collection tick is registered
- [Redis Models](redis-models.md) — the schema-gate rules the eight records follow
