"""Tests for tools.belt_baseline (plan #3081, Lane A).

Pins the two contracts the plan calls out explicitly:

* Aggregation is cross-session: the report folds ``tool_cost``,
  ``token_usage``, and ``pre_tool_use_denial`` events across EVERY session
  JSONL file, which ``read_session_timeline`` (one session at a time) cannot
  provide.
* The empty-stream Failure Path: no measurements is reported as an absence of
  data with its own exit code — never as a zero baseline.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from tools import belt_baseline


def _write_session(tmp_path, session_id: str, events: list[dict]) -> None:
    ts = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with (tmp_path / f"{session_id}.jsonl").open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps({"session_id": session_id, "ts": ts, **event}) + "\n")


def _tool_cost(per_tool: dict, **extra) -> dict:
    calls = sum(row.get("calls", 0) for row in per_tool.values())
    return {
        "type": "tool_cost",
        "method": "assistant-usage-delta/v1",
        "per_tool": per_tool,
        "tool_call_count": calls,
        "dropped_samples": 0,
        **extra,
    }


def _row(calls, input_tokens, output_tokens) -> dict:
    return {
        "calls": calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


class TestEmptyStream:
    def test_empty_dir_reports_absence_not_zeros(self, tmp_path, capsys):
        rc = belt_baseline.main(["--telemetry-dir", str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == belt_baseline.EXIT_NO_DATA
        assert "NO MEASUREMENTS FOUND" in out
        assert "not a zero baseline" in out
        # A zero must never be presented as a measured baseline value.
        assert "Per tool" not in out
        assert "Per merged PR" not in out

    def test_missing_dir_is_unreadable(self, tmp_path, capsys):
        rc = belt_baseline.main(["--telemetry-dir", str(tmp_path / "nope")])
        assert rc == belt_baseline.EXIT_UNREADABLE
        assert "telemetry_dir_missing" in capsys.readouterr().err

    def test_empty_json_says_unmeasured(self, tmp_path, capsys):
        rc = belt_baseline.main(["--telemetry-dir", str(tmp_path), "--json"])
        assert rc == belt_baseline.EXIT_NO_DATA
        assert json.loads(capsys.readouterr().out)["measured"] is False


class TestAggregation:
    def test_aggregates_across_sessions(self, tmp_path, capsys):
        """Two session files fold into one cross-session aggregate — the view
        read_session_timeline cannot provide."""
        _write_session(
            tmp_path,
            "sess-a",
            [
                _tool_cost(
                    {"Bash": _row(3, 900, 100)},
                    initial_context_tokens=40_000,
                    tool_surface_size=18,
                ),
                {"type": "token_usage", "usage": {}, "total_cost_usd": 1.25},
                {"type": "pre_tool_use_denial", "cause": "tool_budget"},
            ],
        )
        _write_session(
            tmp_path,
            "sess-b",
            [
                _tool_cost({"Bash": _row(1, 100, 10), "Read": _row(2, 5000, 40)}),
                {"type": "pre_tool_use_denial", "cause": "tool_budget"},
                {"type": "pre_tool_use_denial", "cause": "foreground_guard"},
                # Noise the report must ignore
                {"type": "turn_start"},
                {"type": "status_transition", "from": "pending", "to": "running"},
            ],
        )

        rc = belt_baseline.main(["--telemetry-dir", str(tmp_path), "--json"])
        assert rc == belt_baseline.EXIT_OK
        summary = json.loads(capsys.readouterr().out)
        assert summary["measured"] is True
        assert summary["sessions_measured"] == 2
        assert summary["harness_turns"] == 2
        assert summary["tool_calls"] == 6
        assert summary["per_tool"]["Bash"] == _row(4, 1000, 110)
        assert summary["per_tool"]["Read"] == _row(2, 5000, 40)
        # Read outranks Bash on total tokens, so it sorts first.
        assert list(summary["per_tool"]) == ["Read", "Bash"]
        assert summary["denials"] == {"tool_budget": 2, "foreground_guard": 1}
        assert summary["denials_total"] == 3
        assert summary["total_cost_usd"] == 1.25
        assert summary["initial_context_tokens_avg"] == 40_000
        assert summary["tool_surface_size_max"] == 18

    def test_per_pr_normalization_requires_explicit_count(self, tmp_path, capsys):
        _write_session(tmp_path, "sess-a", [_tool_cost({"Bash": _row(4, 400, 40)})])

        rc = belt_baseline.main(["--telemetry-dir", str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == belt_baseline.EXIT_OK
        assert "Per-PR normalization skipped" in out

        rc = belt_baseline.main(["--telemetry-dir", str(tmp_path), "--merged-pr-count", "2"])
        out = capsys.readouterr().out
        assert rc == belt_baseline.EXIT_OK
        assert "Per merged PR (over 2 PRs)" in out
        assert "tool calls / PR          2.0" in out

    def test_since_window_excludes_old_events(self, tmp_path, capsys):
        old = {"session_id": "sess-old", "ts": "2020-01-01T00:00:00Z"}
        with (tmp_path / "sess-old.jsonl").open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({**old, **_tool_cost({"Bash": _row(1, 10, 1)})}) + "\n")

        rc = belt_baseline.main(["--telemetry-dir", str(tmp_path), "--since", "1d"])
        out = capsys.readouterr().out
        assert rc == belt_baseline.EXIT_NO_DATA
        assert "NO MEASUREMENTS FOUND" in out

    def test_malformed_lines_are_skipped(self, tmp_path, capsys):
        path = tmp_path / "sess-x.jsonl"
        good = {"session_id": "sess-x", "ts": "2026-01-01T00:00:00Z"}
        path.write_text(
            "NOT JSON AT ALL\n"
            + json.dumps({**good, **_tool_cost({"Grep": _row(1, 50, 5)})})
            + "\n"
        )
        rc = belt_baseline.main(["--telemetry-dir", str(tmp_path), "--json"])
        assert rc == belt_baseline.EXIT_OK
        assert json.loads(capsys.readouterr().out)["tool_calls"] == 1
