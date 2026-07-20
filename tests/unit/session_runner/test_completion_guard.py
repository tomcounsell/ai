"""Unit tests for the ledger-aware completion guard (issue #2158).

Pure-predicate matrix over :func:`agent.session_runner.completion_guard.
evaluate_completion` — no Redis, no subprocess, hand-built stage dicts.
"""

from __future__ import annotations

import pytest

from agent.session_runner.completion_guard import (
    MAX_COMPLETION_REFUSALS,
    evaluate_completion,
)

pytestmark = pytest.mark.sdlc


def _call(**overrides):
    """evaluate_completion with sensible non-terminal eng defaults."""
    kwargs = dict(
        session_type="eng",
        issue_number=2158,
        stage_states={"PLAN": "completed", "CRITIQUE": "completed", "BUILD": "ready"},
        blocked_reason=None,
        refusal_count=0,
        next_skill="/do-build",
        ledger_query_ok=True,
        pr_open=None,
    )
    kwargs.update(overrides)
    return evaluate_completion(**kwargs)


# -- Non-SDLC sessions are never gated -------------------------------------


@pytest.mark.parametrize("session_type", ["teammate", "TEAMMATE", None, "", "other"])
def test_non_eng_session_allowed(session_type):
    d = _call(session_type=session_type)
    assert d.allow is True
    assert d.reason == "not_sdlc"


def test_eng_without_issue_number_allowed():
    d = _call(issue_number=None)
    assert d.allow is True
    assert d.reason == "not_sdlc"


# -- Fail-open cases (concern #1 + BLOCKER + fail-open contract) ------------


def test_ledger_query_failure_fails_open():
    d = _call(ledger_query_ok=False)
    assert d.allow is True
    assert d.reason == "query_failed_fail_open"


def test_empty_ledger_fails_open():
    # query_enriched returns {"stages": {}} for a not-found / not-started
    # pipeline — a NORMAL empty return, must be fail-opened (not refused).
    d = _call(stage_states={})
    assert d.allow is True
    assert d.reason == "ledger_empty"


def test_docs_only_pr_state_unavailable_fails_open():
    # DOCS completed, MERGE not, pr_open unresolved (gh error) -> the BLOCKER
    # fix: fail open rather than escalate a genuinely-done docs-only session.
    d = _call(
        stage_states={"DOCS": "completed", "MERGE": "pending"},
        pr_open=None,
    )
    assert d.allow is True
    assert d.reason == "terminal_pr_state_unavailable_fail_open"


# -- Terminal ledgers complete normally ------------------------------------


def test_terminal_merge_completed_allowed():
    d = _call(
        stage_states={"MERGE": "completed", "DOCS": "completed", "REVIEW": "completed"},
    )
    assert d.allow is True
    assert d.reason == "terminal"


def test_terminal_docs_only_closed_pr_allowed():
    # DOCS completed, MERGE not, PR resolved as closed/none -> docs-only
    # terminal path is ALLOW.
    d = _call(
        stage_states={"DOCS": "completed", "MERGE": "pending"},
        pr_open=False,
    )
    assert d.allow is True
    assert d.reason == "terminal"


def test_docs_only_pr_still_open_is_non_terminal_refused():
    # DOCS completed but the PR is still open -> not terminal -> refused.
    d = _call(
        stage_states={"DOCS": "completed", "MERGE": "pending"},
        pr_open=True,
    )
    assert d.allow is False
    assert d.reason == "refused_non_terminal"


# -- Non-terminal ledger: refuse / escalate / blocked-reason ---------------


def test_non_terminal_no_reason_refused_with_nudge():
    d = _call(next_skill="/do-plan")
    assert d.allow is False
    assert d.reason == "refused_non_terminal"
    assert d.escalate_to_user is False
    assert d.reroute_message is not None
    # Criterion (a) binding: the nudge names the router's next skill.
    assert "/do-plan" in d.reroute_message
    assert "#2158" in d.reroute_message


def test_non_terminal_with_blocked_reason_allowed():
    d = _call(blocked_reason="superseded by #9999; abandoning")
    assert d.allow is True
    assert d.reason == "blocked_reason_given"


def test_whitespace_blocked_reason_treated_as_absent():
    d = _call(blocked_reason="   \n\t  ")
    assert d.allow is False
    assert d.reason == "refused_non_terminal"


def test_refusals_exhausted_escalates_to_user():
    d = _call(refusal_count=MAX_COMPLETION_REFUSALS)
    assert d.allow is False
    assert d.reason == "escalate_exhausted"
    assert d.escalate_to_user is True
    assert d.reroute_message is None


def test_below_cap_still_refuses_not_escalates():
    d = _call(refusal_count=MAX_COMPLETION_REFUSALS - 1)
    assert d.allow is False
    assert d.reason == "refused_non_terminal"
    assert d.escalate_to_user is False


def test_reroute_message_falls_back_when_next_skill_missing():
    d = _call(next_skill=None)
    assert d.allow is False
    assert d.reroute_message is not None
    assert "sdlc-tool next-skill" in d.reroute_message
