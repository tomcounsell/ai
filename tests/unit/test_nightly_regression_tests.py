"""Unit tests for scripts/nightly_regression_tests.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Insert the scripts directory so we can import the module directly
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import nightly_regression_tests as nrt


class TestLoadLastRun:
    def test_returns_empty_dict_when_file_missing(self, tmp_path: Path) -> None:
        nrt.LAST_RUN_FILE = tmp_path / "nonexistent.json"
        result = nrt.load_last_run()
        assert result == {}

    def test_returns_empty_dict_on_corrupt_json(self, tmp_path: Path) -> None:
        corrupt = tmp_path / "last_run.json"
        corrupt.write_text("not valid json{{{")
        nrt.LAST_RUN_FILE = corrupt
        result = nrt.load_last_run()
        assert result == {}

    def test_loads_valid_state(self, tmp_path: Path) -> None:
        state = {
            "passed": 100,
            "failed": 3,
            "error": 0,
            "total": 103,
            "run_at": "2026-04-16T03:00:00+00:00",
        }
        state_file = tmp_path / "last_run.json"
        state_file.write_text(json.dumps(state))
        nrt.LAST_RUN_FILE = state_file
        result = nrt.load_last_run()
        assert result == state


class TestSaveLastRun:
    def test_saves_state_to_file(self, tmp_path: Path) -> None:
        nrt.DATA_DIR = tmp_path
        nrt.LAST_RUN_FILE = tmp_path / "last_run.json"
        state = {
            "passed": 50,
            "failed": 2,
            "error": 0,
            "total": 52,
            "run_at": "2026-04-16T03:00:00+00:00",
        }
        nrt.save_last_run(state)
        assert nrt.LAST_RUN_FILE.exists()
        loaded = json.loads(nrt.LAST_RUN_FILE.read_text())
        assert loaded == state

    def test_creates_data_dir_if_missing(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "data"
        nrt.DATA_DIR = new_dir
        nrt.LAST_RUN_FILE = new_dir / "last_run.json"
        assert not new_dir.exists()
        nrt.save_last_run({"passed": 1, "failed": 0, "error": 0, "total": 1, "run_at": "now"})
        assert new_dir.exists()
        assert nrt.LAST_RUN_FILE.exists()


class TestDeltaLogic:
    """Test the alert conditions using the main() alert logic inline."""

    def _compute_alert(self, prev: dict, current: dict) -> str | None:
        """Reproduce main()'s alert condition logic and return the message category."""
        is_first_run = not prev
        delta = current["failed"] - prev.get("failed", 0)
        new_errors = current.get("error", 0)

        if is_first_run:
            return "baseline"
        elif delta > 0:
            return "regression"
        elif new_errors > 0:
            return "collection_error"
        else:
            return None  # clean run, silent

    def test_first_run_sends_baseline(self) -> None:
        result = self._compute_alert({}, {"passed": 100, "failed": 5, "error": 0, "total": 105})
        assert result == "baseline"

    def test_regression_detected(self) -> None:
        prev = {"failed": 3}
        current = {"passed": 97, "failed": 7, "error": 0, "total": 104}
        result = self._compute_alert(prev, current)
        assert result == "regression"

    def test_zero_delta_is_silent(self) -> None:
        prev = {"failed": 5}
        current = {"passed": 95, "failed": 5, "error": 0, "total": 100}
        result = self._compute_alert(prev, current)
        assert result is None

    def test_improved_results_are_silent(self) -> None:
        prev = {"failed": 10}
        current = {"passed": 95, "failed": 5, "error": 0, "total": 100}
        result = self._compute_alert(prev, current)
        assert result is None

    def test_collection_error_triggers_alert(self) -> None:
        prev = {"failed": 0}
        current = {"passed": 0, "failed": 0, "error": 3, "total": 3}
        result = self._compute_alert(prev, current)
        assert result == "collection_error"

    def test_delta_zero_with_no_errors_is_silent(self) -> None:
        prev = {"failed": 0}
        current = {"passed": 100, "failed": 0, "error": 0, "total": 100}
        result = self._compute_alert(prev, current)
        assert result is None


class TestExtractFailingNodeIds:
    def test_extracts_failed_and_error_outcomes(self) -> None:
        report = {
            "tests": [
                {"nodeid": "tests/unit/test_a.py::test_pass", "outcome": "passed"},
                {"nodeid": "tests/unit/test_a.py::test_fail", "outcome": "failed"},
                {"nodeid": "tests/unit/test_b.py::test_err", "outcome": "error"},
                {"nodeid": "tests/unit/test_c.py::test_skip", "outcome": "skipped"},
            ]
        }
        result = nrt.extract_failing_node_ids(report)
        assert result == [
            "tests/unit/test_a.py::test_fail",
            "tests/unit/test_b.py::test_err",
        ]

    def test_empty_report_returns_empty(self) -> None:
        assert nrt.extract_failing_node_ids({}) == []
        assert nrt.extract_failing_node_ids({"tests": []}) == []

    def test_dedupes_and_sorts(self) -> None:
        report = {
            "tests": [
                {"nodeid": "z::t", "outcome": "failed"},
                {"nodeid": "a::t", "outcome": "failed"},
                {"nodeid": "a::t", "outcome": "failed"},
            ]
        }
        assert nrt.extract_failing_node_ids(report) == ["a::t", "z::t"]

    def test_skips_entries_without_nodeid(self) -> None:
        report = {"tests": [{"outcome": "failed"}]}
        assert nrt.extract_failing_node_ids(report) == []


class TestReconfirmSerial:
    def test_empty_input_short_circuits(self) -> None:
        with patch("subprocess.run") as mock_run:
            confirmed, artifacts = nrt.reconfirm_serial([])
            mock_run.assert_not_called()
        assert confirmed == []
        assert artifacts == []

    def test_classifies_confirmed_vs_artifact(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        # test_x still fails serially (confirmed); test_y passes serially (artifact).
        serial_report = {
            "tests": [
                {"nodeid": "tests/unit/test_x.py::test_a", "outcome": "failed"},
                {"nodeid": "tests/unit/test_y.py::test_b", "outcome": "passed"},
            ]
        }
        report_path = Path(nrt.PYTEST_SERIAL_JSON_TMP)
        report_path.write_text(json.dumps(serial_report))

        class FakeResult:
            returncode = 1

        with patch("subprocess.run", return_value=FakeResult()):
            confirmed, artifacts = nrt.reconfirm_serial(
                ["tests/unit/test_y.py::test_b", "tests/unit/test_x.py::test_a"]
            )
        assert confirmed == ["tests/unit/test_x.py::test_a"]
        assert artifacts == ["tests/unit/test_y.py::test_b"]

    def test_fail_safe_treats_all_confirmed_on_error(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        node_ids = ["tests/unit/test_x.py::test_a", "tests/unit/test_y.py::test_b"]
        with patch("subprocess.run", side_effect=FileNotFoundError("no pytest")):
            confirmed, artifacts = nrt.reconfirm_serial(node_ids)
        assert confirmed == sorted(node_ids)
        assert artifacts == []


class TestComputeNewFailures:
    def test_new_confirmed_failure_detected(self) -> None:
        prev = {"failing_tests": ["tests/unit/test_a.py::test_1"]}
        confirmed = ["tests/unit/test_a.py::test_1", "tests/unit/test_b.py::test_2"]
        assert nrt.compute_new_failures(prev, confirmed) == ["tests/unit/test_b.py::test_2"]

    def test_shifting_set_same_count_is_not_new(self) -> None:
        # Same count as prev, but the failing test is one previously seen — a
        # stable failure, not a new regression.
        prev = {"failing_tests": ["tests/unit/test_a.py::test_1"]}
        confirmed = ["tests/unit/test_a.py::test_1"]
        assert nrt.compute_new_failures(prev, confirmed) == []

    def test_missing_prev_key_treats_all_as_new(self) -> None:
        prev: dict = {}
        confirmed = ["tests/unit/test_a.py::test_1"]
        assert nrt.compute_new_failures(prev, confirmed) == ["tests/unit/test_a.py::test_1"]

    def test_healed_failure_is_not_new(self) -> None:
        prev = {"failing_tests": ["tests/unit/test_a.py::test_1"]}
        assert nrt.compute_new_failures(prev, []) == []


class TestSendTelegram:
    def test_dry_run_does_not_call_subprocess(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch("subprocess.run") as mock_run:
            nrt.send_telegram("test message", dry_run=True)
            mock_run.assert_not_called()

    def test_missing_binary_logs_warning_and_returns(self, tmp_path: Path) -> None:
        nrt.TELEGRAM_BIN = tmp_path / "nonexistent-bin"
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch("shutil.which", return_value=None):
            with patch("subprocess.run") as mock_run:
                nrt.send_telegram("test message", dry_run=False)
                mock_run.assert_not_called()


class TestRunTtftGate:
    """Tests for the post-run TTFT gate hook (issue #1227)."""

    def test_pass_returns_none(self, tmp_path: Path) -> None:
        """A passing TTFT gate returns None — no alert fired."""
        log = tmp_path / "logs" / "cold_start_metrics.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            json.dumps({"session_type": "eng", "ttft_seconds": 30.0})
            + "\n"
            + json.dumps({"session_type": "eng", "ttft_seconds": 50.0})
            + "\n"
        )
        nrt.LOG_FILE = tmp_path / "nightly.log"
        msg = nrt.run_ttft_gate(log_file=log, session_type="eng", last=10, threshold=120.0)
        assert msg is None

    def test_fail_returns_alert_message(self, tmp_path: Path) -> None:
        """A failing gate returns a non-empty alert message string."""
        log = tmp_path / "logs" / "cold_start_metrics.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            json.dumps({"session_type": "eng", "ttft_seconds": 200.0})
            + "\n"
            + json.dumps({"session_type": "eng", "ttft_seconds": 250.0})
            + "\n"
        )
        nrt.LOG_FILE = tmp_path / "nightly.log"
        msg = nrt.run_ttft_gate(log_file=log, session_type="eng", last=10, threshold=120.0)
        assert msg is not None
        # Plan: report as a "regression" not a test failure
        assert "TTFT" in msg
        assert "regression" in msg.lower() or "regress" in msg.lower()

    def test_missing_log_returns_none_silently(self, tmp_path: Path) -> None:
        """Missing JSONL is not a failure — first runs may have no data yet."""
        log = tmp_path / "logs" / "absent.jsonl"
        nrt.LOG_FILE = tmp_path / "nightly.log"
        msg = nrt.run_ttft_gate(log_file=log, session_type="eng", last=10, threshold=120.0)
        assert msg is None

    def test_swallows_exceptions(self, tmp_path: Path) -> None:
        """run_ttft_gate must never crash the nightly run."""
        nrt.LOG_FILE = tmp_path / "nightly.log"
        with patch.object(nrt, "_invoke_check_ttft", side_effect=RuntimeError("boom")):
            msg = nrt.run_ttft_gate(
                log_file=tmp_path / "anything.jsonl",
                session_type="eng",
                last=10,
                threshold=120.0,
            )
            assert msg is None  # exceptions are swallowed
