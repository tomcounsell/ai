"""Tests for the standalone reflection scheduler subprocess entry point.

Covers ``python -m reflections`` (``reflections/__main__.py``):
  - ``--dry-run`` loads the registry, prints status, exits 0
  - the per-tick heartbeat file is written on every tick, and a write
    failure is swallowed (logged, never fatal)
  - the boot start-record (`_record_boot`) is resilient and atomic:
    absent file -> count=1; corrupt file -> WARNING logged, count is NOT
    reset to 1, `last_start_ts` is refreshed; the write goes through
    `os.replace` (atomic rename)
  - SIGTERM triggers a clean shutdown (no exception escapes, the scheduler
    task is cancelled and awaited)
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import reflections.__main__ as reflections_main
from agent.reflection_scheduler import ReflectionScheduler


@pytest.fixture
def isolated_data_paths(tmp_path, monkeypatch):
    """Redirect the module's data-file constants into a scratch tmp_path.

    Prevents tests from reading/writing the real repo `data/` heartbeat
    files.
    """
    data_dir = tmp_path / "data"
    heartbeat = data_dir / "last_reflection_tick"
    starts = data_dir / "reflection_worker_starts"
    monkeypatch.setattr(reflections_main, "_DATA", data_dir)
    monkeypatch.setattr(reflections_main, "_HEARTBEAT", heartbeat)
    monkeypatch.setattr(reflections_main, "_STARTS", starts)
    return data_dir, heartbeat, starts


@pytest.fixture
def empty_registry_env(tmp_path, monkeypatch):
    """Point REFLECTIONS_YAML at an empty registry.

    Keeps scheduler.tick()/start() hermetic — no real reflections fire
    during tests that exercise the tick loop or the signal-shutdown path.
    """
    empty_yaml = tmp_path / "empty_reflections.yaml"
    empty_yaml.write_text("reflections: []\n")
    monkeypatch.setenv("REFLECTIONS_YAML", str(empty_yaml))
    return empty_yaml


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_loads_registry_and_prints_status(self, empty_registry_env, capsys):
        """Direct function call: --dry-run loads the registry and prints status."""
        await reflections_main._run(dry_run=True)
        out = capsys.readouterr().out
        assert "No reflections registered." in out

    def test_dry_run_subprocess_exits_zero(self, empty_registry_env):
        """Smoke test: `python -m reflections --dry-run` exits 0 as a real subprocess."""
        env = dict(os.environ)
        env["REFLECTIONS_YAML"] = str(empty_registry_env)
        result = subprocess.run(
            [sys.executable, "-m", "reflections", "--dry-run"],
            cwd=Path(__file__).parent.parent.parent,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "Reflections" in result.stdout or "No reflections" in result.stdout


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    def test_write_heartbeat_creates_file_with_timestamp(self, isolated_data_paths):
        _data_dir, heartbeat, _starts = isolated_data_paths
        before = time.time()
        reflections_main._write_heartbeat()
        assert heartbeat.exists()
        written = float(heartbeat.read_text())
        assert written >= before

    @pytest.mark.asyncio
    async def test_wrapped_tick_writes_heartbeat_each_call(
        self, isolated_data_paths, empty_registry_env
    ):
        _data_dir, heartbeat, _starts = isolated_data_paths
        scheduler = ReflectionScheduler()
        scheduler.load()
        wrapped = reflections_main._wrap_tick_with_heartbeat(scheduler)

        await wrapped()
        first = heartbeat.read_text()
        assert first  # written on first tick

        time.sleep(0.01)
        await wrapped()
        second = heartbeat.read_text()
        assert float(second) > float(first)  # refreshed on second tick

    def test_heartbeat_oserror_is_swallowed_and_logged(self, isolated_data_paths, caplog):
        _data_dir, heartbeat, _starts = isolated_data_paths
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            reflections_main._write_heartbeat()  # must not raise
        assert any(
            "heartbeat write failed" in record.message.lower()
            or "heartbeat write failed" in str(record.msg).lower()
            for record in caplog.records
        )
        assert not heartbeat.exists()

    @pytest.mark.asyncio
    async def test_tick_loop_continues_after_heartbeat_write_failure(
        self, isolated_data_paths, empty_registry_env, caplog
    ):
        """A heartbeat write failure (real OSError from write_text) must not
        crash the wrapped tick — it logs a WARNING and the tick still
        completes and returns its normal (int) result."""
        scheduler = ReflectionScheduler()
        scheduler.load()
        wrapped = reflections_main._wrap_tick_with_heartbeat(scheduler)

        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            result = await wrapped()  # must not raise

        assert isinstance(result, int)
        assert any(
            "heartbeat write failed" in str(record.msg).lower()
            or "heartbeat write failed" in record.message.lower()
            for record in caplog.records
        )


# ---------------------------------------------------------------------------
# _record_boot
# ---------------------------------------------------------------------------


class TestRecordBoot:
    def test_absent_file_starts_count_at_one(self, isolated_data_paths):
        _data_dir, _heartbeat, starts = isolated_data_paths
        assert not starts.exists()
        reflections_main._record_boot()
        payload = json.loads(starts.read_text())
        assert payload["count"] == 1
        assert isinstance(payload["last_start_ts"], float)

    def test_corrupt_file_does_not_reset_count_to_one(self, isolated_data_paths, caplog):
        _data_dir, _heartbeat, starts = isolated_data_paths
        starts.parent.mkdir(parents=True, exist_ok=True)
        starts.write_text("{not valid json!!")

        before = time.time()
        reflections_main._record_boot()

        payload = json.loads(starts.read_text())
        assert payload["count"] != 1, "corrupt file must not reset the crash-loop counter to 1"
        assert payload["count"] >= 2
        assert payload["last_start_ts"] >= before
        assert any(
            "corrupt" in str(record.msg).lower() or "corrupt" in record.message.lower()
            for record in caplog.records
        )

    def test_valid_prior_count_is_preserved_and_incremented(self, isolated_data_paths):
        _data_dir, _heartbeat, starts = isolated_data_paths
        starts.parent.mkdir(parents=True, exist_ok=True)
        starts.write_text(json.dumps({"count": 41, "last_start_ts": time.time() - 3600}))

        reflections_main._record_boot()

        payload = json.loads(starts.read_text())
        assert payload["count"] == 42

    def test_record_boot_writes_atomically_via_os_replace(self, isolated_data_paths):
        _data_dir, _heartbeat, starts = isolated_data_paths
        with patch.object(reflections_main.os, "replace", wraps=os.replace) as mock_replace:
            reflections_main._record_boot()
        assert mock_replace.called
        args, _kwargs = mock_replace.call_args
        assert str(args[1]) == str(starts)
        # No leftover temp file after a successful atomic replace.
        leftovers = list(starts.parent.glob("*.tmp*"))
        assert leftovers == []

    def test_record_boot_write_failure_is_logged_not_fatal(self, isolated_data_paths, caplog):
        _data_dir, _heartbeat, starts = isolated_data_paths
        with patch.object(Path, "write_text", side_effect=OSError("read-only fs")):
            reflections_main._record_boot()  # must not raise
        assert any(
            "start-record write failed" in str(record.msg).lower()
            or "start-record write failed" in record.message.lower()
            for record in caplog.records
        )


# ---------------------------------------------------------------------------
# SIGTERM shutdown
# ---------------------------------------------------------------------------


class TestSignalShutdown:
    @pytest.mark.asyncio
    async def test_sigterm_triggers_clean_shutdown(self, isolated_data_paths, empty_registry_env):
        """Firing SIGTERM against a running `_run()` shuts it down cleanly.

        No exception should escape `_run`, and it must return promptly once
        the signal fires (the scheduler task is cancelled and awaited).
        """
        import asyncio

        task = asyncio.create_task(reflections_main._run(dry_run=False))
        await asyncio.sleep(0.1)  # let the scheduler start + register handlers
        os.kill(os.getpid(), signal.SIGTERM)

        try:
            await asyncio.wait_for(task, timeout=10)
        except TimeoutError:
            task.cancel()
            pytest.fail("_run() did not shut down within 10s of SIGTERM")

        assert task.done()
        assert task.exception() is None
