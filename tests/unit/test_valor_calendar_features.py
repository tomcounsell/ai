"""Tests for the feature-keyed, day-bounded, client-facing calendar redesign.

Covers: trivial-prompt gating, feature-key derivation (coalescing), jargon-
stripped client-facing naming, day-boundary clamping, multi-day rejection,
event create/extend/coalesce, display-name caching, rate-limiting, and the
run_hook entry-point gating. No network: the Google service is faked.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import tools.valor_calendar as vc


def dt(y=2026, mo=6, d=30, h=10, mi=0, s=0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolate_caches(tmp_path, monkeypatch):
    """Redirect every on-disk cache/queue into a temp dir for each test."""
    monkeypatch.setattr(vc, "EVENT_ID_CACHE_PATH", tmp_path / "event_ids.json")
    monkeypatch.setattr(vc, "NAME_CACHE_PATH", tmp_path / "names.json")
    monkeypatch.setattr(vc, "STAMP_CACHE_PATH", tmp_path / "stamps.json")
    monkeypatch.setattr(vc, "QUEUE_PATH", tmp_path / "queue.jsonl")
    yield


class FakeEvents:
    def __init__(self, store):
        self.store = store

    def _exec(self, value):
        class _R:
            def execute(self):
                return value

        return _R()

    def get(self, calendarId, eventId):  # noqa: N803 (mirror Google API kwargs)
        return self._exec(self.store["by_id"].get(eventId))

    def list(self, **kwargs):
        return self._exec({"items": list(self.store["by_id"].values())})

    def insert(self, calendarId, body):  # noqa: N803 (mirror Google API kwargs)
        eid = f"evt{len(self.store['by_id']) + 1}"
        event = {"id": eid, "status": "confirmed", **body}
        self.store["by_id"][eid] = event
        self.store["inserts"].append(event)
        return self._exec(event)

    def patch(self, calendarId, eventId, body):  # noqa: N803 (mirror Google API kwargs)
        event = self.store["by_id"][eventId]
        event["end"] = body["end"]
        self.store["patches"].append(event)
        return self._exec(event)


class FakeService:
    """Minimal stand-in for the googleapiclient calendar service."""

    def __init__(self, seed_events=None):
        self.store = {"by_id": {}, "inserts": [], "patches": []}
        for ev in seed_events or []:
            self.store["by_id"][ev["id"]] = ev

    def events(self):
        return FakeEvents(self.store)


def _event(eid, summary, start, end):
    return {
        "id": eid,
        "status": "confirmed",
        "summary": summary,
        "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
    }


class TestIsTrivialPrompt:
    @pytest.mark.parametrize(
        "prompt",
        ["", "  ", "ok", "OK", "thanks", "do it", "continue", "yes", "k", "go", "hi"],
    )
    def test_trivial(self, prompt):
        assert vc.is_trivial_prompt(prompt) is True

    @pytest.mark.parametrize(
        "prompt",
        [
            "implement the appointment reminder feature",
            "add a member export button to the dashboard",
            "investigate why checkout is slow",
        ],
    )
    def test_substantive(self, prompt):
        assert vc.is_trivial_prompt(prompt) is False


class TestDeriveFeatureKey:
    def test_branch_strips_session_prefix(self):
        assert (
            vc.derive_feature_key("/x", env={}, branch="session/member-export") == "member-export"
        )

    @pytest.mark.parametrize(
        "prefix", ["feature/", "feat/", "fix/", "bugfix/", "chore/", "hotfix/"]
    )
    def test_branch_strips_known_prefixes(self, prefix):
        assert vc.derive_feature_key("/x", env={}, branch=f"{prefix}foo-bar") == "foo-bar"

    @pytest.mark.parametrize("trunk", ["main", "master", "develop", "trunk", "HEAD", ""])
    def test_trunk_yields_no_key(self, trunk):
        assert vc.derive_feature_key("/x", env={}, branch=trunk) is None

    def test_task_list_slug_used_when_on_trunk(self):
        env = {"CLAUDE_CODE_TASK_LIST_ID": "faster-checkout"}
        assert vc.derive_feature_key("/x", env=env, branch="main") == "faster-checkout"

    def test_thread_scoped_task_list_ignored(self):
        env = {"CLAUDE_CODE_TASK_LIST_ID": "thread-123-456"}
        assert vc.derive_feature_key("/x", env=env, branch="main") is None

    def test_branch_wins_over_task_list(self):
        env = {"CLAUDE_CODE_TASK_LIST_ID": "other"}
        assert vc.derive_feature_key("/x", env=env, branch="session/the-branch") == "the-branch"


class TestCleanFeatureName:
    def test_strips_technical_jargon(self):
        assert "sdlc" not in vc.clean_feature_name("sdlc-driver-work")
        assert "prompt" not in vc.clean_feature_name("prompt-tuning-appointment-reminders")

    def test_strips_pure_number_tokens(self):
        assert vc.clean_feature_name("issue-574-member-export") == "member-export"

    def test_never_returns_empty(self):
        # All-jargon input must still yield a non-empty name (fallback).
        assert vc.clean_feature_name("sdlc-prompt-hook") != ""

    def test_truncates_to_60(self):
        assert len(vc.clean_feature_name("word-" * 40)) <= 60


class TestDayBoundaries:
    def test_segment_clamped_to_end_of_day(self):
        start, end = vc.current_segment(dt(h=23, mi=55))
        assert end == vc.end_of_day(dt(h=23, mi=55))
        assert end.day == 30  # never spills into July 1

    def test_starts_today_true_for_today(self):
        ev = _event("e", "x", dt(h=9), dt(h=9, mi=20))
        assert vc._starts_today(ev, dt(h=10)) is True

    def test_starts_today_false_for_prior_day(self):
        ev = _event("e", "x", dt(d=28, h=9), dt(d=30, h=16))  # multi-day block
        assert vc._starts_today(ev, dt(d=30, h=10)) is False


class TestProcessCalendarEvent:
    def test_creates_event_titled_with_display_name(self):
        svc = FakeService()
        msg = vc.process_calendar_event(svc, "cal", "member-export", "Member Export", dt(h=10))
        assert "Created" in msg
        assert svc.store["inserts"][0]["summary"] == "Member Export"

    def test_coalesces_same_feature_key_into_one_event(self):
        svc = FakeService()
        # Two different prompts, same feature key, later in the day -> extend, not create.
        vc.process_calendar_event(svc, "cal", "member-export", "Member Export", dt(h=10))
        vc.process_calendar_event(svc, "cal", "member-export", "Member Export", dt(h=11))
        assert len(svc.store["inserts"]) == 1
        assert len(svc.store["patches"]) == 1

    def test_does_not_extend_prior_day_event(self):
        # A multi-day block from a prior day must NOT be matched/extended.
        seed = _event("old", "Member Export", dt(d=28, h=9), dt(d=30, h=16))
        svc = FakeService(seed_events=[seed])
        vc.process_calendar_event(svc, "cal", "member-export", "Member Export", dt(d=30, h=10))
        # A fresh event is created instead of extending the stale multi-day one.
        assert len(svc.store["inserts"]) == 1
        assert svc.store["patches"] == []

    def test_extension_never_crosses_midnight(self):
        svc = FakeService()
        vc.process_calendar_event(svc, "cal", "k", "Name", dt(h=23, mi=50))
        inserted = svc.store["inserts"][0]
        assert inserted["end"]["dateTime"].startswith("2026-06-30T23:59:59")


class TestResolveDisplayName:
    def test_tracked_work_is_deterministic_and_cached(self, monkeypatch):
        # from_prompt=False must never call Haiku.
        monkeypatch.setattr(
            vc, "_haiku_feature_name", lambda p: (_ for _ in ()).throw(AssertionError("called"))
        )
        name = vc.resolve_display_name("redis-durability-hardening", "", dt(), from_prompt=False)
        assert name == "durability-hardening"
        # Second call returns the cached value.
        assert (
            vc.resolve_display_name("redis-durability-hardening", "", dt(), from_prompt=False)
            == name
        )

    def test_ad_hoc_falls_back_when_haiku_unavailable(self, monkeypatch):
        monkeypatch.setattr(vc, "_haiku_feature_name", lambda p: None)
        name = vc.resolve_display_name(
            "valor", "add appointment reminders for clients", dt(), from_prompt=True
        )
        assert name  # non-empty deterministic fallback
        assert "appointment" in name

    def test_ad_hoc_uses_haiku_when_available(self, monkeypatch):
        monkeypatch.setattr(vc, "_haiku_feature_name", lambda p: "appointment-reminders")
        name = vc.resolve_display_name("valor", "wire up the reminder cron", dt(), from_prompt=True)
        assert name == "appointment-reminders"


class TestHookRateLimit:
    def test_first_fire_not_limited_then_limited(self):
        assert vc._hook_rate_limited("k", dt(h=10, mi=0)) is False
        assert vc._hook_rate_limited("k", dt(h=10, mi=5)) is True  # within 10 min

    def test_window_expiry_allows_again(self):
        assert vc._hook_rate_limited("k", dt(h=10, mi=0)) is False
        assert vc._hook_rate_limited("k", dt(h=10, mi=20)) is False  # past window


class TestRunHook:
    def _stdin(self, monkeypatch, payload):
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(payload))

    def test_trivial_prompt_skipped(self, monkeypatch):
        called = {"service": False}
        monkeypatch.setattr(
            vc, "get_calendar_id", lambda *a, **k: called.__setitem__("service", True)
        )
        self._stdin(monkeypatch, '{"prompt": "thanks", "cwd": "/x"}')
        vc.run_hook("prompt")
        assert called["service"] is False  # bailed before any resolution

    def test_unmapped_project_skipped(self, monkeypatch):
        self._stdin(monkeypatch, '{"prompt": "do the real feature work here now", "cwd": "/x"}')
        monkeypatch.setattr("config.project_key_resolver.resolve_project_key", lambda **k: "valor")
        # valor not in the (empty) calendar config -> no calendar id -> skip
        monkeypatch.setattr(vc, "load_calendar_config", lambda: {"calendars": {}})
        sentinel = {"built": False}
        monkeypatch.setattr(
            "tools.google_workspace.auth.get_service",
            lambda *a, **k: sentinel.__setitem__("built", True),
        )
        vc.run_hook("prompt")
        assert sentinel["built"] is False

    def test_mapped_project_logs_event(self, monkeypatch):
        self._stdin(
            monkeypatch,
            '{"prompt": "build the member export feature", "cwd": "/x"}',
        )
        monkeypatch.setattr("config.project_key_resolver.resolve_project_key", lambda **k: "cyndra")
        monkeypatch.setattr(vc, "load_calendar_config", lambda: {"calendars": {"cyndra": "cal-id"}})
        monkeypatch.setattr(vc, "derive_feature_key", lambda *a, **k: "member-export")
        monkeypatch.setattr(vc, "git_branch", lambda cwd: "session/member-export")
        svc = FakeService()
        monkeypatch.setattr("tools.google_workspace.auth.get_service", lambda *a, **k: svc)
        vc.run_hook("prompt")
        assert len(svc.store["inserts"]) == 1
        assert svc.store["inserts"][0]["summary"] == "member-export"
