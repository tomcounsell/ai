"""The stream-json parse path feeds per-tool cost to telemetry (plan #3081).

``tests/unit/test_tool_cost_attribution.py`` pins the attributor's arithmetic
and ``tests/unit/test_session_telemetry.py`` pins the recorder. Neither proves
the two are connected — an attributor nobody calls produces no baseline. These
tests drive a real stream-json byte stream through ``_run_harness_subprocess``
and assert the ``tool_cost`` event lands on disk.

The subprocess itself is replaced (spawning a real ``claude`` in a unit test
would cost a model call); everything downstream of the pipe — the JSON parse,
the attributor, the recorder, the JSONL write — is production code.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, patch

import pytest

import agent.session_telemetry as telemetry_mod
from agent.session_telemetry import read_session_timeline


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


@pytest.fixture()
def tmp_telemetry(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry_mod, "_TELEMETRY_DIR_RELATIVE", tmp_path / "session_telemetry")
    yield tmp_path / "session_telemetry"


@pytest.fixture(autouse=True)
def _reset_telemetry_state():
    yield
    for sid in list(telemetry_mod._handles):
        fh = telemetry_mod._handles.pop(sid, None)
        if fh:
            try:
                fh.close()
            except Exception:
                pass
    telemetry_mod._locks.clear()
    telemetry_mod._last_event_monotonic.clear()
    telemetry_mod._event_counts.clear()
    telemetry_mod._truncated.clear()


def _assistant(tool_names, usage=None) -> str:
    content = [{"type": "text", "text": "working"}]
    content += [
        {"type": "tool_use", "id": f"tu_{i}", "name": n, "input": {}}
        for i, n in enumerate(tool_names)
    ]
    message = {"role": "assistant", "content": content}
    if usage is not None:
        message["usage"] = usage
    return json.dumps({"type": "assistant", "message": message})


def _usage(inp=0, cache_read=0, cache_create=0, out=0) -> dict:
    return {
        "input_tokens": inp,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_create,
        "output_tokens": out,
    }


def _result(usage=None) -> str:
    ev = {"type": "result", "result": "ok", "session_id": "claude-uuid-xyz"}
    if usage is not None:
        ev["usage"] = usage
    return json.dumps(ev)


async def _drive_subprocess(stream: str, on_tool_cost=None):
    """Run the real parse path over *stream*, with the subprocess stubbed out."""
    from agent.sdk_client import _run_harness_subprocess

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.stdout = _AsyncLineIterator(stream)
        proc.stderr = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        mock_exec.return_value = proc
        return await _run_harness_subprocess(
            ["claude", "-p", "hi"], "/tmp", {}, on_tool_cost=on_tool_cost
        )


class TestParsePathAttributesCost:
    @pytest.mark.asyncio
    async def test_snapshot_reaches_the_callback_with_ranked_tools(self):
        captured: list[dict] = []
        stream = "\n".join(
            [
                json.dumps({"type": "system", "subtype": "init", "tools": ["Bash", "Read"]}),
                _assistant(["Bash"], _usage(cache_read=10_000, out=100)),
                _assistant(["Read"], _usage(cache_read=15_000, out=40)),
                _result(_usage(cache_read=15_200, out=10)),
            ]
        )
        result_text, *_ = await _drive_subprocess(stream, on_tool_cost=captured.append)

        assert result_text == "ok"
        assert len(captured) == 1, "exactly one snapshot per subprocess"
        snap = captured[0]
        assert snap["tool_call_count"] == 2
        assert snap["tool_surface_size"] == 2
        assert snap["initial_context_tokens"] == 10_000
        # Bash's result grew the prefix by 5_000; Read's by 200. The absolute
        # numbers are approximate, the ORDER is the product.
        assert snap["per_tool"]["Bash"]["total_tokens"] > snap["per_tool"]["Read"]["total_tokens"]

    @pytest.mark.asyncio
    async def test_snapshot_fires_even_when_the_subprocess_exits_nonzero(self):
        """A turn that burned context before dying is exactly the sample we want."""
        from agent.sdk_client import _run_harness_subprocess

        captured: list[dict] = []
        stream = _assistant(["Bash"], _usage(cache_read=500, out=20))
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = AsyncMock()
            proc.stdout = _AsyncLineIterator(stream)
            proc.stderr = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b"boom"))
            proc.returncode = 1
            mock_exec.return_value = proc
            await _run_harness_subprocess(
                ["claude", "-p", "hi"], "/tmp", {}, on_tool_cost=captured.append
            )
        assert captured and captured[0]["per_tool"]["Bash"]["calls"] == 1

    @pytest.mark.asyncio
    async def test_binary_not_found_never_fires_the_callback(self):
        from agent.sdk_client import _run_harness_subprocess

        captured: list[dict] = []
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("claude")):
            await _run_harness_subprocess(["claude"], "/tmp", {}, on_tool_cost=captured.append)
        assert captured == []

    @pytest.mark.asyncio
    async def test_callback_error_never_breaks_the_turn(self, caplog):
        stream = "\n".join([_assistant(["Bash"], _usage(cache_read=10, out=1)), _result(_usage())])
        with caplog.at_level(logging.WARNING):

            def _explode(_snapshot):
                raise RuntimeError("consumer exploded")

            result_text, _sid, returncode, *_ = await _drive_subprocess(
                stream, on_tool_cost=_explode
            )
        assert result_text == "ok"
        assert returncode == 0
        assert any("on_tool_cost" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_return_tuple_stays_nine_wide(self):
        """The attribution rides a callback precisely so this shape is untouched."""
        out = await _drive_subprocess(_result(_usage()), on_tool_cost=None)
        assert len(out) == 9


class TestTurnLevelTelemetryEvent:
    @pytest.mark.asyncio
    async def test_tool_cost_event_written_and_totals_persisted(self, tmp_telemetry):
        """get_response_via_harness merges the snapshots and emits one event."""
        from agent.session_runner.harness import claude as claude_mod

        session_id = "wiring-tool-cost-001"
        snapshot = {
            "method": "assistant-usage-delta/v1",
            "per_tool": {
                "Bash": {
                    "calls": 2,
                    "input_tokens": 900,
                    "output_tokens": 100,
                    "total_tokens": 1000,
                }
            },
            "tool_call_count": 2,
            "attributed_input_tokens": 900,
            "attributed_output_tokens": 100,
            "initial_context_tokens": 4_000,
            "tool_surface_size": 12,
            "dropped_samples": 0,
        }

        async def _fake_subprocess(*_args, on_tool_cost=None, **_kwargs):
            if on_tool_cost is not None:
                on_tool_cost(snapshot)
            return ("ok", "claude-uuid", 0, None, None, None, 1, 2, None)

        with (
            patch.object(claude_mod, "_run_harness_subprocess", side_effect=_fake_subprocess),
            patch("agent.sdk_client.accumulate_session_tokens"),
            # No Redis in this test — the Popoto write is exercised separately.
            patch("models.agent_session.AgentSession.query") as mock_query,
        ):
            mock_query.filter.return_value = []
            await claude_mod.get_response_via_harness(
                "hello", session_id=session_id, working_dir="/tmp"
            )

        events = [e for e in read_session_timeline(session_id) if e["type"] == "tool_cost"]
        assert len(events) == 1
        assert events[0]["per_tool"]["Bash"]["total_tokens"] == 1000
        assert events[0]["tool_call_count"] == 2
        assert events[0]["method"] == "assistant-usage-delta/v1"

    @pytest.mark.asyncio
    async def test_tool_free_turn_writes_no_tool_cost_event(self, tmp_telemetry):
        """Absence must be an absence, never a row of zeros posing as a measurement."""
        from agent.session_runner.harness import claude as claude_mod

        session_id = "wiring-tool-cost-002"

        async def _fake_subprocess(*_args, on_tool_cost=None, **_kwargs):
            if on_tool_cost is not None:
                on_tool_cost(
                    {
                        "method": "assistant-usage-delta/v1",
                        "per_tool": {},
                        "tool_call_count": 0,
                        "attributed_input_tokens": 0,
                        "attributed_output_tokens": 0,
                        "initial_context_tokens": None,
                        "tool_surface_size": None,
                        "dropped_samples": 0,
                    }
                )
            return ("ok", "claude-uuid", 0, None, None, None, 1, 0, None)

        with (
            patch.object(claude_mod, "_run_harness_subprocess", side_effect=_fake_subprocess),
            patch("agent.sdk_client.accumulate_session_tokens"),
            patch("models.agent_session.AgentSession.query") as mock_query,
        ):
            mock_query.filter.return_value = []
            await claude_mod.get_response_via_harness(
                "hello", session_id=session_id, working_dir="/tmp"
            )

        assert [e for e in read_session_timeline(session_id) if e["type"] == "tool_cost"] == []
