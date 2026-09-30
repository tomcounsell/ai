"""Observation measurement and window comparison (#3218, lane 6).

``measure`` reads real ``ImprovementEvidence`` rows from the claimed test DB;
``compare_windows`` is pure and is driven with dict inputs. The lineage read
at the end seeds a real release + evaluation + experiment row set with lane
4's exact string expressions.

Uses the autouse ``redis_test_db`` fixture (tests/conftest.py).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from models.improvement_evidence import ImprovementEvidence
from tools.improvement_release.observation import (
    DETECTION_DECLINE_RATIO,
    EVIDENCE_TTL_DAYS,
    OBSERVATION_METRICS,
    READ_LIMIT,
    compare_windows,
    falsifier,
    measure,
    wilson_upper,
)

PK = "test-3218-observation"
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def _seed_rows(project_key: str, *, at: datetime, corrections: int, architectural: int, ticks: int):
    """Write ``corrections`` correction rows (``architectural`` of them) and ``ticks`` ticks."""
    for i in range(corrections):
        ImprovementEvidence.create(
            project_key=project_key,
            created_at=at + timedelta(seconds=i),
            kind="correction",
            classification="architectural" if i < architectural else "preference",
            source_ref=f"memory:{project_key}:{at.isoformat()}:{i}",
        )
    for i in range(ticks):
        ImprovementEvidence.create(
            project_key=project_key,
            created_at=at + timedelta(seconds=100 + i),
            kind="other",
            classification="unknown",
            source_ref=f"coverage:{at.isoformat()}:{i}",
        )


def _window(*, corrections=0, architectural=0, ticks=0, truncated=False, days=7):
    return {
        "corrections_total": corrections,
        "corrections_architectural": architectural,
        "coverage_ticks": ticks,
        "architectural_correction_rate": (architectural / ticks) if ticks else None,
        "truncated": truncated,
        "days": days,
    }


class TestConstants:
    def test_evidence_ttl_days_matches_model(self):
        assert EVIDENCE_TTL_DAYS * 86400 == ImprovementEvidence._meta.ttl

    def test_read_limit_matches_dashboard(self):
        from ui.data.improvement import READ_LIMIT as DASHBOARD_LIMIT

        assert READ_LIMIT == DASHBOARD_LIMIT == 1000

    def test_metrics_vocabulary(self):
        assert OBSERVATION_METRICS == (
            "corrections_total",
            "corrections_architectural",
            "coverage_ticks",
            "architectural_correction_rate",
        )
        assert DETECTION_DECLINE_RATIO == 0.8

    def test_falsifier_names_the_window(self):
        text = falsifier(14)
        assert "14" in text and "baseline band" in text and "coverage" in text


class TestMeasure:
    def test_counts_inside_half_open_window(self):
        start, end = NOW - timedelta(days=7), NOW
        _seed_rows(PK, at=start + timedelta(days=1), corrections=3, architectural=1, ticks=4)
        # rows on the end boundary and before the start are outside [start, end)
        _seed_rows(PK, at=end, corrections=5, architectural=5, ticks=5)
        _seed_rows(PK, at=start - timedelta(hours=1), corrections=5, architectural=5, ticks=5)

        result = measure(PK, start, end)

        assert result["unavailable"] is False
        assert result["corrections_total"] == 3
        assert result["corrections_architectural"] == 1
        assert result["coverage_ticks"] == 4
        assert result["architectural_correction_rate"] == 0.25
        assert result["truncated"] is False
        assert result["days"] == 7
        assert result["start"] == start and result["end"] == end

    def test_zero_denominator_rate_is_none(self):
        start, end = NOW - timedelta(days=7), NOW
        _seed_rows(PK, at=start + timedelta(days=1), corrections=2, architectural=2, ticks=0)
        result = measure(PK, start, end)
        assert result["corrections_architectural"] == 2
        assert result["coverage_ticks"] == 0
        assert result["architectural_correction_rate"] is None

    def test_truncated_when_read_hits_limit_inside_window(self):
        start, end = NOW - timedelta(days=7), NOW
        _seed_rows(PK, at=start + timedelta(days=2), corrections=0, architectural=0, ticks=6)
        result = measure(PK, start, end, limit=4)
        assert result["truncated"] is True
        assert result["coverage_ticks"] == 4

    def test_not_truncated_when_oldest_row_is_before_window(self):
        start, end = NOW - timedelta(days=7), NOW
        _seed_rows(PK, at=start - timedelta(days=1), corrections=0, architectural=0, ticks=1)
        _seed_rows(PK, at=start + timedelta(days=2), corrections=0, architectural=0, ticks=3)
        result = measure(PK, start, end, limit=4)
        assert result["truncated"] is False
        assert result["coverage_ticks"] == 3

    def test_raising_read_is_unavailable_with_warning(self, monkeypatch, caplog):
        def _boom(*_args, **_kwargs):
            raise RuntimeError("redis down")

        monkeypatch.setattr(ImprovementEvidence, "recent", classmethod(_boom))
        with caplog.at_level(logging.WARNING, logger="tools.improvement_release.observation"):
            result = measure(PK, NOW - timedelta(days=7), NOW)
        assert result["unavailable"] is True
        assert result["architectural_correction_rate"] is None
        assert any("redis down" in record.getMessage() for record in caplog.records)


class TestCompareWindows:
    def test_held_when_rate_inside_band_and_coverage_steady(self):
        baseline = _window(corrections=10, architectural=5, ticks=50, days=7)
        window = _window(corrections=10, architectural=5, ticks=50, days=7)
        result = compare_windows(baseline, window, window_days=7, baseline_days=7)
        assert result["verdict"] == "held"
        assert result["reason"] is None
        assert result["detection_declined"] is False
        assert result["baseline_band"]["upper"] >= 0.1
        assert result["deltas"]["window"]["coverage_ticks"] == 50
        assert result["deltas"]["window"]["per_day"]["coverage_ticks"] == pytest.approx(50 / 7)
        assert result["deltas"]["baseline"]["architectural_correction_rate"] == 0.1

    def test_regressed_when_rate_exceeds_band(self):
        baseline = _window(corrections=10, architectural=5, ticks=50, days=7)
        window = _window(corrections=40, architectural=30, ticks=50, days=7)
        result = compare_windows(baseline, window, window_days=7, baseline_days=7)
        assert result["verdict"] == "regressed"
        assert result["reason"] is None
        assert result["deltas"]["rate_delta"] == pytest.approx(0.5)

    def test_undetermined_when_detection_declines(self):
        baseline = _window(corrections=10, architectural=5, ticks=50, days=7)
        window = _window(corrections=1, architectural=0, ticks=20, days=7)
        result = compare_windows(baseline, window, window_days=7, baseline_days=7)
        assert result["verdict"] == "undetermined"
        assert result["reason"] == "DETECTION_DECLINED"
        assert result["detection_declined"] is True

    def test_undetermined_on_zero_denominator(self):
        baseline = _window(corrections=10, architectural=5, ticks=50, days=7)
        window = _window(corrections=1, architectural=0, ticks=0, days=7)
        result = compare_windows(baseline, window, window_days=7, baseline_days=7)
        assert result["verdict"] == "undetermined"
        assert result["reason"] == "ZERO_DENOMINATOR"
        assert result["deltas"]["window"]["architectural_correction_rate"] is None

    def test_compare_windows_undetermined_when_truncated(self):
        baseline = _window(corrections=10, architectural=5, ticks=50, days=7, truncated=True)
        window = _window(corrections=10, architectural=5, ticks=50, days=7)
        result = compare_windows(baseline, window, window_days=7, baseline_days=7)
        assert result["verdict"] == "undetermined"
        assert result["reason"] == "EVIDENCE_TRUNCATED"

    def test_unavailable_window_is_undetermined(self):
        baseline = _window(corrections=10, architectural=5, ticks=50, days=7)
        window = {"unavailable": True}
        result = compare_windows(baseline, window, window_days=7, baseline_days=7)
        assert result["verdict"] == "undetermined"
        assert result["reason"] == "EVIDENCE_UNAVAILABLE"

    def test_per_day_normalization_uses_each_window_length(self):
        baseline = _window(corrections=14, architectural=7, ticks=140, days=14)
        window = _window(corrections=7, architectural=3, ticks=70, days=7)
        result = compare_windows(baseline, window, window_days=7, baseline_days=14)
        assert result["deltas"]["baseline"]["per_day"]["coverage_ticks"] == pytest.approx(10.0)
        assert result["deltas"]["window"]["per_day"]["coverage_ticks"] == pytest.approx(10.0)
        assert result["detection_declined"] is False
        assert result["verdict"] == "held"


class TestWilson:
    def test_upper_bound_brackets_the_rate(self):
        upper = wilson_upper(5, 50)
        assert 0.1 < upper < 0.25

    def test_zero_denominator_is_none(self):
        assert wilson_upper(0, 0) is None

    def test_rate_above_one_is_clamped(self):
        assert wilson_upper(60, 50) == 1.0


class TestSeededVerdicts:
    """The three fixtures, end to end through real rows and ``measure``."""

    @pytest.mark.parametrize(
        ("window_seed", "expected"),
        [
            ({"corrections": 5, "architectural": 2, "ticks": 20}, "held"),
            ({"corrections": 20, "architectural": 15, "ticks": 20}, "regressed"),
            ({"corrections": 1, "architectural": 0, "ticks": 5}, "undetermined"),
        ],
    )
    def test_three_fixtures(self, window_seed, expected):
        exposed_at = NOW - timedelta(days=7)
        baseline_start = exposed_at - timedelta(days=7)
        _seed_rows(
            PK, at=baseline_start + timedelta(days=1), corrections=5, architectural=2, ticks=20
        )
        _seed_rows(PK, at=exposed_at + timedelta(days=1), **window_seed)
        baseline = measure(PK, baseline_start, exposed_at)
        window = measure(PK, exposed_at, NOW)
        result = compare_windows(baseline, window, window_days=7, baseline_days=7)
        assert result["verdict"] == expected


class TestLineage:
    def test_lineage_reads_lane4_string_shape(self):
        from models.improvement_evaluation import ImprovementEvaluation
        from models.improvement_experiment import ImprovementExperiment
        from models.improvement_release import ImprovementRelease
        from tools.improvement_eval.runner import freeze_protocol
        from tools.improvement_release.lineage import release_lineage

        pk = "test-3218-lineage"
        ref = freeze_protocol({"primary_endpoint": "primary", "endpoints": ["primary"]})
        experiment = ImprovementExperiment(
            project_key=pk,
            created_at=NOW,
            state="complete",
            hypothesis="a wider candidate finds the gold memory",
            contract_digest="sha256:" + "c" * 64,
            candidate_surfaces=json.dumps(["tools/x.py"]),
            manifest=json.dumps({"protocol_ref": ref, "base_revision": "abc123"}),
        )
        assert experiment.save() is not False
        interval = {
            "lower": 0.02,
            "upper": 0.22,
            "n": 12,
            "raw_p_value": 0.01,
            "adjusted_p_value": 0.02,
        }
        evaluation = ImprovementEvaluation(
            project_key=pk,
            created_at=NOW,
            state="complete",
            verdict="accept",
            experiment_id=str(experiment.id),
            # runner.py:499-508's exact expressions
            effect=json.dumps({"primary": 0.12}, sort_keys=True),
            confidence_interval=json.dumps({"primary": interval}, sort_keys=True),
            notes="\n".join(["accept"]),
            judge_records="",
        )
        assert evaluation.save() is not False
        release = ImprovementRelease(
            project_key=pk,
            created_at=NOW,
            state="observing",
            kind="core_workflow",
            evaluation_id=str(evaluation.id),
            surfaces=json.dumps(["tools/x.py"]),
            candidate_ref="session/x",
            exposed_at=NOW - timedelta(days=3),
            observation_window_ends_at=NOW + timedelta(days=4),
            rollback_drill=json.dumps({"result": "pass", "drilled_at": NOW.isoformat()}),
            outcome=json.dumps({"history": []}),
        )
        assert release.save() is not False

        lineage = release_lineage(pk, now=NOW)

        assert lineage["unavailable"] is False
        assert lineage["no_releases_yet"] is False
        assert lineage["promotion_gate"]["automated"] is False
        assert set(lineage["promotion_gate"]["unmet"]) == {
            "credential_separation",
            "charter_names_reversible_surfaces",
        }
        (row,) = lineage["releases"]
        assert row["id"] == str(release.id)
        assert row["state"] == "observing"
        assert row["evaluation"]["verdict"] == "accept"
        assert row["evaluation"]["effect"] == 0.12
        assert row["evaluation"]["confidence_interval"] == interval
        assert row["experiment"]["hypothesis"] == "a wider candidate finds the gold memory"
        assert row["experiment"]["contract_digest"] == "sha256:" + "c" * 64
        assert row["drill"] == {"result": "pass", "drilled_at": NOW.isoformat()}
        assert row["window"]["days_remaining"] == 4
        assert row["outcome"]["verdict"] is None

    def test_lineage_unavailable_still_returns_gate(self, monkeypatch, caplog):
        from models.improvement_release import ImprovementRelease
        from tools.improvement_release.lineage import release_lineage

        class _Query:
            def filter(self, **_kwargs):
                raise RuntimeError("redis down")

        monkeypatch.setattr(ImprovementRelease, "query", _Query())
        with caplog.at_level(logging.WARNING, logger="tools.improvement_release.lineage"):
            lineage = release_lineage("test-3218-lineage-down")
        assert lineage["unavailable"] is True
        assert lineage["releases"] == []
        assert lineage["promotion_gate"]["automated"] is False
        assert any("redis down" in record.getMessage() for record in caplog.records)

    def test_no_releases_yet(self):
        from tools.improvement_release.lineage import release_lineage

        lineage = release_lineage("test-3218-lineage-empty")
        assert lineage["no_releases_yet"] is True
        assert lineage["unavailable"] is False
