"""The three-day assumption digest (lane 5, #3217; charter §11).

A status report, never a question: the closing line is fixed, no ``?``
appears outside a quoted assumption body, and no poll or
``AskUserQuestion`` symbol is imported. The watermark is the newest
timestamp actually rendered and moves only after a successful send.
"""

from __future__ import annotations

import inspect
import json
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from models.improvement_case import ImprovementCase
from models.improvement_controller_state import ImprovementControllerState
from models.improvement_evidence import ImprovementEvidence
from models.improvement_investigation import ImprovementInvestigation
from reflections import improvement_assumption_digest as digest

T0 = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _enabled_and_clean(monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings.improvement, "enabled", True)
    digest.drain_overruns()
    yield
    digest.drain_overruns()


def fresh_pk() -> str:
    return f"test-3217-digest-{uuid.uuid4().hex[:8]}"


class Sender:
    """An injected transport that records every message and answers ``result``."""

    def __init__(self, result: bool = True):
        self.result = result
        self.messages: list[str] = []

    def __call__(self, message: str, *, logger_prefix: str) -> bool:
        self.messages.append(message)
        return self.result


def case(pk: str, *, area="inference", title="cheaper inference", blocked_by=None):
    return ImprovementCase.create(
        project_key=pk,
        state="investigating",
        title=title,
        priority_area=area,
        created_at=T0,
        blocked_by=blocked_by,
    )


def assumption_row(
    pk: str,
    case_id: str | None,
    *,
    assumption="the free tier stays free through Q4",
    resolved_at=T0,
    overturning="a 402 from the provider on any request",
    charter_passage="§3 discover free or inexpensive inference",
    consequence="the case proceeds on the free tier",
    confidence="low",
):
    return ImprovementInvestigation.create(
        project_key=pk,
        created_at=resolved_at - timedelta(hours=1),
        kind="web_research",
        state="resolved",
        case_id=case_id,
        uncertainty="whether the tier persists",
        query="provider pricing page",
        provisional_assumption=assumption,
        assumption_detail=json.dumps(
            {
                "charter_passage": charter_passage,
                "confidence": confidence,
                "consequence": consequence,
                "overturning_observation": overturning,
            }
        ),
        resolved_at=resolved_at,
    )


def vault_request_row(pk: str, case_id: str, *, resource_name="meta_model_api"):
    return ImprovementInvestigation.create(
        project_key=pk,
        created_at=T0,
        kind="resource_acquisition",
        state="resolved",
        case_id=case_id,
        query="meta model api terms",
        claims=json.dumps(
            {
                "claims": [],
                "notes": [],
                "disposition": "vault_request_written",
                "resource_name": resource_name,
            }
        ),
        resolved_at=T0,
    )


def run(pk: str, sender: Sender, *, now=None) -> dict:
    return digest.run_improvement_assumption_digest(
        sender=sender, now=now or T0 + timedelta(days=1), project_key=pk
    )


def outside_quoted_bodies(text: str) -> str:
    """The rendered text with every double-quoted span removed."""
    return re.sub(r'"[^"]*"', "", text)


# ---------------------------------------------------------------------------
# Charter §11: a status report, not a request for direction
# ---------------------------------------------------------------------------


class TestStatusReportNotQuestion:
    def test_silence_validates_nothing(self):
        pk = fresh_pk()
        assumption_row(pk, case(pk).id)
        sender = Sender()

        result = run(pk, sender)

        assert result["status"] == "success"
        assert len(sender.messages) == 1
        assert sender.messages[0].rstrip().endswith(digest.CLOSING_LINE)
        assert digest.CLOSING_LINE == (
            "This is a status report. It asks nothing. Silence validates none of the "
            "above; each assumption stands until evidence overturns it."
        )

    def test_asks_nothing(self):
        """No ``?`` outside a quoted assumption body, even when the bodies
        themselves carry one, and no poll or question symbol imported."""
        pk = fresh_pk()
        c = case(pk, blocked_by="vault:meta_model_api")
        assumption_row(
            pk,
            c.id,
            assumption="is the free tier permanent? assume yes",
            overturning="does a 402 appear?",
            consequence="proceed on the free tier, or not?",
            charter_passage="§3 free inference?",
        )
        vault_request_row(pk, c.id)
        ImprovementEvidence.record_once(
            pk, "resource_acquired", source_ref="vault:Test key", text="Test key", detail="ab12"
        )
        digest.on_escalation(
            {"resource": "vm-1", "action": "kept_running", "forecast_usd": 4.0, "failure_mode": "x"}
        )
        sender = Sender()

        run(pk, sender)

        rendered = sender.messages[0]
        assert "?" in rendered  # the quoted bodies keep their own text
        assert "?" not in outside_quoted_bodies(rendered)

        source = inspect.getsource(digest)
        assert "AskUserQuestion" not in source
        assert "poll" not in source.lower()
        for name in dir(digest):
            assert "poll" not in name.lower()
            assert "askuserquestion" not in name.lower()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestRendering:
    def test_grouped_by_priority_area_with_the_four_assumption_fields(self):
        pk = fresh_pk()
        inference = case(pk, area="inference", title="cheap inference")
        skills = case(pk, area="skills", title="skill library")
        assumption_row(
            pk,
            inference.id,
            assumption="the free tier stays free",
            charter_passage="§3 inference",
            confidence="low",
            overturning="a 402 from the provider",
        )
        assumption_row(
            pk,
            skills.id,
            assumption="the vetted skill is safe to load",
            charter_passage="§5 skills",
            confidence="medium",
            overturning="the skill writes outside its declared surfaces",
            resolved_at=T0 + timedelta(minutes=5),
        )
        sender = Sender()

        result = run(pk, sender)

        rendered = sender.messages[0]
        assert result["counts"]["assumptions"] == 2
        assert rendered.index("[inference]") < rendered.index("[skills]")
        inference_block = rendered[rendered.index("[inference]") : rendered.index("[skills]")]
        assert '"the free tier stays free"' in inference_block
        assert 'charter: "§3 inference"' in inference_block
        assert "confidence: low" in inference_block
        assert 'overturned by: "a 402 from the provider"' in inference_block
        assert "cheap inference" in inference_block
        skills_block = rendered[rendered.index("[skills]") :]
        assert '"the vetted skill is safe to load"' in skills_block
        assert "confidence: medium" in skills_block

    def test_resource_acquired_and_overrun_render_under_their_headings(self):
        pk = fresh_pk()
        ImprovementEvidence.record_once(
            pk,
            "resource_acquired",
            source_ref="vault:Meta Model API key",
            text="Meta Model API key",
            detail="sha256:abcd",
        )
        digest.on_escalation(
            {
                "resource": "vm-esc",
                "action": "kept_running",
                "failure_mode": "returned_False",
                "forecast_usd": 4.0,
            }
        )
        sender = Sender()

        result = run(pk, sender)

        rendered = sender.messages[0]
        resources = rendered[rendered.index("Resources acquired:") :]
        assert "Meta Model API key (sha256:abcd)" in resources
        overruns = rendered[rendered.index("Infrastructure overruns:") :]
        assert "vm-esc" in overruns
        assert "kept running" in overruns
        assert "$4.00" in overruns
        assert "returned_False" in overruns
        assert result["counts"] == {
            "assumptions": 0,
            "vault_requests": 0,
            "resources_acquired": 1,
            "overruns": 1,
        }

    def test_vault_request_renders_with_its_title_and_block(self):
        pk = fresh_pk()
        c = case(pk, title="meta models", blocked_by="vault:meta_model_api")
        vault_request_row(pk, c.id)
        sender = Sender()

        result = run(pk, sender)

        rendered = sender.messages[0]
        section = rendered[rendered.index("Vault requests:") :]
        assert "Meta Model API key" in section
        assert "meta models" in section
        assert "vault:meta_model_api" in section
        assert result["counts"]["vault_requests"] == 1

    def test_empty_digest_sends_nothing_and_reports_success(self):
        sender = Sender()

        result = run(fresh_pk(), sender)

        assert sender.messages == []
        assert result["status"] == "success"
        assert result["counts"] == {
            "assumptions": 0,
            "vault_requests": 0,
            "resources_acquired": 0,
            "overruns": 0,
        }
        assert result["findings"] == []

    def test_disabled_setting_skips_without_reading(self, monkeypatch):
        from config.settings import settings

        monkeypatch.setattr(settings.improvement, "enabled", False)
        pk = fresh_pk()
        assumption_row(pk, case(pk).id)
        sender = Sender()

        result = run(pk, sender)

        assert result["status"] == "skipped"
        assert sender.messages == []
        assert ImprovementControllerState.get(pk) is None


# ---------------------------------------------------------------------------
# The watermark (Race 4) and the failed-send path
# ---------------------------------------------------------------------------


class TestWatermark:
    def test_failed_send_keeps_the_watermark_and_the_next_run_resends(self):
        pk = fresh_pk()
        c = case(pk)
        assumption_row(pk, c.id, assumption="first assumption", resolved_at=T0)
        digest.on_escalation({"resource": "vm-w", "action": "continues", "forecast_usd": 1.0})
        failing = Sender(result=False)

        first = run(pk, failing)

        assert first["findings"] == ["digest-not-delivered"]
        assert first["status"] == "error"
        state = ImprovementControllerState.get(pk)
        assert state is None or state.digest_watermark is None

        working = Sender()
        second = run(pk, working)

        assert second["status"] == "success"
        assert second["findings"] == []
        assert '"first assumption"' in working.messages[0]
        assert "vm-w" in working.messages[0]
        assert ImprovementControllerState.get(pk).digest_watermark == T0.isoformat()

    def test_watermark_is_the_newest_rendered_resolved_at_not_now(self):
        pk = fresh_pk()
        c = case(pk)
        assumption_row(pk, c.id, assumption="older", resolved_at=T0)
        newest = T0 + timedelta(hours=2)
        assumption_row(pk, c.id, assumption="newer", resolved_at=newest)
        sender = Sender()

        run(pk, sender, now=T0 + timedelta(days=3))

        assert ImprovementControllerState.get(pk).digest_watermark == newest.isoformat()

        # A row resolved after the read but before the send is newer than the
        # watermark, so the next digest carries it and nothing older.
        assumption_row(pk, c.id, assumption="late", resolved_at=newest + timedelta(seconds=1))
        run(pk, sender, now=T0 + timedelta(days=6))

        assert len(sender.messages) == 2
        assert '"late"' in sender.messages[1]
        assert '"older"' not in sender.messages[1]
        assert '"newer"' not in sender.messages[1]

    def test_overruns_drain_only_after_a_successful_send(self):
        pk = fresh_pk()
        digest.on_escalation({"resource": "vm-d", "action": "continues", "forecast_usd": 2.0})
        assert run(pk, Sender(result=False))["findings"] == ["digest-not-delivered"]
        assert len(digest.pending_overruns()) == 1

        assert run(pk, Sender())["counts"]["overruns"] == 1
        assert digest.pending_overruns() == []
        assert run(pk, Sender())["counts"]["overruns"] == 0


# ---------------------------------------------------------------------------
# The optional sink on lane 7's teardown ladder
# ---------------------------------------------------------------------------


class TestEscalationSink:
    def test_apply_teardown_feeds_the_next_digest(self):
        from tools.infrastructure_budget import apply_teardown

        pk = fresh_pk()
        apply_teardown(
            [{"name": "vm-sink", "window_key": "2026-W37"}],
            open_sessions=[],
            export_verifier=lambda resource: False,
            destroy=lambda resource: True,
            continuation_forecast_usd=4.0,
            next_window_key="2026-W38",
            project_key=pk,
            on_escalation=digest.on_escalation,
        )
        sender = Sender()

        result = run(pk, sender)

        assert result["counts"]["overruns"] == 1
        section = sender.messages[0][sender.messages[0].index("Infrastructure overruns:") :]
        assert "vm-sink" in section
        assert "kept running" in section
        assert "returned_False" in section
        assert digest.pending_overruns() == []
