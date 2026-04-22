"""Unit tests for child-session message preservation on PM→dev spawn (#1109).

When the PM session spawns a continuation PM carrying a dev session's
result, the continuation PM's `initial_telegram_message.message_text`
MUST preserve the full enriched payload. Previously the result was capped
at 500 characters, which silently truncated task content after the
PROJECT/FROM/SESSION_ID/TASK_SCOPE/SCOPE headers (~500 chars), causing
Dev sessions to receive gutted instructions.

This regression test guards against re-introduction of the 500-char cap
on dev-result previews used in child session payloads.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Bootstrap: ensure repo root is on sys.path
_repo_root = Path(__file__).parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from agent.session_completion import _create_continuation_pm  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_parent() -> MagicMock:
    parent = MagicMock()
    parent.session_id = "pm-parent"
    parent.agent_session_id = "pm-parent-uuid"
    parent.project_key = "valor"
    parent.chat_id = "0"
    parent.continuation_depth = 0
    parent.project_config = None
    return parent


def _make_agent_session() -> MagicMock:
    a = MagicMock()
    a.session_id = "dev-child"
    a.agent_session_id = "dev-child-uuid"
    return a


def _build_long_payload(n: int) -> str:
    """Build a payload that exceeds the old 500-char cap."""
    # ~540 chars of realistic instruction text.
    tail = "x" * (n - 540)
    return (
        "PROJECT: valor\n"
        "FROM: pm-session\n"
        "SESSION_ID: pm-parent\n"
        "TASK_SCOPE: sdlc-1109\n"
        "SCOPE: This session is scoped to the message below. "
        "When reporting completion or summarizing work, only reference "
        "tasks and work initiated in this specific session. Do not include "
        "work, PRs, or requests from other sessions, other senders, or "
        "prior conversation threads.\n"
        "MESSAGE: Build the fix for issue #1109. Both defects must be "
        "addressed. The tests must cover both regressions. Do NOT skip "
        "any step." + tail
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestContinuationPMPreservesFullPayload:
    def test_continuation_pm_receives_result_longer_than_500_chars(self):
        """Continuation PM's message_text must contain a preview >500 chars.

        Before the fix, `result_preview = result[:500]` at session_completion.py
        line 799/826/903 capped the dev result at 500 chars before embedding it
        into the continuation PM's message_text. This broke PM→dev handoffs.
        """
        parent = _make_parent()
        agent_session = _make_agent_session()
        long_result = _build_long_payload(2000)

        # Capture the create() kwargs to inspect message_text.
        captured_kwargs: dict = {}

        def fake_create(**kwargs):
            captured_kwargs.update(kwargs)
            m = MagicMock()
            m.session_id = "continuation-pm"
            return m

        with (
            patch("models.agent_session.AgentSession.create", side_effect=fake_create),
            patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis,
        ):
            mock_redis.set.return_value = True  # acquire dedup lock
            _create_continuation_pm(
                parent=parent,
                agent_session=agent_session,
                issue_number=1109,
                stage="BUILD",
                outcome="success",
                # result_preview is what the caller passes in — represents the
                # already-truncated preview. The fix raises the cap upstream
                # so a 2000-char preview must flow through unchanged.
                result_preview=long_result,
            )

        message_text = captured_kwargs.get("message_text", "")
        # The message must preserve the full enriched payload.
        assert "MESSAGE: Build the fix for issue #1109" in message_text
        # The tail "x" run (which a 500-char cap would have trimmed) must be
        # embedded somewhere in the final message_text — continuation PM
        # appends its own "Resume..." footer after the preview, so we check
        # containment, not tail position.
        assert "xxxxxxxxxx" in message_text
        # The message_text must be longer than the old 500-char ceiling so
        # callers can verify we're no longer pinned to the broken cap.
        assert len(message_text) > 1500

    def test_handle_dev_completion_raises_preview_cap_above_500(self):
        """The preview built by _handle_dev_session_completion must be >500 chars.

        The cap at session_completion.py:799/826/903 must be raised so that long
        dev results are not silently truncated before landing in a child PM's
        message_text.
        """
        # Read the source and assert the caps are raised.
        src = Path(_repo_root, "agent", "session_completion.py").read_text()
        # There must be NO `result[:500]` truncation remaining.
        # The fix replaces [:500] with a much larger constant (or no cap).
        assert "result[:500]" not in src, (
            "session_completion.py still contains result[:500] cap — fix regressed"
        )
