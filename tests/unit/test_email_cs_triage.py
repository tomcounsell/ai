"""Unit tests for Tier 1 triage (tools/email_cs/triage.py).

Fail-safe contract: every error path returns an escalate Triage, never raises.
``run_typed`` (the PydanticAI wrapper, #1925) is mocked so these run offline
and deterministically.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tools.email_cs.schema import Category, Triage
from tools.email_cs.triage import EmailTriageDecision, triage_local


async def test_customer_id_none_escalates_without_model():
    # customer_id is None -> escalate before the model is ever called.
    with patch("tools.email_cs.triage.run_typed") as mock_run_typed:
        mock_run_typed.side_effect = AssertionError("run_typed must not be called")
        t = await triage_local("Subject", "Body", None)
    assert t.category == Category.RAISE_TO_HUMAN
    assert t.confidence == 0.0
    assert "customer_id is None" in t.reason


async def test_empty_subject_and_body_escalates():
    with patch("tools.email_cs.triage.run_typed") as mock_run_typed:
        mock_run_typed.side_effect = AssertionError("run_typed must not be called")
        t = await triage_local("", "", "cust_1")
    assert t.category == Category.RAISE_TO_HUMAN
    assert "empty" in t.reason.lower()


async def test_llm_failure_escalates():
    with patch(
        "tools.email_cs.triage.run_typed", new=AsyncMock(side_effect=RuntimeError("llm down"))
    ):
        t = await triage_local("Where is my episode?", "Status please", "cust_1")
    assert t.category == Category.RAISE_TO_HUMAN
    assert "llm failure" in t.reason.lower()


async def test_valid_classification_passes_through():
    decision = EmailTriageDecision(
        category=Category.MANAGE_EPISODE,
        confidence=0.92,
        escalation_signal="",
        reason="status lookup",
    )
    with patch("tools.email_cs.triage.run_typed", new=AsyncMock(return_value=decision)):
        t = await triage_local("Where is episode 3?", "Just checking status", "cust_1")
    assert isinstance(t, Triage)
    assert t.category == Category.MANAGE_EPISODE
    assert t.confidence == pytest.approx(0.92)
    assert t.escalation_signal == ""


async def test_escalation_signal_passes_through():
    decision = EmailTriageDecision(
        category=Category.OTHER_CUSTOMER_SERVICE,
        confidence=0.8,
        escalation_signal="refund",
        reason="wants a refund",
    )
    with patch("tools.email_cs.triage.run_typed", new=AsyncMock(return_value=decision)):
        t = await triage_local("refund please", "I want my money back", "cust_1")
    assert t.category == Category.OTHER_CUSTOMER_SERVICE
    assert t.escalation_signal == "refund"


async def test_unknown_escalation_signal_coerced_to_empty():
    decision = EmailTriageDecision(
        category=Category.MANAGE_PODCAST,
        confidence=0.9,
        escalation_signal="made_up_signal",
        reason="x",
    )
    with patch("tools.email_cs.triage.run_typed", new=AsyncMock(return_value=decision)):
        t = await triage_local("change my show title", "new title please", "cust_1")
    # Unknown signal is normalized away (both by EmailTriageDecision's own
    # validator and Triage's) — it should not block on a bad signal.
    assert t.escalation_signal == ""
