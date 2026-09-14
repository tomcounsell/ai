---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-14
tracking: https://github.com/tomcounsell/ai/issues/3217
last_comment_id: 5662856978
---

# Improvement controller lane 5: the first complete research cycle

## Problem

Four lanes have built an observer, a charter, a control journal (in planning), and an evaluation
harness (in review). Nothing yet connects them. `ImprovementEvidence` rows accumulate on a
fifteen-minute tick and are shown on three dashboard panels, and that is where the loop ends: no
row becomes a case, no case is ranked, no ranking selects an investigation, no investigation
produces a hypothesis, no hypothesis is frozen and measured, and no verdict changes what the system
does next. The charter's claim ladder starts at "loop operational: an autonomous
discovery-to-evaluation cycle completed" (charter §6), and today the rung does not exist.

**Current behavior:**

- `ImprovementCase`, `ImprovementModelRevision`, `ImprovementInvestigation`, and
  `ImprovementExperiment` have zero production writers. The goals partial renders "No cases opened
  yet" (`ui/templates/improvement/goals.html:49`) and "Rejected approaches: lane 5 records what was
  tried and set aside" (`ui/data/improvement.py:221`).
- `INVESTIGATION_KINDS` holds five values (`models/improvement_investigation.py:45-51`); the
  charter v2 reconciliation named eight (`docs/plans/recursive-self-improvement.md:394`), and the
  three missing ones (`inspiration_intake`, `skill_acquisition`, `charter_amendment`) are the ones
  that carry charter §4, §5, and §9.
- The order of open cases is held nowhere. `ranking_rationale` is free text on one case; nothing
  records the ordered list, the factors, or the movement between ticks, so "the verdict changed the
  next selection" cannot be demonstrated, only asserted.
- Provisional assumptions have a dashboard surface (`get_provisional_assumptions`,
  `ui/data/improvement.py:162`) and no writer; the three-day Telegram digest the charter §11
  preserves does not exist, and lane 7 already wired an optional sink to it
  (`tools/infrastructure_budget.py:568-574`) that today has nothing to call.
- `scripts/sdlc_reflection.py` prefix-scrapes `- lesson:` lines from merged PR bodies
  (`:130-166`) and appends them verbatim to `docs/sdlc/{stage}.md` (`:169-217`), then opens a PR.
  A lesson's effect on later behavior is never measured. That is the pattern this whole system
  exists to replace, and it still runs every three days under `com.valor.sdlc-reflection`.
- Charter §10's strict no-promises rule has no detector. Outbound messages are recorded
  (`AgentSession.chat_message_log`, `direction="out"`) and nothing reads them for an unqualified
  commitment.
- Lane 4's harness (`tools/improvement_eval/runner.py::evaluate`) is driven only by constructed
  fixtures. It has never been pointed at a hypothesis anyone generated.

**Desired outcome:**

One complete cycle, run by the system on this machine and recorded so every step is checkable from
the records alone: evidence opens a case; a planner tick ranks the open cases and writes an
immutable ranking snapshot; a research session, briefed with the charter first, investigates
through the eight kinds without asking Tom anything but an amendment request; a hypothesis with a
mechanism and a falsifier is frozen under a contract and measured by lane 4's paired blinded
harness; the verdict moves the case to `rejected` or leaves it for lane 6, and the next snapshot
shows the move. The first resource-acquisition action stops at a prepared adapter and a written
vault request. `scripts/sdlc_reflection.py` is gone, its lessons now enter as evidence. A
qualified-result report says what was measured, what it does not establish, and what would change
the answer. The claim made at the end is exactly "loop operational" and nothing above it.

## Freshness Check

**Baseline commit:** `89f800876` (`main`, 2026-09-14), plus lane 4's PR #3309 head `fe6f55072`
(`session/sdlc-3216`) for the harness surface this lane consumes.
**Issue filed at:** 2026-09-07T04:45:50Z
**Disposition:** **Minor drift, reconciled by the owner.** The issue body is charter v1
vocabulary. Charter v2 (`docs/improvement-charter.md`, effective 2026-09-07), the parent plan's
reconciliation (`docs/plans/recursive-self-improvement.md:24-35`, lane-5 paragraph at `:802`),
and Tom's refresh comment on the issue (2026-09-09, id 5603829577) all post-date the body and all
say the same thing, so the plan follows them and names each override below rather than planning
for the stale text. The Recon Summary appended to the issue on 2026-09-14 records the same
reconciliation on the issue itself.

**Where the issue body and charter v2 disagree, and which wins:**

| Issue body (v1) | Charter v2 / parent plan / refresh comment | Plan follows |
|---|---|---|
| "Five kinds, no sixth" | Eight kinds (`recursive-self-improvement.md:394`); §4 inspiration intake, §5 skill acquisition, §9 amendment request | Eight kinds; the schema gate's default maximum is exactly eight (`tests/unit/test_improvement_models.py:92`) |
| "The controller asks Tom nothing... an anti-criterion fails the build if a question path reappears" | §9: no *routine* research questions; one permitted class, the evidence-backed amendment request | Anti-criterion narrowed to: no question kind, no attention queue, no poll binding; the only outbound message classes are the amendment request and the three-day status digest, both plain messages that bind to no message id |
| "The first experiment: journey preservation... a change to planning and context assembly" | §3: journey preservation "is not a mandatory first experiment"; parent plan: "the top-ranked opportunity by charter §3 (expected, not mandated...)" | The first experiment is the top-ranked *experimentable* case under the ranking snapshot, in the arm shape lane 4's harness can measure (retrieval parameters over the frozen corpus). See Technical Approach |
| No mention of ranking snapshots, the brief, the digest, `skill_acquisition`, or the no-promises detector | Refresh comment: "Gap G's durable ordered artifact is still this lane's to build"; "the §5 skill-acquisition cycle and the no-promises detector are untouched by lane 2b"; parent `:802` assigns the brief, the digest, and the snapshot to lane 5 | All in scope, with the `skill_acquisition` evaluation step honestly deferred (No-Gos) |

**File:line references re-verified** (the issue body cites none; the refresh comment's claims):
- `models/improvement_case.py` carries `priority_area` (`:118`), `ranking_rationale` (`:127`),
  `charter_digest` (`:126`), `PRIORITY_AREAS` with eleven values (`:72-84`); `objective` is gone.
  Holds.
- `/_partials/improvement/goals/` renders unresolved assumptions from
  `ImprovementInvestigation.provisional_assumption` (`ui/data/improvement.py:162-216`). Holds; no
  writer exists.
- "Open cases show as 'no cases opened yet, lane 3 opens the first one'"
  (`ui/templates/improvement/goals.html:49`). Holds. The template names lane 3; the parent plan and
  this plan make the planner tick (lane 5) the first case writer, so the template text is corrected
  by this lane.

**Cited sibling issues/PRs re-checked:**
- #3216 (lane 4): OPEN, PR #3309 OPEN and MERGEABLE at `fe6f55072`, in review. Blocks this build.
- #3215 (lane 3): OPEN, in PLAN, no branch on origin, worktree at `main`. Blocks this build.
- #3255 (lane 2b): CLOSED 2026-09-09, merged as PR #3275 (`aff4d7e2e`). Delivered the charter
  digest surface, `priority_area`, and the goals partial this lane extends.
- #3274 (lane 7): CLOSED 2026-09-14, merged as PR #3299 (`37dc10b33`). Added `spend_receipt` and
  `resource_probe` to `EVIDENCE_KINDS` (`models/improvement_evidence.py:78-79`), the
  `improvement-assumption-digest` optional sink (`tools/infrastructure_budget.py:568-574`), and
  the retention root outside the checkout. Consumed, not changed.
- #3218 (lane 6): OPEN; its plan exists on `session/sdlc-3218`
  (`docs/plans/improvement-controller-lane-6-promotion-rollback-and-recursion.md`) and its
  2026-09-14 comment on #3217 (id 5662856978) names three seams this lane provides (Technical
  Approach, "Provided to lane 6"). Owns releases and the recursive comparison; this lane writes no
  `ImprovementRelease`.
- #3177 (parent): OPEN.

**Commits on main since the issue was filed touching referenced files:**
- `aff4d7e2e` (#3275, lane 2b) — `models/improvement_case.py`, `models/improvement_charter.py`,
  `config/settings.py`, `ui/data/improvement.py`, `docs/features/improvement-controller.md`:
  changed the vocabulary this lane writes in. Incorporated.
- `37dc10b33` (#3299, lane 7) — `models/improvement_evidence.py`, `models/verifying_artifact_store.py`,
  `scripts/update/migrations.py`, `docs/features/improvement-controller.md`: two evidence kinds and
  the retention root. Incorporated; the cardinality argument for two more kinds is written against
  lane 7's comment at `models/improvement_evidence.py:58-66`.
- `06ccc8d50`, `16c8ce696`, `6cd1ef7e6` — executor and Room-inbox changes; irrelevant to this lane.
- No commit touched `scripts/sdlc_reflection.py` since filing.

**Active plans in `docs/plans/` overlapping this area:**
- `improvement-controller-lane-4-frozen-evaluation-inputs.md` (active, in review): owns the
  harness. Its No-Gos at `:1157-1174` hand "the first real experiment" and "comparing full
  candidate agent runs" to #3217. Coordination, not conflict: this lane extends the arm worker's
  job spec by two pass-through keys and changes no gate.
- `recursive-self-improvement.md` (parent, Planning): its lane-5 paragraph is this plan's scope
  statement. This plan carries `tracking:` to #3217, not #3177.
- `improvement-controller-lane-6-promotion-rollback-and-recursion.md` (on `session/sdlc-3218`,
  not yet on main): treats this lane as a seam, not a blocker, and asks for three things this plan
  provides. Coordination, not conflict.
- `sdlc-control-plane-asserted-facts.md`: no overlap.

**Notes:** Lane 3 has no plan document yet, so its surface is consumed here as a requirements
contract (Technical Approach, "Consumed from lane 3") and gated by import checks in Prerequisites.
If lane 3's build names things differently, the build adapts to lane 3's names; the contract is
what this lane needs, not what it is called.

## Prior Art

- **PR #932** (2026-04-14): "per-stage docs/sdlc/ repo addenda with reflection agent". The origin
  of `scripts/sdlc_reflection.py`. It shipped the prefix scrape and the docs append as a deliberate
  "lightweight heuristic" (`scripts/sdlc_reflection.py:134-135`). It works exactly as designed; the
  design is the problem. Retired by this lane.
- **PR #3224** (lanes 1 and 2): the eight records, `ImprovementSettings`, the evidence tick, the
  verifying store, and the correction, inspiration, and coverage adapters. This lane adds two
  adapters to the same tuple (`reflections/improvement_collect.py:495-499`).
- **PR #3275** (lane 2b): charter digest seeding, `priority_area`/`ranking_rationale`, the goals
  partial with lane placeholders, `tools/improvement_eligibility.py`, `tools/improvement_resources.py`.
  Every case this lane opens cites the digest lane 2b seeds.
- **PR #3299** (lane 7): unit-3 metering, the teardown policy, the operating report, and the
  optional digest sink. The report format at `tools/improvement_operating_report.py` is the shape
  the qualified-result report follows (generated from records, never hand-written).
- **PR #3309** (lane 4, open): the harness. Its `capture_baseline`, `freeze_protocol`,
  `compute_contract_digest`, and `evaluate` are the four calls this lane makes; its arm worker is
  the one file this lane edits inside `tools/improvement_eval/`.
- **#410 / PR #411** (autoexperiment): the retired hypothesize-edit-evaluate loop. Its failure
  shape (single noisy judge, strict-inequality acceptance, no isolation) is why every verdict here
  comes from lane 4's harness and nowhere else.
- **PR #2135** (hybrid retrieval eval, `docs/plans/hybrid-retrieval-eval.md`, #2082 adopt verdict):
  the one prior evaluation of retrieval parameters in this repo. Its existence is exactly what the
  novelty check must surface: a hypothesis that re-derives #2082's comparison is a prior answer,
  and the research session must cite it before proposing anything in the same envelope.

## Research

Retrieved 2026-09-14. Every claim here is plan-time context; the build's `resource_acquisition`
investigation re-verifies with its own URLs and retrieval date, and the plan asserts none of it as a
fact the loop may rely on.

**Queries used:**
- `Meta Muse 1.3 model API pricing free inference 2026`
- `OpenRouter free models rate limits no API key required 2026`

**Key findings:**
- "Meta Muse 1.3" resolves to **Muse Spark 1.3** (released 2026-09-02). Two endpoints: a standard
  tier and a **contributor** tier at roughly $0.10/M input and $0.20/M output whose price is paid
  for by consenting to Meta training on the traffic. No free tier appears in any source. Sources:
  [OpenRouter listing](https://openrouter.ai/meta/muse-spark-1.3),
  [contributor endpoint](https://openrouter.ai/meta/muse-spark-1.3-contributor),
  [getdeploying pricing](https://getdeploying.com/llms/muse-spark-1.3). **Informs:** the
  resource-acquisition action ends at a prepared adapter and a written vault request, as the issue
  says; and charter §7 confines any contributor-tier use to open-source work, so the adapter is
  gated by `tools/improvement_eligibility.is_open_source` at the call site, not at configuration.
- OpenRouter `:free` models require an account and API key (no card), with a 20 req/min cap and a
  daily cap that sources disagree on (50 or 200 per day, 1000 after a $10 purchase); provider-side
  429s dominate in practice and the free lineup rotates without notice. Sources:
  [klymentiev free-tier notes](https://klymentiev.com/blog/openrouter-free-tier),
  [costgoat free list](https://costgoat.com/pricing/openrouter-free-models),
  [ask-coreai limits](https://ask-coreai.com/blog/openrouter-free-models-2026-limits-catches).
  **Informs:** the repo's existing OpenRouter key (`config/models.py:139`, `OPENROUTER_GEMMA4_FREE`)
  already reaches a `:free` model, so a "keyless-for-us" source exists today for the no-promises
  judge and the `serves_charter` judge under unit 2; and the investigation must record the daily
  cap as a `provisional_assumption` with the two conflicting sources, not as a claim.
- No source addresses how to decide, from a repository's own evidence, which retrieval-parameter
  change is worth a paired trial. That is a design decision this plan makes explicitly (the
  novelty check against #2082 and the rejected-case memory) rather than a borrowed recipe.

Findings saved to the memory store per the repo addendum (fire-and-forget).

## Spike Results

All six spikes were code-reads against `main` at `89f800876` and `session/sdlc-3216` at
`fe6f55072`, run at plan time; none needed a prototype.

### spike-1: Does a labeled query set exist for the harness's endpoints, or must this lane invent one?
- **Assumption**: "The first experiment can compute `recall_at_k` and `mrr` only if some
  query-to-gold-memory labeling exists; otherwise the harness has endpoints and no ground truth."
- **Method**: code-read
- **Finding**: `tools/memory_eval/query_set.py::build_known_item_set(records, *, n_queries, seed)`
  (`:128`) already builds the objective label: it samples memories biased toward importance and
  LLM-generates one query per record whose answer is that record, skipping degenerate generations
  (`:100`). It routes through `agent.llm.run_typed` (`agent/llm/wrapper.py:139`, Haiku by default
  via the Anthropic API key, so **paid inference, unit 2**). `tools/memory_eval/hybrid_eval.py:183`
  is its one caller. The protocol shape lane 4 fixed (`runner.py:96-106`) takes exactly
  `{"trial_id", "query_text", "gold_id"}` per query.
- **Confidence**: high
- **Impact on plan**: the experiment freeze reuses `build_known_item_set` on the **exported**
  corpus records (so every `gold_id` exists in the frozen corpus), freezes the generated queries
  into the protocol beside `capture_baseline`'s output, and settles the generation cost against
  unit 2 through lane 3's meter. No second labeling mechanism is built.

### spike-2: Can the candidate arm vary anything but `limit`?
- **Assumption**: "Lane 4's arm boundary is a JSON job spec, so a retrieval-parameter candidate is
  expressible without touching the gates."
- **Method**: code-read
- **Finding**: `_retrieve_job` (`runner.py:291-299`) copies every `arm_params` key into the job
  except `mode`, but `handle_job` (`arm_worker.py:82-121`) reads only `limit` (`:114`) before
  calling `retrieve_ranked_ids(query_text, project_key, limit=)` (`retrieval.py:30`), and
  `retrieve_memories` accepts `rrf_k` and `min_rrf_score` as well (`agent/memory_retrieval.py:246-252`).
  `retrieval_mode` is read from settings inside `retrieve_memories` (`:276`) and is **not** a
  per-call parameter; varying it would mean varying the arm's environment, which lane 4's arena
  test `test_carries_the_four_arm_keys` pins.
- **Confidence**: high
- **Impact on plan**: this lane's candidate envelope is exactly `{limit, rrf_k, min_rrf_score}`.
  The build adds the two pass-throughs to `handle_job` and `retrieve_ranked_ids`, and nothing else
  inside `tools/improvement_eval/`. `retrieval_mode` is out of the envelope and the experiment
  validator refuses a manifest naming it.

### spike-3: Does the schema gate admit eight investigation kinds and the parent plan's lifecycle?
- **Assumption**: "Eight kinds fit; the eleven-value lifecycle does not."
- **Method**: code-read
- **Finding**: `DEFAULT_VOCABULARY_MAXIMUM = 8` (`tests/unit/test_improvement_models.py:92`);
  `INVESTIGATION_KINDS` has five (`models/improvement_investigation.py:45-51`), so eight fits
  without an exemption. `INVESTIGATION_STATES` has four (`:54`). The parent plan's lifecycle
  (`draft → deduplicated → policy_checked → running → recorded → interpreted → applied` plus
  `cancelled`, `superseded`, `failed`, `awaiting_authorization`) is eleven values, which the gate
  refuses on an index without a named exemption, and the exemption's own message says an index set
  per value "stops being cheap well before this".
- **Confidence**: high
- **Impact on plan**: the indexed `state` gains exactly one value, `awaiting_authorization`, used
  only by `charter_amendment` rows; the fine-grained step lives in a new plain field `stage`
  (never indexed; added to `FORBIDDEN_INDEX_NAMES`). Readers that need "what is the investigation
  doing" read `stage`; readers that need "is it open" read the index. A migration adds the field
  as additive-only, on the `_migrate_confirm_improvement_v2_fields` precedent.

### spike-4: Where does an immutable ranking snapshot live, and how is it referenced?
- **Assumption**: "The verifying artifact store can hold a content-addressed document outside a
  `ContentField`, the way lane 4 stores a protocol."
- **Method**: code-read
- **Finding**: `freeze_protocol` (`runner.py:181-194`) calls
  `verifying_artifact_store.save(payload, key=f"protocol-{digest[:16]}", model_class_name="ImprovementProtocol")`
  and returns a `$CF:<sha256>:...` reference; `VerifyingArtifactStore.load` re-hashes on every path
  (`models/verifying_artifact_store.py:70-118`). The retention root sits outside the checkout
  since lane 7.
- **Confidence**: high
- **Impact on plan**: `tools/improvement_ranking.py::write_snapshot` uses the identical call with
  `key=f"ranking-{digest[:16]}"` and `model_class_name="ImprovementRankingSnapshot"`; the
  `ranking_recorded` journal event carries the reference; `valor-improve ranking --at DIGEST`
  loads by reference and a corrupted snapshot raises `ArtifactIntegrityError` rather than
  rendering.

### spike-5: Can a function reflection carry a three-day cadence and a long-running evaluation?
- **Assumption**: "The registration seam accepts an arbitrary cadence, and a reflection tick can
  host the evaluation."
- **Method**: code-read
- **Finding**: `register_reflection` (`scripts/update/reflection_register.py:484-495`) takes
  `cadence: str | None` free-form and an optional `timeout`; `"259200s"` is legal. Function
  reflections default to `DEFAULT_FUNCTION_TIMEOUT = 1800` (`agent/reflection_scheduler.py:40`).
  A Bash tool call inside an eng session is bounded by
  `TOOL_TIMEOUT_DECLARED_MAX_SEC + TOOL_TIMEOUT_DECLARED_GRACE_SEC = 660s`
  (`docs/features/config-timeout-catalog.md:131`), while the eng turn itself has a 40-minute idle
  deadline and a 6-hour absolute one.
- **Confidence**: high
- **Impact on plan**: the planner tick never runs an evaluation. `valor-improve experiment evaluate`
  is invoked by the research session in the background (`run_in_background`) and polled, so
  neither the 1800-second reflection cap nor the 660-second tool cap bounds the trial count. The
  digest reflection registers with `cadence="259200s"` and no custom timeout.

### spike-6: Do outbound messages have a production writer the no-promises detector can read?
- **Assumption**: "`chat_message_log` entries with `direction="out"` are written on every outbound
  Telegram message, so the detector reads a field something fills in."
- **Method**: code-read
- **Finding**: writers at `bridge/telegram_bridge.py:3226`, `bridge/telegram_relay.py:390`,
  `:1308`, `:1511`; the field contract at `models/agent_session.py:685`;
  `agent/session_completion.py:364` already filters on `direction == "out"` for another purpose.
- **Confidence**: high
- **Impact on plan**: `collect_promises` reads the same session window as `collect_corrections`
  (`_recent_sessions`, `SESSION_SCAN_LIMIT`) and the outbound entries, samples them under a
  per-tick cap, and the detector's inputs are named in the module docstring and pinned by a test in
  the `TestDetectorInputsHaveProductionWriters` style.

## Data Flow

1. **Entry point: the evidence tick** (`reflections/improvement_collect.py::run_improvement_collect`,
   every `controller_tick_seconds`). Five adapters now: corrections, inspirations, expectation
   coverage, **lessons** (merged PR bodies via `gh`, the seven prefixes `sdlc_reflection.py`
   scraped, one `lesson` row per PR line, dedup on `source_ref="pr:{number}:{sha256(line)[:16]}"`),
   and **promises** (sampled outbound `chat_message_log` entries judged by a cheap model, one
   `promise` row per flagged message, dedup on `source_session_id` plus entry hash). Gated on
   `ImprovementSettings.enabled`.
2. **The planner tick** (`reflections/improvement_plan.py::run_improvement_planner`, same cadence,
   registered as `improvement-planner-tick`). In order: (a) `ImprovementCharter.load_from_file`
   then `pinned`; (b) **case opening**: cluster unconsumed evidence by `priority_area` heuristics
   (correction classified `architectural` → `orchestration`/`memory`; `lesson` → the stage's area;
   `inspiration` with a URL → an `inspiration_intake` investigation, not yet a case; `promise` →
   `personas`), run the novelty check (any `rejected` case or resolved investigation whose
   `summary`/`interpretation` shares the evidence cluster's dedup identity refuses re-opening and
   records why on the existing case), and write `ImprovementCase` rows with `charter_digest`,
   `priority_area`, and a `ranking_rationale` that names the charter passage; (c) **ranking**:
   `tools/improvement_ranking.py::rank(open_cases)` scores five ordinal factors per case
   (opportunity cost, quality, resource cost, uncertainty, unlocked capacity), each derived from
   named record fields and each stated as a rule in the module docstring, orders by the §3 starting
   hypotheses first and evidence second, and writes the immutable snapshot (ordered ids, factors,
   rationale, charter digest, diff against the previous snapshot); (d) `ranking_recorded` journal
   event on the case-independent controller head (lane 3's journal) carrying the snapshot
   reference; (e) **one action proposal** for the top-ranked case that is not blocked: an
   investigation intent if the case is `observed`/`investigating`, an experiment intent if a
   proposed experiment exists and no frozen one does. The proposal is a journal event; lane 3's
   scheduler adapter admits, reserves the lane slot, and dispatches the research session.
3. **The research session** (`claude -p`, skill `.claude/skills/improve-research/SKILL.md`,
   dispatched by lane 3). Its first action is `valor-improve brief --case ID`, which prints the
   bounded brief: the pinned charter verbatim, then the case, its evidence, prior investigations
   and rejected cases in the same area, the current ranking position, and the §9 resolution rule.
   It then runs investigations through `valor-improve investigation open --kind K` /
   `record --claims JSON` / `resolve --interpretation ... [--assumption ...]`. `web_research` and
   `resource_acquisition` use `WebSearch`/`WebFetch`; `inspiration_intake` uses
   `valor-youtube-transcribe` or `valor-ingest`; `trace_analysis` reads transcripts and events;
   `memory_retrieval` uses `memory_search`; `probe` runs a bounded command; `skill_acquisition`
   searches the library and the web; `charter_amendment` goes through lane 3's
   `propose-amendment`. Every claim is `{claim, url, retrieved_at}` or it is stored as a note.
   Unresolvable uncertainty becomes a `provisional_assumption` with charter passage, evidence,
   confidence, consequence, and overturning observation. Model revisions go through
   `valor-improve revise-model --summary --rationale --prediction`, and a revision with no
   prediction is refused.
4. **Hypothesis to frozen contract** (`tools/improvement_experiment.py`). The session proposes a
   hypothesis through lane 3's `valor-improve propose` (journal-authorized); `valor-improve
   experiment freeze --case ID` validates the candidate against this lane's envelope (retrieval
   parameters only), exports the corpus (`export_corpus`), builds the known-item query set on the
   exported records (`build_known_item_set`, unit-2 metered), captures the baseline
   (`capture_baseline`), freezes the protocol (`freeze_protocol`), writes the manifest with the
   `protocol_ref`, computes and stores `contract_digest`, sets `state="frozen"` and `frozen_at`,
   and records `model_revision_id` and `charter_version`. Hashing happens before any arm runs.
5. **Evaluation** (`valor-improve experiment evaluate --id`, run in the background from the
   session): calls `tools.improvement_eval.runner.evaluate(experiment_id, project_key)`. Every
   gate, the arena, blinding, Holm, and the verdict are lane 4's; this lane passes arguments and
   reads the result.
6. **Verdict to selection** (`tools/improvement_experiment.py::apply_verdict`): `reject` moves the
   case to `rejected` with the evaluation id on the case (the memory that stops re-proposal);
   `accept` leaves the case at `evaluating` with the verdict recorded, for lane 6; `inconclusive`
   returns the case to `investigating` with the evaluation attached and raises its `uncertainty`
   factor; `infra_failure` and `invalidated` leave the case where it was, mark the experiment
   `aborted`, and record a `probe` investigation naming the failure. Each transition is a journal
   event first and an ORM `save()` second.
7. **The next tick** re-ranks. The rejected case has left the open set; the snapshot's diff names
   it under `left` with the evaluation id as the reason. The novelty check refuses a new case with
   the same identity. That diff is the demonstration the acceptance criterion asks for.
8. **Outputs**: the dashboard partials (`/_partials/improvement/ranking/`, `/hypotheses/`,
   `/rejected/`) read the latest snapshot, the experiments in `proposed`/`frozen`/`running`, and
   the `rejected` cases with their evaluations; `valor-improve ranking [--at DIGEST]` prints any
   snapshot; the three-day digest (`reflections/improvement_assumption_digest.py`) sends new
   assumptions, `resource_acquired` rows, and lane 7's overrun payloads to Telegram via
   `send_host_eng_telegram` as a status report; `valor-improve report --case ID` generates the
   qualified-result report from the records.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #932, `scripts/sdlc_reflection.py` | Scraped `- lesson:` lines from merged PR bodies and appended them verbatim to `docs/sdlc/{stage}.md` under "Reflection Notes (auto-generated)" every three days | A lesson entered the docs on the strength of having been typed. Nothing checked whether the lesson was true, whether it changed any later behavior, or whether it contradicted an earlier lesson. The 300-line cap (`:197-205`) silently skipped whole stages once a file filled, so the mechanism also degraded without notice. It measured that PR authors write bullet points |
| #410 / PR #411, autoexperiment | Hypothesize, edit, run a single judge, accept on strict inequality | No isolation, no blinding, no correction for repeated selection, and a judge whose noise exceeded the effects it scored. Never ran on this machine; retired in lane 1 |
| `TaskTypeProfile.rework_rate` (deleted in lane 2) | Aggregated a session flag into a delegation recommendation | The flag had no writer, so the aggregate was structurally zero and the recommendation was derived from nothing |

**Root cause pattern:** each earlier mechanism produced an output whose input had no validated
connection to behavior. This lane's rule is the inverse: a lesson, an inspiration, or a claim is
evidence only; it changes the system's behavior only after it has been ranked, turned into a
hypothesis with a falsifier, frozen, and measured by the harness, and the record shows every step.

## Architectural Impact

- **New dependencies**: none. `gh` (already used by `reflections/sdlc_progress.py::_run_gh`),
  `WebSearch`/`WebFetch` (already the research session's tools), `agent.llm.run_typed` (already
  the known-item builder's route), and lane 4's harness. No new package.
- **Interface changes**: `tools/improvement_eval/arm_worker.py::handle_job` and
  `retrieval.py::retrieve_ranked_ids` gain two optional keys (`rrf_k`, `min_rrf_score`), defaulting
  to `retrieve_memories`'s own defaults; an absent key produces byte-identical behavior to lane 4's
  code. `INVESTIGATION_KINDS` grows from five to eight; `INVESTIGATION_STATES` from four to five;
  `EVIDENCE_KINDS` from seven (eight with lane 3) to ten. `ui/data/improvement.py` exports seven
  getters. `valor-improve` gains six subcommand groups. `reflections/improvement_collect.py`'s
  adapter tuple grows from three to five.
- **Coupling**: the planner reads records and writes journal events; it never imports the
  research skill, the bridge, or the harness internals. The research session reaches state only
  through `valor-improve`. The harness is called at one function (`evaluate`) plus three helpers
  (`capture_baseline`, `freeze_protocol`, `compute_contract_digest`). Coupling to lane 3 is through
  the CLI's argparse tree, the journal's `transition`, and the scheduler adapter's intent record,
  each stated as a requirement in Technical Approach.
- **Data ownership**: this lane is the first writer of `ImprovementCase`,
  `ImprovementModelRevision`, `ImprovementInvestigation`, and `ImprovementExperiment`, and the
  owner of the ranking snapshot artifact. It writes `ImprovementEvaluation` never (lane 4's runner
  is the single writer) and `ImprovementRelease` never (lane 6). The charter file and model stay
  unwritten.
- **Reversibility**: the two new evidence kinds and the `stage` field are additive; removing them
  leaves orphan index sets that `Model.rebuild_indexes()` clears. The deletion of
  `scripts/sdlc_reflection.py` is reversible by revert; its launchd job is booted out fleet-wide
  by the obsolete-service sweep and would need reinstalling. Ranking snapshots are immutable
  artifacts and are never deleted.

## Appetite

**Size:** Large

**Team:** Lead orchestrator, five builders in three waves, one validator per wave, one
documentarian, one final validator.

**Interactions:**
- PM check-ins: 2-3 (the reconciliation of the issue body against charter v2 in Freshness Check;
  the deferral of the `skill_acquisition` evaluation step; the interpretation of the first real
  cycle's result)
- Review rounds: 2+ (the parent plan's lanes have each taken two or more)

This lane is the integration point of four others, and its appetite is dominated by sequencing:
the build cannot start until lanes 3 and 4 merge, and the first real cycle on this machine is a
run the builder observes rather than a test the builder writes. Everything that can be exercised
against seeded records in a test database is; the real cycle is run once, its records are the
evidence, and its report is posted to #3217. Scope that would push past Large is named in No-Gos
and carried to a filed follow-up, not squeezed in.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Lane 4 merged: the harness surface | `python -c "from tools.improvement_eval.runner import evaluate, capture_baseline, freeze_protocol, compute_contract_digest; from tools.improvement_eval.arm_worker import handle_job"` | The four calls this lane makes and the one file it edits inside the harness |
| Lane 4 merged: `charter_digest` on the evaluation record | `python -c "from models.improvement_evaluation import ImprovementEvaluation as E; assert hasattr(E,'charter_digest')"` | `apply_verdict` records the charter the verdict was measured under |
| Lane 3 merged: the CLI module | `python -c "import tools.improvement"` | The argparse tree this lane's subcommands attach to |
| Lane 3 merged: the control journal | `python -c "import importlib.util as u; assert any(u.find_spec(n) for n in ('tools.improvement_journal','agent.improvement_journal','tools.improvement_control','models.improvement_journal')), 'no control journal module found'"` | `ranking_recorded` and every case transition are journal events first |
| Lane 3 merged: the paid-inference meter | `python -c "import tools.paid_inference_meter"` | The known-item builder, the promise judge, and the `serves_charter` judge settle against unit 2 |
| Lane 3 merged: the research skill exists | `test -f .claude/skills/improve-research/SKILL.md` | This lane extends it with the brief contract and the eight kinds |
| Lane 3 merged: `resource_acquired` evidence kind | `python -c "from models.improvement_evidence import EVIDENCE_KINDS as K; assert 'resource_acquired' in K"` | The digest renders it in its own section |
| `redis-server` binary | `redis-server --version` | Lane 4's per-arm private Redis |
| Python pin matches the repo | `python -c "import pathlib,sys; want=pathlib.Path('.python-version').read_text().strip(); got='.'.join(map(str,sys.version_info[:3])); sys.exit(0 if got.startswith(want) else 1)"` | Worktree venv is on the committed pin |
| Known-item builder importable | `python -c "from tools.memory_eval.query_set import build_known_item_set"` | spike-1: the labeled query set is reused, not rebuilt |
| Verifying artifact store writable | `python -c "from models.verifying_artifact_store import verifying_artifact_store as s; import os; os.makedirs(s.base_path, exist_ok=True)"` | Ranking snapshots and protocols land here |
| `gh` authenticated | `gh auth status` | The lesson adapter reads merged PR bodies |
| Anthropic API key resolvable (never printed) | `python -c "from utils.api_keys import get_anthropic_api_key as g; assert g()"` | `build_known_item_set` routes through `run_typed` (`agent/llm/wrapper.py:208`) |

The three lane-3 rows are written against the names the parent plan and #3215's body use
(`tools/improvement.py`, `tools/paid_inference_meter.py`, `.claude/skills/improve-research/SKILL.md`).
The journal row is deliberately tolerant of the module name because lane 3 has not planned yet;
if lane 3 lands the journal under another name, the build corrects this row in its first commit
and the correction is the record of the contract's actual shape. **The build does not start until
every row passes**; `python scripts/check_prerequisites.py docs/plans/improvement-controller-lane-5-first-complete-research-cycle.md`
is the gate, and a failing lane-3 or lane-4 row means "wait for that merge", never "stub it".

## Solution

### Key Elements

- **Planner tick** (`reflections/improvement_plan.py`): the deterministic controller. Pins the
  charter, opens cases from evidence, ranks them, writes the immutable snapshot and its journal
  event, and proposes exactly one action per tick. Never runs an LLM, never dispatches, never
  evaluates.
- **Ranking snapshot** (`tools/improvement_ranking.py`): the durable ordered artifact. Five ordinal
  factors per case with the rule that produced each, the order, the rationale, the charter digest,
  and the diff against the previous snapshot. The dashboard and `valor-improve ranking` read it;
  nothing re-derives it.
- **Investigation lifecycle** (`tools/improvement_investigations.py` plus the model change): eight
  kinds, the claim rule, provisional assumptions with their interpretation, the novelty check,
  and `awaiting_authorization` for amendment requests only.
- **The brief** (`tools/improvement_brief.py`, `valor-improve brief`): what a research session
  reads first. Opens with the pinned charter verbatim, then the case and its evidence, prior
  answers, and the §9 resolution rule.
- **Experiment freeze and verdict application** (`tools/improvement_experiment.py`): the candidate
  envelope, the frozen contract built from lane 4's helpers and the known-item builder, the
  background evaluation call, and the verdict-to-case-state rule.
- **Two observer adapters** in `reflections/improvement_collect.py`: `collect_lessons` (the
  `sdlc_reflection.py` fold-in) and `collect_promises` (charter §10, sampled, cheap-model judged).
- **The assumption digest** (`reflections/improvement_assumption_digest.py`): the three-day status
  report to Telegram that asks nothing and says so.
- **Three dashboard partials**: ranking with movement, hypotheses in flight, rejected approaches
  with their evaluations.
- **The qualified-result report** (`tools/improvement_report.py`, `valor-improve report`): generated
  from records; states what was measured, what it does not establish, and what would change the
  answer.
- **Retirement of `scripts/sdlc_reflection.py`**: the script, its installer, its plist, its docs
  rows, and its launchd job (via the obsolete-service sweep) are gone.

### Flow

**Evidence tick** → five adapters write `ImprovementEvidence` → **Planner tick** → charter pinned
→ cases opened (novelty-checked) → ranking snapshot written, `ranking_recorded` journaled → one
action proposed → **lane 3 admits and dispatches** → **Research session** → `valor-improve brief`
(charter first) → investigations (`web_research`, `resource_acquisition`, `memory_retrieval`,
`trace_analysis`, `probe`, `inspiration_intake`, `skill_acquisition`; `charter_amendment` only via
`propose-amendment`) → claims, assumptions, model revision → hypothesis proposed → `valor-improve
experiment freeze` (envelope validated, corpus exported, queries built, baseline captured, protocol
frozen, contract hashed) → `valor-improve experiment evaluate` in the background → lane 4's
harness writes the verdict → `apply_verdict` moves the case → **Next planner tick** → new snapshot
shows the move → `valor-improve report` → report posted on #3217. Every three days: **assumption
digest** to Telegram. Continuously: dashboard partials read the records.

### Technical Approach

#### Consumed from lane 3 (#3215), stated as requirements

Lane 3 has no plan yet. This lane needs the following and adapts to whatever names lane 3 lands;
each is checked by a Prerequisites row and the build's first commit corrects any name.

| Need | Requirement | Where this lane calls it |
|---|---|---|
| Journal event | A function that records `(case_id or controller head, action_id, event, payload_digest)` through the Lua `transition`, returns a reason code on refusal, never raises | `ranking_recorded` on the case-independent controller head each tick; `case_opened`, `investigation_opened`, `hypothesis_proposed`, `experiment_frozen`, `verdict_applied` per case |
| Action proposal | `valor-improve propose` accepts a proposed action (investigation or experiment) for a case, refuses an unranked case (empty `ranking_rationale`, unset `priority_area`, or a digest that is not the pinned one) with a reason code, and writes the intent the scheduler adapter reads | The planner tick calls the same Python function the CLI wraps; the research session calls the CLI |
| Dispatch | The scheduler adapter admits an intent, reserves the lane slot, checks worker liveness, and dispatches a research session running the `improve-research` skill with `research_case_id`, `action_id` provenance and the case id available to the session | The planner never dispatches; it proposes |
| Amendment request | `valor-improve propose-amendment` records a `charter_amendment` investigation in `awaiting_authorization` and sends one plain Telegram message | The research session, when a decision depends on ungranted authority |
| Paid-inference meter | A reserve/settle pair keyed by a purpose string, settling from the provider's usage envelope or a dated price table, marking `metering="estimated"` otherwise | `build_known_item_set` generation, the promise judge, and the `serves_charter` judge's calls during evaluation |
| Vault write | `tools/vault_write.py` writes a `resource_acquired` evidence row on success | The digest renders that row; this lane never calls `vault_write` (no credential is placed) |
| CLI tree | `tools/improvement.py::main` with an argparse subparser registry this lane can add to | `brief`, `ranking`, `investigation`, `revise-model`, `experiment`, `report` |
| Research skill | `.claude/skills/improve-research/SKILL.md` exists, project-only, and instructs `valor-improve propose` | This lane rewrites its body to the brief-first contract below; lane 3's authorship of the file is preserved in git history |

If lane 3 lands `propose` without a Python-callable seam, the planner shells to the CLI; the
requirement is the refusal semantics, not the call shape.

#### Provided to lane 6 (#3218), from its 2026-09-14 comment on #3217

Lane 6's plan (`docs/plans/improvement-controller-lane-6-promotion-rollback-and-recursion.md` on
`session/sdlc-3218`) names three seams it leaves open for this lane. None blocks lane 6; each is
built here so the recursive comparison can run on real arms later.

1. **Process digest.** Every `ImprovementModelRevision` this lane writes carries
   `research_process_digest` computed from a `ResearchProcessSpec` with these values:
   `selection_rule="ordinal-lexicographic-v1"`, `investigation_budget_split={}` (this lane splits
   nothing), `revision_cadence_seconds=settings.improvement.controller_tick_seconds`,
   `planner_prompt_digest=sha256` of the brief template text, `skill_digest=sha256` of
   `.claude/skills/improve-research/SKILL.md`, `extra={"ranking_module_digest": sha256 of
   tools/improvement_ranking.py}`. When `tools.improvement_recursion.process.research_process_digest`
   is importable it is called; otherwise `tools/improvement_ranking.py::process_digest` computes
   the identical canonical form (`json.dumps(asdict(spec), sort_keys=True, separators=(",", ":"))`,
   `"sha256:<hex>"`) so the two agree byte for byte once both exist. A test pins the canonical form
   against a fixture spec.
2. **Arm runner.** The planner tick's core is a pure function,
   `reflections/improvement_plan.py::plan_tick(project_key, *, process_spec, case_ids=None,
   budget_cap=None, arm_run_id=None) -> TickResult`, and `run_improvement_planner` is its
   reflection wrapper. `tools/improvement_plan_arm.py::PlannerArmRunner.run(process_digest,
   opportunity_ids, budget_cap, arm_run_id)` runs one tick under the pinned spec restricted to the
   named opportunities and returns lane 6's `ArmResult`: per-opportunity gains taken from
   `accept` evaluations **already on record** for those cases and budget use from lane 3's meter
   keyed by `arm_run_id`. Its docstring states the limit: a multi-tick arm that dispatches and
   evaluates inside `run` is #3311's. `tools/improvement.py`'s CLI entry calls
   `register_arm_runner(PlannerArmRunner())` when lane 6's `tools.improvement_recursion.arms` is
   importable, so `compare run` under the same CLI finds it; a test asserts `get_arm_runner()`
   returns it after CLI init and that nothing registers at import time.
3. **`candidate_ref` and `base_revision` on the manifest.** This lane's manifest is
   `{"protocol_ref", "base_revision", "candidate_ref", "candidate", "incumbent", "envelope",
   "corpus_digest"}`. `base_revision` is the checkout SHA at freeze (lane 4's key); `candidate_ref`
   is the same SHA for a parameter-only candidate and is written explicitly so lane 6's `propose`
   reads it rather than refusing `MANIFEST_LACKS_BASE_REVISION`; a candidate that changes code
   (#3311) writes the candidate branch's ref there.

#### Consumed from lane 4 (#3216), exact calls

- `tools.improvement_eval.corpus.export_corpus(project_key) -> CorpusExport` (`corpus.py:145`):
  the frozen corpus and its digest; its `records` are what the known-item builder samples from.
- `tools.improvement_eval.runner.capture_baseline(project_key, queries, *, incumbent, export)`
  (`runner.py:309`): the incumbent's ranked ids on the frozen corpus.
- `tools.improvement_eval.runner.freeze_protocol(protocol, *, store) -> "$CF:..."` (`runner.py:181`).
- `tools.improvement_eval.runner.compute_contract_digest(experiment)` (`runner.py:168`): computed
  once at freeze and stored; the runner recomputes it at Gate 0.
- `tools.improvement_eval.runner.evaluate(experiment_id, project_key)` (`runner.py:534`): the
  single writer of `ImprovementEvaluation`. This lane passes no `judges` override in production;
  the default roster is lane 4's calibrated `serves_charter` judge.
- `tools.improvement_eval.runner.repair_wedged_experiment(project_key=, experiment_id=)`
  (`runner.py:220`): wrapped as `valor-improve experiment repair --id`, the break-glass for a
  `running` experiment whose evaluation died.
- The one edit inside the harness: `arm_worker.py::handle_job` passes `rrf_k` and
  `min_rrf_score` through when present (`:114` reads `limit` today), and
  `retrieval.py::retrieve_ranked_ids` accepts them as keyword-only optionals forwarded to
  `retrieve_memories`. Absent keys are not passed, so lane 4's parity tests stay byte-identical.

#### Records

- `INVESTIGATION_KINDS` becomes `("web_research", "memory_retrieval", "trace_analysis", "probe",
  "resource_acquisition", "inspiration_intake", "skill_acquisition", "charter_amendment")`: the
  five existing values in place, three appended. Eight is the gate's maximum and the docstring
  says so, so a ninth kind costs an argument.
- `INVESTIGATION_STATES` becomes `("open", "awaiting_authorization", "resolved", "abandoned",
  "expired")`; `awaiting_authorization` is legal only when `kind == "charter_amendment"`, enforced
  by `tools/improvement_investigations.py::open_investigation` and `transition_investigation`.
- New plain field `stage = Field(null=True)` on `ImprovementInvestigation`: one of `draft`,
  `deduplicated`, `policy_checked`, `running`, `recorded`, `interpreted`, `applied`, `cancelled`,
  `superseded`, `failed`. Unindexed; added to the test's `FORBIDDEN_INDEX_NAMES`. The lifecycle
  helper advances it in order and refuses a skip.
- New plain fields on `ImprovementInvestigation`, all `null=True`: `sources` (JSON list of
  `{url, retrieved_at, title}`, the raw source pointers a claim keeps), `prior_answers` (JSON list
  of investigation and case ids the novelty check surfaced), `expected_information_value`
  (free text), `decision_affected` (free text), `assumption_detail` (JSON:
  `{charter_passage, evidence_ids, confidence, consequence, overturning_observation}`).
- `EVIDENCE_KINDS` gains `lesson` and `promise`. The `VOCABULARY_MAXIMUMS` entry
  `(ImprovementEvidence, "kind"): 10` carries the argument: each is written by its own adapter and
  read by its own consumer (the planner's case-opening rules and the dashboard's burden panel), so
  each needs its own index set; a `lesson` coerced to `other` is unqueryable as a lesson.
- New plain fields on `ImprovementCase`, `null=True`: `evaluation_ids` (JSON list),
  `rejected_reason` (free text set by `apply_verdict`), `dedup_identity` (the evidence cluster
  identity the novelty check compares; a plain string, never indexed).
- `ImprovementExperiment` is unchanged; `candidate_surfaces` holds the envelope keys the candidate
  varies, and `manifest` holds `{"protocol_ref", "base_revision", "candidate_ref", "candidate":
  {...}, "incumbent": {...}, "envelope": "retrieval_parameters", "corpus_digest"}` (the two ref
  keys per lane 6's request, "Provided to lane 6").
- Two migrations in `scripts/update/migrations.py`, registered in `MIGRATIONS`, idempotent:
  `improvement_investigation_stage_field` (additive confirm, on the
  `_migrate_confirm_improvement_v2_fields` precedent at `:1429`) and `retire_sdlc_reflection`
  (removes `data/sdlc_reflection_last_run.json` if present and records the retirement).

#### Observer adapters

- **`collect_lessons(project_key)`**: runs `gh pr list --state merged --search "merged:>=<since>"
  --json number,title,body,mergedAt` through the same `_run_gh` shape `reflections/sdlc_progress.py:223`
  uses, with `since` = the newest `lesson` row's `observed_at` or 14 days; extracts the seven
  prefixes `sdlc_reflection.py:153-161` scraped; writes one `lesson` row per line with
  `source_ref="pr:{number}:{sha256(line)[:16]}"`, `text=line`, `detail=JSON{title, stage_guess}`
  where `stage_guess` reuses the `STAGE_KEYWORDS` table moved into the adapter, and
  `observed_at=mergedAt`. Fail-soft: a `gh` failure yields zero rows and a warning, never a raise.
- **`collect_promises(project_key)`**: reads the same session window as `collect_corrections`,
  takes `direction="out"` entries, samples up to `PROMISE_SAMPLE_PER_TICK = 10` newest unseen
  entries (dedup on `source_session_id` plus entry hash in `source_ref`), and asks a cheap model
  one yes/no question per entry with the charter §10 paragraph quoted: "Does this message
  guarantee delivery, future effort, future communication, or an outcome the sender does not
  control, without qualification?" A `yes` writes a `promise` row with `text` = the entry,
  `detail` = the judge's quoted span, `confidence` = the judge's stated confidence. The judge routes
  through lane 3's meter under the purpose `promise_detector`; when the meter refuses (unit 2
  exhausted or `metering="unknown"`), the adapter writes nothing and records
  `findings.append("promises-skipped: unit 2 unavailable")`, which the tick summary reports.
  Gated additionally by `ImprovementSettings.promise_detector_enabled` (new, default `False`,
  `IMPROVEMENT__PROMISE_DETECTOR_ENABLED`, declared `# @optional` in `.env.example` with a
  `Field(description=...)` sentence). **Off by default because it spends money**; turning it on
  is a deliberate act on the owning machine.
- **Retirement**: `scripts/sdlc_reflection.py`, `scripts/install_sdlc_reflection.sh`, and
  `com.valor.sdlc-reflection.plist` are deleted; `"sdlc-reflection"` joins
  `OBSOLETE_SERVICE_SUFFIXES` (`scripts/update/service.py:39-51`) with a comment naming this plan;
  the `/update` and `/setup` skill lines and the four feature-doc references go; the `docs/sdlc/`
  stub files keep any existing "Reflection Notes (auto-generated)" sections as they are (they are
  history, and `create_sdlc_stubs` is unrelated and stays).

#### Case opening and the novelty check

- `reflections/improvement_plan.py::open_cases(project_key, charter)` reads evidence rows newer
  than the last tick's watermark (stored on the controller head payload, not in a file), groups
  them by a **dedup identity**: for `correction` rows, the classification plus the normalized first
  eight words of `text`; for `lesson` rows, the `stage_guess` plus the same normalized prefix; for
  `promise` rows, the constant `"unqualified-promises"` (one case, growing evidence); for
  `inspiration` rows with a URL, no case: an `inspiration_intake` investigation is opened instead,
  `stage="draft"`, with the URL in `sources`, for the research session to fulfil.
- A cluster becomes a case when it has at least `CASE_OPEN_MIN_EVIDENCE = 2` rows (provisional,
  tunable) or a single `architectural` correction. The case carries `priority_area` from a rule
  table in the module docstring (architectural correction → `orchestration`; `lesson` for
  `do-build`/`do-patch` → `orchestration`, for `do-test`/`do-pr-review` → `evaluators`, for
  `do-plan`/`do-plan-critique` → `research_process`, for `do-docs`/`do-merge` → `other`;
  `promise` → `personas`; a `memory_retrieval`-sourced cluster → `memory`), a `ranking_rationale`
  that names the charter passage, `charter_digest` from the pinned row, `alternative_explanations`
  with at least one alternative reading (a template the rule table supplies; the research session
  replaces it), and `dedup_identity`.
- **The novelty check** runs before every open: any `ImprovementCase` (any state) or resolved
  `ImprovementInvestigation` with the same `dedup_identity` refuses the open. A `rejected` match
  appends the new evidence ids to the existing case's `evidence_ids` and writes a journal event
  `evidence_attached_to_rejected` naming the evaluation that rejected it; the case stays
  `rejected`. That is the mechanism behind "a rejected hypothesis is not re-proposed on the next
  tick", and the integration test seeds a rejected case and re-runs the tick to prove it.
- The research session may open cases too (`valor-improve case open`), through the same function
  and the same novelty check.

#### Ranking

- `tools/improvement_ranking.py::rank(cases, *, evidence, investigations, experiments, charter)`
  returns an ordered list of `RankedCase(case_id, position, factors, reason)` where `factors` is
  five ordinals in `{1, 2, 3}` (low, medium, high), each derived by a rule the module docstring
  states and a test pins per rule:
  - **opportunity cost**: high when the case's `priority_area` is one of the five §3 starting
    priorities and no other open case in that area ranks above it; medium for the other five
    named means; low for `other`.
  - **quality**: high when the case has at least one `architectural` correction or three or more
    evidence rows; medium for two; low for one.
  - **resource cost**: low when the case's likely action is an investigation; medium when a
    frozen experiment exists in this lane's envelope; high when the case needs an arm shape this
    lane does not have (an `agent_run`-shaped hypothesis) or a credential that is not in the vault.
  - **uncertainty**: high when no investigation has resolved for the case; medium when one has;
    low when a model revision cites it. An `inconclusive` verdict resets it to high.
  - **unlocked capacity**: high for `inference`, `cloud_execution`, `research_process`; medium for
    `skills`, `evaluators`, `memory`, `orchestration`; low otherwise.
- Order: by a lexicographic key `(blocked, -opportunity_cost, -unlocked_capacity, -quality,
  resource_cost, -uncertainty, created_at)`, where `blocked` is true when the case's next action
  cannot run (resource cost high for a missing credential or arm shape). Blocked cases keep their
  position in the printed list with a `blocked_by` reason; the tick's single proposal goes to the
  first unblocked case. **This is how "cheap inference ranks first and journey preservation is
  eligible" both hold at once**: the inference case sits at position 1 blocked on a vault request,
  and the first experimentable case is the first unblocked position.
- Ordinal, not numeric: no weights, no sums. The parent plan says "ordinal until calibration
  supports numbers" (`recursive-self-improvement.md:392`); a numeric score here would be a number
  nobody calibrated.
- `write_snapshot(ranked, *, previous_ref, charter_digest, store) -> str`: canonical JSON
  `{"schema": 1, "charter_digest", "at", "order": [{case_id, position, factors, reason,
  blocked_by}], "intake_pool": [investigation ids not yet cases], "previous_ref", "diff":
  {"entered": [...], "left": [{case_id, reason}], "moved": [{case_id, from, to, why}]}}`, saved
  through the verifying store as spike-4 describes. The `at` timestamp and `previous_ref` make
  the chain reconstructable from any snapshot. The latest reference is stored on the controller
  head payload by the `ranking_recorded` event.
- `load_snapshot(ref)` and `latest_snapshot(project_key)` back both the dashboard and
  `valor-improve ranking [--at DIGEST]`; a `--at` that does not verify prints the
  `ArtifactIntegrityError` and exits 2.

#### The planner tick

- `run_improvement_planner()` is a function reflection registered as `improvement-planner-tick`
  with `cadence=f"{settings.improvement.controller_tick_seconds}s"` through
  `register_improvement_planner(project_dir)` beside `register_improvement_collect`
  (`reflection_register.py:625`), called from `scripts/update/run.py` and returning a
  `RegisterResult` on the run result dataclass. Owner-gated by `_this_machine_owns_valor`.
- Gate: `settings.improvement.enabled`, same shape as the evidence tick (`status="skipped"`).
- Order per tick, each step fail-soft and reported in `counts`: `load_from_file` then `pinned`
  (a missing or unreadable charter ends the tick with `status="error"` and writes nothing, because
  a case with no digest cannot be ranked); `open_cases`; `rank` + `write_snapshot` +
  `ranking_recorded`; `propose_one_action`.
- **One proposal per tick, idempotent.** The action id is
  `sha256(case_id + snapshot_ref + action_kind)[:16]`, so a re-run of the same tick proposes the
  same action and lane 3's intent record dedups it. A case with an intent already `admitted`,
  `materialized`, or `running` is skipped as busy.
- The controller head payload carries `{last_snapshot_ref, evidence_watermark, last_tick_at}`;
  the tick reads it first and refuses to run when the head is `paused` (lane 3's break-glass).
- No LLM call anywhere in the tick. The one place judgment enters is the research session.

#### The brief and the research skill

- `tools/improvement_brief.py::build_brief(case_id, project_key) -> str` renders, in this order:
  the pinned charter's full text verbatim (from `ImprovementCharter.text`), a line naming its
  version and digest; the case (title, summary, `priority_area`, `ranking_rationale`, position and
  factors from the latest snapshot); its evidence rows (text, kind, observed_at); prior answers in
  the same `priority_area` (resolved investigations' interpretations, rejected cases with their
  `rejected_reason` and evaluation ids); open investigations for the case; the §9 resolution rule
  ("evidence and investigation first, then a guarded provisional assumption, then deferral plus an
  amendment request when authority is missing"); the claim rule; the candidate envelope for this
  lane; and the list of `valor-improve` subcommands the session may use. Bounded: evidence is
  capped at `BRIEF_MAX_EVIDENCE = 40` rows and prior answers at 20, newest first, with the
  truncation stated in the brief.
- `valor-improve brief --case ID` prints it. A test asserts the first non-blank line of the
  brief is the charter's first heading and that the charter text appears byte-identical.
- `.claude/skills/improve-research/SKILL.md` (lane 3's file, rewritten body): step 1 is
  `valor-improve brief --case $CASE_ID`; step 2 states the eight kinds and what each may use;
  step 3 the claim rule and the assumption rule; step 4 `revise-model` when the evidence changes
  the system's model of itself; step 5 `propose` a hypothesis with mechanism and falsifier inside
  the envelope, or `propose-amendment` when authority is missing; step 6 `experiment freeze`,
  `experiment evaluate` in the background, poll `experiment show`, then `report`. The skill says
  in its own text: no `AskUserQuestion`, no Telegram send, no session creation, no `.env`, no `op`.

#### Investigations

- `tools/improvement_investigations.py`: `open_investigation(project_key, *, kind, case_id,
  uncertainty, query, decision_affected, expected_information_value, expires_at=None)` runs the
  novelty check (`prior_answers` filled from matching resolved investigations), sets
  `stage="deduplicated"` or `"draft"` accordingly, `state="open"`; `record_claims(investigation_id,
  claims, sources)` validates every entry: `{claim, url, retrieved_at}` with a parseable
  `retrieved_at` and an `http(s)` URL is a claim; anything else is stored under `notes` in the
  `claims` JSON with `"is_claim": false`, never dropped and never promoted; `resolve(investigation_id,
  *, interpretation, provisional_assumption=None, assumption_detail=None)` sets `stage="interpreted"`
  and `state="resolved"`, and refuses a `provisional_assumption` whose `assumption_detail` lacks
  any of `charter_passage`, `confidence`, `consequence`, `overturning_observation`; a resolve with
  an assumption also writes the assumption's summary onto the case's `summary` tail so it survives
  the 30-day TTL.
- The assumption guard: `assumption_detail.consequence` is checked against four refusal patterns
  (redefines the intended outcome, erases a requirement, grants authority, increases a budget)
  by a rule the module states; a match is refused with a reason code and the session is told to
  defer the decision and use `propose-amendment`. This is a text rule and the docstring says a
  text rule catches only the phrasing it names; the `serves_charter` judge and the digest are the
  backstops.
- Kinds, what each records, and what runs it:
  - `web_research`: query, URLs, retrieval dates, claims; the session's `WebSearch`/`WebFetch`.
  - `memory_retrieval`: the `memory_search` query and the memory ids read; the session.
  - `trace_analysis`: the session or event ids read and what they showed; the session.
  - `probe`: the command run (bounded, recorded verbatim) and its observed result; the session.
  - `resource_acquisition`: provider, documentation URLs and dates, price, terms that matter to
    §7 (training on inputs, retention), what an adapter would cost, and the disposition (`prepared`,
    `keyless_integrated`, `vault_request_written`, `unsuitable`); the session.
  - `inspiration_intake`: the source URL, the extraction route (`valor-youtube-transcribe`,
    `valor-ingest`, `WebFetch`), the extracted substance (capped, stored on the row), and
    `accessible: bool`; an inaccessible source is recorded as such, never as reviewed; the session.
  - `skill_acquisition`: the observed gap (evidence ids), candidates found (library, web, with
    URLs and dates), the vetting result, the integration made (a skill directory under
    `.claude/skills/` in the lane worktree, or a proposal), and the evaluation disposition, which
    in this lane is always `deferred: no agent-run arm` with the follow-up issue cited; the session.
  - `charter_amendment`: through lane 3's `propose-amendment` only; `state="awaiting_authorization"`;
    resolved when the charter file's digest changes (the tick notices a new pinned digest and
    resolves every awaiting row with `interpretation="charter digest changed to ..."`) or when the
    session records a decline.

#### The first resource-acquisition action

- The build seeds one case in `priority_area="inference"` from the charter §3 first priority
  (evidence: an `inspiration` row the builder writes citing charter §3, so the case has real
  evidence and the novelty check has an identity). Its investigation of kind
  `resource_acquisition` targets "Muse Spark 1.3 (Meta) and any other nearly-free token source" and
  the research session records what current documentation says with URLs and dates.
- The action ends in one of two dispositions and no third: (a) a source needing **no new
  credential** (an OpenRouter `:free` model through the key already in the vault) is integrated as
  a config entry behind `ImprovementSettings.cheap_inference_model` (new, default `""` meaning
  off, `IMPROVEMENT__CHEAP_INFERENCE_MODEL`, `# @optional`), read only by the promise judge and by
  nothing on a client path, with `tools/improvement_eligibility.is_open_source` checked at the call
  site; (b) a source needing a credential produces a **prepared adapter** (a provider entry in
  `config/models.py` guarded by a settings field that defaults off, plus the call-site eligibility
  check) and a **written vault request**: an `ImprovementInvestigation` row whose `interpretation`
  names the vault item title the adapter expects (`Meta Model API key` under `m-valor`), the
  fingerprint field it will verify through `tools/improvement_resources.probe`, and the terms
  clause that confines it to open-source work. The request reaches Tom in the three-day digest
  under its own heading. **No controller module places a credential**, writes `.env`, or invokes
  `op`; the Verification table asserts it.
- The case then ranks at position 1 **blocked** with `blocked_by="vault request: Meta Model API
  key"` until `probe` reports the item `verified`, at which point the block lifts on the next tick
  and lane 5b (No-Gos) owns the experiment that would use it.

#### Experiments

- **Envelope**: `tools/improvement_experiment.py::ENVELOPES = {"retrieval_parameters": {"limit":
  (1, 50), "rrf_k": (1, 200), "min_rrf_score": (0.0, 1.0)}}`. `validate_candidate(candidate)`
  refuses any key outside the envelope, any value outside its range, and a candidate identical to
  the incumbent. The incumbent is the production defaults (`limit=10`, `rrf_k=None`,
  `min_rrf_score=None`), read from `retrieve_memories`'s signature at freeze time and recorded in
  the manifest, never assumed.
- **Freeze** (`freeze_experiment(case_id, project_key, *, hypothesis, mechanism, falsifier,
  candidate, n_queries=30, seed)`): novelty check against `rejected` cases and #2082's plan
  (`docs/plans/hybrid-retrieval-eval.md`, cited in `prior_answers` if the candidate varies only
  `retrieval_mode`-adjacent behavior); `export_corpus`; `build_known_item_set(export.records,
  n_queries=, seed=)` under unit-2 reservation `known_item_generation`; `capture_baseline`;
  protocol `{"batch_size": n_queries, "endpoints": ["recall_at_5", "mrr"], "thresholds":
  {"mrr": {"margin": 0.02, "alpha": 0.05}, "recall_at_5": {"margin": 0.02, "alpha": 0.05}},
  "holdout_partition": f"known-item-{seed}", "queries", "baseline", "incumbent", "candidate",
  "infra_failure_cap": 0}`; `freeze_protocol`; manifest; `contract_digest`; `state="frozen"`,
  `frozen_at`; journal event `experiment_frozen` with the digest. The margins are provisional and
  named in the protocol, which is what makes them part of the contract. `n_queries=30` is a
  minimum-worthwhile-effect placeholder the protocol discloses; small samples yield
  `inconclusive`, and the plan says so rather than pretending 30 is powered.
- **Evaluate** (`valor-improve experiment evaluate --id`): a unit-2 reservation `evaluation_judges`
  sized from `n_queries * 2 * judge_price_estimate`, then `runner.evaluate`. The session runs it
  with `run_in_background` and polls `valor-improve experiment show --id` (prints state, verdict,
  and `notes`). Refuses to start when the lane slot is not held by this session's action id.
- **Apply verdict** (`apply_verdict(evaluation)`): the rule in Data Flow step 6, each transition a
  journal event then a `save()`. `reject` sets `rejected_reason` from the evaluation's
  `rationale`/`notes` and appends the evaluation id to `evaluation_ids`. Called by `experiment
  evaluate` after `runner.evaluate` returns and, as a backstop, by the planner tick for any
  `complete` evaluation whose case still reads `evaluating`.

#### The `skill_acquisition` cycle in this lane

Stages 1 through 3 of charter §5 run for real: the research session detects a gap from evidence
(a `correction` or `lesson` row naming a missing capability), searches the library and the web
with URLs and dates, vets (records what the candidate skill claims, who wrote it, when, and what
it would need to be usable here), and integrates by writing a skill directory in the lane worktree
or recording why it should not be integrated. Stage 4 (comparative evaluation on a similar task)
needs an agent-run arm this lane does not have; the investigation resolves with a
`provisional_assumption` that states exactly that, cites the follow-up issue, and carries the
overturning observation "an agent-run paired evaluation shows no gain". Stage 5 (reuse
observation) is recorded as "not yet observable". The plan says this plainly: **this lane
demonstrates the cycle's shape, not an acquired ability.**

#### The assumption digest

- `reflections/improvement_assumption_digest.py::run_improvement_assumption_digest()`, registered
  as `improvement-assumption-digest` with `cadence="259200s"`. Reads investigations resolved since
  the last digest (watermark on the controller head payload) that carry a
  `provisional_assumption`, `resource_acquired` evidence rows, lane 7's `spend_receipt` overrun
  rows (and exposes `on_escalation(payload)` for `tools/infrastructure_budget.py:568-574` to call,
  which appends to a pending list the next digest drains), and vault requests (investigations of
  kind `resource_acquisition` with disposition `vault_request_written`).
- Renders one message grouped by `priority_area`, each assumption with its charter passage,
  confidence, and overturning observation; a "Vault requests" section; a "Resources acquired"
  section; an "Infrastructure overruns" section; and the fixed closing line: **"This is a status
  report. It asks nothing. Silence validates none of the above; each assumption stands until
  evidence overturns it."** Sent through `send_host_eng_telegram(message,
  logger_prefix="improvement_assumption_digest")`. An empty digest sends nothing and reports
  `status="success"` with `counts={"assumptions": 0}`.
- A test asserts the closing line is present, no `?` appears in the rendered text outside a
  quoted assumption body, no poll or `AskUserQuestion` symbol is imported, and a seeded
  `resource_acquired` row and a seeded overrun payload each render under their heading.

#### Dashboard

- `ui/data/improvement.py` gains `get_ranking(project_key)` (the latest snapshot's `order` with
  `moved`/`entered`/`left` from its diff, plus the intake pool; `unavailable` when the store read
  fails or the snapshot does not verify; `no_snapshot_yet` when none exists),
  `get_hypotheses(project_key)` (experiments in `proposed`, `frozen`, `running` with hypothesis,
  mechanism, falsifier, contract digest, and `frozen_at`), and `get_rejected_approaches(project_key)`
  (cases in `rejected` with `rejected_reason`, the evaluation's verdict, effect, and confidence
  interval, and the snapshot in which it left). Three templates under `ui/templates/improvement/`,
  three inline routes in `ui/app.py`, three links from `/`. `get_goals`'s "Open cases" section reads
  positions from `get_ranking` when a snapshot exists and its placeholder text names lane 5, not
  lane 3. The exact-list test moves to seven names and gains the no-activity-counter assertion.

#### The qualified-result report

- `tools/improvement_report.py::build_report(case_id, project_key) -> str`, printed by
  `valor-improve report --case ID`, generated entirely from records in this order: the case and
  its charter digest; the ranking positions it held (from the snapshot chain); the investigations
  (kind, claims with URLs and dates, assumptions); the model revisions with their predictions; the
  experiment (hypothesis, mechanism, falsifier, contract digest, envelope, candidate vs incumbent);
  the evaluation (verdict, effect and interval per endpoint, correction, `blinded`, trials,
  identity scan result, judge calibration); and three mandatory sections whose content is
  derived, not authored: **"What was measured"** (endpoints, corpus digest, query count, holdout
  partition), **"What this does not establish"** (always includes: no claim above "loop
  operational"; the sample size and margin; that a retrieval-parameter gain says nothing about
  agent behavior; any `metering="estimated"` receipts), and **"What would change the answer"** (the
  falsifier, the overturning observations of every assumption cited, and a larger sample). The
  builder posts the first real cycle's report on #3217 verbatim.

#### Running the first real cycle (the build's last task, on the owning machine)

1. `IMPROVEMENT__ENABLED=true` for the run, in the shell that runs the ticks, never in `.env`.
2. Seed the one `inference` inspiration row and let the evidence tick run once
   (`python -c "from reflections.improvement_collect import run_improvement_collect as r; print(r())"`).
3. Run the planner tick once; confirm a snapshot exists and `valor-improve ranking` prints it.
4. Let lane 3's adapter dispatch the research session (or, if the adapter is not yet enabled on
   this machine, run `valor-session create` for the research skill with the case id, which is the
   same top-level path the adapter uses and is recorded as such in the report).
5. Observe the session through `valor-improve investigation list --case ID` and `experiment show`.
6. After the verdict, run the planner tick again; confirm the second snapshot's diff shows the
   move; run `valor-improve report --case ID`; post it on #3217.
7. Leave `IMPROVEMENT__ENABLED` at its default afterwards; the reflections stay registered.

What this run can and cannot prove is written in the report's mandatory sections, and the
Success Criteria below claim only what the records show.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `reflections/improvement_collect.py`: every adapter is wrapped at `:500-505` with
  `logger.warning` and a `findings` entry. The two new adapters follow the same shape, and each
  gains a test that injects a raising `gh` runner (lessons) and a raising judge transport
  (promises) and asserts the warning text, the `findings` entry, and that the other adapters'
  counts are unaffected.
- [ ] `reflections/improvement_plan.py`: each of the four steps is wrapped independently; a test
  per step injects a failure (store unwritable, journal refusal reason code, `pinned()` returning
  `None`) and asserts the tick's `status`, the `findings` text, and that no `ImprovementCase` was
  written by a step after the failure. A `pinned() is None` ends the tick with `status="error"`
  and zero writes, asserted by counting rows before and after.
- [ ] `tools/improvement_experiment.py`: `freeze_experiment` wraps `build_known_item_set` and
  `capture_baseline`; a failure in either leaves the experiment in `proposed`, writes no protocol,
  and returns a reason; a test asserts the experiment record is unchanged and the store holds no
  new `protocol-` key. `evaluate` does not catch: lane 4's runner owns its three disjoint handlers.
- [ ] `reflections/improvement_assumption_digest.py`: a `send_host_eng_telegram` returning
  `False` records `findings=["digest-not-delivered"]` and does **not** advance the watermark, so
  the next digest re-sends; asserted by two runs with the transport failing then succeeding.
- [ ] `tools/improvement_ranking.py::load_snapshot`: `ArtifactIntegrityError` propagates to the
  CLI (exit 2, message printed) and is caught by `ui/data/improvement.py::get_ranking` into
  `unavailable=True`; both asserted, the second by corrupting the archive copy the way lane 4's
  mutation test does.

### Empty/Invalid Input Handling
- [ ] `rank([])` returns an empty order and `write_snapshot` still writes a snapshot with an empty
  `order` and a diff naming every previously ranked case under `left` with reason
  `"no open cases"`; asserted.
- [ ] `record_claims` with an empty list, `None`, or whitespace-only `claim` text: empty list is a
  no-op returning 0; `None` raises `ValueError` at the boundary (the CLI prints it); a
  whitespace-only claim is stored as a note with `is_claim=False`; each asserted.
- [ ] `build_brief` for a case with no evidence renders the charter and the case and the line
  "No evidence rows are attached to this case" rather than an empty section; asserted.
- [ ] `collect_promises` with an empty outbound log, an entry whose `content` is empty, and a judge
  returning unparseable text: zero rows, zero rows, and zero rows plus a `findings` entry naming
  the parse failure; each asserted.
- [ ] `collect_lessons` with a PR body of `None` or a body with no prefixed lines: zero rows, no
  exception; asserted.
- [ ] `validate_candidate({})` and `validate_candidate(incumbent)` both refuse with distinct reason
  codes; asserted.
- [ ] The research session's empty output is not this lane's to loop on: it runs under lane 3's
  dispatch and the executor's turn deadline, and the planner tick proposes at most one action per
  tick, so an idle session cannot make the tick spin.

### Error State Rendering
- [ ] Each new partial renders three distinguishable states (content, "nothing yet", "unavailable")
  on the goals partial's pattern (`ui/templates/improvement/goals.html`), and a test hits each route
  in each state and asserts the distinguishing text; a corrupted snapshot renders "unavailable" and
  the `ArtifactIntegrityError` message, never a stale order.
- [ ] `valor-improve ranking --at <bad digest>` prints the integrity error to stderr and exits 2;
  `valor-improve experiment show` on an `aborted` experiment prints the `notes` that name the
  infra failure; both asserted through the CLI entry point.
- [ ] The report's "What this does not establish" section is non-empty for every verdict,
  including `accept`; asserted with a seeded accept.

## Test Impact

Every file below was read on `main` at `89f800876` (or on `session/sdlc-3216` at `fe6f55072` where marked lane 4) and the disposition names the exact assertion that moves.

- [ ] `tests/unit/test_improvement_models.py::INDEXED_VOCABULARIES` (`:60-63`) — UPDATE: `ImprovementInvestigation.kind` grows to eight values and `state` to five (`awaiting_authorization`); the tuple import picks the new values up, and the test keeps failing if a ninth kind or a sixth state appears without an argument here.
- [ ] `tests/unit/test_improvement_models.py::VOCABULARY_MAXIMUMS` (`:93-97`) — UPDATE: add `(ImprovementEvidence, "kind"): 10` with the cardinality argument (two adapter-owned kinds, `lesson` and `promise`, each an index set with its own reader; `other` would make both unqueryable). This is the "named entry carrying its reason" the test's own message demands.
- [ ] `tests/unit/test_improvement_models.py::TestLane7EvidenceKinds` (`:259`) — UPDATE: gains the sibling class `TestLane5EvidenceKinds` asserting `lesson` and `promise` are declared and round-trip without coercion to `other`, on the same shape as `:267-292`.
- [ ] `tests/unit/test_ui_app.py::test_dashboard_never_offers_experiment_or_patch_counts` (`:719-733`) — UPDATE: the exact getter list becomes `["get_coverage", "get_goals", "get_hypotheses", "get_intervention_burden", "get_provisional_assumptions", "get_ranking", "get_rejected_approaches"]`. The rule the test protects (no activity counter) stands: none of the three new getters returns a count of experiments or patches, and a new assertion checks that no getter's result dict carries a key named `experiment_count` or `merged_patch_count`.
- [ ] `tests/unit/test_ui_app.py::test_index_page_links_all_improvement_partials` (`:735`) — UPDATE: asserts the three new partial routes (`/_partials/improvement/ranking/`, `/hypotheses/`, `/rejected/`) are linked from `/`.
- [ ] `tests/unit/test_sdlc_stubs.py::TestCheckExistingReflectionPR` (`:156-194`) — DELETE: it tests `scripts.sdlc_reflection._check_existing_reflection_pr`, and the script is deleted whole. The stub-creation tests above it (`:34-141`) test `scripts/update/migrations.py`'s `create_sdlc_stubs` and stay.
- [ ] `tests/unit/test_install_scripts_bootstrap.py` (`:62`, `:84`) — UPDATE: remove the `install_sdlc_reflection.sh` entries from both dicts; the installer is deleted with the script.
- [ ] `tests/unit/test_reflection_register.py` (`:682-790`, the `improvement_collect` registration cases) — UPDATE: parametrize the six cases over the three improvement registrations (`improvement-evidence-collect`, `improvement-planner-tick`, `improvement-assumption-digest`) instead of one, so each new registration inherits owner-gating, idempotency, non-owner skip, missing-vault skip, and scheduler-registry loading.
- [ ] `tests/unit/test_improvement_evidence.py::TestCollectCorrections` and siblings (`:260-344`) — no change to existing cases; the file gains `TestCollectLessons` and `TestCollectPromises` classes on the same fixture shape.
- [ ] `tests/unit/test_migrations.py` — UPDATE: the registered-migration assertions gain the two entries this lane registers (`retire_sdlc_reflection` and `improvement_investigation_stage_field`).
- [ ] (lane 4) `tests/unit/test_improvement_eval_arena.py::test_carries_the_four_arm_keys` (`:48`) — no change; the arm env is untouched. The arm worker's job-spec test file gains cases for the `rrf_k` and `min_rrf_score` pass-throughs, asserting an absent key leaves `retrieve_memories` at its defaults.
- [ ] `tests/unit/test_infrastructure_budget.py` — no change: lane 7 already tests the `on_escalation` sink both present and absent (`tools/infrastructure_budget.py:568-574`); this lane supplies the callable and adds one integration case in its own digest test file.

## Rabbit Holes

- **Building the agent-run arm "while we are in there."** It is the natural next thing and it is
  a lane of its own (#3311). This lane's envelope is retrieval parameters, and the validator
  refuses anything else so a builder cannot drift into it.
- **A numeric ranking score.** Weights nobody calibrated produce an order nobody can defend.
  Ordinal factors with stated rules, lexicographic order, and a diff that names why something
  moved.
- **Making the planner clever.** The tick is deterministic rules over records. Every place
  judgment is needed is the research session's, and the session reaches state only through
  `valor-improve`. An LLM call inside the tick would make the ranking unreproducible.
- **Making the promise detector precise.** It is a sampled, cheap, yes/no judge whose rows are
  evidence with `confidence`. Tuning its prompt to a benchmark that does not exist is the
  autoexperiment failure with a new name.
- **Rebuilding the known-item set.** `build_known_item_set` exists, is seeded, and skips
  degenerate generations. Reuse it.
- **A second lease, journal, or dead-letter sink.** Lane 3's. If it is not there, wait.
- **Keeping `sdlc_reflection.py` around "just in case."** Development principle 1. The lessons
  it scraped are now evidence rows with a reader; the script has no remaining job.
- **Reading `retrieval_mode` into the envelope.** It is an environment setting, not a call
  parameter, and lane 4's arena test pins the arm environment (spike-2).
- **Treating the first real cycle as a demonstration of improvement.** It demonstrates that the
  loop runs. The report's mandatory sections say what it does not establish, and the Success
  Criteria claim level 1 only.

## Risks

### Risk 1: The build starts before lanes 3 and 4 merge and drifts against a moving head
**Impact:** The consumed contract (journal, `propose`, the harness) shifts under the build; the
lane ships against names that no longer exist.
**Mitigation:** Prerequisites gate every lane-3 and lane-4 surface by import, and the build's
first commit corrects any name to what merged. Nothing is stubbed. If lane 3 lands `propose`
without a Python seam, the planner shells to the CLI (Technical Approach, "Consumed from lane 3").

### Risk 2: The novelty check is a string heuristic and lets a rejected idea back in with new wording
**Impact:** The loop rediscovers its own dead end; the acceptance criterion "a rejected hypothesis
is not re-proposed" holds only for identical wording.
**Mitigation:** The dedup identity is classification plus a normalized prefix, and the rule is
stated so its limits are known. The brief shows rejected cases in the same `priority_area` with
their reasons, so the research session sees the prior answer before proposing. The report's
"What this does not establish" names the identity rule's limit. A semantic novelty check is a
research target for the loop itself, not a builder's guess.

### Risk 3: The evaluation's judge calls exhaust unit 2 or hit an `unknown` metering
**Impact:** Lane 4's runner raises `InfraFailure` on an unreachable judge and the experiment lands
`aborted`; the cycle stops without a verdict.
**Mitigation:** `experiment evaluate` reserves `evaluation_judges` before starting and refuses
with a reason if the reservation fails, so the abort is a refusal, not a mid-run failure. An
`infra_failure` leaves the case where it was and records a `probe` investigation naming the cause;
`experiment repair` returns the experiment to `frozen` for a retry once unit 2 reopens.

### Risk 4: Thirty known-item queries are underpowered and every verdict is `inconclusive`
**Impact:** The cycle completes with a verdict that moves the case back to `investigating`; the
demonstration of "verdict changes selection" is a move within the open set, not an exit.
**Mitigation:** That is a legitimate outcome and the snapshot diff still shows the move (the
`uncertainty` factor resets to high, and the position changes). The report says the sample is a
placeholder. `n_queries` is a parameter of `freeze`, and the second cycle can raise it under the
same contract shape. The plan does not pretend 30 is powered.

### Risk 5: The promise detector spends money on the fifteen-minute tick
**Impact:** Ten judge calls per tick is 960 calls a day at a cheap model's price, and unit 2 is
shared with the evaluator.
**Mitigation:** Off by default (`promise_detector_enabled=False`); reserves through lane 3's meter
under its own purpose so it cannot starve `evaluation_judges`; skips with a `findings` entry when
the meter refuses; the sample cap is a named constant. The cheap model is the one in
`ImprovementSettings.cheap_inference_model` when set, else `OPENROUTER_GEMMA4_FREE`.

### Risk 6: The digest becomes a question by accident
**Impact:** Charter §11 is violated the moment a rendered line reads as a request for direction.
**Mitigation:** The fixed closing line, the no-`?` assertion outside quoted assumption bodies, and
no import of any poll or `AskUserQuestion` symbol, all tested. Vault requests are phrased as
statements of what the adapter expects, not as asks.

### Risk 7: Retiring `sdlc_reflection.py` loses lessons already in flight
**Impact:** A PR merged between the last cron run and the retirement never has its lessons read.
**Mitigation:** `collect_lessons`'s first run uses a 14-day window (the script's own lookback was
7), so the gap is covered twice over, and dedup makes the overlap free.

### Risk 8: The first real cycle needs lane 3's dispatch on this machine and it is not enabled
**Impact:** The run stalls at step 4 of the runbook.
**Mitigation:** The runbook names the fallback: `valor-session create` for the research skill with
the case id is the same top-level path the adapter uses, and the report records which path ran.
The integration test does not depend on dispatch at all: it drives the research steps through the
Python functions the CLI wraps.

## Race Conditions

### Race 1: Two planner ticks run concurrently and both propose for the same case
**Location:** `reflections/improvement_plan.py::propose_one_action`
**Trigger:** The reflection scheduler restarts while a tick is mid-flight, or an operator runs the
tick by hand during a scheduled one.
**Data prerequisite:** The snapshot reference on the controller head payload.
**State prerequisite:** The case's intent state in lane 3's record.
**Mitigation:** The action id is a digest of `(case_id, snapshot_ref, action_kind)`, so both ticks
compute the same id and lane 3's intent record dedups on it; the journal's `transition` refuses
the second `ranking_recorded` at the same expected revision with a reason code, and the loser
reports `findings=["ranking_recorded refused: revision moved"]` and writes no proposal.

### Race 2: The evaluation finishes while the planner tick is reading the case
**Location:** `tools/improvement_experiment.py::apply_verdict` versus
`reflections/improvement_plan.py::rank`
**Trigger:** `experiment evaluate` returns during a tick.
**Data prerequisite:** The `ImprovementEvaluation` row with a verdict.
**State prerequisite:** The case at `evaluating`.
**Mitigation:** `apply_verdict` journals `verdict_applied` before the ORM save; the tick reads the
head, and a case whose head revision moved since the tick's snapshot read is re-read once before
ranking. The backstop in the next tick applies any `complete` evaluation whose case still reads
`evaluating`, so a verdict is never lost, only delayed one tick.

### Race 3: The research session records claims on an investigation the tick just expired
**Location:** `tools/improvement_investigations.py::record_claims`
**Trigger:** A 30-day TTL or an `expires_at` passes mid-session.
**Data prerequisite:** The investigation row.
**State prerequisite:** `state="open"`.
**Mitigation:** `record_claims` re-reads the row and refuses on a missing row or a non-`open`
state with a reason code; the session opens a new investigation citing the old id in
`prior_answers`. The TTL is thirty days and a session is hours, so this is a correctness rule more
than an expected event.

### Race 4: The digest and a resolving session touch the same watermark
**Location:** `reflections/improvement_assumption_digest.py`
**Trigger:** An investigation resolves with an assumption between the digest's read and its
watermark write.
**Data prerequisite:** `resolved_at` on the investigation (set by `resolve`).
**State prerequisite:** None.
**Mitigation:** The watermark is the newest `resolved_at` the digest actually rendered, not
"now", so a row resolved after the read is newer than the watermark and appears in the next
digest. Watermark writes happen only after a successful send.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3311] The paired agent-run arm, charter §5 stages 4 and 5 of the
  skill-acquisition cycle, and the cheap-inference integration experiment once a credential is
  vaulted. This lane's envelope is retrieval parameters; the validator refuses anything else, and
  the Verification row "the arm worker still reads only what `retrieve_memories` accepts" is the
  anti-criterion.
- [SEPARATE-SLUG #3218] Any writer for `ImprovementRelease`, exposure assignment, rollback, or
  promotion on an `accept` verdict. `apply_verdict` leaves an accepted case at `evaluating`. The
  Verification row "No `ImprovementRelease` writer in this lane" is the anti-criterion.
- [SEPARATE-SLUG #3215] The control journal, the lease, dispatch intents, the scheduler adapter,
  `propose`, `propose-amendment`, the paid-inference meter, `vault_write`, and the base CLI. This
  lane consumes each and adds subcommands to the CLI; it opens no PR against
  `agent/agent_session_queue.py` and defines no second journal.
- [SEPARATE-SLUG #3216] Every gate inside `tools/improvement_eval/` except the two pass-through
  keys in the arm worker. The Verification row on `handle_job` bounds the edit.
- [EXTERNAL] Placing any credential. The resource-acquisition action writes a vault request;
  Tom places the item in `m-valor` or declines. No controller module writes `.env` or invokes
  `op`; `tools/improvement_resources.probe` (read-only) and lane 3's `tools/vault_write.py` are the
  only improvement-path `op` callers. The Verification row asserts it.
- [ORDERED] Amending `docs/improvement-charter.md` or `models/improvement_charter.py`. The
  session may `propose-amendment`; only Tom authorizes. The Verification row "Charter unwritten by
  this lane" is the anti-criterion.
- [DESTRUCTIVE] Deleting the "Reflection Notes (auto-generated)" sections that
  `sdlc_reflection.py` already appended to `docs/sdlc/*.md`. They are history; the retirement
  removes the writer, not what it wrote. A Verification row asserts the `docs/sdlc/` stub count is
  unchanged.
- [ORDERED] Turning `IMPROVEMENT__ENABLED` or `promise_detector_enabled` on in any machine's
  `.env`. The first real cycle runs with the switch set in the invoking shell; leaving the loop on
  is an operating decision Tom makes after reading the report.

## Update System

- `scripts/update/run.py` gains two registration steps beside `register_improvement_collect`:
  `register_improvement_planner` (cadence from `settings.improvement.controller_tick_seconds`)
  and `register_improvement_assumption_digest` (`cadence="259200s"`), each idempotent, each a
  `RegisterResult` field on the run result dataclass, each inheriting `_this_machine_owns_valor`.
  Task 5 owns the edit and the `tests/unit/test_reflection_register.py` parametrization.
- `"sdlc-reflection"` joins `OBSOLETE_SERVICE_SUFFIXES` (`scripts/update/service.py:39-51`), so
  `/update` boots out `com.valor.sdlc-reflection` and removes its plist on every fleet machine
  that ever ran `install_sdlc_reflection.sh`, by exact label match as the sweep already does.
- Two migrations registered in `MIGRATIONS` (Records, above), idempotent, recorded once in
  `data/migrations_completed.json`.
- Two new `ImprovementSettings` fields (`promise_detector_enabled`, `cheap_inference_model`),
  both defaulting off, both declared in `.env.example` with `# @optional` and a
  `Field(description=...)` sentence that clears `tests/unit/test_env_declaration_readers.py`. No
  key is required at runtime.
- No new dependencies. `gh` is already a runtime requirement of other reflections.
- Fleet execution stays single-machine (the `valor` owner); the research session and the
  evaluation run where the worker runs.

## Agent Integration

- **CLI**: six subcommand groups added to lane 3's `valor-improve` (`tools/improvement.py`):
  `brief --case ID`; `ranking [--at DIGEST]`; `investigation open|record|resolve|list`;
  `revise-model`; `experiment freeze|evaluate|show|repair`; `report --case ID`; plus `case open`
  routed through the planner's `open_cases` function. Each subcommand is a thin wrapper over a
  function in `tools/improvement_*.py`, and the research session reaches state through nothing
  else. No new `pyproject.toml` entry: `valor-improve` is lane 3's entry point.
- **Reflections**: two new function reflections (`improvement-planner-tick`,
  `improvement-assumption-digest`) registered through `reflection_register.py`; the evidence
  tick gains two adapters. Nothing agent-type, so no vault hand-edit.
- **The research skill**: `.claude/skills/improve-research/SKILL.md` body rewritten to the
  brief-first contract; project-only, never synced.
- **The bridge imports nothing new and the poll registry is untouched.** The digest and the
  amendment request are plain messages through `send_host_eng_telegram`; nothing binds to a
  message id. The Verification row on `tools/ask_poll.py`, `bridge/poll_registry.py`, and
  `bridge/answer_routing.py` asserts it.
- **Integration tests**: `tests/integration/test_improvement_research_cycle.py` seeds evidence in
  the claimed test database, runs the evidence tick and the planner tick, drives the research
  steps through the Python functions the CLI wraps (with an injected judge transport and a fixture
  `gh` runner), freezes an experiment in the envelope, runs `runner.evaluate` with lane 4's test
  judges, applies the verdict, runs the tick again, and asserts the second snapshot's diff. A
  second test seeds a `rejected` case and proves the tick refuses to re-open it. A third invokes
  `valor-improve brief`, `ranking`, and `report` through the console entry point and asserts the
  charter-first line, the printed order, and the three mandatory report sections.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/improvement-research-cycle.md`: the planner tick, the ranking snapshot and its diff, the investigation lifecycle (eight kinds, five indexed states, the unindexed `stage`), the claim rule, provisional assumptions and the three-day digest, the brief contract, the experiment envelope for this lane (retrieval arm parameters), the verdict-to-selection rule, the two new observer adapters, and the qualified-result report format
- [ ] Update `docs/features/improvement-controller.md`: "What exists today" (`:12-21`) names lane 5; the Three layers "Research reasoning" paragraph (`:29-32`) drops "Not built yet (lane 5)"; the Dashboard section (`:364-395`) documents the three new partials and the seven-getter list; the Evidence collection table (`:92-96`) gains the `lesson` and `promise` adapter rows; a "Research cycle" section links the new feature doc
- [ ] Update `docs/features/sdlc-repo-addenda.md` (`:46-92`): the reflection-agent section is replaced by one paragraph stating that lessons in PR bodies are now `ImprovementEvidence` rows of kind `lesson` read by the planner, and the `com.valor.sdlc-reflection` rows leave the file table
- [ ] Update `docs/features/launchctl-bootstrap-fail-soft.md:76`: remove the `install_sdlc_reflection.sh` row
- [ ] Update `docs/features/log-rotation.md:40` and `docs/features/nightly-regression-tests.md:371`: remove the `sdlc_reflection.py` / `sdlc_reflection_last_run.json` references
- [ ] Update the three prose mentions the "No live reference" Verification row also catches: `docs/features/bridge-self-healing.md:637` (drop `install_sdlc_reflection.sh` from the helper list), `scripts/lib/launchctl.sh:41` (drop `sdlc-reflection` from the StartInterval example), `tests/integration/test_install_reflection_worker.py:180` (rename the idiom comment; the idiom itself stays)
- [ ] Update `docs/tools-reference.md` (`:345-361`): the `valor-improve` block gains `ranking [--at DIGEST]`, `investigation open|record|resolve`, `revise-model`, `experiment freeze|evaluate`, and `report`, and its "planned, lane 3" marker is corrected once lane 3 lands
- [ ] Update `docs/plans/critiques/recursive-self-improvement-capability-matrix.md`: add a lane-5 section grading each component on the four axes, and correct the "Not built, by lane" row for lane 5 (`:134`)
- [ ] Update `.claude/skills/update/SKILL.md:115` and `.claude/skills/setup/SKILL.md:105`: remove the `./scripts/install_sdlc_reflection.sh` line
- [ ] Add the feature doc row to `docs/features/README.md`
- [ ] Post the qualified-result report of the first real cycle as a comment on #3217 (what was measured, what it does not establish, what would change the answer), linked from the feature doc

### Inline Documentation
- [ ] Module docstrings on every new module state the TTL and index decisions in the schema-gate voice `tests/unit/test_improvement_models.py::test_ttl_decision_is_recorded_in_the_docstring` reads
- [ ] `reflections/improvement_collect.py` module docstring lists five adapters, not three, and names the production writer of each new input

## Success Criteria

The issue's seven acceptance criteria, restated against charter v2 where the Freshness Check
records an override, plus this plan's own.

- [ ] One autonomous hypothesis inside the retrieval-parameter envelope is frozen under a contract
  (digest stored before any arm runs) and measured by lane 4's harness with paired blinded
  evaluation, producing `accept`, `reject`, `inconclusive`, or `infra_failure` with complete
  lineage (case, investigations, model revision, experiment, evaluation, charter digest) readable
  from the records alone
- [ ] **The verdict changes the next selection, demonstrated**: two consecutive ranking snapshots
  exist whose diff names the case under `left` (reject) or `moved` (inconclusive) with the
  evaluation id as the reason, and a seeded `rejected` case is refused re-opening by the tick
- [ ] The memory-inspiration adapter (existing) and the `web_research` kind are both exercised end
  to end in the real cycle, with claims carrying URLs and retrieval dates
- [ ] The `resource_acquisition` investigation produces a prepared adapter and a written vault
  request rendered in the digest, and places no credential; the anti-criterion row passes
- [ ] `scripts/sdlc_reflection.py`, its installer, and its plist are gone; `collect_lessons` writes
  `lesson` rows from merged PR bodies; `sdlc-reflection` is in the obsolete-service sweep
- [ ] At least one provisional assumption is recorded with its charter passage, confidence,
  consequence, and overturning observation, rendered on the goals partial and in a digest
- [ ] The qualified-result report for the real cycle is generated from records, carries the three
  mandatory sections, and is posted on #3217
- [ ] Eight investigation kinds, five indexed states, the unindexed `stage`, two new evidence
  kinds with their cardinality argument, and both migrations registered
- [ ] Every planner tick writes an immutable snapshot and a `ranking_recorded` journal event; the
  dashboard's ranking partial and `valor-improve ranking` read the same artifact
- [ ] The brief opens with the pinned charter verbatim; the research skill's first step prints it
- [ ] The digest asks nothing, says silence validates nothing, and renders `resource_acquired`
  rows and lane 7's overrun payload under their own headings
- [ ] The `skill_acquisition` kind runs stages 1 through 3 in the real cycle and resolves stage 4
  as a provisional assumption citing #3311
- [ ] The `promise` adapter is wired, gated off by default, and tested with an injected transport
- [ ] Three new dashboard partials render content, empty, and unavailable states; the getter list
  is exactly seven and carries no activity counter
- [ ] Lane 6's three seams exist: every model revision carries a `research_process_digest` in
  lane 6's canonical form, `PlannerArmRunner` registers from the CLI entry when lane 6's module is
  importable, and every manifest carries `base_revision` and `candidate_ref`
- [ ] The claim made on #3217 is "loop operational" (charter §6, level 1) and no higher
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER
builds directly.

### Team Members

- **Builder (records and adapters)**
  - Name: records-builder
  - Role: Model changes, migrations, `EVIDENCE_KINDS`/`INVESTIGATION_KINDS`, the two observer
    adapters, the `sdlc_reflection.py` retirement, settings fields
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Builder (planner and ranking)**
  - Name: planner-builder
  - Role: `reflections/improvement_plan.py`, `tools/improvement_ranking.py`, case opening, the
    novelty check, the snapshot, the journal events, registration
  - Agent Type: builder
  - Resume: true

- **Builder (investigations, brief, skill)**
  - Name: research-builder
  - Role: `tools/improvement_investigations.py`, `tools/improvement_brief.py`, the CLI
    subcommands for both, the research skill rewrite, the resource-acquisition disposition logic
  - Agent Type: builder
  - Resume: true

- **Builder (experiments and report)**
  - Name: experiment-builder
  - Role: `tools/improvement_experiment.py`, the arm-worker pass-throughs, `apply_verdict`,
    `tools/improvement_report.py`, the CLI subcommands for both
  - Agent Type: builder
  - Resume: true

- **Builder (digest and dashboard)**
  - Name: surface-builder
  - Role: `reflections/improvement_assumption_digest.py`, the three partials, `get_ranking`,
    `get_hypotheses`, `get_rejected_approaches`, the goals partial correction
  - Agent Type: builder
  - Resume: true

- **Validator (wave 1)**
  - Name: records-validator
  - Role: Verify records, migrations, adapters, retirement against Verification rows
  - Agent Type: validator
  - Resume: true

- **Validator (wave 2)**
  - Name: loop-validator
  - Role: Verify planner, ranking, investigations, brief, experiments against Verification rows;
    run the integration test
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: documentarian
  - Role: Feature doc, feature-doc updates, tools reference, capability matrix, skill lines
  - Agent Type: documentarian
  - Resume: true

- **Final validator**
  - Name: final-validator
  - Role: Run every Verification row, confirm the real cycle's records and report, confirm the
    Success Criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

Task 0 is a gate, not work. Wave 1 (tasks 1 and 2) has no dependency on lane 3's surface beyond
the evidence kind and can be validated while lane 3 is still in flight, but no task starts until
task 0 passes.

### 0. Prerequisites gate
- **Task ID**: gate-prereqs
- **Depends On**: none
- **Assigned To**: lead
- **Agent Type**: validator
- **Parallel**: false
- Run `python scripts/check_prerequisites.py docs/plans/improvement-controller-lane-5-first-complete-research-cycle.md`
- Every row passes, or the build waits for the named merge. Correct any lane-3 module name in the
  Prerequisites table in the first commit and record the correction in the PR body.

### 1. Records, settings, and migrations
- **Task ID**: build-records
- **Depends On**: gate-prereqs
- **Validates**: tests/unit/test_improvement_models.py, tests/unit/test_migrations.py,
  tests/unit/test_env_declaration_readers.py
- **Informed By**: spike-3 (eight kinds fit; the lifecycle does not; `stage` is unindexed)
- **Assigned To**: records-builder
- **Agent Type**: builder
- **Parallel**: true
- `INVESTIGATION_KINDS` to eight, `INVESTIGATION_STATES` to five, `stage` and the four new plain
  fields on `ImprovementInvestigation`, three new plain fields on `ImprovementCase`, `lesson` and
  `promise` on `EVIDENCE_KINDS` with the docstring argument, `VOCABULARY_MAXIMUMS` entry,
  `FORBIDDEN_INDEX_NAMES` gains `stage`, `dedup_identity`, `evaluation_ids`
- `ImprovementSettings.promise_detector_enabled` and `cheap_inference_model`, `.env.example`
  declarations with `# @optional`
- Migrations `improvement_investigation_stage_field` and `retire_sdlc_reflection`, registered
- Module docstrings state every TTL and index decision

### 2. Observer adapters and the retirement
- **Task ID**: build-adapters
- **Depends On**: build-records
- **Validates**: tests/unit/test_improvement_evidence.py (new `TestCollectLessons`,
  `TestCollectPromises`, `TestDetectorInputsHaveProductionWriters` additions),
  tests/unit/test_sdlc_stubs.py, tests/unit/test_install_scripts_bootstrap.py
- **Informed By**: spike-6 (outbound writers exist)
- **Assigned To**: records-builder
- **Agent Type**: builder
- **Parallel**: false
- `collect_lessons` with the seven prefixes and `STAGE_KEYWORDS` moved from the script; a
  fixture `gh` runner in tests
- `collect_promises` with the sampled cheap-model judge, lane 3's meter under purpose
  `promise_detector`, the `enabled` and `promise_detector_enabled` gates, an injectable transport
- Both adapters in the tick's tuple; the module docstring lists five and names each input's
  production writer
- Delete `scripts/sdlc_reflection.py`, `scripts/install_sdlc_reflection.sh`,
  `com.valor.sdlc-reflection.plist`; add `"sdlc-reflection"` to `OBSOLETE_SERVICE_SUFFIXES` with
  a comment; delete `TestCheckExistingReflectionPR`; remove the installer from both dicts in
  `test_install_scripts_bootstrap.py`; remove the `/update` and `/setup` skill lines

### 3. Validate wave 1
- **Task ID**: validate-records
- **Depends On**: build-adapters
- **Assigned To**: records-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the record, migration, evidence, stubs, bootstrap, and env-reader test files
- Run the Verification rows for kinds, retirement, obsolete sweep, no live reference, adapters wired
- Mutation-check: revert the `VOCABULARY_MAXIMUMS` entry and confirm the gate fails; restore

### 4. Investigations and the brief
- **Task ID**: build-research
- **Depends On**: build-records
- **Validates**: tests/unit/test_improvement_investigations.py (create),
  tests/unit/test_improvement_brief.py (create)
- **Informed By**: spike-3 (`awaiting_authorization` only for amendments)
- **Assigned To**: research-builder
- **Agent Type**: builder
- **Parallel**: true
- `tools/improvement_investigations.py`: `open_investigation`, `record_claims` with the claim
  rule, `resolve` with the assumption guard and the four refusal patterns, `transition_investigation`
  advancing `stage` in order, the novelty check (`prior_answers`), the amendment-resolution hook
  the tick calls on a new pinned digest
- `tools/improvement_brief.py::build_brief` with the ordered sections and the caps
- CLI subcommands `brief`, `investigation open|record|resolve|list`, `revise-model` (refuses an
  empty `prediction`), `case open`
- Rewrite `.claude/skills/improve-research/SKILL.md` to the brief-first contract, stating the
  eight kinds, the claim rule, the assumption rule, and the six prohibitions in its own text
- The resource-acquisition disposition vocabulary and the vault-request record shape

### 5. Planner tick, ranking, and registration
- **Task ID**: build-planner
- **Depends On**: build-records
- **Validates**: tests/unit/test_improvement_planner.py (create),
  tests/unit/test_improvement_ranking.py (create), tests/unit/test_reflection_register.py
- **Informed By**: spike-4 (snapshot via the verifying store's `save`), spike-5 (no evaluation in
  the tick)
- **Assigned To**: planner-builder
- **Agent Type**: builder
- **Parallel**: true
- `tools/improvement_ranking.py`: `rank` with the five ordinal rules each pinned by a test,
  the lexicographic order with `blocked`, `write_snapshot`, `load_snapshot`, `latest_snapshot`,
  the diff
- `reflections/improvement_plan.py`: `open_cases` with the identity rules and the novelty check,
  the pure `plan_tick` core and its `run_improvement_planner` wrapper with the four fail-soft
  steps, the `enabled` gate, the paused-head refusal, the idempotent single proposal, the
  `apply_verdict` backstop
- `process_digest` in `tools/improvement_ranking.py` on lane 6's canonical form, called by
  `revise-model`; `tools/improvement_plan_arm.py::PlannerArmRunner` and its conditional
  registration from the CLI entry (tests for both, including "nothing registers at import")
- `register_improvement_planner` and `register_improvement_assumption_digest` in
  `reflection_register.py` and `scripts/update/run.py`; parametrize the six registration tests
- CLI `ranking [--at DIGEST]`

### 6. Experiments, the arm pass-throughs, and the report
- **Task ID**: build-experiments
- **Depends On**: build-research, build-planner
- **Validates**: tests/unit/test_improvement_experiment.py (create),
  tests/unit/test_improvement_report.py (create), the arm worker's job-spec tests (extend)
- **Informed By**: spike-1 (`build_known_item_set` on exported records), spike-2 (the envelope is
  `{limit, rrf_k, min_rrf_score}`)
- **Assigned To**: experiment-builder
- **Agent Type**: builder
- **Parallel**: false
- `tools/improvement_experiment.py`: `ENVELOPES`, `validate_candidate`, `freeze_experiment`
  (manifest carries `base_revision` and `candidate_ref`), `evaluate_experiment` (reservation then
  `runner.evaluate`), `apply_verdict`, `repair`
- `arm_worker.py::handle_job` and `retrieval.py::retrieve_ranked_ids` pass-throughs, absent keys
  not forwarded
- `tools/improvement_report.py::build_report` with the three mandatory derived sections
- CLI `experiment freeze|evaluate|show|repair`, `report`

### 7. Digest and dashboard
- **Task ID**: build-surfaces
- **Depends On**: build-planner, build-research
- **Validates**: tests/unit/test_improvement_assumption_digest.py (create), tests/unit/test_ui_app.py
- **Assigned To**: surface-builder
- **Agent Type**: builder
- **Parallel**: true
- `reflections/improvement_assumption_digest.py` with the watermark rule, `on_escalation`, the
  fixed closing line, the sections, `send_host_eng_telegram`
- `get_ranking`, `get_hypotheses`, `get_rejected_approaches`; three templates; three inline
  routes; three index links; the goals partial's placeholder text and position column
- The exact-list test to seven names plus the no-activity-counter assertion

### 8. Validate wave 2 and the integration test
- **Task ID**: validate-loop
- **Depends On**: build-experiments, build-surfaces
- **Validates**: tests/integration/test_improvement_research_cycle.py (create)
- **Assigned To**: loop-validator
- **Agent Type**: validator
- **Parallel**: false
- Write and run the three integration tests named in Agent Integration
- Run every Verification row that does not depend on the real cycle
- Mutation-check each guard: disable the novelty check and confirm `rejected_is_not_reproposed`
  fails; drop the charter from the brief and confirm `opens_with_charter` fails; add `?` to the
  digest and confirm `asks_nothing` fails; widen the envelope and confirm the arm-worker row
  fails; restore each

### 9. The first real cycle
- **Task ID**: run-cycle
- **Depends On**: validate-loop
- **Assigned To**: lead (on the owning machine)
- **Agent Type**: builder
- **Parallel**: false
- Follow "Running the first real cycle" in Technical Approach, steps 1 through 7
- Record which dispatch path ran (adapter or `valor-session create`) and every reason code seen
- Post `valor-improve report --case ID` output on #3217 verbatim, with the claim "loop
  operational" and nothing higher

### 10. Documentation
- **Task ID**: document-feature
- **Depends On**: run-cycle
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Every item in the Documentation section, including the capability matrix's lane-5 section
  graded from the real cycle's records

### 11. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row
- Confirm each Success Criterion against the records, not the PR description
- Confirm the PR body carries the red-state proof for every anti-criterion row

## Verification

Anti-criteria use the `... | wc -l` shape so a clean tree emits `0` rather than empty stdout. Every command was executed against the tree at plan time to confirm it runs and produces the shape claimed; rows that depend on lane 3 or lane 4 surfaces are marked and were run against `session/sdlc-3216` where possible.

| Check | Command | Expected |
|-------|---------|----------|
| Planner, ranking, investigation, and adapter unit tests pass | `scripts/pytest-clean.sh tests/unit/test_improvement_planner.py tests/unit/test_improvement_ranking.py tests/unit/test_improvement_investigations.py tests/unit/test_improvement_evidence.py -q` | exit code 0 |
| Digest and brief tests pass | `scripts/pytest-clean.sh tests/unit/test_improvement_assumption_digest.py tests/unit/test_improvement_brief.py -q` | exit code 0 |
| Record, migration, registration, and dashboard tests pass | `scripts/pytest-clean.sh tests/unit/test_improvement_models.py tests/unit/test_migrations.py tests/unit/test_reflection_register.py tests/unit/test_ui_app.py -q` | exit code 0 |
| The complete cycle runs end to end on seeded evidence | `scripts/pytest-clean.sh tests/integration/test_improvement_research_cycle.py -q` | exit code 0 |
| Lint clean | `python -m ruff check reflections/ tools/improvement_ranking.py tools/improvement_investigations.py tools/improvement_brief.py tools/improvement_experiment.py tools/improvement_report.py tools/improvement_plan_arm.py ui/data/improvement.py models/improvement_investigation.py models/improvement_evidence.py scripts/update/` | exit code 0 |
| Format clean | `python -m ruff format --check reflections/ tools/ ui/data/ models/` | exit code 0 |
| Eight investigation kinds, exactly | `python -c "from models.improvement_investigation import INVESTIGATION_KINDS as K; assert set(K)=={'probe','trace_analysis','memory_retrieval','inspiration_intake','web_research','resource_acquisition','skill_acquisition','charter_amendment'}, K"` | exit code 0 |
| No question kind and no attention queue | `grep -rEn "\"question\"|attention_queue|daily_question|AskUserQuestion" models/improvement_*.py reflections/improvement_*.py tools/improvement_*.py \| wc -l` | match count == 0 |
| The poll registry and answer routing are untouched | `git diff --stat origin/main -- tools/ask_poll.py bridge/poll_registry.py bridge/answer_routing.py \| wc -l` | match count == 0 |
| No controller module writes `.env` or invokes `op` (`probe` and `vault_write` excluded) | `grep -rEln "\"op\"|\bop \b|/\.env\b|\.env\"" reflections/improvement_*.py tools/improvement_ranking.py tools/improvement_investigations.py tools/improvement_brief.py tools/improvement_experiment.py tools/improvement_report.py \| wc -l` | match count == 0 |
| Every recorded claim carries a URL and a retrieval date | `scripts/pytest-clean.sh tests/unit/test_improvement_investigations.py -k "claim_without_url_is_a_note or claim_without_date_is_a_note" -q` | exit code 0 |
| A rejected hypothesis is not re-proposed on the next tick | `scripts/pytest-clean.sh tests/integration/test_improvement_research_cycle.py -k rejected_is_not_reproposed -q` | exit code 0 |
| The verdict moves the ranking, visibly, between two snapshots | `scripts/pytest-clean.sh tests/integration/test_improvement_research_cycle.py -k verdict_moves_ranking -q` | exit code 0 |
| The brief opens with the pinned charter verbatim | `scripts/pytest-clean.sh tests/unit/test_improvement_brief.py -k opens_with_charter -q` | exit code 0 |
| The digest text says silence validates nothing and asks nothing | `scripts/pytest-clean.sh tests/unit/test_improvement_assumption_digest.py -k "silence_validates_nothing and asks_nothing" -q` | exit code 0 |
| `scripts/sdlc_reflection.py`, its installer, and its plist are gone | `ls scripts/sdlc_reflection.py scripts/install_sdlc_reflection.sh com.valor.sdlc-reflection.plist 2>/dev/null \| wc -l` | match count == 0 |
| `sdlc-reflection` is in the obsolete-service sweep | `grep -c '"sdlc-reflection"' scripts/update/service.py` | output > 0 |
| No live reference to the deleted script remains | `grep -rn "sdlc_reflection\|install_sdlc_reflection\|sdlc-reflection" --include="*.py" --include="*.md" --include="*.sh" --include="*.toml" . --exclude-dir=.worktrees --exclude-dir=archive --exclude-dir=.git \| grep -v "docs/plans/" \| grep -v "scripts/update/service.py" \| wc -l` | match count == 0 |
| Lesson adapter is wired into the tick | `grep -c "collect_lessons" reflections/improvement_collect.py` | output > 1 |
| Promise adapter is wired into the tick and gated | `grep -c "collect_promises\|promise_detector" reflections/improvement_collect.py` | output > 1 |
| The dashboard exports exactly the seven honest getters | `python -c "import ui.data.improvement as m; assert [n for n in dir(m) if n.startswith('get_')]==['get_coverage','get_goals','get_hypotheses','get_intervention_burden','get_provisional_assumptions','get_ranking','get_rejected_approaches']"` | exit code 0 |
| No dashboard getter returns an activity counter | `grep -rEn "experiment_count|merged_patch_count|patches_merged" ui/data/improvement.py ui/templates/improvement/ \| wc -l` | match count == 0 |
| No `ImprovementRelease` writer in this lane | `grep -rEn "ImprovementRelease\(|ImprovementRelease\.create|release\.save\(" reflections/improvement_*.py tools/improvement_ranking.py tools/improvement_investigations.py tools/improvement_experiment.py tools/improvement_report.py \| wc -l` | match count == 0 |
| The arm worker still reads only what `retrieve_memories` accepts (lane 4 surface) | `python -c "import inspect; from agent.memory_retrieval import retrieve_memories as r; from tools.improvement_eval import arm_worker; src=inspect.getsource(arm_worker.handle_job); assert 'rrf_k' in src and 'min_rrf_score' in src and 'retrieval_mode' not in src"` | exit code 0 |
| Both migrations registered | `python -c "from scripts.update.migrations import MIGRATIONS; ks=' '.join(MIGRATIONS); assert 'retire_sdlc_reflection' in ks and 'improvement_investigation_stage' in ks, ks"` | exit code 0 |
| Three improvement reflections registered from `run.py` | `grep -c "register_improvement_collect\|register_improvement_planner\|register_improvement_assumption_digest" scripts/update/run.py` | output > 2 |
| Charter unwritten by this lane | `git diff --stat origin/main -- docs/improvement-charter.md models/improvement_charter.py \| wc -l` | match count == 0 |
| `docs/sdlc/` files untouched by the retirement (eleven on main) | `python -c "import glob,sys; sys.exit(0 if len(glob.glob('docs/sdlc/*.md'))==11 else 1)"` | exit code 0 |
| Existing auto-generated reflection notes preserved | `git diff --stat origin/main -- docs/sdlc/ \| wc -l` | match count == 0 |
| Process digest matches lane 6's canonical form | `scripts/pytest-clean.sh tests/unit/test_improvement_ranking.py -k process_digest_canonical -q` | exit code 0 |
| Manifest carries both refs | `scripts/pytest-clean.sh tests/unit/test_improvement_experiment.py -k "manifest_carries_base_revision_and_candidate_ref" -q` | exit code 0 |
| Arm runner registers only from the CLI entry | `scripts/pytest-clean.sh tests/unit/test_improvement_planner.py -k "arm_runner" -q` | exit code 0 |
| Retrieval envelope refuses `retrieval_mode` | `scripts/pytest-clean.sh tests/unit/test_improvement_experiment.py -k "refuses_retrieval_mode" -q` | exit code 0 |
| Plan critique verdict recorded | `grep -c "READY TO BUILD" docs/plans/improvement-controller-lane-5-first-complete-research-cycle.md` | output > 0 |

## Critique Results

Critique mode: sequential lenses (Agent tool unavailable in the stage runner: not in tool list); three roster lenses applied in sequence, FULL depth (force-FULL: `appetite: Large`, touches `.claude/skills/`). Verdict: READY TO BUILD (with concerns).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | Freeze step does not fit lane 4's surfaces: `build_known_item_set(export.records, ...)` names an attribute `CorpusExport` lacks (`corpus.py:130-142` carries `jsonl_text`, not `records`), the builder returns fewer than `n_queries` on degenerate generations, and `"batch_size": n_queries` then makes `FixedBatchStoppingRule.is_complete` (`correction.py:95-97`, `runner.py:768-770`) return `inconclusive` unconditionally on any shortfall. | pending | Build `records` from `export.jsonl_text` via a small adapter type carrying `content`, `importance`, `memory_id`; `queries = [...]` from the generated items; refuse with `KNOWN_ITEM_SHORTFALL` below a stated `MIN_QUERIES`; set `protocol["batch_size"] = len(queries)` after generation. The incumbent dict passed to `capture_baseline` and written to `protocol["incumbent"]` must be exactly `{"limit": 10}` with no `None`-valued keys, because `_retrieve_job` (`runner.py:291-299`) copies every present key into the job and a present `None` is not "absent". |
| CONCERN | Risk & Robustness | `ImprovementInvestigation` and `ImprovementEvidence` carry `Meta.ttl = 86400 * 30` (`models/improvement_investigation.py:96-99`, `models/improvement_evidence.py:141-146`) while the case is immortal; the `vault_request_written` disposition that keeps the inference case blocked, and the `charter_amendment` row in `awaiting_authorization`, both wait on Tom with no deadline and expire silently after thirty days (block lifts or reason vanishes, digest cannot render the request, no decline is ever recorded). | pending | Add plain unindexed `blocked_by = Field(null=True)` on `ImprovementCase` (add to `FORBIDDEN_INDEX_NAMES`), set by `resolve()` on a `vault_request_written` disposition (`"vault request: <item title>"`) and cleared by the tick when `tools.improvement_resources.probe` reports `verified`; `rank()` reads `case.blocked_by`, never an investigation. For amendments, the tick's amendment-resolution hook calls `investigation.save()` each tick while `state == "awaiting_authorization"` (refreshing the TTL) and writes the request text onto `case.summary` the way the assumption tail is written. |
| CONCERN | Scope & Value | A second implementation of lane 6's canonical form (`tools/improvement_ranking.py::process_digest`, "so the two agree byte for byte once both exist") is the drift the verifying store exists to prevent; one field-order or float-formatting difference makes every revision digest written before lane 6 merges incomparable with every one after. | pending | Keep one implementation: store the canonical spec JSON on the revision (`research_process_spec = Field(null=True)`, `json.dumps(asdict(spec), sort_keys=True, separators=(",", ":"))`), set `research_process_digest` only via `from tools.improvement_recursion.process import research_process_digest` and leave it `None` when the import fails; a one-line backfill computes digests for rows with a spec and no digest once lane 6 lands. The fixture test pins the canonical spec bytes, shareable verbatim with lane 6's plan. |
| CONCERN | Scope & Value | The first case is planted by the builder ("an `inspiration` row the builder writes citing charter §3"), and nothing in the records distinguishes a seeded row from an observed one, so the report's "What this does not establish" cannot say it and a reader of the records alone cannot tell. This is the root-cause pattern the plan itself names (output whose input had no validated connection to behavior). | pending | Seed with `source_ref="seed:charter-s3:inference"` and `detail=json.dumps({"seeded_by": "build task 9", "plan": "#3217"})`; `build_report` adds to "What this does not establish" every case whose evidence has any `source_ref.startswith("seed:")`; Success Criteria state whether any case in the real cycle opened from organically collected evidence. Integration tests keep using seeded rows (they test mechanism, not provenance). |
| CONCERN | History & Consistency | The freeze's novelty check cites `docs/plans/hybrid-retrieval-eval.md`, which does not exist (migrated to `docs/archive/plans-completed/hybrid-retrieval-eval.md` in 449df07a0; live doc `docs/features/hybrid-retrieval-eval.md`), and its trigger ("varies only `retrieval_mode`-adjacent behavior") is too vague for a builder to apply, so the one prior answer the plan says must be surfaced may never be. | pending | In `freeze_experiment`: `if envelope == "retrieval_parameters": prior_answers.append({"ref": "#2082", "doc": "docs/features/hybrid-retrieval-eval.md", "plan": "docs/archive/plans-completed/hybrid-retrieval-eval.md", "why": "prior paired evaluation of retrieval over this corpus"})`, unconditionally for this envelope; `test_improvement_experiment.py` asserts a frozen retrieval experiment's `prior_answers` names `#2082`. |
| CONCERN | History & Consistency | Case opening "reads evidence rows newer than the last tick's watermark" and opens a case only at `CASE_OPEN_MIN_EVIDENCE = 2` rows; a cluster that accrues one row per tick never has two rows inside one window, the watermark advances past the first, and the cluster never opens (only the single-`architectural` exception or rows landing between the same two ticks ever become cases). The `rejected_is_not_reproposed` test seeds both rows at once and cannot see this. | pending | Cluster over unconsumed rows, not a time window: `consumed = {eid for c in ImprovementCase.query.filter(project_key=pk) for eid in (c.evidence_ids or [])}`; `pending = [e for e in evidence_rows_since(watermark_minus_ttl) if e.id not in consumed]`; cluster `pending` by `dedup_identity`; open at `len(cluster) >= CASE_OPEN_MIN_EVIDENCE` and append every cluster row id to `case.evidence_ids` in the same save. Add a planner test: one row, tick, second row same identity, tick, exactly one case with both ids. |
| CONCERN | History & Consistency | Success Criteria say the existing memory-inspiration adapter is "exercised end to end in the real cycle", but runbook step 2 seeds the `inspiration` evidence row directly; `collect_inspirations` reads `human_memories(project_key)` (`reflections/improvement_collect.py:205`, `:329`) and writes the row itself, so a directly written row bypasses the adapter and no Verification row would notice. | pending | Runbook step 2 becomes: save one `Memory` row into the partition `human_memories` enumerates, with the fields (including the URL) `collect_inspirations` keys on, then run `run_improvement_collect()` and assert `counts["inspirations"] >= 1`; the seeded memory carries the `seed:` marker in whatever field survives into `source_ref`. |
| NIT | History & Consistency | `run_improvement_collect`'s status rule is hard-coded to three adapters (`"status": "error" if len(findings) == 3`, `reflections/improvement_collect.py:511`), and the promise adapter's meter-refusal skip is appended to `findings`, so a routine unit-2 refusal would count toward "error" once five adapters exist. | pending | Track failures and skips in separate lists; `status = "error" if len(failed) == n_adapters else "success"`. |
| NIT | History & Consistency | Freshness Check says lane 6's plan is "on `session/sdlc-3218`, not yet on main"; it is on main as of `ee656d8af`. | pending | Drop the branch qualifier in the revision pass. |

---

## Open Questions

Each question carries the disposition the build proceeds on if no answer arrives (charter §9:
provisional assumptions, stated with their overturning observation). These are plan-scope
questions to the pipeline's owner, which charter §9 permits; none is a research question the loop
would ask.

1. **The issue body versus charter v2.** The plan follows charter v2, the parent plan's lane-5
   paragraph, and Tom's 2026-09-09 refresh comment where the issue body (v1) disagrees (Freshness
   Check table). Confirm that reconciliation, in particular that the first experiment is the top
   ranked *experimentable* case in the retrieval-parameter envelope rather than the issue's
   journey-preservation change to planning and context assembly.
   **Disposition:** proceed on charter v2; the issue's acceptance criteria are re-read against it
   in Success Criteria. Overturned by: a comment on #3217 directing the v1 reading.

2. **Deferring charter §5 stages 4 and 5 and the cheap-inference experiment to #3311.** This lane
   has no agent-run arm and cannot honestly measure a skill's effect or a provider's suitability
   on agent tasks. The parent plan's "Carried to child issues" line for lane 5 expects one complete
   `skill_acquisition` cycle; this plan runs stages 1 through 3 and records stage 4 as a
   provisional assumption.
   **Disposition:** proceed with the deferral; #3311 is filed and cited. Overturned by: direction
   to fold the agent-run arm into this lane, which would move the appetite past Large and is
   recorded here as the cost.

3. **Turning the promise detector on.** It spends unit-2 money on a fifteen-minute tick and is off
   by default. The build tests it with an injected transport and does not enable it on the owning
   machine.
   **Disposition:** ship it off; the real cycle runs without it. Overturned by: an explicit
   `IMPROVEMENT__PROMISE_DETECTOR_ENABLED=true` on the owning machine after reading the report.

4. **Lane 3's actual surface.** The consumed contract is written as requirements because lane 3
   has not planned. If lane 3 lands `propose` as CLI-only, the planner shells to it; if the journal
   module is named differently, the Prerequisites row is corrected in the first commit.
   **Disposition:** adapt to what merges; the contract is the requirement, not the name.
   Overturned by: lane 3's plan declaring it will not provide one of the eight requirements, in
   which case that requirement moves to Open Questions on #3215 before this build starts.

5. **The first real cycle's dispatch path.** If lane 3's scheduler adapter is not enabled on the
   owning machine when the build reaches task 9, the runbook falls back to `valor-session create`
   for the research skill.
   **Disposition:** either path is acceptable for a level-1 claim and the report records which
   ran. Overturned by: direction that only adapter-dispatched runs count.
