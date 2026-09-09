---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-10
tracking: https://github.com/tomcounsell/ai/issues/3216
last_comment_id: 5603829139
---

# Improvement controller lane 4: frozen evaluation inputs, blinding, and corrected statistics

## Problem

A verdict is worth exactly what its inputs are worth, and today this repo has none of the
protections that make an input worth anything.

Nothing here compares a full candidate agent run against an incumbent. Every judge that
exists scores one artifact with the candidate's identity in plain view: `tools/cross_vendor_judge.py`
sends a PR diff to a non-Claude model and gets back a verdict, and `agent/sdlc_review_consensus.py`
aggregates several of those into one. There is no second arm, no holdout, no blinding, and no
measured agreement against any reference set anywhere in the codebase.

Lane 2 shipped the two records this lane fills. `ImprovementExperiment` carries `contract_digest`,
`state="frozen"`, `frozen_at`, and a `manifest` on the verifying artifact store. `ImprovementEvaluation`
carries `blinded`, `arm_assignment_digest`, `correction`, `effect`, `confidence_interval`, and
`judge_records`. Every one of those fields is null on every row, because nothing writes them.
`ImprovementExperiment.state` has never left `proposed`.

**Current behavior:**

- Two arms would share one Redis and one memory corpus, so a retrieval difference between them
  would be an artifact of the first arm's writes rather than a result. `tools/memory_eval/retrieval_arms.py`
  runs its two arms against the live corpus in one process; that was acceptable for a
  retrieval-quality question and is not acceptable for a candidate-versus-incumbent comparison.
- A judge sees the candidate's identity. `tools/cross_vendor_judge.py:158` stamps a fixed
  `judge_id` onto an envelope built from a PR diff that names branches, authors, and files.
- `VALOR_PROJECT_KEY` is absent from `_harness_env` (`agent/session_executor.py:2116`), so a
  harness subprocess resolves its project partition from ambient environment rather than from
  the arm it was launched for. `tools/memory_search/__init__.py:62` and `reflections/redis_access.py:40`
  both read that variable and both silently fall back to `"valor"`.
- `VerifyingArtifactStore.load` raises `ArtifactIntegrityError` correctly
  (`models/verifying_artifact_store.py:70-118`) and nothing catches it, so there is no code path
  that turns a corrupted artifact into `state="invalidated"`.
- No repeated-selection correction exists anywhere. Running many candidates against one holdout
  and reporting the winner is how a null effect becomes a discovery, and there is nothing in the
  repo that would stop it.
- `EVALUATION_VERDICTS` already separates `infra_failure` from `reject`
  (`models/improvement_evaluation.py:52`), but no producer distinguishes them, so the distinction
  is a vocabulary rather than a behavior.

**Desired outcome:**

`tools/improvement_eval/` runs a paired, blinded, frozen-input comparison and writes an
`ImprovementEvaluation` whose every claim is checkable from the record alone. Two arms read a
byte-identical corpus from separate private Redis processes. The incumbent arm reproduces a
known baseline on that corpus before any candidate number is read, and a parity miss invalidates
the run. Judges see a blinded arm ID and `blinded` records whether that actually held. Holm
correction is applied across the endpoints and named in `correction`. A corrupted artifact
produces `state="invalidated"` and no verdict at all. `infra_failure` and `reject` come from
distinguishable conditions. And a `serves_charter` judge scores the candidate against
`ImprovementCharter.text`, quoting the digest it judged under, calibrated against a frozen
reference set of retained architectural corrections.

## Freshness Check

**Baseline commit:** `191bd42a1339d695c406642d9e7d6ceffd5d67e1` (main, 2026-09-10)
**Build-against commit:** `b05dde885` on `session/sdlc-3255` (lane 2b, PR #3275) — this plan is
written against lane 2b's head, not main, because the charter surface this lane consumes lands there.
**Issue filed at:** 2026-09-07T04:45:09Z
**Disposition:** Minor drift

**File:line references re-verified (on `b05dde885`):**

- `models/improvement_evaluation.py:52` — `EVALUATION_VERDICTS = ("accept", "reject", "inconclusive", "infra_failure")` — holds.
- `models/improvement_evaluation.py:87-92` — `blinded`, `arm_assignment_digest`, `trials`, `effect`, `confidence_interval`, `correction` — all hold, all plain `Field(null=True)`.
- `models/improvement_evaluation.py:93` — `judge_records = ContentField(store=verifying_artifact_store)` — holds.
- `models/verifying_artifact_store.py:70-118` — `load()` re-hashes the archive fallback; the archive-mismatch raise is at `:102`, the live-corrupt-no-archive raise at `:110` — holds, and the two raises are separately reachable, which the mutation test needs.
- `models/improvement_experiment.py:78-88` — `state`, `contract_digest`, `manifest`, `frozen_at`, `charter_version` — hold.
- `tools/memory_eval/metrics.py:68-99` — `bootstrap_ci` seeded, `BootstrapCI.significant = lower > 0.0` — holds.
- `models/improvement_charter.py:137-139` — `digest`, `effective`, `text` — hold. `pinned()` at `:234`, `load_from_file()` at `:148`.
- `models/improvement_case.py:109,117,118` — `priority_area`, `charter_digest`, `ranking_rationale` — hold.
- `models/improvement_investigation.py:94` and `models/improvement_release.py:84` — `charter_digest` — hold.
- `config/settings.py:622,634` — `daily_paid_inference_usd`, `weekly_infrastructure_usd` — hold.
- `tools/improvement_eligibility.py:83` — `is_open_source(project_key, *, ttl_seconds=...)` — holds; the §7 guard.
- `agent/session_executor.py:2116` — `_harness_env` dict literal; `VALOR_PROJECT_KEY` confirmed **absent**.
- `reflections/improvement_collect.py:112` — `classify_correction` returning `"architectural"` on `_ARCHITECTURAL_MARKERS` — holds.

**Cited sibling issues/PRs re-checked:**

- #3177 (parent) — OPEN. The family plan `docs/plans/recursive-self-improvement.md` still names this lane at `:801` and `:408`.
- #3215 (lane 3, declared dependency) — OPEN, unstarted. See Prerequisites for why this lane does not block on it.
- #3255 / PR #3275 (lane 2b) — OPEN, one re-review from merge. This lane's Prerequisites record the ordering.
- #3217 (lane 5), #3218 (lane 6) — OPEN. Neither blocks this lane; both consume it.

**Commits on main since the issue was filed (touching referenced files):**

- `5b994db3f` "Recursive self-improvement controller: lanes 1 and 2" — created every record this lane writes. Not drift; it is the premise.
- `a9822d719` "ETL-grade pipeline hardening" — touched `agent/session_executor.py`. Read the diff: it added `VALOR_CORRELATION_ID` to `_harness_env`. That is the exact shape this lane adds `VALOR_PROJECT_KEY` in, and it moved no line this plan depends on beyond the dict literal's own extent.
- `2a1138d5f` "Synthetic-slug worktrees are invisible to both busy-guard predicates" — irrelevant to this surface.

**Active plans in `docs/plans/` overlapping this area:** none. `recursive-self-improvement.md` is the
family plan this lane serves, and `improvement-controller-lane-2b-charter-v2-delta.md` is the sibling
whose head this plan builds against. Neither claims lane 4's surfaces.

**Notes:**

Two premises in the issue body needed correcting, and both are recorded in the issue's Recon Summary:

1. **`charter_digest` is not on `ImprovementEvaluation`.** The 2026-09-09 refresh comment (id `5603829139`)
   supersedes the issue body here and makes adding it this lane's job. That is a schema change, so it
   carries a migration.
2. **The calibration reference set decays.** `ImprovementEvidence` has `ttl = 86400 * 30`
   (`models/improvement_evidence.py:129`). A judge calibrated against a 30-day rolling window is not
   calibrated. This plan freezes the reference set to the verifying artifact store and cites it by digest.

## Prior Art

Searched closed issues and merged PRs for evaluation harnesses, judge infrastructure, and
bootstrap statistics. Three pieces of real prior art exist in this repo, and all three are
reused rather than reinvented.

- **Issue #2200** (closed 2026-07-22): *Memory telemetry: corpus-level metrics JSON export +
  pre-intervention baseline*. Produced `tools/memory_eval/snapshot.py`, which writes
  `docs/baselines/memory-telemetry-baseline.json` with a record count, an ISO timestamp, and the
  git SHA, and refuses to overwrite without `--force`. **Relevance:** its provenance-plus-refusal
  shape is the model for this lane's corpus export. It is a *telemetry* snapshot, not a corpus
  export — it stores aggregate metrics, not the records — so this lane writes a separate exporter
  rather than extending it.

- **The hybrid-retrieval eval harness** (`docs/plans/completed/hybrid-retrieval-eval.md`,
  `tools/memory_eval/`). Produced `metrics.py` (paired deltas, seeded bootstrap CIs, nDCG),
  `retrieval_arms.py` (two arms with an explicit errored-vs-empty distinction), and
  `provider_gate.py`. **Relevance:** `metrics.py` is imported verbatim by acceptance criterion 7.
  `retrieval_arms.py`'s `ArmQueryResult.errored` flag is the precedent for this lane's
  `infra_failure`-versus-`reject` split at the trial level: an errored trial is excluded from
  scoring rather than counted as a loss, and that is exactly the discipline `infra_failure` encodes
  at the evaluation level. The rest of `memory_eval` is retrieval-specific and is not extended.

- **PR #3202** (merged 2026-09-06): *REVIEW consensus refuses APPROVED on a judge quorum shortfall*,
  and **issue #1626** which produced `tools/cross_vendor_judge.py`. **Relevance:** the envelope
  contract this lane wraps. `cross_vendor_judge.py` already establishes the pattern of a judge that
  (a) reserves a named `judge_id` constant proven disjoint from the others by a test
  (`tests/unit/test_review_multi_judge.py:676`), (b) emits a status-discriminated envelope
  (`{"status": "ok", "judge": {...}}` versus `{"status": "skipped", "reason": ...}`), and (c) routes
  to a non-Claude provider under a settings gate. The `serves_charter` judge takes all three.
  `agent/sdlc_review_consensus.py::compute_consensus` is the aggregation shape the judge envelope
  builds on, per the contract doc.

- **PR #2838** (merged 2026-08-17): *Retire the inert multi-judge review kill switches*.
  **Relevance:** a caution. Judge infrastructure in this repo has already accumulated one round of
  switches that were never exercised and had to be deleted. This lane ships the `serves_charter`
  judge wired into a real path with a real test, or it does not ship it.

No prior attempt at blinding, holdout partitioning, judge calibration, or multiplicity correction
exists in this repo. Those parts are greenfield, which is why the `## Why Previous Fixes Failed`
section is absent: there are no previous fixes to analyze.

## Research

**Queries used:**

- `Holm-Bonferroni step-down procedure implementation monotonicity adjusted p-values`
- `blinded LLM-as-judge calibration reference set agreement Cohen's kappa position bias 2026`
- `preregistered stopping rule fixed-batch vs alpha spending function sequential A/B testing peeking`

**Key findings:**

1. **Holm's adjusted p-values have one canonical form and one canonical bug.** The adjusted value is
   `p_adj(i) = min(1, max over j <= i of (m - j + 1) * p_(j))` on the ascending-sorted p-values, then
   mapped back to the original input order. The cumulative maximum is what enforces monotonicity, and
   it is the standard defect site: CRAN's `RHSDB` package shipped 0.2.0 specifically to fix a
   monotonicity bug in exactly this step. Order of operations matters — the cumulative max must be
   applied before or jointly with the cap at 1.0.
   ([RHSDB](https://search.r-project.org/CRAN/refmans/RHSDB/html/rh.sd.bonferroni.html),
   [Statistics How To](https://www.statisticshowto.com/holm-bonferroni-method/),
   [SAS p-value adjustments](https://www.sfu.ca/sasdoc/sashtml/stat/chap43/sect14.htm))
   **Informs:** `tools/improvement_eval/correction.py` implements those three steps as three
   explicitly-named operations, and the test suite pins the monotonicity property directly
   (`p_adj` non-decreasing in sorted order) rather than only spot-checking a worked example.
   `scipy` and `statsmodels` are **not installed** in this venv (`numpy` 2.4.4 is), and Holm is
   roughly fifteen lines, so this adds no dependency.

2. **Chance-corrected agreement is the only honest calibration metric, and it is not sufficient alone.**
   The largest recent study of LLM judges (21 models, nine providers, 541,000 judgments) reports
   "kappa deflation": raw agreement overstates chance-corrected discrimination by 33–41 percentage
   points in every model evaluated. It also reports a "consistency–bias paradox" — test–retest
   reliability above 0.95 coexisting with position bias above 0.10 in two production-deployed judges —
   and warns explicitly that reporting Cohen's kappa without a paired position-swap check "can produce
   a false sense of having addressed judge reliability". The recommended bar is human–human agreement
   on the same frozen set, not an abstract threshold. It also advises never using the same model family
   as generator and judge.
   ([arXiv 2606.19544](https://arxiv.org/html/2606.19544v1),
   [practitioner protocol](https://futureagi.com/blog/llm-as-judge-best-practices-2026/))
   **Informs:** the `serves_charter` calibration reports Cohen's kappa against the frozen reference
   set **and** a paired position-swap consistency number, both stored on the calibration artifact.
   The plan's success criteria do not name a kappa threshold, because the study's own conclusion is
   that an abstract threshold is the wrong bar; the criterion is that the number is measured, frozen,
   and cited. The cross-family rule reinforces charter §7's routing: for an open-source project the
   judge may run on a non-Claude provider, which is a correctness argument as well as a cost one.
   The study also notes that *blinding* as a distinct intervention is not covered in that literature,
   which is precisely why `ImprovementEvaluation.blinded` records whether it held rather than
   asserting that it does.

3. **A stopping rule must be named before measurement, and the two honest families are different
   commitments.** A fixed-batch design fixes the maximum number of looks and the observations between
   them in advance. An alpha-spending function distributes type-I error across looks and, uniquely
   among the sequential families, tolerates deviation from the pre-specified look schedule without
   inflating type-I error (Lan and DeMets, 1983: what matters is *when* you peek, not how much).
   Post-stopping estimation is biased in both: after an interim look, the p-value is too small, the
   point estimate too large, and the confidence interval too narrow.
   ([Analytics-Toolkit on error spending](https://blog.analytics-toolkit.com/2020/error-spending-in-sequential-testing-explained/),
   [Spotify Engineering](https://engineering.atspotify.com/2023/03/choosing-sequential-testing-framework-comparisons-and-discussions),
   [Stata GSD introduction](https://www.stata.com/manuals/adaptgsdintro.pdf))
   **Informs:** this lane ships **fixed-batch only** as the named stopping rule, recorded in the
   frozen contract and echoed into `ImprovementEvaluation.correction` alongside the Holm label.
   Alpha-spending is a No-Go for this lane: it is the strictly harder commitment, it needs a
   pre-specified maximum sample size the loop does not yet have evidence to choose, and shipping it
   half-implemented would produce exactly the peeking it exists to prevent. The post-stopping bias
   note is why the harness refuses to compute a verdict from a partial batch at all rather than
   computing one and flagging it.

## Spike Results

placeholder

## Data Flow

placeholder

## Architectural Impact

placeholder

## Appetite

placeholder

## Prerequisites

placeholder

## Solution

placeholder

## Failure Path Test Strategy

placeholder

## Test Impact

placeholder

## Rabbit Holes

placeholder

## Risks

placeholder

## Race Conditions

placeholder

## No-Gos (Out of Scope)

placeholder

## Update System

placeholder

## Agent Integration

placeholder

## Documentation

placeholder

## Success Criteria

placeholder

## Team Orchestration

placeholder

## Step by Step Tasks

placeholder

## Verification

placeholder

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

placeholder
