"""Update-flow drain probe (issue #2141).

`scripts.update.drain` is the busy-check `remote-update.sh` consults before
restarting the worker: exit 0 = idle (restart now), exit 3 = still busy
(shell DEFERS to the next cycle). Fail-open on probe errors — a broken
probe must degrade to today's restart behavior, never wedge fleet updates.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from scripts.update import drain


def _sessions(*flags):
    """Fake running-session rows; each flag is the row's is_ledger value."""
    return [SimpleNamespace(is_ledger=f) for f in flags]


# ---------------------------------------------------------------------------
# count_running_sessions
# ---------------------------------------------------------------------------


def test_count_excludes_ledger_rows():
    with patch("models.agent_session.AgentSession") as fake:
        fake.query.filter.return_value = _sessions(False, True, "True", "False")
        # 'True' string (Popoto round-trip) and bool True are ledgers;
        # False and 'False' are real sessions.
        assert drain.count_running_sessions() == 2
        fake.query.filter.assert_called_once_with(status="running")


def test_count_zero_when_only_ledgers():
    with patch("models.agent_session.AgentSession") as fake:
        fake.query.filter.return_value = _sessions(True, "True")
        assert drain.count_running_sessions() == 0


# ---------------------------------------------------------------------------
# wait_for_idle
# ---------------------------------------------------------------------------


def test_wait_returns_true_immediately_when_idle():
    with patch.object(drain, "count_running_sessions", return_value=0):
        with patch.object(drain.time, "sleep") as fake_sleep:
            assert drain.wait_for_idle(60, 5, log=lambda *_: None) is True
            fake_sleep.assert_not_called()


def test_wait_polls_until_idle():
    counts = iter([2, 1, 0])
    with patch.object(drain, "count_running_sessions", side_effect=lambda: next(counts)):
        with patch.object(drain.time, "sleep") as fake_sleep:
            assert drain.wait_for_idle(60, 5, log=lambda *_: None) is True
            assert fake_sleep.call_count == 2


def test_wait_returns_false_when_busy_past_deadline():
    clock = iter([0.0, 0.0, 100.0, 100.0])
    with (
        patch.object(drain, "count_running_sessions", return_value=1),
        patch.object(drain.time, "monotonic", side_effect=lambda: next(clock, 200.0)),
        patch.object(drain.time, "sleep"),
    ):
        assert drain.wait_for_idle(50, 5, log=lambda *_: None) is False


# ---------------------------------------------------------------------------
# main / exit codes
# ---------------------------------------------------------------------------


def test_main_exit_idle():
    with patch.object(drain, "wait_for_idle", return_value=True):
        assert drain.main(["--timeout", "1", "--poll", "1"]) == drain.EXIT_IDLE


def test_main_exit_busy():
    with patch.object(drain, "wait_for_idle", return_value=False):
        assert drain.main(["--timeout", "1", "--poll", "1"]) == drain.EXIT_BUSY


def test_main_fails_open_on_probe_error(capsys):
    with patch.object(drain, "wait_for_idle", side_effect=RuntimeError("redis down")):
        assert drain.main(["--timeout", "1", "--poll", "1"]) == drain.EXIT_IDLE
    err = capsys.readouterr().err
    assert "failing open" in err
