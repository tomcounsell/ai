"""Unit tests for the daily-log renderer + signal mapping (#1263, inlined per #1292).

Covers _render_day_log section ordering, empty-day handling, full-named-entity
formatting, and _activity_to_signals → builder mapping shape.

The renderer originally lived in ``reflections/daily_report.py``; it was
inlined into ``reflections.pm_briefings.daily_log`` when the legacy
``daily-report-and-notify`` registry entry was retired (issue #1292).
"""

from __future__ import annotations

from datetime import UTC, datetime

import reflections.pm_briefings.daily_log as dr


def _basic_date():
    return datetime(2026, 5, 2, tzinfo=UTC)


# --- Empty / minimal cases ---------------------------------------------------


def test_render_empty_activity_uses_no_activity_marker():
    a = dr.DayActivity(date_iso="2026-05-02")
    body = dr._render_day_log(a, _basic_date())
    assert body.startswith("# Daily Log: 2026-05-02")
    assert "No system activity recorded for 2026-05-02" in body


def test_render_with_only_errors_shows_aggregator_notes():
    a = dr.DayActivity(date_iso="2026-05-02", errors={"git:foo": "timeout after 30s"})
    body = dr._render_day_log(a, _basic_date())
    assert "## Aggregator Notes" in body
    assert "[ERROR: git:foo]" in body
    assert "timeout after 30s" in body


# --- Section ordering --------------------------------------------------------


def test_render_section_order_is_stable():
    """Sections must appear in plan-defined priority order."""
    a = dr.DayActivity(
        date_iso="2026-05-02",
        commits=[
            {
                "project": "ai",
                "sha": "abc12345",
                "author": "v",
                "subject": "feat: X",
                "is_merge": False,
            }
        ],
        prs=[{"project": "ai", "number": 1, "title": "Add Y", "state": "MERGED", "url": "u"}],
        issues=[
            {
                "project": "ai",
                "number": 2,
                "title": "Bug Z",
                "state": "CLOSED",
                "url": "u",
                "action": "closed",
            }
        ],
        sessions=[
            {
                "session_id": "sid-1",
                "session_type": "dev",
                "project_key": "ai",
                "status": "completed",
                "turn_count": 5,
                "total_cost_usd": 0.01,
            }
        ],
        telegram_decisions=[
            {
                "chat_id": -1,
                "sender": "tom",
                "content": "ship it",
                "classification_type": "decision",
                "ts": 1714608000.0,
            }
        ],
        memories=[
            {
                "memory_id": "m1",
                "category": "decision",
                "content": "decided to X",
                "project_key": "ai",
                "importance": 4.0,
            }
        ],
        crashes=[{"timestamp": 1714608000.0, "commit_sha": "deadbeef", "reason": "OOM"}],
        reflection_runs=[
            {
                "name": "fooaudit",
                "status": "success",
                "duration": 1.0,
                "error": None,
                "projects": [],
            }
        ],
    )
    body = dr._render_day_log(a, _basic_date())

    # Verify presence
    for marker in (
        "## Commits & PRs",
        "## Issues",
        "## Agent Sessions",
        "## Telegram Decisions",
        "## Memory Observations",
        "## Errors & Incidents",
        "## Reflection Findings",
    ):
        assert marker in body, f"missing {marker}"

    # Verify ordering
    indices = [
        body.index("## Commits & PRs"),
        body.index("## Issues"),
        body.index("## Agent Sessions"),
        body.index("## Telegram Decisions"),
        body.index("## Memory Observations"),
        body.index("## Errors & Incidents"),
        body.index("## Reflection Findings"),
    ]
    assert indices == sorted(indices), "sections out of priority order"


def test_render_uses_full_named_entities_not_bare_numbers():
    """Renderer must include subject lines, full URLs, and named slugs."""
    a = dr.DayActivity(
        date_iso="2026-05-02",
        prs=[
            {
                "project": "ai",
                "number": 1263,
                "title": "Daily Log Overhaul: vault archival + audio brief",
                "state": "MERGED",
                "url": "https://example.test/pr/1263",
            }
        ],
        issues=[
            {
                "project": "ai",
                "number": 1263,
                "title": "Daily Log Overhaul issue",
                "state": "CLOSED",
                "url": "https://example.test/issue/1263",
                "action": "closed",
            }
        ],
    )
    body = dr._render_day_log(a, _basic_date())
    assert "Daily Log Overhaul: vault archival + audio brief" in body
    assert "https://example.test/pr/1263" in body
    assert "Daily Log Overhaul issue" in body
    assert "https://example.test/issue/1263" in body


def test_render_groups_commits_by_project():
    a = dr.DayActivity(
        date_iso="2026-05-02",
        commits=[
            {"project": "ai", "sha": "a1", "author": "v", "subject": "msg1", "is_merge": False},
            {"project": "popoto", "sha": "b1", "author": "v", "subject": "msg2", "is_merge": True},
            {"project": "ai", "sha": "a2", "author": "v", "subject": "msg3", "is_merge": False},
        ],
    )
    body = dr._render_day_log(a, _basic_date())
    assert "### ai" in body
    assert "### popoto" in body
    # Merge marker should appear on the merge commit
    assert "[merge]" in body


def test_render_omits_empty_sections():
    a = dr.DayActivity(
        date_iso="2026-05-02",
        commits=[
            {"project": "ai", "sha": "abc12345", "author": "v", "subject": "S", "is_merge": False}
        ],
    )
    body = dr._render_day_log(a, _basic_date())
    assert "## Commits & PRs" in body
    assert "## Issues" not in body
    assert "## Agent Sessions" not in body
    assert "## Telegram Decisions" not in body


# --- _activity_to_signals mapping --------------------------------------------


def test_activity_to_signals_maps_categories_correctly():
    a = dr.DayActivity(
        date_iso="2026-05-02",
        commits=[
            {"project": "ai", "sha": "a1", "author": "v", "subject": "feat", "is_merge": False},
            {"project": "ai", "sha": "a2", "author": "v", "subject": "merge", "is_merge": True},
        ],
        prs=[{"project": "ai", "number": 1, "title": "Title", "state": "MERGED", "url": "u"}],
        issues=[
            {
                "project": "ai",
                "number": 2,
                "title": "I",
                "state": "OPEN",
                "url": "u",
                "action": "opened",
            }
        ],
        crashes=[{"timestamp": 1, "commit_sha": "deadbeef", "reason": "OOM"}],
        telegram_decisions=[
            {
                "chat_id": -1,
                "sender": "tom",
                "content": "Ship it",
                "classification_type": "decision",
                "ts": 1.0,
            }
        ],
    )
    signals = dr._activity_to_signals(a)
    assert "merges" in signals and signals["merges"][0]["pr_number"] == 1
    # Only non-merge commit goes to "commits"
    assert "commits" in signals and len(signals["commits"]) == 1
    assert signals["commits"][0]["subject"] == "feat"
    assert "issues" in signals and signals["issues"][0]["number"] == 2
    assert "incidents" in signals and signals["incidents"][0]["subject"] == "OOM"
    assert "decisions" in signals


def test_activity_to_signals_empty_activity_returns_empty_dict():
    assert dr._activity_to_signals(dr.DayActivity(date_iso="2026-05-02")) == {}


# Note: target-chat selection (formerly _select_target_chat_id) was removed
# from this module when the legacy `daily_report.run()` orchestration was
# retired (issue #1292). Per-project chat resolution now lives in the
# dispatcher (`reflections.pm_briefings.delivery`); see
# `tests/unit/reflections/test_pm_briefings_delivery.py`.
