"""Fresh sessions and the critique runner, through the router, on a
workspace the kernel provisioned: real Postgres, real git, the real kernel
mirror and blind checkout, and a scripted session in place of `claude -p`
(`tests/scripted.py`, FRESH).

Also the fold's rule that only the working session's turns name the session
the next working turn resumes, in the state machine's fold and in the
legacy one.

Live spend: none.
"""

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

from core import broker, db, fresh, ledger, machine, router, session, signals, tasks
from core import workspace as kws
from core.gateway import Gateway
from core.machine import State
from tests import scripted

pytestmark = pytest.mark.spend(usd=0)


def run(coro):
    return asyncio.run(coro)


async def drive(dsn, task, runners) -> dict:
    gateway = Gateway(dsn)
    await gateway.start()
    try:
        return await scripted.route(gateway, task, runners, dsn=dsn)
    finally:
        await gateway.close()


async def rows(dsn, task) -> list[dict]:
    async with await db.connect(dsn) as conn:
        return await ledger.read(conn, task)


def critique_turns(ws: Path) -> list[dict]:
    return [t for t in scripted.turns(ws) if t["stage"] == "critique"]


def planned(dsn, tmp_path, **steer):
    """A provisioned task whose plan is written, waiting in critique."""

    async def go():
        task, b = await scripted.provisioned(dsn, tmp_path)
        ws = Path(b.workspace)
        scripted.steer(ws, **steer)
        out = await drive(dsn, task, scripted.RUNNERS)
        assert out["status"] == "no runner" and out["missing"] == ["critique"], out
        return task, b, ws

    return run(go())


@pytest.mark.macos
def test_a_sound_critique_goes_to_build_and_the_build_resumes_the_working_session(dsn, tmp_path):
    task, _b, ws = planned(dsn, tmp_path, critique="sound")
    out = run(drive(dsn, task, scripted.fresh_runners(ws)))
    assert out["status"] == "no runner" and out["missing"] == ["test", "review", "docs"]
    stages = [(t["stage"], t["resume"]) for t in scripted.turns(ws)]
    assert stages == [("plan", ""), ("critique", None), ("build", scripted.SESSION)]
    written = run(rows(dsn, task))
    decided = next(r["payload"] for r in written if r["type"] == "critique.decided")
    started = [r["payload"] for r in written if r["type"] == "turn.started"]
    assert decided["leg"] == "session" and decided["turn_id"] == started[1]["turn_id"]
    assert decided["model"] == "claude-opus-5-5" and "provenance" not in decided
    assert started[1]["fresh"] is True and started[1]["stage"] == "critique"
    assert "# How this session reaches the kernel" in started[1]["brief"]
    assert "Workspace:" not in started[1]["brief"] and ".valor/effects" not in started[1]["brief"]
    # The fresh turn's own session id was never resumed.
    assert machine.fold(written).session == scripted.SESSION
    # A fresh session's effect requests reach no broker.
    assert not [r for r in written if r["type"].startswith("effect.")]


@pytest.mark.macos
def test_turn_files_are_read_off_the_event_loop(dsn, tmp_path, monkeypatch):
    """The router runs every task on one loop, so the kernel's reads of
    files a turn controls run in a worker thread."""
    task, _b, ws = planned(dsn, tmp_path, critique="sound")
    seen: dict[str, list[bool]] = {}

    def off_loop(owner, name):
        real = getattr(owner, name)

        def wrapped(*args, **kwargs):
            try:
                asyncio.get_running_loop()
                on_loop = True
            except RuntimeError:
                on_loop = False
            seen.setdefault(name, []).append(on_loop)
            return real(*args, **kwargs)

        monkeypatch.setattr(owner, name, wrapped)

    off_loop(kws, "read_verdict")
    off_loop(signals, "collect")
    off_loop(session, "_verdict")
    out = run(drive(dsn, task, scripted.fresh_runners(ws)))
    assert out["missing"] == ["test", "review", "docs"]
    assert seen == {"read_verdict": [False], "collect": [False], "_verdict": [False]}


@pytest.mark.macos
def test_the_critique_checkout_is_blind_and_has_its_own_tmp_and_config(dsn, tmp_path):
    task, b, ws = planned(dsn, tmp_path)
    run(drive(dsn, task, scripted.fresh_runners(ws)))
    turn = critique_turns(ws)[0]
    checkout = Path(turn["cwd"])
    assert turn["log"] == ["candidate", "base"]  # no builder message, no intermediate commit
    builder_commits = subprocess.run(
        ["git", "-C", b.workspace, "rev-list", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.split()
    for sha in builder_commits:  # not one of the builder's commit objects is in the checkout
        missing = subprocess.run(
            ["git", "-C", str(checkout), "cat-file", "-e", sha], capture_output=True, check=False
        )
        assert missing.returncode != 0
    assert not (checkout / ".valor" / "handled" / "done.md").exists()
    assert turn["env"]["TMPDIR"] == str(checkout.parent / "tmp")
    assert turn["env"]["CLAUDE_CONFIG_DIR"] == str(checkout.parent / "claude")
    inputs = {p.name for p in (checkout / ".valor" / "inputs").iterdir()}
    assert inputs == {"request.md", "answers.md", "diff.patch", "plan.md", "critiques.md"}
    assert (checkout / ".valor" / "inputs" / "request.md").read_text() == "Write Tom a greeting."
    assert "docs/plan.md" in (checkout / ".valor" / "inputs" / "diff.patch").read_text()


@pytest.mark.macos
def test_revise_with_a_round_left_goes_back_to_plan_and_without_one_to_build_with_findings(dsn, tmp_path):
    counts = {"critique_rounds": 1, "review_rounds": 1}
    task, _b, ws = planned(dsn, tmp_path, counts=counts, fresh_acts=["revise", "revise"])
    runners = scripted.fresh_runners(ws)
    run(drive(dsn, task, runners))
    stages = [t["stage"] for t in scripted.turns(ws)]
    assert stages == ["plan", "critique", "plan", "critique", "build"]
    build = scripted.turns(ws)[-1]
    assert "# Critique: revise" in build["prompt"] and "the plan reads the wrong module" in build["prompt"]
    second = critique_turns(ws)[1]
    critiques = (Path(second["cwd"]) / ".valor" / "inputs" / "critiques.md").read_text()
    assert "the plan reads the wrong module" in critiques  # the second round sees the first's findings
    guards = [r["payload"]["guard_id"] for r in run(rows(dsn, task)) if r["type"] == "critique.decided"]
    assert guards == [machine.GUARD_CRITIQUE, None]


@pytest.mark.macos
def test_a_raise_applies_and_an_out_of_range_raise_is_no_verdict(dsn, tmp_path):
    task, _b, ws = planned(dsn, tmp_path, fresh_acts=["bad_raise", "raise"])
    runners = scripted.fresh_runners(ws)
    first = run(drive(dsn, task, runners))
    assert first["status"] == "failed" and "each count raised is" in str(first["turn"]["result"])
    assert not [r for r in run(rows(dsn, task)) if r["type"] == "critique.decided"]
    run(drive(dsn, task, runners))
    f = machine.fold(run(rows(dsn, task)))
    assert f.loops.review_rounds == 2


@pytest.mark.parametrize(
    ("act", "why"),
    [
        ("fail", None),
        ("none", "no .valor/verdict.json"),
        ("malformed", "not JSON"),
        ("symlink", "verdict.json is a link, not a plain file"),
        ("fifo", "not a regular file"),
        ("dir_symlink", ".valor is not a plain directory"),
        (
            "nul",
            "the critique.decided row: the ledger's JSON (Postgres jsonb) cannot store it: Untranslatable",
        ),
    ],
)
@pytest.mark.macos
def test_a_critique_that_leaves_no_valid_verdict_writes_none_and_only_critique_reruns(
    dsn, tmp_path, act, why
):
    secret = tmp_path / "pgpass-stand-in"
    secret.write_text("*:*:*:valor_kernel:not-a-real-password\n")
    task, _b, ws = planned(dsn, tmp_path, fresh_acts=[act, "sound"], target=str(secret))
    runners = scripted.fresh_runners(ws)
    first = run(drive(dsn, task, runners))
    assert first["status"] == "failed"
    if why:
        assert why in str(first["turn"]["result"])
    written = run(rows(dsn, task))
    assert not [r for r in written if r["type"] == "critique.decided"]
    assert "not-a-real-password" not in json.dumps([r["payload"] for r in written])
    assert machine.fold(written).state is State.CRITIQUE
    second = run(drive(dsn, task, runners))
    assert [t["stage"] for t in scripted.turns(ws)] == ["plan", "critique", "critique", "build"]
    assert second["missing"] == ["test", "review", "docs"]


@pytest.mark.macos
def test_a_critique_stopped_mid_turn_leaves_no_verdict(dsn, tmp_path):
    task, _b, ws = planned(dsn, tmp_path, fresh_acts=["hang"])

    async def go():
        gateway = Gateway(dsn)
        await gateway.start()
        try:
            running = asyncio.create_task(scripted.route(gateway, task, scripted.fresh_runners(ws), dsn=dsn))
            for _ in range(200):
                if any(
                    r["type"] == "turn.started" and r["payload"].get("fresh") for r in await rows(dsn, task)
                ):
                    break
                await asyncio.sleep(0.05)
            async with await db.connect(dsn) as conn:
                await tasks.stop(conn, task, reason="test")
            return await running
        finally:
            await gateway.close()

    out = run(go())
    assert out["status"] == "stopped"
    written = run(rows(dsn, task))
    assert not [r for r in written if r["type"] == "critique.decided"]
    ended = [r["payload"] for r in written if r["type"] == "turn.ended"][-1]
    assert ended["outcome"] == "stopped"


@pytest.mark.macos
def test_a_task_the_kernel_did_not_provision_gets_no_fresh_session(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws)
        await drive(dsn, task, scripted.RUNNERS)
        return task, await drive(dsn, task, scripted.fresh_runners(ws))

    _task, out = run(go())
    assert out["status"] == "failed" and "provisioned" in out["turn"]["result"]
    assert not critique_turns(ws)


# -- the fold: whose session is resumed ----------------------------------------------------


def _row(i, kind, **payload):
    return {"id": i, "type": kind, "payload": payload}


WORKING = "00000000-0000-4000-8000-0000000000a1"


def test_a_fresh_turn_never_names_the_session_in_either_fold():
    turns = [
        _row(2, "turn.started", turn_id="w1", state="plan"),
        _row(3, "turn.ended", turn_id="w1", outcome="done", result={"session_id": WORKING}),
        _row(4, "turn.started", turn_id="f1", state="critique", fresh=True, stage="critique"),
        _row(
            5,
            "turn.ended",
            turn_id="f1",
            outcome="done",
            result={"session_id": "00000000-0000-4000-8000-0000000000c1"},
        ),
    ]
    sdlc = [_row(1, "task.started", sdlc=1, instruction="x"), *turns]
    legacy = [_row(1, "task.started", instruction="x"), *turns]
    assert machine.fold(sdlc).session == WORKING
    assert machine.fold(legacy).legacy and machine.fold(legacy).session == WORKING


@pytest.mark.macos
def test_the_merge_of_a_provisioned_task_reads_the_mirror_and_lands_on_its_own_origin(dsn, tmp_path):
    task, b, ws = planned(dsn, tmp_path)

    async def go():
        await drive(dsn, task, scripted.fresh_runners(ws))
        await scripted.check(dsn, task, "test", "pass")
        await scripted.check(dsn, task, "review", "pass")
        f = machine.fold(await rows(dsn, task))
        docs = scripted.commit(ws, "docs/notes.md", "notes\n", "docs")
        scripted.keep_docs(b, ws, docs)
        # Move the builder's branch back, as a send-back would; the mirror
        # still has the kept head.
        scripted.git(ws, "reset", "-q", "--hard", f.candidate.sha)
        scripted.git(ws, "reflog", "expire", "--expire=now", "--all")
        await scripted.check(dsn, task, "docs", "updated", head=docs)
        effect = (machine.fold(await rows(dsn, task))).merge_effect["effect_id"]
        scripted.git(ws, "gc", "-q", "--prune=now")  # the builder's clone no longer holds the docs commit
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, effect, note="merge it")
            done = await scripted.release(conn, effect)
        return done, docs, machine.fold(await rows(dsn, task))

    done, docs, f = run(go())
    assert done.kind == "done" and f.state is State.MERGED
    assert scripted.git(b.origin_url, "rev-parse", b.target_branch) == docs
    assert b.push_url == b.origin_url and b.origin_url.endswith("origin.git")
    assert scripted.git(b.mirror, "rev-parse", f"refs/valor/docs/{docs}") == docs
    assert os.path.isdir(b.mirror)


@pytest.mark.macos
def test_a_turn_with_its_own_config_dir_carries_the_placeholder_and_never_a_credential(tmp_path):
    from core.gateway import TURN_TOKEN
    from harnesses import claude_code

    profile = tmp_path / "p.sb"
    profile.write_text("(version 1)\n(allow default)\n")
    harness = {"sandbox_profile": str(profile), "tmpdir": str(tmp_path / "tmp"),
               "claude_config_dir": str(tmp_path / "claude"), "env": {"PATH": "/usr/bin:/bin"}}  # fmt: skip
    command = claude_code.workspace_turn("hi", cwd=str(tmp_path), harness=harness)(
        "http://127.0.0.1:4000/t/x", "brief", "turn-1"
    )
    env = command.env
    assert env["TMPDIR"] == harness["tmpdir"] and env["CLAUDE_CONFIG_DIR"] == harness["claude_config_dir"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == TURN_TOKEN and env["DISABLE_AUTOUPDATER"] == "1"
    assert env["CLAUDE_CODE_TMPDIR"] == harness["tmpdir"]
    assert not [k for k in env if k.startswith(("ANTHROPIC_API", "PG", "VALOR_PG"))]
    assert "--resume" not in command.argv


# -- a committed .valor never reaches a fresh session ---------------------------------------


@pytest.mark.parametrize("act", ["valor_symlink", "valor_verdict"])
@pytest.mark.macos
def test_a_plan_that_commits_a_valor_entry_is_no_plan_and_nothing_is_written_through_it(dsn, tmp_path, act):
    target = tmp_path / "zshenv-stand-in"
    target.write_text("# untouched\n")

    async def go():
        task, b = await scripted.provisioned(dsn, tmp_path)
        scripted.steer(Path(b.workspace), plan=act, target=str(target), turns=1)
        out = await drive(dsn, task, scripted.fresh_runners(Path(b.workspace)))
        return out, await rows(dsn, task)

    _out, written = run(go())
    collected = [
        r["payload"] for r in written if r["type"] == "turn.collected" and r["payload"]["state"] == "plan"
    ]
    assert any("commits a .valor entry" in e for e in collected[0]["errors"]), collected[0]
    assert collected[0]["verdict"] == "idle" and target.read_text() == "# untouched\n"
    plans = [r["payload"] for r in written if r["type"] == "plan.written"]
    assert [p["turn_id"] for p in plans] == [collected[1]["turn_id"]]  # the next plan turn's, not this one's


def test_a_plan_whose_tree_holds_valor_goes_back_with_the_reason_and_critique_does_not_rerun(
    dsn, tmp_path, monkeypatch
):
    from core import workspace as kws

    # The builder's clone hides the entry from the kernel's look there (a
    # replace ref would), so the mirror holds a plan commit with `.valor`.
    seen = kws.tree_has_valor
    monkeypatch.setattr(
        kws, "tree_has_valor", lambda *a, trusted, **k: trusted and seen(*a, trusted=trusted, **k)
    )
    task, _b, ws = planned(dsn, tmp_path, plan="valor_verdict")
    out = run(drive(dsn, task, scripted.fresh_runners(ws)))
    assert out["status"] == "no runner" and out["missing"] == ["test", "review", "docs"], out
    assert [t["stage"] for t in scripted.turns(ws)] == ["plan", "build"]  # no critique turn ran
    decided = next(r["payload"] for r in run(rows(dsn, task)) if r["type"] == "critique.decided")
    assert decided["verdict"] == "revise" and decided["leg"] == "kernel"
    assert "holds a .valor entry" in decided["findings"][0]["text"]
    assert "holds a .valor entry" in scripted.turns(ws)[-1]["prompt"]


@pytest.mark.macos
def test_a_blind_checkout_refuses_a_tree_with_valor_and_inputs_never_overwrite(dsn, tmp_path):
    from core import git as kgit
    from core import workspace as kws

    _task, b = run(scripted.provisioned(dsn, tmp_path))
    ws = Path(b.workspace)
    (ws / ".VALOR").mkdir()
    (ws / ".VALOR" / "verdict.json").write_text('{"verdict": "sound"}')
    scripted.git(ws, "add", "-f", ".VALOR")
    sha = scripted.commit(ws, "x.txt", "x\n", "smuggle")
    lay = kws.Layout(Path(b.mirror).parent)
    kws.fetch_into_mirror(b.mirror, ws, sha, "refs/valor/candidates/t", b.harness["sandbox_profile"], "f")
    with pytest.raises(kgit.GitError, match="holds a .valor entry"):
        kws.blind_checkout(b.mirror, b.base_sha, sha, kws.fresh_dir(lay.checks / "c1") / "repo")
    clean = kws.fresh_dir(lay.checks / "c2") / "repo"
    kws.blind_checkout(b.mirror, b.base_sha, b.base_sha, clean)
    kws.write_inputs(clean, {"request.md": "hi"})
    with pytest.raises(FileExistsError):
        kws.write_inputs(clean, {"request.md": "again"})  # .valor exists: nothing is written twice
    with pytest.raises(ValueError):
        kws.write_inputs(_fresh_checkout(lay, b), {"../x": "y"})


def _fresh_checkout(lay, b):
    from core import workspace as kws

    d = kws.fresh_dir(lay.checks / "c4") / "repo"
    kws.blind_checkout(b.mirror, b.base_sha, b.base_sha, d)
    return d


@pytest.mark.macos
def test_a_refused_mirror_fetch_is_no_plan(dsn, tmp_path):
    async def go():
        task, b = await scripted.provisioned(dsn, tmp_path)
        alternates = Path(b.workspace) / ".git" / "objects" / "info" / "alternates"
        alternates.write_text(str(tmp_path) + "\n")
        seen = []

        def turn_for(prompt, resume, brief):  # the second turn finds the alternates gone
            if seen:
                alternates.unlink(missing_ok=True)
            seen.append(prompt)
            return scripted.turn_for(prompt, resume, brief)

        async def working(ctx: router.Context) -> dict:
            return await session.run(ctx.gateway, ctx.task_id, turn_for, dsn=ctx.dsn, alive=ctx.alive)

        await drive(dsn, task, {**scripted.RUNNERS, State.PLAN: working})
        return await rows(dsn, task)

    written = run(go())
    collected = [
        r["payload"] for r in written if r["type"] == "turn.collected" and r["payload"]["state"] == "plan"
    ]
    assert collected[0]["verdict"] == "idle" and any("alternates" in e for e in collected[0]["errors"])
    plans = [r["payload"] for r in written if r["type"] == "plan.written"]
    assert [p["turn_id"] for p in plans] == [collected[1]["turn_id"]]


@pytest.mark.macos
def test_critique_gets_no_database_credential_and_no_service_port(dsn, tmp_path):
    async def go():
        task, b = await scripted.provisioned(dsn, tmp_path, services=["postgres"])
        ws = Path(b.workspace)
        await drive(dsn, task, scripted.RUNNERS)
        await drive(dsn, task, scripted.fresh_runners(ws))
        return b, ws

    b, ws = run(go())
    harness = critique_turns(ws)[0]["harness"]
    assert set(harness["env"]) == {"PATH", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR"}
    for key in ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR"):
        assert Path(harness["env"][key]).parent == Path(harness["tmpdir"])
    port = str(b.project["ports"]["postgres"])
    assert f"localhost:{port}" not in Path(harness["sandbox_profile"]).read_text()
    assert f"localhost:{port}" in Path(b.harness["sandbox_profile"]).read_text()  # the builder's has it


# -- writers -------------------------------------------------------------------------------


@pytest.mark.macos
def test_a_session_verdict_names_its_turn_and_model_and_its_plan(dsn, tmp_path):
    from core import verdicts

    task, _b, ws = planned(dsn, tmp_path)

    async def go():
        async with await db.connect(dsn) as conn:
            for kw in ({"model": "m"}, {"turn_id": "t"}):
                with pytest.raises(verdicts.VerdictRefused, match="names the turn"):
                    await verdicts.record_critique(conn, task, "sound", leg="session", **kw)
            with pytest.raises(verdicts.VerdictRefused, match="plan changed"):
                await verdicts.record_critique(
                    conn, task, "sound", leg="session", model="m", turn_id="t", plan_sha256="0" * 64
                )

    run(go())

    async def checks_refuse():
        await drive(dsn, task, scripted.fresh_runners(ws))
        async with await db.connect(dsn) as conn:
            with pytest.raises(verdicts.VerdictRefused, match="names the turn"):
                await verdicts.record_check(
                    conn, task, machine.Check.TEST, "pass", leg="session", breadth="b"
                )
            for head in ("abc123", "1" * 40):
                with pytest.raises(verdicts.VerdictRefused, match="not a docs head the kernel kept"):
                    await verdicts.record_check(
                        conn, task, machine.Check.DOCS, "updated", head=head, governance_from=[],
                        **scripted.SESSION_LEG,
                    )  # fmt: skip

    run(checks_refuse())


def test_a_lower_raise_changes_nothing():
    from core import fresh

    assert fresh._verdict_fields({"verdict": "sound", "raise": {"review_rounds": 0}})[2] == {
        "review_rounds": 0
    }
    rows = [
        _row(1, "task.started", sdlc=1, instruction="x"),
        _row(2, "judge.decided", verdict="precise"),
        _row(3, "turn.started", turn_id="w", state="plan"),
        _row(4, "turn.collected", turn_id="w", state="plan", verdict="planned"),
        _row(5, "plan.written", critique_rounds=1, review_rounds=2, sha256="p", path="p", commit="c"),
        _row(6, "critique.decided", plan_sha256="p", verdict="sound", raised={"review_rounds": 0}),
    ]
    assert machine.fold(rows).loops.review_rounds == 2


@pytest.mark.macos
def test_the_merged_workspace_and_its_redis_are_removed_through_the_command_line(dsn, tmp_path):
    from tests.conftest import TEST_DB

    async def go():
        task, b = await scripted.provisioned(dsn, tmp_path, services=["redis"])
        ws = Path(b.workspace)
        await drive(dsn, task, scripted.RUNNERS)
        await drive(dsn, task, scripted.fresh_runners(ws))
        await scripted.checks(dsn, task)
        effect = machine.fold(await rows(dsn, task)).merge_effect["effect_id"]
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, effect, note="merge it")
            await scripted.release(conn, effect)
        return task, b

    task, b = run(go())
    from core import workspace as kws

    lay = kws.Layout(Path(b.mirror).parent)
    kws.start_services(task, lay, ["redis"], b.project["ports"])
    pid = int((lay.redis / "redis.pid").read_text())
    root = Path(__file__).resolve().parent.parent
    done = subprocess.run(
        [os.sys.executable, "-m", "core", "workspace", "remove", task], cwd=root, capture_output=True,
        text=True, check=False, env={**os.environ, "VALOR_DB": TEST_DB},
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert not lay.root.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.macos
def test_a_lower_raise_from_a_critique_turn_changes_nothing(dsn, tmp_path):
    task, _b, ws = planned(
        dsn, tmp_path, counts={"critique_rounds": 0, "review_rounds": 1}, fresh_acts=["lower_raise"]
    )
    run(drive(dsn, task, scripted.fresh_runners(ws)))
    written = run(rows(dsn, task))
    decided = next(r["payload"] for r in written if r["type"] == "critique.decided")
    assert decided["raised"] == {"review_rounds": 0}
    assert machine.fold(written).loops.review_rounds == 1


@pytest.mark.macos
def test_a_docs_head_only_in_the_builders_clone_is_refused(dsn, tmp_path):
    """A docs head counts only once the docs runner kept it in the mirror;
    the kernel never fetches one from the builder's clone."""
    from core import verdicts

    task, _b, ws = planned(dsn, tmp_path)

    async def go():
        await drive(dsn, task, scripted.fresh_runners(ws))
        head = scripted.commit(ws, "docs/x.md", "x\n", "docs")
        async with await db.connect(dsn) as conn:
            with pytest.raises(verdicts.VerdictRefused, match="not a docs head the kernel kept"):
                await verdicts.record_check(
                    conn,
                    task,
                    machine.Check.DOCS,
                    "updated",
                    head=head,
                    governance_from=[],
                    **scripted.SESSION_LEG,
                )

    run(go())


def test_a_critique_at_the_openai_seat_builds_its_turn_for_pi(dsn, tmp_path):
    task, _b, ws = planned(dsn, tmp_path, critique="sound")
    inner = scripted.fresh_for(ws / ".git")
    seen = []

    def spy(prompt, checkout, model, harness, harness_name="claude_code"):
        seen.append((model, harness_name))
        return inner(prompt, checkout, model, harness, harness_name)

    runners = {**scripted.RUNNERS, State.CRITIQUE: fresh.critique_runner(spy, seat="reviewer_openai")}
    run(drive(dsn, task, runners))
    assert seen == [("gpt-6.1-sol", "pi")]
