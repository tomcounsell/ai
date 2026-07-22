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
from tools.memory_eval.ingest_quality import compute_corpus_metrics
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


# ---------------------------------------------------------------------------
# Corpus ingest-quality aggregation (issue #2200, Phase-1 measure-only)
# ---------------------------------------------------------------------------


def _outcome_history(*outcomes: str) -> list[dict]:
    return [{"outcome": o} for o in outcomes]


def _record(
    *,
    content: str = "The deployment uses blue-green rollout with automated rollback.",
    outcome_history: list[dict] | None = None,
    source: str = "human",
    importance: float = 6.0,
    confidence: float = 0.5,
    access_count: int = 1,
    decay_imminent: bool = False,
    superseded: bool = False,
) -> dict:
    return {
        "content": content,
        "outcome_history": outcome_history if outcome_history is not None else [],
        "source": source,
        "importance": importance,
        "confidence": confidence,
        "access_count": access_count,
        "decay_imminent": decay_imminent,
        "superseded": superseded,
    }


class TestIngestQuality:
    def test_used_excluded_aggregate_equals_micro_average(self):
        """Pinned formula: "used" excluded from both num/denom; aggregate
        is sum(acted)/sum(acted+dismissed) across qualifying records."""
        records = [
            _record(outcome_history=_outcome_history("acted", "used", "dismissed", "acted")),
            _record(outcome_history=_outcome_history("dismissed", "dismissed", "used")),
        ]
        metrics = compute_corpus_metrics(records, min_evidence=2)

        # record 1: acted=2, dismissed=1, evidence=3 ("used" excluded)
        # record 2: acted=0, dismissed=2, evidence=2 ("used" excluded)
        assert metrics["acted_total"] == 2
        assert metrics["dismissed_total"] == 3
        assert metrics["evidence_total"] == 5
        assert metrics["aggregate_act_rate"] == pytest.approx(2 / 5)
        assert metrics["aggregate_dismissal_rate"] == pytest.approx(3 / 5)
        assert metrics["qualifying_record_count"] == 2
        assert metrics["excluded_thin_evidence_count"] == 0

    def test_record_below_min_evidence_excluded_from_aggregate_but_counted(self):
        records = [
            _record(outcome_history=_outcome_history("acted")),  # evidence=1 < min_evidence=2
            _record(outcome_history=_outcome_history("acted", "dismissed")),  # evidence=2, ok
        ]
        metrics = compute_corpus_metrics(records, min_evidence=2)

        assert metrics["qualifying_record_count"] == 1
        assert metrics["excluded_thin_evidence_count"] == 1
        # Only the qualifying record contributes: acted=1, evidence=2
        assert metrics["acted_total"] == 1
        assert metrics["evidence_total"] == 2
        assert metrics["aggregate_act_rate"] == pytest.approx(0.5)

    def test_empty_record_list_returns_zero_filled_dict_without_exceptions(self):
        metrics = compute_corpus_metrics([], min_evidence=2)

        assert metrics["total_records"] == 0
        assert metrics["superseded_count"] == 0
        assert metrics["durable_denominator"] == 0
        assert metrics["aggregate_act_rate"] is None
        assert metrics["aggregate_dismissal_rate"] is None
        assert metrics["junk_rate"] is None
        assert metrics["qualifying_record_count"] == 0
        assert metrics["excluded_thin_evidence_count"] == 0
        assert metrics["source_counts"] == {}
        assert metrics["act_rate_definition"]
        # Histograms present, zero-filled.
        assert all(v == 0 for v in metrics["importance_histogram"].values())
        assert all(v == 0 for v in metrics["confidence_histogram"].values())
        assert all(v == 0 for v in metrics["act_rate_distribution"].values())

    def test_malformed_record_does_not_raise(self):
        malformed_records = [
            {"content": "fine", "metadata": "not-a-dict"},  # non-dict metadata
            {"content": "fine"},  # missing outcome_history entirely
            {},  # empty dict
            "not-a-dict-at-all",  # not even a dict
        ]
        metrics = compute_corpus_metrics(malformed_records, min_evidence=2)

        assert metrics["total_records"] == 4
        assert metrics["evidence_total"] == 0
        assert metrics["excluded_thin_evidence_count"] == 4
        assert metrics["aggregate_act_rate"] is None

    def test_junk_rate_uses_durable_denominator_excluding_superseded(self):
        records = [
            _record(content="Yup"),  # ack_only, durable-eligible
            _record(content="includes:"),  # fragment, durable-eligible
            _record(content="A full durable sentence about deployment strategy."),
            _record(content="Yup", superseded=True),  # excluded from denominator
        ]
        metrics = compute_corpus_metrics(records, min_evidence=2)

        assert metrics["superseded_count"] == 1
        assert metrics["durable_denominator"] == 3
        assert metrics["junk_count"] == 2
        assert metrics["ack_only_count"] == 1
        assert metrics["fragment_suspect_count"] == 1
        assert metrics["junk_rate"] == pytest.approx(2 / 3)

    def test_source_counts_grouped_and_never_injected_decay_imminent_tracked(self):
        records = [
            _record(source="human", access_count=0),
            _record(source="agent", access_count=3, decay_imminent=True),
            _record(source="human", access_count=0),
        ]
        metrics = compute_corpus_metrics(records)

        assert metrics["source_counts"] == {"human": 2, "agent": 1}
        assert metrics["never_injected_count"] == 2
        assert metrics["decay_imminent_count"] == 1

    def test_act_rate_definition_documents_used_exclusion(self):
        metrics = compute_corpus_metrics([])
        assert "used" in metrics["act_rate_definition"]
        assert "micro" in metrics["act_rate_definition"].lower()
