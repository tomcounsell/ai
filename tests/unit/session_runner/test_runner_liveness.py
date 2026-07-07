"""Runner liveness: role-aware turn timeout + subprocess-death detection.

The wedge-coverage replacement (plan #1924, task 5): the deleted PTY
frozen-frame detectors have no headless analog — liveness now comes from
the protocol:

* a role-aware per-turn timeout bounds every turn (the preempt watcher's
  timeout kill is covered in test_runner_preempt.py; this file covers the
  role-aware selection seam and the driver's own bounded-wait backstop), and
* a subprocess that dies (or hangs) without a ``result`` event classifies
  as ``exit_reason="error"`` — NEVER a clean completion (the #1916
  false-success regression net) — with a persona-safe user message (no raw
  exit strings reach the CEO).
"""

from __future__ import annotations

import asyncio

import pytest

from agent.session_runner.adapter import SessionRunnerAdapter
from agent.session_runner.role_driver import HeadlessRoleDriver, HeadlessTurnOutcome
from agent.session_runner.runner import (
    ENG_TURN_TIMEOUT_S,
    RUNNER_ERROR_USER_MESSAGE,
    TEAMMATE_TURN_TIMEOUT_S,
    SessionRunner,
    turn_timeout_for,
)


class FakeSession:
    """Minimal AgentSession stand-in (session_events list + save capture)."""

    def __init__(self):
        self.session_id = "sess-liveness-test"
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


def make_runner(script, *, session=None, **kwargs):
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
        steering_pop_fn=lambda: [],
        **kwargs,
    )
    return runner, deliveries, session, driver


# --------------------------------------------------------------------------
# Role-aware turn timeout
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "session_type,expected",
    [
        ("teammate", TEAMMATE_TURN_TIMEOUT_S),
        ("TEAMMATE", TEAMMATE_TURN_TIMEOUT_S),
        ("  teammate  ", TEAMMATE_TURN_TIMEOUT_S),
        # Eng/PM sessions carry the Dev subagent's work inside the PM turn
        # (D1), so they get the generous ceiling.
        ("eng", ENG_TURN_TIMEOUT_S),
        ("pm", ENG_TURN_TIMEOUT_S),
        (None, ENG_TURN_TIMEOUT_S),
        ("", ENG_TURN_TIMEOUT_S),
    ],
)
def test_turn_timeout_for_role_table(session_type, expected):
    assert turn_timeout_for(session_type) == expected


def test_runner_defaults_to_role_aware_timeout():
    """With no explicit turn_timeout_s, the runner picks the role's ceiling."""
    eng_runner, _, _, _ = make_runner([], session_type="eng")
    tm_runner, _, _, _ = make_runner([], session_type="teammate")
    assert eng_runner._turn_timeout_s == ENG_TURN_TIMEOUT_S
    assert tm_runner._turn_timeout_s == TEAMMATE_TURN_TIMEOUT_S


def test_explicit_turn_timeout_overrides_role_default():
    runner, _, _, _ = make_runner([], session_type="eng", turn_timeout_s=12.5)
    assert runner._turn_timeout_s == 12.5


# --------------------------------------------------------------------------
# Subprocess-death detection (#1916 regression net)
# --------------------------------------------------------------------------


async def test_subprocess_death_classifies_error_never_completed():
    """A turn whose subprocess dies without a result event → exit_reason=error,
    persona-safe user message — never a clean completion (the #1916 class)."""
    dead_turn = HeadlessTurnOutcome(
        turn_ended=False,
        exit_reason="headless_subprocess_error: [Errno 32] broken pipe",
    )
    runner, deliveries, session, _ = make_runner([dead_turn])
    summary = await runner.run("do the thing")

    assert summary.exit_reason == "error", "a dead subprocess must classify as error"
    assert summary.exit_reason not in ("pm_complete", "pm_user"), "never a clean completion"
    # Persona-safe delivery: the canned message, never the raw exit string.
    assert deliveries == [RUNNER_ERROR_USER_MESSAGE]
    assert all("broken pipe" not in d for d in deliveries), (
        "raw subprocess error text must never reach the user"
    )
    # Terminal exit_summary persisted with the error classification.
    exit_events = [e for e in session.session_events if e["type"] == "exit_summary"]
    assert exit_events and exit_events[-1]["exit_reason"] == "error"


async def test_hung_subprocess_bounded_wait_classifies_error():
    """The driver's bounded-wait backstop (no result, no Stop edge within the
    turn budget) surfaces as exit_reason=error — a hang is never silent and
    never a completion."""
    hung_turn = HeadlessTurnOutcome(
        turn_ended=False,
        hung=True,
        exit_reason="headless_turn_timeout",
    )
    runner, deliveries, _, _ = make_runner([hung_turn])
    summary = await runner.run("go")

    assert summary.exit_reason == "error"
    assert deliveries == [RUNNER_ERROR_USER_MESSAGE]


async def test_missing_binary_classifies_error():
    """A missing claude binary is a deterministic infra failure — error, not
    a completion and not an unbounded retry loop."""
    missing = HeadlessTurnOutcome(
        reply_text="Error: CLI harness not found",
        turn_ended=False,
        exit_reason="headless_binary_missing",
    )
    runner, deliveries, _, driver = make_runner([missing])
    summary = await runner.run("go")

    assert summary.exit_reason == "error"
    assert len(driver.calls) == 1, "an infra failure must not spin the turn loop"
    assert deliveries == [RUNNER_ERROR_USER_MESSAGE]


# --------------------------------------------------------------------------
# Driver-level bounded wait (the real seam, fake harness only)
# --------------------------------------------------------------------------


async def test_driver_hung_harness_times_out_with_headless_turn_timeout(tmp_path):
    """HeadlessRoleDriver's own asyncio.wait_for backstop converts a hung
    subprocess into a classified outcome instead of an infinite wait."""

    async def _never(message, working_dir, **kwargs):
        await asyncio.sleep(30)
        return "never"

    driver = HeadlessRoleDriver(
        role="pm",
        session_id="liveness-driver-test",
        working_dir=str(tmp_path),
        turn_timeout_s=0.05,
        harness_fn=_never,
    )
    outcome = await driver.run_turn("hello")
    assert outcome.hung is True
    assert outcome.exit_reason == "headless_turn_timeout"
    assert outcome.turn_ended is False


async def test_driver_subprocess_exception_classified_not_raised(tmp_path):
    """A harness exception (subprocess died) is classified, never raised into
    the runner loop."""

    async def _boom(message, working_dir, **kwargs):
        raise RuntimeError("claude exited -9")

    driver = HeadlessRoleDriver(
        role="pm",
        session_id="liveness-driver-exc",
        working_dir=str(tmp_path),
        turn_timeout_s=5.0,
        harness_fn=_boom,
    )
    outcome = await driver.run_turn("hello")
    assert outcome.turn_ended is False
    assert outcome.exit_reason is not None
    assert outcome.exit_reason.startswith("headless_subprocess_error")
