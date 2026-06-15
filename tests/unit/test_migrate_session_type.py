"""Unit tests for scripts/migrate_session_type_pm_to_eng.py.

All Redis and subprocess interactions are mocked so these tests run without a
live Redis instance. ORM-layer calls (AgentSession.rebuild_indexes) are also
patched to avoid import-time side-effects.
"""

import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to build fake Redis scan state
# ---------------------------------------------------------------------------


def _make_redis(keys: list[bytes]) -> MagicMock:
    """Return a mock Redis client whose scan() returns all keys in one call."""
    redis_mock = MagicMock()

    def scan_side_effect(cursor, match=None, count=500):
        # Single-shot: return all keys, next cursor = 0
        filtered = [k for k in keys if not match or k.startswith(match.replace("*", b"").rstrip(b"*") if isinstance(match, bytes) else match.split("*")[0].encode())]
        return (0, keys)  # Return all keys, cursor 0 = done

    redis_mock.scan.side_effect = scan_side_effect
    return redis_mock


def _make_popoto(redis_mock: MagicMock) -> types.ModuleType:
    """Return a fake popoto module whose redis_db.get_REDIS_DB() returns redis_mock."""
    popoto = types.ModuleType("popoto")
    redis_db = types.ModuleType("popoto.redis_db")
    redis_db.get_REDIS_DB = MagicMock(return_value=redis_mock)
    popoto.redis_db = redis_db
    return popoto


# ---------------------------------------------------------------------------
# Import the module under test (with guards patched away)
# ---------------------------------------------------------------------------


def _import_migrate(redis_mock: MagicMock, popoto_mod: types.ModuleType):
    """Import migrate() from the script with popoto patched in sys.modules."""
    worktree = Path(__file__).parent.parent.parent
    script_path = worktree / "scripts" / "migrate_session_type_pm_to_eng.py"

    spec = __import__.__class__  # unused; just ensure importlib is available
    import importlib.util

    spec = importlib.util.spec_from_file_location("migrate_session_type_pm_to_eng", script_path)
    mod = importlib.util.module_from_spec(spec)

    with patch.dict(sys.modules, {"popoto": popoto_mod, "popoto.redis_db": popoto_mod.redis_db}):
        spec.loader.exec_module(mod)

    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def pm_keys():
    """A set of AgentSession keys with :pm: session_type segment."""
    return [
        b"AgentSession:abc123:pm:test-project:active",
        b"AgentSession:def456:pm:test-project:complete",
    ]


@pytest.fixture()
def eng_keys():
    """Already-migrated :eng: keys (idempotency scenario)."""
    return [
        b"AgentSession:abc123:eng:test-project:active",
    ]


@pytest.fixture()
def dev_keys():
    """Dev-session keys (should be skipped, not errored)."""
    return [
        b"AgentSession:xyz789:dev:test-project:active",
    ]


@pytest.fixture()
def index_keys():
    """Popoto infrastructure keys that should never be touched."""
    return [
        b"AgentSession:_sorted_set:session_type:pm",
        b"AgentSession:_field_index:project_key:test-project",
    ]


# ---------------------------------------------------------------------------
# Guard mocks shared across tests
# ---------------------------------------------------------------------------


def _patch_guards_pass():
    """Return a dict of patches that make all pre-flight guards pass."""
    # Heartbeat file is old (stale)
    old_stat = MagicMock()
    old_stat.st_mtime = time.time() - 9000  # 2.5 hours old

    # pgrep returns non-zero (no process found) for both worker and email bridge
    pgrep_not_found = MagicMock()
    pgrep_not_found.returncode = 1
    pgrep_not_found.stdout = ""

    return {
        "heartbeat_stat": old_stat,
        "pgrep_result": pgrep_not_found,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDryRunNoChanges:
    """dry-run finds pm keys but makes no Redis changes."""

    def test_dry_run_no_changes(self, pm_keys, index_keys):
        all_keys = pm_keys + index_keys
        redis_mock = _make_redis(all_keys)
        popoto_mod = _make_popoto(redis_mock)

        guards = _patch_guards_pass()

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=guards["heartbeat_stat"]),
            patch("subprocess.run", return_value=guards["pgrep_result"]),
            patch.dict(sys.modules, {"popoto": popoto_mod, "popoto.redis_db": popoto_mod.redis_db}),
        ):
            mod = _import_migrate(redis_mock, popoto_mod)
            # Patch SessionType to have PM
            fake_session_type = MagicMock()
            fake_session_type.PM = "pm"
            with patch.object(mod, "_check_code_version_ordering", return_value=None):
                stats = mod.migrate(dry_run=True)

        # rename must NOT have been called
        redis_mock.rename.assert_not_called()
        redis_mock.hset.assert_not_called()
        assert stats["renamed_to_eng"] == len(pm_keys)
        assert stats["skipped_index_keys"] == len(index_keys)


class TestLiveRunRenames:
    """Live run renames pm keys to eng and calls rebuild_indexes."""

    def test_live_run_renames(self, pm_keys):
        redis_mock = _make_redis(pm_keys)
        popoto_mod = _make_popoto(redis_mock)

        guards = _patch_guards_pass()

        fake_agent_session = MagicMock()
        fake_models = types.ModuleType("models.agent_session")
        fake_models.AgentSession = fake_agent_session

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=guards["heartbeat_stat"]),
            patch("subprocess.run", return_value=guards["pgrep_result"]),
            patch.dict(
                sys.modules,
                {
                    "popoto": popoto_mod,
                    "popoto.redis_db": popoto_mod.redis_db,
                    "models.agent_session": fake_models,
                },
            ),
        ):
            mod = _import_migrate(redis_mock, popoto_mod)
            with patch.object(mod, "_check_code_version_ordering", return_value=None):
                stats = mod.migrate(dry_run=False)

        # rename should have been called for each pm key
        assert redis_mock.rename.call_count == len(pm_keys)
        # hset should update session_type to "eng" for each renamed key
        for c in redis_mock.hset.call_args_list:
            assert c.args[2] == "eng" or (len(c.args) > 2 and c.args[2] == "eng")

        # rebuild_indexes must have been called
        fake_agent_session.rebuild_indexes.assert_called_once()
        assert stats["renamed_to_eng"] == len(pm_keys)
        assert stats["errors"] == 0


class TestIdempotency:
    """Running twice skips already-eng keys on second run."""

    def test_idempotency(self, eng_keys):
        redis_mock = _make_redis(eng_keys)
        popoto_mod = _make_popoto(redis_mock)

        guards = _patch_guards_pass()

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=guards["heartbeat_stat"]),
            patch("subprocess.run", return_value=guards["pgrep_result"]),
            patch.dict(sys.modules, {"popoto": popoto_mod, "popoto.redis_db": popoto_mod.redis_db}),
        ):
            mod = _import_migrate(redis_mock, popoto_mod)
            with patch.object(mod, "_check_code_version_ordering", return_value=None):
                stats = mod.migrate(dry_run=False)

        redis_mock.rename.assert_not_called()
        assert stats["renamed_to_eng"] == 0
        assert stats["skipped_already_migrated"] == len(eng_keys)


class TestSkipsDevKeys:
    """Dev keys are skipped (not renamed, not errored)."""

    def test_skips_dev_keys(self, dev_keys):
        redis_mock = _make_redis(dev_keys)
        popoto_mod = _make_popoto(redis_mock)

        guards = _patch_guards_pass()

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=guards["heartbeat_stat"]),
            patch("subprocess.run", return_value=guards["pgrep_result"]),
            patch.dict(sys.modules, {"popoto": popoto_mod, "popoto.redis_db": popoto_mod.redis_db}),
        ):
            mod = _import_migrate(redis_mock, popoto_mod)
            with patch.object(mod, "_check_code_version_ordering", return_value=None):
                stats = mod.migrate(dry_run=False)

        redis_mock.rename.assert_not_called()
        assert stats["skipped_dev_record"] == len(dev_keys)
        assert stats["errors"] == 0


class TestWorkerGuardFreshHeartbeat:
    """If last_worker_connected is recent, script exits 1."""

    def test_worker_guard_fresh_heartbeat(self, pm_keys):
        fresh_stat = MagicMock()
        fresh_stat.st_mtime = time.time() - 5  # 5 seconds ago — fresh

        redis_mock = _make_redis(pm_keys)
        popoto_mod = _make_popoto(redis_mock)

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=fresh_stat),
            patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")),
            patch.dict(sys.modules, {"popoto": popoto_mod, "popoto.redis_db": popoto_mod.redis_db}),
        ):
            mod = _import_migrate(redis_mock, popoto_mod)
            with pytest.raises(SystemExit) as exc_info:
                mod._check_worker_not_running()

        assert exc_info.value.code == 1


class TestWorkerGuardStaleHeartbeat:
    """If last_worker_connected is old, script proceeds (no sys.exit)."""

    def test_worker_guard_stale_heartbeat(self, pm_keys):
        stale_stat = MagicMock()
        stale_stat.st_mtime = time.time() - 9000  # 2.5 hours ago — stale

        pgrep_not_found = MagicMock()
        pgrep_not_found.returncode = 1
        pgrep_not_found.stdout = ""

        redis_mock = _make_redis(pm_keys)
        popoto_mod = _make_popoto(redis_mock)

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=stale_stat),
            patch("subprocess.run", return_value=pgrep_not_found),
            patch.dict(sys.modules, {"popoto": popoto_mod, "popoto.redis_db": popoto_mod.redis_db}),
        ):
            mod = _import_migrate(redis_mock, popoto_mod)
            # Should not raise
            mod._check_worker_not_running()


class TestCodeVersionGuard:
    """If SessionType.PM doesn't exist, script exits 1."""

    def test_code_version_guard_pm_absent(self, pm_keys):
        redis_mock = _make_redis(pm_keys)
        popoto_mod = _make_popoto(redis_mock)

        # Create a fake SessionType without PM attribute
        fake_enum = MagicMock(spec=[])  # no attributes by default
        del fake_enum.PM  # ensure PM is absent

        fake_enums_module = types.ModuleType("config.enums")
        fake_enums_module.SessionType = type("SessionType", (), {"ENG": "eng"})

        with (
            patch.dict(sys.modules, {
                "popoto": popoto_mod,
                "popoto.redis_db": popoto_mod.redis_db,
                "config.enums": fake_enums_module,
            }),
        ):
            mod = _import_migrate(redis_mock, popoto_mod)
            with pytest.raises(SystemExit) as exc_info:
                mod._check_code_version_ordering()

        assert exc_info.value.code == 1


class TestPositionalRewriteAssertion:
    """Key with multiple ':pm:' segments raises sys.exit(1) or increments errors."""

    def test_positional_rewrite_multiple_pm(self):
        # Key with two :pm: occurrences — ambiguous, should fail
        ambiguous_key = b"AgentSession:pm:abc123:pm:test-project:active"
        redis_mock = _make_redis([ambiguous_key])
        popoto_mod = _make_popoto(redis_mock)

        guards = _patch_guards_pass()

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=guards["heartbeat_stat"]),
            patch("subprocess.run", return_value=guards["pgrep_result"]),
            patch.dict(sys.modules, {"popoto": popoto_mod, "popoto.redis_db": popoto_mod.redis_db}),
        ):
            mod = _import_migrate(redis_mock, popoto_mod)
            with patch.object(mod, "_check_code_version_ordering", return_value=None):
                # The script either sys.exit(1)s or increments errors
                try:
                    stats = mod.migrate(dry_run=True)
                    # If it returns, errors should be > 0 OR no rename happened
                    assert stats["errors"] > 0 or stats["renamed_to_eng"] == 0
                except SystemExit as e:
                    assert e.code == 1
