"""Unit tests for `tools/memory_eval/distilled_ingest_report.py` -- the
Phase 3 (memory-distilled-ingest, issue #2202, Task 3) lift-report CLI.

Mirrors `tests/unit/test_memory_eval_snapshot.py`'s pattern: artifact schema
on write, the existence-guard clobber protection, and never-write-partial-
output on a computation failure. No network, no live Redis --
`ui.data.memories.get_corpus_records` / `get_corpus_metrics` are
monkeypatched in every test, and all file I/O is redirected into `tmp_path`.

Also covers the per-source segmentation contract (plan Task 3 requirement
1): `compute_corpus_metrics` itself is never modified -- segmentation is
achieved purely by filtering the decorated-record list by `source` before
calling it once per subset (see `segment_records_by_source` /
`compute_segmented_metrics`).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tools.memory_eval import distilled_ingest_report as report_mod

pytestmark = pytest.mark.sdlc


def _record(
    *,
    source: str = "human",
    content: str = "Tom wants the justfile rewritten.",
    importance: float = 3.0,
    outcome_history: list[dict] | None = None,
    superseded: bool = False,
) -> dict:
    return {
        "content": content,
        "outcome_history": outcome_history if outcome_history is not None else [],
        "source": source,
        "importance": importance,
        "confidence": 0.5,
        "access_count": 1,
        "decay_imminent": False,
        "superseded": superseded,
    }


def _fake_phase1_baseline(**overrides) -> dict:
    base = {
        "snapshot_timestamp": "2026-07-22T08:26:23+00:00",
        "git_sha": "b55cc3b8d70ea852c87302724e1d67b3c22c456a",
        "total_records": 1991,
        "aggregate_act_rate": 0.990,
        "junk_rate": 0.030,
        "source_counts": {"agent": 1963, "human": 28},
        "importance_histogram": {"0.8-1.0": 1407, ">1.0": 584},
    }
    base.update(overrides)
    return base


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Redirect the module's artifact paths (and the Phase 1 baseline read
    path) into a tmp dir."""
    baseline_dir = tmp_path / "baselines"
    json_path = baseline_dir / "memory-distilled-ingest-report.json"
    md_path = baseline_dir / "memory-distilled-ingest-report.md"
    phase1_path = baseline_dir / "memory-telemetry-baseline.json"
    monkeypatch.setattr(report_mod, "BASELINE_DIR", baseline_dir)
    monkeypatch.setattr(report_mod, "REPORT_JSON_PATH", json_path)
    monkeypatch.setattr(report_mod, "REPORT_MD_PATH", md_path)
    monkeypatch.setattr(report_mod, "PHASE1_BASELINE_JSON_PATH", phase1_path)
    return json_path, md_path, phase1_path


# ---------------------------------------------------------------------------
# Per-source segmentation (Task 3 requirement 1) -- compute_corpus_metrics
# itself is untouched; segmentation is filter-then-aggregate.
# ---------------------------------------------------------------------------


class TestSegmentation:
    def test_segment_records_by_source_groups_and_normalizes(self):
        records = [
            _record(source="human"),
            _record(source="agent"),
            _record(source="human"),
            _record(source=""),  # falls back to "unknown"
            {"content": "no source key at all"},  # also "unknown"
        ]
        segments = report_mod.segment_records_by_source(records)

        assert len(segments["human"]) == 2
        assert len(segments["agent"]) == 1
        assert len(segments["unknown"]) == 2

    def test_compute_segmented_metrics_returns_aggregate_and_by_source(self):
        records = [
            _record(source="human", importance=3.0),
            _record(source="human", importance=4.0),
            _record(source="agent", importance=1.0),
        ]
        segmented = report_mod.compute_segmented_metrics(records, min_evidence=2)

        assert segmented["aggregate"]["total_records"] == 3
        assert set(segmented["by_source"]) == {"human", "agent"}
        assert segmented["by_source"]["human"]["total_records"] == 2
        assert segmented["by_source"]["agent"]["total_records"] == 1

    def test_segmented_counts_sum_back_to_pooled_aggregate(self):
        """The aggregator is unchanged (no source param); segmentation must
        not lose or double-count records relative to the pooled call."""
        records = [
            _record(source="human", importance=3.0),
            _record(source="human", importance=6.0),
            _record(source="agent", importance=1.0),
            _record(source="agent", importance=1.0),
        ]
        segmented = report_mod.compute_segmented_metrics(records)

        summed = sum(m["total_records"] for m in segmented["by_source"].values())
        assert summed == segmented["aggregate"]["total_records"] == 4


# ---------------------------------------------------------------------------
# Baseline comparison
# ---------------------------------------------------------------------------


class TestBaselineComparison:
    def test_missing_baseline_yields_not_present(self):
        comparison = report_mod.compare_to_baseline({"total_records": 5}, None)
        assert comparison == {"baseline_present": False}

    def test_computes_deltas_against_baseline(self):
        current = {
            "total_records": 2000,
            "junk_rate": 0.02,
            "aggregate_act_rate": 0.95,
            "source_counts": {"agent": 1970, "human": 30},
            "importance_histogram": {"0.8-1.0": 1400, ">1.0": 600},
        }
        baseline = _fake_phase1_baseline()

        comparison = report_mod.compare_to_baseline(current, baseline)

        assert comparison["baseline_present"] is True
        assert comparison["record_count"] == {"baseline": 1991, "current": 2000, "delta": 9}
        assert comparison["junk_rate"]["delta"] == pytest.approx(0.02 - 0.030)
        assert comparison["aggregate_act_rate"]["delta"] == pytest.approx(0.95 - 0.990)
        assert comparison["importance_histogram"]["0.8-1.0"] == {
            "baseline": 1407,
            "current": 1400,
            "delta": -7,
        }
        assert comparison["importance_histogram"][">1.0"]["delta"] == 16

    def test_undefined_rates_do_not_raise(self):
        current = {"total_records": 0, "junk_rate": None, "aggregate_act_rate": None}
        baseline = _fake_phase1_baseline()
        comparison = report_mod.compare_to_baseline(current, baseline)
        assert comparison["junk_rate"]["delta"] is None
        assert comparison["aggregate_act_rate"]["delta"] is None

    def test_load_phase1_baseline_missing_file_returns_none(self, tmp_path):
        missing = tmp_path / "nope.json"
        assert report_mod.load_phase1_baseline(missing) is None

    def test_load_phase1_baseline_reads_json(self, tmp_path):
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps(_fake_phase1_baseline()))
        loaded = report_mod.load_phase1_baseline(path)
        assert loaded["total_records"] == 1991


# ---------------------------------------------------------------------------
# build_report / render_markdown
# ---------------------------------------------------------------------------


class TestBuildReport:
    def test_pins_model_prompt_version_and_git_provenance(self, isolated_paths):
        records = [_record(source="human"), _record(source="agent")]
        with (
            patch("ui.data.memories.get_corpus_records", return_value=(records, ["test-proj"])),
            patch(
                "ui.data.memories.get_corpus_metrics",
                return_value={"provisional_count": 0, "distilled_count": 0, "abandoned_count": 0},
            ),
            patch.object(report_mod, "_git_sha", return_value="deadbeef"),
        ):
            report = report_mod.build_report(project_key="test-proj")

        header = report["header"]
        assert header["distill_model"]  # pinned constant, non-empty
        assert header["distill_prompt_version"] == "v1"
        assert header["git_sha"] == "deadbeef"
        assert "snapshot_timestamp" in header
        assert "MERGE-TIME" in header["measurement_note"]
        assert report["aggregate"]["total_records"] == 2
        assert set(report["by_source"]) == {"human", "agent"}
        # No Phase1 baseline on disk in this test -> comparison skipped, not a raise.
        assert report["baseline_comparison"]["baseline_present"] is False

    def test_loads_phase1_baseline_when_present(self, isolated_paths):
        _, _, phase1_path = isolated_paths
        phase1_path.parent.mkdir(parents=True, exist_ok=True)
        phase1_path.write_text(json.dumps(_fake_phase1_baseline()))

        with (
            patch("ui.data.memories.get_corpus_records", return_value=([], ["test-proj"])),
            patch(
                "ui.data.memories.get_corpus_metrics",
                return_value={"provisional_count": 0, "distilled_count": 0, "abandoned_count": 0},
            ),
        ):
            report = report_mod.build_report(project_key="test-proj")

        assert report["baseline_comparison"]["baseline_present"] is True
        assert report["baseline_comparison"]["record_count"]["baseline"] == 1991

    def test_includes_distillation_coverage_gauges(self, isolated_paths):
        with (
            patch("ui.data.memories.get_corpus_records", return_value=([], ["test-proj"])),
            patch(
                "ui.data.memories.get_corpus_metrics",
                return_value={
                    "provisional_count": 4,
                    "distilled_count": 1,
                    "abandoned_count": 0,
                },
            ),
        ):
            report = report_mod.build_report(project_key="test-proj")

        assert report["distillation_coverage"] == {
            "provisional_count": 4,
            "distilled_count": 1,
            "abandoned_count": 0,
        }


class TestRenderMarkdown:
    def test_includes_pinned_header_and_methodology_disclaimer(self, isolated_paths):
        with (
            patch("ui.data.memories.get_corpus_records", return_value=([], ["test-proj"])),
            patch(
                "ui.data.memories.get_corpus_metrics",
                return_value={"provisional_count": 0, "distilled_count": 0, "abandoned_count": 0},
            ),
        ):
            report = report_mod.build_report(project_key="test-proj")
        md = report_mod.render_markdown(report)

        assert "MERGE-TIME IMPORTANCE-DISTRIBUTION SNAPSHOT" in md
        assert "not an act-rate lift claim" in md
        assert report["header"]["distill_prompt_version"] in md
        assert report["header"]["git_sha"] in md

    def test_includes_per_source_sections(self, isolated_paths):
        records = [_record(source="human"), _record(source="agent")]
        with (
            patch("ui.data.memories.get_corpus_records", return_value=(records, ["test-proj"])),
            patch(
                "ui.data.memories.get_corpus_metrics",
                return_value={"provisional_count": 0, "distilled_count": 0, "abandoned_count": 0},
            ),
        ):
            report = report_mod.build_report(project_key="test-proj")
        md = report_mod.render_markdown(report)

        assert "Source: `human`" in md
        assert "Source: `agent`" in md

    def test_includes_distillation_coverage_counts(self, isolated_paths):
        with (
            patch("ui.data.memories.get_corpus_records", return_value=([], ["test-proj"])),
            patch(
                "ui.data.memories.get_corpus_metrics",
                return_value={
                    "provisional_count": 2,
                    "distilled_count": 1,
                    "abandoned_count": 0,
                },
            ),
        ):
            report = report_mod.build_report(project_key="test-proj")
        md = report_mod.render_markdown(report)

        assert "Provisional (awaiting distillation): 2" in md
        assert "Distilled (settled): 1" in md
        assert "Abandoned (terminal, attempt-cap or write-filter drop): 0" in md

    def test_missing_phase1_baseline_handled_without_raising(self, isolated_paths):
        with (
            patch("ui.data.memories.get_corpus_records", return_value=([], ["test-proj"])),
            patch(
                "ui.data.memories.get_corpus_metrics",
                return_value={"provisional_count": 0, "distilled_count": 0, "abandoned_count": 0},
            ),
        ):
            report = report_mod.build_report(project_key="test-proj")
        md = report_mod.render_markdown(report)

        assert "No Phase 1 baseline artifact found" in md

    def test_includes_baseline_comparison_table_when_present(self, isolated_paths):
        _, _, phase1_path = isolated_paths
        phase1_path.parent.mkdir(parents=True, exist_ok=True)
        phase1_path.write_text(json.dumps(_fake_phase1_baseline()))

        with (
            patch("ui.data.memories.get_corpus_records", return_value=([], ["test-proj"])),
            patch(
                "ui.data.memories.get_corpus_metrics",
                return_value={"provisional_count": 0, "distilled_count": 0, "abandoned_count": 0},
            ),
        ):
            report = report_mod.build_report(project_key="test-proj")
        md = report_mod.render_markdown(report)

        assert "Comparison to Phase 1 baseline" in md
        assert "Importance histogram: baseline vs. current" in md


class TestMainArtifactSchema:
    def test_writes_json_and_markdown_with_provenance(self, isolated_paths):
        json_path, md_path, _ = isolated_paths
        with (
            patch("ui.data.memories.get_corpus_records", return_value=([], ["test-proj"])),
            patch(
                "ui.data.memories.get_corpus_metrics",
                return_value={"provisional_count": 0, "distilled_count": 0, "abandoned_count": 0},
            ),
        ):
            exit_code = report_mod.main(["--project-key", "test-proj"])

        assert exit_code == 0
        assert json_path.exists()
        assert md_path.exists()

        data = json.loads(json_path.read_text())
        assert data["header"]["distill_model"]
        assert "Memory Distilled-Ingest Report" in md_path.read_text()


class TestClobberGuard:
    def test_refuses_without_force_and_leaves_files_byte_identical(self, isolated_paths):
        json_path, md_path, _ = isolated_paths
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text("PRE-EXISTING")
        md_path.write_text("PRE-EXISTING MD")

        with (
            patch("ui.data.memories.get_corpus_records", return_value=([], ["test-proj"])),
            patch(
                "ui.data.memories.get_corpus_metrics",
                return_value={"provisional_count": 0, "distilled_count": 0, "abandoned_count": 0},
            ),
        ):
            exit_code = report_mod.main(["--project-key", "test-proj"])

        assert exit_code != 0
        assert json_path.read_text() == "PRE-EXISTING"
        assert md_path.read_text() == "PRE-EXISTING MD"

    def test_force_overwrites_both_artifacts(self, isolated_paths):
        json_path, md_path, _ = isolated_paths
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text("PRE-EXISTING")
        md_path.write_text("PRE-EXISTING MD")

        with (
            patch("ui.data.memories.get_corpus_records", return_value=([], ["test-proj"])),
            patch(
                "ui.data.memories.get_corpus_metrics",
                return_value={"provisional_count": 0, "distilled_count": 0, "abandoned_count": 0},
            ),
        ):
            exit_code = report_mod.main(["--project-key", "test-proj", "--force"])

        assert exit_code == 0
        assert json_path.read_text() != "PRE-EXISTING"
        assert "Memory Distilled-Ingest Report" in md_path.read_text()


class TestComputationFailureNeverWritesPartial:
    def test_exception_exits_nonzero_and_writes_nothing(self, isolated_paths):
        json_path, md_path, _ = isolated_paths

        def raise_(**_kwargs):
            raise RuntimeError("redis down")

        with patch("ui.data.memories.get_corpus_records", side_effect=raise_):
            exit_code = report_mod.main(["--project-key", "test-proj"])

        assert exit_code != 0
        assert not json_path.exists()
        assert not md_path.exists()
