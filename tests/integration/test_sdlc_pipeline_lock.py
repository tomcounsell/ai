"""Integration test for SDLC pipeline G7 plan-revising lock (issue #1302).

Simulates the full critique → plan_revising=True → router blocks build →
plan clears lock → router routes to build flow using a real AgentSession
in Redis.

Uses project_key prefix "test-1302-lock-" so test sessions are easy to identify
and clean up. All cleanup goes through the ORM only — never raw Redis.

Markers:
    sdlc  — SDLC pipeline test
    integration — requires Redis
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.sdlc


def _make_session(project_key: str, issue_number: int | None = None):
    """Create a minimal PM AgentSession for testing.

    Returns the session or None if AgentSession is unavailable.
    """
    try:
        from models.agent_session import AgentSession

        session = AgentSession()
        session.session_id = f"test-1302-{uuid.uuid4().hex[:8]}"
        session.session_type = "pm"
        session.project_key = project_key
        session.status = "active"
        if issue_number:
            session.issue_number = issue_number
        session.stage_states = json.dumps(
            {
                "ISSUE": "completed",
                "PLAN": "completed",
                "CRITIQUE": "completed",
                "BUILD": "pending",
                # Use READY TO BUILD (with concerns) so G1 does not fire
                # (G1 only trips on NEEDS REVISION / MAJOR REWORK + critique-just-ran)
                "_verdicts": {"CRITIQUE": {"verdict": "READY TO BUILD (with concerns)"}},
            }
        )
        session.save()
        return session
    except Exception as e:
        pytest.skip(f"Cannot create test session (Redis unavailable?): {e}")


def _cleanup_sessions(project_key_prefix: str):
    """Delete all test sessions matching the project_key prefix via ORM only."""
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.all())
        for s in sessions:
            if getattr(s, "project_key", "").startswith(project_key_prefix):
                try:
                    s.delete()
                except Exception:
                    pass
    except Exception:
        pass


PROJECT_KEY_PREFIX = "test-1302-lock-"


@pytest.fixture(autouse=True)
def cleanup_test_sessions():
    """Auto-cleanup test sessions before and after each test."""
    _cleanup_sessions(PROJECT_KEY_PREFIX)
    yield
    _cleanup_sessions(PROJECT_KEY_PREFIX)


class TestPlanRevisingLockIntegration:
    """Integration tests for G7 using real AgentSession in Redis."""

    def test_critique_sets_lock_blocks_build_plan_clears_lock_routes_build(self):
        """Full flow: critique sets lock → build is blocked → plan clears → build dispatches."""
        from agent.sdlc_router import decide_next_dispatch
        from tools.sdlc_meta_set import write_meta
        from tools.stage_states_helpers import update_stage_states

        project_key = f"{PROJECT_KEY_PREFIX}full-flow-{uuid.uuid4().hex[:6]}"
        session = _make_session(project_key)

        # Step 1: Simulate critique writing the plan_revising lock.
        def set_lock(states: dict) -> dict:
            states["_plan_revising"] = True
            return states

        success = update_stage_states(session, set_lock)
        assert success, "update_stage_states failed to set _plan_revising"

        # Reload session to see the updated stage_states.
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        assert sessions, "Session not found after save"
        reloaded = sessions[0]

        raw = json.loads(reloaded.stage_states or "{}")
        assert raw.get("_plan_revising") is True, "Lock was not set"

        # Step 2: Build the meta dict simulating what sdlc_stage_query returns.
        meta_with_lock = {
            "plan_revising": True,
            "revision_applied": False,
            "pr_number": None,
            # Use READY TO BUILD (with concerns) so G1 does not fire.
            # G1 only fires on NEEDS REVISION / MAJOR REWORK + critique-just-ran.
            # G7 fires on any plan_revising=True + critique-just-ran.
            "latest_critique_verdict": "READY TO BUILD (with concerns)",
            "last_dispatched_skill": "/do-plan-critique",
            "same_stage_dispatch_count": 0,
            "critique_cycle_count": 1,
            "patch_cycle_count": 0,
            "latest_review_verdict": None,
            "pr_merge_state": None,
            "ci_all_passing": None,
            "plan_hash_at_build_start": None,
        }

        # Step 3: Assert router routes to /do-plan (not /do-build) with lock set.
        stage_states = raw
        result = decide_next_dispatch(stage_states, meta_with_lock, {})

        from agent.sdlc_router import Dispatch

        assert isinstance(result, Dispatch), f"Expected Dispatch, got {result!r}"
        assert result.skill == "/do-plan", f"Expected /do-plan with lock set, got {result.skill!r}"
        assert result.row_id == "G7", f"Expected G7, got {result.row_id!r}"

        # Step 4: Simulate plan clearing the lock (write_meta with plan_revising=false).
        with patch("tools.sdlc_meta_set._find_session", return_value=reloaded):
            clear_result = write_meta(key="plan_revising", value="false")

        assert clear_result == {"key": "plan_revising", "value": False}, (
            f"write_meta failed to clear lock: {clear_result!r}"
        )

        # Reload and verify lock is cleared.
        sessions2 = list(AgentSession.query.filter(session_id=session.session_id))
        reloaded2 = sessions2[0]
        raw2 = json.loads(reloaded2.stage_states or "{}")
        assert raw2.get("_plan_revising") is False, "Lock was not cleared"

        # Step 5: With lock cleared, assert router routes to /do-build.
        meta_cleared = {
            **meta_with_lock,
            "plan_revising": False,
            "revision_applied": True,
            "last_dispatched_skill": "/do-plan",
        }

        result2 = decide_next_dispatch(raw2, meta_cleared, {})
        assert isinstance(result2, Dispatch), f"Expected Dispatch after lock clear, got {result2!r}"
        assert result2.skill == "/do-build", (
            f"Expected /do-build after lock cleared, got {result2.skill!r}"
        )

    def test_write_meta_persists_plan_revising(self):
        """write_meta correctly persists plan_revising=True to stage_states."""
        from tools.sdlc_meta_set import write_meta

        project_key = f"{PROJECT_KEY_PREFIX}persist-{uuid.uuid4().hex[:6]}"
        session = _make_session(project_key)

        with patch("tools.sdlc_meta_set._find_session", return_value=session):
            result = write_meta(key="plan_revising", value="true")

        assert result == {"key": "plan_revising", "value": True}

        # Verify persistence by reloading from Redis.
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        reloaded = sessions[0]
        raw = json.loads(reloaded.stage_states or "{}")
        assert raw.get("_plan_revising") is True

    def test_write_meta_persists_plan_hash(self):
        """write_meta correctly persists plan_hash_at_build_start to stage_states."""
        from tools.sdlc_meta_set import write_meta

        project_key = f"{PROJECT_KEY_PREFIX}hash-{uuid.uuid4().hex[:6]}"
        session = _make_session(project_key)
        test_hash = "deadbeef1234567890"

        with patch("tools.sdlc_meta_set._find_session", return_value=session):
            result = write_meta(key="plan_hash_at_build_start", value=test_hash)

        assert result == {"key": "plan_hash_at_build_start", "value": test_hash}

        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session.session_id))
        reloaded = sessions[0]
        raw = json.loads(reloaded.stage_states or "{}")
        assert raw.get("_plan_hash_at_build_start") == test_hash
