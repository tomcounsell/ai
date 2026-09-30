"""Unit tests for `tools/memory_eval/snapshot.py` -- the baseline CLI.

Covers artifact schema on write, the existence-guard clobber protection
(refuse without --force, overwrite with --force), and the never-write-
partial-output contract on a metrics-computation failure. No network, no
live Redis -- `ui.data.memories.get_corpus_metrics` is monkeypatched in
every test, and all file I/O is redirected into `tmp_path`.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tools.memory_eval import snapshot

pytestmark = pytest.mark.sdlc


def _fake_metrics(**overrides) -> dict:
    base = {
        "total_records": 10,
        "superseded_count": 1,
        "durable_denominator": 9,
        "min_evidence": 2,
        "act_rate_definition": "some definition",
        "aggregate_act_rate": 0.5,
        "aggregate_dismissal_rate": 0.25,
        "acted_total": 4,
        "dismissed_total": 2,
        "evidence_total": 6,
        "qualifying_record_count": 3,
        "excluded_thin_evidence_count": 2,
        "act_rate_distribution": {"0.0-0.2": 0},
        "junk_count": 2,
        "junk_rate": 0.222,
        "ack_only_count": 1,
        "fragment_suspect_count": 1,
        "source_counts": {"agent": 6, "human": 3},
        "importance_histogram": {},
        "confidence_histogram": {},
        "decay_imminent_count": 1,
        "never_injected_count": 5,
        "project_key": "test-proj",
    }
    base.update(overrides)
    return base


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Redirect the module's baseline artifact paths into a tmp dir."""
    baseline_dir = tmp_path / "baselines"
    json_path = baseline_dir / "memory-telemetry-baseline.json"
    md_path = baseline_dir / "memory-telemetry-baseline.md"
    monkeypatch.setattr(snapshot, "BASELINE_DIR", baseline_dir)
    monkeypatch.setattr(snapshot, "JSON_PATH", json_path)
    monkeypatch.setattr(snapshot, "MD_PATH", md_path)
    return json_path, md_path


class TestBuildBaseline:
    def test_adds_provenance_fields_without_duplicating_act_rate_definition(self):
        with (
            patch("ui.data.memories.get_corpus_metrics", return_value=_fake_metrics()),
            patch.object(snapshot, "_git_sha", return_value="abc123"),
        ):
            baseline = snapshot.build_baseline(project_key="test-proj")

        assert baseline["record_count"] == 10
        assert baseline["git_sha"] == "abc123"
        assert "snapshot_timestamp" in baseline
        # act_rate_definition comes straight from the metrics dict, not
        # reconstructed here.
        assert baseline["act_rate_definition"] == "some definition"


class TestRenderMarkdown:
    def test_includes_key_numbers(self):
        baseline = _fake_metrics(
            record_count=10, snapshot_timestamp="2026-01-01T00:00:00+00:00", git_sha="abc123"
        )
        md = snapshot.render_markdown(baseline)
        assert "Record count: 10" in md
        assert "abc123" in md
        assert "Aggregate act rate: 0.500" in md

    def test_handles_undefined_rates_without_raising(self):
        baseline = _fake_metrics(aggregate_act_rate=None, junk_rate=None)
        md = snapshot.render_markdown(baseline)
        assert "undefined" in md


class TestMainArtifactSchema:
    def test_writes_json_and_markdown_with_provenance(self, isolated_paths):
        json_path, md_path = isolated_paths
        with patch("ui.data.memories.get_corpus_metrics", return_value=_fake_metrics()):
            exit_code = snapshot.main(["--project-key", "test-proj"])

        assert exit_code == 0
        assert json_path.exists()
        assert md_path.exists()

        data = json.loads(json_path.read_text())
        assert data["record_count"] == 10
        assert "snapshot_timestamp" in data
        assert "git_sha" in data
        assert data["act_rate_definition"] == "some definition"
        assert "Memory Telemetry Baseline" in md_path.read_text()


class TestClobberGuard:
    def test_refuses_without_force_and_leaves_files_byte_identical(self, isolated_paths):
        json_path, md_path = isolated_paths
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text("PRE-EXISTING")
        md_path.write_text("PRE-EXISTING MD")

        with patch("ui.data.memories.get_corpus_metrics", return_value=_fake_metrics()):
            exit_code = snapshot.main(["--project-key", "test-proj"])

        assert exit_code != 0
        assert json_path.read_text() == "PRE-EXISTING"
        assert md_path.read_text() == "PRE-EXISTING MD"

    def test_force_overwrites_both_artifacts(self, isolated_paths):
        json_path, md_path = isolated_paths
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text("PRE-EXISTING")
        md_path.write_text("PRE-EXISTING MD")

        with patch("ui.data.memories.get_corpus_metrics", return_value=_fake_metrics()):
            exit_code = snapshot.main(["--project-key", "test-proj", "--force"])

        assert exit_code == 0
        assert json_path.read_text() != "PRE-EXISTING"
        assert "Memory Telemetry Baseline" in md_path.read_text()

    def test_single_existing_artifact_still_blocks(self, isolated_paths):
        json_path, md_path = isolated_paths
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text("PRE-EXISTING")
        # md_path deliberately absent -- one existing artifact is enough
        # to trip the guard.

        with patch("ui.data.memories.get_corpus_metrics", return_value=_fake_metrics()):
            exit_code = snapshot.main(["--project-key", "test-proj"])

        assert exit_code != 0
        assert json_path.read_text() == "PRE-EXISTING"
        assert not md_path.exists()


class TestComputationFailureNeverWritesPartial:
    def test_exception_exits_nonzero_and_writes_nothing(self, isolated_paths):
        json_path, md_path = isolated_paths

        def raise_(**_kwargs):
            raise RuntimeError("redis down")

        with patch("ui.data.memories.get_corpus_metrics", side_effect=raise_):
            exit_code = snapshot.main(["--project-key", "test-proj"])

        assert exit_code != 0
        assert not json_path.exists()
        assert not md_path.exists()
