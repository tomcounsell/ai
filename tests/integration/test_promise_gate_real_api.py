"""Integration tests for ``bridge.promise_gate`` against the real Anthropic API.

Validates the LLM classifier prompt end-to-end with a live Haiku call.
Mocked unit tests verify every branch of the gate's control flow, but
they do NOT validate the prompt itself — only a real-API test can prove
the prompt's few-shot examples and forward-deferral class definition
actually steer Haiku to the right verdict on representative inputs.

Plan: docs/plans/sdlc-1219.md (issue #1219), §Step-by-Step Tasks Task 5
and §Success Criteria "Integration test exercises one bypass path
end-to-end with a real Anthropic API key".

Cost / latency: each call is ~$0.001 and ~500ms-3s. Two tests total.

Skipped automatically when ``ANTHROPIC_API_KEY`` is unset (CI without
secrets, local runs without the key file).

These tests use **fuzzy assertions** — verdict shape, action membership,
class membership when relevant — not exact-string matches on Haiku's
``reason`` field. Haiku's reasoning text is non-deterministic across
runs; the structural verdict is stable.
"""

from __future__ import annotations

import os

import pytest

from bridge.promise_gate import PromiseVerdict, evaluate_promise

pytestmark = [
    pytest.mark.integration,
    pytest.mark.sdlc,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set -- skipping real-API promise-gate test",
    ),
]


def test_forward_deferral_blocks_real_api(monkeypatch):
    """An empty forward-deferral promise → real Haiku returns BLOCK.

    Validates the LLM-first path (no mocks, no heuristic fallback) by
    feeding a canonical forward-deferral phrase and asserting the
    real classifier blocks it. This is the integration-level companion
    to the dozen unit tests that cover the same input via mocked
    Haiku responses.

    The fuzzy assertion bound is ``action == "block"`` plus
    ``class_ == "forward_deferral"`` — Haiku's ``reason`` field is
    free-form prose and varies across runs.
    """
    # Ensure the kill switch is NOT active for this test even if the
    # local env file disables the gate. The test's purpose is to
    # exercise the LLM path end-to-end.
    monkeypatch.setenv("PROMISE_GATE_ENABLED", "true")

    # Use a synthetic session_id so the test writes audit JSONL only
    # (no AgentSession side effects). The session_events emission
    # silently no-ops on a synthetic ID per the documented contract.
    verdict = evaluate_promise(
        "Reading the docs now, will come back with thoughts in a bit.",
        transport="telegram",
        session_id=f"cli-real-api-test-{os.getpid()}",
    )

    assert isinstance(verdict, PromiseVerdict)
    assert verdict.action == "block", (
        f"Expected real Haiku to BLOCK forward-deferral; got "
        f"action={verdict.action!r} reason={verdict.reason!r} "
        f"class_={verdict.class_!r}"
    )
    assert verdict.class_ == "forward_deferral", (
        f"Expected class_='forward_deferral' on a deferral BLOCK; got "
        f"class_={verdict.class_!r} reason={verdict.reason!r}"
    )
    assert isinstance(verdict.reason, str) and verdict.reason


def test_honest_completion_allows_real_api(monkeypatch):
    """An honest completion message with evidence → real Haiku returns ALLOW.

    The complement to the forward-deferral test. Confirms the prompt
    does not over-block — concrete actions with file-path / commit-hash
    evidence pass cleanly through the real classifier.

    Fuzzy assertion: ``action == "allow"``. Haiku's ``reason`` is
    free-form so we only check it's a non-empty string.
    """
    monkeypatch.setenv("PROMISE_GATE_ENABLED", "true")

    verdict = evaluate_promise(
        "Updated bridge/promise_gate.py to handle the empty-string "
        "env-var case. Committed abc1234. Tests pass.",
        transport="telegram",
        session_id=f"cli-real-api-test-{os.getpid()}",
    )

    assert isinstance(verdict, PromiseVerdict)
    assert verdict.action == "allow", (
        f"Expected real Haiku to ALLOW an honest completion with "
        f"evidence; got action={verdict.action!r} reason={verdict.reason!r} "
        f"class_={verdict.class_!r}"
    )
    assert isinstance(verdict.reason, str) and verdict.reason
