"""Path B simulation unit test for chat_message_log → drafter integration (issue #1192).

Simulates a turn where the relay has already written a Path B outbound entry to
session.chat_message_log (representing a mid-session 'valor-telegram send'), then
calls _build_draft_prompt directly and asserts the prompt contains the prior message.

This is a unit test — it does NOT exercise live Telegram, Redis, or agent sessions.
It exercises the drafter read path against a pre-populated in-memory session object.
"""

from unittest.mock import MagicMock

from bridge.message_drafter import _build_draft_prompt


def _make_session_with_chat_log(chat_log):
    """Return a minimal mock session with the given chat_message_log."""
    session = MagicMock()
    session.message_text = "What is the deployment status?"
    session.classification_type = None
    session.branch_name = None
    session.slug = None
    session.session_type = None
    session.get_links = MagicMock(return_value={})
    session._get_history_list = MagicMock(return_value=[])
    session.chat_message_log = chat_log
    return session


class TestPathBSimulation:
    """Core acceptance test: Path B outbound entry appears in drafter prompt.

    The relay writes an outbound entry to chat_message_log after a successful
    'valor-telegram send' from inside an agent session. The drafter must include
    that prior outbound content so it doesn't produce a duplicate message.
    """

    def test_path_b_prior_outbound_appears_in_drafter_prompt(self):
        """Drafter prompt includes a prior Path B outbound message verbatim.

        Simulates: agent ran 'valor-telegram send "Working on it."' mid-session.
        The relay recorded: {direction='out', sender='valor', content='Working on it.'}.
        The drafter is now invoked for the final response.
        Expected: the prompt contains 'Working on it.' so the drafter knows not
        to repeat it.
        """
        path_b_content = "Working on it — checking the pods now."
        chat_log = [
            {
                "direction": "out",
                "sender": "valor",
                "content": path_b_content,
                "message_id": 777,
                "ts": 1234567890.0,
            }
        ]
        session = _make_session_with_chat_log(chat_log)
        agent_output = "Deployment is stable. All pods are running."

        prompt = _build_draft_prompt(agent_output, {}, session=session)

        assert path_b_content in prompt, (
            f"Expected Path B content in prompt but it was absent.\n"
            f"Content: {path_b_content!r}\n"
            f"Prompt: {prompt[:500]!r}"
        )

    def test_out_lines_instruction_present_in_prompt(self):
        """The 'you have already said the out lines' instruction appears in the prompt."""
        chat_log = [
            {
                "direction": "out",
                "sender": "valor",
                "content": "Prior send.",
                "message_id": 1,
                "ts": 1.0,
            }
        ]
        session = _make_session_with_chat_log(chat_log)
        prompt = _build_draft_prompt("Final output.", {}, session=session)

        assert "already said the 'out' lines" in prompt

    def test_multiple_path_b_sends_all_appear(self):
        """Multiple prior outbound entries all appear so the drafter sees the full context."""
        chat_log = [
            {
                "direction": "out",
                "sender": "valor",
                "content": "Starting the migration.",
                "message_id": 101,
                "ts": 1.0,
            },
            {
                "direction": "out",
                "sender": "valor",
                "content": "Migration 50% complete.",
                "message_id": 102,
                "ts": 2.0,
            },
        ]
        session = _make_session_with_chat_log(chat_log)
        prompt = _build_draft_prompt("Migration done.", {}, session=session)

        assert "Starting the migration." in prompt
        assert "Migration 50% complete." in prompt

    def test_inbound_and_outbound_both_appear(self):
        """Both inbound and outbound entries appear in the prompt."""
        chat_log = [
            {
                "direction": "in",
                "sender": "Tom",
                "content": "Status?",
                "message_id": 10,
                "ts": 1.0,
            },
            {
                "direction": "out",
                "sender": "valor",
                "content": "Checking now.",
                "message_id": 11,
                "ts": 2.0,
            },
        ]
        session = _make_session_with_chat_log(chat_log)
        prompt = _build_draft_prompt("All done.", {}, session=session)

        assert "Status?" in prompt
        assert "Checking now." in prompt
        assert "[in] Tom:" in prompt
        assert "[out] valor:" in prompt

    def test_empty_chat_log_produces_valid_prompt(self):
        """A session with no chat history still produces a valid drafter prompt."""
        session = _make_session_with_chat_log([])
        prompt = _build_draft_prompt("Agent output.", {}, session=session)

        assert "Recent chat in this thread" not in prompt
        assert "Agent output." in prompt
        assert isinstance(prompt, str)
