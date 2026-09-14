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

State as of lane 4 (#3177). Later lanes update this file rather than starting a
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

## Not built, by lane

| Component | Owning lane | Blocked on |
|---|---|---|
| Observer→planner loop, investigations, first journey-preservation experiment | 5 | nothing (lanes 3 and 4 both shipped; see their sections above) |
| Release records, exposure, rollback, recursive comparison | 6 | lane 5 |

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
