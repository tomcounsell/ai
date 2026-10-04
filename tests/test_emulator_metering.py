"""The emulator's stand-in and judge calls, metered through the kernel's
gateway onto one emulator task, a calibration task; and what the driver
reads from the kernel mirror, never the turn's workdir.

`claude` is replaced by a small Python program that posts one Messages API
call to `$ANTHROPIC_BASE_URL`, writes the environment it was given to a
file, and prints a `claude -p` style JSON result. The gateway forwards to a
local upstream replaying the recorded Haiku stream, as
`tests/test_gateway_meter.py` does. No model call.

Live spend: none.
"""

import asyncio
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from core import db, guards, ledger, machine, runs, session, settings, spending, tasks, verdicts
from core.gateway import TURN_TOKEN, Gateway
from core.machine import Check
from tests import scripted
from tests.conftest import TEST_DB
from tests.emulator import common, judge, replay, stand_in
from tests.ports import listen

pytestmark = pytest.mark.spend(usd=0)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
STREAM = (FIXTURES / "messages_stream_haiku.sse").read_bytes()

FAKE_CLAUDE = r"""#!{python}
import http.client, json, os, sys, urllib.parse
args = sys.argv[1:]
model = args[args.index("--model") + 1]
sys.stdin.read()
cfg = os.environ.get("CLAUDE_CONFIG_DIR")
with open(os.environ["FAKE_CLAUDE_OUT"], "a") as f:
    f.write(json.dumps({{"env": dict(os.environ), "args": args,
                        "config_entries": sorted(os.listdir(cfg)) if cfg and os.path.isdir(cfg) else None}}) + "\n")
url = urllib.parse.urlparse(os.environ["ANTHROPIC_BASE_URL"])
conn = http.client.HTTPConnection(url.hostname, url.port)
body = json.dumps({{"model": model, "max_tokens": 32, "stream": True,
                   "messages": [{{"role": "user", "content": "hi"}}]}})
conn.request("POST", url.path + "/v1/messages", body, {{"content-type": "application/json",
             "authorization": "Bearer " + os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")}})
r = conn.getresponse()
seen = b""
while b"message_stop" not in seen:
    chunk = r.read1(4096) if hasattr(r, "read1") else r.read(4096)
    if not chunk:
        break
    seen += chunk
ok = r.status == 200 and b"message_stop" in seen
print(json.dumps({{"result": os.environ.get("FAKE_CLAUDE_REPLY", "Go ahead."), "is_error": not ok,
                  "total_cost_usd": 0}}))
sys.stdout.flush()
os._exit(0)
"""


async def _upstream(*, hold: float = 0.0):
    """The recorded stream; `hold` seconds between the last byte and the
    end of the response."""

    async def handle(request: web.Request) -> web.StreamResponse:
        await request.read()
        response = web.StreamResponse(headers={"content-type": "text/event-stream"})
        await response.prepare(request)
        await response.write(STREAM)
        await asyncio.sleep(hold)
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/v1/messages", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", listen())
    await site.start()
    return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A fake `claude`, the driver's environment seeded with credentials it
    must not pass on, and where the fake writes what it saw."""
    exe = tmp_path / "claude"
    exe.write_text(FAKE_CLAUDE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    out = tmp_path / "seen.jsonl"
    monkeypatch.setattr(common, "CLAUDE", str(exe))
    monkeypatch.setattr(common, "DEMO", tmp_path / "demo")
    monkeypatch.setenv("VALOR_DB", TEST_DB)
    monkeypatch.setenv("FAKE_CLAUDE_OUT", str(out))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-a-real-key")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "not-a-real-token")
    monkeypatch.setenv("PGPASSFILE", str(tmp_path / "pgpass"))
    monkeypatch.setenv("VALOR_PGPASSWORD", "not-a-real-password")
    monkeypatch.setenv("AI_AGENT", "x")
    return out


def _seen(out: Path) -> list[dict]:
    return [json.loads(line) for line in out.read_text().splitlines()]


def _meter(dsn: str, task: str, *, hold: float = 0.0) -> common.Meter:
    meter = common.Meter(task, dsn=dsn, upstream="http://127.0.0.1:1", login=False)
    meter.__enter__()
    meter.runner, meter.gateway.upstream = meter._await(_upstream(hold=hold))
    return meter


def _close(meter: common.Meter) -> None:
    runner = meter.runner
    meter._await(runner.cleanup())
    meter.__exit__(None, None, None)


async def _rows(dsn, task):
    async with await db.connect(dsn) as conn:
        return await ledger.read(conn, task)


def test_stand_in_and_judge_calls_meter_onto_the_emulator_task_and_hold_no_credential(dsn, fake, tmp_path):
    item_task = asyncio.run(_start(dsn))
    task = common.start_emulator_task("toy-gate", item_task, dsn=dsn)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    meter = _meter(dsn, task)
    try:
        for call_id, model in (("stand-in.answer.1", stand_in.MODEL), ("judge", judge.JUDGE_MODEL)):
            reply = common.claude_json(
                "q", system="s", model=model, meter=meter, call_id=call_id, workdir=run_dir
            )
            assert reply["text"] == "Go ahead."
        spent = meter.spend()
    finally:
        _close(meter)

    got = asyncio.run(_rows(dsn, task))
    opened = [r["payload"] for r in got if r["type"] == "gateway.opened"]
    charged = [r["payload"] for r in got if r["type"] == "gateway.charged"]
    assert [o["turn_id"] for o in opened] == ["stand-in.answer.1", "judge"]
    assert all(o["route"] == "gateway" for o in opened)
    assert [c["model"] for c in charged] == ["claude-opus-5-5", judge.JUDGE_MODEL]
    assert spent["usd"] * 1e6 == pytest.approx(sum(c["usd_micros"] for c in charged)) and spent["usd"] > 0
    assert not spent["open_calls"]
    # The item task's spending is untouched by the emulator's calls.
    assert not [r for r in asyncio.run(_rows(dsn, item_task)) if r["type"].startswith("gateway.")]

    for seen in _seen(fake):
        env = seen["env"]
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == TURN_TOKEN
        assert env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1:")
        config = Path(env["CLAUDE_CONFIG_DIR"])
        assert config.is_relative_to(run_dir) and seen["config_entries"] == []
        for name in env:
            assert name in ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "ANTHROPIC_BASE_URL") or not (
                name.startswith(("CLAUDE", "ANTHROPIC", "PG", "VALOR_PG")) or name == "AI_AGENT"
            ), name
        assert "--tools" in seen["args"] and "--safe-mode" in seen["args"]
    assert not list(tmp_path.rglob("costs.jsonl"))


def test_the_token_is_retired_after_a_call_returns_and_after_it_raises(dsn, fake, tmp_path, monkeypatch):
    task = common.start_emulator_task("toy-retire", None, dsn=dsn)
    meter = _meter(dsn, task)
    issued = []
    real_issue = meter.issue

    def issue(call_id):
        issued.append(real_issue(call_id))
        return issued[-1]

    monkeypatch.setattr(meter, "issue", issue)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    try:
        common.claude_json("q", system="s", model=stand_in.MODEL, meter=meter, call_id="a", workdir=run_dir)
        monkeypatch.setattr(common, "CLAUDE", str(tmp_path / "no-such-claude"))
        with pytest.raises(FileNotFoundError):
            common.claude_json(
                "q", system="s", model=stand_in.MODEL, meter=meter, call_id="b", workdir=run_dir
            )

        async def refused(url):
            async with aiohttp.ClientSession() as http, http.post(url + "/v1/messages", json={}) as r:
                return r.status

        assert meter.gateway.grants == {}
        assert [meter._await(refused(u)) for u in issued] == [403, 403]
    finally:
        _close(meter)


def test_spend_is_read_after_the_late_charge_lands(dsn, fake, tmp_path):
    """The provider holds the response open after its last event, so the
    child exits before the gateway writes `gateway.charged`; the spend
    read drains first."""
    task = common.start_emulator_task("toy-drain", None, dsn=dsn)
    meter = _meter(dsn, task, hold=1.5)
    (tmp_path / "run").mkdir()
    try:
        common.claude_json(
            "q", system="s", model=judge.JUDGE_MODEL, meter=meter, call_id="judge", workdir=tmp_path / "run"
        )
        spent = meter.spend()
    finally:
        _close(meter)
    charged = [r["payload"] for r in asyncio.run(_rows(dsn, task)) if r["type"] == "gateway.charged"]
    assert len(charged) == 1 and spent["usd"] * 1e6 == pytest.approx(charged[0]["usd_micros"])
    assert not spent["open_calls"]


async def _start(dsn) -> str:
    async with await db.connect(dsn) as conn:
        return await tasks.start(conn, tasks.Brief(instruction="item"))


def test_the_emulator_task_is_a_calibration_task_every_writer_refuses(dsn, tmp_path):
    item_task = asyncio.run(_start(dsn))
    task = common.start_emulator_task("pop-b-gate", item_task, dsn=dsn)

    async def go():
        async with await db.connect(dsn) as conn:
            got = await ledger.read(conn, task)
            with pytest.raises(tasks.CalibrationTask):
                await tasks.stop(conn, task, reason="x")
            with pytest.raises(LookupError, match="calibration"):
                await session.answer(conn, task, "x")
            with pytest.raises(LookupError, match="calibration"):
                await session.feedback(conn, task, "x")
            with pytest.raises(guards.GrantRefused, match="calibration"):
                await guards.grant(conn, task, "i1", note="x")
            with pytest.raises(verdicts.VerdictRefused, match="calibration"):
                await verdicts.record_check(conn, task, Check.REVIEW, "pass", governance_from=[])
        gateway = Gateway(dsn)
        await gateway.start(port=listen())
        try:
            with pytest.raises(tasks.CalibrationTask):
                await runs.run_turn(
                    gateway, task, scripted.turn_for("x", None, tasks.Brief("x", workspace=str(tmp_path)))
                )
        finally:
            await gateway.close()
        return got

    got = asyncio.run(go())
    assert machine.fold(got).calibration
    (started,) = [r["payload"] for r in got if r["type"] == "task.started"]
    assert started["emulator"] == {"run": "pop-b-gate", "item_task": item_task}
    assert started["instruction"] == "meter emulator run pop-b-gate"
    assert started["provenance"]["via"] == "python -m tests.emulator.replay"
    assert started["max_effect_class"] == "read"


def test_models_are_pinned_ids():
    assert stand_in.MODEL == settings.SEATS["frontier"][1] == "claude-opus-5-5"
    model = judge.JUDGE_MODEL
    assert model.startswith("claude-sonnet-") and model not in ("sonnet", "opus", "haiku")
    assert not model.endswith("-latest") and spending.prices(model) is not None


# -- kernel-owned reads ------------------------------------------------------------


def _mirror(tmp_path: Path) -> tuple[Path, Path, str]:
    """A workdir and a kernel-only mirror holding its commits."""
    ws, _ = scripted.workspace(tmp_path)
    base = scripted.git(ws, "rev-parse", "HEAD")
    mirror = tmp_path / "kernel.git"
    subprocess.run(["git", "init", "-q", "--bare", str(mirror)], check=True)
    return ws, mirror, base


def _push(ws: Path, mirror: Path) -> None:
    scripted.git(ws, "push", "-q", str(mirror), "+HEAD:refs/heads/candidate")


async def _held(dsn, task, head) -> str:
    effect_id = ledger.new_id()
    payload = {
        "url": "x",
        "target_branch": "main",
        "head_sha": head,
        "candidate": {"sha": head, "turn_id": "t"},
    }
    async with await db.connect(dsn) as conn:
        await ledger.append(
            conn,
            task,
            "effect.held",
            {"effect_id": effect_id, "action_type": "merge", "effect_class": "act", "target": "main",
             "payload": payload, "payload_sha256": ledger.digest(payload), "idempotency_key": ledger.new_id(),
             "adds_governance": False},
        )  # fmt: skip
    return effect_id


def test_the_rev_read_is_the_held_merge_head_else_the_candidate(dsn, tmp_path, monkeypatch):
    monkeypatch.setenv("VALOR_DB", TEST_DB)
    task = asyncio.run(_start(dsn))
    head = asyncio.run(_held(dsn, task, "h" * 40))
    held = {"merge_effect": {"effect_id": head, "state": "held"}, "candidate": {"sha": "c" * 40}}
    assert common.review_rev(task, held, dsn=dsn) == "h" * 40
    # A later refused effect does not change which held row is read.
    later = asyncio.run(_held(dsn, task, "r" * 40))
    assert later != head and common.review_rev(task, held, dsn=dsn) == "h" * 40
    assert (
        common.review_rev(task, {"merge_effect": None, "candidate": {"sha": "c" * 40}}, dsn=dsn) == "c" * 40
    )
    assert common.review_rev(task, {"merge_effect": {"effect_id": later, "state": "refused"},
                                     "candidate": {"sha": "c" * 40}}, dsn=dsn) == "c" * 40  # fmt: skip


def test_the_delivery_is_read_from_the_mirror_with_the_workdir_gone(tmp_path):
    ws, mirror, base = _mirror(tmp_path)
    rev = scripted.commit(ws, "lib/a.py", "A = 1\n", "the candidate")
    _push(ws, mirror)
    scripted.commit(ws, "lib/b.py", "B = 2\n", "newer than the candidate, never pushed")
    ws.rename(tmp_path / "moved-away")
    context = stand_in.delivery_context(mirror, base, rev)
    assert "lib/a.py" in context and "lib/b.py" not in context


def test_the_judge_diff_is_the_whole_change_whatever_the_plan_is_named(tmp_path):
    """The baseline judge read the whole diff; so does this one. A task
    whose plan names `app.py` hides nothing from it."""
    ws, mirror, base = _mirror(tmp_path)
    scripted.commit(ws, "app.py", "# the plan, so the turn said\n", "plan")
    scripted.commit(ws, "app.py", "# the plan, so the turn said\nprint('the change')\n", "build")
    rev = scripted.commit(ws, "lib/big.py", "x = '" + "y" * 80_000 + "'\n", "big")
    _push(ws, mirror)
    result = {"task_id": "t", "final_rev": rev, "workspace": {"mirror": str(mirror), "base": base}}
    diff, recorded = judge.candidate_diff(result)
    assert "print('the change')" in diff and "lib/big.py" in diff
    assert set(recorded) == {"chars", "lines", "truncated"}
    assert recorded["truncated"] and recorded["chars"] == len(diff) > judge.DIFF_LIMIT
    assert recorded["lines"] == diff.count("\n") + 1


def test_the_hidden_test_tree_is_made_under_checks_from_the_mirror(tmp_path):
    from core import workspace as kws

    ws, mirror, _base = _mirror(tmp_path)
    rev = scripted.commit(ws, "tests/test_hidden.py", "def test_x():\n    pass\n", "c")
    _push(ws, mirror)
    task_dir = tmp_path / "task"
    (task_dir / "checks").mkdir(parents=True)
    result = {
        "run": "toy-gate",
        "final_rev": rev,
        "workspace": {"mirror": str(mirror), "task_dir": str(task_dir), "workdir": str(task_dir / "repo")},
    }
    (task_dir / "checks" / "verify-toy-gate" / "stale").mkdir(parents=True)
    root, tree = judge.export_final(result)
    assert root == task_dir / "checks" / "verify-toy-gate" and tree == root / "repo"
    assert (tree / "tests" / "test_hidden.py").exists() and not (root / "stale").exists()
    assert (root / "tmp").is_dir() and not (tree / ".git").exists()
    lay = kws.Layout(task_dir)
    assert str(root) not in kws.turn_profile(lay, [])
    assert str(root) in judge.verify_profile(lay, [], root)


# -- the driver's ends -------------------------------------------------------------


class Args:
    stand_in_model = stand_in.MODEL
    max_feedback = 2


def _drive(monkeypatch, state: dict, *, said: str | Exception = "ran", reply: dict | None = None, left=()):
    """One driver step against a task whose status is `state`; `core run`
    answers `said`, or raises it."""
    calls = {"stand_in": 0, "run": 0}

    def fake_stand_in(*a, **kw):
        calls["stand_in"] += 1
        return reply or {"kind": "accept", "text": None, "reason": "ok"}

    def fake_core(*args):
        calls["run"] += 1
        assert args[0] == "run"
        if isinstance(said, Exception):
            raise said
        return said

    monkeypatch.setattr(replay, "status", lambda task: state)
    monkeypatch.setattr(replay, "stand_in", fake_stand_in)
    monkeypatch.setattr(replay, "core", fake_core)
    monkeypatch.setattr(replay, "release_pushes", lambda task, ws, log: list(left))
    result = {"task_id": "t", "run": "toy", "log": [], "outcome": None}
    ws = {"mirror": "m", "base": "b", "run_dir": "/nonexistent"}
    replay.step(result, {"answer_key": "k"}, ws, Args, meter=None)
    return result, calls


def _merge(**kw) -> dict:
    return {
        "state": "merge",
        "delivery": {"outcome": "passed"},
        "join": {"row": 1, "goes_to": "merge", "outcome": "passed"},
        "governance": [],
        "merge_effect": None,
        **kw,
    }


def test_a_delivery_that_did_not_pass_ends_to_tom(monkeypatch):
    result, calls = _drive(monkeypatch, _merge(delivery={"outcome": "did_not_pass"}))
    assert result["outcome"] == "to tom" and calls == {"stand_in": 0, "run": 0}


def test_a_refused_merge_ends_to_tom(monkeypatch):
    result, calls = _drive(monkeypatch, _merge(merge_effect={"effect_id": "e", "state": "refused"}))
    assert result["outcome"] == "to tom" and calls["stand_in"] == 0


@pytest.mark.parametrize(
    "state",
    [
        _merge(governance=[{"id": "i1", "path": "p", "summary": "s", "granted": False}]),
        _merge(join={"row": 2, "goes_to": "merge", "outcome": "governance_refused"}),
    ],
)
def test_an_awaited_grant_pauses_without_the_stand_in(monkeypatch, state):
    result, calls = _drive(monkeypatch, state)
    assert result["outcome"] is None and result["paused"] == "awaiting a grant"
    assert calls == {"stand_in": 0, "run": 0}


@pytest.mark.parametrize("kind", ["accept", "cap"])
def test_a_held_merge_the_stand_in_accepts_or_has_spent_its_rounds_on_ends_held(monkeypatch, kind):
    state = _merge(merge_effect={"effect_id": "e", "state": "held"})
    result, calls = _drive(monkeypatch, state, reply={"kind": kind, "text": None, "reason": "r"})
    assert result["outcome"] == "held" and calls == {"stand_in": 1, "run": 0}


def test_feedback_at_a_held_merge_goes_on_without_an_outcome(monkeypatch):
    state = _merge(merge_effect={"effect_id": "e", "state": "held"})
    result, calls = _drive(monkeypatch, state, reply={"kind": "feedback", "text": "fix", "reason": "r"})
    assert result["outcome"] is None and not result.get("paused") and calls == {"stand_in": 1, "run": 0}


def test_a_merge_with_no_effect_yet_runs_on(monkeypatch):
    result, calls = _drive(monkeypatch, _merge())
    assert result["outcome"] is None and calls == {"stand_in": 0, "run": 1}


def test_no_runner_and_a_failed_run_pause_with_the_outcome_unset(monkeypatch):
    for said in ("NO RUNNER for review: record its verdict", "FAILED: the turn errored"):
        result, _ = _drive(monkeypatch, {"state": "checks"}, said=said)
        assert result["outcome"] is None and result["paused"] == said


def test_a_core_run_that_fails_keeps_its_whole_error(monkeypatch):
    error = "python -m core run t failed (1):\n" + "\n".join(f"line {i} " + "x" * 400 for i in range(20))
    result, _ = _drive(monkeypatch, {"state": "build"}, said=RuntimeError(error))
    assert result["outcome"] is None and result["paused"] == f"failed: {error}"
    assert result["log"][-1]["step"] == "run failed" and result["log"][-1]["error"] == error


def test_an_answer_the_driver_does_not_know_pauses_it(monkeypatch):
    result, calls = _drive(monkeypatch, {"state": "build"}, said="SOMETHING NEW (task t): x\nmore")
    assert result["outcome"] is None and result["paused"] == "SOMETHING NEW (task t): x"
    assert calls["run"] == 1


# A scripted task, the real router behind `core run`, and its real answer line.


def _step_real(monkeypatch, dsn, task, runners=None) -> dict:
    """One driver step against a ledger task, `core run` being the real
    router with scripted runners and `core/__main__.py`'s answer line."""
    from core import router
    from core.__main__ import _status_line

    calls = {"run": 0}

    async def fold():
        async with await db.connect(dsn) as conn:
            return await tasks.status(conn, task)

    async def route():
        gateway = Gateway(dsn)
        await gateway.start(port=listen())
        try:
            return await router.run(gateway, task, runners or scripted.RUNNERS, dsn=dsn)
        finally:
            await gateway.close()

    def fake_core(*args):
        assert args == ("run", task)
        calls["run"] += 1
        return _status_line(task, asyncio.run(route()))

    def no_stand_in(*a, **kw):
        raise AssertionError("the stand-in has no part here")

    monkeypatch.setattr(replay, "status", lambda t: asyncio.run(fold()))
    monkeypatch.setattr(replay, "core", fake_core)
    monkeypatch.setattr(replay, "stand_in", no_stand_in)
    monkeypatch.setattr(replay, "release_pushes", lambda t, ws, log: [])
    monkeypatch.setattr(replay, "wait_run_lock", lambda t: common.wait_run_lock(t, dsn=dsn))
    result = {"task_id": task, "run": "toy", "log": [], "outcome": None}
    replay.step(
        result, {"answer_key": "k"}, {"mirror": "m", "base": "b", "run_dir": "/nonexistent"}, Args, None
    )
    assert calls["run"] == 1
    return result


def test_a_lost_lock_ends_the_item_with_its_answer_as_the_reason(monkeypatch, dsn, owner_dsn, tmp_path):
    import psycopg

    from core.machine import State

    ws, _ = scripted.workspace(tmp_path)
    task = asyncio.run(scripted.start(dsn, ws))

    async def cut(ctx):
        with psycopg.connect(owner_dsn, autocommit=True) as owner:
            owner.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype = 'advisory' "
                "AND granted AND pid <> pg_backend_pid() AND database = "
                "(SELECT oid FROM pg_database WHERE datname = current_database())"
            )
        return await scripted.working(ctx)

    result = _step_real(monkeypatch, dsn, task, runners={State.PLAN: cut})
    assert result["outcome"] is None and result["paused"].startswith(f"LOCK LOST (task {task}")


def test_a_legacy_task_ends_the_item_with_its_answer_as_the_reason(monkeypatch, dsn, tmp_path):
    from psycopg.types.json import Jsonb

    ws, _ = scripted.workspace(tmp_path)
    b = tasks.Brief(instruction="old", max_effect_class="act", workspace=str(ws))

    async def make():
        old = {k: v for k, v in b.__dict__.items() if k not in ("target_branch", "origin_url", "base_sha")}
        async with await db.connect(dsn) as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO documents (kind, id, body) VALUES ('task', %s, %s)", (b.id, Jsonb(old))
            )
            await ledger.append(conn, b.id, "task.started", {"instruction": "old", "max_effect_class": "act",
                                                             "mode": "bare"})  # fmt: skip
            await ledger.append(conn, b.id, "task.delivered", {"turn_id": "t", "summary": "done"})

    asyncio.run(make())
    result = _step_real(monkeypatch, dsn, b.id)
    assert result["outcome"] is None and result["paused"].startswith(f"LEGACY (task {b.id}")


def test_already_running_waits_on_the_run_lock_then_steps_on(monkeypatch, dsn, tmp_path):
    import threading
    import time

    import psycopg

    ws, _ = scripted.workspace(tmp_path)
    task = asyncio.run(scripted.start(dsn, ws))
    holder = psycopg.connect(dsn, autocommit=True)
    holder.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"run:{task}",))
    released = threading.Event()

    def release():
        time.sleep(0.5)
        holder.close()
        released.set()

    threading.Thread(target=release, daemon=True).start()
    result = _step_real(monkeypatch, dsn, task)
    assert released.is_set()
    assert result["outcome"] is None and "paused" not in result
    assert [e["step"] for e in result["log"]] == ["run", "waiting on the run lock"]
    assert result["log"][0]["said"].startswith(f"ALREADY RUNNING (task {task}")
    assert scripted.turns(ws) == []


def test_a_held_effect_of_another_action_ends_the_run(monkeypatch):
    result, _ = _drive(monkeypatch, {"state": "build"}, left=["e1"])
    assert result["outcome"] == "an effect other than a local push is held for Tom"


def test_a_stopped_task_ends_stopped(monkeypatch):
    result, calls = _drive(monkeypatch, {"state": "stopped"})
    assert result["outcome"] == "stopped" and calls == {"stand_in": 0, "run": 0}


def test_run_names_the_result_and_a_finished_one_is_refused_without_rebuild(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "DEMO", tmp_path)
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "pop-b-gate.json").write_text(json.dumps({"outcome": "held"}))

    class A:
        run, rebuild = "pop-b-gate", False

    with pytest.raises(SystemExit, match="pop-b-gate.json is finished"):
        replay._replay({"name": "pop-b"}, "routed", A)


def test_the_cli_runs_as_a_module():
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run(
        [sys.executable, "-m", "tests.emulator.replay", "--help"], cwd=root, capture_output=True, text=True,
        check=False, env={**os.environ},
    )  # fmt: skip
    assert out.returncode == 0 and "--run" in out.stdout and "--stand-in-model" in out.stdout


# The composition root's runners: every stage the state machine schedules has
# one, except docs, which the plan leaves hand-played while governance's
# calibration entry check fails (docs/plans/m1-5-emulator.md, m1-4b-records.md).

# The router settles these itself (a question for Tom, the merge, an end); every
# other state, and every check but docs, is run by a runner.
SETTLED = {machine.State.WAITING, machine.State.MERGE, machine.State.MERGED, machine.State.STOPPED}
MANUAL = {Check.DOCS}


def test_every_stage_the_state_machine_schedules_has_a_runner_but_docs():
    from core.__main__ import RUNNERS, runners

    scheduled = {s for s in machine.State if s not in SETTLED and s is not machine.State.CHECKS} | set(Check)
    assert set(runners(None)) == scheduled - MANUAL == set(RUNNERS)


@pytest.mark.macos
def test_review_is_run_by_the_kernels_runner_and_docs_pauses_the_driver_for_its_verdict(
    monkeypatch, dsn, tmp_path
):
    """The kernel's own `runners()`, its fresh sessions played by the scripted
    session and its judgement port the local upstream answering `false`,
    carry critique, build, test, and review in one `core run` the driver
    makes; docs has no runner, so the driver pauses on `NO RUNNER` and the
    docs verdict recorded by hand completes the delivery."""
    import core.__main__ as kernel
    from tests import judgement_upstream, test_checks

    async def at_critique():
        task, _b, ws = await test_checks.to_candidate(dsn, tmp_path, writes={"greeting.txt": "hi\n"})
        scripted.steer(ws, critique="sound", build="reasons", fresh_acts=["sound", "review"])
        return task, ws

    task, ws = asyncio.run(at_critique())
    monkeypatch.setattr(kernel, "_fresh_for", scripted.fresh_for(ws / ".git"))
    port = judgement_upstream.shared().port(fixed="false")
    everything = {
        **kernel.runners(port),
        **{s: r for s, r in scripted.RUNNERS.items() if s in machine.WORKING},
    }
    result = _step_real(monkeypatch, dsn, task, runners=everything)
    assert result["outcome"] is None and result["paused"].startswith("NO RUNNER"), result
    assert "no runner for docs yet" in result["paused"]
    got = asyncio.run(_rows(dsn, task))
    assert [r["payload"]["leg"] for r in got if r["type"] == "review.decided"] == ["session"]
    assert machine.fold(got).state is machine.State.CHECKS

    out = subprocess.run(
        [sys.executable, "-m", "core", "verdict", task, "docs", "no_change", "--by", "test", "--role-played"],
        cwd=Path(__file__).resolve().parent.parent, env={**os.environ, "VALOR_DB": TEST_DB},
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert out.returncode == 0, out.stderr
    got = asyncio.run(_rows(dsn, task))
    assert [r["payload"]["leg"] for r in got if r["type"] == "docs.decided"] == ["manual"]
    assert machine.fold(got).state is machine.State.MERGE
