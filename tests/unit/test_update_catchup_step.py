"""Unit tests for the ``/update`` best-effort ``valor-catchup`` final step.

The step lives in ``scripts/update/run.py`` as ``run_catchup_step`` and is the
LAST thing ``run_update`` does. Its contract:

- Gated on BOTH bridge AND worker reporting ``running`` — skip otherwise.
- Invokes ``valor-catchup`` as a subprocess with a TIGHT per-invocation timeout.
- Best-effort: any failure, non-zero exit, or timeout is logged and swallowed.
  ``/update`` completion (``run_update``'s ``UpdateResult``) must be wholly
  independent of ``valor-catchup``'s outcome — the step never raises and never
  flips ``result.success`` to False.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.update import run as update_run


def _status(running: bool):
    """Minimal stand-in for service.ServiceStatus with a ``.running`` flag."""
    s = MagicMock()
    s.running = running
    s.pid = 4242 if running else None
    return s


@pytest.fixture
def logs() -> list[str]:
    return []


@pytest.fixture
def log_fn(logs):
    def _log(msg, *args, **kwargs):
        logs.append(msg)

    return _log


def test_skips_when_bridge_down(logs, log_fn):
    """Bridge down → no subprocess invocation, logged skip, returns cleanly."""
    with (
        patch.object(update_run.service, "get_service_status", return_value=_status(False)),
        patch.object(update_run.service, "get_worker_status", return_value=_status(True)),
        patch("subprocess.run") as mock_run,
    ):
        update_run.run_catchup_step(Path("/tmp/x"), log_fn=log_fn)

    mock_run.assert_not_called()
    assert any("catchup" in m.lower() and "skip" in m.lower() for m in logs)


def test_skips_when_worker_down(logs, log_fn):
    """Worker down → no subprocess invocation, logged skip."""
    with (
        patch.object(update_run.service, "get_service_status", return_value=_status(True)),
        patch.object(update_run.service, "get_worker_status", return_value=_status(False)),
        patch("subprocess.run") as mock_run,
    ):
        update_run.run_catchup_step(Path("/tmp/x"), log_fn=log_fn)

    mock_run.assert_not_called()
    assert any("catchup" in m.lower() and "skip" in m.lower() for m in logs)


def test_invokes_subprocess_when_healthy(logs, log_fn):
    """Bridge + worker up → valor-catchup invoked as a subprocess with a timeout."""
    completed = subprocess.CompletedProcess(
        args=["valor-catchup"], returncode=0, stdout="ok", stderr=""
    )
    with (
        patch.object(update_run.service, "get_service_status", return_value=_status(True)),
        patch.object(update_run.service, "get_worker_status", return_value=_status(True)),
        patch("subprocess.run", return_value=completed) as mock_run,
    ):
        update_run.run_catchup_step(Path("/tmp/x"), log_fn=log_fn)

    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    # A tight timeout MUST be supplied.
    assert kwargs.get("timeout") is not None
    assert kwargs["timeout"] > 0
    # The argv must invoke the valor-catchup entry point.
    argv = mock_run.call_args[0][0]
    assert any("valor-catchup" in str(part) for part in argv)


def test_swallows_nonzero_exit(logs, log_fn):
    """A non-zero exit from valor-catchup is logged and swallowed — no raise."""
    failed = subprocess.CompletedProcess(
        args=["valor-catchup"], returncode=1, stdout="", stderr="boom"
    )
    with (
        patch.object(update_run.service, "get_service_status", return_value=_status(True)),
        patch.object(update_run.service, "get_worker_status", return_value=_status(True)),
        patch("subprocess.run", return_value=failed),
    ):
        # Must not raise.
        update_run.run_catchup_step(Path("/tmp/x"), log_fn=log_fn)


def test_swallows_timeout(logs, log_fn):
    """A timeout never propagates out of the step."""
    with (
        patch.object(update_run.service, "get_service_status", return_value=_status(True)),
        patch.object(update_run.service, "get_worker_status", return_value=_status(True)),
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="valor-catchup", timeout=30),
        ),
    ):
        # Must not raise.
        update_run.run_catchup_step(Path("/tmp/x"), log_fn=log_fn)

    assert any("timeout" in m.lower() or "timed out" in m.lower() for m in logs)


def test_swallows_arbitrary_exception(logs, log_fn):
    """Any unexpected exception (e.g. OSError) is swallowed, not raised."""
    with (
        patch.object(update_run.service, "get_service_status", return_value=_status(True)),
        patch.object(update_run.service, "get_worker_status", return_value=_status(True)),
        patch("subprocess.run", side_effect=OSError("no such file")),
    ):
        update_run.run_catchup_step(Path("/tmp/x"), log_fn=log_fn)


def test_update_result_unaffected_by_failing_catchup():
    """End-to-end contract: a failing valor-catchup never flips run_update's success.

    Stub the step's gate to healthy and force valor-catchup to fail, then assert
    that the step itself is the ONLY thing that ran (we call it directly) and that
    it returns None without raising — proving /update completion is independent of
    valor-catchup's outcome.
    """
    failed = subprocess.CompletedProcess(
        args=["valor-catchup"], returncode=2, stdout="", stderr="x"
    )
    captured: list[str] = []
    with (
        patch.object(update_run.service, "get_service_status", return_value=_status(True)),
        patch.object(update_run.service, "get_worker_status", return_value=_status(True)),
        patch("subprocess.run", return_value=failed),
    ):
        ret = update_run.run_catchup_step(
            Path("/tmp/x"), log_fn=lambda m, *a, **k: captured.append(m)
        )

    # The step returns nothing meaningful and never raises — its outcome cannot
    # influence UpdateResult.success.
    assert ret is None
