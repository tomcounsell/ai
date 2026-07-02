"""Unit tests for scripts/migrate_steering_queue_drain.py.

All Redis interactions are mocked so these tests run without a live Redis
instance. ``agent.steering.push_steering_message`` is patched to assert the
drain writes land on the Redis-list primitive, not a real Redis connection.

``migrate()`` imports ``popoto`` and ``agent.steering`` lazily inside the
function body (matching the sibling migration scripts), so the fake modules
must stay registered in ``sys.modules`` for the duration of the ``migrate()``
call, not just at import time -- see ``_patched_modules()``.
"""

import importlib.util
import json
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to build fake Redis scan/hash state
# ---------------------------------------------------------------------------


def _make_redis(keys: list[bytes], hash_data: dict[bytes, dict[str, object]]) -> MagicMock:
    """Return a mock Redis client whose scan() and h*() reflect hash_data."""
    redis_mock = MagicMock()

    def scan_side_effect(cursor, match=None, count=500):
        return (0, keys)  # single-shot: return all keys, cursor 0 = done

    def hexists_side_effect(key, field):
        return field in hash_data.get(key, {})

    def hget_side_effect(key, field):
        return hash_data.get(key, {}).get(field)

    def hdel_side_effect(key, field):
        hash_data.get(key, {}).pop(field, None)

    redis_mock.scan.side_effect = scan_side_effect
    redis_mock.hexists.side_effect = hexists_side_effect
    redis_mock.hget.side_effect = hget_side_effect
    redis_mock.hdel.side_effect = hdel_side_effect
    return redis_mock


def _make_popoto(redis_mock: MagicMock) -> types.ModuleType:
    """Return a fake popoto module whose redis_db.get_REDIS_DB() returns redis_mock."""
    popoto = types.ModuleType("popoto")
    redis_db = types.ModuleType("popoto.redis_db")
    redis_db.get_REDIS_DB = MagicMock(return_value=redis_mock)
    popoto.redis_db = redis_db
    return popoto


_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "migrate_steering_queue_drain.py"


def _load_module():
    """Load the script as a fresh module object (no execution side effects)."""
    spec = importlib.util.spec_from_file_location("migrate_steering_queue_drain", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextmanager
def _patched_modules(popoto_mod: types.ModuleType, push_mock: MagicMock):
    """Keep fake popoto/agent.steering registered for the duration of migrate()."""
    fake_steering = types.ModuleType("agent.steering")
    fake_steering.push_steering_message = push_mock
    with patch.dict(
        sys.modules,
        {
            "popoto": popoto_mod,
            "popoto.redis_db": popoto_mod.redis_db,
            "agent.steering": fake_steering,
        },
    ):
        yield


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def residual_key():
    return b"AgentSession:abc123:eng:test-project:completed"


@pytest.fixture()
def index_keys():
    return [
        b"AgentSession:_sorted_set:session_type:eng",
        b"AgentSession:_field_index:project_key:test-project",
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_reports_without_writing(self, residual_key, index_keys):
        hash_data = {
            residual_key: {
                "session_id": "sess-abc123",
                "queued_steering_messages": json.dumps(["focus on OAuth", "check tests"]),
            },
        }
        redis_mock = _make_redis([residual_key, *index_keys], hash_data)
        popoto_mod = _make_popoto(redis_mock)
        push_mock = MagicMock()
        mod = _load_module()

        with _patched_modules(popoto_mod, push_mock):
            stats = mod.migrate(apply=False)

        push_mock.assert_not_called()
        redis_mock.hdel.assert_not_called()
        assert stats["drained"] == 1
        assert stats["messages_migrated"] == 2
        assert stats["total_records"] == 1  # index keys excluded


class TestLiveRun:
    def test_live_run_drains_to_redis_list_and_clears_field(self, residual_key):
        hash_data = {
            residual_key: {
                "session_id": "sess-abc123",
                "queued_steering_messages": json.dumps(["focus on OAuth", "check tests"]),
            },
        }
        redis_mock = _make_redis([residual_key], hash_data)
        popoto_mod = _make_popoto(redis_mock)
        push_mock = MagicMock()
        mod = _load_module()

        with _patched_modules(popoto_mod, push_mock):
            stats = mod.migrate(apply=True)

        assert push_mock.call_count == 2
        push_mock.assert_any_call("sess-abc123", "focus on OAuth", "migration-drain")
        push_mock.assert_any_call("sess-abc123", "check tests", "migration-drain")
        # Stale field removed after draining
        assert "queued_steering_messages" not in hash_data[residual_key]
        assert stats["drained"] == 1
        assert stats["messages_migrated"] == 2
        assert stats["errors"] == 0

    def test_falls_back_to_key_derived_session_id_when_missing(self, residual_key):
        """No session_id hash field -> derive from the hash key suffix."""
        hash_data = {
            residual_key: {
                "queued_steering_messages": json.dumps(["hello"]),
            },
        }
        redis_mock = _make_redis([residual_key], hash_data)
        popoto_mod = _make_popoto(redis_mock)
        push_mock = MagicMock()
        mod = _load_module()

        with _patched_modules(popoto_mod, push_mock):
            mod.migrate(apply=True)

        push_mock.assert_called_once_with(
            "abc123:eng:test-project:completed", "hello", "migration-drain"
        )


class TestIdempotency:
    def test_true_double_run_is_idempotent(self, residual_key):
        """Two consecutive --apply runs against the same state.

        The first run drains the residual field onto the steering list. The
        second run sees a genuinely-drained record: it migrates zero and adds
        no duplicate entries to the list the first run built.
        """
        hash_data = {
            residual_key: {
                "session_id": "sess-abc123",
                "queued_steering_messages": json.dumps(["focus on OAuth", "check tests"]),
            },
        }
        redis_mock = _make_redis([residual_key], hash_data)
        popoto_mod = _make_popoto(redis_mock)

        # Model the steering list so we can assert its content across both runs.
        steering_list: list[tuple[str, str]] = []

        def push_side_effect(session_id, text, sender):
            steering_list.append((session_id, text))

        push_mock = MagicMock(side_effect=push_side_effect)
        mod = _load_module()

        with _patched_modules(popoto_mod, push_mock):
            stats_first = mod.migrate(apply=True)
            stats_second = mod.migrate(apply=True)

        expected_list = [
            ("sess-abc123", "focus on OAuth"),
            ("sess-abc123", "check tests"),
        ]

        # First run drained both messages onto the list.
        assert stats_first["drained"] == 1
        assert stats_first["messages_migrated"] == 2
        assert steering_list == expected_list

        # Second run: nothing left to migrate, no duplicate pushes, list unchanged.
        assert stats_second["drained"] == 0
        assert stats_second["messages_migrated"] == 0
        assert stats_second["already_clean"] == 1
        assert push_mock.call_count == 2  # no additional pushes on the second run
        assert steering_list == expected_list

    def test_empty_list_field_is_cleaned_without_pushing(self, residual_key):
        hash_data = {
            residual_key: {
                "session_id": "sess-abc123",
                "queued_steering_messages": json.dumps([]),
            },
        }
        redis_mock = _make_redis([residual_key], hash_data)
        popoto_mod = _make_popoto(redis_mock)
        push_mock = MagicMock()
        mod = _load_module()

        with _patched_modules(popoto_mod, push_mock):
            stats = mod.migrate(apply=True)

        push_mock.assert_not_called()
        assert "queued_steering_messages" not in hash_data[residual_key]
        assert stats["already_clean"] == 1
        assert stats["drained"] == 0
