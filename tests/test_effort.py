"""A seat's effort level reaches Claude Code as `--effort` (docs/plans/effort-levels.md):
a task's turns at the level it started with, a chat turn at the `chat` seat's,
and an unset seat at today's argv."""

import asyncio
import sys

import pytest

from core import __main__, db, settings, tasks
from harnesses import claude_code

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


def test_a_task_turn_runs_at_its_briefs_level_and_a_brief_stored_without_one_at_none():
    body = {"instruction": "x", "workspace": ".", "harness": PROFILE}
    assert effort(turn(tasks.Brief.load(body))) is None
    assert effort(turn(tasks.Brief.load({**body, "effort": "high"}))) == "high"


def test_a_chat_turn_first_and_resumed_runs_at_the_chat_seats_level_whatever_its_brief_says(monkeypatch):
    b = tasks.Brief(
        instruction="x",
        workspace=".",
        harness=PROFILE,
        effort="max",
        chat={"channel": "slack", "chat_id": "C1"},
    )
    assert effort(turn(b)) is None
    monkeypatch.setitem(settings.EFFORT, "chat", "medium")
    assert effort(turn(b)) == effort(turn(b, resume="s")) == "medium"


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
