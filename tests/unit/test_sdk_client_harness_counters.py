"""Unit tests for issue #1245 — turn_count + tool_call_count persistence.

Validates that `get_response_via_harness` accumulates harness-emitted
`num_turns` and `tool_use` counts onto the matching AgentSession via
Popoto, and that fallback subprocess invocations sum (not overwrite).

Uses the autouse `redis_test_db` fixture (see tests/conftest.py) to keep
all Popoto writes in an isolated test database.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest


class _AsyncLineIterator:
    """Async iterator yielding encoded stdout lines."""

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


def _async_lines(data: str):
    return _AsyncLineIterator(data)


def _stdout_with_tool_uses(num_turns: int, tool_use_count: int, result_text: str = "ok") -> str:
    """Build a stream-json stdout with N tool_use blocks + a result event."""
    lines = []
    if tool_use_count > 0:
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": f"Tool{i}",
                                "id": f"t{i}",
                                "input": {},
                            }
                            for i in range(tool_use_count)
                        ]
                    },
                }
            )
        )
    lines.append(
        json.dumps(
            {
                "type": "result",
                "result": result_text,
                "session_id": "claude-uuid",
                "num_turns": num_turns,
            }
        )
    )
    return "\n".join(lines) + "\n"


@pytest.mark.asyncio
async def test_turn_count_persisted(redis_test_db):
    """A single harness turn with num_turns=2 writes turn_count=2 onto AgentSession."""
    from agent.sdk_client import get_response_via_harness
    from models.agent_session import AgentSession

    session_id = "test-1245-turn-count"
    AgentSession.create(
        session_id=session_id,
        project_key="test-1245",
        status="running",
        created_at=datetime.now(tz=UTC),
    )

    stdout = _stdout_with_tool_uses(num_turns=2, tool_use_count=0)
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.stdout = _async_lines(stdout)
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        result = await get_response_via_harness(
            message="hello",
            working_dir="/tmp",
            session_id=session_id,
        )

    assert result == "ok"
    refreshed = AgentSession.query.filter(session_id=session_id).first()
    assert refreshed is not None
    assert refreshed.turn_count == 2

    refreshed.delete()


@pytest.mark.asyncio
async def test_tool_call_count_persisted(redis_test_db):
    """3 tool_use blocks across one assistant event → tool_call_count == 3."""
    from agent.sdk_client import get_response_via_harness
    from models.agent_session import AgentSession

    session_id = "test-1245-tool-count"
    AgentSession.create(
        session_id=session_id,
        project_key="test-1245",
        status="running",
        created_at=datetime.now(tz=UTC),
    )

    stdout = _stdout_with_tool_uses(num_turns=1, tool_use_count=3)
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.stdout = _async_lines(stdout)
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        await get_response_via_harness(
            message="hello",
            working_dir="/tmp",
            session_id=session_id,
        )

    refreshed = AgentSession.query.filter(session_id=session_id).first()
    assert refreshed is not None
    assert refreshed.tool_call_count == 3
    assert refreshed.turn_count == 1

    refreshed.delete()


@pytest.mark.asyncio
async def test_turn_count_accumulates_across_resumes(redis_test_db):
    """Two separate get_response_via_harness calls accumulate (2 + 3 == 5)."""
    from agent.sdk_client import get_response_via_harness
    from models.agent_session import AgentSession

    session_id = "test-1245-accumulate"
    AgentSession.create(
        session_id=session_id,
        project_key="test-1245",
        status="running",
        created_at=datetime.now(tz=UTC),
    )

    # Turn 1 — num_turns=2
    stdout1 = _stdout_with_tool_uses(num_turns=2, tool_use_count=1)
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.stdout = _async_lines(stdout1)
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        await get_response_via_harness(
            message="first",
            working_dir="/tmp",
            session_id=session_id,
        )

    # Turn 2 — num_turns=3, simulating a follow-up turn on the same AgentSession
    stdout2 = _stdout_with_tool_uses(num_turns=3, tool_use_count=2)
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.stdout = _async_lines(stdout2)
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        await get_response_via_harness(
            message="second",
            working_dir="/tmp",
            session_id=session_id,
        )

    refreshed = AgentSession.query.filter(session_id=session_id).first()
    assert refreshed.turn_count == 5
    assert refreshed.tool_call_count == 3

    refreshed.delete()


@pytest.mark.asyncio
async def test_no_persist_when_session_id_none(redis_test_db):
    """No session_id → Popoto write is skipped (no crash)."""
    from agent.sdk_client import get_response_via_harness

    stdout = _stdout_with_tool_uses(num_turns=2, tool_use_count=1)
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.stdout = _async_lines(stdout)
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        result = await get_response_via_harness(
            message="hi",
            working_dir="/tmp",
            session_id=None,
        )

    assert result == "ok"


@pytest.mark.asyncio
async def test_no_persist_when_session_not_found(redis_test_db):
    """Unknown session_id → fail-quiet, no exception bubbles up."""
    from agent.sdk_client import get_response_via_harness

    stdout = _stdout_with_tool_uses(num_turns=2, tool_use_count=1)
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.stdout = _async_lines(stdout)
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        result = await get_response_via_harness(
            message="hi",
            working_dir="/tmp",
            session_id="nonexistent-session-id",
        )

    assert result == "ok"
