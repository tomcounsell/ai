"""Per-endpoint thresholds and clustered bootstrap evaluation.

Paired per-trial deltas arrive grouped by project. Confidence intervals come
from two-stage cluster resampling (resample projects, then resample deltas
within each drawn project); with a single project this delegates directly to
``bootstrap_ci``. Per-endpoint one-sided p-values come from the same
resample distribution, so the CI and the p-value for an endpoint always agree
with each other. Raw p-values go through ``holm_adjust`` before any winner
is declared.

``bootstrap_ci`` and ``BootstrapCI`` are imported from
``tools.memory_eval.metrics`` and that module is not modified here; the
single-project path calls the imported function directly.

Pure Python; only the standard library.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tools.improvement_eval.correction import holm_adjust
from tools.memory_eval.metrics import BootstrapCI, bootstrap_ci

DEFAULT_ALPHA = 0.05
DEFAULT_MARGIN = 0.0
DEFAULT_N_RESAMPLES = 10_000
DEFAULT_CONFIDENCE = 0.95
DEFAULT_SEED = 42


@dataclass(frozen=True)
class EndpointThreshold:
    """What one endpoint must clear: a mean margin and a significance level."""

    name: str
    margin: float = DEFAULT_MARGIN
    alpha: float = DEFAULT_ALPHA


@dataclass(frozen=True)
class EndpointOutcome:
    """One endpoint's measurement: cluster CI plus raw and Holm-adjusted p-values."""

    name: str
    mean: float
    lower: float
    upper: float
    n: int
    raw_p_value: float
    adjusted_p_value: float


def _clusters(deltas_by_project: Mapping[str, Sequence[float]]) -> list[list[float]]:
    """Project deltas in deterministic (sorted-name) order; refuses empty clusters."""
    clusters = []
    for name in sorted(deltas_by_project):
        values = [float(v) for v in deltas_by_project[name]]
        if not values:
            raise ValueError(f"project {name!r} has no deltas")
        clusters.append(values)
    return clusters


def _cluster_resample_means(
    clusters: list[list[float]], *, n_resamples: int, seed: int
) -> list[float]:
    """Sorted replicate means under two-stage cluster resampling."""
    n_clusters = len(clusters)
    total_per_replicate = sum(len(cluster) for cluster in clusters)
    rng = random.Random(seed)
    means = []
    for _ in range(n_resamples):
        total = 0.0
        for _ in range(n_clusters):
            cluster = clusters[rng.randrange(n_clusters)]
            width = len(cluster)
            for _ in range(width):
                total += cluster[rng.randrange(width)]
        means.append(total / total_per_replicate)
    means.sort()
    return means


def _percentile_interval(
    sorted_means: list[float], *, n_resamples: int, confidence: float
) -> tuple[float, float]:
    """Percentile interval using the same index convention as ``bootstrap_ci``."""
    alpha = (1.0 - confidence) / 2.0
    lower = sorted_means[int(alpha * n_resamples)]
    upper = sorted_means[min(n_resamples - 1, int((1.0 - alpha) * n_resamples))]
    return lower, upper


def clustered_bootstrap_ci(
    deltas_by_project: Mapping[str, Sequence[float]],
    *,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int = DEFAULT_SEED,
) -> BootstrapCI:
    """Cluster bootstrap CI on the pooled mean of per-project deltas.

    A single project delegates to the imported ``bootstrap_ci`` directly. An
    empty mapping returns the degenerate non-significant interval, matching
    ``bootstrap_ci`` on empty input.
    """
    clusters = _clusters(deltas_by_project)
    if not clusters:
        return bootstrap_ci([], n_resamples=n_resamples, confidence=confidence, seed=seed)
    if len(clusters) == 1:
        return bootstrap_ci(clusters[0], n_resamples=n_resamples, confidence=confidence, seed=seed)
    means = _cluster_resample_means(clusters, n_resamples=n_resamples, seed=seed)
    lower, upper = _percentile_interval(means, n_resamples=n_resamples, confidence=confidence)
    pooled = [value for cluster in clusters for value in cluster]
    mean = sum(pooled) / len(pooled)
    return BootstrapCI(mean=mean, lower=lower, upper=upper, n=len(pooled), significant=lower > 0.0)


def endpoint_p_value(
    deltas_by_project: Mapping[str, Sequence[float]],
    *,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> float:
    """One-sided bootstrap p-value for H0 (mean <= 0) against H1 (mean > 0).

    The fraction of cluster-resample means at or below zero. No data means no
    evidence, so an empty mapping returns 1.0 and never clears a threshold.
    """
    clusters = _clusters(deltas_by_project)
    if not clusters:
        return 1.0
    means = _cluster_resample_means(clusters, n_resamples=n_resamples, seed=seed)
    return sum(1 for mean in means if mean <= 0.0) / n_resamples


def evaluate_family(
    deltas_by_endpoint: Mapping[str, Mapping[str, Sequence[float]]],
    *,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int = DEFAULT_SEED,
) -> list[EndpointOutcome]:
    """CI plus raw and Holm-adjusted p-values for every endpoint, by name.

    Endpoint ``i`` in sorted-name order evaluates under ``seed + i``, and the
    CI and the p-value for an endpoint share that seed, so both summarize the
    same resample distribution.
    """
    names = sorted(deltas_by_endpoint)
    raw_p_values = [
        endpoint_p_value(deltas_by_endpoint[name], n_resamples=n_resamples, seed=seed + i)
        for i, name in enumerate(names)
    ]
    adjusted_p_values = holm_adjust(raw_p_values)
    outcomes = []
    for i, name in enumerate(names):
        ci = clustered_bootstrap_ci(
            deltas_by_endpoint[name], n_resamples=n_resamples, confidence=confidence, seed=seed + i
        )
        outcomes.append(
            EndpointOutcome(
                name=name,
                mean=ci.mean,
                lower=ci.lower,
                upper=ci.upper,
                n=ci.n,
                raw_p_value=raw_p_values[i],
                adjusted_p_value=adjusted_p_values[i],
            )
        )
    return outcomes


def winners(
    outcomes: Sequence[EndpointOutcome],
    thresholds: Mapping[str, EndpointThreshold] | None = None,
    *,
    corrected: bool = True,
) -> list[str]:
    """Names of endpoints clearing margin, CI floor, and the p-value bar.

    The p-value bar uses Holm-adjusted values when ``corrected`` and raw
    values otherwise; an endpoint missing from ``thresholds`` clears the
    default margin at the default alpha.
    """
    resolved = thresholds or {}
    names = []
    for outcome in outcomes:
        threshold = resolved.get(outcome.name, EndpointThreshold(name=outcome.name))
        p_value = outcome.adjusted_p_value if corrected else outcome.raw_p_value
        if outcome.mean >= threshold.margin and outcome.lower > 0.0 and p_value < threshold.alpha:
            names.append(outcome.name)
    return names
