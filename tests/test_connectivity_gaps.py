"""Tests for Bridge-AgentSession-SDK connectivity gap fixes (issue #209).

Verifies:
1. task_list_id persistence in _execute_job (Fix 1)
2. complete_transcript field preservation through status changes (Fix 2)
3. start_transcript lookup-and-update instead of dual creation (Fix 3)
4. VALOR_SESSION_ID env var in _find_session resolution chain (Fix 4)
5. Summarizer renders stage progress for sessions with history data (Fix 5)

Tests use real Redis (db=1 via redis_test_db fixture) for integration
validation. Mock-based tests are used only where SDK imports are needed.
"""

import os
import time
from unittest.mock import patch

import pytest

from models.agent_session import AgentSession

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def pending_session(redis_test_db):
    """Create an AgentSession as _push_job would (status=pending with queue fields)."""
    return AgentSession.create(
        session_id="tg_valor_-5051_12345",
        project_key="valor",
        status="pending",
        chat_id="-5051",
        sender_name="Tom",
        sender_id=12345,
        created_at=time.time(),
        message_text="SDLC 209",
        priority="high",
    )


@pytest.fixture
def running_session(redis_test_db):
    """Create an AgentSession in running state (after job pickup)."""
    return AgentSession.create(
        session_id="tg_valor_-5051_12345",
        project_key="valor",
        status="running",
        chat_id="-5051",
        sender_name="Tom",
        sender_id=12345,
        created_at=time.time(),
        started_at=time.time(),
        message_text="SDLC 209",
        priority="high",
        task_list_id="thread--5051-12345",
        branch_name="session/test-fix",
        work_item_slug="test-slug",
        classification_type="bug",
        classification_confidence=0.95,
        history=["[user] SDLC 209", "[stage] ISSUE completed"],
        issue_url="https://github.com/org/repo/issues/209",
        plan_url="https://github.com/org/repo/blob/main/docs/plans/fix.md",
        pr_url="https://github.com/org/repo/pull/210",
        turn_count=5,
        tool_call_count=12,
    )


# ── Fix 1: task_list_id persistence ──────────────────────────────────────────


class TestTaskListIdPersistence:
    """Fix 1: _execute_job persists task_list_id on the AgentSession."""

    def test_task_list_id_persisted_after_save(self, redis_test_db):
        """Simulates the Fix 1 code path: find session, set task_list_id, save."""
        # Create session as _push_job would
        s = AgentSession.create(
            session_id="tg_valor_-5051_99999",
            project_key="valor",
            status="running",
            created_at=time.time(),
        )
        assert s.task_list_id is None

        # Simulate _execute_job: set task_list_id and save
        s.task_list_id = "thread--5051-99999"
        s.save()

        # Verify it persists across re-fetch
        found = list(AgentSession.query.filter(session_id="tg_valor_-5051_99999"))
        assert len(found) == 1
        assert found[0].task_list_id == "thread--5051-99999"

    def test_task_list_id_with_work_item_slug(self, redis_test_db):
        """Tier 2 sessions use work_item_slug as task_list_id."""
        s = AgentSession.create(
            session_id="tg_valor_-5051_88888",
            project_key="valor",
            status="running",
            created_at=time.time(),
            work_item_slug="bridge-sdk-fix",
        )

        # Simulate _execute_job computing task_list_id from slug
        s.task_list_id = "bridge-sdk-fix"
        s.save()

        found = list(AgentSession.query.filter(session_id="tg_valor_-5051_88888"))
        assert found[0].task_list_id == "bridge-sdk-fix"


# ── Fix 2: complete_transcript field preservation ────────────────────────────


class TestCompleteTranscriptFieldPreservation:
    """Fix 2: complete_transcript preserves ALL fields through status change."""

    def test_preserves_all_fields_through_status_change(self, running_session):
        """Status transition (running -> completed) preserves every field."""
        from bridge.session_transcript import complete_transcript

        # Record values before transition
        original_session_id = running_session.session_id
        original_task_list_id = running_session.task_list_id
        original_sender_name = running_session.sender_name
        original_branch_name = running_session.branch_name
        original_issue_url = running_session.issue_url
        original_plan_url = running_session.plan_url
        original_pr_url = running_session.pr_url
        original_classification_type = running_session.classification_type
        original_work_item_slug = running_session.work_item_slug
        original_message_text = running_session.message_text

        # Perform status transition
        complete_transcript(original_session_id, status="completed", summary="All done")

        # Find the new session (recreated with status=completed)
        found = list(AgentSession.query.filter(session_id=original_session_id))
        assert len(found) == 1
        s = found[0]

        # Verify status changed
        assert s.status == "completed"
        assert s.summary == "All done"

        # Verify ALL fields preserved (the core of Fix 2)
        assert s.task_list_id == original_task_list_id
        assert s.sender_name == original_sender_name
        assert s.branch_name == original_branch_name
        assert s.issue_url == original_issue_url
        assert s.plan_url == original_plan_url
        assert s.pr_url == original_pr_url
        assert s.classification_type == original_classification_type
        assert s.work_item_slug == original_work_item_slug
        assert s.message_text == original_message_text
        assert s.completed_at is not None
        assert s.last_activity is not None

    def test_preserves_history_through_status_change(self, running_session):
        """History list survives the delete-and-recreate pattern."""
        from bridge.session_transcript import complete_transcript

        complete_transcript(running_session.session_id, status="completed")

        found = list(AgentSession.query.filter(session_id=running_session.session_id))
        s = found[0]
        history = s._get_history_list()
        assert len(history) >= 2
        assert "[user] SDLC 209" in history[0]

    def test_same_status_preserves_without_recreate(self, running_session):
        """When status is unchanged, fields are updated in-place (no delete)."""
        from bridge.session_transcript import complete_transcript

        # Complete with same status (running -> running)
        complete_transcript(
            running_session.session_id, status="running", summary="Still going"
        )

        found = list(AgentSession.query.filter(session_id=running_session.session_id))
        assert len(found) == 1
        assert found[0].summary == "Still going"
        assert found[0].task_list_id == running_session.task_list_id

    def test_preserves_numeric_fields(self, running_session):
        """Numeric fields (turn_count, tool_call_count) survive transition."""
        from bridge.session_transcript import complete_transcript

        complete_transcript(running_session.session_id, status="completed")

        found = list(AgentSession.query.filter(session_id=running_session.session_id))
        s = found[0]
        assert s.turn_count == 5
        assert s.tool_call_count == 12
        assert s.sender_id == 12345


# ── Fix 3: Eliminate dual session creation ───────────────────────────────────


class TestNoDualSessionCreation:
    """Fix 3: start_transcript updates existing session instead of creating duplicate."""

    def test_updates_existing_session(self, pending_session):
        """start_transcript updates the existing session instead of creating a new one."""
        from bridge.session_transcript import start_transcript

        log_path = start_transcript(
            session_id=pending_session.session_id,
            project_key="valor",
            chat_id="-5051",
            sender="Tom",
            branch_name="session/test-fix",
            classification_type="bug",
        )

        # Should only have ONE session with this session_id
        found = list(AgentSession.query.filter(session_id=pending_session.session_id))
        assert len(found) == 1, f"Expected 1 session, found {len(found)}"

        # The single session should have updated fields
        s = found[0]
        assert s.log_path == log_path
        assert s.branch_name == "session/test-fix"
        assert s.classification_type == "bug"

    def test_creates_session_when_none_exists(self, redis_test_db):
        """start_transcript creates a session if none exists (standalone case)."""
        from bridge.session_transcript import start_transcript

        log_path = start_transcript(
            session_id="standalone-session-1",
            project_key="test",
            sender="Alice",
        )

        found = list(AgentSession.query.filter(session_id="standalone-session-1"))
        assert len(found) == 1
        assert found[0].sender_name == "Alice"
        assert found[0].log_path == log_path

    def test_preserves_queue_fields_on_update(self, pending_session):
        """start_transcript preserves queue-phase fields from _push_job."""
        from bridge.session_transcript import start_transcript

        start_transcript(
            session_id=pending_session.session_id,
            project_key="valor",
            sender="Tom",
        )

        found = list(AgentSession.query.filter(session_id=pending_session.session_id))
        s = found[0]
        # Queue fields from _push_job should still be present
        assert s.message_text == "SDLC 209"
        assert s.priority == "high"
        assert s.sender_id == 12345


# ── Fix 4: VALOR_SESSION_ID env var ──────────────────────────────────────────


class TestValorSessionIdEnvVar:
    """Fix 4: _find_session uses VALOR_SESSION_ID env var for session resolution."""

    def test_finds_session_via_env_var(self, running_session):
        """_find_session resolves via VALOR_SESSION_ID env var (priority 1)."""
        from tools.session_progress import _find_session

        # Set VALOR_SESSION_ID to the bridge session_id
        with patch.dict(os.environ, {"VALOR_SESSION_ID": running_session.session_id}):
            # Pass a UUID that doesn't match any session_id
            result = _find_session("claude-code-uuid-that-doesnt-match")

        assert result is not None
        assert result.session_id == running_session.session_id

    def test_falls_back_to_session_id_when_env_not_set(self, running_session):
        """_find_session falls back to direct session_id match (priority 2)."""
        from tools.session_progress import _find_session

        # Ensure VALOR_SESSION_ID is NOT set
        env = os.environ.copy()
        env.pop("VALOR_SESSION_ID", None)
        with patch.dict(os.environ, env, clear=True):
            result = _find_session(running_session.session_id)

        assert result is not None
        assert result.session_id == running_session.session_id

    def test_falls_back_to_task_list_id(self, running_session):
        """_find_session falls back to task_list_id match (priority 3)."""
        from tools.session_progress import _find_session

        env = os.environ.copy()
        env.pop("VALOR_SESSION_ID", None)
        with patch.dict(os.environ, env, clear=True):
            # Pass the task_list_id as session_id
            result = _find_session(running_session.task_list_id)

        assert result is not None
        assert result.task_list_id == running_session.task_list_id

    def test_env_var_takes_priority_over_session_id(self, redis_test_db):
        """VALOR_SESSION_ID env var takes priority over direct session_id match."""
        from tools.session_progress import _find_session

        # Create two sessions
        AgentSession.create(
            session_id="bridge-session-target",
            project_key="test",
            status="running",
            created_at=time.time(),
        )
        AgentSession.create(
            session_id="claude-code-uuid",
            project_key="test",
            status="running",
            created_at=time.time(),
        )

        with patch.dict(os.environ, {"VALOR_SESSION_ID": "bridge-session-target"}):
            result = _find_session("claude-code-uuid")

        # Should find the target (via env var), not the decoy (via session_id)
        assert result.session_id == "bridge-session-target"

    def test_returns_none_when_nothing_matches(self, redis_test_db):
        """_find_session returns None when no resolution path works."""
        from tools.session_progress import _find_session

        env = os.environ.copy()
        env.pop("VALOR_SESSION_ID", None)
        with patch.dict(os.environ, env, clear=True):
            result = _find_session("nonexistent-id")

        assert result is None


# ── Fix 4a: SDK client env var injection ─────────────────────────────────────


class TestSdkClientEnvVar:
    """Fix 4a: Verify VALOR_SESSION_ID is present in sdk_client.py source code."""

    def test_valor_session_id_in_source(self):
        """sdk_client.py contains the VALOR_SESSION_ID env var injection code."""
        from pathlib import Path

        sdk_client_path = Path(__file__).parent.parent / "agent" / "sdk_client.py"
        source = sdk_client_path.read_text()

        # Verify the env var injection code exists
        assert 'env["VALOR_SESSION_ID"] = session_id' in source
        assert "VALOR_SESSION_ID" in source

    def test_valor_session_id_conditional_on_session_id(self):
        """VALOR_SESSION_ID is only set when session_id is provided (not None)."""
        from pathlib import Path

        sdk_client_path = Path(__file__).parent.parent / "agent" / "sdk_client.py"
        source = sdk_client_path.read_text()

        # The env var should be inside an `if session_id:` block
        assert "if session_id:" in source
        # Find the line with VALOR_SESSION_ID and verify it's after the condition
        lines = source.split("\n")
        found_condition = False
        found_env_var = False
        for line in lines:
            if "if session_id:" in line and "VALOR" not in line:
                found_condition = True
            if found_condition and "VALOR_SESSION_ID" in line:
                found_env_var = True
                break
        assert (
            found_env_var
        ), "VALOR_SESSION_ID should be set inside if session_id: block"


# ── Fix 5: Full chain integration ────────────────────────────────────────────


class TestFullChainIntegration:
    """Integration: hook fires -> session resolved -> stage written -> summarizer renders."""

    def test_hook_to_summarizer_chain(self, redis_test_db):
        """Full chain: session with stage data renders correctly in summarizer."""
        from bridge.summarizer import _compose_structured_summary

        # 1. Create session with stage history (as hooks would write)
        s = AgentSession.create(
            session_id="chain-test-1",
            project_key="test",
            status="completed",
            chat_id="-5051",
            sender_name="Tom",
            created_at=time.time(),
            started_at=time.time(),
            last_activity=time.time(),
            message_text="SDLC 209",
            task_list_id="thread--5051-chain",
        )
        s.append_history("user", "SDLC 209")
        s.append_history("stage", "ISSUE completed \u2611")
        s.append_history("stage", "PLAN completed \u2611")
        s.append_history("stage", "BUILD completed \u2611")
        s.append_history("stage", "TEST completed \u2611")
        s.append_history("stage", "REVIEW completed \u2611")
        s.append_history("stage", "DOCS completed \u2611")
        s.set_link("issue", "https://github.com/tomcounsell/ai/issues/209")
        s.set_link("pr", "https://github.com/tomcounsell/ai/pull/210")

        # 2. Compose structured summary
        result = _compose_structured_summary(
            "Fixed connectivity gaps",
            session=s,
            is_completion=True,
        )

        # 3. Verify all components rendered
        assert "209" in result
        assert "\u2611 ISSUE" in result
        assert "\u2611 DOCS" in result
        assert "Issue #209" in result
        assert "PR #210" in result
        assert "Fixed connectivity gaps" in result

    def test_task_list_id_enables_hook_lookup(self, redis_test_db):
        """task_list_id on session allows hooks to resolve via fallback path."""
        from tools.session_progress import _find_session

        # Session created with task_list_id (as Fix 1 ensures)
        AgentSession.create(
            session_id="tg_valor_-5051_hook_test",
            project_key="valor",
            status="running",
            created_at=time.time(),
            task_list_id="thread--5051-hook_test",
        )

        # Hook has Claude Code UUID, not the bridge session_id
        env = os.environ.copy()
        env.pop("VALOR_SESSION_ID", None)
        with patch.dict(os.environ, env, clear=True):
            result = _find_session("thread--5051-hook_test")

        assert result is not None
        assert result.session_id == "tg_valor_-5051_hook_test"

    def test_valor_session_id_enables_hook_lookup(self, redis_test_db):
        """VALOR_SESSION_ID env var allows hooks to resolve with Claude Code UUID."""
        from tools.session_progress import _find_session

        AgentSession.create(
            session_id="tg_valor_-5051_env_test",
            project_key="valor",
            status="running",
            created_at=time.time(),
        )

        # Hook receives Claude Code's internal UUID as session_id
        # but VALOR_SESSION_ID env var points to the bridge session_id
        with patch.dict(os.environ, {"VALOR_SESSION_ID": "tg_valor_-5051_env_test"}):
            result = _find_session("some-claude-code-uuid-abcdef")

        assert result is not None
        assert result.session_id == "tg_valor_-5051_env_test"
