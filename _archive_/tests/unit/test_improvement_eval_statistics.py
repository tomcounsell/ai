"""Tests for tools.improvement_eval.statistics (lane 4, task build-stats).

Pins clustered resampling by project over bootstrap_ci, per-endpoint
thresholds, and the seeded spurious-winner suppression: an uncorrected run
over a null-effect family reports a winner at alpha=0.05 while the
Holm-corrected run reports none.
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path

import pytest

from tools.improvement_eval import statistics as stats
from tools.improvement_eval.correction import FixedBatchStoppingRule
from tools.memory_eval import metrics
from tools.memory_eval.metrics import bootstrap_ci

# Calibrated pair: this null-effect family holds exactly one endpoint with a
# raw p-value below 0.05, and Holm lifts every adjusted p-value above 0.05.
NULL_FAMILY_SEED = 1
NULL_EVAL_SEED = 1
NULL_N_RESAMPLES = 5000
NULL_N_ENDPOINTS = 8


def _null_effect_family():
    rng = random.Random(NULL_FAMILY_SEED)
    family = {}
    for i in range(NULL_N_ENDPOINTS):
        family[f"endpoint-{i}"] = {
            "proj-a": [rng.gauss(0.0, 1.0) for _ in range(14)],
            "proj-b": [rng.gauss(0.0, 1.0) for _ in range(13)],
            "proj-c": [rng.gauss(0.0, 1.0) for _ in range(13)],
        }
    return family


def test_metrics_module_is_imported_not_vendored():
    assert stats.bootstrap_ci is metrics.bootstrap_ci
    assert stats.BootstrapCI is metrics.BootstrapCI


def test_metrics_module_is_unmodified():
    repo_root = Path(__file__).resolve().parents[2]
    target = repo_root / "tools" / "memory_eval" / "metrics.py"
    shown = subprocess.run(
        ["git", "-C", str(repo_root), "show", "main:tools/memory_eval/metrics.py"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    assert target.read_text() == shown


def test_single_project_delegates_to_bootstrap_ci():
    deltas = [0.4, -0.1, 0.2, 0.3, 0.0, 0.1]
    assert stats.clustered_bootstrap_ci({"only": deltas}, n_resamples=500, seed=7) == bootstrap_ci(
        deltas, n_resamples=500, seed=7
    )


def test_clustered_ci_counts_every_delta():
    ci = stats.clustered_bootstrap_ci(
        {"a": [0.2, 0.4], "b": [0.1, 0.3, 0.5]}, n_resamples=500, seed=7
    )
    assert ci.n == 5
    assert ci.mean == pytest.approx(0.3)


def test_clustered_ci_marks_a_clear_win_significant():
    ci = stats.clustered_bootstrap_ci({"a": [0.5] * 10, "b": [0.4] * 10}, n_resamples=500, seed=7)
    assert ci.significant
    assert ci.lower > 0.0


def test_clustered_ci_empty_returns_nonsignificant():
    ci = stats.clustered_bootstrap_ci({})
    assert ci.n == 0
    assert not ci.significant


def test_clustered_ci_is_deterministic_for_a_seed():
    deltas = {"a": [0.2, -0.1, 0.3], "b": [0.0, 0.1]}
    first = stats.clustered_bootstrap_ci(deltas, n_resamples=500, seed=11)
    second = stats.clustered_bootstrap_ci(deltas, n_resamples=500, seed=11)
    assert first == second


def test_clustering_changes_the_interval():
    pooled = [0.9, 0.8, 0.7, -0.9, -0.8, -0.7]
    split = {"a": pooled[:3], "b": pooled[3:]}
    lumped = {"a": pooled[::2], "b": pooled[1::2]}
    split_ci = stats.clustered_bootstrap_ci(split, n_resamples=2000, seed=13)
    lumped_ci = stats.clustered_bootstrap_ci(lumped, n_resamples=2000, seed=13)
    assert (split_ci.lower, split_ci.upper) != (lumped_ci.lower, lumped_ci.upper)


def test_endpoint_p_value_is_zero_for_a_certain_win():
    assert stats.endpoint_p_value({"a": [0.5] * 12, "b": [0.4] * 12}) == 0.0


@pytest.mark.parametrize("deltas", [{"a": [0.0] * 12}, {"a": [-0.3] * 8, "b": [-0.1] * 8}, {}])
def test_endpoint_p_value_is_one_without_evidence(deltas):
    assert stats.endpoint_p_value(deltas) == 1.0


def test_holm_suppresses_the_spurious_winner():
    outcomes = stats.evaluate_family(
        _null_effect_family(), n_resamples=NULL_N_RESAMPLES, seed=NULL_EVAL_SEED
    )
    assert len(outcomes) == NULL_N_ENDPOINTS
    assert [o.name for o in outcomes] == sorted(f"endpoint-{i}" for i in range(NULL_N_ENDPOINTS))
    raw_p = [o.raw_p_value for o in outcomes]
    assert min(raw_p) < stats.DEFAULT_ALPHA
    assert stats.winners(outcomes, corrected=False) != []
    assert stats.winners(outcomes, corrected=True) == []
    rule = FixedBatchStoppingRule(batch_size=40, n_endpoints=NULL_N_ENDPOINTS)
    assert rule.describe() == f"holm; fixed-batch(n=40, endpoints={NULL_N_ENDPOINTS})"


def test_winners_respect_the_margin():
    outcomes = stats.evaluate_family({"endpoint-0": {"a": [0.5] * 12, "b": [0.4] * 12}})
    assert outcomes[0].lower > 0.0
    assert outcomes[0].adjusted_p_value < stats.DEFAULT_ALPHA
    assert stats.winners(outcomes, corrected=True) == ["endpoint-0"]
    strict = {"endpoint-0": stats.EndpointThreshold(name="endpoint-0", margin=10.0)}
    assert stats.winners(outcomes, strict, corrected=True) == []
