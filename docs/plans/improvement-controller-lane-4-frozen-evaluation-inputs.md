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

Five code-read spikes were run against `session/sdlc-3255` at `b05dde885` during planning. Each
resolved an assumption that would otherwise have shaped the design on a guess. No prototype spike
was needed: every question was answerable by reading the checkout and the installed venv.

### spike-1: Is a statistics dependency required for Holm?
- **Assumption**: "Holm correction needs `scipy` or `statsmodels`."
- **Method**: code-read (venv import probe)
- **Finding**: **False.** `scipy` and `statsmodels` are both absent from `.venv`; `numpy` 2.4.4 is
  present. Holm is a sort, a rank-weighted multiply, a cumulative max, and a clamp — fifteen lines.
- **Confidence**: high
- **Impact on plan**: `tools/improvement_eval/correction.py` is pure Python plus stdlib. No
  dependency is added, no `pyproject.toml` change, no `/update` propagation.

### spike-2: Does the private-Redis arm collide with the pytest db-claim pool?
- **Assumption**: "A per-arm private Redis can reuse the test-suite's Redis."
- **Method**: code-read (`tests/db_claim.py`, `tests/unit/test_test_redis_server_resolution.py`)
- **Finding**: **It must not, and there is a documented reason.** `db_claim` is the single source of
  "which server and which db does this pytest process own", `redis_test_host()`/`redis_test_port()`
  resolve it, and `conftest.py` points popoto's canonical client at exactly that. Issue #2799 records
  the live failure when an agent started a private `redis-server` for isolation and got the opposite:
  the suite still hit 6379 and flushed production db1. A private arm server must therefore be reached
  through an **explicitly constructed client on a unix socket**, never through popoto's canonical
  pool and never by re-pointing `REDIS_URL`. `redis-server` v8.10.1 is on this machine at
  `/opt/homebrew/bin/redis-server`.
- **Confidence**: high
- **Impact on plan**: `tools/improvement_eval/arena.py` spawns `redis-server --unixsocket <tmp>/arm.sock
  --port 0 --save '' --appendonly no --dir <tmp>` per arm and hands back a client bound to that socket.
  Port 0 means the arm has no TCP listener at all, so it cannot be reached by accident and cannot
  collide with a concurrent agent's port. `db_claim` is untouched, and a test asserting that is in
  the Verification table.

### spike-3: Which of snapshot, freeze, or copy-on-write does per-arm isolation need?
- **Assumption**: "One of the three is obviously right."
- **Method**: code-read (`tools/memory_eval/retrieval_arms.py`, `models/memory.py`, Redis capabilities)
- **Finding**: **Snapshot, with freeze as an independent second guard; copy-on-write is unavailable.**
  Redis has no logical-database copy-on-write to build on, so COW would mean writing an interception
  layer over every command — a large surface for a property a fresh process gives for free. Freeze
  alone (a writer kill switch on one shared instance) leaves both arms in one keyspace, where a single
  escaped write corrupts the comparison silently. Snapshot-and-restore into a fresh private process
  gives byte-identical reads by construction and makes the freeze cheap to add on top.
- **Confidence**: high
- **Impact on plan**: the design is export-once, restore-per-arm, plus a writer kill switch that
  refuses writes at the client wrapper **and** re-checks the corpus digest at arm teardown. Two
  independent mechanisms, because a guard that can only fail one way is a guard that is trusted
  without evidence.

### spike-4: Is the calibration reference set stable?
- **Assumption**: "Retained architectural corrections are a stable gold set."
- **Method**: code-read (`models/improvement_evidence.py`, `reflections/improvement_collect.py`)
- **Finding**: **False, twice over.** `ImprovementEvidence` carries `ttl = 86400 * 30`, so the
  underlying rows expire on a 30-day rolling window. And `classify_correction` is a regex whose
  default is `unknown` by explicit design ("a confident wrong label is worse than an honest absent
  one"), so the `architectural` bucket is precision-oriented and small. A judge calibrated against a
  set that rotates monthly and whose size is unmeasured is not calibrated.
- **Confidence**: high
- **Impact on plan**: `tools/improvement_eval/calibration.py` freezes the reference set to the
  verifying artifact store at calibration time, content-addressed, and the calibration record cites
  it by digest. Recalibration writes a new artifact rather than mutating one. The build reports the
  observed set size, and a set below a floor produces `infra_failure` on the judge rather than a
  quietly uncalibrated verdict.

### spike-5: Is there an existing judge envelope to build on, or is this greenfield?
- **Assumption**: "The judge envelope is new."
- **Method**: code-read (`tools/cross_vendor_judge.py`, `agent/sdlc_review_consensus.py`, `tools/sdlc_verdict.py`)
- **Finding**: **Mostly existing.** `cross_vendor_judge.py` already establishes a reserved
  `judge_id` constant (`CROSS_VENDOR_JUDGE_ID = "cross-vendor"`, `:28`), a disjointness test
  (`tests/unit/test_review_multi_judge.py:679`), a status-discriminated envelope
  (`{"status": "ok"|"skipped"}`), coercion of every field with a typed fallback (`:140-165`), and
  non-Claude provider routing under a settings gate. `_REQUIRED_KEYS = ("judge_id", "verdict",
  "blockers")` is asserted in two places (`agent/sdlc_review_consensus.py:33`,
  `tools/sdlc_verdict.py:332`).
- **Confidence**: high
- **Impact on plan**: the `serves_charter` judge copies that shape rather than inventing one, and the
  evaluation envelope is that dict *wrapped* — experiment id, contract digest, charter digest,
  evaluator version, trial id, raw-response reference, and the blinded arm id go around it, so the
  inner dict stays consumable by `compute_consensus` unchanged.

## Data Flow

One evaluation run, end to end. Every hand-off names the artifact that carries it, because a
hand-off with no artifact is a claim nobody can check later.

1. **Entry point**: `tools/improvement_eval/runner.py::evaluate(experiment_id, project_key)`.
   Reads the `ImprovementExperiment`. **Gate 0 — the contract is frozen and unaltered.** The runner
   recomputes the contract digest and compares it to `ImprovementExperiment.contract_digest`; a
   mismatch, or `state != "frozen"`, ends the run as `infra_failure`. This is charter §6's
   anti-gaming rule made mechanical: an endpoint chosen after the result is visible cannot enter.

2. **Charter pin**. `ImprovementCharter.pinned(project_key)` supplies `digest`, `effective`, and
   `text`. The digest is written onto the evaluation (the new `ImprovementEvaluation.charter_digest`)
   and into every judge envelope. `text` is the only charter content the `serves_charter` judge ever
   sees — the judge process is given the charter without being given the repository, which the
   refresh comment correctly calls a convenience rather than a security boundary.

3. **Corpus export** (`corpus.py`). One canonical, sorted, newline-delimited export of the project's
   memory corpus is produced once per run, hashed, and written to the verifying artifact store. The
   digest is the corpus identity for the whole run. `VALOR_PROJECT_KEY` is resolved here and threaded
   into `_harness_env` so every arm subprocess partitions on the arm's project rather than on ambient
   environment.

4. **Arm assignment** (`blinding.py`). Arms are assigned randomized run ordering from a seed derived
   from the experiment id. `arm_assignment_digest` is written before either arm runs. Each arm gets
   a blinded id (`arm-a` / `arm-b`) with the incumbent/candidate mapping held only by the runner.

5. **Arena spawn** (`arena.py`). One `redis-server` per arm, unix socket, no TCP, no persistence,
   its own `--dir`. The export from step 3 is loaded into each. **The corpus digest is re-read from
   each arm and compared**; unequal digests are `infra_failure` before anything is measured. This is
   acceptance criterion 1, and it is asserted at run time rather than only in a test.

6. **Writer kill switch** (`writer_guard.py`). Each arm's client is wrapped so corpus-key writes
   raise. The wrapper is the first guard; the second is a digest re-check at arm teardown, which
   catches a write that reached the server by some path the wrapper did not cover.

7. **Gate 1 — baseline parity** (`retrieval.py`). The **incumbent** arm runs the frozen baseline
   query set against the frozen corpus and its result is compared to the recorded baseline. A miss
   ends the run as `infra_failure` and **the candidate arm is never invoked**. The ordering is the
   point: an incumbent that cannot reproduce itself makes every candidate number meaningless, and
   reading the candidate first would let the operator learn the answer before learning the run was
   invalid.

8. **Paired trials**. For each trial in the fixed batch, both arms run the same input in the
   randomized order from step 4. A trial that errors in the harness is excluded and counted toward
   the infra-failure cap; a trial where the *candidate* fails is a candidate failure and scores as
   one. That distinction is `retrieval_arms.py`'s errored-vs-empty discipline, raised to the arm level.

9. **Judges** (`envelope.py`, `judges/serves_charter.py`). Each trial's outputs go to the judge
   roster carrying the blinded arm id and never the candidate's identity. `blinding.py::scan_for_identity`
   inspects the serialized envelope before it is sent; a hit sets `blinded=False` on the evaluation
   and is recorded rather than suppressed. Raw judge responses are written to the verifying artifact
   store and referenced from the envelope; the envelopes themselves land in
   `ImprovementEvaluation.judge_records`.

10. **Statistics** (`statistics.py`, `correction.py`). Paired per-trial deltas per endpoint feed
    `tools.memory_eval.metrics.bootstrap_ci` (imported, unmodified) with clustered resampling by
    project. Per-endpoint p-values go through `holm_adjust`. The stopping rule is checked: a batch
    short of its declared size yields `inconclusive`, never a partial-batch verdict.

11. **Verdict and write**. `accept` when every required endpoint clears its margin and its
    Holm-adjusted threshold with a CI lower bound above zero; `reject` when the measurement ran and
    it did not; `inconclusive` when the measurement ran and could not distinguish the arms;
    `infra_failure` when the harness broke. `correction` is written as the named string
    (`"holm; fixed-batch(n=..., endpoints=...)"`) — a correction nobody can name was not applied.

12. **Output**: one `ImprovementEvaluation` row. On any `ArtifactIntegrityError` raised anywhere in
    steps 2, 9, or 11, the run instead writes `state="invalidated"` and **no verdict**: `verdict`
    stays at its schema default and `has_verdict()` returns False. An unverifiable artifact is not
    weak evidence, it is no evidence.

## Architectural Impact

- **New dependencies**: none. Holm is pure Python (spike-1); `redis-server` v8.10.1 is already
  installed and already a hard requirement of this repo. No `pyproject.toml` change, no
  `/update` propagation, no new secret.
- **Interface changes**: one additive schema change — `ImprovementEvaluation.charter_digest`, a
  plain `Field(null=True)`, with a migration entry mirroring `_migrate_confirm_improvement_v2_fields`
  (`scripts/update/migrations.py:1429`). One additive env change — `VALOR_PROJECT_KEY` in
  `_harness_env` (`agent/session_executor.py:2116`), the same shape `VALOR_CORRELATION_ID` took in
  `a9822d719`. `tools/memory_eval/metrics.py` is imported and **not** modified; that is acceptance
  criterion 7 and a Verification row.
- **Coupling**: `tools/improvement_eval/` depends on `models/` (the improvement records and the
  verifying store), on `tools/memory_eval/metrics.py` (one-directional, import only), and on
  `tools/improvement_eligibility.py` for the §7 provider decision. Nothing in `agent/`, `bridge/`,
  or `worker/` depends on it. The direction is deliberate: the harness is a leaf, so it can be
  deleted or replaced as a unit, which charter §6 explicitly anticipates ("research selection and
  evaluation methods are themselves open to improvement").
- **Data ownership**: the harness owns `ImprovementEvaluation` rows and the frozen artifacts they
  cite (corpus export, calibration reference set, raw judge responses), all on the verifying artifact
  store under `POPOTO_IMPROVEMENT_CONTENT_PATH`. It writes `ImprovementExperiment.state`
  (`frozen` → `running` → `complete`/`aborted`) and nothing else on that record. It never writes
  `ImprovementCharter` — the controller cannot amend its own authority — and it never writes
  `ImprovementRelease`, which is lane 6's.
- **Reversibility**: high. Deleting `tools/improvement_eval/` and the migration leaves a null column
  and an unused env var. The `serves_charter` judge is one input to a consensus envelope and never a
  gate on its own, so removing it degrades the roster rather than breaking it. The one irreversible
  thing is the schema addition, and an additive nullable field on an immortal record is the cheapest
  irreversible change available.

## Appetite

**Size:** Large

**Team:** Solo dev (the lane's Eng session), plus a validator pass per component and one
documentarian pass.

**Interactions:**
- PM check-ins: 2-3 (the isolation mechanism choice, the stopping-rule scope, and the calibration
  floor are each a place where a wrong call is expensive to unwind)
- Review rounds: 2+

Large is the honest size. This is eight new modules, a schema change with a migration, a change to
the harness environment every session inherits, and a test suite whose whole job is to prove that
guards bite. The appetite is set by alignment cost, not typing: three of the design decisions
(snapshot-versus-freeze, fixed-batch-versus-alpha-spending, and where the calibration floor sits)
are commitments the rest of the improvement loop inherits, and getting one wrong is a rewrite of a
downstream lane rather than a patch here.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `redis-server` binary | `redis-server --version` | Per-arm private Redis processes (spike-2) |
| Python pin matches the repo | `python -c "import pathlib,sys; want=pathlib.Path('.python-version').read_text().strip(); got='.'.join(map(str,sys.version_info[:3])); sys.exit(0 if got.startswith(want) else 1)"` | Worktree venv is on the committed pin |
| `numpy` importable | `python -c "import numpy"` | Clustered resampling; already a declared dependency |
| Verifying artifact store writable | `python -c "from models.verifying_artifact_store import verifying_artifact_store as s; import os; os.makedirs(s.base_path, exist_ok=True)"` | Frozen corpus, calibration set, and raw judge responses land here |
| Lane 2b charter surface present | `python -c "from models.improvement_charter import ImprovementCharter as C; assert hasattr(C,'digest') and hasattr(C,'text') and hasattr(C,'pinned')"` | `serves_charter` judges against `ImprovementCharter.text` (#3255) |
| `metrics.py` import path | `python -c "from tools.memory_eval.metrics import bootstrap_ci"` | Acceptance criterion 7 |

`scipy` and `statsmodels` are deliberately absent and no check asks for them (spike-1).

## Solution

### Key Elements

- **`tools/improvement_eval/corpus.py`** — exports the project's memory corpus once per run into a
  canonical, sorted, newline-delimited form, hashes it, and writes it to the verifying artifact
  store. The digest is the corpus identity for the run. Restores that export into an arm's store.
- **`tools/improvement_eval/arena.py`** — spawns one private `redis-server` per arm on a unix socket
  with no TCP listener and no persistence, hands back an explicitly-constructed client, and tears the
  process down. Never touches popoto's canonical pool, `REDIS_URL`, or `tests/db_claim.py`.
- **`tools/improvement_eval/writer_guard.py`** — the writer kill switch. A client wrapper that
  refuses corpus writes, plus an independent corpus-digest re-check at arm teardown so a write that
  slipped past the wrapper still surfaces as `infra_failure` rather than as a result.
- **`tools/improvement_eval/retrieval.py`** — the isolated retrieval adapter, and the **baseline
  parity gate**: the incumbent arm reproduces its recorded baseline on the frozen corpus before any
  candidate result is read.
- **`tools/improvement_eval/blinding.py`** — randomized arm assignment with a recorded
  `arm_assignment_digest`, blinded arm ids, and `scan_for_identity`, the leak detector that decides
  what `ImprovementEvaluation.blinded` actually says.
- **`tools/improvement_eval/envelope.py`** — the judge envelope: the existing
  `judge_id`/`verdict`/`blockers`/`confidence` dict, wrapped with experiment id, contract digest,
  charter digest, evaluator version, trial id, a reference to the raw response on the verifying
  store, and the blinded arm id.
- **`tools/improvement_eval/judges/serves_charter.py`** — the `serves_charter` judge. Scores a
  candidate's output against `ImprovementCharter.text`, quotes the digest it judged under, routes to
  a provider chosen by charter §7, and is one input to the consensus envelope, never a gate.
- **`tools/improvement_eval/calibration.py`** — freezes a reference set of retained architectural
  corrections to the verifying store and measures the judge against it with Cohen's kappa and a
  paired position-swap consistency check.
- **`tools/improvement_eval/correction.py`** — Holm step-down correction and the named fixed-batch
  stopping rule, producing the exact string written to `ImprovementEvaluation.correction`.
- **`tools/improvement_eval/statistics.py`** — per-endpoint thresholds and clustered resampling by
  project, over `bootstrap_ci` imported from `tools/memory_eval/metrics.py`.
- **`tools/improvement_eval/runner.py`** — the orchestration and the single writer of
  `ImprovementEvaluation`.
- **`tools/improvement_eval/errors.py`** — `InfraFailure`, the exception type that keeps a broken
  harness from reading as evidence against a candidate.
- **`ImprovementEvaluation.charter_digest`** — one additive field plus its migration, so a verdict
  can name the charter it was measured under.
- **`VALOR_PROJECT_KEY` in `_harness_env`** — so an arm subprocess partitions on the arm's project
  rather than on whatever the ambient environment happens to say.

### Flow

Frozen `ImprovementExperiment` → `evaluate()` → **contract digest re-check** → charter pinned →
**corpus exported and hashed** → arms assigned and digest recorded → **two private Redis arms
restored from one export** → corpus digests compared → writer kill switch armed → **incumbent
baseline parity gate** → paired trials in randomized order → **blinded judge envelopes** → Holm
correction over per-endpoint bootstrap CIs → stopping rule checked → one `ImprovementEvaluation`

Four exits, and which one you get is the whole design:

**accept** → endpoints cleared their margins and their Holm-adjusted thresholds
**reject** → the measurement ran and they did not
**inconclusive** → the measurement ran and could not tell the arms apart
**infra_failure** → the harness broke, and this says nothing about the candidate

Plus one non-exit: **`state="invalidated"`, no verdict at all**, when an artifact failed its
integrity check.

### Technical Approach

**Isolation is snapshot-and-restore, not freeze or copy-on-write** (spike-3). Redis offers no
logical-database copy-on-write, and a shared instance with a kill switch keeps both arms in one
keyspace where one escaped write corrupts the comparison invisibly. A fresh process per arm gives
byte-identical reads by construction. The kill switch survives as an independent second guard rather
than as the primary mechanism, because a guard that can only fail one way is a guard nobody has
evidence about.

**The arm's Redis is reached by an explicitly-constructed client on a unix socket.** This is the one
place where following the repo's normal Redis convention would be wrong. `tests/db_claim.py` is the
single source of "which server and which db does this pytest process own", and issue #2799 records
what happens when a private server is introduced without respecting that: the suite kept hitting
6379 and flushed production db1. So `arena.py` spawns `redis-server --port 0 --unixsocket
<tmp>/arm.sock --save '' --appendonly no --dir <tmp>`, which has no TCP listener to collide with a
concurrent agent, and reaches it through `redis.Redis(unix_socket_path=...)` constructed in place.
`REDIS_URL` is never reassigned, `db_claim` is never called, and popoto's canonical pool is never
re-pointed. A test asserts all three.

**Byte-identical corpus reads are asserted at run time, not only in a test.** After restore, each
arm's corpus is re-read through its own client, canonicalized the same way the export was, and
hashed. Unequal digests, or a digest differing from the export's, end the run as `infra_failure`
before any measurement. The acceptance criterion's test then exercises the same code path rather
than a parallel one.

**Gate ordering is load-bearing.** The contract-digest re-check runs before the charter is pinned;
the corpus export runs before the arms spawn; the incumbent's baseline parity runs before the
candidate arm is invoked at all. The parity gate's test asserts the candidate arm function was never
called, not merely that the verdict came out invalid — an operator who learns the candidate's number
and then learns the run was invalid has already been influenced by it.

**Holm is three named operations** (research finding 1). Sort ascending; multiply each by
`(m - j + 1)`; take the cumulative maximum and clamp at 1.0; map back to the original input order.
The cumulative max is the standard defect site — CRAN's `RHSDB` shipped a release to fix exactly this
— so the test pins the monotonicity property directly (adjusted values non-decreasing in sorted
order) as well as a worked example, and a mutation that drops the cumulative max must turn a test
red.

**The stopping rule is fixed-batch and named in the frozen contract.** `correction` is written as a
single string naming both the multiplicity correction and the stopping rule, for example
`"holm; fixed-batch(n=40, endpoints=3)"`. A run that has not completed its declared batch yields
`inconclusive` and never computes a verdict from a partial batch — interim estimation is biased
(research finding 3), so refusing is more honest than computing and flagging.

**Blinding is measured, and a leak is recorded rather than suppressed.** `scan_for_identity` runs
over the serialized envelope immediately before it is handed to a judge, looking for the candidate's
branch name, manifest surfaces, arm identity, and any operator-supplied identity tokens from the
experiment. A hit sets `blinded=False` on the evaluation and annotates `notes`. It does not abort:
an evaluation that honestly says its blinding failed is more useful than one that quietly did not
run. `blinded=True` is only ever written when the scan ran and found nothing.

**The `serves_charter` judge copies the shape that already works** (spike-5). A module-level
`SERVES_CHARTER_JUDGE_ID = "serves-charter"` proven disjoint from `code-quality`, `risk`, and
`cross-vendor` by a test; a status-discriminated envelope so a skip is distinguishable from an
approval; every response field coerced with a typed fallback. Its prompt carries
`ImprovementCharter.text` verbatim and the envelope carries the digest that text hashed to, so the
verdict can be re-read years later against the exact authority it was measured under (charter §12).

**Provider routing follows charter §7 through the existing guard.**
`tools/improvement_eligibility.py::is_open_source(project_key)` decides: True routes the judge to any
provider within the inference budget; False keeps it on the Claude and Codex subscriptions. That
function already fails closed to client on every uncertainty and already passes the repository to
`gh` positionally so `GH_REPO` cannot answer for it. This lane calls it and adds a test for each
direction; it does not reimplement the decision.

**Calibration freezes its reference set** (spike-4). `ImprovementEvidence` expires at 30 days and
`classify_correction` is deliberately precision-oriented, so the `architectural` bucket is both
small and rotating. `calibration.py` reads it once, writes the set to the verifying artifact store,
and every calibration record cites that artifact by digest. Recalibration writes a new artifact
rather than mutating one, so a kappa reported in March remains checkable in September. The reported
numbers are Cohen's kappa (chance-corrected: raw agreement overstates discrimination by 33–41
percentage points, research finding 2) and a paired position-swap consistency figure, because kappa
alone produces a false sense of having addressed judge reliability. A reference set below a declared
floor makes the judge return `infra_failure` rather than an uncalibrated opinion.

**`infra_failure`, `reject`, and `invalidated` are produced by three disjoint conditions.**
`infra_failure` comes from `errors.InfraFailure`, raised for a contract-digest mismatch, an arm that
would not spawn, unequal corpus digests, a baseline parity miss, a judge provider that could not be
reached, or an uncalibrated judge. `reject` comes only from a completed measurement whose endpoints
did not clear. `invalidated` comes only from `ArtifactIntegrityError`. The runner catches those three
in separate handlers with no shared fall-through, and a test drives each one.

**"No verdict" is checkable, not aspirational.** `verdict` is an `IndexedField` with a schema default
of `inconclusive`, so an invalidated row cannot literally hold nothing. The rule is therefore
expressed as behavior: on invalidation the runner writes `state="invalidated"`, leaves `verdict` at
its default, and never writes `accept` or `reject`; `runner.has_verdict(evaluation)` returns True
only for `state == "complete"`. Consumers read `has_verdict` before `verdict`, and a test pins that
an invalidated row never carries `accept` or `reject`.

**`VALOR_PROJECT_KEY` joins `_harness_env` in the shape `VALOR_CORRELATION_ID` already took.** It is
resolved through `config/project_key_resolver.py` and added to the dict literal at
`agent/session_executor.py:2116`, so `tools/memory_search/__init__.py:62` and
`reflections/redis_access.py:40` stop silently falling back to `"valor"` inside an arm subprocess.
This is a one-line addition with a two-line test, and it is in this lane because an arm that
partitions on ambient environment is not an isolated arm.

## Failure Path Test Strategy

This lane is almost entirely failure paths. The happy path is one function call; everything that
makes the verdict worth anything is a guard, and a guard with no red-state proof is decoration.

### Exception Handling Coverage

- [ ] No `except Exception: pass` is introduced anywhere in `tools/improvement_eval/`. The three
      broad handlers that do exist in `runner.py` — `InfraFailure`, `ArtifactIntegrityError`, and a
      final catch-all — each write an observable outcome (a state, a verdict, and a `notes` string)
      and log at `warning` or above. A test asserts each handler's observable effect, not just that
      it did not raise.
- [ ] `arena.py`'s subprocess teardown uses a `finally` that terminates the `redis-server` and
      removes the socket even when the arm body raised. Test: force an exception inside the arm
      context and assert the process is gone (`pid` not alive) and the tmpdir is cleaned.
- [ ] `judges/serves_charter.py` follows `cross_vendor_judge.py`'s coercion pattern: every field
      from the model response has a typed fallback, and an unparseable response returns
      `{"status": "skipped", "reason": ...}` rather than a fabricated verdict. Test: feed prose,
      feed truncated JSON, feed a JSON object missing every key.
- [ ] `tools/improvement_eligibility.py::is_open_source` is already fail-closed and separately
      tested; this lane adds only the two call-direction tests and does not re-test its internals.

### Empty/Invalid Input Handling

- [ ] `holm_adjust([])` returns `[]`. `holm_adjust([p])` returns `[min(1.0, p)]`. A p-value outside
      `[0, 1]`, a `None`, or a `NaN` raises `ValueError` rather than silently producing an adjusted
      value — a correction computed from a malformed input is worse than a refusal.
- [ ] An empty corpus export produces a valid digest over zero records and the run proceeds to the
      parity gate, which fails it. Test that the empty case reaches `infra_failure` through the
      parity gate rather than crashing in the exporter.
- [ ] Zero completed trials, or fewer than the declared fixed batch, yields `inconclusive` with a
      `notes` string naming the shortfall. `bootstrap_ci` already returns non-significant below two
      deltas; the harness must not paper over that by treating a one-trial run as measured.
- [ ] A calibration reference set of size zero, or below the declared floor, makes the judge return
      `infra_failure`. Test both boundaries: exactly at the floor passes, one below fails.
- [ ] `scan_for_identity` on an empty envelope returns "no leak" and on a whitespace-only identity
      token raises rather than matching everything.

### Error State Rendering

- [ ] The four verdicts and `state="invalidated"` are each rendered distinctly by
      `ui/data/improvement.py`. `infra_failure` must not be presented anywhere as evidence about the
      candidate; the dashboard row says the harness broke.
- [ ] An `invalidated` evaluation renders as "cannot be scored" and never shows a verdict, matching
      `has_verdict()`. Test the template path, not just the data function.
- [ ] `blinded=False` is rendered as a visible qualification on the evaluation, not omitted. An
      evaluation that cannot state that judges were blinded is not a paired comparison and the
      dashboard shows it as one that cannot be.

### Mutation proofs (each guard, measured)

Every guard below ships with a recorded red-state proof: the named mutation is applied, the named
test is observed to fail, the mutation is reverted, and the test is observed to pass.

| Guard | Mutation | Test that must go red |
|---|---|---|
| Holm monotonicity | Drop the cumulative maximum | `test_holm_adjusted_values_are_non_decreasing` |
| Holm suppression | Return raw p-values unchanged | `test_holm_suppresses_the_spurious_winner` |
| Artifact integrity | Catch `ArtifactIntegrityError` and continue | `test_corrupted_archive_invalidates_without_verdict` |
| Parity gate ordering | Move the parity check after the candidate arm | `test_parity_miss_never_invokes_the_candidate_arm` |
| Corpus identity | Skip the per-arm digest comparison | `test_two_arms_read_a_byte_identical_corpus` |
| Blinding | Return `blinded=True` unconditionally | `test_identity_leak_sets_blinded_false` |
| Writer kill switch | Remove the client wrapper (leave the digest re-check) | `test_arm_write_is_refused` |
| Writer kill switch | Remove the digest re-check (leave the wrapper) | `test_escaped_write_surfaces_as_infra_failure` |
| Verdict disjointness | Merge `infra_failure` into `reject` | `test_infra_failure_and_reject_have_disjoint_causes` |
| §7 routing | Ignore `is_open_source` and always use any provider | `test_client_project_judge_stays_on_subscription_providers` |
| Calibration floor | Return a judge verdict below the floor | `test_reference_set_below_floor_yields_infra_failure` |
| `metrics.py` untouched | Edit `tools/memory_eval/metrics.py` | `test_metrics_module_is_unmodified` |

## Test Impact

Most of this lane is new test surface. Four existing files need changes, and each is named with its
disposition.

- [ ] `tests/unit/test_improvement_models.py` — UPDATE: `FORBIDDEN_INDEX_NAMES` (`:104-120`) already
      lists `contract_digest`; add `charter_digest` so the new `ImprovementEvaluation` field is
      pinned as never-indexed by the same cardinality rule that governs its siblings. The
      `INDEXED_VOCABULARIES` entry for `ImprovementEvaluation` is unchanged — the new field is a
      plain `Field`, so no vocabulary is added and no `VOCABULARY_MAXIMUMS` exemption is needed.
- [ ] `tests/unit/test_session_executor_extraction_decoupling.py::test_harness_env_declares_correlation_id`
      (`:161`) — UPDATE: add a sibling assertion in the same class for `VALOR_PROJECT_KEY`, matching
      the existing source-inspection shape (`assert '"VALOR_PROJECT_KEY"' in source`). Do not modify
      the correlation-id assertion; it is a separate regression pin.
- [ ] `tests/integration/test_session_spawning.py` — UPDATE: the `_harness_env` construction tests
      (`:87`, `:124`, `:148`) build the dict by hand. Add one case asserting `VALOR_PROJECT_KEY` is
      present and equals the resolved project key, so an arm subprocess's partition is covered end
      to end rather than only by source inspection.
- [ ] `tests/unit/test_migrations.py` — UPDATE: register the new migration key so the
      `MIGRATIONS`-dict completeness assertions cover it, following
      `_migrate_confirm_improvement_v2_fields` (`scripts/update/migrations.py:1429`) as the precedent.
- [ ] `tests/unit/test_review_multi_judge.py:676` — UPDATE: the existing judge-id disjointness test
      asserts `CROSS_VENDOR_JUDGE_ID not in {"code-quality", "risk"}`. Extend the same test (or add
      an adjacent one) to include `SERVES_CHARTER_JUDGE_ID`, so a future judge id collision is caught
      in the one place the repo already looks for it.

**Not affected, deliberately:**

- `tests/unit/test_memory_eval.py` — untouched. `metrics.py` is imported and not modified, so its
  tests keep passing unchanged; that is the evidence for acceptance criterion 7, and a Verification
  row asserts the file itself is byte-identical to main.
- `tests/db_claim.py` and `tests/unit/test_test_redis_server_resolution.py` — untouched by design.
  The arm arena deliberately does not participate in the db-claim pool (spike-2), and a new test
  asserts `arena.py` never calls into `db_claim` or reassigns `REDIS_URL`.
- `tests/unit/test_settings.py` — untouched. No new setting is added in this lane; the budget
  settings this harness draws against already exist and are lane 3's to meter.

## Rabbit Holes

- **Building copy-on-write over Redis.** Spike-3 settled this: Redis has no logical-database
  copy-on-write, so building it means intercepting every command, and the property it would buy is
  free from a fresh process. If a later lane finds snapshot-and-restore too slow, that is a
  performance problem with a measurement, not a reason to start here.
- **Alpha-spending functions.** The strictly harder commitment, and it needs a pre-specified maximum
  sample size the loop has no evidence to choose yet. A half-implemented spending function produces
  precisely the peeking it exists to prevent. Fixed-batch is a complete, honest stopping rule and it
  is what this lane ships.
- **Chasing a kappa threshold.** The temptation is to pick 0.7 and gate on it. The literature's own
  conclusion is that the bar is human–human agreement on the same set, not an abstract number, and
  that kappa reported alone creates false confidence. Measure it, freeze the set, cite it, and let a
  later lane with real agreement data set a gate.
- **Making the `serves_charter` judge a gate.** The family plan says explicitly that its verdict is
  one input to the consensus envelope and never a gate on its own. A charter-alignment judge with
  veto power over its own controller's experiments is the shape this whole design exists to avoid.
- **Rewriting `tools/memory_eval/`.** Its arms and gates are retrieval-specific. Extending it to
  carry general candidate-versus-incumbent semantics would couple two evaluation systems whose
  lifecycles differ, and acceptance criterion 7 forbids it outright.
- **Generalizing the arena into a reusable test fixture.** A private-Redis context manager looks like
  something the whole suite should have. It is not: `tests/db_claim.py` is the suite's isolation
  mechanism and issue #2799 is what happens when a second one appears beside it. The arena is
  harness-internal and stays that way.
- **Perfecting `classify_correction`.** The regex is precision-oriented on purpose and its author
  documented why. Improving it changes the calibration set's composition, which is a measurement
  question for a lane that has a measurement, not a plausible-looking regex edit here.
- **Wiring an automated promotion path.** An `accept` verdict produces a proposal for a human. The
  contract doc is explicit that automated promotion stays disabled and that no record enables it.

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
