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
