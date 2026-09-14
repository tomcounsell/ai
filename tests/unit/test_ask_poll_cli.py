"""Tests for tools/ask_poll.py — the `valor-ask-poll` CLI (#2701).

The CLI is where degradation happens, once. Everything here is about that: what
reaches Telegram as a poll, what reaches it as prose, and what refuses to run at
all rather than degrade under a misleading reason.
"""

from unittest.mock import MagicMock, patch

import pytest

from bridge.poll_gating import PollEligibility
from tools.ask_poll import (
    ESCAPE_HATCH_OPTION,
    MAX_OPTION_CHARS,
    MAX_OPTIONS,
    main,
    normalize_options,
)

TELEGRAM_ENV = {
    "TELEGRAM_CHAT_ID": "-1003449100931",
    "VALOR_SESSION_ID": "sess-1",
    "VALOR_TRANSPORT": "telegram",
}


class TestOptionNormalization:
    """The CLI owns ALL option validation.

    `_validate_for_medium(text, medium)` takes text only and cannot see the
    options, so this is their only home.
    """

    def test_escape_hatch_appended_when_absent(self):
        assert normalize_options(["A", "B"]) == ["A", "B", ESCAPE_HATCH_OPTION]

    def test_escape_hatch_not_duplicated_when_supplied(self):
        result = normalize_options(["A", "B", ESCAPE_HATCH_OPTION])
        assert result.count(ESCAPE_HATCH_OPTION) == 1
        assert result[-1] == ESCAPE_HATCH_OPTION

    def test_escape_hatch_moved_last_when_supplied_early(self):
        """An agent that remembers the hatch and one that forgets get the same poll."""
        result = normalize_options([ESCAPE_HATCH_OPTION, "A", "B"])
        assert result == ["A", "B", ESCAPE_HATCH_OPTION]

    def test_duplicates_removed_preserving_order(self):
        assert normalize_options(["A", "A", "B"]) == ["A", "B", ESCAPE_HATCH_OPTION]

    def test_too_few_options_exits_nonzero(self):
        with pytest.raises(SystemExit) as exc:
            normalize_options([])
        assert exc.value.code == 1

    def test_too_many_options_exits_nonzero(self):
        with pytest.raises(SystemExit) as exc:
            normalize_options([f"opt{i}" for i in range(MAX_OPTIONS + 2)])
        assert exc.value.code == 1

    def test_overlong_option_exits_nonzero(self):
        with pytest.raises(SystemExit) as exc:
            normalize_options(["A", "x" * (MAX_OPTION_CHARS + 1)])
        assert exc.value.code == 1

    def test_empty_option_exits_nonzero(self):
        with pytest.raises(SystemExit) as exc:
            normalize_options(["A", "   "])
        assert exc.value.code == 1


class TestEnvTrio:
    """`_resolve_transport()` only tests for PRESENCE of TELEGRAM_CHAT_ID.

    It hands back neither the chat id nor the session id, so reading it alone is
    not enough. Both required members hard-exit rather than degrade.
    """

    def test_missing_chat_id_exits_nonzero(self, monkeypatch):
        monkeypatch.setenv("VALOR_TRANSPORT", "telegram")
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        monkeypatch.setenv("VALOR_SESSION_ID", "sess-1")
        with pytest.raises(SystemExit) as exc:
            main(["--question", "Q?", "--option", "A", "--option", "B"])
        assert exc.value.code == 1

    def test_missing_session_id_exits_nonzero_rather_than_degrading(self, monkeypatch):
        """This must NOT fall through to poll_eligible(chat_id, None).

        That returns `unknown_session_type` and would silently turn an eligible
        eng question into prose under a misleading reason.
        """
        monkeypatch.setenv("VALOR_TRANSPORT", "telegram")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1003449100931")
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        with pytest.raises(SystemExit) as exc:
            main(["--question", "Q?", "--option", "A", "--option", "B"])
        assert exc.value.code == 1

    def test_reply_to_is_read_and_coerced(self, monkeypatch):
        for key, value in TELEGRAM_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("TELEGRAM_REPLY_TO", "4242")
        handler = MagicMock()
        handler.send_poll = MagicMock(return_value=None)

        with (
            patch(
                "bridge.poll_gating.poll_eligible",
                return_value=PollEligibility(ok=True, reason="eligible"),
            ),
            patch("agent.output_handler.TelegramRelayOutputHandler", return_value=handler),
            patch("tools.ask_poll.asyncio.run") as run,
        ):
            main(["--question", "Q?", "--option", "A", "--option", "B"])

        run.assert_called_once()
        assert handler.send_poll.call_args.kwargs["reply_to_msg_id"] == 4242

    def test_unset_reply_to_yields_none_and_still_sends(self, monkeypatch):
        """A poll with reply_to=None still delivers; it just lands unthreaded."""
        for key, value in TELEGRAM_ENV.items():
            monkeypatch.setenv(key, value)
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)
        handler = MagicMock()
        handler.send_poll = MagicMock(return_value=None)

        with (
            patch(
                "bridge.poll_gating.poll_eligible",
                return_value=PollEligibility(ok=True, reason="eligible"),
            ),
            patch("agent.output_handler.TelegramRelayOutputHandler", return_value=handler),
            patch("tools.ask_poll.asyncio.run"),
        ):
            rc = main(["--question", "Q?", "--option", "A", "--option", "B"])

        assert rc == 0
        assert handler.send_poll.call_args.kwargs["reply_to_msg_id"] is None


class TestDegradation:
    """Degradation happens HERE, once, before a poll payload ever exists.

    None of these tests sends anything to Telegram: DM behavior is asserted by
    testing that the CLI queues prose, never by hitting the API. The capability
    matrix is settled and must not be re-probed.
    """

    @pytest.mark.parametrize(
        "reason",
        ["not_a_group", "not_eng_session", "unknown_session_type", "eligibility_error"],
    )
    def test_ineligible_sends_prose_and_queues_no_poll(self, monkeypatch, reason):
        for key, value in TELEGRAM_ENV.items():
            monkeypatch.setenv(key, value)
        handler = MagicMock()

        with (
            patch(
                "bridge.poll_gating.poll_eligible",
                return_value=PollEligibility(ok=False, reason=reason),
            ),
            patch("agent.output_handler.TelegramRelayOutputHandler", return_value=handler),
            patch("tools.send_message.send_message") as send_message,
        ):
            rc = main(["--question", "Which approach?", "--option", "A", "--option", "B"])

        assert rc == 0
        handler.send_poll.assert_not_called()
        send_message.assert_called_once()
        text = send_message.call_args[0][0]
        assert "Which approach?" in text
        assert "1. A" in text
        assert ESCAPE_HATCH_OPTION in text

    def test_dm_gets_prose(self, monkeypatch):
        """A positive chat id is a DM. Asserted through poll_eligible, not the wire."""
        monkeypatch.setenv("VALOR_TRANSPORT", "telegram")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "179144806")
        monkeypatch.setenv("VALOR_SESSION_ID", "sess-1")
        handler = MagicMock()

        with (
            patch("agent.output_handler.TelegramRelayOutputHandler", return_value=handler),
            patch("tools.send_message.send_message") as send_message,
        ):
            rc = main(["--question", "Q?", "--option", "A", "--option", "B"])

        assert rc == 0
        handler.send_poll.assert_not_called()
        send_message.assert_called_once()

    @pytest.mark.parametrize("transport", ["email", "system"])
    def test_non_telegram_transport_gets_prose(self, monkeypatch, transport):
        monkeypatch.setenv("VALOR_TRANSPORT", transport)
        handler = MagicMock()

        with (
            patch("agent.output_handler.TelegramRelayOutputHandler", return_value=handler),
            patch("tools.send_message.send_message") as send_message,
        ):
            rc = main(["--question", "Q?", "--option", "A", "--option", "B"])

        assert rc == 0
        handler.send_poll.assert_not_called()
        send_message.assert_called_once()

    def test_enqueue_failure_still_reaches_the_human(self, monkeypatch):
        """A failed poll must not become a silent non-question."""
        for key, value in TELEGRAM_ENV.items():
            monkeypatch.setenv(key, value)
        handler = MagicMock()

        with (
            patch(
                "bridge.poll_gating.poll_eligible",
                return_value=PollEligibility(ok=True, reason="eligible"),
            ),
            patch("agent.output_handler.TelegramRelayOutputHandler", return_value=handler),
            patch("tools.ask_poll.asyncio.run", side_effect=RuntimeError("redis down")),
            patch("tools.send_message.send_message") as send_message,
        ):
            rc = main(["--question", "Q?", "--option", "A", "--option", "B"])

        assert rc == 0
        send_message.assert_called_once()


class TestEligiblePath:
    def test_eligible_group_eng_session_queues_a_poll(self, monkeypatch):
        for key, value in TELEGRAM_ENV.items():
            monkeypatch.setenv(key, value)
        handler = MagicMock()
        handler.send_poll = MagicMock(return_value=None)

        with (
            patch(
                "bridge.poll_gating.poll_eligible",
                return_value=PollEligibility(ok=True, reason="eligible"),
            ),
            patch("agent.output_handler.TelegramRelayOutputHandler", return_value=handler),
            patch("tools.ask_poll.asyncio.run"),
            patch("tools.send_message.send_message") as send_message,
        ):
            rc = main(["--question", "Which approach?", "--option", "A", "--option", "B"])

        assert rc == 0
        send_message.assert_not_called()
        kwargs = handler.send_poll.call_args.kwargs
        assert kwargs["question"] == "Which approach?"
        # Recommended option first, escape hatch last.
        assert kwargs["options"][0] == "A"
        assert kwargs["options"][-1] == ESCAPE_HATCH_OPTION

    def test_empty_question_exits_nonzero(self, monkeypatch):
        for key, value in TELEGRAM_ENV.items():
            monkeypatch.setenv(key, value)
        with pytest.raises(SystemExit) as exc:
            main(["--question", "   ", "--option", "A", "--option", "B"])
        assert exc.value.code == 1

    def test_overlong_question_rejected_before_any_network_call(self, monkeypatch):
        from bridge.message_drafter import POLL_QUESTION_MAX_CHARS

        for key, value in TELEGRAM_ENV.items():
            monkeypatch.setenv(key, value)
        handler = MagicMock()
        with (
            patch("agent.output_handler.TelegramRelayOutputHandler", return_value=handler),
            pytest.raises(SystemExit) as exc,
        ):
            main(
                [
                    "--question",
                    "x" * (POLL_QUESTION_MAX_CHARS + 1),
                    "--option",
                    "A",
                    "--option",
                    "B",
                ]
            )
        assert exc.value.code == 1
        handler.send_poll.assert_not_called()


class TestConsoleScriptRegistered:
    def test_entry_point_declared(self):
        """The agent reaches this through Bash; a function in tools/ alone is invisible."""
        from pathlib import Path

        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        assert 'valor-ask-poll = "tools.ask_poll:main"' in pyproject.read_text()
