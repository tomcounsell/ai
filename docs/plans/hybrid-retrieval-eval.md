---
status: Ready
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-07-14
tracking: https://github.com/tomcounsell/ai/issues/2082
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-14T06:25:12Z
---

# Evaluate popoto 1.8.0 Hybrid (BM25+vector) Retrieval for Memory Recall

## Problem

popoto 1.8.0 (merged in PR #2081, `6c243ebc`) gave `ContextAssembler` first-class
hybrid BM25+vector retrieval via `retrieval_mode='auto'`. The `Memory` model already
carries both a `BM25Field` and a `GracefulEmbeddingField`, so `auto` resolves to
`hybrid` for us with zero schema work. But nobody has measured whether hybrid retrieval
actually recalls *better* memories than the recall path we run in production today.

**Current behavior:**
Memory recall runs `agent/memory_retrieval.py::retrieve_memories` — a home-grown
**four-signal Reciprocal Rank Fusion** (BM25 keyword + temporal decay + confidence +
embedding cosine). popoto's `ContextAssembler` is invoked at exactly one place
(`tools/memory_search/__init__.py:193`) and only as a non-fatal *quality probe*
(`assembler.assess(...)`); it never drives which memories surface. So "hybrid retrieval
is technically available" and "hybrid retrieval measurably helps" are two different
claims, and the gap between them is unmeasured.

**Desired outcome:**
A defensible, reproducible measurement of hybrid (`ContextAssembler`, BM25+vector)
vs. the current four-signal RRF path on the real `valor` memory corpus, plus a clear
written verdict. **Adoption is conditional on that verdict.** "Measured, hybrid does
not beat current — no recall-path change" is a fully successful outcome. The plan
must not force a recall-path rewrite the evaluation doesn't justify.

> **This is a spike, not a guaranteed build.** Phase 1 (methodology + harness +
> measurement + write-up) is the required, must-do deliverable. Phase 2 (adoption)
> is a conditional branch gated on the Phase 1 decision gate below.

## Freshness Check

**Baseline commit:** `6c243ebc` (popoto 1.7.1 → 1.8.0 upgrade, PR #2081)
**Issue filed at:** 2026-07-14T05:55:22Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/memory_search/__init__.py:193` — sole `ContextAssembler` call site, `assess`-only quality probe — **still holds** (verified live; `grep -rn ContextAssembler tools/ agent/` → single hit).
- `agent/memory_retrieval.py:235-363` — `retrieve_memories`, four-signal RRF fusion — **still holds**.
- `models/memory.py:157-158` — `bm25 = BM25Field(source="content")`, `embedding = GracefulEmbeddingField(source="content")` — **still holds**.
- `.venv/.../popoto/recipes/context_assembler.py:856,928-955` — `retrieval_mode='auto'` param; `auto`→`hybrid` iff BM25Field+EmbeddingField present — **verified in installed popoto 1.8.0**.

**Cited sibling issues/PRs re-checked:**
- #2080 / PR #2081 — merged `6c243ebc`; upgraded popoto but explicitly did not touch the recall path. This issue is its named follow-on.

**Commits on main since issue was filed (touching referenced files):** None. `git log --since=2026-07-14T00:00:00Z` on the three files above is empty.

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** The issue's "BM25+RRF" shorthand for the current path is imprecise: the
production path is **four-signal** RRF and already fuses a vector (embedding-cosine)
signal. The genuine question is whether popoto's 2-signal hybrid (BM25+vector, plus
graph propagation) beats our 4-signal fusion — not "add vectors to a lexical-only
system." The methodology is designed around that corrected framing.

## Prior Art

No prior issues or merged PRs attempt a hybrid-retrieval evaluation for memory recall.
`gh issue list --state closed --search "hybrid retrieval memory"` and
`gh pr list --state merged --search "ContextAssembler retrieval_mode"` return nothing
relevant. The existing four-signal RRF fusion was built organically (see
`agent/memory_retrieval.py` docstring) with no recorded A/B against alternatives — this
spike is the first controlled comparison.

## Research

**Queries used:**
- popoto 1.8.0 ContextAssembler retrieval_mode auto hybrid BM25 vector (verified against installed source, not web)
- known-item retrieval evaluation methodology / pooled relevance judgments nDCG

**Key findings:**
- popoto 1.8.0 `ContextAssembler(model_class, score_weights, ..., retrieval_mode='auto')`
  resolves `auto`→`hybrid` only when the model has BOTH `BM25Field` and `EmbeddingField`
  (`context_assembler.py:928-955`). `Memory` qualifies. Forcing `retrieval_mode='hybrid'`
  raises `QueryException` if either field is missing — not our case.
- Retrieval is driven via `assembler.assemble(query_cues=<dict>, partition_filters=<dict>)`
  → `AssemblyResult.records` (selected instances) + `.metadata` (per-record scores, timing).
  `assemble(assess_quality=True)` optionally attaches a `RetrievalQuality`. The hybrid pull
  path fuses BM25 + vector via RRF before graph propagation (`context_assembler.py:133,796`).
  **Caveat (not yet exercised — see BLOCKING spike-1):** the only real call site
  (`tools/memory_search/__init__.py:193`) uses `.assess()`, not `.assemble()`. The
  record-selecting `.assemble()` path is asserted from source reading, **not** from a live call,
  so spike-1 must confirm it returns records before the comparison run. `.assess()` scores
  quality and does NOT select records; it is **not** a valid retrieval fallback.
- `query_cues` is a **dict of cues**, not a bare string — the harness must discover the
  correct cue shape for `Memory` (spike-1). Getting this wrong silently under-retrieves and
  would bias the comparison against hybrid.
- **Known-item retrieval** (generate a query whose gold answer is one specific memory) is the
  standard cheap, objective way to bootstrap ground truth without hand-labeling a large set;
  **pooled LLM-judged relevance** (judge the union of both systems' top-k, graded 0-3) is the
  standard way to capture partial relevance and compute nDCG without favoring either system.
  The plan uses both, complementarily.

## Spike Results

<!-- Populated by Phase 1.5 spikes during plan execution. The harness build depends on these. -->

### spike-1: Correct `query_cues` shape for `Memory.assemble()` (BLOCKING — addresses Concern 3)
- **Assumption**: "`ContextAssembler(Memory, ...).assemble(query_cues=<dict>)` returns `AssemblyResult.records` (selected instances) for a query-text cue that exercises the BM25+vector hybrid pull path."
- **Method**: prototype (worktree, read-only against a throwaway `dbg-` project clone)
- **Agent Type**: builder in worktree
- **Time cap**: 10 min
- **Result**: _[filled during execution]_
- **Confidence**: _[filled]_
- **This spike is BLOCKING.** It must be RESOLVED (assemble() confirmed to return records for a text cue on the real corpus) **before** the full comparison run — the harness cannot compute recall@k / MRR until record *selection* via `assemble()` is proven.
- **Invalid fallback explicitly forbidden.** The Research section previously implied an
  `assess()`-scored-sets fallback. That is **invalid**: `assess()` scores retrieval *quality*,
  it does not *select* records — recall@k / MRR computed from an assess score is meaningless.
- **Impact if false**: If `assemble()` cannot be driven with a text cue against `Memory`, the
  harness drops to calling the **lower-level hybrid pull path** (BM25+vector RRF, before graph
  propagation — `context_assembler.py:133,796`) to obtain a real record list. If neither
  `assemble()` nor the lower-level pull yields records, the harness **ABORTs** (non-zero exit,
  documented finding). It MUST NOT silently fall back to `assess()`. This changes the harness's
  retrieval adapter, not the metrics.

### spike-2: Read-only guarantee (no `access_count` / outcome-tracking mutation)
- **Assumption**: "Both retrieval paths can be exercised without mutating production `Memory` state (`access_count`, relevance decay, outcome history)."
- **Method**: code-read + prototype (assert `access_count` unchanged before/after a harness query on a `dbg-` clone)
- **Agent Type**: builder in worktree
- **Time cap**: 10 min
- **Result**: _[filled during execution]_
- **Confidence**: _[filled]_
- **Impact if false**: If `retrieve_memories` / `assemble` bump `access_count` via `AccessTrackerMixin`, the harness MUST run against a cloned throwaway project partition, never `valor` directly — hardens the Race Conditions mitigation below.

### spike-3: Embedding coverage on the eval subset
- **Assumption**: "Enough `valor` memories have materialized embeddings to make the hybrid vector signal meaningful, or a bounded backfill can raise coverage for the eval subset."
- **Method**: code-read (`EmbeddingField.load_embeddings(Memory)` count) + measure
- **Agent Type**: Explore / builder
- **Time cap**: 5 min
- **Result**: recon measured ~212 / 1170 valor records embedded (~18%). _[confirm exact embedded-subset count during execution]_
- **Confidence**: medium
- **Impact if false**: If coverage is too low, the eval's **primary** comparison runs on the embedded subset (where hybrid can actually differ), with a bounded pre-eval backfill option; whole-corpus results are reported as secondary/context.

## Data Flow

The evaluation harness is a read-only offline pipeline; it does not sit in the live recall path.

1. **Entry point**: `python -m tools.memory_eval.hybrid_eval --project valor [--k 10] [--backfill-embeddings]` (dev-invoked script; `--backfill-embeddings` is opt-in and off by default — the default run is read-only on the already-embedded subset). **First action: the embedding-provider hard gate** — assert `get_default_provider() is not None`; if the provider is down, raise and exit non-zero before constructing any query set (a degraded provider would silently collapse hybrid to BM25-only; see Technical Approach Concern 1).
2. **Corpus snapshot**: read `Memory.query.filter(project_key='valor')` into an in-memory eval set (optionally cloned into a throwaway `dbg-hybrideval` partition so retrieval side effects never touch `valor`).
3. **Query-set construction**:
   - **Known-item set (REQUIRED, drives the gate)**: for a sample of high-importance / embedded memories, generate (via the repo's PydanticAI LLM path) a natural-language query whose gold-relevant answer is that memory. The (query → gold memory_id) pair is the objective label. This set is the sole gate driver.
   - **Pooled-judgment set (CONDITIONAL, corroboration only)**: constructed **only when the known-item result is near the threshold** (`abs(mean_recall_gain − HYBRID_EVAL_MIN_RECALL_GAIN) < HYBRID_EVAL_MIN_RECALL_GAIN`). When triggered, run both retrievers, pool the union of top-k, LLM-judge each (query, memory) pair on a 0-3 graded scale; judgments feed nDCG. A comfortable clear or clear miss skips this step (see Technical Approach Concern 4).
4. **Dual retrieval (two arms)**: for each query, run (a) current path `retrieve_memories(query, project_key, limit=k)` and (b) hybrid path `ContextAssembler(Memory, ..., retrieval_mode='hybrid').assemble(query_cues=...)`. Both read-only. The `auto` selector is validated by a **single assertion** that it resolves to `hybrid` for `Memory` (not a third measurement arm — see NIT). Per embedded-subset query, assert the hybrid arm's `.metadata` shows non-zero vector contribution; a zero-vector/degraded result aborts the run (Concern 1).
5. **Scoring**: compute recall@k, precision@k, MRR (known-item set); bootstrap a 95% CI on the paired per-query recall deltas and emit `n_known_item` + `significant`; nDCG@k (pooled set, only if constructed); latency p50/p95 per path.
6. **Output**: a comparison report (JSON + human-readable table) written to `docs/features/hybrid-retrieval-eval.md`'s results section, with the decision-gate verdict.

## Architectural Impact

- **New dependencies**: none for Phase 1 (popoto 1.8.0 already installed/propagated by #2081; LLM query-gen/judging uses the existing PydanticAI non-harness path). Phase 2 (if adopted) adds no new deps either.
- **Interface changes**: none in Phase 1 (harness is additive, new module). Phase 2 (if adopted) changes only the internals of `retrieve_memories` (or adds a mode flag) — the `search()` public signature stays stable.
- **Coupling**: Phase 1 adds a new dev-only module `tools/memory_eval/`; no coupling into the live recall path. Phase 2 (if adopted) couples the recall path to `ContextAssembler` — a decision made *only* under the gate.
- **Data ownership**: unchanged. Harness is read-only; production `Memory` records are never mutated (cloned partition for any path with side effects).
- **Reversibility**: Phase 1 is fully reversible (delete the module). Phase 2 adoption is a single call-site swap behind a config flag, trivially revertible.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM (decision-gate sign-off)

**Interactions:**
- PM check-ins: 1-2 (agree the win threshold before running; report the verdict)
- Review rounds: 1 (harness correctness +, if adopted, the recall-path change)

The coding is modest; the cost is methodology rigor (ground truth) and the honest
adopt/don't-adopt call. Appetite is dominated by getting the measurement defensible,
not by lines of code.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| popoto 1.8.0 | `.venv/bin/python -c "import popoto; assert popoto.__version__ == '1.8.0'"` | Hybrid retrieval API present |
| Embedding provider reachable | `.venv/bin/python -c "from popoto.fields.embedding_field import get_default_provider; assert get_default_provider() is not None"` | Vector signal + query embedding available |
| LLM path for query-gen/judging | `.venv/bin/python -c "import pydantic_ai"` | Known-item query generation + pooled relevance judging |
| Non-empty valor corpus | `.venv/bin/python -c "from models.memory import Memory; assert len(list(Memory.query.filter(project_key='valor'))) > 100"` | Enough data to evaluate |

## Solution

### Key Elements

- **`tools/memory_eval/` harness**: read-only offline module that constructs the query
  set, drives both retrievers, and computes recall/precision/MRR/nDCG + latency.
- **Ground-truth construction**: known-item retrieval (objective, REQUIRED, drives the gate) +
  pooled LLM-graded judgments (partial relevance, for nDCG, **conditional corroboration** —
  built only near the threshold). Both are named and justified; neither favors one retriever.
- **Two retrieval arms + a selector assertion**: current 4-signal RRF and forced
  `retrieval_mode='hybrid'` (clean A/B), reported separately. `retrieval_mode='auto'` is NOT a
  third arm — it is validated by a single assertion that `auto` resolves to `hybrid` for
  `Memory` (schema-level, always true given BM25Field+EmbeddingField), which de-risks the
  IF-WIN cutover that ships `auto`.
- **Embedding-provider hard gate**: fail-closed provider-presence check + per-record non-zero
  vector assertion; a degraded/zero-vector run is an error state, never a scored data point.
- **Decision gate**: explicit point-estimate thresholds AND a 95% CI floor (CI lower bound on
  the paired recall delta > 0). Adopt iff hybrid clears both layers.
- **Two conditional branches**: IF-WIN wires `retrieval_mode='auto'` into the real recall
  path with tests; IF-NO-WIN documents the negative result and changes no recall code.

### Flow

Agree threshold with PM → build harness → **provider hard gate (fail-closed)** → construct
known-item query set (required) → run 2 arms read-only on embedded subset (+ whole corpus as
context), asserting non-zero vector contribution per query → compute metrics + 95% CI →
**proximity check** → { near threshold: build pooled/nDCG corroboration | else: skip } →
write verdict (with N, CI, significance) into results doc → **decision gate (point estimate
AND CI floor)** → { IF-WIN: wire `auto` into recall path + tests | IF-NO-WIN: close out with
documented negative result }.

### Technical Approach

- **Metrics.** Primary (drives the gate): **recall@k** and **MRR** on the known-item set
  (objective — the gold memory either surfaces in top-k or it doesn't), reported with a
  **bootstrap 95% CI** on the paired per-query recall delta and an explicit `n_known_item`.
  Secondary (corroboration only, **proximity-gated** — built only near the threshold):
  **nDCG@k** on the pooled LLM-judged set (captures graded partial relevance). Operational:
  **latency p50/p95** per arm (hybrid embeds the query per call; a large latency regression is a
  cost even if quality ties). `k` defaults to 10 (matches `search(limit=10)`).
- **Ground truth — addressed head-on.** No labeled memory-recall eval set exists, so we
  build one two ways: (1) **Known-item**: sample N memories (bias toward embedded +
  high-importance), LLM-generate a realistic query whose answer is that memory; the
  (query→memory_id) map is the objective label. Cheap, reproducible, unbiased between
  arms. **This set is the required deliverable and the sole gate driver.** (2) **Pooled
  judgments (conditional)**: built **only when the known-item result lands near the
  threshold**; for a smaller query subset, pool both arms' top-k and LLM-judge each pair 0-3;
  nDCG over those judgments. Pooling (vs. judging one arm's results) is what keeps the judged
  metric fair. Because nDCG is corroboration-only (Risk 1) it can never flip the verdict, so a
  comfortable clear or clear miss skips it — saving ~2x ground-truth cost.
- **Evaluation subset — read-only by default (supervisor-approved).** The **primary** run
  evaluates the **already-embedded subset** (~212 records) and is strictly read-only /
  non-destructive: no backfill, no corpus mutation. A bounded embedding backfill of the eval
  subset is **optional and opt-in only** — gated behind `--backfill-embeddings` (off by
  default) — and, when used, is reported as a clearly-labeled **secondary** run, never the
  default path. The harness must never mutate the corpus as a required step.
- **Embedding-provider hard gate — fail-closed before any comparison (addresses Concern 1).**
  `Memory.embedding` is a `GracefulEmbeddingField` (models/memory.py:158): if the provider is
  unreachable it degrades **silently** to no vector, collapsing the forced-`hybrid` arm to
  BM25-only and producing a confident-but-WRONG "do-not-adopt" that actually measures a broken
  embedding path. The harness MUST fail-closed, not silently degrade:
  1. **Provider presence gate at entry.** Before constructing any query set or running any arm,
     assert `get_default_provider() is not None` (matches the Prerequisites row). If None, the
     harness **raises and exits non-zero immediately** — it never proceeds to a scored run.
  2. **Per-record non-zero vector assertion.** For each embedded-subset query, assert the
     forced-`hybrid` arm's `AssemblyResult.metadata` shows a **non-zero vector contribution**
     (the RRF fusion actually consumed a vector signal, not a zero/degraded embedding). A query
     whose hybrid pull shows zero vector contribution on a record that IS embedded is a
     **degradation error**.
  3. **A zero-vector / degraded run is an ERROR state, never a data point.** Any degraded run
     aborts the eval with a non-zero exit and a clear message; it is never recorded as a scored
     comparison. This is the single highest-priority guard for a measurement spike — a false
     verdict is worse than no verdict.
- **Fairness / no contamination.** Both arms run against the **same** query set and a
  read-only snapshot. If spike-2 shows either path mutates `access_count`/relevance/outcome
  state, the harness clones the corpus into a throwaway `dbg-hybrideval` partition and runs
  there; `valor` is never mutated. Query embeddings are computed once per query and reused
  across arms.
- **auto vs. forced hybrid — assertion, not a third arm (addresses NIT).** popoto resolves
  `auto`→`hybrid` at schema level whenever a model has both `BM25Field` and `EmbeddingField`
  (always true for `Memory`), so a full `auto` measurement arm would duplicate forced-`hybrid`
  and add no gate signal. Instead of running `auto` as a third metrics arm, the harness runs a
  **single assertion** that `ContextAssembler(Memory, retrieval_mode='auto')` resolves to
  `hybrid` for `Memory`. That assertion — not a duplicate arm — de-risks the IF-WIN cutover
  (which ships `auto`) while the gate measures forced `hybrid`. Only two measurement arms run:
  current 4-signal RRF and forced `hybrid`.
- **Decision gate (the crux) — supervisor-approved.** Adopt **only if** forced-hybrid beats
  the current path on the **primary** metric in aggregate on the embedded subset by at least a
  named margin, **and** the win is statistically real (not sampling noise), **and** it does not
  regress latency past a named ceiling. The gate has two layers — a point-estimate bar and a
  confidence-interval floor — both of which must clear.

  **Layer 1 — point-estimate bars.** Four named, env-overridable constants, each carrying a
  grain-of-salt comment marking it provisional/tunable (tune once real numbers land), homed in a
  **single config home: `config/settings.py`** (Pydantic `TimeoutSettings`-style group,
  env-overridable via the standard settings mechanism — addresses Concern 5/6). There is no
  second home in `config/memory_defaults.py`.
  - `HYBRID_EVAL_MIN_RECALL_GAIN` — default `0.05` (absolute recall@10 gain); env `HYBRID_EVAL_MIN_RECALL_GAIN`
  - `HYBRID_EVAL_MIN_MRR_GAIN` — default `0.03` (absolute MRR gain); env `HYBRID_EVAL_MIN_MRR_GAIN`
  - `HYBRID_EVAL_MAX_LATENCY_REGRESSION_PCT` — default `50` (max % p95 latency regression); env `HYBRID_EVAL_MAX_LATENCY_REGRESSION_PCT`
  - `RETRIEVAL_MODE` — Phase-2 recall-path flag, default preserving current behavior; env `RETRIEVAL_MODE` (same single home; see IF-WIN wiring)

  **Layer 2 — statistical-significance floor (addresses Concern 2).** The 0.05 recall gate is a
  point estimate over ~212 embedded records sampled into a known-item set of finite N; with no
  min-N floor or confidence interval the decision could flip on sampling noise while *looking*
  rigorous. So the harness computes **paired per-query deltas** (hybrid recall@10 − current
  recall@10, per known-item query), then **bootstrap-resamples** those paired deltas for a
  **95% confidence interval** on the mean gain. Adoption requires **BOTH**:
  - point estimate: mean recall gain > `HYBRID_EVAL_MIN_RECALL_GAIN` AND mean MRR gain > `HYBRID_EVAL_MIN_MRR_GAIN`, AND
  - CI floor: the **95% CI lower bound on the paired recall delta is > 0** (the win is not an artifact of sampling).

  The results doc emits `n_known_item` (real N), the 95% CI bounds, and a boolean
  `significant: true/false`, so PM sign-off reads the actual sample size and interval — not a
  bare point estimate.

  **Ground-truth cost control — pooled/nDCG is proximity-gated (addresses Concern 4).** The
  known-item set is the **required Phase 1 deliverable** and is the sole driver of the gate. The
  pooled-judgment / nDCG pipeline is **corroboration only** (per Risk 1 it can never change the
  recall@10 / MRR / latency verdict), so it is **guarded behind a proximity check**: construct
  pooled judgments **only when the result is near the threshold**, i.e. only when
  `abs(mean_recall_gain − HYBRID_EVAL_MIN_RECALL_GAIN) < HYBRID_EVAL_MIN_RECALL_GAIN`. A
  comfortable clear or a clear miss **skips** the pooled/nDCG pass entirely, saving ~2x the
  ground-truth cost on a Medium-appetite directional spike.

  A tie or loss (either layer fails) ⇒ **do not adopt**; that is a successful, complete outcome.
  **Aggregate win on the embedded subset (both layers) is the sole adoption criterion** —
  per-query-shape partial adoption is explicitly not this plan's approach (see No-Gos).
- **IF-WIN wiring.** Route `retrieve_memories` (or `search`) through
  `ContextAssembler(retrieval_mode='auto')` behind the single-home config flag
  `config/settings.py::RETRIEVAL_MODE` (env-overridable, default preserving current behavior
  until cutover — the SAME home as the gate constants; no `config.memory_defaults` variant),
  keeping the fail-silent contract and the `superseded_by` filter. Add unit tests for the new
  path; update `tests/unit/test_memory_retrieval.py`.
- **IF-NO-WIN closeout.** No recall-path edit. The harness + results doc satisfy the issue.
  The harness stays in-repo as a reusable retrieval-eval tool (re-runnable after future
  popoto upgrades or embedding-coverage growth).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The harness must not silently swallow retrieval errors that would zero out an arm's
  score (an all-empty arm would falsely "lose"). Any `except` around a retrieval call must
  log AND mark that query as errored/excluded, asserted by a unit test with a forced failure.
- [ ] `retrieve_memories` and `search` already wrap bodies in fail-silent `except` — the
  harness distinguishes "genuinely empty result" from "errored" so a broken arm can't
  masquerade as a legitimate zero.
- [ ] **Provider hard gate (Concern 1):** a unit test forces `get_default_provider()` to return
  `None` and asserts the harness raises + exits non-zero **before** scoring any arm (never
  records a degraded run). A second test forces a zero-vector `.metadata` on an embedded-subset
  query and asserts the run aborts as an error state, not a scored data point.
- [ ] **Significance floor (Concern 2):** a unit test feeds paired deltas whose mean clears the
  point-estimate bar but whose bootstrap 95% CI lower bound is ≤ 0, and asserts the harness
  reports `significant: false` and the gate does NOT declare a win.

### Empty/Invalid Input Handling
- [ ] Harness handles empty query strings, queries with no bloom hits (current path returns
  `{"results": []}`), and memories with no embedding (hybrid vector signal absent) — each is
  a metric data point, not a crash. Unit-tested with fixture queries.
- [ ] Known-item generation that yields a degenerate/empty query is skipped, not scored.

### Error State Rendering
- [ ] The results report renders per-arm error counts and embedded-coverage explicitly, so a
  reader can tell a real tie from a broken run. Verified by a test asserting the report
  includes error-count and coverage fields.

## Test Impact

- [ ] `tests/unit/test_memory_retrieval.py` — **UPDATE (Phase 2 / IF-WIN only)**: if the recall
  path is routed through `ContextAssembler`, update assertions for the new retrieval mode and
  add a case for the `RETRIEVAL_MODE` flag toggling paths. Untouched in the IF-NO-WIN branch.
- [ ] `tests/unit/test_memory_eval.py` — **NEW**: metric-correctness tests (recall@k, MRR, nDCG
  computed correctly on synthetic fixtures with known answers), read-only guarantee test, the
  errored-arm-vs-empty-arm distinction test, the **provider-hard-gate + zero-vector abort**
  tests (Concern 1), the **bootstrap-CI significance-floor** test (Concern 2), and a
  **proximity-gate** test asserting the pooled/nDCG pass is skipped on a comfortable clear/miss
  and built only near the threshold (Concern 4).

No existing tests are affected in the required Phase 1 branch — the harness is a new,
additive, read-only module with no changes to existing recall behavior. Only the conditional
IF-WIN branch touches `test_memory_retrieval.py`, and only if the decision gate says adopt.

## Rabbit Holes

- **Perfect ground truth.** Do not try to hand-label a gold relevance set across 1170
  memories. Known-item + pooled LLM judgments on a sample is enough to make a directional call.
- **Tuning the current 4-signal RRF.** Re-weighting or adding signals to the existing fusion is
  a different optimization problem and is out of scope; the comparison is against the current
  path *as it ships today*.
- **Backfilling the entire corpus's embeddings.** A bounded backfill of the eval subset is
  fine; embedding all 1170 records (and every project) to make coverage pretty is not this
  spike's job.
- **Generalizing the harness into a standing benchmark service / dashboard.** Build a
  re-runnable script, not a framework.
- **Chasing the `auto` selector's internals.** Measure what it picks and whether it matters;
  don't reverse-engineer popoto's per-query heuristic.

## Risks

### Risk 1: LLM-constructed ground truth is biased or noisy
**Impact:** A biased query set could make either arm look artificially strong, corrupting the verdict.
**Mitigation:** Known-item labels are objective (gold = the memory the query was generated from), independent of either retriever. Pooled judgments judge the *union* of both arms' results, never one arm's. Report both metrics; require the primary (objective) metric to clear the gate, using nDCG only as corroboration. Include a small human spot-check of generated queries.

### Risk 2: Low embedding coverage masks hybrid's real quality
**Impact:** With ~18% of records embedded, hybrid's vector signal is inert for most of the corpus, so a whole-corpus run could show a false tie.
**Mitigation:** Run the **primary** comparison on the embedded subset (where hybrid can differ), with an optional bounded pre-eval backfill; report whole-corpus numbers as secondary context. State coverage explicitly in the report (spike-3).

### Risk 3: Retrieval side effects contaminate production memory
**Impact:** If a retrieval path bumps `access_count` / relevance / outcome tracking, running the eval would mutate `valor` and skew live recall.
**Mitigation:** spike-2 verifies read-only behavior; if any path mutates, the harness runs against a cloned throwaway `dbg-hybrideval` partition and never against `valor`. Assert `access_count` unchanged in a harness test.

### Risk 4: Latency regression not captured by quality metrics
**Impact:** Hybrid embeds the query per call; it could tie on quality but be materially slower in the live hook path.
**Mitigation:** Measure latency p50/p95 per arm; the decision gate includes a named latency-regression ceiling, not just quality.

## Race Conditions

### Race 1: Concurrent live recall mutating the corpus during an eval run
**Location:** `agent/memory_retrieval.py` (live recall) vs. `tools/memory_eval/` (harness), both over `project_key='valor'`.
**Trigger:** The worker services live recall (bumping `access_count`, decaying relevance) while the harness iterates the same partition.
**Data prerequisite:** The eval set must be a stable snapshot for both arms to see identical corpus state.
**State prerequisite:** No writes to the evaluated partition mid-run.
**Mitigation:** Snapshot the corpus into memory (or clone into `dbg-hybrideval`) at run start; run both arms against the frozen snapshot, not live `valor`. The harness is a manual, dev-invoked run — schedule it when not racing a heavy live session, and never mutate the evaluated partition.

## No-Gos (Out of Scope)

- **Per-query-shape / partial hybrid adoption.** The decision gate is an *aggregate* win on the
  embedded subset. Conditionally routing only some query shapes through hybrid is a different,
  more complex optimization and is not part of this measurement spike's scope or its adopt/don't
  decision.
- **Re-tuning the existing four-signal RRF fusion** (weights, new signals, `RRF_K`). The
  comparison baseline is the current path exactly as it ships; changing it would move the goalposts.
- **Embedding-backfilling the whole corpus or other projects.** Only a bounded backfill of the
  `valor` eval subset is in scope, purely to make the vector signal measurable.
- [DESTRUCTIVE] **Running the harness directly against the live `valor` partition if spike-2 shows
  any retrieval path mutates state.** Review-before-execute: the harness must clone to a throwaway
  partition first; a mutating run against production memory is irreversible corruption of recall state.

## Update System

No update system changes required for Phase 1. popoto 1.8.0 was already propagated to every
machine by PR #2081; the harness is a dev-only, in-repo script that is never deployed to the
bridge/worker runtime. **Phase 2 (IF-WIN only):** routing recall through `ContextAssembler`
introduces no new dependency and no new config file — it adds a single `RETRIEVAL_MODE` field
to **`config/settings.py`** (the single config home — env-overridable via the Pydantic settings
mechanism, defaulting to current behavior until cutover; there is no `config.memory_defaults`
variant), which propagates with no migration. The four `HYBRID_EVAL_*` gate constants and
`RETRIEVAL_MODE` all live in `config/settings.py` — one home, consistent across Technical
Approach, Update System, and the Decision Record. No Popoto schema change (the `Memory` fields
already exist), so no `scripts/update/migrations.py` entry is needed.

## Agent Integration

No agent integration required for Phase 1. The evaluation harness is invoked by a developer via
`python -m tools.memory_eval.hybrid_eval`; it is a measurement tool, not a capability the agent
reaches through the bridge or an MCP surface. It needs no `pyproject.toml [project.scripts]`
entry and no `.mcp.json` change (though a `[project.scripts]` alias may be added for
convenience — non-load-bearing).

**Phase 2 (IF-WIN only):** adoption changes the *internals* of the already-wired recall path
(`tools/memory_search/__init__.py::search` → `retrieve_memories`), which the agent already
reaches via the `mcp__memory__memory_search` MCP tool and the memory recall hooks. No new agent
surface is created; recall simply returns better-ranked memories. An integration test asserts
`memory_search` still returns well-formed results through the new path.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/hybrid-retrieval-eval.md` — the methodology (metrics, ground-truth
  construction, arms, decision gate), the measured results table, and the **verdict**
  (adopt / do-not-adopt with numbers). This doc is the primary deliverable of the spike.
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] IF-WIN only: update `docs/features/subconscious-memory.md` recall-path description to
  reflect the `ContextAssembler(retrieval_mode='auto')` cutover and the `RETRIEVAL_MODE` flag.

### Inline Documentation
- [ ] Docstrings on the harness module explaining the metrics and read-only contract.

## Success Criteria

- [ ] A concrete recall-quality comparison methodology is documented (metrics, query set,
  judging approach, decision gate with named thresholds + the 95% CI floor) in `docs/features/hybrid-retrieval-eval.md`.
- [ ] The comparison is actually run: current 4-signal RRF vs. forced `hybrid` (two arms; `auto`
  covered by a resolution assertion), on the real `valor` corpus (embedded subset primary, whole
  corpus as context), read-only.
- [ ] The embedding-provider hard gate is enforced: the harness fails-closed if the provider is
  down and never records a zero-vector/degraded run as a data point (Concern 1).
- [ ] Results are written up with a clear, numeric verdict: adopt or do-not-adopt, reporting
  `n_known_item`, the 95% CI, and `significant` — the gate requires the CI lower bound > 0 as
  well as the point-estimate bars (Concern 2).
- [ ] The pooled/nDCG pass is proximity-gated: skipped on a comfortable clear/miss, built only
  near the threshold (Concern 4).
- [ ] The four gate knobs (`HYBRID_EVAL_*` + `RETRIEVAL_MODE`) live in the single home
  `config/settings.py`, env-overridable, each marked provisional/tunable (Concern 5/6).
- [ ] The harness is read-only: a test asserts `access_count` is unchanged after an eval run.
- [ ] IF-WIN: recall path routed through `ContextAssembler(retrieval_mode='auto')` behind a
  config flag, with tests covering the new path and the flag toggle.
- [ ] IF-NO-WIN: no change to `agent/memory_retrieval.py` / `tools/memory_search/__init__.py`
  recall behavior; the methodology + results doc alone satisfy the issue.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

Solo-dev spike. The lead deploys one builder for the harness and one validator; the PM signs off
on the decision gate before any IF-WIN wiring.

### Team Members

- **Builder (eval-harness)**
  - Name: eval-harness-builder
  - Role: Build `tools/memory_eval/` harness — query-set construction, 3 retrieval arms, metrics, report. Read-only.
  - Agent Type: builder
  - Domain: redis-popoto
  - Resume: true

- **Validator (eval-harness)**
  - Name: eval-harness-validator
  - Role: Verify read-only guarantee, metric correctness on fixtures, and that the report renders a numeric verdict.
  - Agent Type: validator
  - Resume: true

- **Builder (recall-cutover, CONDITIONAL — IF-WIN only)**
  - Name: recall-cutover-builder
  - Role: Wire `ContextAssembler(retrieval_mode='auto')` into the recall path behind `RETRIEVAL_MODE`; update tests. Only spawned if the gate says adopt.
  - Agent Type: builder
  - Domain: redis-popoto
  - Resume: true

### Available Agent Types

Tier 1 `builder` + `validator`; `documentarian` for the results doc. Paste the
`redis-popoto` domain framing from `DOMAIN_FRAMING.md` into the builder tasks.

## Step by Step Tasks

### 1. Spikes (parallel, worktree-isolated, read-only)
- **Task ID**: spike-all
- **Depends On**: none
- **Assigned To**: eval-harness-builder
- **Agent Type**: builder
- **Parallel**: true
- Run spike-1 (query_cues shape — **BLOCKING**: must confirm `assemble()` returns records for a text cue, or the harness drops to the lower-level hybrid pull / ABORTs; never falls back to `assess()`), spike-2 (read-only guarantee), spike-3 (embedded-subset count) against a throwaway `dbg-` clone; record findings into Spike Results.

### 2. Build the evaluation harness
- **Task ID**: build-harness
- **Depends On**: spike-all
- **Validates**: tests/unit/test_memory_eval.py (create)
- **Informed By**: spike-1 (cue shape), spike-2 (read-only path), spike-3 (subset)
- **Assigned To**: eval-harness-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/memory_eval/` with:
  - the **embedding-provider hard gate** at entry (`assert get_default_provider() is not None`; raise + exit non-zero) and a **per-record non-zero vector assertion** on the hybrid arm; a degraded/zero-vector run aborts, never scores (Concern 1).
  - query-set construction: **known-item set (required, gate driver)** + **pooled/nDCG set built only under the proximity check** `abs(mean_recall_gain − HYBRID_EVAL_MIN_RECALL_GAIN) < HYBRID_EVAL_MIN_RECALL_GAIN` (Concern 4).
  - **two retrieval arms** (current 4-signal RRF; forced `hybrid`) + a **single `auto`→`hybrid` resolution assertion** (not a third arm — NIT); if `assemble()` is unavailable, use the lower-level hybrid pull or ABORT (never `assess()` — Concern 3).
  - metrics: recall@k, MRR, nDCG (conditional), latency p50/p95, plus a **bootstrap 95% CI** on paired per-query recall deltas emitting `n_known_item` + `significant` (Concern 2).
  - the four gate constants (`HYBRID_EVAL_*`) and `RETRIEVAL_MODE` read from the **single home `config/settings.py`** (Pydantic env-override, each with a provisional/tunable grain-of-salt comment — Concern 5/6).
  - read-only snapshot/clone and a report renderer that prints `n_known_item`, the 95% CI, `significant`, per-arm error counts, and embedded coverage.
- Add `tests/unit/test_memory_eval.py`: metric correctness on fixtures, read-only assertion, errored-vs-empty-arm distinction, provider-hard-gate + zero-vector abort, bootstrap-CI significance floor, proximity-gate skip/build.

### 3. Validate the harness
- **Task ID**: validate-harness
- **Depends On**: build-harness
- **Assigned To**: eval-harness-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify read-only guarantee, metric correctness, and numeric-verdict rendering.

### 4. Run the evaluation + write the verdict
- **Task ID**: run-eval
- **Depends On**: validate-harness
- **Assigned To**: eval-harness-builder
- **Agent Type**: builder
- **Parallel**: false
- After the provider hard gate passes, run the **two arms** on `valor` (embedded subset primary, whole corpus context) read-only; compute metrics + the 95% CI; run the proximity check and only then (if near threshold) the pooled/nDCG pass; write methodology + results + verdict — including `n_known_item`, the 95% CI, `significant`, and per-arm error/coverage counts — into `docs/features/hybrid-retrieval-eval.md`.

### 5. DECISION GATE (PM sign-off)
- **Task ID**: decision-gate
- **Depends On**: run-eval
- **Assigned To**: PM
- **Parallel**: false
- Compare measured gains against `HYBRID_EVAL_MIN_RECALL_GAIN` / `HYBRID_EVAL_MIN_MRR_GAIN` / `HYBRID_EVAL_MAX_LATENCY_REGRESSION_PCT` (point-estimate bars) **AND** confirm the 95% CI lower bound on the paired recall delta is > 0 (`significant: true`). Both layers must clear. Route to task 6a (adopt) or 6b (do-not-adopt).

### 6a. IF-WIN: wire hybrid into the recall path
- **Task ID**: build-cutover
- **Depends On**: decision-gate (adopt)
- **Validates**: tests/unit/test_memory_retrieval.py (update), tests/unit/test_memory_eval.py
- **Assigned To**: recall-cutover-builder
- **Agent Type**: builder
- **Parallel**: false
- Route `retrieve_memories`/`search` through `ContextAssembler(retrieval_mode='auto')` behind `RETRIEVAL_MODE`; preserve fail-silent + superseded filter; update tests + `docs/features/subconscious-memory.md`.

### 6b. IF-NO-WIN: document negative result, no code change
- **Task ID**: document-negative
- **Depends On**: decision-gate (do-not-adopt)
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Finalize the results doc with the do-not-adopt verdict and the numbers behind it; confirm zero recall-path edits.

### 7. Documentation + final validation
- **Task ID**: validate-all
- **Depends On**: build-cutover OR document-negative
- **Assigned To**: eval-harness-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify success criteria for the taken branch; run scoped tests; confirm docs updated.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Harness tests pass | `pytest tests/unit/test_memory_eval.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/memory_eval/` | exit code 0 |
| Format clean | `python -m ruff format --check tools/memory_eval/` | exit code 0 |
| Results doc exists | `test -f docs/features/hybrid-retrieval-eval.md` | exit code 0 |
| Verdict recorded | `grep -iE "verdict:.*(adopt|do-not-adopt|do not adopt)" docs/features/hybrid-retrieval-eval.md` | exit code 0 |
| IF-NO-WIN: recall path untouched | `git diff --name-only main -- agent/memory_retrieval.py tools/memory_search/__init__.py \| wc -l` | output contains 0 |
| popoto 1.8.0 present | `.venv/bin/python -c "import popoto; assert popoto.__version__=='1.8.0'"` | exit code 0 |

<!-- The "recall path untouched" row is the anti-criterion for the IF-NO-WIN branch: it
     mechanically asserts the No-Go against a recall-path rewrite the evaluation didn't justify.
     In the IF-WIN branch this row is expected to change and is replaced by the cutover tests. -->

## Critique Results

<!-- Populated by /do-plan-critique (war room) 2026-07-14. Verdict: READY TO BUILD (with concerns). FULL depth (3 critics). 0 blockers, 5 concerns, 1 nit. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | `GracefulEmbeddingField` (models/memory.py:158) degrades silently when the embedding provider is down (it currently fails: `get_default_provider()` returns None), collapsing the forced-`hybrid` arm to BM25-only and driving a false do-not-adopt verdict that actually measures a broken embedding path. | Technical Approach / Fairness | Harness retrieval adapter: `assert get_default_provider() is not None` AND assert the hybrid arm's per-record `.metadata` shows non-zero vector contribution for embedded-subset queries; raise + exit non-zero on failure. A zero-vector hybrid run must be an error state, not a scored data point. |
| CONCERN | Risk & Robustness | The gate compares point estimates (`HYBRID_EVAL_MIN_RECALL_GAIN=0.05`) over only ~212 embedded records sampled into a known-item set of unspecified N — no min-N floor, CI, or significance test, so the decision can flip on sampling noise while looking rigorous. | Technical Approach / Decision gate | Compute paired per-query deltas, bootstrap-resample for a 95% CI; require CI lower bound > 0 in addition to mean gain > threshold. Emit `n_known_item`, the CI, and a `significant: true/false` field into the results doc so PM sign-off reads real N. |
| CONCERN | Risk & Robustness | Research lines 86-89 assert `assembler.assemble(query_cues=<dict>)` as verified fact, but the only real call site (tools/memory_search/__init__.py:193) uses `.assess()`; `.assemble()` is exercised nowhere (unfilled spike-1). The declared `.assess()`-scored-sets fallback is invalid — assess() scores quality, it does not select records. | Research / spike-1 | spike-1 must verify `ContextAssembler(Memory, ...).assemble(query_cues=<dict>)` returns `AssemblyResult.records` for a text cue. Failure branch must NOT fall through to `assess({"query": ...})` as a retrieval proxy; drop to the lower-level hybrid pull path or ABORT — recall@k/MRR computed from an assess score is meaningless. |
| CONCERN | Scope & Value | The entire pooled-judgment/nDCG pipeline (union pooling, 0-3 LLM grading, nDCG, fixture tests) is built but Risk 1 (line 298) makes nDCG "corroboration only" — it can never change the recall@10/MRR/latency gate, roughly doubling ground-truth cost for a Medium-appetite directional spike. | Technical Approach / Decision gate | Make the known-item set the required Phase 1 deliverable; guard the pooled/nDCG pass behind a proximity check, e.g. only construct pooled judgments when `abs(recall_gain - HYBRID_EVAL_MIN_RECALL_GAIN) < HYBRID_EVAL_MIN_RECALL_GAIN`. A comfortable clear or miss skips the pooled pipeline. |
| CONCERN | History & Consistency | Lines 228-231 claim the three `HYBRID_EVAL_*` gate constants are "env-overridable" in config/memory_defaults.py, but that file holds only bare literals with no `os.getenv()` — claimed compliance that isn't wired, the exact pattern the repo's env-overridable-constants note warns against. | Technical Approach / Decision gate | Either add per-constant `float(os.getenv("HYBRID_EVAL_MIN_RECALL_GAIN", "0.05"))`-style casts in config/memory_defaults.py (guard non-float env strings), or home these constants in config/settings.py (Pydantic env-override). Pick one and state it. |
| CONCERN | History & Consistency | The Phase-2 `RETRIEVAL_MODE` flag has two named homes: IF-WIN wiring (line 238) + Solution say `config.memory_defaults.RETRIEVAL_MODE`; Update System (line 342) says `config/settings.py` / config.memory_defaults. The two are not interchangeable — only settings.py is env-overridable, so the "env-overridable" claim holds in one. | Update System vs Technical Approach | Name a single home consistently across Technical Approach, Update System, and Decision Record. Given the env-overridable requirement, config/settings.py is correct; update line 238 to match (same root defect as the constant-home concern). |
| NIT | Scope & Value | The third `auto` arm is run as a full measurement arm, but popoto resolves `auto`→`hybrid` at schema level whenever BM25Field+EmbeddingField are present (always true for Memory), so `auto` results are identical to forced-`hybrid` and add no gate signal. | Solution / Three retrieval arms | Replace the full third arm with a single assertion that `auto` resolves to `hybrid` for `Memory`; that assertion (not a duplicate metrics arm) de-risks the IF-WIN cutover, which ships `auto` while the gate measures forced `hybrid`. |

**Revision applied 2026-07-14 (critique fold-in, 0 blockers):** All 5 concerns + the NIT are now
folded into concrete plan tasks / Implementation Notes without changing the spike framing or the
gate values. (1) Embedding-provider hard gate — fail-closed provider check + per-record non-zero
vector assertion; a degraded run aborts, never scores (Technical Approach; Data Flow entry;
build-harness task; Failure Path tests). (2) Statistical floor — bootstrap 95% CI on paired recall
deltas, CI lower bound > 0 required alongside the point-estimate bars; `n_known_item` +
`significant` emitted (Decision gate; Metrics; decision-gate task; Failure Path tests). (3)
`.assemble()` proven before comparison — spike-1 is BLOCKING; failure drops to the lower-level
hybrid pull or ABORTs, never falls back to `assess()` (Research caveat; spike-1; spike-all task).
(4) Pooled/nDCG proximity-gated — known-item set is the required gate driver; pooled pass built
only near the threshold (Decision gate; Ground truth; Data Flow; build-harness task). (5) Single
config home — all four knobs (`HYBRID_EVAL_*` + `RETRIEVAL_MODE`) in `config/settings.py`, Pydantic
env-override, provisional/tunable comments (Decision gate; IF-WIN wiring; Update System; Decision
Record). NIT — `auto` is an assertion, not a third arm (Solution; Data Flow; Decision Record).

---

## Decision Record

All planning questions are resolved (supervisor sign-off, 2026-07-14). No open questions remain.

1. **Win-threshold gate — RESOLVED (approved as proposed; hardened in revision).** The gate is
   the three named, env-overridable, provisional point-estimate constants
   (`HYBRID_EVAL_MIN_RECALL_GAIN=0.05`, `HYBRID_EVAL_MIN_MRR_GAIN=0.03`,
   `HYBRID_EVAL_MAX_LATENCY_REGRESSION_PCT=50`) **plus a statistical-significance floor**: the
   bootstrap 95% CI lower bound on the paired per-query recall delta must be > 0. All four gate
   knobs (`HYBRID_EVAL_*` + the Phase-2 `RETRIEVAL_MODE` flag) live in a **single config home,
   `config/settings.py`** (Pydantic env-override), each with a provisional/tunable grain-of-salt
   comment. The point-estimate bars are unchanged from the approved values; the CI floor and the
   single-home wiring were added in the critique-revision pass, not a change to the gate values
   or the spike framing.
2. **Embedding backfill — RESOLVED (read-only by default).** Primary evaluation runs on the
   already-embedded subset (~212 records); the spike is read-only / non-destructive by default.
   A bounded backfill is optional and opt-in only (`--backfill-embeddings`, off by default),
   reported as a secondary run. Corpus mutation is never a required step.
3. **`auto` selector scope — RESOLVED (aggregate gate only; assertion not a third arm).** A
   **single assertion** that `auto` resolves to `hybrid` for `Memory` (schema-level, always true
   given BM25Field+EmbeddingField) plus a clean forced-hybrid A/B is sufficient — `auto` is NOT
   run as a duplicate measurement arm (it would be identical to forced `hybrid` and add no gate
   signal). Aggregate win on the embedded subset is the sole adoption criterion; a per-query-shape
   breakdown is out of scope for the decision (stays a No-Go) and, if ever pursued, is a separate
   future optimization.
