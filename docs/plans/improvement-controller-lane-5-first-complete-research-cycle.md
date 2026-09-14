---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-14
tracking: https://github.com/tomcounsell/ai/issues/3217
last_comment_id: 5603829577
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
- #3218 (lane 6): OPEN, filed 2026-09-07. Owns releases and the recursive comparison; this lane
  writes no `ImprovementRelease`.
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

(filled below)

## Data Flow

(filled below)

## Why Previous Fixes Failed

(filled below)

## Architectural Impact

(filled below)

## Appetite

(filled below)

## Prerequisites

(filled below)

## Solution

### Key Elements

(filled below)

### Flow

(filled below)

### Technical Approach

(filled below)

## Failure Path Test Strategy

(filled below)

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

(filled below)

## Risks

(filled below)

## Race Conditions

(filled below)

## No-Gos (Out of Scope)

(filled below)

## Update System

(filled below)

## Agent Integration

(filled below)

## Documentation

### Feature Documentation
- [ ] Create `docs/features/improvement-research-cycle.md`: the planner tick, the ranking snapshot and its diff, the investigation lifecycle (eight kinds, five indexed states, the unindexed `stage`), the claim rule, provisional assumptions and the three-day digest, the brief contract, the experiment envelope for this lane (retrieval arm parameters), the verdict-to-selection rule, the two new observer adapters, and the qualified-result report format
- [ ] Update `docs/features/improvement-controller.md`: "What exists today" (`:12-21`) names lane 5; the Three layers "Research reasoning" paragraph (`:29-32`) drops "Not built yet (lane 5)"; the Dashboard section (`:364-395`) documents the three new partials and the seven-getter list; the Evidence collection table (`:92-96`) gains the `lesson` and `promise` adapter rows; a "Research cycle" section links the new feature doc
- [ ] Update `docs/features/sdlc-repo-addenda.md` (`:46-92`): the reflection-agent section is replaced by one paragraph stating that lessons in PR bodies are now `ImprovementEvidence` rows of kind `lesson` read by the planner, and the `com.valor.sdlc-reflection` rows leave the file table
- [ ] Update `docs/features/launchctl-bootstrap-fail-soft.md:76`: remove the `install_sdlc_reflection.sh` row
- [ ] Update `docs/features/log-rotation.md:40` and `docs/features/nightly-regression-tests.md:371`: remove the `sdlc_reflection.py` / `sdlc_reflection_last_run.json` references
- [ ] Update `docs/tools-reference.md` (`:345-361`): the `valor-improve` block gains `ranking [--at DIGEST]`, `investigation open|record|resolve`, `revise-model`, `experiment freeze|evaluate`, and `report`, and its "planned, lane 3" marker is corrected once lane 3 lands
- [ ] Update `docs/plans/critiques/recursive-self-improvement-capability-matrix.md`: add a lane-5 section grading each component on the four axes, and correct the "Not built, by lane" row for lane 5 (`:134`)
- [ ] Update `.claude/skills/update/SKILL.md:115` and `.claude/skills/setup/SKILL.md:105`: remove the `./scripts/install_sdlc_reflection.sh` line
- [ ] Add the feature doc row to `docs/features/README.md`
- [ ] Post the qualified-result report of the first real cycle as a comment on #3217 (what was measured, what it does not establish, what would change the answer), linked from the feature doc

### Inline Documentation
- [ ] Module docstrings on every new module state the TTL and index decisions in the schema-gate voice `tests/unit/test_improvement_models.py::test_ttl_decision_is_recorded_in_the_docstring` reads
- [ ] `reflections/improvement_collect.py` module docstring lists five adapters, not three, and names the production writer of each new input

## Success Criteria

(filled below)

## Team Orchestration

(filled below)

## Step by Step Tasks

(filled below)

## Verification

Anti-criteria use the `... | wc -l` shape so a clean tree emits `0` rather than empty stdout. Every command was executed against the tree at plan time to confirm it runs and produces the shape claimed; rows that depend on lane 3 or lane 4 surfaces are marked and were run against `session/sdlc-3216` where possible.

| Check | Command | Expected |
|-------|---------|----------|
| Planner, ranking, investigation, and adapter unit tests pass | `scripts/pytest-clean.sh tests/unit/test_improvement_planner.py tests/unit/test_improvement_ranking.py tests/unit/test_improvement_investigations.py tests/unit/test_improvement_evidence.py -q` | exit code 0 |
| Digest and brief tests pass | `scripts/pytest-clean.sh tests/unit/test_improvement_assumption_digest.py tests/unit/test_improvement_brief.py -q` | exit code 0 |
| Record, migration, registration, and dashboard tests pass | `scripts/pytest-clean.sh tests/unit/test_improvement_models.py tests/unit/test_migrations.py tests/unit/test_reflection_register.py tests/unit/test_ui_app.py -q` | exit code 0 |
| The complete cycle runs end to end on seeded evidence | `scripts/pytest-clean.sh tests/integration/test_improvement_research_cycle.py -q` | exit code 0 |
| Lint clean | `python -m ruff check reflections/ tools/improvement_ranking.py tools/improvement_investigations.py tools/improvement_brief.py tools/improvement_experiment.py tools/improvement_report.py ui/data/improvement.py models/improvement_investigation.py models/improvement_evidence.py scripts/update/` | exit code 0 |
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
| Plan critique verdict recorded | `grep -c "READY TO BUILD" docs/plans/improvement-controller-lane-5-first-complete-research-cycle.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

(filled below)
