"""The escalation gate — the only function before a customer-facing side effect.

Given a Tier 1 ``Triage``, ``decide()`` returns a ``Disposition``:

- ``escalate`` for: low confidence (< threshold), any escalation signal, the
  ``raise_to_human`` lane, or any category whose Tier 2 tool whitelist is empty
  (the *structural* gate — a lane with no safe tool cannot auto-handle by
  construction).
- ``auto`` otherwise.

``draft`` is decided downstream by Tier 2 (an invalid/absent tool name from the
action agent yields ``draft_for_human``); the gate itself only chooses between
``auto`` and ``escalate``. This keeps the gate a pure, side-effect-free function
that is trivial to test exhaustively.

The whitelist lookup is imported from ``agents.py`` via the lightweight
``category_has_tools`` helper, which does NOT import the Anthropic SDK — so the
gate stays cheap and importable without API dependencies.
"""

from __future__ import annotations

import logging

from .schema import CONFIDENCE_THRESHOLD, Category, Disposition, Triage
from .tools import category_has_tools

logger = logging.getLogger(__name__)


def decide(triage: Triage, threshold: float = CONFIDENCE_THRESHOLD) -> Disposition:
    """Decide the disposition for a Tier 1 verdict.

    Args:
        triage: The validated Tier 1 verdict.
        threshold: Minimum confidence for an auto disposition. Defaults to
            ``CONFIDENCE_THRESHOLD`` (0.75).

    Returns:
        ``Disposition.ESCALATE`` for any unsafe condition, else
        ``Disposition.AUTO``.
    """
    # (1) Explicit raise_to_human lane.
    if triage.category == Category.RAISE_TO_HUMAN:
        logger.info("[email_cs.gate] escalate: raise_to_human lane")
        return Disposition.ESCALATE

    # (2) Any escalation signal forces escalate regardless of category/confidence.
    if triage.has_escalation_signal:
        logger.info(f"[email_cs.gate] escalate: signal={triage.escalation_signal}")
        return Disposition.ESCALATE

    # (3) Low confidence forces escalate.
    if triage.confidence < threshold:
        logger.info(
            f"[email_cs.gate] escalate: confidence {triage.confidence:.2f} < {threshold:.2f}"
        )
        return Disposition.ESCALATE

    # (4) Structural gate: a category with an empty tool whitelist cannot
    # auto-handle. There is no safe verb to call, so escalate by construction.
    if not category_has_tools(triage.category):
        logger.info(f"[email_cs.gate] escalate: empty tool whitelist for {triage.category.value}")
        return Disposition.ESCALATE

    return Disposition.AUTO
