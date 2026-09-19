"""Unit tests for scripts/nightly_regression_tests.py."""

from __future__ import annotations

import fcntl
import hashlib
import inspect
import json
import re
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Insert the scripts directory so we can import the module directly
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import nightly_regression_tests as nrt

# The four spellings of the index-backed lookup no prompt may name. Compiled
# here rather than written inline at each call site: the same alternation lives
# in the #3170 plan's Verification table, where a markdown cell escaped its
# pipes and made the row pass on entirely unfixed code.
SEARCH_TOKENS = re.compile(r"--search|gh search|search all|search open", re.I)


# Argv prefixes that mutate the real tracker. A unit test that reaches one of
# these has escaped its stubs: during this feature's build, informational runs of
# this file created 94 real GitHub issues (#3423-#3516) because four fake-`gh`
# harnesses stubbed ``comment_on_issue`` but not ``create_issue``. The fixture
# below makes that failure loud instead of indistinguishable from a pass.
_FORBIDDEN_GH_ARGV = (
    ("gh", "issue", "create"),
    ("gh", "issue", "close"),
)


@pytest.fixture(autouse=True)
def _no_real_tracker_writes(monkeypatch: pytest.MonkeyPatch):
    """Fail any test in this module whose argv would write to the live tracker.

    Autouse and module-wide on purpose: a future test that forgets to stub
    ``create_issue`` must fail rather than quietly file an issue. Tests that patch
    ``subprocess.run`` themselves shadow this guard, which is correct — they are
    not shelling out at all. If this fixture ever fires, fix the test; never the
    guard.
    """
    real_run = subprocess.run

    def guarded_run(argv, *args, **kwargs):
        as_tuple = tuple(str(a) for a in argv) if isinstance(argv, (list, tuple)) else (str(argv),)
        for forbidden in _FORBIDDEN_GH_ARGV:
            if as_tuple[: len(forbidden)] == forbidden:
                pytest.fail(
                    "a unit test reached the live tracker: "
                    f"{' '.join(as_tuple)} — stub create_issue/comment_on_issue"
                )
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)
    return guarded_run


# An issue-creation instruction in any prompt this module emits. The whole point
# of #3418 is that the detector creates every issue itself, so a surviving
# instruction telling an agent to open one is the drift this anti-criterion
# exists to catch. Written with word boundaries and an explicit article on
# purpose: ISSUE_LOOKUP_INSTRUCTION legitimately contains the phrase "issue
# creation" (describing the search index's lag), and a looser pattern would
# match the sentence that forbids the behaviour.
CREATE_INSTRUCTION_TOKENS = re.compile(
    r"gh issue create|\b(?:open|file|create)\s+(?:ONE|one|a|an)\s+(?:new\s+|umbrella\s+)?issue\b",
    re.I,
)


def _assert_per_node_dispatch(mock_dispatch, nodes: list[str]) -> None:
    """Assert one investigation dispatch went out carrying a real number per node.

    Replaces the pre-#3418 assertion over ``dispositions=``, which described what
    a filing agent was told about each node. No agent files now, so the only
    thing worth pinning about the hand-off is that every entry in it is an issue
    the detector already created: a ``(number, subject)`` pair whose number is a
    real integer GitHub returned, in the ``node {nodeid}`` subject shape
    ``dispatch_findings`` builds. A dispatch naming an issue that does not exist
    is the defect this channel used to have.
    """
    mock_dispatch.assert_called_once()
    args, kwargs = mock_dispatch.call_args
    assert len(args) == 1
    issues = list(args[0])
    assert [subject for _, subject in issues] == [f"node {n}" for n in nodes]
    assert all(isinstance(number, int) and number > 0 for number, _ in issues)
    assert kwargs["dry_run"] is False


# Sentinel for "this read answers with the same map as the run's opening read".
# Distinct from ``None``, which is the module's own "the read failed" signal.
_SAME_AS_OPEN = object()

_AUTO_NUMBER = object()


def _numbering(create_return: object = _AUTO_NUMBER):
    """A ``create_issue`` stand-in for the main() harnesses.

    ``create_issue`` reaching a real ``gh issue create`` subprocess from a unit
    test is not a hypothetical: an unstubbed run of this file opened 94 real
    issues. Every main() patch block routes through here so there is one place
    that has to stay stubbed, and it hands back plausible ascending numbers so
    the state main() persists is the state a real night would persist.
    ``create_return=None`` models the create that failed.
    """
    numbers: list[int] = []

    def fake_create(title: str, body: str, *, dry_run: bool = False):
        number = len(numbers) + 7001 if create_return is _AUTO_NUMBER else create_return
        numbers.append(number)
        return number

    fake_create.numbers = numbers
    return fake_create


class FakeGitHub:
    """One in-memory stand-in for every ``gh`` call the filing path makes.

    Replaces four near-identical hand-rolled harnesses. Each of those patched
    ``open_issues``, ``closed_issue_dispositions``, ``comment_on_issue`` and
    ``maybe_dispatch_triage_session`` and **none** patched a create, which was
    harmless while filing was delegated to a triage session and became a live
    hazard the moment ``create_issue`` landed: informational runs of this file
    created 94 real GitHub issues that way (#3423-#3516). One fixture that stubs
    the create alongside the comment is the structural fix; the module-wide
    ``_no_real_tracker_writes`` guard is the backstop for whatever this fixture
    does not cover.

    The fake is stateful rather than a set of constant returns, because the
    behaviour under test is check-then-act: a created issue becomes open from
    that moment on, which is what lets one instance answer both passes of
    :class:`TestFilingIdempotence` honestly. Reads hand out copies, so a caller
    holding an earlier snapshot sees the state it actually read.
    """

    def __init__(self) -> None:
        self.open_map: dict[str, int] | None = {}
        self.closed_map: dict[str, tuple[int, str]] | None = {}
        # What every read AFTER the run's opening one answers. The pre-create
        # refresh is the reason this is separately settable.
        self.refresh_map: object = _SAME_AS_OPEN
        self.open_reads = 0
        self.closed_reads = 0
        self.create_calls: list[tuple[str, str]] = []
        self.comment_calls: list[tuple[int, str]] = []
        # ``dry_run`` as each call site passed it, per call, so a flag that
        # stopped propagating is visible rather than merely harmless-looking.
        self.create_dry_runs: list[bool] = []
        self.comment_dry_runs: list[bool] = []
        self.dispatches: list[tuple[list[tuple[int, str]], dict]] = []
        self._next_number = 9000
        # Hooks, for the failure postures: ``create_hook(title, body) -> int|None``
        # and ``comment_hook(number, body) -> bool``.
        self.create_hook = None
        self.comment_hook = None

    # -- assertions read these -------------------------------------------------
    @property
    def created_titles(self) -> list[str]:
        return [title for title, _ in self.create_calls]

    @property
    def commented(self) -> list[int]:
        return [number for number, _ in self.comment_calls]

    def body_for(self, title: str) -> str:
        return next(body for t, body in self.create_calls if t == title)

    # -- the stubbed module functions -----------------------------------------
    def open_issues(self, *args, **kwargs):
        self.open_reads += 1
        source = self.open_map
        if self.open_reads > 1 and self.refresh_map is not _SAME_AS_OPEN:
            source = self.refresh_map
        return None if source is None else dict(source)

    def closed_issue_dispositions(self, *args, **kwargs):
        self.closed_reads += 1
        return None if self.closed_map is None else dict(self.closed_map)

    def create_issue(self, title: str, body: str, *, dry_run: bool = False):
        self.create_calls.append((title, body))
        self.create_dry_runs.append(dry_run)
        if self.create_hook is not None:
            number = self.create_hook(title, body)
        else:
            self._next_number += 1
            number = self._next_number
        if number is not None and self.open_map is not None:
            self.open_map[title] = number
        return number

    def comment_on_issue(self, number: int, body: str, *, dry_run: bool = False) -> bool:
        self.comment_calls.append((number, body))
        self.comment_dry_runs.append(dry_run)
        return True if self.comment_hook is None else self.comment_hook(number, body)

    def maybe_dispatch_triage_session(self, issues, **kwargs):
        pairs = list(issues)
        self.dispatches.append((pairs, kwargs))
        return "sess-1" if pairs else None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
        monkeypatch.setattr(nrt, "open_issues", self.open_issues)
        monkeypatch.setattr(nrt, "closed_issue_dispositions", self.closed_issue_dispositions)
        monkeypatch.setattr(nrt, "create_issue", self.create_issue)
        monkeypatch.setattr(nrt, "comment_on_issue", self.comment_on_issue)
        monkeypatch.setattr(
            nrt, "maybe_dispatch_triage_session", self.maybe_dispatch_triage_session
        )
        return self


@pytest.fixture
def fake_github(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeGitHub:
    """A :class:`FakeGitHub` installed over the module, with the log redirected."""
    monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
    return FakeGitHub().install(monkeypatch)


def log_text() -> str:
    """Whatever the module logged this test, or ``""`` if it logged nothing."""
    return nrt.LOG_FILE.read_text() if nrt.LOG_FILE.exists() else ""


def dispatch_with(
    gh: FakeGitHub,
    *,
    nodes: list[str],
    report: dict,
    open_map: object = _SAME_AS_OPEN,
    closed_map: object = _SAME_AS_OPEN,
    refresh_map: object = _SAME_AS_OPEN,
    prev: dict | None = None,
    run_at: str = "2026-09-04T03:00:00Z",
    head_commit: str = "cafe1234",
    **kwargs,
) -> nrt.DispatchOutcome:
    """Run one ``dispatch_findings`` pass against ``gh``.

    ``open_map=None`` and ``closed_map=None`` mean the read failed, which is the
    module's own convention and why the "leave it alone" default is a sentinel
    rather than ``None``.
    """
    if open_map is not _SAME_AS_OPEN:
        gh.open_map = open_map
    if closed_map is not _SAME_AS_OPEN:
        gh.closed_map = closed_map
    if refresh_map is not _SAME_AS_OPEN:
        gh.refresh_map = refresh_map
    return nrt.dispatch_findings(
        report,
        nodes,
        prev or {},
        run_at=run_at,
        head_commit=head_commit,
        **kwargs,
    )


def body_failure_report(nodes: list[str]) -> dict:
    """A report whose nodes each failed in their own test BODY with their own line.

    Distinct lines on purpose: body grouping needs a shared normalized line, so
    each node stays its own finding and the per-node filing path is what runs.
    """
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


class TestBuildInvestigationPrompt:
    """The one prompt this module still emits: investigate, comment, create nothing.

    Replaces ``TestBuildTriagePrompt``. Its subject — what a filing agent was
    told about each node it should file — no longer exists: the detector creates
    every issue itself and hands the session numbers GitHub already confirmed
    (#3418). What is left worth pinning is that the numbers arrive literally
    (the #2559 concern, one layer on: an agent that has to *derive* an
    identifier gets it wrong), and that nothing in the text asks for a second
    tracker write.
    """

    def test_literal_issue_numbers_present(self) -> None:
        """The numbers, AND the lookup mechanism the session is told to use.

        The identifier half alone stayed true across the #3170 change and was
        therefore blind to it: the prompt could carry every literal identifier
        while still directing the agent at the index-backed lookup that produced
        the #2960-#2999 wave. The two halves belong in one test.
        """
        issues = [(4242, "node tests/unit/test_a.py::test_1"), (4243, "cascade umbrella 'boom'")]
        prompt = nrt._build_investigation_prompt(issues)
        for number, subject in issues:
            assert f"#{number}" in prompt
            assert subject in prompt
        assert "gh issue list --state all" in prompt
        assert not SEARCH_TOKENS.search(prompt)

    def test_the_prompt_asks_for_a_comment_and_nothing_else(self) -> None:
        """The anti-criterion at the builder, not only at the parametrized gate."""
        prompt = nrt._build_investigation_prompt([(4242, "node a::t1")])
        assert "COMMENT" in prompt
        assert CREATE_INSTRUCTION_TOKENS.findall(prompt) == []

    def test_an_empty_issue_list_still_renders_the_instruction_block(self) -> None:
        """``maybe_dispatch_triage_session`` gates on emptiness before it ever
        builds a prompt, so an empty list must degrade rather than raise."""
        prompt = nrt._build_investigation_prompt([])
        assert nrt.ISSUE_LOOKUP_INSTRUCTION in prompt


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

    def test_body_states_one_issue_with_a_collapsed_node_list(self) -> None:
        """Renamed from ``test_prompt_orders_one_issue_...``.

        The one-defect framing and the collapsed node list used to be
        INSTRUCTIONS to an agent about the issue it should open. The detector
        writes that issue itself now, so the same claims are asserted against the
        body it creates -- and "Do NOT open per-node issues" has no analogue,
        because there is no longer anything that could: per-node filing is
        suppressed in Python by ``group_setup_error_cascades`` claiming the nodes.
        """
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(5)]
        report = {"tests": [_errored(n, "gw2", self.MSG) for n in nodes]}
        cascade = nrt.group_setup_error_cascades(report, nodes)[0][0]
        body = nrt.cascade_issue_body(cascade, run_at="RUN", head_commit="HEAD")
        assert "ONE defect" in body
        assert "<details>" in body
        assert CREATE_INSTRUCTION_TOKENS.findall(body) == []
        for n in nodes:
            assert n in body


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


class TestCreateIssue:
    """Mirrors ``TestPreFileDedup`` one-for-one, but for the write side (#3418).

    ``create_issue`` is deliberately modelled on ``comment_on_issue`` two hundred
    lines above it: same argv shape, same stdin discipline, same "log and return
    falsy, never raise" posture, same ``dry_run`` short-circuit. These tests pin
    that contract directly rather than through ``dispatch_findings``.
    """

    def test_argv_shape_and_body_on_stdin_and_cwd(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        seen: dict[str, object] = {}

        class FakeResult:
            returncode = 0
            stdout = "https://github.com/owner/repo/issues/4242\n"
            stderr = ""

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            return FakeResult()

        monkeypatch.setattr(nrt.subprocess, "run", fake_run)
        title = "Nightly regression: tests/unit/test_a.py::test_1"
        assert nrt.create_issue(title, "the body") == 4242
        assert seen["argv"] == ["gh", "issue", "create", "--title", title, "--body-file", "-"]
        assert title not in seen["argv"][-2:], "the title is not smuggled onto stdin"
        assert seen["kwargs"]["cwd"] == nrt.PROJECT_DIR
        assert seen["kwargs"]["input"] == "the body"

    def test_dry_run_creates_nothing_and_returns_a_truthy_sentinel(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        monkeypatch.setattr(
            nrt.subprocess, "run", lambda *a, **k: pytest.fail("dry run must not shell out")
        )
        result = nrt.create_issue("title", "body", dry_run=True)
        # Truthy-but-not-a-real-number: the caller's success path (budget,
        # recorded, cascade_issues, the dispatch payload) runs exactly as it
        # would for a real create, but the number is the DRY_RUN_ISSUE_NUMBER
        # sentinel, never a number GitHub actually assigned.
        assert result == nrt.DRY_RUN_ISSUE_NUMBER
        assert result

    @pytest.mark.parametrize("side", ["title", "body"])
    def test_empty_title_or_body_is_refused_before_any_subprocess(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, side: str
    ) -> None:
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        monkeypatch.setattr(
            nrt.subprocess, "run", lambda *a, **k: pytest.fail("must not shell out")
        )
        title = "" if side == "title" else "Nightly regression: a::t1"
        body = "" if side == "body" else "some body"
        assert nrt.create_issue(title, body) is None

    @pytest.mark.parametrize("failure", ["rc", "raise", "garbage", "empty"])
    def test_create_issue_returns_none_on_any_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
    ) -> None:
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")

        class FakeResult:
            returncode = 1 if failure == "rc" else 0
            stdout = {
                "garbage": "not a url",
                "empty": "   ",
            }.get(failure, "https://github.com/owner/repo/issues/1")
            stderr = "boom"

        def fake_run(argv, **kwargs):
            if failure == "raise":
                raise FileNotFoundError("gh missing")
            return FakeResult()

        monkeypatch.setattr(nrt.subprocess, "run", fake_run)
        assert nrt.create_issue("Nightly regression: a::t1", "body") is None


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
    """Every entry is a real number now, so there is no pending state to upgrade."""

    MSG = "RuntimeError: redis client is not on the claimed server"

    def test_a_still_open_entry_is_kept_verbatim(self) -> None:
        """Replaces the ``None``-sentinel upgrade case.

        The detector creates its own umbrellas, so the number is known the moment
        one exists and ``cascade_issues`` never holds a placeholder to resolve on
        a later run (#3418). What the function still has to do is keep an entry
        whose issue is demonstrably open.
        """
        title = nrt.cascade_title(self.MSG)
        assert nrt.carry_cascade_issues({self.MSG: 4242}, {title: 4242}) == {self.MSG: 4242}

    def test_closed_issue_drops_out(self) -> None:
        """No issue is open, so a recurrence deserves a fresh filing, not silence."""
        assert nrt.carry_cascade_issues({self.MSG: 4242}, {"unrelated": 7}) == {}

    def test_an_empty_open_map_drops_everything(self) -> None:
        assert nrt.carry_cascade_issues({self.MSG: 4242}, {}) == {}

    def test_unreadable_open_list_keeps_the_map_verbatim(self) -> None:
        """`None` is "could not tell", never evidence that anything closed."""
        prev = {self.MSG: 4242, "other": 77}
        assert nrt.carry_cascade_issues(prev, None) == prev


class TestDispatchFindings:
    """Comment-over-create is the default posture, not a fallback (#3134)."""

    MSG = "RuntimeError: redis client is not on the claimed server"

    def _report(self, nodes):
        return {"tests": [_errored(n, "gw2", self.MSG) for n in nodes]}

    def test_night_one_creates_one_umbrella_and_records_its_real_number(
        self, fake_github: FakeGitHub
    ) -> None:
        """Renamed from ``..._records_the_signature_as_pending``.

        There is no pending state left to record: ``create_issue`` returns the
        number GitHub assigned, so ``cascade_issues`` and ``filed_issues`` both
        carry that number from the moment the umbrella exists (#3418).
        """
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(9)]
        outcome = dispatch_with(fake_github, nodes=nodes, report=self._report(nodes), open_map={})
        title = nrt.cascade_title(self.MSG)
        assert fake_github.created_titles == [title]
        number = outcome.filed_issues[self.MSG]
        assert isinstance(number, int)
        assert (outcome.issues_filed, outcome.comments_posted) == (1, 0)
        assert fake_github.commented == []
        assert outcome.recorded == sorted(nodes)
        assert outcome.cascade_issues == {self.MSG: number}
        # The created body carries the fingerprint of the cascade's STATE KEY,
        # which is what makes a twin an exact-match question later.
        assert nrt.fingerprint_marker(self.MSG) in fake_github.body_for(title)
        assert fake_github.dispatches[-1][0] == [
            (number, f"cascade umbrella {title!r} (9 node(s))")
        ]

    def test_night_two_comments_instead_of_filing_a_second_issue(
        self, fake_github: FakeGitHub
    ) -> None:
        """The whole point: a recurring cascade accretes a comment, never a twin."""
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(9)]
        outcome = dispatch_with(
            fake_github,
            nodes=nodes,
            report=self._report(nodes),
            open_map={nrt.cascade_title(self.MSG): 4242},
            prev={"cascade_issues": {self.MSG: 4242}},
        )
        assert fake_github.create_calls == []
        assert (outcome.issues_filed, outcome.comments_posted) == (0, 1)
        assert fake_github.commented == [4242]
        assert outcome.recorded == sorted(nodes)
        assert outcome.cascade_issues == {self.MSG: 4242}

    def test_a_comment_that_failed_to_post_leaves_the_finding_unrecorded(
        self, fake_github: FakeGitHub
    ) -> None:
        """Recording it as handled would lose the recurrence permanently."""
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(9)]
        fake_github.comment_hook = lambda number, body: False
        outcome = dispatch_with(
            fake_github,
            nodes=nodes,
            report=self._report(nodes),
            open_map={nrt.cascade_title(self.MSG): 4242},
        )
        assert outcome.recorded == []
        assert outcome.comments_posted == 0

    def test_a_create_that_failed_leaves_the_finding_unrecorded(
        self, fake_github: FakeGitHub
    ) -> None:
        """The create half of the same contract, which had no coverage before.

        ``create_issue`` never retries (a retried POST is a second issue), so a
        failure has to leave the finding out of ``recorded`` or the next run
        suppresses it against an issue that was never created.
        """
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(9)]
        fake_github.create_hook = lambda title, body: None
        outcome = dispatch_with(fake_github, nodes=nodes, report=self._report(nodes), open_map={})
        assert len(fake_github.create_calls) == 1
        assert outcome.recorded == []
        assert outcome.filed_issues == {}
        assert outcome.issues_filed == 0
        assert outcome.cascade_issues == {}
        assert "Create failed" in log_text()

    def test_per_node_recurrence_is_commented_not_suppressed(self, fake_github: FakeGitHub) -> None:
        """#3131 dropped the duplicate silently, which lost the recurrence signal."""
        nodes = ["tests/unit/test_a.py::test_1", "tests/unit/test_b.py::test_2"]
        outcome = dispatch_with(
            fake_github,
            nodes=nodes,
            report=body_failure_report(nodes),
            open_map={"Nightly regression: tests/unit/test_a.py::test_1": 11},
        )
        assert fake_github.commented == [11]
        assert fake_github.created_titles == ["Nightly regression: tests/unit/test_b.py::test_2"]
        assert sorted(outcome.recorded) == sorted(nodes)

    def test_comments_do_not_spend_the_issue_budget(
        self, fake_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cap bounds NEW tracker surface, and a comment creates none.

        The budget now decrements per confirmed create, so a cap of 1 against
        three recurrences plus one genuinely new node has to produce three
        comments and still afford the one create.
        """
        monkeypatch.setenv("NIGHTLY_MAX_ISSUES_PER_RUN", "1")
        nodes = [f"tests/unit/test_{c}.py::test_1" for c in "abcd"]
        outcome = dispatch_with(
            fake_github,
            nodes=nodes,
            report=body_failure_report(nodes),
            open_map={f"Nightly regression: {n}": i for i, n in enumerate(nodes[:3], start=1)},
        )
        assert sorted(fake_github.commented) == [1, 2, 3]
        assert fake_github.created_titles == ["Nightly regression: tests/unit/test_d.py::test_1"]
        assert outcome.issues_filed == 1
        assert sorted(outcome.recorded) == sorted(nodes)

    def test_cascades_only_suppresses_per_node_filing(self, fake_github: FakeGitHub) -> None:
        """``cascades_only`` suppresses per-node CREATES now, not a per-node dispatch."""
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
        outcome = dispatch_with(
            fake_github,
            nodes=[*cascade_nodes, loner],
            report=report,
            open_map={},
            cascades_only=True,
        )
        assert outcome.issues_filed == 1
        assert fake_github.created_titles == [nrt.cascade_title(self.MSG)]
        assert f"Nightly regression: {loner}" not in fake_github.created_titles
        assert loner not in outcome.recorded
        # The investigation dispatch still fires from the cascades-only exit, and
        # carries the umbrella's real number rather than a `cascade:` pseudo-node.
        dispatched, _ = fake_github.dispatches[-1]
        assert [n for n, _ in dispatched] == [outcome.filed_issues[self.MSG]]

    def test_a_clean_night_never_shells_out_to_gh(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Extended to the create path: a clean night reaches no `gh` verb at all."""
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "nightly.log")
        monkeypatch.setattr(nrt, "open_issues", lambda: pytest.fail("read gh on a clean night"))
        monkeypatch.setattr(
            nrt, "closed_issue_dispositions", lambda: pytest.fail("read gh closed set")
        )
        monkeypatch.setattr(
            nrt, "create_issue", lambda *a, **k: pytest.fail("created on a clean night")
        )
        monkeypatch.setattr(
            nrt, "comment_on_issue", lambda *a, **k: pytest.fail("commented on a clean night")
        )
        monkeypatch.setattr(nrt, "maybe_dispatch_triage_session", lambda *a, **k: None)
        outcome = nrt.dispatch_findings({"tests": []}, [], {}, run_at="RUN", head_commit="HEAD")
        assert outcome.recorded == []
        assert outcome.issues_filed == 0


class TestSurvivorsReachCreateIssue:
    """Replaces ``TestDispositionHandoff``.

    Its subject — the ``dispositions=`` a filing agent was handed — ceased to
    exist with ``NodeDisposition``. The same four questions are still worth
    asking, one layer in: which findings reach ``create_issue``, and what the two
    degraded-read postures do to that set.
    """

    MSG = "RuntimeError: redis client is not on the claimed server"

    def test_only_the_untracked_survivor_is_created(self, fake_github: FakeGitHub) -> None:
        """The second-writer hazard, pinned at the create.

        An already-open node and a closed-not-planned node are commented on and
        dropped before filing; only the genuinely new node becomes an issue, with
        the exact title ``partition_already_open`` keys its dedup on and a body
        carrying that node's fingerprint.
        """
        already_open = "tests/unit/test_open.py::test_1"
        closed_np = "tests/unit/test_closed.py::test_2"
        survivor = "tests/unit/test_new.py::test_3"
        nodes = [already_open, closed_np, survivor]
        outcome = dispatch_with(
            fake_github,
            nodes=nodes,
            report=body_failure_report(nodes),
            open_map={f"Nightly regression: {already_open}": 11},
            closed_map={f"Nightly regression: {closed_np}": (22, "NOT_PLANNED")},
        )
        assert fake_github.created_titles == [f"Nightly regression: {survivor}"]
        body = fake_github.body_for(f"Nightly regression: {survivor}")
        assert nrt.fingerprint_marker(survivor) in body
        assert f"`{survivor}`" in body
        assert sorted(fake_github.commented) == [11, 22]
        assert outcome.filed_issues == {
            survivor: fake_github.open_map[f"Nightly regression: {survivor}"]
        }

    def test_an_unreadable_open_read_still_files(self, fake_github: FakeGitHub) -> None:
        """Fail-open, preserved for the create path, and SAID so in the log.

        An unreadable open map is the one night the dedup is blind, and the
        degraded-refresh line is the only signal of it — the run otherwise files
        everything it should, so a missing line makes a blind night look normal.
        """
        survivor = "tests/unit/test_new.py::test_3"
        outcome = dispatch_with(
            fake_github,
            nodes=[survivor],
            report=body_failure_report([survivor]),
            open_map=None,
            closed_map={},
        )
        assert fake_github.created_titles == [f"Nightly regression: {survivor}"]
        assert outcome.issues_filed == 1
        text = log_text()
        assert "Dedup disabled for this run (open issues unreadable)" in text
        assert "Pre-create refresh unreadable" in text

    def test_an_unreadable_closed_read_still_files(self, fake_github: FakeGitHub) -> None:
        """The same posture for the closed read, which has its own log line."""
        survivor = "tests/unit/test_new.py::test_3"
        outcome = dispatch_with(
            fake_github,
            nodes=[survivor],
            report=body_failure_report([survivor]),
            open_map={},
            closed_map=None,
        )
        assert fake_github.created_titles == [f"Nightly regression: {survivor}"]
        assert outcome.issues_filed == 1
        assert "Closed-state dedup disabled for this run" in log_text()

    def test_the_cascade_umbrella_is_created_not_dispatched_to_be_filed(
        self, fake_github: FakeGitHub
    ) -> None:
        """The umbrella used to be an instruction in a prompt; it is a create now."""
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(9)]
        report = {"tests": [_errored(n, "gw2", self.MSG) for n in nodes]}
        outcome = dispatch_with(fake_github, nodes=nodes, report=report, open_map={}, closed_map={})
        title = nrt.cascade_title(self.MSG)
        assert fake_github.created_titles == [title]
        number = outcome.filed_issues[self.MSG]
        dispatched, kwargs = fake_github.dispatches[-1]
        assert dispatched == [(number, f"cascade umbrella {title!r} (9 node(s))")]
        assert "prompt" not in kwargs
        assert "dispositions" not in kwargs


class TestFilingIdempotence:
    """The regression test for #3382-#3405 and the whole #3170 class.

    Run ``dispatch_findings`` twice against ONE in-memory ``FakeGitHub`` whose
    ``create_issue`` mutates the open-issue map, the way a real create does.
    Nothing prior to #3418 could write this test: the duplication happened
    inside an LLM session's subprocess with no assertable surface. Now the
    create is a module function reached from one Python loop, so a second pass
    over the same findings has to create nothing.
    """

    def test_second_pass_creates_zero_and_comments_once_per_node(
        self, fake_github: FakeGitHub
    ) -> None:
        nodes = ["tests/unit/test_a.py::test_1", "tests/unit/test_b.py::test_2"]
        report = body_failure_report(nodes)

        first = dispatch_with(fake_github, nodes=nodes, report=report, open_map={}, closed_map={})
        assert sorted(first.filed_issues) == sorted(nodes)
        assert len(fake_github.create_calls) == len(nodes)
        created_numbers = sorted(fake_github.open_map.values())

        second = dispatch_with(fake_github, nodes=nodes, report=report, closed_map={})
        assert len(fake_github.create_calls) == len(nodes), "pass two created zero new issues"
        assert second.filed_issues == {}
        assert second.issues_filed == 0
        assert sorted(fake_github.commented) == created_numbers
        assert second.comments_posted == len(nodes)
        assert sorted(second.recorded) == sorted(nodes)


class TestPreCreateRefreshComments:
    """A fake whose opening read is empty but the pre-create refresh is not.

    Models an external actor filing the same title between this run's opening
    ``open_issues()`` read and its per-survivor create: the refresh is the last
    check before a create, so it must win over a stale opening snapshot.
    """

    def test_refresh_hit_comments_instead_of_creating(self, fake_github: FakeGitHub) -> None:
        survivor = "tests/unit/test_new.py::test_3"
        title = f"Nightly regression: {survivor}"
        outcome = dispatch_with(
            fake_github,
            nodes=[survivor],
            report=body_failure_report([survivor]),
            open_map={},
            closed_map={},
            refresh_map={title: 555},
        )
        assert fake_github.create_calls == []
        assert fake_github.commented == [555]
        assert outcome.recorded == [survivor]
        assert "Pre-create refresh found #555" in log_text()

    def test_refresh_hit_whose_comment_fails_leaves_the_node_unrecorded(
        self, fake_github: FakeGitHub
    ) -> None:
        survivor = "tests/unit/test_new.py::test_3"
        title = f"Nightly regression: {survivor}"
        fake_github.comment_hook = lambda number, body: False
        outcome = dispatch_with(
            fake_github,
            nodes=[survivor],
            report=body_failure_report([survivor]),
            open_map={},
            closed_map={},
            refresh_map={title: 555},
        )
        assert fake_github.create_calls == []
        assert outcome.recorded == []
        assert outcome.comments_posted == 0


class TestFingerprintCollisionIsLogged:
    """A second finding resolving to a fingerprint already filed THIS run.

    The report-only decision is deliberate (#3418 plan, ``## Decisions`` #2): no
    read-back, no closing of a twin. The anti-assertion below pins that the
    detector never gains a close privilege it was not given.
    """

    def test_second_finding_with_the_same_fingerprint_is_skipped(
        self, fake_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(nrt, "finding_fingerprint", lambda identity: "collided-fingerprint")
        nodes = ["tests/unit/test_a.py::test_1", "tests/unit/test_b.py::test_2"]
        outcome = dispatch_with(
            fake_github,
            nodes=nodes,
            report=body_failure_report(nodes),
            open_map={},
            closed_map={},
        )
        assert len(fake_github.create_calls) == 1
        assert outcome.filed_issues == {
            nodes[0]: fake_github.open_map[f"Nightly regression: {nodes[0]}"]
        }
        assert nodes[1] not in outcome.recorded

        text = log_text()
        assert "WARNING" in text
        assert "fingerprint collision" in text
        assert "collided-fingerprint" in text
        assert f"node {nodes[1]}" in text

        # Anti-assertion: report-only. No close ever shelled out, and no close
        # helper exists on the module for a caller to reach for.
        assert not hasattr(nrt, "close_issue")
        assert "gh issue close" not in text


class TestIssueBudgetSpendsOnlyConfirmedCreates:
    """The cap bounds confirmed creates, never intentions (#3418)."""

    def test_cap_creates_exactly_the_cap_and_defers_the_rest(
        self, fake_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NIGHTLY_MAX_ISSUES_PER_RUN", "2")
        nodes = [f"tests/unit/test_{c}.py::test_1" for c in "abcd"]
        outcome = dispatch_with(
            fake_github,
            nodes=nodes,
            report=body_failure_report(nodes),
            open_map={},
            closed_map={},
        )
        assert len(fake_github.create_calls) == 2
        assert outcome.issues_filed == 2
        deferred = [n for n in nodes if n not in outcome.recorded]
        assert len(deferred) == 2
        assert sorted(outcome.recorded) == nodes[:2]
        assert "Issue budget reached" in log_text()

    def test_a_failed_create_spends_no_budget_so_the_next_survivor_can_still_file(
        self, fake_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NIGHTLY_MAX_ISSUES_PER_RUN", "1")
        calls = {"n": 0}

        def hook(title: str, body: str):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            fake_github._next_number += 1
            number = fake_github._next_number
            fake_github.open_map[title] = number
            return number

        fake_github.create_hook = hook
        nodes = ["tests/unit/test_a.py::test_1", "tests/unit/test_b.py::test_2"]
        outcome = dispatch_with(
            fake_github,
            nodes=nodes,
            report=body_failure_report(nodes),
            open_map={},
            closed_map={},
        )
        assert calls["n"] == 2
        # The failed first create spent no budget, so the cap of 1 still
        # affords the second node's create.
        assert outcome.issues_filed == 1
        assert nodes[0] not in outcome.recorded
        assert nodes[1] in outcome.recorded
        assert "Create failed" in log_text()


class TestKillSwitchSuppressesCreates:
    """``NIGHTLY_AUTO_FILE`` is read at call time, never captured at import.

    Set after ``nightly_regression_tests`` is already imported, which is the
    only way this test can distinguish an import-time read (always the in-code
    default, since the vault ``.env`` is only loaded inside ``main()``) from the
    call-time read the module actually implements (:func:`resolve_bool_knob`).
    """

    def test_kill_switch_suppresses_creates_but_not_comments(
        self, fake_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NIGHTLY_AUTO_FILE", "false")
        already_open = "tests/unit/test_open.py::test_1"
        survivors = ["tests/unit/test_new.py::test_2", "tests/unit/test_new.py::test_3"]
        nodes = [already_open, *survivors]
        outcome = dispatch_with(
            fake_github,
            nodes=nodes,
            report=body_failure_report(nodes),
            open_map={f"Nightly regression: {already_open}": 11},
            closed_map={},
        )
        assert fake_github.create_calls == []
        assert fake_github.commented == [11]
        assert outcome.recorded == [already_open]
        for survivor in survivors:
            assert survivor not in outcome.recorded
        text = log_text()
        assert text.count("NIGHTLY_AUTO_FILE is off") == len(survivors)


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

    def _state_file(self, monkeypatch, tmp_path: Path) -> Path:
        monkeypatch.setattr(nrt, "LAST_RUN_FILE", tmp_path / "last_run.json")
        monkeypatch.setattr(nrt, "_get_head_commit", lambda: "cafe1234")
        return tmp_path / "last_run.json"

    def test_trip_still_files_the_cascade(
        self, fake_github: FakeGitHub, monkeypatch, tmp_path: Path
    ) -> None:
        state = self._state_file(monkeypatch, tmp_path)
        nodes, report = self._storm()

        rc = nrt._handle_integrity_trip(
            "infrastructure, not a red suite", report, None, self._prev()
        )

        assert rc == 1
        title = nrt.cascade_title(self.MSG)
        assert fake_github.created_titles == [title], "the storm was filed exactly once"
        saved = json.loads(state.read_text())
        assert saved["dispatched_nodes"] == sorted(nodes)
        # The recorded number is the umbrella's REAL number now, not the None
        # placeholder the agent-filing era had to persist.
        assert saved["cascade_issues"] == {self.MSG: fake_github.open_map[title]}

    def test_trip_never_overwrites_the_baseline(
        self, fake_github: FakeGitHub, monkeypatch, tmp_path: Path
    ) -> None:
        """The guard just declared these totals untrustworthy; they must not land."""
        state = self._state_file(monkeypatch, tmp_path)
        _, report = self._storm()

        nrt._handle_integrity_trip("bad run", report, {"total": 40, "failed": 20}, self._prev())

        saved = json.loads(state.read_text())
        assert saved["total"] == 16000
        assert saved["failed"] == 0
        assert saved["failing_tests"] == []

    def test_trip_with_no_report_writes_nothing(
        self, fake_github: FakeGitHub, monkeypatch, tmp_path: Path
    ) -> None:
        state = self._state_file(monkeypatch, tmp_path)
        fake_github.create_hook = lambda title, body: pytest.fail("filed with no report")
        monkeypatch.setattr(nrt, "open_issues", lambda: pytest.fail("read gh with no report"))
        monkeypatch.setattr(
            nrt, "closed_issue_dispositions", lambda: pytest.fail("read gh closed set")
        )
        assert nrt._handle_integrity_trip("no report", None, None, self._prev()) == 1
        assert not state.exists()

    def test_already_filed_storm_is_not_filed_again(
        self, fake_github: FakeGitHub, monkeypatch, tmp_path: Path
    ) -> None:
        """Night after night, the same storm is one issue plus one comment each.

        The twin guard is pinned to ``create_issue`` rather than to the dispatch:
        the dispatch no longer files anything, so watching it would leave the
        actual duplication path unwatched.
        """
        state = self._state_file(monkeypatch, tmp_path)
        fake_github.open_map = {nrt.cascade_title(self.MSG): 4242}
        fake_github.create_hook = lambda title, body: pytest.fail("filed a twin")
        nodes, report = self._storm()
        prev = self._prev() | {"cascade_issues": {self.MSG: 4242}}

        assert nrt._handle_integrity_trip("bad run", report, None, prev) == 1
        assert fake_github.commented == [4242]
        assert json.loads(state.read_text())["dispatched_nodes"] == sorted(nodes)

    def test_dry_run_trip_writes_no_state(
        self, fake_github: FakeGitHub, monkeypatch, tmp_path: Path
    ) -> None:
        state = self._state_file(monkeypatch, tmp_path)
        _, report = self._storm()
        assert nrt._handle_integrity_trip("bad run", report, None, self._prev(), dry_run=True) == 1
        assert not state.exists()

    def test_dry_run_trip_creates_no_issue(
        self, fake_github: FakeGitHub, monkeypatch, tmp_path: Path
    ) -> None:
        """The trip path has its own filing call site and its own dry_run kwarg.

        Before #3418 the flag only had to reach a dispatch; it now has to reach
        ``create_issue``, and a storm is the largest single population the script
        ever files at once.
        """
        self._state_file(monkeypatch, tmp_path)
        _, report = self._storm()
        nrt._handle_integrity_trip("bad run", report, None, self._prev(), dry_run=True)
        assert fake_github.create_dry_runs == [True]


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
        with patch("subprocess.run") as mock_run:
            session_id = nrt.maybe_dispatch_triage_session(
                [(4242, "node tests/unit/test_a.py::test_1")], dry_run=True
            )
        mock_run.assert_not_called()
        assert session_id == nrt.DRY_RUN_SESSION_ID

    def test_dispatch_once(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        issues = [(4242, "node tests/unit/test_a.py::test_1")]
        with patch(
            "subprocess.run", return_value=self._fake_result('{"session_id": "abc123"}')
        ) as mock_run:
            session_id = nrt.maybe_dispatch_triage_session(issues)
        assert session_id == "abc123"
        argv = mock_run.call_args.args[0]
        assert argv[0] == sys.executable
        assert "--role" in argv
        assert "eng" in argv
        assert "--slug" in argv
        slug_idx = argv.index("--slug")
        # The slug is keyed on the ISSUE NUMBERS now, not on node ids: the
        # investigation's subject is the set of issues it was handed.
        expected_slug_hash = hashlib.sha256(b"4242").hexdigest()[:8]
        assert argv[slug_idx + 1] == f"nightly-triage-{expected_slug_hash}"
        assert "--json" in argv

    def test_literal_issue_numbers_in_prompt(self, tmp_path: Path) -> None:
        """Renamed from ``test_literal_titles_in_prompt``.

        A prompt that only INSTRUCTS the agent to look up its issues proves
        nothing (#2559). The detector already created them, so the numbers and
        their subjects must be computed and asserted literally.
        """
        nrt.LOG_FILE = tmp_path / "test.log"
        issues = [
            (11, "node tests/unit/test_a.py::test_1"),
            (22, "node tests/unit/test_b.py::test_2"),
        ]
        with patch(
            "subprocess.run", return_value=self._fake_result('{"session_id": "abc123"}')
        ) as mock_run:
            nrt.maybe_dispatch_triage_session(issues)
        argv = mock_run.call_args.args[0]
        msg = argv[argv.index("--message") + 1]
        for number, subject in issues:
            assert f"#{number}" in msg
            assert subject in msg

    def test_the_dispatch_takes_no_prompt_override(self, tmp_path: Path) -> None:
        """Replaces ``test_prompt_override_replaces_default``.

        The seed path used to hand in its own prompt, which is how two callers
        ended up able to ask an agent to file. There is one prompt builder now
        and the parameter is gone, so passing it must be a hard error rather
        than a silently ignored kwarg.
        """
        nrt.LOG_FILE = tmp_path / "test.log"
        assert "prompt" not in inspect.signature(nrt.maybe_dispatch_triage_session).parameters
        with patch("subprocess.run", return_value=self._fake_result('{"session_id": "abc"}')):
            with pytest.raises(TypeError):
                nrt.maybe_dispatch_triage_session(
                    [(4242, "seed umbrella")], prompt="CUSTOM UMBRELLA PROMPT"
                )

    def test_slug_suffix_override(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch(
            "subprocess.run", return_value=self._fake_result('{"session_id": "abc123"}')
        ) as mock_run:
            nrt.maybe_dispatch_triage_session([(4242, "seed umbrella")], slug_suffix="baseline")
        argv = mock_run.call_args.args[0]
        assert argv[argv.index("--slug") + 1] == "nightly-triage-baseline"

    def test_subprocess_failure_safe(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch("subprocess.run", side_effect=FileNotFoundError("no python")):
            session_id = nrt.maybe_dispatch_triage_session([(11, "node a::t1")])
        assert session_id is None

    def test_session_id_parsed_from_json_stdout(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch("subprocess.run", return_value=self._fake_result('{"session_id": "xyz789"}')):
            session_id = nrt.maybe_dispatch_triage_session([(11, "node a::t1")])
        assert session_id == "xyz789"

    def test_malformed_stdout_returns_none_not_crash(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch("subprocess.run", return_value=self._fake_result("not json")):
            session_id = nrt.maybe_dispatch_triage_session([(11, "node a::t1")])
        assert session_id is None

    def test_empty_stdout_returns_none_not_crash(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch("subprocess.run", return_value=self._fake_result("")):
            session_id = nrt.maybe_dispatch_triage_session([(11, "node a::t1")])
        assert session_id is None

    def test_empty_dispatch_set_no_dispatch(self, tmp_path: Path) -> None:
        nrt.LOG_FILE = tmp_path / "test.log"
        with patch("subprocess.run") as mock_run:
            assert nrt.maybe_dispatch_triage_session([]) is None
            mock_run.assert_not_called()

    def test_the_dispatch_writes_no_ledger_and_takes_no_dispositions(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Replaces the four ledger tests deleted with ``write_triage_ledger``.

        The ledger existed to tell a filing agent which issues it had already
        created across a replayed turn. The detector's own idempotence replaced
        it, so the guarantee worth keeping is the negative one: a dispatch
        writes nothing under DATA_DIR and has no ``dispositions=`` parameter.
        """
        monkeypatch.setattr(nrt, "LOG_FILE", tmp_path / "test.log")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(nrt, "DATA_DIR", data_dir)
        params = inspect.signature(nrt.maybe_dispatch_triage_session).parameters
        assert "dispositions" not in params
        assert not hasattr(nrt, "write_triage_ledger")
        with patch("subprocess.run", return_value=self._fake_result('{"session_id": "abc123"}')):
            assert nrt.maybe_dispatch_triage_session([(11, "node a::t1")]) == "abc123"
        assert list(data_dir.iterdir()) == []


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
        create_return: object = _AUTO_NUMBER,
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
            # Filing is the detector's own subprocess now (#3418), so main()
            # reaches `gh issue create` directly on every non-clean night.
            # Leaving it unpatched is how an informational run of this file
            # opened 94 real issues.
            patch.object(nrt, "create_issue", side_effect=_numbering(create_return)) as mock_create,
            patch.object(nrt, "comment_on_issue", return_value=True),
            patch.object(nrt, "run_ttft_gate", return_value=None),
            patch.object(nrt, "_get_head_commit", return_value="deadbeef"),
        ):
            rc = nrt.main()
        self.mock_create = mock_create
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
        _assert_per_node_dispatch(mock_dispatch, [fresh])
        assert saved["dispatched_nodes"] == sorted([standing, fresh])

    def test_no_dispatch_when_everything_is_already_filed(self, tmp_path: Path) -> None:
        standing = "tests/unit/test_watchdog.py::test_dead_node"
        prev = self._prev(failing_tests=[standing], dispatched_nodes=[standing])
        rc, saved, mock_dispatch, _ = self._run_main(tmp_path, prev, [standing], None)
        assert rc == 0
        mock_dispatch.assert_called_once_with([], dry_run=False)
        assert saved["dispatched_nodes"] == [standing]

    def test_a_failed_dispatch_keeps_the_nodes_filed(self, tmp_path: Path) -> None:
        """Renamed from ``test_failed_dispatch_leaves_nodes_unfiled_for_retry``.

        The dispatch used to BE the filing, so its failure meant nothing had
        been filed. ``create_issue`` has already succeeded by the time the
        session is dispatched (#3418), so retrying the node would open a second
        issue for the same failure — the exact duplication this change exists to
        stop. The node stays recorded; only the session id is left alone so the
        earlier one is not overwritten with nothing.
        """
        node = "tests/unit/test_a.py::test_new"
        prev = self._prev(
            failing_tests=[], dispatched_nodes=[], dispatched_session_id="earlier-session"
        )
        rc, saved, mock_dispatch, _ = self._run_main(tmp_path, prev, [node], None)
        assert rc == 0
        _assert_per_node_dispatch(mock_dispatch, [node])
        assert saved["dispatched_nodes"] == [node]
        assert saved["dispatched_session_id"] == "earlier-session"

    def test_a_failed_create_leaves_the_node_unfiled_for_retry(self, tmp_path: Path) -> None:
        """The retry guarantee, re-pinned to the step that now owns filing.

        A create that returned no number filed nothing, so the node must stay
        out of ``dispatched_nodes`` or the next run suppresses it against an
        issue that does not exist — and with no dispatch either, since there is
        no issue to investigate.
        """
        node = "tests/unit/test_a.py::test_new"
        prev = self._prev(
            failing_tests=[], dispatched_nodes=[], dispatched_session_id="earlier-session"
        )
        rc, saved, mock_dispatch, _ = self._run_main(
            # None matches what the real dispatch returns for an empty issue
            # list, which is what a night with nothing filed hands it.
            tmp_path,
            prev,
            [node],
            None,
            create_return=None,
        )
        assert rc == 0
        assert saved["dispatched_nodes"] == []
        assert saved["dispatched_session_id"] == "earlier-session"
        mock_dispatch.assert_called_once_with([], dry_run=False)

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
            patch.object(nrt, "create_issue", side_effect=_numbering(_AUTO_NUMBER)) as create,
            patch.object(nrt, "comment_on_issue", return_value=True),
            patch.object(nrt, "run_ttft_gate", return_value=None),
            patch.object(nrt, "_get_head_commit", return_value="deadbeef"),
        ):
            rc = nrt.main()
        self.mock_create = create
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
            patch.object(nrt, "create_issue", return_value=nrt.DRY_RUN_ISSUE_NUMBER),
            patch.object(nrt, "comment_on_issue", return_value=True),
            patch.object(nrt, "run_ttft_gate", return_value=None),
            patch.object(nrt, "_get_head_commit", return_value="deadbeef"),
        ):
            rc = nrt.main()

        assert rc == 0
        assert nrt.LAST_RUN_FILE.read_text() == pre_existing, "--dry-run persisted state"

    def test_the_seed_umbrella_is_created_with_the_exact_title(self, tmp_path: Path) -> None:
        """Replaces ``test_seed_prompt_carries_the_exact_umbrella_title``.

        The title used to be asserted through the prompt because an agent was
        the one typing it. main() now hands it to ``create_issue`` itself, so
        the title is asserted at the create and the resulting number is what
        travels to the investigation dispatch.
        """
        standing = ["a::t1", "b::t2"]
        rc, saved, mock_dispatch, _ = self._run_main(tmp_path, {}, standing, "seed-session")
        assert rc == 0
        expected_title = (
            f"Nightly regression baseline: {len(standing)} nodes absorbed on {saved['head_commit']}"
        )
        assert self.mock_create.call_args.args[0] == expected_title
        umbrella_number = self.mock_create.side_effect.numbers[0]
        assert mock_dispatch.call_args.args[0] == [(umbrella_number, expected_title)]

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

    def test_an_unprovable_seed_umbrella_writes_no_baseline(self, tmp_path: Path) -> None:
        """Replaces ``test_failed_seed_dispatch_writes_no_baseline``.

        The evidence gate moved: a failed triage dispatch used to be fatal,
        because the agent was the thing that would have filed. The umbrella now
        exists (or does not) before any session is dispatched, so the fatal
        condition is ``file_seed_umbrella`` returning no NUMBER — deliberately
        keyed on the issue rather than on the session, since a session proves
        nothing about issue existence.

        Recording a baseline anyway would mark every absorbed node as filed
        while no umbrella exists, so compute_dispatch_set() would suppress the
        entire night-one population forever -- behind a Telegram message that
        reads like success. Refusing to save state means the next run re-seeds
        and retries, matching _fatal()'s invariant that no untrusted run
        reaches save_last_run().
        """
        prev = {"collection": ["tests/unit/"], "failing_tests": [], "dispatched_nodes": []}
        pre_existing_bytes = json.dumps(prev)
        nrt.LAST_RUN_FILE = tmp_path / "last_run.json"
        confirmed = ["a::t1", "b::t2", "c::t3"]
        rc, _, mock_dispatch, log_text = self._run_main(
            tmp_path, prev, confirmed, "seed-session", create_return=None
        )

        assert rc == 1
        # The pre-existing state file is byte-identical -- no baseline written.
        assert nrt.LAST_RUN_FILE.read_text() == pre_existing_bytes
        # And the reason is recorded, rather than a success-shaped log line.
        assert "no re-baseline umbrella issue can be proven to exist" in log_text
        # The investigation session is never dispatched against an issue that
        # cannot be proven to exist.
        mock_dispatch.assert_not_called()

    def test_a_failed_seed_dispatch_still_writes_the_baseline(self, tmp_path: Path) -> None:
        """The other half of the moved gate, which had no coverage before.

        The umbrella is proven to exist by the time the dispatch is attempted,
        so a session that fails to spawn costs the run its root-cause narrative
        and nothing else. Treating it as fatal would throw away a baseline whose
        issue is already on the tracker, and the next run would re-seed against
        it and mint nothing but noise.
        """
        confirmed = ["a::t1", "b::t2"]
        rc, saved, _, _ = self._run_main(tmp_path, {}, confirmed, None)
        assert rc == 0
        assert saved["seeded_nodes"] == sorted(confirmed)
        assert saved.get("dispatched_session_id") is None

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
        _assert_per_node_dispatch(mock_dispatch, [node])
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

    @pytest.fixture(autouse=True)
    def _no_filing_on_a_fatal_path(self, monkeypatch: pytest.MonkeyPatch):
        """A fatal exit must reach no tracker write, and must not be able to.

        Every case here trips before dispatch, so an unstubbed ``create_issue``
        would sit here silently until a refactor moved one of these exits past
        the filing step — at which point the test would start opening real
        issues instead of failing. Stubbing it to fail makes that a red test.
        """
        monkeypatch.setattr(
            nrt, "create_issue", lambda *a, **k: pytest.fail("a fatal path filed an issue")
        )
        monkeypatch.setattr(
            nrt, "comment_on_issue", lambda *a, **k: pytest.fail("a fatal path commented")
        )

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
            patch.object(nrt, "create_issue", side_effect=_numbering()),
            patch.object(nrt, "comment_on_issue", return_value=True),
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

    def _dispatch(self, gh: FakeGitHub, *, nodes, report, open_map, closed_map, prev=None):
        """Thin wrapper over the shared harness, kept for this class's argument order."""
        return dispatch_with(
            gh,
            nodes=nodes,
            report=report,
            open_map=open_map,
            closed_map=closed_map,
            prev=prev,
        )

    def test_closed_not_planned_node_gets_a_comment_never_a_refile(
        self, fake_github: FakeGitHub
    ) -> None:
        """The regression test the acceptance criteria demand: only issue is CLOSED."""
        node = "tests/unit/test_dead.py::test_watchdog"
        outcome = self._dispatch(
            fake_github,
            nodes=[node],
            report={"tests": [_body_failed(node, "gw0", "E   AssertionError: dead")]},
            open_map={},
            closed_map={f"Nightly regression: {node}": (2971, "NOT_PLANNED")},
        )
        assert fake_github.create_calls == []
        assert fake_github.commented == [2971]
        assert outcome.recorded == [node]
        assert outcome.issues_filed == 0

    def test_closed_completed_refiles_because_recurrence_is_new_information(
        self, fake_github: FakeGitHub
    ) -> None:
        node = "tests/unit/test_fixed.py::test_regressed_again"
        outcome = self._dispatch(
            fake_github,
            nodes=[node],
            report={"tests": [_body_failed(node, "gw0", "E   AssertionError: back")]},
            open_map={},
            closed_map={f"Nightly regression: {node}": (2500, "COMPLETED")},
        )
        assert fake_github.commented == []
        assert fake_github.created_titles == [f"Nightly regression: {node}"]
        assert outcome.issues_filed == 1

    def test_unknown_close_reason_comments_rather_than_refiling(
        self, fake_github: FakeGitHub
    ) -> None:
        node = "tests/unit/test_x.py::test_y"
        self._dispatch(
            fake_github,
            nodes=[node],
            report={"tests": [_body_failed(node, "gw0", "E   AssertionError: eh")]},
            open_map={},
            closed_map={f"Nightly regression: {node}": (11, "")},
        )
        assert fake_github.create_calls == []
        assert fake_github.commented == [11]

    def test_unreadable_closed_set_fails_open_and_files(self, fake_github: FakeGitHub) -> None:
        node = "tests/unit/test_x.py::test_y"
        self._dispatch(
            fake_github,
            nodes=[node],
            report={"tests": [_body_failed(node, "gw0", "E   AssertionError: eh")]},
            open_map={},
            closed_map=None,
        )
        assert fake_github.commented == []
        assert fake_github.created_titles == [f"Nightly regression: {node}"]

    def test_open_issue_wins_over_closed_record(self, fake_github: FakeGitHub) -> None:
        """An open issue is the live tracker even when a closed twin also matches."""
        node = "tests/unit/test_x.py::test_y"
        title = f"Nightly regression: {node}"
        self._dispatch(
            fake_github,
            nodes=[node],
            report={"tests": [_body_failed(node, "gw0", "E   AssertionError: eh")]},
            open_map={title: 99},
            closed_map={title: (11, "NOT_PLANNED")},
        )
        assert fake_github.create_calls == []
        assert fake_github.commented == [99]

    def test_closed_not_planned_cascade_comments_instead_of_refiling(
        self, fake_github: FakeGitHub
    ) -> None:
        msg = "RuntimeError: registry mismatch client=localhost:6379"
        nodes = [f"tests/unit/test_m.py::test_{i}" for i in range(6)]
        report = {"tests": [_errored(n, "gw2", msg) for n in nodes]}
        normalized = nrt.setup_error_signature(report["tests"][0])[1]
        outcome = self._dispatch(
            fake_github,
            nodes=nodes,
            report=report,
            open_map={},
            closed_map={nrt.cascade_title(normalized): (3131, "NOT_PLANNED")},
        )
        assert fake_github.create_calls == []
        assert fake_github.commented == [3131]
        assert outcome.recorded == sorted(nodes)
        assert normalized not in outcome.cascade_issues

    def test_environmental_nodes_are_excluded_and_reported(self, fake_github: FakeGitHub) -> None:
        env_node = "tests/integration/test_net.py::test_fetch"
        code_node = "tests/unit/test_logic.py::test_math"
        report = {
            "tests": [
                _body_failed(env_node, "gw0", "E   httpx.ConnectError: Connection refused"),
                _body_failed(code_node, "gw1", "E   AssertionError: 3 != 4"),
            ]
        }
        outcome = self._dispatch(
            fake_github,
            nodes=[env_node, code_node],
            report=report,
            open_map={},
            closed_map={},
        )
        assert fake_github.created_titles == [f"Nightly regression: {code_node}"]
        assert outcome.environmental == [env_node]
        assert env_node not in outcome.recorded

    def test_body_group_files_one_umbrella_with_body_namespaced_state(
        self, fake_github: FakeGitHub
    ) -> None:
        line = "E   TypeError: AsyncAnthropic.__init__() got an unexpected keyword"
        nodes = [f"tests/unit/test_llm_{i}.py::test_{i}" for i in range(6)]
        report = {"tests": [_body_failed(n, "gw0", line) for n in nodes]}
        normalized = nrt.body_failure_signature(report["tests"][0])
        outcome = self._dispatch(
            fake_github, nodes=nodes, report=report, open_map={}, closed_map={}
        )
        assert fake_github.commented == []
        title = nrt.body_cascade_title(normalized)
        assert fake_github.created_titles == [title]
        # The body namespace survives into state keyed to the umbrella's real
        # number; a `None` here was the placeholder the agent-filing era needed.
        assert outcome.cascade_issues == {"body::" + normalized: fake_github.open_map[title]}
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

    def _dispatch(self, gh: FakeGitHub, *, nodes, report, open_map, closed_map, prev=None):
        """Thin wrapper over the shared harness, kept for this class's argument order."""
        return dispatch_with(
            gh,
            nodes=nodes,
            report=report,
            open_map=open_map,
            closed_map=closed_map,
            prev=prev,
            run_at="2026-09-05T03:00:00Z",
        )

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
        self, fake_github: FakeGitHub
    ):
        env_node = "tests/integration/test_net.py::test_fetch"
        report = {
            "tests": [_body_failed(env_node, "gw0", "E   httpx.ConnectError: Connection refused")]
        }
        outcome = self._dispatch(
            fake_github,
            nodes=[env_node],
            report=report,
            open_map={},
            closed_map={},
            prev={"environmental_streaks": {env_node: 1}},
        )
        assert fake_github.create_calls == []
        assert fake_github.commented == []
        assert outcome.environmental == [env_node]
        assert outcome.escalated == []
        assert outcome.environmental_streaks == {env_node: 2}
        assert env_node not in outcome.recorded

    def test_environmental_streak_escalates_to_ordinary_filing_on_night_three(
        self, fake_github: FakeGitHub
    ):
        """#3163 gap 1: a persistent environmental-looking failure is a code bug
        until proven otherwise. Night three files it through the normal path."""
        env_node = "tests/integration/test_net.py::test_fetch"
        report = {
            "tests": [_body_failed(env_node, "gw0", "E   httpx.ConnectError: Connection refused")]
        }
        outcome = self._dispatch(
            fake_github,
            nodes=[env_node],
            report=report,
            open_map={},
            closed_map={},
            prev={"environmental_streaks": {env_node: 2}},
        )
        assert fake_github.created_titles == [f"Nightly regression: {env_node}"]
        assert outcome.escalated == [env_node]
        assert outcome.environmental == []
        assert outcome.environmental_streaks == {env_node: 3}
        assert env_node in outcome.recorded

    def test_environmental_streak_resets_when_tonight_is_not_environmental(
        self, fake_github: FakeGitHub
    ):
        env_node = "tests/integration/test_net.py::test_fetch"
        gone_node = "tests/integration/test_net.py::test_gone"
        report = {"tests": [_body_failed(env_node, "gw0", "E   AssertionError: 3 != 4")]}
        outcome = self._dispatch(
            fake_github,
            nodes=[env_node],
            report=report,
            open_map={},
            closed_map={},
            prev={"environmental_streaks": {env_node: 2, gone_node: 5}},
        )
        assert fake_github.created_titles == [f"Nightly regression: {env_node}"]
        assert outcome.environmental_streaks == {}

    def test_environmental_escalation_knob_zero_disables(
        self, fake_github: FakeGitHub, monkeypatch
    ):
        monkeypatch.setenv("NIGHTLY_ENVIRONMENTAL_ESCALATE_NIGHTS", "0")
        env_node = "tests/integration/test_net.py::test_fetch"
        report = {
            "tests": [_body_failed(env_node, "gw0", "E   httpx.ConnectError: Connection refused")]
        }
        outcome = self._dispatch(
            fake_github,
            nodes=[env_node],
            report=report,
            open_map={},
            closed_map={},
            prev={"environmental_streaks": {env_node: 40}},
        )
        assert fake_github.create_calls == []
        assert outcome.environmental == [env_node]
        assert outcome.environmental_streaks == {env_node: 41}

    def test_environmental_node_with_open_issue_gets_one_recurrence_comment(
        self, fake_github: FakeGitHub
    ):
        """#3163 gap 1b: the exclusion used to run before the open-issue
        partition, so a tracked node got no recurrence comment while it looked
        environmental. It now gets one and is recorded, so tomorrow's dispatch
        set suppresses it like any other commented node."""
        env_node = "tests/integration/test_net.py::test_fetch"
        report = {
            "tests": [_body_failed(env_node, "gw0", "E   httpx.ConnectError: Connection refused")]
        }
        outcome = self._dispatch(
            fake_github,
            nodes=[env_node],
            report=report,
            open_map={f"Nightly regression: {env_node}": 777},
            closed_map={},
        )
        assert fake_github.create_calls == []
        assert fake_github.commented == [777]
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

    def test_environmental_setup_storm_files_nothing(self, fake_github: FakeGitHub):
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
        outcome = self._dispatch(
            fake_github, nodes=nodes, report=report, open_map={}, closed_map={}
        )
        assert sorted(outcome.environmental) == sorted(nodes)
        assert fake_github.create_calls == []
        assert fake_github.commented == []
        assert outcome.issues_filed == 0

    def test_end_to_end_replay_dispatch_shapes(self, fake_github: FakeGitHub):
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
        outcome = self._dispatch(
            fake_github,
            nodes=nodes,
            report=report,
            open_map={f"Nightly regression: {node_open}": 500},
            closed_map={
                f"Nightly regression: {node_np}": (501, "NOT_PLANNED"),
                f"Nightly regression: {node_done}": (502, "COMPLETED"),
            },
        )
        assert sorted(outcome.environmental) == sorted(env)
        # Three creates, and the umbrellas are now identified by their real
        # titles rather than by a `cascade:` pseudo-node in a dispatch list.
        created = fake_github.created_titles
        assert len(created) == 3
        assert len(outcome.cascade_issues) == 2
        assert f"Nightly regression: {node_done}" in created
        assert sorted(fake_github.commented) == [500, 501]
        assert outcome.issues_filed == 3
        assert outcome.comments_posted == 2
        # One investigation dispatch for the whole night, carrying every number
        # the detector actually created.
        dispatched, _ = fake_github.dispatches[-1]
        assert sorted(n for n, _ in dispatched) == sorted(outcome.filed_issues.values())

    def test_epilogue_is_single_sourced(self):
        node_comment = nrt.closed_recurrence_comment(
            "a::t", "NOT_PLANNED", run_at="R", head_commit="H"
        )
        assert nrt.closed_epilogue("NOT_PLANNED") in node_comment


class TestPromptsNeverNameTheSearchIndex:
    """The one surviving prompt hands over a REST command (#3170 fix 1).

    The module's own reads were moved off GitHub's index-backed lookup by
    ``8524e765b`` and are held there by
    ``test_open_issues_uses_the_rest_list_not_the_lagging_search``. This is the
    same contract one layer out, for the read the module tells an *agent* to
    make -- the layer that stayed unfixed through four passes and produced the
    #2960-#2999 duplicate wave. The two tests are one contract; a prompt that
    names the index is the same defect as a script that queries it.

    The parametrization over three prompts is gone with the two prompts: the
    detector files every issue itself now, so there is one prompt and no way for
    three copies of this instruction to drift apart (#3418).
    """

    ISSUES = [(4242, "node tests/unit/test_a.py::test_1")]

    def _prompt(self) -> str:
        return nrt._build_investigation_prompt(self.ISSUES)

    def test_prompt_hands_over_the_rest_command(self) -> None:
        assert (
            "gh issue list --state all --json number,title,state,stateReason --limit 200"
            in self._prompt()
        )

    def test_prompt_carries_the_prohibition_and_its_reason(self) -> None:
        prompt = self._prompt()
        assert "search index" in prompt
        assert "#2960-#2999" in prompt

    def test_prompt_warns_that_statereason_is_empty_on_open_rows(self) -> None:
        prompt = self._prompt()
        assert "stateReason" in prompt
        assert "empty string on OPEN rows" in prompt

    def test_prompt_never_names_the_index_backed_lookup(self) -> None:
        assert SEARCH_TOKENS.findall(self._prompt()) == []

    def test_the_lookup_instruction_is_still_read_from_the_one_constant(self) -> None:
        """Replaces ``test_all_three_share_one_constant``.

        Three readers cannot drift once there is one reader, but an inlined copy
        still could, so the prompt must be composed from the constant.
        """
        assert nrt.ISSUE_LOOKUP_INSTRUCTION in self._prompt()

    def test_the_prompt_asks_for_no_issue_creation(self) -> None:
        """The sibling the search-index contract always needed.

        Every assertion above is about how the agent READS the tracker. The
        #3418 incident was about what it WROTE: an instruction to file is how 8
        findings became 24 issues, because an LLM turn can be replayed. The
        prompt must therefore carry no creation instruction at all, for an empty
        issue list as much as a populated one.
        """
        for issues in ([], self.ISSUES, [(1, "cascade umbrella 'x' (9 node(s))")]):
            prompt = nrt._build_investigation_prompt(issues)
            assert CREATE_INSTRUCTION_TOKENS.findall(prompt) == []


class TestSeedUmbrellaIsCreatedByTheDetector:
    """Replaces ``TestBuildSeedPrompt``: the seed umbrella is a create, not a prompt.

    The seed path used to render its own prompt telling an agent to search for
    an exact title and file the umbrella only if nothing matched. The stricter
    rule that prompt carried in prose -- a CLOSED umbrella is commented on and
    never re-filed, whatever the ``state_reason`` -- is now executable code in
    :func:`file_seed_umbrella`, so it is asserted as behaviour rather than as
    wording (#3418).
    """

    TITLE = "Nightly regression baseline: 2 nodes absorbed on abc1234"

    def _file(self, gh: FakeGitHub, *, dry_run: bool = False):
        return nrt.file_seed_umbrella(
            self.TITLE,
            body="the seed body",
            recurrence_body="the recurrence body",
            dry_run=dry_run,
        )

    def test_no_existing_umbrella_creates_exactly_one(self, fake_github: FakeGitHub) -> None:
        number = self._file(fake_github)
        assert fake_github.created_titles == [self.TITLE]
        assert fake_github.body_for(self.TITLE) == "the seed body"
        assert number == fake_github.open_map[self.TITLE]
        assert fake_github.commented == []

    def test_an_open_umbrella_is_commented_on_and_not_re_filed(
        self, fake_github: FakeGitHub
    ) -> None:
        fake_github.open_map = {self.TITLE: 3300}
        assert self._file(fake_github) == 3300
        assert fake_github.create_calls == []
        assert fake_github.comment_calls == [(3300, "the recurrence body")]

    @pytest.mark.parametrize("reason", ["COMPLETED", "NOT_PLANNED", ""])
    def test_a_closed_umbrella_is_never_re_filed_whatever_the_reason(
        self, fake_github: FakeGitHub, reason: str
    ) -> None:
        """The seed's rule is deliberately stricter than the per-node one.

        ``COMPLETED`` is the case that matters and the one a reader expects to
        behave like :func:`partition_closed_matches`, which re-files it because a
        recurrence after a fix is new information. A seed is not a failure report
        but a declaration of a baseline, so a re-baseline retry at the same commit
        must not mint a twin umbrella -- it comments and returns the closed
        number.
        """
        fake_github.open_map = {}
        fake_github.closed_map = {self.TITLE: (2900, reason)}
        assert self._file(fake_github) == 2900
        assert fake_github.create_calls == []
        assert fake_github.commented == [2900]
        body = fake_github.comment_calls[0][1]
        assert body.startswith("the recurrence body")
        assert nrt.closed_epilogue(reason) in body
        assert "NOT re-filing, whatever the close reason" in log_text()

    def test_a_closed_umbrella_whose_comment_failed_proves_nothing(
        self, fake_github: FakeGitHub
    ) -> None:
        """No number means main() refuses the baseline, and that is the point.

        A recurrence that could not be written down has not been reported, so
        the run has no trustworthy record either. Returning the number anyway
        would let main() persist ``seeded_nodes`` -- a sticky set -- against an
        umbrella nobody was told recurred.
        """
        fake_github.open_map = {}
        fake_github.closed_map = {self.TITLE: (2900, "COMPLETED")}
        fake_github.comment_hook = lambda number, body: False
        assert self._file(fake_github) is None
        assert fake_github.create_calls == []

    def test_an_open_umbrella_whose_comment_failed_proves_nothing(
        self, fake_github: FakeGitHub
    ) -> None:
        fake_github.open_map = {self.TITLE: 3300}
        fake_github.comment_hook = lambda number, body: False
        assert self._file(fake_github) is None
        assert fake_github.create_calls == []

    def test_both_reads_unreadable_creates_anyway_and_says_so(
        self, fake_github: FakeGitHub
    ) -> None:
        """Fail open, bounded at one twin on a night GitHub was unreadable.

        Refusing the baseline instead would risk losing a whole night-one
        population, so the cost is paid deliberately -- and logged, because this
        is the one seed path that can mint a duplicate umbrella.
        """
        fake_github.open_map = None
        fake_github.closed_map = None
        number = self._file(fake_github)
        assert fake_github.created_titles == [self.TITLE]
        assert number is not None
        assert "Seed dedup ran blind" in log_text()

    def test_one_unreadable_read_still_lets_the_other_decide(self, fake_github: FakeGitHub) -> None:
        """A read that returned ``None`` contributes no answer, it does not veto one.

        With the open map unreadable and a closed match present, the closed rule
        must still fire -- and without the blind-dedup log line, which would be a
        false claim that neither map could answer.
        """
        fake_github.open_map = None
        fake_github.closed_map = {self.TITLE: (2900, "NOT_PLANNED")}
        assert self._file(fake_github) == 2900
        assert fake_github.create_calls == []
        assert "Seed dedup ran blind" not in log_text()

    def test_dry_run_propagates_to_the_create(self, fake_github: FakeGitHub) -> None:
        self._file(fake_github, dry_run=True)
        assert fake_github.create_dry_runs == [True]

    def test_the_seed_reads_both_maps_itself(self, fake_github: FakeGitHub) -> None:
        """It lives outside ``dispatch_findings``, so neither opening read is in scope.

        The per-create refresh would not help either: it reads open issues only
        and is structurally blind to a closed umbrella, which is the case the
        seed's stricter rule exists for.
        """
        self._file(fake_github)
        assert (fake_github.open_reads, fake_github.closed_reads) == (1, 1)
