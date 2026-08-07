"""DM recovery coverage for the three scanners (Task 10, issue #2494).

The DM loss class: all three recovery scanners used to open with
``if not chat_title: continue`` — a Telegram private chat is a ``User`` entity
with no ``title``, so DMs were permanently lost if the bridge crashed before
enqueue. These tests pin the new behavior: scanners iterate Rooms (groups AND
DMs), DMs resolve via the whitelist (``find_project_for_dm``), bots/self never
scan, and a newly-covered DM Room's cursor initializes at "now" — the first
deploy must never replay old DM history as "recovered" messages.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bridge.routing as routing
from bridge.reconciler import reconcile_once


def _make_message(msg_id, text=None, out=False, minutes_ago=2, sender_id=42):
    msg = MagicMock()
    msg.id = msg_id
    msg.text = f"Message {msg_id}" if text is None else text
    msg.out = out
    msg.date = datetime.now(UTC) - timedelta(minutes=minutes_ago)

    sender = MagicMock()
    sender.first_name = "Tom"
    sender.username = "tom"
    sender.id = sender_id
    msg.get_sender = AsyncMock(return_value=sender)
    return msg


def _make_dm_dialog(user_id, first_name="Tom", bot=False, is_self=False):
    """A Telethon DM dialog: a User entity with NO title attribute."""
    dialog = MagicMock()
    dialog.entity = MagicMock()
    dialog.entity.title = None  # User entities have no title
    dialog.entity.id = user_id
    dialog.entity.first_name = first_name
    dialog.entity.bot = bot
    dialog.entity.is_self = is_self
    dialog.id = user_id  # DM chat_id == user id (no -100 prefix)
    return dialog


@pytest.fixture
def dm_project():
    return {"_key": "test-dmrec", "working_directory": "/tmp/test-dmrec"}


@pytest.fixture
def fresh_user_id():
    """Unique user id per test so coverage-epoch keys never collide."""
    uid = int(uuid.uuid4().int % 10**9) + 10**9
    yield uid
    from bridge.dedup import _get_redis

    _get_redis().delete(f"bridge:dm_coverage_epoch:{uid}")


def _reconcile_patches():
    return (
        patch("bridge.catchup.catchup_disabled", return_value=False),
        patch("bridge.reconciler.is_duplicate_message", new=AsyncMock(return_value=False)),
        patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
        patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
    )


async def _run_reconcile(client, should_respond_fn=None, enqueue=None):
    return await reconcile_once(
        client=client,
        monitored_groups=["some group"],
        should_respond_fn=should_respond_fn or AsyncMock(return_value=(True, False)),
        enqueue_agent_session_fn=enqueue or AsyncMock(),
        find_project_fn=MagicMock(return_value=None),
    )


class TestReconcilerDMCoverage:
    @pytest.mark.asyncio
    async def test_first_dm_scan_initializes_epoch_and_skips(
        self, dm_project, fresh_user_id, monkeypatch
    ):
        """Newly-covered DM Room: cursor initializes at now, no history replay."""
        monkeypatch.setitem(routing.DM_USER_TO_PROJECT, fresh_user_id, dm_project)
        dialog = _make_dm_dialog(fresh_user_id)
        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])

        enqueue = AsyncMock()
        p1, p2, p3, p4 = _reconcile_patches()
        with p1, p2, p3, p4:
            recovered = await _run_reconcile(client, enqueue=enqueue)

        assert recovered == 0
        enqueue.assert_not_called()

        from bridge.dedup import get_dm_coverage_epoch

        epoch = await get_dm_coverage_epoch(fresh_user_id)
        assert epoch is not None
        assert abs((datetime.now(UTC) - epoch).total_seconds()) < 60

    @pytest.mark.asyncio
    async def test_second_dm_scan_recovers_missed_message(
        self, dm_project, fresh_user_id, monkeypatch
    ):
        """After the epoch exists, a missed DM inside the window is recovered."""
        monkeypatch.setitem(routing.DM_USER_TO_PROJECT, fresh_user_id, dm_project)
        dialog = _make_dm_dialog(fresh_user_id)
        missed = _make_message(7, minutes_ago=5, sender_id=fresh_user_id)
        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])

        # Epoch predates the missed message (coverage began before it arrived).
        epoch = datetime.now(UTC) - timedelta(minutes=10)
        enqueue = AsyncMock()
        should_respond = AsyncMock(return_value=(True, False))

        p1, p2, p3, p4 = _reconcile_patches()
        with (
            p1,
            p2,
            p3,
            p4,
            patch(
                "bridge.reconciler.get_or_init_dm_coverage_epoch",
                new=AsyncMock(return_value=(epoch, False)),
            ),
            patch(
                "bridge.reconciler.fetch_messages_back_to",
                new=AsyncMock(return_value=[missed]),
            ),
        ):
            recovered = await _run_reconcile(client, should_respond, enqueue)

        assert recovered == 1
        kwargs = enqueue.call_args.kwargs
        assert kwargs["project_key"] == "test-dmrec"
        assert kwargs["chat_id"] == str(fresh_user_id)
        assert kwargs["chat_title"] is None, "DM parity: live handler has no chat_title"
        assert kwargs["telegram_message_id"] == 7

        # should_respond saw a DM: is_dm=True, chat_title=None, private event.
        sr_args = should_respond.call_args.args
        minimal_event = sr_args[1]
        assert minimal_event.is_private is True
        assert sr_args[3] is True  # is_dm
        assert sr_args[4] is None  # chat_title

    @pytest.mark.asyncio
    async def test_dm_message_before_epoch_is_never_replayed(
        self, dm_project, fresh_user_id, monkeypatch
    ):
        """Pre-coverage history stays untouched even inside the global window."""
        monkeypatch.setitem(routing.DM_USER_TO_PROJECT, fresh_user_id, dm_project)
        dialog = _make_dm_dialog(fresh_user_id)
        # In the 30-min global window, but BEFORE the coverage epoch.
        old = _make_message(3, minutes_ago=20, sender_id=fresh_user_id)
        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])

        epoch = datetime.now(UTC) - timedelta(minutes=10)
        enqueue = AsyncMock()

        p1, p2, p3, p4 = _reconcile_patches()
        with (
            p1,
            p2,
            p3,
            p4,
            patch(
                "bridge.reconciler.get_or_init_dm_coverage_epoch",
                new=AsyncMock(return_value=(epoch, False)),
            ),
            patch(
                "bridge.reconciler.fetch_messages_back_to",
                new=AsyncMock(return_value=[old]),
            ),
        ):
            recovered = await _run_reconcile(client, enqueue=enqueue)

        assert recovered == 0
        enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_bot_and_self_dm_dialogs_never_scan(self, dm_project, monkeypatch):
        bot_id, self_id = 111000111, 222000222
        monkeypatch.setitem(routing.DM_USER_TO_PROJECT, bot_id, dm_project)
        monkeypatch.setitem(routing.DM_USER_TO_PROJECT, self_id, dm_project)
        dialogs = [
            _make_dm_dialog(bot_id, bot=True),
            _make_dm_dialog(self_id, is_self=True),
        ]
        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=dialogs)

        enqueue = AsyncMock()
        p1, p2, p3, p4 = _reconcile_patches()
        with p1, p2, p3, p4:
            recovered = await _run_reconcile(client, enqueue=enqueue)

        assert recovered == 0
        enqueue.assert_not_called()
        from bridge.dedup import get_dm_coverage_epoch

        # No epoch was initialized for either — they are not covered Rooms.
        assert await get_dm_coverage_epoch(bot_id) is None
        assert await get_dm_coverage_epoch(self_id) is None

    @pytest.mark.asyncio
    async def test_non_whitelisted_dm_skipped(self):
        dialog = _make_dm_dialog(333000333)
        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])

        enqueue = AsyncMock()
        p1, p2, p3, p4 = _reconcile_patches()
        with p1, p2, p3, p4:
            recovered = await _run_reconcile(client, enqueue=enqueue)

        assert recovered == 0
        enqueue.assert_not_called()


class TestCatchupDMCoverage:
    @pytest.mark.asyncio
    async def test_startup_catchup_recovers_dm(self, dm_project, fresh_user_id, monkeypatch):
        from bridge.catchup import scan_for_missed_messages

        monkeypatch.setitem(routing.DM_USER_TO_PROJECT, fresh_user_id, dm_project)
        dialog = _make_dm_dialog(fresh_user_id)
        missed = _make_message(9, minutes_ago=5, sender_id=fresh_user_id)
        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])

        epoch = datetime.now(UTC) - timedelta(minutes=30)
        enqueue = AsyncMock()
        should_respond = AsyncMock(return_value=(True, False))

        with (
            patch("bridge.catchup.catchup_disabled", return_value=False),
            patch("bridge.dedup.is_duplicate_message", new=AsyncMock(return_value=False)),
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
            patch("bridge.dedup.record_last_processed", new_callable=AsyncMock),
            patch(
                "bridge.catchup.get_or_init_dm_coverage_epoch",
                new=AsyncMock(return_value=(epoch, False)),
            ),
            patch(
                "bridge.catchup.fetch_messages_back_to",
                new=AsyncMock(return_value=[missed]),
            ),
        ):
            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["some group"],
                projects_config={},
                should_respond_fn=should_respond,
                enqueue_agent_session_fn=enqueue,
                find_project_fn=MagicMock(return_value=None),
            )

        assert queued == 1
        kwargs = enqueue.call_args.kwargs
        assert kwargs["chat_id"] == str(fresh_user_id)
        assert kwargs["chat_title"] is None

    @pytest.mark.asyncio
    async def test_startup_catchup_first_dm_scan_skips(
        self, dm_project, fresh_user_id, monkeypatch
    ):
        from bridge.catchup import scan_for_missed_messages

        monkeypatch.setitem(routing.DM_USER_TO_PROJECT, fresh_user_id, dm_project)
        dialog = _make_dm_dialog(fresh_user_id)
        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        enqueue = AsyncMock()

        with patch("bridge.catchup.catchup_disabled", return_value=False):
            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=[],
                projects_config={},
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue,
                find_project_fn=MagicMock(return_value=None),
            )

        assert queued == 0
        enqueue.assert_not_called()


class TestAgentCatchupDMCoverage:
    @pytest.mark.asyncio
    async def test_resolve_owned_chats_includes_covered_dm(
        self, dm_project, fresh_user_id, monkeypatch
    ):
        from bridge.agent_catchup import resolve_owned_chats

        monkeypatch.setitem(routing.DM_USER_TO_PROJECT, fresh_user_id, dm_project)
        dialog = _make_dm_dialog(fresh_user_id)
        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])

        epoch = datetime.now(UTC) - timedelta(minutes=30)
        with patch(
            "bridge.agent_catchup.get_or_init_dm_coverage_epoch",
            new=AsyncMock(return_value=(epoch, False)),
        ):
            owned = await resolve_owned_chats(client, [], MagicMock(return_value=None))

        assert len(owned) == 1
        chat = owned[0]
        assert chat.chat_id == fresh_user_id
        assert chat.is_dm is True
        assert chat.chat_title is None
        assert chat.project["_key"] == "test-dmrec"

    @pytest.mark.asyncio
    async def test_resolve_owned_chats_skips_dm_on_epoch_init(
        self, dm_project, fresh_user_id, monkeypatch
    ):
        from bridge.agent_catchup import resolve_owned_chats

        monkeypatch.setitem(routing.DM_USER_TO_PROJECT, fresh_user_id, dm_project)
        dialog = _make_dm_dialog(fresh_user_id)
        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])

        owned = await resolve_owned_chats(client, [], MagicMock(return_value=None))
        assert owned == []  # first pass: epoch just initialized, nothing to sweep


class TestNoChatTitleFilterRemains:
    def test_scanners_do_not_skip_titleless_dialogs(self):
        """Verification row: `grep 'if not chat_title' bridge/` must be 0."""
        import pathlib

        bridge_dir = pathlib.Path(__file__).resolve().parents[2] / "bridge"
        offenders = [
            str(p)
            for p in bridge_dir.glob("*.py")
            if "if not chat_title" in p.read_text(encoding="utf-8")
        ]
        assert offenders == []
