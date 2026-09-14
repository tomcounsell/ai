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

(filled below)

## Freshness Check

(filled below)

## Prior Art

(filled below)

## Research

(filled below)

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
