"""Unit tests for the daily-log aggregator (#1263, inlined per #1292).

Covers the per-source collectors for AgentSession, TelegramMessage, Memory,
crash_tracker, Reflection.run_history, and date-boundary correctness. Git/gh
collectors are exercised via subprocess fakes (FakePopen) so the tests don't
depend on a live shell.

The aggregator originally lived in ``reflections/daily_report.py``; it was
inlined into ``reflections.pm_briefings.daily_log`` when the legacy
``daily-report-and-notify`` registry entry was retired (issue #1292).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

import reflections.pm_briefings.daily_log as dr


@pytest.fixture
def yesterday_utc() -> datetime:
    return datetime.now(UTC) - timedelta(days=1)


# --- Date boundary tests -----------------------------------------------------


def test_utc_day_bounds_returns_full_day(yesterday_utc):
    start, end = dr._utc_day_bounds(yesterday_utc)
    assert start.tzinfo is UTC
    assert end.tzinfo is UTC
    assert start.hour == 0 and start.minute == 0 and start.second == 0
    assert end.hour == 23 and end.minute == 59 and end.second == 59
    assert (end - start) >= timedelta(hours=23, minutes=59)


def test_iso_in_window_inclusive():
    start = datetime(2026, 5, 2, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 5, 2, 23, 59, 59, 999999, tzinfo=UTC)
    assert dr._iso_in_window("2026-05-02T00:00:00Z", start, end) is True
    assert dr._iso_in_window("2026-05-02T23:59:59Z", start, end) is True
    assert dr._iso_in_window("2026-05-03T00:00:00Z", start, end) is False
    assert dr._iso_in_window("2026-05-01T23:59:59Z", start, end) is False
    assert dr._iso_in_window("", start, end) is False
    assert dr._iso_in_window("not-a-date", start, end) is False


# --- AgentSession collector --------------------------------------------------


def test_collect_sessions_filters_to_target_date(yesterday_utc):
    """AgentSession query yields only sessions completed on the target UTC day."""
    from models.agent_session import AgentSession

    target_str = yesterday_utc.strftime("%Y-%m-%d")
    yesterday_noon = datetime(
        yesterday_utc.year, yesterday_utc.month, yesterday_utc.day, 12, 0, 0, tzinfo=UTC
    )
    two_days_ago = yesterday_noon - timedelta(days=1)

    on_target = AgentSession.create(
        session_type="dev",
        project_key=f"daily-report-test-{target_str}-on",
        status="completed",
        completed_at=yesterday_noon,
        turn_count=3,
        total_cost_usd=0.42,
        pr_url="https://example.test/pr/1",
        issue_url="https://example.test/issue/1",
    )
    off_target = AgentSession.create(
        session_type="dev",
        project_key=f"daily-report-test-{target_str}-off",
        status="completed",
        completed_at=two_days_ago,
    )
    try:
        sessions, err = dr._collect_sessions(yesterday_utc)
        assert err is None
        ids = {s["session_id"] for s in sessions}
        assert on_target.agent_session_id in ids, "in-window session must appear"
        assert off_target.agent_session_id not in ids, "out-of-window session must be excluded"
        # Verify carried fields
        on_dict = next(s for s in sessions if s["session_id"] == on_target.agent_session_id)
        assert on_dict["pr_url"] == "https://example.test/pr/1"
        assert on_dict["issue_url"] == "https://example.test/issue/1"
        assert on_dict["turn_count"] == 3
        assert on_dict["total_cost_usd"] == pytest.approx(0.42)
    finally:
        on_target.delete()
        off_target.delete()


# --- TelegramMessage collector -----------------------------------------------


def test_collect_telegram_decisions_filters_by_classification(yesterday_utc):
    """Decision-bearing classifications pass; ack/null classifications are filtered."""
    from models.telegram import TelegramMessage

    target_str = yesterday_utc.strftime("%Y-%m-%d")
    chat_id = f"-test-daily-{target_str}"
    yesterday_ts = datetime(
        yesterday_utc.year, yesterday_utc.month, yesterday_utc.day, 12, 0, 0, tzinfo=UTC
    ).timestamp()

    decision_msg = TelegramMessage.create(
        chat_id=chat_id,
        message_id=f"daily-test-1-{target_str}",
        direction="in",
        sender="tester",
        content="Let's ship the new pipeline today.",
        timestamp=yesterday_ts,
        message_type="text",
        classification_type="decision",
        classification_confidence=0.9,
    )
    ack_msg = TelegramMessage.create(
        chat_id=chat_id,
        message_id=f"daily-test-2-{target_str}",
        direction="in",
        sender="tester",
        content="ok",
        timestamp=yesterday_ts,
        message_type="text",
        classification_type="acknowledgment",
        classification_confidence=0.9,
    )
    low_conf_msg = TelegramMessage.create(
        chat_id=chat_id,
        message_id=f"daily-test-3-{target_str}",
        direction="in",
        sender="tester",
        content="maybe a decision",
        timestamp=yesterday_ts,
        message_type="text",
        classification_type="decision",
        classification_confidence=0.2,
    )
    try:
        items, err = dr._collect_telegram_decisions(yesterday_utc)
        assert err is None
        contents = [it["content"] for it in items if it.get("chat_id") == chat_id]
        assert any("ship the new pipeline" in c for c in contents)
        assert not any(c == "ok" for c in contents)
        assert not any("maybe a decision" in c for c in contents)
    finally:
        decision_msg.delete()
        ack_msg.delete()
        low_conf_msg.delete()


# --- Memory collector --------------------------------------------------------


def test_collect_memories_filters_by_outcome_history_ts(yesterday_utc):
    """Memory date is inferred from metadata.outcome_history[0].ts; categories
    must be in {decision, correction, surprise}."""
    from models.memory import Memory

    target_str = yesterday_utc.strftime("%Y-%m-%d")
    yesterday_ts = datetime(
        yesterday_utc.year, yesterday_utc.month, yesterday_utc.day, 12, 0, 0, tzinfo=UTC
    ).timestamp()
    older_ts = yesterday_ts - 7 * 86400

    on_target = Memory.create(
        project_key=f"daily-report-test-{target_str}",
        content="A correction observed yesterday",
        importance=4.0,
        source="agent",
        metadata={
            "category": "correction",
            "outcome_history": [{"ts": yesterday_ts, "kind": "test"}],
        },
    )
    off_target = Memory.create(
        project_key=f"daily-report-test-{target_str}",
        content="An old correction",
        importance=4.0,
        source="agent",
        metadata={
            "category": "correction",
            "outcome_history": [{"ts": older_ts, "kind": "test"}],
        },
    )
    pattern_mem = Memory.create(
        project_key=f"daily-report-test-{target_str}",
        content="A pattern (filtered out by category)",
        importance=1.0,
        source="agent",
        metadata={
            "category": "pattern",
            "outcome_history": [{"ts": yesterday_ts, "kind": "test"}],
        },
    )
    try:
        mems, err = dr._collect_memories(yesterday_utc)
        assert err is None
        ids = {m["memory_id"] for m in mems}
        assert on_target.memory_id in ids
        assert off_target.memory_id not in ids
        assert pattern_mem.memory_id not in ids, "patterns must be filtered out"
    finally:
        on_target.delete()
        off_target.delete()
        pattern_mem.delete()


# --- Reflection.run_history collector ----------------------------------------


def test_collect_reflection_runs_filters_by_run_history_ts(yesterday_utc):
    """Reflection runs are matched on the run_history entry's timestamp."""
    from models.reflection import Reflection

    target_str = yesterday_utc.strftime("%Y-%m-%d")
    yesterday_ts = datetime(
        yesterday_utc.year, yesterday_utc.month, yesterday_utc.day, 12, 0, 0, tzinfo=UTC
    ).timestamp()
    older_ts = yesterday_ts - 7 * 86400

    refl = Reflection.get_or_create(name=f"daily-report-test-{target_str}")
    refl.run_history = [
        {"timestamp": yesterday_ts, "status": "success", "duration": 1.5, "error": None},
        {"timestamp": older_ts, "status": "success", "duration": 2.0, "error": None},
    ]
    refl.save()

    try:
        items, err = dr._collect_reflection_runs(yesterday_utc)
        assert err is None
        matched = [it for it in items if it["name"] == refl.name]
        assert len(matched) == 1, "only the in-window run should match"
        assert matched[0]["status"] == "success"
        assert matched[0]["duration"] == pytest.approx(1.5)
    finally:
        refl.delete()


# --- Crash collector ---------------------------------------------------------


def test_collect_crashes_filters_by_window(yesterday_utc):
    """Crashes outside the target UTC day are filtered out."""
    from monitoring.crash_tracker import CrashEvent

    yesterday_ts = datetime(
        yesterday_utc.year, yesterday_utc.month, yesterday_utc.day, 12, 0, 0, tzinfo=UTC
    ).timestamp()
    older_ts = yesterday_ts - 7 * 86400

    fake_events = [
        CrashEvent(
            timestamp=yesterday_ts,
            event_type="crash",
            commit_sha="abc12345",
            commit_age_seconds=10,
            reason="boom",
        ),
        CrashEvent(
            timestamp=older_ts,
            event_type="crash",
            commit_sha="bbb12345",
            commit_age_seconds=10,
            reason="old",
        ),
        CrashEvent(
            timestamp=yesterday_ts, event_type="start", commit_sha="ccc12345", commit_age_seconds=10
        ),
    ]

    with patch("monitoring.crash_tracker.get_recent_events", return_value=fake_events):
        items, err = dr._collect_crashes(yesterday_utc)
    assert err is None
    assert len(items) == 1
    assert items[0]["commit_sha"] == "abc12345"
    assert items[0]["reason"] == "boom"


# --- Git collector (subprocess fake) -----------------------------------------


def _fake_subprocess(stdout: str, returncode: int = 0):
    class FakeResult:
        def __init__(self, sout, code):
            self.stdout = sout
            self.stderr = ""
            self.returncode = code

    def fn(cmd, **kwargs):
        return FakeResult(stdout, returncode)

    return fn


def test_collect_git_for_project_parses_pretty_format(tmp_path, yesterday_utc):
    """Git collector parses %H|%P|%an|%s and identifies merge commits."""
    project = {"slug": "fake", "working_directory": str(tmp_path)}
    fake_out = (
        "abcdef1234567890|111 222|Alice|Merge pull request #1\n"
        "1234567890abcdef|111|Bob|feat: ship the thing\n"
    )
    with patch("subprocess.run", side_effect=_fake_subprocess(fake_out)):
        items, err = dr._collect_git_for_project(project, yesterday_utc)
    assert err is None
    assert len(items) == 2
    # First commit has two parents → merge
    assert items[0]["sha"] == "abcdef123456"
    assert items[0]["author"] == "Alice"
    assert items[0]["is_merge"] is True
    # Second has one parent → not merge
    assert items[1]["is_merge"] is False
    assert items[1]["subject"] == "feat: ship the thing"


def test_collect_git_for_project_handles_missing_wd(yesterday_utc):
    """Missing working_directory returns empty list (no error)."""
    items, err = dr._collect_git_for_project({"slug": "no-wd"}, yesterday_utc)
    assert items == []
    assert err is None


# --- Aggregator wiring (smoke) -----------------------------------------------


def test_collect_day_activity_returns_dataclass_with_errors_dict(yesterday_utc):
    """Smoke-test the aggregator: returns a DayActivity even when sources empty."""
    activity = asyncio.run(dr._collect_day_activity(yesterday_utc))
    assert isinstance(activity, dr.DayActivity)
    assert activity.date_iso == yesterday_utc.strftime("%Y-%m-%d")
    assert isinstance(activity.commits, list)
    assert isinstance(activity.errors, dict)


# --- Module-level invariants -------------------------------------------------


def test_decision_bearing_classifications_matches_plan():
    """Plan default proposal: decision, correction, instruction, plan-request."""
    assert dr.DECISION_BEARING_CLASSIFICATIONS == {
        "decision",
        "correction",
        "instruction",
        "plan-request",
    }


def test_resolve_vault_path_includes_daily_logs():
    """Public verification table command relies on this string."""
    p = dr._resolve_vault_path()
    assert "daily-logs" in str(p)
    assert "AI Valor Engels System" in str(p)


def test_old_api_removed():
    """Per plan Verification table: legacy names must not exist on the module."""
    assert not hasattr(dr, "_collect_reflection_findings")
    assert not hasattr(dr, "_post_to_telegram")
