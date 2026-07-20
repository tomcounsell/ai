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
import contextlib
import signal

from agent.session_runner.adapter import SessionRunnerAdapter
from agent.session_runner.role_driver import HeadlessTurnOutcome
from agent.session_runner.router import ExitReason, TurnFailure
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
                reply_text="",
                failure=TurnFailure(ExitReason.HEADLESS_SUBPROCESS_ERROR, "signaled"),
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
    assert summary.exit_reason is ExitReason.PM_USER
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
    assert summary.exit_reason is ExitReason.PM_USER


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
    pushed: list[dict] = []
    pops = [[], [{"text": "late steer"}]]
    runner, deliveries, _ = make_preempt_runner(
        driver,
        steering=lambda: pops.pop(0) if pops else [],
        steering_push_fn=lambda m: pushed.append(m),
        kill_fn=lambda p, s: kills.append((p, s)),
        killpg_fn=lambda p, s: kills.append((p, s)),
        steer_debounce_s=0.3,  # turn finishes inside the debounce window
    )
    summary = await runner.run("task")
    assert kills == []  # generation/done guard held
    assert deliveries == ["natural finish"]
    assert summary.exit_reason is ExitReason.PM_USER
    # The undelivered steer is pushed back to the steering list on loop exit
    # (PR #1930 review, A7) — the executor's leftover-steering re-enqueue
    # drains only the Redis list, so retaining it in-memory would drop it.
    assert [m["text"] for m in pushed] == ["late steer"]
    assert runner._pending_steers == []


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
    assert summary.exit_reason is ExitReason.PM_USER
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
    assert summary.exit_reason is ExitReason.PM_USER


# --------------------------------------------------------------------------
# Cancellation-proof teardown reap (issue #1938)
# --------------------------------------------------------------------------


class HangingSpawnDriver:
    """A driver that records a spawned pid, signals ``spawned``, then hangs.

    Emulates a live ``claude -p`` in its own process group that the runner's
    ``_run_one_turn`` finally must reap when the run task is torn down.
    """

    def __init__(self, pid=5555):
        self.pid = pid
        self.runner: SessionRunner | None = None
        self.spawned = asyncio.Event()

    def attach(self, runner: SessionRunner) -> None:
        self.runner = runner

    async def run_turn(self, message):
        if self.runner is not None:
            self.runner._on_turn_spawn(self.pid)
        self.spawned.set()
        await asyncio.sleep(3600)  # hang until externally cancelled
        return HeadlessTurnOutcome(reply_text="[/user]\ndone", turn_ended=True)


async def test_external_cancel_reaps_turn_process_group(monkeypatch):
    """External cancellation of the run task SIGKILLs the turn's process group.

    This is the load-bearing fix (#1938): a cancelled ``SessionHandle.task``
    unwinds the runner coroutine, and the ``_run_one_turn`` finally must reap
    the detached group so no live ``claude -p`` orphans to the worker.
    """
    # ``_on_turn_spawn`` derives the pgid via os.getpgid; give it a real value.
    monkeypatch.setattr("agent.session_runner.runner.os.getpgid", lambda pid: pid)
    driver = HangingSpawnDriver(pid=5555)
    killpg_calls: list[tuple[int, int]] = []

    def fake_killpg(pgid, sig):
        killpg_calls.append((pgid, sig))
        if sig == 0:
            # Group is gone after SIGKILL — confirm-poll sees it dead at once.
            raise ProcessLookupError

    runner, _, session = make_preempt_runner(
        driver,
        steering=lambda: [],
        killpg_fn=fake_killpg,
        kill_fn=lambda p, s: None,
    )
    task = asyncio.create_task(runner.run("task"))
    await asyncio.wait_for(driver.spawned.wait(), timeout=5)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # The turn's process group was SIGKILLed in the finally (pgid == pid).
    assert (5555, signal.SIGKILL) in killpg_calls
    # Confirmed dead → no reap-failed marker was written.
    assert not any(e["type"] == "runner_reap_failed" for e in (session.session_events or []))
    # claude_pid was cleared on turn exit.
    assert getattr(session, "claude_pid", "unset") is None


def _reap_runner(*, killpg_fn, kill_fn=None, pid_alive_fn=None, enum_subtree_fn=None):
    """Build a bare runner (no turn) for direct ``_reap_turn_group`` tests.

    ``enum_subtree_fn`` defaults to an empty snapshot so escalation is a no-op
    unless a test opts into a subtree (issue #2146).
    """
    session = FakeSession()
    adapter = SessionRunnerAdapter(
        session, "test-proj", "telegram", resolve_callbacks=lambda pk, t: (lambda *a: None, None)
    )
    runner = SessionRunner(
        agent_session=session,
        adapter=adapter,
        working_dir="/tmp/wd",
        driver=KillableDriver(hang_first=False),
        steering_pop_fn=lambda: [],
        killpg_fn=killpg_fn,
        kill_fn=kill_fn,
        pid_alive_fn=pid_alive_fn,
        enum_subtree_fn=enum_subtree_fn or (lambda pid: []),
    )
    return runner, session


def test_reap_turn_group_no_pid_is_noop():
    """A handle with no recorded pid has nothing to reap → confirmed dead."""
    calls: list = []
    runner, _ = _reap_runner(killpg_fn=lambda *a: calls.append(a))
    confirmed, pgid, survivors = runner._reap_turn_group(_TurnHandle(generation=1, pid=None))
    assert confirmed is True
    assert pgid is None
    assert survivors == []
    assert calls == []


def test_reap_turn_group_second_cancel_mid_reap_still_confirms_dead():
    """A synchronous SIGKILL + confirm cannot be aborted by a re-delivered
    cancel — the group stays 'alive' for one poll then dies, and the reap
    still returns confirmed-dead (uninterruptible synchronous path)."""
    probes = {"n": 0}

    def fake_killpg(pgid, sig):
        if sig == 0:
            probes["n"] += 1
            if probes["n"] >= 2:
                raise ProcessLookupError  # dead on the second poll
            return  # still alive on the first poll
        # SIGKILL delivered.

    runner, _ = _reap_runner(killpg_fn=fake_killpg)
    confirmed, pgid, survivors = runner._reap_turn_group(
        _TurnHandle(generation=1, pid=4242, pgid=4242)
    )
    assert confirmed is True
    assert pgid == 4242
    assert survivors == []
    assert probes["n"] >= 2


def test_reap_turn_group_unkillable_group_reports_not_confirmed(monkeypatch):
    """A group that never dies within the cap → not confirmed (drives the
    runner_reap_failed marker + WARNING)."""
    # Collapse the confirm cap so the test does not sleep ~1s.
    monkeypatch.setattr("agent.session_runner.runner.REAP_CONFIRM_TIMEOUT_S", 0.05)
    monkeypatch.setattr("agent.session_runner.runner.REAP_CONFIRM_POLL_S", 0.01)

    def fake_killpg(pgid, sig):
        # Never dies: signal 0 always reports alive (returns without raising).
        return

    runner, _ = _reap_runner(killpg_fn=fake_killpg)
    confirmed, pgid, survivors = runner._reap_turn_group(
        _TurnHandle(generation=1, pid=4242, pgid=4242)
    )
    assert confirmed is False
    assert pgid == 4242
    # Empty subtree snapshot → escalation is a no-op, no survivors to persist.
    assert survivors == []


async def test_unkillable_group_records_reap_failed_event_and_warns(monkeypatch, caplog):
    """When the teardown reap cannot confirm death, the finally emits ONE
    durable ``runner_reap_failed`` session event AND a WARNING naming the
    session (the operator-visibility side effect Fix 3 keys on)."""
    monkeypatch.setattr("agent.session_runner.runner.os.getpgid", lambda pid: pid)
    monkeypatch.setattr("agent.session_runner.runner.REAP_CONFIRM_TIMEOUT_S", 0.05)
    monkeypatch.setattr("agent.session_runner.runner.REAP_CONFIRM_POLL_S", 0.01)
    driver = HangingSpawnDriver(pid=7777)

    def fake_killpg(pgid, sig):
        return  # never dies

    runner, _, session = make_preempt_runner(
        driver,
        steering=lambda: [],
        killpg_fn=fake_killpg,
        kill_fn=lambda p, s: None,
        enum_subtree_fn=lambda pid: [],  # deterministic: no descendants
    )
    task = asyncio.create_task(runner.run("task"))
    await asyncio.wait_for(driver.spawned.wait(), timeout=5)
    with caplog.at_level("WARNING"):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    reap_failed = [e for e in (session.session_events or []) if e["type"] == "runner_reap_failed"]
    assert len(reap_failed) == 1
    assert reap_failed[0]["pgid"] == 7777
    assert reap_failed[0]["survivor_pids"] == []
    assert any("reap" in r.message.lower() for r in caplog.records)


# === Issue #2146: per-PID subtree escalation on EPERM/unconfirmed group death ===


def test_reap_escalates_per_pid_on_eperm_and_persists_survivor(monkeypatch):
    """killpg raises EPERM → the pre-kill subtree snapshot is swept per-PID; a
    child that survives the sweep is returned as a survivor and persisted to the
    durable kill-list (AC1 + AC2 + AC4)."""
    monkeypatch.setattr("agent.session_runner.runner.REAP_CONFIRM_TIMEOUT_S", 0.05)
    monkeypatch.setattr("agent.session_runner.runner.REAP_CONFIRM_POLL_S", 0.01)

    def fake_killpg(pgid, sig):
        if sig == 0:
            return  # group probe: report alive (never confirmed dead)
        raise PermissionError(1, "Operation not permitted")  # EPERM on SIGKILL

    per_pid_kills: list = []
    # child 501 dies after the SIGKILL; child 502 (the setsid'd pytest) survives.
    alive = {501: True, 502: True}

    def fake_kill(pid, sig):
        per_pid_kills.append((pid, sig))
        if pid == 501:
            alive[501] = False  # 501 dies

    def fake_pid_alive(pid):
        return alive.get(pid, False)

    snapshot = [(501, 111.0), (502, 222.0)]
    persisted: list = []
    monkeypatch.setattr(
        "agent.reap_killlist.add", lambda entries: persisted.extend(list(entries)) or len(persisted)
    )

    runner, _ = _reap_runner(
        killpg_fn=fake_killpg,
        kill_fn=fake_kill,
        pid_alive_fn=fake_pid_alive,
        enum_subtree_fn=lambda pid: snapshot,
    )
    handle = _TurnHandle(generation=1, pid=4242, pgid=4242)
    confirmed, pgid, survivors = runner._reap_turn_group(handle)

    assert confirmed is False
    assert pgid == 4242
    # The per-PID sweep targeted exactly the snapshot PIDs.
    assert {p for p, _ in per_pid_kills} == {501, 502}
    # Only the surviving setsid child (502) is returned + its create_time rides along.
    assert survivors == [(502, 222.0, 4242)]

    # The reap-failed record persists the survivor to the kill-list.
    runner._record_reap_failed(handle, pgid, survivors)
    assert len(persisted) == 1
    assert persisted[0][0] == 502  # pid
    assert persisted[0][1] == 222.0  # create_time


def test_reap_happy_path_skips_escalation_and_snapshot_sweep(monkeypatch):
    """Group SIGKILL succeeds and confirm reports dead on the first probe → NO
    per-PID sweep, NO kill-list persistence. The snapshot is taken once (it must
    precede the kill) but the escalation path is never entered (regression
    guard)."""
    monkeypatch.setattr("agent.session_runner.runner.REAP_CONFIRM_TIMEOUT_S", 0.05)
    monkeypatch.setattr("agent.session_runner.runner.REAP_CONFIRM_POLL_S", 0.01)

    def fake_killpg(pgid, sig):
        if sig == 0:
            raise ProcessLookupError  # group gone → confirmed dead on first probe
        return  # SIGKILL succeeds

    per_pid_kills: list = []
    enum_calls = {"n": 0}

    def counting_enum(pid):
        enum_calls["n"] += 1
        return [(501, 111.0)]

    persisted: list = []
    monkeypatch.setattr("agent.reap_killlist.add", lambda entries: persisted.extend(list(entries)))

    runner, _ = _reap_runner(
        killpg_fn=fake_killpg,
        kill_fn=lambda p, s: per_pid_kills.append((p, s)),
        pid_alive_fn=lambda pid: True,
        enum_subtree_fn=counting_enum,
    )
    confirmed, pgid, survivors = runner._reap_turn_group(
        _TurnHandle(generation=1, pid=4242, pgid=4242)
    )

    assert confirmed is True
    assert survivors == []
    assert per_pid_kills == []  # per-PID sweep never ran
    assert persisted == []  # nothing persisted
    assert enum_calls["n"] <= 1  # snapshot taken at most once, pre-kill
