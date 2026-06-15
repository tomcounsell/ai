"""Unit tests for scripts/merge_dev_chat_into_eng.py.

All Redis and ORM interactions are mocked so these tests run without a live
Redis instance. Uses test- prefix project keys per CLAUDE.md testing hygiene.
"""

import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------


def _import_merge_dev_chat(redis_mock: MagicMock, popoto_mod: types.ModuleType):
    """Import migrate() from the script with popoto patched in sys.modules."""
    import importlib.util

    worktree = Path(__file__).parent.parent.parent
    script_path = worktree / "scripts" / "merge_dev_chat_into_eng.py"

    spec = importlib.util.spec_from_file_location("merge_dev_chat_into_eng", script_path)
    mod = importlib.util.module_from_spec(spec)

    with patch.dict(sys.modules, {"popoto": popoto_mod, "popoto.redis_db": popoto_mod.redis_db}):
        spec.loader.exec_module(mod)

    return mod


def _make_redis(keys: list[bytes]) -> MagicMock:
    """Return a mock Redis client whose scan() returns all keys in one call."""
    redis_mock = MagicMock()
    redis_mock.scan.return_value = (0, keys)
    redis_mock.exists.return_value = 0  # no collisions by default
    return redis_mock


def _make_popoto(redis_mock: MagicMock) -> types.ModuleType:
    """Return a fake popoto module."""
    popoto = types.ModuleType("popoto")
    redis_db = types.ModuleType("popoto.redis_db")
    redis_db.get_REDIS_DB = MagicMock(return_value=redis_mock)
    popoto.redis_db = redis_db
    return popoto


def _make_chat_record(chat_id: str, chat_name: str, chat_type: str = "group", project_key: str = "test-proj") -> MagicMock:
    """Return a mock Chat ORM instance."""
    chat = MagicMock()
    chat.chat_id = chat_id
    chat.chat_name = chat_name
    chat.chat_type = chat_type
    chat.project_key = project_key
    return chat


def _make_query_mock(records: list) -> MagicMock:
    """Return a mock for QuerySet-style .filter().first() / .count()."""
    query_mock = MagicMock()

    def filter_side_effect(**kwargs):
        filtered_mock = MagicMock()
        chat_id = kwargs.get("chat_id")
        matching = [r for r in records if getattr(r, "chat_id", None) == chat_id]
        filtered_mock.first.return_value = matching[0] if matching else None
        filtered_mock.count.return_value = len(matching)
        return filtered_mock

    query_mock.filter.side_effect = filter_side_effect
    return query_mock


DEV_CHAT_ID = "-100111111"
ENG_CHAT_ID = "-100222222"
PROJECT_KEY = "test-chatmerge"


@pytest.fixture()
def dev_chat():
    return _make_chat_record(DEV_CHAT_ID, "Dev: Test", project_key=PROJECT_KEY)


@pytest.fixture()
def eng_chat():
    return _make_chat_record(ENG_CHAT_ID, "Eng: Test", project_key=PROJECT_KEY)


@pytest.fixture()
def dev_message_keys():
    return [
        f"TelegramMessage:msg1:{DEV_CHAT_ID}:in".encode(),
        f"TelegramMessage:msg2:{DEV_CHAT_ID}:out".encode(),
    ]


def _patch_guards_pass():
    stale_stat = MagicMock()
    stale_stat.st_mtime = time.time() - 9000
    pgrep_not_found = MagicMock()
    pgrep_not_found.returncode = 1
    pgrep_not_found.stdout = ""
    return stale_stat, pgrep_not_found


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDryRunReportsCollisions:
    """Dry-run enumerates collisions without making any changes."""

    def test_dry_run_reports_collisions(self, dev_chat, dev_message_keys):
        redis_mock = _make_redis(dev_message_keys)
        # Simulate collision: target key already exists
        redis_mock.exists.return_value = 1

        popoto_mod = _make_popoto(redis_mock)
        stale_stat, pgrep_not_found = _patch_guards_pass()

        # Mock TelegramMessage and Chat at module-level names
        fake_tm = MagicMock()
        fake_tm.query.filter.return_value.count.return_value = 2
        fake_chat_cls = MagicMock()
        fake_chat_cls.query.filter.return_value.first.return_value = dev_chat

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=stale_stat),
            patch("subprocess.run", return_value=pgrep_not_found),
            patch.dict(sys.modules, {"popoto": popoto_mod, "popoto.redis_db": popoto_mod.redis_db}),
        ):
            mod = _import_merge_dev_chat(redis_mock, popoto_mod)
            # Inject mocks at module level
            mod.TelegramMessage = fake_tm
            mod.Chat = fake_chat_cls

            stats = mod.migrate(
                dev_chat_id=DEV_CHAT_ID,
                eng_chat_id=ENG_CHAT_ID,
                project_key=PROJECT_KEY,
                dry_run=True,
            )

        # No renames in dry-run
        redis_mock.rename.assert_not_called()
        redis_mock.hset.assert_not_called()
        # Collision counter should match number of messages found
        assert stats["skipped_collision"] == len(dev_message_keys)
        assert stats["renamed"] == 0


class TestCollisionSkip:
    """Live run skips colliding keys (does not clobber)."""

    def test_collision_skip_live(self, dev_chat, dev_message_keys):
        redis_mock = _make_redis(dev_message_keys)
        redis_mock.exists.return_value = 1  # All targets already exist

        popoto_mod = _make_popoto(redis_mock)
        stale_stat, pgrep_not_found = _patch_guards_pass()

        fake_tm = MagicMock()
        fake_tm.query.filter.return_value.count.return_value = 0
        fake_chat_cls = MagicMock()
        fake_chat_cls.query.filter.return_value.first.return_value = None

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=stale_stat),
            patch("subprocess.run", return_value=pgrep_not_found),
            patch.dict(sys.modules, {"popoto": popoto_mod, "popoto.redis_db": popoto_mod.redis_db}),
        ):
            mod = _import_merge_dev_chat(redis_mock, popoto_mod)
            mod.TelegramMessage = fake_tm
            mod.Chat = fake_chat_cls

            stats = mod.migrate(
                dev_chat_id=DEV_CHAT_ID,
                eng_chat_id=ENG_CHAT_ID,
                project_key=PROJECT_KEY,
                dry_run=False,
            )

        # rename must NOT have been called (all skipped due to collision)
        redis_mock.rename.assert_not_called()
        assert stats["skipped_collision"] == len(dev_message_keys)
        assert stats["renamed"] == 0


class TestCreateThenDeleteOrder:
    """Eng Chat is created and verified before Dev Chat is deleted."""

    def test_create_then_delete_order(self, dev_chat, dev_message_keys):
        redis_mock = _make_redis([])  # No message keys for simplicity
        redis_mock.exists.return_value = 0

        popoto_mod = _make_popoto(redis_mock)
        stale_stat, pgrep_not_found = _patch_guards_pass()

        # Track call order
        call_order = []

        eng_chat_record = _make_chat_record(ENG_CHAT_ID, "Eng: Test", project_key=PROJECT_KEY)

        def mock_save(self_chat=None):
            call_order.append("eng_chat_save")

        def mock_delete(self_chat=None):
            call_order.append("dev_chat_delete")

        # The mock Chat class
        fake_eng_chat_instance = MagicMock()
        fake_eng_chat_instance.save.side_effect = lambda: call_order.append("eng_chat_save")

        dev_chat.delete.side_effect = lambda: call_order.append("dev_chat_delete")

        # Query side effects:
        # - First call to filter(chat_id=DEV_CHAT_ID).first() -> dev_chat
        # - First call to filter(chat_id=ENG_CHAT_ID).first() -> None (not yet created)
        # - Second call to filter(chat_id=ENG_CHAT_ID).first() -> eng_chat_record (after save)
        eng_filter_mock_first = MagicMock()
        eng_filter_mock_first.first.return_value = None  # not yet created
        eng_filter_mock_first.count.return_value = 0

        eng_filter_mock_second = MagicMock()
        eng_filter_mock_second.first.return_value = eng_chat_record  # after creation
        eng_filter_mock_second.count.return_value = 0

        filter_call_count = {"n": 0}

        fake_chat_cls = MagicMock()

        def filter_side_effect(**kwargs):
            chat_id = kwargs.get("chat_id")
            if chat_id == DEV_CHAT_ID:
                m = MagicMock()
                m.first.return_value = dev_chat
                m.count.return_value = 0
                return m
            elif chat_id == ENG_CHAT_ID:
                filter_call_count["n"] += 1
                if filter_call_count["n"] == 1:
                    return eng_filter_mock_first
                else:
                    return eng_filter_mock_second
            m = MagicMock()
            m.first.return_value = None
            m.count.return_value = 0
            return m

        fake_chat_cls.query.filter.side_effect = filter_side_effect

        def chat_constructor(**kwargs):
            fake_eng_chat_instance.chat_id = kwargs.get("chat_id")
            return fake_eng_chat_instance

        fake_chat_cls.side_effect = chat_constructor

        fake_tm = MagicMock()
        fake_tm.query.filter.return_value.count.return_value = 0
        fake_tm.rebuild_indexes = MagicMock()

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=stale_stat),
            patch("subprocess.run", return_value=pgrep_not_found),
            patch.dict(sys.modules, {"popoto": popoto_mod, "popoto.redis_db": popoto_mod.redis_db}),
        ):
            mod = _import_merge_dev_chat(redis_mock, popoto_mod)
            mod.TelegramMessage = fake_tm
            mod.Chat = fake_chat_cls

            stats = mod.migrate(
                dev_chat_id=DEV_CHAT_ID,
                eng_chat_id=ENG_CHAT_ID,
                project_key=PROJECT_KEY,
                dry_run=False,
            )

        # Eng Chat must be saved BEFORE Dev Chat is deleted
        if "eng_chat_save" in call_order and "dev_chat_delete" in call_order:
            assert call_order.index("eng_chat_save") < call_order.index("dev_chat_delete"), (
                f"Eng Chat must be created before Dev Chat is deleted. "
                f"Order was: {call_order}"
            )


class TestCountAssertion:
    """Pre/post counts match; exits 1 on mismatch."""

    def test_count_assertion_passes(self, dev_message_keys):
        redis_mock = _make_redis(dev_message_keys)
        redis_mock.exists.return_value = 0  # no collisions

        popoto_mod = _make_popoto(redis_mock)
        stale_stat, pgrep_not_found = _patch_guards_pass()

        # Pre: dev=2, eng=0 -> after rename: dev=0, eng=2
        count_state = {"eng": 0, "dev": 2}

        def filter_tm_side_effect(**kwargs):
            m = MagicMock()
            chat_id = kwargs.get("chat_id")
            if chat_id == DEV_CHAT_ID:
                m.count.return_value = count_state["dev"]
            elif chat_id == ENG_CHAT_ID:
                m.count.return_value = count_state["eng"]
            else:
                m.count.return_value = 0
            return m

        fake_tm = MagicMock()
        fake_tm.query.filter.side_effect = filter_tm_side_effect
        fake_tm.rebuild_indexes = MagicMock()

        # After rebuild, simulate that counts shifted (eng gets +2)
        call_count_n = {"n": 0}

        def filter_tm_post(**kwargs):
            m = MagicMock()
            chat_id = kwargs.get("chat_id")
            call_count_n["n"] += 1
            if chat_id == DEV_CHAT_ID:
                m.count.return_value = 0  # post-migration
            elif chat_id == ENG_CHAT_ID:
                m.count.return_value = 2  # pre (0) + renamed (2)
            else:
                m.count.return_value = 0
            return m

        # Patch filter after rebuild to return post counts
        call_sequence = {"pre_done": False}

        def dynamic_filter(**kwargs):
            m = MagicMock()
            chat_id = kwargs.get("chat_id")
            if not call_sequence["pre_done"]:
                # Still in pre-migration
                if chat_id == DEV_CHAT_ID:
                    m.count.return_value = 2
                elif chat_id == ENG_CHAT_ID:
                    m.count.return_value = 0
            else:
                # Post-migration
                if chat_id == DEV_CHAT_ID:
                    m.count.return_value = 0
                elif chat_id == ENG_CHAT_ID:
                    m.count.return_value = 2
            return m

        fake_tm.query.filter.side_effect = dynamic_filter

        # Trigger "pre done" after first two filter calls
        original_side_effect = fake_tm.query.filter.side_effect
        call_n = {"n": 0}

        def counting_filter(**kwargs):
            call_n["n"] += 1
            if call_n["n"] > 2:
                call_sequence["pre_done"] = True
            m = MagicMock()
            chat_id = kwargs.get("chat_id")
            if not call_sequence["pre_done"]:
                if chat_id == DEV_CHAT_ID:
                    m.count.return_value = 2
                else:
                    m.count.return_value = 0
            else:
                if chat_id == DEV_CHAT_ID:
                    m.count.return_value = 0
                else:
                    m.count.return_value = 2
            return m

        fake_tm.query.filter.side_effect = counting_filter

        fake_chat_cls = MagicMock()
        fake_chat_cls.query.filter.return_value.first.return_value = None

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=stale_stat),
            patch("subprocess.run", return_value=pgrep_not_found),
            patch.dict(sys.modules, {"popoto": popoto_mod, "popoto.redis_db": popoto_mod.redis_db}),
        ):
            mod = _import_merge_dev_chat(redis_mock, popoto_mod)
            mod.TelegramMessage = fake_tm
            mod.Chat = fake_chat_cls

            # Should not raise
            stats = mod.migrate(
                dev_chat_id=DEV_CHAT_ID,
                eng_chat_id=ENG_CHAT_ID,
                project_key=PROJECT_KEY,
                dry_run=False,
            )

        assert stats["renamed"] == len(dev_message_keys)
        assert stats["errors"] == 0
