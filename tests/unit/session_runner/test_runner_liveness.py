"""Runner liveness: role-aware turn timeout + subprocess-death detection.

The wedge-coverage replacement (plan #1924, task 5): the deleted PTY
frozen-frame detectors have no headless analog — liveness now comes from
the protocol:

* role-aware turn deadlines bound every turn — an activity-aware IDLE
  deadline plus an absolute wall-clock ceiling (#3289). The preempt
  watcher's timeout kill is covered in test_runner_preempt.py; this file
  covers the role-aware selection seam, the driver's own bounded-wait
  backstop, and the activity signal's UNKNOWN / clock-skew contracts, and
* a subprocess that dies (or hangs) without a ``result`` event classifies
  as ``exit_reason="error"`` — NEVER a clean completion (the #1916
  false-success regression net) — with a persona-safe user message (no raw
  exit strings reach the CEO).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from agent.session_runner.adapter import SessionRunnerAdapter
from agent.session_runner.role_driver import HeadlessRoleDriver, HeadlessTurnOutcome
from agent.session_runner.router import ExitReason, TurnFailure
from agent.session_runner.runner import (
    ENG_ABSOLUTE_TIMEOUT_S,
    ENG_IDLE_TIMEOUT_S,
    RUNNER_ERROR_USER_MESSAGE,
    TEAMMATE_TURN_TIMEOUT_S,
    SessionRunner,
    TurnDeadlines,
    deadlines_for,
)


class FakeSession:
    """Minimal AgentSession stand-in (session_events list + save capture).

    Carries the fenced execution record (durability #2494 / #2518) so the
    ``on_spawn`` wiring test below genuinely exercises the stamping path rather
    than no-op'ing against a session that has nowhere to put the fence.
    """

    def __init__(self):
        self.session_id = "sess-liveness-test"
        self.chat_id = 111
        self.telegram_message_id = 222
        self.session_events = None
        self.last_stdout_at = None
        self.saved_fields: list[list[str]] = []
        self.exec_pid = None
        self.pid_create_time = None
        self.exec_cwd = None
        self.exec_harness = None
        self.spawn_history = None

    def save(self, update_fields=None):
        self.saved_fields.append(list(update_fields or []))

    def stamp_execution_spawn(
        self, *, pid, create_time, cwd, harness, generation=None, agent_id=None
    ):
        """Faithful stand-in for ``AgentSession.stamp_execution_spawn``.

        Appends to ``spawn_history`` (newest == live fence), updates the
        denormalized scalars, and issues the same scoped partial save.
        """
        hist = self.spawn_history if isinstance(self.spawn_history, list) else []
        hist.append(
            {
                "pid": pid,
                "create_time": create_time,
                "cwd": cwd,
                "harness": harness,
                "generation": generation,
                "agent_id": agent_id,
            }
        )
        self.spawn_history = hist
        self.exec_pid = pid
        self.pid_create_time = create_time
        self.exec_cwd = cwd
        self.exec_harness = harness
        self.save(
            update_fields=[
                "exec_pid",
                "pid_create_time",
                "exec_cwd",
                "exec_harness",
                "spawn_history",
            ]
        )


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


_TEAMMATE_DEADLINES = TurnDeadlines(
    idle_s=TEAMMATE_TURN_TIMEOUT_S, absolute_s=TEAMMATE_TURN_TIMEOUT_S
)
_ENG_DEADLINES = TurnDeadlines(idle_s=ENG_IDLE_TIMEOUT_S, absolute_s=ENG_ABSOLUTE_TIMEOUT_S)


@pytest.mark.parametrize(
    "session_type,expected",
    [
        # Teammate sessions are conversational: one tight budget serves as
        # BOTH deadlines, so their observable behavior is unchanged by the
        # two-deadline model.
        ("teammate", _TEAMMATE_DEADLINES),
        ("TEAMMATE", _TEAMMATE_DEADLINES),
        ("  teammate  ", _TEAMMATE_DEADLINES),
        # Eng/PM sessions carry the Dev subagent's work inside the PM turn
        # (D1), so they get the activity-aware idle deadline plus a far-off
        # absolute ceiling.
        ("eng", _ENG_DEADLINES),
        ("pm", _ENG_DEADLINES),
        (None, _ENG_DEADLINES),
        ("", _ENG_DEADLINES),
    ],
)
def test_deadlines_for_role_table(session_type, expected):
    """Both deadlines resolve from the session type, as one value object."""
    resolved = deadlines_for(session_type)
    assert resolved == expected
    assert (resolved.idle_s, resolved.absolute_s) == (expected.idle_s, expected.absolute_s)


def test_runner_defaults_to_role_aware_timeout():
    """With no explicit deadline kwargs, the runner resolves BOTH deadlines
    from the role — not one shared ceiling."""
    eng_runner, _, _, _ = make_runner([], session_type="eng")
    tm_runner, _, _, _ = make_runner([], session_type="teammate")
    assert eng_runner._idle_timeout_s == ENG_IDLE_TIMEOUT_S
    assert eng_runner._absolute_timeout_s == ENG_ABSOLUTE_TIMEOUT_S
    assert tm_runner._idle_timeout_s == TEAMMATE_TURN_TIMEOUT_S
    assert tm_runner._absolute_timeout_s == TEAMMATE_TURN_TIMEOUT_S
    # The idle deadline is the operative limit for an eng turn; the ceiling
    # is a backstop that must sit strictly above it.
    assert eng_runner._absolute_timeout_s > eng_runner._idle_timeout_s


def test_explicit_turn_timeout_overrides_role_default():
    """The ctor seam is two explicit kwargs; each overrides independently."""
    both, _, _, _ = make_runner(
        [], session_type="eng", idle_timeout_s=12.5, absolute_timeout_s=99.0
    )
    assert both._idle_timeout_s == 12.5
    assert both._absolute_timeout_s == 99.0

    idle_only, _, _, _ = make_runner([], session_type="eng", idle_timeout_s=1.5)
    assert idle_only._idle_timeout_s == 1.5
    assert idle_only._absolute_timeout_s == ENG_ABSOLUTE_TIMEOUT_S

    abs_only, _, _, _ = make_runner([], session_type="teammate", absolute_timeout_s=7.0)
    assert abs_only._idle_timeout_s == TEAMMATE_TURN_TIMEOUT_S
    assert abs_only._absolute_timeout_s == 7.0


# --------------------------------------------------------------------------
# Subprocess-death detection (#1916 regression net)
# --------------------------------------------------------------------------


async def test_subprocess_death_classifies_error_never_completed():
    """A turn whose subprocess dies without a result event → exit_reason=error,
    persona-safe user message — never a clean completion (the #1916 class)."""
    dead_turn = HeadlessTurnOutcome(
        turn_ended=False,
        failure=TurnFailure(ExitReason.HEADLESS_SUBPROCESS_ERROR, "[Errno 32] broken pipe"),
    )
    runner, deliveries, session, _ = make_runner([dead_turn])
    summary = await runner.run("do the thing")

    assert summary.exit_reason is ExitReason.ERROR, "a dead subprocess must classify as error"
    assert summary.exit_reason not in (
        ExitReason.PM_COMPLETE,
        ExitReason.PM_USER,
        ExitReason.PM_NEEDS_HUMAN,
    ), "never a clean completion"
    # The exit_message preserves the legacy "reason: detail" wire format.
    assert summary.exit_message == "headless_subprocess_error: [Errno 32] broken pipe"
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
        failure=TurnFailure(ExitReason.HEADLESS_TURN_TIMEOUT),
    )
    runner, deliveries, _, _ = make_runner([hung_turn])
    summary = await runner.run("go")

    assert summary.exit_reason is ExitReason.ERROR
    assert deliveries == [RUNNER_ERROR_USER_MESSAGE]


async def test_missing_binary_classifies_error():
    """A missing claude binary is a deterministic infra failure — error, not
    a completion and not an unbounded retry loop."""
    missing = HeadlessTurnOutcome(
        reply_text="Error: CLI harness not found",
        turn_ended=False,
        failure=TurnFailure(ExitReason.HEADLESS_BINARY_MISSING),
    )
    runner, deliveries, _, driver = make_runner([missing])
    summary = await runner.run("go")

    assert summary.exit_reason is ExitReason.ERROR
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
    assert outcome.failure is not None
    assert outcome.failure.reason is ExitReason.HEADLESS_TURN_TIMEOUT
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
    assert outcome.failure is not None
    assert outcome.failure.reason is ExitReason.HEADLESS_SUBPROCESS_ERROR
    assert outcome.failure.detail == "claude exited -9"


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


def test_build_driver_wires_on_spawn_adapter(monkeypatch):
    """_build_driver wires ``on_spawn`` to the runner's ``_on_turn_spawn``,
    which stamps the fenced execution record (#2518 Job 1).

    Vacuity guard — this is the trap this test exists to avoid.
    ``_on_turn_spawn`` early-returns when ``_current_handle is None``
    (``runner.py``), and that is the state a freshly-built runner is in. A test
    that merely invokes the wired callback and asserts "no crash" therefore
    passes against a completely unwired runner AND against a runner whose
    stamping was deleted. So this test:

      1. asserts the callback is wired at all,
      2. installs a current turn handle — the state a real spawn fires in —
         and asserts the FULL fence lands, and
      3. pins the early return, proving step 2's assertions come from the
         stamping path rather than from a no-op.
    """
    from agent.session_runner.runner import _TurnHandle

    runner, session = _make_stdout_liveness_runner("sess-spawn-wiring")

    # (1) The adapter exists on the driver the runner actually built.
    assert runner._driver._on_spawn is not None
    assert runner._driver._on_spawn == runner._on_turn_spawn

    # (3) …but with no current handle it is deliberately inert.
    monkeypatch.setattr("agent.pid_fence.proc_create_time", lambda pid: 1738000000.5)
    runner._driver._on_spawn(4242)
    assert session.exec_pid is None, (
        "_on_turn_spawn early-returns without a current handle — any assertion "
        "made in this state is vacuous"
    )

    # (2) With a live turn handle, the wired callback stamps the full fence.
    monkeypatch.setattr("agent.session_runner.runner.os.getpgid", lambda pid: pid)
    runner._generation = 7
    runner._current_handle = _TurnHandle(generation=7)

    runner._driver._on_spawn(4242)

    assert session.exec_pid == 4242
    assert session.pid_create_time == 1738000000.5
    assert session.exec_cwd == "/tmp/wd"
    assert session.exec_harness == "claude"
    assert len(session.spawn_history) == 1
    assert session.spawn_history[0]["generation"] == 7
    assert [
        "exec_pid",
        "pid_create_time",
        "exec_cwd",
        "exec_harness",
        "spawn_history",
    ] in session.saved_fields


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
# turn's IDLE deadline, NOT by session-health's never-started gate (which
# correctly no longer fires once init stamps last_stdout_at).
# --------------------------------------------------------------------------


async def test_post_init_hang_is_caught_by_turn_deadline_not_never_started_gate(monkeypatch):
    """A subprocess that streams `init` (real output — last_stdout_at gets
    stamped via the production SessionRunner._on_init_composed ->
    _stamp_stdout_liveness path) and then hangs forever must NOT be caught by
    the never-started gate (it correctly does not fire, since sdk_ever_output
    is now True).

    Original intent, preserved: the never-started liveness gate is not what
    recovers this turn. New mechanism (#3289): the thing that recovers it is
    the watcher's **idle** deadline — the stream went silent after `init` and
    no tool-activity marker ever ticked, which is exactly the preempt
    predicate. The absolute ceiling is left at its role default, orders of
    magnitude away, so a preempt inside this test's lifetime can only have
    come from the idle deadline.

    Built via ``_make_stdout_liveness_runner`` (like its neighbors) so this
    exercises the real ``SessionRunner``/``_build_driver``/
    ``_on_init_composed``/``_stamp_stdout_liveness`` wiring, not a hand-rolled
    on_init stand-in.
    """
    from datetime import UTC, datetime, timedelta

    from agent.session_health import _never_started_past_grace
    from agent.session_runner.router import ExitReason as _ExitReason

    async def _init_then_hang(message, working_dir, **kwargs):
        on_init = kwargs.get("on_init")
        if on_init is not None:
            on_init({"type": "system", "subtype": "init", "session_id": "claude-uuid-hang"})
        await asyncio.sleep(30)  # never resolves within this test's lifetime
        return "never reached"

    runner, session = _make_stdout_liveness_runner(
        "sess-post-init-hang",
        harness_fn=_init_then_hang,
        idle_timeout_s=0.1,
        steer_poll_interval_s=0.02,
        term_grace_s=0.0,
    )
    # Resume-scalar persistence is covered by
    # test_build_driver_on_init_composes_resume_persist_and_stamp; stub it
    # here so this test stays focused on the liveness stamp + idle-deadline
    # path under test.
    monkeypatch.setattr(runner._adapter, "persist_resume_scalars", lambda **kw: None)

    summary = await asyncio.wait_for(runner.run("go"), timeout=10)

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

    # The turn IS still recovered — by the idle deadline, not the
    # never-started gate and not the absolute ceiling (which is the role
    # default, hours away, and cannot have fired in a sub-second test).
    assert summary.exit_reason is _ExitReason.TURN_TIMEOUT
    assert runner._absolute_timeout_s == ENG_ABSOLUTE_TIMEOUT_S
    assert runner._idle_timeout_s == 0.1
    timeout_records = [
        e
        for e in session.session_events
        if e["type"] == "runner_turn" and e["turn_end_source"] == "timeout"
    ]
    assert len(timeout_records) == 1


# --------------------------------------------------------------------------
# Deadline ordering + the UNKNOWN / clock-skew contracts on the activity
# signal (#3289). ``_turn_idle_seconds`` is the direct seam for the two
# signal-contract tests: it is the whole of the normalization step, so
# driving it beats driving the watcher around it.
# --------------------------------------------------------------------------


def _tool_activity_marker(base_dir, session_id: str, *, at: float) -> None:
    """Stamp the REAL hook-edge tool-activity marker for ``session_id``.

    Uses the production writer (``liveness_hook.stamp``) and the production
    path helper (``hook_edge.tool_activity_path``) rather than hand-rolling
    the file format, so a change to either side breaks this test instead of
    silently diverging from it.
    """
    import pathlib

    from agent.session_runner.hook_edge import tool_activity_path
    from agent.session_runner.liveness_hook import stamp

    session_dir = pathlib.Path(base_dir) / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    stamp(str(tool_activity_path(session_dir / "pm_hook_edges.ndjson")), at)


def test_driver_backstop_exceeds_largest_watcher_deadline():
    """The driver's own ``asyncio.wait_for`` backstop must strictly exceed the
    LARGEST deadline the watcher can fire on, so the watcher's graceful
    timeout-preempt always wins and the backstop only ever catches a watcher
    that itself failed.

    Asserted as a relation over whatever the runner actually resolved — never
    a restatement of the arithmetic — so a future constant change (or a new
    third deadline) cannot silently invert the ordering.
    """
    for session_type in ("eng", "teammate"):
        runner, _ = _make_stdout_liveness_runner(
            f"sess-backstop-{session_type}", session_type=session_type
        )
        largest = max(runner._idle_timeout_s, runner._absolute_timeout_s)
        assert runner._driver.turn_timeout_s > largest, (
            f"driver backstop must outlast every watcher deadline ({session_type})"
        )

    # The backstop is derived from the absolute ceiling, so "largest watcher
    # deadline == absolute ceiling" is a PRECONDITION of the ordering above,
    # not a coincidence. Pin it for every role so a future role table that
    # hands out an idle deadline above its own ceiling fails here rather
    # than silently inverting the watcher/backstop ordering.
    for session_type in ("eng", "teammate", "pm", None):
        resolved = deadlines_for(session_type)
        assert resolved.idle_s <= resolved.absolute_s

    # And it holds for an explicitly overridden pair too.
    runner, _ = _make_stdout_liveness_runner(
        "sess-backstop-override", idle_timeout_s=500.0, absolute_timeout_s=5000.0
    )
    assert runner._driver.turn_timeout_s > max(runner._idle_timeout_s, runner._absolute_timeout_s)


def test_absent_tool_activity_marker_never_shortens_deadline(tmp_path, monkeypatch):
    """UNKNOWN contract (#2662 precedent): a missing tool-activity marker is
    NOT evidence of a wedge. It drops out, leaving the stream-only idle value
    exactly as it was before the signal existed — never shorter."""
    import agent.session_runner.adapter as adapter_module

    monkeypatch.setattr(adapter_module, "_hook_edge_base_dir", lambda: str(tmp_path))
    runner, _ = _make_stdout_liveness_runner("sess-unknown-marker")

    now_mono = 10_000.0
    runner._last_activity_mono = now_mono - 1234.0
    stream_only = now_mono - runner._last_activity_mono

    # No marker exists at all (the foreign-repo / misconfigured-spawn case).
    from agent.session_runner.liveness import tool_activity_ts

    assert tool_activity_ts("sess-unknown-marker") is None
    assert runner._turn_idle_seconds(now_mono) == stream_only

    # A marker that exists but is STALE must not shorten the budget either:
    # the combined idle can never exceed the stream-only value.
    _tool_activity_marker(tmp_path, "sess-unknown-marker", at=time.time() - 9999.0)
    assert runner._turn_idle_seconds(now_mono) == stream_only

    # And an unreadable/garbage marker reads UNKNOWN, not "wedged".
    (tmp_path / "sess-unknown-marker" / "pm_hook_edges.toolactivity").write_text("not-a-float")
    assert runner._turn_idle_seconds(now_mono) == stream_only


def test_future_stamped_marker_reads_as_just_active(tmp_path, monkeypatch):
    """Clock-skew guard: the hook marker is a WALL-CLOCK epoch while the
    stream stamp is monotonic. A marker stamped in the future must read as
    "just active" (0.0) and must never produce a negative or absurd idle."""
    import agent.session_runner.adapter as adapter_module

    monkeypatch.setattr(adapter_module, "_hook_edge_base_dir", lambda: str(tmp_path))
    runner, _ = _make_stdout_liveness_runner("sess-clock-skew")

    now_mono = 10_000.0
    runner._last_activity_mono = now_mono - 5000.0  # stream long silent
    _tool_activity_marker(tmp_path, "sess-clock-skew", at=time.time() + 3600.0)

    idle = runner._turn_idle_seconds(now_mono)
    assert idle == 0.0
    assert idle >= 0.0
    assert idle < runner._idle_timeout_s, "a skewed marker must not trip the deadline"
