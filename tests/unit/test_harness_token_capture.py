"""Unit tests for harness-path token capture (issue #1128).

Covers the plan's "B3 fix" — the harness execution path must extract
`usage` + `total_cost_usd` off the `result` event and feed them into
`accumulate_session_tokens` so harness-served sessions (production PM /
Dev / Teammate) no longer report zero tokens.

What we validate:
- `_run_harness_subprocess` returns a 5-tuple including usage + cost.
- The `result` event's `usage` dict and `total_cost_usd` float are
  threaded through unchanged.
- Missing / malformed usage payloads default to None — the helper handles
  them without raising.
- `get_response_via_harness` calls `accumulate_session_tokens` as a side
  effect with the captured values.
- The public return signature of `get_response_via_harness` remains a
  plain `str` (no call site changes required).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


class _AsyncLineIterator:
    """Async iterator yielding encoded stdout lines. Mirrors test_harness_streaming."""

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
    return _AsyncLineIterator(data)


def _result_event(
    result: str = "ok",
    session_id: str = "sess_abc",
    usage: dict | None = None,
    total_cost_usd: float | None = 0.12,
) -> str:
    event: dict = {
        "type": "result",
        "result": result,
        "session_id": session_id,
    }
    if usage is not None:
        event["usage"] = usage
    if total_cost_usd is not None:
        event["total_cost_usd"] = total_cost_usd
    return json.dumps(event)


class TestRunHarnessSubprocessReturnTuple:
    """Validate the 5-tuple return shape introduced by issue #1128."""

    @pytest.mark.asyncio
    async def test_extracts_usage_and_cost(self):
        from agent.sdk_client import _run_harness_subprocess

        usage = {
            "input_tokens": 1234,
            "output_tokens": 567,
            "cache_read_input_tokens": 89,
            "cache_creation_input_tokens": 10,
        }
        lines = _result_event(usage=usage, total_cost_usd=0.99)
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(lines + "\n")
            mock_proc.stderr = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await _run_harness_subprocess(
                ["claude", "-p", "test"],
                "/tmp",
                {},
            )

        assert isinstance(result, tuple)
        # Issue #1099 Mode 1 — return tuple widened to 6 elements (adds stderr_snippet).
        assert len(result) == 6
        result_text, session_id, returncode, out_usage, out_cost, stderr_snippet = result
        assert result_text == "ok"
        assert session_id == "sess_abc"
        assert returncode == 0
        assert out_usage == usage
        assert out_cost == pytest.approx(0.99)
        # Healthy run (returncode == 0) → stderr_snippet is None.
        assert stderr_snippet is None

    @pytest.mark.asyncio
    async def test_missing_usage_returns_none(self):
        """If the `result` event omits usage/cost, both fields are None."""
        from agent.sdk_client import _run_harness_subprocess

        lines = _result_event(usage=None, total_cost_usd=None)
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(lines + "\n")
            mock_proc.stderr = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            # Issue #1099 Mode 1 — 6-tuple return (adds trailing stderr_snippet).
            _, _, _, usage_out, cost_out, _ = await _run_harness_subprocess(
                ["claude", "-p", "test"],
                "/tmp",
                {},
            )
        assert usage_out is None
        assert cost_out is None

    @pytest.mark.asyncio
    async def test_binary_not_found_returns_six_tuple(self):
        """Issue #1099 Mode 1 — binary-not-found path still returns a 6-tuple."""
        from agent.sdk_client import _run_harness_subprocess

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("claude")):
            out = await _run_harness_subprocess(["claude"], "/tmp", {})
        assert isinstance(out, tuple)
        assert len(out) == 6
        # On binary-not-found: returncode, usage, cost, stderr_snippet all None.
        assert out[2] is None
        assert out[3] is None
        assert out[4] is None
        assert out[5] is None


class TestGetResponseViaHarnessAccumulates:
    """End-to-end: harness returns a `result` event → accumulator fires as a side effect."""

    @pytest.mark.asyncio
    async def test_accumulate_called_with_extracted_values(self):
        from agent import sdk_client
        from agent.sdk_client import get_response_via_harness

        usage = {
            "input_tokens": 1000,
            "output_tokens": 400,
            "cache_read_input_tokens": 50,
        }
        stdout = _result_event(usage=usage, total_cost_usd=2.50) + "\n"

        captured = {}

        def fake_accumulate(sid, in_tok, out_tok, cache, cost):
            captured["args"] = (sid, in_tok, out_tok, cache, cost)

        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch.object(sdk_client, "accumulate_session_tokens", fake_accumulate),
            patch.object(sdk_client, "_store_claude_session_uuid", lambda *a, **k: None),
        ):
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout)
            mock_proc.stderr = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(
                message="hello",
                working_dir="/tmp",
                session_id="bridge-sess-1",
            )

        assert result == "ok"  # plain str, signature unchanged
        assert "args" in captured, "accumulate_session_tokens was not called"
        sid, in_tok, out_tok, cache, cost = captured["args"]
        assert sid == "bridge-sess-1"
        assert in_tok == 1000
        assert out_tok == 400
        assert cache == 50
        assert cost == pytest.approx(2.50)

    @pytest.mark.asyncio
    async def test_accumulate_not_called_when_session_id_is_none(self):
        from agent import sdk_client
        from agent.sdk_client import get_response_via_harness

        stdout = _result_event(usage={"input_tokens": 10, "output_tokens": 5}, total_cost_usd=0.01)
        called = []
        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch.object(
                sdk_client,
                "accumulate_session_tokens",
                lambda *a, **k: called.append(a),
            ),
        ):
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout + "\n")
            mock_proc.stderr = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(
                message="hi",
                working_dir="/tmp",
                session_id=None,  # no id → no accumulate
            )
        assert result == "ok"
        assert called == []

    @pytest.mark.asyncio
    async def test_accumulate_not_called_when_usage_and_cost_both_missing(self):
        from agent import sdk_client
        from agent.sdk_client import get_response_via_harness

        # No usage and no total_cost_usd on the result event — both should be
        # None inside the helper, and the accumulator is therefore skipped.
        stdout = _result_event(usage=None, total_cost_usd=None)
        called = []
        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch.object(
                sdk_client,
                "accumulate_session_tokens",
                lambda *a, **k: called.append(a),
            ),
            patch.object(sdk_client, "_store_claude_session_uuid", lambda *a, **k: None),
        ):
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout + "\n")
            mock_proc.stderr = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(
                message="hi",
                working_dir="/tmp",
                session_id="bridge-sess-2",
            )
        assert result == "ok"
        # accumulate not called — neither usage nor cost was present
        assert called == []

    @pytest.mark.asyncio
    async def test_return_signature_remains_str(self):
        """Guard against signature regression — callers expect plain str."""
        from agent.sdk_client import get_response_via_harness

        stdout = _result_event(usage={"input_tokens": 1, "output_tokens": 1})
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.stdout = _async_lines(stdout + "\n")
            mock_proc.stderr = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await get_response_via_harness(
                message="hello",
                working_dir="/tmp",
                # No session_id — accumulator path is skipped
            )
        assert isinstance(result, str)
