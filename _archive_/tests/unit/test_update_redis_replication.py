"""Unit tests for scripts/update/redis_replication.py (Fix #5 — #1827).

All subprocess calls and the role-gate marker are mocked — no real redis-cli
invocations and no real Redis. The module is BOOTSTRAP-ONLY / seed-once and must
NEVER ``CONFIG SET replicaof`` on a node reporting ``role:master``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import scripts.update.redis_replication as rr
from scripts.update.redis_replication import apply_redis_replication

VALID_ACTIONS = {"applied", "applied_with_warning", "skipped", "failed"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _assert_no_config_set_replicaof(mock_run: MagicMock) -> None:
    """Assert the module never issued CONFIG SET replicaof — the HARD INVARIANT."""
    for call in mock_run.call_args_list:
        args = call.args[0] if call.args else call.kwargs.get("args", [])
        upper = [str(a).upper() for a in args]
        assert not ("CONFIG" in upper and "SET" in upper and "REPLICAOF" in upper), (
            f"HARD INVARIANT violated: CONFIG SET replicaof issued in {args!r}"
        )


# ---------------------------------------------------------------------------
# Import smoke test
# ---------------------------------------------------------------------------


def test_module_importable():
    from scripts.update import redis_replication  # noqa: F401


# ---------------------------------------------------------------------------
# Role gate: marker absent → skipped (client-only machine, the common case)
# ---------------------------------------------------------------------------


def test_skip_when_marker_absent(tmp_path: Path):
    absent = tmp_path / "nope-marker"
    mock_run = MagicMock()
    with patch.object(rr, "REPLICATION_MARKER_FILE", absent):
        with patch("subprocess.run", mock_run):
            with patch("shutil.which", return_value="/usr/bin/redis-cli"):
                result = apply_redis_replication()

    assert result.action == "skipped"
    assert result.success is False
    # Role gate short-circuits before any subprocess call.
    mock_run.assert_not_called()
    _assert_no_config_set_replicaof(mock_run)


# ---------------------------------------------------------------------------
# redis-cli absent → skipped
# ---------------------------------------------------------------------------


def test_skip_when_redis_cli_absent(tmp_path: Path):
    marker = _present_marker(tmp_path)
    with patch.object(rr, "REPLICATION_MARKER_FILE", marker):
        with patch("shutil.which", return_value=None):
            result = apply_redis_replication()

    assert result.action == "skipped"
    assert result.success is False
    assert result.error is not None and "redis-cli" in result.error.lower()


# ---------------------------------------------------------------------------
# Redis down → skipped, no raise (non-fatal)
# ---------------------------------------------------------------------------


def test_skip_when_redis_down(tmp_path: Path):
    marker = _present_marker(tmp_path)
    ping_fail = _proc(returncode=1, stderr="Could not connect to Redis")

    mock_run = MagicMock(return_value=ping_fail)
    with patch.object(rr, "REPLICATION_MARKER_FILE", marker):
        with patch("shutil.which", return_value="/usr/bin/redis-cli"):
            with patch("subprocess.run", mock_run):
                result = apply_redis_replication()

    assert result.action == "skipped"
    assert result.success is False
    _assert_no_config_set_replicaof(mock_run)


def test_no_raise_when_ping_oserror(tmp_path: Path):
    marker = _present_marker(tmp_path)
    with patch.object(rr, "REPLICATION_MARKER_FILE", marker):
        with patch("shutil.which", return_value="/usr/bin/redis-cli"):
            with patch("subprocess.run", side_effect=OSError("boom")):
                result = apply_redis_replication()

    assert result.action == "skipped"
    assert result.success is False


# ---------------------------------------------------------------------------
# Established master (role:master + connected replicas) → skipped, NEVER mutates
# ---------------------------------------------------------------------------


def test_skip_and_never_demote_established_master(tmp_path: Path):
    marker = _present_marker(tmp_path)

    def fake_run(cmd, **kwargs):
        if "ping" in cmd:
            return _proc(stdout="PONG")
        if "SENTINEL" in cmd:
            # No Sentinel monitoring → force the role:master branch specifically.
            return _proc(returncode=1, stderr="could not connect to sentinel")
        if "INFO" in cmd and "replication" in cmd:
            return _proc(stdout="role:master\r\nconnected_slaves:1\r\n")
        return _proc(stdout="OK")

    mock_run = MagicMock(side_effect=fake_run)
    with patch.object(rr, "REPLICATION_MARKER_FILE", marker):
        with patch("shutil.which", return_value="/usr/bin/redis-cli"):
            with patch("subprocess.run", mock_run):
                result = apply_redis_replication()

    assert result.action == "skipped"
    assert result.success is True  # established topology, intentional no-op
    # The CORE invariant: never demote a promoted master.
    _assert_no_config_set_replicaof(mock_run)


def test_skip_when_sentinel_already_monitors(tmp_path: Path):
    marker = _present_marker(tmp_path)

    def fake_run(cmd, **kwargs):
        if "ping" in cmd:
            return _proc(stdout="PONG")
        if "SENTINEL" in cmd:
            return _proc(returncode=0, stdout="name\nvalor-redis\n")
        if "INFO" in cmd and "replication" in cmd:
            return _proc(stdout="role:master\r\nconnected_slaves:0\r\n")
        return _proc(stdout="OK")

    mock_run = MagicMock(side_effect=fake_run)
    with patch.object(rr, "REPLICATION_MARKER_FILE", marker):
        with patch("shutil.which", return_value="/usr/bin/redis-cli"):
            with patch("subprocess.run", mock_run):
                result = apply_redis_replication()

    assert result.action == "skipped"
    assert result.success is True
    _assert_no_config_set_replicaof(mock_run)


def test_skip_when_already_replica(tmp_path: Path):
    marker = _present_marker(tmp_path)

    def fake_run(cmd, **kwargs):
        if "ping" in cmd:
            return _proc(stdout="PONG")
        if "SENTINEL" in cmd:
            return _proc(returncode=1)
        if "INFO" in cmd and "replication" in cmd:
            return _proc(stdout="role:slave\r\nmaster_link_status:up\r\n")
        return _proc(stdout="OK")

    mock_run = MagicMock(side_effect=fake_run)
    with patch.object(rr, "REPLICATION_MARKER_FILE", marker):
        with patch("shutil.which", return_value="/usr/bin/redis-cli"):
            with patch("subprocess.run", mock_run):
                result = apply_redis_replication()

    assert result.action == "skipped"
    assert result.success is True
    _assert_no_config_set_replicaof(mock_run)


# ---------------------------------------------------------------------------
# Virgin opted-in node → seeds a file-only stub (applied_with_warning), NEVER CONFIG SET
# ---------------------------------------------------------------------------


def test_seed_virgin_node_stages_stub(tmp_path: Path):
    marker = _present_marker(tmp_path)
    conf_dir = tmp_path / "redis-data"
    conf_dir.mkdir()

    def fake_run(cmd, **kwargs):
        if "ping" in cmd:
            return _proc(stdout="PONG")
        if "SENTINEL" in cmd:
            return _proc(returncode=1)
        if "INFO" in cmd and "replication" in cmd:
            return _proc(stdout="role:master\r\nconnected_slaves:0\r\n")
        if "CONFIG" in cmd and "GET" in cmd and "dir" in cmd:
            return _proc(stdout=f"dir\n{conf_dir}\n")
        return _proc(stdout="OK")

    mock_run = MagicMock(side_effect=fake_run)
    with patch.object(rr, "REPLICATION_MARKER_FILE", marker):
        with patch("shutil.which", return_value="/usr/bin/redis-cli"):
            with patch("subprocess.run", mock_run):
                result = apply_redis_replication()

    assert result.action == "applied_with_warning"
    assert result.success is True
    assert result.warning is not None

    staged = conf_dir / "redis-replica.conf"
    assert staged.exists(), "virgin node should stage a redis-replica.conf stub"
    content = staged.read_text()
    assert "replicaof <PRIMARY_HOST> <PRIMARY_PORT>" in content
    assert "replica-read-only yes" in content
    assert "appendonly yes" in content
    # Seeding is file-only — the running master was never mutated.
    _assert_no_config_set_replicaof(mock_run)


def test_seed_failed_when_config_dir_undeterminable(tmp_path: Path):
    marker = _present_marker(tmp_path)

    def fake_run(cmd, **kwargs):
        if "ping" in cmd:
            return _proc(stdout="PONG")
        if "SENTINEL" in cmd:
            return _proc(returncode=1)
        if "INFO" in cmd and "replication" in cmd:
            return _proc(stdout="role:master\r\nconnected_slaves:0\r\n")
        if "CONFIG" in cmd and "GET" in cmd and "dir" in cmd:
            return _proc(returncode=1, stderr="nope")
        return _proc(stdout="OK")

    mock_run = MagicMock(side_effect=fake_run)
    with patch.object(rr, "REPLICATION_MARKER_FILE", marker):
        with patch("shutil.which", return_value="/usr/bin/redis-cli"):
            with patch("subprocess.run", mock_run):
                result = apply_redis_replication()

    assert result.action == "failed"
    assert result.success is False
    assert result.error is not None
    _assert_no_config_set_replicaof(mock_run)


# ---------------------------------------------------------------------------
# Contract: action is always one of the four valid values; never raises
# ---------------------------------------------------------------------------


def test_all_actions_are_valid_and_non_fatal(tmp_path: Path):
    marker = _present_marker(tmp_path)
    conf_dir = tmp_path / "rd"
    conf_dir.mkdir()

    # marker absent → skipped
    with patch.object(rr, "REPLICATION_MARKER_FILE", tmp_path / "missing"):
        assert apply_redis_replication().action in VALID_ACTIONS

    # redis-cli absent → skipped
    with patch.object(rr, "REPLICATION_MARKER_FILE", marker):
        with patch("shutil.which", return_value=None):
            assert apply_redis_replication().action in VALID_ACTIONS

    # virgin seed → applied_with_warning
    def fake_run(cmd, **kwargs):
        if "ping" in cmd:
            return _proc(stdout="PONG")
        if "SENTINEL" in cmd:
            return _proc(returncode=1)
        if "INFO" in cmd and "replication" in cmd:
            return _proc(stdout="role:master\r\nconnected_slaves:0\r\n")
        if "CONFIG" in cmd and "GET" in cmd and "dir" in cmd:
            return _proc(stdout=f"dir\n{conf_dir}\n")
        return _proc(stdout="OK")

    with patch.object(rr, "REPLICATION_MARKER_FILE", marker):
        with patch("shutil.which", return_value="/usr/bin/redis-cli"):
            with patch("subprocess.run", side_effect=fake_run):
                assert apply_redis_replication().action in VALID_ACTIONS
