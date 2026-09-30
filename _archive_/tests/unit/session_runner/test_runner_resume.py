"""Simple resume (D3): four-scalar consumption, capture-at-init, sidechain capture.

Plan #1924 task 3 (build-resume). Covers:

* Four-scalar consumption: valid scalars seed ``--resume`` + skip prime and
  reintroduce ``dev_agent_id`` in the resumed first message.
* Validation (Race 3): malformed UUID, missing/nonexistent/mismatched
  ``runner_cwd``, garbage ``dev_agent_id`` → discard, cold start with prime,
  no crash.
* Stale-UUID fallback context: a seeded (skip-prime) driver still hands the
  harness a prime-prefixed full-context message for the cold retry.
* Capture-at-init (Race 5): a fake CLI that emits ``system/init`` then hangs
  leaves the NEW turn's id persisted after preempt; the next ``--resume`` is
  built with it.
* ``dev_agent_id`` structural sidechain capture — after a turn and after a
  preempt; never from PM prose.
* Turn-history mirror: shape ``{ts, actor, text}``, length cap, never
  consulted for resume.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import uuid

import pytest

from agent.session_runner.adapter import (
    SessionRunnerAdapter,
    sidechain_agent_ids,
)
from agent.session_runner.role_driver import HeadlessRoleDriver, HeadlessTurnOutcome
from agent.session_runner.runner import (
    TURN_HISTORY_MAX_CHARS,
    ResumeContext,
    SessionRunner,
)
from tests.unit.session_runner.test_runner_turns import FakeSession, ScriptedDriver

VALID_UUID = "12345678-abcd-4ef0-9876-0123456789ab"


class SeedableDriver(ScriptedDriver):
    """Scripted driver that records seed_resume calls (fake resume target)."""

    def __init__(self, script):
        super().__init__(script)
        self.seeded_with: str | None = None
        self.claude_session_id: str | None = None

    def seed_resume(self, claude_session_id: str) -> None:
        self.seeded_with = claude_session_id
        self.claude_session_id = claude_session_id


def make_resume_runner(script, *, resume, working_dir, projects_root=None, **kwargs):
    session = FakeSession()
    deliveries: list[str] = []

    def send_cb(chat_id, payload, reply_to, agent_session):
        deliveries.append(payload)

    adapter = SessionRunnerAdapter(
        session, "test-proj", "telegram", resolve_callbacks=lambda pk, t: (send_cb, None)
    )
    driver = SeedableDriver(script)
    runner = SessionRunner(
        agent_session=session,
        adapter=adapter,
        working_dir=working_dir,
        driver=driver,
        resume=resume,
        steering_pop_fn=lambda: [],
        projects_root=projects_root,
        **kwargs,
    )
    return runner, driver, deliveries, session


# --------------------------------------------------------------------------
# Four-scalar consumption
# --------------------------------------------------------------------------


async def test_valid_scalars_seed_resume_and_reintroduce_dev_agent(tmp_path):
    ctx = ResumeContext(
        claude_session_id=VALID_UUID,
        dev_agent_id="agent-dev123",
        runner_cwd=str(tmp_path),
        claude_version="2.1.201",
    )
    runner, driver, _, _ = make_resume_runner(
        ["[/user]\nresumed fine"], resume=ctx, working_dir=str(tmp_path)
    )
    assert driver.seeded_with == VALID_UUID  # --resume seeded, prime skipped
    summary = await runner.run("please continue")
    assert summary.exit_reason == "pm_user"
    # The resumed first message reintroduces the SAME dev agent id.
    first = driver.calls[0]
    assert "agent-dev123" in first
    assert "SAME agent" in first
    assert first.endswith("please continue")


async def test_resume_without_dev_agent_passes_message_verbatim(tmp_path):
    ctx = ResumeContext(claude_session_id=VALID_UUID, runner_cwd=str(tmp_path))
    runner, driver, _, _ = make_resume_runner(
        ["[/user]\nok"], resume=ctx, working_dir=str(tmp_path)
    )
    await runner.run("the user reply")
    assert driver.calls[0] == "the user reply"


# --------------------------------------------------------------------------
# Validation → cold start (Race 3), no crash
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ctx_kwargs",
    [
        {"claude_session_id": "not-a-uuid"},  # malformed UUID
        {"claude_session_id": None},  # missing UUID
        {"claude_session_id": VALID_UUID, "runner_cwd": None},  # missing cwd
        {
            "claude_session_id": VALID_UUID,
            "runner_cwd": f"/nonexistent/gone-{uuid.uuid4().hex}",
        },  # cwd vanished (worktree GC'd)
        {
            "claude_session_id": VALID_UUID,
            "runner_cwd": "MISMATCH",  # replaced with a real-but-different dir below
        },
        {
            "claude_session_id": VALID_UUID,
            "runner_cwd": "SELF",
            "dev_agent_id": "rm -rf /; not an id",  # garbage dev_agent_id
        },
    ],
)
async def test_garbage_scalars_cold_start_with_prime(tmp_path, ctx_kwargs):
    if ctx_kwargs.get("runner_cwd") == "MISMATCH":
        other = tmp_path / "other"
        other.mkdir()
        ctx_kwargs["runner_cwd"] = str(other)
    elif ctx_kwargs.get("runner_cwd") == "SELF":
        ctx_kwargs["runner_cwd"] = str(tmp_path)
    ctx = ResumeContext(**ctx_kwargs)
    runner, driver, _, _ = make_resume_runner(
        ["[/user]\ncold started"], resume=ctx, working_dir=str(tmp_path)
    )
    # Discarded: never seeded, cold start (prime is the driver's business).
    assert driver.seeded_with is None
    assert runner._resume_active is False
    summary = await runner.run("go")
    assert summary.exit_reason == "pm_user"
    assert driver.calls[0] == "go"  # no dev-continuation prefix injected


# --------------------------------------------------------------------------
# Seeded driver: skip-prime + prime-prefixed stale-UUID fallback context
# --------------------------------------------------------------------------


async def test_seeded_driver_skips_prime_and_resumes(tmp_path):
    calls = []

    async def harness(message, working_dir, **kwargs):
        calls.append({"message": message, **kwargs})
        return "resumed reply"

    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-resume-1",
        working_dir=str(tmp_path),
        harness_fn=harness,
    )
    driver.seed_resume(VALID_UUID)
    outcome = await driver.run_turn("continue please")
    assert outcome.reply_text == "resumed reply"
    # Skip prime: the message is NOT slash-prefixed…
    assert calls[0]["message"] == "continue please"
    # …the turn rides --resume on the seeded uuid…
    assert calls[0]["prior_uuid"] == VALID_UUID
    # …and the stale-UUID fallback context IS prime-prefixed, so the only
    # recovery tier cold-starts with the persona.
    assert calls[0]["full_context_message"].startswith("/roles:prime-pm-role")
    assert calls[0]["full_context_message"].endswith("continue please")


# --------------------------------------------------------------------------
# Capture-at-init (Race 5)
# --------------------------------------------------------------------------


NEW_TURN_UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


async def test_capture_at_init_persists_before_result_and_survives_preempt(tmp_path):
    """Fake CLI emits system/init then hangs; after preempt the persisted id
    is the NEW turn's id, and the next --resume is built with it."""
    session = FakeSession()
    deliveries: list[str] = []
    adapter = SessionRunnerAdapter(
        session,
        "test-proj",
        "telegram",
        resolve_callbacks=lambda pk, t: (
            lambda c, p, r, s: deliveries.append(p),
            None,
        ),
    )
    kill_event = asyncio.Event()
    harness_calls: list[dict] = []

    async def fake_cli(message, working_dir, **kwargs):
        harness_calls.append({"message": message, **kwargs})
        if len(harness_calls) == 1:
            # Emit system/init (the driver adopts the id and the runner
            # persists it), then hang until killed — never a result event.
            kwargs["on_init"]({"type": "system", "subtype": "init", "session_id": NEW_TURN_UUID})
            await kill_event.wait()
            return ""  # signaled subprocess: empty, nonzero-shaped
        return "[/user]\nback on track"

    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-race5",
        working_dir=str(tmp_path),
        harness_fn=fake_cli,
    )
    pops = [[], [{"text": "steer: change course"}]]
    runner = SessionRunner(
        agent_session=session,
        adapter=adapter,
        working_dir=str(tmp_path),
        driver=driver,
        steering_pop_fn=lambda: pops.pop(0) if pops else [],
        steer_poll_interval_s=0.05,
        steer_debounce_s=0.05,
        term_grace_s=0.5,
        killpg_fn=lambda p, s: kill_event.set() if s == signal.SIGTERM else None,
        kill_fn=lambda p, s: kill_event.set() if s == signal.SIGTERM else None,
        pid_alive_fn=lambda pid: False,
    )
    # Wire the runner's init observer the way _build_driver would.
    driver._on_init = runner._on_harness_init

    summary = await runner.run("long task")
    assert summary.exit_reason == "pm_user"
    # Persisted BEFORE result: the preempted turn's id landed on the session.
    assert session.claude_session_uuid == NEW_TURN_UUID
    assert session.runner_cwd == str(tmp_path)
    # And the resumed (post-preempt) invocation rides --resume on it.
    assert harness_calls[1]["prior_uuid"] == NEW_TURN_UUID


async def test_capture_at_init_updates_resume_target_every_turn(tmp_path):
    """Each --resume invocation forks to a NEW session id; the freshest init
    id is always the next turn's resume target."""
    ids = iter(
        [
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
        ]
    )
    calls = []

    async def fake_cli(message, working_dir, **kwargs):
        calls.append({"prior_uuid": kwargs.get("prior_uuid")})
        kwargs["on_init"]({"type": "system", "subtype": "init", "session_id": next(ids)})
        return "reply"

    driver = HeadlessRoleDriver(
        role="pm", session_id="sess-fork", working_dir=str(tmp_path), harness_fn=fake_cli
    )
    await driver.run_turn("t1")
    await driver.run_turn("t2")
    assert calls[0]["prior_uuid"] is None
    assert calls[1]["prior_uuid"] == "11111111-1111-4111-8111-111111111111"
    assert driver.claude_session_id == "22222222-2222-4222-8222-222222222222"


# --------------------------------------------------------------------------
# dev_agent_id structural sidechain capture
# --------------------------------------------------------------------------


def _write_sidechain(projects_root, cwd, session_id, agent_id, text="dev report"):
    slug = os.path.realpath(cwd).replace("/", "-").replace(".", "-")
    base = projects_root / slug / session_id / "subagents"
    base.mkdir(parents=True, exist_ok=True)
    entry = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    }
    (base / f"{agent_id}.jsonl").write_text(json.dumps(entry) + "\n")


def test_sidechain_agent_ids_scan(tmp_path):
    projects = tmp_path / "projects"
    cwd = tmp_path / "wd"
    cwd.mkdir()
    assert sidechain_agent_ids(str(cwd), VALID_UUID, projects_root=str(projects)) == []
    _write_sidechain(projects, str(cwd), VALID_UUID, "agent-first")
    ids = sidechain_agent_ids(str(cwd), VALID_UUID, projects_root=str(projects))
    assert ids == ["agent-first"]


async def test_dev_agent_id_captured_from_sidechain_after_turn(tmp_path):
    projects = tmp_path / "projects"
    cwd = tmp_path / "wd"
    cwd.mkdir()

    runner, driver, _, session = make_resume_runner(
        ["[/user]\ndone"], resume=None, working_dir=str(cwd), projects_root=str(projects)
    )
    # The fake driver exposes the current claude session id; the 'PM' spawned
    # a dev subagent during the turn — its sidechain file exists from spawn.
    driver.claude_session_id = VALID_UUID
    _write_sidechain(projects, str(cwd), VALID_UUID, "agent-devxyz", text="built the thing")

    await runner.run("go")
    # Structurally captured + persisted via the four-scalar writer.
    assert session.dev_agent_id == "agent-devxyz"
    # And mirrored into the turn history as the dev actor.
    dev_history = [
        e
        for e in session.session_events
        if e.get("type") == "turn_history" and e.get("actor") == "dev"
    ]
    assert dev_history and dev_history[0]["text"] == "built the thing"


async def test_dev_agent_id_captured_on_preempt_mid_spawn(tmp_path):
    """A preempt mid-Dev-spawn still captures the id — the sidechain file
    exists from the moment of spawn (Race 5, third-pass concern)."""
    projects = tmp_path / "projects"
    cwd = tmp_path / "wd"
    cwd.mkdir()
    session = FakeSession()
    adapter = SessionRunnerAdapter(
        session,
        "test-proj",
        "telegram",
        resolve_callbacks=lambda pk, t: (
            lambda c, p, r, s: None,
            None,
        ),
    )
    kill_event = asyncio.Event()

    class MidSpawnDriver:
        claude_session_id = VALID_UUID

        def __init__(self):
            self.calls = []
            self._first = True

        async def run_turn(self, message):
            self.calls.append(message)
            if self._first:
                self._first = False
                # Dev spawn happens mid-turn: sidechain file appears…
                _write_sidechain(projects, str(cwd), VALID_UUID, "agent-midspawn")
                await kill_event.wait()  # …then the turn is preempted.
                return HeadlessTurnOutcome(reply_text="", exit_reason="signaled")
            return HeadlessTurnOutcome(
                reply_text="[/user]\nresumed", turn_ended=True, turn_end_source="result"
            )

    pops = [[], [{"text": "steer"}]]
    runner = SessionRunner(
        agent_session=session,
        adapter=adapter,
        working_dir=str(cwd),
        driver=MidSpawnDriver(),
        steering_pop_fn=lambda: pops.pop(0) if pops else [],
        steer_poll_interval_s=0.05,
        steer_debounce_s=0.05,
        term_grace_s=0.3,
        killpg_fn=lambda p, s: kill_event.set(),
        kill_fn=lambda p, s: kill_event.set(),
        pid_alive_fn=lambda pid: False,
        projects_root=str(projects),
    )
    await runner.run("task")
    assert session.dev_agent_id == "agent-midspawn"


# --------------------------------------------------------------------------
# Turn-history mirror
# --------------------------------------------------------------------------


async def test_turn_history_mirror_shape_and_cap(tmp_path):
    long_reply = "[/user]\n" + ("x" * (TURN_HISTORY_MAX_CHARS + 500))
    runner, _, _, session = make_resume_runner([long_reply], resume=None, working_dir=str(tmp_path))
    await runner.run("go")
    entries = [e for e in session.session_events if e.get("type") == "turn_history"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["actor"] == "pm"
    # Dual-keyed like the adapter's other dashboard-visible runner events:
    # ``event_type`` is the stream's canonical dashboard key (SessionEvent
    # convention), ``type`` stays for the disaster-recovery seed filter.
    assert entry["event_type"] == "turn_history"
    assert set(entry) >= {"ts", "actor", "text", "type", "event_type"}
    # Full user-visible text, length env-capped.
    assert len(entry["text"]) == TURN_HISTORY_MAX_CHARS


async def test_mirror_is_not_read_on_resume(tmp_path):
    """The mirror is disaster-recovery seed + observability ONLY: a session
    resumed with valid scalars never consults session_events turn_history
    (lossless-resume is the explicit rabbit hole)."""
    ctx = ResumeContext(claude_session_id=VALID_UUID, runner_cwd=str(tmp_path))
    session = FakeSession()
    # Poison the mirror: if resume ever read it, this would surface.
    session.session_events = [{"type": "turn_history", "actor": "pm", "text": "POISON", "ts": "t"}]
    deliveries: list[str] = []
    adapter = SessionRunnerAdapter(
        session,
        "test-proj",
        "telegram",
        resolve_callbacks=lambda pk, t: (lambda c, p, r, s: deliveries.append(p), None),
    )
    driver = SeedableDriver(["[/user]\nclean"])
    runner = SessionRunner(
        agent_session=session,
        adapter=adapter,
        working_dir=str(tmp_path),
        driver=driver,
        resume=ctx,
        steering_pop_fn=lambda: [],
    )
    await runner.run("continue")
    assert "POISON" not in driver.calls[0]
    assert deliveries == ["clean"]


# --------------------------------------------------------------------------
# claude --version probe: failure caching + off-loop dispatch (PR #1930, A2)
# --------------------------------------------------------------------------


def test_probe_failure_is_cached(monkeypatch):
    """A failed ``claude --version`` probe is cached — a machine with a
    broken binary must not re-block on the probe every turn."""
    from agent.session_runner import runner as runner_module

    calls: list = []

    def _boom(*args, **kwargs):
        calls.append(args)
        raise OSError("no claude binary")

    monkeypatch.setattr(runner_module, "_claude_version_cache", None)
    monkeypatch.setattr(runner_module.subprocess, "run", _boom)
    assert runner_module._probe_claude_version() is None
    assert runner_module._probe_claude_version() is None
    assert len(calls) == 1


def test_probe_success_is_cached(monkeypatch):
    from types import SimpleNamespace

    from agent.session_runner import runner as runner_module

    calls: list = []

    def _ok(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout="2.0.14 (Claude Code)", returncode=0)

    monkeypatch.setattr(runner_module, "_claude_version_cache", None)
    monkeypatch.setattr(runner_module.subprocess, "run", _ok)
    assert runner_module._probe_claude_version() == "2.0.14"
    assert runner_module._probe_claude_version() == "2.0.14"
    assert len(calls) == 1


async def test_init_version_probe_runs_off_loop(monkeypatch, tmp_path):
    """When the init event carries no version field, the probe is dispatched
    to a worker thread — never run synchronously on the event-loop thread
    (a blocking subprocess.run there stalls the whole worker loop)."""
    import threading

    from agent.session_runner import runner as runner_module

    loop_thread = threading.get_ident()
    probe_threads: list[int] = []

    def _fake_probe():
        probe_threads.append(threading.get_ident())
        return "9.9.9"

    monkeypatch.setattr(runner_module, "_claude_version_cache", None)
    monkeypatch.setattr(runner_module, "_probe_claude_version", _fake_probe)
    runner, _driver, _deliveries, session = make_resume_runner(
        ["[/user]\nok"], resume=None, working_dir=str(tmp_path)
    )
    runner._on_harness_init({"session_id": VALID_UUID})

    for _ in range(200):
        if probe_threads:
            break
        await asyncio.sleep(0.01)
    assert probe_threads, "version probe never ran"
    assert probe_threads[0] != loop_thread, "probe ran on the event-loop thread"

    # The probed version is adopted and persisted once known.
    for _ in range(200):
        if getattr(session, "claude_version", None) == "9.9.9":
            break
        await asyncio.sleep(0.01)
    assert session.claude_version == "9.9.9"
