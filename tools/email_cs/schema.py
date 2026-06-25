"""Type contract for the email customer-service triage layer.

Single source of truth for the four triage lanes (``Category``), the three
dispositions (``Disposition``), the validated Tier 1 verdict (``Triage``), the
confidence threshold, and the escalation-signal vocabulary.

The escalation-signal set is fixed up front (anger/churn/threats,
legal/press/compliance, refund/credit mentions, identity mismatch, low
confidence, VIP markers) and surfaced by Tier 1 via the ``escalation_signal``
field. Any signal forces ``raise_to_human`` at the gate regardless of category
or confidence (see ``gate.py``).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

# Confidence threshold below which the gate forces escalate. Issue placeholder
# (0.75); tunable against shadow-mode verdict logs before any auto-send.
CONFIDENCE_THRESHOLD: float = 0.75

# The fixed escalation-signal vocabulary. Tier 1 must emit one of these (or an
# empty string for "no signal"). Any non-empty signal forces escalate.
ESCALATION_SIGNALS: frozenset[str] = frozenset(
    {
        "anger",  # anger / churn / threats
        "legal",  # legal / press / compliance
        "refund",  # refund / credit mentions
        "identity_mismatch",  # sender identity does not match the account
        "low_confidence",  # classifier is unsure
        "vip",  # VIP / flagged account
    }
)


class Category(StrEnum):
    """The four triage lanes. ``raise_to_human`` is an override, not a topic."""

    MANAGE_PODCAST = "manage_podcast"
    MANAGE_EPISODE = "manage_episode"
    OTHER_CUSTOMER_SERVICE = "other_customer_service"
    RAISE_TO_HUMAN = "raise_to_human"


class Disposition(StrEnum):
    """What the gate decides to do with a triaged email."""

    AUTO = "auto"  # safe tool + high confidence -> Tier 2 action agent
    DRAFT = "draft"  # a tool exists but the action needs human review
    ESCALATE = "escalate"  # route to a human; never auto-send


class Triage(BaseModel):
    """A validated Tier 1 verdict.

    ``category`` is one of the four lanes. ``confidence`` is in [0, 1].
    ``escalation_signal`` is one of ``ESCALATION_SIGNALS`` or "" (no signal).
    ``reason`` is a short human-readable explanation for the audit trail.
    """

    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    escalation_signal: str = ""
    reason: str = ""

    @field_validator("escalation_signal")
    @classmethod
    def _normalize_signal(cls, v: str) -> str:
        """Coerce unknown signals to the empty string (no signal).

        A Tier 1 model that emits a signal outside the fixed vocabulary is
        treated as "no signal" rather than crashing — the gate still escalates
        on low confidence / empty body, so a malformed signal never weakens the
        safety posture. ``None`` is coerced to "".
        """
        if not v:
            return ""
        v = v.strip().lower()
        return v if v in ESCALATION_SIGNALS else ""

    @property
    def has_escalation_signal(self) -> bool:
        """True if Tier 1 surfaced any escalation signal."""
        return bool(self.escalation_signal)


def escalate_triage(reason: str, *, signal: str = "low_confidence") -> Triage:
    """Build a deterministic escalate verdict.

    Used by Tier 1 fail-safe paths (Ollama failure, parse failure, empty input,
    ``customer_id is None``) so every failure resolves to ``raise_to_human`` with
    a recorded reason rather than crashing into the bridge.
    """
    return Triage(
        category=Category.RAISE_TO_HUMAN,
        confidence=0.0,
        escalation_signal=signal if signal in ESCALATION_SIGNALS else "low_confidence",
        reason=reason,
    )
