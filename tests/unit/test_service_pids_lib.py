"""Shell-level tests for ``scripts/lib/service_pids.sh`` (#3187, #3265).

Every probe here runs as a genuine DESCENDANT of its own target, because that is
the only topology in which the defect exists. A decoy process with the worker's
argv shape is spawned, and it in turn spawns the bash child that does the
probing — exactly the shape of a ``claude -p`` agent session probing the worker
that hosts it. Nothing here stubs ``ps``: the point is to read the real host
process table the way a real probe does.

Two properties are pinned:

- **Visibility.** ``service_pids`` sees an ancestor target. The companion
  assertion that BSD ``pgrep`` does not is what makes this a regression test
  rather than a tautology — without it, a probe that quietly went back to
  ``pgrep`` would still pass every other test in this file on a machine where
  the target happens not to be an ancestor.

- **Refusal, failing closed.** An ancestor-safe lookup hands a caller the PID of
  its own host service, so every kill path gates on
  ``service_pid_refuse_self_kill``. ``pgrep`` made caller-fratricide unreachable
  by accident (it hid the host, so ``stop_worker`` printed "not running" and did
  nothing — a lie, but a harmless one); with a correct lookup the refusal is the
  only thing standing between ``kill -9`` and the session running the command.
  It therefore has to hold when the answer is uncertain, not just when it is a
  clean "yes": a multi-PID list whose ancestor is not the first entry, and a
  lookup that cannot run at all, both have to refuse.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_DIR = Path(__file__).parent.parent.parent
HELPER = PROJECT_DIR / "scripts" / "lib" / "service_pids.sh"

# The decoy uses the `python <path>/worker/__main__.py` launch shape rather than
# `python -m worker`, which would start the real worker.
_DECOY = """
import subprocess, sys, os
sys.exit(subprocess.run(["bash", sys.argv[1], str(os.getpid())]).returncode)
"""

_PROBE_HEADER = f"""
PROJECT_DIR={PROJECT_DIR}
SCRIPT_DIR={PROJECT_DIR}/scripts
source {HELPER}
DECOY_PID="$1"
"""


def _run_under_decoy(tmp_path: Path, probe_body: str) -> subprocess.CompletedProcess:
    """Run ``probe_body`` in a bash child of a live worker-shaped decoy process.

    The decoy's PID arrives as ``$1``. Its exit status is the probe's, so a
    caller can assert on the probe's own exit code.
    """
    script = tmp_path / "worker" / "__main__.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(_DECOY)
    probe = tmp_path / "probe.sh"
    probe.write_text(_PROBE_HEADER + probe_body)
    return subprocess.run(
        [sys.executable, str(script), str(probe)],
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.macos_only
def test_pgrep_hides_an_ancestor_target(tmp_path):
    """The premise: BSD pgrep excludes the caller's whole ancestor chain.

    macOS-only — on Linux/procps pgrep has no such exclusion, so this asserts a
    Darwin behavior rather than a portable one. It exists so the visibility test
    below cannot pass vacuously.
    """
    result = _run_under_decoy(
        tmp_path,
        'pgrep -f "worker/__main__.py" || echo NO_MATCH\n',
    )
    assert "NO_MATCH" in result.stdout, (
        "pgrep reported a match for a target that is the caller's ancestor — the "
        f"defect this helper exists to close may not reproduce here: {result.stdout!r}"
    )


def test_service_pids_sees_an_ancestor_target(tmp_path):
    """The fix: the shared helper resolves the live ancestor's real PID."""
    result = _run_under_decoy(
        tmp_path,
        'echo "DECOY $DECOY_PID"\n'
        "service_pids --script-suffix worker/__main__.py || echo NO_MATCH\n",
    )
    decoy_pid = _decoy_pid(result)
    pids = [token for token in result.stdout.split() if token.isdigit()]
    assert decoy_pid in pids, (
        f"the live decoy {decoy_pid} is missing from the lookup's output {result.stdout!r}"
    )


def _decoy_pid(result: subprocess.CompletedProcess) -> str:
    """The decoy's PID, recovered from the probe's own echo of ``$1``."""
    for line in result.stdout.splitlines():
        if line.startswith("DECOY "):
            return line.split()[1]
    raise AssertionError(f"probe did not report its decoy pid: {result.stdout!r}")


def test_refuse_self_kill_refuses_an_ancestor(tmp_path):
    result = _run_under_decoy(
        tmp_path,
        'echo "DECOY $DECOY_PID"\n'
        'service_pid_refuse_self_kill "$DECOY_PID" worker alt && exit 9\n'
        "exit 0\n",
    )
    assert result.returncode == 0, f"guard allowed a kill of its own ancestor: {result.stdout!r}"
    assert "REFUSING to stop worker" in result.stdout


def test_refuse_self_kill_checks_every_pid_in_the_list(tmp_path):
    """A selector can return several PIDs, and all of them are about to be killed.

    Checking only the first would let a caller kill its own ancestor whenever the
    ancestor is not the lowest-numbered match — the state ``start_bridge.sh``
    cleans up (two live bridges) is exactly when a list comes back.
    """
    result = _run_under_decoy(
        tmp_path,
        'echo "DECOY $DECOY_PID"\n'
        # 99999 stands in for an unrelated match ordered ahead of the ancestor.
        'PIDS="99999\n$DECOY_PID"\n'
        'service_pid_refuse_self_kill "$PIDS" worker alt && exit 9\n'
        "exit 0\n",
    )
    assert result.returncode == 0, (
        f"guard checked only the first PID and allowed the kill: {result.stdout!r}"
    )
    assert "REFUSING to stop worker" in result.stdout


def test_refuse_self_kill_fails_closed_when_the_lookup_cannot_run(tmp_path):
    """An unanswerable question must refuse, not proceed.

    Anything short of the CLI's definitive "walked to init, no match" (exit 1) is
    inconclusive: argparse rejecting a malformed PID token, a missing
    interpreter, an import failure. Treating those as "not an ancestor" is how a
    probe failure escalates into signalling our own host service.
    """
    result = _run_under_decoy(
        tmp_path,
        'echo "DECOY $DECOY_PID"\n'
        '_SERVICE_PIDS_PYTHON="/nonexistent/python"\n'
        'service_pid_refuse_self_kill "$DECOY_PID" worker alt && exit 9\n'
        "exit 0\n",
    )
    assert result.returncode == 0, (
        f"guard failed OPEN when the lookup could not run: {result.stdout!r}"
    )
    assert "REFUSING to stop worker" in result.stdout
