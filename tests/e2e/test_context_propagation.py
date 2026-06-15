"""E2E tests for context propagation across session types.

Verifies that thread context, task lists, worktree paths, and parent
linkage flow correctly from Eng session → child Eng session → Redis.
Uses real Redis via conftest redis_test_db.
"""

import json
import time

import pytest

from models.agent_session import (
    SESSION_TYPE_ENG,
    AgentSession,
)


@pytest.mark.e2e
class TestEngContextFields:
    """Verify all context fields persist through create → save → reload."""

    def test_all_context_fields_roundtrip(self):
        ts = int(time.time())
        session = AgentSession.create_eng(
            session_id=f"ctx_{ts}",
            project_key="valor",
            working_dir="/Users/test/src/ai",
            chat_id="ctx_chat_123",
            telegram_message_id=42,
            message_text="build the feature",
            sender_name="Valor",
            sender_id=111222,
            chat_title="Eng: Valor",
        )
        # Set additional context fields
        session.task_list_id = "thread-ctx_chat_123-42"
        session.slug = "my-feature"
        session.branch_name = "session/my-feature"
        session.correlation_id = f"corr_{ts}"
        session.save()

        # Reload from Redis
        reloaded = list(AgentSession.query.filter(session_id=session.session_id))[0]

        assert reloaded.session_id == f"ctx_{ts}"
        assert reloaded.session_type == SESSION_TYPE_ENG
        assert reloaded.project_key == "valor"
        assert reloaded.working_dir == "/Users/test/src/ai"
        assert reloaded.chat_id == "ctx_chat_123"
        assert reloaded.telegram_message_id == 42
        assert reloaded.message_text == "build the feature"
        assert reloaded.sender_name == "Valor"
        assert reloaded.sender_id == 111222
        assert reloaded.chat_title == "Eng: Valor"
        assert reloaded.task_list_id == "thread-ctx_chat_123-42"
        assert reloaded.slug == "my-feature"
        assert reloaded.branch_name == "session/my-feature"
        assert reloaded.correlation_id == f"corr_{ts}"


@pytest.mark.e2e
class TestChildParentLinkage:
    """Verify a child Eng session correctly links to its parent Eng session."""

    def test_child_session_has_parent_reference(self):
        ts = int(time.time())
        parent = AgentSession.create_eng(
            session_id=f"parent_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="link_chat",
            telegram_message_id=1,
            message_text="do work",
        )

        child = AgentSession.create_child(
            session_id=f"child_{ts}",
            project_key="valor",
            working_dir="/tmp/test/.worktrees/my-feature",
            parent_agent_session_id=parent.agent_session_id,
            message_text="/do-build",
            slug="my-feature",
        )

        assert child.session_type == SESSION_TYPE_ENG
        assert child.parent_agent_session_id == parent.agent_session_id

        # Navigate from child to parent via filter
        parents = list(AgentSession.query.filter(session_id=parent.session_id))
        assert len(parents) == 1
        assert parents[0].agent_session_id == parent.agent_session_id
        assert parents[0].session_type == SESSION_TYPE_ENG

    def test_parent_session_finds_its_child_sessions(self):
        ts = int(time.time())
        parent = AgentSession.create_eng(
            session_id=f"multi_parent_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="multi_chat",
            telegram_message_id=5,
            message_text="complex task",
        )

        child1 = AgentSession.create_child(
            session_id=f"dev1_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            parent_agent_session_id=parent.agent_session_id,
            message_text="/do-build",
        )
        child2 = AgentSession.create_child(
            session_id=f"dev2_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            parent_agent_session_id=parent.agent_session_id,
            message_text="/do-test",
        )

        children = parent.get_child_sessions()
        child_ids = {c.agent_session_id for c in children}
        assert child1.agent_session_id in child_ids
        assert child2.agent_session_id in child_ids

    def test_orphan_child_session_returns_none_parent(self):
        ts = int(time.time())
        child = AgentSession.create_child(
            session_id=f"orphan_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            parent_agent_session_id="nonexistent_id",
            message_text="lost",
        )
        assert child.get_parent_session() is None


@pytest.mark.e2e
class TestDerivedPaths:
    """Verify slug-derived branch names and plan paths."""

    def test_slug_derives_branch_name(self):
        ts = int(time.time())
        child = AgentSession.create_child(
            session_id=f"slug_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            parent_agent_session_id="parent_x",
            message_text="build",
            slug="my-cool-feature",
        )
        assert child.derived_branch_name == "session/my-cool-feature"
        assert child.plan_path == "docs/plans/my-cool-feature.md"

    def test_no_slug_falls_back_to_branch_name(self):
        ts = int(time.time())
        session = AgentSession.create_eng(
            session_id=f"nosluq_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="fb_chat",
            telegram_message_id=1,
            message_text="quick question",
        )
        session.branch_name = "feature/manual-branch"
        session.save()

        reloaded = list(AgentSession.query.filter(session_id=session.session_id))[0]
        assert reloaded.derived_branch_name == "feature/manual-branch"
        assert reloaded.plan_path is None


@pytest.mark.e2e
class TestSDLCStagesPropagation:
    """Verify SDLC stages dict flows correctly on Eng sessions."""

    def test_stage_states_persist_as_json(self):
        ts = int(time.time())
        stages = {"PLAN": "completed", "BUILD": "in_progress", "TEST": "pending"}
        child = AgentSession.create_child(
            session_id=f"stages_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            parent_agent_session_id="parent_y",
            message_text="/do-build",
            stage_states=stages,
        )

        reloaded = list(AgentSession.query.filter(session_id=child.session_id))[0]
        parsed = json.loads(reloaded.stage_states)
        assert parsed["PLAN"] == "completed"
        assert parsed["BUILD"] == "in_progress"
        assert parsed["TEST"] == "pending"
        assert reloaded.is_sdlc is True
        assert reloaded.current_stage == "BUILD"

    def test_eng_session_without_stage_states(self):
        ts = int(time.time())
        session = AgentSession.create_eng(
            session_id=f"nosdlc_{ts}",
            project_key="valor",
            working_dir="/tmp/test",
            chat_id="chat_no_sdlc",
            telegram_message_id=1,
            message_text="what time is it?",
        )
        assert session.is_sdlc is False
        assert session.current_stage is None


@pytest.mark.e2e
class TestSessionTypeDiscriminator:
    """Verify session_type field correctly discriminates session kinds."""

    def test_factory_methods_set_correct_types(self):
        ts = int(time.time())

        parent = AgentSession.create_eng(
            session_id=f"type_eng_{ts}",
            project_key="valor",
            working_dir="/tmp",
            chat_id="tc",
            telegram_message_id=1,
            message_text="hi",
        )
        child = AgentSession.create_child(
            session_id=f"type_child_{ts}",
            project_key="valor",
            working_dir="/tmp",
            parent_agent_session_id=parent.agent_session_id,
            message_text="build",
        )

        assert parent.is_eng and not parent.is_teammate
        assert child.is_eng and not child.is_teammate

    def test_session_type_queryable(self):
        ts = int(time.time())
        AgentSession.create_eng(
            session_id=f"qt_eng_{ts}",
            project_key="query_test",
            working_dir="/tmp",
            chat_id="qt",
            telegram_message_id=1,
            message_text="hi",
        )
        AgentSession.create_child(
            session_id=f"qt_child_{ts}",
            project_key="query_test",
            working_dir="/tmp",
            parent_agent_session_id="parent_x",
            message_text="build",
        )

        eng_sessions = list(AgentSession.query.filter(session_type=SESSION_TYPE_ENG))

        eng_ids = {s.session_id for s in eng_sessions}

        assert f"qt_eng_{ts}" in eng_ids
        assert f"qt_child_{ts}" in eng_ids
