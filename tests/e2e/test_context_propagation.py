"""E2E tests for context propagation across session types.

Verifies that thread context, task lists, worktree paths, and parent
linkage flow correctly from ChatSession → DevSession → Redis.
Uses real Redis via conftest redis_test_db.
"""

import json
import time

import pytest

from models.agent_session import (
    SESSION_TYPE_CHAT,
    SESSION_TYPE_DEV,
    AgentSession,
)


@pytest.mark.e2e
class TestChatSessionContextFields:
    """Verify all context fields persist through create → save → reload."""

    def test_all_context_fields_roundtrip(self):
        ts = int(time.time())
        session = AgentSession.create_chat(
            session_id=f"ctx_{ts}",
            project_key="valor",
            working_dir="/Users/test/src/ai",
            chat_id="ctx_chat_123",
            message_id=42,
            message_text="build the feature",
            sender_name="Valor",
            sender_id=111222,
            chat_title="Dev: Valor",
        )
        # Set additional context fields
        session.task_list_id = "thread-ctx_chat_123-42"
        session.work_item_slug = "my-feature"
        session.branch_name = "session/my-feature"
        session.correlation_id = f"corr_{ts}"
        session.save()

        # Reload from Redis
        reloaded = list(AgentSession.query.filter(session_id=session.session_id))[0]

        assert reloaded.session_id == f"ctx_{ts}"
        assert reloaded.session_type == SESSION_TYPE_CHAT
        assert reloaded.project_key == "valor"
        assert reloaded.working_dir == "/Users/test/src/ai"
        assert reloaded.chat_id == "ctx_chat_123"
        assert reloaded.message_id == 42
        assert reloaded.message_text == "build the feature"
        assert reloaded.sender_name == "Valor"
        assert reloaded.sender_id == 111222
        assert reloaded.chat_title == "Dev: Valor"
        assert reloaded.task_list_id == "thread-ctx_chat_123-42"
        assert reloaded.work_item_slug == "my-feature"
        assert reloaded.branch_name == "session/my-feature"
        assert reloaded.correlation_id == f"corr_{ts}"


@pytest.mark.e2e
class TestDevSessionParentLinkage:
    """Verify DevSession correctly links to its parent ChatSession."""

    def test_dev_session_has_parent_reference(self):
        ts = int(time.time())
        chat = AgentSession.create_chat(
            session_id=f"parent_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="link_chat",
            message_id=1,
            message_text="do work",
        )

        dev = AgentSession.create_dev(
            session_id=f"child_{ts}",
            project_key="valor",
            working_dir="/tmp/test/.worktrees/my-feature",
            parent_chat_session_id=chat.job_id,
            message_text="/do-build",
            slug="my-feature",
        )

        assert dev.session_type == SESSION_TYPE_DEV
        assert dev.parent_chat_session_id == chat.job_id

        # Navigate from child to parent via filter
        parents = list(AgentSession.query.filter(session_id=chat.session_id))
        assert len(parents) == 1
        assert parents[0].job_id == chat.job_id
        assert parents[0].session_type == SESSION_TYPE_CHAT

    def test_chat_session_finds_its_dev_sessions(self):
        ts = int(time.time())
        chat = AgentSession.create_chat(
            session_id=f"multi_parent_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="multi_chat",
            message_id=5,
            message_text="complex task",
        )

        dev1 = AgentSession.create_dev(
            session_id=f"dev1_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            parent_chat_session_id=chat.job_id,
            message_text="/do-build",
        )
        dev2 = AgentSession.create_dev(
            session_id=f"dev2_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            parent_chat_session_id=chat.job_id,
            message_text="/do-test",
        )

        children = chat.get_dev_sessions()
        child_ids = {c.job_id for c in children}
        assert dev1.job_id in child_ids
        assert dev2.job_id in child_ids

    def test_orphan_dev_session_returns_none_parent(self):
        ts = int(time.time())
        dev = AgentSession.create_dev(
            session_id=f"orphan_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            parent_chat_session_id="nonexistent_id",
            message_text="lost",
        )
        assert dev.get_parent_chat_session() is None


@pytest.mark.e2e
class TestDerivedPaths:
    """Verify slug-derived branch names and plan paths."""

    def test_slug_derives_branch_name(self):
        ts = int(time.time())
        dev = AgentSession.create_dev(
            session_id=f"slug_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            parent_chat_session_id="parent_x",
            message_text="build",
            slug="my-cool-feature",
        )
        assert dev.derived_branch_name == "session/my-cool-feature"
        assert dev.plan_path == "docs/plans/my-cool-feature.md"

    def test_no_slug_falls_back_to_branch_name(self):
        ts = int(time.time())
        session = AgentSession.create_chat(
            session_id=f"nosluq_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="fb_chat",
            message_id=1,
            message_text="quick question",
        )
        session.branch_name = "feature/manual-branch"
        session.save()

        reloaded = list(AgentSession.query.filter(session_id=session.session_id))[0]
        assert reloaded.derived_branch_name == "feature/manual-branch"
        assert reloaded.plan_path is None


@pytest.mark.e2e
class TestSDLCStagesPropagation:
    """Verify SDLC stages dict flows correctly on DevSessions."""

    def test_stage_states_persist_as_json(self):
        ts = int(time.time())
        stages = {"PLAN": "completed", "BUILD": "in_progress", "TEST": "pending"}
        dev = AgentSession.create_dev(
            session_id=f"stages_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            parent_chat_session_id="parent_y",
            message_text="/do-build",
            stage_states=stages,
        )

        reloaded = list(AgentSession.query.filter(session_id=dev.session_id))[0]
        parsed = json.loads(reloaded.stage_states)
        assert parsed["PLAN"] == "completed"
        assert parsed["BUILD"] == "in_progress"
        assert parsed["TEST"] == "pending"
        assert reloaded.is_sdlc is True
        assert reloaded.current_stage == "BUILD"

    def test_chat_session_without_stage_states(self):
        ts = int(time.time())
        chat = AgentSession.create_chat(
            session_id=f"nosdlc_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="chat_no_sdlc",
            message_id=1,
            message_text="what time is it?",
        )
        assert chat.is_sdlc is False
        assert chat.current_stage is None


@pytest.mark.e2e
class TestSessionTypeDiscriminator:
    """Verify session_type field correctly discriminates session kinds."""

    def test_factory_methods_set_correct_types(self):
        ts = int(time.time())

        chat = AgentSession.create_chat(
            session_id=f"type_chat_{ts}",
            project_key="valor",
            working_dir="/tmp",
            chat_id="tc",
            message_id=1,
            message_text="hi",
        )
        dev = AgentSession.create_dev(
            session_id=f"type_dev_{ts}",
            project_key="valor",
            working_dir="/tmp",
            parent_chat_session_id=chat.job_id,
            message_text="build",
        )

        assert chat.is_chat and not chat.is_dev
        assert dev.is_dev and not dev.is_chat

    def test_session_type_queryable(self):
        ts = int(time.time())
        AgentSession.create_chat(
            session_id=f"qt_chat_{ts}",
            project_key="query_test",
            working_dir="/tmp",
            chat_id="qt",
            message_id=1,
            message_text="hi",
        )
        AgentSession.create_dev(
            session_id=f"qt_dev_{ts}",
            project_key="query_test",
            working_dir="/tmp",
            parent_chat_session_id="parent_x",
            message_text="build",
        )

        chats = list(AgentSession.query.filter(session_type=SESSION_TYPE_CHAT))
        devs = list(AgentSession.query.filter(session_type=SESSION_TYPE_DEV))

        chat_ids = {s.session_id for s in chats}
        dev_ids = {s.session_id for s in devs}

        assert f"qt_chat_{ts}" in chat_ids
        assert f"qt_dev_{ts}" in dev_ids
        assert f"qt_chat_{ts}" not in dev_ids
