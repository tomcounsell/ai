# Improvement Evaluation

How a candidate change is measured against the incumbent, and what makes the
resulting verdict worth anything.

Tracking issue: [#3177](https://github.com/tomcounsell/ai/issues/3177).
Companion to [Improvement Controller](improvement-controller.md).

## What exists today

The two records — `ImprovementExperiment` and `ImprovementEvaluation` — and the
verifying artifact store their content fields sit on. The harness itself
(`tools/improvement_eval/`), the frozen corpora, per-arm isolation, and the
judge envelope arrive with lane 4. This document is the contract lane 4 builds
to, written down before it is built so the endpoints cannot be chosen after the
results are visible.

## Why a new harness

Nothing in this repo compares a full candidate agent run against an incumbent.
Every existing judge scores one artifact with the candidate's identity visible.
There is no holdout split, no blinding, and no judge calibration anywhere.

`tools/memory_eval/metrics.py` is reused for bootstrap confidence intervals: it
has real paired per-query deltas and seeded bootstrap 95% CIs, and it is
domain-neutral and unit-tested. `tools/improvement_eval/` **imports** it rather
than extending `memory_eval`, whose arms and gates are retrieval-specific.

## The frozen contract

An experiment's contract is preregistered and hashed before any arm runs.
`ImprovementExperiment.contract_digest` holds the `"sha256:<hex>"` normalized
digest, using the same shape as `tools/sdlc_verdict.py`.

| Field | What it fixes |
|---|---|
| `hypothesis` | What the change is expected to do |
| `mechanism` | Why it would do that |
| `falsifier` | What result would show the hypothesis is wrong |
| `candidate_surfaces` | Which surfaces the candidate may modify |
| Endpoints | What is measured, named before measuring |
| Stopping rule | When measurement ends, named before it starts |
| Holdout partition | Which split, and its rotation epoch |

**Freezing precedes measurement.** `state="frozen"` and `frozen_at` are what
make that checkable. An experiment whose contract digest changes after freezing
is a new experiment, not a revised one — that is the whole point of the digest.

## The manifest

`ImprovementExperiment.manifest` is a `ContentField` on the verifying artifact
store: the exact candidate under test, hashed at write time and re-hashed on
every load. It names the surfaces changed, the base revision, and the
environment the arm ran in.

**A corrupted manifest invalidates the evaluation rather than scoring it.** The
store raises `ArtifactIntegrityError` and the evaluation records
`state="invalidated"`. See [Improvement Controller § Artifacts](improvement-controller.md#artifacts)
for why the popoto default is unsafe here.

## Arms and isolation

| Requirement | Why |
|---|---|
| Randomized run ordering | Order effects are real; `arm_assignment_digest` records the assignment so it can be checked |
| Per-arm writable state | Two arms sharing writable state measure their interaction, not themselves |
| Byte-identical corpus reads across arms | A retrieval difference between arms is a confound, not a result |
| Holdout partition with rotation | A holdout read enough times stops being held out |
| Candidate cannot read holdout answers | Otherwise the measurement is of memorization |

Memory arm isolation is lane 4's work. Partition-by-key exists today; snapshot,
freeze, and copy-on-write do not.

## The judge envelope

Built on the record shape already used by `agent/sdlc_review_consensus.py`
(`judge_id`, `verdict`, `blockers`, confidence), wrapped with:

- experiment ID and contract digest
- evaluator version
- trial ID
- a reference to the raw response, on the verifying store
- **the blinded arm ID** — not the candidate's identity

`ImprovementEvaluation.blinded` records whether judges saw candidate identity.
An evaluation that cannot state this is not a paired comparison and the
dashboard shows it as one that cannot be. Recording blinding rather than
assuming it is the difference between a claim and an assertion.

Judge calibration is lane 4's: a judge whose agreement with a reference set is
unmeasured is an opinion, not an instrument.

## Statistics

- Paired per-trial deltas, from `tools/memory_eval/metrics.py`.
- Seeded bootstrap 95% confidence intervals, same source.
- Clustered resampling by project, so one noisy project cannot carry a result.
- **Repeated-selection correction.** Running many candidates against one holdout
  and reporting the winner is how a null effect becomes a discovery. Holm
  correction, applied outside `metrics.py`, and the correction actually used is
  recorded in `ImprovementEvaluation.correction` — a correction nobody can name
  was not applied.
- Named stopping rules, fixed in the frozen contract.

`confidence_interval` and `effect` are stored per endpoint, as JSON. A point
estimate is never sufficient on its own: the CI lower bound has to clear zero.

## Verdicts

`ImprovementEvaluation.verdict` is one of four, and the fourth is the important
one:

| Verdict | Means |
|---|---|
| `accept` | The candidate cleared its endpoints and the correction |
| `reject` | It did not |
| `inconclusive` | The measurement ran and could not distinguish the arms |
| `infra_failure` | The harness broke. **This says nothing about the candidate** |

Collapsing `infra_failure` into `reject` would let flaky infrastructure read as
evidence against a change, and would quietly bias the whole loop toward the
status quo. They are separate index values on purpose.

## Releases

An `accept` verdict produces an `ImprovementRelease` in `state="proposed"`, which
is a proposal for a human. It carries the surfaces, the exposure, the
`rollback_plan`, and the `observation_window_ends_at` — all written **before**
anything is exposed, so "we can undo this" is a written commitment rather than a
reassurance offered after something goes wrong.

**Automated promotion is disabled and no record here enables it.** It stays
disabled until evaluator secrets and production credentials are separated from
candidate execution, and until a human-amended charter names the reversible
surfaces. Both are events outside this work. A worktree is not a security
boundary.

## What the dashboard will never show

Experiment count and merged-patch count. They are activity. Presenting either as
improvement is the specific dishonesty the plan names, and
`ui/data/improvement.py` has no function that returns them — pinned by a test.

## See also

- [Improvement Controller](improvement-controller.md) — records, evidence collection, control namespace, break-glass
- [`docs/plans/recursive-self-improvement.md`](../plans/recursive-self-improvement.md) — the full design
- [Capability matrix](../plans/critiques/recursive-self-improvement-capability-matrix.md) — what is implemented versus measured
