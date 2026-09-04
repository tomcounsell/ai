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
``pytest`` never imported, asserting the ``RotatingFileHandler`` for
``logs/bridge.log`` IS attached to the root logger — pinning that production
behavior is unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
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
    """
    probe = f"""
import json, logging, logging.handlers, sys
sys.path.insert(0, {str(REPO_ROOT)!r})
import bridge.telegram_bridge as m
assert m.__file__.startswith({str(REPO_ROOT)!r}), m.__file__
root = logging.getLogger()
has_bridge_log_handler = any(
    isinstance(h, logging.handlers.RotatingFileHandler)
    and "bridge.log" in getattr(h, "baseFilename", "")
    for h in root.handlers
)
print(json.dumps({{"has_bridge_log_handler": has_bridge_log_handler}}))
"""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    env.pop("PYTEST_CURRENT_TEST", None)

    # The probe deliberately emulates a real (non-pytest) process, so
    # bridge.telegram_bridge's module-scope import-time logging (e.g. the
    # routing-map INFO lines) genuinely writes to the real logs/bridge.log —
    # that IS the production behavior under test. Snapshot/restore the file
    # around the subprocess so this probe does not itself leave test-run
    # artifacts in the operator's production log (the exact class of
    # pollution #2854 is about).
    bridge_log = REPO_ROOT / "logs" / "bridge.log"
    prior_bytes = bridge_log.read_bytes() if bridge_log.exists() else None
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        if prior_bytes is None:
            bridge_log.unlink(missing_ok=True)
        else:
            bridge_log.write_bytes(prior_bytes)

    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip().splitlines()[-1])
    assert state["has_bridge_log_handler"] is True, (
        "Production process no longer attaches the logs/bridge.log handler "
        f"— production logging regressed: {state}"
    )
