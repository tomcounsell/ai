# Recursive self-improvement: capability matrix

Companion to [`docs/plans/recursive-self-improvement.md`](../recursive-self-improvement.md),
produced by lane 2 of #3177.

The matrix exists because "we built it" and "it works in production" are
different claims, and the plan's own Risk 2 is that missing outcome evidence
gets read as success. Each component below is marked against four separate
columns, and a component can be **implemented** while being **not deployed**,
**not measured**, and honestly **unknown** in its effect. Collapsing those into
one "done" is the failure this table prevents.

Column meanings, stated once so nothing is graded on a sliding scale:

| Column | What it means |
|---|---|
| **Implemented** | The code exists on `main` and its tests pass |
| **Deployed** | It runs on the machine that owns the `valor` project, on a schedule or in a request path |
| **Measured** | Someone has read a number off it that came from real traffic, not a fixture |
| **Effect** | What we can honestly say about whether it helps. `unknown` is the correct answer for everything shipped this week |

State as of lane 5 (#3217), the first real cycle. Later lanes update this file
rather than starting a new one.

## Lane 1 — autoexperiment retirement

| Component | Implemented | Deployed | Measured | Effect |
|---|---|---|---|---|
| `scripts/autoexperiment.py` and its installer, plist, tests, and doc removed | yes | yes (on merge) | n/a | removes a live footgun; nothing to measure |
| `/update` reaps the `<prefix>.autoexperiment` LaunchAgent by exact label | yes | on the next `/update` per machine | no | unknown — no machine in this fleet is known to have the job installed |
| Legacy corpora retained and marked | yes | n/a | n/a | n/a |

The retired script was **never installed or run on any machine in this fleet**:
no log, no dated result set, no run history. Any claim that autoexperiment
"optimized prompts in production" would be false, and this row is here so nobody
later reconstructs one from the corpora.

## Lane 2 — records, settings, evidence, dashboard

| Component | Implemented | Deployed | Measured | Effect |
|---|---|---|---|---|
| Eight `Improvement*` Popoto models | yes | yes (schema is live once merged) | no | n/a — storage, not behavior |
| `ImprovementSettings` in `config/settings.py` | yes | yes, `enabled=False` | no | n/a |
| `IMPROVEMENT__ENABLED` declared and read | yes | unset everywhere | no | n/a |
| `VerifyingArtifactStore` | yes | yes: lane 4's harness writes corpora, protocols, judge responses, and calibration sets to it | no | unknown |
| Correction detector (`collect_corrections`) | yes | registered on the owning machine; writes only once `IMPROVEMENT__ENABLED=true` | **yes, once**: the first real cycle's evidence tick found zero corrections in the window | unknown |
| Memory-inspiration adapter (`collect_inspirations`) | yes | same | **yes, once**: two `inspiration` rows written from the `valor` memory partition (one seeded, one organic) | unknown |
| Expectation coverage adapter | yes | same | **yes, once**: one coverage row per tick in the first real cycle | unknown |
| Shipped-work evidence at the reconciler's own site | yes | with the next reconciler run | **no** | unknown |
| `improvement-evidence-collect` reflection registration | yes | on the next `/update` of the owning machine | no | n/a |
| `retire_task_type_profile` migration | yes | on the next `/update` per machine | no | n/a |
| Dashboard: coverage + intervention-burden partials | yes | yes | no | n/a |

**The honest reading of this lane.** Evidence collection is implemented and
registered; on merge day `ImprovementEvidence` is empty, because the master
switch is off and no correction may have occurred yet either way. That is why
the plan's merge-day gate is `count() >= 0` plus a render smoke test rather than
`count() > 0`. Graduation to `count() > 0` needs two things, in this order:
`IMPROVEMENT__ENABLED=true` in the vault `.env` on the machine that owns
`valor`, then a week of normal use, checked by hand and recorded on #3177.

**The detector's inputs both have production writers**, which is the property
the retired instrumentation lacked: inbound `AgentSession.chat_message_log`
entries (`bridge/dispatch.py::_append_inbound_chat_log`) and `Memory` rows with
`source="human"` (`bridge/telegram_bridge.py`). `AgentSession.log_path` is not
read — its only assigner has no production caller, which would reproduce the
exact defect the table below records as retired.

## Lane 2b: charter v2 delta

| Component | Implemented | Deployed | Measured | Effect |
|---|---|---|---|---|
| `ImprovementCharter.digest` / `effective` / `text` | yes | yes (schema is live once merged) | no | n/a (storage, not behavior) |
| `ImprovementCharter.load_from_file` seed | yes | called by every planner tick | **yes**: charter v2 pinned at `sha256:2df4522a…` on the owning machine | n/a |
| `ImprovementCharter.pinned` | yes | read by the goals partial | no | n/a |
| `PRIORITY_AREAS` + `priority_area` / `ranking_rationale` | yes | written by lane 5's planner tick | **yes, once**: one production case in `inference` | n/a |
| `charter_digest` on case, investigation, release | yes | written on cases by the planner tick, on investigations by `open_investigation`; no release row exists | **yes, once** | n/a |
| `objective` deleted | yes | yes, no rows existed | n/a | n/a |
| Three budget units in `ImprovementSettings` | yes | yes, defaults only | **no** | n/a (declared limits, nothing meters them) |
| `is_open_source` charter §7 guard | yes | consulted by the promise judge's default transport and lane 4's judge routing | no | unknown |
| `probe()` charter §8 verification | yes | run by hand, see below | **yes, once** | n/a |
| Goals partial (`/_partials/improvement/goals/`) | yes | yes | no | n/a |
| `confirm_improvement_v2_fields` migration | yes | on the next `/update` per machine | no | n/a |

**The resource probe's measured result**, run on Tom's MacBook Air at build
time. This is one machine at one moment, not a fleet statement.

| Resource | State | Why |
|---|---|---|
| `workspace_personal` | unknown | the `m-valor` vault listing could not be read |
| `workspace_work` | unknown | same |
| `virtual_debit_card` | unknown | same |
| `cloudflare_account` | unknown | same |
| `cloudflare_cli` | absent | `wrangler` is not installed on this machine |
| `vault_write` | absent | the sanctioned vault writer is lane 3's and does not exist |

The four `unknown`s are an environment result, not a vault result. `op` is
installed but `OP_SERVICE_ACCOUNT_TOKEN` was not set in the build shell, so the
CLI fell back to an interactive prompt and the probe's timeout bounded it. That
is exactly why `unknown` and `absent` are separate states: reporting these four
absent would send lane 3 out to acquire accounts that may well already exist.
Re-run the probe under the service-account token before treating any of them as
missing.

**The honest reading of this lane.** It corrects vocabulary and adds two guards
with no callers. Nothing here changes behavior on merge day: the guards are
libraries lane 3 consumes, the seed has no caller, and the goals partial renders
mostly empty sections that name the lane which will fill each. The one thing it
does change is that the settings and the feature doc stop describing decisions
the charter withdrew.

## Lane 3 — control journal, fenced dispatch, and the valor-improve CLI

| Component | Implemented | Deployed | Measured | Effect |
|---|---|---|---|---|
| Control journal (`journal.py::transition`, one Lua `EVAL` per Decision 3) | yes | yes: the first real cycle wrote 27 revisions on one production case (`case_opened` through `verdict_applied`), including a research session's own `action_proposed` under its running intent | **yes, once** (one case) | n/a (it fenced; no race occurred) |
| Lease protocol + interim `CaseLease` | yes | consumed by the tick, the reconcile pass, and the CLI's own writes | no | n/a (fences a controller that has never yet raced) |
| Dispatch intents, six-state machine, lane-slot reservation | yes | yes: two intents in production, one `admitted → materialized → running → reconciliation_required → cancelled`, one `admitted → materialized → running → settled`; the slot released both times | **yes, once** | n/a |
| `admitted` session status (lifecycle, ownership, dashboard) | yes | yes, the constant is live | no | n/a (no row has used it yet) |
| Scheduler adapter (`improvement-controller-tick`) | yes | registered; gated off by `ImprovementSettings.enabled=False` default; run by hand twice in the first real cycle with the switch set in the shell | **yes, once**: the second dispatch admitted, materialized, and activated in one pass. The first exposed two defects (session lookup by `session_id` instead of `agent_session_id`; no lane slug from a worktree), both fixed on lane 5's branch | unknown |
| Reconcile reflection (`improvement-intent-reconcile`, 300s) | yes | registered, runs regardless of the enabled switch | **yes, once**: forced reconciliation of the first cycle's stranded intent (`session_gone_or_terminal`), slot released, cleared by `resume --force` | n/a |
| Unit-2 paid-inference meter (`tools/paid_inference_meter.py`) | yes | yes: the first real cycle reserved and settled `known_item_generation` (USD 0.06, `metering="estimated"`) and reserved then released `evaluation_judges` | **yes, once** | n/a (the settled figure is an estimate until the reconcile pass corrects it) |
| Cross-vendor judge's `sdlc_review` receipt | yes | yes, fires on every judge run | **yes** (receipts accumulate on every SDLC review) | n/a (record-only, never gates) |
| Vault writer (`tools/vault_write.py`) | yes | no write attempted in production yet | no | unknown |
| `valor-improve` CLI, twelve control subcommands | yes | yes: every write of the first real cycle's research session went through it | **yes, once** | n/a |
| `.claude/skills/improve-research/SKILL.md` | yes (body rewritten by lane 5 to the brief-first contract) | yes: one research session ran under it for real (8 turns, 2026-09-15) | **yes, once**: six investigations, three revisions, one proposal, one frozen experiment, nothing written to the tree, none of the six prohibitions broken | unknown |
| Dashboard control panel (`get_control_status`) | yes | yes, renders on the root dashboard | no | n/a |
| Mutation review of every fence (Task 12) | yes, 12/12 mutations caught | one-time build-time exercise, not a running check | n/a | n/a |

**The honest reading of this lane.** Every primitive in the plan's Data Flow is
implemented and tested — including the four fault-injection scenarios the
issue's acceptance criteria name (stale-generation rejection split by writer
kind, a crash between admission and session creation, an unreleased lane slot
on restart, and journal unavailability with `doctor` as the break-glass read)
— and lane 5's first real cycle ran every primitive against one real
research session: the adapter admitted and dispatched one production case, the
session proposed under its running intent, the reconcile pass cleared one
stranded intent, and the meter settled USD 0.06. One case is one data point,
so every "yes, once" above is exactly that. `#3220` (the production
session-execution lease) is still open; this
lane consumes its declared interface through the interim `CaseLease`, deletable
in one commit the day `models/redis_lease.py` exists (hand-off posted on
#3220).

## Retired instrumentation

| Component | Status | Why |
|---|---|---|
| `TaskTypeProfile.rework_rate` | **deleted** | Structurally always zero. Its input, `AgentSession.rework_triggered`, had no production writer, so the aggregate measured nothing and the recommendation derived from it was derived from nothing |
| `TaskTypeProfile.failure_stage_distribution` | **deleted** | Zero writers |
| `get_delegation_recommendation` | **deleted** | Zero production callers; only tests |
| `models/task_type_profile.py` | **deleted whole** | `delegation_recommendation` was an `IndexedField` whose sole reader was the dead recommendation function; keeping the model while removing the reader would strand a live indexed field |

Rework is now derived from `ImprovementEvidence` rows carrying
`classification="architectural"`, which have a real writer. **No baseline is
available from the deleted instrumentation.** Its historical values were zeros
produced by an absent writer, not observations of low rework, and treating them
as a baseline would manufacture an improvement out of nothing.

## Lane 4 — frozen evaluation inputs

| Component | Implemented | Deployed | Measured | Effect |
|---|---|---|---|---|
| `tools/improvement_eval/runner.py`: gate ordering, three exit handlers, single `ImprovementEvaluation` writer | yes | yes: `valor-improve experiment evaluate` (lane 5) calls `evaluate()` from the research session | **yes, once**: one production evaluation, verdict `infra_failure`, trials 0 | n/a |
| Frozen corpus export, canonical digest, ORM restore (`corpus.py`) | yes | no | no | n/a |
| Per-arm private Redis with a subprocess-only client (`arena.py`, `arm_worker.py`) | yes | no | **measurable**: two arms re-export and hash their own corpus at run time, and the run refuses on a mismatch | unknown |
| Writer kill switch plus digest re-check (`writer_guard.py`) | yes | no | **measurable**: an escaped write surfaces as `infra_failure`, proven by mutation | unknown |
| Baseline parity gate before the candidate arm (`retrieval.py`) | yes | no | **measurable**: a parity miss is an `infra_failure` and the candidate is never invoked, proven by mutation | unknown |
| Blinding: seeded assignment, blinded ids, identity scan (`blinding.py`) | yes | no | **measurable**: `blinded` is a typed boolean written from the scan, never assumed | unknown |
| `serves-charter` judge with charter §7 routing (`judges/serves_charter.py`) | yes | no | no — no real provider call has been made; every test injects the transport | unknown |
| Calibration against a frozen reference set (`calibration.py`) | yes | yes | **yes, once, and blocked as predicted**: the first real cycle's evaluation returned `infra_failure` because the `valor` partition holds 0 `architectural` corrections against the floor of 20 | unknown |
| Holm correction and fixed-batch stopping (`correction.py`, `statistics.py`) | yes | no | no | n/a |
| `ImprovementEvaluation.charter_digest` and its migration | yes | on the next `/update` per machine | no | n/a |
| `VALOR_PROJECT_KEY` in `_harness_env` | yes | with the next worker restart | no | fixes a real partition leak for non-`valor` sessions; unmeasured |

**The honest reading of this lane.** The apparatus is implemented and every
guard has a red-state proof, which makes the *properties* (byte-identical
reads, parity, blinding, an honest `infra_failure`) measurable at run time.
No candidate has been evaluated: the arms compare retrieval on a seeded corpus
in tests, and the control loop that would freeze a real experiment is lane
3's. A calibrated judge needs twenty retained architectural corrections and
there are none yet, so the first real run will report `infra_failure` from the
calibration floor, on purpose.

## Lane 6: promotion, rollback drills, and the recursive comparison

| Component | Implemented | Deployed | Measured | Effect |
|---|---|---|---|---|
| Release lifecycle (`tools/improvement_release/lifecycle.py`): six states, every transition a guarded function, `ReleaseRefused` closed vocabulary, bounded history | yes | no: every transition is operator-invoked through `valor-improve-release`; nothing calls it on a schedule or in a request path | no: no production `ImprovementRelease` row exists; the only rows ever written live in claimed test dbs | n/a |
| `ImprovementRelease` lane-6 fields, `accepted` state, `confirm_improvement_release_lane6_fields` migration | yes | on the next `/update` per machine | no | n/a: storage, not behavior |
| Rollback drill (`drill.py`): throwaway worktree under the retention root, three pre-revert range checks, range revert, per-surface and whole-tree restoration, timed `verify`, `exercised`/`not_exercised` record, `drill_log` on the verifying store, `--sweep` | yes | no | **measurable in a worktree**: `tests/integration/test_improvement_release_drill.py` runs the whole path against a real temporary git repository, a real `ImprovementRelease` row in a claimed test db, and the real `SubprocessRunner`, and reads `pass` with the transcript re-hashed on load. A `pass` proves a range revert on a candidate branch restores the tree; `not_exercised` names the fleet update, production traffic, and the `-m 1` merge-commit revert the real rollback runs. No drill has run against a production release row, because none exists | unknown |
| Real rollback (`lifecycle.rollback`): fetch-first parent, revert of the merge on `origin/<target>`, push gate confirmed by `ls-remote`, `ROLLBACK_PUSH_REFUSED` with the orphaned revert recorded | yes | no | no: never run against a real release; the push gate and the fetch-before-worktree order are proven by recording-runner tests and mutation | unknown |
| Exposure anchored on `mergedAt`, frozen baseline over `[merged_at - baseline_days, merged_at)`, window restamp, `EVIDENCE_EXPIRED` at expose | yes | no | no: `gh pr view` is canned in every test | n/a |
| Observation window (`observation.py`): raw counts beside every rate, `EVIDENCE_TRUNCATED`, Wilson band, 0.8 detection-decline ratio, `claim_level_2_supported`, falsifier | yes | no | no: no window has been observed on real traffic; `ImprovementEvidence` is empty while `IMPROVEMENT__ENABLED` is off | unknown |
| Promotion gate (`promotion.py`): `automated=False`, both preconditions named, `promote_automatically` raises on every call; no setting, env key, or file flag reads into it | yes | yes: the gate answer is read by the releases partial on every dashboard load and recorded on every approval | **yes**: against the real pinned charter v2 the gate reports both preconditions unmet, checked by a Verification row | n/a: it refuses; automated promotion is **not implemented, by design** |
| Candidate-surface denylist (`denylist.py`): charter, charter model, identity, this package, secrets files, git hooks; normalized paths, globs and escapes refused | yes | yes: consulted by every `propose` | **yes**: a Verification row runs it against the charter paths | n/a. It catches only what it names; a candidate editing `models/__init__.py` or the loader's owner check from another file passes it, and the loader's owner refusal and the pinned-digest gate are the other two guards |
| Research process digest (`tools/improvement_recursion/process.py`): canonical bytes, one hashing routine shared with lane 5 | yes | yes: lane 5's `revise-model` stores `process_spec_json(spec)` and sets `research_process_digest` only through this function; the cross-lane byte test runs against `tools/improvement_ranking.py`'s fixture | **yes, once**: three production revisions carry `sha256:3d9c6660…` | n/a |
| Freshness by record lookup (`freshness.py`) | yes | no | no | n/a |
| Budget accounting (`budget.py`): four units, `LedgerBudgetReader` unit 3 by `arm:<run>:` prefix, `None` on zero rows, `budgets_comparable` | yes | no | **partly measurable**: unit 3 reads real ledger rows through the same `admit()`/`settle()` path a production arm takes. **Unit 2 (paid inference) has no arm-scoped read**; lane 3's meter windows the daily pool with no `arm_run_id`, so `unit2_usd` answers `None`, so every comparison today refuses a claim with `BUDGET_UNKNOWN:unit2`; unit 1 is the subscription lane slot, accounted as `subscription_turns` | n/a |
| Comparison (`compare.py`): frozen contract through lane 4's `freeze_protocol`, two arm seams (`--arm-runner` lazy import, in-process registry), seeded arm order, paired deltas clustered by `priority_area`, Holm, one evaluation in lane 4's string shapes, new-then-supersede revision write, `REVISION_CONFLICT` | yes | no | no: every run uses `ReplayArmRunner` fixtures. The production `ArmRunner` is lane 5's `PlannerArmRunner` (one planner tick per run, gains from `accept` evaluations already on record), registered by every `valor-improve` invocation; no comparison has run under it | unknown |
| Claim report (`report.py`): three ladder levels, each degrading independently, interval, correction, falsifier, `why_not`; no counts | yes | yes: `valor-improve-release report` and the releases partial | **yes, trivially**: on the real project it reports all three levels unsupported (no complete cycle, no accepted release, no comparison) | n/a: the report says what is true, and today that is "unsupported" three times |
| Dashboard releases partial and `get_release_lineage` (sixth getter beside lane 3's `get_control_status`, pinned list) | yes | yes | no | n/a |
| `valor-improve-release` entry point | yes | with the next `/update` (editable reinstall) | no | n/a |

**The honest reading of this lane.** Every capability the parent plan asked
for in a release is implemented and guarded, and every guard has a red-state
proof. None of it has been exercised on a real release, because no evaluation
has produced a real `accept` verdict: lane 5's first real cycle froze and
evaluated one experiment and the verdict was `infra_failure` from lane 4's
calibration floor. Two things are not implemented and the code says so:
automated promotion (the gate refuses and names the two events that would
change that, neither of which is this codebase's to produce) and an
arm-scoped unit-2 read (lane 3's meter ships, but windows the daily pool with
no `arm_run_id` to sum over; lane 5's `PlannerArmRunner` reports
`unit2_usd=None` for the same reason). Every drill that has run was
against a temporary repository in a test db, and its record names what it did
not rehearse. No level-2 or level-3 claim is supported, and the report says
so.

## Lane 5: the first complete research cycle

Graded from the records of the first real cycle (2026-09-15, production Redis
db 0, project `valor`, one case; report on
[#3217](https://github.com/tomcounsell/ai/issues/3217#issuecomment-5680368659)).
"Exercised for real" means the component ran on the owning machine inside that
cycle, once. See [Improvement Research Cycle](../../features/improvement-research-cycle.md).

| Component | Implemented | Deployed | Measured | Effect |
|---|---|---|---|---|
| Planner tick (`reflections/improvement_plan.py`, `improvement-planner-tick`): charter pin, keep-alive and unblock, case opening with the novelty check, rank and snapshot, one proposal, verdict backstop | yes | registered on the owning machine; gated off by `enabled=False`; run three times by hand in the first real cycle | **yes, once**: opened 1 case from a seeded row, ranked 1, proposed 1 `investigate`; re-proposed a successor id after a cancelled intent; wrote snapshot 2 after the verdict and proposed nothing (case at `evaluating`, experiment `aborted`) | unknown |
| `ImprovementControllerState` cursor and its migration | yes | yes | **yes**: one row for `valor` carrying both snapshot refs, the evidence watermark, the charter digest, and the digest watermark | n/a: storage |
| Ranking snapshot (`tools/improvement_ranking.py`): five ordinal factors, lexicographic order with `blocked`, content-addressed snapshot, diff, `valor-improve ranking` | yes | yes | **yes, once**: two production snapshots chained by `previous_ref`; the verdict moved `uncertainty` 3 to 1 and left the position, so `entered`/`left`/`moved` were empty on snapshot 2. The `left`-with-evaluation-id demonstration is carried by `test_verdict_moves_ranking` on live arms, not by the real cycle | unknown |
| Case opening from organically collected evidence | yes | yes | **no**: the only case opened from a seeded row (`seed:charter-s3:inference`); the organic inspiration row had no URL and no cluster partner, the coverage row is unclustered, zero lessons and zero corrections in the window | unknown |
| Investigation lifecycle (`tools/improvement_investigations.py`): eight kinds, five states, unindexed `stage`, claim rule, assumption guard | yes | yes | **yes, once**: six investigations opened and resolved by a research session (`web_research`, `resource_acquisition`, `probe`, `memory_retrieval`, two `trace_analysis`), 13 claims with URLs and retrieval dates, one provisional assumption with all four detail keys; one `probe` opened by `apply_verdict` and left open | unknown |
| `resource_acquisition` ending in a vault request and the unblock-on-verified-probe path | yes | yes | **no**: the session resolved `keyless_integrated` (Muse Spark 1.3 and 22 `$0/$0` listings are reachable through the OpenRouter key already in the vault), so no vault request was written and `blocked_by` stayed empty; the block and unblock path is exercised only by `test_blocked_case_unblocks_on_verified_probe` and `test_blocked_by_names_a_known_resource` | unknown |
| `charter_amendment` through `propose-amendment` and `awaiting_authorization` | yes | yes | **no**: the session needed no authority the charter withholds; exercised by unit tests only | unknown |
| `skill_acquisition` and `inspiration_intake` kinds | yes | yes | **`skill_acquisition` yes, once**: `3dbf7e2f7c67457bb768a03050772558` on the first cycle's case, run from the shell through the same CLI after the session (stages 1 to 3, the `improve-preflight` skill integrated, stage 4 a provisional assumption citing #3311, stage 5 "not yet observable"); **`inspiration_intake` no**: the intake pool was empty (the seeded row carried `priority_area` and opened a case directly) | unknown |
| The brief (`tools/improvement_brief.py`, `valor-improve brief`) | yes | yes | **yes, once**: read by the research session as its first action; charter first, position and factors from the real snapshot (the real cycle's dry run exposed and fixed a snapshot-shape read defect in `_ranking_lines`) | unknown |
| Research skill (`.claude/skills/improve-research/SKILL.md`, six steps, six prohibitions) | yes | yes | **yes, once**: 8 turns, nothing written to the tree, every write through `valor-improve`, no prohibition broken | unknown |
| Model revisions with predictions and `research_process_spec` (`revise-model`) | yes | yes | **yes, once**: three production revisions, two superseded, one current, each with a falsifiable prediction and lane 6's digest | unknown |
| Experiment envelope, freeze, evaluate, `apply_verdict` (`tools/improvement_experiment.py`) | yes | yes | **yes, once**: `{"rrf_k": 10}` against `{"limit": 10}`, 30 known-item queries, #2082 cited, both manifest refs, contract hashed before any arm ran; `evaluate` ran lane 4's harness; `apply_verdict` applied `infra_failure` (experiment `aborted`, case unchanged, probe opened) | unknown |
| A `reject`, `accept`, or `inconclusive` verdict on the real corpus | yes | yes | **no**: lane 4's calibration floor (0 of 20 architectural corrections) returns `infra_failure` with zero trials; every verdict's case move and its survival of `projection.apply` is proven by `test_each_verdict_moves_the_case_and_survives_projection` and the integration cycle | unknown |
| Arm-worker pass-throughs (`rrf_k`, `min_rrf_score`) | yes | yes | **no**: no trial ran, so no arm read them on the real corpus; parity and pass-through proven by `TestArmJobPassThroughs` | n/a |
| `lesson` adapter (`collect_lessons`) | yes | yes | **yes, once, empty**: `gh` ran against the mapped repo with no failure and found no prefixed lines in the 14-day merged window | unknown |
| `promise` adapter (`collect_promises`) | yes | registered; gated off by `promise_detector_enabled=False` | **no**: skipped in the real cycle (`promises-skipped: promise_detector_enabled is False`); the judge is exercised with an injected transport only. The session found the fallback model `OPENROUTER_GEMMA4_FREE` (`google/gemma-4-e2b:free`) delisted (HTTP 400), so the judge is dead while `cheap_inference_model` is empty | unknown |
| `scripts/sdlc_reflection.py` retired; `retire_sdlc_reflection` migration; service sweep | yes | on the next `/update` per machine | n/a | removes a writer whose output no reader consumed; lessons now enter as evidence rows |
| Assumption digest (`reflections/improvement_assumption_digest.py`, `improvement-assumption-digest`, 3 days) | yes | registered; gated off by `enabled=False`; run once by hand | **yes, once**: rendered 1 assumption, 0 vault requests, 0 resources acquired, 0 overruns, with the fixed closing line, and advanced `digest_watermark` to the assumption's `resolved_at`. The send itself went to a chat this machine has no cache for and `reflections/utilities.py`'s transport reports True on any attempted send, so delivery is unconfirmed | unknown |
| Qualified-result report (`tools/improvement_report.py`, `valor-improve report`) | yes | yes | **yes, once**: 115 lines from records, three mandatory sections, the seeded-inputs line naming the one case; posted verbatim on #3217 | n/a: it says what is true, and today that is "loop operational" and nothing higher |
| Dashboard: ranking, hypotheses, rejected partials; nine-getter list | yes | yes | no: rendered against test databases; the production ranking partial reads the same two snapshots but nobody has read a number off it | n/a |
| `PlannerArmRunner` (lane 6's production arm), registered on every `valor-improve` invocation | yes | yes | **no**: no `compare run` has used it; gains and `BudgetUse` shape proven by unit tests | unknown |
| `revise-model --backfill-digests` | yes | yes | **no**: every production revision already carried a digest, nothing to backfill | n/a |
| Scheduler adapter dispatch of a research session (lane 3's seam, driven for real here) | yes | yes | **yes, once**, after two defects surfaced and were fixed on this branch (`agent_session_id` lookup; lane slug from a worktree) plus one in the planner (spent action id after a cancelled intent) and one in every improvement reflection entry point (project key `default` instead of `valor`) | unknown |

**The honest reading of this lane.** The loop closed once, end to end, on
production records: evidence became a case, the case became a brief, a
research session investigated and proposed, the proposal became a frozen
contract, the contract was evaluated, the verdict was applied, the next tick
re-ranked, and the report was generated from the records and posted. That
supports charter §6 level 1, "loop operational", and nothing above it. The
verdict was `infra_failure` from lane 4's calibration floor, so the cycle
produced no measurement of retrieval quality, no `reject` that would have
moved the case out of the order, and no `accept` for lane 6 to release; the
current model revision predicts the same outcome for every envelope until
twenty architectural corrections exist. The one case was seeded, no case
opened from organic evidence, the vault-request path and three investigation
kinds ran only under tests, and the promise judge's fallback model is dead.
Unit-2 spend was USD 0.06 at `metering="estimated"`. Four defects that no
test had caught surfaced in the first hour of running the real thing, which
is the argument for having run it.

## Not built, by lane

| Component | Owning lane | Blocked on |
|---|---|---|
| A verdict other than `infra_failure` on the real corpus | 4 (calibration floor) | twenty retained `architectural` corrections in the `valor` partition; the first real cycle's model revision `6b25e970` predicts `infra_failure` for every envelope until then |
| The paired agent-run arm, charter §5 stages 4 and 5, the cheap-inference experiment | [#3311](https://github.com/tomcounsell/ai/issues/3311) | lane 5's envelope is retrieval parameters only |
| Arm-scoped unit-2 read (`BudgetReader.unit2_usd` answers `None`; lane 3's meter windows the daily pool and carries no `arm_run_id`) | none | an arm's inference reservations carrying the arm |
| Automated promotion | none | two events outside the codebase: credential separation from candidate execution, and a Tom-amended charter naming the reversible surfaces. The gate refuses until both |

## Five questions the plan asked, and where they stand

1. **Lease primitive for case transitions** — answered by ownership: #3183's
   lane 6 extracts the epoch-lease Lua idiom into `models/redis_lease.py` and the
   control journal imports it. This plan writes no second lease.
2. **Per-parent fanout cap** — answered as concurrency rather than fanout.
   `max_concurrent_research_sessions=1`; research sessions are top-level
   scheduler-owned dispatches with no parent, so the child session gate is never
   engaged and never lifted.
3. **Dispatch intent persistence** — deferred to lane 3, consuming #3183's
   idempotent create-or-bind seam rather than a second one.
4. **Memory arm isolation** — decided by lane 4: snapshot-and-restore into a
   private `redis-server` per arm, reached only by a child process, with a
   writer kill switch as the second guard. Copy-on-write and shared-instance
   freeze were rejected (spike-3).
5. **`rework_rate`** — decided and executed: deleted, see the retired table
   above.

## Reading rule

A row that is `implemented: yes` and `measured: no` supports exactly one claim:
the code exists. It supports no claim about the system getting better. The
dashboard follows the same rule and never presents an experiment count or a
merged-patch count as improvement.
