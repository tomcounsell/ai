"""Unit tests for tools.process_lookup (#3164).

macOS ships BSD ``pgrep``, which excludes the calling process **and all of its
ancestors** from the match list. Agent sessions run as ``claude -p`` children of
the bridge, so ``pgrep -f telegram_bridge.py`` executed inside a session cannot
see the bridge: ``scripts/update/service.py::get_bridge_pid`` returned ``None``
and release verify classified a healthy, freshly-beaconed bridge as ``unknown``.

``tools.process_lookup`` reads the whole process table with
``ps -axww -o pid=,args=`` (``ps`` has no ancestor filter) and matches the
**tokenised** argv rather than a substring. These tests pin both halves of that
contract:

* the matcher, driven entirely by synthetic ``ps`` output so the assertions are
  host-independent — every realistic launch shape resolves, and every decoy that
  merely *mentions* a service path does not;
* the failure paths, all of which must degrade to ``[]`` rather than raise or
  guess a wrong PID, so callers land in their existing "not running" branch.

Two live tests at the bottom exercise the real host process table. Both skip
cleanly when the bridge is absent or when the test process is not one of its
descendants, so they never fail on a machine that simply is not running the
service.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from scripts.update import service as update_service
from tools import process_lookup

# The launchd-spawned bridge: the framework Python binary (capital-P basename,
# the case the old `pgrep -fi` needed `-i` for) followed by the script path.
FRAMEWORK_PYTHON = (
    "/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework"
    "/Versions/3.14/Resources/Python.app/Contents/MacOS/Python"
)
BRIDGE_SCRIPT = "/Users/valorengels/src/ai/bridge/telegram_bridge.py"
BRIDGE_SUFFIX = "bridge/telegram_bridge.py"
WORKER_SUFFIX = "worker/__main__.py"


class _FakeCompleted:
    """Stand-in for ``subprocess.CompletedProcess`` with just what ``ps`` needs."""

    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


def fake_ps(monkeypatch, lines, *, returncode: int = 0) -> list[list[str]]:
    """Point ``tools.process_lookup``'s ``ps`` at synthetic output.

    ``lines`` are emitted verbatim, so a test can inject the leading whitespace
    ``ps`` uses for right-aligned PIDs, blank lines, or outright garbage.
    Returns the list that records each command ``subprocess.run`` was handed.
    """
    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompleted(returncode, "".join(f"{line}\n" for line in lines))

    monkeypatch.setattr(process_lookup.subprocess, "run", _run)
    return calls


# ---------------------------------------------------------------------------
# list_processes: parsing
# ---------------------------------------------------------------------------


def test_list_processes_parses_pid_and_argv_tokens(monkeypatch):
    """A multi-token command line becomes (pid, argv) with argv split on whitespace."""
    calls = fake_ps(monkeypatch, [f"  94804 {FRAMEWORK_PYTHON} {BRIDGE_SCRIPT} --verbose"])

    assert process_lookup.list_processes() == [
        (94804, [FRAMEWORK_PYTHON, BRIDGE_SCRIPT, "--verbose"]),
    ]
    # The exact ps invocation is part of the contract: `-axww -o pid=,args=` is what
    # yields every process (including ancestors) with no header line.
    assert calls == [["ps", "-axww", "-o", "pid=,args="]]


def test_list_processes_skips_unparseable_lines(monkeypatch):
    """Garbage, blank, and command-less lines are dropped; valid lines survive."""
    fake_ps(
        monkeypatch,
        [
            "not-a-pid some junk",
            "",
            "   ",
            "12345",
            f"  94804 {FRAMEWORK_PYTHON} {BRIDGE_SCRIPT}",
        ],
    )

    assert process_lookup.list_processes() == [
        (94804, [FRAMEWORK_PYTHON, BRIDGE_SCRIPT]),
    ]


# ---------------------------------------------------------------------------
# find_python_service_pids: the launch shapes that must resolve
# ---------------------------------------------------------------------------


def test_launchd_framework_python_bridge_resolves(monkeypatch):
    """The reported bug's process: `.../MacOS/Python /abs/.../bridge/telegram_bridge.py`."""
    fake_ps(monkeypatch, [f"  94804 {FRAMEWORK_PYTHON} {BRIDGE_SCRIPT}"])

    assert process_lookup.find_python_service_pids(script_suffix=BRIDGE_SUFFIX) == [94804]


def test_module_launch_shape_resolves(monkeypatch):
    """`python -m worker` — how launchd starts the worker."""
    fake_ps(monkeypatch, [f"  94409 {FRAMEWORK_PYTHON} -m worker"])

    assert process_lookup.find_python_service_pids(module="worker") == [94409]


def test_script_launch_shape_resolves(monkeypatch):
    """`python /abs/path/worker/__main__.py` — how a direct start looks."""
    fake_ps(
        monkeypatch,
        [
            "  77001 /Users/valorengels/src/ai/.venv/bin/python3 "
            "/Users/valorengels/src/ai/worker/__main__.py"
        ],
    )

    assert process_lookup.find_python_service_pids(script_suffix=WORKER_SUFFIX) == [77001]


def test_both_selectors_are_an_or_without_double_counting(monkeypatch):
    """Supplying module AND script_suffix matches either shape, once per PID."""
    fake_ps(
        monkeypatch,
        [
            f"  100 {FRAMEWORK_PYTHON} -m worker",
            "  200 /usr/local/bin/python3 /Users/valorengels/src/ai/worker/__main__.py",
            # Satisfies BOTH selectors; must still appear exactly once.
            "  300 /usr/local/bin/python3 -m worker /Users/valorengels/src/ai/worker/__main__.py",
        ],
    )

    assert process_lookup.find_python_service_pids(
        module="worker", script_suffix=WORKER_SUFFIX
    ) == [100, 200, 300]


def test_multiple_matches_returned_ascending(monkeypatch):
    """PIDs sort ascending even when ps emits them out of order (callers take [0])."""
    fake_ps(
        monkeypatch,
        [
            f"  94804 {FRAMEWORK_PYTHON} {BRIDGE_SCRIPT}",
            f"    311 /usr/bin/python3 {BRIDGE_SCRIPT}",
            f"  10250 /usr/local/bin/python3.14 {BRIDGE_SCRIPT}",
        ],
    )

    assert process_lookup.find_python_service_pids(script_suffix=BRIDGE_SUFFIX) == [
        311,
        10250,
        94804,
    ]


def test_garbage_line_beside_valid_line_still_resolves(monkeypatch):
    """An unparseable line never suppresses a real match on a later line."""
    fake_ps(
        monkeypatch,
        [
            "not-a-pid some junk",
            "",
            "12345",
            f"  94804 {FRAMEWORK_PYTHON} {BRIDGE_SCRIPT}",
        ],
    )

    assert process_lookup.find_python_service_pids(script_suffix=BRIDGE_SUFFIX) == [94804]


# ---------------------------------------------------------------------------
# find_python_service_pids: the decoys tokenised matching must reject
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "why"),
    [
        (
            "  55001 /bin/zsh -c cd /Users/valorengels/src/ai && python bridge/telegram_bridge.py",
            "argv[0] is a shell, not a Python interpreter",
        ),
        (
            "  55002 grep telegram_bridge.py",
            "the classic `ps | grep` false positive: argv[0] is grep",
        ),
        (
            "  55003 /usr/bin/python3 -c import x;print('bridge/telegram_bridge.py')",
            "the token after -c is source code, not a path, and is skipped",
        ),
        (
            "  55004 /Users/valorengels/venvs/bridge/telegram_bridge.py/bin/python3 -m http.server",
            "telegram_bridge.py appears only inside argv[0]'s own path",
        ),
    ],
)
def test_bridge_decoys_are_not_matched(monkeypatch, line, why):
    fake_ps(monkeypatch, [line])

    assert process_lookup.find_python_service_pids(script_suffix=BRIDGE_SUFFIX) == [], why


def test_argv0_is_excluded_from_script_suffix_matching(monkeypatch):
    """A suffix that matches the interpreter path itself never matches.

    The bridge line's argv[0] ends in ``Contents/MacOS/Python``; because argv[0]
    is excluded from the script-suffix scan, that suffix resolves to nothing.
    """
    fake_ps(monkeypatch, [f"  94804 {FRAMEWORK_PYTHON} {BRIDGE_SCRIPT}"])

    assert process_lookup.find_python_service_pids(script_suffix="Contents/MacOS/Python") == []


def test_module_match_is_token_equality_not_prefix(monkeypatch):
    """`python -m workerfoo` does not satisfy module="worker"."""
    fake_ps(monkeypatch, [f"  55005 {FRAMEWORK_PYTHON} -m workerfoo"])

    assert process_lookup.find_python_service_pids(module="worker") == []


# ---------------------------------------------------------------------------
# Failure paths: always [], never an exception, never a guessed PID
# ---------------------------------------------------------------------------


def test_ps_nonzero_exit_returns_empty(monkeypatch):
    fake_ps(monkeypatch, [f"  94804 {FRAMEWORK_PYTHON} {BRIDGE_SCRIPT}"], returncode=1)

    assert process_lookup.list_processes() == []
    assert process_lookup.find_python_service_pids(script_suffix=BRIDGE_SUFFIX) == []


def test_empty_process_table_returns_empty(monkeypatch):
    fake_ps(monkeypatch, [])

    assert process_lookup.list_processes() == []
    assert process_lookup.find_python_service_pids(script_suffix=BRIDGE_SUFFIX) == []


@pytest.mark.parametrize(
    "error",
    [
        OSError("ps: cannot fork"),
        subprocess.TimeoutExpired(cmd=["ps", "-axo", "pid=,args="], timeout=10),
    ],
    ids=["oserror", "timeout"],
)
def test_ps_raising_returns_empty(monkeypatch, error):
    """A wedged or unavailable `ps` degrades to "no match", never propagates."""

    def _raise(cmd, **kwargs):
        raise error

    monkeypatch.setattr(process_lookup.subprocess, "run", _raise)

    assert process_lookup.list_processes() == []
    assert process_lookup.find_python_service_pids(module="worker") == []


def test_no_selector_raises_value_error():
    """Calling with neither selector is a programming error, not a silent [] ."""
    with pytest.raises(ValueError):
        process_lookup.find_python_service_pids()


# ---------------------------------------------------------------------------
# Caller contract in scripts/update/service.py
# ---------------------------------------------------------------------------


def test_get_bridge_pid_returns_none_when_lookup_finds_nothing(monkeypatch):
    """No PID must stay `None` — never a wrong PID — so verify_release says "unknown"."""
    monkeypatch.setattr(update_service, "find_python_service_pids", lambda **kwargs: [])

    assert update_service.get_bridge_pid() is None


def test_get_bridge_pid_takes_the_first_pid(monkeypatch):
    """The caller takes ``pids[0]``; the helper has already sorted ascending."""
    monkeypatch.setattr(update_service, "find_python_service_pids", lambda **kwargs: [100, 300])

    assert update_service.get_bridge_pid() == 100


# ---------------------------------------------------------------------------
# Live tests against the real process table (skip when the bridge is absent)
# ---------------------------------------------------------------------------


def _ancestor_pids() -> set[int]:
    """PIDs of this process's ancestors, walked with ``ps -o ppid=``."""
    chain: set[int] = set()
    pid = os.getppid()
    while pid > 1 and len(chain) < 64:
        chain.add(pid)
        try:
            result = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            break
        parent = result.stdout.strip()
        if result.returncode != 0 or not parent.isdigit():
            break
        pid = int(parent)
    return chain


@pytest.mark.skipif(sys.platform != "darwin", reason="BSD pgrep ancestor exclusion is macOS-only")
def test_lookup_sees_an_ancestor_bridge_that_pgrep_hides():
    """The #3164 regression, asserted against the live process table.

    When this test runs inside a bridge-hosted agent session the bridge is an
    ancestor of the test process. ``ps``-based lookup finds it; BSD ``pgrep``
    is documented to exclude the caller and its ancestors, so it does not. That
    divergence is the whole bug, so it is pinned by assertion rather than prose.

    Skips when the bridge is not running, or when the runner is not one of its
    descendants — there is nothing to compare in either case.
    """
    pids = process_lookup.find_python_service_pids(script_suffix=BRIDGE_SUFFIX)
    if not pids:
        pytest.skip("bridge is not running on this host")

    ancestors = _ancestor_pids()
    ancestor_bridge_pids = [pid for pid in pids if pid in ancestors]
    if not ancestor_bridge_pids:
        pytest.skip("test process is not a descendant of the bridge")

    result = subprocess.run(
        ["pgrep", "-f", "telegram_bridge.py"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    pgrep_pids = {int(token) for token in result.stdout.split() if token.isdigit()}

    for pid in ancestor_bridge_pids:
        assert pid not in pgrep_pids, (
            f"pgrep reported ancestor bridge pid {pid}; BSD ancestor exclusion no longer "
            "holds, so this test's premise (and #3164's diagnosis) needs re-checking"
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="BSD pgrep ancestor exclusion is macOS-only")
def test_get_bridge_pid_resolves_the_live_bridge():
    """End-to-end: the caller returns the same live PID the helper found."""
    pids = process_lookup.find_python_service_pids(script_suffix=BRIDGE_SUFFIX)
    if not pids:
        pytest.skip("bridge is not running on this host")

    assert update_service.get_bridge_pid() == pids[0]
