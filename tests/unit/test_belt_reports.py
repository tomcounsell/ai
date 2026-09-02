"""The two Lane A report CLIs (plan #3081 task 1).

``tools.belt_baseline`` publishes the pre-activation measurement that task 4
gates the ``TOOLBELTS_ENFORCE`` flip on. ``tools.belt_skew_report`` is the
cross-session view of Race 3 fleet skew that ``read_session_timeline`` (one
session at a time) cannot provide.

Both are driven here through their real ``main()`` over real JSONL files on
disk — no mocks — because the thing worth testing is exactly the file reading
and folding.

Two behaviours are load-bearing and get the most attention:

* **Empty states are definitive.** An unmeasured window must SAY it is
  unmeasured. A zero baseline would make the plan's -40% context target
  trivially "met", which is the specific failure the plan's Empty/Invalid Input
  Handling section calls out.
* **The denial denominator excludes causes a belt cannot affect** (Risk 1).
  Counting them would inflate task 4's escalation ceiling and mask a belt that
  is genuinely cut too tight.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import belt_baseline, belt_skew_report


def _write_stream(telemetry_dir: Path, session_id: str, events: list[dict]) -> None:
    telemetry_dir.mkdir(parents=True, exist_ok=True)
    path = telemetry_dir / f"{session_id}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for event in events:
            event.setdefault("session_id", session_id)
            event.setdefault("ts", "2026-09-01T12:00:00.000000Z")
            fh.write(json.dumps(event) + "\n")


def _tool_cost(per_tool: dict, **extra) -> dict:
    event = {
        "type": "tool_cost",
        "method": "assistant-usage-delta/v1",
        "per_tool": per_tool,
        "tool_call_count": sum(r.get("calls", 0) for r in per_tool.values()),
        "dropped_samples": 0,
    }
    event.update(extra)
    return event


def _row(calls=1, inp=0, out=0) -> dict:
    return {
        "calls": calls,
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": inp + out,
    }


# ---------------------------------------------------------------------------
# belt_baseline
# ---------------------------------------------------------------------------


class TestBaselineEmptyStates:
    def test_missing_directory_is_a_structured_error(self, tmp_path, capsys):
        code = belt_baseline.main(["--telemetry-dir", str(tmp_path / "nope")])
        assert code == belt_baseline.EXIT_UNREADABLE
        payload = json.loads(capsys.readouterr().err)
        assert payload["error"] == "telemetry_dir_missing"

    def test_bad_since_is_a_usage_error_not_an_unreadable_stream(self, tmp_path, capsys):
        """A typo'd --since is the caller's mistake, not an unreadable telemetry
        directory. The two must not share an exit code or a script cannot tell
        "fix your flag" from "the stream is gone"."""
        telemetry = tmp_path / "session_telemetry"
        telemetry.mkdir()
        code = belt_baseline.main(["--telemetry-dir", str(telemetry), "--since", "yesterday"])
        assert code == belt_baseline.EXIT_USAGE
        assert belt_baseline.EXIT_USAGE != belt_baseline.EXIT_UNREADABLE
        assert json.loads(capsys.readouterr().err)["error"] == "bad_since"

    def test_empty_stream_says_so_instead_of_reporting_zeros(self, tmp_path, capsys):
        """The plan's Empty/Invalid Input contract, stated as a test."""
        telemetry = tmp_path / "session_telemetry"
        telemetry.mkdir()
        code = belt_baseline.main(["--telemetry-dir", str(telemetry)])
        out = capsys.readouterr().out

        assert code == belt_baseline.EXIT_NO_DATA, "an unmeasured window is not a success"
        assert "NO MEASUREMENTS FOUND" in out
        assert "not a zero baseline" in out
        # The distinguishing property: no measurement is presented as a number.
        assert "attributed tokens   0" not in out
        # Nor is task 4's ceiling baseline — an unmeasured window must not
        # publish "0 belt-relevant denials" as if that were a reading.
        assert "belt-relevant" not in out

    def test_stream_with_only_unconsumed_events_is_still_unmeasured(self, tmp_path, capsys):
        telemetry = tmp_path / "session_telemetry"
        _write_stream(telemetry, "s1", [{"type": "turn_start"}, {"type": "idle_gap"}])
        code = belt_baseline.main(["--telemetry-dir", str(telemetry)])
        out = capsys.readouterr().out
        assert code == belt_baseline.EXIT_NO_DATA
        assert "NO MEASUREMENTS FOUND" in out
        assert "belt-relevant" not in out

    def test_measured_window_with_no_denials_says_zero_is_not_a_reading(self, tmp_path, capsys):
        """A window that HAS measurements but no denial events reports a
        belt-relevant count of 0. That 0 is a real observation of the stream,
        not a ceiling baseline, so the report caveats it in words rather than
        letting task 4 read it as "no belt pressure"."""
        telemetry = tmp_path / "session_telemetry"
        _write_stream(telemetry, "s1", [_tool_cost({"Bash": _row(inp=10)})])

        assert belt_baseline.main(["--telemetry-dir", str(telemetry)]) == belt_baseline.EXIT_OK
        out = capsys.readouterr().out
        assert "PreToolUse denials: 0" in out
        assert "none recorded" in out
        assert "not necessarily no denials" in out


class TestBaselineAggregation:
    def test_folds_per_tool_cost_across_sessions_and_ranks_it(self, tmp_path, capsys):
        telemetry = tmp_path / "session_telemetry"
        _write_stream(
            telemetry,
            "s1",
            [
                _tool_cost(
                    {"Bash": _row(calls=2, inp=5_000, out=100)},
                    initial_context_tokens=40_000,
                    tool_surface_size=18,
                ),
                {"type": "token_usage", "usage": {}, "total_cost_usd": 0.5},
            ],
        )
        _write_stream(
            telemetry,
            "s2",
            [_tool_cost({"Bash": _row(calls=1, inp=1_000), "Read": _row(calls=3, inp=300)})],
        )

        summary = belt_baseline.collect_baseline(telemetry)
        assert summary["sessions_measured"] == 2
        assert summary["tool_calls"] == 6
        assert summary["per_tool"]["Bash"]["calls"] == 3
        assert summary["per_tool"]["Bash"]["total_tokens"] == 6_100
        assert summary["attributed_tokens"] == 6_100 + 300
        assert summary["initial_context_tokens_avg"] == 40_000
        assert summary["tool_surface_size_max"] == 18
        assert summary["total_cost_usd"] == pytest.approx(0.5)
        # Ranked by cost — this ordering is what sets Lane B's wrapper order.
        assert list(summary["per_tool"]) == ["Bash", "Read"]

        assert belt_baseline.main(["--telemetry-dir", str(telemetry)]) == belt_baseline.EXIT_OK
        assert "Bash" in capsys.readouterr().out

    def test_since_window_excludes_older_events(self, tmp_path):
        telemetry = tmp_path / "session_telemetry"
        _write_stream(
            telemetry,
            "s1",
            [_tool_cost({"Bash": _row(inp=100)}, ts="2000-01-01T00:00:00.000000Z")],
        )
        from datetime import UTC, datetime

        recent = belt_baseline.collect_baseline(telemetry, cutoff=datetime.now(UTC))
        assert recent["tool_calls"] == 0
        assert belt_baseline.collect_baseline(telemetry)["tool_calls"] == 1

    def test_malformed_lines_do_not_sink_the_window(self, tmp_path):
        telemetry = tmp_path / "session_telemetry"
        telemetry.mkdir(parents=True)
        (telemetry / "s1.jsonl").write_text(
            "not json at all\n"
            + json.dumps(
                {"type": "tool_cost", "per_tool": {"Bash": _row(inp=42)}, "tool_call_count": 1}
            )
            + "\n[]\n",
            encoding="utf-8",
        )
        summary = belt_baseline.collect_baseline(telemetry)
        assert summary["per_tool"]["Bash"]["total_tokens"] == 42

    def test_json_output_is_parseable(self, tmp_path, capsys):
        telemetry = tmp_path / "session_telemetry"
        _write_stream(telemetry, "s1", [_tool_cost({"Bash": _row(inp=10)})])
        belt_baseline.main(["--telemetry-dir", str(telemetry), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["tool_calls"] == 1


class TestBeltRelevantDenialSplit:
    """Risk 1: the escalation-ceiling denominator excludes causes a belt
    cannot affect. Counting them would inflate the ceiling and mask a belt cut
    too tight."""

    def test_exclusion_set_is_exactly_the_two_the_plan_authorizes(self):
        """PINNED. The plan (Risk 1, task 4, Success Criteria) authorizes two
        exclusions and no more: the sensitive-path block and, pre-Lane-B, the
        teammate write-restriction block.

        Widening this set is how ``denials_belt_relevant`` silently becomes a
        structural zero — every additional exclusion removes signal the
        escalation ceiling exists to watch. ``tool_budget`` in particular IS
        belt-affected: a narrower belt means fewer tool calls, hence fewer
        budget trips. Any widening must amend the plan first, and will fail
        here until it does."""
        assert belt_baseline.BELT_IRRELEVANT_CAUSES == frozenset(
            {"sensitive_path", "teammate_write"}
        )

    def test_split_separates_affectable_from_unaffectable_causes(self, tmp_path):
        telemetry = tmp_path / "session_telemetry"
        _write_stream(
            telemetry,
            "s1",
            [
                {"type": "pre_tool_use_denial", "cause": "sensitive_path"},
                {"type": "pre_tool_use_denial", "cause": "sensitive_path"},
                {"type": "pre_tool_use_denial", "cause": "teammate_write"},
                {"type": "pre_tool_use_denial", "cause": "tool_budget"},
                {"type": "pre_tool_use_denial", "cause": "foreground_guard"},
                {"type": "pre_tool_use_denial", "cause": "missing_tool"},
                {"type": "pre_tool_use_denial", "cause": "missing_tool"},
            ],
        )
        summary = belt_baseline.collect_baseline(telemetry)
        assert summary["denials_total"] == 7
        # tool_budget + foreground_guard + 2x missing_tool are all belt-affected.
        assert summary["denials_belt_relevant"] == 4
        assert summary["denials_belt_irrelevant"] == 3
        assert (
            summary["denials_belt_relevant"] + summary["denials_belt_irrelevant"]
            == summary["denials_total"]
        )

    @pytest.mark.parametrize("cause", ["sensitive_path", "teammate_write"])
    def test_each_named_exclusion_stays_out_of_the_denominator(self, cause, tmp_path):
        telemetry = tmp_path / "session_telemetry"
        _write_stream(telemetry, "s1", [{"type": "pre_tool_use_denial", "cause": cause}])
        summary = belt_baseline.collect_baseline(telemetry)
        assert summary["denials_belt_relevant"] == 0, f"{cause} must not inflate the ceiling"

    @pytest.mark.parametrize("cause", ["tool_budget", "foreground_guard"])
    def test_budget_and_guard_denials_stay_in_the_denominator(self, cause, tmp_path):
        """A belt controls how many tools a session can reach, so it controls
        how often the spend cap and the foreground guard trip. Excluding these
        was the defect that made ``denials_belt_relevant`` structurally zero."""
        telemetry = tmp_path / "session_telemetry"
        _write_stream(telemetry, "s1", [{"type": "pre_tool_use_denial", "cause": cause}])
        summary = belt_baseline.collect_baseline(telemetry)
        assert summary["denials_belt_relevant"] == 1, f"{cause} is belt-affected"
        assert summary["denials_belt_irrelevant"] == 0

    def test_unknown_cause_counts_as_belt_relevant(self, tmp_path):
        """Fail-loud on an unrecognised cause: under-counting the denominator
        makes the ceiling too tight, which is the safe direction to be wrong."""
        telemetry = tmp_path / "session_telemetry"
        _write_stream(telemetry, "s1", [{"type": "pre_tool_use_denial"}])
        assert belt_baseline.collect_baseline(telemetry)["denials_belt_relevant"] == 1

    def test_split_is_rendered_with_its_task_4_role(self, tmp_path, capsys):
        telemetry = tmp_path / "session_telemetry"
        _write_stream(
            telemetry,
            "s1",
            [
                {"type": "pre_tool_use_denial", "cause": "sensitive_path"},
                {"type": "pre_tool_use_denial", "cause": "missing_tool"},
            ],
        )
        belt_baseline.main(["--telemetry-dir", str(telemetry)])
        out = capsys.readouterr().out
        assert "belt-relevant" in out
        assert "belt-irrelevant" in out


# ---------------------------------------------------------------------------
# belt_skew_report
# ---------------------------------------------------------------------------


def _skew(host: str, prior: str = "off", current: str = "on", **extra) -> dict:
    event = {
        "type": "belt_enforce_skew",
        "level": "WARNING",
        "host": host,
        "prior_enforce_state": prior,
        "current_enforce_state": current,
    }
    event.update(extra)
    return event


class TestSkewReportEmptyState:
    def test_help_exits_zero(self):
        """Pinned by the plan's Verification table."""
        with pytest.raises(SystemExit) as exc:
            belt_skew_report.main(["--help"])
        assert exc.value.code == 0

    def test_no_skew_events_is_stated_not_blank(self, tmp_path, capsys):
        """Task 2 has not shipped the emitter yet, so zero events is the
        expected reading — it must still produce a definitive answer."""
        telemetry = tmp_path / "session_telemetry"
        _write_stream(telemetry, "s1", [{"type": "turn_start"}, _tool_cost({"Bash": _row()})])

        code = belt_skew_report.main(["--telemetry-dir", str(telemetry)])
        out = capsys.readouterr().out
        assert code == belt_skew_report.EXIT_OK
        assert "NO SKEW EVENTS FOUND" in out
        assert out.strip(), "an empty state is never blank output"

    def test_completely_empty_directory_still_reports(self, tmp_path, capsys):
        telemetry = tmp_path / "session_telemetry"
        telemetry.mkdir()
        assert (
            belt_skew_report.main(["--telemetry-dir", str(telemetry)]) == belt_skew_report.EXIT_OK
        )
        assert "NO SKEW EVENTS FOUND" in capsys.readouterr().out

    def test_missing_directory_is_a_structured_error(self, tmp_path, capsys):
        code = belt_skew_report.main(["--telemetry-dir", str(tmp_path / "gone")])
        assert code == belt_skew_report.EXIT_UNREADABLE
        assert json.loads(capsys.readouterr().err)["error"] == "telemetry_dir_missing"


class TestSkewReportAggregation:
    def test_aggregates_across_sessions_by_host(self, tmp_path, capsys):
        """The cross-session view read_session_timeline cannot give."""
        telemetry = tmp_path / "session_telemetry"
        _write_stream(telemetry, "sess-a", [_skew("valor-cowboy"), _skew("valor-cowboy")])
        _write_stream(telemetry, "sess-b", [_skew("valor-captain")])
        _write_stream(telemetry, "sess-c", [{"type": "turn_start"}])

        summary = belt_skew_report.summarize(belt_skew_report.collect_skew_events(telemetry))
        assert summary["total_events"] == 3
        assert summary["sessions_affected"] == 2
        assert summary["hosts_affected"] == 2
        assert summary["by_host"]["valor-cowboy"] == 2
        assert summary["transitions"]["off->on"] == 3
        # Busiest session first — the operator's triage order.
        assert list(summary["by_session"])[0] == "sess-a"

        belt_skew_report.main(["--telemetry-dir", str(telemetry)])
        out = capsys.readouterr().out
        assert "valor-cowboy" in out and "sess-a" in out

    def test_session_id_falls_back_to_the_filename(self, tmp_path):
        telemetry = tmp_path / "session_telemetry"
        telemetry.mkdir(parents=True)
        (telemetry / "orphan.jsonl").write_text(
            json.dumps({"type": "belt_enforce_skew", "host": "h1"}) + "\n", encoding="utf-8"
        )
        events = belt_skew_report.collect_skew_events(telemetry)
        assert events[0]["session_id"] == "orphan"

    def test_other_event_types_are_not_counted(self, tmp_path):
        telemetry = tmp_path / "session_telemetry"
        _write_stream(
            telemetry,
            "s1",
            [
                {"type": "status_transition", "reason": "mentions belt_enforce_skew in text"},
                _skew("h1"),
            ],
        )
        assert len(belt_skew_report.collect_skew_events(telemetry)) == 1

    def test_since_window_excludes_older_events(self, tmp_path):
        from datetime import UTC, datetime

        telemetry = tmp_path / "session_telemetry"
        _write_stream(telemetry, "s1", [_skew("h1", ts="2000-01-01T00:00:00.000000Z")])
        assert belt_skew_report.collect_skew_events(telemetry) != []
        assert belt_skew_report.collect_skew_events(telemetry, cutoff=datetime.now(UTC)) == []

    def test_bad_since_is_a_structured_error(self, capsys, tmp_path):
        telemetry = tmp_path / "session_telemetry"
        telemetry.mkdir()
        code = belt_skew_report.main(["--telemetry-dir", str(telemetry), "--since", "yesterday"])
        assert code == belt_skew_report.EXIT_USAGE
        assert json.loads(capsys.readouterr().err)["error"] == "bad_since"

    def test_json_output_is_parseable(self, tmp_path, capsys):
        telemetry = tmp_path / "session_telemetry"
        _write_stream(telemetry, "s1", [_skew("h1")])
        belt_skew_report.main(["--telemetry-dir", str(telemetry), "--json"])
        assert json.loads(capsys.readouterr().out)["total_events"] == 1

    def test_full_flag_lists_every_session(self, tmp_path, capsys):
        telemetry = tmp_path / "session_telemetry"
        for i in range(belt_skew_report._COMPACT_ROW_LIMIT + 5):
            _write_stream(telemetry, f"sess-{i:03d}", [_skew("h1")])

        belt_skew_report.main(["--telemetry-dir", str(telemetry)])
        compact = capsys.readouterr().out
        assert "pass --full" in compact

        belt_skew_report.main(["--telemetry-dir", str(telemetry), "--full"])
        full = capsys.readouterr().out
        assert "pass --full" not in full
        assert full.count("sess-") >= belt_skew_report._COMPACT_ROW_LIMIT + 5


class TestBaselinePerPRNormalization:
    """The plan's headline metrics are per merged PR; the count is an explicit
    input (never inferred), so an operator cannot publish a normalization
    against the wrong window by accident."""

    def test_normalization_requires_explicit_count(self, tmp_path, capsys):
        telemetry = tmp_path / "session_telemetry"
        _write_stream(telemetry, "s1", [_tool_cost({"Bash": _row(calls=4, inp=400, out=40)})])

        code = belt_baseline.main(["--telemetry-dir", str(telemetry)])
        out = capsys.readouterr().out
        assert code == belt_baseline.EXIT_OK
        assert "Per-PR normalization skipped" in out

        code = belt_baseline.main(["--telemetry-dir", str(telemetry), "--merged-pr-count", "2"])
        out = capsys.readouterr().out
        assert code == belt_baseline.EXIT_OK
        assert "Per merged PR (over 2 PRs)" in out
        assert "tool calls / PR          2.0" in out

    def test_nonpositive_count_is_a_structured_error(self, tmp_path, capsys):
        telemetry = tmp_path / "session_telemetry"
        telemetry.mkdir()
        code = belt_baseline.main(["--telemetry-dir", str(telemetry), "--merged-pr-count", "0"])
        assert code == belt_baseline.EXIT_USAGE
        assert "bad_merged_pr_count" in capsys.readouterr().err


class TestSkewEventTypeContract:
    def test_fixture_matches_the_pinned_constant(self):
        """The report and the resolver share one event-type string through
        session_telemetry.BELT_ENFORCE_SKEW_EVENT — a rename on either side
        must fail here, not silently empty the report."""
        from agent.session_telemetry import BELT_ENFORCE_SKEW_EVENT

        assert _skew("h1")["type"] == BELT_ENFORCE_SKEW_EVENT

    def test_resolver_emission_constant_equals_report_filter_constant(self):
        """The resolver emits via belt_resolver.BELT_SKEW_EVENT_TYPE and the
        report filters via session_telemetry.BELT_ENFORCE_SKEW_EVENT — two
        names for one contract. If either side drifts, skew events silently
        vanish from the report; this pins them equal."""
        from agent.session_runner.belt_resolver import BELT_SKEW_EVENT_TYPE
        from agent.session_telemetry import BELT_ENFORCE_SKEW_EVENT

        assert BELT_SKEW_EVENT_TYPE == BELT_ENFORCE_SKEW_EVENT == "belt_enforce_skew"

    def test_last_ts_is_the_newest_event(self, tmp_path, capsys):
        telemetry = tmp_path / "session_telemetry"
        _write_stream(
            telemetry,
            "s1",
            [
                _skew("h1", ts="2026-09-02T10:00:00Z"),
                _skew("h1", ts="2026-09-02T11:00:00Z"),
            ],
        )
        belt_skew_report.main(["--telemetry-dir", str(telemetry), "--json"])
        summary = json.loads(capsys.readouterr().out)
        assert summary["by_session"]["s1"]["last_ts"] == "2026-09-02T11:00:00Z"
