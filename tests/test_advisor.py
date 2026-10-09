"""The advisor: a working turn that writes `.valor/advice.md` and ends no
stage is followed by one fresh session on the other vendor's seat, whose
final message opens the next working turn, in the same session and state.

Real Postgres, real git, the real kernel mirror and blind checkout, and
scripted sessions in place of `claude -p` and Pi (`tests/scripted.py`): the
working script writes `advice.md` as its `advise` entries say, and the
fresh script answers as the advisor with `advice_text`.

Live spend: none.
"""

import asyncio
from pathlib import Path

import pytest

from core import db, fresh, ledger, machine, persona, session, signals, slot, tasks
from core.gateway import Gateway
from core.machine import State
from core.settings import settings
from tests import scripted
from tests.ports import listen

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent
QUESTION = "Plain or formal greeting?"


def run(coro):
    return asyncio.run(coro)


async def with_gateway(dsn, go):
    gateway = Gateway(dsn)
    await gateway.start(port=listen())
    try:
        return await go(gateway)
    finally:
        await gateway.close()


def drive(dsn, task, runners) -> dict:
    return run(with_gateway(dsn, lambda g: scripted.route(g, task, runners, dsn=dsn)))


def ledger_rows(dsn, task) -> list[dict]:
    async def go():
        async with await db.connect(dsn) as conn:
            return await ledger.read(conn, task)

    return run(go())


def building(dsn, tmp_path, **steer):
    """A provisioned task whose plan passed critique, waiting in build, with
    the script steered as `steer` says."""

    async def go():
        task, b = await scripted.provisioned(dsn, tmp_path)
        out = await with_gateway(dsn, lambda g: scripted.route(g, task, scripted.RUNNERS, dsn=dsn))
        assert out["missing"] == ["critique"], out
        await scripted.critique(dsn, task)
        return task, b

    task, b = run(go())
    ws = Path(b.workspace)
    scripted.steer(ws, **steer)
    return task, b, ws


def seen_seats(ws: Path, seen: list):
    """The scripted fresh session, noting the harness and model it is asked
    to run at."""
    make = scripted.fresh_for(ws / ".git")

    def wrapped(prompt, checkout, model, harness, harness_name="claude_code"):
        seen.append((harness_name, model))
        return make(prompt, checkout, model, harness, harness_name)

    return wrapped


def of(rows, kind) -> list[dict]:
    return [r["payload"] for r in rows if r["type"] == kind]


def advisor_turns(ws: Path) -> list[dict]:
    return [t for t in scripted.turns(ws) if t["stage"] == "advice"]


@pytest.mark.macos
def test_an_idle_turn_that_asks_gets_the_other_vendors_answer_in_its_next_turn(dsn, tmp_path):
    task, _b, ws = building(
        dsn,
        tmp_path,
        advise=[{"text": QUESTION, "act": "nothing"}],
        advice_text="Plain.\nTom writes plainly.",
    )
    seen: list = []
    out = drive(dsn, task, scripted.advising_runners(ws, seen_seats(ws, seen)))
    assert out["missing"] == ["test", "review", "docs"]
    t = scripted.turns(ws)
    assert [(x["stage"], x["resume"]) for x in t] == [
        ("plan", ""),
        ("build", scripted.SESSION),
        ("advice", None),
        ("build", scripted.SESSION),
    ]
    assert seen == [("pi", "gpt-6.1-sol")]  # a Claude Code task asks the OpenAI seat
    assert "# The advisor's answer" in t[3]["prompt"]
    assert "  > Plain.\n  > Tom writes plainly." in t[3]["prompt"]

    rows = ledger_rows(dsn, task)
    started = of(rows, "turn.started")
    asked_id, advisor_id = started[1]["turn_id"], started[2]["turn_id"]
    assert started[2]["fresh"] is True and started[2]["stage"] == "advice"
    assert "# Stage: advice" in started[2]["brief"] and "asks no one" in started[2]["brief"]
    assert "# How this session reaches the kernel" not in started[2]["brief"]
    assert "Workspace:" not in started[2]["brief"]
    collected = of(rows, "turn.collected")
    assert (collected[1]["verdict"], collected[1]["advice"]) == ("idle", QUESTION)
    [given] = of(rows, fresh.ADVICE_GIVEN)
    assert given["asked_turn_id"] == asked_id and given["advisor_turn_id"] == advisor_id
    assert (given["seat"], given["model"]) == ("reviewer_openai", "gpt-6.1-sol")
    assert given["answer"] == "Plain.\nTom writes plainly." and "error" not in given
    assert given["head"] == scripted.git(ws, "rev-parse", "HEAD~1")  # the build's head when it asked
    assert isinstance(given["usd_micros"], int)

    # The advisor's checkout: blind, its inputs written by the kernel.
    checkout = Path(advisor_turns(ws)[0]["cwd"])
    inputs = checkout / ".valor" / "inputs"
    assert {p.name for p in inputs.iterdir()} == {
        "question.md",
        "request.md",
        "answers.md",
        "plan.md",
        "diff.patch",
        "uncommitted.md",
    }
    assert (inputs / "question.md").read_text() == QUESTION
    assert "docs/plan.md" in (inputs / "diff.patch").read_text()
    assert not (checkout / ".valor" / "handled").exists()

    # The fold does not read the advisor: without its rows it is the same.
    advisor_rows = [
        r
        for r in rows
        if r["type"] == fresh.ADVICE_GIVEN
        or (r["type"] in ("turn.started", "turn.ended") and r["payload"]["turn_id"] == advisor_id)
    ]
    without = [r for r in rows if r not in advisor_rows]
    a, b = machine.fold(rows), machine.fold(without)
    assert (a.state, a.session, a.entry, a.entry_finished, a.steering) == (
        b.state,
        b.session,
        b.entry,
        b.entry_finished,
        b.steering,
    )

    # What is pending, at each point of the same ledger.
    def at(kind, turn_id, rows=rows):
        return next(
            i for i, r in enumerate(rows) if r["type"] == kind and r["payload"].get("turn_id") == turn_id
        )

    assert session.pending_advice(rows[: at("turn.collected", asked_id) + 1])["turn_id"] == asked_id
    no_answer = [r for r in rows if r["type"] != fresh.ADVICE_GIVEN]
    # the advisor's own turn ending is no working turn
    assert (
        session.pending_advice(no_answer[: at("turn.ended", advisor_id, no_answer) + 1])["turn_id"]
        == asked_id
    )
    answered = next(i for i, r in enumerate(rows) if r["type"] == fresh.ADVICE_GIVEN)
    assert session.pending_advice(rows[: answered + 1]) is None
    built_id = started[3]["turn_id"]
    assert session.pending_advice(no_answer[: at("turn.ended", built_id, no_answer) + 1]) is None
    assert session.pending_advice(no_answer) is None
    assert session.pending_advice(rows) is None


@pytest.mark.macos
@pytest.mark.parametrize(
    "steer, reason",
    [
        ({"fresh_acts": ["fail"]}, "the advisor's turn failed"),
        ({"advice_text": ""}, "the advisor's final message was empty"),
        ({"advice_text": "a\x00b"}, ledger.UNSTORABLE),
    ],
    ids=["failed", "empty", "nul"],
)
def test_an_advisor_that_does_not_answer_is_reported_and_the_task_moves_on(dsn, tmp_path, steer, reason):
    task, _b, ws = building(dsn, tmp_path, advise=[{"text": QUESTION, "act": "nothing"}], **steer)
    out = drive(dsn, task, scripted.advising_runners(ws))
    assert out["missing"] == ["test", "review", "docs"]
    assert len(advisor_turns(ws)) == 1
    [given] = of(ledger_rows(dsn, task), fresh.ADVICE_GIVEN)
    assert "answer" not in given and reason in given["error"]
    last = scripted.turns(ws)[-1]
    assert last["stage"] == "build" and "# The advisor did not answer" in last["prompt"]
    assert reason in last["prompt"]
    out = drive(dsn, task, scripted.advising_runners(ws))  # asks nothing again
    assert len(advisor_turns(ws)) == 1


@pytest.mark.macos
@pytest.mark.parametrize("act, verdict", [("default", "candidate"), ("ask", "asked")])
def test_advice_beside_a_signal_that_ends_the_stage_runs_no_advisor(dsn, tmp_path, act, verdict):
    task, _b, ws = building(dsn, tmp_path, advise=[{"text": QUESTION, "act": act}])
    drive(dsn, task, scripted.advising_runners(ws))
    assert advisor_turns(ws) == []
    rows = ledger_rows(dsn, task)
    last = of(rows, "turn.collected")[-1]
    assert last["verdict"] == verdict and last["advice"] is None
    assert session.ADVICE_BESIDE in last["errors"]
    assert not of(rows, fresh.ADVICE_GIVEN)


@pytest.mark.parametrize(
    "state, found",
    [
        (State.CLARIFY, {"question": "q"}),
        (State.CLARIFY, {"no_question": "none"}),
        (State.PLAN, {"plan": {"path": "docs/plan.md"}}),
        (State.BUILD, {"done": "built"}),
        (State.PATCH, {"question": "q"}),
    ],
)
def test_advice_counts_only_on_a_turn_that_ends_no_stage(state, found, tmp_path):
    beside = signals.Signals(advice=QUESTION, **found)
    _verdict, extra, errors = session._verdict(state, beside, str(tmp_path), "t", True)
    assert "advice" not in extra and session.ADVICE_BESIDE in errors
    alone = signals.Signals(advice=QUESTION)
    assert session._verdict(state, alone, str(tmp_path), "t", True) == ("idle", {"advice": QUESTION}, [])
    assert session._verdict(state, alone, str(tmp_path), "t", False) == (
        "failed",
        {},
        [session.ADVICE_UNFINISHED],
    )


def test_a_candidate_row_reduced_to_idle_carries_no_advice(dsn, tmp_path, monkeypatch):
    """A candidate turn that also wrote advice.md, whose row Postgres refuses
    until `_reduce` cuts it to idle, keeps no advice, so nothing asks."""
    ws, _ = scripted.workspace(tmp_path)
    (ws / ".valor").mkdir()
    (ws / ".valor" / "done.md").write_text("built")
    (ws / ".valor" / "advice.md").write_text(QUESTION)
    real, calls = session._written, []

    async def refusing(conn, task_id, collected, follow):
        calls.append(collected)
        return "refused for the test" if len(calls) <= 2 else await real(conn, task_id, collected, follow)

    monkeypatch.setattr(session, "_written", refusing)

    async def go():
        task = await scripted.start(dsn, ws)
        turn = ledger.new_id()
        found = signals.collect(ws, turn)
        async with await db.connect(dsn) as conn:
            verdict = await session.record(conn, task, turn, found, state=State.BUILD, workspace=str(ws))
            return verdict, await ledger.read(conn, task)

    verdict, rows = run(go())
    assert calls[0]["verdict"] == "candidate" and calls[0]["advice"] is None
    [collected] = of(rows, "turn.collected")
    assert verdict == "idle" and collected["advice"] is None
    assert session.pending_advice(rows) is None


@pytest.mark.macos
def test_a_run_ended_before_the_answer_asks_on_its_next_run_once(dsn, tmp_path):
    """An advisor that is preempted, or whose run lock dies, ends the run
    as a turn would, and no working turn runs past the question; the next
    run asks it, once."""
    task, b, ws = building(dsn, tmp_path, advise=[{"text": QUESTION, "act": "nothing"}])
    asked: list = []

    def stub(answer):
        async def advise(gateway, task_id, dsn_, alive, collected):
            asked.append(collected["turn_id"])
            return dict(answer)

        return advise

    async def once(gateway, advise):
        return await session.run(
            gateway, task, scripted.turn_for, dsn=dsn, performers=scripted.performers(b), advise=advise
        )

    assert run(with_gateway(dsn, lambda g: once(g, stub(slot.PREEMPTED)))) == slot.PREEMPTED
    assert run(with_gateway(dsn, lambda g: once(g, stub({"status": "lock lost"}))))["status"] == "lock lost"
    assert run(with_gateway(dsn, lambda g: once(g, stub({"status": "stopped"}))))["status"] == "stopped"
    assert [t["stage"] for t in scripted.turns(ws)] == ["plan", "build"]
    advise = fresh.advise(scripted.fresh_for(ws / ".git"))
    out = run(with_gateway(dsn, lambda g: once(g, advise)))
    assert out["status"] == "moved"
    assert [t["stage"] for t in scripted.turns(ws)] == ["plan", "build", "advice", "build"]
    assert len(set(asked)) == 1 and len(asked) == 3
    assert len(of(ledger_rows(dsn, task), fresh.ADVICE_GIVEN)) == 1


def asked_row(rows) -> dict:
    return of(rows, "turn.collected")[-1]


@pytest.mark.macos
def test_a_task_with_no_mirror_is_told_the_advisor_cannot_run(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws)
        await with_gateway(dsn, lambda g: scripted.route(g, task, scripted.RUNNERS, dsn=dsn))
        await scripted.critique(dsn, task)
        scripted.steer(ws, advise=[{"text": QUESTION, "act": "nothing"}])
        await with_gateway(dsn, lambda g: scripted.route(g, task, scripted.advising_runners(ws), dsn=dsn))
        return task

    task = run(go())
    [given] = of(ledger_rows(dsn, task), fresh.ADVICE_GIVEN)
    assert given["error"] == "a fresh session runs only in a workspace the kernel provisioned"
    assert advisor_turns(ws) == []
    assert "# The advisor did not answer" in scripted.turns(ws)[-1]["prompt"]


def advise_once(dsn, task, ws, alive_answers=(True, True), collected=None):
    """`fresh.advise` called once on the task's latest collected turn, with
    `alive` answering in turn as `alive_answers` says."""
    answers = list(alive_answers)

    async def alive():
        return answers.pop(0) if answers else True

    async def go(gateway):
        async with await db.connect(dsn) as conn:
            asked = collected or asked_row(await ledger.read(conn, task))
        return await fresh.advise(scripted.fresh_for(ws / ".git"))(gateway, task, dsn, alive, asked)

    return run(with_gateway(dsn, go))


@pytest.mark.macos
def test_a_head_that_commits_valor_is_the_advisors_recorded_reason(dsn, tmp_path):
    task, _b, ws = building(dsn, tmp_path)
    scripted.commit(ws, ".valor/x.md", "planted", "plant")
    asked = {"turn_id": "asked-1", "state": "build", "verdict": "idle", "advice": QUESTION}
    out = advise_once(dsn, task, ws, collected=asked)
    assert out["status"] == "advised"
    [given] = of(ledger_rows(dsn, task), fresh.ADVICE_GIVEN)
    assert "commits a .valor entry" in given["error"] and "answer" not in given
    assert advisor_turns(ws) == []


@pytest.mark.macos
@pytest.mark.parametrize("alive, turns", [((False,), 0), ((True, False), 1)], ids=["before", "after"])
def test_a_dead_run_lock_appends_nothing(dsn, tmp_path, alive, turns):
    task, _b, ws = building(dsn, tmp_path)
    asked = {"turn_id": "asked-1", "state": "build", "verdict": "idle", "advice": QUESTION}
    assert advise_once(dsn, task, ws, alive, asked) == {"status": "lock lost"}
    assert len(advisor_turns(ws)) == turns
    assert not of(ledger_rows(dsn, task), fresh.ADVICE_GIVEN)


@pytest.mark.macos
def test_a_stopped_task_asks_nothing(dsn, tmp_path):
    task, _b, ws = building(dsn, tmp_path)

    async def stop():
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="a test stop")

    run(stop())
    asked = {"turn_id": "asked-1", "state": "build", "verdict": "idle", "advice": QUESTION}
    assert advise_once(dsn, task, ws, collected=asked)["status"] == "stopped"
    assert advisor_turns(ws) == [] and not of(ledger_rows(dsn, task), fresh.ADVICE_GIVEN)


def test_the_advisor_is_the_seat_on_the_other_vendor():
    assert fresh.advisor_seat("claude_code") == "reviewer_openai"
    assert fresh.advisor_seat("pi") == "reviewer"


OLD_ASKING_RULE = (
    "materially changes the outcome",
    "the authority needed",
    "authority it needs",
    "A material question goes",
    "A material question found",
)
CONDUCT_LINE = (
    "When unsure, ask the advisor, then act. Ask Tom only for vision, priorities, the cost and "
    "benefit of a tradeoff in how the company works, or something only he holds."
)


def test_the_persona_and_every_stage_file_carry_the_rulings_asking_rule():
    rendered = " ".join(persona.render(settings.persona_dir).split())
    assert CONDUCT_LINE in rendered
    stages = sorted(Path(settings.stages_dir).glob("*.md"))
    assert {p.name for p in stages} >= {"advice.md", "channel.md", "clarify.md", "build.md"}
    for text in [rendered, *(" ".join(p.read_text().split()) for p in stages)]:
        for old in OLD_ASKING_RULE:
            assert old not in text
    channel = " ".join((Path(settings.stages_dir) / "channel.md").read_text().split())
    assert "`.valor/advice.md`" in channel and "The advisor informs; you decide." in channel
    for name in ("build.md", "plan.md", "patch.md", "clarify.md"):
        assert "`.valor/advice.md`" in (Path(settings.stages_dir) / name).read_text()
