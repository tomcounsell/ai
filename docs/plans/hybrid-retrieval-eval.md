---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-07-14
tracking: https://github.com/tomcounsell/ai/issues/2082
last_comment_id:
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

### spike-1: Correct `query_cues` shape for `Memory.assemble()`
- **Assumption**: "`ContextAssembler.assemble()` can be driven for `Memory` with a query-text cue that exercises the BM25+vector hybrid pull path."
- **Method**: prototype (worktree, read-only against a throwaway `dbg-` project clone)
- **Agent Type**: builder in worktree
- **Time cap**: 10 min
- **Result**: _[filled during execution]_
- **Confidence**: _[filled]_
- **Impact if false**: If `assemble()` cannot be driven with a text cue against `Memory`, the harness must call the hybrid pull path at a lower level (or the eval compares `assess()`-scored sets instead) — changes the harness's retrieval adapter, not the metrics.

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

1. **Entry point**: `python -m tools.memory_eval.hybrid_eval --project valor [--backfill-embeddings] [--k 10]` (dev-invoked script).
2. **Corpus snapshot**: read `Memory.query.filter(project_key='valor')` into an in-memory eval set (optionally cloned into a throwaway `dbg-hybrideval` partition so retrieval side effects never touch `valor`).
3. **Query-set construction**:
   - **Known-item set**: for a sample of high-importance / embedded memories, generate (via the repo's PydanticAI LLM path) a natural-language query whose gold-relevant answer is that memory. The (query → gold memory_id) pair is the objective label.
   - **Pooled-judgment set**: run both retrievers, pool the union of top-k, LLM-judge each (query, memory) pair on a 0-3 graded scale. Judgments feed nDCG.
4. **Dual retrieval**: for each query, run (a) current path `retrieve_memories(query, project_key, limit=k)` and (b) hybrid path `ContextAssembler(Memory, ..., retrieval_mode='hybrid').assemble(query_cues=...)` — plus (c) `retrieval_mode='auto'` to separately audit the mode selector. All read-only.
5. **Scoring**: compute recall@k, precision@k, MRR (known-item set); nDCG@k (pooled set); latency p50/p95 per path.
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
- **Ground-truth construction**: known-item retrieval (objective, primary) + pooled
  LLM-graded judgments (partial relevance, for nDCG). Both are named and justified;
  neither favors one retriever.
- **Three retrieval arms**: current 4-signal RRF; forced `retrieval_mode='hybrid'`
  (clean A/B); `retrieval_mode='auto'` (audits the mode selector). Reported separately.
- **Decision gate**: an explicit, pre-agreed win threshold. Adopt iff hybrid clears it.
- **Two conditional branches**: IF-WIN wires `retrieval_mode='auto'` into the real recall
  path with tests; IF-NO-WIN documents the negative result and changes no recall code.

### Flow

Agree threshold with PM → build harness → construct query set (known-item + pooled) →
run 3 arms read-only on embedded subset (+ whole corpus as context) → compute metrics →
write verdict into results doc → **decision gate** → { IF-WIN: wire `auto` into recall
path + tests | IF-NO-WIN: close out with documented negative result }.

### Technical Approach

- **Metrics.** Primary: **recall@k** and **MRR** on the known-item set (objective — the
  gold memory either surfaces in top-k or it doesn't). Secondary: **nDCG@k** on the
  pooled LLM-judged set (captures graded partial relevance). Operational: **latency
  p50/p95** per arm (hybrid embeds the query per call; a large latency regression is a
  cost even if quality ties). `k` defaults to 10 (matches `search(limit=10)`).
- **Ground truth — addressed head-on.** No labeled memory-recall eval set exists, so we
  build one two ways: (1) **Known-item**: sample N memories (bias toward embedded +
  high-importance), LLM-generate a realistic query whose answer is that memory; the
  (query→memory_id) map is the objective label. Cheap, reproducible, unbiased between
  arms. (2) **Pooled judgments**: for a smaller query subset, pool both arms' top-k and
  LLM-judge each pair 0-3; nDCG over those judgments. Pooling (vs. judging one arm's
  results) is what keeps the judged metric fair.
- **Fairness / no contamination.** Both arms run against the **same** query set and a
  read-only snapshot. If spike-2 shows either path mutates `access_count`/relevance/outcome
  state, the harness clones the corpus into a throwaway `dbg-hybrideval` partition and runs
  there; `valor` is never mutated. Query embeddings are computed once per query and reused
  across arms.
- **auto vs. forced hybrid.** Run BOTH: forced `hybrid` for the clean quality A/B, and
  `auto` to check whether the selector actually picks hybrid for `Memory` (it should, per
  recon) and whether per-query selection changes anything. Report both.
- **Decision gate (the crux).** Adopt **only if** forced-hybrid beats the current path on
  the **primary** metric in aggregate on the embedded subset by at least a named margin,
  **and** does not regress latency past a named ceiling. Provisional, env-overridable
  thresholds (grain of salt — tune once real numbers land): `HYBRID_EVAL_MIN_RECALL_GAIN`
  (default `0.05` absolute recall@10 gain), `HYBRID_EVAL_MIN_MRR_GAIN` (default `0.03`),
  `HYBRID_EVAL_MAX_LATENCY_REGRESSION_PCT` (default `50`). A tie or loss ⇒ **do not adopt**;
  that is a successful, complete outcome. Aggregate win is the bar — **per-query-shape
  partial adoption is explicitly not this plan's approach** (see No-Gos).
- **IF-WIN wiring.** Route `retrieve_memories` (or `search`) through
  `ContextAssembler(retrieval_mode='auto')` behind a config flag
  (`config.memory_defaults.RETRIEVAL_MODE`, default preserving current behavior until
  cutover), keeping the fail-silent contract and the `superseded_by` filter. Add unit
  tests for the new path; update `tests/unit/test_memory_retrieval.py`.
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
  computed correctly on synthetic fixtures with known answers), read-only guarantee test, and
  the errored-arm-vs-empty-arm distinction test.

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
to `config/settings.py` / `config.memory_defaults` (env-overridable, defaulting to current
behavior until cutover), which propagates via the existing settings mechanism with no
migration. No Popoto schema change (the `Memory` fields already exist), so no
`scripts/update/migrations.py` entry is needed.

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
  judging approach, decision gate with named thresholds) in `docs/features/hybrid-retrieval-eval.md`.
- [ ] The comparison is actually run: current 4-signal RRF vs. forced `hybrid` vs. `auto`, on the
  real `valor` corpus (embedded subset primary, whole corpus as context), read-only.
- [ ] Results are written up with a clear, numeric verdict: adopt or do-not-adopt.
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
- Run spike-1 (query_cues shape), spike-2 (read-only guarantee), spike-3 (embedded-subset count) against a throwaway `dbg-` clone; record findings into Spike Results.

### 2. Build the evaluation harness
- **Task ID**: build-harness
- **Depends On**: spike-all
- **Validates**: tests/unit/test_memory_eval.py (create)
- **Informed By**: spike-1 (cue shape), spike-2 (read-only path), spike-3 (subset)
- **Assigned To**: eval-harness-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/memory_eval/` with query-set construction (known-item + pooled), 3 retrieval arms, metrics (recall@k, MRR, nDCG, latency), read-only snapshot/clone, and a report renderer.
- Add `tests/unit/test_memory_eval.py`: metric correctness on fixtures, read-only assertion, errored-vs-empty-arm distinction.

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
- Run the 3 arms on `valor` (embedded subset primary, whole corpus context); write methodology + results + verdict into `docs/features/hybrid-retrieval-eval.md`.

### 5. DECISION GATE (PM sign-off)
- **Task ID**: decision-gate
- **Depends On**: run-eval
- **Assigned To**: PM
- **Parallel**: false
- Compare measured gains against `HYBRID_EVAL_MIN_RECALL_GAIN` / `HYBRID_EVAL_MIN_MRR_GAIN` / `HYBRID_EVAL_MAX_LATENCY_REGRESSION_PCT`. Route to task 6a (adopt) or 6b (do-not-adopt).

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

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Win threshold sign-off.** Provisional gate: recall@10 gain ≥ 0.05 AND MRR gain ≥ 0.03,
   latency regression ≤ 50%, on the embedded subset. Are these the right bars, or do you want a
   different primary metric / margin before the eval runs?
2. **Embedding backfill.** OK to run a bounded embedding backfill of the `valor` eval subset so
   hybrid's vector signal is meaningfully exercised (only ~18% embedded today), or keep the eval
   strictly on already-embedded records?
3. **Scope of `auto` selector evaluation.** Is auditing that `auto` picks hybrid (plus a clean
   forced-hybrid A/B) sufficient, or do you also want a per-query breakdown of when `auto` would
   diverge — noting that per-query-shape adoption is currently a No-Go?
