"""#2139 — schedulable check-in primitive.

`agent_session_scheduler checkin` lets a session register a one-shot future
Eng session (arbitrary prompt, delivered to the originating chat, at T) and
returns a citable `schedule_id` accepted by the promise gate's
scheduled-delivery patterns. These tests cover:

- time resolution (`--at` / `--in`, past, too-far, malformed),
- the `schedule_id` ↔ promise-gate contract,
- the recovery template naming the primitive,
- end-to-end `cmd_checkin` against real Redis: field shape, chat delivery
  target, `session_pickup` deferral, and the shared rate limit.
"""

from __future__ import annotations

import argparse
import io
import re
import time
import uuid
from contextlib import redirect_stdout

import pytest

pytestmark = [pytest.mark.unit]


def _args(**kw) -> argparse.Namespace:
    base = {
        "prompt": "Check whether job X finished and report to the chat",
        "at": None,
        "in_": None,
        "chat_id": None,
        "priority": None,
        "project": None,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _run_checkin(args, monkeypatch, env=None) -> dict:
    """Invoke cmd_checkin with a clean env and capture its JSON output."""
    import json

    from tools import agent_session_scheduler as sched

    env = env or {}
    for key in ("VALOR_SESSION_ID", "CHAT_ID", "PROJECT_KEY", "MESSAGE_ID", "PERSONA"):
        monkeypatch.delenv(key, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = sched.cmd_checkin(args)
    return {"rc": rc, **json.loads(buf.getvalue())}


# --- time resolution -------------------------------------------------------


class TestResolveCheckinTime:
    def test_relative_in_duration(self):
        from tools.agent_session_scheduler import _resolve_checkin_time

        ts, err = _resolve_checkin_time(_args(in_="30m"))
        assert err is None
        assert ts is not None
        assert 29 * 60 < ts - time.time() < 31 * 60

    def test_absolute_at_future(self):
        from datetime import UTC, datetime, timedelta

        from tools.agent_session_scheduler import _resolve_checkin_time

        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        ts, err = _resolve_checkin_time(_args(at=future))
        assert err is None
        assert ts is not None

    def test_past_at_rejected(self):
        from tools.agent_session_scheduler import _resolve_checkin_time

        ts, err = _resolve_checkin_time(_args(at="2000-01-01T00:00:00Z"))
        assert ts is None
        assert "past" in err.lower()

    def test_too_far_rejected(self):
        from tools.agent_session_scheduler import _resolve_checkin_time

        ts, err = _resolve_checkin_time(_args(in_="30d"))
        assert ts is None
        assert "too far" in err.lower()

    def test_malformed_in_rejected(self):
        from tools.agent_session_scheduler import _resolve_checkin_time

        ts, err = _resolve_checkin_time(_args(in_="soon"))
        assert ts is None
        assert "duration" in err.lower()

    def test_malformed_at_rejected(self):
        from tools.agent_session_scheduler import _resolve_checkin_time

        ts, err = _resolve_checkin_time(_args(at="not-a-date"))
        assert ts is None
        assert "iso" in err.lower()


# --- schedule_id ↔ promise gate contract -----------------------------------


class TestScheduleIdContract:
    def test_hex_schedule_id_matches_gate_pattern(self):
        """A `schedule_id=<hex>` citation matches _SCHEDULED_DELIVERY_PATTERNS."""
        from bridge.promise_gate import _SCHEDULED_DELIVERY_PATTERNS

        citation = f"schedule_id={uuid.uuid4().hex}"
        assert any(re.search(p, citation.lower()) for p in _SCHEDULED_DELIVERY_PATTERNS)

    def test_gate_allows_forward_deferral_that_cites_checkin(self):
        """The heuristic gate ALLOWs a forward-deferral draft citing a check-in."""
        from bridge.promise_gate import _evaluate_promise_heuristic

        schedule_id = uuid.uuid4().hex
        draft = (
            "The research job is still running. I'll report back the moment it "
            f"lands — scheduled check-in schedule_id={schedule_id}."
        )
        verdict = _evaluate_promise_heuristic(draft)
        assert verdict.action == "allow"


class TestRecoveryTemplateNamesPrimitive:
    def test_template_names_checkin_primitive(self):
        from bridge.promise_gate import PromiseVerdict, _format_recovery_template

        v = PromiseVerdict(action="block", reason="t", class_="forward_deferral")
        rendered = _format_recovery_template("I'll report back", v)
        assert "checkin" in rendered
        assert "schedule_id" in rendered

    def test_template_still_hides_bypass(self):
        from bridge.promise_gate import PromiseVerdict, _format_recovery_template

        v = PromiseVerdict(action="block", reason="t", class_="forward_deferral")
        rendered = _format_recovery_template("I'll report back", v)
        assert "PROMISE_GATE_ENABLED" not in rendered
        assert "no-promise-gate" not in rendered


# --- cmd_checkin end-to-end (real Redis) -----------------------------------

_PROJECT = "test-checkin-2139"


def _cleanup(project: str) -> None:
    from models.agent_session import AgentSession
    from models.reflection import Reflection

    for s in list(AgentSession.query.filter(project_key=project)):
        s.delete()
    # Reflection isn't indexed by project_key; match the check-in name prefix.
    for r in Reflection.get_all_states():
        if (getattr(r, "name", "") or "").startswith("scheduled-checkin-"):
            r.delete()


class TestCmdCheckin:
    def teardown_method(self):
        _cleanup(_PROJECT)

    def test_creates_scheduled_eng_session_for_chat(self, monkeypatch):
        from models.agent_session import AgentSession

        out = _run_checkin(
            _args(in_="30m", project=_PROJECT),
            monkeypatch,
            env={"CHAT_ID": "555777"},
        )
        assert out["rc"] == 0
        assert out["status"] == "scheduled"
        assert out["chat_id"] == "555777"
        # schedule_id is citable by the promise gate.
        assert out["citation"] == f"schedule_id={out['schedule_id']}"

        sessions = list(AgentSession.query.filter(project_key=_PROJECT))
        assert len(sessions) == 1
        s = sessions[0]
        assert s.session_id.startswith("checkin-")
        assert s.session_type == "eng"
        assert s.chat_id == "555777"
        assert s.message_text == "Check whether job X finished and report to the chat"
        assert s.scheduled_at is not None

    def test_scheduled_session_is_deferred_then_eligible(self, monkeypatch):
        """session_pickup skips the check-in until scheduled_at passes."""
        from datetime import UTC, datetime

        from agent.session_pickup import is_scheduled_eligible
        from models.agent_session import AgentSession

        _run_checkin(_args(in_="1h", project=_PROJECT), monkeypatch, env={"CHAT_ID": "1"})
        s = list(AgentSession.query.filter(project_key=_PROJECT))[0]

        # Not eligible now (scheduled_at in the future).
        assert is_scheduled_eligible(s) is False

        # Force the scheduled_at into the past → becomes eligible.
        s.scheduled_at = datetime.now(UTC).timestamp() - 60
        assert is_scheduled_eligible(s) is True

    def test_past_at_creates_no_session(self, monkeypatch):
        from models.agent_session import AgentSession

        out = _run_checkin(
            _args(at="2000-01-01T00:00:00Z", project=_PROJECT), monkeypatch, env={"CHAT_ID": "1"}
        )
        assert out["rc"] == 1
        assert out["status"] == "error"
        assert list(AgentSession.query.filter(project_key=_PROJECT)) == []

    def test_rate_limit_counts_checkins(self, monkeypatch):
        """The shared per-hour limit sees check-ins (parent-less) and blocks past cap."""
        import tools.agent_session_scheduler as sched

        monkeypatch.setattr(sched, "MAX_SCHEDULED_PER_HOUR", 2, raising=True)

        rcs = [
            _run_checkin(_args(in_="30m", project=_PROJECT), monkeypatch, env={"CHAT_ID": "1"})
            for _ in range(3)
        ]
        statuses = [r["status"] for r in rcs]
        assert statuses[:2] == ["scheduled", "scheduled"]
        assert statuses[2] == "error"
        assert "rate limit" in rcs[2]["message"].lower()

    def test_depth_cap_blocks(self, monkeypatch):
        import tools.agent_session_scheduler as sched
        from models.agent_session import AgentSession

        monkeypatch.setattr(sched, "_get_scheduling_depth", lambda: sched.MAX_SCHEDULING_DEPTH)
        out = _run_checkin(_args(in_="30m", project=_PROJECT), monkeypatch, env={"CHAT_ID": "1"})
        assert out["rc"] == 1
        assert "depth" in out["message"].lower()
        assert list(AgentSession.query.filter(project_key=_PROJECT)) == []
