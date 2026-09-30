# Hybrid Retrieval Evaluation (popoto ContextAssembler vs. four-signal RRF)

**Issue:** [#2082](https://github.com/tomcounsell/ai/issues/2082) · **Plan:** `docs/plans/hybrid-retrieval-eval.md` · **Measured:** 2026-07-17

## Verdict: ADOPT

popoto 1.8.0's `ContextAssembler(retrieval_mode='auto')` hybrid BM25+vector
retrieval measurably beats the previous four-signal RRF memory-recall path on
the live `valor` corpus, clearing every layer of the pre-registered decision
gate. The recall path now defaults to hybrid (`RETRIEVAL_MODE=auto`), with a
fail-silent fallback to the four-signal path and a single-flag revert
(`RETRIEVAL_MODE=current`).

## Results (seed 42, k=10, n_known_item=60, 0 errored queries in either arm)

| Metric | Current (4-signal RRF) | Forced hybrid (BM25+vector) |
|--------|------------------------|------------------------------|
| recall@10 | 0.933 | **1.000** |
| MRR | 0.336 | **0.877** |
| latency p50 (ms) | 510.5 | 484.9 |
| latency p95 (ms) | 704.7 | 645.9 |

Decision-gate evaluation (all four must clear; knobs in
`config/settings.py::HybridEvalSettings`, env-overridable, provisional):

| Gate layer | Threshold | Measured | Clears |
|------------|-----------|----------|--------|
| Mean recall@10 gain | > 0.05 (`HYBRID_EVAL_MIN_RECALL_GAIN`) | +0.067 | yes |
| Mean MRR gain | > 0.03 (`HYBRID_EVAL_MIN_MRR_GAIN`) | +0.542 | yes |
| 95% CI lower bound on paired recall delta | > 0 (significance floor) | +0.017 (CI [+0.017, +0.133], `significant: true`) | yes |
| p95 latency regression | <= 50% (`HYBRID_EVAL_MAX_LATENCY_REGRESSION_PCT`) | -8.3% (hybrid is faster) | yes |

Proximity-gated corroboration (built because the recall gain landed near the
threshold, `|0.067 - 0.05| < 0.05`): pooled 0-3 LLM-judged nDCG@10 over 15
queries — hybrid **0.858** vs current **0.690**. Corroborates the known-item
verdict; per the methodology it could not have flipped it.

Corpus state at measurement: 243 active `valor` records, **100%** embedded at
the corpus dimension (1536). Embedding coverage was the plan's biggest
anticipated confound (recon estimated ~18%); by execution time the backfill
reflection had closed the gap entirely, so the embedded-subset-vs-whole-corpus
split and the plan's optional `--backfill-embeddings` branch were both moot.

## Methodology

Read-only, offline, two-arm comparison on the live `valor` partition
(`tools/memory_eval/`, entry `python -m tools.memory_eval.hybrid_eval`).

- **Arms.** (a) The production path exactly as it shipped:
  `agent/memory_retrieval.py::retrieve_memories` four-signal RRF (BM25 +
  temporal decay + confidence + embedding cosine). (b) Forced
  `ContextAssembler(Memory, {}, retrieval_mode='hybrid').assemble(query_cues=...,
  partition_filters=...)`. `retrieval_mode='auto'` is not a third arm — it is
  covered by a single schema-level assertion that `auto` resolves to `hybrid`
  for `Memory` (the model has both a `BM25Field` and an `EmbeddingField`).
- **Ground truth: known-item retrieval (sole gate driver).** 60 records
  sampled (seeded, importance-weighted, restricted to
  current-provider-dimension-valid embeddings) from the corpus snapshot; for
  each, an LLM (PydanticAI/Haiku via `agent.llm.run_typed`) generated one
  realistic paraphrased query whose gold answer is that record. Degenerate or
  verbatim-parroting generations are skipped, never scored.
- **Statistical floor.** Paired per-query recall deltas, bootstrap-resampled
  (10,000 resamples, seeded) for a 95% CI; adoption required the CI lower
  bound > 0 in addition to the point-estimate bars.
- **Pooled judgments (conditional corroboration).** Only built when the
  known-item result lands near the threshold: union of both arms' top-10 per
  query, each (query, memory) pair LLM-graded 0-3, nDCG@10 over the grades.
  Pooling both arms keeps the graded metric fair to each retriever.
- **Error discipline.** A query that errors in either arm is excluded from
  scoring (never a fake zero-recall data point); an arm error rate > 20%
  aborts the run as broken rather than recording a verdict.
- **Latency fairness.** The harness's per-query non-zero-vector probe (an
  extra provider HTTP call, pure instrumentation) runs outside the latency
  clock. An earlier run that timed it showed +69.3% p95 regression and a
  false do-not-adopt on the latency gate; the corrected run measures the
  retrieval path only.

### The embedding-provider hard gate (and the production bug it caught)

`Memory.embedding` is a `GracefulEmbeddingField`: when the provider is absent
or dimension-mismatched, vector search degrades **silently** to BM25-only. A
degraded "hybrid" run would produce a confident, wrong verdict. The harness
therefore fails closed, before any scoring, on two checks:

1. a default embedding provider is configured, and
2. the provider's output dimension matches the stored corpus vectors
   (per-record `.npy` probe), plus a per-query non-zero-vector assertion.

This gate caught a real production defect: the stored corpus is uniformly
**1536-dim**, written by popoto's `OpenAIProvider` (configured by the bridge
at startup), while `agent/embedding_provider.py` configured a **768-dim**
Ollama provider in worker/CLI processes — so the recall path's cosine signal
was silently dead everywhere except the bridge. The cutover fixed this:
`configure_embedding_provider()` now installs the corpus-matched
`OpenAIProvider` in every process (graceful `None` when `OPENAI_API_KEY` is
unavailable; vectorless saves heal via the `memory-embedding-backfill`
reflection, whose provider probe was updated to match).

## What shipped

- **Recall cutover** (`agent/memory_retrieval.py`): `retrieve_memories()`
  dispatches on `settings.hybrid_eval.retrieval_mode`. `auto` (default)
  routes through `ContextAssembler(retrieval_mode='auto')` with rank-based
  `score` attributes and the `superseded_by` filter preserved; any hybrid
  failure or empty result falls back fail-silently to the four-signal RRF
  path, which remains intact as `_retrieve_memories_rrf`. The public
  `search()` signature and the MCP `memory_search` surface are unchanged.
- **Single config home** (`config/settings.py::HybridEvalSettings`): the
  three gate knobs plus `RETRIEVAL_MODE` (env-overridable; `current` reverts
  the cutover with no code change).
- **Reusable harness** (`tools/memory_eval/`): metrics (recall@k, MRR,
  nDCG@10, bootstrap CI, latency percentiles), LLM query-set construction,
  two retrieval arms, hard gates, report renderer. Re-run after future popoto
  upgrades or corpus shifts:
  `.venv/bin/python -m tools.memory_eval.hybrid_eval --project valor`.
- **Tests**: `tests/unit/test_memory_eval.py` (27 tests: metric correctness,
  CI significance floor, gate layers, proximity gate, provider hard gate,
  zero-vector abort, errored-vs-empty distinction, read-only guarantee,
  report fields) and `tests/unit/test_memory_retrieval.py`
  (`TestRetrievalModeDispatch`, `TestConfigureEmbeddingProvider`, legacy-path
  tests pinned to `RETRIEVAL_MODE=current`).

## Caveats

- The gate thresholds remain provisional/tunable; this was their first use
  with real numbers and they behaved sensibly (the CI floor was the binding
  constraint at +0.017).
- The known-item query set is LLM-generated; labels are objective
  (query → source record) and arm-independent, but the query *style*
  reflects one generator model. The pooled nDCG corroboration partially
  hedges this.
- Latency was measured with both arms embedding queries via the same OpenAI
  provider in one process; absolute numbers are network-dependent, the
  comparison is same-condition.

## See also

- `docs/features/subconscious-memory.md` — recall-path description
- `docs/plans/hybrid-retrieval-eval.md` — full methodology plan, spikes,
  critique record, decision record
