"""Unit tests for the escalation gate (tools/email_cs/gate.py).

The gate is a pure function — these tests exhaustively cover the escalate
triggers and the single auto path.
"""

from __future__ import annotations

from tools.email_cs.gate import decide
from tools.email_cs.schema import CONFIDENCE_THRESHOLD, Category, Disposition, Triage


def _t(category, confidence=0.99, signal="", reason="t"):
    return Triage(category=category, confidence=confidence, escalation_signal=signal, reason=reason)


def test_raise_to_human_lane_escalates():
    assert decide(_t(Category.RAISE_TO_HUMAN)) == Disposition.ESCALATE


def test_escalation_signal_forces_escalate_even_at_high_confidence():
    t = _t(Category.MANAGE_EPISODE, confidence=1.0, signal="anger")
    assert decide(t) == Disposition.ESCALATE


def test_refund_signal_forces_escalate():
    t = _t(Category.OTHER_CUSTOMER_SERVICE, confidence=1.0, signal="refund")
    assert decide(t) == Disposition.ESCALATE


def test_low_confidence_escalates():
    t = _t(Category.MANAGE_EPISODE, confidence=CONFIDENCE_THRESHOLD - 0.01)
    assert decide(t) == Disposition.ESCALATE


def test_confidence_at_threshold_is_auto():
    t = _t(Category.MANAGE_EPISODE, confidence=CONFIDENCE_THRESHOLD)
    assert decide(t) == Disposition.AUTO


def test_high_confidence_no_signal_is_auto():
    assert decide(_t(Category.MANAGE_EPISODE, confidence=0.95)) == Disposition.AUTO


def test_other_cs_high_confidence_is_auto():
    assert decide(_t(Category.OTHER_CUSTOMER_SERVICE, confidence=0.9)) == Disposition.AUTO


def test_manage_podcast_high_confidence_is_auto():
    assert decide(_t(Category.MANAGE_PODCAST, confidence=0.9)) == Disposition.AUTO


def test_custom_threshold_respected():
    t = _t(Category.MANAGE_EPISODE, confidence=0.8)
    assert decide(t, threshold=0.85) == Disposition.ESCALATE
    assert decide(t, threshold=0.75) == Disposition.AUTO


def test_structural_gate_empty_whitelist_escalates(monkeypatch):
    # Force the whitelist lookup to report no tools for an otherwise-auto lane.
    import tools.email_cs.gate as gate_mod

    monkeypatch.setattr(gate_mod, "category_has_tools", lambda c: False)
    t = _t(Category.MANAGE_EPISODE, confidence=0.99)
    assert decide(t) == Disposition.ESCALATE
