"""Tests for tools.belt_skew_report (plan #3081, Race 3).

Pins the cross-session aggregation contract — the report globs every session
JSONL file and groups ``belt_enforce_skew`` events by session and host, the
fleet-convergence view ``read_session_timeline`` (one session at a time)
cannot provide — and the explicit no-skew empty state.
"""

from __future__ import annotations

import json

from agent.session_telemetry import BELT_ENFORCE_SKEW_EVENT
from tools import belt_skew_report


def _skew(session_id: str, host: str, prior: str, current: str, ts: str) -> dict:
    return {
        "type": BELT_ENFORCE_SKEW_EVENT,
        "session_id": session_id,
        "host": host,
        "prior_enforce_state": prior,
        "current_enforce_state": current,
        "ts": ts,
    }


def _write(tmp_path, session_id: str, events: list[dict]) -> None:
    with (tmp_path / f"{session_id}.jsonl").open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")


class TestEmptyState:
    def test_no_skew_is_stated_explicitly(self, tmp_path, capsys):
        _write(tmp_path, "sess-a", [{"type": "turn_start", "session_id": "sess-a"}])
        rc = belt_skew_report.main(["--telemetry-dir", str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == belt_skew_report.EXIT_OK
        assert "NO SKEW EVENTS FOUND" in out

    def test_missing_dir_is_unreadable(self, tmp_path, capsys):
        rc = belt_skew_report.main(["--telemetry-dir", str(tmp_path / "nope")])
        assert rc == belt_skew_report.EXIT_UNREADABLE
        assert "telemetry_dir_missing" in capsys.readouterr().err


class TestCrossSessionAggregation:
    def test_groups_by_session_and_host(self, tmp_path, capsys):
        _write(
            tmp_path,
            "sess-a",
            [
                _skew("sess-a", "cowboy", "on", "off", "2026-09-02T10:00:00Z"),
                _skew("sess-a", "cowboy", "on", "off", "2026-09-02T11:00:00Z"),
                {"type": "turn_start", "session_id": "sess-a"},
            ],
        )
        _write(
            tmp_path,
            "sess-b",
            [_skew("sess-b", "captain", "off", "on", "2026-09-02T12:00:00Z")],
        )

        rc = belt_skew_report.main(["--telemetry-dir", str(tmp_path), "--json"])
        assert rc == belt_skew_report.EXIT_OK
        summary = json.loads(capsys.readouterr().out)
        assert summary["total_events"] == 3
        assert summary["sessions_affected"] == 2
        assert summary["hosts_affected"] == 2
        assert summary["by_host"] == {"cowboy": 2, "captain": 1}
        assert summary["by_session"]["sess-a"]["count"] == 2
        assert summary["by_session"]["sess-a"]["hosts"] == {"cowboy": 2}
        assert summary["by_session"]["sess-a"]["last_ts"] == "2026-09-02T11:00:00Z"
        assert summary["by_session"]["sess-b"]["transitions"] == {"off->on": 1}

    def test_text_output_names_sessions_and_hosts(self, tmp_path, capsys):
        _write(
            tmp_path,
            "sess-a",
            [_skew("sess-a", "cowboy", "on", "off", "2026-09-02T10:00:00Z")],
        )
        rc = belt_skew_report.main(["--telemetry-dir", str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == belt_skew_report.EXIT_OK
        assert "sess-a" in out
        assert "cowboy" in out
        assert "on->off" in out

    def test_since_window_excludes_old_events(self, tmp_path, capsys):
        _write(
            tmp_path,
            "sess-old",
            [_skew("sess-old", "cowboy", "on", "off", "2020-01-01T00:00:00Z")],
        )
        rc = belt_skew_report.main(["--telemetry-dir", str(tmp_path), "--since", "1d"])
        out = capsys.readouterr().out
        assert rc == belt_skew_report.EXIT_OK
        assert "NO SKEW EVENTS FOUND" in out
