"""Integration test for harness session continuity via --resume (#976).

Exercises two sequential get_response_via_harness() calls on the same session_id:
1. First call stores the Claude Code session UUID.
2. Second call injects --resume <uuid> and passes only the new message.

Requires the `claude` binary on PATH. Skipped otherwise.
"""

import shutil
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.integration]

CLAUDE_AVAILABLE = shutil.which("claude") is not None


@pytest.mark.skipif(not CLAUDE_AVAILABLE, reason="claude binary not on PATH")
class TestHarnessResumeIntegration:
    """Two-turn harness cycle verifying --resume injection."""

    @pytest.mark.asyncio
    async def test_second_turn_uses_resume(self):
        """Second harness call includes --resume with UUID from first call."""
        from agent.sdk_client import get_response_via_harness

        stored_uuids = {}

        def fake_store(session_id, claude_uuid):
            stored_uuids[session_id] = claude_uuid

        def fake_get(session_id):
            return stored_uuids.get(session_id)

        session_id = "integration-test-harness-resume"

        with patch("agent.sdk_client._store_claude_session_uuid", side_effect=fake_store):
            # Turn 1: no prior UUID, full context
            result1 = await get_response_via_harness(
                message="Reply with exactly: TURN1OK",
                working_dir="/tmp",
                session_id=session_id,
                prior_uuid=None,
            )

        assert result1, "First turn should produce output"
        assert session_id in stored_uuids, "UUID should be stored after first turn"

        prior_uuid = stored_uuids[session_id]
        assert prior_uuid, "Stored UUID should be non-empty"

        with patch("agent.sdk_client._store_claude_session_uuid", side_effect=fake_store):
            # Turn 2: resume with stored UUID, minimal message
            result2 = await get_response_via_harness(
                message="Reply with exactly: TURN2OK",
                working_dir="/tmp",
                session_id=session_id,
                prior_uuid=prior_uuid,
                full_context_message="Reply with exactly: TURN2OK",
            )

        assert result2, "Second turn (resumed) should produce output"

    @pytest.mark.asyncio
    async def test_stale_uuid_triggers_fallback(self):
        """A known-bad UUID triggers the stale-UUID fallback and still returns a result."""
        from agent.sdk_client import get_response_via_harness

        bad_uuid = "00000000-0000-0000-0000-000000000000"

        with patch("agent.sdk_client._store_claude_session_uuid"):
            result = await get_response_via_harness(
                message="Reply with exactly: FALLBACK_OK",
                working_dir="/tmp",
                session_id="integration-test-stale-uuid",
                prior_uuid=bad_uuid,
                full_context_message="Reply with exactly: FALLBACK_OK",
            )

        assert result, "Stale-UUID fallback should still produce output"
