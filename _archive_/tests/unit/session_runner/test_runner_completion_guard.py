"""Runner-level wiring tests for the ledger-aware completion guard (#2158).

Exercises ``SessionRunner._route_turn`` / ``_guard_completion`` with a fake
ledger so a PM ``complete`` route is refused / escalated / allowed against the
per-issue SDLC pipeline state. Gates BOTH the schema route AND the regex
fallback.
"""

from __future__ import annotations

import pytest

from agent.session_runner.adapter import SessionRunnerAdapter
from agent.session_runner.completion_guard import MAX_COMPLETION_REFUSALS
from agent.session_runner.role_driver import HeadlessTurnOutcome
from agent.session_runner.router import ExitReason
from agent.session_runner.runner import SessionRunner

pytestmark = pytest.mark.sdlc


class FakeEngSession:
    """AgentSession stand-in carrying an issue_number (an SDLC eng session)."""

    def __init__(self, issue_number=2158):
        self.session_id = "sess-guard-test"
        self.chat_id = 111
        self.telegram_message_id = 222
        self.session_events = None
        self.issue_number = issue_number
        self.session_type = "eng"
        self.saved_fields = []

    def save(self, update_fields=None):
        self.saved_fields.append(list(update_fields or []))


def _make_runner(session=None):
    session = session or FakeEngSession()
    deliveries = []

    def send_cb(chat_id, payload, reply_to, agent_session):
        deliveries.append(payload)

    adapter = SessionRunnerAdapter(
        session, "test-proj", "telegram", resolve_callbacks=lambda pk, t: (send_cb, None)
    )
    runner = SessionRunner(
        agent_session=session,
        adapter=adapter,
        working_dir="/tmp/wd",
        session_type="eng",
        driver=None,
        steering_pop_fn=lambda: [],
    )
    return runner, deliveries, session


def _schema_complete(message="done", blocked_reason=None):
    structured = {"route": "complete", "message": message}
    if blocked_reason is not None:
        structured["blocked_reason"] = blocked_reason
    return HeadlessTurnOutcome(
        reply_text=message, turn_ended=True, turn_end_source="result", structured_output=structured
    )


def _regex_complete(text="[/complete]\nshipped"):
    return HeadlessTurnOutcome(reply_text=text, turn_ended=True, turn_end_source="result")


def _patch_ledger(runner, stage_states, *, next_skill="/do-plan", pr_open=None, ok=True, meta=None):
    meta = meta if meta is not None else {"_resolved_target_repo": "tomcounsell/ai"}
    runner._load_ledger = lambda issue_number: (stage_states, meta, next_skill, pr_open, ok)
    # Persist is a no-op in tests (no real ledger).
    runner._persist_refusal_count = lambda *a, **k: None


NON_TERMINAL = {"PLAN": "completed", "CRITIQUE": "completed", "BUILD": "ready"}
TERMINAL = {"MERGE": "completed", "DOCS": "completed", "REVIEW": "completed"}


def test_schema_complete_non_terminal_is_refused_and_continues():
    runner, deliveries, _ = _make_runner()
    _patch_ledger(runner, NON_TERMINAL, next_skill="/do-plan")
    decision = runner._route_turn(_schema_complete())
    assert decision.should_break is False
    assert decision.next_message is not None
    assert "/do-plan" in decision.next_message
    assert deliveries == []  # nothing delivered — the run continues


def test_regex_fallback_complete_non_terminal_is_refused():
    # Concern #3: the regex fallback path must be gated too.
    runner, deliveries, _ = _make_runner()
    _patch_ledger(runner, NON_TERMINAL, next_skill="/do-build")
    decision = runner._route_turn(_regex_complete())
    assert decision.should_break is False
    assert "/do-build" in (decision.next_message or "")


def test_terminal_ledger_completes():
    runner, deliveries, _ = _make_runner()
    _patch_ledger(runner, TERMINAL)
    decision = runner._route_turn(_schema_complete("shipped it"))
    assert decision.should_break is True
    assert decision.exit_reason is ExitReason.PM_COMPLETE
    assert deliveries == ["shipped it"]


def test_blocked_reason_allows_completion_on_non_terminal():
    runner, deliveries, _ = _make_runner()
    _patch_ledger(runner, NON_TERMINAL)
    decision = runner._route_turn(
        _schema_complete("abandoning", blocked_reason="superseded by #9999")
    )
    assert decision.should_break is True
    assert decision.exit_reason is ExitReason.PM_COMPLETE


def test_non_sdlc_session_completes_without_ledger():
    # issue_number=None → not_sdlc fast path, no ledger consulted.
    session = FakeEngSession(issue_number=None)
    runner, deliveries, _ = _make_runner(session)

    # Deliberately make _load_ledger explode — it must never be called.
    def _boom(issue_number):
        raise AssertionError("_load_ledger should not run for a non-SDLC session")

    runner._load_ledger = _boom
    decision = runner._route_turn(_schema_complete("chatting done"))
    assert decision.should_break is True
    assert decision.exit_reason is ExitReason.PM_COMPLETE


def test_ledger_query_failure_fails_open_and_completes():
    runner, deliveries, _ = _make_runner()
    # ledger_ok=False → fail-open ALLOW.
    _patch_ledger(runner, {}, ok=False)
    decision = runner._route_turn(_schema_complete("done"))
    assert decision.should_break is True
    assert decision.exit_reason is ExitReason.PM_COMPLETE


def test_refusals_exhausted_escalates_to_human():
    runner, deliveries, session = _make_runner()
    _patch_ledger(runner, NON_TERMINAL)
    # Drive MAX refusals; each REFUSE increments the in-memory ladder.
    for _ in range(MAX_COMPLETION_REFUSALS):
        d = runner._route_turn(_schema_complete("still going"))
        assert d.should_break is False
    # Next complete → ladder exhausted → escalate to human.
    final = runner._route_turn(_schema_complete("please finish"))
    assert final.should_break is True
    assert final.exit_reason is ExitReason.PM_NEEDS_HUMAN
    assert deliveries and "please finish" in deliveries[-1]


def test_refusal_counter_seeds_from_persisted_ledger_value():
    # Concern #2: a resumed runner seeds the ladder from the persisted count,
    # so it does not restart from zero. Persisted count already at the cap →
    # first complete escalates immediately (no fresh 3-refusal ladder).
    runner, deliveries, _ = _make_runner()
    _patch_ledger(
        runner,
        NON_TERMINAL,
        meta={
            "_resolved_target_repo": "tomcounsell/ai",
            "completion_refusal_count": MAX_COMPLETION_REFUSALS,
        },
    )
    decision = runner._route_turn(_schema_complete("finish now"))
    assert decision.should_break is True
    assert decision.exit_reason is ExitReason.PM_NEEDS_HUMAN
