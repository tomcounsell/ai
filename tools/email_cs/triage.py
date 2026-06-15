"""Tier 1 triage — cheap local classification of every inbound customer email.

Mirrors ``reflections/memory_management.py::_gemma_classify``: a single
``ollama.chat(model=OLLAMA_CLASSIFIER_MODEL, options={"temperature": 0})`` call
with tolerant post-hoc JSON extraction (``extract_json_payload``), NOT
``format=json``. Classification runs on granite (the resident classifier).

Fail-safe contract: this function NEVER raises into the bridge. Any failure
(Ollama down, parse error, validation error, empty input, ``customer_id is
None``) deterministically returns an ``escalate`` ``Triage`` with a recorded
reason. The escalation gate (``gate.py``) is the only thing between this verdict
and a side effect.
"""

from __future__ import annotations

import json
import logging

from agent.memory_extraction import extract_json_payload
from config.models import OLLAMA_CLASSIFIER_MODEL

from .schema import Category, Triage, escalate_triage

logger = logging.getLogger(__name__)

# Body text is truncated before classification to bound latency and cost,
# matching the _gemma_classify pattern (content[:1000]). A longer window is used
# here because a CS email's intent can sit below a quoted reply chain.
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

Respond with JSON only, no preamble:
{{"category": "manage_podcast"|"manage_episode"|"other_customer_service"|"raise_to_human", \
"confidence": 0.0-1.0, "escalation_signal": "anger"|"legal"|"refund"|"identity_mismatch"|"vip"|"", \
"reason": "brief explanation"}}"""


def triage_local(subject: str, body: str, customer_id: str | None) -> Triage:
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

    try:
        from tools import ollama_client

        prompt = _TRIAGE_PROMPT.format(
            subject=(subject or "").strip()[:500],
            body=(body or "").strip()[:_MAX_BODY_CHARS],
        )
        raw = ollama_client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=OLLAMA_CLASSIFIER_MODEL,
            options={"temperature": 0},
        ).strip()
    except Exception as e:
        logger.warning(f"[email_cs.triage] Ollama classification failed, escalating: {e}")
        return escalate_triage(f"tier1 ollama failure: {e}")

    # Tolerant JSON parse — same helper the extractor uses for this model family.
    try:
        payload = extract_json_payload(raw) or raw
        data = json.loads(payload)
    except Exception as e:
        logger.warning(f"[email_cs.triage] Parse failure, escalating (raw={raw[:200]!r}): {e}")
        return escalate_triage(f"tier1 parse failure: {e}")

    # Validate into the type contract. A model that emits a bad category or an
    # out-of-range confidence escalates rather than crashing.
    try:
        triage = Triage(
            category=Category(str(data["category"])),
            confidence=float(data["confidence"]),
            escalation_signal=str(data.get("escalation_signal", "") or ""),
            reason=str(data.get("reason", "") or ""),
        )
    except Exception as e:
        logger.warning(f"[email_cs.triage] Validation failure, escalating (data={data!r}): {e}")
        return escalate_triage(f"tier1 validation failure: {e}")

    logger.info(
        f"[email_cs.triage] customer={customer_id!r} -> {triage.category.value} "
        f"conf={triage.confidence:.2f} signal={triage.escalation_signal or '-'}"
    )
    return triage
