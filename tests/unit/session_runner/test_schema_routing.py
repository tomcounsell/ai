"""Schema-first PM turn routing (plan #2000 Task 2.3).

Covers, with a scripted fake driver:

* Schema-first: a valid ``structured_output`` on ``HeadlessTurnOutcome``
  routes directly (``route`` -> destination, ``message`` -> payload) — the
  prefix-regex parser is never consulted, proven by a ``reply_text`` that
  would parse to a DIFFERENT destination if the regex ran.
* Fallback: ``structured_output`` absent, or present but shaped wrong
  (missing/invalid ``route``/``message``), falls back to
  ``classify_pm_prefix`` on ``reply_text``.
* Every fallback emits ``schema_routing_fallback`` session telemetry
  (``agent.session_telemetry.record_telemetry_event``) — observable, not
  silent.
* ``file_paths`` on the schema output reaches the REAL delivery call (the
  ``send_cb`` the adapter resolves), not merely the parsed
  ``ClassificationResult`` — this is the #1802 acceptance bar: a named file
  becomes an attachment on the delivery call, proven end to end through
  ``SessionRunner.run()``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from agent.session_runner.adapter import SessionRunnerAdapter
from agent.session_runner.role_driver import HeadlessTurnOutcome
from agent.session_runner.router import ExitReason, validate_structured_route
from agent.session_runner.runner import SessionRunner


class FakeSession:
    """Minimal AgentSession stand-in (session_events list + save capture)."""

    def __init__(self):
        self.session_id = "sess-schema-routing-test"
        self.chat_id = 111
        self.telegram_message_id = 222
        self.session_events = None
        self.saved_fields: list[list[str]] = []

    def save(self, update_fields=None):
        self.saved_fields.append(list(update_fields or []))


class ScriptedDriver:
    """Fake role driver returning scripted HeadlessTurnOutcome objects in order."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[str] = []

    async def run_turn(self, message: str) -> HeadlessTurnOutcome:
        self.calls.append(message)
        item = self.script.pop(0) if self.script else HeadlessTurnOutcome()
        if isinstance(item, HeadlessTurnOutcome):
            return item
        return HeadlessTurnOutcome(reply_text=item, turn_ended=True, turn_end_source="result")


def make_runner(script, *, session=None, send_cb=None, **kwargs):
    """Build (runner, deliveries, session, driver) with a capturing send_cb.

    ``deliveries`` records every ``(chat_id, payload, reply_to, file_paths)``
    call the send_cb receives — ``file_paths`` is whatever the caller passed
    (``None`` when omitted), so a test can assert real attachment plumbing,
    not just that the router parsed a ``file_paths`` slot.
    """
    session = session or FakeSession()
    deliveries: list[tuple] = []

    def _default_send_cb(chat_id, payload, reply_to, agent_session, file_paths=None):
        deliveries.append((chat_id, payload, reply_to, file_paths))

    send_cb = send_cb or _default_send_cb

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


# ---------------------------------------------------------------------------
# validate_structured_route — pure classifier unit coverage
# ---------------------------------------------------------------------------


class TestValidateStructuredRoute:
    def test_valid_user_route(self):
        result = validate_structured_route({"route": "user", "message": "hello human"})
        assert result is not None
        assert result.destination == "user"
        assert result.payload == "hello human"
        assert result.compliance_miss is False
        assert result.file_paths == []

    def test_valid_route_with_file_paths(self):
        result = validate_structured_route(
            {"route": "complete", "message": "shipped it", "file_paths": ["/tmp/a.png"]}
        )
        assert result is not None
        assert result.file_paths == ["/tmp/a.png"]

    def test_none_input_returns_none(self):
        assert validate_structured_route(None) is None

    def test_missing_route_returns_none(self):
        assert validate_structured_route({"message": "no route field"}) is None

    def test_invalid_route_value_returns_none(self):
        assert validate_structured_route({"route": "banana", "message": "x"}) is None

    def test_non_string_message_returns_none(self):
        assert validate_structured_route({"route": "user", "message": 123}) is None

    def test_malformed_file_paths_dropped_not_fatal(self):
        """A non-list / non-str-list file_paths degrades to [] rather than
        invalidating an otherwise-valid route+message."""
        result = validate_structured_route(
            {"route": "user", "message": "ok", "file_paths": "not-a-list"}
        )
        assert result is not None
        assert result.file_paths == []


# ---------------------------------------------------------------------------
# Schema-first routing through the runner
# ---------------------------------------------------------------------------


async def test_schema_route_wins_over_conflicting_prefix_text():
    """A valid structured_output routes directly — the regex parser is never
    consulted. reply_text carries a DIFFERENT (regex-only) destination to
    prove the schema path, not the text, decided the route."""
    outcome = HeadlessTurnOutcome(
        reply_text="[/complete]\nthis text says complete",
        turn_ended=True,
        turn_end_source="result",
        structured_output={"route": "user", "message": "schema says user"},
    )
    runner, deliveries, _, _ = make_runner([outcome])
    summary = await runner.run("do the thing")

    assert deliveries == [(111, "schema says user", 222, None)]
    assert summary.exit_reason is ExitReason.PM_USER


async def test_schema_route_complete():
    outcome = HeadlessTurnOutcome(
        reply_text="irrelevant raw text",
        turn_ended=True,
        turn_end_source="result",
        structured_output={"route": "complete", "message": "shipped the fix"},
    )
    runner, deliveries, _, _ = make_runner([outcome])
    summary = await runner.run("go")

    assert deliveries == [(111, "shipped the fix", 222, None)]
    assert summary.exit_reason is ExitReason.PM_COMPLETE


async def test_schema_route_continue_is_not_a_compliance_miss():
    """route: "continue" is a deliberate schema decision — it must not
    increment compliance_misses (unlike a genuine prefix-regex miss)."""
    # reply_text is realistically non-empty here: the harness's `result` text
    # is the JSON-stringified structured_output (Task 2.1 empirical finding),
    # never a blank string alongside a populated structured_output.
    outcome = HeadlessTurnOutcome(
        reply_text='{"route": "continue", "message": "still working"}',
        turn_ended=True,
        turn_end_source="result",
        structured_output={"route": "continue", "message": "still working"},
    )
    final = HeadlessTurnOutcome(
        reply_text='{"route": "complete", "message": "done now"}',
        turn_ended=True,
        turn_end_source="result",
        structured_output={"route": "complete", "message": "done now"},
    )
    runner, deliveries, _, _ = make_runner([outcome, final])
    summary = await runner.run("go")

    assert deliveries == [(111, "done now", 222, None)]
    assert summary.compliance_misses == 0


# ---------------------------------------------------------------------------
# Fallback: structured_output absent or invalid -> regex parse + telemetry
# ---------------------------------------------------------------------------


async def test_fallback_to_regex_when_structured_output_absent():
    outcome = HeadlessTurnOutcome(
        reply_text="[/user]\nhello via regex fallback",
        turn_ended=True,
        turn_end_source="result",
        structured_output=None,
    )
    runner, deliveries, _, _ = make_runner([outcome])

    with patch("agent.session_telemetry.record_telemetry_event") as mock_telemetry:
        summary = await runner.run("do the thing")

    assert deliveries == [(111, "hello via regex fallback", 222, None)]
    assert summary.exit_reason is ExitReason.PM_USER

    fallback_events = [
        call.args[1]
        for call in mock_telemetry.call_args_list
        if call.args[1].get("type") == "schema_routing_fallback"
    ]
    assert len(fallback_events) == 1, mock_telemetry.call_args_list


async def test_fallback_to_regex_when_structured_output_invalid():
    """A malformed structured_output (unknown route enum value) must fall
    back exactly like an absent one — never raise, never silently drop the
    turn."""
    outcome = HeadlessTurnOutcome(
        reply_text="[/complete]\nregex still works",
        turn_ended=True,
        turn_end_source="result",
        structured_output={"route": "not-a-real-route", "message": "garbage"},
    )
    runner, deliveries, _, _ = make_runner([outcome])

    with patch("agent.session_telemetry.record_telemetry_event") as mock_telemetry:
        summary = await runner.run("go")

    assert deliveries == [(111, "regex still works", 222, None)]
    assert summary.exit_reason is ExitReason.PM_COMPLETE
    fallback_events = [
        call.args[1]
        for call in mock_telemetry.call_args_list
        if call.args[1].get("type") == "schema_routing_fallback"
    ]
    assert len(fallback_events) == 1


async def test_no_fallback_telemetry_on_schema_success():
    """A healthy schema turn must NOT emit schema_routing_fallback — the
    telemetry signal has to stay meaningful (only fires on a real fallback)."""
    outcome = HeadlessTurnOutcome(
        reply_text="ignored",
        turn_ended=True,
        turn_end_source="result",
        structured_output={"route": "complete", "message": "all good"},
    )
    runner, _, _, _ = make_runner([outcome])

    with patch("agent.session_telemetry.record_telemetry_event") as mock_telemetry:
        await runner.run("go")

    fallback_events = [
        call.args[1]
        for call in mock_telemetry.call_args_list
        if call.args[1].get("type") == "schema_routing_fallback"
    ]
    assert fallback_events == []


# ---------------------------------------------------------------------------
# file_paths — real delivery-call attachment, not just parsing (#1802)
# ---------------------------------------------------------------------------


async def test_file_paths_reach_the_real_send_cb_call():
    """The #1802 acceptance bar: a named file in the schema output must
    become an attachment ON THE DELIVERY CALL (send_cb's file_paths kwarg),
    not merely a value living on the parsed ClassificationResult. Plumbing
    that stops at ClassificationResult.file_paths without reaching send_cb
    would pass a plumbing-only test but fail this one."""
    outcome = HeadlessTurnOutcome(
        reply_text="ignored",
        turn_ended=True,
        turn_end_source="result",
        structured_output={
            "route": "user",
            "message": "here is the screenshot",
            "file_paths": ["/tmp/screenshot.png"],
        },
    )
    runner, deliveries, _, _ = make_runner([outcome])
    await runner.run("show me")

    assert deliveries == [(111, "here is the screenshot", 222, ["/tmp/screenshot.png"])]


async def test_file_paths_omitted_when_send_cb_does_not_support_it():
    """A send_cb without a file_paths parameter (e.g. EmailOutputHandler.send)
    must still receive the delivery — the attachment is silently omitted,
    never a TypeError that drops the whole message."""
    outcome = HeadlessTurnOutcome(
        reply_text="ignored",
        turn_ended=True,
        turn_end_source="result",
        structured_output={
            "route": "user",
            "message": "no attachment support here",
            "file_paths": ["/tmp/screenshot.png"],
        },
    )
    calls: list[tuple] = []

    def legacy_send_cb(chat_id, payload, reply_to, agent_session):
        # No file_paths parameter at all — mirrors bridge/email_bridge.py's
        # EmailOutputHandler.send() signature.
        calls.append((chat_id, payload, reply_to))

    runner, _, _, _ = make_runner([outcome], send_cb=legacy_send_cb)
    summary = await runner.run("show me")

    assert calls == [(111, "no attachment support here", 222)]
    assert summary.exit_reason is ExitReason.PM_USER


async def test_file_paths_reach_outbox_fallback_on_delivery_failure(monkeypatch):
    """Adapter-level (mirrors test_runner_turns.py's
    test_deliver_sync_same_thread_failure_reenqueues_outbox): when the async
    send_cb's fire-and-forget task raises, the done-callback's outbox
    re-enqueue must still carry file_paths — a recovered delivery must not
    silently drop its attachment."""
    session = FakeSession()

    async def failing_send_cb(chat_id, payload, reply_to, agent_session, file_paths=None):
        raise RuntimeError("telegram down")

    adapter = SessionRunnerAdapter(
        session, "test-proj", "telegram", resolve_callbacks=lambda pk, t: (failing_send_cb, None)
    )
    adapter.capture_event_loop()

    enqueued: list[tuple] = []

    def fake_enqueue(chat_id, payload, reply_to, file_paths=None):
        enqueued.append((chat_id, payload, reply_to, file_paths))
        return True

    monkeypatch.setattr(adapter, "_enqueue_to_outbox", fake_enqueue)
    result = adapter._deliver_sync(
        failing_send_cb, 111, "attach me", 222, session, 5.0, ["/tmp/recovered.png"]
    )
    assert result is True  # handed off; recovery is the done-callback's job
    # Let the task run, then let its done-callback (call_soon) fire.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert enqueued == [(111, "attach me", 222, ["/tmp/recovered.png"])]
