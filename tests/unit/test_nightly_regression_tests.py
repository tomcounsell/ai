"""Unit tests for scripts/nightly_regression_tests.py."""

from __future__ import annotations

import fcntl
import hashlib
import json
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def _fake_popen(returncode: int = 0, pid: int = 4242):
    """Build a MagicMock standing in for a subprocess.Popen instance."""
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.communicate.return_value = ("", "")
    return proc


class TestSpawnPytest:
    """_spawn_pytest owns the process group so a timeout kill reaches the
    whole xdist fleet, not just the wrapper's direct bash child (issue #2823).
    """

    def test_starts_new_session(self) -> None:
        proc = _fake_popen(returncode=0)
        with patch("subprocess.Popen", return_value=proc) as mock_popen:
            rc = nrt._spawn_pytest(["echo", "hi"], timeout=10)
        assert rc == 0
        assert mock_popen.call_args.kwargs["start_new_session"] is True

    def test_timeout_kills_process_group_then_reraises(self) -> None:
        proc = _fake_popen()
        proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=10)
        with (
            patch("subprocess.Popen", return_value=proc),
            patch("os.getpgid", return_value=999) as mock_getpgid,
            patch("os.killpg") as mock_killpg,
            patch("time.sleep"),
        ):
            with pytest.raises(subprocess.TimeoutExpired):
                nrt._spawn_pytest(["echo", "hi"], timeout=10)
        mock_getpgid.assert_called_once_with(proc.pid)
        assert mock_killpg.call_args_list[0].args == (999, signal.SIGTERM)
        assert mock_killpg.call_args_list[1].args == (999, signal.SIGKILL)
        proc.wait.assert_called_once()

    def test_timeout_kill_tolerates_already_dead_group(self) -> None:
        proc = _fake_popen()
        proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=10)
        with (
            patch("subprocess.Popen", return_value=proc),
            patch("os.getpgid", side_effect=ProcessLookupError),
            patch("time.sleep"),
        ):
            with pytest.raises(subprocess.TimeoutExpired):
                nrt._spawn_pytest(["echo", "hi"], timeout=10)
        proc.wait.assert_called_once()


class TestRunTests:
    """run_tests() runs the widened collection through pytest-clean.sh and
    returns a (raw_report, summary_or_None, returncode) 3-tuple (issue #2823).
    """

    def _write_report(self, path: Path, total: int = 5, error: int = 0, failed: int = 0) -> None:
        path.write_text(
            json.dumps(
                {
                    "summary": {
                        "passed": total - error - failed,
                        "failed": failed,
                        "error": error,
                        "skipped": 0,
                        "total": total,
                    },
                    "tests": [],
                }
            )
        )

    def test_unlinks_report_before_spawning(self, tmp_path: Path) -> None:
        report_path = tmp_path / "report.json"
        report_path.write_text('{"summary": {"total": 999}}')
        nrt.PYTEST_JSON_TMP = str(report_path)
        nrt.LOG_FILE = tmp_path / "test.log"

        def _spawn_side_effect(argv, timeout, env=None):
            # By the time the subprocess "runs", the stale report must be gone.
            assert not report_path.exists()
            self._write_report(report_path, total=3)
            return 0

        with patch.object(nrt, "_spawn_pytest", side_effect=_spawn_side_effect):
            raw, current, rc = nrt.run_tests()
        assert rc == 0
        assert current["total"] == 3

    def test_argv_uses_collection_paths_and_worker_constant(self, tmp_path: Path) -> None:
        report_path = tmp_path / "report.json"
        nrt.PYTEST_JSON_TMP = str(report_path)
        nrt.LOG_FILE = tmp_path / "test.log"
        captured = {}

        def _spawn_side_effect(argv, timeout, env=None):
            captured["argv"] = argv
            captured["env"] = env
            self._write_report(report_path, total=3)
            return 0

        with patch.object(nrt, "_spawn_pytest", side_effect=_spawn_side_effect):
            nrt.run_tests()
        argv = captured["argv"]
        assert argv[0] == str(nrt.PYTEST_CLEAN_SH)
        for p in nrt.COLLECTION_PATHS:
            assert p in argv
        # Assert against the CONSTANT, never the literal "6" — an operator
        # override on a differently-sized machine must not turn the suite red.
        assert argv[argv.index("-n") + 1] == nrt.NIGHTLY_XDIST_WORKERS
        env = captured["env"]
        assert env["TEST_DB_CLAIM_WAIT_S"] == "300"
        # Bounded by pytest-clean.sh's PYTEST_STALL_LIMIT_S (600s), not by
        # pyproject.toml's --timeout=420 (the claim runs before any per-item
        # timer is armed, since #2628).
        assert int(env["TEST_DB_CLAIM_WAIT_S"]) * 2 <= 600

    def test_missing_report_returns_none_none_rc(self, tmp_path: Path) -> None:
        report_path = tmp_path / "does_not_exist.json"
        nrt.PYTEST_JSON_TMP = str(report_path)
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch.object(nrt, "_spawn_pytest", return_value=0):
            raw, current, rc = nrt.run_tests()
        assert raw is None
        assert current is None
        assert rc == 0

    def test_corrupt_report_returns_none_none_rc(self, tmp_path: Path) -> None:
        report_path = tmp_path / "report.json"
        report_path.write_text("not valid json{{{")
        nrt.PYTEST_JSON_TMP = str(report_path)
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch.object(nrt, "_spawn_pytest", return_value=0):
            raw, current, rc = nrt.run_tests()
        assert raw is None
        assert current is None

    def test_timeout_propagates_rather_than_returning_sentinel(self, tmp_path: Path) -> None:
        """run_tests() lets TimeoutExpired propagate -- main() catches it
        explicitly and routes it through _fatal(), matching the original
        exception-arm shape rather than a swallowed sentinel."""
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch.object(
            nrt, "_spawn_pytest", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)
        ):
            with pytest.raises(subprocess.TimeoutExpired):
                nrt.run_tests()

    def test_healthy_report_yields_positive_total(self, tmp_path: Path) -> None:
        report_path = tmp_path / "report.json"
        nrt.PYTEST_JSON_TMP = str(report_path)
        nrt.LOG_FILE = tmp_path / "test.log"

        def _spawn_side_effect(argv, timeout, env=None):
            self._write_report(report_path, total=100)
            return 0

        with patch.object(nrt, "_spawn_pytest", side_effect=_spawn_side_effect):
            raw, current, rc = nrt.run_tests()
        assert rc == 0
        assert current["total"] == 100
        assert raw is not None


def test_module_constant_is_actually_seeded() -> None:
    """The shipped default must be a real measurement, not 0.

    Deliberately OUTSIDE TestValidateRunIntegrity: that class's autouse
    fixture zeroes MIN_EXPECTED_COLLECTED, and every floor test in it
    monkeypatches the value. So reverting the shipped default to 0 — which
    disables night one's only protection entirely — leaves the whole suite
    green. This is the one assertion that reads the module default as shipped.

    The bound is deliberately loose. Pinning the exact figure would fail on
    every commit that adds a test, and the value is a floor whose point is to
    tolerate growth. What must never happen is it silently returning to
    "no floor".
    """
    assert nrt.MIN_EXPECTED_COLLECTED > 10000, (
        "MIN_EXPECTED_COLLECTED looks unseeded. It is the only guard on a first "
        "run, which has no baseline to diff against."
    )
    # And it must actually bite: a run at half the floor has to trip.
    half = nrt.MIN_EXPECTED_COLLECTED // 2
    report = {"summary": {"total": half, "error": 0, "failed": 0}, "tests": []}
    reason, _ = nrt.validate_run_integrity(report, 0, {})
    assert reason is not None
    assert "truncated" in reason


class TestValidateRunIntegrity:
    """validate_run_integrity classifies a completed run before anything
    downstream trusts it (issue #2823). The headline case is the coverage
    floor: test-DB starvation yields zero error outcomes and a legal exit
    code, so only a floor on `total` catches it (spike-3).
    """

    @pytest.fixture(autouse=True)
    def _no_ambient_floor(self, monkeypatch):
        """Neutralise the seeded module constant for this class by default.

        MIN_EXPECTED_COLLECTED carries a real measured floor, so the
        small synthetic totals these cases use would trip it and mask the
        condition each one actually pins. Tests that are *about* the floor set
        it explicitly, as they already did.
        """
        monkeypatch.setattr(nrt, "MIN_EXPECTED_COLLECTED", 0)

    def _healthy(self, total=100, error=0, failed=0):
        return {"summary": {"total": total, "error": error, "failed": failed}, "tests": []}

    def test_seeded_constant_floors_a_truncated_first_run(self, monkeypatch) -> None:
        """The seeded measurement is what protects night one.

        With no prior state there is no baseline to diff against, so the
        module constant is the only thing standing between a partially-starved
        run and a baseline written from a fraction of the suite.
        """
        monkeypatch.setattr(nrt, "MIN_EXPECTED_COLLECTED", 15248)
        reason, _ = nrt.validate_run_integrity(self._healthy(total=9000), 0, {})
        assert reason is not None
        assert "truncated" in reason
        # A full run against the same floor passes.
        reason, _ = nrt.validate_run_integrity(self._healthy(total=15248), 0, {})
        assert reason is None

    def test_re_baseline_night_is_floorless(self, monkeypatch) -> None:
        """A changed collection must not inherit the old collection's floor.

        Otherwise deliberately narrowing COLLECTION_PATHS would trip the guard
        every night forever, judged against a scope that no longer applies.
        """
        monkeypatch.setattr(nrt, "MIN_EXPECTED_COLLECTED", 15248)
        prev = {"collection": ["tests/unit/"], "total": 13788}
        reason, _ = nrt.validate_run_integrity(self._healthy(total=42), 0, prev)
        assert reason is None

    def test_missing_report_trips(self) -> None:
        reason, warnings = nrt.validate_run_integrity(None, 0, {})
        assert reason is not None
        assert "did not happen" in reason

    def test_exit_1_with_healthy_report_does_not_trip(self) -> None:
        # Pytest's 1 means "tests failed" -- a legitimate red night.
        reason, warnings = nrt.validate_run_integrity(self._healthy(), 1, {})
        assert reason is None

    def test_exit_0_zero_tests_trips(self) -> None:
        """The spike-3 headline case: exit 0, zero tests executed."""
        reason, warnings = nrt.validate_run_integrity(self._healthy(total=0), 0, {})
        assert reason is not None
        assert "did not happen" in reason

    @pytest.mark.parametrize("rc", [2, 3, 4, 5])
    def test_usage_and_internal_error_codes_trip(self, rc: int) -> None:
        reason, warnings = nrt.validate_run_integrity(self._healthy(), rc, {})
        assert reason is not None

    @pytest.mark.parametrize("rc", [-9, 130, 143])
    def test_signal_death_exit_codes_trip(self, rc: int) -> None:
        """spike-7 measured exit 143 from the wrapper's own stall watchdog."""
        reason, warnings = nrt.validate_run_integrity(self._healthy(), rc, {})
        assert reason is not None

    def test_missing_summary_key_trips(self) -> None:
        reason, warnings = nrt.validate_run_integrity({"tests": []}, 0, {})
        assert reason is not None
        assert "summary" in reason

    def test_fixture_error_storm_trips(self) -> None:
        report = self._healthy(total=10000, error=9000, failed=0)
        reason, warnings = nrt.validate_run_integrity(report, 1, {})
        assert reason is not None
        assert "errored at setup" in reason

    def test_high_failed_count_alone_does_not_trip(self) -> None:
        """A very red suite (failed, not error) must never read as infra failure."""
        report = self._healthy(total=10000, error=0, failed=9000)
        reason, warnings = nrt.validate_run_integrity(report, 1, {})
        assert reason is None

    def test_error_boundary_below_threshold_does_not_trip(self) -> None:
        # max(50, 0.02*total) with total=1000 -> 50. 50 errors == boundary, not above.
        report = self._healthy(total=1000, error=50)
        reason, warnings = nrt.validate_run_integrity(report, 1, {})
        assert reason is None

    def test_error_boundary_above_threshold_trips(self) -> None:
        report = self._healthy(total=1000, error=51)
        reason, warnings = nrt.validate_run_integrity(report, 1, {})
        assert reason is not None

    def test_coverage_floor_trips_on_partial_starvation(self) -> None:
        """The round-6 fix: partial starvation has error=0, failed=0, exit 0,
        and a merely-reduced total -- every absolute check passes it. Only a
        floor on `total` against the prior same-collection baseline catches it.
        """
        report = self._healthy(total=9000, error=0, failed=0)
        prev = {"collection": nrt.COLLECTION_PATHS, "total": 14899}
        reason, warnings = nrt.validate_run_integrity(report, 0, prev)
        assert reason is not None
        assert "truncated" in reason

    def test_floor_boundary_just_above_does_not_trip(self) -> None:
        prev = {"collection": nrt.COLLECTION_PATHS, "total": 1000}
        report = self._healthy(total=901)  # just above 0.9 * 1000
        reason, warnings = nrt.validate_run_integrity(report, 0, prev)
        assert reason is None

    def test_floor_boundary_just_below_trips(self) -> None:
        prev = {"collection": nrt.COLLECTION_PATHS, "total": 1000}
        report = self._healthy(total=899)  # just below 0.9 * 1000
        reason, warnings = nrt.validate_run_integrity(report, 0, prev)
        assert reason is not None

    def test_pre_baseline_floor_from_module_constant(self, monkeypatch) -> None:
        monkeypatch.setattr(nrt, "MIN_EXPECTED_COLLECTED", 1000)
        report = self._healthy(total=899)
        reason, warnings = nrt.validate_run_integrity(report, 0, {})
        assert reason is not None
        assert "truncated" in reason

    def test_pre_baseline_floor_from_persisted_state(self, monkeypatch) -> None:
        monkeypatch.setattr(nrt, "MIN_EXPECTED_COLLECTED", 0)
        prev = {"min_expected_collected": 1000}
        report = self._healthy(total=899)
        reason, warnings = nrt.validate_run_integrity(report, 0, prev)
        assert reason is not None

    def test_no_floor_when_both_unset(self, monkeypatch) -> None:
        """The widening night: no persisted floor, unset constant -> floorless."""
        monkeypatch.setattr(nrt, "MIN_EXPECTED_COLLECTED", 0)
        report = self._healthy(total=1)
        reason, warnings = nrt.validate_run_integrity(report, 0, {})
        assert reason is None

    def test_collection_mismatch_skips_floor_and_warning_however_far_total_dropped(self) -> None:
        prev = {"collection": ["tests/unit/"], "total": 100000}
        report = self._healthy(total=1)
        reason, warnings = nrt.validate_run_integrity(report, 0, prev)
        assert reason is None
        assert warnings == []

    def test_shallow_shrink_warns_not_trips(self) -> None:
        prev = {"collection": nrt.COLLECTION_PATHS, "total": 1000}
        report = self._healthy(total=950)  # 95% of baseline
        reason, warnings = nrt.validate_run_integrity(report, 0, prev)
        assert reason is None
        assert any("shrank" in w for w in warnings)

    def test_no_shrink_no_warning(self) -> None:
        prev = {"collection": nrt.COLLECTION_PATHS, "total": 1000}
        report = self._healthy(total=1000)
        reason, warnings = nrt.validate_run_integrity(report, 0, prev)
        assert reason is None
        assert warnings == []


class TestReconfirmSerial:
    def test_empty_input_short_circuits(self) -> None:
        with patch.object(nrt, "_spawn_pytest") as mock_spawn:
            confirmed, artifacts, trusted = nrt.reconfirm_serial([])
            mock_spawn.assert_not_called()
        assert confirmed == []
        assert artifacts == []
        assert trusted is True

    def test_max_reconfirm_nodes_bails_without_spawning(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        nodes = [f"tests/unit/test_{i}.py::test_x" for i in range(nrt.MAX_RECONFIRM_NODES + 1)]
        with patch.object(nrt, "_spawn_pytest") as mock_spawn:
            confirmed, artifacts, trusted = nrt.reconfirm_serial(nodes)
            mock_spawn.assert_not_called()
        assert confirmed == sorted(nodes)
        assert artifacts == []
        assert trusted is True

    def test_classifies_confirmed_vs_artifact(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        serial_report = {
            "tests": [
                {"nodeid": "tests/unit/test_x.py::test_a", "outcome": "failed"},
                {"nodeid": "tests/unit/test_y.py::test_b", "outcome": "passed"},
            ]
        }
        report_path = tmp_path / "serial.json"
        report_path.write_text(json.dumps(serial_report))
        nrt.PYTEST_SERIAL_JSON_TMP = str(report_path)

        def _spawn_side_effect(argv, timeout, env=None):
            report_path.write_text(json.dumps(serial_report))
            return 1

        with patch.object(nrt, "_spawn_pytest", side_effect=_spawn_side_effect):
            confirmed, artifacts, trusted = nrt.reconfirm_serial(
                ["tests/unit/test_y.py::test_b", "tests/unit/test_x.py::test_a"]
            )
        assert confirmed == ["tests/unit/test_x.py::test_a"]
        assert artifacts == ["tests/unit/test_y.py::test_b"]
        assert trusted is True

    def test_fail_safe_treats_all_confirmed_on_error(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        node_ids = ["tests/unit/test_x.py::test_a", "tests/unit/test_y.py::test_b"]
        with patch.object(nrt, "_spawn_pytest", side_effect=FileNotFoundError("no pytest")):
            confirmed, artifacts, trusted = nrt.reconfirm_serial(node_ids)
        assert confirmed == sorted(node_ids)
        assert artifacts == []
        assert trusted is True

    def test_timeout_treats_all_confirmed_and_trusted(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        node_ids = ["tests/unit/test_x.py::test_a"]
        with patch.object(
            nrt, "_spawn_pytest", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)
        ):
            confirmed, artifacts, trusted = nrt.reconfirm_serial(node_ids)
        assert confirmed == node_ids
        assert trusted is True

    def test_incomplete_coverage_is_untrusted_and_empties_confirmed(self, tmp_path: Path) -> None:
        """A serial report that does not cover every input node must not be
        read as "everything passed" -- that is the starved-serial-pass false
        green this check exists to prevent."""
        nrt.LOG_FILE = tmp_path / "test.log"
        report_path = tmp_path / "serial.json"
        nrt.PYTEST_SERIAL_JSON_TMP = str(report_path)
        # Report covers only one of the two input nodes.
        partial_report = {
            "tests": [{"nodeid": "tests/unit/test_a.py::test_1", "outcome": "passed"}]
        }

        def _spawn_side_effect(argv, timeout, env=None):
            report_path.write_text(json.dumps(partial_report))
            return 0

        with patch.object(nrt, "_spawn_pytest", side_effect=_spawn_side_effect):
            confirmed, artifacts, trusted = nrt.reconfirm_serial(
                ["tests/unit/test_a.py::test_1", "tests/unit/test_b.py::test_2"]
            )
        assert trusted is False
        assert confirmed == []
        assert artifacts == []

    def test_full_coverage_mixed_outcomes_is_trusted(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        report_path = tmp_path / "serial.json"
        nrt.PYTEST_SERIAL_JSON_TMP = str(report_path)
        report = {
            "tests": [
                {"nodeid": "a::t1", "outcome": "error"},
                {"nodeid": "b::t2", "outcome": "failed"},
            ]
        }

        def _spawn_side_effect(argv, timeout, env=None):
            report_path.write_text(json.dumps(report))
            return 1

        with patch.object(nrt, "_spawn_pytest", side_effect=_spawn_side_effect):
            confirmed, artifacts, trusted = nrt.reconfirm_serial(["a::t1", "b::t2"])
        assert trusted is True
        assert confirmed == ["a::t1", "b::t2"]

    def test_env_carries_claim_wait_override(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        report_path = tmp_path / "serial.json"
        nrt.PYTEST_SERIAL_JSON_TMP = str(report_path)
        captured = {}

        def _spawn_side_effect(argv, timeout, env=None):
            captured["env"] = env
            report_path.write_text(json.dumps({"tests": []}))
            return 0

        with patch.object(nrt, "_spawn_pytest", side_effect=_spawn_side_effect):
            nrt.reconfirm_serial(["a::t1"])
        assert captured["env"]["TEST_DB_CLAIM_WAIT_S"] == "300"


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


class TestFatal:
    """_fatal() gives every pre-alert exit one alerting path (issue #2823)."""

    def test_logs_alerts_and_returns_1(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch.object(nrt, "send_telegram") as mock_send:
            rc = nrt._fatal("something broke", dry_run=False)
        assert rc == 1
        mock_send.assert_called_once()
        assert "something broke" in mock_send.call_args.args[0]
        assert "something broke" in tmp_path.joinpath("test.log").read_text()

    def test_send_telegram_failure_does_not_crash(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch.object(nrt, "send_telegram", side_effect=RuntimeError("telegram down")):
            with pytest.raises(RuntimeError):
                # send_telegram itself is documented never-fatal in production
                # (it swallows its own exceptions); this asserts _fatal does
                # not add its OWN try/except around it, per the plan.
                nrt._fatal("boom", dry_run=False)


class TestRunLock:
    """Tests for the run-collision lock (fcntl.flock sidecar file)."""

    def test_contention_returns_none_and_skips_run(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "nightly_tests.lock"
        nrt.LOG_FILE = tmp_path / "test.log"

        # Hold the lock in-process, simulating a concurrent nightly run.
        holder = open(lock_path, "a+")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with patch("subprocess.run") as mock_run:
                result = nrt._acquire_run_lock(lock_path)
                mock_run.assert_not_called()
            assert result is None
        finally:
            holder.close()

    def test_main_returns_0_on_collision_without_running_tests(self, tmp_path: Path) -> None:
        nrt.LOCK_FILE = tmp_path / "nightly_tests.lock"
        nrt.LOG_FILE = tmp_path / "test.log"

        holder = open(nrt.LOCK_FILE, "a+")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with (
                patch("sys.argv", ["nightly_regression_tests.py"]),
                # .env is machine-local and absent from worktrees, where the
                # real load_env_or_die() would refuse for reasons unrelated
                # to what this test asserts (#2573).
                patch.object(nrt, "load_env_or_die", return_value=(42, None)),
                patch.object(nrt, "run_tests") as mock_run_tests,
                patch.object(nrt, "send_telegram") as mock_send_telegram,
            ):
                result = nrt.main()
            mock_run_tests.assert_not_called()
            mock_send_telegram.assert_not_called()
            assert result == 0
        finally:
            holder.close()

    def test_clean_acquire_and_release_allows_subsequent_acquire(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "nightly_tests.lock"
        nrt.LOG_FILE = tmp_path / "test.log"

        first = nrt._acquire_run_lock(lock_path)
        assert first is not None
        first.close()  # Release the lock explicitly.

        second = nrt._acquire_run_lock(lock_path)
        assert second is not None
        second.close()


class TestSummarizeFailures:
    """Tests for the LLM-backed failure summarizer with raw-preview fallback."""

    @staticmethod
    def _closing(value=None, exc=None):
        """Build an asyncio.run replacement that closes the coroutine arg.

        Avoids "coroutine was never awaited" RuntimeWarnings since the real
        ``run_typed(...)`` coroutine object is still constructed by the call
        site even though ``asyncio.run`` itself is mocked out.
        """

        def _fake_run(coro, *a, **kw):
            coro.close()
            if exc is not None:
                raise exc
            return value

        return _fake_run

    def test_fallback_on_exception(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        confirmed = ["tests/unit/test_a.py::test_1", "tests/unit/test_b.py::test_2"]
        with patch("asyncio.run", side_effect=self._closing(exc=RuntimeError("llm boom"))):
            result = nrt.summarize_failures(confirmed, {})
        assert result == nrt._raw_failure_preview(confirmed)
        log_contents = nrt.LOG_FILE.read_text()
        assert "summarize_failures" in log_contents or "WARNING" in log_contents

    def test_empty_input_returns_raw_fallback_no_llm_call(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch("asyncio.run") as mock_run:
            result = nrt.summarize_failures([], {})
            mock_run.assert_not_called()
        assert result == ""

    def test_success_drives_run_typed_via_asyncio_run(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        confirmed = ["tests/unit/test_a.py::test_1"]
        with patch(
            "asyncio.run",
            side_effect=self._closing(value=nrt.FailureSummary(summary="mocked")),
        ) as mock_run:
            result = nrt.summarize_failures(confirmed, {})
            mock_run.assert_called_once()
        assert result == "mocked"


class TestComputeDispatchSet:
    """Already-filed nodes must not reach triage a second time (issue #2559)."""

    def test_new_node_is_dispatchable(self) -> None:
        prev = {"dispatched_nodes": ["tests/unit/test_a.py::test_1"]}
        confirmed = ["tests/unit/test_a.py::test_1", "tests/unit/test_b.py::test_2"]
        assert nrt.compute_dispatch_set(prev, confirmed) == ["tests/unit/test_b.py::test_2"]

    def test_standing_failure_is_suppressed(self) -> None:
        """The #2559 defect: a node with an issue already open must not re-dispatch.

        Under the old code the whole confirmed set went out whenever any single
        failure was new, which re-filed the same dead watchdog node in #2429,
        #2430 and #2462.
        """
        standing = "tests/unit/test_bridge_watchdog.py::test_dead_node"
        prev = {"dispatched_nodes": [standing], "failing_tests": [standing]}
        assert nrt.compute_dispatch_set(prev, [standing]) == []
        assert nrt.compute_dispatch_set(prev, [standing, "tests/unit/x.py::test_new"]) == [
            "tests/unit/x.py::test_new"
        ]

    def test_failed_dispatch_is_retried_next_run(self) -> None:
        """A node whose dispatch failed is still unfiled, so it stays dispatchable.

        This is why dispatch diffs against dispatched_nodes rather than reusing
        compute_new_failures — the node is no longer "new" but is still unfiled.
        """
        node = "tests/unit/test_a.py::test_1"
        prev = {"failing_tests": [node], "dispatched_nodes": []}
        assert nrt.compute_new_failures(prev, [node]) == []
        assert nrt.compute_dispatch_set(prev, [node]) == [node]

    def test_absent_key_falls_back_to_prior_confirmed_set(self) -> None:
        """State written before dispatch tracking existed must not mass-dispatch."""
        prev = {"failing_tests": ["tests/unit/test_a.py::test_1"]}
        assert nrt.compute_dispatch_set(prev, ["tests/unit/test_a.py::test_1"]) == []

    def test_empty_prev_dispatches_everything(self) -> None:
        assert nrt.compute_dispatch_set({}, ["tests/unit/test_a.py::test_1"]) == [
            "tests/unit/test_a.py::test_1"
        ]


class TestComputeNewFailures:
    def test_new_confirmed_failure_detected(self) -> None:
        prev = {"failing_tests": ["tests/unit/test_a.py::test_1"]}
        confirmed = ["tests/unit/test_a.py::test_1", "tests/unit/test_b.py::test_2"]
        assert nrt.compute_new_failures(prev, confirmed) == ["tests/unit/test_b.py::test_2"]

    def test_shifting_set_same_count_is_not_new(self) -> None:
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


class TestCarryDispatchedNodes:
    """The persisted dispatched set keeps failing nodes and retires passing ones."""

    def test_keeps_still_failing_and_adds_new(self) -> None:
        prev = {"dispatched_nodes": ["a::t1"]}
        assert nrt.carry_dispatched_nodes(prev, ["a::t1", "b::t2"], ["b::t2"]) == ["a::t1", "b::t2"]

    def test_drops_a_node_that_stopped_failing(self) -> None:
        """A fixed node must become dispatchable again if it ever regresses."""
        prev = {"dispatched_nodes": ["a::t1"]}
        assert nrt.carry_dispatched_nodes(prev, [], []) == []
        assert nrt.compute_dispatch_set({"dispatched_nodes": []}, ["a::t1"]) == ["a::t1"]

    def test_retires_a_renamed_node_id(self) -> None:
        """df6097fe6 renamed the watchdog node the churn kept citing.

        A node ID that can never match again simply stops appearing in the
        confirmed set, so it falls out of the state file with no special case.
        """
        old = "tests/unit/test_x.py::test_bridge_watchdog_no_agent_session_import"
        new = "tests/unit/test_x.py::test_bridge_watchdog_has_no_module_level_agent_session_import"
        prev = {"dispatched_nodes": [old]}
        assert nrt.carry_dispatched_nodes(prev, [new], [new]) == [new]

    def test_failed_dispatch_records_nothing(self) -> None:
        prev = {"dispatched_nodes": []}
        assert nrt.carry_dispatched_nodes(prev, ["a::t1"], []) == []


class TestBuildTriagePrompt:
    """Titles are computed in Python -- literal, not agent-derived (#2559)."""

    def test_literal_titles_present(self) -> None:
        nodes = ["tests/unit/test_a.py::test_1", "tests/unit/test_b.py::test_2"]
        prompt = nrt._build_triage_prompt(nodes)
        for n in nodes:
            assert f"Nightly regression: {n}" in prompt


class TestMaybeDispatchTriage:
    """Tests for triage-session dispatch."""

    def _fake_result(self, stdout: str, returncode: int = 0):
        class FakeResult:
            pass

        r = FakeResult()
        r.stdout = stdout
        r.returncode = returncode
        r.stderr = ""
        return r

    def test_dry_run_spawns_no_session(self, tmp_path: Path) -> None:
        """``--dry-run`` must not create a real Eng session or file real issues.

        It previously suppressed only the Telegram send while still spawning
        the session subprocess, which made the one command an operator would
        reach for to preview a run the very command that could not be
        previewed safely.
        """
        nrt.LOG_FILE = tmp_path / "test.log"
        nodes = ["tests/unit/test_a.py::test_1"]
        with patch("subprocess.run") as mock_run:
            session_id = nrt.maybe_dispatch_triage_session(nodes, dry_run=True)
        mock_run.assert_not_called()
        assert session_id == nrt.DRY_RUN_SESSION_ID

    def test_dispatch_once(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        nodes = ["tests/unit/test_a.py::test_1"]
        with patch(
            "subprocess.run", return_value=self._fake_result('{"session_id": "abc123"}')
        ) as mock_run:
            session_id = nrt.maybe_dispatch_triage_session(nodes)
        assert session_id == "abc123"
        argv = mock_run.call_args.args[0]
        assert argv[0] == sys.executable
        assert "--role" in argv
        assert "eng" in argv
        assert "--slug" in argv
        slug_idx = argv.index("--slug")
        expected_slug_hash = hashlib.sha256(",".join(sorted(set(nodes))).encode()).hexdigest()[:8]
        assert argv[slug_idx + 1] == f"nightly-triage-{expected_slug_hash}"
        assert "--json" in argv

    def test_literal_titles_in_prompt(self, tmp_path: Path) -> None:
        """A prompt that only INSTRUCTS the agent to build a title proves
        nothing (#2559) -- the titles must be computed and asserted literally."""
        nrt.LOG_FILE = tmp_path / "test.log"
        nodes = ["tests/unit/test_a.py::test_1", "tests/unit/test_b.py::test_2"]
        with patch(
            "subprocess.run", return_value=self._fake_result('{"session_id": "abc123"}')
        ) as mock_run:
            nrt.maybe_dispatch_triage_session(nodes)
        argv = mock_run.call_args.args[0]
        msg = argv[argv.index("--message") + 1]
        for n in nodes:
            assert f"Nightly regression: {n}" in msg

    def test_prompt_override_replaces_default(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch(
            "subprocess.run", return_value=self._fake_result('{"session_id": "abc123"}')
        ) as mock_run:
            nrt.maybe_dispatch_triage_session(["seed:3"], prompt="CUSTOM UMBRELLA PROMPT")
        argv = mock_run.call_args.args[0]
        msg = argv[argv.index("--message") + 1]
        assert msg == "CUSTOM UMBRELLA PROMPT"

    def test_slug_suffix_override(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch(
            "subprocess.run", return_value=self._fake_result('{"session_id": "abc123"}')
        ) as mock_run:
            nrt.maybe_dispatch_triage_session(["seed:3"], slug_suffix="baseline")
        argv = mock_run.call_args.args[0]
        assert argv[argv.index("--slug") + 1] == "nightly-triage-baseline"

    def test_subprocess_failure_safe(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch("subprocess.run", side_effect=FileNotFoundError("no python")):
            session_id = nrt.maybe_dispatch_triage_session(["tests/unit/test_a.py::test_1"])
        assert session_id is None

    def test_session_id_parsed_from_json_stdout(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch("subprocess.run", return_value=self._fake_result('{"session_id": "xyz789"}')):
            session_id = nrt.maybe_dispatch_triage_session(["tests/unit/test_a.py::test_1"])
        assert session_id == "xyz789"

    def test_malformed_stdout_returns_none_not_crash(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch("subprocess.run", return_value=self._fake_result("not json")):
            session_id = nrt.maybe_dispatch_triage_session(["tests/unit/test_a.py::test_1"])
        assert session_id is None

    def test_empty_stdout_returns_none_not_crash(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch("subprocess.run", return_value=self._fake_result("")):
            session_id = nrt.maybe_dispatch_triage_session(["tests/unit/test_a.py::test_1"])
        assert session_id is None

    def test_empty_dispatch_set_no_dispatch(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch("subprocess.run") as mock_run:
            assert nrt.maybe_dispatch_triage_session([]) is None
            mock_run.assert_not_called()


class TestMainDispatchPersistence:
    """main() persists only what actually went out to triage (issue #2559)."""

    def _run_result(self, confirmed: list[str], total: int = 11):
        return {
            "passed": total - len(confirmed),
            "failed": len(confirmed),
            "error": 0,
            "skipped": 0,
            "total": total,
            "failing_parallel": list(confirmed),
            "run_at": "2026-07-21T00:00:00+00:00",
        }

    def _run_main(
        self,
        tmp_path: Path,
        prev_state: dict,
        confirmed: list[str],
        dispatch_return,
        *,
        serial_trusted: bool = True,
    ):
        nrt.LOG_FILE = tmp_path / "test.log"
        nrt.LOCK_FILE = tmp_path / "nightly_tests.lock"
        nrt.LAST_RUN_FILE = tmp_path / "last_run.json"
        nrt.LAST_RUN_FILE.write_text(json.dumps(prev_state))

        raw_report = {"summary": {"total": 11}, "tests": []}
        run_result = (raw_report, self._run_result(confirmed), 0)
        with (
            # NOT --dry-run: these cases assert on the state main() persists,
            # and --dry-run deliberately writes none. Telegram and dispatch are
            # patched below, so the real path is already side-effect free here.
            patch("sys.argv", ["nightly_regression_tests.py"]),
            # These cases exercise dispatch bookkeeping against a deliberately
            # tiny synthetic run (total=11). The real coverage floor (measured,
            # measured 2026-08-20) would trip validate_run_integrity on every
            # one of them before dispatch was reached. The floor has its own
            # coverage in TestValidateRunIntegrity.
            patch.object(nrt, "MIN_EXPECTED_COLLECTED", 0),
            patch.object(nrt, "load_env_or_die", return_value=(42, None)),
            patch.object(nrt, "run_tests", return_value=run_result),
            patch.object(
                nrt, "reconfirm_serial", return_value=(list(confirmed), [], serial_trusted)
            ),
            patch.object(nrt, "summarize_failures", return_value="mocked summary"),
            patch.object(
                nrt, "maybe_dispatch_triage_session", return_value=dispatch_return
            ) as mock_dispatch,
            patch.object(nrt, "send_telegram") as mock_send,
            patch.object(nrt, "run_ttft_gate", return_value=None),
            patch.object(nrt, "_get_head_commit", return_value="deadbeef"),
        ):
            rc = nrt.main()
        return rc, json.loads(nrt.LAST_RUN_FILE.read_text()), mock_dispatch, mock_send

    def _prev(self, **kwargs):
        base = {"collection": nrt.COLLECTION_PATHS}
        base.update(kwargs)
        return base

    def test_standing_failure_is_not_re_dispatched(self, tmp_path: Path) -> None:
        """The end-to-end #2559 regression: a filed node plus a new one dispatches one."""
        standing = "tests/unit/test_watchdog.py::test_dead_node"
        fresh = "tests/unit/test_new.py::test_regression"
        prev = self._prev(failing_tests=[standing], dispatched_nodes=[standing])
        rc, saved, mock_dispatch, _ = self._run_main(
            tmp_path, prev, [standing, fresh], "triage-session-1"
        )
        assert rc == 0
        mock_dispatch.assert_called_once_with([fresh], dry_run=False)
        assert saved["dispatched_nodes"] == sorted([standing, fresh])

    def test_no_dispatch_when_everything_is_already_filed(self, tmp_path: Path) -> None:
        standing = "tests/unit/test_watchdog.py::test_dead_node"
        prev = self._prev(failing_tests=[standing], dispatched_nodes=[standing])
        rc, saved, mock_dispatch, _ = self._run_main(tmp_path, prev, [standing], None)
        assert rc == 0
        mock_dispatch.assert_called_once_with([], dry_run=False)
        assert saved["dispatched_nodes"] == [standing]

    def test_failed_dispatch_leaves_nodes_unfiled_for_retry(self, tmp_path: Path) -> None:
        node = "tests/unit/test_a.py::test_new"
        prev = self._prev(
            failing_tests=[], dispatched_nodes=[], dispatched_session_id="earlier-session"
        )
        rc, saved, mock_dispatch, _ = self._run_main(tmp_path, prev, [node], None)
        assert rc == 0
        mock_dispatch.assert_called_once_with([node], dry_run=False)
        assert saved["dispatched_nodes"] == []
        assert saved["dispatched_session_id"] == "earlier-session"

    def test_successful_dispatch_records_the_nodes_and_session(self, tmp_path: Path) -> None:
        node = "tests/unit/test_a.py::test_new"
        prev = self._prev(
            failing_tests=[], dispatched_nodes=[], dispatched_session_id="earlier-session"
        )
        rc, saved, _, mock_send = self._run_main(tmp_path, prev, [node], "new-session-id")
        assert rc == 0
        assert saved["dispatched_nodes"] == [node]
        assert saved["dispatched_session_id"] == "new-session-id"
        mock_send.assert_called_once()
        assert "new-session-id" in mock_send.call_args.args[0]

    def test_node_that_stopped_failing_drops_out(self, tmp_path: Path) -> None:
        prev = self._prev(failing_tests=["a::t1"], dispatched_nodes=["a::t1"])
        rc, saved, _, _ = self._run_main(tmp_path, prev, [], None)
        assert rc == 0
        assert saved["dispatched_nodes"] == []

    def test_baseline_run_seeds_rather_than_dispatches(self, tmp_path: Path) -> None:
        """A first run declares the known state; it must not file the whole suite."""
        standing = ["a::t1", "b::t2"]
        rc, saved, mock_dispatch, _ = self._run_main(tmp_path, {}, standing, "seed-session")
        assert rc == 0
        # The default per-node dispatch call never fires on a seed run.
        mock_dispatch.assert_called_once()
        assert mock_dispatch.call_args.kwargs.get("slug_suffix") == "baseline"
        assert saved["dispatched_nodes"] == sorted(standing)
        assert saved["seed_size"] == 2
        # The seed run must PRODUCE seeded_nodes, not merely carry one forward.
        # Every other seeded-node test injects this set through _prev(), so
        # without this assertion deleting the line that writes it leaves the
        # whole suppression mechanism green while doing nothing on night one —
        # the exact night it exists for.
        assert saved["seeded_nodes"] == sorted(standing)

    def _dry_run_main(self, tmp_path: Path, prev_state: dict, confirmed: list[str]):
        """Run main() under --dry-run, returning (rc, dispatch_mock, saved_bytes)."""
        nrt.LOG_FILE = tmp_path / "test.log"
        nrt.LOCK_FILE = tmp_path / "nightly_tests.lock"
        nrt.LAST_RUN_FILE = tmp_path / "last_run.json"
        pre_existing = json.dumps(prev_state)
        nrt.LAST_RUN_FILE.write_text(pre_existing)

        raw_report = {"summary": {"total": 11}, "tests": []}
        with (
            patch("sys.argv", ["nightly_regression_tests.py", "--dry-run"]),
            patch.object(nrt, "MIN_EXPECTED_COLLECTED", 0),
            patch.object(nrt, "load_env_or_die", return_value=(42, None)),
            patch.object(
                nrt, "run_tests", return_value=(raw_report, self._run_result(confirmed), 0)
            ),
            patch.object(nrt, "reconfirm_serial", return_value=(list(confirmed), [], True)),
            patch.object(nrt, "summarize_failures", return_value="mocked"),
            patch.object(nrt, "maybe_dispatch_triage_session", return_value="sentinel") as disp,
            patch.object(nrt, "send_telegram"),
            patch.object(nrt, "run_ttft_gate", return_value=None),
            patch.object(nrt, "_get_head_commit", return_value="deadbeef"),
        ):
            rc = nrt.main()
        return rc, disp, pre_existing

    def test_dry_run_propagates_to_per_node_dispatch(self, tmp_path: Path) -> None:
        """--dry-run must reach the per-node dispatch call, not just Telegram.

        _run_main deliberately does NOT pass --dry-run (it asserts on persisted
        state), so without this case, breaking `dry_run=args.dry_run` at the
        call site leaves every test green while restoring #2899 in full:
        `--dry-run` spawning real Eng sessions that file real GitHub issues.
        """
        node = "tests/unit/test_a.py::test_new"
        prev = self._prev(failing_tests=[], dispatched_nodes=[])
        _, disp, _ = self._dry_run_main(tmp_path, prev, [node])
        assert disp.call_args.kwargs.get("dry_run") is True

    def test_dry_run_propagates_to_seed_dispatch(self, tmp_path: Path) -> None:
        """The seed path has its own dispatch call site and its own kwarg.

        Its blast radius is larger than the per-node one: a seed umbrella
        covers the ENTIRE currently-failing population, so an un-propagated
        flag here files against everything at once.
        """
        _, disp, _ = self._dry_run_main(tmp_path, {}, ["a::t1", "b::t2"])
        assert disp.call_args.kwargs.get("slug_suffix") == "baseline"
        assert disp.call_args.kwargs.get("dry_run") is True

    def test_dry_run_writes_no_state(self, tmp_path: Path) -> None:
        """--dry-run must not persist a baseline.

        The dry-run dispatch short-circuit returns a truthy sentinel so the
        caller's success path runs realistically. That makes persisting
        actively harmful: on a seed night the success path writes
        `seeded_nodes`, and because that set is sticky, the whole absorbed
        population would be suppressed forever against an umbrella issue that
        was never filed — reachable through the one command whose purpose is
        to change nothing.
        """
        nrt.LOG_FILE = tmp_path / "test.log"
        nrt.LOCK_FILE = tmp_path / "nightly_tests.lock"
        nrt.LAST_RUN_FILE = tmp_path / "last_run.json"
        pre_existing = json.dumps({"collection": nrt.COLLECTION_PATHS, "failing_tests": []})
        nrt.LAST_RUN_FILE.write_text(pre_existing)

        raw_report = {"summary": {"total": 11}, "tests": []}
        confirmed = ["a::t1", "b::t2"]
        with (
            patch("sys.argv", ["nightly_regression_tests.py", "--dry-run"]),
            patch.object(nrt, "MIN_EXPECTED_COLLECTED", 0),
            patch.object(nrt, "load_env_or_die", return_value=(42, None)),
            patch.object(
                nrt, "run_tests", return_value=(raw_report, self._run_result(confirmed), 0)
            ),
            patch.object(nrt, "reconfirm_serial", return_value=(list(confirmed), [], True)),
            patch.object(nrt, "summarize_failures", return_value="mocked"),
            patch.object(nrt, "maybe_dispatch_triage_session", return_value="sentinel"),
            patch.object(nrt, "send_telegram"),
            patch.object(nrt, "run_ttft_gate", return_value=None),
            patch.object(nrt, "_get_head_commit", return_value="deadbeef"),
        ):
            rc = nrt.main()

        assert rc == 0
        assert nrt.LAST_RUN_FILE.read_text() == pre_existing, "--dry-run persisted state"

    def test_seed_prompt_carries_the_exact_umbrella_title(self, tmp_path: Path) -> None:
        standing = ["a::t1", "b::t2"]
        rc, saved, mock_dispatch, _ = self._run_main(tmp_path, {}, standing, "seed-session")
        assert rc == 0
        prompt = mock_dispatch.call_args.kwargs.get("prompt")
        expected_title = (
            f"Nightly regression baseline: {len(standing)} nodes absorbed on {saved['head_commit']}"
        )
        assert expected_title in prompt

    def test_dispatched_hash_is_gone_from_persisted_state(self, tmp_path: Path) -> None:
        prev = self._prev(failing_tests=[], dispatched_hash="stale", dispatched_nodes=[])
        rc, saved, _, _ = self._run_main(tmp_path, prev, ["a::t1"], "s1")
        assert rc == 0
        assert "dispatched_hash" not in saved

    def test_untrusted_serial_result_is_fatal_and_writes_no_state(self, tmp_path: Path) -> None:
        prev = self._prev(failing_tests=[], dispatched_nodes=[])
        pre_existing_bytes = json.dumps(prev)
        nrt.LAST_RUN_FILE = tmp_path / "last_run.json"
        rc, _, mock_dispatch, mock_send = self._run_main(
            tmp_path, prev, ["a::t1"], None, serial_trusted=False
        )
        assert rc == 1
        mock_dispatch.assert_not_called()
        # The pre-existing state file is untouched.
        assert nrt.LAST_RUN_FILE.read_text() == pre_existing_bytes
        mock_send.assert_called_once()
        assert "did not happen" in mock_send.call_args.args[0]

    def test_collection_mismatch_reseeds_with_no_per_node_dispatch(self, tmp_path: Path) -> None:
        prev = {"collection": ["tests/unit/"], "failing_tests": [], "dispatched_nodes": []}
        confirmed = ["a::t1", "b::t2", "c::t3"]
        rc, saved, mock_dispatch, _ = self._run_main(tmp_path, prev, confirmed, "umbrella-id")
        assert rc == 0
        assert saved["dispatched_nodes"] == sorted(confirmed)
        assert saved["collection"] == nrt.COLLECTION_PATHS
        # The umbrella dispatch is the only call; no per-node dispatch fires.
        # call_count, not just call_args: the latter is the LAST call, so a
        # stray per-node dispatch before it would go unnoticed.
        assert mock_dispatch.call_count == 1
        assert mock_dispatch.call_args.kwargs.get("slug_suffix") == "baseline"

    def test_failed_seed_dispatch_writes_no_baseline(self, tmp_path: Path) -> None:
        """A seed whose umbrella dispatch failed must not record a baseline.

        Recording it would mark every absorbed node as filed while no umbrella
        issue exists, so compute_dispatch_set() would suppress the entire
        night-one population forever -- behind a Telegram message that reads
        like success. Refusing to save state means the next run re-seeds and
        retries, matching _fatal()'s invariant that no untrusted run reaches
        save_last_run().
        """
        prev = {"collection": ["tests/unit/"], "failing_tests": [], "dispatched_nodes": []}
        pre_existing_bytes = json.dumps(prev)
        nrt.LAST_RUN_FILE = tmp_path / "last_run.json"
        confirmed = ["a::t1", "b::t2", "c::t3"]
        rc, _, _, mock_send = self._run_main(tmp_path, prev, confirmed, None)

        assert rc == 1
        # The pre-existing state file is byte-identical -- no baseline written.
        assert nrt.LAST_RUN_FILE.read_text() == pre_existing_bytes
        # And the operator is told, rather than seeing a success-shaped message.
        assert "seed triage dispatch failed" in mock_send.call_args.args[0]

    def test_seeded_nodes_are_carried_forward_across_runs(self, tmp_path: Path) -> None:
        """The seed's umbrella coverage must outlive the night it was written.

        Without carry-forward the set is gone by night two, leaving
        compute_dispatch_set() blind to it exactly when the first flap lands.
        """
        prev = self._prev(failing_tests=[], dispatched_nodes=[], seeded_nodes=["a::t1"])
        rc, saved, _, _ = self._run_main(tmp_path, prev, [], "session")
        assert rc == 0
        assert saved["seeded_nodes"] == ["a::t1"]

    def test_flapping_seeded_node_is_not_refiled(self, tmp_path: Path) -> None:
        """Blocker regression: a seeded node that passes then fails again.

        It has no per-node issue (the seed filed one umbrella under a different
        title), so dispatching it would open a duplicate. It must stay
        suppressed while still producing its Telegram alert -- suppressing the
        auto-filing is not the same as going silent.
        """
        node = "tests/unit/test_sdlc_review_finalize.py::test_anti_criterion"
        # Night 2 dropped it from dispatched_nodes when it passed; the seed set
        # is what remains.
        prev = self._prev(failing_tests=[], dispatched_nodes=[], seeded_nodes=[node])
        rc, saved, mock_dispatch, mock_send = self._run_main(tmp_path, prev, [node], None)

        assert rc == 0
        mock_dispatch.assert_called_once_with([], dry_run=False)
        assert saved["seeded_nodes"] == [node]
        # The regression is still announced even though no issue is filed.
        assert "newly-confirmed failure" in mock_send.call_args.args[0]

    def test_unseeded_node_that_regresses_is_still_dispatchable(self, tmp_path: Path) -> None:
        """The counterpart the seed suppression must not break.

        A node with its own per-node issue, fixed and then genuinely
        regressed, stays dispatchable -- the behaviour
        test_drops_a_node_that_stopped_failing legitimately covers. Both must
        hold at once.
        """
        node = "tests/unit/test_b.py::test_real"
        prev = self._prev(failing_tests=[], dispatched_nodes=[], seeded_nodes=[])
        rc, saved, mock_dispatch, _ = self._run_main(tmp_path, prev, [node], "new-session")
        assert rc == 0
        mock_dispatch.assert_called_once_with([node], dry_run=False)
        assert saved["dispatched_nodes"] == [node]

    def test_collection_mismatch_regression_seed_survives_successful_dispatch(
        self, tmp_path: Path
    ) -> None:
        """The blocker-1 regression test: a *successful* umbrella dispatch must
        not wipe the seed. Reusing main()'s per-node reassignment on this
        branch sets dispatched_nodes to [] (dispatch_nodes, which is empty on
        the seed path) -- this must not happen.
        """
        prev = {"collection": ["tests/unit/"], "failing_tests": [], "dispatched_nodes": []}
        confirmed = ["a::t1", "b::t2", "c::t3"]
        rc, saved, mock_dispatch, _ = self._run_main(
            tmp_path, prev, confirmed, "umbrella-session-id"
        )
        assert rc == 0
        assert saved["dispatched_nodes"] == sorted(confirmed)
        assert saved["dispatched_session_id"] == "umbrella-session-id"

    def test_head_commit_persisted(self, tmp_path: Path) -> None:
        prev = self._prev()
        rc, saved, _, _ = self._run_main(tmp_path, prev, [], None)
        assert rc == 0
        assert saved["head_commit"] == "deadbeef"

    def test_integrity_trip_writes_no_state(self, tmp_path: Path) -> None:
        prev = self._prev(failing_tests=[], dispatched_nodes=[])
        pre_existing_bytes = json.dumps(prev)
        nrt.LAST_RUN_FILE = tmp_path / "last_run.json"
        nrt.LAST_RUN_FILE.write_text(pre_existing_bytes)
        nrt.LOG_FILE = tmp_path / "test.log"
        nrt.LOCK_FILE = tmp_path / "nightly_tests.lock"

        with (
            patch("sys.argv", ["nightly_regression_tests.py", "--dry-run"]),
            patch.object(nrt, "load_env_or_die", return_value=(42, None)),
            patch.object(nrt, "run_tests", return_value=(None, None, 0)),
            patch.object(nrt, "reconfirm_serial") as mock_reconfirm,
            patch.object(nrt, "send_telegram") as mock_send,
        ):
            rc = nrt.main()
        assert rc == 1
        mock_reconfirm.assert_not_called()
        mock_send.assert_called_once()
        assert nrt.LAST_RUN_FILE.read_text() == pre_existing_bytes


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


class TestLoadEnvOrDie:
    """Guard the #2327 fix: the entrypoint loads .env itself and returns a
    refusal reason on a silent-empty environment (the actual defect when
    /bin/bash EPERM'd on the TCC-protected Desktop-folder symlink) instead of
    raising SystemExit -- both refusal paths must route through _fatal()."""

    def test_loads_keys_into_environ_and_returns_count(self, monkeypatch) -> None:
        monkeypatch.setattr(nrt, "MIN_ENV_KEYS", 2)
        monkeypatch.delenv("NIGHTLY_TEST_KEY_A", raising=False)
        monkeypatch.delenv("NIGHTLY_TEST_KEY_B", raising=False)
        with (
            patch(
                "dotenv.dotenv_values",
                return_value={"NIGHTLY_TEST_KEY_A": "1", "NIGHTLY_TEST_KEY_B": "2"},
            ),
            patch.object(nrt, "log"),
        ):
            count, reason = nrt.load_env_or_die()
        assert count == 2
        assert reason is None
        assert nrt.os.environ["NIGHTLY_TEST_KEY_A"] == "1"
        assert nrt.os.environ["NIGHTLY_TEST_KEY_B"] == "2"

    def test_does_not_clobber_already_set_var(self, monkeypatch) -> None:
        monkeypatch.setattr(nrt, "MIN_ENV_KEYS", 1)
        monkeypatch.setenv("NIGHTLY_TEST_PRESET", "preset-wins")
        with (
            patch("dotenv.dotenv_values", return_value={"NIGHTLY_TEST_PRESET": "file-value"}),
            patch.object(nrt, "log"),
        ):
            nrt.load_env_or_die()
        assert nrt.os.environ["NIGHTLY_TEST_PRESET"] == "preset-wins"

    def test_unreadable_env_file_returns_reason_not_raise(self) -> None:
        """The exact TCC EPERM the fix exists to surface -- a refusal reason,
        never a raised SystemExit (so it can route through _fatal())."""
        with patch("dotenv.dotenv_values", side_effect=OSError("Operation not permitted")):
            count, reason = nrt.load_env_or_die()
        assert reason is not None
        assert "could not read" in reason

    def test_short_load_below_floor_returns_reason(self, monkeypatch) -> None:
        monkeypatch.setattr(nrt, "MIN_ENV_KEYS", 10)
        with patch("dotenv.dotenv_values", return_value={"ONLY_ONE": "x"}):
            count, reason = nrt.load_env_or_die()
        assert count == 1
        assert reason is not None
        assert "only 1 env vars" in reason

    def test_none_values_are_skipped_not_counted(self, monkeypatch) -> None:
        monkeypatch.setattr(nrt, "MIN_ENV_KEYS", 1)
        monkeypatch.delenv("NIGHTLY_REAL", raising=False)
        with (
            patch(
                "dotenv.dotenv_values",
                return_value={"NIGHTLY_REAL": "v", "NIGHTLY_BLANK": None},
            ),
            patch.object(nrt, "log"),
        ):
            count, reason = nrt.load_env_or_die()
        assert count == 1
        assert reason is None
        assert "NIGHTLY_BLANK" not in nrt.os.environ


class TestFatalPathIntegration:
    """Every pre-alert exit from main() routes through _fatal() (issue #2823)."""

    def _base_patches(self, tmp_path: Path):
        nrt.LOG_FILE = tmp_path / "test.log"
        nrt.LOCK_FILE = tmp_path / "nightly_tests.lock"
        nrt.LAST_RUN_FILE = tmp_path / "last_run.json"
        nrt.LAST_RUN_FILE.write_text(json.dumps({"collection": nrt.COLLECTION_PATHS}))

    def test_env_refusal_is_fatal(self, tmp_path: Path) -> None:
        self._base_patches(tmp_path)
        pre_existing = nrt.LAST_RUN_FILE.read_text()
        with (
            patch("sys.argv", ["nightly_regression_tests.py"]),
            patch.object(nrt, "load_env_or_die", return_value=(0, "vault unreadable")),
            patch.object(nrt, "run_tests") as mock_run_tests,
            patch.object(nrt, "send_telegram") as mock_send,
        ):
            rc = nrt.main()
        assert rc == 1
        mock_run_tests.assert_not_called()
        mock_send.assert_called_once()
        assert "vault unreadable" in mock_send.call_args.args[0]
        assert nrt.LAST_RUN_FILE.read_text() == pre_existing

    def test_integrity_trip_is_fatal(self, tmp_path: Path) -> None:
        self._base_patches(tmp_path)
        pre_existing = nrt.LAST_RUN_FILE.read_text()
        with (
            patch("sys.argv", ["nightly_regression_tests.py"]),
            patch.object(nrt, "load_env_or_die", return_value=(42, None)),
            patch.object(nrt, "run_tests", return_value=(None, None, 0)),
            patch.object(nrt, "send_telegram") as mock_send,
        ):
            rc = nrt.main()
        assert rc == 1
        mock_send.assert_called_once()
        assert nrt.LAST_RUN_FILE.read_text() == pre_existing

    def test_run_tests_timeout_is_fatal(self, tmp_path: Path) -> None:
        """main() catches the propagated TimeoutExpired explicitly and routes
        it through _fatal(), rather than a bare uncaught exception."""
        self._base_patches(tmp_path)
        pre_existing = nrt.LAST_RUN_FILE.read_text()
        with (
            patch("sys.argv", ["nightly_regression_tests.py"]),
            patch.object(nrt, "load_env_or_die", return_value=(42, None)),
            patch.object(
                nrt, "run_tests", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)
            ),
            patch.object(nrt, "send_telegram") as mock_send,
        ):
            rc = nrt.main()
        assert rc == 1
        mock_send.assert_called_once()
        assert "timed out" in mock_send.call_args.args[0]
        assert nrt.LAST_RUN_FILE.read_text() == pre_existing
