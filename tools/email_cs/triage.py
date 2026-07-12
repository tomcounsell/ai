"""Tier 1 triage — cheap classification of every inbound customer email.

Non-harness LLM call (#1925): routes through ``agent.llm.run_typed`` with a
typed ``EmailTriageDecision`` output model instead of the previous hand-rolled
Ollama-chat call + tolerant post-hoc JSON extraction
(``extract_json_payload``).

Fail-safe contract: this function NEVER raises into the bridge. Any failure
(LLM call down, schema-validation exhaustion, empty input, ``customer_id is
None``) deterministically returns an ``escalate`` ``Triage`` with a recorded
reason. The escalation gate (``gate.py``) is the only thing between this verdict
and a side effect.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field, field_validator

from agent.llm import run_typed
from config.models import MODEL_FAST

from .schema import ESCALATION_SIGNALS, Category, Triage, escalate_triage

logger = logging.getLogger(__name__)

# Body text is truncated before classification to bound latency and cost.
# A longer window is used here because a CS email's intent can sit below a
# quoted reply chain.
_MAX_BODY_CHARS = 2000

_TRIAGE_PROMPT = """You are a customer-service email triage classifier for a \
personalized podcast service. Sort the email below into exactly one lane and \
surface any escalation signal.

Lanes:
- manage_podcast: show/feed/account-level podcast changes (create show, change \
title/description/art, cadence/length/style/voice/format, pause/archive, \
distribution).
- manage_episode: single-episode requests (generate, regenerate, edit metadata, \
publish/schedule, takedown, status lookup).
- other_customer_service: account ops not tied to podcast/episode content \
(subscription status, upgrade/checkout, cancel, billing, account access, \
how-to, bug reports).
- raise_to_human: a SIGNAL, not a topic. Use only when none of the above clearly \
applies or when a signal below is present.

Escalation signals (set escalation_signal to one of these, else ""):
- anger: anger, churn threats, hostility.
- legal: legal, press, compliance, regulatory.
- refund: refund or credit requests.
- identity_mismatch: sender appears not to be the account owner.
- vip: VIP or flagged account markers.
Leave escalation_signal "" if none apply. If you are unsure of the lane, choose \
raise_to_human with low confidence.

Email subject: {subject}

Email body:
{body}

Classify the category, confidence, escalation_signal, and reason for the email \
above."""


class EmailTriageDecision(BaseModel):
    """Typed structured output for the Tier 1 email triage classifier (#1925).

    Replaces the previous hand-rolled Ollama-chat call + tolerant JSON parse
    -- PydanticAI validates this schema directly via forced tool-calling
    (with a single auto-retry on mismatch), so a bad category or an
    out-of-range confidence can no longer slip through as malformed JSON.
    """

    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    escalation_signal: str = ""
    reason: str = ""

    @field_validator("escalation_signal")
    @classmethod
    def _normalize_signal(cls, v: str) -> str:
        """Coerce unknown signals to the empty string (no signal).

        Mirrors ``Triage._normalize_signal`` — a model that emits a signal
        outside the fixed vocabulary is treated as "no signal" rather than
        crashing.
        """
        if not v:
            return ""
        v = v.strip().lower()
        return v if v in ESCALATION_SIGNALS else ""


async def triage_local(subject: str, body: str, customer_id: str | None) -> Triage:
    """Classify an inbound customer email into one of four lanes (Tier 1).

    Args:
        subject: The email subject line.
        body: The email body text.
        customer_id: The resolved customer id. ``None`` deterministically
            escalates — Tier 1 must never run for an unresolved sender.

    Returns:
        A validated ``Triage``. Fail-safe: every error path returns an
        ``escalate`` verdict (never raises, never silently auto-handles).
    """
    # customer_id is None -> escalate deterministically, never call the model.
    if customer_id is None:
        return escalate_triage("customer_id is None — sender not resolved")

    # Empty subject+body carries no signal -> escalate (low signal).
    if not (subject or "").strip() and not (body or "").strip():
        return escalate_triage("empty subject and body — no classifiable signal")

    prompt = _TRIAGE_PROMPT.format(
        subject=(subject or "").strip()[:500],
        body=(body or "").strip()[:_MAX_BODY_CHARS],
    )

    # #1925: single run_typed call replaces the Ollama-chat call + tolerant
    # JSON parse + manual Triage validation. Schema-validation exhaustion
    # (bad category, out-of-range confidence) now raises here too, so it
    # shares the same conservative escalate path as an LLM-down failure.
    try:
        decision = await run_typed(prompt, EmailTriageDecision, model=MODEL_FAST)
    except Exception as e:
        logger.warning(f"[email_cs.triage] LLM classification failed, escalating: {e}")
        return escalate_triage(f"tier1 llm failure: {e}")

    triage = Triage(
        category=decision.category,
        confidence=decision.confidence,
        escalation_signal=decision.escalation_signal,
        reason=decision.reason,
    )

    logger.info(
        f"[email_cs.triage] customer={customer_id!r} -> {triage.category.value} "
        f"conf={triage.confidence:.2f} signal={triage.escalation_signal or '-'}"
    )
    return triage
