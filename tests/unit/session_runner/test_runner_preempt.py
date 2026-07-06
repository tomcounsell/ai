"""Steer-preempt watcher: generation guard, kill escalation, isolation (task 2).

Covers D4 + Race 1 with a killable fake driver and injected signal functions:

* Steer preempt: SIGTERM the turn, drain the steer, resume with it injected;
  turn record carries ``turn_end_source="preempted"``.
* Generation-token guard: a stale handle (prior generation) is never killed;
  a steer landing while the turn completes drains at the boundary.
* SIGTERM → grace → SIGKILL escalation.
* Timeout expiry is a graceful preempt (``turn_end_source="timeout"``,
  persona-safe needs-attention message), not an error.
* A watcher exception never kills the runner loop.
* Empty steers drained mid-turn are ignored (turn not killed).
* Kill-before-spawn falls back to cooperative task cancel.
"""

from __future__ import annotations

import asyncio
import signal

from agent.session_runner.adapter import SessionRunnerAdapter
from agent.session_runner.role_driver import HeadlessTurnOutcome
from agent.session_runner.runner import (
    TIMEOUT_NEEDS_ATTENTION_MESSAGE,
    SessionRunner,
    _TurnHandle,
)
from tests.unit.session_runner.test_runner_turns import FakeSession

FAST = {
    "steer_poll_interval_s": 0.05,
    "steer_debounce_s": 0.05,
    "term_grace_s": 0.5,
}


class KillableDriver:
    """Fake driver whose 'subprocess' runs until a kill signal arrives.

    ``attach(runner)`` wires the spawn callback so the runner records
    PID/PGID before awaiting (Race 2). The injected kill functions set
    ``kill_event`` to emulate the real subprocess dying on signal.
    """

    def __init__(self, script=None, *, pid=4242, hang_first=True):
        self.script = list(script or [])
        self.calls: list[str] = []
        self.kill_event = asyncio.Event()
        self.pid = pid
        self.hang_first = hang_first
        self.runner: SessionRunner | None = None
        self._first = True

    def attach(self, runner: SessionRunner) -> None:
        self.runner = runner

    async def run_turn(self, message: str) -> HeadlessTurnOutcome:
        self.calls.append(message)
        if self._first and self.hang_first:
            self._first = False
            if self.runner is not None and self.pid is not None:
                self.runner._on_turn_spawn(self.pid)
            await self.kill_event.wait()
            # Emulates the harness observing the signaled subprocess.
            return HeadlessTurnOutcome(
                reply_text="", exit_reason="headless_subprocess_error: signaled"
            )
        reply = self.script.pop(0) if self.script else "[/user]\ndone"
        return HeadlessTurnOutcome(reply_text=reply, turn_ended=True, turn_end_source="result")


def make_preempt_runner(driver, *, steering, deliveries=None, **kwargs):
    session = FakeSession()
    deliveries = deliveries if deliveries is not None else []

    def send_cb(chat_id, payload, reply_to, agent_session):
        deliveries.append(payload)

    adapter = SessionRunnerAdapter(
        session, "test-proj", "telegram", resolve_callbacks=lambda pk, t: (send_cb, None)
    )
    runner = SessionRunner(
        agent_session=session,
        adapter=adapter,
        working_dir="/tmp/wd",
        driver=driver,
        steering_pop_fn=steering,
        **{**FAST, **kwargs},
    )
    if hasattr(driver, "attach"):
        driver.attach(runner)
    return runner, deliveries, session


# --------------------------------------------------------------------------
# Steer preempt end-to-end
# --------------------------------------------------------------------------


async def test_steer_preempt_kills_turn_and_resumes_with_steer():
    driver = KillableDriver(script=["[/user]\nadjusted per your steer"])
    kills: list[tuple[int, int]] = []

    def fake_kill(pid, sig):
        kills.append((pid, sig))
        driver.kill_event.set()

    pops = [[], [{"text": "actually target staging"}]]

    def steering():
        return pops.pop(0) if pops else []

    runner, deliveries, session = make_preempt_runner(
        driver,
        steering=steering,
        kill_fn=fake_kill,
        killpg_fn=fake_kill,
        pid_alive_fn=lambda pid: False,
    )
    summary = await runner.run("original task")

    # The in-flight turn was SIGTERMed…
    assert kills[0][1] == signal.SIGTERM
    # …the steer was injected into the resumed turn…
    assert driver.calls[1] == "actually target staging"
    # …and the resumed turn's answer was delivered.
    assert deliveries == ["adjusted per your steer"]
    assert summary.exit_reason == "pm_user"
    # Turn record: preempted, with PID recorded pre-await (Race 2).
    events = {e["type"]: e for e in session.session_events}
    assert events["runner_turn_spawned"]["pid"] == 4242
    preempt_records = [
        e
        for e in session.session_events
        if e["type"] == "runner_turn" and e["turn_end_source"] == "preempted"
    ]
    assert len(preempt_records) == 1


async def test_sigterm_then_sigkill_escalation():
    """A subprocess that survives the SIGTERM grace window is SIGKILLed."""
    driver = KillableDriver(script=["[/user]\nok"])
    kills: list[tuple[int, int]] = []

    def fake_kill(pid, sig):
        kills.append((pid, sig))
        if sig == signal.SIGKILL:
            driver.kill_event.set()  # only SIGKILL fells this one

    pops = [[], [{"text": "steer now"}]]
    runner, _, _ = make_preempt_runner(
        driver,
        steering=lambda: pops.pop(0) if pops else [],
        kill_fn=fake_kill,
        killpg_fn=fake_kill,
        pid_alive_fn=lambda pid: True,  # stays alive through the grace window
        term_grace_s=0.2,
    )
    await runner.run("task")
    sigs = [s for _, s in kills]
    assert sigs == [signal.SIGTERM, signal.SIGKILL]


async def test_kill_before_spawn_cancels_task_cooperatively():
    """A steer arriving before the subprocess exists cancels the turn task."""

    class NeverSpawnsDriver:
        def __init__(self):
            self.calls = []
            self._first = True

        async def run_turn(self, message):
            self.calls.append(message)
            if self._first:
                self._first = False
                await asyncio.sleep(30)  # no on_spawn — nothing to signal
            return HeadlessTurnOutcome(
                reply_text="[/user]\nafter steer", turn_ended=True, turn_end_source="result"
            )

    driver = NeverSpawnsDriver()
    pops = [[], [{"text": "redirect"}]]
    kills = []
    runner, deliveries, _ = make_preempt_runner(
        driver,
        steering=lambda: pops.pop(0) if pops else [],
        kill_fn=lambda p, s: kills.append((p, s)),
        killpg_fn=lambda p, s: kills.append((p, s)),
    )
    summary = await runner.run("task")
    assert kills == []  # nothing to signal — cooperative cancel path
    assert driver.calls[1] == "redirect"
    assert deliveries == ["after steer"]
    assert summary.exit_reason == "pm_user"


# --------------------------------------------------------------------------
# Generation-token guard (Race 1)
# --------------------------------------------------------------------------


async def test_stale_generation_handle_is_never_killed():
    """_kill_turn refuses a handle whose generation is not current."""
    driver = KillableDriver(hang_first=False)
    kills = []
    runner, _, _ = make_preempt_runner(
        driver,
        steering=lambda: [],
        kill_fn=lambda p, s: kills.append((p, s)),
        killpg_fn=lambda p, s: kills.append((p, s)),
    )
    stale = _TurnHandle(generation=1, pid=999, pgid=999)
    runner._generation = 2  # a newer turn is current
    task = asyncio.create_task(asyncio.sleep(5))
    try:
        await runner._kill_turn(stale, task, cause="steer")
        assert stale.killed is False
        assert kills == []
    finally:
        task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await task


async def test_steer_during_completion_drains_at_boundary_no_kill():
    """A steer landing as the turn completes naturally (kill-at-boundary
    race) is NOT a kill — it drains at the boundary that is already
    occurring."""

    class SlowishDriver:
        def __init__(self):
            self.calls = []

        async def run_turn(self, message):
            self.calls.append(message)
            await asyncio.sleep(0.15)  # completes during the watcher debounce
            return HeadlessTurnOutcome(
                reply_text="[/user]\nnatural finish", turn_ended=True, turn_end_source="result"
            )

    driver = SlowishDriver()
    kills = []
    pops = [[], [{"text": "late steer"}]]
    runner, deliveries, _ = make_preempt_runner(
        driver,
        steering=lambda: pops.pop(0) if pops else [],
        kill_fn=lambda p, s: kills.append((p, s)),
        killpg_fn=lambda p, s: kills.append((p, s)),
        steer_debounce_s=0.3,  # turn finishes inside the debounce window
    )
    summary = await runner.run("task")
    assert kills == []  # generation/done guard held
    assert deliveries == ["natural finish"]
    assert summary.exit_reason == "pm_user"
    # The undelivered steer is retained for a future boundary, not lost.
    assert runner._pending_steers and runner._pending_steers[0]["text"] == "late steer"


# --------------------------------------------------------------------------
# Timeout expiry = graceful preempt
# --------------------------------------------------------------------------


async def test_timeout_expiry_is_graceful_preempt_not_error():
    driver = KillableDriver()
    kills = []

    def fake_kill(pid, sig):
        kills.append((pid, sig))
        driver.kill_event.set()

    runner, deliveries, session = make_preempt_runner(
        driver,
        steering=lambda: [],
        kill_fn=fake_kill,
        killpg_fn=fake_kill,
        pid_alive_fn=lambda pid: False,
        turn_timeout_s=0.12,
    )
    summary = await runner.run("long task")
    assert kills and kills[0][1] == signal.SIGTERM
    assert summary.exit_reason == "turn_timeout"
    assert summary.exit_reason != "error"
    assert deliveries == [TIMEOUT_NEEDS_ATTENTION_MESSAGE]
    timeout_records = [
        e
        for e in session.session_events
        if e["type"] == "runner_turn" and e["turn_end_source"] == "timeout"
    ]
    assert len(timeout_records) == 1


# --------------------------------------------------------------------------
# Watcher isolation + empty steers
# --------------------------------------------------------------------------


async def test_watcher_exception_does_not_kill_the_turn(caplog):
    """An exception inside the watcher is logged and the turn stays intact."""

    class SlowDriver:
        async def run_turn(self, message):
            await asyncio.sleep(0.2)
            return HeadlessTurnOutcome(
                reply_text="[/user]\nintact", turn_ended=True, turn_end_source="result"
            )

    def broken_steering():
        raise RuntimeError("redis fell over")

    runner, deliveries, _ = make_preempt_runner(SlowDriver(), steering=broken_steering)
    with caplog.at_level("WARNING"):
        summary = await runner.run("task")
    assert deliveries == ["intact"]
    assert summary.exit_reason == "pm_user"
    assert any("watcher" in r.message for r in caplog.records)


async def test_empty_steer_mid_turn_is_ignored_turn_not_killed():
    """Whitespace-only steers drained mid-turn never trigger a preempt."""

    class SlowDriver:
        async def run_turn(self, message):
            await asyncio.sleep(0.25)
            return HeadlessTurnOutcome(
                reply_text="[/user]\nuninterrupted", turn_ended=True, turn_end_source="result"
            )

    kills = []
    runner, deliveries, _ = make_preempt_runner(
        SlowDriver(),
        steering=lambda: [{"text": "   "}],  # empty steer on every poll
        kill_fn=lambda p, s: kills.append((p, s)),
        killpg_fn=lambda p, s: kills.append((p, s)),
    )
    summary = await runner.run("task")
    assert kills == []
    assert deliveries == ["uninterrupted"]
    assert summary.exit_reason == "pm_user"
