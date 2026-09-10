"""Unit tests for tools.process_lookup (#3164).

macOS ships BSD ``pgrep``, which excludes the calling process **and all of its
ancestors** from the match list. Agent sessions run as ``claude -p`` children of
the bridge, so ``pgrep -f telegram_bridge.py`` executed inside a session cannot
see the bridge: ``scripts/update/service.py::get_bridge_pid`` returned ``None``
and release verify classified a healthy, freshly-beaconed bridge as ``unknown``.

``tools.process_lookup`` reads the whole process table with
``ps -axww -o pid=,args=`` (``ps`` has no ancestor filter) and parses each
command line as a **CPython invocation** — the interpreter's own ``-m`` module
or its own script position — rather than scanning the argv for a token that
looks right. These tests pin every half of that contract:

* the CLI grammar in ``_parse_python_invocation``, both directly and through the
  public matcher: every realistic launch shape resolves, and every decoy that
  merely *mentions* a service path or a ``-m`` pair as a **program argument**
  does not;
* ``is_own_ancestor``, the guard restart/signal callers must gate on now that an
  ancestor-safe lookup can hand them the PID of their own parent service;
* the failure paths, all of which must degrade to ``[]`` / ``False`` rather than
  raise or guess a wrong PID, so callers land in their existing "not running"
  branch.

Two live tests exercise the real host process table against the bridge. Both
skip cleanly when the bridge is absent or when the test process is not one of
its descendants, so they never fail on a machine that simply is not running the
service.

The final section covers the one-shot migration guards in
``scripts/migrate_session_type_pm_to_eng.py`` and
``scripts/merge_dev_chat_into_eng.py`` (#3187), whose ``pgrep`` probes failed in
the dangerous direction: a live worker read as stopped and the migration
proceeded. Two topologies are covered, and the distinction matters:

* a decoy spawned as a **child**, which pins launch-shape and liveness-recheck
  coverage and runs on every machine — but which BSD ``pgrep`` would also have
  found, so it does not exercise the defect;
* a decoy the guard runs as a **descendant** of, which is where the defect lives
  and is the only shape that reproduces it. macOS-only, since the ancestor
  exclusion being closed is a BSD ``pgrep`` behavior.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

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

# This checkout, pinned onto the probe subprocess's sys.path so the guard under
# test is the worktree's copy rather than whatever the ambient PATH resolves.
PROJECT_ROOT = Path(__file__).parent.parent.parent


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


def test_relative_script_path_matches_by_equality(monkeypatch):
    """A relative start (`python bridge/telegram_bridge.py` from the repo root).

    Exercises the ``script == script_suffix`` equality branch rather than the
    ``endswith("/" + suffix)`` one: there is no leading directory to strip, so a
    suffix-only match would miss the repo's own documented manual start.
    """
    fake_ps(monkeypatch, [f"  61001 /Users/valorengels/src/ai/.venv/bin/python3 {BRIDGE_SUFFIX}"])

    assert process_lookup.find_python_service_pids(script_suffix=BRIDGE_SUFFIX) == [61001]


@pytest.mark.parametrize(
    ("line", "why"),
    [
        (
            "  62001 /usr/bin/python3 -u -m worker",
            "a simple short flag before -m is skipped, not read as the script",
        ),
        (
            "  62002 /usr/bin/python3 -Esu -m worker",
            "a bundled short-flag cluster is one token and is skipped whole",
        ),
        (
            "  62003 /usr/bin/python3 -W ignore -m worker",
            "-W consumes its value, so `ignore` is not mistaken for the script",
        ),
        (
            "  62004 /usr/bin/python3 -X importtime -m worker",
            "-X consumes its value, so `importtime` is not mistaken for the script",
        ),
        (
            "  62005 /usr/bin/python3 --check-hash-based-pycs always -m worker",
            "the long value-option consumes its value too",
        ),
    ],
)
def test_interpreter_options_before_dash_m_still_resolve(monkeypatch, line, why):
    """Options preceding ``-m`` must not hide the module (false NEGATIVE guard)."""
    fake_ps(monkeypatch, [line])

    pid = int(line.split()[0])
    assert process_lookup.find_python_service_pids(module="worker") == [pid], why


def test_clustered_short_flag_module_launch_resolves(monkeypatch):
    """`python -um worker` — a cluster ending in `m` must still resolve the module.

    Skipping the cluster whole reads `worker` as a *script path*, so both
    selectors miss and the lookup returns nothing (a silent false negative).
    """
    fake_ps(monkeypatch, ["  62007 /usr/bin/python3 -um worker"])

    assert process_lookup.find_python_service_pids(module="worker") == [62007]
    assert process_lookup.find_python_service_pids(script_suffix=WORKER_SUFFIX) == []


def test_value_option_before_a_script_path_still_resolves(monkeypatch):
    """`python -X importtime /abs/.../worker/__main__.py` resolves via script_suffix."""
    fake_ps(
        monkeypatch,
        ["  62006 /usr/bin/python3 -X importtime /Users/valorengels/src/ai/worker/__main__.py"],
    )

    assert process_lookup.find_python_service_pids(script_suffix=WORKER_SUFFIX) == [62006]


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
            "-c terminates option parsing: the payload is source, not a script path",
        ),
        (
            "  55006 /usr/bin/python3 -m ruff check bridge/telegram_bridge.py",
            "the bridge path is ruff's argument; the module being run is `ruff`",
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


def test_attached_module_form_resolves(monkeypatch):
    """`python -mworker` — the attached form CPython accepts with no space.

    It used to fall into the generic short-flag branch and parse as
    ``(None, None)``. A false negative here is what drives ``kickstart -k``
    escalation against a perfectly healthy worker (the #1331 failure mode).
    """
    fake_ps(monkeypatch, [f"  94410 {FRAMEWORK_PYTHON} -mworker"])

    assert process_lookup.find_python_service_pids(module="worker") == [94410]


def test_module_match_is_token_equality_not_prefix(monkeypatch):
    """`python -m workerfoo` does not satisfy module="worker"."""
    fake_ps(monkeypatch, [f"  55005 {FRAMEWORK_PYTHON} -m workerfoo"])

    assert process_lookup.find_python_service_pids(module="worker") == []


@pytest.mark.parametrize(
    ("line", "why"),
    [
        (
            "  56001 /usr/bin/python3 -m pytest tests/unit/x.py --foo worker/__main__.py",
            "worker/__main__.py is pytest's argument, not the interpreter's script",
        ),
        (
            # The one shape the old `pgrep -f "python -m worker"` got RIGHT and a
            # naive whole-argv scan got wrong: `-m worker` here is pytest's marker
            # expression, and the two tokens are not even adjacent to `python`.
            "  56002 /usr/bin/python3 -m pytest tests/ -m worker",
            "the second -m is pytest's marker expression; the module run is `pytest`",
        ),
        (
            "  56003 /usr/bin/python3 -m pytest -m worker and not slow tests/unit",
            "a multi-token marker expression is still pytest's argument",
        ),
    ],
)
def test_pytest_arguments_are_not_the_worker(monkeypatch, line, why):
    """A test run that merely *mentions* the worker must never read as the worker.

    Every one of these is a live process on this machine during a test sweep;
    matching one would let a caller restart a healthy worker — or, worse, decide
    a dead one is alive.
    """
    fake_ps(monkeypatch, [line])

    assert (
        process_lookup.find_python_service_pids(module="worker", script_suffix=WORKER_SUFFIX) == []
    ), why


@pytest.mark.parametrize(
    ("line", "why"),
    [
        (
            "  57001 /usr/bin/python3 -c from worker import main; main()",
            "a -c payload naming the module is source code, not `python -m worker`",
        ),
        (
            # `python -c 'print(sys.argv)' -m worker` is a legal command line: -c
            # ends option parsing, so `-m worker` is the *program's* own argv.
            "  57002 /usr/bin/python3 -c print(sys.argv) -m worker",
            "-c truncates the parse; a later -m pair belongs to the program",
        ),
    ],
)
def test_dash_c_truncates_against_a_module_selector(monkeypatch, line, why):
    fake_ps(monkeypatch, [line])

    assert process_lookup.find_python_service_pids(module="worker") == [], why


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("  58001 /usr/bin/python3", "a bare REPL has no module and no script"),
        ("  58002 /usr/bin/python3 -", "`-` reads the program from stdin"),
        ("  58003 /usr/bin/python3 -m", "a dangling -m has no module token to read"),
        ("  58004 /usr/bin/python3 -u -I", "options only, never a module or script"),
    ],
)
def test_argv_without_a_program_matches_nothing_and_does_not_raise(monkeypatch, line, why):
    """Truncated argvs are an index-guard hazard; they must yield [], never raise."""
    fake_ps(monkeypatch, [line])

    assert (
        process_lookup.find_python_service_pids(module="worker", script_suffix=WORKER_SUFFIX) == []
    ), why


# ---------------------------------------------------------------------------
# _parse_python_invocation: the CPython CLI grammar, asserted directly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        # -- the module form -------------------------------------------------
        (["python", "-m", "worker"], ("worker", None)),
        (["python", "-u", "-m", "worker"], ("worker", None)),
        (["python", "-Esu", "-m", "worker"], ("worker", None)),
        (["python", "-W", "ignore", "-m", "worker"], ("worker", None)),
        (["python", "-X", "importtime", "-m", "worker"], ("worker", None)),
        (["python", "--check-hash-based-pycs", "always", "-m", "worker"], ("worker", None)),
        # `-m` ends option parsing: pytest's own `-m worker` marker never wins.
        (["python", "-m", "pytest", "tests/", "-m", "worker"], ("pytest", None)),
        (["python", "-m", "ruff", "check", "bridge/telegram_bridge.py"], ("ruff", None)),
        # CPython accepts the value attached to the flag, with no space.
        (["python", "-mworker"], ("worker", None)),
        (["python", "-u", "-mworker"], ("worker", None)),
        # -- the script form -------------------------------------------------
        (["python", "worker/__main__.py"], (None, "worker/__main__.py")),
        (["python", "-u", "/abs/worker/__main__.py"], (None, "/abs/worker/__main__.py")),
        (
            ["python", "-X", "importtime", "/abs/worker/__main__.py"],
            (None, "/abs/worker/__main__.py"),
        ),
        # A script's own arguments never re-open option parsing.
        (["python", "script.py", "-m", "worker"], (None, "script.py")),
        # An option that takes a value consumes the NEXT token unconditionally,
        # exactly as CPython does — here `-m` is -W's filter, and `worker` the script.
        (["python", "-W", "-m", "worker"], (None, "worker")),
        # -- no program at all ------------------------------------------------
        (["python"], (None, None)),
        (["python", "-"], (None, None)),
        (["python", "-m"], (None, None)),
        (["python", "-u", "-I"], (None, None)),
        (["python", "-c", "import", "worker"], (None, None)),
        (["python", "-c", "print(sys.argv)", "-m", "worker"], (None, None)),
        # The attached `-c` form ends option parsing just like the separated one.
        (["python", "-cprint(1)"], (None, None)),
        (["python", "-cimport worker", "-m", "worker"], (None, None)),
        # `--check-hash-based-pycs` also begins with `-c`: it must keep taking a
        # value token, not be read as an attached `-c` payload.
        (["python", "--check-hash-based-pycs", "always", "-mworker"], ("worker", None)),
        # -- bundled short-flag clusters ---------------------------------------
        # A cluster whose LAST letter is a terminator or a value option cannot be
        # skipped whole: `-um worker` would otherwise report `worker` as a script
        # path. All of these are legal CPython (verified against the interpreter).
        (["python", "-um", "worker"], ("worker", None)),
        (["python", "-uEm", "platform"], ("platform", None)),
        (["python", "-umworker"], ("worker", None)),
        (["python", "-uc", "print(1)"], (None, None)),
        # `W` inside a cluster takes the rest of the cluster as its value, or the
        # next argv token when the cluster ends at the flag.
        (["python", "-uW", "ignore", "-m", "worker"], ("worker", None)),
        (["python", "-uWignore", "-m", "worker"], ("worker", None)),
        (["python", "-uW", "ignore", "/abs/worker/__main__.py"], (None, "/abs/worker/__main__.py")),
    ],
)
def test_parse_python_invocation(argv, expected):
    """At most one of (module, script) is ever non-None, per the CPython grammar."""
    parsed = process_lookup._parse_python_invocation(argv)

    assert parsed == expected
    assert None in parsed, "module and script are mutually exclusive"


# ---------------------------------------------------------------------------
# _parent_pid / is_own_ancestor: the guard restart paths must gate on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lines", "returncode", "expected"),
    [
        (["  4242"], 0, 4242),
        (["  4242"], 1, None),  # `ps -p` on a dead pid
        (["not-a-number"], 0, None),
        ([], 0, None),  # empty stdout
    ],
    ids=["ppid", "nonzero-exit", "garbage", "empty"],
)
def test_parent_pid_reads_ppid_or_degrades_to_none(monkeypatch, lines, returncode, expected):
    fake_ps(monkeypatch, lines, returncode=returncode)

    assert process_lookup._parent_pid(1234) == expected


def test_parent_pid_returns_none_when_ps_raises(monkeypatch):
    def _raise(cmd, **kwargs):
        raise OSError("ps: cannot fork")

    monkeypatch.setattr(process_lookup.subprocess, "run", _raise)

    assert process_lookup._parent_pid(1234) is None


def test_is_own_ancestor_true_for_self():
    """The walk starts at ``os.getpid()``, so this process is its own ancestor."""
    assert process_lookup.is_own_ancestor(os.getpid()) is True


def test_is_own_ancestor_true_for_parent():
    """One real hop up the live process tree — the case the guard exists for."""
    assert process_lookup.is_own_ancestor(os.getppid()) is True


def test_is_own_ancestor_false_for_init():
    """The walk stops at pid <= 1, so launchd never reads as an ancestor.

    A True here would suppress every restart on the machine.
    """
    assert process_lookup.is_own_ancestor(1) is False


@pytest.mark.parametrize("on_unreadable", [False, True], ids=["fail-open", "fail-closed"])
def test_is_own_ancestor_unreadable_tree_returns_the_requested_polarity(monkeypatch, on_unreadable):
    """An unreadable chain is INCONCLUSIVE, so the caller picks the answer.

    The restart path (`run.py`) takes the default False — a spurious True would
    disable a legitimate recovery restart. The kill paths (`recover`,
    `stop_email`) pass True, because "unreadable → proceed to kill" is the
    dangerous direction there.
    """
    monkeypatch.setattr(process_lookup, "_parent_pid", lambda pid: None)

    assert process_lookup.is_own_ancestor(999_999, on_unreadable=on_unreadable) is on_unreadable


@pytest.mark.parametrize("on_unreadable", [False, True], ids=["fail-open", "fail-closed"])
def test_is_own_ancestor_terminates_on_a_parent_cycle(monkeypatch, on_unreadable):
    """A cycle in the reported tree must exhaust the bound, not hang the caller.

    Exhausting the bound is inconclusive too — the walk never reached pid 1 — so
    it returns ``on_unreadable`` rather than a hardcoded False.
    """
    seen: list[int] = []

    def _cycle(pid: int) -> int:
        seen.append(pid)
        return 777  # always the same pid: the walk never reaches 1

    monkeypatch.setattr(process_lookup, "_parent_pid", _cycle)

    assert process_lookup.is_own_ancestor(999_999, on_unreadable=on_unreadable) is on_unreadable
    assert len(seen) <= 64, "the walk must be bounded"


@pytest.mark.parametrize("on_unreadable", [False, True], ids=["fail-open", "fail-closed"])
def test_is_own_ancestor_polarity_does_not_affect_conclusive_answers(on_unreadable):
    """``on_unreadable`` is returned ONLY when the walk cannot answer.

    Reaching pid <= 1 with no match is a real "no", and a live self/ancestor hit
    is a real "yes"; neither may be flipped by the keyword.
    """
    assert process_lookup.is_own_ancestor(1, on_unreadable=on_unreadable) is False
    assert process_lookup.is_own_ancestor(os.getpid(), on_unreadable=on_unreadable) is True
    assert process_lookup.is_own_ancestor(os.getppid(), on_unreadable=on_unreadable) is True


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


@pytest.mark.parametrize(
    ("is_ancestor", "expected_kills"),
    [(True, []), (False, [(4242, signal.SIGTERM)])],
    ids=["ancestor-skipped", "not-ancestor-signalled"],
)
def test_stop_email_refuses_to_signal_its_own_ancestor(
    monkeypatch, tmp_path, is_ancestor, expected_kills
):
    """The fallback SIGTERM branch must gate on ``is_own_ancestor`` (#3164).

    ``get_email_pid`` is ancestor-safe now, so a caller hosted by the email
    bridge can be handed its own ancestor's PID; SIGTERMing it would take the
    caller down. ``pgrep`` made that unreachable by accident.

    ``tmp_path`` has no ``scripts/valor-service.sh``, which is what selects the
    direct-signal fallback over the service script.
    """
    monkeypatch.setattr(update_service, "get_email_pid", lambda: 4242)
    monkeypatch.setattr(update_service, "is_own_ancestor", lambda pid, **kwargs: is_ancestor)
    kills: list[tuple[int, int]] = []
    # Patch the module's own `_signal_pid` seam, not `os.kill`, so nothing else
    # in the process sees a stubbed `os.kill` for the duration of the test.
    monkeypatch.setattr(update_service, "_signal_pid", lambda pid, sig: kills.append((pid, sig)))

    # The PID is still resolvable either way, so the "did it stop?" answer is False.
    assert update_service.stop_email(tmp_path) is False
    assert kills == expected_kills


def test_stop_email_refuses_to_signal_when_the_process_tree_is_unreadable(monkeypatch, tmp_path):
    """A kill path must gate with ``on_unreadable=True`` (#3164).

    If `ps` cannot be read, "is this my own ancestor?" is unanswered — and here
    the unsafe direction is proceeding, so the call site must ask for the
    fail-closed polarity rather than the default.
    """
    monkeypatch.setattr(update_service, "get_email_pid", lambda: 4242)
    monkeypatch.setattr(process_lookup, "_parent_pid", lambda pid: None)
    kills: list[tuple[int, int]] = []
    monkeypatch.setattr(update_service, "_signal_pid", lambda pid, sig: kills.append((pid, sig)))

    assert update_service.stop_email(tmp_path) is False
    assert kills == [], "an unreadable process tree must mean 'refuse to signal'"


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


# ---------------------------------------------------------------------------
# Caller contract in the one-shot migration guards (#3187)
# ---------------------------------------------------------------------------

# Both scripts carry the same pair of preconditions, converted together.
MIGRATION_GUARD_MODULES = (
    "scripts.migrate_session_type_pm_to_eng",
    "scripts.merge_dev_chat_into_eng",
)


@contextlib.contextmanager
def _decoy_worker_process(tmp_path):
    """Spawn a real ``python <tmp>/worker/__main__.py`` and yield its PID.

    A genuine subprocess in the host process table, not a stubbed ``ps``: the
    guard has to resolve it through ``ps`` the way it will on a real migration
    run. The script-path launch shape is used deliberately — spawning the
    ``python -m worker`` shape would start the actual worker.
    """
    script = tmp_path / "worker" / "__main__.py"
    script.parent.mkdir(parents=True)
    script.write_text("import time\n\ntime.sleep(120)\n")
    proc = subprocess.Popen([sys.executable, str(script)])
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if proc.pid in process_lookup.find_python_service_pids(script_suffix=WORKER_SUFFIX):
                break
            time.sleep(0.05)
        else:
            pytest.fail(f"decoy worker pid {proc.pid} never appeared in the process table")
        yield proc.pid
    finally:
        proc.kill()
        proc.wait(timeout=10)


@pytest.mark.parametrize("module_name", MIGRATION_GUARD_MODULES)
def test_migration_worker_guard_sees_a_live_worker_in_the_process_table(
    module_name, monkeypatch, tmp_path, caplog
):
    """The guard must refuse to migrate while a worker-shaped process is live.

    This is the #3187 failure direction: the old ``pgrep -f "python -m worker"``
    excluded the caller's own ancestors, so a migration launched from inside a
    worker-hosted agent session read the live worker as absent and mutated
    session rows underneath it. Exercised against a real subprocess so the
    lookup goes through ``ps``, and asserted on the decoy's own PID — a machine
    that happens to be running the real worker would otherwise make any
    ``SystemExit`` look like a pass.
    """
    guard_module = importlib.import_module(module_name)
    # Neutralise the heartbeat-file signal (a fresh heartbeat exits first), so
    # the process-table signal is the only thing that can trip the guard.
    monkeypatch.setattr(guard_module, "WORKER_HEARTBEAT_THRESHOLD", 0)

    with _decoy_worker_process(tmp_path) as decoy_pid:
        with caplog.at_level(logging.ERROR):
            with pytest.raises(SystemExit) as exit_info:
                guard_module._check_worker_not_running()

    assert exit_info.value.code == 1
    assert str(decoy_pid) in caplog.text, (
        f"the guard exited, but pid {decoy_pid} is absent from its message — it did "
        "not exit because it saw the decoy worker"
    )


@pytest.mark.parametrize("module_name", MIGRATION_GUARD_MODULES)
def test_migration_worker_guard_treats_a_reaped_pid_as_stopped(module_name, monkeypatch, tmp_path):
    """The ``os.kill(pid, 0)`` re-check survives the conversion.

    A PID the lookup reported but that has since exited must not block the
    migration.
    """
    guard_module = importlib.import_module(module_name)
    monkeypatch.setattr(guard_module, "WORKER_HEARTBEAT_THRESHOLD", 0)

    reaped = subprocess.Popen([sys.executable, "-c", "pass"])
    reaped.wait(timeout=30)
    monkeypatch.setattr(guard_module, "find_python_service_pids", lambda **kwargs: [reaped.pid])

    guard_module._check_worker_not_running()  # must not raise SystemExit


@pytest.mark.parametrize("module_name", MIGRATION_GUARD_MODULES)
def test_migration_email_guard_exits_on_a_live_email_bridge_pid(module_name, monkeypatch):
    """The email-bridge precondition trips on a live PID from the lookup.

    ``os.getpid()`` is used as the "live" PID because the guard re-checks
    liveness with ``os.kill(pid, 0)``; the exit happens before the Redis
    ``email:last_poll_ts`` freshness check, so no Redis is touched.
    """
    guard_module = importlib.import_module(module_name)
    monkeypatch.setattr(guard_module, "find_python_service_pids", lambda **kwargs: [os.getpid()])

    with pytest.raises(SystemExit) as exit_info:
        guard_module._check_email_bridge_not_running()

    assert exit_info.value.code == 1


# The decoy re-execs the guard in a CHILD of itself, which is the topology the
# defect needs: `$1` is the path of the script that actually calls the guard.
_MIGRATION_DECOY = """
import subprocess, sys
sys.exit(subprocess.run([sys.executable, sys.argv[1]]).returncode)
"""

# Runs as a grandchild of the worker-shaped decoy — the migration script's real
# position when it is launched from inside a worker-hosted agent session.
_MIGRATION_GUARD_PROBE = """
import sys
sys.path.insert(0, {repo!r})
import importlib

guard = importlib.import_module({module!r})
# Neutralise the heartbeat-file signal so the process-table signal is the only
# thing that can trip the guard.
guard.WORKER_HEARTBEAT_THRESHOLD = 0
try:
    guard._check_worker_not_running()
except SystemExit as exc:
    print("GUARD_TRIPPED", exc.code)
else:
    print("GUARD_PASSED")
"""


@pytest.mark.macos_only
@pytest.mark.parametrize("module_name", MIGRATION_GUARD_MODULES)
def test_migration_worker_guard_trips_when_run_inside_the_live_worker(module_name, tmp_path):
    """The actual #3187 topology: the guard runs as a DESCENDANT of the worker.

    The other worker-guard test above spawns its decoy as a *child*, which BSD
    ``pgrep`` would have found — so it pins launch-shape coverage, not the
    ancestor defect. This one puts the guard where the defect lives: a migration
    launched from inside a worker-hosted agent session, where ``pgrep -f`` hid
    the very worker whose absence the guard is asserting, let the precondition
    pass, and mutated session rows underneath a running worker.

    macOS-only: the ancestor exclusion being closed here is a BSD ``pgrep``
    behavior, and the premise does not hold on Linux/procps.
    """
    decoy = tmp_path / "worker" / "__main__.py"
    decoy.parent.mkdir(parents=True)
    decoy.write_text(_MIGRATION_DECOY)
    probe = tmp_path / "probe.py"
    probe.write_text(_MIGRATION_GUARD_PROBE.format(repo=str(PROJECT_ROOT), module=module_name))

    result = subprocess.run(
        [sys.executable, str(decoy), str(probe)],
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert "GUARD_TRIPPED 1" in result.stdout, (
        "the guard let the migration proceed while its own host worker was live "
        f"(stdout={result.stdout!r} stderr={result.stderr[-2000:]!r})"
    )
