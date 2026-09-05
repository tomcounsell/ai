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
        # MAX_SETUP_ERRORS is absolute: 50 errors == boundary, not above.
        report = self._healthy(total=1000, error=50)
        reason, warnings = nrt.validate_run_integrity(report, 1, {})
        assert reason is None

    def test_error_boundary_above_threshold_trips(self) -> None:
        report = self._healthy(total=1000, error=51)
        reason, warnings = nrt.validate_run_integrity(report, 1, {})
        assert reason is not None

    def test_setup_error_ceiling_is_absolute_not_relative(self) -> None:
        """The #3131 regression: the ceiling must bite at the widened scale.

        It was `max(50, 0.02 * total)`, where the relative term RAISES the bar.
        At the real measured shape of 2026-09-03 — 278 setup errors in a 16255
        item collection — that ceiling was 325, so a single poisoned xdist
        worker read as a legitimately red suite and 26 issues were filed off
        one defect.
        """
        report = self._healthy(total=16255, error=278, failed=24)
        reason, _ = nrt.validate_run_integrity(report, 1, {})
        assert reason is not None
        assert "errored at setup" in reason

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


class TestNothingNotifies:
    """The tracker is the detector's only output surface (issue #3134).

    The owner's requirement was "i don't want alerts either" — so this is not a
    style preference about where a message goes, it is a contract that no code
    path in this script notifies anything. A regression that reintroduces a
    Telegram send (or any other outbound channel) must fail here rather than be
    discovered by a 03:00 page.
    """

    def test_module_has_no_telegram_surface_at_all(self) -> None:
        for attr in ("send_telegram", "TELEGRAM_CHAT", "TELEGRAM_BIN"):
            assert not hasattr(nrt, attr), f"{attr} is back — the detector must not notify"

    def test_source_spawns_no_notifier_binary(self) -> None:
        """Belt and braces: the send could come back under any name.

        Docstrings are allowed to mention the removed sender (they explain WHY
        it is gone), so this looks for the executable shapes: the binary name as
        a string literal, and a definition of a sender.
        """
        source = Path(nrt.__file__).read_text()
        for token in ('"valor-telegram"', "'valor-telegram'", "def send_telegram"):
            assert token not in source, f"{token} appears in the nightly detector source"


class TestFatal:
    """_fatal() records a run-level failure in the log and nowhere else."""

    def test_logs_and_returns_1(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        rc = nrt._fatal("something broke")
        assert rc == 1
        assert "something broke" in tmp_path.joinpath("test.log").read_text()

    def test_runs_no_subprocess(self, tmp_path: Path) -> None:
        """A fatal path must not shell out — there is nothing left for it to call."""
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch("subprocess.run") as mock_run:
            nrt._fatal("boom")
        mock_run.assert_not_called()


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
            ):
                result = nrt.main()
            mock_run_tests.assert_not_called()
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


def _errored(nodeid: str, worker: str, message: str) -> dict:
    """A pytest-json-report entry shaped like a real setup-phase error."""
    return {
        "nodeid": nodeid,
        "outcome": "error",
        "setup": {
            "outcome": "failed",
            "crash": {"path": "tests/conftest.py", "lineno": 851, "message": message},
        },
        "teardown": {
            "outcome": "passed",
            "longrepr": f"[{worker}] darwin -- Python 3.14.3 /Users/x/.venv/bin/python3",
        },
    }


class TestGroupSetupErrorCascades:
    """One poisoned xdist worker is ONE defect, not N findings (#3131)."""

    MSG = (
        "RuntimeError: Test Redis client is not on the server the db-claim registry "
        "is keyed to: client=localhost:6379 registry=127.0.0.1:6379."
    )

    def test_identical_setup_errors_on_one_worker_collapse_to_one_cascade(self) -> None:
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(8)]
        report = {"tests": [_errored(n, "gw3", self.MSG) for n in nodes]}
        cascades, singles = nrt.group_setup_error_cascades(report, nodes)
        assert singles == []
        assert len(cascades) == 1
        assert cascades[0]["nodes"] == sorted(nodes)
        assert cascades[0]["workers"] == ["gw3"]

    def test_identically_poisoned_workers_file_one_umbrella(self) -> None:
        """Filing merges by message so four workers do not race for one title."""
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(12)]
        report = {"tests": [_errored(n, f"gw{i % 4}", self.MSG) for i, n in enumerate(nodes)]}
        cascades, singles = nrt.group_setup_error_cascades(report, nodes)
        assert singles == []
        assert len(cascades) == 1
        assert cascades[0]["workers"] == ["gw0", "gw1", "gw2", "gw3"]

    def test_below_threshold_stays_per_node(self) -> None:
        nodes = ["tests/unit/test_m.py::test_a", "tests/unit/test_m.py::test_b"]
        report = {"tests": [_errored(n, "gw1", self.MSG) for n in nodes]}
        cascades, singles = nrt.group_setup_error_cascades(report, nodes)
        assert cascades == []
        assert singles == nodes

    def test_test_body_failures_are_never_collapsed(self) -> None:
        """A node that failed in its own test body is its own finding."""
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(8)]
        report = {
            "tests": [
                {
                    "nodeid": n,
                    "outcome": "failed",
                    "setup": {"outcome": "passed"},
                    "call": {"outcome": "failed", "longrepr": "[gw3] AssertionError: nope"},
                }
                for n in nodes
            ]
        }
        cascades, singles = nrt.group_setup_error_cascades(report, nodes)
        assert cascades == []
        assert singles == nodes

    def test_distinct_messages_do_not_merge(self) -> None:
        a = [f"tests/unit/test_a.py::test_{i}" for i in range(4)]
        b = [f"tests/unit/test_b.py::test_{i}" for i in range(4)]
        report = {
            "tests": [_errored(n, "gw0", self.MSG) for n in a]
            + [_errored(n, "gw1", "OSError: address already in use") for n in b]
        }
        cascades, singles = nrt.group_setup_error_cascades(report, a + b)
        assert singles == []
        assert {len(c["nodes"]) for c in cascades} == {4}
        assert len({c["title"] for c in cascades}) == 2

    def test_title_is_stable_across_worker_and_size(self) -> None:
        """A title keyed on anything that shifts nightly cannot be deduped against."""
        small = [f"tests/unit/test_m.py::test_{i}" for i in range(3)]
        big = [f"tests/unit/test_m.py::test_{i}" for i in range(30)]
        one = nrt.group_setup_error_cascades(
            {"tests": [_errored(n, "gw0", self.MSG) for n in small]}, small
        )[0][0]
        two = nrt.group_setup_error_cascades(
            {"tests": [_errored(n, "gw5", self.MSG) for n in big]}, big
        )[0][0]
        assert one["title"] == two["title"]

    def test_prompt_orders_one_issue_with_a_collapsed_node_list(self) -> None:
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(5)]
        report = {"tests": [_errored(n, "gw2", self.MSG) for n in nodes]}
        cascade = nrt.group_setup_error_cascades(report, nodes)[0][0]
        prompt = nrt._build_cascade_prompt(cascade)
        assert cascade["title"] in prompt
        assert "ONE defect" in prompt
        assert "<details>" in prompt
        assert "Do NOT open per-node issues" in prompt
        for n in nodes:
            assert n in prompt


class TestResolveIntKnob:
    """Noise-control knobs must resolve at CALL time, not at import.

    `.env` only reaches os.environ through load_env_or_die() inside main(), so
    an import-time read would freeze the in-code default and make the vault
    setting inert on the one surface that matters.
    """

    @pytest.fixture(autouse=True)
    def _quiet_log(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")

    def test_unset_uses_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NIGHTLY_MAX_SETUP_ERRORS", raising=False)
        assert nrt.resolve_int_knob("NIGHTLY_MAX_SETUP_ERRORS", 50) == 50

    def test_a_value_set_after_import_is_honored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIGHTLY_MAX_SETUP_ERRORS", "7")
        assert nrt.resolve_int_knob("NIGHTLY_MAX_SETUP_ERRORS", 50) == 7

    def test_malformed_degrades_to_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A bad knob must never take down the nightly."""
        monkeypatch.setenv("NIGHTLY_MAX_SETUP_ERRORS", "fifty")
        assert nrt.resolve_int_knob("NIGHTLY_MAX_SETUP_ERRORS", 50) == 50

    def test_the_ceiling_reads_the_knob(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(nrt, "MIN_EXPECTED_COLLECTED", 0)
        monkeypatch.setenv("NIGHTLY_MAX_SETUP_ERRORS", "5")
        report = {"summary": {"total": 1000, "error": 6, "failed": 0}, "tests": []}
        reason, _ = nrt.validate_run_integrity(report, 1, {})
        assert reason is not None
        assert "ceiling 5" in reason


class TestPreFileDedup:
    """The only dedup that spans machines: read the open issue set first (#3131).

    Since #3134 the partition also hands back the issue *number*, because the
    default posture is to comment on the open issue rather than stay silent.
    """

    def test_already_open_titles_are_paired_with_their_issue_number(self) -> None:
        nodes = ["tests/unit/test_a.py::test_1", "tests/unit/test_b.py::test_2"]
        open_issues = {"Nightly regression: tests/unit/test_a.py::test_1": 77}
        to_dispatch, already = nrt.partition_already_open(nodes, open_issues)
        assert to_dispatch == ["tests/unit/test_b.py::test_2"]
        assert already == [("tests/unit/test_a.py::test_1", 77)]

    def test_unreadable_open_set_fails_open(self) -> None:
        """Suppressing everything on a `gh` hiccup would silence a real regression."""
        nodes = ["tests/unit/test_a.py::test_1"]
        assert nrt.partition_already_open(nodes, None) == (nodes, [])

    def test_open_issues_uses_the_rest_list_not_the_lagging_search(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        seen: dict[str, list[str]] = {}

        class FakeResult:
            returncode = 0
            stdout = '[{"number": 5, "title": "Nightly regression: a::t1"}]'
            stderr = ""

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            return FakeResult()

        monkeypatch.setattr(nrt.subprocess, "run", fake_run)
        assert nrt.open_issues() == {"Nightly regression: a::t1": 5}
        assert seen["argv"][:4] == ["gh", "issue", "list", "--state"]
        assert "--search" not in seen["argv"]
        assert "number" in ",".join(seen["argv"]), "the number is what makes commenting possible"

    @pytest.mark.parametrize("failure", ["rc", "raise", "garbage"])
    def test_open_issues_returns_none_on_any_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
    ) -> None:
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")

        class FakeResult:
            returncode = 1 if failure == "rc" else 0
            stdout = "not json" if failure == "garbage" else "[]"
            stderr = "boom"

        def fake_run(argv, **kwargs):
            if failure == "raise":
                raise OSError("gh missing")
            return FakeResult()

        monkeypatch.setattr(nrt.subprocess, "run", fake_run)
        assert nrt.open_issues() is None


class TestRecurrenceComments:
    """A recurrence that is not written down has not been reported (#3134)."""

    MSG = "RuntimeError: redis client is not on the claimed server"

    def _cascade(self, count: int = 6):
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(count)]
        report = {"tests": [_errored(n, "gw2", self.MSG) for n in nodes]}
        return nrt.group_setup_error_cascades(report, nodes)[0][0]

    def test_cascade_comment_carries_run_head_and_blast_radius(self) -> None:
        body = nrt.cascade_recurrence_comment(
            self._cascade(), run_at="2026-09-04T03:00:00Z", head_commit="cafe1234"
        )
        assert "2026-09-04T03:00:00Z" in body
        assert "cafe1234" in body
        assert "6 node(s)" in body
        assert "gw2" in body
        assert "tests/unit/test_m.py::test_0" in body

    def test_cascade_comment_truncates_a_whole_suite_poisoning(self) -> None:
        """GitHub rejects a body over 65536 chars, and a rejected comment reports nothing.

        The motivating incident was 278 nodes (~31KB). A worker that poisons a
        larger schedule would silently post nothing at all without this cap.
        """
        body = nrt.cascade_recurrence_comment(
            self._cascade(nrt.MAX_COMMENT_NODES_LISTED + 40),
            run_at="2026-09-04T03:00:00Z",
            head_commit="cafe1234",
        )
        assert "...and 40 more" in body
        assert len(body) < 65536
        # The counts survive truncation -- they are the load-bearing part.
        assert f"{nrt.MAX_COMMENT_NODES_LISTED + 40} node(s)" in body

    def test_node_comment_carries_run_head_and_node(self) -> None:
        body = nrt.node_recurrence_comment(
            "tests/unit/test_a.py::test_1", run_at="RUN", head_commit="HEAD"
        )
        assert "RUN" in body and "HEAD" in body
        assert "tests/unit/test_a.py::test_1" in body

    def test_missing_head_commit_is_stated_not_omitted(self) -> None:
        assert "unknown" in nrt.node_recurrence_comment("a::t1", run_at="RUN", head_commit=None)

    def test_comment_passes_the_body_on_stdin(self, monkeypatch, tmp_path: Path) -> None:
        """A 278-node body has no business being an argv value."""
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        seen: dict = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            seen["input"] = kwargs.get("input")
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(nrt.subprocess, "run", fake_run)
        assert nrt.comment_on_issue(42, "the body") is True
        assert seen["argv"] == ["gh", "issue", "comment", "42", "--body-file", "-"]
        assert seen["input"] == "the body"

    @pytest.mark.parametrize("failure", ["rc", "raise"])
    def test_failed_comment_reports_false(self, monkeypatch, tmp_path: Path, failure) -> None:
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")

        def fake_run(argv, **kwargs):
            if failure == "raise":
                raise OSError("gh missing")
            return subprocess.CompletedProcess(argv, 1, "", "no such issue")

        monkeypatch.setattr(nrt.subprocess, "run", fake_run)
        assert nrt.comment_on_issue(42, "body") is False

    def test_dry_run_posts_nothing(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        monkeypatch.setattr(
            nrt.subprocess, "run", lambda *a, **k: pytest.fail("dry run shelled out")
        )
        assert nrt.comment_on_issue(42, "body", dry_run=True) is True


class TestResolveCascadeIssue:
    """Cascade identity is the normalized signature, not the rendered title."""

    MSG = "RuntimeError: redis client is not on the claimed server"

    def _cascade(self):
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(6)]
        report = {"tests": [_errored(n, "gw2", self.MSG) for n in nodes]}
        return nrt.group_setup_error_cascades(report, nodes)[0][0]

    def test_signature_map_finds_the_issue_after_a_human_retitles_it(self) -> None:
        """The case exact-title matching gets wrong, and the reason for the map.

        A human renaming the issue to something readable must not cause the same
        cascade to be filed a second time the following night.
        """
        cascade = self._cascade()
        open_map = {"Redis pool poisoning on gw2 (renamed by hand)": 4242}
        assert nrt.resolve_cascade_issue(cascade, open_map, {cascade["message"]: 4242}) == 4242

    def test_title_match_bootstraps_the_map(self) -> None:
        """This script never opens the issue, so night one cannot know its number."""
        cascade = self._cascade()
        assert nrt.resolve_cascade_issue(cascade, {cascade["title"]: 99}, {}) == 99

    def test_recorded_number_that_is_no_longer_open_does_not_resolve(self) -> None:
        cascade = self._cascade()
        assert nrt.resolve_cascade_issue(cascade, {}, {cascade["message"]: 4242}) is None

    def test_unreadable_open_list_fails_open(self) -> None:
        cascade = self._cascade()
        assert nrt.resolve_cascade_issue(cascade, None, {cascade["message"]: 4242}) is None


class TestCarryCascadeIssues:
    MSG = "RuntimeError: redis client is not on the claimed server"

    def test_pending_entry_is_upgraded_once_the_title_appears(self) -> None:
        title = nrt.cascade_title(self.MSG)
        assert nrt.carry_cascade_issues({self.MSG: None}, {title: 4242}) == {self.MSG: 4242}

    def test_unresolvable_pending_entry_is_dropped(self) -> None:
        """No issue exists, so a recurrence deserves a fresh filing, not silence."""
        assert nrt.carry_cascade_issues({self.MSG: None}, {}) == {}

    def test_closed_issue_drops_out(self) -> None:
        assert nrt.carry_cascade_issues({self.MSG: 4242}, {"unrelated": 7}) == {}

    def test_unreadable_open_list_keeps_the_map_verbatim(self) -> None:
        """`None` is "could not tell", never evidence that anything closed."""
        prev = {self.MSG: 4242, "other": None}
        assert nrt.carry_cascade_issues(prev, None) == prev


class TestDispatchFindings:
    """Comment-over-create is the default posture, not a fallback (#3134)."""

    MSG = "RuntimeError: redis client is not on the claimed server"

    def _report(self, nodes):
        return {"tests": [_errored(n, "gw2", self.MSG) for n in nodes]}

    def _body_failures(self, nodes):
        """Test-body failures never collapse, so each is its own finding."""
        return {
            "tests": [
                {
                    "nodeid": n,
                    "outcome": "failed",
                    "setup": {"outcome": "passed"},
                    "call": {"outcome": "failed", "longrepr": f"[gw1] AssertionError: {n}"},
                }
                for n in nodes
            ]
        }

    def _dispatch(self, monkeypatch, tmp_path, *, nodes, open_map, prev=None, report=None, **kw):
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        monkeypatch.setattr(nrt, "open_issues", lambda: open_map)
        monkeypatch.setattr(nrt, "closed_issue_dispositions", lambda: {})
        commented: list[int] = []
        monkeypatch.setattr(
            nrt, "comment_on_issue", lambda n, body, **kw: commented.append(n) or True
        )
        filed: list[list[str]] = []

        def fake_dispatch(ns, **kw):
            if not ns:
                return None
            filed.append(list(ns))
            return "sess-1"

        monkeypatch.setattr(nrt, "maybe_dispatch_triage_session", fake_dispatch)
        outcome = nrt.dispatch_findings(
            report if report is not None else self._report(nodes),
            nodes,
            prev or {},
            run_at="2026-09-04T03:00:00Z",
            head_commit="cafe1234",
            **kw,
        )
        return outcome, commented, filed

    def test_night_one_files_one_issue_and_records_the_signature_as_pending(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(9)]
        outcome, commented, filed = self._dispatch(monkeypatch, tmp_path, nodes=nodes, open_map={})
        assert (outcome.issues_filed, outcome.comments_posted) == (1, 0)
        assert commented == []
        assert len(filed) == 1
        assert outcome.recorded == sorted(nodes)
        assert outcome.cascade_issues == {self.MSG: None}

    def test_night_two_comments_instead_of_filing_a_second_issue(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """The whole point: a recurring cascade accretes a comment, never a twin."""
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(9)]
        outcome, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=nodes,
            open_map={nrt.cascade_title(self.MSG): 4242},
            prev={"cascade_issues": {self.MSG: None}},
        )
        assert (outcome.issues_filed, outcome.comments_posted) == (0, 1)
        assert commented == [4242]
        assert filed == []
        assert outcome.recorded == sorted(nodes)
        assert outcome.cascade_issues == {self.MSG: 4242}

    def test_a_comment_that_failed_to_post_leaves_the_finding_unrecorded(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Recording it as handled would lose the recurrence permanently."""
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(9)]
        monkeypatch.setattr(nrt, "open_issues", lambda: {nrt.cascade_title(self.MSG): 4242})
        monkeypatch.setattr(nrt, "closed_issue_dispositions", lambda: {})
        monkeypatch.setattr(nrt, "comment_on_issue", lambda *a, **k: False)
        monkeypatch.setattr(nrt, "maybe_dispatch_triage_session", lambda *a, **k: None)
        outcome = nrt.dispatch_findings(
            self._report(nodes), nodes, {}, run_at="RUN", head_commit="HEAD"
        )
        assert outcome.recorded == []
        assert outcome.comments_posted == 0

    def test_per_node_recurrence_is_commented_not_suppressed(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """#3131 dropped the duplicate silently, which lost the recurrence signal."""
        nodes = ["tests/unit/test_a.py::test_1", "tests/unit/test_b.py::test_2"]
        outcome, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=nodes,
            report=self._body_failures(nodes),
            open_map={"Nightly regression: tests/unit/test_a.py::test_1": 11},
        )
        assert commented == [11]
        assert filed == [["tests/unit/test_b.py::test_2"]]
        assert sorted(outcome.recorded) == sorted(nodes)

    def test_comments_do_not_spend_the_issue_budget(self, monkeypatch, tmp_path: Path) -> None:
        """The cap bounds NEW tracker surface, and a comment creates none."""
        monkeypatch.setenv("NIGHTLY_MAX_ISSUES_PER_RUN", "1")
        nodes = [f"tests/unit/test_{c}.py::test_1" for c in "abcd"]
        outcome, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=nodes,
            report=self._body_failures(nodes),
            open_map={f"Nightly regression: {n}": i for i, n in enumerate(nodes[:3], start=1)},
        )
        assert sorted(commented) == [1, 2, 3]
        assert filed == [["tests/unit/test_d.py::test_1"]]
        assert sorted(outcome.recorded) == sorted(nodes)

    def test_cascades_only_suppresses_per_node_filing(self, monkeypatch, tmp_path: Path) -> None:
        cascade_nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(9)]
        report = self._report(cascade_nodes)
        loner = "tests/unit/test_z.py::test_solo"
        report["tests"].append(
            {
                "nodeid": loner,
                "outcome": "failed",
                "setup": {"outcome": "passed"},
                "call": {"outcome": "failed", "longrepr": "[gw1] AssertionError: nope"},
            }
        )
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        monkeypatch.setattr(nrt, "open_issues", lambda: {})
        monkeypatch.setattr(nrt, "closed_issue_dispositions", lambda: {})
        filed: list[list[str]] = []
        monkeypatch.setattr(
            nrt,
            "maybe_dispatch_triage_session",
            lambda ns, **kw: (filed.append(list(ns)), "sess-1")[1] if ns else None,
        )
        outcome = nrt.dispatch_findings(
            report,
            [*cascade_nodes, loner],
            {},
            run_at="RUN",
            head_commit="HEAD",
            cascades_only=True,
        )
        assert outcome.issues_filed == 1
        assert loner not in outcome.recorded
        assert len(filed) == 1 and filed[0][0].startswith("cascade:")

    def test_a_clean_night_never_shells_out_to_gh(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        monkeypatch.setattr(nrt, "open_issues", lambda: pytest.fail("read gh on a clean night"))
        monkeypatch.setattr(
            nrt, "closed_issue_dispositions", lambda: pytest.fail("read gh closed set")
        )
        monkeypatch.setattr(nrt, "maybe_dispatch_triage_session", lambda *a, **k: None)
        outcome = nrt.dispatch_findings({"tests": []}, [], {}, run_at="RUN", head_commit="HEAD")
        assert outcome.recorded == []


class TestHandleIntegrityTrip:
    """A storm must not become invisible now that nothing alerts (#3134)."""

    MSG = "RuntimeError: redis client is not on the claimed server"

    def _storm(self, count=20):
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(count)]
        return nodes, {"tests": [_errored(n, "gw2", self.MSG) for n in nodes]}

    def _prev(self):
        return {
            "collection": nrt.COLLECTION_PATHS,
            "total": 16000,
            "failed": 0,
            "failing_tests": [],
            "dispatched_nodes": [],
        }

    def test_trip_still_files_the_cascade(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        monkeypatch.setattr(nrt, "LAST_RUN_FILE", tmp_path / "last_run.json")
        monkeypatch.setattr(nrt, "open_issues", lambda: {})
        monkeypatch.setattr(nrt, "closed_issue_dispositions", lambda: {})
        monkeypatch.setattr(nrt, "_get_head_commit", lambda: "cafe1234")
        filed: list[list[str]] = []
        monkeypatch.setattr(
            nrt,
            "maybe_dispatch_triage_session",
            lambda ns, **kw: (filed.append(list(ns)), "sess-1")[1] if ns else None,
        )
        nodes, report = self._storm()
        prev = self._prev()

        rc = nrt._handle_integrity_trip("infrastructure, not a red suite", report, None, prev)

        assert rc == 1
        assert len(filed) == 1, "the storm was filed exactly once"
        saved = json.loads(nrt.LAST_RUN_FILE.read_text())
        assert saved["dispatched_nodes"] == sorted(nodes)
        assert saved["cascade_issues"] == {self.MSG: None}

    def test_trip_never_overwrites_the_baseline(self, monkeypatch, tmp_path: Path) -> None:
        """The guard just declared these totals untrustworthy; they must not land."""
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        monkeypatch.setattr(nrt, "LAST_RUN_FILE", tmp_path / "last_run.json")
        monkeypatch.setattr(nrt, "open_issues", lambda: {})
        monkeypatch.setattr(nrt, "closed_issue_dispositions", lambda: {})
        monkeypatch.setattr(nrt, "_get_head_commit", lambda: "cafe1234")
        monkeypatch.setattr(nrt, "maybe_dispatch_triage_session", lambda ns, **kw: "sess-1")
        _, report = self._storm()
        prev = self._prev()

        nrt._handle_integrity_trip("bad run", report, {"total": 40, "failed": 20}, prev)

        saved = json.loads(nrt.LAST_RUN_FILE.read_text())
        assert saved["total"] == 16000
        assert saved["failed"] == 0
        assert saved["failing_tests"] == []

    def test_trip_with_no_report_writes_nothing(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        monkeypatch.setattr(nrt, "LAST_RUN_FILE", tmp_path / "last_run.json")
        monkeypatch.setattr(nrt, "open_issues", lambda: pytest.fail("read gh with no report"))
        monkeypatch.setattr(
            nrt, "closed_issue_dispositions", lambda: pytest.fail("read gh closed set")
        )
        assert nrt._handle_integrity_trip("no report", None, None, self._prev()) == 1
        assert not nrt.LAST_RUN_FILE.exists()

    def test_already_filed_storm_is_not_filed_again(self, monkeypatch, tmp_path: Path) -> None:
        """Night after night, the same storm is one issue plus one comment each."""
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        monkeypatch.setattr(nrt, "LAST_RUN_FILE", tmp_path / "last_run.json")
        monkeypatch.setattr(nrt, "_get_head_commit", lambda: "cafe1234")
        monkeypatch.setattr(nrt, "open_issues", lambda: {nrt.cascade_title(self.MSG): 4242})
        monkeypatch.setattr(nrt, "closed_issue_dispositions", lambda: {})
        commented: list[int] = []
        monkeypatch.setattr(
            nrt, "comment_on_issue", lambda n, body, **kw: commented.append(n) or True
        )
        monkeypatch.setattr(
            nrt, "maybe_dispatch_triage_session", lambda *a, **k: pytest.fail("filed a twin")
        )
        nodes, report = self._storm()
        prev = self._prev() | {"cascade_issues": {self.MSG: 4242}}

        assert nrt._handle_integrity_trip("bad run", report, None, prev) == 1
        assert commented == [4242]
        assert json.loads(nrt.LAST_RUN_FILE.read_text())["dispatched_nodes"] == sorted(nodes)

    def test_dry_run_trip_writes_no_state(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        monkeypatch.setattr(nrt, "LAST_RUN_FILE", tmp_path / "last_run.json")
        monkeypatch.setattr(nrt, "open_issues", lambda: {})
        monkeypatch.setattr(nrt, "closed_issue_dispositions", lambda: {})
        monkeypatch.setattr(nrt, "_get_head_commit", lambda: "cafe1234")
        monkeypatch.setattr(nrt, "maybe_dispatch_triage_session", lambda ns, **kw: "sess-1")
        _, report = self._storm()
        assert nrt._handle_integrity_trip("bad run", report, None, self._prev(), dry_run=True) == 1
        assert not nrt.LAST_RUN_FILE.exists()


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
            # and --dry-run deliberately writes none. Dispatch is patched
            # below, so the real path is already side-effect free here.
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
            patch.object(
                nrt, "maybe_dispatch_triage_session", return_value=dispatch_return
            ) as mock_dispatch,
            patch.object(nrt, "open_issues", return_value={}),
            patch.object(nrt, "closed_issue_dispositions", return_value={}),
            patch.object(nrt, "run_ttft_gate", return_value=None),
            patch.object(nrt, "_get_head_commit", return_value="deadbeef"),
        ):
            rc = nrt.main()
        return (
            rc,
            json.loads(nrt.LAST_RUN_FILE.read_text()),
            mock_dispatch,
            nrt.LOG_FILE.read_text(),
        )

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
        rc, saved, _, log_text = self._run_main(tmp_path, prev, [node], "new-session-id")
        assert rc == 0
        assert saved["dispatched_nodes"] == [node]
        assert saved["dispatched_session_id"] == "new-session-id"
        # The tracker is the only output surface, so the log is where the shape
        # of the night is recorded (#3134).
        assert "Tracker:" in log_text

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
            patch.object(nrt, "maybe_dispatch_triage_session", return_value="sentinel") as disp,
            patch.object(nrt, "open_issues", return_value={}),
            patch.object(nrt, "closed_issue_dispositions", return_value={}),
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
            patch.object(nrt, "maybe_dispatch_triage_session", return_value="sentinel"),
            patch.object(nrt, "open_issues", return_value={}),
            patch.object(nrt, "closed_issue_dispositions", return_value={}),
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
        rc, _, mock_dispatch, log_text = self._run_main(
            tmp_path, prev, ["a::t1"], None, serial_trusted=False
        )
        assert rc == 1
        mock_dispatch.assert_not_called()
        # The pre-existing state file is untouched.
        assert nrt.LAST_RUN_FILE.read_text() == pre_existing_bytes
        assert "did not happen" in log_text

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
        rc, _, _, log_text = self._run_main(tmp_path, prev, confirmed, None)

        assert rc == 1
        # The pre-existing state file is byte-identical -- no baseline written.
        assert nrt.LAST_RUN_FILE.read_text() == pre_existing_bytes
        # And the reason is recorded, rather than a success-shaped log line.
        assert "seed triage dispatch failed" in log_text

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
        rc, saved, mock_dispatch, log_text = self._run_main(tmp_path, prev, [node], None)

        assert rc == 0
        mock_dispatch.assert_called_once_with([], dry_run=False)
        assert saved["seeded_nodes"] == [node]
        # The regression is still recorded even though no issue is filed.
        assert "newly-confirmed failure" in log_text

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
        ):
            rc = nrt.main()
        assert rc == 1
        mock_reconfirm.assert_not_called()
        assert "FATAL" in nrt.LOG_FILE.read_text()
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
        ):
            rc = nrt.main()
        assert rc == 1
        mock_run_tests.assert_not_called()
        assert "vault unreadable" in nrt.LOG_FILE.read_text()
        assert nrt.LAST_RUN_FILE.read_text() == pre_existing

    def test_integrity_trip_is_fatal(self, tmp_path: Path) -> None:
        self._base_patches(tmp_path)
        pre_existing = nrt.LAST_RUN_FILE.read_text()
        with (
            patch("sys.argv", ["nightly_regression_tests.py"]),
            patch.object(nrt, "load_env_or_die", return_value=(42, None)),
            patch.object(nrt, "run_tests", return_value=(None, None, 0)),
        ):
            rc = nrt.main()
        assert rc == 1
        assert "FATAL" in nrt.LOG_FILE.read_text()
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
        ):
            rc = nrt.main()
        assert rc == 1
        assert "timed out" in nrt.LOG_FILE.read_text()
        assert nrt.LAST_RUN_FILE.read_text() == pre_existing


class TestSpawnPytestCwdSeam:
    """The `cwd` parameter exists for the classifier; both existing callers are
    byte-identical in behavior and still spawn at PROJECT_DIR (issue #2334)."""

    def test_spawn_pytest_cwd_defaults_to_project_dir(self) -> None:
        proc = _fake_popen(returncode=0)
        with patch("subprocess.Popen", return_value=proc) as mock_popen:
            nrt._spawn_pytest(["echo", "hi"], timeout=10)
        assert mock_popen.call_args.kwargs["cwd"] == nrt.PROJECT_DIR

    def test_spawn_pytest_cwd_is_forwarded_when_given(self, tmp_path: Path) -> None:
        proc = _fake_popen(returncode=0)
        with patch("subprocess.Popen", return_value=proc) as mock_popen:
            nrt._spawn_pytest(["echo", "hi"], timeout=10, cwd=tmp_path)
        assert mock_popen.call_args.kwargs["cwd"] == tmp_path

    def test_spawn_pytest_cwd_unchanged_for_run_tests(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        report_path = tmp_path / "report.json"
        nrt.PYTEST_JSON_TMP = str(report_path)
        proc = _fake_popen(returncode=0)

        def _popen(argv, **kwargs):
            report_path.write_text(json.dumps({"summary": {"total": 3, "passed": 3}, "tests": []}))
            return proc

        with patch("subprocess.Popen", side_effect=_popen) as mock_popen:
            nrt.run_tests()
        assert mock_popen.call_args.kwargs["cwd"] == nrt.PROJECT_DIR

    def test_spawn_pytest_cwd_unchanged_for_reconfirm_serial(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        report_path = tmp_path / "serial.json"
        nrt.PYTEST_SERIAL_JSON_TMP = str(report_path)
        proc = _fake_popen(returncode=0)

        def _popen(argv, **kwargs):
            report_path.write_text(
                json.dumps({"tests": [{"nodeid": "a::t1", "outcome": "failed"}]})
            )
            return proc

        with patch("subprocess.Popen", side_effect=_popen) as mock_popen:
            nrt.reconfirm_serial(["a::t1"])
        assert mock_popen.call_args.kwargs["cwd"] == nrt.PROJECT_DIR


class TestPersistedStateKeyInvariance:
    """The shadow tier persists NO new key in data/nightly_tests_last_run.json.

    `classify_attempts` was dropped as structurally unreachable at this scope;
    this test is what keeps it (or any sibling map) from creeping back in.
    """

    def _run(self, tmp_path: Path, mode: str) -> dict:
        run_dir = tmp_path / mode
        run_dir.mkdir()
        confirmed = ["tests/unit/test_a.py::test_one"]
        nrt.LOG_FILE = run_dir / "nightly.log"
        nrt.LOCK_FILE = run_dir / "nightly.lock"
        nrt.LAST_RUN_FILE = run_dir / "last_run.json"
        serial_report = run_dir / "serial.json"
        serial_report.write_text(json.dumps({"tests": []}))
        nrt.PYTEST_SERIAL_JSON_TMP = str(serial_report)
        nrt.LAST_RUN_FILE.write_text(
            json.dumps(
                {
                    "collection": nrt.COLLECTION_PATHS,
                    "failing_tests": [],
                    "dispatched_nodes": [],
                    "head_commit": "baselinesha",
                }
            )
        )
        run_result = {
            "passed": 10,
            "failed": 1,
            "error": 0,
            "skipped": 0,
            "total": 11,
            "failing_parallel": list(confirmed),
            "run_at": "2026-09-02T00:00:00+00:00",
        }
        classification = {
            "newly_broken": list(confirmed),
            "pre_existing": [],
            "inconclusive": [],
        }
        with (
            patch("sys.argv", ["nightly_regression_tests.py"]),
            patch.object(nrt, "resolve_fix_mode", return_value=mode),
            patch.object(nrt, "MIN_EXPECTED_COLLECTED", 0),
            patch.object(nrt, "load_env_or_die", return_value=(42, None)),
            patch.object(
                nrt, "run_tests", return_value=({"summary": {"total": 11}}, run_result, 0)
            ),
            patch.object(nrt, "reconfirm_serial", return_value=(list(confirmed), [], True)),
            patch.object(nrt, "maybe_dispatch_triage_session", return_value="sess-1"),
            patch.object(nrt, "open_issues", return_value={}),
            patch.object(nrt, "closed_issue_dispositions", return_value={}),
            patch.object(nrt, "run_ttft_gate", return_value=None),
            patch.object(nrt, "_get_head_commit", return_value="headsha"),
            patch.object(nrt, "classify_against_baseline", return_value=classification),
        ):
            assert nrt.main() == 0
        return json.loads(nrt.LAST_RUN_FILE.read_text())

    def test_state_key_invariance_between_shadow_and_off(self, tmp_path: Path) -> None:
        off_state = self._run(tmp_path, "off")
        shadow_state = self._run(tmp_path, "shadow")
        assert set(shadow_state) == set(off_state)
        assert "classify_attempts" not in shadow_state
        assert "fix_sessions" not in shadow_state


def _body_failed(nodeid: str, worker: str, line: str) -> dict:
    """A pytest-json-report entry shaped like a real test-BODY failure."""
    return {
        "nodeid": nodeid,
        "outcome": "failed",
        "setup": {"outcome": "passed"},
        "call": {
            "outcome": "failed",
            "longrepr": f"[{worker}] darwin -- Python 3.14.3\n{line}",
        },
    }


class TestBodyFailureGrouping:
    """Same normalized first error line across test bodies = one root cause (#3075).

    The 2026-08-24 incident: 39 issues filed over two causes, both body
    failures the setup-cascade path could not see.
    """

    LINE = "E   TypeError: AsyncAnthropic.__init__() got an unexpected keyword argument"
    OTHER = "E   AssertionError: worker_key must route to the lane slug"

    def test_same_line_across_workers_collapses_to_one_group(self) -> None:
        nodes = [f"tests/unit/test_llm_{i}.py::test_{i}" for i in range(6)]
        report = {"tests": [_body_failed(n, f"gw{i % 3}", self.LINE) for i, n in enumerate(nodes)]}
        groups, singles = nrt.group_body_failure_cascades(report, nodes)
        assert singles == []
        assert len(groups) == 1
        assert groups[0]["nodes"] == sorted(nodes)
        assert groups[0]["kind"] == "body"
        assert groups[0]["workers"] == ["gw0", "gw1", "gw2"]

    def test_second_cause_with_different_line_stays_separate(self) -> None:
        """The worker-key nodes inside the TypeError batch must NOT merge."""
        typeerror = [f"tests/unit/test_llm_{i}.py::test_{i}" for i in range(6)]
        workerkey = [f"tests/unit/test_wk_{i}.py::test_{i}" for i in range(3)]
        report = {
            "tests": [_body_failed(n, "gw0", self.LINE) for n in typeerror]
            + [_body_failed(n, "gw0", self.OTHER) for n in workerkey]
        }
        groups, singles = nrt.group_body_failure_cascades(report, typeerror + workerkey)
        assert len(groups) == 1
        assert groups[0]["nodes"] == sorted(typeerror)
        assert sorted(singles) == sorted(workerkey)

    def test_below_threshold_stays_per_node(self) -> None:
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(3)]
        report = {"tests": [_body_failed(n, "gw1", self.LINE) for n in nodes]}
        groups, singles = nrt.group_body_failure_cascades(report, nodes)
        assert groups == []
        assert singles == nodes

    def test_setup_errors_are_not_body_grouped(self) -> None:
        """Setup storms belong to group_setup_error_cascades, not this grouper."""
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(6)]
        report = {"tests": [_errored(n, "gw1", "RuntimeError: poisoned") for n in nodes]}
        groups, singles = nrt.group_body_failure_cascades(report, nodes)
        assert groups == []
        assert singles == nodes

    def test_title_namespace_is_distinct_from_setup_cascades(self) -> None:
        msg = "TypeError: same normalized message"
        assert nrt.body_cascade_title(msg) != nrt.cascade_title(msg)
        assert nrt.title_for_state_key(msg) == nrt.cascade_title(msg)
        assert nrt.title_for_state_key("body::" + msg) == nrt.body_cascade_title(msg)


class TestEnvironmentalClassification:
    """Network-layer failure text files nothing (#3075 defect 3, from #2932)."""

    def test_connection_refused_is_environmental(self) -> None:
        test = _body_failed("t.py::a", "gw0", "E   ConnectionRefusedError: [Errno 61]")
        assert nrt.is_environmental_failure(test)

    def test_dns_failure_is_environmental(self) -> None:
        test = _body_failed(
            "t.py::a", "gw0", "E   socket.gaierror: [Errno 8] nodename nor servname provided"
        )
        assert nrt.is_environmental_failure(test)

    def test_tls_failure_is_environmental(self) -> None:
        test = _body_failed("t.py::a", "gw0", "E   ssl.SSLError: CERTIFICATE_VERIFY_FAILED")
        assert nrt.is_environmental_failure(test)

    def test_plain_assertion_is_not_environmental(self) -> None:
        test = _body_failed("t.py::a", "gw0", "E   AssertionError: expected 3 == 4")
        assert not nrt.is_environmental_failure(test)

    def test_bare_timeout_is_deliberately_not_environmental(self) -> None:
        """Unit-test timeouts are routinely genuine regressions; do not silence them."""
        test = _body_failed("t.py::a", "gw0", "E   TimeoutError: took too long")
        assert not nrt.is_environmental_failure(test)


class TestClosedIssueDedup:
    """A closed exact-title issue must never be silently re-filed (#3075 defect 1)."""

    def _dispatch(self, monkeypatch, tmp_path, *, nodes, report, open_map, closed_map, prev=None):
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        monkeypatch.setattr(nrt, "open_issues", lambda: open_map)
        monkeypatch.setattr(nrt, "closed_issue_dispositions", lambda: closed_map)
        commented: list[int] = []
        monkeypatch.setattr(
            nrt, "comment_on_issue", lambda n, body, **kw: commented.append(n) or True
        )
        filed: list[list[str]] = []

        def fake_dispatch(ns, **kw):
            if not ns:
                return None
            filed.append(list(ns))
            return "sess-1"

        monkeypatch.setattr(nrt, "maybe_dispatch_triage_session", fake_dispatch)
        outcome = nrt.dispatch_findings(
            report, nodes, prev or {}, run_at="2026-09-04T03:00:00Z", head_commit="cafe1234"
        )
        return outcome, commented, filed

    def test_closed_not_planned_node_gets_a_comment_never_a_refile(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """The regression test the acceptance criteria demand: only issue is CLOSED."""
        node = "tests/unit/test_dead.py::test_watchdog"
        outcome, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=[node],
            report={"tests": [_body_failed(node, "gw0", "E   AssertionError: dead")]},
            open_map={},
            closed_map={f"Nightly regression: {node}": (2971, "NOT_PLANNED")},
        )
        assert filed == []
        assert commented == [2971]
        assert outcome.recorded == [node]
        assert outcome.issues_filed == 0

    def test_closed_completed_refiles_because_recurrence_is_new_information(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        node = "tests/unit/test_fixed.py::test_regressed_again"
        outcome, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=[node],
            report={"tests": [_body_failed(node, "gw0", "E   AssertionError: back")]},
            open_map={},
            closed_map={f"Nightly regression: {node}": (2500, "COMPLETED")},
        )
        assert commented == []
        assert filed == [[node]]
        assert outcome.issues_filed == 1

    def test_unknown_close_reason_comments_rather_than_refiling(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        node = "tests/unit/test_x.py::test_y"
        _, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=[node],
            report={"tests": [_body_failed(node, "gw0", "E   AssertionError: eh")]},
            open_map={},
            closed_map={f"Nightly regression: {node}": (11, "")},
        )
        assert filed == []
        assert commented == [11]

    def test_unreadable_closed_set_fails_open_and_files(self, monkeypatch, tmp_path: Path) -> None:
        node = "tests/unit/test_x.py::test_y"
        _, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=[node],
            report={"tests": [_body_failed(node, "gw0", "E   AssertionError: eh")]},
            open_map={},
            closed_map=None,
        )
        assert commented == []
        assert filed == [[node]]

    def test_open_issue_wins_over_closed_record(self, monkeypatch, tmp_path: Path) -> None:
        """An open issue is the live tracker even when a closed twin also matches."""
        node = "tests/unit/test_x.py::test_y"
        title = f"Nightly regression: {node}"
        _, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=[node],
            report={"tests": [_body_failed(node, "gw0", "E   AssertionError: eh")]},
            open_map={title: 99},
            closed_map={title: (11, "NOT_PLANNED")},
        )
        assert filed == []
        assert commented == [99]

    def test_closed_not_planned_cascade_comments_instead_of_refiling(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        msg = "RuntimeError: registry mismatch client=localhost:6379"
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(6)]
        report = {"tests": [_errored(n, "gw2", msg) for n in nodes]}
        normalized = nrt.setup_error_signature(report["tests"][0])[1]
        outcome, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=nodes,
            report=report,
            open_map={},
            closed_map={nrt.cascade_title(normalized): (3131, "NOT_PLANNED")},
        )
        assert filed == []
        assert commented == [3131]
        assert outcome.recorded == sorted(nodes)
        assert normalized not in outcome.cascade_issues

    def test_environmental_nodes_are_excluded_and_reported(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        env_node = "tests/integration/test_net.py::test_fetch"
        code_node = "tests/unit/test_logic.py::test_math"
        report = {
            "tests": [
                _body_failed(env_node, "gw0", "E   httpx.ConnectError: Connection refused"),
                _body_failed(code_node, "gw1", "E   AssertionError: 3 != 4"),
            ]
        }
        outcome, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=[env_node, code_node],
            report=report,
            open_map={},
            closed_map={},
        )
        assert filed == [[code_node]]
        assert outcome.environmental == [env_node]
        assert env_node not in outcome.recorded

    def test_body_group_files_one_umbrella_with_body_namespaced_state(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        line = "E   TypeError: AsyncAnthropic.__init__() got an unexpected keyword"
        nodes = [f"tests/unit/test_llm_{i}.py::test_{i}" for i in range(6)]
        report = {"tests": [_body_failed(n, "gw0", line) for n in nodes]}
        normalized = nrt.body_failure_signature(report["tests"][0])
        outcome, commented, filed = self._dispatch(
            monkeypatch, tmp_path, nodes=nodes, report=report, open_map={}, closed_map={}
        )
        assert commented == []
        assert len(filed) == 1
        assert filed[0] == [f"cascade:{nrt.body_cascade_title(normalized)}"]
        assert outcome.cascade_issues == {"body::" + normalized: None}
        assert outcome.recorded == sorted(nodes)

    def test_closed_issue_dispositions_parses_state_reason(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        seen: dict[str, list[str]] = {}

        class FakeResult:
            returncode = 0
            stdout = (
                '[{"number": 5, "title": "Nightly regression: a::t1", '
                '"stateReason": "NOT_PLANNED"},'
                ' {"number": 6, "title": "Nightly regression: b::t2", "stateReason": null}]'
            )
            stderr = ""

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            return FakeResult()

        monkeypatch.setattr(nrt.subprocess, "run", fake_run)
        assert nrt.closed_issue_dispositions() == {
            "Nightly regression: a::t1": (5, "NOT_PLANNED"),
            "Nightly regression: b::t2": (6, ""),
        }
        assert "--state" in seen["argv"] and "closed" in seen["argv"]
        assert "--search" not in seen["argv"]

    @pytest.mark.parametrize("failure", ["rc", "raise", "garbage"])
    def test_closed_issue_dispositions_returns_none_on_any_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
    ) -> None:
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")

        class FakeResult:
            returncode = 1 if failure == "rc" else 0
            stdout = "not json" if failure == "garbage" else "[]"
            stderr = "boom"

        def fake_run(argv, **kwargs):
            if failure == "raise":
                raise OSError("gh missing")
            return FakeResult()

        monkeypatch.setattr(nrt.subprocess, "run", fake_run)
        assert nrt.closed_issue_dispositions() is None


def _setup_failed(nodeid: str, worker: str, line: str) -> dict:
    """A pytest-json-report entry shaped like a real SETUP-phase failure."""
    return {
        "nodeid": nodeid,
        "outcome": "error",
        "setup": {
            "outcome": "failed",
            "longrepr": f"[{worker}] darwin -- Python 3.14.3\n{line}",
        },
    }


class TestReviewFindings3142:
    """Regression pins for the #3142 review round (blocker + tech debt)."""

    def _dispatch(self, monkeypatch, tmp_path, *, nodes, report, open_map, closed_map, prev=None):
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        monkeypatch.setattr(nrt, "open_issues", lambda: open_map)
        monkeypatch.setattr(nrt, "closed_issue_dispositions", lambda: closed_map)
        commented: list[int] = []
        monkeypatch.setattr(
            nrt, "comment_on_issue", lambda n, body, **kw: commented.append(n) or True
        )
        filed: list[list[str]] = []

        def fake_dispatch(ns, **kw):
            if not ns:
                return None
            filed.append(list(ns))
            return "sess-1"

        monkeypatch.setattr(nrt, "maybe_dispatch_triage_session", fake_dispatch)
        outcome = nrt.dispatch_findings(
            report, nodes, prev or {}, run_at="2026-09-05T03:00:00Z", head_commit="cafe1234"
        )
        return outcome, commented, filed

    def test_duplicate_closed_titles_resolve_to_newest_closure(self, monkeypatch, tmp_path):
        """The row with the newest ``closedAt`` per title wins, never the oldest.

        The #3142 review blocker: last-write-wins over gh's newest-first
        listing resolved six live nightly nodes to their oldest COMPLETED
        closure, re-filing nodes whose newest closure was NOT_PLANNED.
        """
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")

        class FakeResult:
            returncode = 0
            stdout = (
                '[{"number": 3112, "title": "Nightly regression: a::t", '
                '"stateReason": "NOT_PLANNED", "closedAt": "2026-09-03T21:00:00Z"},'
                ' {"number": 2919, "title": "Nightly regression: a::t", '
                '"stateReason": "COMPLETED", "closedAt": "2026-08-25T10:00:00Z"},'
                ' {"number": 2917, "title": "Nightly regression: a::t", '
                '"stateReason": "COMPLETED", "closedAt": "2026-08-24T10:00:00Z"}]'
            )
            stderr = ""

        monkeypatch.setattr(nrt.subprocess, "run", lambda argv, **kw: FakeResult())
        closed_map = nrt.closed_issue_dispositions()
        assert closed_map == {"Nightly regression: a::t": (3112, "NOT_PLANNED")}
        to_file, closed_matches = nrt.partition_closed_matches(["a::t"], closed_map)
        assert to_file == []
        assert closed_matches == [("a::t", 3112, "NOT_PLANNED")]

    def test_wave_shape_newest_closure_wins_over_newest_created(self, monkeypatch, tmp_path):
        """Creation order is not closure order: max ``closedAt`` decides.

        The #3142 round-2 blocker, in the wave-dup shape #3161 keeps
        generating: the later-created duplicate (#3061) was closed NOT_PLANNED
        early, the original (#3057) was closed COMPLETED later. The newest
        CLOSURE is COMPLETED, so the node must re-file (the one legitimate
        re-file case). First-row-per-title keyed on creation order suppressed
        it.
        """
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")

        class FakeResult:
            returncode = 0
            stdout = (
                '[{"number": 3061, "title": "Nightly regression: w::t", '
                '"stateReason": "NOT_PLANNED", "closedAt": "2026-08-31T05:31:00Z"},'
                ' {"number": 3059, "title": "Nightly regression: w::t", '
                '"stateReason": "NOT_PLANNED", "closedAt": "2026-08-31T05:31:30Z"},'
                ' {"number": 3057, "title": "Nightly regression: w::t", '
                '"stateReason": "COMPLETED", "closedAt": "2026-08-31T09:20:00Z"}]'
            )
            stderr = ""

        monkeypatch.setattr(nrt.subprocess, "run", lambda argv, **kw: FakeResult())
        closed_map = nrt.closed_issue_dispositions()
        assert closed_map == {"Nightly regression: w::t": (3057, "COMPLETED")}
        to_file, closed_matches = nrt.partition_closed_matches(["w::t"], closed_map)
        assert to_file == ["w::t"]
        assert closed_matches == []

    def test_saturated_closed_window_logs_a_warning(self, monkeypatch, tmp_path):
        """Saturation compares the caller's ``limit``, not the module constant."""
        log_file = tmp_path / "nightly.log"
        monkeypatch.setattr(nrt, "LOG_FILE", log_file)

        class FakeResult:
            returncode = 0
            stdout = (
                '[{"number": 2, "title": "t1", "stateReason": "COMPLETED"},'
                ' {"number": 1, "title": "t2", "stateReason": "COMPLETED"}]'
            )
            stderr = ""

        monkeypatch.setattr(nrt.subprocess, "run", lambda argv, **kw: FakeResult())
        assert nrt.closed_issue_dispositions(limit=2) is not None
        assert "saturated" in log_file.read_text()

    def test_assert_diff_embedding_network_string_is_not_environmental(self):
        """A formatter regression comparing against 'Connection refused...' files."""
        test = _body_failed(
            "t.py::a",
            "gw0",
            "E   AssertionError: assert 'wrong output' == 'Connection refused by upstream'",
        )
        assert not nrt.is_environmental_failure(test)

    def test_raised_connection_error_in_setup_is_environmental(self):
        test = _setup_failed(
            "t.py::a", "gw0", "E   ConnectionRefusedError: [Errno 61] Connection refused"
        )
        assert nrt.is_environmental_failure(test)

    def test_call_assertion_with_teardown_network_flake_is_not_environmental(self):
        """A code-level failure in ANY phase disqualifies the node (#3142 r2 nit).

        Under any-phase classification, a teardown network flake suppressed a
        genuine call-phase assertion regression. Every failing phase must look
        network-shaped for the node to classify environmental.
        """
        test = _body_failed(
            "t.py::mixed",
            "gw0",
            "E   AssertionError: assert 'wrong output' == 'expected output'",
        )
        test["teardown"] = {
            "outcome": "failed",
            "longrepr": (
                "[gw0] darwin\nE   ConnectionResetError: [Errno 54] Connection reset by peer"
            ),
        }
        assert not nrt.is_environmental_failure(test)

    def test_environmental_streak_persists_and_stays_excluded_below_threshold(
        self, monkeypatch, tmp_path
    ):
        env_node = "tests/integration/test_net.py::test_fetch"
        report = {
            "tests": [_body_failed(env_node, "gw0", "E   httpx.ConnectError: Connection refused")]
        }
        outcome, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=[env_node],
            report=report,
            open_map={},
            closed_map={},
            prev={"environmental_streaks": {env_node: 1}},
        )
        assert filed == []
        assert commented == []
        assert outcome.environmental == [env_node]
        assert outcome.escalated == []
        assert outcome.environmental_streaks == {env_node: 2}
        assert env_node not in outcome.recorded

    def test_environmental_streak_escalates_to_ordinary_filing_on_night_three(
        self, monkeypatch, tmp_path
    ):
        """#3163 gap 1: a persistent environmental-looking failure is a code bug
        until proven otherwise. Night three files it through the normal path."""
        env_node = "tests/integration/test_net.py::test_fetch"
        report = {
            "tests": [_body_failed(env_node, "gw0", "E   httpx.ConnectError: Connection refused")]
        }
        outcome, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=[env_node],
            report=report,
            open_map={},
            closed_map={},
            prev={"environmental_streaks": {env_node: 2}},
        )
        assert filed == [[env_node]]
        assert outcome.escalated == [env_node]
        assert outcome.environmental == []
        assert outcome.environmental_streaks == {env_node: 3}
        assert env_node in outcome.recorded

    def test_environmental_streak_resets_when_tonight_is_not_environmental(
        self, monkeypatch, tmp_path
    ):
        env_node = "tests/integration/test_net.py::test_fetch"
        gone_node = "tests/integration/test_net.py::test_gone"
        report = {"tests": [_body_failed(env_node, "gw0", "E   AssertionError: 3 != 4")]}
        outcome, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=[env_node],
            report=report,
            open_map={},
            closed_map={},
            prev={"environmental_streaks": {env_node: 2, gone_node: 5}},
        )
        assert filed == [[env_node]]
        assert outcome.environmental_streaks == {}

    def test_environmental_escalation_knob_zero_disables(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NIGHTLY_ENVIRONMENTAL_ESCALATE_NIGHTS", "0")
        env_node = "tests/integration/test_net.py::test_fetch"
        report = {
            "tests": [_body_failed(env_node, "gw0", "E   httpx.ConnectError: Connection refused")]
        }
        outcome, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=[env_node],
            report=report,
            open_map={},
            closed_map={},
            prev={"environmental_streaks": {env_node: 40}},
        )
        assert filed == []
        assert outcome.environmental == [env_node]
        assert outcome.environmental_streaks == {env_node: 41}

    def test_environmental_node_with_open_issue_gets_one_recurrence_comment(
        self, monkeypatch, tmp_path
    ):
        """#3163 gap 1b: the exclusion used to run before the open-issue
        partition, so a tracked node got no recurrence comment while it looked
        environmental. It now gets one and is recorded, so tomorrow's dispatch
        set suppresses it like any other commented node."""
        env_node = "tests/integration/test_net.py::test_fetch"
        report = {
            "tests": [_body_failed(env_node, "gw0", "E   httpx.ConnectError: Connection refused")]
        }
        outcome, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=[env_node],
            report=report,
            open_map={f"Nightly regression: {env_node}": 777},
            closed_map={},
        )
        assert filed == []
        assert commented == [777]
        assert outcome.comments_posted == 1
        assert outcome.recorded == [env_node]
        assert outcome.environmental == [env_node]
        assert outcome.environmental_streaks == {env_node: 1}
        assert "1 consecutive night so far" in nrt.environmental_epilogue(1)
        assert "3 consecutive nights so far" in nrt.environmental_epilogue(3)

    def test_environmental_streaks_reader_ignores_garbage_shapes(self):
        assert nrt.environmental_streaks({}) == {}
        assert nrt.environmental_streaks({"environmental_streaks": "nope"}) == {}
        assert nrt.environmental_streaks(
            {"environmental_streaks": {"a::t": 2, "b::t": 0, "c::t": "3", 4: 1}}
        ) == {"a::t": 2}

    def test_environmental_setup_storm_files_nothing(self, monkeypatch, tmp_path):
        """A >=3-node network setup storm is excluded BEFORE cascade grouping.

        The #3142 formal-review blocker: grouped environmental setup errors
        were collapsed into a cascade umbrella and filed as a code regression.
        """
        nodes = [f"t.py::storm{i}" for i in range(3)]
        report = {
            "tests": [
                _setup_failed(n, "gw3", "E   ConnectionRefusedError: [Errno 61] Connection refused")
                for n in nodes
            ]
        }
        outcome, commented, filed = self._dispatch(
            monkeypatch, tmp_path, nodes=nodes, report=report, open_map={}, closed_map={}
        )
        assert sorted(outcome.environmental) == sorted(nodes)
        assert filed == []
        assert commented == []
        assert outcome.issues_filed == 0

    def test_end_to_end_replay_dispatch_shapes(self, monkeypatch, tmp_path):
        """One dispatch_findings call over a 16-node constructed report.

        In-suite replay per issue #3075 AC 1: setup storm collapses to one
        umbrella, five same-line body failures to another, environmental nodes
        file nothing, an open issue and a NOT_PLANNED closure get comments,
        and only a COMPLETED closure re-files.
        """
        storm = [f"t.py::fd{i}" for i in range(6)]
        body = [f"t.py::typeerr{i}" for i in range(5)]
        env = ["t.py::dns0", "t.py::dns1"]
        node_open = "t.py::already_open"
        node_np = "t.py::consolidated"
        node_done = "t.py::fixed_regressed"
        nodes = storm + body + env + [node_open, node_np, node_done]
        assert len(nodes) == 16
        report = {
            "tests": [
                *[
                    _setup_failed(n, "gw2", "E   OSError: [Errno 24] Too many open files")
                    for n in storm
                ],
                *[
                    _body_failed(
                        n,
                        "gw1",
                        "E   TypeError: run_typed() got an unexpected keyword 'temperature'",
                    )
                    for n in body
                ],
                *[
                    _body_failed(n, "gw0", "E   socket.gaierror: [Errno 8] nodename nor servname")
                    for n in env
                ],
                _body_failed(node_open, "gw0", "E   ValueError: open case"),
                _body_failed(node_np, "gw0", "E   ValueError: consolidated case"),
                _body_failed(node_done, "gw0", "E   ValueError: regressed case"),
            ]
        }
        outcome, commented, filed = self._dispatch(
            monkeypatch,
            tmp_path,
            nodes=nodes,
            report=report,
            open_map={f"Nightly regression: {node_open}": 500},
            closed_map={
                f"Nightly regression: {node_np}": (501, "NOT_PLANNED"),
                f"Nightly regression: {node_done}": (502, "COMPLETED"),
            },
        )
        assert sorted(outcome.environmental) == sorted(env)
        cascade_dispatches = [f for f in filed if f[0].startswith("cascade:")]
        assert len(cascade_dispatches) == 2
        assert [node_done] in filed
        assert len(filed) == 3
        assert sorted(commented) == [500, 501]
        assert outcome.issues_filed == 3
        assert outcome.comments_posted == 2

    def test_epilogue_is_single_sourced(self):
        node_comment = nrt.closed_recurrence_comment(
            "a::t", "NOT_PLANNED", run_at="R", head_commit="H"
        )
        assert nrt.closed_epilogue("NOT_PLANNED") in node_comment
