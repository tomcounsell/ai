---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-10
tracking: https://github.com/tomcounsell/ai/issues/3216
last_comment_id: 5620338927
revision_applied: true
revision_applied_at: 2026-09-09T17:55:19Z
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
- `VALOR_PROJECT_KEY` is absent from `_harness_env` (`agent/session_executor.py:2116`), so every
  `claude -p` harness subprocess resolves its project partition from ambient environment rather than
  from the session that launched it. `tools/memory_search/__init__.py:62` and
  `reflections/redis_access.py:40` both read that variable and both silently fall back to `"valor"`,
  so a non-`valor` session's subprocess searches and writes the wrong partition today.
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
`ImprovementEvaluation` whose every claim is checkable from the record alone. **The arms this lane
builds compare retrieval over a frozen memory corpus** — that is the cheap arm shape on which the
apparatus can be proven, and the apparatus is what this lane is for; a paired *agent-run* arm is
lane 5's and is recorded in No-Gos. Two arms read a byte-identical corpus from separate private Redis
processes, each reached only by its own subprocess whose `REDIS_URL` points at the arm's socket. The incumbent arm reproduces a
known baseline on that corpus before any candidate number is read, and a parity miss invalidates
the run. Judges see a blinded arm ID and `blinded` records whether that actually held. Holm
correction is applied across the endpoints and named in `correction`. A corrupted artifact
produces `state="invalidated"` and no verdict at all. `infra_failure` and `reject` come from
distinguishable conditions. And a `serves_charter` judge scores the candidate against
`ImprovementCharter.text`, quoting the digest it judged under, calibrated against a frozen
reference set of retained architectural corrections.

## Freshness Check

**Baseline commit:** `dea9ed5db8620d548435dda8c4c2f469e352f956` (main, 2026-09-10)
**Build-against commit:** main itself. Lane 2b (PR #3275) merged as `aff4d7e2e`, so the
charter surface this lane consumes is on main and the `b05dde885` worktree head is retired.
Every reference below was re-verified against this baseline for revision 3.
**Issue filed at:** 2026-09-07T04:45:09Z
**Disposition:** Minor drift

**File:line references re-verified (on `dea9ed5db`):**

- `models/improvement_evaluation.py:52` — `EVALUATION_VERDICTS = ("accept", "reject", "inconclusive", "infra_failure")` — holds.
- `models/improvement_evaluation.py:87-92` — `blinded`, `arm_assignment_digest`, `trials`, `effect`, `confidence_interval`, `correction` — all hold, all plain `Field(null=True)`.
- `models/improvement_evaluation.py:93` — `judge_records = ContentField(store=verifying_artifact_store)` — holds.
- `models/verifying_artifact_store.py:70-118` — `load()` re-hashes the archive fallback; the archive-mismatch raise is at `:102`, the live-corrupt-no-archive raise at `:110` — holds, and the two raises are separately reachable, which the mutation test needs.
- `models/improvement_experiment.py:78-88` — `state`, `contract_digest`, `manifest`, `frozen_at`, `charter_version` — hold.
- `tools/memory_eval/metrics.py:68-99` — `bootstrap_ci` seeded, `BootstrapCI.significant = lower > 0.0` — holds.
- `models/improvement_charter.py:138-140` — `digest`, `effective`, `text` — hold. `pinned()` at `:237`, `load_from_file()` at `:149`.
- `models/improvement_case.py:118,126,127` — `priority_area`, `charter_digest`, `ranking_rationale` — hold.
- `models/improvement_investigation.py:94` and `models/improvement_release.py:84` — `charter_digest` — hold.
- `config/settings.py:622,634` — `daily_paid_inference_usd`, `weekly_infrastructure_usd` — hold.
- `tools/improvement_eligibility.py:83` — `is_open_source(project_key, *, ttl_seconds=...)` — holds.
  Arrived with #3275 (merged as `aff4d7e2e`) and is now on main, so the Prerequisites row
  imports it alongside the charter rather than checking the charter half only.
- `scripts/update/migrations.py:1434` — `_migrate_confirm_improvement_v2_fields`, the precedent this
  lane's migration mirrors. Arrived with #3275 and is now on main (`origin/main` used to carry
  only `_migrate_confirm_improvement_models_readable` at `:1384`). `MIGRATIONS` is declared at
  `:1462` as `dict[str, tuple[callable, str]]`, so the registered callable is `v[0]` — a registry
  check that reads `getattr(v, "__name__", "")` against the tuple can never match, and the
  Verification row uses `v[0]`.
- `agent/memory_retrieval.py:117` — `POPOTO_REDIS_DB.zrevrange(...)` inside `get_relevance_ranked`,
  the stored-score read that makes RRF retrieval clock-independent — holds.
- `popoto/models/query.py:494` — `now = time.time()` inside `top_by_decay`; the decay clock has no
  parameter, which is why `top_by_decay` is an anti-criterion here — holds (popoto in `.venv`).
- `popoto/models/base.py:2785`, `:2820` — `Model.export_records` / `Model.import_records`, the ORM
  transfer API the corpus export uses — hold.
- `popoto/redis_db.py:405-414` — `POPOTO_REDIS_DB` built from `REDIS_URL` at module import; the
  mechanism the arm subprocess relies on — holds.
- `popoto/fields/embedding_field.py:227` — `POPOTO_CONTENT_PATH` resolves the `.npy` store — holds.
- `ui/data/improvement.py` — 308 lines on `dea9ed5db`, exposing `get_coverage`,
  `get_intervention_burden`, `get_provisional_assumptions`, `get_goals`. No evaluation surface, which
  is why this lane's Error State Rendering section no longer commits to one.
- `agent/session_executor.py:2116` — `_harness_env` dict literal; `VALOR_PROJECT_KEY` confirmed **absent**.
- `reflections/improvement_collect.py:112` — `classify_correction` returning `"architectural"` on `_ARCHITECTURAL_MARKERS` — holds.

**Cited sibling issues/PRs re-checked:**

- #3177 (parent) — OPEN. The family plan `docs/plans/recursive-self-improvement.md` still names this lane at `:801` and `:408`.
- #3215 (lane 3, declared dependency) — OPEN, unstarted. See Prerequisites for why this lane does not block on it.
- #3255 / PR #3275 (lane 2b) — MERGED as `aff4d7e2e`. The charter surface this lane consumes
  is on main; the `[ORDERED]` gate in No-Gos is satisfied and task 3b proceeds.
- #3217 (lane 5), #3218 (lane 6) — OPEN. Neither blocks this lane; both consume it.

**Commits on main since the issue was filed (touching referenced files):**

- `5b994db3f` "Recursive self-improvement controller: lanes 1 and 2" — created every record this lane writes. Not drift; it is the premise.
- `a9822d719` "ETL-grade pipeline hardening" — touched `agent/session_executor.py`. Read the diff: it added `VALOR_CORRELATION_ID` to `_harness_env`. That is the exact shape this lane adds `VALOR_PROJECT_KEY` in, and it moved no line this plan depends on beyond the dict literal's own extent.
- `2a1138d5f` "Synthetic-slug worktrees are invisible to both busy-guard predicates" — irrelevant to this surface.
- `aff4d7e2e` "Improvement controller lane 2b: charter v2 delta" (PR #3275, merged) — created
  the charter surface this lane consumes (`ImprovementCharter.digest`/`.text`/`.pinned()`,
  `tools/improvement_eligibility.py`, `_migrate_confirm_improvement_v2_fields`). Not drift; it
  retires the `b05dde885` build-against head and satisfies the `[ORDERED]` No-Go gate.

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
  (`tests/unit/test_review_multi_judge.py:675`), (b) emits a status-discriminated envelope
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
  --port 0 --save '' --appendonly no --dir <tmp>` per arm. Port 0 means the arm has no TCP listener at
  all, so it cannot be reached by accident and cannot collide with a concurrent agent's port.
  `db_claim` is untouched, and a test asserting that is in the Verification table. **Revision note:**
  this spike's original conclusion — "reach the arm through an explicitly constructed client on a unix
  socket" — was wrong about the second half. A bare client cannot answer `Memory.query`, which is how
  every retrieval path in this repo reads. The correct conclusion is that the arm is reached through
  its *own process*, whose `REDIS_URL` points at the socket, so popoto's canonical pool inside that
  child is the arm. The parent's pool is still never re-pointed, which is what spike-2 and #2799 were
  really protecting.

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
  (`tests/unit/test_review_multi_judge.py:675`, asserting at `:679`), a status-discriminated envelope
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

3. **Corpus export** (`corpus.py`). The project's memory corpus is exported once per run through
   popoto's ORM-native transfer API — `Memory.export_records(project_key=..., stream=fh)`
   (`popoto/models/base.py:2785`, delegating to `popoto/transfer/export.py`) — which writes a
   manifest line followed by one JSON Lines record per memory, carrying every field plus the
   auxiliary state each field declares through `export_state` (BM25 posting data, the relevance
   score, confidence, the embedding vector). The JSONL bytes are hashed and written to the verifying
   artifact store; that digest is the corpus identity for the whole run. Nothing here issues a raw
   Redis command: the export is a documented ORM read, so it is binary-safe on
   `Memory.embedding`'s float32 bytes and cannot desynchronize an index from its hash.

4. **Arm assignment** (`blinding.py`). Arms are assigned randomized run ordering from a seed derived
   from the experiment id. `arm_assignment_digest` is written before either arm runs. Each arm gets
   a blinded id (`arm-a` / `arm-b`) with the incumbent/candidate mapping held only by the runner.

5. **Arena spawn and arm subprocess** (`arena.py`, `arm_worker.py`). Per arm: one `redis-server` on
   a unix socket in a per-arm tmpdir, with `--port 0` (no TCP listener), no persistence, and its own
   `--dir`. The arm is then reached **only** by a child Python process,
   `python -m tools.improvement_eval.arm_worker`, spawned with an env dict that is built for the
   child and never assigned into the parent's `os.environ`:

   ```python
   child_env = {
       **os.environ,
       "REDIS_URL": f"unix://{sock_path}",          # popoto binds here at import
       "POPOTO_CONTENT_PATH": str(arm_tmp / "content"),  # per-arm .npy embedding store
       "VALOR_PROJECT_KEY": project_key,
       "POPOTO_EMBEDDING_INVALIDATION": "none",
   }
   subprocess.run([sys.executable, "-m", "tools.improvement_eval.arm_worker"], env=child_env, ...)
   ```

   This is the whole answer to "how does an arm read its own corpus". Popoto builds
   `POPOTO_REDIS_DB` from `REDIS_URL` at module import (`popoto/redis_db.py:405-414`), and
   `redis.BlockingConnectionPool.from_url("unix:///…")` resolves to a `UnixDomainSocketConnection`
   — verified in this venv. So inside the child, and only inside the child, the canonical pool *is*
   the arm's private server, and `Memory.query`, `agent.memory_retrieval.retrieve_memories`, and
   `tools.memory_search.search` all work unmodified through the ORM. The parent's pool is never
   re-pointed, `set_REDIS_DB_settings` is never called, and no bare client is ever asked to answer
   `Memory.query`. The child restores the step-3 JSONL with
   `Memory.import_records(fh, on_conflict="overwrite", on_embedding_mismatch="carry")`, which
   preserves keys, saves with `skip_auto_now=True` (so the relevance timestamp carries rather than
   resetting to import time), and carries the exported vectors instead of re-embedding — no Ollama
   call, no non-determinism. **The corpus digest is then recomputed inside each arm by re-running
   `export_records` against the arm's own pool and comparing**; unequal digests are `infra_failure`
   before anything is measured. That is acceptance criterion 1, asserted at run time rather than
   only in a test.

6. **Writer kill switch** (`writer_guard.py`). Each arm's client is wrapped so corpus-key writes
   raise. The wrapper is the first guard; the second is a digest re-check at arm teardown, which
   catches a write that reached the server by some path the wrapper did not cover.

7. **Gate 1 — baseline parity** (`retrieval.py`). The **incumbent** arm runs the frozen baseline
   query set against the frozen corpus and the **ordered list of returned memory ids** is compared to
   the ids recorded in the baseline, which stores the corpus digest it was captured under. Ids, not
   scores: RRF fusion scores are stable here but comparing them buys nothing the ranking does not
   already prove, and a float equality across two processes is a gate that fails for reasons nobody
   wants to debug. A miss ends the run as `infra_failure` and **the candidate arm is never invoked**.
   The ordering is the point: an incumbent that cannot reproduce itself makes every candidate number
   meaningless, and reading the candidate first would let the operator learn the answer before
   learning the run was invalid.

8. **Paired trials**. For each trial in the fixed batch, both arms run the same retrieval input in
   the randomized order from step 4, each inside its own `arm_worker` subprocess, and hand back the
   ranked ids and per-endpoint metrics as JSON on stdout. A trial that errors in the harness is
   excluded and counted toward the infra-failure cap; a trial where the *candidate* fails is a
   candidate failure and scores as one. That distinction is `retrieval_arms.py`'s errored-vs-empty
   discipline, raised to the arm level.

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
  (`scripts/update/migrations.py:1430`). One additive env change — `VALOR_PROJECT_KEY` in
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
| Lane 2b surface present (charter **and** eligibility) | `python -c "from models.improvement_charter import ImprovementCharter as C; from tools.improvement_eligibility import is_open_source; assert hasattr(C,'digest') and hasattr(C,'text') and hasattr(C,'pinned')"` | `serves_charter` judges against `ImprovementCharter.text` and routes through `is_open_source`; **both** arrived with #3255, so the gate covers the whole lane-2b surface this lane consumes rather than the charter half only. Passes on main since `aff4d7e2e` merged. |
| `metrics.py` import path | `python -c "from tools.memory_eval.metrics import bootstrap_ci"` | Acceptance criterion 7 |
| Popoto transfer API present | `python -c "from models.memory import Memory; assert hasattr(Memory,'export_records') and hasattr(Memory,'import_records')"` | The corpus export/restore is `export_records`/`import_records`; without them the arm has no legal corpus load |
| `unix://` URL support in the pinned redis-py | `python -c "import redis; p=redis.BlockingConnectionPool.from_url('unix:///tmp/x.sock'); assert p.connection_kwargs.get('path')=='/tmp/x.sock'"` | The arm subprocess reaches its server through `REDIS_URL=unix://…`; popoto builds its pool with `from_url` |

`scipy` and `statsmodels` are deliberately absent and no check asks for them (spike-1).

## Solution

**What lane 4's arms actually compare.** Two arms retrieving from one frozen memory corpus, scored on
retrieval endpoints. That is narrower than the Problem section's framing of "a candidate agent run
against an incumbent", and the narrowing is deliberate: every mechanism this lane exists to establish
— frozen inputs, per-arm isolation, blinding, a parity gate, multiplicity correction, an honest
`infra_failure` — is mechanism-agnostic, and proving it on retrieval costs minutes per run instead of
the hours a paired agent-run comparison costs. **Comparing full candidate agent runs is a No-Go for
this lane** and is recorded as such below. What ships here is the apparatus a later lane points at a
bigger arm.

### Key Elements

- **`tools/improvement_eval/corpus.py`** — exports the project's memory corpus once per run through
  `Memory.export_records(project_key=..., stream=fh)` (popoto's ORM transfer API), hashes the JSONL
  bytes, and writes them to the verifying artifact store. The digest is the corpus identity for the
  run. Restore is `Memory.import_records(fh, on_conflict="overwrite", on_embedding_mismatch="carry")`,
  which preserves keys, saves with `skip_auto_now=True`, and carries vectors rather than re-embedding.
- **`tools/improvement_eval/arena.py`** — spawns one private `redis-server` per arm on a unix socket
  with no TCP listener and no persistence, hands back the socket path and the per-arm tmpdir, and
  tears the process down. It never opens a client of its own: the arm is reached only through
  `arm_worker.py`. Never re-points popoto's canonical pool, never assigns `REDIS_URL` in the parent
  process, never imports `tests/db_claim.py`.
- **`tools/improvement_eval/arm_worker.py`** — the arm-side entry point, run as
  `python -m tools.improvement_eval.arm_worker` in a child process whose env dict carries
  `REDIS_URL=unix://<arm.sock>`, `POPOTO_CONTENT_PATH=<arm tmp>/content`, `VALOR_PROJECT_KEY`, and
  `POPOTO_EMBEDDING_INVALIDATION=none`. Reads a job spec on stdin, restores the corpus, runs the
  retrieval, re-exports for the digest check, and writes JSON on stdout. Every Redis touch inside it
  goes through the ORM against the child's own canonical pool.
- **`tools/improvement_eval/writer_guard.py`** — the writer kill switch. A client wrapper that
  refuses corpus writes, plus an independent corpus-digest re-check at arm teardown so a write that
  slipped past the wrapper still surfaces as `infra_failure` rather than as a result.
- **`tools/improvement_eval/retrieval.py`** — the isolated retrieval adapter over
  `agent.memory_retrieval.retrieve_memories` (the four-signal RRF path, all of whose inputs are
  persisted state), and the **baseline parity gate**: the incumbent arm reproduces its recorded
  baseline's ranked memory ids on the frozen corpus before any candidate result is read. It never
  calls `Query.top_by_decay`, whose clock cannot be pinned.
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

**The arm's Redis is reached by a child process whose own `REDIS_URL` points at the arm socket.**
This is the single design decision that makes the rest of the lane implementable, and the plan
commits to it rather than leaving two routes open.

Retrieval in this repo goes through the Popoto ORM against the process-global `POPOTO_REDIS_DB`
(`tools/memory_search/__init__.py:45,:381`; `agent/memory_retrieval.py`;
`reflections/redis_access.py::get_redis`). A bare `redis.Redis(unix_socket_path=…)` cannot answer
`Memory.query`, and re-pointing the parent's canonical pool would break every other consumer in the
process. Both dead ends have the same escape: **give the arm its own process.** Popoto builds
`POPOTO_REDIS_DB` from `REDIS_URL` at module import time (`popoto/redis_db.py:405-414`), so a child
launched with `REDIS_URL=unix://<arm.sock>` in its env dict has a canonical pool bound to the arm and
a parent whose pool never moved. `redis.BlockingConnectionPool.from_url` parses the `unix://` scheme
into a `UnixDomainSocketConnection` — confirmed against the pinned redis-py in this venv.

So: `arena.py` spawns `redis-server --port 0 --unixsocket <tmp>/arm.sock --save '' --appendonly no
--dir <tmp>` (no TCP listener, so no port for a concurrent agent to collide on — issue #2799 is the
failure this avoids), and `arm_worker.py` is the only thing that ever talks to it, as
`subprocess.run([sys.executable, "-m", "tools.improvement_eval.arm_worker"], env=child_env)`.
`child_env` is a dict built for the call. The parent's `os.environ["REDIS_URL"]` is never assigned,
`set_REDIS_DB_settings` is never called, `tests/db_claim.py` is never imported, and no raw Redis
command is issued against a Popoto-managed key from either side. Three tests assert exactly that,
and the anti-criteria below match the assignment forms that would violate it rather than the
dict-literal key the design requires.

`POPOTO_CONTENT_PATH` rides along in the same dict. It is what `EmbeddingField` resolves its `.npy`
store from (`popoto/fields/embedding_field.py:226`), so without it two arms would share the parent's
embedding directory and the isolation would be half-built.

**Corpus transfer is popoto's own transfer API, not a hand-rolled text export.**
`Model.export_records` / `Model.import_records` (`popoto/models/base.py:2785`, `:2820`) write and
read JSON Lines with a leading manifest, preserve keys, collect each field's auxiliary state through
its `export_state` hook, and import by `instance.save(skip_auto_now=True)`. That last flag is
load-bearing: `Memory.relevance` is a `DecayingSortedField` with `auto_now=True`, so a plain re-save
would stamp every record with import time and destroy the corpus's temporal structure. Passing
`on_embedding_mismatch="carry"` imports the exported vectors rather than re-embedding, so a restore
makes no Ollama call and is deterministic. Using the ORM's own transfer path is also what keeps this
lane inside CLAUDE.md's rule that Popoto-managed keys are read and written through the ORM.

**Byte-identical corpus reads are asserted at run time, not only in a test.** After restore, each
arm re-runs `export_records` against its own pool, hashes the result the same way the original export
was hashed, and reports the digest. Unequal digests between arms, or a digest differing from the
export's, end the run as `infra_failure` before any measurement. The acceptance criterion's test
exercises that same code path rather than a parallel one.

**Retrieval reproducibility is a property of the ranking path, and the plan pins the path.** Identical
bytes are necessary and not sufficient: `Memory.relevance` is a `DecayingSortedField` whose Lua
computes `base_score * elapsed_days ** (-decay_rate)`, and `Query.top_by_decay` takes its clock from
`now = time.time()` inside the call (`popoto/models/query.py:494`) with no parameter to pin it — the
`as_of` argument gates bitemporal validity, not the decay arithmetic. A harness that ranked through
`top_by_decay` would therefore be irreproducible by construction and there would be no argument to
make about tolerances.

It does not rank that way. The production retrieval path is
`agent.memory_retrieval.retrieve_memories`, whose four RRF signals are BM25, the **stored** relevance
sorted-set scores read by a plain `zrevrange` (`agent/memory_retrieval.py:117`), confidence, and
cosine similarity over the on-disk embedding matrix. Every one of those is a read of persisted state,
and `import_records`' `skip_auto_now=True` carries the persisted relevance scores across unchanged.
Two arms restored from one export therefore rank identically regardless of when each is queried, and
an incumbent baseline recorded against that corpus digest reproduces. **`top_by_decay` is an
anti-criterion for this lane** — a Verification row greps for it under `tools/improvement_eval/` and
expects zero matches, and `test_two_arms_rank_identically_across_a_clock_gap` runs the two arms with
a deliberate sleep between them and asserts the ranked ids match, which is the property criterion 1
actually needs and which an exporter-canonicalization assertion never proved.

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

**`VALOR_PROJECT_KEY` joins `_harness_env` in the shape `VALOR_CORRELATION_ID` already took, and it
stands on its own merit rather than on the arm.** The arm rationale was wrong: an evaluation arm is
spawned by `arena.py`, not by `agent/session_executor.py`, so it never reads `_harness_env` at all
and gets its `VALOR_PROJECT_KEY` from the child env dict `arena.py` builds. The defect the change
fixes is a different and larger one, already stated in the Problem section and needing no arm to
exist: `_harness_env` (`agent/session_executor.py:2116`) omits `VALOR_PROJECT_KEY`, while
`tools/memory_search/__init__.py:62` (`os.environ.get("VALOR_PROJECT_KEY", DEFAULT_PROJECT_KEY)`) and
`reflections/redis_access.py:40` both read it and both fall back to `"valor"`. So **every** `claude -p`
harness subprocess of a non-`valor` session silently searches and writes the wrong memory partition
today. It is a one-line addition in the same dict literal `VALOR_CORRELATION_ID` (`:2137`) joined in
`a9822d719`, with a source-inspection test and an integration test, and it is independent of every
other task in this lane — which is why task 4 already carries `Depends On: none`.

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

No dashboard rendering in this lane. `ui/data/improvement.py` exposes `get_coverage`,
`get_intervention_burden`, `get_provisional_assumptions`, and `get_goals` and has no evaluation
surface at all, and the operator surface for evaluations belongs to lane 3's `valor-improve`
(No-Gos, `[SEPARATE-SLUG #3215]`). Committing to a UI here would have meant a file no builder owns,
a template that does not exist, and a "test the template path" instruction with no path to test.

The semantics those checkboxes were reaching for are pinned where they are actually produced, which
is where a later dashboard will read them from:

- [ ] `runner.has_verdict(evaluation)` returns True only for `state == "complete"`, and
      `test_corrupted_archive_invalidates_without_verdict` asserts an invalidated row never carries
      `accept` or `reject`. Any consumer that reads `has_verdict` before `verdict` renders
      "cannot be scored" correctly by construction.
- [ ] `infra_failure` is a distinct verdict value produced by six named conditions with a test each,
      never merged into `reject` (`test_infra_failure_and_reject_have_disjoint_causes`). A consumer
      cannot present it as evidence about the candidate without deliberately choosing to.
- [ ] `blinded` is written only from `scan_for_identity`'s result and is never null on a completed
      evaluation, so `blinded=False` is a queryable fact rather than an absence a renderer has to
      infer. `test_identity_leak_sets_blinded_false` pins it.

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
| Retrieval reproducibility | Rank through `Query.top_by_decay` instead of `retrieve_memories` | `test_two_arms_rank_identically_across_a_clock_gap` |
| Relevance carry-over | Drop `skip_auto_now` from the restore (re-stamp relevance at import) | `test_two_arms_rank_identically_across_a_clock_gap` |
| Parent pool isolation | Call `set_REDIS_DB_settings` in the parent instead of spawning `arm_worker` | `test_parent_pool_kwargs_survive_an_arena_context` |
| Blinding | Return `blinded=True` unconditionally | `test_identity_leak_sets_blinded_false` |
| Writer kill switch | Remove the client wrapper (leave the digest re-check) | `test_arm_write_is_refused` |
| Writer kill switch | Remove the digest re-check (leave the wrapper) | `test_escaped_write_surfaces_as_infra_failure` |
| Verdict disjointness | Merge `infra_failure` into `reject` | `test_infra_failure_and_reject_have_disjoint_causes` |
| Gate 0 crash disposition | Let Gate 0 accept `state="running"` | `test_crashed_run_leaves_a_documented_repair` |
| Corpus restore fidelity | Drop `on_embedding_mismatch="carry"` (re-embed on restore) | `test_two_arms_rank_identically_across_a_clock_gap` |
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
      `_migrate_confirm_improvement_v2_fields` (`scripts/update/migrations.py:1430`) as the precedent.
- [ ] `tests/unit/test_review_multi_judge.py:675` — UPDATE:
      `test_cross_vendor_id_disjoint_from_claude_ids` is defined at `:675` with its assertion
      `CROSS_VENDOR_JUDGE_ID not in {"code-quality", "risk"}` at `:679`, identical on `origin/main`
      and `b05dde885`. Extend the same test (or add an adjacent one) to include
      `SERVES_CHARTER_JUDGE_ID`, so a future judge id collision is caught in the one place the repo
      already looks for it. (The critique proposed `:677`/`:681`; re-derived against both refs, the
      correct pair is `:675`/`:679`, which is what this plan now carries.)

**Not affected, deliberately:**

- `tests/unit/test_memory_eval.py` — untouched. `metrics.py` is imported and not modified, so its
  tests keep passing unchanged; that is the evidence for acceptance criterion 7, and a Verification
  row asserts the file itself is byte-identical to main.
- `tests/db_claim.py` and `tests/unit/test_test_redis_server_resolution.py` — untouched by design.
  The arm arena deliberately does not participate in the db-claim pool (spike-2), and new tests
  assert that nothing under `tools/improvement_eval/` imports `db_claim`, assigns
  `os.environ["REDIS_URL"]`, or calls `set_REDIS_DB_settings`. The arm's `REDIS_URL` lives in a
  subprocess env dict, which is a different thing and is what the corrected anti-criterion regex
  distinguishes.
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

### Risk 1: A private `redis-server` per arm collides with the machine's other Redis users

**Impact:** This machine runs production Redis on 6379 and up to fifteen concurrent pytest processes
holding claimed dbs. Issue #2799 records the exact failure: a private server introduced for isolation
left the suite hitting 6379 and flushing production db1. Repeating it corrupts unrelated agents' runs
and, at worst, production data.

**Mitigation:** The arm server has **no TCP listener at all** — `--port 0` plus `--unixsocket` in a
per-arm tmpdir. There is no port to collide on and no way for another process to reach it. `REDIS_URL`
points at the arm **only inside the child process's env dict**, which is constructed for the
`subprocess.run` call and never assigned into the parent's `os.environ`; `set_REDIS_DB_settings` is
never called; `tests/db_claim.py` is never imported by this package. Three tests, and the last is the
one that actually bites:

1. `test_arena_never_assigns_redis_url_in_the_parent` — source inspection for
   `os.environ["REDIS_URL"] = …` and `os.environ.setdefault("REDIS_URL"…)` anywhere under
   `tools/improvement_eval/`, expecting none. (The old anti-criterion regex `REDIS_URL[^"]*=` matched
   a dict-literal key, which this design uses deliberately, and would have failed for the wrong
   reason; the Verification table below carries the corrected form.)
2. `test_arena_does_not_touch_the_db_claim_pool` — no import of `tests.db_claim`.
3. `test_parent_pool_kwargs_survive_an_arena_context` — capture
   `popoto.redis_db.POPOTO_REDIS_DB.connection_pool.connection_kwargs`, enter and exit a full arena
   context including a spawned `arm_worker`, and assert the parent's kwargs are the identical dict.
   Under the subprocess design this assertion is honestly true rather than aspirational: nothing in
   the parent process ever rebinds the pool.

### Risk 2: The calibration reference set is too small to calibrate anything

**Impact:** `classify_correction` is precision-oriented and `ImprovementEvidence` expires at 30 days
(spike-4). If the `architectural` bucket holds a handful of rows, a Cohen's kappa computed over it is
noise wearing a number's clothes, and a judge reported as calibrated is worse than one reported as
uncalibrated.

**Mitigation:** A declared floor on the reference-set size, checked before any kappa is computed. Below
the floor the judge returns `infra_failure` and the evaluation says so. The floor is a named constant
with its rationale in the module docstring, and the build reports the observed set size on this
machine so the number is chosen against reality rather than guessed. The frozen artifact means the
set can be grown later without invalidating past calibrations.

### Risk 3: Blinding is claimed rather than achieved

**Impact:** The single most valuable property here is also the easiest to fake. A `blinded=True` that
nobody checked converts every downstream comparison into a measurement of the judge's expectations.

**Mitigation:** `blinded` is written only from the result of `scan_for_identity`, which runs over the
serialized envelope immediately before it is sent. The mutation proof is explicit in the Failure Path
table: hard-code `blinded=True` and `test_identity_leak_sets_blinded_false` must go red. The scan's
own token list is derived from the experiment record (branch, manifest surfaces, arm identity) rather
than hand-maintained, so a new identity-bearing field is covered without an edit.

### Risk 4: The lane ships before #3255 merges and builds on a moving head

**Impact:** `ImprovementCharter.digest`, `.text`, and `pinned()` are the surface the `serves_charter`
judge quotes. If lane 2b's re-review changes them, this lane's judge and its migration are built on
a shape that never landed.

**Mitigation:** This plan is written against `b05dde885` and says so in the Freshness Check. The build
does not start on the charter-consuming components until #3255 merges; the ordering is recorded in
No-Gos with an `[ORDERED]` tag. The parts that do not touch the charter — the arena, the corpus
export, Holm, the statistics, the parity gate — have no dependency on lane 2b and are the first tasks
in the task list precisely so the lane is not idle while it waits.

### Risk 5: `infra_failure` becomes the harness's excuse

**Impact:** A verdict vocabulary that includes "the harness broke" invites a harness that breaks
often and a loop that never learns anything. If most runs end in `infra_failure`, the separation from
`reject` has bought nothing.

**Mitigation:** `infra_failure` is raised from exactly six named conditions and from nowhere else,
each with its own test. The final catch-all handler in `runner.py` writes `infra_failure` with the
exception type in `notes`, so an unnamed cause is visible as an unnamed cause rather than blending
into the six. The dashboard renders `infra_failure` counts beside the others, which makes a rising
rate an observable fact rather than a suspicion.

### Risk 6: Adding `charter_digest` to an immortal record cannot be undone

**Impact:** `ImprovementEvaluation` has no TTL by design — the verdict and its lineage are the product
of the whole loop. A field added to it is permanent, and a wrong shape is permanent too.

**Mitigation:** The field is a plain nullable `Field`, not indexed, matching `charter_digest` on the
three records that already carry it (`ImprovementCase:117`, `ImprovementInvestigation:94`,
`ImprovementRelease:84`). It is added to `FORBIDDEN_INDEX_NAMES` so no later change can index it.
The migration is read-only and idempotent, following `_migrate_confirm_improvement_v2_fields` exactly:
it imports the model and runs one bounded project-scoped query to prove the keyspace resolves, and
writes nothing.

## Race Conditions

### Race 1: A second evaluation of the same experiment runs concurrently

**Location:** `tools/improvement_eval/runner.py::evaluate`
**Trigger:** Two callers invoke `evaluate()` for one `ImprovementExperiment` — an operator retry
alongside a scheduled tick, or two lanes both reacting to the same frozen experiment.
**Data prerequisite:** `ImprovementExperiment.state` must be `frozen` before either run begins, and
must reach `running` before the second caller reads it.
**State prerequisite:** Exactly one evaluation writes a verdict per experiment, or two verdicts
disagree and neither is authoritative.
**Mitigation:** The runner performs a read-modify-write of `state` from `frozen` to `running` as its
first write and refuses to proceed when the state it read was not `frozen`. That is a
compare-and-set in intent but not atomically — popoto offers no CAS and the improvement control
namespace is lane 3's to build (#3215). The honest disposition, matching
`ImprovementCharter.load_from_file`'s documented tolerated race, is: the window is small, the loser
writes an `infra_failure` evaluation naming the state it found rather than a competing verdict, and
`runner.py`'s docstring records that a real lease belongs to lane 3. A test drives the losing branch
directly by pre-setting `state="running"`.

### Race 1b: A crashed run leaves the experiment stuck in `running`

**Location:** `tools/improvement_eval/runner.py::evaluate`
**Trigger:** SIGKILL, a machine restart, or an unhandled crash between the `frozen` → `running` write
and the verdict write. For a run that spawns subprocesses and takes minutes this is the likelier of
the two state hazards, not the rarer one.
**Data prerequisite:** none.
**State prerequisite:** `EXPERIMENT_STATES` is `("proposed", "frozen", "running", "complete",
"aborted")` (`models/improvement_experiment.py:46`) and nothing in this lane ever writes `running`
back to `frozen`, so Gate 0 refuses every retry of that experiment forever.

**Disposition — no automatic reclaim in this lane, and the reason is a missing field, not an
oversight.** A reclaim predicate needs to distinguish "a run is alive" from "a run died", and the
only timestamp `ImprovementExperiment` carries is `frozen_at` (`:86`), which dates the freeze and not
the run. The honest options were to add a heartbeat field — a second permanent addition to an
immortal record, in a lane already making one, to serve a recovery path whose operator surface
belongs to lane 3 — or to keep the refusal and make the repair explicit and cheap. This lane keeps
the refusal.

**Mitigation:**
1. `runner.py`'s module docstring records the repair verbatim, through the ORM as CLAUDE.md requires
   and never through raw Redis:
   ```python
   e = ImprovementExperiment.query.filter(project_key=..., experiment_id=...).first()
   e.state = "frozen"
   e.save()
   ```
2. The same repair is written into `docs/features/improvement-evaluation.md` under a
   "Recovering a wedged experiment" heading, so an operator finds it without reading the source.
3. `test_crashed_run_leaves_a_documented_repair` pins the disposition rather than leaving it as
   prose: it pre-sets `state="running"`, asserts `evaluate()` returns `infra_failure` with the found
   state named in `notes` and writes no `accept`/`reject`, then applies the documented ORM repair and
   asserts the next `evaluate()` proceeds past Gate 0. The wedge and its exit both ship tested.
4. A heartbeat field and an automatic reclaim are named in No-Gos as lane 3's, so the deferral is a
   recorded decision rather than a gap.

### Race 2: The corpus is written while it is being exported

**Location:** `tools/improvement_eval/corpus.py::export_corpus`
**Trigger:** Memory extraction, an ingest, or a decay-prune tick writes a `Memory` row between the
exporter's first and last read.
**Data prerequisite:** The export must be a single consistent view, or the two arms are restored from
a corpus that never existed at any instant.
**State prerequisite:** The export's digest must identify the bytes both arms actually received.
**Mitigation:** The export's digest is computed over the exported bytes, and both arms are restored
from **those bytes**, not from a second read of the live corpus. A torn read therefore produces a
corpus that is internally odd but identical across arms, which preserves the property the comparison
actually needs. The exporter additionally records the record count and the export timestamp in the
artifact's provenance header, following `tools/memory_eval/snapshot.py`'s shape, so a torn export is
diagnosable after the fact. A run does not attempt to lock the live corpus: locking the production
memory corpus for the duration of an evaluation is a far larger hazard than a torn read the digest
already makes harmless.

### Race 3: An arm's `redis-server` outlives its context

**Location:** `tools/improvement_eval/arena.py`
**Trigger:** The arm body raises, the process is signalled, or the harness is killed mid-run.
**Data prerequisite:** none.
**State prerequisite:** No orphaned `redis-server` process holds a socket or a tmpdir after the run.
**Mitigation:** The arena is a context manager whose `finally` terminates the child and removes the
tmpdir, and the child is spawned in its own process group so a signal to the harness does not leave
it reparented and running. Because the socket lives inside the run's own tmpdir, an orphan that
somehow survives is unreachable rather than dangerous. Cleanup is never done by pattern-killing
`redis-server`: several agents run Redis on this machine and a pattern kill takes out their work.
A test asserts the child is gone after both the clean and the raising path.

## No-Gos (Out of Scope)

- [ORDERED] **Building the charter-consuming components before #3255 merges.** The `serves_charter`
  judge quotes `ImprovementCharter.text` and records `ImprovementCharter.digest`, both of which land
  in lane 2b (PR #3275, one re-review from merge). The human-gated event is that merge. The
  non-charter components (arena, corpus export, Holm, statistics, parity gate) have no such
  dependency and are sequenced first so the lane is never blocked as a whole.
- [SEPARATE-SLUG #3215] **The `valor-improve` operator surface for evaluations.** Running an
  evaluation from the command line, listing verdicts, and the break-glass pause belong to lane 3's
  CLI, which owns the control namespace and the budget settlement this harness draws against. This
  lane exposes `tools/improvement_eval/runner.py::evaluate` as an importable function with a
  documented signature and stops there.
- [SEPARATE-SLUG #3217] **Producing the first real experiment to evaluate.** Lane 5 runs the first
  complete research cycle and writes the first frozen contract. This lane's tests drive the harness
  from constructed fixtures, which is the correct dependency direction: the harness must be
  trustworthy before it is pointed at a result anyone wants.
- [SEPARATE-SLUG #3218] **Releases, exposure assignment, and rollback.** An `accept` verdict here
  produces evidence, not a release. Lane 6 owns `ImprovementRelease`, the observation window, and the
  rollback plan.
- [SEPARATE-SLUG #3218] **Automated promotion on an `accept` verdict.** The contract doc states that
  automated promotion is disabled and that no record enables it, pending separation of evaluator
  secrets from candidate execution and a human-amended charter naming reversible surfaces. Both are
  events outside this work.
- [SEPARATE-SLUG #3217] **Comparing full candidate *agent runs*.** This lane's arms compare retrieval
  over a frozen memory corpus. The isolation, blinding, parity, correction, and verdict-disjointness
  machinery is arm-shape-agnostic, and a paired agent-run arm is a much larger and much slower thing
  to build on top of it. Lane 5 runs the first complete research cycle and is where a larger arm
  belongs. Nothing in this lane's design forecloses it: the arm boundary is a subprocess with a JSON
  job spec.
- [SEPARATE-SLUG #3215] **A dashboard surface for evaluations.** `ui/data/improvement.py` has no
  evaluation surface and lane 3 owns the operator surface. This lane makes the semantics queryable
  (`has_verdict`, a distinct `infra_failure`, a non-null `blinded`) and stops there.
- [SEPARATE-SLUG #3215] **A run heartbeat and automatic reclaim of a wedged experiment.** Race 1b:
  reclaiming a `running` experiment needs a liveness timestamp `ImprovementExperiment` does not carry,
  and adding one is a second permanent field on an immortal record in service of a recovery path lane
  3 owns. This lane documents and tests the one-line ORM repair instead.
- **Alpha-spending stopping rules.** Not deferred to anyone: ruled out on the merits (research
  finding 3, Rabbit Holes). Fixed-batch is a complete, named stopping rule and it is what ships.
- **Copy-on-write arm isolation.** Ruled out on the merits by spike-3, not deferred.
- **A kappa gate on the `serves_charter` judge.** Ruled out on the merits: the literature's own
  conclusion is that an abstract threshold is the wrong bar. Kappa is measured, frozen, and cited;
  gating on it is not a follow-up promise but a thing this plan says should not be done yet.
- **Modifying `tools/memory_eval/metrics.py`.** Forbidden by acceptance criterion 7 and pinned by a
  Verification row that asserts the file is byte-identical to main.

## Update System

The `/update` skill needs one change and one only: the Popoto migration.

- [ ] Add `_migrate_improvement_evaluation_charter_digest` to `scripts/update/migrations.py` and
      register it in the `MIGRATIONS` dict. `run_pending_migrations()` iterates `MIGRATIONS`, so an
      unregistered function never runs. It is read-only and idempotent, following
      `_migrate_confirm_improvement_v2_fields` (`:1430`) exactly: import `ImprovementEvaluation`,
      run one bounded `query.filter(project_key="valor")[:1]` to prove the keyspace resolves under
      the new field, return None on success and the error string on failure. It writes nothing and
      is recorded once in `data/migrations_completed.json`.

Nothing else in the update path changes:

- **No new dependency to propagate.** Holm is pure Python and `redis-server` is already required
  (spike-1).
- **No new secret or config file.** The `serves_charter` judge's provider routing reuses the
  existing settings and the existing `is_open_source` guard; no `.env` key is added, so
  `.env.example` and `config/settings.py` are untouched and `tests/unit/test_env_completeness.py`
  stays green without edits.
- **No service restart.** `tools/improvement_eval/` is not imported by the bridge, the worker, or
  any agent code path, so `./scripts/valor-service.sh restart` is not required by this change.
- **`POPOTO_IMPROVEMENT_CONTENT_PATH`** already exists and already defaults to
  `data/improvement_content` inside the repo (`models/verifying_artifact_store.py:52-56`). The new
  artifacts this lane writes (corpus exports, calibration sets, raw judge responses) land under that
  root, so retention policy for them is already whatever lane 1 decided it was.

## Agent Integration

**No agent integration in this lane, deliberately, with one exception that is not an exception.**

`tools/improvement_eval/` is a harness, not an agent-reachable tool. Nothing in it should be
invocable by an agent mid-conversation: an evaluation takes minutes, spawns subprocesses, and writes
an immortal record. Exposing it as a CLI entry point in `pyproject.toml [project.scripts]` or as an
MCP tool would make it reachable by accident, and the operator surface for it belongs to lane 3's
`valor-improve` CLI, which owns the control namespace and the budget settlement (No-Gos,
`[SEPARATE-SLUG #3215]`). So:

- **No new `[project.scripts]` entry.** `runner.evaluate(experiment_id, project_key)` is an
  importable function with a documented signature and a docstring naming lane 3 as its operator
  surface. A Verification row asserts `pyproject.toml` gained no `improvement-eval` script.
- **No bridge import.** `bridge/telegram_bridge.py` does not reference this module and must not.
  A test asserts the import graph: nothing under `bridge/`, `worker/`, or `agent/` imports
  `tools.improvement_eval`.
- **No MCP surface.** No `mcp_servers/` entry. (This repo registers MCP servers as modules
  under `mcp_servers/`; there is no `.mcp.json` at the repo root, so there is nothing to edit
  there and the anti-criterion greps the directory that actually exists.)

The one genuine agent-facing change is `VALOR_PROJECT_KEY` in `_harness_env`
(`agent/session_executor.py:2116`), which is the opposite direction: it makes every harness
subprocess resolve its project partition correctly, including but not limited to an evaluation arm's.
Its integration test lives in `tests/integration/test_session_spawning.py` alongside the existing
`SESSION_TYPE` and `TELEGRAM_CHAT_ID` cases, asserting the variable reaches the subprocess env with
the resolved value rather than a fallback.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/improvement-evaluation.md` — it currently reads as a contract for work
      not yet done ("The harness itself, the frozen corpora, per-arm isolation, and the judge
      envelope arrive with lane 4"). Rewrite those passages to describe the shipped status quo: the
      module layout, the four verdicts and the invalidated state, the gate ordering, the named
      stopping rule, and the `serves_charter` judge with its calibration artifact. No historical
      narration, no "previously this document said" — describe only what is true after this lands.
- [ ] Add the isolation decision to that document: snapshot-and-restore with a writer kill switch,
      and why copy-on-write and shared-instance freeze were rejected (spike-3). The contract doc
      currently says "This lane decides which of the three it needs"; it should say which.
- [ ] Add a `## Calibration` section to `docs/features/improvement-evaluation.md` recording that the
      reference set is frozen to the verifying artifact store and cited by digest, why (the 30-day
      `ImprovementEvidence` TTL), what is reported (Cohen's kappa and paired position-swap
      consistency), and that no kappa threshold gates anything yet.
- [ ] Add a `## Recovering a wedged experiment` section to `docs/features/improvement-evaluation.md`
      giving the ORM repair verbatim (`e = ImprovementExperiment.query.filter(...).first();
      e.state = "frozen"; e.save()`), why no automatic reclaim ships in this lane
      (`ImprovementExperiment` carries no liveness timestamp — Race 1b), and that a heartbeat and an
      automatic reclaim belong to lane 3.
- [ ] Add an `## Arm isolation` subsection to `docs/features/improvement-evaluation.md` describing
      the subprocess boundary: `arena.py` spawns the server, `arm_worker.py` is the only process that
      talks to it, `REDIS_URL`/`POPOTO_CONTENT_PATH`/`VALOR_PROJECT_KEY` live in the child's env dict,
      and the corpus moves as popoto `export_records`/`import_records` JSONL. State plainly that the
      parent's canonical pool is never re-pointed, because that is the constraint a later reader is
      most likely to "simplify" away.
- [ ] Update `docs/features/improvement-controller.md` where it describes evaluation, so the two
      documents do not disagree about what exists.
- [ ] Verify `docs/features/README.md` already indexes `improvement-evaluation.md`; add the entry if
      it does not.
- [ ] Update `docs/plans/critiques/recursive-self-improvement-capability-matrix.md` rows that this
      lane moves from "planned" to "implemented" or "measured". The matrix's whole purpose is the
      distinction between implemented and measured; a row this lane makes *measurable* rather than
      merely present must say so.

### External Documentation Site

Not applicable — this repo has no Sphinx, Read the Docs, or MkDocs site.

### Inline Documentation

- [ ] `tools/improvement_eval/arena.py` module docstring records the unix-socket and `--port 0`
      decision and cites issue #2799 as the failure it avoids. This is the single most surprising
      choice in the lane and the one most likely to be "simplified" by a later reader.
- [ ] `tools/improvement_eval/arm_worker.py` module docstring states why the arm is a *process* and
      not a client: retrieval reads through the Popoto ORM against the process-global
      `POPOTO_REDIS_DB`, a bare `redis.Redis(unix_socket_path=…)` cannot answer `Memory.query`, and
      popoto binds its pool from `REDIS_URL` at import — so a child with its own `REDIS_URL` is the
      only isolation that leaves the parent's pool alone.
- [ ] `tools/improvement_eval/corpus.py` module docstring records that restore passes
      `on_embedding_mismatch="carry"` and relies on `import_records`' `skip_auto_now=True`, and that
      dropping either makes two arms rank differently even from identical bytes.
- [ ] `tools/improvement_eval/retrieval.py` module docstring records the `top_by_decay` ban with its
      reason (`now = time.time()` inside the call, no parameter to pin it).
- [ ] `tools/improvement_eval/correction.py` module docstring names the three Holm operations and
      records that the cumulative maximum is the known defect site.
- [ ] `tools/improvement_eval/calibration.py` module docstring records the frozen-set rationale and
      the reference-set floor with its number.
- [ ] `tools/improvement_eval/runner.py` module docstring enumerates the six `infra_failure`
      conditions, states that a real evaluation lease belongs to lane 3 (Race 1), and carries the
      verbatim ORM repair for an experiment wedged in `running` (Race 1b).
- [ ] `models/improvement_evaluation.py` class docstring gains `charter_digest` in the field list,
      with a sentence on charter §12's rule that actions complete under the digest they carry.

## Success Criteria

The seven acceptance criteria from issue #3216, unchanged, each with the artifact that proves it:

- [ ] **Two arms on private Redis processes produce byte-identical corpus reads** — each arm re-runs
      `export_records` against its own pool after restore and the digests are compared at run time
      (unequal digests end the run as `infra_failure`), pinned by
      `test_two_arms_read_a_byte_identical_corpus`. Byte identity alone is not the claim the criterion
      needs, so it is paired with `test_two_arms_rank_identically_across_a_clock_gap`, which queries
      the two arms with a deliberate wall-clock gap between them and asserts identical ranked ids —
      the reproducibility property that `skip_auto_now=True` on restore and the ban on
      `Query.top_by_decay` together buy.
- [ ] **Baseline retrieval parity holds on the frozen corpus, and a parity miss invalidates the run
      before any candidate result is read** — the gate compares ranked memory ids against a baseline
      record that stores the corpus digest it was captured under, and is pinned by
      `test_parity_miss_never_invokes_the_candidate_arm`, which asserts the candidate arm subprocess
      was never spawned, not merely that the outcome was invalid.
- [ ] **A corrupted artifact invalidates the evaluation rather than scoring it, proven by a mutation
      test that corrupts the archive copy specifically** — `test_corrupted_archive_invalidates_without_verdict`
      writes an artifact, corrupts `.versions/{prefix}/{hash}{ext}` while leaving the live path
      absent, and asserts `state="invalidated"`, `has_verdict()` False, and `verdict` not in
      `("accept", "reject")`.
- [ ] **Judges receive a blinded arm ID and `ImprovementEvaluation.blinded` reflects reality, proven
      by a test that fails if identity leaks into the envelope** —
      `test_identity_leak_sets_blinded_false` injects the candidate's branch name into the envelope
      and asserts `blinded=False`; its sibling asserts a clean envelope yields `blinded=True` only
      after the scan ran.
- [ ] **Holm correction is applied and named in `ImprovementEvaluation.correction`; a test shows an
      uncorrected run reporting a spurious winner and the corrected run not doing so** —
      `test_holm_suppresses_the_spurious_winner` runs a seeded null-effect family through both paths
      and asserts the uncorrected path reports at least one winner at alpha=0.05 while the corrected
      path reports none. `correction` is asserted to contain both the correction name and the
      stopping rule.
- [ ] **`infra_failure` and `reject` are produced by distinguishable conditions, with a test for
      each** — six named `infra_failure` conditions each get a test, `reject` gets one driven by a
      completed measurement that did not clear, and `test_infra_failure_and_reject_have_disjoint_causes`
      asserts no shared code path produces both.
- [ ] **`tools/improvement_eval/` imports `tools/memory_eval/metrics.py` and does not modify it** —
      `test_metrics_module_is_unmodified` compares the file's hash against `git show main:` and a
      Verification row runs `git diff --exit-code main -- tools/memory_eval/metrics.py`.

Plus the criteria this lane adds:

- [ ] `ImprovementEvaluation.charter_digest` exists, is populated by the runner from
      `ImprovementCharter.pinned()`, is never indexed (added to `FORBIDDEN_INDEX_NAMES`), and has a
      registered migration.
- [ ] The `serves_charter` judge quotes `ImprovementCharter.text` and records the digest it judged
      under; `SERVES_CHARTER_JUDGE_ID` is proven disjoint from `code-quality`, `risk`, and
      `cross-vendor`; its verdict is one input to the consensus envelope and gates nothing on its own.
- [ ] Charter §7 routing is honored in both directions: an open-source project may route the judge to
      any provider, a client project stays on the Claude and Codex subscriptions, both tested.
- [ ] The calibration reference set is frozen to the verifying artifact store and cited by digest;
      Cohen's kappa and a paired position-swap consistency figure are recorded; a set below the floor
      yields `infra_failure`.
- [ ] `VALOR_PROJECT_KEY` reaches the harness subprocess env with the resolved value, justified on
      its own merit: `tools/memory_search/__init__.py:62` and `reflections/redis_access.py:40` fall
      back to `"valor"` today, so every `claude -p` subprocess of a non-`valor` session uses the wrong
      memory partition.
- [ ] The arm reads its corpus through the Popoto ORM inside its own process, and the parent's
      canonical pool is provably unmoved — `test_parent_pool_kwargs_survive_an_arena_context`, plus
      the anti-criteria for `set_REDIS_DB_settings`, `os.environ["REDIS_URL"] =`, raw Popoto-key
      commands, and `top_by_decay`.
- [ ] An experiment wedged in `state="running"` by a crash has a documented, tested ORM repair
      (Race 1b), and no automatic reclaim ships in this lane.
- [ ] Every guard in the Failure Path mutation table has a recorded red-state proof.
- [ ] Tests pass (`/do-test`, via `scripts/pytest-clean.sh`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead orchestrates and never builds directly. The split below is by **file ownership**, not by
theme, because two builders converging on one file is how a lane livelocks.

### Team Members

- **Builder (arena and corpus)**
  - Name: `arena-builder`
  - Role: Owns `tools/improvement_eval/{corpus,arena,arm_worker,writer_guard,retrieval,errors}.py`
    and their tests. The isolation substrate, the arm subprocess, and the parity gate.
  - Agent Type: builder
  - Domain: Redis/Popoto data — arms must never touch popoto's canonical pool, `REDIS_URL`, or
    `tests/db_claim.py`; every corpus read goes through the arm's explicitly-constructed client.
  - Resume: true

- **Builder (statistics)**
  - Name: `stats-builder`
  - Role: Owns `tools/improvement_eval/{correction,statistics}.py` and their tests. Holm, the
    fixed-batch stopping rule, per-endpoint thresholds, clustered resampling.
  - Agent Type: builder
  - Resume: true

- **Builder (judges and blinding)**
  - Name: `judge-builder`
  - Role: Owns `tools/improvement_eval/{blinding,envelope,calibration}.py`,
    `tools/improvement_eval/judges/serves_charter.py`, and their tests.
  - Agent Type: builder
  - Domain: security/untrusted-input — a judge's response is untrusted data; every field is coerced
    with a typed fallback and an unparseable response is a skip, never a fabricated verdict.
  - Resume: true

- **Builder (records and environment)**
  - Name: `records-builder`
  - Role: Owns `models/improvement_evaluation.py`, `scripts/update/migrations.py`,
    `agent/session_executor.py`, and the four existing test files in Test Impact. The smallest
    surface and the only one that touches shared files, so it is deliberately one owner.
  - Agent Type: builder
  - Resume: true

- **Builder (runner)**
  - Name: `runner-builder`
  - Role: Owns `tools/improvement_eval/runner.py` and the end-to-end tests. Starts after the four
    component builders so it composes finished interfaces rather than negotiating them.
  - Agent Type: builder
  - Resume: true

- **Test engineer (mutation proofs)**
  - Name: `mutation-prover`
  - Role: Runs every row of the Failure Path mutation table in its **own worktree**, records the
    red-state output, reverts, and reports. Sole ownership of that checkout for the duration.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (harness)**
  - Name: `harness-validator`
  - Role: Read-only verification of the seven acceptance criteria and the Verification table.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `improvement-eval-docs`
  - Role: Executes every item in the Documentation section.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Arena, corpus export, writer kill switch, parity gate
- **Task ID**: build-arena
- **Depends On**: none
- **Validates**: `tests/unit/test_improvement_eval_arena.py` (create), `tests/unit/test_improvement_eval_corpus.py` (create)
- **Informed By**: spike-2 (private Redis must not touch `db_claim`; `redis-server` v8.10.1 present), spike-3 (snapshot-and-restore, not COW or shared-instance freeze)
- **Assigned To**: arena-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/improvement_eval/__init__.py` and `errors.py` with `InfraFailure`.
- `corpus.py`: `export_corpus(project_key)` calls `Memory.export_records(project_key=..., stream=fh)`, hashes the JSONL bytes, and writes them to the verifying artifact store with a provenance header (record count from the manifest's `matched_count`, ISO timestamp, git SHA) following `tools/memory_eval/snapshot.py`'s shape. `restore_corpus(jsonl_bytes)` calls `Memory.import_records(fh, on_conflict="overwrite", on_embedding_mismatch="carry")`. No raw Redis command anywhere in this module.
- `arena.py`: context manager spawning `redis-server --port 0 --unixsocket <tmp>/arm.sock --save '' --appendonly no --dir <tmp>` in its own process group; yields the socket path and the per-arm tmpdir; `finally` terminates the child and removes the tmpdir. It opens no Redis client of its own. Never import `tests.db_claim`, never assign `os.environ["REDIS_URL"]`, never call `set_REDIS_DB_settings`.
- `arm_worker.py`: `python -m tools.improvement_eval.arm_worker`, reading a JSON job spec on stdin and writing JSON on stdout. Modes: `restore`, `retrieve`, `digest`. Launched by `arena.py` with `env={**os.environ, "REDIS_URL": f"unix://{sock}", "POPOTO_CONTENT_PATH": ..., "VALOR_PROJECT_KEY": ..., "POPOTO_EMBEDDING_INVALIDATION": "none"}` — a dict for the call, never an assignment into the parent's environment.
- After restore, each arm re-runs `export_records` against its own pool and reports the digest; unequal digests raise `InfraFailure`.
- `writer_guard.py`: an ORM-level guard in the arm worker that refuses `Memory.save`/`Memory.delete` after restore, plus an independent corpus-digest re-check at arm teardown.
- `retrieval.py`: arm-scoped adapter over `agent.memory_retrieval.retrieve_memories`, returning ranked memory ids; `baseline_parity()` compares those ids to the recorded baseline captured under the same corpus digest; a miss raises `InfraFailure`. Never calls `Query.top_by_decay`.

### 2. Holm correction, stopping rule, statistics
- **Task ID**: build-stats
- **Depends On**: none
- **Validates**: `tests/unit/test_improvement_eval_correction.py` (create), `tests/unit/test_improvement_eval_statistics.py` (create)
- **Informed By**: spike-1 (no scipy/statsmodels; pure Python), research finding 1 (cumulative-max monotonicity is the defect site), research finding 3 (fixed-batch only)
- **Assigned To**: stats-builder
- **Agent Type**: builder
- **Parallel**: true
- `correction.py`: `holm_adjust(p_values)` as three named operations — sort ascending, multiply by `(m - j + 1)`, cumulative-max then clamp at 1.0 — then map back to the original input order. Raise `ValueError` on a p-value outside `[0, 1]`, on `None`, and on `NaN`.
- `correction.py`: `FixedBatchStoppingRule` with a declared batch size; `describe()` returns the exact string written to `ImprovementEvaluation.correction`, e.g. `"holm; fixed-batch(n=40, endpoints=3)"`.
- `statistics.py`: per-endpoint thresholds and clustered resampling by project over `bootstrap_ci` **imported** from `tools.memory_eval.metrics`. Do not edit that file.
- Tests pin monotonicity as a property, the worked example from research finding 1, and the seeded spurious-winner suppression.

### 3. Blinding, judge envelope, `serves_charter`, calibration
- **Task ID**: build-judges
- **Depends On**: none for `blinding.py` and `envelope.py`; the charter-quoting parts of `serves_charter.py` and `calibration.py` wait on #3255 merging (No-Gos, `[ORDERED]`)
- **Validates**: `tests/unit/test_improvement_eval_blinding.py` (create), `tests/unit/test_serves_charter_judge.py` (create), `tests/unit/test_improvement_eval_calibration.py` (create)
- **Informed By**: spike-4 (reference set decays; freeze it), spike-5 (copy `cross_vendor_judge.py`'s envelope shape), research finding 2 (Cohen's kappa plus paired position-swap)
- **Assigned To**: judge-builder
- **Agent Type**: builder
- **Parallel**: true
- `blinding.py`: seeded arm assignment with `arm_assignment_digest`; blinded ids; `scan_for_identity(serialized_envelope, experiment)` deriving its token list from the experiment record rather than a hand-maintained list.
- `envelope.py`: wrap the `judge_id`/`verdict`/`blockers`/`confidence` dict with experiment id, contract digest, charter digest, evaluator version, trial id, raw-response reference, blinded arm id. The inner dict stays consumable by `agent/sdlc_review_consensus.py::compute_consensus` unchanged.
- `judges/serves_charter.py`: `SERVES_CHARTER_JUDGE_ID = "serves-charter"`; status-discriminated envelope; every response field coerced with a typed fallback; prompt carries `ImprovementCharter.text` verbatim; provider chosen by `tools.improvement_eligibility.is_open_source`.
- `calibration.py`: read `ImprovementEvidence` rows classified `architectural`, freeze the set to the verifying artifact store, cite it by digest, compute Cohen's kappa and paired position-swap consistency, and raise `InfraFailure` below the declared floor. Report the observed set size on this machine so the floor is chosen against reality.

### 4. `charter_digest`, migration, `VALOR_PROJECT_KEY`
- **Task ID**: build-records
- **Depends On**: none (independent of #3255 — the field is additive and does not import the charter)
- **Validates**: `tests/unit/test_improvement_models.py`, `tests/unit/test_migrations.py`, `tests/unit/test_session_executor_extraction_decoupling.py`, `tests/integration/test_session_spawning.py`
- **Assigned To**: records-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `charter_digest = Field(null=True)` to `ImprovementEvaluation` with its docstring entry naming charter §12.
- Add `charter_digest` to `FORBIDDEN_INDEX_NAMES` in `tests/unit/test_improvement_models.py`.
- Add `_migrate_improvement_evaluation_charter_digest` to `scripts/update/migrations.py` and register it in `MIGRATIONS`; read-only and idempotent, mirroring `_migrate_confirm_improvement_v2_fields`.
- Add `"VALOR_PROJECT_KEY": <resolved>` to the `_harness_env` dict literal at `agent/session_executor.py:2116`, resolved through `config/project_key_resolver.py`.
- Update the four existing tests per Test Impact. Do not modify the correlation-id assertion.

### 5. Validate the components
- **Task ID**: validate-components
- **Depends On**: build-arena, build-stats, build-judges, build-records
- **Assigned To**: harness-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm each module's public interface matches what `runner.py` will compose.
- Confirm `tools/memory_eval/metrics.py` is byte-identical to main.
- Confirm nothing under `bridge/`, `worker/`, or `agent/` imports `tools.improvement_eval`.
- Confirm `arena.py` does not import `tests.db_claim` and does not assign `REDIS_URL`.

### 6. The runner
- **Task ID**: build-runner
- **Depends On**: validate-components
- **Validates**: `tests/unit/test_improvement_eval_runner.py` (create), `tests/integration/test_improvement_eval_end_to_end.py` (create)
- **Assigned To**: runner-builder
- **Agent Type**: builder
- **Parallel**: false
- Compose the gate order from Data Flow, exactly: contract-digest re-check, charter pin, corpus export, arm assignment, arena spawn plus digest comparison, writer guard, incumbent parity gate, paired trials, judges, statistics and Holm, stopping-rule check, verdict.
- Three disjoint handlers: `InfraFailure` → `verdict="infra_failure"`; `ArtifactIntegrityError` → `state="invalidated"` with no verdict written; a final catch-all → `infra_failure` with the exception type in `notes`. No shared fall-through.
- `has_verdict(evaluation)` returns True only for `state == "complete"`.
- Read-modify-write `ImprovementExperiment.state` from `frozen` to `running` as the first write; the loser writes an `infra_failure` evaluation naming the state it found (Race 1), and the docstring records that a real lease is lane 3's.
- Write the Race 1b crash disposition and its repair into the module docstring, and add `test_crashed_run_leaves_a_documented_repair`: pre-set `state="running"`, assert `infra_failure` with the found state in `notes` and no `accept`/`reject`, apply the documented ORM repair, assert the next `evaluate()` clears Gate 0.
- Spawn each arm through `arena.py` + `arm_worker.py`; never construct a Redis client in the runner and never re-point the parent's pool.

### 7. Mutation proofs
- **Task ID**: prove-guards
- **Depends On**: build-runner
- **Assigned To**: mutation-prover
- **Agent Type**: test-engineer
- **Parallel**: false
- Work in a dedicated worktree with sole ownership; no other agent edits that checkout for the duration.
- For each row of the Failure Path mutation table: apply the mutation, run the named test, record the failure output verbatim, revert, re-run, confirm green.
- Report any row where the test stayed green — that is a guard that reaches no code, and it blocks the lane.

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: build-runner
- **Assigned To**: improvement-eval-docs
- **Agent Type**: documentarian
- **Parallel**: true
- Execute every item in the Documentation section.

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: prove-guards, document-feature
- **Assigned To**: harness-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table.
- Confirm all seven issue acceptance criteria and the six added ones.
- Confirm every mutation row has a recorded red-state proof.
- Generate the final report.

## Verification

Anti-criteria use the `... | wc -l` shape rather than a bare `grep -rn`, because a `grep` that finds
nothing writes empty stdout and the `match count == 0` rule requires non-empty stdout. `wc -l` always
emits a line, so a clean tree reads as `0` rather than as an errored command. Every command below was
executed against the tree at plan time to confirm it runs and produces the shape claimed.

| Check | Command | Expected |
|-------|---------|----------|
| Harness unit tests pass | `scripts/pytest-clean.sh tests/unit/ -k improvement_eval -q` | exit code 0 |
| Judge and calibration tests pass | `scripts/pytest-clean.sh tests/unit/test_serves_charter_judge.py tests/unit/test_improvement_eval_calibration.py -q` | exit code 0 |
| Record and migration tests pass | `scripts/pytest-clean.sh tests/unit/test_improvement_models.py tests/unit/test_migrations.py -q` | exit code 0 |
| Harness env integration test passes | `scripts/pytest-clean.sh tests/integration/test_session_spawning.py -q` | exit code 0 |
| End-to-end evaluation test passes | `scripts/pytest-clean.sh tests/integration/test_improvement_eval_end_to_end.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/improvement_eval/ models/improvement_evaluation.py scripts/update/migrations.py agent/session_executor.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/improvement_eval/ models/improvement_evaluation.py` | exit code 0 |
| `metrics.py` unmodified (criterion 7) | `.venv/bin/python -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('tools/memory_eval/metrics.py').read_bytes()).hexdigest())"` | output is `424dbce86534f955d39f2e56be92ba40b090172045b81e06e6dd8241a84702e7` |
| `metrics.py` unmodified — human cross-check | `git diff --exit-code origin/main -- tools/memory_eval/metrics.py` | exit code 0 |
| `metrics.py` is imported (criterion 7) | `grep -rE "from tools\.memory_eval(\.metrics)? import" tools/improvement_eval/ \| wc -l` | output > 0 |
| `charter_digest` on the evaluation record | `python -c "from models.improvement_evaluation import ImprovementEvaluation as E; assert hasattr(E,'charter_digest')"` | exit code 0 |
| `charter_digest` pinned as never-indexed | `grep -c '"charter_digest"' tests/unit/test_improvement_models.py` | output > 0 |
| Migration function registered in `MIGRATIONS` | `.venv/bin/python -c "from scripts.update.migrations import MIGRATIONS; print(any('improvement_evaluation_charter_digest' in k or 'improvement_evaluation_charter_digest' in getattr(v[0],'__name__','') for k,v in MIGRATIONS.items()))"` | output contains `True` |
| `VALOR_PROJECT_KEY` in `_harness_env` | `grep -c '"VALOR_PROJECT_KEY"' agent/session_executor.py` | output > 0 |
| Judge id disjoint from the existing roster | `python -c "from tools.improvement_eval.judges.serves_charter import SERVES_CHARTER_JUDGE_ID as s; from tools.cross_vendor_judge import CROSS_VENDOR_JUDGE_ID as c; assert s not in {c,'code-quality','risk'}"` | exit code 0 |
| §7 guard is called, not reimplemented | `grep -r "is_open_source" tools/improvement_eval/ \| wc -l` | output > 0 |
| Anti-criterion: arena never touches the db-claim pool | `grep -r "db_claim" tools/improvement_eval/ \| wc -l` | match count == 0 |
| Anti-criterion: the parent never reassigns `REDIS_URL` | `grep -rE "os\.environ\[[\"']REDIS_URL[\"']\][[:space:]]*=\|os\.environ\.setdefault\([[:space:]]*[\"']REDIS_URL\|putenv\([[:space:]]*[\"']REDIS_URL" tools/improvement_eval/ \| wc -l` | match count == 0 (the arm's `REDIS_URL` is a **key in a subprocess env dict**, which this regex deliberately permits and the old `REDIS_URL[^\"]*=` form would have flagged) |
| Anti-criterion: the parent's canonical pool is never re-pointed | `grep -rE "set_REDIS_DB_settings" tools/improvement_eval/ \| wc -l` | match count == 0 |
| Anti-criterion: retrieval never ranks through the unpinnable decay clock | `grep -rE "top_by_decay" tools/improvement_eval/ \| wc -l` | match count == 0 |
| Anti-criterion: no raw Redis command on Popoto-managed keys | `grep -rE "\.(hgetall\|hget\|hmget\|hscan\|scan_iter\|zadd\|zrem\|sadd\|srem)\(" tools/improvement_eval/ \| wc -l` | match count == 0 |
| Corpus transfer goes through the ORM API | `grep -rE "export_records\|import_records" tools/improvement_eval/ \| wc -l` | output > 0 |
| The arm subprocess carries its own content path | `grep -c "POPOTO_CONTENT_PATH" tools/improvement_eval/arena.py` | output > 0 |
| Anti-criterion: no bridge/worker/agent import of the harness | `grep -rE "tools[./]improvement_eval" bridge/ worker/ agent/ \| wc -l` | match count == 0 |
| Anti-criterion: no CLI entry point added | `grep -cE "improvement.eval" pyproject.toml` | match count == 0 |
| Anti-criterion: no MCP surface added | `grep -rE "improvement.eval" mcp_servers/ \| wc -l` | match count == 0 |
| Anti-criterion: no swallowed exceptions | `grep -rA1 -E "except [A-Za-z]+(Error\|Exception)?:" tools/improvement_eval/ \| grep -cE "^[^:]*[-:][[:space:]]*pass$"` | match count == 0 |
| Anti-criterion: no alpha-spending shipped | `grep -rniE "alpha.spending\|obrien\|pocock\|lan.demets" tools/improvement_eval/ \| wc -l` | match count == 0 |
| Anti-criterion: no automated promotion | `grep -r "ImprovementRelease" tools/improvement_eval/ \| wc -l` | match count == 0 |
| Anti-criterion: harness never writes the charter | `grep -rE "ImprovementCharter[^)]*\.(save\|create\|delete)\(" tools/improvement_eval/ \| wc -l` | match count == 0 |
| Anti-criterion: no broad process kill in arena teardown | `grep -rE "pkill\|killall" tools/improvement_eval/ \| wc -l` | match count == 0 |
| Anti-criterion: no `scipy` or `statsmodels` dependency | `grep -rE "scipy\|statsmodels" tools/improvement_eval/ pyproject.toml \| wc -l` | match count == 0 |
| No stale xfails in this lane's tests | `grep -rn 'xfail' tests/ \| grep -i improvement_eval` | exit code 1 |

## Critique Results

Round 2 — FULL roster (Risk & Robustness, Scope & Value, History & Consistency), sequential lenses (Agent tool unavailable: not in tool list), plus automated structural checks, over the revised plan at `631ff2dd0`. **NEEDS REVISION**: 1 blocker, 4 concerns, 2 nits. No finding was independently corroborated, because the lenses ran in sequence.

Round 1 recorded 1 blocker, 6 concerns and 1 nit; **all eight were re-verified closed this round** against `origin/main`, `b05dde885` and the installed venv. Specifically: the subprocess arm route is real (`popoto/redis_db.py` binds `POPOTO_REDIS_DB` from `REDIS_URL` at import; `BlockingConnectionPool.from_url("unix://…")` resolves to a `UnixDomainSocketConnection`, confirmed live); the corrected `REDIS_URL` anti-criterion regex was mutation-tested and bites on all three forbidden assignment forms while permitting the dict-literal key; `import_records` saves with `skip_auto_now=True` (`popoto/transfer/import_.py:252`) and popoto documents that flag as the transfer driver's timestamp-preservation mechanism, so the stored relevance scores `agent/memory_retrieval.py:117` reads do carry across a restore; the migration registry row runs and prints `False`, the correct pre-build shape; `EXPECTED_METRICS_SHA256` matches `tools/memory_eval/metrics.py` on `origin/main`, on `b05dde885` and in the worktree; and the judge-disjointness citation `:675`/`:679` is correct on both refs while round 1's `:677`/`:681` was not.

Structural checks: all four repo-mandated sections present and substantive; task numbering 1 through 9 contiguous with an acyclic `Depends On` graph over real task ids; 7 of 8 Prerequisites pass on this machine (`redis-server` v8.10.1, Python 3.14.6 on the `.python-version` pin, `numpy` 2.4.4, artifact store writable at `data/improvement_content`, `bootstrap_ci` importable, `Memory.export_records`/`import_records` present, the `unix://` pool path resolves) and the widened lane-2b row fails as designed until #3275 merges. Cross-reference check passes: the Error State Rendering items now map to runner tests rather than to unowned dashboard work. One cited path does not resolve (`docs/plans/completed/hybrid-retrieval-eval.md`, nit 2), which round 1's structural pass reported as clean.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness | `Model.export_records` output is non-deterministic across processes, so the run-time corpus-identity gate installed by the round-1 revision fires `infra_failure` on every run with more than one record. Two measured sources: (1) record order — `popoto/transfer/export.py` sorts the key set once, then passes each chunk to `Query.get_many_objects(model_class, set(chunk))`, which with `order_by_attr_name=None` never re-orders and issues one pipelined hash read per key straight off the **set**, so record order is Python set-iteration order over `str` keys, randomized per process by PYTHONHASHSEED (pinned nowhere in this repo); (2) the manifest's `exported_at`, a fresh ISO timestamp on every call, which differs even within one process at a fixed seed. Measured on a private `redis-server --port 0 --unixsocket` holding 40 ORM-restored `Memory` rows — the exact arm shape Data Flow step 5 specifies — four separate export processes over one unchanged corpus produced four different sha256 (`6d85206b…`, `c16fe61c…`, `de406345…`, `06551822…`) over an identical 25019-byte payload; a structural diff of two exports showed exactly one differing field, `exported_at`. The plan makes this comparison the run-time proof of acceptance criterion 1 and pins it with `test_two_arms_read_a_byte_identical_corpus`, so as specified the gate is red on every real corpus and the test cannot go green — and the cheapest field repair is to weaken the gate, the outcome this lane exists to prevent. | pending | Specify a canonical digest function in `corpus.py` and use it for the original export, for both arms' re-exports, and for the artifact-store corpus identity — never raw JSONL bytes. Verified stable across four separate processes on the live arm fixture: take `lines = jsonl_text.splitlines()`, `manifest = json.loads(lines[0])`, `manifest.pop("exported_at", None)`, then hash `json.dumps(manifest, sort_keys=True)` joined by a newline to `"\n".join(sorted(lines[1:], key=lambda line: json.loads(line)["key"]))` — four runs returned `8e6bd6c1c0137ee6602d44f722d6ba05a9de7062c440801fe020fff4eae71e25` every time, where the raw bytes returned four different digests. Two guards: pop `exported_at` **by name** and assert the remaining manifest is byte-equal between arms, so a future volatile manifest key surfaces as a mismatch rather than being absorbed by a broad allowlist; and do not "fix" this by exporting once and reusing the bytes, because the arms' independent re-export is the gate. Pinning `PYTHONHASHSEED` in the child env is not a fix either — it removes source (1) and leaves source (2), as measured. |
| CONCERN | Risk & Robustness | Two of the three mutation rows naming `test_two_arms_rank_identically_across_a_clock_gap` cannot be detected by that test, because both arms suffer the mutation symmetrically. The test asserts the two arms agree with **each other**. Dropping `skip_auto_now` re-stamps `Memory.relevance` at import time in both arms, which import the same JSONL in the same order, so the relative ordering matches and the test stays green; dropping `on_embedding_mismatch="carry"` re-embeds identical content in both arms, producing identical vectors and identical rankings, also green. Only the `top_by_decay` row is genuinely asymmetric, and only if the deliberate clock gap is large against a decay scale that is per-day (`base_score * elapsed_days ** (-decay_rate)`) — a sleep short enough to live in a test suite moves nothing. All three rows therefore risk being guards that reach no code, the exact failure Task 7 exists to catch and which the plan says blocks the lane. | pending | Re-point the "Relevance carry-over" and "Corpus restore fidelity" mutation rows at the baseline parity gate (`test_parity_miss_never_invokes_the_candidate_arm`, or a new `test_restore_without_skip_auto_now_fails_baseline_parity`): the recorded baseline stores ranked ids captured under the pre-restore corpus, so a re-stamped `relevance` changes the ranking relative to the **baseline** even when the two arms still agree with each other. For the clock-gap row do not use `time.sleep` — patch the clock the mutant would read (`popoto.models.query.time.time` returning `now + 30*86400`) so a `top_by_decay`-ranked implementation reorders by days while the `retrieve_memories` path, which reads stored `zrevrange` scores at `agent/memory_retrieval.py:117`, does not move. Record the red output for each of the three rows separately; one shared test going red for one mutation is not evidence for the other two. |
| CONCERN | History & Consistency | The ORM repair the plan commits to publishing verbatim in three places does not run. Race 1b mitigation step 1 filters `ImprovementExperiment` on `experiment_id`, which is not a field on that model: `ImprovementExperiment` carries `id = AutoKeyField()` and `project_key` (`models/improvement_experiment.py:78-88` on `b05dde885`), while `experiment_id` belongs to `ImprovementEvaluation`. Popoto refuses unknown filter parameters rather than ignoring them — executed live in this venv, `ImprovementExperiment.query.filter(project_key='…', experiment_id='x')` raises `QueryException: Invalid filter parameters: experiment_id`. This snippet is the entire disposition that closed round-1 concern 2 (no automatic reclaim, on the grounds that the manual repair is explicit and cheap), it is slated for `runner.py`'s docstring and for `docs/features/improvement-evaluation.md`, and `test_crashed_run_leaves_a_documented_repair` applies it — so the break-glass procedure raises and the test fails on its second half for a reason unrelated to the behaviour it pins. | pending | The working form is `e = ImprovementExperiment.query.filter(project_key=project_key, id=experiment_id).first()` — `id`, not `experiment_id`. `ImprovementExperiment.id` is an `AutoKeyField` and `project_key` is a `KeyField`, so this is a key-only lookup with no index dependency. Keep `evaluate(experiment_id, project_key)` as the function's parameter name; only the filter kwarg changes. Fix it in all three places (Race 1b step 1, the Documentation checkbox, Task 6). To stop the copies drifting again, have `test_crashed_run_leaves_a_documented_repair` execute the snippet extracted from `runner.__doc__` (a fenced block located by its heading) rather than a hand-copied duplicate, so a docstring that stops running turns the test red. |
| CONCERN | History & Consistency | The brief handed to the builder who owns the isolation substrate still describes the design the round-1 blocker fix overturned, and contradicts it twice in one sentence. Team Orchestration's "Builder (arena and corpus)" Domain line reads "arms must never touch popoto's canonical pool, `REDIS_URL`, or `tests/db_claim.py`; every corpus read goes through the arm's explicitly-constructed client." Under the chosen design the arm **does** touch popoto's canonical pool — that is the mechanism ("inside the child, and only inside the child, the canonical pool *is* the arm's private server") — and it does carry `REDIS_URL`, in the child's env dict. The plan's own spike-2 revision note says the "explicitly constructed client" conclusion "was wrong about the second half", and the Technical Approach states that no bare client is ever asked to answer `Memory.query` and that `arena.py` opens no Redis client of its own. This is the one place still directing the assigned builder to build the ruled-out design, and the Domain line does not mention `arm_worker.py` at all. | pending | Replace the Domain sentence with: "Redis/Popoto data — the **parent** process must never re-point popoto's canonical pool, assign `os.environ['REDIS_URL']`, or import `tests/db_claim.py`. The arm is a child process whose own `REDIS_URL` (a key in the `subprocess.run(env=…)` dict, never an assignment) binds popoto's canonical pool to the arm socket at import; inside that child every corpus read goes through the ORM. No bare `redis.Redis` client answers `Memory.query` on either side." The Role line already lists `arm_worker.py` among the owned files, so only the Domain sentence changes. This matters beyond tidiness: the three Risk 1 tests assert **parent-side** properties, and a builder working from the current Domain line would read them as arm-side prohibitions and build the client it has been told to build. |
| CONCERN | Scope & Value | Risk 4's mitigation claims the lane will not idle while it waits on #3255, but the dependency graph does not deliver that. Task 3 (`build-judges`) bundles the charter-free modules (`blinding.py`, `envelope.py`) with the charter-quoting ones (`serves_charter.py`, `calibration.py`) under one task id whose `Depends On` line says the charter parts wait on #3255 merging. Task 5 depends on `build-judges`, task 6 on task 5, and tasks 7, 8 and 9 on task 6 — so a single external merge event gates five of the nine tasks, including the runner that composes everything else and every mutation proof. Open Question 2 states the same problem as a question, and no task, criterion or No-Go records a path for either answer, leaving a Large lane's completion pinned to another lane's re-review with the decision to be made under exactly the deadline pressure Open Question 2 names as the thing to avoid. | pending | Split task 3 by dependency rather than by owner: task **3a** `build-blinding` (`blinding.py`, `envelope.py`; `Depends On: none`; validated by `tests/unit/test_improvement_eval_blinding.py`) and task **3b** `build-charter-judge` (`judges/serves_charter.py`, `calibration.py`; `Depends On: none, gated on #3255 merging`; validated by `tests/unit/test_serves_charter_judge.py` and `tests/unit/test_improvement_eval_calibration.py`). Task 5 then depends on `build-arena, build-stats, build-blinding, build-records`, dropping `build-judges`, and 3b joins before task 9. Both stay assigned to `judge-builder`, so the file-ownership split is unchanged and only the dependency edges move. Note that `envelope.py` carries a charter-digest field, so 3a must accept the digest as an opaque caller-supplied string and must not import `models.improvement_charter`, or the split is nominal and 3a is still blocked. Add one line to Risk 4's mitigation naming 3a/3b so the claim and the graph agree. |
| NIT | Risk & Robustness | The arm socket lives "in a per-arm tmpdir" with no length guard. `AF_UNIX` `sun_path` is 104 bytes on this platform, and redis-py surfaces an over-long path as a bare `redis.exceptions.ConnectionError: Error AF_UNIX path too long` raised inside the child at import — observed in this venv during the arm spike. That reaches the runner as an opaque subprocess failure rather than a named `InfraFailure`, and it is path-length dependent, so it appears on some checkouts and not others. Assert the socket path length in `arena.py` before spawning `redis-server` and raise `InfraFailure` naming the measured length and the limit. | pending | (nit — no implementation note required) |
| NIT | History & Consistency | Two accuracy slips in claims the revision leans on. Prior Art cites `docs/plans/completed/hybrid-retrieval-eval.md`; that directory does not exist — the file moved to `docs/archive/plans-completed/hybrid-retrieval-eval.md` in commit `659f1d0e4` (#2942), and round 1's structural pass reported every cited path as resolving. And Data Flow step 3 says `export_records` carries "BM25 posting data"; the export manifest's own field-policy roll-up, read off a live export in this venv, records `bm25` as `policy: "rebuild"` and `bloom` as `policy: "partial"` with the note "not carried or rebuilt by import; see #556". BM25 is rebuilt from content at import rather than carried — still deterministic across two arms restored from identical bytes, so the design survives, but the sentence explaining why it survives is wrong. `relevance` and `confidence` do travel in the record's `values`/`state` as the plan needs. Fix the Prior Art path and restate step 3 in terms of each field's declared roundtrip policy. | pending | (nit — no implementation note required) |

---

## Open Questions

1. **Where does the calibration reference-set floor sit?** The build reports the observed count of
   `ImprovementEvidence` rows classified `architectural` on this machine. If that number is in the
   low teens, a floor that respects it is a floor that never blocks anything, and a floor that
   respects the statistics blocks the judge permanently until lane 5 generates more corrections.
   The plan's current disposition is to set the floor from the measured number and record the
   reasoning in the module docstring, accepting that early calibrations are weak and honestly
   labelled. Confirm that is the intended tradeoff rather than gating the judge until the set grows.

2. **Should the `serves_charter` judge ship in this lane at all if #3255 slips?** The lane's other
   six acceptance criteria have no dependency on lane 2b. If PR #3275's re-review drags, the honest
   options are to ship lane 4 without the judge and file it as a follow-up, or to hold the whole
   lane. The plan currently sequences the judge last so either choice stays available; naming the
   preference now avoids a decision made under deadline pressure later.

3. **Is a fixed-batch stopping rule sufficient for the loop's first year?** Research finding 3 says
   alpha-spending is the strictly harder commitment and needs a maximum sample size nobody can yet
   choose. That reasoning holds today. It stops holding the moment the loop wants to run an
   experiment long enough that waiting for a full batch is the dominant cost. Confirm fixed-batch is
   accepted as this lane's complete answer rather than a stepping stone with an implied follow-up.
