"""Integration tests for the harness no-op delivery contract.

Validates that streaming chunks from the CLI harness are never forwarded to
any output transport mid-session, and that the output handler (BossMessenger.send)
is called exactly once with the final result string.

These tests exercise the delivery contract at the _execute_agent_session boundary
by mocking get_response_via_harness to return a synthetic result, and spying on
BossMessenger.send to assert single-delivery semantics.

See also: tests/unit/test_harness_streaming.py (isolation tests for parsing/accumulation).
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

FINAL_RESULT = "The task is complete. Here is the final response from the agent."


class TestHarnessNoOpDeliveryContract:
    """The harness no-op contract: output handler called once, with final result only."""

    @pytest.mark.asyncio
    async def test_streaming_chunks_suppressed_single_delivery(self):
        """Output handler is called exactly once with the final result string.

        Patches get_response_via_harness in agent.sdk_client (where it is defined),
        then asserts BossMessenger.send is called exactly once with the final result.
        This validates that no streaming chunks are forwarded mid-session — only the
        single final-result delivery via BackgroundTask occurs.
        """
        from agent.messenger import BackgroundTask, BossMessenger

        # Spy on the output transport: capture every call to BossMessenger.send
        send_calls = []

        async def mock_send_callback(message: str) -> None:
            send_calls.append(message)

        messenger = BossMessenger(
            _send_callback=mock_send_callback,
            chat_id="test-chat",
            session_id="test-session-no-op",
        )

        # Patch get_response_via_harness at its definition site so the lazy import
        # in _execute_agent_session picks up the mock
        async def mock_harness(message, working_dir, harness_cmd=None, env=None):
            return FINAL_RESULT

        with patch("agent.sdk_client.get_response_via_harness", side_effect=mock_harness):
            # Simulate the do_work coroutine exactly as constructed in _execute_agent_session
            async def do_work() -> str:
                from agent.sdk_client import get_response_via_harness

                return await get_response_via_harness(
                    message="test message",
                    working_dir="/tmp/test",
                    env={"AGENT_SESSION_ID": "test-123"},
                )

            task = BackgroundTask(messenger=messenger)
            await task.run(do_work(), send_result=True)

            # Wait for the background task to complete
            if task._task:
                await task._task

        # The output handler must have been called exactly once
        assert len(send_calls) == 1, (
            f"Expected exactly 1 delivery call, got {len(send_calls)}: {send_calls}"
        )
        # The single call must contain the final result string
        assert FINAL_RESULT in send_calls[0], (
            f"Expected final result in delivery call, got: {send_calls[0]!r}"
        )

    @pytest.mark.asyncio
    async def test_no_mid_session_deliveries_during_streaming_events(self):
        """No output handler calls occur while streaming events are being processed.

        Simulates a harness that processes multiple streaming events internally
        before returning the final result. Asserts that BossMessenger.send is only
        called once after the coroutine completes — never during streaming emission.
        """
        from agent.messenger import BackgroundTask, BossMessenger

        send_calls = []

        async def mock_send_callback(message: str) -> None:
            send_calls.append(message)

        messenger = BossMessenger(
            _send_callback=mock_send_callback,
            chat_id="test-chat",
            session_id="test-session-streaming",
        )

        # Simulate a harness that processes streaming events internally then returns
        # (the streaming events are NOT forwarded — only the final string is returned)
        streaming_events_processed = []

        async def mock_harness_with_streaming(message, working_dir, harness_cmd=None, env=None):
            # Simulate internal processing of streaming chunks (no callback calls)
            fake_chunks = ["Hello ", "world", ", how ", "are you?"]
            for chunk in fake_chunks:
                streaming_events_processed.append(chunk)
                await asyncio.sleep(0)  # yield to event loop — no delivery happens here

            # Return only the final result
            return FINAL_RESULT

        with patch(
            "agent.sdk_client.get_response_via_harness",
            side_effect=mock_harness_with_streaming,
        ):

            async def do_work() -> str:
                from agent.sdk_client import get_response_via_harness

                return await get_response_via_harness(
                    message="test message",
                    working_dir="/tmp/test",
                    env={},
                )

            task = BackgroundTask(messenger=messenger)
            await task.run(do_work(), send_result=True)

            if task._task:
                await task._task

        # All streaming chunks were processed internally
        assert len(streaming_events_processed) == 4

        # But the output handler was called exactly once (after all streaming, never during)
        assert len(send_calls) == 1
        assert FINAL_RESULT in send_calls[0]

    @pytest.mark.asyncio
    async def test_fallback_warning_logged(self, caplog):
        """WARNING is logged when harness returns without a result event (full_text fallback).

        Exercises the fallback path in get_response_via_harness where the subprocess
        exits without emitting a result event, causing the function to fall back to
        accumulated streaming text and log a WARNING.
        """
        import json

        from agent.sdk_client import get_response_via_harness

        # Build harness output with streaming chunks but NO result event
        lines = [
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": "fallback text content"},
                    },
                }
            ),
            # No result event — subprocess just exits
        ]
        stdout_data = "\n".join(lines) + "\n"

        class _AsyncLines:
            def __init__(self, data):
                self._lines = [
                    (line + "\n").encode("utf-8") for line in data.splitlines() if line.strip()
                ]
                self._index = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._index >= len(self._lines):
                    raise StopAsyncIteration
                line = self._lines[self._index]
                self._index += 1
                return line

        with caplog.at_level(logging.WARNING, logger="agent.sdk_client"):
            with patch("asyncio.create_subprocess_exec") as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.stdout = _AsyncLines(stdout_data)
                mock_proc.communicate = AsyncMock(return_value=(b"", b""))
                mock_proc.returncode = 0
                mock_exec.return_value = mock_proc

                result = await get_response_via_harness(
                    message="test",
                    working_dir="/tmp",
                )

        # Fallback text is returned
        assert result == "fallback text content"

        # WARNING was logged for the fallback path
        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        has_fallback_warning = any(
            "result event" in msg.lower() or "fallback" in msg.lower() for msg in warning_msgs
        )
        assert has_fallback_warning, f"Expected fallback WARNING in logs, got: {warning_msgs}"

    @pytest.mark.asyncio
    async def test_get_response_via_harness_has_no_send_cb_parameter(self):
        """Regression: get_response_via_harness must not accept a send_cb parameter.

        This test asserts that the dead send_cb parameter has been removed from the
        function signature, ensuring no caller can accidentally re-enable streaming
        delivery by passing a callback.
        """
        import inspect

        from agent.sdk_client import get_response_via_harness

        sig = inspect.signature(get_response_via_harness)
        assert "send_cb" not in sig.parameters, (
            "get_response_via_harness still has a send_cb parameter — "
            "the dead streaming API was not fully removed."
        )
