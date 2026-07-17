"""Unit tests for the hybrid-retrieval eval harness (tools/memory_eval/).

Covers the plan's Failure Path Test Strategy (docs/plans/hybrid-retrieval-eval.md):
metric correctness on synthetic fixtures, the errored-vs-empty-arm
distinction, the provider hard gate + zero-vector abort (Concern 1), the
bootstrap-CI significance floor (Concern 2), the proximity gate (Concern 4),
the read-only guarantee, and report field presence. No network, no LLM
calls, no live-Redis writes -- everything external is monkeypatched.

Marker: sdlc (feature tests for issue #2082).
"""

from __future__ import annotations

import pytest

from tools.memory_eval import metrics as m
from tools.memory_eval.hybrid_eval import (
    evaluate_decision_gate,
    proximity_check,
    render_table,
)
from tools.memory_eval.provider_gate import (
    ProviderDimensionMismatchError,
    ProviderUnavailableError,
    assert_provider_available,
    assert_provider_dimension_match,
)
from tools.memory_eval.query_set import KnownItem, _is_degenerate
from tools.memory_eval.retrieval_arms import run_current_arm, run_hybrid_arm

pytestmark = pytest.mark.sdlc


# ---------------------------------------------------------------------------
# Metric correctness on synthetic fixtures with known answers
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_recall_at_k_hit_and_miss(self):
        assert m.recall_at_k("g", ["a", "g", "b"], k=3) == 1.0
        assert m.recall_at_k("g", ["a", "b", "g"], k=2) == 0.0
        assert m.recall_at_k("g", [], k=10) == 0.0

    def test_mrr_positions(self):
        assert m.mrr("g", ["g", "a"]) == 1.0
        assert m.mrr("g", ["a", "g"]) == 0.5
        assert m.mrr("g", ["a", "b", "c", "g"]) == 0.25
        assert m.mrr("g", ["a", "b"]) == 0.0

    def test_ndcg_perfect_ranking_is_one(self):
        graded = {"a": 3, "b": 2, "c": 1}
        assert m.ndcg_at_k(graded, ["a", "b", "c"], k=3) == pytest.approx(1.0)

    def test_ndcg_worse_ranking_is_lower(self):
        graded = {"a": 3, "b": 0}
        perfect = m.ndcg_at_k(graded, ["a", "b"], k=2)
        inverted = m.ndcg_at_k(graded, ["b", "a"], k=2)
        assert perfect == pytest.approx(1.0)
        assert inverted < perfect

    def test_ndcg_no_relevant_items_is_zero(self):
        assert m.ndcg_at_k({}, ["a", "b"], k=2) == 0.0
        assert m.ndcg_at_k({"a": 0}, ["a"], k=1) == 0.0

    def test_latency_percentiles(self):
        vals = [float(i) for i in range(1, 101)]
        pct = m.latency_percentiles(vals)
        assert pct["p50"] == 50.0
        assert pct["p95"] == 95.0
        assert m.latency_percentiles([]) == {"p50": 0.0, "p95": 0.0}


# ---------------------------------------------------------------------------
# Bootstrap CI significance floor (Concern 2)
# ---------------------------------------------------------------------------


class TestBootstrapCI:
    def test_clear_win_is_significant(self):
        deltas = [0.2] * 30 + [0.1] * 30
        ci = m.bootstrap_ci(deltas, seed=1)
        assert ci.n == 60
        assert ci.significant is True
        assert ci.lower > 0

    def test_mean_clears_bar_but_ci_straddles_zero(self):
        # Mean = +0.10 (clears a 0.05 bar) but variance is huge, so the 95%
        # CI lower bound dips <= 0 -> significant must be False and the gate
        # must NOT declare a win (plan Failure Path, Concern 2).
        deltas = [1.0, -0.8, 1.0, -0.8, 1.0, -0.8, 0.5, -0.3]
        ci = m.bootstrap_ci(deltas, seed=1)
        assert ci.mean > 0.05
        assert ci.significant is False

        gate = evaluate_decision_gate(
            mean_recall_gain=ci.mean,
            mean_mrr_gain=0.5,
            ci_significant=ci.significant,
            latency_regression_pct=0.0,
            gate_settings=_GateSettings(min_recall_gain=0.05, min_mrr_gain=0.03),
        )
        assert gate["verdict"] == "do-not-adopt"
        assert gate["checks"]["ci_lower_bound_positive"] is False

    def test_deterministic_for_seed(self):
        deltas = [0.1, 0.0, 0.2, -0.1, 0.3]
        a = m.bootstrap_ci(deltas, seed=7)
        b = m.bootstrap_ci(deltas, seed=7)
        assert (a.lower, a.upper) == (b.lower, b.upper)

    def test_degenerate_inputs_never_significant(self):
        assert m.bootstrap_ci([], seed=1).significant is False
        assert m.bootstrap_ci([0.5], seed=1).significant is False


# ---------------------------------------------------------------------------
# Decision gate + proximity check (Concern 4)
# ---------------------------------------------------------------------------


class _GateSettings:
    def __init__(self, min_recall_gain=0.05, min_mrr_gain=0.03, max_latency_regression_pct=50.0):
        self.min_recall_gain = min_recall_gain
        self.min_mrr_gain = min_mrr_gain
        self.max_latency_regression_pct = max_latency_regression_pct


class TestDecisionGate:
    def test_adopt_requires_all_layers(self):
        gate = evaluate_decision_gate(
            mean_recall_gain=0.10,
            mean_mrr_gain=0.05,
            ci_significant=True,
            latency_regression_pct=10.0,
            gate_settings=_GateSettings(),
        )
        assert gate["verdict"] == "adopt"

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"mean_recall_gain": 0.01},  # recall bar fails
            {"mean_mrr_gain": 0.0},  # MRR bar fails
            {"ci_significant": False},  # CI floor fails
            {"latency_regression_pct": 80.0},  # latency ceiling fails
        ],
    )
    def test_any_failing_layer_blocks_adoption(self, kwargs):
        base = dict(
            mean_recall_gain=0.10,
            mean_mrr_gain=0.05,
            ci_significant=True,
            latency_regression_pct=10.0,
        )
        base.update(kwargs)
        gate = evaluate_decision_gate(gate_settings=_GateSettings(), **base)
        assert gate["verdict"] == "do-not-adopt"

    def test_proximity_gate_skips_on_comfortable_clear_and_miss(self):
        # near threshold: |gain - 0.05| < 0.05 -> build pooled pass
        assert proximity_check(0.06, 0.05) is True
        assert proximity_check(0.01, 0.05) is True
        # comfortable clear (gain 0.2) and clear miss (gain -0.3) -> skip
        assert proximity_check(0.20, 0.05) is False
        assert proximity_check(-0.30, 0.05) is False


# ---------------------------------------------------------------------------
# Provider hard gate (Concern 1)
# ---------------------------------------------------------------------------


class _FakeProvider:
    def __init__(self, dims):
        self.dimensions = dims

    def embed(self, texts, input_type="query"):
        return [[0.1] * self.dimensions for _ in texts]


class TestProviderHardGate:
    def test_missing_provider_raises_before_scoring(self, monkeypatch):
        import popoto.fields.embedding_field as ef

        monkeypatch.setattr(ef, "_default_embedding_provider", None)
        with pytest.raises(ProviderUnavailableError):
            assert_provider_available()

    def test_dimension_mismatch_raises(self, monkeypatch):
        monkeypatch.setattr(
            "tools.memory_eval.embedding_coverage.record_embedding_coverage",
            lambda record, dim: _coverage(actual=1536, provider=dim),
        )
        with pytest.raises(ProviderDimensionMismatchError):
            assert_provider_dimension_match(_FakeProvider(768), [object()])

    def test_dimension_match_passes(self, monkeypatch):
        monkeypatch.setattr(
            "tools.memory_eval.embedding_coverage.record_embedding_coverage",
            lambda record, dim: _coverage(actual=1536, provider=dim),
        )
        assert assert_provider_dimension_match(_FakeProvider(1536), [object()]) == 1536

    def test_vectorless_sample_raises(self, monkeypatch):
        monkeypatch.setattr(
            "tools.memory_eval.embedding_coverage.record_embedding_coverage",
            lambda record, dim: _coverage(actual=None, provider=dim),
        )
        with pytest.raises(ProviderDimensionMismatchError):
            assert_provider_dimension_match(_FakeProvider(1536), [object(), object()])


def _coverage(actual, provider):
    from tools.memory_eval.embedding_coverage import EmbeddingCoverage

    return EmbeddingCoverage(
        naive_embedded=actual is not None,
        stored_dimension=actual,
        actual_dimension=actual,
        current_provider_dimension=provider,
        current_provider_valid=actual == provider,
    )


# ---------------------------------------------------------------------------
# Zero-vector abort + errored-vs-empty-arm distinction
# ---------------------------------------------------------------------------


class TestArms:
    def test_zero_vector_contribution_is_errored_not_scored(self, monkeypatch):
        monkeypatch.setattr(
            "tools.memory_eval.retrieval_arms.vector_signal_available",
            lambda query, project: False,
        )
        result = run_hybrid_arm("some query", "test-proj", 10, assert_nonzero_vector=True)
        assert result.errored is True
        assert "zero vector contribution" in (result.error_message or "")
        assert result.memory_ids == []

    def test_current_arm_exception_is_errored(self, monkeypatch):
        def _boom(*a, **kw):
            raise RuntimeError("forced failure")

        import agent.memory_retrieval as mr

        monkeypatch.setattr(mr, "retrieve_memories", _boom)
        result = run_current_arm("q", "test-proj", 10)
        assert result.errored is True
        assert result.memory_ids == []

    def test_current_arm_empty_result_is_not_errored(self, monkeypatch):
        import agent.memory_retrieval as mr

        monkeypatch.setattr(mr, "retrieve_memories", lambda *a, **kw: [])
        result = run_current_arm("q", "test-proj", 10)
        assert result.errored is False
        assert result.memory_ids == []

    def test_read_only_no_mutation_during_arm_run(self, monkeypatch):
        """Read-only guarantee (plan Success Criteria): running an arm must
        not change access_count on any returned record (spike-2's live
        verification, kept as a regression check at unit level)."""

        class _Rec:
            def __init__(self, mid):
                self.memory_id = mid
                self.access_count = 3

        records = [_Rec("m1"), _Rec("m2")]
        import agent.memory_retrieval as mr

        monkeypatch.setattr(mr, "retrieve_memories", lambda *a, **kw: records)
        before = [r.access_count for r in records]
        result = run_current_arm("q", "test-proj", 10)
        assert result.memory_ids == ["m1", "m2"]
        assert [r.access_count for r in records] == before


# ---------------------------------------------------------------------------
# Ground-truth hygiene + report rendering
# ---------------------------------------------------------------------------


class TestQuerySetHygiene:
    def test_degenerate_queries_are_skipped(self):
        assert _is_degenerate("", "content") is True
        assert _is_degenerate("short", "content") is True
        # verbatim slice of the memory itself
        assert (
            _is_degenerate("the exact stored sentence", "xx the exact stored sentence yy") is True
        )
        assert _is_degenerate("how do I restart the telegram bridge?", "restart docs") is False


class TestReportRendering:
    def test_report_table_includes_error_counts_and_coverage(self):
        report = {
            "k": 10,
            "recall_at_k": {"current": 0.5, "hybrid": 0.6},
            "mrr": {"current": 0.4, "hybrid": 0.45},
            "latency_ms": {
                "current": {"p50": 10.0, "p95": 20.0},
                "hybrid": {"p50": 15.0, "p95": 30.0},
            },
            "error_counts": {"current": 1, "hybrid": 2},
            "n_known_item": 57,
            "n_generated": 60,
            "mean_recall_gain": 0.1,
            "recall_delta_ci_95": {"lower": 0.02, "upper": 0.18, "mean": 0.1},
            "significant": True,
            "mean_mrr_gain": 0.05,
            "latency_regression_pct": 50.0,
            "coverage": {
                "total": 240,
                "current_provider_valid_count": 240,
                "current_provider_valid_pct": 100.0,
            },
            "pooled_pass_built": False,
            "ndcg": None,
            "verdict": "adopt",
        }
        table = render_table(report)
        # A reader must be able to tell a real tie from a broken run:
        assert "errored queries | 1 | 2" in table
        assert "240/240" in table
        assert "n_known_item: 57" in table
        assert "significant: True" in table
        assert "VERDICT: adopt" in table

    def test_known_item_dataclass(self):
        item = KnownItem(query="q", gold_memory_id="m1")
        assert item.gold_memory_id == "m1"
