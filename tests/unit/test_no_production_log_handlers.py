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
"""

from __future__ import annotations

import logging
from pathlib import Path


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
