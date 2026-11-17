"""A seat's effort level reaches Claude Code as `--effort` (docs/plans/effort-levels.md):
a task's turns at the level it started with, a chat turn at the `chat` seat's,
and an unset seat at today's argv."""

import asyncio
import dataclasses
import sys
import uuid

import pytest

from core import __main__, db, gateway, routines, settings, tasks
from harnesses import claude_code
from tests import bridges

PROFILE = {"sandbox_profile": "turn"}


def argv(harness: dict, resume: str | None = None) -> list[str]:
    return claude_code.workspace_turn("p", cwd=".", resume=resume, model="m", harness=harness)(
        "http://h:1", "b", "t"
    ).argv


def effort(args: list[str]) -> str | None:
    return args[args.index("--effort") + 1] if "--effort" in args else None


def test_the_flag_follows_the_model_only_when_a_level_is_given():
    plain = argv(PROFILE, resume="s")
    assert "--effort" not in plain
    given = argv({**PROFILE, "effort": "low"}, resume="s")
    at = given.index("--model")
    assert given[at : at + 4] == ["--model", "m", "--effort", "low"]
    assert [a for a in given if a not in ("--effort", "low")] == plain


def turn(b: tasks.Brief, resume: str | None = None) -> list[str]:
    return __main__._turn_for("p", resume, b)("http://h:1", "b", "t").argv


def test_a_task_turn_runs_at_its_briefs_level_and_a_brief_stored_without_one_at_its_models():
    body = {"instruction": "x", "workspace": ".", "harness": PROFILE, "model": "claude-fable-5-2"}
    assert effort(turn(tasks.Brief.load(body))) is None
    assert effort(turn(tasks.Brief.load({**body, "model": "claude-opus-5-5"}))) == "medium"
    assert effort(turn(tasks.Brief.load({**body, "effort": "high"}))) == "high"


def test_a_chat_turn_first_and_resumed_runs_at_the_chat_entrys_level_whatever_its_brief_says(monkeypatch):
    b = tasks.Brief(
        instruction="x",
        workspace=".",
        harness=PROFILE,
        effort="max",
        model="claude-fable-5-2",
        chat={"channel": "slack", "chat_id": "C1"},
    )
    monkeypatch.delitem(settings.EFFORT, "chat", raising=False)
    assert tasks.turn_effort(b) is None
    monkeypatch.setitem(settings.EFFORT, "chat", "medium")
    # `session.run` gives each turn its level as it gives it its model.
    run = dataclasses.replace(b, effort=tasks.turn_effort(b))
    assert effort(turn(run)) == effort(turn(run, resume="s")) == "medium"


def test_a_call_workers_turn_runs_at_its_models_level_not_frontiers_or_chats(monkeypatch):
    """The worker runs `_turn_for` on the call session's Brief with its asking
    chat laid on (`core.__main__._call`); that Brief, as `call.py` writes it,
    has no level, so it runs at Opus's."""
    monkeypatch.setitem(settings.EFFORT, "frontier", "high")
    monkeypatch.setitem(settings.EFFORT, "chat", "low")
    worker = tasks.Brief(
        instruction="x", workspace=".", harness=PROFILE, chat={"channel": "slack", "chat_id": "C1"}
    )
    assert effort(turn(worker)) == "medium"


def test_a_routines_objective_and_run_start_with_no_level_with_frontier_set(dsn, monkeypatch):
    monkeypatch.setitem(settings.EFFORT, "frontier", "high")

    async def go():
        async with await db.connect(dsn) as conn:
            objective, _ = await routines.ensure(conn, routines.load("hours"))
            return await tasks.brief(conn, objective)

    assert asyncio.run(go()).effort is None
    given = {}

    async def captured(conn, args):
        given["effort"] = getattr(args, "effort", None)
        return "t"

    monkeypatch.setattr(__main__, "_start_project", captured)
    asyncio.run(
        __main__._routine_project(
            None,
            parent="p",
            instruction="i",
            routine="hours",
            ceiling="act",
            model="frontier",
            project="valor",
            branch=None,
            marker={},
        )
    )
    assert given == {"effort": None}


def test_a_task_tom_starts_by_message_takes_the_frontier_entrys_level(dsn, monkeypatch, tmp_path):
    from tests.test_kernel import at, tom_says

    monkeypatch.setitem(settings.EFFORT, "frontier", "high")
    tom = f"T0ABC/U{uuid.uuid4().hex[:8].upper()}"

    async def go():
        bound = await tom_says(dsn, tom, "write a haiku", at(11))
        async with await db.connect(dsn) as conn:
            return await tasks.brief(conn, bound["task_id"])

    with bridges.operator(tmp_path, operator_slack_ids=(tom,)):
        assert asyncio.run(go()).effort == "high"


def test_a_seat_without_a_level_adds_nothing():
    with pytest.MonkeyPatch.context() as m:
        m.setitem(settings.EFFORT, "reviewer", "low")
        assert settings.seat_effort("reviewer") == {"effort": "low"}
    assert settings.seat_effort("reviewer") == {}


def start(dsn, monkeypatch, tmp_path, *extra) -> tasks.Brief:
    connect = db.connect
    monkeypatch.setattr(db, "connect", lambda d=None, **kw: connect(d or dsn, **kw))
    monkeypatch.setattr(sys, "argv", ["core", "start", "x", "--workspace", str(tmp_path), *extra])
    printed: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a))))
    __main__.main()

    async def read():
        async with await db.connect(dsn) as conn:
            return await tasks.brief(conn, printed[0].strip())

    return asyncio.run(read())


def test_core_start_takes_the_seats_level_none_for_a_bare_model_id_and_effort_wins(
    dsn, monkeypatch, tmp_path
):
    monkeypatch.setitem(settings.EFFORT, "frontier", "medium")
    assert start(dsn, monkeypatch, tmp_path).effort == "medium"
    assert start(dsn, monkeypatch, tmp_path, "--model", "claude-fable-5-2").effort is None
    assert start(dsn, monkeypatch, tmp_path, "--effort", "low").effort == "low"


def test_an_unknown_level_is_refused(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["core", "start", "x", "--effort", "extreme"])
    with pytest.raises(SystemExit):
        __main__.main()
    assert "invalid choice" in capsys.readouterr().err


def test_every_seat_on_opus_runs_at_medium_by_name_and_no_other_is_set(monkeypatch, tmp_path):
    """Tom, 2026-11-17: "Keep Opus on Medium all the time"; chat back at the default."""
    assert settings.EFFORT == {}
    opus = [seat for seat, (harness, model) in settings.SEATS.items() if model == "claude-opus-5-5"]
    assert opus and all(settings.SEATS[s][0] == "claude_code" for s in opus)
    for seat in opus:
        assert effort(claude_code.workspace_turn("p", cwd=".", resume="s", model=settings.resolve_model(seat),
                                                 harness=PROFILE)("http://h:1", "b", "t").argv) == "medium"  # fmt: skip
    assert effort(argv(PROFILE)) is None  # another model keeps Claude Code's default

    seen = []

    async def exec_(*a, **kw):
        seen.extend(a)
        raise OSError("stop")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_)
    for seat in opus:
        with pytest.raises(gateway.Unanswered):
            asyncio.run(gateway.ask("claude", "http://h:1", tmp_path, seat, "s", "t"))
        assert effort(seen) == "medium"
        seen.clear()
