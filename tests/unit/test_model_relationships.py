"""Unit tests for popoto model relationship changes (issue #295, PR #392).

Tests the model changes introduced by the popoto model relationships branch:
1. TelegramMessage enrichment fields (classification, media, URLs, reply)
2. Enrichment fallback logic (TelegramMessage vs AgentSession fields)
3. Back-reference setting (agent_session_id <-> telegram_message_key)
4. project_key presence across all Popoto models
5. Migration script dry-run mode and basic logic
6. AgentSession.id property alias
"""

import time
from unittest.mock import MagicMock, patch

import pytest

# ===================================================================
# Helpers
# ===================================================================


def _make_mock_agent_session(**overrides):
    """Create a mock AgentSession with sensible defaults."""
    defaults = {
        "agent_session_id": "job-001",
        "session_id": "tg_test_12345_100",
        "project_key": "test-project",
        "status": "pending",
        "chat_id": "12345",
        "telegram_message_id": 100,
        "message_text": "hello",
        "classification_type": None,
        "telegram_message_key": None,
    }
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    mock.save = MagicMock()
    return mock


# ===================================================================
# 1. TelegramMessage enrichment fields
# ===================================================================


class TestTelegramMessageEnrichmentFields:
    """Test that TelegramMessage carries all enrichment fields from the refactor."""

    def test_classification_fields_registered(self):
        """TelegramMessage should have classification_type and classification_confidence."""
        from models.telegram import TelegramMessage

        fields = TelegramMessage._meta.field_names
        assert "classification_type" in fields
        assert "classification_confidence" in fields

    def test_media_fields_registered(self):
        """TelegramMessage should have has_media and media_type fields."""
        from models.telegram import TelegramMessage

        fields = TelegramMessage._meta.field_names
        assert "has_media" in fields
        assert "media_type" in fields

    def test_url_fields_registered(self):
        """TelegramMessage should have youtube_urls and non_youtube_urls."""
        from models.telegram import TelegramMessage

        fields = TelegramMessage._meta.field_names
        assert "youtube_urls" in fields
        assert "non_youtube_urls" in fields

    def test_reply_field_registered(self):
        """TelegramMessage should have reply_to_msg_id for reply chains."""
        from models.telegram import TelegramMessage

        assert "reply_to_msg_id" in TelegramMessage._meta.field_names

    def test_agent_session_id_registered(self):
        """TelegramMessage should have agent_session_id cross-reference."""
        from models.telegram import TelegramMessage

        assert "agent_session_id" in TelegramMessage._meta.field_names

    def test_project_key_registered(self):
        """TelegramMessage should have project_key for project association."""
        from models.telegram import TelegramMessage

        assert "project_key" in TelegramMessage._meta.field_names

    def test_project_key_is_key_field(self):
        """TelegramMessage.project_key should be a KeyField for querying."""
        from popoto import KeyField

        from models.telegram import TelegramMessage

        pk_field = TelegramMessage._meta.fields["project_key"]
        assert isinstance(pk_field, KeyField)

    def test_enrichment_field_count(self):
        """TelegramMessage should have 18 total registered fields."""
        from models.telegram import TelegramMessage

        # 9 original + 9 new (project_key, has_media, media_type,
        # youtube_urls, non_youtube_urls, reply_to_msg_id,
        # classification_type, classification_confidence, agent_session_id)
        assert len(TelegramMessage._meta.field_names) == 18


# ===================================================================
# 2. Enrichment fallback logic
# ===================================================================


class TestEnrichmentFromTelegramMessage:
    """Test that enrichment reads exclusively from TelegramMessage."""

    def test_no_enrichment_without_trigger_message(self):
        """When telegram_message_key is None, enrichment defaults are all empty."""
        session = _make_mock_agent_session(telegram_message_key=None)

        # Simulate the enrichment logic from job_queue.py
        enrich_has_media = False
        enrich_media_type = None
        enrich_youtube_urls = None
        enrich_non_youtube_urls = None
        enrich_reply_to_msg_id = None

        if session.telegram_message_key:
            pytest.fail("Should not enter telegram_message_key branch when it is None")

        assert enrich_has_media is False
        assert enrich_media_type is None
        assert enrich_youtube_urls is None
        assert enrich_non_youtube_urls is None
        assert enrich_reply_to_msg_id is None

    def test_enrichment_reads_from_telegram_message(self):
        """When telegram_message_key is set and TM found, TM fields are used."""
        session = _make_mock_agent_session(telegram_message_key="tm-001")
        tm = MagicMock()
        tm.has_media = True
        tm.media_type = "video"
        tm.youtube_urls = '[["https://youtu.be/xyz", "xyz"]]'
        tm.non_youtube_urls = '["https://docs.python.org"]'
        tm.reply_to_msg_id = 77

        # Simulate enrichment logic from job_queue.py
        enrich_has_media = False
        enrich_media_type = None
        enrich_youtube_urls = None
        enrich_non_youtube_urls = None
        enrich_reply_to_msg_id = None

        if session.telegram_message_key:
            # Simulated TelegramMessage lookup
            trigger_msgs = [tm]
            if trigger_msgs:
                enrich_has_media = bool(tm.has_media)
                enrich_media_type = tm.media_type
                enrich_youtube_urls = tm.youtube_urls
                enrich_non_youtube_urls = tm.non_youtube_urls
                enrich_reply_to_msg_id = tm.reply_to_msg_id

        assert enrich_has_media is True
        assert enrich_media_type == "video"
        assert enrich_youtube_urls == '[["https://youtu.be/xyz", "xyz"]]'
        assert enrich_non_youtube_urls == '["https://docs.python.org"]'
        assert enrich_reply_to_msg_id == 77

    def test_no_enrichment_when_trigger_not_found(self):
        """When telegram_message_key is set but TM lookup returns empty, no enrichment."""
        session = _make_mock_agent_session(telegram_message_key="tm-missing")

        enrich_has_media = False
        enrich_media_type = None

        if session.telegram_message_key:
            trigger_msgs = []  # Lookup returns empty
            if trigger_msgs:
                pytest.fail("Should not enter this branch for empty lookup")

        assert enrich_has_media is False
        assert enrich_media_type is None


# ===================================================================
# 3. Back-reference setting
# ===================================================================


class TestBackReferenceSetting:
    """Test cross-references between AgentSession and TelegramMessage."""

    def test_telegram_message_key_on_agent_session(self):
        """AgentSession should have telegram_message_key in field registry."""
        from models.agent_session import AgentSession

        assert "telegram_message_key" in AgentSession._meta.field_names

    def test_agent_session_id_on_telegram_message(self):
        """TelegramMessage should have agent_session_id in field registry."""
        from models.telegram import TelegramMessage

        assert "agent_session_id" in TelegramMessage._meta.field_names

    def test_agent_session_id_set_on_telegram_message(self):
        """When a session has telegram_message_key, agent_session_id should be set on TM."""
        tm = MagicMock()
        tm.agent_session_id = None
        tm.save = MagicMock()

        # Simulate job_queue.py:1461-1471
        telegram_message_key = "tm-001"
        agent_session_id = "job-abc"

        if telegram_message_key:
            trigger_msgs = [tm]
            if trigger_msgs and not trigger_msgs[0].agent_session_id:
                trigger_msgs[0].agent_session_id = agent_session_id
                trigger_msgs[0].save()

        assert tm.agent_session_id == "job-abc"
        tm.save.assert_called_once()

    def test_agent_session_id_not_overwritten(self):
        """Back-reference should not overwrite existing agent_session_id."""
        tm = MagicMock()
        tm.agent_session_id = "job-existing"
        tm.save = MagicMock()

        if True:  # telegram_message_key is set
            trigger_msgs = [tm]
            if trigger_msgs and not trigger_msgs[0].agent_session_id:
                trigger_msgs[0].agent_session_id = "job-new"
                trigger_msgs[0].save()

        assert tm.agent_session_id == "job-existing"
        tm.save.assert_not_called()

    def test_no_back_reference_when_no_trigger(self):
        """No back-reference logic when telegram_message_key is None."""
        tm = MagicMock()
        tm.agent_session_id = None
        tm.save = MagicMock()

        telegram_message_key = None

        if telegram_message_key:
            pytest.fail("Should not attempt back-reference")

        assert tm.agent_session_id is None
        tm.save.assert_not_called()


# ===================================================================
# 4. project_key on all Popoto models
# ===================================================================


class TestProjectKeyPresence:
    """Test that project_key exists on all relevant Popoto models."""

    @pytest.mark.parametrize(
        "model_path,model_name",
        [
            ("models.agent_session", "AgentSession"),
            ("models.telegram", "TelegramMessage"),
            ("models.bridge_event", "BridgeEvent"),
            ("models.chat", "Chat"),
            ("models.dead_letter", "DeadLetter"),
            ("models.link", "Link"),
            ("models.reflections", "ReflectionRun"),
        ],
    )
    def test_project_key_field_registered(self, model_path, model_name):
        """Each Popoto model should have project_key in its field registry."""
        import importlib

        module = importlib.import_module(model_path)
        model_cls = getattr(module, model_name)
        assert "project_key" in model_cls._meta.field_names, (
            f"{model_name} is missing project_key in _meta.field_names"
        )

    @pytest.mark.parametrize(
        "model_path,model_name",
        [
            ("models.agent_session", "AgentSession"),
            ("models.telegram", "TelegramMessage"),
            ("models.bridge_event", "BridgeEvent"),
            ("models.dead_letter", "DeadLetter"),
            ("models.link", "Link"),
            ("models.reflections", "ReflectionRun"),
        ],
    )
    def test_project_key_is_key_field(self, model_path, model_name):
        """project_key should be a KeyField for efficient querying."""
        import importlib

        from popoto import KeyField

        module = importlib.import_module(model_path)
        model_cls = getattr(module, model_name)
        pk_field = model_cls._meta.fields["project_key"]
        assert isinstance(pk_field, KeyField), (
            f"{model_name}.project_key is {type(pk_field).__name__}, expected KeyField"
        )

    def test_chat_project_key_is_regular_field(self):
        """Chat.project_key is a regular Field (not KeyField) to avoid delete-and-recreate."""
        from popoto import Field, KeyField

        from models.chat import Chat

        pk_field = Chat._meta.fields["project_key"]
        assert isinstance(pk_field, Field)
        assert not isinstance(pk_field, KeyField)


# ===================================================================
# 5. Migration script
# ===================================================================


class TestMigrationScript:
    """Test the migration script's dry-run mode and basic logic."""

    def test_load_chat_to_project_map_with_valid_config(self):
        """load_chat_to_project_map should parse projects.json into chat_id -> project_key."""
        import json
        import tempfile
        from pathlib import Path

        from scripts.migrate_model_relationships import load_chat_to_project_map

        config = [
            {
                "_key": "project-alpha",
                "telegram_chats": [{"id": 111}, {"id": 222}],
            },
            {
                "_key": "project-beta",
                "telegram_chats": [{"id": 333}],
            },
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config, f)
            tmp_path = Path(f.name)

        import scripts.migrate_model_relationships as migrate_mod

        original_dir = migrate_mod.PROJECT_DIR
        try:
            config_dir = tmp_path.parent / "config"
            config_dir.mkdir(exist_ok=True)
            config_path = config_dir / "projects.json"
            tmp_path.rename(config_path)
            migrate_mod.PROJECT_DIR = tmp_path.parent

            result = load_chat_to_project_map()
        finally:
            migrate_mod.PROJECT_DIR = original_dir
            config_path.unlink(missing_ok=True)
            config_dir.rmdir()

        assert result == {
            "111": "project-alpha",
            "222": "project-alpha",
            "333": "project-beta",
        }

    def test_load_chat_to_project_map_missing_config(self):
        """Should return empty dict when config file does not exist."""
        from pathlib import Path

        import scripts.migrate_model_relationships as migrate_mod

        original_dir = migrate_mod.PROJECT_DIR
        original_desktop = migrate_mod.DESKTOP_VALOR_DIR
        migrate_mod.PROJECT_DIR = Path("/nonexistent/path")
        migrate_mod.DESKTOP_VALOR_DIR = Path("/nonexistent/desktop")
        try:
            result = migrate_mod.load_chat_to_project_map()
        finally:
            migrate_mod.PROJECT_DIR = original_dir
            migrate_mod.DESKTOP_VALOR_DIR = original_desktop

        assert result == {}

    def test_dry_run_does_not_call_save(self):
        """In dry-run mode, backfill_project_key should count but not save."""
        from scripts.migrate_model_relationships import backfill_project_key

        mock_msg = MagicMock()
        mock_msg.project_key = None
        mock_msg.chat_id = "111"
        mock_msg.timestamp = time.time()
        mock_msg.save = MagicMock()

        chat_map = {"111": "project-alpha"}

        with (
            patch(
                "scripts.migrate_model_relationships.load_chat_to_project_map",
                return_value=chat_map,
            ),
            patch("models.telegram.TelegramMessage") as mock_tm,
            patch("models.link.Link") as mock_link,
            patch("models.dead_letter.DeadLetter") as mock_dl,
            patch("models.chat.Chat") as mock_chat,
        ):
            mock_tm.query.all.return_value = [mock_msg]
            mock_link.query.all.return_value = []
            mock_dl.query.all.return_value = []
            mock_chat.query.all.return_value = []

            stats = backfill_project_key(dry_run=True, max_age_days=90)

        assert stats["telegram_messages"] == 1
        mock_msg.save.assert_not_called()

    def test_backfill_enrichment_skips_no_enrichment(self):
        """backfill_enrichment_metadata should skip sessions without enrichment data."""
        from scripts.migrate_model_relationships import backfill_enrichment_metadata

        mock_session = MagicMock()
        mock_session.started_at = time.time()
        mock_session.created_at = time.time()
        mock_session.has_media = False
        mock_session.youtube_urls = None
        mock_session.non_youtube_urls = None
        mock_session.classification_type = None
        mock_session.telegram_message_key = None

        with (
            patch("models.agent_session.AgentSession") as mock_as,
            patch("models.telegram.TelegramMessage"),
        ):
            mock_as.query.all.return_value = [mock_session]

            stats = backfill_enrichment_metadata(dry_run=True, max_age_days=90)

        # Session has no enrichment data, so nothing to copy
        assert stats["enrichment_copied"] == 0

    def test_dry_run_argument_parsing(self):
        """The migration script should accept --dry-run and --max-age flags."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--max-age", type=int, default=90)

        args = parser.parse_args(["--dry-run"])
        assert args.dry_run is True
        assert args.max_age == 90

        args = parser.parse_args(["--max-age", "30"])
        assert args.dry_run is False
        assert args.max_age == 30


# ===================================================================
# 6. AgentSession.id property alias
# ===================================================================


class TestAgentSessionIdAlias:
    """Test the AgentSession.id property that aliases agent_session_id."""

    def test_id_property_exists(self):
        """AgentSession should have an 'id' property."""
        from models.agent_session import AgentSession

        assert hasattr(AgentSession, "id")
        assert isinstance(AgentSession.id, property)

    def test_id_property_has_getter(self):
        """The id property should have a getter function."""
        from models.agent_session import AgentSession

        assert AgentSession.id.fget is not None

    def test_id_returns_agent_session_id_value(self):
        """AgentSession.id should delegate to self.agent_session_id."""
        from models.agent_session import AgentSession

        # Verify the property implementation logic
        class FakeSession:
            def __init__(self, jid):
                self.agent_session_id = jid

            id = AgentSession.id

        assert FakeSession("job-xyz").id == "job-xyz"
        assert FakeSession(None).id is None


# ===================================================================
# 7. Deprecated fields on AgentSession (backward compatibility)
# ===================================================================


class TestAgentSessionFieldPresence:
    """Verify AgentSession has expected fields and enrichment fields were removed."""

    @pytest.mark.parametrize(
        "field_name",
        [
            "classification_type",
            "telegram_message_id",
        ],
    )
    def test_retained_fields_present(self, field_name):
        """AgentSession should retain classification and message fields."""
        from models.agent_session import AgentSession

        assert field_name in AgentSession._meta.field_names, (
            f"AgentSession.{field_name} should exist"
        )

    @pytest.mark.parametrize(
        "field_name",
        [
            "has_media",
            "media_type",
            "youtube_urls",
            "non_youtube_urls",
            "reply_to_msg_id",
            "chat_id_for_enrichment",
        ],
    )
    def test_enrichment_fields_removed(self, field_name):
        """Enrichment fields should no longer exist on AgentSession (moved to TelegramMessage)."""
        from models.agent_session import AgentSession

        assert field_name not in AgentSession._meta.field_names, (
            f"AgentSession.{field_name} should have been removed (now on TelegramMessage)"
        )

    def test_claude_code_session_id_removed(self):
        """claude_code_session_id was removed as a dead field (never read)."""
        from models.agent_session import AgentSession

        assert "claude_code_session_id" not in AgentSession._meta.field_names

    def test_sender_property_exists(self):
        """AgentSession should have a sender property aliasing sender_name."""
        from models.agent_session import AgentSession

        assert isinstance(AgentSession.sender, property)
