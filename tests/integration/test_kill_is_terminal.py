"""Integration tests for the kill-is-terminal invariant (#1208).

Direct function invocation strategy (Plan Implementation Note Q4): the
guards live at well-defined function boundaries —
``_agent_session_hierarchy_health_check`` iteration,
``_deliver_pipeline_completion`` entry, and ``finalize_session`` body — so
calling those functions directly with crafted AgentSession state covers
the regression contract deterministically. No worker-loop simulation, no
launchd respawn dance, no real 5-minute scheduler ticks.

The live verification target — the haunted parent
``tg_valor_-1003449100931_754`` (PM: Valor, agent_session_id
``04a1b7ba207449a98169171c5e44513a``) — is exercised by the validator step
in the build plan, not here. This file is the deterministic regression
contract that runs in CI.
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.agent_session import AgentSession
from models.session_lifecycle import StatusConflictError, finalize_session


@pytest.fixture
def killed_parent(redis_test_db):
    """Create a real AgentSession in killed state.

    Uses the redis_test_db fixture so the session lives in a per-worker
    isolated Redis database — no risk of touching production data.
    """
    parent = AgentSession.create(
        session_id="kit-killed-parent",
        agent_session_id="agt_kit_killed_parent",
        project_key="kit-test",
        status="killed",
        session_type="pm",
        created_at=datetime.now(tz=UTC),
    )
    yield parent


@pytest.fixture
def waiting_parent(redis_test_db):
    """Create a parent in waiting_for_children — the index-stale victim shape."""
    parent = AgentSession.create(
        session_id="kit-waiting-parent",
        agent_session_id="agt_kit_waiting_parent",
        project_key="kit-test",
        status="waiting_for_children",
        session_type="pm",
        created_at=datetime.now(tz=UTC),
    )
    yield parent


# ===================================================================
# Direct lifecycle: finalize_session reject-from-terminal contract
# ===================================================================


class TestFinalizeSessionRejectFromTerminal:
    """End-to-end through real Popoto storage (not mocks)."""

    def test_killed_to_completed_raises_status_conflict(self, killed_parent):
        """The kill-is-terminal guard fires against a real Redis-backed session."""
        with pytest.raises(StatusConflictError) as exc_info:
            finalize_session(killed_parent, "completed", reason="should be blocked")

        assert "reject_from_terminal=False" in str(exc_info.value)

        # Re-read from Redis to confirm no mutation persisted.
        fresh = list(AgentSession.query.filter(session_id="kit-killed-parent"))[0]
        assert fresh.status == "killed"

    def test_killed_to_completed_with_opt_out_succeeds(self, killed_parent):
        """Explicit opt-out lets a legitimate re-classification through."""
        finalize_session(
            killed_parent,
            "completed",
            reason="opt-out path",
            reject_from_terminal=False,
        )

        fresh = list(AgentSession.query.filter(session_id="kit-killed-parent"))[0]
        assert fresh.status == "completed"


# ===================================================================
# Hierarchy health-check: stale waiting_for_children index entry
# ===================================================================


class TestHierarchyHealthCheckSkipsTerminalParent:
    """Fix B contract: re-read guard at the iteration site."""

    def test_killed_parent_skipped_when_index_is_stale(self, killed_parent, caplog):
        """Simulate a stale index entry: parent's hash status is killed but the
        parent shows up in a query.filter(status="waiting_for_children") result.

        This mimics the bug observed in #1208 where the index entry for
        waiting_for_children was not srem'd at kill time. The Fix B re-read
        guard re-reads the authoritative hash status, sees terminal, and skips.
        """
        from agent.session_health import _agent_session_hierarchy_health_check

        # Patch the query to inject our killed parent into the
        # waiting_for_children candidate list, simulating a stale index entry.
        original_filter = AgentSession.query.filter

        def staged_filter(*args, **kwargs):
            if kwargs.get("status") == "waiting_for_children":
                # The killed parent appears here even though its hash status is
                # now killed — this is the bug condition Fix B defends against.
                return [killed_parent]
            return original_filter(*args, **kwargs)

        with (
            patch.object(AgentSession.query, "filter", side_effect=staged_filter),
            caplog.at_level("INFO"),
        ):
            asyncio.run(_agent_session_hierarchy_health_check())

        # The skip message MUST appear in logs.
        skipped = [
            r
            for r in caplog.records
            if "Skipping terminal parent" in r.message
            and killed_parent.agent_session_id in r.message
        ]
        assert skipped, (
            f"Expected 'Skipping terminal parent {killed_parent.agent_session_id}' "
            f"in INFO logs. Got: {[r.message for r in caplog.records]}"
        )

        # The parent's hash status MUST remain killed.
        fresh = list(AgentSession.query.filter(session_id="kit-killed-parent"))[0]
        assert fresh.status == "killed"


# ===================================================================
# Runner-entry guard: schedule_pipeline_completion + _deliver_pipeline_completion
# ===================================================================


class TestRunnerEntryGuard:
    """Fix C contract: terminal-status guard at runner entry."""

    def test_schedule_pipeline_completion_skips_killed_parent(self, killed_parent, caplog):
        """schedule_pipeline_completion bails on a killed parent without scheduling."""
        from agent.session_completion import schedule_pipeline_completion

        send_cb = AsyncMock()

        with caplog.at_level("INFO"):
            result = schedule_pipeline_completion(
                killed_parent,
                "fake summary context",
                send_cb,
                chat_id="kit-chat",
                telegram_message_id=1,
            )

        # No task scheduled, no send.
        assert result is None
        send_cb.assert_not_called()

        # The skip message MUST appear in logs.
        skipped = [
            r for r in caplog.records if "Skipping schedule" in r.message and "killed" in r.message
        ]
        assert skipped, (
            "Expected '[completion-runner] Skipping schedule for ... — parent terminal' "
            f"in INFO logs. Got: {[r.message for r in caplog.records]}"
        )

    def test_deliver_pipeline_completion_skips_killed_parent(self, killed_parent, caplog):
        """_deliver_pipeline_completion bails before any drafting or queuing."""
        from agent.session_completion import _deliver_pipeline_completion

        send_cb = AsyncMock()

        with caplog.at_level("INFO"):
            asyncio.run(
                _deliver_pipeline_completion(
                    killed_parent,
                    "fake summary context",
                    send_cb,
                    chat_id="kit-chat",
                    telegram_message_id=1,
                )
            )

        send_cb.assert_not_called()

        skipped = [
            r
            for r in caplog.records
            if "Skipping pipeline completion" in r.message and "killed" in r.message
        ]
        assert skipped, (
            "Expected '[completion-runner] Skipping pipeline completion for ... — "
            "parent terminal' in INFO logs."
        )

    def test_completed_parent_passes_through_runner_guard(self, redis_test_db):
        """A 'completed' parent is NOT short-circuited (Risk 3 mitigation).

        The guard's exception list MUST allow ``completed`` so a legitimate
        success-path runner can deliver its final summary; idempotency at
        ``finalize_session`` handles re-finalize.
        """
        from agent.session_completion import schedule_pipeline_completion

        completed_parent = AgentSession.create(
            session_id="kit-completed-parent",
            agent_session_id="agt_kit_completed_parent",
            project_key="kit-test",
            status="completed",
            session_type="pm",
            created_at=datetime.now(tz=UTC),
        )

        send_cb = AsyncMock()

        with patch("agent.session_completion.asyncio.create_task") as mock_create:
            fake_task = MagicMock(done=lambda: False)
            mock_create.return_value = fake_task
            result = schedule_pipeline_completion(
                completed_parent,
                "fake summary context",
                send_cb,
                chat_id="kit-chat",
                telegram_message_id=1,
            )

        # The task must have been created — guard MUST NOT block 'completed'.
        mock_create.assert_called_once()
        assert result is fake_task
