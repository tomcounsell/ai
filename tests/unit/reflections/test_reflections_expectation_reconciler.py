"""Expectation reconciler (#2708): orphaned-lane recovery from Job expectations.

Mirrors tests/unit/reflections/test_reflections_progress_check.py's posture: every
external boundary is faked via monkeypatch; failures log-and-continue; the
shipped-work guard is the sole respawn collision guard.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import reflections.expectation_reconciler as er
from models.job import Job

pytestmark = pytest.mark.unit


def _project(key: str) -> dict:
    return {"slug": key, "working_directory": "/tmp"}


def _mint_job_with_outbound(
    rid: str, owner: str, what: str = "deliver the fix", *, age_hours: float = 2.0
) -> tuple[Job, str]:
    """A Job carrying one open outbound expectation, backdated past min-age."""
    import json

    job = Job.mint(rid, "ship it end to end")
    eid = job.add_expectation(what, direction="outbound", owner=owner)
    data = json.loads(job.goal)
    for entry in data["expectations"]:
        if entry["id"] == eid:
            entry["ts"] = (datetime.now(tz=UTC) - timedelta(hours=age_hours)).isoformat()
    job._write_goal_data(data)
    return job, eid


@pytest.fixture
def owned_project(monkeypatch):
    """Machine owns the project; jobs scoped to a scratch room."""
    key = f"test-reconcile-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(er, "machine_owns_project", lambda _k: True)
    yield key
    for j in Job.query.filter(room_id=f"{key}|telegram:1"):
        j.delete()


class _HandoffSpy:
    """Records every Finding the reconciler hands off; returns a canned result."""

    def __init__(self, kind="created"):
        self.kind = kind
        self.findings: list = []

    def __call__(self, finding):
        self.findings.append(finding)
        if self.kind in ("created", "steered"):
            return er.HandoffResult(self.kind, "sess-1", None)
        return er.HandoffResult(self.kind, None, "no-human-room")


def _evidence(kind="merged", pr=42, closes=(7,)):
    return er.ShippedEvidence(
        kind=kind, branch="session/x", pr_number=pr, closes_issues=list(closes)
    )


class _NoSubprocess:
    def __call__(self, *args, **kwargs):  # pragma: no cover - failure surface
        raise AssertionError(f"subprocess spawned on a no-op tick: {args}")


class TestAttemptsTtlFloor:
    def test_attempts_ttl_floored_at_escalation_ttl(self, monkeypatch):
        """Risk 2: max(configured, escalation_ttl) — never merely > cadence."""
        monkeypatch.setenv("EXPECTATION_ATTEMPTS_TTL_DAYS", "1")
        monkeypatch.setenv("EXPECTATION_ESCALATION_TTL_DAYS", "30")
        assert er._attempts_ttl_seconds() == er._escalation_ttl_seconds()

    def test_attempts_ttl_keeps_larger_configured_value(self, monkeypatch):
        monkeypatch.setenv("EXPECTATION_ATTEMPTS_TTL_DAYS", "60")
        monkeypatch.setenv("EXPECTATION_ESCALATION_TTL_DAYS", "30")
        assert er._attempts_ttl_seconds() == 60 * 86400


class TestNoOpTick:
    def test_zero_open_expectations_spawns_no_subprocess(self, owned_project, monkeypatch):
        monkeypatch.setattr(er.subprocess, "run", _NoSubprocess())
        result = er._reconcile_project(_project(owned_project))
        assert result["status"] == "ok"
        assert "0 job(s)" in result["summary"]

    def test_disabled_kill_switch_skips(self, owned_project, monkeypatch):
        monkeypatch.setenv("EXPECTATION_RECONCILER_ENABLED", "false")
        result = er._reconcile_project(_project(owned_project))
        assert result["status"] == "skipped"

    def test_fresh_expectation_is_left_alone(self, owned_project, monkeypatch):
        """Min-age gate: a just-spawned lane's expectation is not acted on."""
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "lane-fresh", age_hours=0.0)
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        monkeypatch.setattr(er.subprocess, "run", _NoSubprocess())
        result = er._reconcile_project(_project(owned_project))
        assert result["status"] == "ok"
        assert not any("steered" in f or "respawned" in f for f in result["findings"])

    def test_live_owner_row_blocks_action(self, owned_project, monkeypatch):
        """A row claiming life is respawn-blocking (never discharge evidence)."""
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "lane-live")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: False)
        monkeypatch.setattr(er.subprocess, "run", _NoSubprocess())
        result = er._reconcile_project(_project(owned_project))
        assert result["findings"] == []


class TestCorruptGoal:
    def test_corrupt_goal_is_a_finding_and_nothing_acts(self, owned_project, monkeypatch):
        """#2862: a flagged Job whose goal no longer decodes is retained by the
        scan root and surfaced as ``corrupt-goal``; nothing is steered,
        respawned, or written."""
        rid = f"{owned_project}|telegram:1"
        job, _eid = _mint_job_with_outbound(rid, "session/some-lane")
        corrupt_bytes = '{"versions": [{"ts": "2026-08-01T00:00:00+00:00", "author": "pm"'
        job.goal = corrupt_bytes
        job.save()

        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        monkeypatch.setattr(er, "hand_off", lambda *a, **k: pytest.fail("handed off a corrupt Job"))
        monkeypatch.setattr(
            er, "_respawn_lane", lambda *a, **k: pytest.fail("respawned from a corrupt Job")
        )
        monkeypatch.setattr(er.subprocess, "run", _NoSubprocess())

        result = er._reconcile_project(_project(owned_project))

        assert f"corrupt-goal: {job.job_id}" in result["findings"]
        fresh = Job.query.get(id=job.id, room_id=rid)
        assert fresh.goal == corrupt_bytes
        assert fresh.has_open_expectations is True


class TestShippedWorkGuard:
    def test_shipped_work_is_handed_off_never_respawned(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        job, eid = _mint_job_with_outbound(rid, "session/shipped-lane")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        monkeypatch.setattr(er, "_shipped_evidence", lambda _wd, _s: _evidence())
        spy = _HandoffSpy("steered")
        monkeypatch.setattr(er, "hand_off", spy)
        monkeypatch.setattr(
            er, "_respawn_lane", lambda *a, **k: pytest.fail("respawned shipped work")
        )
        result = er._reconcile_project(_project(owned_project))
        assert len(spy.findings) == 1
        finding = spy.findings[0]
        assert finding.room_id == rid and finding.expectation_id == eid
        assert finding.evidence["shipped_kind"] == "merged"
        assert finding.evidence["closes_issues"] == [7]
        assert any("handed-off: steered" in f for f in result["findings"])
        # Never discharged mechanically: the expectation is still open.
        fresh = Job.query.get(id=job.id, room_id=rid)
        assert any(e["id"] == eid for e in fresh.open_expectations(direction="outbound"))

    def test_closed_unmerged_pr_is_evidence_not_a_respawn(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "session/closed-lane")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        monkeypatch.setattr(
            er, "_shipped_evidence", lambda _wd, _s: _evidence("closed_unmerged", closes=())
        )
        spy = _HandoffSpy("created")
        monkeypatch.setattr(er, "hand_off", spy)
        monkeypatch.setattr(er, "_respawn_lane", lambda *a, **k: pytest.fail("respawned"))
        er._reconcile_project(_project(owned_project))
        assert spy.findings[0].evidence["shipped_kind"] == "closed_unmerged"
        assert "closed without merging" in spy.findings[0].facts[0]

    @pytest.mark.parametrize(
        "kind,count_text",
        [
            ("unreachable", "1 unreachable, 0 rate-capped"),
            ("rate-capped", "0 unreachable, 1 rate-capped"),
        ],
    )
    def test_undelivered_handoff_reports_and_leaves_expectation_retryable(
        self, owned_project, monkeypatch, kind, count_text
    ):
        """No Eng: group / no agent / rate cap: an operator finding counted in the
        summary, never a human page, no sentinel, and no exhausted annotation, so
        a later tick can retry under the cooldown."""
        rid = f"{owned_project}|telegram:1"
        job, eid = _mint_job_with_outbound(rid, "session/capped-lane")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        monkeypatch.setattr(er, "_attempts_count", lambda _j, _e: er._max_attempts())
        spy = _HandoffSpy(kind)
        monkeypatch.setattr(er, "hand_off", spy)
        result = er._reconcile_project(_project(owned_project))
        assert "0 handed off" in result["summary"] and count_text in result["summary"]
        assert any(f.startswith("handoff-unreachable") for f in result["findings"])
        assert er._escalation_exists(job.job_id, eid) is False
        fresh = Job.query.get(id=job.id, room_id=rid)
        entry = next(e for e in fresh.open_expectations(direction="outbound") if e["id"] == eid)
        assert entry.get("blocked") is None

    def test_unshipped_orphan_respawns_when_no_live_holder(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "session/dead-lane", what="deliver the migration PR")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        monkeypatch.setattr(er, "_shipped_evidence", lambda _wd, _s: None)
        monkeypatch.setattr(er.agent_handoff, "_live_session_in_room", lambda _r, _h: None)
        respawns: list[tuple] = []
        monkeypatch.setattr(
            er, "_respawn_lane", lambda pk, slug, what, jid: respawns.append((slug, what)) or True
        )
        result = er._reconcile_project(_project(owned_project))
        assert any("respawned" in f for f in result["findings"])
        assert respawns == [("dead-lane", "deliver the migration PR")]

    def test_unshipped_orphan_prefers_steering_a_live_holder(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "session/dead-lane")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        monkeypatch.setattr(er, "_shipped_evidence", lambda _wd, _s: None)
        monkeypatch.setattr(er.agent_handoff, "_live_session_in_room", lambda _r, _h: object())
        spy = _HandoffSpy("steered")
        monkeypatch.setattr(er, "hand_off", spy)
        monkeypatch.setattr(
            er, "_respawn_lane", lambda *a, **k: pytest.fail("respawned despite live holder")
        )
        result = er._reconcile_project(_project(owned_project))
        assert any("handed-off: steered" in f for f in result["findings"])
        assert len(spy.findings) == 1


class TestOwnerResolution:
    def test_placeholder_owners_have_no_lane_slug(self):
        assert er._lane_slug("dev") is None
        assert er._lane_slug("pm") is None
        assert er._lane_slug("session/dev") is None
        assert er._lane_slug("session/real-lane") == "real-lane"

    def test_shipped_evidence_picks_merged_over_closed(self, monkeypatch):
        import json
        import subprocess

        payload = [
            {"number": 1, "state": "CLOSED", "closingIssuesReferences": []},
            {"number": 2, "state": "MERGED", "closingIssuesReferences": [{"number": 9}]},
        ]

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

        monkeypatch.setattr(er.subprocess, "run", fake_run)
        ev = er._shipped_evidence("/tmp", "x")
        assert ev.kind == "merged" and ev.pr_number == 2 and ev.closes_issues == [9]
        assert ev.counts_as_shipped

    def test_closed_unmerged_does_not_count_as_shipped(self, monkeypatch):
        import json
        import subprocess

        payload = [{"number": 3, "state": "CLOSED", "closingIssuesReferences": []}]
        monkeypatch.setattr(
            er.subprocess,
            "run",
            lambda cmd, **kw: subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(payload), stderr=""
            ),
        )
        ev = er._shipped_evidence("/tmp", "x")
        assert ev.kind == "closed_unmerged" and not ev.counts_as_shipped


class TestLadderBookkeeping:
    def test_discharged_between_scan_and_act_is_a_no_op(self, owned_project, monkeypatch):
        """Race 3: re-fetch before acting; the PM's discharge wins."""
        rid = f"{owned_project}|telegram:1"
        job, eid = _mint_job_with_outbound(rid, "session/raced-lane")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        original_claim = er._cooldown_claim

        def claim_then_discharge(job_id, e):
            job.discharge_expectation(eid)
            return original_claim(job_id, e)

        monkeypatch.setattr(er, "_cooldown_claim", claim_then_discharge)
        monkeypatch.setattr(
            er, "_shipped_evidence", lambda *_a: pytest.fail("acted on a discharged expectation")
        )
        result = er._reconcile_project(_project(owned_project))
        assert not any("steered" in f or "respawned" in f for f in result["findings"])

    def test_attempt_cap_hands_off_once_then_stops(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        job, eid = _mint_job_with_outbound(rid, "session/capped-lane")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        monkeypatch.setattr(er, "_attempts_count", lambda _j, _e: er._max_attempts())
        spy = _HandoffSpy("created")
        monkeypatch.setattr(er, "hand_off", spy)
        first = er._reconcile_project(_project(owned_project))
        assert any("handed-off: created" in f for f in first["findings"])
        er._reconcile_project(_project(owned_project))
        assert len(spy.findings) == 1

    def test_per_expectation_failure_logs_and_continues(self, owned_project, monkeypatch, caplog):
        """Every except in the loop: warn and keep going, never raise."""
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "session/exploding-lane")

        def boom(_o, _p=None):
            raise RuntimeError("owner query exploded")

        monkeypatch.setattr(er, "_owner_is_gone", boom)
        with caplog.at_level(logging.WARNING):
            result = er._reconcile_project(_project(owned_project))
        assert result["status"] == "ok"
        assert any("per-expectation pass failed" in r.message for r in caplog.records)

    def test_unknown_owner_liveness_declines(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "session/unknown-lane")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: None)
        monkeypatch.setattr(er.subprocess, "run", _NoSubprocess())
        result = er._reconcile_project(_project(owned_project))
        assert any("gate-unknown: owner-liveness" in f for f in result["findings"])


class TestBlockedAnnotation:
    """#2862: the reconciler's own ``blocked`` annotation — Site A (crash-window
    repair) and Site B (fresh escalation). Never a third lifecycle state, never
    a discharge; ``open_expectations()`` still returns the row."""

    def test_blocked_row_is_skipped_with_finding_and_no_action(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        job, eid = _mint_job_with_outbound(rid, "session/blocked-lane")
        job.block_expectation(eid, code="needs_human", by="pm")
        monkeypatch.setattr(
            er, "_owner_is_gone", lambda _o, _p=None: pytest.fail("acted on a blocked row")
        )
        monkeypatch.setattr(er.subprocess, "run", _NoSubprocess())
        result = er._reconcile_project(_project(owned_project))
        assert f"blocked: {eid} needs_human" in result["findings"]

    def test_crash_window_repair_owner_gone(self, owned_project, monkeypatch):
        """Site A fires above the liveness gate: owner-gone is not required."""
        rid = f"{owned_project}|telegram:1"
        job, eid = _mint_job_with_outbound(rid, "session/crashed-lane")
        monkeypatch.setattr(er, "_escalation_exists", lambda _j, _e: True)
        monkeypatch.setattr(er, "_attempts_count", lambda _j, _e: er._max_attempts())
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        monkeypatch.setattr(er.subprocess, "run", _NoSubprocess())
        result = er._reconcile_project(_project(owned_project))
        assert f"blocked: {eid} attempts_exhausted" in result["findings"]
        fresh = Job.query.get(id=job.id, room_id=rid)
        entry = next(e for e in fresh.open_expectations(direction="outbound") if e["id"] == eid)
        assert entry["blocked"]["code"] == "attempts_exhausted"
        assert entry["blocked"]["by"] == "reconciler"

    def test_crash_window_repair_owner_alive(self, owned_project, monkeypatch):
        """Site A fires even when the owner is still alive — this is what
        distinguishes it from the liveness gate below it: the recovery
        budget was already spent regardless of whether the lane is up."""
        rid = f"{owned_project}|telegram:1"
        job, eid = _mint_job_with_outbound(rid, "session/still-alive-lane")
        monkeypatch.setattr(er, "_escalation_exists", lambda _j, _e: True)
        monkeypatch.setattr(er, "_attempts_count", lambda _j, _e: er._max_attempts())
        monkeypatch.setattr(
            er, "_owner_is_gone", lambda _o, _p=None: pytest.fail("liveness gate reached")
        )
        monkeypatch.setattr(er.subprocess, "run", _NoSubprocess())
        result = er._reconcile_project(_project(owned_project))
        assert f"blocked: {eid} attempts_exhausted" in result["findings"]
        fresh = Job.query.get(id=job.id, room_id=rid)
        entry = next(e for e in fresh.open_expectations(direction="outbound") if e["id"] == eid)
        assert entry["blocked"]["code"] == "attempts_exhausted"
        assert entry["blocked"]["by"] == "reconciler"

    def test_site_a_does_not_fire_when_escalation_exists_is_none(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "session/unknown-escalation-lane")
        monkeypatch.setattr(er, "_escalation_exists", lambda _j, _e: None)
        monkeypatch.setattr(er, "_attempts_count", lambda _j, _e: er._max_attempts())
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        result = er._reconcile_project(_project(owned_project))
        assert not any("blocked:" in f for f in result["findings"])
        assert not any("gate-unknown" in f for f in result["findings"])

    def test_site_a_does_not_fire_below_max_attempts(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "session/under-budget-lane")
        monkeypatch.setattr(er, "_escalation_exists", lambda _j, _e: True)
        monkeypatch.setattr(er, "_attempts_count", lambda _j, _e: 0)
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        result = er._reconcile_project(_project(owned_project))
        assert not any("blocked:" in f for f in result["findings"])

    def test_site_a_does_not_fire_when_attempts_count_is_none(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "session/unreadable-attempts-lane")
        monkeypatch.setattr(er, "_escalation_exists", lambda _j, _e: True)
        monkeypatch.setattr(er, "_attempts_count", lambda _j, _e: None)
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        result = er._reconcile_project(_project(owned_project))
        assert not any("blocked:" in f for f in result["findings"])
        assert not any("gate-unknown" in f for f in result["findings"])

    def test_site_a_does_not_rewrite_an_already_annotated_row(self, owned_project, monkeypatch):
        rid = f"{owned_project}|telegram:1"
        job, eid = _mint_job_with_outbound(rid, "session/already-blocked-lane")
        job.block_expectation(eid, code="attempts_exhausted", by="reconciler")
        monkeypatch.setattr(er, "_escalation_exists", lambda _j, _e: True)
        monkeypatch.setattr(er, "_attempts_count", lambda _j, _e: er._max_attempts())
        monkeypatch.setattr(
            er, "_owner_is_gone", lambda _o, _p=None: pytest.fail("acted on a blocked row")
        )
        result = er._reconcile_project(_project(owned_project))
        # The already-annotated row is caught by the earlier skip-with-finding
        # branch, not re-processed by Site A.
        assert f"blocked: {eid} attempts_exhausted" in result["findings"]
        assert result["findings"].count(f"blocked: {eid} attempts_exhausted") == 1

    def test_annotation_attributed_to_reconciler(self, owned_project, monkeypatch):
        """Site B: a fresh handoff on the attempts-cap branch writes the
        annotation, and it is attributed to the reconciler itself."""
        rid = f"{owned_project}|telegram:1"
        job, eid = _mint_job_with_outbound(rid, "session/capped-lane")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        monkeypatch.setattr(er, "_attempts_count", lambda _j, _e: er._max_attempts())
        monkeypatch.setattr(er, "hand_off", _HandoffSpy("created"))
        er._reconcile_project(_project(owned_project))
        fresh = Job.query.get(id=job.id, room_id=rid)
        entry = next(e for e in fresh.open_expectations(direction="outbound") if e["id"] == eid)
        assert entry["blocked"]["code"] == "attempts_exhausted"
        assert entry["blocked"]["by"] == "reconciler"

    def test_refused_annotation_write_still_hands_off(self, owned_project, monkeypatch):
        """Hand-off-first is load-bearing: even if the annotation write fails,
        the finding must already have gone to an agent."""
        rid = f"{owned_project}|telegram:1"
        _mint_job_with_outbound(rid, "session/capped-lane-2")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        monkeypatch.setattr(er, "_attempts_count", lambda _j, _e: er._max_attempts())
        spy = _HandoffSpy("created")
        monkeypatch.setattr(er, "hand_off", spy)
        monkeypatch.setattr(
            er,
            "_annotate_attempts_exhausted",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("annotation write exploded")),
        )
        er._reconcile_project(_project(owned_project))
        assert len(spy.findings) == 1

    def test_no_annotation_from_evidence_handoff(self, owned_project, monkeypatch):
        """The shipped-evidence site never annotates: attempts remain and the
        row is still re-steerable."""
        rid = f"{owned_project}|telegram:1"
        job, eid = _mint_job_with_outbound(rid, "session/shipped-capped-lane")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        monkeypatch.setattr(er, "_shipped_evidence", lambda _wd, _s: _evidence(pr=7))
        monkeypatch.setattr(er, "hand_off", _HandoffSpy("created"))
        er._reconcile_project(_project(owned_project))
        fresh = Job.query.get(id=job.id, room_id=rid)
        entry = next(e for e in fresh.open_expectations(direction="outbound") if e["id"] == eid)
        assert entry.get("blocked") is None

    def test_no_annotation_from_no_slug_handoff(self, owned_project, monkeypatch):
        """The no-holder/no-slug site never annotates."""
        rid = f"{owned_project}|telegram:1"
        job, eid = _mint_job_with_outbound(rid, "not-a-lane-slug")
        monkeypatch.setattr(er, "_owner_is_gone", lambda _o, _p=None: True)
        monkeypatch.setattr(er, "_shipped_evidence", lambda _wd, _s: None)
        monkeypatch.setattr(er.agent_handoff, "_live_session_in_room", lambda _r, _h: None)
        monkeypatch.setattr(er, "_lane_slug", lambda _o, _p=None: None)
        spy = _HandoffSpy("created")
        monkeypatch.setattr(er, "hand_off", spy)
        result = er._reconcile_project(_project(owned_project))
        assert any("handed-off: created" in f for f in result["findings"])
        assert "unrecorded" in spy.findings[0].facts[0]
        fresh = Job.query.get(id=job.id, room_id=rid)
        entry = next(e for e in fresh.open_expectations(direction="outbound") if e["id"] == eid)
        assert entry.get("blocked") is None


class TestDriftAdvisory:
    """#2708 Risks 1 & 4 backstop in agent/session_health.py: advisory only."""

    @pytest.fixture
    def pm_with_child(self):
        from bridge.job_router import bind_message_to_job, telegram_message_key
        from models.agent_session import AgentSession
        from models.room import room_id as make_room_id

        key = f"test-drift-{uuid.uuid4().hex[:8]}"
        chat_id = "66"
        msg_id = 3
        parent = AgentSession.create(
            session_id=f"tg_{key}_{chat_id}_{msg_id}",
            project_key=key,
            status="active",
            session_type="eng",
            chat_id=chat_id,
            message_text="x",
            working_dir="/tmp",
            created_at=datetime.now(tz=UTC),
        )
        child = AgentSession.create(
            session_id=f"{chat_id}_{uuid.uuid4().hex[:10]}",
            project_key=key,
            status="active",
            session_type="eng",
            slug="drift-lane",
            parent_agent_session_id=parent.session_id,
            message_text="y",
            working_dir="/tmp",
            created_at=datetime.now(tz=UTC),
        )
        rid = make_room_id(key, f"telegram:{chat_id}")
        job = Job.mint(rid, "ship the thing")
        message_key = telegram_message_key(chat_id, msg_id)
        bind_message_to_job(message_key, job.job_id, room_id=rid)

        yield parent, child, job

        from popoto.redis_db import POPOTO_REDIS_DB

        POPOTO_REDIS_DB.delete(f"reply:{message_key}")
        for j in Job.query.filter(room_id=rid):
            j.delete()
        parent.delete()
        child.delete()

    async def test_fires_on_uncovered_child(self, pm_with_child, caplog):
        from agent.session_health import _check_expectation_drift_advisory

        _parent, child, job = pm_with_child
        with caplog.at_level(logging.WARNING):
            flagged = await _check_expectation_drift_advisory()
        assert flagged >= 1
        messages = [r.getMessage() for r in caplog.records]
        assert any(
            "expectation-drift" in m and child.session_id in m and job.job_id in m for m in messages
        )

    async def test_silent_when_child_is_covered(self, pm_with_child, caplog):
        from agent.session_health import _check_expectation_drift_advisory

        _parent, child, job = pm_with_child
        job.add_expectation("lane delivers it", direction="outbound", owner=child.slug)
        with caplog.at_level(logging.WARNING):
            await _check_expectation_drift_advisory()
        assert not any(
            "expectation-drift" in m and child.session_id in m
            for m in (r.getMessage() for r in caplog.records)
        )

    async def test_advisory_makes_no_writes(self, pm_with_child):
        from agent.session_health import _check_expectation_drift_advisory

        _parent, _child, job = pm_with_child
        before = job.goal
        await _check_expectation_drift_advisory()
        fresh = Job.query.get(id=job.id, room_id=job.room_id)
        assert fresh.goal == before
