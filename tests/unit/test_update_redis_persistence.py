"""Unit tests for scripts/update/redis_persistence.py.

All subprocess calls are mocked — no real redis-cli invocations.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_completed_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# Import-time: module importable
# ---------------------------------------------------------------------------


def test_module_importable():
    """Smoke-test: the module can be imported without errors."""
    from scripts.update import redis_persistence  # noqa: F401


# ---------------------------------------------------------------------------
# Skip path: redis-cli absent
# ---------------------------------------------------------------------------


def test_skip_when_redis_cli_absent():
    """When redis-cli is not on PATH, apply_redis_persistence returns 'skipped'."""
    from scripts.update.redis_persistence import apply_redis_persistence

    with patch("shutil.which", return_value=None):
        result = apply_redis_persistence()

    assert result.action == "skipped"
    assert result.success is False
    assert result.error is not None
    assert "redis-cli" in result.error.lower()


# ---------------------------------------------------------------------------
# Skip path: Redis down (connection refused)
# ---------------------------------------------------------------------------


def test_skip_when_redis_down():
    """When redis-cli ping fails, apply_redis_persistence returns 'failed'."""
    from scripts.update.redis_persistence import apply_redis_persistence

    ping_fail = _make_completed_proc(returncode=1, stderr="Could not connect to Redis")

    with patch("shutil.which", return_value="/usr/bin/redis-cli"):
        with patch("subprocess.run", return_value=ping_fail):
            result = apply_redis_persistence()

    assert result.action in ("skipped", "failed")
    assert result.success is False
    assert result.error is not None


# ---------------------------------------------------------------------------
# Happy path: CONFIG REWRITE succeeds and redis.conf exists
# ---------------------------------------------------------------------------


def _config_set_proc(directive: str) -> MagicMock:
    return _make_completed_proc(returncode=0, stdout="OK")


def test_happy_path_config_rewrite_success(tmp_path: Path):
    """When CONFIG REWRITE succeeds and redis.conf exists, action == 'applied'."""
    from scripts.update.redis_persistence import apply_redis_persistence

    conf_file = tmp_path / "redis.conf"
    conf_file.write_text("# existing config\n")

    def fake_run(cmd, **kwargs):
        if "ping" in cmd:
            return _make_completed_proc(returncode=0, stdout="PONG")
        if "CONFIG" in cmd and "SET" in cmd:
            return _make_completed_proc(returncode=0, stdout="OK")
        if "CONFIG" in cmd and "REWRITE" in cmd:
            return _make_completed_proc(returncode=0, stdout="OK")
        if "CONFIG" in cmd and "GET" in cmd and "dir" in cmd:
            return _make_completed_proc(returncode=0, stdout=f"dir\n{tmp_path}\n")
        if "CONFIG" in cmd and "GET" in cmd and "maxmemory-policy" in cmd:
            return _make_completed_proc(returncode=0, stdout="maxmemory-policy\nnoeviction\n")
        if "INFO" in cmd and "persistence" in cmd:
            return _make_completed_proc(returncode=0, stdout="aof_enabled:1\n")
        return _make_completed_proc(returncode=0, stdout="OK")

    with patch("shutil.which", return_value="/usr/bin/redis-cli"):
        with patch("subprocess.run", side_effect=fake_run):
            result = apply_redis_persistence()

    assert result.success is True
    assert result.action == "applied"
    assert result.error is None


# ---------------------------------------------------------------------------
# Stub-write path: CONFIG REWRITE fails / no redis.conf
# ---------------------------------------------------------------------------


def test_stub_write_when_no_redis_conf(tmp_path: Path):
    """When CONFIG REWRITE fails and no redis.conf exists, writes a stub conf."""
    from scripts.update.redis_persistence import apply_redis_persistence

    # tmp_path has no redis.conf — simulates "Redis started without --config"

    def fake_run(cmd, **kwargs):
        if "ping" in cmd:
            return _make_completed_proc(returncode=0, stdout="PONG")
        if "CONFIG" in cmd and "SET" in cmd:
            return _make_completed_proc(returncode=0, stdout="OK")
        if "CONFIG" in cmd and "REWRITE" in cmd:
            # CONFIG REWRITE fails — no config file on disk
            return _make_completed_proc(
                returncode=1,
                stderr="ERR The server is running without a config file",
            )
        if "CONFIG" in cmd and "GET" in cmd and "dir" in cmd:
            return _make_completed_proc(returncode=0, stdout=f"dir\n{tmp_path}\n")
        if "CONFIG" in cmd and "GET" in cmd and "maxmemory-policy" in cmd:
            return _make_completed_proc(returncode=0, stdout="maxmemory-policy\nnoeviction\n")
        if "INFO" in cmd and "persistence" in cmd:
            return _make_completed_proc(returncode=0, stdout="aof_enabled:1\n")
        return _make_completed_proc(returncode=0, stdout="OK")

    with patch("shutil.which", return_value="/usr/bin/redis-cli"):
        with patch("subprocess.run", side_effect=fake_run):
            result = apply_redis_persistence()

    # A stub redis.conf should have been written
    stub = tmp_path / "redis.conf"
    assert stub.exists(), "stub redis.conf should be created when CONFIG REWRITE fails"
    content = stub.read_text()
    assert "appendonly yes" in content
    assert "appendfsync everysec" in content
    assert "maxmemory-policy noeviction" in content

    # Result must be non-fatal (applied with warning, not raised)
    assert result.action in ("applied", "applied_with_warning")
    assert result.warning is not None
    assert "WARNING" in result.warning or "stub" in result.warning.lower()


# ---------------------------------------------------------------------------
# Post-condition check: aof_enabled:0 → failure logged, non-fatal
# ---------------------------------------------------------------------------


def test_postcondition_aof_disabled_is_non_fatal(tmp_path: Path):
    """Post-condition failure (aof_enabled:0) is logged as a warning, not an exception."""
    from scripts.update.redis_persistence import apply_redis_persistence

    conf_file = tmp_path / "redis.conf"
    conf_file.write_text("# existing config\n")

    def fake_run(cmd, **kwargs):
        if "ping" in cmd:
            return _make_completed_proc(returncode=0, stdout="PONG")
        if "CONFIG" in cmd and "SET" in cmd:
            return _make_completed_proc(returncode=0, stdout="OK")
        if "CONFIG" in cmd and "REWRITE" in cmd:
            return _make_completed_proc(returncode=0, stdout="OK")
        if "CONFIG" in cmd and "GET" in cmd and "dir" in cmd:
            return _make_completed_proc(returncode=0, stdout=f"dir\n{tmp_path}\n")
        if "CONFIG" in cmd and "GET" in cmd and "maxmemory-policy" in cmd:
            return _make_completed_proc(returncode=0, stdout="maxmemory-policy\nnoeviction\n")
        if "INFO" in cmd and "persistence" in cmd:
            # aof_enabled:0 — AOF did NOT take effect
            return _make_completed_proc(returncode=0, stdout="aof_enabled:0\n")
        return _make_completed_proc(returncode=0, stdout="OK")

    with patch("shutil.which", return_value="/usr/bin/redis-cli"):
        with patch("subprocess.run", side_effect=fake_run):
            result = apply_redis_persistence()

    # Must NOT raise — non-fatal
    assert result is not None
    # Success is False because the post-condition failed
    assert result.success is False
    assert result.action == "failed"
    assert result.error is not None


# ---------------------------------------------------------------------------
# OSError from subprocess → non-fatal
# ---------------------------------------------------------------------------


def test_oserror_from_subprocess_is_non_fatal():
    """OSError (e.g. redis-cli binary missing at runtime) returns failed, never raises."""
    from scripts.update.redis_persistence import apply_redis_persistence

    with patch("shutil.which", return_value="/usr/bin/redis-cli"):
        with patch("subprocess.run", side_effect=OSError("Exec format error")):
            result = apply_redis_persistence()

    assert result.action in ("skipped", "failed")
    assert result.success is False
    assert result.error is not None
    # The function must NOT propagate the exception
