"""Unit tests for Pi builder: parse_pi_final_text and PiSubprocessBuilder."""

import json
import signal
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from agent.granite_container.builder import (
    PI_SUBPROCESS_TIMEOUT_S,
    PiSubprocessBuilder,
    parse_pi_final_text,
)

# ---------------------------------------------------------------------------
# Fixtures / test data
# ---------------------------------------------------------------------------

# Build the NDJSON fixtures programmatically to avoid E501 long-line violations.

_REAL_LINES = [
    json.dumps({"type": "session", "session": {}}),
    json.dumps({"type": "agent_start", "messages": []}),
    json.dumps({"type": "turn_start"}),
    json.dumps({"type": "message_start"}),
    json.dumps({"type": "text_end"}),
    json.dumps({"type": "turn_end"}),
    json.dumps(
        {
            "type": "agent_end",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Reply with exactly: PONG"}],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "PONG"}],
                    "usage": {"input_tokens": 10, "output_tokens": 4},
                },
            ],
        }
    ),
]
REAL_ENVELOPE_FIXTURE = "\n".join(_REAL_LINES)

THINKING_ONLY_FIXTURE = json.dumps(
    {
        "type": "agent_end",
        "messages": [
            {
                "role": "assistant",
                "content": [{"type": "thinking", "text": "some reasoning"}],
            }
        ],
    }
)

MULTI_TEXT_FIXTURE = json.dumps(
    {
        "type": "agent_end",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Part 1. "},
                    {"type": "thinking", "text": "..."},
                    {"type": "text", "text": "Part 2."},
                ],
            }
        ],
    }
)

NO_AGENT_END_FIXTURE = "\n".join(
    [
        json.dumps({"type": "session"}),
        json.dumps({"type": "turn_start"}),
        json.dumps({"type": "message_start"}),
        json.dumps({"type": "text_delta", "delta": "incomplete"}),
    ]
)


# Helper: build a minimal agent_end NDJSON line for ad-hoc tests.
def _agent_end(text: str, role: str = "assistant") -> str:
    content = [{"type": "text", "text": text}]
    return json.dumps(
        {
            "type": "agent_end",
            "messages": [{"role": role, "content": content}],
        }
    )


def _agent_end_thinking(thinking: str) -> str:
    return json.dumps(
        {
            "type": "agent_end",
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "thinking", "text": thinking}],
                }
            ],
        }
    )


# ---------------------------------------------------------------------------
# parse_pi_final_text
# ---------------------------------------------------------------------------


class TestParsePiFinalText:
    def test_empty_string_returns_empty(self):
        assert parse_pi_final_text("") == ""

    def test_real_envelope_extracts_pong(self):
        result = parse_pi_final_text(REAL_ENVELOPE_FIXTURE)
        assert result == "PONG"

    def test_thinking_only_returns_empty(self):
        result = parse_pi_final_text(THINKING_ONLY_FIXTURE)
        assert result == ""

    def test_multi_text_concatenated(self):
        result = parse_pi_final_text(MULTI_TEXT_FIXTURE)
        assert result == "Part 1. Part 2."

    def test_no_agent_end_returns_empty(self):
        result = parse_pi_final_text(NO_AGENT_END_FIXTURE)
        assert result == ""

    def test_malformed_json_lines_skipped(self):
        stream = "not-json\n" + _agent_end("ok")
        assert parse_pi_final_text(stream) == "ok"

    def test_no_assistant_message_returns_empty(self):
        stream = _agent_end("user text", role="user")
        assert parse_pi_final_text(stream) == ""

    def test_multiple_agent_end_uses_last(self):
        stream = _agent_end("first") + "\n" + _agent_end("last")
        assert parse_pi_final_text(stream) == "last"

    def test_whitespace_only_lines_skipped(self):
        stream = "\n   \n" + _agent_end("hi")
        assert parse_pi_final_text(stream) == "hi"

    def test_empty_messages_list_returns_empty(self):
        stream = json.dumps({"type": "agent_end", "messages": []})
        assert parse_pi_final_text(stream) == ""

    def test_mixed_content_types_only_text_concatenated(self):
        """Thinking blocks are excluded; only text blocks concatenated."""
        stream = json.dumps(
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "text": "internal"},
                            {"type": "text", "text": "visible"},
                        ],
                    }
                ],
            }
        )
        assert parse_pi_final_text(stream) == "visible"


# ---------------------------------------------------------------------------
# PiSubprocessBuilder init
# ---------------------------------------------------------------------------


class TestPiSubprocessBuilderInit:
    def test_raises_on_empty_string_cwd(self):
        """Risk 6: falsy builder_cwd must raise, never Popen(cwd=None)."""
        with pytest.raises(ValueError, match="builder_cwd must be non-empty"):
            PiSubprocessBuilder(builder_cwd="", rails_path="/r", persona_path="/p")

    def test_raises_on_none_cwd(self):
        with pytest.raises((ValueError, TypeError)):
            PiSubprocessBuilder(builder_cwd=None, rails_path="/r", persona_path="/p")  # type: ignore[arg-type]

    def test_valid_init_stores_cwd(self, tmp_path):
        b = PiSubprocessBuilder(
            builder_cwd=str(tmp_path),
            rails_path="/rails",
            persona_path="/persona",
        )
        assert b.builder_cwd == str(tmp_path)

    def test_name_is_pi(self, tmp_path):
        b = PiSubprocessBuilder(
            builder_cwd=str(tmp_path),
            rails_path="/rails",
            persona_path="/persona",
        )
        assert b.name == "pi"

    def test_default_timeout_is_pi_subprocess_timeout(self, tmp_path):
        b = PiSubprocessBuilder(
            builder_cwd=str(tmp_path),
            rails_path="/rails",
            persona_path="/persona",
        )
        assert b.timeout_s == PI_SUBPROCESS_TIMEOUT_S

    def test_custom_timeout_stored(self, tmp_path):
        b = PiSubprocessBuilder(
            builder_cwd=str(tmp_path),
            rails_path="/r",
            persona_path="/p",
            timeout_s=30,
        )
        assert b.timeout_s == 30

    def test_compatible_surface_defaults(self, tmp_path):
        """PtyClaudeBuilder-compatible surface: last_dev_buf/marker/ms/hung."""
        b = PiSubprocessBuilder(builder_cwd=str(tmp_path), rails_path="/r", persona_path="/p")
        assert b.last_dev_buf == ""
        assert b.last_dev_marker == ""
        assert b.last_dev_ms == 0
        assert b.last_hung is False


# ---------------------------------------------------------------------------
# PiSubprocessBuilder.run_turn (mocked subprocess)
# ---------------------------------------------------------------------------


class TestPiSubprocessBuilderRunTurn:
    def _make_builder(self, tmp_path, **kwargs):
        return PiSubprocessBuilder(
            builder_cwd=str(tmp_path),
            rails_path=str(tmp_path / "rails.md"),
            persona_path=str(tmp_path / "persona.md"),
            provider="google",
            model="ollama/gemma4:31b",
            **kwargs,
        )

    def _make_mock_proc(self, stdout="", stderr="", returncode=0, pid=12345):
        mock_proc = MagicMock()
        mock_proc.communicate.return_value = (stdout, stderr)
        mock_proc.returncode = returncode
        mock_proc.pid = pid
        return mock_proc

    def test_successful_run_returns_text(self, tmp_path):
        """Happy path: pi runs and returns PONG."""
        b = self._make_builder(tmp_path)
        mock_proc = self._make_mock_proc(stdout=REAL_ENVELOPE_FIXTURE, returncode=0)

        with patch("subprocess.Popen", return_value=mock_proc):
            result = b.run_turn("Reply with exactly: PONG")

        assert result == "PONG"

    def test_nonzero_exit_returns_empty(self, tmp_path):
        """Pi exits non-zero: log warning, return empty."""
        b = self._make_builder(tmp_path)
        mock_proc = self._make_mock_proc(stdout="", stderr="error output", returncode=1)

        with patch("subprocess.Popen", return_value=mock_proc):
            result = b.run_turn("do something")

        assert result == ""

    def test_timeout_kills_process_group(self, tmp_path):
        """On TimeoutExpired: os.killpg called on the process group, returns ''."""
        b = self._make_builder(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd=["pi"], timeout=10),
            ("", ""),  # second communicate after kill
        ]

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("os.killpg") as mock_killpg,
            patch("os.getpgid", return_value=99999),
        ):
            result = b.run_turn("do something slow")

        assert result == ""
        mock_killpg.assert_called_once_with(99999, signal.SIGKILL)

    def test_start_new_session_passed_to_popen(self, tmp_path):
        """Popen must be called with start_new_session=True for pgid-based kill."""
        b = self._make_builder(tmp_path)
        mock_proc = self._make_mock_proc(stdout=REAL_ENVELOPE_FIXTURE, returncode=0)

        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            b.run_turn("test")

        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs.get("start_new_session") is True

    def test_cwd_passed_to_popen(self, tmp_path):
        """Risk 6: subprocess must run in builder_cwd, not repo root."""
        b = self._make_builder(tmp_path)
        mock_proc = self._make_mock_proc(stdout=REAL_ENVELOPE_FIXTURE, returncode=0)

        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            b.run_turn("test")

        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs.get("cwd") == str(tmp_path)

    def test_pi_not_found_returns_empty(self, tmp_path):
        """FileNotFoundError (pi not on PATH) -> log error, return ''."""
        b = self._make_builder(tmp_path)
        with patch("subprocess.Popen", side_effect=FileNotFoundError("pi not found")):
            result = b.run_turn("test")
        assert result == ""

    def test_prepare_is_noop(self, tmp_path):
        """prepare() must not raise; it's a no-op for stateless Pi."""
        b = self._make_builder(tmp_path)
        b.prepare(spec=None)  # must not raise
        b.prepare()  # no spec variant

    def test_close_kills_live_proc(self, tmp_path):
        """close() reaps any live subprocess via killpg."""
        b = self._make_builder(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 55555
        b._proc = mock_proc

        with patch("os.killpg") as mock_killpg, patch("os.getpgid", return_value=55555):
            b.close()

        mock_killpg.assert_called_once_with(55555, signal.SIGKILL)
        assert b._proc is None

    def test_close_no_proc_is_noop(self, tmp_path):
        """close() with no live proc must not raise."""
        b = self._make_builder(tmp_path)
        b.close()  # must not raise

    def test_run_turn_clears_proc_on_success(self, tmp_path):
        """_proc is None after a successful run_turn (resource cleanup)."""
        b = self._make_builder(tmp_path)
        mock_proc = self._make_mock_proc(stdout=REAL_ENVELOPE_FIXTURE, returncode=0)

        with patch("subprocess.Popen", return_value=mock_proc):
            b.run_turn("test")

        assert b._proc is None

    def test_run_turn_returns_string_type(self, tmp_path):
        """run_turn always returns str, even on error paths."""
        b = self._make_builder(tmp_path)
        with patch("subprocess.Popen", side_effect=RuntimeError("unexpected")):
            result = b.run_turn("test")
        assert isinstance(result, str)

    def test_process_lookup_error_on_kill_swallowed(self, tmp_path):
        """ProcessLookupError during killpg (race: process already dead) must not raise."""
        b = self._make_builder(tmp_path)
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd=["pi"], timeout=10),
            ("", ""),
        ]

        with (
            patch("subprocess.Popen", return_value=mock_proc),
            patch("os.getpgid", return_value=99999),
            patch("os.killpg", side_effect=ProcessLookupError),
        ):
            result = b.run_turn("slow")

        assert result == ""  # no exception raised
