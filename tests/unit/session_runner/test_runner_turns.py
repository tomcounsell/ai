"""SessionRunner turn loop: simplified route table + wrap-up guard (plan #1924, task 2).

Covers, with a scripted fake driver and a sync delivery callback:

* ``[/user]`` → delivered payload, exit ``pm_user``.
* ``[/complete]`` → delivered summary, exit ``pm_complete``; empty payload
  backstopped by the wrap-up guard.
* Unroutable turns → bounded compliance nudge, then the wrap-up guard —
  never an infinite loop.
* Empty/whitespace-only PM text → wrap-up guard (plan Failure Path).
* Turn failure → ``exit_reason="error"`` (never completed) + persona-safe
  apology delivered.
* Async send_cb on the runner's own loop (the production delivery shape):
  same-thread handoff counts as routed, wrap-up guard does not fire, and
  failure recovery re-enqueues to the outbox.
* Steering boundary drain: abort at boundary, steer text injected.
"""

from __future__ import annotations

import asyncio

import pytest

from agent.session_runner.adapter import SessionRunnerAdapter
from agent.session_runner.role_driver import HeadlessTurnOutcome
from agent.session_runner.runner import (
    OPERATOR_TERMINAL_MESSAGE,
    PM_COMPLIANCE_NUDGE,
    RUNNER_ERROR_USER_MESSAGE,
    STEER_ABORT_USER_MESSAGE,
    SessionRunner,
    turn_timeout_for,
)


class FakeSession:
    """Minimal AgentSession stand-in (session_events list + save capture)."""

    def __init__(self):
        self.session_id = "sess-runner-test"
        self.chat_id = 111
        self.telegram_message_id = 222
        self.session_events = None
        self.saved_fields: list[list[str]] = []

    def save(self, update_fields=None):
        self.saved_fields.append(list(update_fields or []))


class ScriptedDriver:
    """Fake role driver returning scripted replies/outcomes in order."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[str] = []

    async def run_turn(self, message: str) -> HeadlessTurnOutcome:
        self.calls.append(message)
        item = self.script.pop(0) if self.script else ""
        if isinstance(item, HeadlessTurnOutcome):
            return item
        return HeadlessTurnOutcome(reply_text=item, turn_ended=True, turn_end_source="result")


def make_runner(script, *, session=None, steering=None, **kwargs):
    """Build (runner, deliveries, session, driver) with a sync send_cb."""
    session = session or FakeSession()
    deliveries: list[str] = []

    def send_cb(chat_id, payload, reply_to, agent_session):
        deliveries.append(payload)

    adapter = SessionRunnerAdapter(
        session, "test-proj", "telegram", resolve_callbacks=lambda pk, t: (send_cb, None)
    )
    driver = ScriptedDriver(script)
    runner = SessionRunner(
        agent_session=session,
        adapter=adapter,
        working_dir="/tmp/wd",
        driver=driver,
        steering_pop_fn=steering or (lambda: []),
        **kwargs,
    )
    return runner, deliveries, session, driver


# --------------------------------------------------------------------------
# Simplified route table
# --------------------------------------------------------------------------


async def test_user_route_delivers_and_exits():
    # A real [/user] answer stays "pm_user" — distinct from the needs_human-edge
    # fallback (test_needs_human_edge_with_unroutable_text_delivers below), which
    # now exits "pm_needs_human". This assertion pins the real-answer branch.
    runner, deliveries, session, driver = make_runner(["[/user]\nhello human"])
    summary = await runner.run("do the thing")
    assert deliveries == ["hello human"]
    assert summary.exit_reason == "pm_user"
    assert summary.user_facing_routed is True
    assert summary.turn_count == 1
    # Exit summary published to session_events.
    kinds = [e["type"] for e in session.session_events]
    assert "exit_summary" in kinds


async def test_complete_route_delivers_summary():
    runner, deliveries, _, _ = make_runner(["[/complete]\nshipped the fix"])
    summary = await runner.run("go")
    assert deliveries == ["shipped the fix"]
    assert summary.exit_reason == "pm_complete"
    assert summary.user_facing_routed is True


async def test_complete_with_empty_payload_backstopped_by_wrapup():
    """A bare [/complete] with no summary still yields a user-facing message."""
    runner, deliveries, _, driver = make_runner(["[/complete]", "[/user]\nfinal word"])
    summary = await runner.run("go")
    # Wrap-up guard ran one extra PM turn and delivered its answer.
    assert deliveries == ["final word"]
    assert summary.exit_reason == "pm_user"
    assert "wrapping up" in driver.calls[1]


async def test_unroutable_turns_bounded_nudge_then_wrapup():
    """Unknown prefixes get MAX_COMPLIANCE_NUDGES nudges, then the wrap-up
    guard takes over — never an infinite relay loop."""
    runner, deliveries, _, driver = make_runner(
        ["no prefix at all", "still no prefix", "[/user]\nrecovered"]
    )
    summary = await runner.run("go")
    assert driver.calls[1] == PM_COMPLIANCE_NUDGE
    assert "wrapping up" in driver.calls[2]
    assert deliveries == ["recovered"]
    assert summary.exit_reason == "pm_user"


@pytest.mark.parametrize("empty_reply", ["", "   \n\t"])
async def test_empty_pm_text_routes_to_wrapup_guard(empty_reply):
    """Empty/whitespace-only PM text → wrap-up guard, not an infinite loop."""
    runner, deliveries, _, driver = make_runner([empty_reply, "prefix-less but real answer"])
    summary = await runner.run("go")
    # The wrap-up guard floor-delivered the prefix-less text.
    assert deliveries == ["prefix-less but real answer"]
    assert summary.exit_reason == "pm_floor_delivered"
    assert len(driver.calls) == 2  # one real turn + one wrapup turn


async def test_wrapup_silent_pm_gets_terminal_message():
    """A PM that stays silent even through the wrap-up prompt yields the
    canned terminal message — the human always receives something."""
    runner, deliveries, _, _ = make_runner(["", ""])
    summary = await runner.run("go")
    assert deliveries == [OPERATOR_TERMINAL_MESSAGE]
    assert summary.exit_reason == "pm_no_user_message"
    assert summary.user_facing_routed is True


async def test_max_turns_exhaustion_reaches_wrapup():
    runner, deliveries, _, _ = make_runner(["nope", "[/user]\nwrapped"], max_turns=1)
    summary = await runner.run("go")
    assert deliveries == ["wrapped"]
    assert summary.exit_reason == "pm_user"
    assert summary.turn_count == 1


async def test_turn_failure_is_error_with_persona_safe_apology():
    """A failed subprocess turn exits ``error`` (never completed) and the
    user gets a persona-safe apology — the #1916 false-success class."""
    failing = HeadlessTurnOutcome(reply_text="", exit_reason="headless_subprocess_error: exploded")
    runner, deliveries, session, _ = make_runner([failing])
    summary = await runner.run("go")
    assert summary.exit_reason == "error"
    assert "exploded" in summary.exit_message
    assert deliveries == [RUNNER_ERROR_USER_MESSAGE]
    # Terminal exit_reason persisted via the exit summary.
    assert session.exit_reason == "error"


async def test_needs_human_edge_with_unroutable_text_delivers():
    """A substantive needs-human edge (post-#1919 filtering) alongside an
    unroutable turn delivers the PM's text as the question."""
    from agent.session_runner.hook_edge import NEEDS_HUMAN, HookEdge

    outcome = HeadlessTurnOutcome(
        reply_text="Which environment should I target?",
        turn_ended=True,
        turn_end_source="result",
        needs_human=HookEdge(kind=NEEDS_HUMAN, event="Notification"),
    )
    runner, deliveries, _, _ = make_runner([outcome])
    summary = await runner.run("go")
    assert deliveries == ["Which environment should I target?"]
    assert summary.exit_reason == "pm_needs_human"
    assert summary.user_facing_routed is True


# --------------------------------------------------------------------------
# Async send_cb on the loop thread (the production delivery shape)
# --------------------------------------------------------------------------


def make_async_runner(script, *, session=None, **kwargs):
    """Build (runner, deliveries, session, driver) with an ASYNC send_cb.

    Mirrors production: ``TelegramRelayOutputHandler.send`` is a coroutine
    function and the runner delivers from its own event loop's thread, so
    ``_deliver_sync`` takes the same-thread fire-and-forget path.
    """
    session = session or FakeSession()
    deliveries: list[str] = []

    async def send_cb(chat_id, payload, reply_to, agent_session):
        deliveries.append(payload)

    adapter = SessionRunnerAdapter(
        session, "test-proj", "telegram", resolve_callbacks=lambda pk, t: (send_cb, None)
    )
    driver = ScriptedDriver(script)
    runner = SessionRunner(
        agent_session=session,
        adapter=adapter,
        working_dir="/tmp/wd",
        driver=driver,
        steering_pop_fn=lambda: [],
        **kwargs,
    )
    return runner, deliveries, session, driver


async def test_async_send_cb_same_thread_counts_as_routed_no_wrapup():
    """Production shape: async send_cb, delivery on the runner's own loop.

    The same-thread handoff has guaranteed outbox recovery, so it counts as
    routed: the wrap-up guard must NOT fire (no duplicate messages, no
    OPERATOR_TERMINAL_MESSAGE) and exactly one payload reaches the human.
    """
    runner, deliveries, _, driver = make_async_runner(["[/user]\nhello human"])
    summary = await runner.run("do the thing")
    # Drain the fire-and-forget delivery task scheduled on this loop.
    await asyncio.sleep(0)
    assert deliveries == ["hello human"]
    assert summary.user_facing_routed is True
    assert summary.exit_reason == "pm_user"
    assert len(driver.calls) == 1  # wrap-up guard never ran an extra turn


async def test_deliver_sync_async_cb_same_thread_returns_true():
    """Adapter-level: _deliver_sync from the captured loop's own thread
    returns True (handed off with guaranteed outbox recovery)."""
    session = FakeSession()
    delivered: list[str] = []

    async def send_cb(chat_id, payload, reply_to, agent_session):
        delivered.append(payload)

    adapter = SessionRunnerAdapter(
        session, "test-proj", "telegram", resolve_callbacks=lambda pk, t: (send_cb, None)
    )
    adapter.capture_event_loop()
    result = adapter._deliver_sync(send_cb, 111, "payload", 222, session, 5.0)
    assert result is True
    await asyncio.sleep(0)
    assert delivered == ["payload"]


async def test_deliver_sync_same_thread_failure_reenqueues_outbox(monkeypatch):
    """Adapter-level: when the fire-and-forget task raises, the done-callback
    re-enqueues the payload to the Redis outbox (recovery preserved)."""
    session = FakeSession()

    async def send_cb(chat_id, payload, reply_to, agent_session):
        raise RuntimeError("telegram down")

    adapter = SessionRunnerAdapter(
        session, "test-proj", "telegram", resolve_callbacks=lambda pk, t: (send_cb, None)
    )
    adapter.capture_event_loop()
    enqueued: list[tuple] = []

    def fake_enqueue(chat_id, payload, reply_to, file_paths=None):
        enqueued.append((chat_id, payload, reply_to))
        return True

    monkeypatch.setattr(adapter, "_enqueue_to_outbox", fake_enqueue)
    result = adapter._deliver_sync(send_cb, 111, "payload", 222, session, 5.0)
    assert result is True  # handed off; recovery is the done-callback's job
    # Let the task run, then let its done-callback (call_soon) fire.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert enqueued == [(111, "payload", 222)]


# --------------------------------------------------------------------------
# Steering boundary drain
# --------------------------------------------------------------------------


async def test_steer_abort_at_boundary_stops_before_any_turn():
    steers = [[{"text": "stop it", "is_abort": True}]]
    runner, deliveries, _, driver = make_runner(
        ["[/user]\nnever reached"], steering=lambda: steers.pop(0) if steers else []
    )
    summary = await runner.run("go")
    assert deliveries == [STEER_ABORT_USER_MESSAGE]
    assert summary.exit_reason == "steer_abort"
    assert driver.calls == []


async def test_boundary_steer_injected_into_first_message():
    steers = [[{"text": "also check the logs"}]]
    runner, _, _, driver = make_runner(
        ["[/user]\nok"], steering=lambda: steers.pop(0) if steers else []
    )
    await runner.run("original ask")
    assert "original ask" in driver.calls[0]
    assert "also check the logs" in driver.calls[0]


# --------------------------------------------------------------------------
# Role-aware timeouts
# --------------------------------------------------------------------------


def test_role_aware_turn_timeout():
    """Teammate turns are short; eng turns are generous (Dev work runs
    inside the PM turn)."""
    assert turn_timeout_for("teammate") < turn_timeout_for("eng")
    assert turn_timeout_for(None) == turn_timeout_for("eng")
    assert turn_timeout_for("teammate") > 0


# --------------------------------------------------------------------------
# Compliance-miss accounting (PR #1930 review, A3)
# --------------------------------------------------------------------------


async def test_compliance_misses_counted_in_summary():
    """A prefix-less turn increments RunSummary.compliance_misses, and the
    published exit_summary event carries the count."""
    runner, deliveries, session, _ = make_runner(["no prefix here", "[/user]\nok"])
    summary = await runner.run("go")
    assert deliveries == ["ok"]
    assert summary.exit_reason == "pm_user"
    assert summary.compliance_misses == 1
    exit_events = [e for e in session.session_events if e["type"] == "exit_summary"]
    assert exit_events[-1]["compliance_misses"] == 1


async def test_clean_routing_reports_zero_compliance_misses():
    runner, _, _, _ = make_runner(["[/user]\nhello"])
    summary = await runner.run("go")
    assert summary.compliance_misses == 0


# --------------------------------------------------------------------------
# session_events entry cap (PR #1930 review, A6)
# --------------------------------------------------------------------------


def test_session_events_list_is_capped(monkeypatch):
    """_append_session_event trims the list to the entry cap (the whole
    ListField re-serializes per save — an unbounded list is quadratic write
    amplification), preserving any exit_summary entry."""
    from agent.session_runner import adapter as adapter_module

    monkeypatch.setattr(adapter_module, "SESSION_EVENTS_MAX_ENTRIES", 10)
    session = FakeSession()
    adapter_module._append_session_event(
        session, {"type": "exit_summary", "exit_reason": "pm_user"}
    )
    for i in range(30):
        adapter_module._append_session_event(session, {"type": "runner_turn", "n": i})

    events = session.session_events
    kinds = [e["type"] for e in events]
    # Bounded: at most the cap plus the preserved exit_summary entry.
    assert len(events) <= 11
    # The exit_summary entry survives trimming.
    assert "exit_summary" in kinds
    # Newest entries retained, oldest trimmed.
    assert events[-1]["n"] == 29
    assert all(e["n"] != 0 for e in events if e["type"] == "runner_turn")
