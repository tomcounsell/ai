"""Unit tests for scripts/migrate_memory_project_key.py.

Tests the key classifier logic, key structure parsing, and two-phase rename
behavior (dry-run vs. --apply) without requiring a live Redis connection.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add project root to path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


class TestIsTelegramDm:
    """Tests for _is_telegram_dm() classifier logic."""

    def _make_redis(self, source: str | None, agent_id: str | None) -> MagicMock:
        """Build a mock Redis client that returns given source and agent_id fields."""
        redis = MagicMock()

        def hget_side_effect(key, field):
            if field == "source":
                return source.encode() if source else None
            if field == "agent_id":
                return agent_id.encode() if agent_id else None
            return None

        redis.hget.side_effect = hget_side_effect
        return redis

    def test_genuine_telegram_dm_returns_true(self):
        """Records with source=human AND agent_id=dm are genuine DMs."""
        from scripts.migrate_memory_project_key import _is_telegram_dm

        redis = self._make_redis(source="human", agent_id="dm")
        assert _is_telegram_dm(redis, b"Memory:abc:dm:dm") is True

    def test_hook_sourced_agent_returns_false(self):
        """Records with source=agent are not DMs, even if agent_id=dm."""
        from scripts.migrate_memory_project_key import _is_telegram_dm

        redis = self._make_redis(source="agent", agent_id="dm")
        assert _is_telegram_dm(redis, b"Memory:abc:dm:dm") is False

    def test_human_source_non_dm_agent_id_returns_false(self):
        """Records with source=human but agent_id != dm are not DMs."""
        from scripts.migrate_memory_project_key import _is_telegram_dm

        redis = self._make_redis(source="human", agent_id="valor")
        assert _is_telegram_dm(redis, b"Memory:abc:valor:dm") is False

    def test_missing_source_returns_false(self):
        """Records with no source field are not classified as DMs."""
        from scripts.migrate_memory_project_key import _is_telegram_dm

        redis = self._make_redis(source=None, agent_id="dm")
        assert _is_telegram_dm(redis, b"Memory:abc:dm:dm") is False

    def test_missing_agent_id_returns_false(self):
        """Records with no agent_id field are not classified as DMs."""
        from scripts.migrate_memory_project_key import _is_telegram_dm

        redis = self._make_redis(source="human", agent_id=None)
        assert _is_telegram_dm(redis, b"Memory:abc:dm:dm") is False

    def test_empty_strings_return_false(self):
        """Empty source and agent_id values are not classified as DMs."""
        from scripts.migrate_memory_project_key import _is_telegram_dm

        redis = self._make_redis(source="", agent_id="")
        assert _is_telegram_dm(redis, b"Memory:abc:dm:dm") is False

    def test_hook_key_structure_returns_false(self):
        """Keys created by hooks (agent_id = session ID) are not DMs."""
        from scripts.migrate_memory_project_key import _is_telegram_dm

        redis = self._make_redis(source="agent", agent_id="local-abc123")
        assert _is_telegram_dm(redis, b"Memory:xyz:local-abc123:dm") is False


class TestKeyStructureParsing:
    """Tests for key structure parsing in the migration script."""

    def test_is_index_key_sorted_set(self):
        """Sorted set infrastructure keys are recognized as index keys."""
        from scripts.migrate_memory_project_key import _is_index_key

        assert _is_index_key(b"Memory:_sorted_set:importance") is True

    def test_is_index_key_field_index(self):
        """Field index keys are recognized as index keys."""
        from scripts.migrate_memory_project_key import _is_index_key

        assert _is_index_key(b"Memory:_field_index:project_key") is True

    def test_is_index_key_bloom(self):
        """Bloom filter keys are recognized as index keys."""
        from scripts.migrate_memory_project_key import _is_index_key

        assert _is_index_key(b"Memory:bloom:content") is True

    def test_is_index_key_bm25(self):
        """BM25 index keys are recognized as index keys."""
        from scripts.migrate_memory_project_key import _is_index_key

        assert _is_index_key(b"Memory:bm25:content") is True

    def test_data_record_not_index_key(self):
        """Actual Memory hash records are not flagged as index keys."""
        from scripts.migrate_memory_project_key import _is_index_key

        assert _is_index_key(b"Memory:abc123:dm:dm") is False

    def test_decode_value_bytes(self):
        """Byte values are decoded to strings."""
        from scripts.migrate_memory_project_key import _decode_value

        assert _decode_value(b"human") == "human"

    def test_decode_value_none(self):
        """None values return None."""
        from scripts.migrate_memory_project_key import _decode_value

        assert _decode_value(None) is None

    def test_decode_value_string(self):
        """String values pass through as-is."""
        from scripts.migrate_memory_project_key import _decode_value

        assert _decode_value("agent") == "agent"

    def test_decode_value_strips_popoto_prefix(self):
        """Popoto non-ASCII byte prefixes are stripped from decoded values."""
        from scripts.migrate_memory_project_key import _decode_value

        # Simulate popoto-prefixed value: \xa3 + "human"
        raw = b"\xa3human"
        result = _decode_value(raw)
        # After lstrip of non-alphanumeric, "human" should remain
        assert result is not None
        assert "human" in result


class TestMigrateDryRun:
    """Tests for two-phase rename logic — dry-run mode."""

    def _build_mock_redis(self, keys, source_map):
        """Build a Redis mock with given scan results and field values.

        Args:
            keys: list of bytes keys returned by scan
            source_map: dict of {key_bytes: {"source": ..., "agent_id": ...}}
        """
        redis = MagicMock()

        # scan returns (cursor=0, keys) to signal end of iteration
        redis.scan.return_value = (0, keys)

        def hget_side_effect(key, field):
            record = source_map.get(key, {})
            val = record.get(field)
            return val.encode() if val else None

        redis.hget.side_effect = hget_side_effect
        return redis

    def test_dry_run_does_not_rename(self):
        """Dry-run mode logs what would happen but does not call RENAME."""
        from scripts.migrate_memory_project_key import migrate

        hook_key = b"Memory:abc123:session-x:dm"
        redis = self._build_mock_redis(
            keys=[hook_key],
            source_map={hook_key: {"source": "agent", "agent_id": "session-x"}},
        )

        with patch("popoto.redis_db.get_REDIS_DB", return_value=redis):
            stats = migrate(dry_run=True)

        # RENAME must not be called in dry-run mode
        redis.rename.assert_not_called()
        assert stats["migrated_to_valor"] == 1

    def test_dry_run_counts_correctly(self):
        """Dry-run mode reports the correct would-migrate count."""
        from scripts.migrate_memory_project_key import migrate

        keys = [
            b"Memory:aa:session-a:dm",
            b"Memory:bb:session-b:dm",
            b"Memory:cc:dm:dm",  # genuine DM
        ]
        source_map = {
            b"Memory:aa:session-a:dm": {"source": "agent", "agent_id": "session-a"},
            b"Memory:bb:session-b:dm": {"source": "agent", "agent_id": "session-b"},
            b"Memory:cc:dm:dm": {"source": "human", "agent_id": "dm"},
        }
        redis = self._build_mock_redis(keys=keys, source_map=source_map)

        with patch("popoto.redis_db.get_REDIS_DB", return_value=redis):
            stats = migrate(dry_run=True)

        assert stats["migrated_to_valor"] == 2
        assert stats["kept_as_dm_telegram"] == 1
        redis.rename.assert_not_called()

    def test_apply_mode_renames_keys(self):
        """--apply mode calls RENAME and updates hash fields."""
        from scripts.migrate_memory_project_key import migrate

        hook_key = b"Memory:abc123:session-x:dm"
        redis = self._build_mock_redis(
            keys=[hook_key],
            source_map={hook_key: {"source": "agent", "agent_id": "session-x"}},
        )

        with (
            patch("popoto.redis_db.get_REDIS_DB", return_value=redis),
            patch("models.memory.Memory.rebuild_indexes"),
        ):
            stats = migrate(dry_run=False)

        # RENAME should have been called
        redis.rename.assert_called_once()
        rename_args = redis.rename.call_args[0]
        assert rename_args[0] == hook_key
        # New key should end with :valor
        new_key = rename_args[1]
        new_key_str = new_key.decode() if isinstance(new_key, bytes) else new_key
        assert new_key_str.endswith(":valor")
        assert stats["migrated_to_valor"] == 1

    def test_apply_mode_updates_project_key_field(self):
        """--apply mode updates the project_key hash field to 'valor'."""
        from scripts.migrate_memory_project_key import migrate

        hook_key = b"Memory:abc123:session-x:dm"
        redis = self._build_mock_redis(
            keys=[hook_key],
            source_map={hook_key: {"source": "agent", "agent_id": "session-x"}},
        )

        with (
            patch("popoto.redis_db.get_REDIS_DB", return_value=redis),
            patch("models.memory.Memory.rebuild_indexes"),
        ):
            migrate(dry_run=False)

        # hset should have been called to update project_key
        hset_calls = redis.hset.call_args_list
        field_updates = {args[1]: args[2] for args, kwargs in hset_calls}
        assert "project_key" in field_updates
        assert field_updates["project_key"] == "valor"

    def test_apply_mode_preserves_telegram_dm_records(self):
        """--apply mode does not rename genuine Telegram DM records."""
        from scripts.migrate_memory_project_key import migrate

        dm_key = b"Memory:dm123:dm:dm"
        redis = self._build_mock_redis(
            keys=[dm_key],
            source_map={dm_key: {"source": "human", "agent_id": "dm"}},
        )

        with (
            patch("popoto.redis_db.get_REDIS_DB", return_value=redis),
            patch("models.memory.Memory.rebuild_indexes"),
        ):
            stats = migrate(dry_run=False)

        redis.rename.assert_not_called()
        assert stats["kept_as_dm_telegram"] == 1
        assert stats["migrated_to_valor"] == 0

    def test_no_keys_returns_zero_stats(self):
        """When scan finds no records, all stats are zero."""
        from scripts.migrate_memory_project_key import migrate

        redis = MagicMock()
        redis.scan.return_value = (0, [])

        with patch("popoto.redis_db.get_REDIS_DB", return_value=redis):
            stats = migrate(dry_run=True)

        assert stats["total_scanned"] == 0
        assert stats["migrated_to_valor"] == 0
        redis.rename.assert_not_called()

    def test_index_keys_are_excluded_from_count(self):
        """Popoto infrastructure keys are filtered out and counted separately."""
        from scripts.migrate_memory_project_key import migrate

        keys = [
            b"Memory:abc:session-x:dm",
            b"Memory:_field_index:project_key:dm",  # index key
            b"Memory:bloom:content:dm",  # bloom filter key
        ]
        source_map = {b"Memory:abc:session-x:dm": {"source": "agent", "agent_id": "session-x"}}
        redis = self._build_mock_redis(keys=keys, source_map=source_map)

        with patch("popoto.redis_db.get_REDIS_DB", return_value=redis):
            stats = migrate(dry_run=True)

        # Only 1 real data record, 2 infrastructure keys skipped
        assert stats["total_scanned"] == 1
        assert stats["skipped_index_keys"] == 2
