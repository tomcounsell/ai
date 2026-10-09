"""API-failure detection on the stream-json result event (#3615).

CLI 2.1.x reports an Anthropic API failure (529 overloaded, 429, 5xx) as a
``result`` event with ``subtype: "success"`` and ``is_error: true`` plus an
``api_error_status``; its ``result`` text is ``"API Error: 529 {...}"``. These
tests drive real stream bytes through ``_run_harness_subprocess`` and assert
the failure is surfaced via ``on_api_error`` rather than passing as a reply.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from agent.session_runner.router import is_transient_api_status


class _AsyncLineIterator:
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


async def _drive(result_event: dict):
    from agent.sdk_client import _run_harness_subprocess

    captured: list[tuple[bool, int | None]] = []
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.stdout = _AsyncLineIterator(json.dumps(result_event))
        proc.stderr = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        mock_exec.return_value = proc
        await _run_harness_subprocess(
            ["claude", "-p", "hi"],
            "/tmp",
            {},
            on_api_error=lambda is_err, status: captured.append((is_err, status)),
        )
    return captured


async def test_overloaded_result_fires_api_error_with_status():
    captured = await _drive(
        {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "api_error_status": 529,
            "result": 'API Error: 529 {"type":"error","error":{"type":"overloaded_error"}}',
            "session_id": "claude-uuid-1",
        }
    )
    assert captured == [(True, 529)]


async def test_clean_result_reports_no_api_error():
    captured = await _drive(
        {"type": "result", "subtype": "success", "is_error": False, "result": "done"}
    )
    assert captured == [(False, None)]


async def test_error_subtypes_are_not_api_errors():
    """``error_max_turns`` etc. are budget exits, not API failures."""
    captured = await _drive({"type": "result", "subtype": "error_max_turns", "is_error": True})
    assert captured == [(False, None)]


def test_transient_status_classification():
    assert is_transient_api_status(529)
    assert is_transient_api_status(500)
    assert is_transient_api_status(429)
    assert is_transient_api_status(408)
    assert is_transient_api_status(None)  # unknown status: assume transient
    assert not is_transient_api_status(400)
    assert not is_transient_api_status(401)
    assert not is_transient_api_status(413)
