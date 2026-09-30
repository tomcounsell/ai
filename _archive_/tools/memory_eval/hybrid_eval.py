"""Read-only two-arm eval: popoto hybrid retrieval vs. the current 4-signal RRF path.

Entry point: ``.venv/bin/python -m tools.memory_eval.hybrid_eval --project valor``

Methodology: docs/plans/hybrid-retrieval-eval.md. Results + verdict:
docs/features/hybrid-retrieval-eval.md.

Order of operations (each failure exits non-zero BEFORE any scoring):

1. :func:`configure_corpus_provider` -- load the repo ``.env`` and configure
   popoto's ``OpenAIProvider`` (1536-dim), the SAME provider the bridge
   configures in production (``bridge/telegram_bridge.py``) and the one that
   wrote every vector in the on-disk corpus cache. The repo's implicit
   Ollama provider (768-dim) is dimension-mismatched with that corpus and
   would silently degrade the hybrid arm to BM25-only (Concern 1).
2. Hard gate: provider presence + provider/corpus dimension match
   (``tools/memory_eval/provider_gate.py``).
3. ``assert_auto_resolves_to_hybrid()`` -- single schema-level assertion
   (NOT a third measurement arm) de-risking the IF-WIN cutover that ships
   ``retrieval_mode='auto'``.
4. Snapshot the corpus into memory (read-only; the harness never mutates
   the evaluated partition).
5. Embedding-coverage report (naive vs. current-provider-valid counts).
6. Known-item query-set construction (LLM-generated, seeded sampling,
   importance-weighted; the sole gate driver).
7. Two arms per query: current 4-signal RRF (``retrieve_memories``) and
   forced ``retrieval_mode='hybrid'`` (``ContextAssembler.assemble``) with
   the per-query non-zero-vector assertion. Errored queries are EXCLUDED
   from scoring (never masquerade as zero-recall data points); an arm whose
   error rate exceeds ``MAX_ARM_ERROR_RATE`` aborts the run as broken.
8. Metrics: recall@k + MRR per arm, paired per-query recall deltas ->
   bootstrap 95% CI (``n_known_item``, ``significant``), latency p50/p95.
9. Proximity check (Concern 4): the pooled 0-3 LLM-judgment / nDCG pass is
   built ONLY when the mean recall gain lands near the threshold.
10. Decision gate (single config home ``config/settings.py`` ->
    ``settings.hybrid_eval``): adopt iff mean recall gain >
    ``min_recall_gain`` AND mean MRR gain > ``min_mrr_gain`` AND the 95% CI
    lower bound on the paired recall delta is > 0 AND the hybrid arm's p95
    latency regression is <= ``max_latency_regression_pct``.
11. Emit a JSON report + a human-readable table with the verdict.

The plan's optional ``--backfill-embeddings`` flag was dropped: measured
coverage is 100% at the corpus dimension (2026-07-17), so the bounded
backfill branch it gated is moot -- there is nothing to backfill.

This module is a dev-invoked measurement tool. It is never imported by the
live recall path and makes no writes to any Popoto-managed key.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Abort threshold: if more than this fraction of known-item queries errored
# in either arm, the run is BROKEN (not a verdict). Provisional/tunable --
# grain of salt applies.
MAX_ARM_ERROR_RATE = 0.20

# Number of scored queries fed to the (conditional) pooled/nDCG pass.
# Provisional/tunable -- bounds LLM-judging cost. Grain of salt applies.
POOLED_PASS_QUERY_CAP = 15

# Sample size for the dimension-match hard gate's stored-vector probe.
# Provisional/tunable. Grain of salt applies.
DIMENSION_GATE_SAMPLE = 10


def configure_corpus_provider() -> object:
    """Configure the corpus-matched embedding provider (popoto OpenAIProvider).

    Loads the repo ``.env`` (symlink to the secrets vault) so
    ``OPENAI_API_KEY`` is present, then configures the same provider the
    bridge uses in production. Returns the provider instance.
    """
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")

    import popoto
    from popoto.embeddings.openai import OpenAIProvider

    provider = OpenAIProvider()
    popoto.configure(embedding_provider=provider)
    return provider


def load_corpus_snapshot(project_key: str) -> list:
    """Read-only snapshot of the active (non-superseded) corpus."""
    from models.memory import Memory

    records = list(Memory.query.filter(project_key=project_key))
    return [r for r in records if not (getattr(r, "superseded_by", "") or "").strip()]


def evaluate_decision_gate(
    *,
    mean_recall_gain: float,
    mean_mrr_gain: float,
    ci_significant: bool,
    latency_regression_pct: float,
    gate_settings,
) -> dict:
    """Apply the two-layer decision gate (plan Decision Record item 1).

    Layer 1: point-estimate bars (recall gain, MRR gain, latency ceiling).
    Layer 2: statistical floor (bootstrap 95% CI lower bound > 0).
    All must clear for an adopt verdict.
    """
    checks = {
        "recall_gain_clears_bar": mean_recall_gain > gate_settings.min_recall_gain,
        "mrr_gain_clears_bar": mean_mrr_gain > gate_settings.min_mrr_gain,
        "ci_lower_bound_positive": ci_significant,
        "latency_within_ceiling": latency_regression_pct
        <= gate_settings.max_latency_regression_pct,
    }
    verdict = "adopt" if all(checks.values()) else "do-not-adopt"
    return {"checks": checks, "verdict": verdict}


def proximity_check(mean_recall_gain: float, min_recall_gain: float) -> bool:
    """Concern 4: build the pooled/nDCG corroboration pass only near the threshold."""
    return abs(mean_recall_gain - min_recall_gain) < min_recall_gain


def run_eval(
    *,
    project_key: str,
    k: int,
    n_queries: int,
    seed: int,
    known_items: list | None = None,
) -> dict:
    """Run the full two-arm comparison and return the report dict.

    ``known_items`` may be injected (tests / re-runs); when ``None`` the
    known-item set is LLM-generated from the corpus snapshot.
    """
    from config.settings import settings
    from tools.memory_eval import metrics as m
    from tools.memory_eval.embedding_coverage import coverage_report, record_embedding_coverage
    from tools.memory_eval.provider_gate import (
        assert_provider_available,
        assert_provider_dimension_match,
    )
    from tools.memory_eval.query_set import build_known_item_set, build_pooled_judgments
    from tools.memory_eval.retrieval_arms import (
        assert_auto_resolves_to_hybrid,
        run_current_arm,
        run_hybrid_arm,
    )

    gate_settings = settings.hybrid_eval

    # --- Hard gates (Concern 1): abort non-zero before ANY scoring -------
    provider = assert_provider_available()
    snapshot = load_corpus_snapshot(project_key)
    if not snapshot:
        raise RuntimeError(f"Empty corpus for project_key={project_key!r}; nothing to evaluate.")
    dimension = assert_provider_dimension_match(provider, snapshot[:DIMENSION_GATE_SAMPLE])
    assert_auto_resolves_to_hybrid()

    coverage = coverage_report(snapshot, dimension)
    eligible = [
        r for r in snapshot if record_embedding_coverage(r, dimension).current_provider_valid
    ]
    logger.info(
        "[memory_eval] corpus=%d eligible(dimension-valid)=%d provider_dim=%d",
        len(snapshot),
        len(eligible),
        dimension,
    )

    # --- Ground truth (known-item set, the sole gate driver) -------------
    if known_items is None:
        known_items = build_known_item_set(eligible, n_queries=n_queries, seed=seed)
    if not known_items:
        raise RuntimeError("Known-item set is empty -- cannot score anything.")

    # --- Two arms, read-only ---------------------------------------------
    content_by_id = {r.memory_id: (getattr(r, "content", "") or "") for r in snapshot}
    per_query = []
    for item in known_items:
        cur = run_current_arm(item.query, project_key, k)
        hyb = run_hybrid_arm(item.query, project_key, k, assert_nonzero_vector=True)
        per_query.append({"item": item, "current": cur, "hybrid": hyb})

    n_total = len(per_query)
    cur_errors = sum(1 for q in per_query if q["current"].errored)
    hyb_errors = sum(1 for q in per_query if q["hybrid"].errored)
    for arm_name, err_count in (("current", cur_errors), ("hybrid", hyb_errors)):
        if err_count / n_total > MAX_ARM_ERROR_RATE:
            raise RuntimeError(
                f"BROKEN RUN: {arm_name} arm errored on {err_count}/{n_total} queries "
                f"(> {MAX_ARM_ERROR_RATE:.0%}). This is an error state, not a verdict "
                "(plan Failure Path: a broken arm must never masquerade as a loss)."
            )

    scored = [q for q in per_query if not q["current"].errored and not q["hybrid"].errored]
    if not scored:
        raise RuntimeError("No scorable queries (all errored in at least one arm).")

    # --- Metrics ----------------------------------------------------------
    cur_recalls, hyb_recalls, cur_mrrs, hyb_mrrs = [], [], [], []
    for q in scored:
        gold = q["item"].gold_memory_id
        cur_recalls.append(m.recall_at_k(gold, q["current"].memory_ids, k))
        hyb_recalls.append(m.recall_at_k(gold, q["hybrid"].memory_ids, k))
        cur_mrrs.append(m.mrr(gold, q["current"].memory_ids))
        hyb_mrrs.append(m.mrr(gold, q["hybrid"].memory_ids))

    n = len(scored)
    mean_cur_recall = sum(cur_recalls) / n
    mean_hyb_recall = sum(hyb_recalls) / n
    mean_cur_mrr = sum(cur_mrrs) / n
    mean_hyb_mrr = sum(hyb_mrrs) / n
    mean_recall_gain = mean_hyb_recall - mean_cur_recall
    mean_mrr_gain = mean_hyb_mrr - mean_cur_mrr

    paired_deltas = [h - c for h, c in zip(hyb_recalls, cur_recalls)]
    ci = m.bootstrap_ci(paired_deltas, seed=seed)

    cur_latency = m.latency_percentiles(
        [q["current"].latency_ms for q in per_query if not q["current"].errored]
    )
    hyb_latency = m.latency_percentiles(
        [q["hybrid"].latency_ms for q in per_query if not q["hybrid"].errored]
    )
    latency_regression_pct = (
        ((hyb_latency["p95"] - cur_latency["p95"]) / cur_latency["p95"] * 100.0)
        if cur_latency["p95"] > 0
        else 0.0
    )

    # --- Conditional pooled/nDCG corroboration (Concern 4) ---------------
    pooled_built = proximity_check(mean_recall_gain, gate_settings.min_recall_gain)
    ndcg_summary = None
    if pooled_built:
        cur_ndcgs, hyb_ndcgs = [], []
        for q in scored[:POOLED_PASS_QUERY_CAP]:
            pooled_ids = set(q["current"].memory_ids[:k]) | set(q["hybrid"].memory_ids[:k])
            pooled = {mid: content_by_id.get(mid, "") for mid in pooled_ids}
            grades = build_pooled_judgments(q["item"].query, pooled)
            cur_ndcgs.append(m.ndcg_at_k(grades, q["current"].memory_ids, k))
            hyb_ndcgs.append(m.ndcg_at_k(grades, q["hybrid"].memory_ids, k))
        if cur_ndcgs:
            ndcg_summary = {
                "n_pooled_queries": len(cur_ndcgs),
                "current_mean_ndcg": sum(cur_ndcgs) / len(cur_ndcgs),
                "hybrid_mean_ndcg": sum(hyb_ndcgs) / len(hyb_ndcgs),
            }

    # --- Decision gate ----------------------------------------------------
    gate = evaluate_decision_gate(
        mean_recall_gain=mean_recall_gain,
        mean_mrr_gain=mean_mrr_gain,
        ci_significant=ci.significant,
        latency_regression_pct=latency_regression_pct,
        gate_settings=gate_settings,
    )

    return {
        "project_key": project_key,
        "k": k,
        "seed": seed,
        "corpus_size": len(snapshot),
        "coverage": coverage,
        "provider_dimension": dimension,
        "n_known_item": n,
        "n_generated": n_total,
        "error_counts": {"current": cur_errors, "hybrid": hyb_errors},
        "recall_at_k": {"current": mean_cur_recall, "hybrid": mean_hyb_recall},
        "mrr": {"current": mean_cur_mrr, "hybrid": mean_hyb_mrr},
        "mean_recall_gain": mean_recall_gain,
        "mean_mrr_gain": mean_mrr_gain,
        "recall_delta_ci_95": {"lower": ci.lower, "upper": ci.upper, "mean": ci.mean},
        "significant": ci.significant,
        "latency_ms": {"current": cur_latency, "hybrid": hyb_latency},
        "latency_regression_pct": latency_regression_pct,
        "pooled_pass_built": pooled_built,
        "ndcg": ndcg_summary,
        "gate": gate,
        "verdict": gate["verdict"],
        "queries": [
            {
                "query": q["item"].query,
                "gold": q["item"].gold_memory_id,
                "current_errored": q["current"].errored,
                "hybrid_errored": q["hybrid"].errored,
                "current_top": q["current"].memory_ids[:k],
                "hybrid_top": q["hybrid"].memory_ids[:k],
            }
            for q in per_query
        ],
    }


def render_table(report: dict) -> str:
    """Human-readable results table + verdict (also embeddable in the results doc)."""
    ci = report["recall_delta_ci_95"]
    lat = report["latency_ms"]
    lines = [
        "| Metric | Current (4-signal RRF) | Forced hybrid (BM25+vector) |",
        "|--------|------------------------|------------------------------|",
        f"| recall@{report['k']} | {report['recall_at_k']['current']:.3f}"
        f" | {report['recall_at_k']['hybrid']:.3f} |",
        f"| MRR | {report['mrr']['current']:.3f} | {report['mrr']['hybrid']:.3f} |",
        f"| latency p50 (ms) | {lat['current']['p50']:.1f} | {lat['hybrid']['p50']:.1f} |",
        f"| latency p95 (ms) | {lat['current']['p95']:.1f} | {lat['hybrid']['p95']:.1f} |",
        f"| errored queries | {report['error_counts']['current']}"
        f" | {report['error_counts']['hybrid']} |",
        "",
        f"n_known_item: {report['n_known_item']} (generated: {report['n_generated']})",
        f"mean recall gain: {report['mean_recall_gain']:+.3f}"
        f" (95% CI [{ci['lower']:+.3f}, {ci['upper']:+.3f}], significant: {report['significant']})",
        f"mean MRR gain: {report['mean_mrr_gain']:+.3f}",
        f"latency p95 regression: {report['latency_regression_pct']:+.1f}%",
        f"embedding coverage: {report['coverage']['current_provider_valid_count']}"
        f"/{report['coverage']['total']} current-provider-valid"
        f" ({report['coverage']['current_provider_valid_pct']:.0f}%)",
        f"pooled/nDCG pass built: {report['pooled_pass_built']}"
        + (f" -> {report['ndcg']}" if report.get("ndcg") else ""),
        "",
        f"VERDICT: {report['verdict']}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--project", default="valor")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--n-queries", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "docs" / "features" / "hybrid-retrieval-eval-results.json"),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    configure_corpus_provider()
    try:
        report = run_eval(
            project_key=args.project, k=args.k, n_queries=args.n_queries, seed=args.seed
        )
    except Exception as e:
        logger.error("[memory_eval] ABORT (no verdict recorded): %s", e)
        return 1

    Path(args.output).write_text(json.dumps(report, indent=2, default=str))
    print(render_table(report))
    print(f"\nFull report: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
