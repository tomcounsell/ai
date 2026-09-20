"""Tests for Bridge-AgentSession-SDK connectivity gap fixes (issue #209).

Verifies:
1. task_list_id persistence in _execute_agent_session (Fix 1)
2. complete_transcript field preservation through status changes (Fix 2)
3. start_transcript lookup-and-update instead of dual creation (Fix 3)
4. VALOR_SESSION_ID env var in _find_session resolution chain (Fix 4)
5. Summarizer renders stage progress for sessions with history data (Fix 5)

Tests use real Redis (db=1 via redis_test_db fixture) for integration
validation. Mock-based tests are used only where SDK imports are needed.
"""

import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from models.agent_session import AgentSession
from models.session_event import format_event_lines

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def pending_session(redis_test_db):
    """Create an AgentSession as _push_agent_session would (status=pending with queue fields)."""
    return AgentSession.create(
        session_id="tg_valor_-5051_12345",
        project_key="valor",
        status="pending",
        chat_id="-5051",
        sender_name="Tom",
        sender_id=12345,
        created_at=datetime.now(tz=UTC),
        message_text="SDLC 209",
        priority="high",
    )


@pytest.fixture
def running_session(redis_test_db):
    """Create an AgentSession in running state (after session pickup)."""
    return AgentSession.create(
        session_id="tg_valor_-5051_12345",
        project_key="valor",
        status="running",
        chat_id="-5051",
        sender_name="Tom",
        sender_id=12345,
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        message_text="SDLC 209",
        priority="high",
        task_list_id="thread--5051-12345",
        branch_name="session/test-fix",
        slug="test-slug",
        classification_type="bug",
        classification_confidence=0.95,
        session_events=["[user] SDLC 209", "[stage] ISSUE completed"],
        issue_url="https://github.com/org/repo/issues/209",
        plan_url="https://github.com/org/repo/blob/main/docs/plans/fix.md",
        pr_url="https://github.com/org/repo/pull/210",
        turn_count=5,
        tool_call_count=12,
    )


# ── Fix 1: task_list_id persistence ──────────────────────────────────────────


class TestTaskListIdPersistence:
    """Fix 1: _execute_agent_session persists task_list_id on the AgentSession."""

    def test_task_list_id_persisted_after_save(self, redis_test_db):
        """Simulates the Fix 1 code path: find session, set task_list_id, save."""
        # Create session as _push_agent_session would
        s = AgentSession.create(
            session_id="tg_valor_-5051_99999",
            project_key="valor",
            status="running",
            created_at=datetime.now(tz=UTC),
        )
        assert s.task_list_id is None

        # Simulate _execute_agent_session: set task_list_id and save
        s.task_list_id = "thread--5051-99999"
        s.save()

        # Verify it persists across re-fetch
        found = list(AgentSession.query.filter(session_id="tg_valor_-5051_99999"))
        assert len(found) == 1
        assert found[0].task_list_id == "thread--5051-99999"

    def test_task_list_id_with_slug(self, redis_test_db):
        """Tier 2 sessions use slug as task_list_id."""
        s = AgentSession.create(
            session_id="tg_valor_-5051_88888",
            project_key="valor",
            status="running",
            created_at=datetime.now(tz=UTC),
            slug="bridge-sdk-fix",
        )

        # Simulate _execute_agent_session computing task_list_id from slug
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
        original_slug = running_session.slug
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
        assert s.slug == original_slug
        assert s.message_text == original_message_text
        assert s.completed_at is not None
        assert s.updated_at is not None

    def test_preserves_history_through_status_change(self, running_session):
        """History list survives the delete-and-recreate pattern."""
        from bridge.session_transcript import complete_transcript

        complete_transcript(running_session.session_id, status="completed")

        found = list(AgentSession.query.filter(session_id=running_session.session_id))
        s = found[0]
        history = format_event_lines(s.session_events)
        assert len(history) >= 2
        assert "[user] SDLC 209" in history[0]

    def test_same_status_preserves_without_recreate(self, running_session):
        """When status is unchanged, fields are updated in-place (no delete)."""
        from bridge.session_transcript import complete_transcript

        # Complete with same status (running -> running)
        complete_transcript(running_session.session_id, status="running", summary="Still going")

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
        """start_transcript preserves queue-phase fields from _push_agent_session."""
        from bridge.session_transcript import start_transcript

        start_transcript(
            session_id=pending_session.session_id,
            project_key="valor",
            sender="Tom",
        )

        found = list(AgentSession.query.filter(session_id=pending_session.session_id))
        s = found[0]
        # Queue fields from _push_agent_session should still be present
        assert s.message_text == "SDLC 209"
        assert s.priority == "high"
        assert s.sender_id == 12345


# ── Fix 4: VALOR_SESSION_ID env var ──────────────────────────────────────────


# TestValorSessionIdEnvVar removed — tested _find_session from
# tools/session_progress.py which was deleted (Observer Agent, issue #309).
# Session resolution for stage progress is now handled by the deterministic
# PipelineStateMachine in agent/pipeline_state.py.


# ── Fix 4a: SDK client env var injection ─────────────────────────────────────


class _EnvCapturingRunner:
    """Spy over ``agent.session_runner.SessionRunner`` recording ctor kwargs.

    Patched in at ``agent.session_runner.SessionRunner`` because the executor
    imports the symbol at call time. Recording the kwargs is what lets these
    tests assert on the env dict the executor actually HANDS to the runner,
    rather than on the source text that builds it.
    """

    instances: list["_EnvCapturingRunner"] = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        type(self).instances.append(self)

    async def run(self, user_message: str):
        from agent.session_runner import RunSummary

        return RunSummary(exit_reason="pm_complete", turn_count=1)


@contextmanager
def _dispatch_harness():
    """Patch the runner and stub the git worktree operations the executor does.

    A slugless eng session gets a synthetic slug and then a real worktree
    create + branch verify, neither of which can succeed under test. Mirrors
    ``tests/unit/test_session_executor_runner_dispatch.py::_patch_worktree``.
    """
    _EnvCapturingRunner.instances = []
    wt_path = os.path.join(tempfile.mkdtemp(), ".worktrees", "test-slot")
    os.makedirs(wt_path, exist_ok=True)
    with (
        patch("agent.session_runner.SessionRunner", _EnvCapturingRunner),
        patch("agent.worktree_manager.get_or_create_worktree", return_value=wt_path),
        patch("agent.worktree_manager.verify_worktree_branch", return_value=None),
    ):
        yield
    _EnvCapturingRunner.instances = []


def _dispatch_session(session_id: str) -> AgentSession:
    """A minimal eng AgentSession the executor will dispatch to the runner."""
    session = AgentSession.create(
        session_id=session_id,
        session_type="eng",
        project_key="test",
        working_dir="/tmp",
        status="pending",
        chat_id="999",
        message_text="hello runner",
        sender_name="tester",
        created_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
    )
    # The executor's agent_session lookup filters on status="running"; the
    # worker performs this transition before dispatch.
    session.status = "running"
    session.save(update_fields=["status"])
    return session


async def _captured_session_env(session: AgentSession) -> dict:
    """Run the executor against ``session`` and return the runner's session_env."""
    from agent.session_executor import _execute_agent_session

    with _dispatch_harness():
        await _execute_agent_session(session)
        assert _EnvCapturingRunner.instances, "SessionRunner was never constructed"
        return _EnvCapturingRunner.instances[0].init_kwargs.get("session_env")


class TestSdkClientEnvVar:
    """Fix 4a: VALOR_SESSION_ID is injected into the harness subprocess env.

    The injection site is the ``_harness_env`` dict built in
    agent/session_executor.py (issue #2206) and handed to ``SessionRunner``
    as ``session_env``. It is NOT in agent/sdk_client.py: that module has
    zero occurrences of ``VALOR_SESSION_ID``, which is the load-bearing fact
    here. (An earlier revision of this docstring called sdk_client.py "a thin
    re-export shim"; it is 1531 lines. The characterization was wrong even
    though the zero-occurrences fact it rested on is right.) See
    agent/hooks/session_resolver.py's module docstring, which documents
    session_executor.py as the injection site consumers rely on.

    Both tests below assert on the env dict the executor HANDS to the runner.
    They previously grepped agent/session_executor.py for the literal
    ``'"VALOR_SESSION_ID": session.session_id or ""'`` -- the same
    source-grep failure mode that broke these two nodes in the first place,
    only aimed at a newer module. A ``ruff format`` reflow, a dict-key
    reorder, or renaming the local ``session`` would have re-broken them with
    behavior untouched, and conversely they passed whether or not
    ``_harness_env`` ever reached the subprocess. Capturing the constructor
    kwarg removes both halves of that.
    """

    @pytest.mark.asyncio
    async def test_valor_session_id_in_source(self, redis_test_db):
        """The env handed to the runner carries VALOR_SESSION_ID = session_id.

        Pins it to the human-shaped ``session_id`` (tg_valor_..., sdlc-local-...)
        and NOT the per-run Popoto AutoKey ``agent_session_id``, which travels
        separately as AGENT_SESSION_ID. Conflating the two is the wrong-identifier
        namespace bug that silently resolves no session downstream.
        """
        session = _dispatch_session("tg_valor_connectivity_4a")

        env = await _captured_session_env(session)

        assert isinstance(env, dict)
        assert env["VALOR_SESSION_ID"] == "tg_valor_connectivity_4a"
        assert env["VALOR_SESSION_ID"] == session.session_id
        assert env["VALOR_SESSION_ID"] != env.get("AGENT_SESSION_ID")

    @pytest.mark.asyncio
    async def test_valor_session_id_conditional_on_session_id(self, redis_test_db):
        """A falsy session_id yields "" -- never a bare None, never absent.

        Downstream consumers read this with a truthy check (e.g.
        ``os.environ.get("VALOR_SESSION_ID")`` in
        agent/hooks/session_resolver.py::resolve_inflight_session), and the env
        dict is passed to a subprocess, where a ``None`` value is not a falsy
        string but a TypeError. So the key must be present and hold ``""``.
        """
        session = _dispatch_session("tg_valor_connectivity_4a_falsy")
        # session_id is a plain Field (models/agent_session.py:164), not a
        # KeyField, so the in-memory value can be emptied to exercise the
        # falsy branch without fighting KeyMutationError.
        session.session_id = ""

        env = await _captured_session_env(session)

        assert "VALOR_SESSION_ID" in env, "key must be present, not omitted"
        assert env["VALOR_SESSION_ID"] == ""
        assert isinstance(env["VALOR_SESSION_ID"], str), "None would break the subprocess env"


# ── Fix 5: Full chain integration ────────────────────────────────────────────


class TestFullChainIntegration:
    """Integration: hook fires -> session resolved -> stage written -> summarizer renders."""

    def test_hook_to_summarizer_chain(self, redis_test_db):
        """Full chain: session renders summary text via _compose_structured_draft.

        Stage progress lines and link footers were removed in #488 (SDLC stage
        consolidation). The structured summary now renders emoji + summary text.
        """
        from bridge.message_drafter import _compose_structured_draft

        # 1. Create session with stage history (as hooks would write)
        s = AgentSession.create(
            session_id="chain-test-1",
            project_key="test",
            status="completed",
            chat_id="-5051",
            sender_name="Tom",
            created_at=datetime.now(tz=UTC),
            started_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
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
        result = _compose_structured_draft(
            "Fixed connectivity gaps",
            session=s,
            is_completion=True,
        )

        # 3. Verify summary text is rendered (stage progress and link footer
        #    no longer rendered — removed in #488)
        assert "Fixed connectivity gaps" in result

    # test_task_list_id_enables_hook_lookup and
    # test_valor_session_id_enables_hook_lookup removed —
    # tested _find_session from tools/session_progress.py which was deleted
    # (Observer Agent, issue #309).
