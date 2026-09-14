"""Per-tool context-cost attribution arithmetic (plan #3081, Lane A task 1).

``ToolCostAttributor`` is a pure state machine over the events ``claude -p
--output-format stream-json`` already emits, so these tests feed it real event
shapes rather than mocking it out.

Two properties matter:

1. **Ranking fidelity.** The absolute numbers are approximate by design (see
   the module docstring); what must hold is that a tool whose results dominate
   the context prefix ranks above one whose results are small.
2. **Fail-quiet.** A malformed event logs a WARNING, increments
   ``dropped_samples``, and leaves the attributor usable — a turn must never
   die on an approximation.

The wiring from the parse path to the telemetry stream lives in
``tests/unit/test_harness_tool_cost_wiring.py``.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from agent.tool_cost_attribution import (
    ATTRIBUTION_METHOD,
    ToolCostAttributor,
    merge_tool_cost_snapshots,
    session_tool_cost_summary,
)


@pytest.fixture()
def tmp_telemetry(tmp_path, monkeypatch):
    """Point the telemetry stream at a temp dir and reap module state after."""
    import agent.session_telemetry as telemetry_mod

    monkeypatch.setattr(telemetry_mod, "_TELEMETRY_DIR_RELATIVE", tmp_path / "session_telemetry")
    yield tmp_path / "session_telemetry"
    for sid in list(telemetry_mod._handles):
        fh = telemetry_mod._handles.pop(sid, None)
        if fh:
            try:
                fh.close()
            except Exception:  # swallow-ok: fixture teardown, handle may already be closed
                pass
    telemetry_mod._locks.clear()
    telemetry_mod._last_event_monotonic.clear()
    telemetry_mod._event_counts.clear()
    telemetry_mod._truncated.clear()


def _assistant(tool_names: list[str], usage: dict | None = None) -> dict:
    """A stream-json `assistant` event invoking *tool_names*."""
    content: list[dict] = [{"type": "text", "text": "thinking"}]
    content += [
        {"type": "tool_use", "id": f"tu_{i}", "name": n, "input": {}}
        for i, n in enumerate(tool_names)
    ]
    message: dict = {"role": "assistant", "content": content}
    if usage is not None:
        message["usage"] = usage
    return {"type": "assistant", "message": message}


def _usage(inp: int = 0, cache_create: int = 0, cache_read: int = 0, out: int = 0) -> dict:
    return {
        "input_tokens": inp,
        "cache_creation_input_tokens": cache_create,
        "cache_read_input_tokens": cache_read,
        "output_tokens": out,
    }


class TestAttributionArithmetic:
    def test_empty_stream_snapshot_is_definitive(self):
        snap = ToolCostAttributor().snapshot()
        assert snap["method"] == ATTRIBUTION_METHOD
        assert snap["per_tool"] == {}
        assert snap["tool_call_count"] == 0
        assert snap["dropped_samples"] == 0
        assert snap["initial_context_tokens"] is None
        assert snap["tool_surface_size"] is None

    def test_init_event_records_tool_surface_size(self):
        a = ToolCostAttributor()
        a.observe_event(
            {"type": "system", "subtype": "init", "tools": ["Bash", "Read", "Edit", "Glob"]}
        )
        assert a.snapshot()["tool_surface_size"] == 4

    def test_input_growth_is_attributed_to_the_preceding_tool(self):
        """The tokens a tool result adds to the prefix land on that tool."""
        a = ToolCostAttributor()
        # Assistant calls Bash with a 10_000-token prefix.
        a.observe_event(_assistant(["Bash"], _usage(cache_read=10_000, out=100)))
        # Prefix grew by 5_000 — the Bash result. Assistant now calls Read.
        a.observe_event(_assistant(["Read"], _usage(cache_read=15_000, out=40)))
        # Prefix grew by only 200 — Read returned something small.
        a.observe_event(_assistant([], _usage(cache_read=15_200, out=10)))

        per_tool = a.snapshot()["per_tool"]
        assert per_tool["Bash"]["input_tokens"] == 5_000
        assert per_tool["Read"]["input_tokens"] == 200
        # The output tokens spent emitting each tool_use block land on it too.
        assert per_tool["Bash"]["output_tokens"] == 100
        assert per_tool["Read"]["output_tokens"] == 40
        assert per_tool["Bash"]["total_tokens"] == 5_100
        assert per_tool["Read"]["total_tokens"] == 240
        # Ranking — the whole point of the approximation.
        assert per_tool["Bash"]["total_tokens"] > per_tool["Read"]["total_tokens"]

    def test_input_delta_splits_evenly_across_concurrent_calls(self):
        a = ToolCostAttributor()
        a.observe_event(_assistant(["Bash", "Read"], _usage(cache_read=1_000, out=60)))
        a.observe_event(_assistant([], _usage(cache_read=1_900, out=0)))

        per_tool = a.snapshot()["per_tool"]
        assert per_tool["Bash"]["input_tokens"] + per_tool["Read"]["input_tokens"] == 900
        assert per_tool["Bash"]["output_tokens"] + per_tool["Read"]["output_tokens"] == 60
        assert abs(per_tool["Bash"]["input_tokens"] - per_tool["Read"]["input_tokens"]) <= 1

    def test_odd_split_loses_no_tokens(self):
        """Integer remainder is handed to the first tool, never dropped."""
        a = ToolCostAttributor()
        a.observe_event(_assistant(["A", "B", "C"], _usage(cache_read=100, out=0)))
        a.observe_event(_assistant([], _usage(cache_read=200, out=0)))
        per_tool = a.snapshot()["per_tool"]
        assert sum(t["input_tokens"] for t in per_tool.values()) == 100

    def test_repeated_calls_accumulate_and_count(self):
        a = ToolCostAttributor()
        a.observe_event(_assistant(["Bash"], _usage(cache_read=100, out=10)))
        a.observe_event(_assistant(["Bash"], _usage(cache_read=300, out=10)))
        a.observe_event(_assistant([], _usage(cache_read=500, out=0)))
        snap = a.snapshot()
        assert snap["per_tool"]["Bash"]["calls"] == 2
        assert snap["per_tool"]["Bash"]["input_tokens"] == 400
        assert snap["tool_call_count"] == 2

    def test_shrinking_prefix_never_attributes_negative_tokens(self):
        """Compaction or cache eviction shrinks the prefix — clamp, don't invert."""
        a = ToolCostAttributor()
        a.observe_event(_assistant(["Bash"], _usage(cache_read=50_000, out=0)))
        a.observe_event(_assistant([], _usage(cache_read=2_000, out=0)))
        assert a.snapshot()["per_tool"]["Bash"]["input_tokens"] == 0

    def test_cache_creation_and_read_are_summed_across_the_boundary(self):
        """A cache-write reading and a cache-read reading describe one prefix."""
        a = ToolCostAttributor()
        a.observe_event(_assistant(["Bash"], _usage(inp=5, cache_create=9_995, out=0)))
        a.observe_event(_assistant([], _usage(inp=5, cache_read=9_995, out=0)))
        assert a.snapshot()["per_tool"]["Bash"]["input_tokens"] == 0

    def test_initial_context_tokens_is_the_first_prefix_seen(self):
        a = ToolCostAttributor()
        a.observe_event(_assistant(["Bash"], _usage(inp=12, cache_create=30_000, out=5)))
        a.observe_event(_assistant([], _usage(cache_read=31_000, out=5)))
        assert a.snapshot()["initial_context_tokens"] == 30_012

    def test_assistant_event_without_usage_is_tolerated(self):
        a = ToolCostAttributor()
        a.observe_event(_assistant(["Bash"]))
        snap = a.snapshot()
        assert snap["tool_call_count"] == 1
        assert snap["per_tool"]["Bash"]["calls"] == 1
        assert snap["dropped_samples"] == 0

    def test_unnamed_tool_block_gets_a_stable_label(self):
        a = ToolCostAttributor()
        a.observe_event(
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "x"}]}}
        )
        assert "<unnamed>" in a.snapshot()["per_tool"]

    def test_non_assistant_events_are_ignored(self):
        a = ToolCostAttributor()
        for ev in (
            {"type": "result", "usage": _usage(cache_read=99_999)},
            {"type": "stream_event", "event": {"type": "content_block_delta"}},
            {"type": "user", "message": {"content": [{"type": "tool_result"}]}},
        ):
            a.observe_event(ev)
        assert a.snapshot()["per_tool"] == {}


class TestFailQuiet:
    @pytest.mark.parametrize("bad", [["not", "a", "dict"], "a bare string", 42, None])
    def test_malformed_event_warns_and_drops_the_sample(self, bad, caplog):
        a = ToolCostAttributor()
        with caplog.at_level(logging.WARNING, logger="agent.tool_cost_attribution"):
            a.observe_event(bad)
        assert any("tool-cost" in r.message.lower() for r in caplog.records), (
            "a dropped attribution sample must log a WARNING"
        )
        assert a.snapshot()["dropped_samples"] == 1

    def test_attributor_keeps_working_after_a_dropped_sample(self, caplog):
        """One bad event must not poison the rest of the stream."""
        a = ToolCostAttributor()
        with caplog.at_level(logging.WARNING, logger="agent.tool_cost_attribution"):
            a.observe_event(_assistant(["Bash"], _usage(cache_read=1_000, out=10)))
            a.observe_event("garbage")
            a.observe_event(_assistant([], _usage(cache_read=1_500, out=0)))
        snap = a.snapshot()
        assert snap["dropped_samples"] == 1
        assert snap["per_tool"]["Bash"]["input_tokens"] == 500

    def test_snapshot_never_raises_on_corrupt_internal_state(self):
        a = ToolCostAttributor()
        a._per_tool = None  # simulate impossible corruption
        snap = a.snapshot()
        assert snap["per_tool"] == {}
        assert snap["dropped_samples"] >= 1


class TestMergeSnapshots:
    def test_merge_sums_per_tool_and_counters(self):
        a = ToolCostAttributor()
        a.observe_event(_assistant(["Bash"], _usage(cache_read=100, out=10)))
        a.observe_event(_assistant([], _usage(cache_read=400, out=0)))

        b = ToolCostAttributor()
        b.observe_event(_assistant(["Bash", "Read"], _usage(cache_read=50, out=8)))
        b.observe_event(_assistant([], _usage(cache_read=150, out=0)))

        merged = merge_tool_cost_snapshots([a.snapshot(), b.snapshot()])
        assert merged["per_tool"]["Bash"]["calls"] == 2
        assert merged["per_tool"]["Bash"]["input_tokens"] == 300 + 50
        assert merged["tool_call_count"] == 3
        assert merged["method"] == ATTRIBUTION_METHOD

    def test_merge_of_nothing_is_an_empty_snapshot(self):
        merged = merge_tool_cost_snapshots([])
        assert merged["per_tool"] == {}
        assert merged["tool_call_count"] == 0

    def test_merge_tolerates_junk_entries(self):
        """The harness merges a stored blob with a fresh snapshot; a corrupt
        stored blob must degrade to the fresh half, not crash the turn."""
        a = ToolCostAttributor()
        a.observe_event(_assistant(["Bash"], _usage(cache_read=10, out=4)))
        merged = merge_tool_cost_snapshots([{}, "junk", None, a.snapshot()])
        assert merged["per_tool"]["Bash"]["calls"] == 1


class TestSessionSummary:
    """The summary reads the cumulative snapshot the harness persists on the
    AgentSession, so a stage transition can stamp it without replaying the
    telemetry file."""

    def test_summary_from_stored_json(self):
        stored = {
            "per_tool": {
                "Bash": {
                    "calls": 3,
                    "input_tokens": 900,
                    "output_tokens": 100,
                    "total_tokens": 1000,
                },
                "Read": {"calls": 1, "input_tokens": 40, "output_tokens": 10, "total_tokens": 50},
            },
            "tool_call_count": 4,
        }
        summary = session_tool_cost_summary(SimpleNamespace(tool_cost_json=json.dumps(stored)))
        assert summary["tool_calls"] == 4
        assert summary["attributed_tokens"] == 1050
        assert summary["top_tools"][0] == ["Bash", 1000]

    def test_top_tools_are_ranked_and_capped(self):
        stored = {
            "per_tool": {
                name: {"calls": 1, "input_tokens": t, "output_tokens": 0, "total_tokens": t}
                for name, t in [("A", 10), ("B", 500), ("C", 50), ("D", 5000)]
            },
            "tool_call_count": 4,
        }
        summary = session_tool_cost_summary(SimpleNamespace(tool_cost_json=json.dumps(stored)))
        assert [name for name, _ in summary["top_tools"]] == ["D", "B", "C"]

    @pytest.mark.parametrize(
        "session",
        [
            None,
            SimpleNamespace(),
            SimpleNamespace(tool_cost_json=None),
            SimpleNamespace(tool_cost_json=""),
            SimpleNamespace(tool_cost_json="{not json"),
        ],
    )
    def test_summary_is_none_when_absent_or_unreadable(self, session):
        """An absent or unreadable snapshot is an absence, not a zeroed row."""
        assert session_tool_cost_summary(session) is None
