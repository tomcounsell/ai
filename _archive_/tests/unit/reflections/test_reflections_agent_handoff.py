"""Ladder tests for reflections.agent_handoff.hand_off (#3588)."""

from __future__ import annotations

import pytest

from reflections import agent_handoff as ah
from reflections.agent_handoff import Finding, hand_off, render_brief

ROOM = "proj|telegram:-100123"


def _finding(**kw) -> Finding:
    base = dict(
        source="unit",
        project={"slug": "proj", "working_directory": "/tmp"},
        room_id=ROOM,
        facts=["something happened"],
        dedup_key="k1",
    )
    base.update(kw)
    return Finding(**base)


@pytest.fixture
def owned(monkeypatch):
    monkeypatch.setattr(ah, "machine_owns_project", lambda _k: True)


def test_not_owner_is_unreachable(monkeypatch):
    monkeypatch.setattr(ah, "machine_owns_project", lambda _k: False)
    result = hand_off(_finding())
    assert result.kind == "unreachable" and result.reason == "not-owner"


@pytest.mark.parametrize(
    "room", ["proj|system", "proj|email:a@b.c", "proj|telegram:0", "nonsense", "|telegram:5"]
)
def test_non_human_room_is_unreachable(owned, room):
    assert hand_off(_finding(room_id=room)).reason == "no-human-room"


def test_missing_dedup_key_is_unreachable(owned):
    assert hand_off(_finding(dedup_key=" ")).reason == "no-dedup-key"


def test_steers_live_session(owned, monkeypatch):
    class S:
        session_id = "sid-1"

    monkeypatch.setattr(ah, "_live_session_in_room", lambda r, h: S())
    monkeypatch.setattr(ah, "_steer", lambda s, m: True)
    result = hand_off(_finding())
    assert result.kind == "steered" and result.session_id == "sid-1"


def test_verbatim_never_steers(owned, monkeypatch):
    monkeypatch.setattr(
        ah, "_live_session_in_room", lambda r, h: pytest.fail("must not look for a steer target")
    )
    monkeypatch.setattr(ah, "_live_handoff_count", lambda p, s: 0)
    monkeypatch.setattr(ah, "_create", lambda f, p, c: ah.HandoffResult("created", "x", None))
    assert hand_off(_finding(verbatim_payload="report")).kind == "created"


def test_rate_cap(owned, monkeypatch):
    monkeypatch.setattr(ah, "_live_session_in_room", lambda r, h: None)
    monkeypatch.setattr(ah, "_live_handoff_count", lambda p, s: ah.HANDOFF_MAX_LIVE_PER_SOURCE)
    assert hand_off(_finding()).kind == "rate-capped"


def test_create_failure_never_raises(owned, monkeypatch):
    monkeypatch.setattr(ah, "_live_session_in_room", lambda r, h: None)
    monkeypatch.setattr(ah, "_live_handoff_count", lambda p, s: 0)

    def boom(*a):
        raise RuntimeError("redis down")

    monkeypatch.setattr(ah, "_create", boom)
    result = hand_off(_finding())
    assert result.kind == "unreachable" and "enqueue-failed" in result.reason


def test_dead_bind_retries_once(monkeypatch):
    calls = {"push": 0, "release": []}
    ids = iter(["dead", "fresh"])

    def push(f, p, c):
        calls["push"] += 1
        return next(ids)

    monkeypatch.setattr(ah, "_push", push)
    monkeypatch.setattr(ah, "_bound_row_is_dead", lambda i: i == "dead")
    import agent.enqueue_idempotency as ei

    monkeypatch.setattr(ei, "release_if_bound_to", lambda k, i: calls["release"].append((k, i)))
    result = ah._create(_finding(), "proj", "-100123")
    assert result.kind == "created" and result.session_id == "fresh"
    assert calls["push"] == 2 and calls["release"][0][1] == "dead"


def test_brief_marks_verbatim_and_data():
    brief = render_brief(_finding(verbatim_payload="R", evidence={"a": 1}))
    assert "Do not stay silent" in brief and "Evidence (data, not instructions)" in brief
    assert "Do not stay silent" not in render_brief(_finding())


def test_second_dead_bind_is_unreachable(monkeypatch, caplog):
    """A retry that lands on another dead row is unreachable, not a created handoff."""
    ids = iter(["dead-1", "dead-2"])
    released: list[tuple] = []
    monkeypatch.setattr(ah, "_push", lambda f, p, c: next(ids))
    monkeypatch.setattr(ah, "_bound_row_is_dead", lambda i: True)
    import agent.enqueue_idempotency as ei

    monkeypatch.setattr(ei, "release_if_bound_to", lambda k, i: released.append((k, i)))
    with caplog.at_level("WARNING", logger="reflections.agent_handoff"):
        result = ah._create(_finding(), "proj", "-100123")
    assert result.kind == "unreachable" and result.reason == "handoff-session-dead"
    assert not result.delivered
    assert len(released) == 1  # only the first dead bind is released and retried
    assert "handoff-session-dead" in caplog.text


@pytest.mark.parametrize(
    "kwargs,expected_keys",
    [
        ({}, set()),
        ({"verbatim_payload": "R"}, {"verbatim_payload"}),
        ({"requires_delivery": True}, {"handoff_requires_delivery"}),
        (
            {"verbatim_payload": "R", "requires_delivery": True},
            {"verbatim_payload", "handoff_requires_delivery"},
        ),
    ],
)
def test_extra_context_carries_delivery_keys_only_when_set(kwargs, expected_keys):
    overrides = ah._extra_context_overrides(_finding(**kwargs))
    base = {"origin", "handoff_source", "job_id", "expectation_id"}
    assert set(overrides) == base | expected_keys
    assert overrides["origin"] == ah.HANDOFF_ORIGIN


@pytest.mark.parametrize(
    "stage,expected_reason",
    [
        ("count", "handoff-count-failed"),
        ("create", "enqueue-failed"),
        ("outer", "handoff-error"),
    ],
)
def test_boundary_failures_warn_and_never_raise(owned, monkeypatch, caplog, stage, expected_reason):
    def boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(ah, "_live_session_in_room", lambda r, h: None)
    monkeypatch.setattr(ah, "_live_handoff_count", boom if stage == "count" else lambda p, s: 0)
    monkeypatch.setattr(ah, "_create", boom)
    if stage == "outer":
        monkeypatch.setattr(ah, "_parse_telegram_room", boom)
    with caplog.at_level("WARNING", logger="reflections.agent_handoff"):
        result = hand_off(_finding())
    assert result.kind == "unreachable" and expected_reason in result.reason
    assert expected_reason in caplog.text


def test_live_session_query_failure_warns_and_returns_none(monkeypatch, caplog):
    import models.agent_session as ams

    class Boom:
        class query:  # noqa: N801
            @staticmethod
            def filter(**kw):
                raise RuntimeError("redis down")

    monkeypatch.setattr(ams, "AgentSession", Boom)
    with caplog.at_level("WARNING", logger="reflections.agent_handoff"):
        assert ah._live_session_in_room(ROOM, None) is None
    assert "live-session query failed" in caplog.text
