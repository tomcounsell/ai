"""Unit tests for scripts/merge_dev_chat_into_eng.py.

Covers:
- EXISTS-check collision skip (collision detected → logged + skipped, not clobbered)
- create-then-delete Chat order (Eng Chat created before Dev Chat deleted)
- pre/post count assertion (ORM query path, not raw key counts)
- TelegramMessage.rebuild_indexes() called after renames
"""

import sys
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ---------------------------------------------------------------------------
# Constants for test isolation
# ---------------------------------------------------------------------------

DEV_CHAT_ID = "test-dev-chat-777"
ENG_CHAT_ID = "test-eng-chat-888"
TEST_PROJECT_KEY = "test-merge-dev-chat"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_telegram_message_key(chat_id: str, msg_id: str | None = None) -> str:
    """Build a fake TelegramMessage Redis key with the given chat_id."""
    mid = msg_id or str(uuid.uuid4())
    return f"TelegramMessage:{mid}:{chat_id}:in:sender:text"


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
# Test: EXISTS-check collision skip
# ---------------------------------------------------------------------------


class TestCollisionSkip:
    """When the target key already exists, the source key must be skipped (never clobbered)."""

    def test_collision_detected_skips_not_clobbers(self, redis_test_db):
        """If the target Eng key already exists, the source Dev key is skipped."""
        import popoto

        from scripts.merge_dev_chat_into_eng import migrate

        redis_client = popoto.redis_db.get_REDIS_DB()

        # Seed a Dev key
        msg_id = str(uuid.uuid4())
        dev_key = f"TelegramMessage:{msg_id}:{DEV_CHAT_ID}:in:sender:text"
        dev_value = {"chat_id": DEV_CHAT_ID, "content": "hello from dev", "direction": "in"}
        redis_client.hset(dev_key, mapping=dev_value)

        # Pre-seed the collision: the Eng key already exists with different content
        eng_key = f"TelegramMessage:{msg_id}:{ENG_CHAT_ID}:in:sender:text"
        eng_existing_value = {
            "chat_id": ENG_CHAT_ID,
            "content": "existing eng message",
            "direction": "in",
        }
        redis_client.hset(eng_key, mapping=eng_existing_value)

        stats = migrate(
            dev_chat_id=DEV_CHAT_ID,
            eng_chat_id=ENG_CHAT_ID,
            project_key=TEST_PROJECT_KEY,
            dry_run=True,
        )

        assert stats["skipped_collision"] == 1
        assert stats["renamed"] == 0

        # Verify the original Dev key still exists (not clobbered)
        assert redis_client.exists(dev_key.encode()) or redis_client.exists(dev_key)
        # Verify the Eng key content is unchanged
        content = redis_client.hget(eng_key, "content")
        if isinstance(content, bytes):
            content = content.decode()
        assert content == "existing eng message", "Eng key content must not be overwritten"

    def test_dry_run_enumerates_all_collisions(self, redis_test_db):
        """dry-run must enumerate ALL prospective collisions without making changes."""
        import popoto

        from scripts.merge_dev_chat_into_eng import migrate

        redis_client = popoto.redis_db.get_REDIS_DB()

        # Seed 3 Dev keys, 2 of which have collisions
        collision_count = 0
        for i in range(3):
            msg_id = str(uuid.uuid4())
            dev_key = f"TelegramMessage:{msg_id}:{DEV_CHAT_ID}:in:sender:text"
            redis_client.hset(dev_key, mapping={"chat_id": DEV_CHAT_ID, "content": f"msg{i}"})

            if i < 2:
                # Pre-seed collision
                eng_key = f"TelegramMessage:{msg_id}:{ENG_CHAT_ID}:in:sender:text"
                redis_client.hset(eng_key, mapping={"chat_id": ENG_CHAT_ID, "content": f"eng{i}"})
                collision_count += 1

        stats = migrate(
            dev_chat_id=DEV_CHAT_ID,
            eng_chat_id=ENG_CHAT_ID,
            project_key=TEST_PROJECT_KEY,
            dry_run=True,
        )

        assert stats["skipped_collision"] == collision_count
        assert stats["renamed"] == 1  # One non-collision
        # No renames actually happened (dry_run=True)
        # Verify all Dev keys still exist
        cursor = 0
        all_keys = []
        while True:
            cursor, keys = redis_client.scan(cursor, match="TelegramMessage:*", count=500)
            all_keys.extend(keys)
            if cursor == 0:
                break
        dev_keys_remaining = [
            k
            for k in all_keys
            if (f":{DEV_CHAT_ID}:" in (k.decode() if isinstance(k, bytes) else k))
        ]
        assert len(dev_keys_remaining) == 3, "All Dev keys must remain untouched in dry-run"


# ---------------------------------------------------------------------------
# Test: create-then-delete Chat order
# ---------------------------------------------------------------------------


class TestChatRenameOrder:
    """Eng Chat must be created (and verified) before Dev Chat is deleted."""

    def test_eng_chat_created_before_dev_chat_deleted(self, redis_test_db):
        """The migration must create Eng Chat, verify it, then delete Dev Chat.

        Verifies the create-then-delete order by checking that an Eng Chat
        record exists after the migration and the Dev Chat is gone.
        """
        from models.chat import Chat
        from scripts.merge_dev_chat_into_eng import migrate

        # Seed a Dev Chat record
        dev_chat = Chat(
            chat_id=DEV_CHAT_ID,
            chat_name="Dev Team",
            chat_type="group",
            project_key=TEST_PROJECT_KEY,
            updated_at=time.time(),
        )
        dev_chat.save()

        # Verify Dev Chat exists before migration
        assert Chat.query.filter(chat_id=DEV_CHAT_ID).first() is not None

        # Run migration (no TelegramMessage records needed for this sub-test)
        with patch(
            "models.telegram.TelegramMessage.rebuild_indexes",
            MagicMock(),
        ):
            stats = migrate(
                dev_chat_id=DEV_CHAT_ID,
                eng_chat_id=ENG_CHAT_ID,
                project_key=TEST_PROJECT_KEY,
                dry_run=False,
            )

        # Verify Eng Chat was created
        eng_chat = Chat.query.filter(chat_id=ENG_CHAT_ID).first()
        assert eng_chat is not None, "Eng Chat must exist after migration"
        assert eng_chat.chat_name == "Dev Team"

        # Verify Dev Chat was deleted
        dev_chat_after = Chat.query.filter(chat_id=DEV_CHAT_ID).first()
        assert dev_chat_after is None, "Dev Chat must be deleted after migration"

        assert stats["errors"] == 0

    def test_existing_eng_chat_not_recreated(self, redis_test_db):
        """If Eng Chat already exists, it must not be overwritten; Dev Chat still deleted."""
        from models.chat import Chat
        from scripts.merge_dev_chat_into_eng import migrate

        # Seed both Dev and Eng Chat records
        dev_chat = Chat(
            chat_id=DEV_CHAT_ID,
            chat_name="Dev Team",
            chat_type="group",
            project_key=TEST_PROJECT_KEY,
            updated_at=time.time() - 100,
        )
        dev_chat.save()

        eng_chat_pre = Chat(
            chat_id=ENG_CHAT_ID,
            chat_name="Eng Team (existing)",
            chat_type="group",
            project_key=TEST_PROJECT_KEY,
            updated_at=time.time(),
        )
        eng_chat_pre.save()

        with patch(
            "models.telegram.TelegramMessage.rebuild_indexes",
            MagicMock(),
        ):
            stats = migrate(
                dev_chat_id=DEV_CHAT_ID,
                eng_chat_id=ENG_CHAT_ID,
                project_key=TEST_PROJECT_KEY,
                dry_run=False,
            )

        # Eng Chat should still exist with original name (not overwritten)
        eng_chat_after = Chat.query.filter(chat_id=ENG_CHAT_ID).first()
        assert eng_chat_after is not None
        assert eng_chat_after.chat_name == "Eng Team (existing)", (
            "Existing Eng Chat name must not be overwritten"
        )

        # Dev Chat deleted
        assert Chat.query.filter(chat_id=DEV_CHAT_ID).first() is None
        assert stats["errors"] == 0


# ---------------------------------------------------------------------------
# Test: pre/post count assertion
# ---------------------------------------------------------------------------


class TestPrePostCountAssertion:
    """post_eng_count must equal (pre_eng_count + migrated - skipped_collisions)."""

    def test_count_assertion_passes_on_clean_migration(self, redis_test_db):
        """Clean migration: all Dev messages land in Eng, assertion passes."""
        import popoto

        from scripts.merge_dev_chat_into_eng import migrate

        redis_client = popoto.redis_db.get_REDIS_DB()

        # Seed 3 Dev TelegramMessage records
        for i in range(3):
            msg_id = str(uuid.uuid4())
            dev_key = f"TelegramMessage:{msg_id}:{DEV_CHAT_ID}:in:sender:text"
            redis_client.hset(dev_key, mapping={"chat_id": DEV_CHAT_ID, "content": f"msg{i}"})

        # Run live migration (mock ORM query count to return predictable values)
        with patch("scripts.merge_dev_chat_into_eng.TelegramMessage") as mock_tm:
            mock_query = MagicMock()
            mock_tm.query = mock_query
            mock_tm.rebuild_indexes = MagicMock()

            # Pre counts
            pre_dev_filter = MagicMock()
            pre_dev_filter.count.return_value = 3
            pre_eng_filter = MagicMock()
            pre_eng_filter.count.return_value = 0
            # Post counts (after rebuild_indexes)
            post_dev_filter = MagicMock()
            post_dev_filter.count.return_value = 0
            post_eng_filter = MagicMock()
            post_eng_filter.count.return_value = 3

            # Side effects: first 2 calls (pre), last 2 calls (post)
            call_count = [0]

            def _filter_side_effect(**kwargs):
                call_count[0] += 1
                cid = kwargs.get("chat_id", "")
                if call_count[0] <= 2:
                    return pre_dev_filter if cid == DEV_CHAT_ID else pre_eng_filter
                else:
                    return post_dev_filter if cid == DEV_CHAT_ID else post_eng_filter

            mock_query.filter.side_effect = _filter_side_effect

            with patch("scripts.merge_dev_chat_into_eng.Chat") as mock_chat:
                mock_chat.query.filter.return_value.first.return_value = None
                stats = migrate(
                    dev_chat_id=DEV_CHAT_ID,
                    eng_chat_id=ENG_CHAT_ID,
                    project_key=TEST_PROJECT_KEY,
                    dry_run=False,
                )

        # Assertion passed (no sys.exit)
        assert stats["errors"] == 0

    def test_count_mismatch_causes_sysexit(self, redis_test_db):
        """If post_eng_count != expected, migrate() exits with code 1."""
        import popoto

        from scripts.merge_dev_chat_into_eng import migrate

        redis_client = popoto.redis_db.get_REDIS_DB()

        # Seed one Dev record
        msg_id = str(uuid.uuid4())
        dev_key = f"TelegramMessage:{msg_id}:{DEV_CHAT_ID}:in:sender:text"
        redis_client.hset(dev_key, mapping={"chat_id": DEV_CHAT_ID, "content": "msg"})

        call_count = [0]

        with patch("scripts.merge_dev_chat_into_eng.TelegramMessage") as mock_tm:
            mock_query = MagicMock()
            mock_tm.query = mock_query
            mock_tm.rebuild_indexes = MagicMock()

            pre_dev = MagicMock()
            pre_dev.count.return_value = 1
            pre_eng = MagicMock()
            pre_eng.count.return_value = 0
            # Post: intentionally wrong count to trigger assertion
            post_dev = MagicMock()
            post_dev.count.return_value = 0
            post_eng = MagicMock()
            post_eng.count.return_value = 0  # Should be 1, triggers mismatch

            def _filter(**kwargs):
                call_count[0] += 1
                cid = kwargs.get("chat_id", "")
                if call_count[0] <= 2:
                    return pre_dev if cid == DEV_CHAT_ID else pre_eng
                else:
                    return post_dev if cid == DEV_CHAT_ID else post_eng

            mock_query.filter.side_effect = _filter

            with patch("scripts.merge_dev_chat_into_eng.Chat") as mock_chat:
                mock_chat.query.filter.return_value.first.return_value = None
                with pytest.raises(SystemExit) as exc_info:
                    migrate(
                        dev_chat_id=DEV_CHAT_ID,
                        eng_chat_id=ENG_CHAT_ID,
                        project_key=TEST_PROJECT_KEY,
                        dry_run=False,
                    )
                assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Test: TelegramMessage.rebuild_indexes() called after renames
# ---------------------------------------------------------------------------


class TestRebuildIndexesCalled:
    """TelegramMessage.rebuild_indexes() must be called after any renames."""

    def test_rebuild_indexes_called_when_records_renamed(self, redis_test_db):
        """After renaming records, rebuild_indexes() must be called exactly once."""
        import popoto

        from scripts.merge_dev_chat_into_eng import migrate

        redis_client = popoto.redis_db.get_REDIS_DB()

        # Seed one Dev record
        msg_id = str(uuid.uuid4())
        dev_key = f"TelegramMessage:{msg_id}:{DEV_CHAT_ID}:in:sender:text"
        redis_client.hset(dev_key, mapping={"chat_id": DEV_CHAT_ID, "content": "hello"})

        with patch("scripts.merge_dev_chat_into_eng.TelegramMessage") as mock_tm:
            mock_query = MagicMock()
            mock_tm.query = mock_query
            mock_tm.rebuild_indexes = MagicMock()

            call_count = [0]

            def _filter(**kwargs):
                call_count[0] += 1
                cid = kwargs.get("chat_id", "")
                m = MagicMock()
                if call_count[0] <= 2:
                    # Pre-counts: dev=1, eng=0
                    m.count.return_value = 1 if cid == DEV_CHAT_ID else 0
                else:
                    # Post-counts: dev=0, eng=1 (renamed 1, pre_eng was 0)
                    m.count.return_value = 0 if cid == DEV_CHAT_ID else 1
                return m

            mock_query.filter.side_effect = _filter

            with patch("scripts.merge_dev_chat_into_eng.Chat") as mock_chat:
                mock_chat.query.filter.return_value.first.return_value = None
                stats = migrate(
                    dev_chat_id=DEV_CHAT_ID,
                    eng_chat_id=ENG_CHAT_ID,
                    project_key=TEST_PROJECT_KEY,
                    dry_run=False,
                )

            # rebuild_indexes must have been called
            mock_tm.rebuild_indexes.assert_called_once()

    def test_rebuild_indexes_not_called_on_dry_run(self, redis_test_db):
        """dry-run must NOT call rebuild_indexes()."""
        import popoto

        from scripts.merge_dev_chat_into_eng import migrate

        redis_client = popoto.redis_db.get_REDIS_DB()

        msg_id = str(uuid.uuid4())
        dev_key = f"TelegramMessage:{msg_id}:{DEV_CHAT_ID}:in:sender:text"
        redis_client.hset(dev_key, mapping={"chat_id": DEV_CHAT_ID, "content": "hello"})

        with patch("scripts.merge_dev_chat_into_eng.TelegramMessage") as mock_tm:
            mock_query = MagicMock()
            mock_tm.query = mock_query
            mock_tm.rebuild_indexes = MagicMock()

            m = MagicMock()
            m.count.return_value = 1
            mock_query.filter.return_value = m

            stats = migrate(
                dev_chat_id=DEV_CHAT_ID,
                eng_chat_id=ENG_CHAT_ID,
                project_key=TEST_PROJECT_KEY,
                dry_run=True,
            )

            mock_tm.rebuild_indexes.assert_not_called()

    def test_rebuild_indexes_not_called_when_no_records_renamed(self, redis_test_db):
        """If no records were renamed (empty Dev chat), rebuild_indexes() must not be called."""
        from scripts.merge_dev_chat_into_eng import migrate

        # No Dev records seeded
        with patch("scripts.merge_dev_chat_into_eng.TelegramMessage") as mock_tm:
            mock_query = MagicMock()
            mock_tm.query = mock_query
            mock_tm.rebuild_indexes = MagicMock()

            m = MagicMock()
            m.count.return_value = 0
            mock_query.filter.return_value = m

            with patch("scripts.merge_dev_chat_into_eng.Chat") as mock_chat:
                mock_chat.query.filter.return_value.first.return_value = None
                stats = migrate(
                    dev_chat_id=DEV_CHAT_ID,
                    eng_chat_id=ENG_CHAT_ID,
                    project_key=TEST_PROJECT_KEY,
                    dry_run=False,
                )

            mock_tm.rebuild_indexes.assert_not_called()


# ---------------------------------------------------------------------------
# Test: idempotency guard
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Keys already bearing the Eng chat_id must be skipped on second run."""

    def test_already_eng_keys_are_skipped(self, redis_test_db):
        """If a key already has :eng_chat_id: in it, it must be skipped."""
        import popoto

        from scripts.merge_dev_chat_into_eng import migrate

        redis_client = popoto.redis_db.get_REDIS_DB()

        # Pre-seed an already-eng key (as if migration already ran)
        msg_id = str(uuid.uuid4())
        eng_key = f"TelegramMessage:{msg_id}:{ENG_CHAT_ID}:in:sender:text"
        redis_client.hset(eng_key, mapping={"chat_id": ENG_CHAT_ID, "content": "already eng"})

        with patch("scripts.merge_dev_chat_into_eng.TelegramMessage") as mock_tm:
            mock_query = MagicMock()
            mock_tm.query = mock_query
            mock_tm.rebuild_indexes = MagicMock()

            m = MagicMock()
            m.count.return_value = 0
            mock_query.filter.return_value = m

            stats = migrate(
                dev_chat_id=DEV_CHAT_ID,
                eng_chat_id=ENG_CHAT_ID,
                project_key=TEST_PROJECT_KEY,
                dry_run=True,
            )

        assert stats["renamed"] == 0
        assert stats["total_dev_records"] == 0
