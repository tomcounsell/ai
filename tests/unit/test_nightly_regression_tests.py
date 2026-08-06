"""Unit tests for scripts/nightly_regression_tests.py."""

from __future__ import annotations

import fcntl
import hashlib
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
                # .env is machine-local and absent from worktrees, where the real
                # load_env_or_die() raises SystemExit and fails this test for
                # reasons unrelated to what it asserts (#2573).
                patch.object(nrt, "load_env_or_die", return_value=42),
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

    def test_prompt_does_not_call_the_set_newly_confirmed(self, tmp_path: Path) -> None:
        """The dispatch set is "not yet filed", which is not the same as "new"."""
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch(
            "subprocess.run", return_value=self._fake_result('{"session_id": "abc123"}')
        ) as mock_run:
            nrt.maybe_dispatch_triage_session(["tests/unit/test_a.py::test_1"])
        argv = mock_run.call_args.args[0]
        prompt = argv[argv.index("--message") + 1]
        assert "Newly-confirmed" not in prompt
        assert "Not-yet-triaged failing node IDs:" in prompt
        assert "Search open issues first" in prompt

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

    _RUN_RESULT = {
        "passed": 10,
        "failed": 1,
        "error": 0,
        "skipped": 0,
        "total": 11,
        "failing_parallel": [],
        "run_at": "2026-07-21T00:00:00+00:00",
    }

    def _run_main(self, tmp_path: Path, prev_state: dict, confirmed: list[str], dispatch_return):
        nrt.LOG_FILE = tmp_path / "test.log"
        nrt.LOCK_FILE = tmp_path / "nightly_tests.lock"
        nrt.LAST_RUN_FILE = tmp_path / "last_run.json"
        nrt.LAST_RUN_FILE.write_text(json.dumps(prev_state))

        run_result = dict(self._RUN_RESULT, failing_parallel=list(confirmed))
        with (
            patch("sys.argv", ["nightly_regression_tests.py", "--dry-run"]),
            # .env is machine-local and absent from worktrees, where the real
            # load_env_or_die() raises SystemExit and fails this test for
            # reasons unrelated to what it asserts (#2573). The sentinel is
            # deliberately non-zero so a leak into main()'s return value fails
            # the `nrt.main() == 0` assertion below instead of passing silently.
            patch.object(nrt, "load_env_or_die", return_value=42),
            patch.object(nrt, "run_tests", return_value=run_result),
            patch.object(nrt, "reconfirm_serial", return_value=(list(confirmed), [])),
            patch.object(nrt, "summarize_failures", return_value="mocked summary"),
            patch.object(
                nrt, "maybe_dispatch_triage_session", return_value=dispatch_return
            ) as mock_dispatch,
            patch.object(nrt, "send_telegram") as mock_send,
            patch.object(nrt, "run_ttft_gate", return_value=None),
        ):
            assert nrt.main() == 0
        return json.loads(nrt.LAST_RUN_FILE.read_text()), mock_dispatch, mock_send

    def test_standing_failure_is_not_re_dispatched(self, tmp_path: Path) -> None:
        """The end-to-end #2559 regression: a filed node plus a new one dispatches one.

        Under the old code main() passed the full confirmed set, so the standing
        node went to triage again under a "Newly-confirmed" header.
        """
        standing = "tests/unit/test_watchdog.py::test_dead_node"
        fresh = "tests/unit/test_new.py::test_regression"
        prev = {"failing_tests": [standing], "dispatched_nodes": [standing]}
        saved, mock_dispatch, _ = self._run_main(
            tmp_path, prev, [standing, fresh], "triage-session-1"
        )
        mock_dispatch.assert_called_once_with([fresh])
        assert saved["dispatched_nodes"] == sorted([standing, fresh])

    def test_no_dispatch_when_everything_is_already_filed(self, tmp_path: Path) -> None:
        standing = "tests/unit/test_watchdog.py::test_dead_node"
        prev = {"failing_tests": [standing], "dispatched_nodes": [standing]}
        saved, mock_dispatch, _ = self._run_main(tmp_path, prev, [standing], None)
        mock_dispatch.assert_called_once_with([])
        assert saved["dispatched_nodes"] == [standing]

    def test_failed_dispatch_leaves_nodes_unfiled_for_retry(self, tmp_path: Path) -> None:
        node = "tests/unit/test_a.py::test_new"
        prev = {
            "failing_tests": [],
            "dispatched_nodes": [],
            "dispatched_session_id": "earlier-session",
        }
        saved, mock_dispatch, _ = self._run_main(tmp_path, prev, [node], None)
        mock_dispatch.assert_called_once_with([node])
        assert saved["dispatched_nodes"] == []
        assert saved["dispatched_session_id"] == "earlier-session"

    def test_successful_dispatch_records_the_nodes_and_session(self, tmp_path: Path) -> None:
        node = "tests/unit/test_a.py::test_new"
        prev = {
            "failing_tests": [],
            "dispatched_nodes": [],
            "dispatched_session_id": "earlier-session",
        }
        saved, _, mock_send = self._run_main(tmp_path, prev, [node], "new-session-id")
        assert saved["dispatched_nodes"] == [node]
        assert saved["dispatched_session_id"] == "new-session-id"
        mock_send.assert_called_once()
        assert "new-session-id" in mock_send.call_args.args[0]

    def test_node_that_stopped_failing_drops_out(self, tmp_path: Path) -> None:
        prev = {"failing_tests": ["a::t1"], "dispatched_nodes": ["a::t1"]}
        saved, _, _ = self._run_main(tmp_path, prev, [], None)
        assert saved["dispatched_nodes"] == []

    def test_baseline_run_seeds_rather_than_dispatches(self, tmp_path: Path) -> None:
        """A first run declares the known state; it must not file the whole suite.

        Without the seed, the *next* run would treat every standing baseline
        failure as an undispatched discovery.
        """
        standing = ["a::t1", "b::t2"]
        saved, mock_dispatch, _ = self._run_main(tmp_path, {}, standing, None)
        mock_dispatch.assert_called_once_with([])
        assert saved["dispatched_nodes"] == sorted(standing)

    def test_dispatched_hash_is_gone_from_persisted_state(self, tmp_path: Path) -> None:
        """dispatched_nodes supersedes the set-wide hash; the hash is not carried."""
        prev = {"failing_tests": [], "dispatched_hash": "stale", "dispatched_nodes": []}
        saved, _, _ = self._run_main(tmp_path, prev, ["a::t1"], "s1")
        assert "dispatched_hash" not in saved


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
    """Guard the #2327 fix: the entrypoint loads .env itself and fails loud on
    a silent-empty environment (the actual defect when /bin/bash EPERM'd on the
    TCC-protected Desktop-folder symlink)."""

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
            count = nrt.load_env_or_die()
        assert count == 2
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

    def test_unreadable_env_file_exits_1_loudly(self) -> None:
        """The exact TCC EPERM the fix exists to surface — must be a loud
        non-zero exit, not a silent degraded run."""
        with (
            patch("dotenv.dotenv_values", side_effect=OSError("Operation not permitted")),
            patch.object(nrt, "log") as mock_log,
        ):
            with pytest.raises(SystemExit) as exc:
                nrt.load_env_or_die()
        assert exc.value.code == 1
        assert any("FATAL" in str(c.args[0]) for c in mock_log.call_args_list)

    def test_short_load_below_floor_exits_1(self, monkeypatch) -> None:
        monkeypatch.setattr(nrt, "MIN_ENV_KEYS", 10)
        with (
            patch("dotenv.dotenv_values", return_value={"ONLY_ONE": "x"}),
            patch.object(nrt, "log") as mock_log,
        ):
            with pytest.raises(SystemExit) as exc:
                nrt.load_env_or_die()
        assert exc.value.code == 1
        assert any("only 1 env vars" in str(c.args[0]) for c in mock_log.call_args_list)

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
            count = nrt.load_env_or_die()
        assert count == 1
        assert "NIGHTLY_BLANK" not in nrt.os.environ
