"""Unit tests for tools.doctor._check_redis_replication_health (Fix #5 — #1827).

The check is ROLE-GATED on the ``data/redis-replication-enabled`` marker. On a
client-only machine it must return a neutral SKIP (``passed=True``) and never warn.
All subprocess calls and the marker are mocked — no real redis-cli, no real Redis.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import tools.doctor as doctor
from tools.doctor import _check_redis_replication_health


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    p = MagicMock(spec=subprocess.CompletedProcess)
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def _present_marker(tmp_path: Path) -> Path:
    marker = tmp_path / "redis-replication-enabled"
    marker.write_text("1")
    return marker


# ---------------------------------------------------------------------------
# Registered in the checks list
# ---------------------------------------------------------------------------


def test_check_registered():
    checks = doctor.get_checks()
    names = {getattr(fn, "__name__", "") for fn in checks}
    assert "_check_redis_replication_health" in names


# ---------------------------------------------------------------------------
# Role gate: marker absent → neutral SKIP, passed=True, no warning, no subprocess
# ---------------------------------------------------------------------------


def test_neutral_skip_when_marker_absent(tmp_path: Path):
    mock_run = MagicMock()
    with patch.object(doctor, "_REPLICATION_MARKER_FILE", tmp_path / "missing"):
        with patch("subprocess.run", mock_run):
            result = _check_redis_replication_health()

    assert result.passed is True
    assert "client-only" in result.message
    assert result.fix is None
    mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Graceful degradation when redis-cli / Redis unavailable
# ---------------------------------------------------------------------------


def test_graceful_skip_when_redis_cli_absent(tmp_path: Path):
    marker = _present_marker(tmp_path)
    with patch.object(doctor, "_REPLICATION_MARKER_FILE", marker):
        with patch("subprocess.run", side_effect=FileNotFoundError("no redis-cli")):
            result = _check_redis_replication_health()

    assert result.passed is True
    assert "skipped" in result.message.lower()


def test_graceful_skip_when_redis_unreachable(tmp_path: Path):
    marker = _present_marker(tmp_path)
    with patch.object(doctor, "_REPLICATION_MARKER_FILE", marker):
        with patch("subprocess.run", return_value=_proc(returncode=1, stderr="refused")):
            result = _check_redis_replication_health()

    assert result.passed is True
    assert "unreachable" in result.message.lower()


# ---------------------------------------------------------------------------
# Replica present and linked → healthy pass
# ---------------------------------------------------------------------------


def test_replica_linked_up_is_healthy(tmp_path: Path):
    marker = _present_marker(tmp_path)

    def fake_run(cmd, **kwargs):
        if "INFO" in cmd and "replication" in cmd:
            return _proc(stdout="role:slave\r\nmaster_link_status:up\r\n")
        if "SENTINEL" in cmd:
            return _proc(returncode=0, stdout="name\nvalor-redis\n")
        return _proc(stdout="")

    with patch.object(doctor, "_REPLICATION_MARKER_FILE", marker):
        with patch("subprocess.run", side_effect=fake_run):
            result = _check_redis_replication_health()

    assert result.passed is True
    assert "replica" in result.message
    assert "master_link_status=up" in result.message


def test_replica_link_down_fails(tmp_path: Path):
    marker = _present_marker(tmp_path)

    def fake_run(cmd, **kwargs):
        if "INFO" in cmd and "replication" in cmd:
            return _proc(stdout="role:slave\r\nmaster_link_status:down\r\n")
        if "SENTINEL" in cmd:
            return _proc(returncode=1)
        return _proc(stdout="")

    with patch.object(doctor, "_REPLICATION_MARKER_FILE", marker):
        with patch("subprocess.run", side_effect=fake_run):
            result = _check_redis_replication_health()

    assert result.passed is False
    assert result.fix is not None
    assert "redis-durability.md" in result.fix


def test_master_with_replica_is_healthy(tmp_path: Path):
    marker = _present_marker(tmp_path)

    def fake_run(cmd, **kwargs):
        if "INFO" in cmd and "replication" in cmd:
            return _proc(stdout="role:master\r\nconnected_slaves:1\r\n")
        if "SENTINEL" in cmd:
            return _proc(returncode=0, stdout="name\nvalor-redis\n")
        return _proc(stdout="")

    with patch.object(doctor, "_REPLICATION_MARKER_FILE", marker):
        with patch("subprocess.run", side_effect=fake_run):
            result = _check_redis_replication_health()

    assert result.passed is True
    assert "connected_slaves=1" in result.message


# ---------------------------------------------------------------------------
# Never raises
# ---------------------------------------------------------------------------


def test_never_raises_on_subprocess_exception(tmp_path: Path):
    marker = _present_marker(tmp_path)
    with patch.object(doctor, "_REPLICATION_MARKER_FILE", marker):
        with patch("subprocess.run", side_effect=RuntimeError("kaboom")):
            result = _check_redis_replication_health()

    assert result is not None
    assert result.passed is True
