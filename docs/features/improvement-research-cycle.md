# Improvement Research Cycle

The reasoning half of the improvement controller: the planner tick that opens
cases from evidence and ranks them, the research session that investigates
one case through `valor-improve`, the experiment it freezes inside a fixed
envelope, the verdict that changes the next selection, and the report that
says what the cycle established and what it did not.

Tracking issue: [#3217](https://github.com/tomcounsell/ai/issues/3217).
Companion to [Improvement Controller](improvement-controller.md) (records,
control journal, dispatch, the base CLI), [Improvement Evaluation](improvement-evaluation.md)
(the harness the experiment runs on), and [Improvement Release](improvement-release.md)
(what consumes an `accept`). North star: [`docs/improvement-charter.md`](../improvement-charter.md).

## What exists

| Module | Owns |
|---|---|
| `reflections/improvement_plan.py` | `plan_tick`, `open_cases`, `propose_one_action`, the verdict backstop, and `run_improvement_planner` (the `improvement-planner-tick` reflection) |
| `tools/improvement_ranking.py` | `rank`, the five factor rules, `write_snapshot`, `load_snapshot`, `latest_snapshot`, `compute_diff`, `process_spec_json`, `cmd_ranking` |
| `tools/improvement_investigations.py` | `open_investigation`, `record_claims`, `resolve`, `transition_investigation`, `resolve_awaiting_on_new_digest`, `disposition_of`, the assumption guard |
| `tools/improvement_brief.py` | `build_brief`, `BRIEF_TEMPLATE`, `brief_template_digest` |
| `tools/improvement_experiment.py` | `propose_experiment`, `validate_candidate`, `freeze_experiment`, `evaluate_experiment`, `apply_verdict`, `repair`, `show_experiment` |
| `tools/improvement_report.py` | `build_report` and `is_seeded` |
| `tools/improvement_plan_arm.py` | `PlannerArmRunner`, the planner tick as one of lane 6's comparison arms |
| `reflections/improvement_assumption_digest.py` | `run_improvement_assumption_digest` (the `improvement-assumption-digest` reflection), `render_digest`, `on_escalation` |
| `reflections/improvement_collect.py` | The `lesson` and `promise` observer adapters beside the three that precede them |
| `models/improvement_controller_state.py` | `ImprovementControllerState`, the tick's cursor row |
| `ui/data/improvement.py` | `get_ranking`, `get_hypotheses`, `get_rejected_approaches` |
| `.claude/skills/improve-research/SKILL.md` | The research session's own skill, project-only |
| `.claude/skills/improve-preflight/SKILL.md` | The read-only pre-freeze check (doctor, budget, calibration floor), project-only |

Every write a research session makes goes through `valor-improve`
(`tools/improvement.py`); the subcommands are listed in
[`docs/tools-reference.md`](../tools-reference.md#improvement-controller-valor-improve).

## The planner tick

`run_improvement_planner` is a function reflection registered as
`improvement-planner-tick` at `ImprovementSettings.controller_tick_seconds`
(default 900). It resolves the owning project through
`reflections.redis_access.get_project_key()` (`VALOR_PROJECT_KEY`, fallback
`valor`), which is the key `valor-improve` is bound to, and it returns
`status="skipped"` with zero writes while `ImprovementSettings.enabled` is
`False`. Its core, `plan_tick(project_key, ...)`, is pure controller logic: no
LLM call, no dispatch, no evaluation. Judgment enters the loop only in the
research session.

The steps run in this order. Each is wrapped independently; a step that raises
becomes a `<name>-failed` finding and `status="error"`, a later step that
depends on it is skipped, and a finding with no failed step is
`status="partial"`.

1. **Charter.** `ImprovementCharter.load_from_file` then `pinned`. No pinned
   charter ends the tick with `status="error"` and nothing written; a case with
   no digest cannot be ranked or proposed.
2. **Keep-alive and unblock.** Every `awaiting_authorization` investigation and
   every `resource_acquisition` investigation with disposition
   `vault_request_written` gets a `save()` so its 30-day TTL restarts. When
   some open case carries `blocked_by`, `tools.improvement_resources.probe()`
   runs once; a case whose resource reports `verified` has its block cleared
   after a `case_unblocked` journal event. When the pinned digest differs from
   the one the last tick recorded, `resolve_awaiting_on_new_digest` resolves
   the awaiting amendment rows.
3. **Open cases** (`open_cases`, below).
4. **Rank and snapshot** (below). `ranking_recorded` is journaled once per case
   whose rank moved, on that case's own journal.
5. **Cursor.** The snapshot reference, the tick time, the charter digest, and
   the evidence watermark are written to the `ImprovementControllerState` row
   by ORM `save()`.
6. **One proposal** (`propose_one_action`, below).
7. **Verdict backstop.** Every `complete` evaluation whose case still reads
   `evaluating` and whose id is absent from the case's `evaluation_ids` is
   handed to `apply_verdict`; `tools.improvement_experiment` is imported inside
   the step.

**The cursor row.** `ImprovementControllerState` (`models/improvement_controller_state.py`)
holds one row per `project_key`, keyed by `project_key` alone, with plain
fields `last_snapshot_ref`, `evidence_watermark`, `last_tick_at`,
`charter_digest`, and `digest_watermark`. It has no index and no TTL: an
expiring cursor would make the first tick after a quiet month write a snapshot
whose diff names every case as `entered`. It is written by ordinary `save()`,
never through the control journal, because lane 3's journal is keyed by case
and there is no case to fence a tick cursor against.

**Lease-fenced journal events.** Every per-case event the tick writes
(`case_opened`, `evidence_attached`, `evidence_attached_to_rejected`,
`investigation_opened`, `case_unblocked`, `ranking_recorded`) mints its
generation from that case's own lease:
`default_lease().acquire(keys.lease_key(project_key, case_id), ttl=settings.improvement.lease_ttl_seconds)`,
then `transition(...)` against the head's current revision, then release in
`finally`. A held lease is a `lease_busy:<case_id>` finding and the write is
skipped this tick, never made with a fixed generation. A `PAUSED` refusal from
`transition()` (lane 3's break-glass, namespace or case) is recorded as
`paused:<case_id>` and the case is skipped for the tick; the tick reads no
pause hash itself. The tick moves no case state: a new case is `observed`,
`case_unblocked` changes no state, and state moves belong to `apply_verdict`.

**One proposal per tick.** `propose_one_action` walks the ranked order and
proposes for the first case that is unblocked, unpaused, and has no intent in
`admitted`, `materialized`, or `running`. The action is `experiment` when a
`proposed` experiment exists for the case and no `frozen` or `running` one
does, `investigate` when the case is `observed` or `investigating`, and nothing
otherwise (a case at `evaluating` with an `aborted` experiment gets no
proposal). The proposal goes through `tools.improvement.cmd_propose`, so lane
3's scheduler adapter admits it, reserves the lane slot, and dispatches the
research session; the tick never dispatches. The action id is
`sha256(case_id + snapshot_ref + action_kind)[:16]`, so a re-run of the same
tick computes the same id and finds its own un-admitted proposal already last
on the journal. An intent hash in any state, cancelled included, reads to the
adapter as already admitted, so after a cancelled dispatch
`action_id_for(..., attempt)` mints the first successor id no intent on the
case has spent. A proposal the CLI refuses (`CHARTER_DIGEST_STALE`,
`MISSING_RANKING_RATIONALE`, a `ranking_recorded` refusal on the same case) is
a finding and nothing is written.

### Case opening

`open_cases(project_key, charter)` clusters unconsumed evidence, not a time
window. A row is pending when its id is absent from every case's
`evidence_ids` and from every intake investigation's `sources[*].evidence_id`;
`evidence_watermark` is only a scan bound (rows newer than
`watermark - EVIDENCE_TTL`, capped at `EVIDENCE_SCAN_LIMIT = 500`), so a
cluster that accrues one row per tick still reaches two rows.

Only `correction`, `lesson`, `promise`, and `inspiration` rows cluster. The
dedup identity is the `classification` plus the normalized first eight words
of `text` for a correction, the `stage_guess` plus the same prefix for a
lesson, the constant `unqualified-promises` for every promise (one case,
growing evidence), and `inspiration:` plus the prefix for an inspiration with
no URL. A cluster opens a case at `CASE_OPEN_MIN_EVIDENCE = 2` rows or a
single `architectural` correction, with every row id consumed in the same
`save()`.

Two rows take special routes before clustering:

- **Seeded rows (cold start).** A row whose `detail` parses as a JSON object
  carrying a `seed` key and a `priority_area` in `PRIORITY_AREAS` is a one-row
  cluster that opens a case in that area with
  `dedup_identity=f"seed:{seed}"`, bypassing both the URL route and the
  minimum. A seeded row lacking a valid `priority_area` takes the ordinary
  route.
- **Inspirations with a URL.** These open an `inspiration_intake`
  investigation (`stage="draft"`, the URL and the row id in `sources`), never
  a case, and the tick never proposes for an investigation. The intake pool
  is worked by a research session already dispatched for a case in the same
  `priority_area` (the brief lists it), whose `resolve()` may call
  `open_cases` with the extracted substance. Until then the snapshot's
  `intake_pool` shows it waiting.

The `priority_area` rule table: an `architectural` correction opens in
`orchestration` and any other correction in `other`; a `lesson` for
`do-build`/`do-patch` opens in `orchestration`, for `do-test`/`do-pr-review`
in `evaluators`, for `do-plan`/`do-plan-critique` in `research_process`, for
`do-docs`/`do-merge` in `other`; a `promise` opens in `personas`; an
inspiration cluster whose `source_ref` starts with `memory:` opens in
`memory`; a seeded row opens in the area its `detail` names. Each new case
carries `charter_digest` from the pinned row, a `ranking_rationale` naming
the charter passage, at least one alternative explanation from a template the
session replaces, and its `dedup_identity`.

**The novelty check** runs before every open. Any `ImprovementCase` in any
state with the same `dedup_identity` refuses the open: a `rejected` match
appends the new ids and journals `evidence_attached_to_rejected` naming the
rejecting evaluation, and an open match appends them and journals
`evidence_attached`. A resolved investigation whose `interpretation` contains
the identity also refuses, attaching the ids to its case when it has one.
This is the mechanism behind "a rejected hypothesis is not re-proposed";
`test_rejected_is_not_reproposed` seeds a `rejected` case through
`journal.set_state` plus `projection.apply` and re-runs the tick. The
research session opens cases through the same function
(`valor-improve case open [--evidence-ids ...]`).

## The ranking snapshot

`rank(cases, *, evidence, investigations, experiments, charter, evaluations, revisions)`
is pure and reads no Redis. Charter §3 names the five considerations, and the
parent plan keeps the ranking ordinal until calibration supports numbers, so
each factor is an ordinal in `{1, 2, 3}` derived by a rule the module
docstring states and `tests/unit/test_improvement_ranking.py` pins:

| Factor | 3 | 2 | 1 |
|---|---|---|---|
| `opportunity_cost` | the leader of a charter §3 starting area (`inference`, `token_efficiency`, `skills`, `personas`, `cloud_execution`) | a later case in a starting area, or one of the five named means (`research_process`, `evaluators`, `memory`, `orchestration`, `infrastructure`) | `other` |
| `quality` | at least one `architectural` correction, or three or more evidence rows | two rows | one or none |
| `resource_cost` | an experiment whose `candidate_surfaces` names `agent_run`, or `blocked_by` set | a `frozen` or `running` experiment inside this lane's envelope | the likely action is an investigation (the default) |
| `uncertainty` | no investigation has resolved for the case, or an `inconclusive` verdict on any of its experiments | one investigation has resolved | a model revision cites the case (its `evidence_ids` intersect the case's) |
| `unlocked_capacity` | `inference`, `cloud_execution`, `research_process` | `skills`, `evaluators`, `memory`, `orchestration` | otherwise |

The order is the lexicographic key
`(blocked, -opportunity_cost, -unlocked_capacity, -quality, resource_cost, -uncertainty, created_at)`
with `blocked = bool(case.blocked_by)`. A blocked case keeps its position in
the printed list with its `blocked_by` text and the tick's proposal goes to
the first unblocked position, which is how "cheap inference ranks first" and
"the first experimentable case is eligible" hold at once. `rank()` reads
`blocked_by` off the case row and no investigation row, so a 30-day
investigation TTL cannot lift a block.

`write_snapshot` saves canonical JSON content-addressed through the verifying
artifact store as `ImprovementRankingSnapshot/ranking-<digest[:16]>`, the same
call shape as lane 4's `freeze_protocol`, so a corrupted snapshot raises
`ArtifactIntegrityError` on load instead of rendering:

```json
{
  "schema": 1,
  "charter_digest": "sha256:...",
  "at": "2026-09-15T11:53:04+00:00",
  "order": [{"case_id": "...", "position": 1, "factors": {"opportunity_cost": 3, "...": 1}, "reason": "...", "blocked_by": null}],
  "intake_pool": ["<investigation id>"],
  "previous_ref": "$CF:...:ImprovementRankingSnapshot/ranking-....txt",
  "diff": {"entered": ["<case id>"], "left": [{"case_id": "...", "reason": "rejected: evaluation <id>"}], "moved": [{"case_id": "...", "from": 2, "to": 1, "why": "factors changed: uncertainty 3->1"}]}
}
```

`diff` is computed against `load_snapshot(previous_ref)`: `entered` names ids
absent from the previous order, `left` names ids that dropped out with
`rejected: evaluation <id>` when the case row is `rejected`, and `moved`
names ids whose position changed with the factor deltas as `why`. When the
computed order, factors, intake pool, and charter digest equal the previous
snapshot's, the tick reuses the previous reference and journals nothing, so
the action id stays stable across a re-run. `ranking_recorded` is journaled
per case that entered, left, or changed position, carrying the snapshot
reference as `artifact_ref` and the snapshot digest as `payload_digest`.

`valor-improve ranking [--at REF]` prints the latest snapshot or the one
`--at` names (`--json` for the document). A reference that does not verify
prints the integrity error and exits 2; no snapshot yet prints "no snapshot
yet" and exits 0. The dashboard's ranking partial reads the same artifact
through `get_ranking`; nothing re-derives an order.

## The investigation lifecycle

`INVESTIGATION_KINDS` holds eight values, the schema gate's default maximum,
so a ninth costs a reasoned `VOCABULARY_MAXIMUMS` entry. What each records
and what runs it:

| Kind | Records | Runs it |
|---|---|---|
| `web_research` | the query, URLs, retrieval dates, claims | `WebSearch`, `WebFetch` |
| `memory_retrieval` | the `memory_search` query and the memory ids read | `python -m tools.memory_search` |
| `trace_analysis` | the session or event ids read and what they showed | `Read` on transcripts and event logs |
| `probe` | the command run, bounded and recorded verbatim, and its observed result | one bounded command |
| `resource_acquisition` | provider, documentation URLs and dates, price, the charter §7 terms (training on inputs, retention), adapter cost, and one disposition from `RESOURCE_DISPOSITIONS`: `prepared`, `keyless_integrated`, `vault_request_written`, `unsuitable` | `WebSearch`, `WebFetch` |
| `inspiration_intake` | the source URL, the extraction route (`valor-youtube-transcribe`, `valor-ingest`, `WebFetch`), the extracted substance, `accessible: bool`; the one kind that opens with no case | the intake route |
| `skill_acquisition` | the observed gap (evidence ids), candidates with URLs and dates, the vetting result, the integration made or proposed, and the evaluation disposition, in this lane always `deferred: no agent-run arm` citing #3311 | library search and the web |
| `charter_amendment` | the request text | only lane 3's `propose-amendment`, never `investigation open` |

`INVESTIGATION_STATES` holds five indexed values: `open`,
`awaiting_authorization`, `resolved`, `abandoned`, `expired`.
`awaiting_authorization` is legal only when `kind == "charter_amendment"`;
`open_investigation` and `transition_investigation` refuse it elsewhere with
`AWAITING_REQUIRES_AMENDMENT`. The tick resolves every awaiting row when the
pinned charter digest changes, with `interpretation="charter digest changed to ..."`.

`stage` is a plain field, never an index: ten values are past the gate's cap
and the only query the loop runs is open-or-closed, which `state` answers.
The seven linear stages `draft`, `deduplicated`, `policy_checked`, `running`,
`recorded`, `interpreted`, `applied` advance one at a time
(`transition_investigation` refuses a skip with `STAGE_SKIPPED`); the three
exits `cancelled`, `superseded`, `failed` are reachable from any non-terminal
stage.

`open_investigation(project_key, *, kind, case_id, uncertainty, query, decision_affected, expected_information_value)`
runs the novelty check (`prior_answers` filled from matching resolved
investigations, `stage="deduplicated"` when it found any, else `draft`) and
needs a case for every kind except `inspiration_intake`. The first
investigation on an `observed` case moves it to `investigating` through
`journal.set_state` under the case lease and `projection.apply`; `resolve()`
never changes case state.

### The claim rule

`record_claims(investigation_id, claims, sources)` validates every entry. A
claim is `{claim, url, retrieved_at}` with non-blank text, an `http(s)` URL,
and a parseable ISO-8601 `retrieved_at`. Anything short of that is stored
under `notes` in the `claims` JSON with `is_claim: false`: never dropped,
never promoted. The `claims` field is the envelope
`{"claims": [...], "notes": [...], "disposition": ..., "resource_name": ...}`
and `disposition_of(row)` reads the last two. `record_claims` returns the
number of entries stored; `None` or a non-list raises `ValueError`, and a
missing or non-open row raises `InvestigationRefusedError`, which the CLI
prints as `refused: <CODE>`.

### Provisional assumptions and the guard

`resolve(investigation_id, *, interpretation, provisional_assumption=None, assumption_detail=None, disposition=None, resource_name=None)`
sets `stage="interpreted"`, `state="resolved"`, and `resolved_at`. A
`provisional_assumption` needs a complete `assumption_detail`:
`charter_passage`, `confidence`, `consequence`, `overturning_observation`
(`evidence_ids` optional). The `consequence` is checked against the four
patterns charter §9 forbids an assumption to do (redefine the intended
outcome, erase a requirement, grant authority, increase a budget;
`ASSUMPTION_REFUSAL_PATTERNS`); a match is refused
`ASSUMPTION_EXCEEDS_AUTHORITY` and the session is told to defer the decision
through `propose-amendment`. This is a text rule and catches only the phrasing
it names; the `serves_charter` judge and the assumption digest are the
backstops. A resolve with an assumption appends its summary to `case.summary`
so it outlives the row's 30-day TTL.

A `resource_acquisition` resolve with disposition `vault_request_written`
takes a required `resource_name`, refuses any name outside
`tools.improvement_resources.RESOURCES` with `UNKNOWN_RESOURCE`, writes the
request text (item title, fingerprint field, terms clause) onto
`case.summary`, and sets `case.blocked_by = f"vault:{resource_name}"`. The
block lives on the immortal case because a human-paced wait has no deadline;
the tick's keep-alive `save()` keeps the row itself alive while the wait
lasts, and the block clears on a `verified` probe. `meta_model_api` is in
`RESOURCES` and `_VAULT_TITLE_KEYWORDS` (matching "Meta Model API" or "Muse
API" in a vault item title) so the wait is probeable. The other three
dispositions set no block; `keyless_integrated` means the source is reachable
through a credential already in the vault.

## The brief

`build_brief(case_id, project_key)` renders, in this order and only this
order: the pinned charter's full text verbatim (the brief's first non-blank
line is the charter's first non-blank line), a line naming the charter's
version and digest, the case (title, summary, priority area, ranking
rationale, position and factors from the latest snapshot), its evidence rows,
prior answers in the same priority area (resolved investigations'
interpretations, rejected cases with `rejected_reason` and evaluation ids),
the case's open investigations, the intake pool for the area, the charter §9
resolution rule, the claim rule, this lane's candidate envelope, and the
`valor-improve` subcommands the session may use. Evidence is capped at
`BRIEF_MAX_EVIDENCE = 40` rows and prior answers at
`BRIEF_MAX_PRIOR_ANSWERS = 20`, newest first, and the brief states the
truncation. Every list is sorted by recency then id, so two renders of an
unchanged case are byte-identical. `valor-improve brief --case ID` prints it
and exits 1 on `CASE_NOT_FOUND` or `CHARTER_NOT_PINNED`.

The static sections form `BRIEF_TEMPLATE`, and `brief_template_digest()` is
the `planner_prompt_digest` on every `ResearchProcessSpec` this lane writes,
so a wording change in the brief is a process change the recursion comparison
can see.

## The research skill

`.claude/skills/improve-research/SKILL.md` is project-only and never synced.
The dispatch message is `/improve-research case=... action=... type=...`
(plus `brief_ref=$CF:...` when the proposal payload is in the verifying
store), and the CLI is `$CLAUDE_PROJECT_DIR/.venv/bin/valor-improve`. Six
steps:

1. `valor-improve brief --case $CASE_ID`; a prior answer that covers the
   question is the answer.
2. `investigation open --kind K ...` per uncertainty, through the eight kinds;
   `investigation record --id ID --claims @file` as findings accrue.
3. `investigation resolve` with the claim rule and, when the uncertainty
   cannot be resolved, a provisional assumption with all four detail keys; a
   credential need ends as `--disposition vault_request_written --resource-name NAME`.
4. `revise-model --case ID --summary --rationale --prediction` when the
   evidence changes the system's model of itself; an empty prediction is
   refused `EMPTY_PREDICTION`.
5. `propose --case ID --payload FILE` with one hypothesis, mechanism,
   falsifier, and candidate inside the envelope, or `propose-amendment` when
   the decision depends on authority the charter has not granted.
6. The `improve-preflight` skill (`doctor`, `--json budget`, and the
   calibration-floor read, all read-only; below the floor the hypothesis
   stays `proposed` and the reading becomes a `probe` investigation), then
   `experiment freeze --case ID`, `experiment evaluate --id ID` in the
   background, `experiment show --id ID` until the state leaves `running`,
   then `report --case ID`.

Six prohibitions hold for the whole session, in the skill's own words: no
`AskUserQuestion`, no Telegram send (the only page is the one
`propose-amendment` sends), no session creation, no `.env` write, no `op`
invocation, and no direct `ImprovementCase` or control-namespace write (the
projection is overwritten by the next journal-driven `apply()`).

## The experiment envelope

`ENVELOPES = {"retrieval_parameters": {"limit": (1, 50), "rrf_k": (1, 200), "min_rrf_score": (0.0, 1.0)}}`.
`validate_candidate` refuses an empty candidate (`EMPTY_CANDIDATE`), any key
outside the envelope (`KEY_OUTSIDE_ENVELOPE`; `retrieval_mode` is an arena
setting, never a call parameter), any value outside its range
(`VALUE_OUTSIDE_RANGE`), any `None` value (`NONE_VALUE`), and a candidate
equal to the incumbent (`IDENTICAL_TO_INCUMBENT`). The incumbent is exactly
`INCUMBENT = {"limit": 10}`: `_retrieve_job` copies every present key into
the arm job and a present `None` is not "absent", so `rrf_k` and
`min_rrf_score` are omitted, never `None`. The one edit inside the harness is
that `arm_worker.py::handle_job` forwards `rrf_k` and `min_rrf_score` when
present and `retrieval.py::retrieve_ranked_ids` accepts them as keyword-only
optionals; absent keys are not passed, so lane 4's parity tests stay
byte-identical.

`propose_experiment` turns the accepted `propose` payload
(`{hypothesis, mechanism, falsifier, candidate, envelope}`) into a `proposed`
`ImprovementExperiment` and journals `hypothesis_proposed`; the candidate and
envelope live in the experiment's `notes` JSON until freeze writes the
manifest. `freeze_experiment(project_key, experiment_id, *, n_queries=30, seed=3217)`
runs seven steps in order, and every refusal before step 7 leaves the
experiment `proposed` with no protocol written:

1. Novelty check against `rejected` cases sharing the case's `dedup_identity`
   (`REJECTED_IDENTITY`).
2. The prior answer, unconditional for the envelope: every
   retrieval-parameter experiment cites #2082
   (`docs/features/hybrid-retrieval-eval.md`, the one prior paired evaluation
   of retrieval over this corpus) in `notes.prior_answers`. No heuristic
   decides relevance.
3. `export_corpus`, then `known_item_records(export)` parses
   `export.jsonl_text` into the `KnownItemRecord` triples the builder reads.
4. `build_known_item_set` under the unit-2 reservation
   `known_item_generation` (`UNIT2_UNAVAILABLE` on a refusal,
   `BUILDER_FAILED` on an exception); fewer than `MIN_QUERIES = 20` queries
   is `KNOWN_ITEM_SHORTFALL` naming produced versus requested.
5. `capture_baseline` with the incumbent (`BASELINE_FAILED` on an exception).
6. The protocol: `batch_size = len(queries)` set after generation (a fixed
   batch reads any shortfall as `inconclusive`), endpoints `recall_at_5` and
   `mrr`, thresholds `margin 0.02, alpha 0.05` per endpoint, holdout
   partition `known-item-{seed}`, `infra_failure_cap 0`.
7. `freeze_protocol`; the manifest
   `{protocol_ref, base_revision, candidate_ref, candidate, incumbent, envelope, corpus_digest}`
   with `base_revision` the checkout SHA at freeze and `candidate_ref` the
   same SHA for a parameter-only candidate (a code-changing candidate writes
   its branch ref there); `contract_digest`; `state="frozen"`, `frozen_at`,
   `model_revision_id` (the newest `current` revision), `charter_version`;
   the `experiment_frozen` event under the case lease.

The hashing happens before any arm runs. Freeze moves an `observed` or
`investigating` case to `experimenting`.

`evaluate_experiment` refuses `SLOT_NOT_HELD` unless no `AGENT_SESSION_ID` is
set (an operator at a terminal is break-glass) or a `running` intent on the
case names that session, reserves `evaluation_judges`
(`n_queries * 2 * JUDGE_PRICE_ESTIMATE_USD`), moves the case to `evaluating`,
calls lane 4's `runner.evaluate` (the single writer of
`ImprovementEvaluation`, default judge roster), settles the reservation as
`metering="estimated"` (released when no trial ran), and applies the verdict.
`experiment show --id` prints state, verdict, and notes;
`experiment repair --id` wraps lane 4's `repair_wedged_experiment` for a
`running` or `aborted` experiment whose evaluation died.

## The verdict changes the next selection

`apply_verdict(project_key, evaluation_id)` moves the case per
`VERDICT_TO_CASE_STATE`:

| Verdict | Case | Also |
|---|---|---|
| `reject` | `rejected` | `rejected_reason` from the evaluation's rationale; the id joins `evaluation_ids`; the next snapshot names the case under `left` |
| `accept` | stays `evaluating` | for lane 6's release path; no `ImprovementRelease` writer lives here |
| `inconclusive` | `investigating` | `uncertainty` reads 3 again on the next rank |
| `infra_failure`, `invalidated` | unchanged | the experiment is `aborted` and a `probe` investigation naming the failure is opened on the case |

Order: the `verdict_applied` event (its `payload_digest` is
`evaluation:<id>:<verdict>`) then `set_state` for a verdict that moves the
case, both under one lease-minted generation; then `projection.apply`; then
the plain `save()` of `evaluation_ids` and `rejected_reason`. A direct ORM
write of `state` is reverted by the next projection (lane 3's head-state
contract), and `test_apply_verdict_state_survives_projection` runs one
`projection.apply` after a `reject` and asserts `rejected` survived it. Every
applied evaluation id is recorded, so a second call is `ALREADY_APPLIED` and
the planner backstop shares that memory. A held lease is `CASE_BUSY`.

## The observer adapters

`reflections/improvement_collect.py` runs five adapters; the two below join
`collect_corrections`, `collect_inspirations`, and
`collect_expectation_coverage`. Each input's production writer is named in
the module docstring and pinned by a test.

**`collect_lessons`** runs `gh pr list --state merged` over the last 14 days
(or since the newest `lesson` row) through the same runner shape
`reflections/sdlc_progress.py` uses, extracts lines starting with one of the
seven `LESSON_PREFIXES` (`- lesson:`, `- pattern:`, `- note:`,
`- convention:`, `- learning:`, `- reminder:`, `- caveat:`), and writes one
`lesson` row per line with `source_ref="pr:{number}:{sha256(line)[:16]}"`,
`text=line`, `detail={title, stage_guess}` (`STAGE_KEYWORDS` matched on the
line, then the title, then the body, `unknown` otherwise), and
`observed_at=mergedAt`. A `gh` failure yields zero rows, a warning, and a
findings entry, never a raise.

**`collect_promises`** is charter §10's no-promises observer. It reads the
same session window as the correction detector, takes outbound
`chat_message_log` entries (written by
`bridge/telegram_relay.py::_append_outbound_chat_log`), samples the newest
`PROMISE_SAMPLE_PER_TICK = 10` unjudged entries, and asks a cheap model one
yes/no question per entry with the charter paragraph quoted. A `yes` writes a
`promise` row (`text` = the entry, `detail` = the judge's quoted span,
`confidence` = the judge's number) deduped on
`source_ref="promise:{session_id}:{sha256(session_id + content)[:16]}"`;
judged-but-clean entries are remembered in a plain Redis set under the
improvement control namespace (`keys.promise_judged_key`,
`improve:{project}:_ns:promise_judged`, 30-day TTL) so a `no` is not bought
again. The judge call is
metered through `tools.paid_inference_meter` under `purpose="promise_detector"`
(the meter is the reason the adapter exists as a spend: a refusal is a skip,
never a failure) and the default transport is refused on a project
`is_open_source` does not clear. The adapter is gated by
`ImprovementSettings.promise_detector_enabled`, off by default because it
spends money, on top of the module-wide `enabled` switch.

The tick keeps `failed` (an adapter raised) and `skipped` (declined by rule)
as separate lists; only a tick in which every adapter failed reports
`status="error"`.

## The assumption digest

`run_improvement_assumption_digest` is registered as
`improvement-assumption-digest` with `cadence="259200s"` (three days). One
message, in this order: provisional assumptions on investigations resolved
since the watermark, grouped by the case's `priority_area` in charter §3
order, each with its charter passage, confidence, consequence, and
overturning observation; a "Vault requests" section (`resource_acquisition`
rows with disposition `vault_request_written` whose case still carries a
`vault:` block, rendered every digest as standing status); a "Resources
acquired" section (`resource_acquired` evidence rows since the watermark,
which `tools/vault_write.py` writes); an "Infrastructure overruns" section
(payloads lane 7's teardown ladder handed to `on_escalation` since the last
delivered digest); and the fixed closing line:

> This is a status report. It asks nothing. Silence validates none of the
> above; each assumption stands until evidence overturns it.

The message goes through `send_host_eng_telegram`. Every user-authored string
renders inside double quotes; every other line is a statement, and a test
asserts no `?` appears outside a quoted body and no poll or `AskUserQuestion`
symbol is imported. An empty digest sends nothing and reports
`counts={"assumptions": 0}`.

**The watermark rule.** `ImprovementControllerState.digest_watermark` advances
to the newest timestamp the digest actually rendered (the newest `resolved_at`
among rendered assumptions, or the newest `created_at` among rendered
`resource_acquired` rows when later), never "now", so a row resolved between
the read and the send appears next time. It is written only after the sender
reports delivery; a sender returning `False` is `status="error"` with
`digest-not-delivered`, and the watermark and pending overruns are untouched
so the next run re-sends whole. Vault requests neither scope by nor advance
the watermark.

**The overrun sink is process-local.** `on_escalation(payload)` appends to a
list in this interpreter's memory; the `spend_receipt` row the ladder writes
is the record, and the sink only lets the next digest in the same process
mention it. Payloads drain after a successful send and are kept across a
failed one.

## The qualified-result report

`build_report(case_id, project_key)`, printed by `valor-improve report --case ID`,
is generated from records alone in this order: the case and its charter
digest; the ranking positions it held, walking the snapshot chain along
`previous_ref`; the investigations with claims, notes, and assumptions; the
model revisions with their predictions and process digests; the experiment
(hypothesis, mechanism, falsifier, contract digest, envelope, candidate versus
incumbent, both manifest refs, prior answers); the evaluation (verdict,
effect and interval per endpoint, correction, `blinded`, trials, identity
scan, judge calibration); then three mandatory sections whose content is
derived, never authored:

- **What was measured**: endpoints, corpus digest, query count and batch
  size, holdout partition, thresholds, incumbent versus candidate, read from
  the frozen protocol.
- **What this does not establish**: always non-empty. No claim above "loop
  operational"; the sample size and margin; that a retrieval-parameter gain
  says nothing about agent behavior; every `metering="estimated"` spend
  receipt on the case; the novelty rule's limit (a rejected idea reworded past
  the eight-word prefix is not caught); and the seeded-inputs line naming
  every case any of whose evidence `is_seeded` reports, with its marker text.
- **What would change the answer**: the falsifier, the overturning
  observation of every assumption cited, and a larger sample.

`is_seeded(evidence)` is the single definition of "the builder planted this":
`source_ref` starting with `seed:` (rows the integration tests write
directly) or a `detail` JSON object carrying a `seed` key (rows the
inspiration adapter wrote from a seeded memory). Nothing else counts.

## The dashboard

Three partials join the root dashboard, each rendering one of three
distinguishable states (content, "nothing yet", "unavailable" with the error).

- `/_partials/improvement/ranking/` (`get_ranking`): the latest snapshot's
  order with each case's title, factors, `blocked_by`, and `movement`
  (`entered`, `moved from N: <why>`, or none), the `left` entries with their
  reason, and the intake pool. `no_snapshot_yet` when the cursor names none;
  `unavailable` when the store read raised or the snapshot did not verify. A
  corrupted snapshot never renders as an order.
- `/_partials/improvement/hypotheses/` (`get_hypotheses`): experiments in
  `proposed`, `frozen`, `running` with hypothesis, mechanism, falsifier,
  contract digest, and `frozen_at`. Never a count.
- `/_partials/improvement/rejected/` (`get_rejected_approaches`): cases in
  `rejected` with `rejected_reason`, the latest evaluation's verdict with
  effect and interval per endpoint, and `left_in`, the snapshot whose diff
  lists the case under `left`.

`ui/data/improvement.py` exports exactly nine getters (`get_control_status`,
`get_coverage`, `get_goals`, `get_hypotheses`, `get_intervention_burden`,
`get_provisional_assumptions`, `get_ranking`, `get_rejected_approaches`,
`get_release_lineage`); `test_dashboard_never_offers_experiment_or_patch_counts`
pins the list and asserts no result carries an activity counter. The goals
partial's "Open cases" section reads positions from `get_ranking` when a
snapshot exists.

## Lane 6 seams

- **Process spec.** `revise-model` builds the real `ResearchProcessSpec`
  (`selection_rule="ordinal-lexicographic-v1"`, an empty
  `investigation_budget_split`, `revision_cadence_seconds` from
  `controller_tick_seconds`, `planner_prompt_digest` from the brief template,
  `skill_digest` from the skill file, and the ranking module digest under
  `extra`), stores `process_spec_json(spec)` (canonical text:
  `sort_keys=True`, separators `(",", ":")`) on
  `ImprovementModelRevision.research_process_spec`, and sets
  `research_process_digest` only through lane 6's
  `tools.improvement_recursion.process.research_process_digest`. One hashing
  routine, lane 6's; `test_process_spec_canonical_bytes` pins the bytes
  against the fixture lane 6's own test reads.
  `revise-model --backfill-digests` digests every stored spec that has none
  and writes no revision.
- **Arm runner.** `PlannerArmRunner.run(process_digest, opportunity_ids, budget_cap, arm_run_id)`
  runs exactly one `plan_tick` restricted to the named opportunities. The
  restricted tick ranks that subset in memory and leaves the ranking chain
  alone (no snapshot, no cursor write, no `ranking_recorded`; the chain
  records full ticks only), proposes for the named case under the cursor's
  existing snapshot, and before any full tick proposes nothing. It
  returns lane 6's `ArmResult` with per-opportunity gains taken from `accept`
  evaluations already on record and
  `BudgetUse(unit2_usd=None, unit3_usd=None, subscription_turns=0, wall_seconds=elapsed)`;
  lane 6's `budget.accounted_use` reads the dollars from the meters by
  `arm_run_id`. `tools/improvement.py`'s entry registers a
  `PlannerArmRunner()` through `register_arm_runner` on every invocation, and
  `compare run --arm-runner tools.improvement_plan_arm:PlannerArmRunner`
  resolves the class by lazy import with no constructor arguments. A
  multi-tick arm that dispatches and evaluates inside `run` is #3311's.
- **Manifest refs.** Every manifest carries `base_revision` and
  `candidate_ref`, so lane 6's `propose` reads them rather than refusing
  `MANIFEST_LACKS_BASE_REVISION`.

## Settings, registrations, migrations

Two `ImprovementSettings` fields, both `# @optional` in `.env.example`:

| Field | Default | Meaning |
|---|---|---|
| `promise_detector_enabled` | `False` | Whether `collect_promises` runs on the evidence tick. Off because each sampled message costs a judge call against the daily paid-inference pool. `IMPROVEMENT__PROMISE_DETECTOR_ENABLED` |
| `cheap_inference_model` | `""` | The OpenRouter model id the promise judge runs on; empty falls back to `config.models.OPENROUTER_GEMMA4_FREE`. The spend gate is `promise_detector_enabled`, never this field. `IMPROVEMENT__CHEAP_INFERENCE_MODEL` |

`scripts/update/run.py` calls `register_improvement_planner` (cadence from
`controller_tick_seconds`) and `register_improvement_assumption_digest`
(`259200s`) beside `register_improvement_collect`, each idempotent, each a
`RegisterResult` on the run result, each inheriting the
`_this_machine_owns_valor` guard. Registration is independent of the kill
switch: the planner and the digest return `skipped` while
`ImprovementSettings.enabled` is `False`. All five improvement reflection
entry points resolve their project through
`reflections.redis_access.get_project_key()`.

Three migrations in `scripts/update/migrations.py`, registered in
`MIGRATIONS`, idempotent: `improvement_investigation_stage_field` (additive
confirm of the lane-5 investigation fields), `improvement_controller_state`
(confirms the one-row-per-project cursor model, imported directly because
`models.__all__` pins exactly eight `Improvement*` exports), and
`retire_sdlc_reflection` (removes `data/sdlc_reflection_last_run.json` when
present). `scripts/sdlc_reflection.py`, its installer, and its plist are
gone; `com.valor.sdlc-reflection` is in `/update`'s service sweep
(`scripts/update/service.py`) so every fleet machine boots it out by exact
label. Lessons now enter the loop as `lesson` evidence rows.

## First real cycle

The first cycle ran on 2026-09-15 on the owning machine against production
Redis db 0, project `valor`, with `IMPROVEMENT__ENABLED=true` set only as an
environment variable on the tick and dispatch commands. The report, generated
by `valor-improve report` and posted verbatim with its run record, is
[#3217 (comment)](https://github.com/tomcounsell/ai/issues/3217#issuecomment-5680368659).

**What it establishes: loop operational, charter §6 level 1, and nothing
higher.** One seeded `Memory` became an `inspiration` row through
`collect_inspirations`; the planner tick opened case
`1ec40086ca1d422e90ef747775ff7f64` (`inference`,
`dedup_identity="seed:charter-s3:inference"`), wrote snapshot 1, and proposed
one investigation; the scheduler adapter admitted, materialized, and
activated a research session that ran 8 turns in the lane worktree and wrote
nothing to the tree; the session read the brief, opened and resolved six
investigations (`web_research`, `resource_acquisition`, `probe`,
`memory_retrieval`, two `trace_analysis`, one carrying a provisional
assumption with all four detail keys), wrote three model revisions with
predictions and process digests, proposed under its running intent, froze an
experiment (`{"rrf_k": 10}` against `{"limit": 10}`, 30 known-item queries,
#2082 cited, both manifest refs), and evaluated it; `apply_verdict` ran and
opened a seventh investigation, the `probe` (`34fc3fae…`) that names the
calibration failure; the post-verdict tick wrote snapshot 2 with
`previous_ref` = snapshot 1; the report carried its three mandatory sections;
the digest sent one assumption and no vault request. The eighth
investigation on the case, `3dbf7e2f7c67457bb768a03050772558`
(`skill_acquisition`), was run from the shell through the same CLI after the
session (commit `c1716fb76`): stages 1 to 3 ran (the gap: no skill guided a
session through a paired evaluation's prerequisites before `experiment
freeze`; the skill library and a web search found no covering skill, and two
open-source candidates were declined), the `improve-preflight` skill was
integrated at `.claude/skills/improve-preflight/SKILL.md`, stage 4 resolved
as a provisional assumption citing #3311 (the agent-run arm is not yet
available), and stage 5 recorded "not yet observable"; eight investigations
on the case in all. Lineage from case to charter digest is readable from the
records alone.

**What it does not establish.**

- The verdict was `infra_failure` from lane 4's calibration gate: the valor
  partition holds 0 `architectural` corrections against
  `MIN_REFERENCE_SET_SIZE = 20`, so the `serves_charter` judge ran no trial.
  Per the verdict rule the experiment is `aborted`, the case stays at
  `evaluating`, and a `probe` investigation names the failure. Snapshot 2's
  diff is empty under `entered`, `left`, and `moved`; the verdict changed the
  factors (`uncertainty` 3 to 1, because the current model revision cites the
  case) and left the position. The move-in-ranking demonstration (a case
  under `left` or `moved` with the evaluation id as the reason) is carried by
  the integration test `test_verdict_moves_ranking`, which produces a
  deterministic `reject` on live arms and asserts the second snapshot's diff.
  Model revision `6b25e970` predicts `infra_failure` with zero trials for
  every envelope until twenty architectural corrections exist.
- The `resource_acquisition` investigation resolved `keyless_integrated`, not
  `vault_request_written`: the recorded evidence is that Muse Spark 1.3 and
  its contributor variant are served through OpenRouter under the key already
  in the vault, alongside 22 `$0/$0` listings, so no vault request was
  written, `blocked_by` stayed empty, and the unblock-on-verified-probe path
  is exercised only by `test_blocked_case_unblocks_on_verified_probe`.
- The only case opened from seeded evidence; no case opened from organically
  collected evidence (one organic inspiration row with no URL and no cluster
  partner, one coverage row, zero lessons in the 14-day merged-PR window,
  zero corrections, promises skipped). The report's seeded-inputs line names
  the case with marker `charter-s3:inference`.
- Unit-2 spend settled at 0.06 USD, one receipt for `known_item_generation`
  at `metering="estimated"`; the `evaluation_judges` reservation was released
  because no trial ran. The true cost is unknown until the reconcile pass
  corrects it.
- The session found that `config.models.OPENROUTER_GEMMA4_FREE`
  (`google/gemma-4-e2b:free`) is delisted (a live request returns HTTP 400),
  so the promise judge's fallback model is dead while
  `cheap_inference_model` is empty. Recorded as a resolved `probe`
  investigation and a model revision; the constant itself is unchanged.

**How the parked case moves.** The planner proposes nothing for a case at
`evaluating` (`_action_kind` returns `None`), so case
`1ec40086ca1d422e90ef747775ff7f64` progresses only when the calibration
reference set reaches `MIN_REFERENCE_SET_SIZE = 20` architectural
corrections. At that point an operator runs
`valor-improve experiment repair --id 3e5627da17ba4938bf6fff3c36c8952f`
(the `aborted` experiment returns to `frozen`) and then
`valor-improve experiment evaluate --id 3e5627da17ba4938bf6fff3c36c8952f`
from a terminal; `apply_verdict` then moves the case.

The cycle exposed four defects, each fixed under test on the lane branch: the
improvement reflection entry points resolved the project as the literal
`default`; the adapter, the reconcile pass, and the CLI looked up the bound
session by `session_id` where the seam and the worker use
`agent_session_id`; the executor refuses a slugless eng session in a
pre-provisioned worktree, so the adapter passes the lane slug when it runs
from `.worktrees/<slug>`; and a re-proposal after a cancelled intent reused
the spent action id.

## Tests

- `tests/unit/test_improvement_planner.py`: case opening (seeded cold start,
  two-tick cluster, novelty refusal, intake pool), the unblock step, the
  paused and lease-busy findings, idempotent action ids and the cancelled-intent
  successor, the `ranking_recorded` refusal, the arm-runner registration and
  gains source, and the no-LLM, no-dispatch import rule.
- `tests/unit/test_improvement_ranking.py`: one class per factor rule, the
  lexicographic order with `blocked` last, the snapshot diff, the integrity
  error on load, and the process-spec canonical bytes.
- `tests/unit/test_improvement_investigations.py`: the claim rule, the
  assumption completeness and refusal patterns, `blocked_by` shape,
  `awaiting_authorization` scoping, stage ordering.
- `tests/unit/test_improvement_brief.py`: charter first and byte-identical,
  the no-evidence sentinel, positions and factors from the real snapshot.
- `tests/unit/test_improvement_experiment.py`: every refusal code, the
  shortfall, `batch_size` after generation, #2082, the no-`None` incumbent,
  both manifest refs, each verdict's case move and its survival of
  `projection.apply`, `ALREADY_APPLIED`, `SLOT_NOT_HELD`, and the arm-worker
  pass-throughs.
- `tests/unit/test_improvement_report.py`: the three sections and the
  seeded-inputs line.
- `tests/unit/test_improvement_assumption_digest.py`: the closing line, no
  `?` outside quoted bodies, no question symbol imported, the watermark on a
  failed send, the headings.
- `tests/unit/test_improvement_evidence.py`: the lesson and promise adapters,
  the failed-versus-skipped status rule, the meter refusal as a skip.
- `tests/unit/test_improvement_cli.py`, `tests/unit/test_improvement_reflection_project_key.py`,
  `tests/unit/test_improvement_models.py`, `tests/unit/test_migrations.py`,
  `tests/unit/test_reflection_register.py`, `tests/unit/test_ui_app.py`
  (`TestLane5Partials`, the nine-getter list).
- `tests/integration/test_improvement_research_cycle.py`: the whole loop in a
  claimed test database (`test_verdict_moves_ranking`,
  `test_rejected_is_not_reproposed`, the CLI through the Python entry and the
  console binary).

## See also

- [Improvement Controller](improvement-controller.md): records, the control namespace contract, dispatch, break-glass
- [Improvement Evaluation](improvement-evaluation.md): the frozen contract, arm isolation, the judge envelope, calibration, verdicts
- [Improvement Release](improvement-release.md): what consumes an `accept`, the recursive comparison this lane's arm runner plugs into
- [Capability matrix](../plans/critiques/recursive-self-improvement-capability-matrix.md): what is built, tested, exercised, and measured
- [`docs/tools-reference.md`](../tools-reference.md#improvement-controller-valor-improve): the `valor-improve` subcommands
