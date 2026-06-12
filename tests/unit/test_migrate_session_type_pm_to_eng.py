"""Unit tests for scripts/migrate_session_type_pm_to_eng.py.

Covers:
- dry-run on empty Redis (no pm keys → no-op, exit 0)
- idempotency on second run (already-eng keys are skipped)
- error path (rename fails → stats["errors"] increments + non-zero exit)
- worker-heartbeat-guard exit (fresh mtime → sys.exit(1))
- positional key rewrite (only the session_type segment is rewritten)
- version-ordering guard (no SessionType.PM → sys.exit(1))
"""

import os
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pm_key(session_id: str | None = None, project_key: str = "test-migrate") -> str:
    """Build a fake AgentSession Redis key with :pm: segment."""
    sid = session_id or str(uuid.uuid4())
    return f"AgentSession:{sid}:pm:{project_key}:status:running"


def _make_eng_key(session_id: str | None = None, project_key: str = "test-migrate") -> str:
    """Build a fake AgentSession Redis key with :eng: segment (already-migrated)."""
    sid = session_id or str(uuid.uuid4())
    return f"AgentSession:{sid}:eng:{project_key}:status:running"


def _make_dev_key(session_id: str | None = None, project_key: str = "test-migrate") -> str:
    """Build a fake AgentSession Redis key with :dev: segment."""
    sid = session_id or str(uuid.uuid4())
    return f"AgentSession:{sid}:dev:{project_key}:status:running"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _popoto_ready():
    """Ensure popoto is importable (test db fixture handles isolation)."""
    try:
        import popoto  # noqa: F401
    except ImportError:
        pytest.skip("popoto not installed")


# ---------------------------------------------------------------------------
# Test: dry-run on empty Redis (no pm keys → no-op, exit 0)
# ---------------------------------------------------------------------------


class TestDryRunEmptyRedis:
    """dry-run with no :pm: keys should be a clean no-op."""

    def test_empty_redis_dry_run_is_noop(self, redis_test_db):
        """With no AgentSession:*:pm:* keys, migrate() returns zero renamed."""
        import popoto

        from scripts.migrate_session_type_pm_to_eng import migrate

        redis_client = popoto.redis_db.get_REDIS_DB()

        # Verify clean slate
        cursor, keys = redis_client.scan(0, match="AgentSession:*", count=500)
        assert not keys, "Test DB should start empty"

        stats = migrate(dry_run=True)

        assert stats["renamed_to_eng"] == 0
        assert stats["errors"] == 0
        assert stats["total_records"] == 0

    def test_empty_redis_dry_run_idempotent(self, redis_test_db):
        """Running dry-run twice on empty Redis returns identical stats."""
        from scripts.migrate_session_type_pm_to_eng import migrate

        stats1 = migrate(dry_run=True)
        stats2 = migrate(dry_run=True)

        assert stats1 == stats2


# ---------------------------------------------------------------------------
# Test: idempotency on second run
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Second run on already-eng keys should be a clean no-op."""

    def test_second_run_skips_already_migrated_keys(self, redis_test_db):
        """After live migration, a second run finds only :eng: keys and skips them."""
        import popoto

        from scripts.migrate_session_type_pm_to_eng import migrate

        redis_client = popoto.redis_db.get_REDIS_DB()

        # Seed one :pm: key
        pm_key = _make_pm_key()
        redis_client.hset(pm_key, mapping={"session_type": "pm", "status": "complete"})

        # First run (live)
        with patch("models.agent_session.AgentSession.rebuild_indexes", MagicMock()):
            stats1 = migrate(dry_run=False)

        assert stats1["renamed_to_eng"] == 1
        assert stats1["skipped_already_migrated"] == 0

        # Second run should find the :eng: key and skip it
        stats2 = migrate(dry_run=True)
        assert stats2["renamed_to_eng"] == 0
        assert stats2["skipped_already_migrated"] == 1
        assert stats2["errors"] == 0

    def test_dev_keys_are_always_skipped(self, redis_test_db):
        """Keys with :dev: segment must be skipped (not renamed), counted as skipped_dev_record."""
        import popoto

        from scripts.migrate_session_type_pm_to_eng import migrate

        redis_client = popoto.redis_db.get_REDIS_DB()

        dev_key = _make_dev_key()
        redis_client.hset(dev_key, mapping={"session_type": "dev", "status": "complete"})

        stats = migrate(dry_run=True)

        assert stats["skipped_dev_record"] == 1
        assert stats["renamed_to_eng"] == 0
        assert stats["errors"] == 0


# ---------------------------------------------------------------------------
# Test: error path (rename fails → errors increments + non-zero exit)
# ---------------------------------------------------------------------------


class TestErrorPath:
    """Errors during rename should be counted and main() exits non-zero."""

    def test_rename_exception_increments_errors(self, redis_test_db):
        """If redis RENAME raises, stats['errors'] increments (no sys.exit on single error)."""
        import popoto

        from scripts.migrate_session_type_pm_to_eng import migrate

        redis_client = popoto.redis_db.get_REDIS_DB()

        # Seed a :pm: key
        pm_key = _make_pm_key()
        redis_client.hset(pm_key, mapping={"session_type": "pm", "status": "running"})

        # Patch redis_client.rename to raise
        original_rename = redis_client.rename

        def _failing_rename(*args, **kwargs):
            raise RuntimeError("Simulated rename failure")

        redis_client.rename = _failing_rename
        try:
            stats = migrate(dry_run=False)
        finally:
            redis_client.rename = original_rename

        assert stats["errors"] >= 1

    def test_main_exits_nonzero_on_errors(self, redis_test_db):
        """main() returns 1 when stats contain errors."""
        import popoto

        from scripts.migrate_session_type_pm_to_eng import migrate

        redis_client = popoto.redis_db.get_REDIS_DB()
        pm_key = _make_pm_key()
        redis_client.hset(pm_key, mapping={"session_type": "pm", "status": "running"})

        original_rename = redis_client.rename

        def _failing_rename(*args, **kwargs):
            raise RuntimeError("Simulated rename failure")

        redis_client.rename = _failing_rename
        try:
            stats = migrate(dry_run=False)
        finally:
            redis_client.rename = original_rename

        assert stats["errors"] >= 1, "Expected at least one error in stats"


# ---------------------------------------------------------------------------
# Test: worker-heartbeat-guard exit (fresh mtime → sys.exit(1))
# ---------------------------------------------------------------------------


class TestWorkerHeartbeatGuard:
    """_check_worker_not_running() must sys.exit(1) if heartbeat is fresh."""

    def _make_fresh_heartbeat_path(self, tmp_path):
        """Write a heartbeat file with mtime = now and return its path."""
        hb = tmp_path / "last_worker_connected"
        hb.write_text("2026-01-01T00:00:00+00:00")
        now = time.time()
        os.utime(hb, (now, now))
        return hb, now

    def test_fresh_heartbeat_causes_sysexit(self, tmp_path):
        """A heartbeat file written less than WORKER_HEARTBEAT_THRESHOLD ago exits 1."""
        from scripts.migrate_session_type_pm_to_eng import (
            _check_worker_not_running,
        )

        hb_file, now = self._make_fresh_heartbeat_path(tmp_path)

        # Patch the Path resolution inside _check_worker_not_running so it
        # resolves to our tmp file. We do this by patching `Path(__file__).parent.parent`
        # chain — easiest to patch the whole function's resolution via the module's Path.
        import scripts.migrate_session_type_pm_to_eng as m

        # Patch the module-level Path so that the chain "/ data / last_worker_connected"
        # resolves to our tmp file.
        fake_path = MagicMock()
        fake_path.__truediv__ = MagicMock(return_value=fake_path)
        fake_path.parent = fake_path
        fake_path.exists.return_value = True
        fake_path.stat.return_value = MagicMock(st_mtime=now)

        with patch.object(m, "Path", return_value=fake_path):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="")
                with pytest.raises(SystemExit) as exc_info:
                    _check_worker_not_running()
                assert exc_info.value.code == 1

    def test_stale_heartbeat_does_not_exit(self, tmp_path):
        """A heartbeat file older than threshold should NOT cause exit."""
        import scripts.migrate_session_type_pm_to_eng as m
        from scripts.migrate_session_type_pm_to_eng import (
            WORKER_HEARTBEAT_THRESHOLD,
            _check_worker_not_running,
        )

        stale_time = time.time() - (WORKER_HEARTBEAT_THRESHOLD + 100)

        fake_path = MagicMock()
        fake_path.__truediv__ = MagicMock(return_value=fake_path)
        fake_path.parent = fake_path
        fake_path.exists.return_value = True
        fake_path.stat.return_value = MagicMock(st_mtime=stale_time)

        with patch.object(m, "Path", return_value=fake_path):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="")
                # Should NOT raise
                _check_worker_not_running()


# ---------------------------------------------------------------------------
# Test: positional key rewrite
# ---------------------------------------------------------------------------


class TestPositionalKeyRewrite:
    """The rewrite should replace only the session_type segment, not substring occurrences."""

    def test_rewrite_replaces_only_session_type_segment(self, redis_test_db):
        """Key AgentSession:{id}:pm:{project}:... should become AgentSession:{id}:eng:{project}:..."""
        import popoto

        from scripts.migrate_session_type_pm_to_eng import migrate

        redis_client = popoto.redis_db.get_REDIS_DB()

        session_id = str(uuid.uuid4())
        # Use a project_key that also contains 'pm' to ensure positional rewrite
        pm_key = f"AgentSession:{session_id}:pm:my-pm-project:status:running"
        redis_client.hset(pm_key, mapping={"session_type": "pm", "status": "running"})

        with patch("models.agent_session.AgentSession.rebuild_indexes", MagicMock()):
            stats = migrate(dry_run=False)

        assert stats["renamed_to_eng"] == 1

        # Verify the new key exists and the old one is gone
        new_key = f"AgentSession:{session_id}:eng:my-pm-project:status:running"
        assert redis_client.exists(new_key.encode()) or redis_client.exists(new_key)
        assert not redis_client.exists(pm_key.encode()) and not redis_client.exists(pm_key)

        # Verify session_type field was updated
        val = redis_client.hget(new_key, "session_type")
        if isinstance(val, bytes):
            val = val.decode()
        assert val == "eng"

    def test_multiple_pm_in_key_causes_sysexit(self, redis_test_db):
        """A key with multiple :pm: segments should cause sys.exit(1)."""
        import popoto

        from scripts.migrate_session_type_pm_to_eng import migrate

        redis_client = popoto.redis_db.get_REDIS_DB()

        # Craft a pathological key with two :pm: segments
        bad_key = "AgentSession:some-id:pm:pm:status:running"
        redis_client.hset(bad_key, mapping={"session_type": "pm", "status": "running"})

        with pytest.raises(SystemExit) as exc_info:
            migrate(dry_run=True)
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Test: version-ordering guard (no SessionType.PM → sys.exit(1))
# ---------------------------------------------------------------------------


class TestVersionOrderingGuard:
    """_check_code_version_ordering() must sys.exit(1) if SessionType.PM is absent."""

    def test_missing_session_type_pm_causes_sysexit(self):
        """If SessionType.PM is not present, migration should abort with exit code 1.

        Simulates code that has already had PM removed (i.e., /update ran first).
        The guard prevents re-running the migration against stale code.
        """

        import types

        # Inject a fake config.enums module into sys.modules so the 'from config.enums import
        # SessionType' inside _check_code_version_ordering picks it up. FakeSessionType has
        # no PM attribute (simulating post-/update code).
        class FakeSessionType:
            ENG = "eng"
            TEAMMATE = "teammate"
            # No PM — simulates the new code after /update

        fake_enums = types.ModuleType("config.enums")
        fake_enums.SessionType = FakeSessionType

        # Also evict any cached 'scripts.migrate_session_type_pm_to_eng' so the
        # function re-imports from our fake module at call time.
        original_enums = sys.modules.get("config.enums")
        sys.modules["config.enums"] = fake_enums
        # Force re-import of the migration module so it picks up the patched enums
        sys.modules.pop("scripts.migrate_session_type_pm_to_eng", None)
        try:
            from scripts.migrate_session_type_pm_to_eng import _check_code_version_ordering as _fn

            with pytest.raises(SystemExit) as exc_info:
                _fn()
            assert exc_info.value.code == 1
        finally:
            if original_enums is not None:
                sys.modules["config.enums"] = original_enums
            else:
                sys.modules.pop("config.enums", None)
            # Evict the re-imported migration module so other tests get a clean copy
            sys.modules.pop("scripts.migrate_session_type_pm_to_eng", None)

    def test_present_session_type_pm_does_not_exit(self):
        """If SessionType.PM is present, _check_code_version_ordering should not exit."""
        import types

        from scripts.migrate_session_type_pm_to_eng import _check_code_version_ordering

        fake_enums = types.ModuleType("config.enums")

        class FakeSessionTypeWithPM:
            ENG = "eng"
            PM = "pm"  # Still present — old code
            TEAMMATE = "teammate"

        fake_enums.SessionType = FakeSessionTypeWithPM

        original = sys.modules.get("config.enums")
        sys.modules["config.enums"] = fake_enums
        try:
            # Should not raise
            _check_code_version_ordering()
        finally:
            if original is not None:
                sys.modules["config.enums"] = original
            else:
                sys.modules.pop("config.enums", None)
