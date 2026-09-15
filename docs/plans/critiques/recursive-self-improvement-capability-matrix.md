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

State as of lane 6 (#3177). Later lanes update this file rather than starting a
new one.

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
| Correction detector (`collect_corrections`) | yes | registered on the next `/update` of the owning machine; writes only once `IMPROVEMENT__ENABLED=true` | **no** | unknown |
| Memory-inspiration adapter (`collect_inspirations`) | yes | same | **no** | unknown |
| Expectation coverage adapter | yes | same | **no** | unknown |
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
| `ImprovementCharter.load_from_file` seed | yes | no caller yet (lane 3's tick is the first) | no | unknown |
| `ImprovementCharter.pinned` | yes | read by the goals partial | no | n/a |
| `PRIORITY_AREAS` + `priority_area` / `ranking_rationale` | yes | no writer yet (lane 3 opens the first case) | no | n/a |
| `charter_digest` on case, investigation, release | yes | no writer yet | no | n/a |
| `objective` deleted | yes | yes, no rows existed | n/a | n/a |
| Three budget units in `ImprovementSettings` | yes | yes, defaults only | **no** | n/a (declared limits, nothing meters them) |
| `is_open_source` charter §7 guard | yes | no caller yet (lane 3 routes the first session) | no | unknown |
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
| Control journal (`journal.py::transition`, one Lua `EVAL` per Decision 3) | yes | no caller in production yet (no case has been admitted) | no | unknown |
| Lease protocol + interim `CaseLease` | yes | consumed by the tick, the reconcile pass, and the CLI's own writes | no | n/a (fences a controller that has never yet raced) |
| Dispatch intents, six-state machine, lane-slot reservation | yes | no intent admitted yet | no | unknown |
| `admitted` session status (lifecycle, ownership, dashboard) | yes | yes, the constant is live | no | n/a (no row has used it yet) |
| Scheduler adapter (`improvement-controller-tick`) | yes | registered; gated off by `ImprovementSettings.enabled=False` default | **no** | unknown |
| Reconcile reflection (`improvement-intent-reconcile`, 300s) | yes | registered, runs regardless of the enabled switch | no | unknown (nothing to reconcile yet) |
| Unit-2 paid-inference meter (`tools/paid_inference_meter.py`) | yes | no reservation made in production yet | no | unknown |
| Cross-vendor judge's `sdlc_review` receipt | yes | yes, fires on every judge run | **yes** (receipts accumulate on every SDLC review) | n/a (record-only, never gates) |
| Vault writer (`tools/vault_write.py`) | yes | no write attempted in production yet | no | unknown |
| `valor-improve` CLI, twelve subcommands | yes | binary materializes on the next `/update`'s `uv sync` | no | unknown |
| `.claude/skills/improve-research/SKILL.md` | yes | no research session has run under it yet (lane 5) | no | unknown |
| Dashboard control panel (`get_control_status`) | yes | yes, renders on the root dashboard | no | n/a (empty-namespace state until a case is admitted) |
| Mutation review of every fence (Task 12) | yes, 12/12 mutations caught | one-time build-time exercise, not a running check | n/a | n/a |

**The honest reading of this lane.** Every primitive in the plan's Data Flow is
implemented and tested — including the four fault-injection scenarios the
issue's acceptance criteria name (stale-generation rejection split by writer
kind, a crash between admission and session creation, an unreleased lane slot
on restart, and journal unavailability with `doctor` as the break-glass read)
— but **nothing in this lane has run against a real research session**, because
no research session exists yet: that is lane 5's plan. The scheduler adapter
has admitted zero cases in production and the meter has settled zero dollars.
This lane is infrastructure a later lane will exercise, not yet a measured
effect. `#3220` (the production session-execution lease) is still open; this
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
| `tools/improvement_eval/runner.py`: gate ordering, three exit handlers, single `ImprovementEvaluation` writer | yes | no — nothing calls `evaluate()` in a request path or on a schedule until lane 3's control loop | no | n/a |
| Frozen corpus export, canonical digest, ORM restore (`corpus.py`) | yes | no | no | n/a |
| Per-arm private Redis with a subprocess-only client (`arena.py`, `arm_worker.py`) | yes | no | **measurable**: two arms re-export and hash their own corpus at run time, and the run refuses on a mismatch | unknown |
| Writer kill switch plus digest re-check (`writer_guard.py`) | yes | no | **measurable**: an escaped write surfaces as `infra_failure`, proven by mutation | unknown |
| Baseline parity gate before the candidate arm (`retrieval.py`) | yes | no | **measurable**: a parity miss is an `infra_failure` and the candidate is never invoked, proven by mutation | unknown |
| Blinding: seeded assignment, blinded ids, identity scan (`blinding.py`) | yes | no | **measurable**: `blinded` is a typed boolean written from the scan, never assumed | unknown |
| `serves-charter` judge with charter §7 routing (`judges/serves_charter.py`) | yes | no | no — no real provider call has been made; every test injects the transport | unknown |
| Calibration against a frozen reference set (`calibration.py`) | yes | no | **no, and blocked**: the architectural `ImprovementEvidence` bucket held 0 rows on the build machine; the floor is 20, so the judge returns `infra_failure` until evidence accrues | unknown |
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
| Research process digest (`tools/improvement_recursion/process.py`): canonical bytes, one hashing routine shared with lane 5 | yes | no writer yet: lane 5 sets `research_process_digest` on revisions by importing this function; the cross-lane byte test skips until `tools/improvement_ranking.py` lands | no | n/a |
| Freshness by record lookup (`freshness.py`) | yes | no | no | n/a |
| Budget accounting (`budget.py`): four units, `LedgerBudgetReader` unit 3 by `arm:<run>:` prefix, `None` on zero rows, `budgets_comparable` | yes | no | **partly measurable**: unit 3 reads real ledger rows through the same `admit()`/`settle()` path a production arm takes. **Unit 2 (paid inference) has no arm-scoped read**; lane 3's meter windows the daily pool with no `arm_run_id`, so `unit2_usd` answers `None`, so every comparison today refuses a claim with `BUDGET_UNKNOWN:unit2`; unit 1 is the subscription lane slot, accounted as `subscription_turns` | n/a |
| Comparison (`compare.py`): frozen contract through lane 4's `freeze_protocol`, two arm seams (`--arm-runner` lazy import, in-process registry), seeded arm order, paired deltas clustered by `priority_area`, Holm, one evaluation in lane 4's string shapes, new-then-supersede revision write, `REVISION_CONFLICT` | yes | no | no: every run uses `ReplayArmRunner` fixtures. **The production `ArmRunner` is lane 5's** planner tick and does not exist; without it `compare run` is `ARM_RUNNER_ABSENT` or replays fixtures | unknown |
| Claim report (`report.py`): three ladder levels, each degrading independently, interval, correction, falsifier, `why_not`; no counts | yes | yes: `valor-improve-release report` and the releases partial | **yes, trivially**: on the real project it reports all three levels unsupported (no complete cycle, no accepted release, no comparison) | n/a: the report says what is true, and today that is "unsupported" three times |
| Dashboard releases partial and `get_release_lineage` (sixth getter beside lane 3's `get_control_status`, pinned list) | yes | yes | no | n/a |
| `valor-improve-release` entry point | yes | with the next `/update` (editable reinstall) | no | n/a |

**The honest reading of this lane.** Every capability the parent plan asked
for in a release is implemented and guarded, and every guard has a red-state
proof. None of it has been exercised on a real release, because no evaluation
has produced a real `accept` verdict: the control loop that would freeze one
shipped with lane 3, and the research process that would drive it is lane 5's. Three
things are not implemented and the code says so: automated promotion (the
gate refuses and names the two events that would change that, neither of
which is this codebase's to produce), an arm-scoped unit-2 read (lane 3's
meter ships, but windows the daily pool with no `arm_run_id` to sum over),
and the production arm runner (lane 5). Every drill that has run was
against a temporary repository in a test db, and its record names what it did
not rehearse. No level-2 or level-3 claim is supported, and the report says
so.

## Not built, by lane

| Component | Owning lane | Blocked on |
|---|---|---|
| Observer→planner loop, investigations, first journey-preservation experiment | 5 | nothing (lanes 3, 4, and 6 all shipped; see their sections above) |
| Production `ArmRunner` (the planner tick under a pinned process spec) and the writer of planner-tick `ImprovementModelRevision` rows | 5 | lane 5 |
| Arm-scoped unit-2 read (`BudgetReader.unit2_usd` answers `None`; lane 3's meter windows the daily pool and carries no `arm_run_id`) | 5 | an arm's inference reservations carrying the arm |
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
