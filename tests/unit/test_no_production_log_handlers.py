"""Regression guard for #2854: pytest processes never write production logs.

``bridge/telegram_bridge.py`` attaches a RotatingFileHandler for
``logs/bridge.log`` to the ROOT logger at module import time, so a single
test importing it (directly or transitively) used to route every logger's
records — including test fixtures' deliberate failure-branch ERRORs — into
the production log file, where they read as daily production failures.

The guard here deterministically imports the known attacher first, so it
pins the real mechanism regardless of xdist scheduling, then asserts no
handler on the root or ``bridge`` loggers targets a file under any
``logs/`` directory. It cannot see attachers that run only in OTHER
workers or later in this worker's schedule; it exists to keep the
import-time attach in ``telegram_bridge`` (the one observed leaking) from
regressing.

The absence assertion above is only half the guard: it would stay green if
the production handler were deleted outright or the ``_UNDER_PYTEST``
condition were inverted. ``test_production_process_still_attaches_the_handler``
below is the companion probe (precedent:
``tests/unit/test_watchdog_log_isolation.py::_run_probe``) that runs the
same import in a subprocess with ``PYTEST_CURRENT_TEST`` stripped and
``pytest`` never imported, asserting a ``RotatingFileHandler`` requesting
exactly ``logs/bridge.log`` IS attached to the root logger — pinning that
production behavior is unchanged. The probe redirects the handler's actual
file into a tmp directory, so it never reads, writes, rotates, or restores
the real production log.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _log_file_handlers(logger: logging.Logger) -> list[str]:
    return [
        h.baseFilename
        for h in logger.handlers
        if isinstance(h, logging.FileHandler)
        and "logs" in Path(getattr(h, "baseFilename", "")).parts
    ]


def test_pytest_process_attaches_no_production_log_file_handler():
    # Import the known import-time attacher so this process has definitely
    # executed the guarded attach path before we assert.
    import bridge.telegram_bridge  # noqa: F401

    offenders: dict[str, list[str]] = {}
    for name in ("", "bridge", "bridge.room_inbox", "bridge.telegram_bridge"):
        found = _log_file_handlers(logging.getLogger(name))
        if found:
            offenders[name or "<root>"] = found

    assert not offenders, (
        "Production log-file handler active under pytest — test-fixture "
        f"ERROR lines will masquerade as production failures (#2854): {offenders}"
    )


def test_production_process_still_attaches_the_handler():
    """Companion to the absence guard above: pin that a non-pytest process
    still gets the production ``logs/bridge.log`` handler.

    Without this, deleting the production handler entirely — or inverting
    ``_UNDER_PYTEST`` in ``bridge/telegram_bridge.py`` — would leave the
    absence-only guard green while silently killing all production log
    output. Runs the import in a fresh subprocess (never touched pytest, so
    "pytest" is not in ``sys.modules``) with ``PYTEST_CURRENT_TEST`` scrubbed
    from the environment, mirroring
    ``tests/unit/test_watchdog_log_isolation.py::_run_probe``.

    The probe must never touch the REAL ``logs/bridge.log``: a live bridge
    (or a nightly run in the serving checkout) writes it concurrently, and a
    snapshot/restore around the subprocess was proven to destroy those
    concurrent writes. So BEFORE the import, the probe patches
    ``RotatingFileHandler.__init__`` to record the requested filename and
    redirect the actual open into a private tmp directory. The assertion is
    on the recorded request — exact-path equality with
    ``REPO_ROOT/logs/bridge.log`` — while the bytes land in the tmp file.
    """
    probe = f"""
import json, logging, logging.handlers, pathlib, sys, tempfile
sys.path.insert(0, {str(REPO_ROOT)!r})

requested = []
tmp_dir = tempfile.mkdtemp(prefix="probe-bridge-log-")
_real_init = logging.handlers.RotatingFileHandler.__init__

def _redirecting_init(self, filename, *args, **kwargs):
    requested.append(str(filename))
    redirected = pathlib.Path(tmp_dir) / pathlib.Path(filename).name
    _real_init(self, str(redirected), *args, **kwargs)

# Patch the class __init__ (not a name binding), so every construction form
# — logging.handlers.RotatingFileHandler(...) included — is redirected.
logging.handlers.RotatingFileHandler.__init__ = _redirecting_init

import bridge.telegram_bridge as m
assert m.__file__.startswith({str(REPO_ROOT)!r}), m.__file__
root = logging.getLogger()
attached = [
    h for h in root.handlers
    if isinstance(h, logging.handlers.RotatingFileHandler)
]
print(json.dumps({{
    "has_bridge_log_handler": bool(attached),
    "requested_paths": requested,
    "actual_paths": [getattr(h, "baseFilename", "") for h in attached],
    "tmp_dir": tmp_dir,
}}))
"""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    env.pop("PYTEST_CURRENT_TEST", None)

    # timeout: this module has an import-time hang precedent (TCC/iCloud
    # open() deadlock in _get_active_projects, fixed in 261ebbd77). A wedged
    # probe should fail this test with output, not ride the 420s suite cap.
    # 180s, not 60: under a pytest-inherited environment the probe import
    # measures ~65s on a worker-only host (blocks ~60s, then completes), so
    # a 60s bound fails a healthy probe. Measured 2026-09-05.
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip().splitlines()[-1])

    # Best-effort cleanup of the probe subprocess's redirect directory.
    shutil.rmtree(state.get("tmp_dir", ""), ignore_errors=True)

    assert state["has_bridge_log_handler"] is True, (
        "Production process no longer attaches the logs/bridge.log handler "
        f"— production logging regressed: {state}"
    )
    expected = str(REPO_ROOT / "logs" / "bridge.log")
    assert state["requested_paths"] == [expected], (
        "Production process requested an unexpected log path "
        f"(want exactly [{expected!r}]): {state}"
    )
    # The redirect held: nothing the probe attached points at the real log.
    assert all(expected not in p for p in state["actual_paths"]), (
        f"Probe redirect failed — real production log was opened: {state}"
    )
