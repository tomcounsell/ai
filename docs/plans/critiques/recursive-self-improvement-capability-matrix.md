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

State as of lane 2 (#3177). Later lanes update this file rather than starting a
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
| `VerifyingArtifactStore` | yes | no writer yet — lanes 4 and 5 write the first artifact | no | unknown |
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

## Lane 2b — charter v2 delta

| Component | Implemented | Deployed | Measured | Effect |
|---|---|---|---|---|
| `ImprovementCharter.digest` / `effective` / `text` | yes | yes (schema is live once merged) | no | n/a — storage, not behavior |
| `ImprovementCharter.load_from_file` seed | yes | no caller yet — lane 3's tick is the first | no | unknown |
| `ImprovementCharter.pinned` | yes | read by the goals partial | no | n/a |
| `PRIORITY_AREAS` + `priority_area` / `ranking_rationale` | yes | no writer yet — lane 3 opens the first case | no | n/a |
| `charter_digest` on case, investigation, release | yes | no writer yet | no | n/a |
| `objective` deleted | yes | yes, no rows existed | n/a | n/a |
| Three budget units in `ImprovementSettings` | yes | yes, defaults only | **no** | n/a — declared limits, nothing meters them |
| `is_open_source` charter §7 guard | yes | no caller yet — lane 3 routes the first session | no | unknown |
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

## Not built, by lane

| Component | Owning lane | Blocked on |
|---|---|---|
| Control journal, Lua transition, dispatch intents, `admitted` status | 3 | #3183's create-or-bind seam and dead-letter record; #3183's lane 6 for the fencing lease |
| `valor-improve` CLI, break-glass `pause`/`resume`/`doctor` | 3 | lane 3 |
| Frozen corpora, per-arm isolation, judge envelope, `tools/improvement_eval/` | 4 | lane 3 |
| Observer→planner loop, investigations, first journey-preservation experiment | 5 | lane 4 |
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
4. **Memory arm isolation** — deferred to lane 4. Partition-by-key exists today;
   snapshot, freeze, and copy-on-write do not.
5. **`rework_rate`** — decided and executed: deleted, see the retired table
   above.

## Reading rule

A row that is `implemented: yes` and `measured: no` supports exactly one claim:
the code exists. It supports no claim about the system getting better. The
dashboard follows the same rule and never presents an experiment count or a
merged-patch count as improvement.
