# Improvement Controller

The loop that lets the system find its own weaknesses, gather what it needs to
know, test candidate changes, and explain with evidence why the next version
deserves to exist.

Tracking issue: [#3177](https://github.com/tomcounsell/ai/issues/3177).
Plan: [`docs/plans/recursive-self-improvement.md`](../plans/recursive-self-improvement.md).
Current state: [capability matrix](../plans/critiques/recursive-self-improvement-capability-matrix.md).
North star: [`docs/improvement-charter.md`](../improvement-charter.md), which Tom owns and only Tom edits.

## What exists today

Lanes 1 through 6 and lane 7's unit-3 budget work. The records, the settings, the evidence
collection tick, the verifying artifact store, eight dashboard panels, the evaluation harness
(`tools/improvement_eval/`, see [Improvement Evaluation](improvement-evaluation.md)), and the
release lane (`tools/improvement_release/` and `tools/improvement_recursion/`, see
[Improvement Release](improvement-release.md)): the six-state release lifecycle, the rollback
drill, exposure with a frozen baseline and a scored observation window, the promotion gate that
refuses and names both preconditions, the candidate-surface denylist, the recursive comparison of
two research processes behind an `ArmRunner` seam, the three-level claim report, and the
`valor-improve-release` binary.
Unit 3 (USD 50 per week for infrastructure) has a
meter with an admission gate, a teardown policy, a generated charter section 2 progress report, two new
evidence kinds (`spend_receipt` and `resource_probe`), and an artifact retention root outside the
checkout. The cloud sandbox itself is decided and unbuilt; see
[Improvement Cloud Execution](improvement-cloud-execution.md). Lane 3 ([#3215](https://github.com/tomcounsell/ai/issues/3215))
shipped the control journal, dispatch intents, the `admitted` session status,
the reconcile reflection, the unit-2 paid-inference meter, the vault writer,
and the `valor-improve` CLI, the only door through which a research session
writes anything. Lane 5 ([#3217](https://github.com/tomcounsell/ai/issues/3217))
shipped the reasoning half: the planner tick that opens cases from evidence
and ranks them, the immutable ranking snapshot, the investigation lifecycle,
the brief, the research skill, the retrieval-parameter experiment envelope and
the verdict-to-selection rule, the `lesson` and `promise` observer adapters,
the three-day assumption digest, three more dashboard panels, the
qualified-result report, and the production `ArmRunner` for lane 6's
comparison. See [Improvement Research Cycle](improvement-research-cycle.md).
Lane 3's paid-inference meter is what lets a comparison account unit 2 (the
parent plan's Gap D numbering: unit 1 is the subscription lane slot, unit 2
daily paid inference, unit 3 weekly infrastructure).

The first real cycle ran on 2026-09-15 and established charter §6 level 1,
"loop operational", and nothing higher; its report is posted on
[#3217](https://github.com/tomcounsell/ai/issues/3217#issuecomment-5680368659)
and its limits are stated in the research-cycle doc.

Read the capability matrix before believing anything is working. It grades each
component on four separate axes (implemented, deployed, measured, effect) and
"implemented" supports exactly one claim: the code exists.

## Three layers

**Research reasoning.** Bounded sessions that read evidence, revise the system's
model of itself, generate hypotheses with a mechanism and a falsifier, and
propose actions. Natural-language research instructions live in one narrowly
scoped skill (`.claude/skills/improve-research/SKILL.md`); authority lives in
code. The deterministic planner tick decides what a session is asked about,
and the session reaches state through `valor-improve` alone. Shipped in lane 5
(`reflections/improvement_plan.py`, `tools/improvement_*.py`); see
[Improvement Research Cycle](improvement-research-cycle.md).

**Control journal.** A small non-Popoto Redis namespace, `improve:{project}:{case}:*`,
whose Lua transition script is the sole authority for research-state
transitions, leases, dispatch intents, and budget reservations. The flat Popoto
records below are its queryable projection: every accepting `valor-improve` writer
(`propose`, `propose-amendment`, `pause --case`, `resume`) calls
`projection.apply` once its transition has committed, and `replay-projection`
reconciles on demand. Decisions read the journal head, never the projection.
Shipped in lane 3 (`tools/improvement_control/`).

**Existing execution infrastructure.** Jobs, AgentSessions, the reflection
scheduler, worktree and venv isolation, the SDLC pipeline, the content store,
and `tools/memory_eval/` statistics. Reused through public APIs and extended at
named seams, never forked.

## Three authorities

| Authority | May | May not |
|---|---|---|
| Research | Read scoped evidence, author hypotheses, request investigations, propose experiments within the charter | Enqueue sessions, contact anyone, amend the charter |
| Candidate | Modify declared candidate surfaces in isolation | Read holdout answers, alter release policy, touch production state, contact stakeholders |
| Evaluation and release | Run frozen contracts, write access-controlled verdicts under a separate process identity, propose a release from an `accept` verdict with its rollback and observation plans written first | Promote anything automatically. `tools/improvement_release/promotion.py::promotion_gate` refuses and names both unmet preconditions; `promote_automatically` raises on every call |

**A worktree is not a security boundary.** Until evaluator secrets and
production credentials are separated from candidate execution, and until a
human-amended charter names the reversible surfaces, automated promotion stays
disabled. Both are events outside this plan; the gate reads the pinned charter
row and its own constants, and no setting, env key, or file flag reads into it.

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
| `ImprovementRelease` | A qualified candidate put in front of a human. Six states (`proposed`, `approved`, `observing`, `accepted`, `rolled_back`, `withdrawn`), written only by `tools/improvement_release/lifecycle.py`. Beside the surfaces, exposure, rollback plan, and outcome it carries `kind`, `candidate_ref`, `base_revision`, `exposed_at`, `observation` (the plan), `rollback_drill` (the drill record), `drill_log` (the transcript, on the verifying store), and `promotion_gate` (the gate's answer at approval) | Immortal |

Evidence and investigations expire on `ReflectionRun`'s 30-day horizon: they are
high-volume observation rows and retrieval-dated external claims respectively,
and a cached claim outliving its retrieval date is worse than no claim. What
must outlive the window is distilled onto the case, which is immortal. The rest
are lineage: an evaluation nobody can trace back to what was actually run is not
a verdict, so expiring any of them would erase the comparison the system exists
to make.

Every one records its TTL decision in prose in its module docstring, and
`tests/unit/test_improvement_models.py` fails if a docstring stops saying so.

A ninth module, `models/improvement_controller_state.py::ImprovementControllerState`,
is the planner tick's cursor rather than a record of anything that happened:
one row per `project_key`, keyed by `project_key` alone, no index, no TTL,
written by ordinary `save()` because the control journal is keyed by case and
there is no case to fence a cursor against. It holds `last_snapshot_ref`,
`evidence_watermark`, `last_tick_at`, `charter_digest`, and
`digest_watermark`, and it is imported directly rather than through
`models.__all__`, which pins exactly eight `Improvement*` exports.

Lane 5's plain, unindexed fields on the records above: `ImprovementCase`
gains `evaluation_ids`, `rejected_reason`, `dedup_identity`, and `blocked_by`
(the exact shape `vault:{resource_name}`); `ImprovementInvestigation` gains
`stage`, `sources`, `prior_answers`, `expected_information_value`,
`decision_affected`, `assumption_detail`, and `resolved_at`;
`ImprovementModelRevision` gains `research_process_spec`;
`ImprovementExperiment` gains `notes`. `INVESTIGATION_KINDS` holds eight
values, `INVESTIGATION_STATES` five, and `EVIDENCE_KINDS` ten, each with its
cardinality argument in the model docstring.

## Evidence collection

`reflections/improvement_collect.py` runs five observer adapters on a 900-second
tick. Each is independently fail-soft; one broken source degrades the tick
rather than ending it. The tick keeps `failed` (an adapter raised) and
`skipped` (an adapter declined by rule) as separate lists, and reports
`status="error"` only when every adapter failed.

| Adapter | Reads | Writes |
|---|---|---|
| `collect_corrections` | Inbound `AgentSession.chat_message_log` turns and Tom-sourced `Memory` rows, via `reflections.utilities.CORRECTION_PATTERNS` | One `correction` row per session that needed correcting, plus one per correcting memory |
| `collect_inspirations` | `Memory` rows with `source="human"` (the links Tom sends) | One `inspiration` row per memory, preserving the text, its reference, and its date |
| `collect_expectation_coverage` | Open outbound expectations on Jobs | One `owner_liveness` row per gone owner, plus one coverage row per tick |
| `collect_lessons` | Merged PR bodies over the last 14 days through `gh pr list`, the seven `LESSON_PREFIXES` lines (`- lesson:`, `- pattern:`, `- note:`, `- convention:`, `- learning:`, `- reminder:`, `- caveat:`) | One `lesson` row per flagged line, `source_ref="pr:{number}:{sha256(line)[:16]}"`, with the PR title and a `stage_guess` in `detail` |
| `collect_promises` | Outbound `AgentSession.chat_message_log` entries (`bridge/telegram_relay.py::_append_outbound_chat_log`), up to 10 newest unjudged per tick, judged by a cheap model under the charter §10 paragraph | One `promise` row per `yes`, with the judge's quoted span and confidence. Metered under `purpose="promise_detector"`; gated by `promise_detector_enabled`, off by default |

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
detector keyed on it can never fire (measured at 0 of 58 rows on the machine
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
called from `scripts/update/run.py` at Step 1.6585, **before** Step 1.66's
vault→config copy, so the entry propagates into this machine's
`config/reflections.yaml` on the same cycle. Registration writes the vault file
`~/Desktop/Valor/reflections.yaml`; writing the config copy instead would be
erased by the very next `/update`.

Machine pinning is inherited from `register_reflection`'s existing
`_this_machine_owns_valor` guard, not re-implemented, so only the machine that
owns the `valor` project schedules the tick.

The same file registers the planner tick (`register_improvement_planner`,
`improvement-planner-tick`, cadence `controller_tick_seconds`) and the
assumption digest (`register_improvement_assumption_digest`,
`improvement-assumption-digest`, `259200s`), beside lane 3's
`improvement-controller-tick` and `improvement-intent-reconcile`. All five
improvement reflection entry points resolve the owning project through
`reflections.redis_access.get_project_key()` (`VALOR_PROJECT_KEY`, fallback
`valor`), the key `valor-improve` is bound to.

### The kill switch

`ImprovementSettings.enabled` (`IMPROVEMENT__ENABLED`) gates every write in
`reflections/improvement_collect.py`, the planner tick, and the assumption
digest. False, the default, means each entry point returns `status="skipped"`
and writes nothing, which is exactly what `config/settings.py` and
`.env.example` promise it means. So an `/update` that registers the
reflections does not, by itself, start a 15-minute writer against production
Redis.

Registration is independent of the switch, so turning collection on is a single
`IMPROVEMENT__ENABLED=true` in the vault `.env` on the machine that owns the
project with no re-registration, and evidence starts accumulating from that moment.
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
| `controller_tick_seconds` | `900` | Cadence of the evidence, controller, and planner ticks |
| `lease_ttl_seconds` | `90` | TTL of the interim case lease; the reconcile pass's staleness threshold is four times it |
| `journal_max_entries` | `1000` | Entries the per-case journal list retains |
| `max_dispatch_attempts` | `3` | Stale sweeps an `admitted`/`materialized` intent survives before `reconciliation_required` |
| `promise_detector_enabled` | `False` | Whether the `promise` adapter runs; off because each sampled message costs a judge call. `IMPROVEMENT__PROMISE_DETECTOR_ENABLED` |
| `cheap_inference_model` | `""` | The OpenRouter model id the promise judge runs on; empty falls back to `config.models.OPENROUTER_GEMMA4_FREE`. `IMPROVEMENT__CHEAP_INFERENCE_MODEL` |

Three budget units, reserved separately, and one of them is not money: Claude
work runs on the subscription and is budgeted as lane concurrency. The window
boundaries are settings rather than assumptions because charter §8 requires them
disclosed: a reservation that resets on an undisclosed boundary cannot be
audited against what was actually spent.

**Both units now have a meter.** `tools/infrastructure_budget.py` admits and refuses
against `weekly_infrastructure_usd` (see Unit-3 metering and teardown below). `tools/paid_inference_meter.py`
(lane 3) mirrors its shape for `daily_paid_inference_usd`: reserve-then-check in one Lua `EVAL` on a
plain `improve:{project}:budget:unit2:{day_key}` key, settling from `response.usage.cost` when a call
carries it (`metering="exact"`) or a dated price table otherwise (`metering="estimated"`). Settle
and release are each one Lua call under a CAS on the reservation's `state`, so a repeated call
after a mid-write crash moves the cents exactly once in either direction. Only
`purpose="rsi"` reservations count against the pool; `tools/cross_vendor_judge.py`'s ordinary review
spend is receipted `purpose="sdlc_review"` and never gated. Uncertain metering is not zero cost: a
reservation whose day window closes unsettled is receipted `metering="unknown"` by the reconcile pass,
never defaulted to zero.

**There is no fixed allocation across areas.** Ranking is by expected
contribution to the north star (charter §3), recorded per case as
`ranking_rationale` beside the `priority_area` and the charter digest it was
ranked under. The eleven-value `PRIORITY_AREAS` vocabulary is a classification,
not a quota.

**There is no `daily_question_ceiling`.** Charter §9 forbids routine research
questions: the controller resolves uncertainty from Tom-sourced memories and
online research, and records what it cannot resolve as a provisional assumption
with its evidence, shown on the dashboard as an assumption rather than a fact.
One message class is permitted, the evidence-backed charter amendment request:
`valor-improve propose-amendment` records it as a `charter_amendment`
investigation in `awaiting_authorization` and pages Tom once. A Verification row
fails the build if a routine question path reappears.

## Unit-3 metering and teardown

`tools/infrastructure_budget.py` is unit 3's meter and admission gate. It reads three settings and only
three: `weekly_infrastructure_usd`, `budget_week_start`, and `budget_day_boundary`. It never reads the
paid-inference pool, so charter section 8's no-transfer rule holds by construction.

**Window computation.** `current_window` derives `(window_key, window_start, window_end)` from the
moment, the week-start convention (Monday 00:00 UTC by default, Sunday 00:00 UTC when
`budget_week_start` is `"sunday"`), and a 7-day length. The key derives from the window start with a
`-sun` suffix under the Sunday convention, so the two conventions never share a counter. The forecast
horizon is the remainder of the current window plus the whole next window, prorated from the resource's
weekly rate at its duty cycle. Every admission decision discloses the window key, both boundaries, the
forecast, and remaining headroom, and refusals land as ledger rows, so "no forecastable rate" and "week
exhausted" stay distinguishable states.

**The window counter lives outside Popoto.** Admission reserves against the plain Redis string key
`improvement:budget:unit3:{window_key}` with one Lua `EVAL` (reserve-then-check in a single atomic
step). The placement is deliberate: Popoto offers no compare-and-set, so a counter modeled in the ORM
could not be reserved atomically, and a non-Popoto key sits outside the "never use raw Redis on
Popoto-managed keys" rule. Decision records (reservations, refusals, releases, settlements) are
`InfrastructureReservation` Popoto rows read and written through the ORM; only the counter is raw, and
only because atomicity requires it. Every reservation has a paired, idempotent release. The window key
expires past the audit horizon (the 30-day evidence TTL plus one window of margin), never at the window
length, since a counter expiring mid-window would reset headroom to full. **Lane 3 declined the migration**
(its plan's No-Gos): moving a live counter mid-window buys a naming consistency and risks a double-count.
Unit 3 stays on its own key; `valor-improve budget` reads it through `tools.infrastructure_budget.status_dict`
beside units 1 and 2.

**Admission refuses what it cannot forecast.** A `None`, empty, non-numeric, negative, NaN, or infinite
rate is refused, never defaulted to zero. A credit with no expiry covers the current window only. A
resource whose provider reports nothing settles at its forecast, with a logged warning. The trial's
live-window stop reads `max(settled, forecast)`, since a per-second metered provider settles after the
fact and a stop keyed on settled spend alone fires after the money is gone.

**Teardown ladder.** A budget-exhausted window closes admission, classifies each running resource as
`trial` (an open experiment still gathers evidence from it, or a claimed-and-unfinished session runs on
it) or `standing`, tears down `standing` resources at window end, and lets `trial` resources run to a
bounded horizon with the continuation booked as a forecast overrun against the next window before it
accrues. Every teardown is gated on a verified evidence export that fails closed: an unverified export
leaves the resource running and writes an escalation `spend_receipt` row. A teardown that cannot be
confirmed is treated as still running and still charging.

**Operating report.** `tools/improvement_operating_report.py` generates charter section 2's five answers
from records: which sessions ran in cloud sandboxes, whether the loop continues unattended, what
resources sustain it, what they cost against unit 3 with both window boundaries disclosed, and what
still prevents mostly-cloud operation. Each answer degrades independently, and the fifth is assembled
from recorded assumptions, `unknown` probe entries, and refused acquisitions, so it stays non-empty
while any of those inputs is non-empty. Sandbox count, uptime, and token volume are deliberately absent.

## Charter

`docs/improvement-charter.md` is the north star. Tom owns it and only Tom edits
it; nothing in `models/`, `tools/`, `reflections/`, or `ui/` writes that file.
`tools/improvement_release/denylist.py` refuses it, and `models/improvement_charter.py`,
as a candidate surface before a release row exists.

`ImprovementCharter.load_from_file` projects the file into a record. It digests
the bytes with the same CRLF-to-LF normalization `tools/sdlc_verdict.py::compute_plan_hash`
applies, so the same charter digests identically on any checkout. It **refuses**
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

`improve:{project_key}:*`, with an explicit schema version (`tools/improvement_control/keys.py`),
holding only what is research-specific: the case head and its journal, dispatch
intents, lane-slot reservations, the case lease, and the unit-2 window.

- `{case_id}:head` holds `{revision, state, epoch, highest_accepted, owner, updated_at,
  paused, pause_reason}`; `{case_id}:journal` is a bounded list of
  `{revision, action_id, event, payload_digest, generation, action_type, artifact_ref, ts}`,
  `LTRIM`med to `journal_max_entries`. **Head `state` contract:** the head is the
  authority for case lifecycle state and its `state` field has exactly two writers,
  both inside the transition script. The `state_changed` event
  (`journal.set_state(project_key, case_id, generation=, state=, by=)`, the lifecycle
  owner's one door) sets it to the event's payload; every other accepted transition
  seeds it from the `ImprovementCase` row's own `state` only while the stored value is
  empty, so a head written before its row existed is re-seeded on its next accepted
  write. `projection.apply` and `replay` copy `state` and `revision` onto the row and
  never write `state` from an empty head; a direct ORM save of `state` is overwritten
  by the next apply. `{case_id}:intents` is the per-case set of action ids
  (the one index every reader — `resume --force`, `doctor`, `case explain`,
  `get_control_status` — enumerates through; no reader ever runs `KEYS` or `SCAN`).
  `{case_id}:intent:{action_id}` is one dispatch intent (Decision 13's six-state
  machine: `admitted → materialized → running → settled`, with
  `reconciliation_required` as a holding state whose only exit is `cancel`).
- `tools/improvement_control/journal.py::transition()` is one Lua `EVAL` per Decision 3:
  schema check, namespace/case pause, `generation >= highest_accepted`, `expected_revision
  == revision`, and — when a research session presents its own `agent_session_id` — the
  intent-binding compare (`intent.state == "running" and intent.agent_session_id ==
  ARGV.agent_session_id`) inside the same call as the head advance. Every reason
  (`SCHEMA_MISMATCH`, `PAUSED`, `STALE_GENERATION`, `REVISION_MISMATCH`, `INTENT_STATE`,
  `SLOT_EXHAUSTED`, `UNAVAILABLE`, plus the Python-side `INVALID_ARGUMENT` and
  `NOT_A_RESEARCH_SESSION`) is a return value; no caller sees an exception, and a
  connection error becomes `UNAVAILABLE` rather than a fallback to the projection.
- Leases: `tools/improvement_control/lease.py::LeaseProtocol` matches
  [#3220](https://github.com/tomcounsell/ai/issues/3220)'s declared session-execution-lease
  interface (`acquire`/`renew`/`release`, a monotonic generation, Redis-server-time expiry),
  implemented as `CaseLease` — an interim module confined to the `improve:` prefix — until
  #3220 lands, at which point `default_lease()` (the single swap point) repoints at
  `models.redis_lease` and `CaseLease` is deleted. The hand-off is posted on #3220. The accept
  rule `generation >= highest_accepted` lives in the journal, not the lease, so it holds
  independent of which lease minted the generation.
- **Two fences, one per kind of writer.** Controllers (the scheduler tick, the reconcile
  pass, an operator's `pause`/`resume`, the CLI's own `propose` write) each acquire the case
  lease and present the generation it minted; a stalled controller's late write is refused
  `STALE_GENERATION`. A research session presents no generation at all — one copied at
  dispatch time would be stale the moment the adapter's own next write lands — and is fenced
  instead by its **intent binding**: `intent:{action_id}` in state `running` with
  `agent_session_id` equal to the session's own id, checked inside the same `transition` call
  as the head advance.
- Every effect script re-checks the generation and revision fence and appends its own journal
  entry in the same call. A lease check followed by an unguarded effect is not fencing; the
  mutation-review round (lane 3's Task 12) confirmed a named test fails for each of the
  generation, revision, `from_state`, and compare-and-delete checks across every script.
- Payloads are written to `VerifyingArtifactStore` and hashed before `propose` references
  their digest on the journal entry (`artifact_ref` alongside `payload_digest`). A refused
  proposal's artifact reference is kept as `ImprovementEvidence(kind="other",
  text="intent_state:<reason>", detail=<artifact_ref>)` rather than lost. A store write
  that fails (`OSError`: full disk, missing content root) is refused
  `ARTIFACT_WRITE_FAILED` before the lease is taken, never a traceback.
- `import` refuses `FOREIGN_KEY` before writing anything when the archive's `project_key`
  differs from the target or its unit-2 section names a key outside
  `improve:{project_key}:budget:unit2:`; every restored key goes through the package's
  own key builders, unit-2 hashes get their 30-day retention TTL re-applied, and
  `--force` deletes the namespace slot and pause hashes and each archived case's
  journal, intents set, and intent hashes before restoring them, so a forced restore
  replaces history rather than appending to it (a slot admitted after the export is
  dropped with the intent that held it, never stranded with no release path).
- `budget` prints `metering="unknown"` receipts in their own block (`unit2_receipted_unknown`
  in `--json`, each with the `day_key` window it was charged to), read from
  `ImprovementEvidence(kind="spend_receipt")` rows, so an unknown never reads as zero.

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
session; sessions carry `research_case_id`, `experiment_id`, `action_id`, and
`idempotency_key` as provenance in `extra_context` — deliberately never a
`generation`, since one copied at dispatch time is stale by the adapter's own
next write.

Only `tools/improvement_control/scheduler_adapter.py::tick()` (the
`improvement-controller-tick` reflection) admits and dispatches research sessions,
after checking the case's `intents` set for a `reconciliation_required` wedge (before
the slot count — a wedged case never consumes a slot), the allowed action type, the
lane-slot count, and the generation/revision fence. It materializes through
`_push_agent_session(idempotency_key=..., status="admitted", ...)` — the create-or-bind
seam #3183 shipped, unchanged — and activates only once
`agent.session_health.any_worker_alive()` is true, re-reading the row fresh and branching
on what it actually finds (an `admitted` row flips to `pending` and publishes a wake; a
`pending` row from a retry republishes without flipping; a terminal row settles through
the same terminal hook the worker uses; a missing row is left for the reconcile pass). The
publish (`agent.agent_session_queue.publish_session_notify`) runs strictly after the row is
`pending`, so the worker's own pickup loop finds it within one notify hop. The dispatched
session's `message_text` is `/improve-research case=... action=... type=...` with the
checkout's absolute path as `working_dir`; when the adapter runs from a lane worktree
(`.worktrees/<slug>`) it also passes that slug, because the executor gives a slugless eng
session a fresh worktree of its own and refuses a pre-provisioned one on another branch
(#1377). When the proposal's payload is in the verifying store the message also carries
`brief_ref=<$CF: reference>`, which the skill loads through
`VerifyingArtifactStore().load(ref)`.

`_push_agent_session` returns the row's `agent_session_id`, the worker exports that same
id as `AGENT_SESSION_ID`, and the adapter's activation, the reconcile pass, and the CLI's
own session lookup all resolve the bound row through `AgentSession.get_by_id` on it.

A research session writes only through `valor-improve`. `propose` resolves the session's
own row through `AGENT_SESSION_ID`, presents its intent binding, and writes one
`action_proposed` journal event; the investigation, model-revision, experiment, and
report subcommands lane 5 added take the same door
([Improvement Research Cycle](improvement-research-cycle.md)). `finalize_session` gains
one post-transition hook (step 7, gated on `extra_context.action_id`, lazy-imported,
exception-isolated) that releases the session's lane slot and settles its intent by
outcome on every termination path.

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
(`POPOTO_IMPROVEMENT_CONTENT_PATH`, defaulting to `~/.popoto/improvement_content`) so
retention and export policy for evaluation evidence can differ from ordinary
content without a path heuristic. The default sits outside any repo checkout, beside the shared
popoto content directory, so an image rebuild cannot destroy gathered artifacts. The archive path is
`.versions/{prefix}/{hash}{ext}` under the root. `valor-improve export` and `import` write against
this destination contract, and lane 5's ranking snapshots live here as `ImprovementRankingSnapshot/`; see [Improvement Cloud Execution](improvement-cloud-execution.md)
for the sandbox-local Redis topology and the export path behind it.

## Dashboard

Eight panels on the root dashboard.

**Goals** is the charter §11 readable record: which charter version and digest
the work is ranked under, the §3 early priorities marked as starting hypotheses
rather than an allocation, open cases with their `priority_area`,
`ranking_rationale`, and ranking position (read from `get_ranking` when a
snapshot exists), and unresolved assumptions. Each section renders one of
three distinguishable states: content, "nothing yet", or "unavailable" when
the read failed. A bare zero would claim a measurement was taken.

Two panels are backed by `ImprovementEvidence`.

**Coverage** comes first and is the denominator. A falling correction count with
a falling scan count is not an improvement, and coverage is what makes the two
distinguishable. When the collection tick has gone quiet the panel says every
count below it is a count of nothing, rather than showing a comforting zero.

**Intervention burden** splits corrections by classification and publishes the
raw count beside every share. `unknown` is expected to be the largest bucket.
An empty window reads as "nothing observed", never "nothing happened".

**Control** (lane 3, `get_control_status`) shows dispatch intents by state, lane-slot
usage, paused heads, `reconciliation_required` wedges (each naming its clearing
`valor-improve resume --force` invocation), and unit-2 spend for the window open now —
read through `intents.list_intents` over each open case's own set, never a keyspace scan.

**Releases** (`/_partials/improvement/releases/`) renders release lineage: one
row per release joined to the evaluation that qualified it, the drill that
exercised its rollback plan ("drilled (worktree)"), the observation window, and
the outcome scored when the window closed, plus the promotion gate as a
sentence on every load. See [Improvement Release](improvement-release.md).

**Ranking** (`/_partials/improvement/ranking/`, `get_ranking`) renders the
latest ranking snapshot: each case's position, factors, `blocked_by`, and
movement against the previous snapshot, the cases that left and why, and the
intake pool. A snapshot that does not verify renders as "unavailable" with the
integrity error, never as an order.

**Hypotheses** (`/_partials/improvement/hypotheses/`, `get_hypotheses`)
renders experiments in `proposed`, `frozen`, and `running` with hypothesis,
mechanism, falsifier, contract digest, and `frozen_at`.

**Rejected approaches** (`/_partials/improvement/rejected/`,
`get_rejected_approaches`) renders `rejected` cases with `rejected_reason`,
the evaluation's verdict with effect and interval per endpoint, and the
snapshot in which the case left the order.

`ui/data/improvement.py` exports exactly nine getters (`get_control_status`,
`get_coverage`, `get_goals`, `get_hypotheses`, `get_intervention_burden`,
`get_provisional_assumptions`, `get_ranking`, `get_rejected_approaches`,
`get_release_lineage`), and `tests/unit/test_ui_app.py::test_dashboard_never_offers_experiment_or_patch_counts`
pins that list as an exact list and asserts no result carries an activity
counter. **Experiment count and merged-patch count are activity, not
improvement**, and there is deliberately no function here that returns them.

## Research cycle

The planner tick, the ranking snapshot, the investigation lifecycle, the
brief, the research skill, the experiment envelope, the verdict-to-selection
rule, the observer adapters, the assumption digest, the qualified-result
report, and the first real cycle's record are documented in
[Improvement Research Cycle](improvement-research-cycle.md).

## Break-glass

The CLI, `.venv/bin/valor-improve` (Decision 14: it lives in the venv only, never on
system PATH), is the only door through which a research session — or an operator —
touches the control namespace.

**Is it a namespace outage or a wedged case?** Run `valor-improve doctor`. It
prints every paused head, every `reconciliation_required` intent, and every
outstanding reservation: each held unit-1 slot from `_ns:slots` named with the case
whose live intent holds it (a slot with no live intent is printed as such, since no
release path can reach it), plus the open unit-2 window's reserved amount when it
is above zero. `--json` carries these under `reservations` (`slots`, `unit2`). So
you see the blast radius before touching anything. `namespace unreachable: <error>`
with exit code 2 means the substrate itself is down (the slot and unit-2 reads sit
under the same guard); a clean namespace prints "no paused heads, no stale intents,
no outstanding reservations" only when all three views are empty, rather than a
bare success with nothing after it.

- **`doctor` cannot read the namespace**. That is a Redis or connectivity
  problem, not a wedged case. Fix the substrate; `doctor` again once it is up —
  every head reads as it was, since nothing was written from the projection.
- **A `reconciliation_required` intent, head not paused (the common shape)**.
  `mark_reconciliation_required` never writes the head, so this is the ordinary
  wedge signature, not a paused case. Run `valor-improve case explain --case ID`
  — it names the blocking action id and the exact clearing command.

**Commands.**

| Command | Effect |
|---|---|
| `valor-improve doctor` | Print paused heads, `reconciliation_required` intents, outstanding reservations |
| `valor-improve case show --case ID` | The head, its revision, and the journal tail |
| `valor-improve case explain --case ID` | Why the case is where it is: state, pause reason, last event, every intent, whether the charter is pinned, and the blocking action ids with their clearing command |
| `valor-improve pause [--case ID] [--reason TEXT]` | Pause explicitly (whole namespace when `--case` is omitted). A pause is never self-clearing |
| `valor-improve resume --case ID [--force]` | Scans for `reconciliation_required` intents **before** consulting `paused` (Decision 9) — the ordering that makes the common unpaused wedge actually exit. Without `--force`, blocking intents print and exit 1 with no write. With `--force`, cancels each (`intent_cancelled` journaled), then clears a pause if one exists, else prints "not paused; cancelled N intent(s)" |
| `valor-improve budget` | All three units — slots in use, unit-2 window, unit-3 window — with boundaries disclosed |
| `valor-improve export [--root PATH]` / `import --archive PATH [--force]` | Dump/restore the namespace against lane 7's export-root contract |
| `valor-improve replay-projection --case ID` | Reconcile `ImprovementCase` to the head, folding the journal tail as a cross-check (reported, never raised, when `LTRIM` has trimmed past what the fold can verify) |

**Evidence to keep before resuming:** the journal tail, the `doctor` output, and
the reservation state. `resume` advances the head; the pre-resume state is not
recoverable from the projection.

**Stale intents recover on their own cadence.** The `improvement-intent-reconcile`
reflection (300s, `tools/improvement_control/recovery.py::reconcile()`) is itself a
controller: it acquires each case's own lease before touching its intents. `admitted`/
`materialized` intents past `4 * lease_ttl_seconds` get their `stale_sweeps` counter
incremented (a pass-owned counter, distinct from `record_materialized`'s `attempts`);
at `max_dispatch_attempts` sweeps the intent moves to `reconciliation_required`, its
slot releases, `intents.dead_letter_exhausted` writes the one `improve_intent` dead
letter (`replayable=False`, on every branch, bound row or none), and a still-live bound
row is forced `abandoned` through the existing `finalize_session()`. A `running`
intent whose bound row is missing or already terminal is acted on the **first**
qualifying sweep — no budget applies, since the session holding the slot is already
gone. It never mints a second identity, and it reads intents rather than a status
map, so it does not depend on `RECOVERY_OWNERSHIP`, which is explicitly an
informational constant, not used for runtime routing. The same pass receipts any
unit-2 reservation whose day window closed unsettled at `metering="unknown"`.

## Dependency on #3183

[#3183 (ETL-grade pipeline hardening)](https://github.com/tomcounsell/ai/issues/3183)
owns three primitives this system consumes rather than redesigns:

| Primitive | Owner | Consumed by |
|---|---|---|
| Idempotent create-or-bind seam on `_push_agent_session` | #3183, PR #3229 (merged) | Lane 3 dispatch. `status="admitted"` is a caller argument, not a seam change |
| Renewed execution lease with a fencing generation (`models/redis_lease.py`) | [#3220](https://github.com/tomcounsell/ai/issues/3220), a child issue of #3183 (**open**) | `LeaseProtocol`'s single swap point (`default_lease()`); until it lands, `CaseLease` is the interim implementation, confined to `improve:*` keys and deleted the moment `models/redis_lease.py` exists |
| Generalized `DeadLetter` record with a `stage` field | #3183, PR #3229 (merged) | Exhausted improvement intents write `stage="improve_intent"` (reserved for this lane in `bridge/dead_letters.py`) |

Lane 3 shipped against the two merged primitives and forked no second lease for the
still-open third (see Break-glass and the Control namespace contract above for the
hand-off). It opens no PR against `agent/agent_session_queue.py` and defines no second
dead-letter sink.

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

`scripts/sdlc_reflection.py`, its installer, and `com.valor.sdlc-reflection.plist`
are gone: lessons flagged in merged PR bodies enter the loop as `lesson`
evidence rows written by `collect_lessons`, read by the planner. The
`retire_sdlc_reflection` migration removes `data/sdlc_reflection_last_run.json`,
and `/update`'s service sweep boots the launchd job out by exact label on every
machine that ever installed it. The "Reflection Notes (auto-generated)"
sections it appended to `docs/sdlc/*.md` stay as they are.

## Claim levels

The plan's yardstick, restated so nobody grades on a curve:

1. **Loop operational**: one complete autonomous investigation-to-measurement cycle.
2. **System improvement demonstrated**: held-out and production gains over incumbent.
3. **Recursive improvement demonstrated**: a changed research process produces
   greater validated gains per comparable total budget on fresh opportunities.

Repeated edits satisfy none of the latter two. This is system-level recursive
improvement with externally supplied models; model-weight training and claims of
unbounded acceleration are out of scope.

## See also

- [Improvement Research Cycle](improvement-research-cycle.md): planner tick, ranking snapshot, investigations, brief, research skill, experiment envelope, verdict rule, observer adapters, assumption digest, qualified-result report, the first real cycle
- [Improvement Evaluation](improvement-evaluation.md): contract fields, manifest, judge envelope, statistics, holdout policy
- [Improvement Release](improvement-release.md): release lifecycle, rollback drill, observation window, promotion gate, denylist, recursive comparison, claim report, `valor-improve-release`
- [Improvement Cloud Execution](improvement-cloud-execution.md): provider decision, auth verdict, sandbox topology, host updates, evidence path
- [`docs/plans/recursive-self-improvement.md`](../plans/recursive-self-improvement.md): the full design
- [Capability matrix](../plans/critiques/recursive-self-improvement-capability-matrix.md): what is implemented, deployed, measured, and unknown
- [Adding Reflection Tasks](adding-reflection-tasks.md): how the collection tick is registered
- [Redis Models](redis-models.md): the schema-gate rules the eight records follow
