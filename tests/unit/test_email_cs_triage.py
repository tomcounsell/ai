"""Unit tests for Tier 1 triage (tools/email_cs/triage.py).

Fail-safe contract: every error path returns an escalate Triage, never raises.
Ollama is stubbed via monkeypatch so these run offline and deterministically.
"""

from __future__ import annotations

import json

import pytest

from tools.email_cs.schema import Category, Triage
from tools.email_cs.triage import triage_local


def _stub_ollama(monkeypatch, *, content=None, raises=None):
    """Install a fake ``ollama`` module exposing chat()."""
    import sys
    import types

    mod = types.ModuleType("ollama")

    def chat(*args, **kwargs):
        if raises is not None:
            raise raises
        return {"message": {"content": content}}

    mod.chat = chat
    monkeypatch.setitem(sys.modules, "ollama", mod)


def test_customer_id_none_escalates_without_model(monkeypatch):
    # If ollama were called it would raise; assert it is never reached.
    _stub_ollama(monkeypatch, raises=AssertionError("ollama must not be called"))
    t = triage_local("Subject", "Body", None)
    assert t.category == Category.RAISE_TO_HUMAN
    assert t.confidence == 0.0
    assert "customer_id is None" in t.reason


def test_empty_subject_and_body_escalates(monkeypatch):
    _stub_ollama(monkeypatch, raises=AssertionError("ollama must not be called"))
    t = triage_local("", "", "cust_1")
    assert t.category == Category.RAISE_TO_HUMAN
    assert "empty" in t.reason.lower()


def test_ollama_failure_escalates(monkeypatch):
    _stub_ollama(monkeypatch, raises=RuntimeError("ollama down"))
    t = triage_local("Where is my episode?", "Status please", "cust_1")
    assert t.category == Category.RAISE_TO_HUMAN
    assert "ollama failure" in t.reason


def test_parse_failure_escalates(monkeypatch):
    _stub_ollama(monkeypatch, content="this is not json at all")
    t = triage_local("hi", "body", "cust_1")
    assert t.category == Category.RAISE_TO_HUMAN
    assert "parse failure" in t.reason


def test_invalid_category_escalates(monkeypatch):
    payload = json.dumps(
        {"category": "not_a_lane", "confidence": 0.9, "escalation_signal": "", "reason": "x"}
    )
    _stub_ollama(monkeypatch, content=payload)
    t = triage_local("hi", "body", "cust_1")
    assert t.category == Category.RAISE_TO_HUMAN
    assert "validation failure" in t.reason


def test_out_of_range_confidence_escalates(monkeypatch):
    payload = json.dumps(
        {"category": "manage_episode", "confidence": 1.5, "escalation_signal": "", "reason": "x"}
    )
    _stub_ollama(monkeypatch, content=payload)
    t = triage_local("hi", "body", "cust_1")
    assert t.category == Category.RAISE_TO_HUMAN


def test_valid_classification_passes_through(monkeypatch):
    payload = json.dumps(
        {
            "category": "manage_episode",
            "confidence": 0.92,
            "escalation_signal": "",
            "reason": "status lookup",
        }
    )
    _stub_ollama(monkeypatch, content=payload)
    t = triage_local("Where is episode 3?", "Just checking status", "cust_1")
    assert isinstance(t, Triage)
    assert t.category == Category.MANAGE_EPISODE
    assert t.confidence == pytest.approx(0.92)
    assert t.escalation_signal == ""


def test_fenced_json_is_parsed(monkeypatch):
    payload = (
        "```json\n"
        + json.dumps(
            {
                "category": "other_customer_service",
                "confidence": 0.8,
                "escalation_signal": "refund",
                "reason": "wants a refund",
            }
        )
        + "\n```"
    )
    _stub_ollama(monkeypatch, content=payload)
    t = triage_local("refund please", "I want my money back", "cust_1")
    assert t.category == Category.OTHER_CUSTOMER_SERVICE
    assert t.escalation_signal == "refund"


def test_unknown_escalation_signal_coerced_to_empty(monkeypatch):
    payload = json.dumps(
        {
            "category": "manage_podcast",
            "confidence": 0.9,
            "escalation_signal": "made_up_signal",
            "reason": "x",
        }
    )
    _stub_ollama(monkeypatch, content=payload)
    t = triage_local("change my show title", "new title please", "cust_1")
    # Unknown signal is normalized away; it should not block on a bad signal.
    assert t.escalation_signal == ""
