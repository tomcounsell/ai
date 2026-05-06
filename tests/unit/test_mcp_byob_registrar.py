"""Unit tests for scripts/update/mcp_byob.py.

Modeled directly on tests/unit/test_update_mcp_memory.py. Verifies:
  - idempotent verify_byob_mcp(write=True) installs the entry on a fresh
    file and is a no-op on re-run
  - drift detection in --verify mode (write=False) reports without writing
  - drift heal: a wrong BYOB_ALLOW_EVAL value gets corrected back to "1" on next run
  - lock contention triggers the 3-attempt backoff path
  - atomic backup -> tmp -> rename pattern
  - other mcpServers entries (e.g. memory) preserved
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from threading import Thread

import pytest


@pytest.fixture
def claude_config_path(tmp_path, monkeypatch):
    """Redirect mcp_byob's claude config + lock + backup to tmp_path.

    Also redirects the BYOB binary paths to existing files in tmp_path so
    ``verify_byob_mcp()``'s existence gate sees the binaries as installed.
    Tests covering the not-installed case use ``byob_not_installed`` instead.
    """
    from scripts.update import mcp_byob

    cfg = tmp_path / "claude.json"
    lock = tmp_path / "claude.json.lock"
    bak = tmp_path / "claude.json.bak"
    monkeypatch.setattr(mcp_byob, "CLAUDE_CONFIG_PATH", cfg)
    monkeypatch.setattr(mcp_byob, "CLAUDE_CONFIG_LOCK_PATH", lock)
    monkeypatch.setattr(mcp_byob, "CLAUDE_CONFIG_BACKUP_PATH", bak)

    # Put fake binaries under a `.byob` dir so the existing smoke check
    # `assert "/.byob/" in entry["command"]` continues to validate that the
    # entry points under a BYOB home, not just any path.
    fake_byob = tmp_path / ".byob"
    fake_byob.mkdir()
    fake_tsx = fake_byob / "tsx"
    fake_ts = fake_byob / "byob-mcp.ts"
    fake_tsx.touch()
    fake_ts.touch()
    monkeypatch.setattr(mcp_byob, "BYOB_TSX_BIN", fake_tsx)
    monkeypatch.setattr(mcp_byob, "BYOB_MCP_SERVER_TS", fake_ts)

    return cfg


@pytest.fixture
def byob_not_installed(tmp_path, monkeypatch):
    """Same as ``claude_config_path`` but BYOB binaries are absent.

    Used to verify the existence gate: a machine that pulls this code via
    ``/update`` but has not run ``/setup`` Step 8.5 yet must NOT get a
    ``mcpServers.byob`` entry written.
    """
    from scripts.update import mcp_byob

    cfg = tmp_path / "claude.json"
    lock = tmp_path / "claude.json.lock"
    bak = tmp_path / "claude.json.bak"
    monkeypatch.setattr(mcp_byob, "CLAUDE_CONFIG_PATH", cfg)
    monkeypatch.setattr(mcp_byob, "CLAUDE_CONFIG_LOCK_PATH", lock)
    monkeypatch.setattr(mcp_byob, "CLAUDE_CONFIG_BACKUP_PATH", bak)

    missing_dir = tmp_path / "byob-not-installed"
    monkeypatch.setattr(mcp_byob, "BYOB_TSX_BIN", missing_dir / "tsx")
    monkeypatch.setattr(mcp_byob, "BYOB_MCP_SERVER_TS", missing_dir / "byob-mcp.ts")

    return cfg


def test_install_on_fresh_file(claude_config_path):
    from scripts.update import mcp_byob

    # Pre-create with empty mcpServers to mimic a real config.
    claude_config_path.write_text(json.dumps({"mcpServers": {}}))

    result = mcp_byob.verify_byob_mcp(write=True)
    assert result.ok is True
    assert result.action in ("installed", "ok")

    config = json.loads(claude_config_path.read_text())
    assert "byob" in config["mcpServers"]
    entry = config["mcpServers"]["byob"]
    assert entry["type"] == "stdio"
    # BYOB v0.3+ ships a TS entry executed via tsx; both binaries live in
    # the workspace under ~/.byob/packages/mcp-server/.
    assert entry["command"].endswith("/tsx")
    assert "/.byob/" in entry["command"]
    assert len(entry["args"]) == 1
    assert "byob" in entry["args"][0]
    assert entry["args"][0].endswith("/byob-mcp.ts")
    # Project policy: BYOB_ALLOW_EVAL=1 (standard for browser testing)
    assert entry["env"]["BYOB_ALLOW_EVAL"] == "1"


def test_idempotent_no_op_when_correct(claude_config_path):
    from scripts.update import mcp_byob

    # First install
    claude_config_path.write_text(json.dumps({"mcpServers": {}}))
    first = mcp_byob.verify_byob_mcp(write=True)
    assert first.ok is True

    first_contents = claude_config_path.read_text()

    # Second run should be a no-op (action="ok").
    second = mcp_byob.verify_byob_mcp(write=True)
    assert second.ok is True
    assert second.action == "ok"

    # File contents unchanged on second run.
    assert claude_config_path.read_text() == first_contents


def test_drift_heal_corrects_eval_flag(claude_config_path):
    """A drifted BYOB_ALLOW_EVAL=0 must be corrected back to '1' on next run."""
    from scripts.update import mcp_byob

    # Drifted entry: eval disabled (legacy default; project now ships eval-on).
    claude_config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "byob": {
                        "type": "stdio",
                        "command": str(mcp_byob.BYOB_TSX_BIN),
                        "args": [str(mcp_byob.BYOB_MCP_SERVER_TS)],
                        "env": {"BYOB_ALLOW_EVAL": "0"},
                    }
                }
            }
        )
    )

    result = mcp_byob.verify_byob_mcp(write=True)
    assert result.ok is True
    assert result.action == "repaired"

    config = json.loads(claude_config_path.read_text())
    assert config["mcpServers"]["byob"]["env"]["BYOB_ALLOW_EVAL"] == "1"


def test_drift_heal_corrects_command(claude_config_path):
    """A drifted command name (wrong runtime) gets repaired."""
    from scripts.update import mcp_byob

    claude_config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "byob": {
                        "type": "stdio",
                        "command": "deno",
                        "args": ["wrong"],
                        "env": {"BYOB_ALLOW_EVAL": "1"},
                    }
                }
            }
        )
    )

    result = mcp_byob.verify_byob_mcp(write=True)
    assert result.ok is True
    assert result.action == "repaired"

    config = json.loads(claude_config_path.read_text())
    assert config["mcpServers"]["byob"]["command"] == str(mcp_byob.BYOB_TSX_BIN)


def test_verify_mode_reports_drift_without_writing(claude_config_path):
    from scripts.update import mcp_byob

    claude_config_path.write_text(json.dumps({"mcpServers": {}}))

    result = mcp_byob.verify_byob_mcp(write=False)
    assert result.ok is False
    assert result.action == "drift_detected"

    # File must be unchanged.
    config = json.loads(claude_config_path.read_text())
    assert "byob" not in config.get("mcpServers", {})


def test_verify_mode_reports_ok_when_correct(claude_config_path):
    from scripts.update import mcp_byob

    # Install once in write mode.
    claude_config_path.write_text(json.dumps({"mcpServers": {}}))
    mcp_byob.verify_byob_mcp(write=True)

    # Now verify-only should report ok.
    result = mcp_byob.verify_byob_mcp(write=False)
    assert result.ok is True
    assert result.action == "ok"


def test_atomic_write_creates_backup(claude_config_path):
    from scripts.update import mcp_byob

    claude_config_path.write_text(json.dumps({"mcpServers": {}, "marker": "byob-v1"}))
    mcp_byob.verify_byob_mcp(write=True)

    bak = mcp_byob.CLAUDE_CONFIG_BACKUP_PATH
    assert bak.exists()
    backup_data = json.loads(bak.read_text())
    assert backup_data.get("marker") == "byob-v1"


def test_other_servers_preserved(claude_config_path):
    from scripts.update import mcp_byob

    claude_config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "memory": {
                        "type": "stdio",
                        "command": "python3",
                        "args": ["-m", "mcp_servers.memory_server"],
                        "env": {"PYTHONPATH": "/some/repo"},
                    }
                },
                "otherKey": {"unrelated": True},
            }
        )
    )

    mcp_byob.verify_byob_mcp(write=True)
    config = json.loads(claude_config_path.read_text())
    # memory server preserved untouched.
    assert config["mcpServers"]["memory"]["command"] == "python3"
    # Top-level unrelated keys preserved.
    assert config.get("otherKey") == {"unrelated": True}
    # byob installed alongside.
    assert "byob" in config["mcpServers"]


def test_lock_contention_skips_after_retries(claude_config_path):
    """Hold an exclusive lock; verify_byob_mcp must give up cleanly after retries.

    The retry schedule (50/200/800ms) means total wait is well under 2s, so
    this test does not block the suite.
    """
    from scripts.update import mcp_byob

    claude_config_path.write_text(json.dumps({"mcpServers": {}}))

    # Hold the lock from a separate file descriptor.
    mcp_byob.CLAUDE_CONFIG_LOCK_PATH.touch()
    holder_fd = os.open(
        str(mcp_byob.CLAUDE_CONFIG_LOCK_PATH),
        os.O_RDWR | os.O_CREAT,
        0o644,
    )
    fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        start = time.monotonic()
        result = mcp_byob.verify_byob_mcp(write=True)
        elapsed = time.monotonic() - start
        assert result.ok is False
        assert result.action == "skipped"
        # Sanity: roughly the sum of the retry backoffs.
        assert elapsed >= 0.05  # at least the first retry slept 50ms
        assert elapsed < 5.0
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)


def test_concurrent_safe_write_under_load(claude_config_path):
    """Run multiple verify_byob_mcp calls concurrently; result must be consistent.

    This is not a torture test -- just a smoke check that two threads cannot
    leave ~/.claude.json in a half-written state.
    """
    from scripts.update import mcp_byob

    claude_config_path.write_text(json.dumps({"mcpServers": {}}))

    results = []

    def _run():
        results.append(mcp_byob.verify_byob_mcp(write=True))

    threads = [Thread(target=_run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # At least one of them succeeded (installed or ok). At most one
    # got "skipped" if the lock was contended for too long.
    actions = [r.action for r in results]
    assert any(a in ("installed", "ok") for a in actions), actions

    # Final file must be a valid JSON with byob registered.
    config = json.loads(claude_config_path.read_text())
    assert "byob" in config["mcpServers"]


# --- Existence gate (BYOB binaries missing) ---------------------------------
#
# These tests cover the scenario that motivated the gate: a machine pulls
# this code via the daily update cron but has never run /setup Step 8.5.
# Without the gate, `verify_byob_mcp(write=True)` writes a `mcpServers.byob`
# entry pointing at non-existent paths, and Claude Code logs MCP spawn
# failures on every session restart.


def test_skip_when_binaries_missing_and_no_entry(byob_not_installed):
    """Fresh machine, no ~/.byob/, no prior entry: nothing to do."""
    from scripts.update import mcp_byob

    byob_not_installed.write_text(json.dumps({"mcpServers": {}}))
    result = mcp_byob.verify_byob_mcp(write=True)
    assert result.ok is True
    assert result.action == "skipped"

    config = json.loads(byob_not_installed.read_text())
    assert "byob" not in config.get("mcpServers", {})


def test_skip_when_binaries_missing_and_config_absent(byob_not_installed):
    """Even rawer fresh machine: no ~/.claude.json yet, no ~/.byob/ either."""
    from scripts.update import mcp_byob

    # Do not pre-create the config file at all.
    assert not byob_not_installed.exists()

    result = mcp_byob.verify_byob_mcp(write=True)
    assert result.ok is True
    assert result.action == "skipped"

    # Registrar must NOT create ~/.claude.json on a fresh machine just to
    # write a useless empty mcpServers map.
    assert not byob_not_installed.exists()


def test_remove_stale_entry_when_binaries_missing(byob_not_installed):
    """BYOB was installed once, then ~/.byob/ was removed: drift heal in reverse."""
    from scripts.update import mcp_byob

    byob_not_installed.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "byob": {
                        "type": "stdio",
                        "command": "/old/path/to/tsx",
                        "args": ["/old/path/to/byob-mcp.ts"],
                        "env": {"BYOB_ALLOW_EVAL": "0"},
                    },
                    "memory": {
                        "type": "stdio",
                        "command": "python3",
                        "args": ["-m", "mcp_servers.memory_server"],
                        "env": {},
                    },
                }
            }
        )
    )

    result = mcp_byob.verify_byob_mcp(write=True)
    assert result.ok is True
    assert result.action == "removed"

    config = json.loads(byob_not_installed.read_text())
    assert "byob" not in config["mcpServers"]
    # Sibling entries must be preserved.
    assert config["mcpServers"]["memory"]["command"] == "python3"


def test_verify_mode_reports_drift_for_stale_entry(byob_not_installed):
    """`/update --verify` (write=False) must surface the stale entry as drift."""
    from scripts.update import mcp_byob

    byob_not_installed.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "byob": {
                        "type": "stdio",
                        "command": "/old/tsx",
                        "args": ["/old/byob-mcp.ts"],
                        "env": {"BYOB_ALLOW_EVAL": "0"},
                    }
                }
            }
        )
    )

    result = mcp_byob.verify_byob_mcp(write=False)
    assert result.ok is False
    assert result.action == "drift_detected"

    # File unchanged in verify mode.
    config = json.loads(byob_not_installed.read_text())
    assert "byob" in config["mcpServers"]


def test_verify_mode_ok_when_binaries_missing_and_no_entry(byob_not_installed):
    """Clean state on a non-BYOB machine: verify mode reports ok-skipped."""
    from scripts.update import mcp_byob

    byob_not_installed.write_text(json.dumps({"mcpServers": {}}))
    result = mcp_byob.verify_byob_mcp(write=False)
    assert result.ok is True
    assert result.action == "skipped"
