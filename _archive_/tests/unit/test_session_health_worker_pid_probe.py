"""Unit tests for the B2-probe duplicate-worker registration check (issue #1817).

``register_worker_pid()`` gained an OPTIONAL, observability-only,
liveness-gated probe that detects a second LIVE worker for the same
host+role and logs a WARNING. It NEVER refuses to start, NEVER exits, and
NEVER blocks -- the atomic pending->running run-claim (B2 proper, in
models/session_lifecycle.py) is what makes exactly-one-actor-per-session
correctness hold. This probe is diagnostic only.

Tests use the real Redis client (matching the existing SETNX-idiom test
style elsewhere, e.g. tests/unit/test_dedup.py) plus targeted mocking of the
liveness/heartbeat helpers for deterministic control over the decision
matrix.
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from agent.session_health import (
    _ORPHAN_REAP_HOSTNAME,
    _WORKER_PID_HEARTBEAT_TS_KEY_PREFIX,
    _WORKER_ROLE_PID_KEY_PREFIX,
    HEARTBEAT_FRESHNESS_WINDOW,
    WORKER_REGISTERED_PID_KEY_PREFIX,
    _pid_is_live,
    _probe_duplicate_worker_registration,
    _worker_pid_heartbeat_fresh,
    register_worker_pid,
)

ROLE = "test-probe-role"


def _role_key() -> str:
    return f"{_WORKER_ROLE_PID_KEY_PREFIX}{_ORPHAN_REAP_HOSTNAME}:{ROLE}"


def _hb_key(pid: int) -> str:
    return f"{_WORKER_PID_HEARTBEAT_TS_KEY_PREFIX}{_ORPHAN_REAP_HOSTNAME}:{pid}"


@pytest.fixture(autouse=True)
def _role_env(monkeypatch):
    monkeypatch.setenv("VALOR_PROJECT_KEY", ROLE)
    yield


@pytest.fixture(autouse=True)
def _cleanup():
    from popoto.redis_db import POPOTO_REDIS_DB as _R

    yield
    _R.delete(_role_key())
    _R.delete(f"{WORKER_REGISTERED_PID_KEY_PREFIX}{_ORPHAN_REAP_HOSTNAME}:{os.getpid()}")
    for pid in (1, 999999, os.getpid(), os.getpid() + 1, 424242, 424243, 424244):
        _R.delete(_hb_key(pid))


class TestPidIsLive:
    def test_current_process_is_live(self):
        assert _pid_is_live(os.getpid()) is True

    def test_dead_pid_is_not_live(self):
        # A pid vanishingly unlikely to exist on any machine running this suite.
        assert _pid_is_live(999999) is False

    def test_permission_error_treated_as_live(self):
        """A process owned by another user can't be confirmed dead -- treat
        conservatively as live rather than silently superseding without
        evidence."""
        with patch("agent.session_health.os.kill", side_effect=PermissionError()):
            assert _pid_is_live(1) is True


class TestWorkerPidHeartbeatFresh:
    def test_missing_heartbeat_is_stale(self):
        assert _worker_pid_heartbeat_fresh(424242) is False

    def test_fresh_heartbeat_is_fresh(self):
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        pid = 424243
        _R.set(_hb_key(pid), datetime.now(UTC).timestamp(), ex=60)
        assert _worker_pid_heartbeat_fresh(pid) is True

    def test_stale_heartbeat_is_stale(self):
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        pid = 424244
        old_ts = (
            datetime.now(UTC) - timedelta(seconds=HEARTBEAT_FRESHNESS_WINDOW + 30)
        ).timestamp()
        _R.set(_hb_key(pid), old_ts, ex=60)
        assert _worker_pid_heartbeat_fresh(pid) is False


class TestProbeDuplicateWorkerRegistration:
    def test_never_flags_self(self, caplog):
        """os.getpid() must never be compared against itself as a conflict."""
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        pid = os.getpid()
        _R.set(_role_key(), pid, ex=100)
        with caplog.at_level(logging.WARNING, logger="agent.session_health"):
            _probe_duplicate_worker_registration(pid)
        assert not any("second live worker" in r.message for r in caplog.records)

    def test_dead_competitor_silently_superseded(self, caplog):
        """A dead pid under the role key produces no WARNING and is superseded."""
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        dead_pid = 999999
        _R.set(_role_key(), dead_pid, ex=100)
        with caplog.at_level(logging.WARNING, logger="agent.session_health"):
            _probe_duplicate_worker_registration(os.getpid())
        assert not any("second live worker" in r.message for r in caplog.records)
        # Additive: registration always proceeds and overwrites the role key.
        assert int(_R.get(_role_key())) == os.getpid()

    def test_live_but_stale_heartbeat_silently_superseded(self, caplog):
        """A live pid with a stale/missing heartbeat is dead-worker residue --
        no WARNING, no refusal (the exact launchd-respawn case)."""
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        other_pid = os.getpid() + 1
        _R.set(_role_key(), other_pid, ex=100)
        # No heartbeat key written for other_pid -> stale by definition.
        with (
            patch("agent.session_health._pid_is_live", return_value=True),
            caplog.at_level(logging.WARNING, logger="agent.session_health"),
        ):
            _probe_duplicate_worker_registration(os.getpid())
        assert not any("second live worker" in r.message for r in caplog.records)

    def test_confirmed_live_and_fresh_logs_warning(self, caplog):
        """Only a CONFIRMED LIVE + fresh-heartbeat competitor logs a WARNING."""
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        other_pid = os.getpid() + 1
        _R.set(_role_key(), other_pid, ex=100)
        with (
            patch("agent.session_health._pid_is_live", return_value=True),
            patch("agent.session_health._worker_pid_heartbeat_fresh", return_value=True),
            caplog.at_level(logging.WARNING, logger="agent.session_health"),
        ):
            _probe_duplicate_worker_registration(os.getpid())
        warnings = [r for r in caplog.records if "second live worker" in r.message]
        assert warnings, "a confirmed-live same-host+role duplicate must log a WARNING"
        # Never refuses: registration still proceeds and overwrites the role key.
        assert int(_R.get(_role_key())) == os.getpid()

    def test_cross_role_pid_never_compared(self):
        """A pid registered under a DIFFERENT role is never even looked up."""
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        other_role_key = f"{_WORKER_ROLE_PID_KEY_PREFIX}{_ORPHAN_REAP_HOSTNAME}:some-other-role"
        _R.set(other_role_key, 999999, ex=100)
        try:
            _probe_duplicate_worker_registration(os.getpid())
            assert int(_R.get(other_role_key)) == 999999
        finally:
            _R.delete(other_role_key)


class TestRegisterWorkerPidNeverRefuses:
    def test_register_worker_pid_does_not_raise_on_probe_failure(self):
        """A probe failure must never prevent the additive registration write."""
        with patch(
            "agent.session_health._probe_duplicate_worker_registration",
            side_effect=RuntimeError("boom"),
        ):
            register_worker_pid()  # must not raise

    def test_register_worker_pid_never_exits_or_raises_on_confirmed_duplicate(self):
        """Even with a confirmed-live duplicate, register_worker_pid must not
        exit or raise -- it only logs."""
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        other_pid = os.getpid() + 1
        _R.set(_role_key(), other_pid, ex=100)
        with (
            patch("agent.session_health._pid_is_live", return_value=True),
            patch("agent.session_health._worker_pid_heartbeat_fresh", return_value=True),
        ):
            register_worker_pid()  # must not raise or sys.exit
