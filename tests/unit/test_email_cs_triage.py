"""Unit tests for Tier 1 triage (tools/email_cs/triage.py).

Fail-safe contract: every error path returns an escalate Triage, never raises.
``run_typed`` (the PydanticAI wrapper, #1925) is mocked so these run offline
and deterministically.
"""

from __future__ import annotations

import os
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


# ---------------------------------------------------------------------------
# Fixture parity: ollama -> Haiku swap (#1925 patch — plan Step 4 note)
# ---------------------------------------------------------------------------
#
# These call the REAL Haiku backend (mirrors
# tests/unit/test_work_request_classifier.py::TestLlmClassification) against a
# small, hand-labeled fixture set covering all four triage lanes. Every other
# test in this file mocks run_typed for determinism -- this class is the one
# place the actual model decision is pinned, guarding against a future
# model-swap regression in triage quality.


@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="requires ANTHROPIC_API_KEY",
)
class TestTriageLocalLlmClassification:
    """Real-Haiku fixture parity for triage_local (#1925)."""

    @pytest.mark.parametrize(
        "subject, body",
        [
            (
                "Change my podcast title",
                "I'd like to rename my show from 'Daily Update' to 'Morning Brief' going forward.",
            ),
            (
                "Pause my show",
                "Can you pause my podcast feed for a few weeks while I'm on vacation?",
            ),
        ],
    )
    async def test_manage_podcast(self, subject, body):
        t = await triage_local(subject, body, "cust_1")
        assert t.category == Category.MANAGE_PODCAST, (
            f"Expected manage_podcast for: {subject!r}, got: {t.category}"
        )

    @pytest.mark.parametrize(
        "subject, body",
        [
            (
                "Regenerate episode 12",
                "Episode 12 came out wrong, can you regenerate it with the corrected script?",
            ),
            (
                "Where is my episode?",
                "I was expecting episode 5 this morning but I don't see it published "
                "yet, what's the status?",
            ),
        ],
    )
    async def test_manage_episode(self, subject, body):
        t = await triage_local(subject, body, "cust_1")
        assert t.category == Category.MANAGE_EPISODE, (
            f"Expected manage_episode for: {subject!r}, got: {t.category}"
        )

    @pytest.mark.parametrize(
        "subject, body",
        [
            (
                "Cancel my subscription",
                "I'd like to cancel my subscription at the end of this billing cycle.",
            ),
            (
                "Can't log in",
                "I'm getting an error when I try to log into my account, "
                "can you help me get access back?",
            ),
        ],
    )
    async def test_other_customer_service(self, subject, body):
        t = await triage_local(subject, body, "cust_1")
        assert t.category == Category.OTHER_CUSTOMER_SERVICE, (
            f"Expected other_customer_service for: {subject!r}, got: {t.category}"
        )

    @pytest.mark.parametrize(
        "subject, body",
        [
            (
                "This is unacceptable",
                "I've been charged three times and nobody has responded. I am "
                "extremely angry and considering canceling everything and telling "
                "everyone how bad this service is.",
            ),
            (
                "Legal notice regarding billing",
                "Our legal team will be reaching out regarding what we believe are "
                "unauthorized charges on our account, please have someone review "
                "this urgently.",
            ),
        ],
    )
    async def test_raise_to_human(self, subject, body):
        t = await triage_local(subject, body, "cust_1")
        assert t.category == Category.RAISE_TO_HUMAN, (
            f"Expected raise_to_human for: {subject!r}, got: {t.category}"
        )
