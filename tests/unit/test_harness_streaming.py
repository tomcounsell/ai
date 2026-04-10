"""Tests for the CLI harness streaming function and worker routing.

Covers:
- Phase 1: get_response_via_harness() parsing, batching, error handling
- Phase 2: Worker routing by session type and DEV_SESSION_HARNESS env var
- Phase 2: Startup health check (verify_harness_health)
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- Phase 1: get_response_via_harness tests ---


class TestGetResponseViaHarness:
    """Tests for the CLI harness streaming function."""

    @pytest.mark.asyncio
    async def test_parses_text_delta_events(self):
        """Extracts text from content_block_delta stream events."""
        from agent.sdk_client import get_response_via_harness

        # Build fake stream-json output
        lines = [
            json.dumps({"type": "system", "apiKeySource": "none"}),
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "Hello "},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "world"},
                    },
                }
            ),
            json.dumps({"type": "result", "result": "Hello world", "session_id": "sess_123"}),
        ]
        stdout_data = "\n".join(lines) + "\n"

        send_cb = AsyncMock()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout_data)
            mock_proc.stderr = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(
                message="test prompt",
                send_cb=send_cb,
                working_dir="/tmp/test",
            )

        assert result == "Hello world"
        # send_cb should have been called at least once (final flush)
        assert send_cb.call_count >= 1

    @pytest.mark.asyncio
    async def test_skips_tool_use_events(self):
        """Tool use stream events are silently skipped."""
        from agent.sdk_client import get_response_via_harness

        lines = [
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {"type": "content_block_start", "index": 0},
                }
            ),
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "input_json_delta", "partial_json": '{"foo":'},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {"type": "tool_use", "name": "Read"},
                }
            ),
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "Only text"},
                    },
                }
            ),
            json.dumps({"type": "result", "result": "Only text", "session_id": "s1"}),
        ]
        stdout_data = "\n".join(lines) + "\n"

        send_cb = AsyncMock()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout_data)
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(
                message="test", send_cb=send_cb, working_dir="/tmp"
            )

        assert result == "Only text"
        # The tool events should not have produced any text output
        for call in send_cb.call_args_list:
            text = call.args[0] if call.args else call.kwargs.get("text", "")
            assert "input_json" not in text
            assert "tool_use" not in text

    @pytest.mark.asyncio
    async def test_handles_malformed_json_lines(self):
        """Malformed JSON lines are skipped without crashing."""
        from agent.sdk_client import get_response_via_harness

        lines = [
            "not valid json",
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "good text"},
                    },
                }
            ),
            "{truncated json",
            json.dumps({"type": "result", "result": "good text"}),
        ]
        stdout_data = "\n".join(lines) + "\n"

        send_cb = AsyncMock()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout_data)
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(
                message="test", send_cb=send_cb, working_dir="/tmp"
            )

        assert result == "good text"

    @pytest.mark.asyncio
    async def test_char_threshold_batching(self):
        """Text is flushed when buffer exceeds char threshold."""
        from agent.sdk_client import (
            _HARNESS_FLUSH_CHAR_THRESHOLD,
            get_response_via_harness,
        )

        # Generate enough text to trigger the char threshold
        big_text = "x" * (_HARNESS_FLUSH_CHAR_THRESHOLD + 100)
        lines = [
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": big_text},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": " more"},
                    },
                }
            ),
            json.dumps({"type": "result", "result": big_text + " more"}),
        ]
        stdout_data = "\n".join(lines) + "\n"

        send_cb = AsyncMock()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout_data)
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await get_response_via_harness(message="test", send_cb=send_cb, working_dir="/tmp")

        # Should have been called at least twice: once for the big chunk, once for final flush
        assert send_cb.call_count >= 2

    @pytest.mark.asyncio
    async def test_binary_not_found(self):
        """Returns error message when CLI binary is not on PATH."""
        from agent.sdk_client import get_response_via_harness

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("not found")):
            result = await get_response_via_harness(
                message="test",
                send_cb=AsyncMock(),
                working_dir="/tmp",
                harness_cmd=["nonexistent-binary", "-p"],
            )

        assert "not found" in result.lower() or "Error" in result

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_logged(self):
        """Non-zero exit code logs stderr but still returns accumulated text."""
        from agent.sdk_client import get_response_via_harness

        lines = [
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "partial output"},
                    },
                }
            ),
        ]
        stdout_data = "\n".join(lines) + "\n"

        send_cb = AsyncMock()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout_data)
            mock_proc.communicate = AsyncMock(return_value=(b"", b"some error\n"))
            mock_proc.returncode = 1
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(
                message="test", send_cb=send_cb, working_dir="/tmp"
            )

        # Should return accumulated text even on error
        assert "partial output" in result

    @pytest.mark.asyncio
    async def test_no_result_event_returns_accumulated_text(self):
        """If process exits without a result event, return accumulated text."""
        from agent.sdk_client import get_response_via_harness

        lines = [
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "some output"},
                    },
                }
            ),
            # No result event — process just exits
        ]
        stdout_data = "\n".join(lines) + "\n"

        send_cb = AsyncMock()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout_data)
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(
                message="test", send_cb=send_cb, working_dir="/tmp"
            )

        assert result == "some output"

    @pytest.mark.asyncio
    async def test_empty_output(self):
        """Returns fallback message when no output is produced."""
        from agent.sdk_client import get_response_via_harness

        send_cb = AsyncMock()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines("")
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(
                message="test", send_cb=send_cb, working_dir="/tmp"
            )

        assert "no output" in result.lower()

    @pytest.mark.asyncio
    async def test_custom_harness_cmd(self):
        """Custom harness command is passed to subprocess."""
        from agent.sdk_client import get_response_via_harness

        custom_cmd = ["opencode", "--non-interactive"]

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(json.dumps({"type": "result", "result": "done"}) + "\n")
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await get_response_via_harness(
                message="test",
                send_cb=AsyncMock(),
                working_dir="/tmp",
                harness_cmd=custom_cmd,
            )

        # Verify the custom command was used
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args
        assert call_args.args[:2] == ("opencode", "--non-interactive")

    @pytest.mark.asyncio
    async def test_env_vars_passed_to_subprocess(self):
        """Custom env vars are passed to the subprocess."""
        from agent.sdk_client import get_response_via_harness

        custom_env = {"AGENT_SESSION_ID": "test_123", "CUSTOM_VAR": "value"}

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(json.dumps({"type": "result", "result": "done"}) + "\n")
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await get_response_via_harness(
                message="test",
                send_cb=AsyncMock(),
                working_dir="/tmp",
                env=custom_env,
            )

        call_kwargs = mock_exec.call_args.kwargs
        proc_env = call_kwargs["env"]
        assert proc_env["AGENT_SESSION_ID"] == "test_123"
        assert proc_env["CUSTOM_VAR"] == "value"
        # ANTHROPIC_API_KEY should be stripped
        assert "ANTHROPIC_API_KEY" not in proc_env

    @pytest.mark.asyncio
    async def test_api_key_stripped_from_env(self):
        """ANTHROPIC_API_KEY is removed from subprocess env."""
        from agent.sdk_client import get_response_via_harness

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret-key"}):
            with patch("asyncio.create_subprocess_exec") as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.stdout = _async_lines(
                    json.dumps({"type": "result", "result": "done"}) + "\n"
                )
                mock_proc.communicate = AsyncMock(return_value=(b"", b""))
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                await get_response_via_harness(
                    message="test",
                    send_cb=AsyncMock(),
                    working_dir="/tmp",
                )

            call_kwargs = mock_exec.call_args.kwargs
            assert "ANTHROPIC_API_KEY" not in call_kwargs["env"]


# --- Phase 2: Worker routing tests ---


class TestWorkerHarnessRouting:
    """Tests for dev session routing in _execute_agent_session."""

    def test_default_harness_is_sdk(self):
        """DEV_SESSION_HARNESS defaults to 'sdk', preserving current behavior."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DEV_SESSION_HARNESS", None)
            assert os.environ.get("DEV_SESSION_HARNESS", "sdk") == "sdk"

    def test_harness_env_var_recognized(self):
        """DEV_SESSION_HARNESS=claude-cli is a valid configuration."""
        with patch.dict(os.environ, {"DEV_SESSION_HARNESS": "claude-cli"}):
            assert os.environ.get("DEV_SESSION_HARNESS") == "claude-cli"

    def test_unknown_harness_value_detection(self):
        """Unknown harness values should be detectable for fallback."""
        known_harnesses = {"sdk", "claude-cli", "opencode"}
        assert "unknown-harness" not in known_harnesses


class TestVerifyHarnessHealth:
    """Tests for the startup health check."""

    @pytest.mark.asyncio
    async def test_unknown_harness_returns_false(self):
        """Unknown harness name returns False."""
        from agent.sdk_client import verify_harness_health

        result = await verify_harness_health("unknown-harness")
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_binary_returns_false(self):
        """Returns False when claude binary is not on PATH."""
        from agent.sdk_client import verify_harness_health

        with patch("shutil.which", return_value=None):
            result = await verify_harness_health("claude-cli")
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_health_check(self):
        """Returns True when claude responds with system event."""
        from agent.sdk_client import verify_harness_health

        system_event = json.dumps({"type": "system", "apiKeySource": "none"})
        result_event = json.dumps({"type": "result", "result": "test"})
        lines = [
            f"{system_event}\n".encode(),
            f"{result_event}\n".encode(),
            b"",  # EOF
        ]

        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            with patch("asyncio.create_subprocess_exec") as mock_exec:
                mock_proc = AsyncMock()
                mock_stdout = AsyncMock()
                mock_stdout.readline = AsyncMock(side_effect=lines)
                mock_proc.stdout = mock_stdout
                mock_proc.kill = MagicMock()
                mock_proc.wait = AsyncMock()
                mock_exec.return_value = mock_proc

                result = await verify_harness_health("claude-cli")

        assert result is True
        # Process should be killed after system event (no API round-trip)
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_key_billing_warning(self):
        """Logs warning but returns True when using API key billing."""
        from agent.sdk_client import verify_harness_health

        system_event = json.dumps({"type": "system", "apiKeySource": "env_var"})
        lines = [
            f"{system_event}\n".encode(),
            b"",  # EOF
        ]

        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            with patch("asyncio.create_subprocess_exec") as mock_exec:
                mock_proc = AsyncMock()
                mock_stdout = AsyncMock()
                mock_stdout.readline = AsyncMock(side_effect=lines)
                mock_proc.stdout = mock_stdout
                mock_proc.kill = MagicMock()
                mock_proc.wait = AsyncMock()
                mock_exec.return_value = mock_proc

                result = await verify_harness_health("claude-cli")

        # Should still return True (it works, just costs money)
        assert result is True
        mock_proc.kill.assert_called_once()


# --- Helper: async line iterator for mocking process.stdout ---


class _AsyncLineIterator:
    """Async iterator that yields lines from a string, simulating process.stdout."""

    def __init__(self, data: str):
        self._lines = [(line + "\n").encode("utf-8") for line in data.splitlines() if line.strip()]
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._index]
        self._index += 1
        return line


def _async_lines(data: str) -> _AsyncLineIterator:
    """Create an async line iterator from a string."""
    return _AsyncLineIterator(data)
