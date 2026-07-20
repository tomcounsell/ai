"""Retrieval-quality metrics for the hybrid-retrieval eval harness.

Pure functions over id lists / graded judgments -- no Redis, no popoto, no
network. Metric correctness is unit-tested on synthetic fixtures with known
answers (tests/unit/test_memory_eval.py), per the plan's Failure Path Test
Strategy (docs/plans/hybrid-retrieval-eval.md).

Known-item metrics (recall@k, MRR) take a single gold id because the
known-item ground-truth construction maps each generated query to exactly
one gold memory (the record the query was generated from). nDCG@k takes a
0-3 graded judgment dict (pooled LLM judgments, conditional corroboration).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


def recall_at_k(gold_id: str, retrieved_ids: list[str], k: int) -> float:
    """Known-item recall@k: 1.0 iff the gold id appears in the top-k."""
    return 1.0 if gold_id in retrieved_ids[:k] else 0.0


def mrr(gold_id: str, retrieved_ids: list[str]) -> float:
    """Reciprocal rank of the gold id (1-based), or 0.0 if absent."""
    for i, rid in enumerate(retrieved_ids, start=1):
        if rid == gold_id:
            return 1.0 / i
    return 0.0


def ndcg_at_k(graded: dict[str, int], retrieved_ids: list[str], k: int) -> float:
    """nDCG@k over 0-3 graded relevance judgments.

    ``graded`` maps memory_id -> integer grade 0-3 (pooled LLM judgments).
    Ids absent from ``graded`` are treated as grade 0. Returns 0.0 when the
    ideal DCG is 0 (no positively-graded items exist).
    """
    dcg = 0.0
    for i, rid in enumerate(retrieved_ids[:k], start=1):
        rel = graded.get(rid, 0)
        dcg += (2**rel - 1) / math.log2(i + 1)
    ideal_grades = sorted(graded.values(), reverse=True)[:k]
    idcg = sum((2**rel - 1) / math.log2(i + 1) for i, rel in enumerate(ideal_grades, start=1))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


@dataclass(frozen=True)
class BootstrapCI:
    """Bootstrap confidence interval on the mean of paired per-query deltas.

    ``significant`` is the decision gate's Layer-2 floor (plan Concern 2):
    True iff the CI lower bound is strictly > 0 -- the measured win is not
    an artifact of sampling noise.
    """

    mean: float
    lower: float
    upper: float
    n: int
    significant: bool


def bootstrap_ci(
    paired_deltas: list[float],
    *,
    n_resamples: int = 10_000,
    confidence: float = 0.95,
    seed: int = 42,
) -> BootstrapCI:
    """Bootstrap a confidence interval on the mean of ``paired_deltas``.

    Deterministic for a given seed. With fewer than 2 deltas the interval
    collapses to the point estimate and is never significant (a one-query
    eval must not clear the statistical floor).
    """
    n = len(paired_deltas)
    if n == 0:
        return BootstrapCI(mean=0.0, lower=0.0, upper=0.0, n=0, significant=False)
    mean = sum(paired_deltas) / n
    if n < 2:
        return BootstrapCI(mean=mean, lower=mean, upper=mean, n=n, significant=False)

    rng = random.Random(seed)
    resample_means = []
    for _ in range(n_resamples):
        sample = [paired_deltas[rng.randrange(n)] for _ in range(n)]
        resample_means.append(sum(sample) / n)
    resample_means.sort()
    alpha = (1.0 - confidence) / 2.0
    lo_idx = int(alpha * n_resamples)
    hi_idx = min(n_resamples - 1, int((1.0 - alpha) * n_resamples))
    lower = resample_means[lo_idx]
    upper = resample_means[hi_idx]
    return BootstrapCI(mean=mean, lower=lower, upper=upper, n=n, significant=lower > 0.0)


def latency_percentiles(values_ms: list[float]) -> dict[str, float]:
    """p50/p95 latency (nearest-rank) over per-query latencies in ms."""
    if not values_ms:
        return {"p50": 0.0, "p95": 0.0}
    ordered = sorted(values_ms)
    n = len(ordered)

    def nearest_rank(p: float) -> float:
        rank = max(1, math.ceil(p * n))
        return ordered[rank - 1]

    return {"p50": nearest_rank(0.50), "p95": nearest_rank(0.95)}
