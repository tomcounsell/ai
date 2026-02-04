"""Tests for the BossMessenger and BackgroundTask classes."""

import asyncio
import importlib.util
import pytest
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Direct import of messenger module to avoid sdk_client dependency
messenger_path = Path(__file__).parent.parent / "agent" / "messenger.py"
spec = importlib.util.spec_from_file_location("messenger", messenger_path)
messenger_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(messenger_module)
BossMessenger = messenger_module.BossMessenger
BackgroundTask = messenger_module.BackgroundTask


class TestBossMessenger:
    """Tests for BossMessenger."""

    @pytest.mark.asyncio
    async def test_send_message(self):
        """Test basic message sending."""
        sent_messages = []

        async def mock_send(msg: str):
            sent_messages.append(msg)

        messenger = BossMessenger(
            _send_callback=mock_send,
            chat_id="test_chat",
            session_id="test_session",
        )

        result = await messenger.send("Hello, boss!")

        assert result is True
        assert len(sent_messages) == 1
        assert sent_messages[0] == "Hello, boss!"
        assert messenger.has_communicated() is True

    @pytest.mark.asyncio
    async def test_send_empty_message_skipped(self):
        """Test that empty messages are skipped."""
        sent_messages = []

        async def mock_send(msg: str):
            sent_messages.append(msg)

        messenger = BossMessenger(
            _send_callback=mock_send,
            chat_id="test_chat",
            session_id="test_session",
        )

        result = await messenger.send("")
        assert result is False
        assert len(sent_messages) == 0

        result = await messenger.send("   ")
        assert result is False
        assert len(sent_messages) == 0

    @pytest.mark.asyncio
    async def test_acknowledgment_sent_once(self):
        """Test that acknowledgment is only sent once."""
        sent_messages = []

        async def mock_send(msg: str):
            sent_messages.append(msg)

        messenger = BossMessenger(
            _send_callback=mock_send,
            chat_id="test_chat",
            session_id="test_session",
        )

        # First acknowledgment should send
        result1 = await messenger.send_acknowledgment("Working on it...")
        assert result1 is True
        assert len(sent_messages) == 1

        # Second acknowledgment should be skipped
        result2 = await messenger.send_acknowledgment("Still working...")
        assert result2 is False
        assert len(sent_messages) == 1  # Still just 1

    @pytest.mark.asyncio
    async def test_acknowledgment_skipped_if_already_communicated(self):
        """Test that acknowledgment is skipped if we already sent a message."""
        sent_messages = []

        async def mock_send(msg: str):
            sent_messages.append(msg)

        messenger = BossMessenger(
            _send_callback=mock_send,
            chat_id="test_chat",
            session_id="test_session",
        )

        # Send a regular message first
        await messenger.send("Here's the result!")

        # Now acknowledgment should be skipped
        result = await messenger.send_acknowledgment()
        assert result is False
        assert len(sent_messages) == 1  # Only the first message

    @pytest.mark.asyncio
    async def test_has_communicated(self):
        """Test the has_communicated check."""

        async def mock_send(msg: str):
            pass

        messenger = BossMessenger(
            _send_callback=mock_send,
            chat_id="test_chat",
            session_id="test_session",
        )

        assert messenger.has_communicated() is False

        await messenger.send("Hello!")

        assert messenger.has_communicated() is True

    @pytest.mark.asyncio
    async def test_callback_error_handled(self):
        """Test that callback errors are handled gracefully."""

        async def failing_send(msg: str):
            raise Exception("Network error")

        messenger = BossMessenger(
            _send_callback=failing_send,
            chat_id="test_chat",
            session_id="test_session",
        )

        result = await messenger.send("Hello!")

        assert result is False
        assert messenger.has_communicated() is False


class TestBackgroundTask:
    """Tests for BackgroundTask."""

    @pytest.mark.asyncio
    async def test_quick_task_no_acknowledgment(self):
        """Test that quick tasks don't trigger acknowledgment."""
        sent_messages = []

        async def mock_send(msg: str):
            sent_messages.append(msg)

        messenger = BossMessenger(
            _send_callback=mock_send,
            chat_id="test_chat",
            session_id="test_session",
        )

        task = BackgroundTask(
            messenger=messenger,
            acknowledgment_timeout=1.0,  # 1 second for testing
        )

        async def quick_work():
            await asyncio.sleep(0.1)  # Very quick
            return "Done quickly!"

        await task.run(quick_work(), send_result=True)

        # Wait for task to complete
        await asyncio.sleep(0.5)

        assert task.is_complete
        assert len(sent_messages) == 1
        assert sent_messages[0] == "Done quickly!"

    @pytest.mark.asyncio
    async def test_slow_task_sends_acknowledgment(self):
        """Test that slow tasks trigger acknowledgment after timeout."""
        sent_messages = []

        async def mock_send(msg: str):
            sent_messages.append(msg)

        messenger = BossMessenger(
            _send_callback=mock_send,
            chat_id="test_chat",
            session_id="test_session",
        )

        task = BackgroundTask(
            messenger=messenger,
            acknowledgment_timeout=0.2,  # 200ms for testing
            acknowledgment_message="I'm on it!",
        )

        async def slow_work():
            await asyncio.sleep(0.5)  # Slower than timeout
            return "Finally done!"

        await task.run(slow_work(), send_result=True)

        # Wait for acknowledgment timeout
        await asyncio.sleep(0.3)

        # Should have acknowledgment by now
        assert len(sent_messages) >= 1
        assert sent_messages[0] == "I'm on it!"

        # Wait for completion
        await asyncio.sleep(0.5)

        assert task.is_complete
        assert len(sent_messages) == 2
        assert sent_messages[1] == "Finally done!"

    @pytest.mark.asyncio
    async def test_task_error_sends_error_message(self):
        """Test that task errors are reported."""
        sent_messages = []

        async def mock_send(msg: str):
            sent_messages.append(msg)

        messenger = BossMessenger(
            _send_callback=mock_send,
            chat_id="test_chat",
            session_id="test_session",
        )

        task = BackgroundTask(
            messenger=messenger,
            acknowledgment_timeout=1.0,
        )

        async def failing_work():
            await asyncio.sleep(0.1)
            raise ValueError("Something went wrong!")

        await task.run(failing_work(), send_result=True)

        # Wait for task to fail
        await asyncio.sleep(0.3)

        assert task.is_complete
        assert task.error is not None
        assert len(sent_messages) == 1
        assert "error" in sent_messages[0].lower()

    @pytest.mark.asyncio
    async def test_task_with_send_result_false(self):
        """Test that send_result=False doesn't auto-send."""
        sent_messages = []

        async def mock_send(msg: str):
            sent_messages.append(msg)

        messenger = BossMessenger(
            _send_callback=mock_send,
            chat_id="test_chat",
            session_id="test_session",
        )

        task = BackgroundTask(
            messenger=messenger,
            acknowledgment_timeout=1.0,
        )

        async def work():
            return "Result that shouldn't be sent"

        await task.run(work(), send_result=False)

        await asyncio.sleep(0.2)

        assert task.is_complete
        assert task.result == "Result that shouldn't be sent"
        assert len(sent_messages) == 0  # Nothing sent

    @pytest.mark.asyncio
    async def test_is_running_property(self):
        """Test the is_running property."""

        async def mock_send(msg: str):
            pass

        messenger = BossMessenger(
            _send_callback=mock_send,
            chat_id="test_chat",
            session_id="test_session",
        )

        task = BackgroundTask(
            messenger=messenger,
            acknowledgment_timeout=1.0,
        )

        assert task.is_running is False

        async def slow_work():
            await asyncio.sleep(0.5)
            return "Done"

        await task.run(slow_work(), send_result=False)

        assert task.is_running is True

        await asyncio.sleep(0.6)

        assert task.is_running is False
        assert task.is_complete is True


class TestIntegration:
    """Integration tests simulating real usage."""

    @pytest.mark.asyncio
    async def test_multiple_messages_scenario(self):
        """Test a scenario where agent sends multiple messages."""
        sent_messages = []
        message_times = []

        async def mock_send(msg: str):
            sent_messages.append(msg)
            message_times.append(datetime.now())

        messenger = BossMessenger(
            _send_callback=mock_send,
            chat_id="test_chat",
            session_id="test_session",
        )

        # Simulate agent sending progress then result
        await messenger.send("Starting analysis...")
        await asyncio.sleep(0.1)
        await messenger.send("Here's what I found: ...")

        assert len(sent_messages) == 2
        assert messenger.has_communicated() is True

        # Acknowledgment should be skipped since we already communicated
        result = await messenger.send_acknowledgment()
        assert result is False

    @pytest.mark.asyncio
    async def test_concurrent_tasks(self):
        """Test that multiple background tasks can run concurrently."""
        results = {}

        async def create_task(task_id: str, duration: float):
            sent = []

            async def mock_send(msg: str):
                sent.append(msg)
                results[task_id] = sent

            messenger = BossMessenger(
                _send_callback=mock_send,
                chat_id=f"chat_{task_id}",
                session_id=f"session_{task_id}",
            )

            task = BackgroundTask(
                messenger=messenger,
                acknowledgment_timeout=0.5,
            )

            async def work():
                await asyncio.sleep(duration)
                return f"Result from {task_id}"

            await task.run(work(), send_result=True)
            return task

        # Launch multiple tasks
        task1 = await create_task("fast", 0.1)
        task2 = await create_task("slow", 0.8)

        # Wait for both
        await asyncio.sleep(1.0)

        assert task1.is_complete
        assert task2.is_complete

        # Fast task should have just result
        assert len(results["fast"]) == 1

        # Slow task should have acknowledgment + result
        assert len(results["slow"]) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
