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
        self.last_stdout_at = None
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


# --------------------------------------------------------------------------
# Headless per-stream liveness (issue #1935): on_stdout_event/on_init wiring
# --------------------------------------------------------------------------


def _make_stdout_liveness_runner(session_id: str, *, harness_fn=None, **runner_kwargs):
    """Build a real SessionRunner via _build_driver (no injected `driver=`),
    so the wiring under test (the runner's own on_stdout_event/on_init
    adapters) actually runs. Extra ``runner_kwargs`` (e.g. ``turn_timeout_s``,
    ``term_grace_s``) pass straight through to ``SessionRunner``."""
    session = FakeSession()
    session.session_id = session_id

    def send_cb(chat_id, payload, reply_to, agent_session):
        return None

    adapter = SessionRunnerAdapter(
        session, "test-proj", "telegram", resolve_callbacks=lambda pk, t: (send_cb, None)
    )
    runner = SessionRunner(
        agent_session=session,
        adapter=adapter,
        working_dir="/tmp/wd",
        harness_fn=harness_fn,
        steering_pop_fn=lambda: [],
        **runner_kwargs,
    )
    return runner, session


def test_build_driver_wires_on_stdout_event_adapter():
    """_build_driver wires a 0-arg on_stdout_event adapter that stamps
    last_stdout_at (issue #1935 Element 1)."""
    runner, session = _make_stdout_liveness_runner("sess-stdout-wiring")
    assert session.last_stdout_at is None
    assert runner._driver._on_stdout_event is not None

    runner._driver._on_stdout_event()

    assert session.last_stdout_at is not None
    assert ["last_stdout_at"] in session.saved_fields


def test_build_driver_on_init_composes_resume_persist_and_stamp(monkeypatch):
    """The on_init adapter FIRST persists resume scalars via
    _on_harness_init (unchanged), THEN stamps last_stdout_at — never the
    inline-inside-_on_harness_init alternative (CRITIQUE pass 2 HARD
    CONSTRAINT)."""
    runner, session = _make_stdout_liveness_runner("sess-init-composed")

    persisted = []
    monkeypatch.setattr(
        runner._adapter,
        "persist_resume_scalars",
        lambda **kw: persisted.append(kw),
    )

    assert session.last_stdout_at is None
    runner._driver._on_init({"type": "system", "subtype": "init", "session_id": "claude-uuid-1"})

    # _on_harness_init's resume-scalar persistence still fires.
    assert persisted and persisted[0]["claude_session_id"] == "claude-uuid-1"
    # AND the liveness stamp fires too — composition, not replacement.
    assert session.last_stdout_at is not None
    assert ["last_stdout_at"] in session.saved_fields


def test_on_init_skips_resume_persist_but_still_stamps_liveness(monkeypatch):
    """A session_id-less init event hits _on_harness_init's early return
    (runner.py, `if not sid: return`) — resume scalars are correctly
    skipped, but the liveness stamp (which fires unconditionally AFTER
    _on_harness_init returns) must still land. This is exactly why the
    stamp cannot live inside _on_harness_init's try/except."""
    runner, session = _make_stdout_liveness_runner("sess-init-no-sid")

    persisted = []
    monkeypatch.setattr(
        runner._adapter,
        "persist_resume_scalars",
        lambda **kw: persisted.append(kw),
    )

    runner._driver._on_init({"type": "system", "subtype": "init"})  # no session_id

    assert persisted == []  # resume-scalar persistence correctly skipped
    assert session.last_stdout_at is not None  # liveness stamp still fires


# --------------------------------------------------------------------------
# _stamp_stdout_liveness: fail-silent + per-session-keyed cooldown
# --------------------------------------------------------------------------


def test_stamp_stdout_liveness_fail_silent_on_save_error():
    """A save() failure must never raise — the turn must never crash or
    wedge on a liveness-write failure."""
    runner, session = _make_stdout_liveness_runner("sess-stdout-fail")

    def _boom(update_fields=None):
        raise RuntimeError("redis down")

    session.save = _boom
    runner._stamp_stdout_liveness()  # must not raise


def test_stamp_stdout_liveness_cooldown_suppresses_rapid_repeats():
    """Two stamps within the cooldown window collapse to a single Redis
    write (Risk 2 — write-amplification bound)."""
    runner, session = _make_stdout_liveness_runner("sess-stdout-cooldown")

    runner._stamp_stdout_liveness()
    first_saves = len(session.saved_fields)
    runner._stamp_stdout_liveness()
    assert len(session.saved_fields) == first_saves  # second stamp coalesced


def test_stamp_stdout_liveness_cooldown_is_per_session_not_shared():
    """CRITIQUE pass 3 BLOCKER fix: two concurrently instantiated
    SessionRunner instances (distinct session_ids) each get an independent
    last_stdout_at stamp within the same 5s window — the cooldown state
    must NOT be a bare module/class-level timestamp that would let one
    session's stdout suppress another's stamp."""
    runner_a, session_a = _make_stdout_liveness_runner("sess-A")
    runner_b, session_b = _make_stdout_liveness_runner("sess-B")

    runner_a._stamp_stdout_liveness()
    runner_b._stamp_stdout_liveness()

    assert session_a.last_stdout_at is not None
    assert session_b.last_stdout_at is not None
    assert ["last_stdout_at"] in session_a.saved_fields
    assert ["last_stdout_at"] in session_b.saved_fields


def test_stamp_stdout_liveness_noop_without_session_id():
    """No session_id resolvable → no-op, no crash."""
    runner, session = _make_stdout_liveness_runner("")
    runner._stamp_stdout_liveness()
    assert session.saved_fields == []


# --------------------------------------------------------------------------
# Risk 1 regression guard (issue #1935): a post-init hang is caught by the
# whole-turn deadline, NOT by session-health's never-started gate (which
# correctly no longer fires once init stamps last_stdout_at).
# --------------------------------------------------------------------------


async def test_post_init_hang_is_caught_by_turn_deadline_not_never_started_gate(monkeypatch):
    """A subprocess that streams `init` (real output — last_stdout_at gets
    stamped via the production SessionRunner._on_init_composed ->
    _stamp_stdout_liveness path) and then hangs forever must NOT be caught by
    the never-started gate (it correctly does not fire, since sdk_ever_output
    is now True) — the actual backstop is the driver's own whole-turn
    deadline (asyncio.wait_for -> outcome.hung=True /
    exit_reason=headless_turn_timeout).

    Built via ``_make_stdout_liveness_runner`` (like its neighbors) so this
    exercises the real ``SessionRunner``/``_build_driver``/
    ``_on_init_composed``/``_stamp_stdout_liveness`` wiring, not a hand-rolled
    on_init stand-in.
    """
    from datetime import UTC, datetime, timedelta

    import agent.session_runner.runner as runner_module
    from agent.session_health import _never_started_past_grace

    async def _init_then_hang(message, working_dir, **kwargs):
        on_init = kwargs.get("on_init")
        if on_init is not None:
            on_init({"type": "system", "subtype": "init", "session_id": "claude-uuid-hang"})
        await asyncio.sleep(30)  # never resolves within the tiny turn budget
        return "never reached"

    # Collapse the driver-backstop margin _build_driver adds on top of the
    # role timeout so the tiny turn_timeout_s below actually bounds the
    # driver's own asyncio.wait_for within this test's lifetime.
    monkeypatch.setattr(runner_module, "DRIVER_BACKSTOP_MARGIN_S", 0.0)

    runner, session = _make_stdout_liveness_runner(
        "sess-post-init-hang",
        harness_fn=_init_then_hang,
        turn_timeout_s=0.05,
        term_grace_s=0.0,
    )
    # Resume-scalar persistence is covered by
    # test_build_driver_on_init_composes_resume_persist_and_stamp; stub it
    # here so this test stays focused on the liveness stamp + turn-deadline
    # path under test.
    monkeypatch.setattr(runner._adapter, "persist_resume_scalars", lambda **kw: None)

    outcome = await runner._driver.run_turn("go")

    # The init event's liveness stamp landed via the real production path —
    # real output was produced.
    assert session.last_stdout_at is not None
    assert ["last_stdout_at"] in session.saved_fields

    # A session whose last_stdout_at is fresh must NOT be flagged by the
    # never-started gate, even though this simulated session is well past
    # the grace window on created_at/started_at.
    session.created_at = datetime.now(tz=UTC) - timedelta(seconds=500)
    session.started_at = None
    assert _never_started_past_grace(session) is False

    # The turn IS still recovered — via the whole-turn deadline, not the
    # never-started gate.
    assert outcome.hung is True
    assert outcome.exit_reason == "headless_turn_timeout"
    assert outcome.turn_ended is False
