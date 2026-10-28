"""A chat over several repositories (Tom, 2026-10-28: "scope this channel
to several repositories"): a spec's `projects` lists the other projects its
chats work in; `task.start` starts a task under the one the request names,
carrying the chat it came from through provisioning; the session's Brief
lists the repositories; the board covers their tasks."""

import asyncio
import pathlib
import uuid

import pytest

from core import __main__ as kernel_main
from core import board, broker, db, ledger, notices, tasks, workspace
from core.settings import settings
from tests import bridges, scripted, test_kernel
from tests.test_board import ask, board_rows, channel, owe, session_task
from tests.test_kernel import new_task

dm = test_kernel.dm  # Tom's Slack DM and the operator, as a fixture

pytestmark = pytest.mark.spend(usd=0)
run = asyncio.run


def spec(name: str, *, chats=(), board_chat=None, projects=(), repo="unused", machine=None) -> None:
    text = f'name = "{name}"\nrepo = "{repo}"\nkind = "plain"\nsuite = "true"\n'
    text += f"chats = [{', '.join(f'"slack:{c}"' for c in chats)}]\n"
    text += f'board = "slack:{board_chat}"\n' if board_chat else ""
    text += f"projects = [{', '.join(f'"{p}"' for p in projects)}]\n"
    text += f'machine = "{machine}"\n' if machine else ""
    (pathlib.Path(settings.projects_dir) / f"{name}.toml").write_text(text)


def names() -> tuple[str, str]:
    tag = uuid.uuid4().hex[:6]
    return f"site{tag}", f"app{tag}"


async def performer(dsn, session_id, action_type="task.start"):
    from tools import chat_tasks

    async with await db.connect(dsn) as conn:
        b = await tasks.brief(conn, session_id)
    return next(p for p in chat_tasks.performers(b, dsn) if p.action_type == action_type)


def start(project=None):
    payload = {"instruction": "Fix the tiling hotkey", "by": "tom"}
    return broker.Action(
        "task.start", "work", payload if project is None else {**payload, "project": project}
    )


async def started(dsn, session_id, project=None) -> str:
    p = await performer(dsn, session_id)
    async with await db.connect(dsn) as conn:
        assert await p.refuse(conn, start(project)) is None
    return (await p.perform(start(project), ledger.new_id()))["task_id"]


async def brief_of(dsn, task_id) -> tasks.Brief:
    async with await db.connect(dsn) as conn:
        return await tasks.brief(conn, task_id)


# -- the spec ------------------------------------------------------------------------


def test_a_spec_lists_other_projects_by_name_and_none_by_default():
    base = {"name": "s", "repo": "r", "kind": "plain", "suite": "true"}
    assert workspace.Spec.from_dict(base).projects == ()
    assert workspace.Spec.from_dict({**base, "projects": ["a", "b-c"]}).projects == ("a", "b-c")
    for bad in ([""], [3]):
        with pytest.raises(workspace.Refused, match="not a project name"):
            workspace.Spec.from_dict({**base, "projects": bad})


# -- task.start under a listed project -----------------------------------------------


def test_task_start_runs_under_the_named_project_else_the_sessions_own(dsn, dm, tmp_path):
    """No `project` is the session's own; a listed one or its own by name
    is taken. Nothing is refused for its project (no new limit)."""
    chat = channel()
    site, app = names()
    spec(site, chats=[chat], projects=[app])
    spec(app)

    async def go():
        here = await session_task(dsn, chat)
        default = await started(dsn, here)
        own = await started(dsn, here, site)
        listed = await started(dsn, here, app)
        return [await brief_of(dsn, t) for t in (default, own, listed)]

    briefs = run(go())
    assert [b.project for b in briefs] == [
        {"name": site, "chat": f"slack:{chat}"},
        {"name": site, "chat": f"slack:{chat}"},
        {"name": app, "chat": f"slack:{chat}"},
    ]
    assert all(b.chat is None for b in briefs)  # a child is no chat session


def test_a_listed_projects_child_checks_out_its_repo_and_keeps_its_chat_once_provisioned(dsn, dm, tmp_path):
    """Provisioning remakes `project` from the spec; the chat the child came
    from survives it, so the turn reads that chat (`VALOR_CHAT`), and its
    workspace is a checkout of the listed project's repository."""
    _, origin = scripted.workspace(tmp_path)
    chat = channel()
    site, app = names()
    spec(site, chats=[chat], projects=[app])
    spec(app, repo=str(origin))

    async def go():
        here = await session_task(dsn, chat)
        child = await started(dsn, here, app)
        made = await asyncio.to_thread(workspace.provision, child, workspace.Spec.load(app), {})
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, child, "workspace.provisioned", {"fields": made.brief_fields()})
        return await brief_of(dsn, child)

    b = run(go())
    assert b.project["name"] == app and b.project["chat"] == f"slack:{chat}"
    assert b.project["kind"] == "plain" and b.project["repo"] == str(origin)
    assert (pathlib.Path(b.workspace) / "README.md").read_text() == "a toy repository\n"
    assert kernel_main._reading_env(b)["VALOR_CHAT"] == f"slack:{chat}"


def test_a_task_reads_its_own_chat_else_the_one_it_came_from_else_its_projects_first(dsn, dm):
    """A yuda.me child of the Slack channel reads that channel, not the
    project's first chat; a task no chat started keeps the first."""
    team, chat = f"-{uuid.uuid4().int % 10**12}", channel()
    site, app = names()
    (pathlib.Path(settings.projects_dir) / f"{site}.toml").write_text(
        f'name = "{site}"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\n'
        f'chats = ["telegram:{team}", "slack:{chat}"]\nprojects = ["{app}"]\n'
    )
    spec(app)

    async def go():
        here = await session_task(dsn, chat)
        kids = [await started(dsn, here), await started(dsn, here, app)]
        loose = await new_task(dsn, project={"name": site})
        return [await brief_of(dsn, t) for t in (here, *kids, loose)]

    session_brief, own_child, app_child, loose = run(go())
    read = [kernel_main._reading_env(b)["VALOR_CHAT"] for b in (session_brief, own_child, app_child, loose)]
    assert read == [f"slack:{chat}", f"slack:{chat}", f"slack:{chat}", f"telegram:{team}"]


# -- the Brief's Repos line ----------------------------------------------------------


def test_every_chat_of_a_project_listing_projects_names_its_repositories(dsn, dm):
    """Each chat of the spec, its team chats too (their room keeps
    engineering out), gets the Repos line, its own first; a project
    listing none gets no line."""
    chat, team, plain_chat = channel(), channel(), channel()
    site, app = names()
    spec(site, chats=[chat, team], projects=[app], repo="https://example.com/site.git")
    spec(app, repo="https://example.com/app.git")
    spec(f"plain{uuid.uuid4().hex[:6]}", chats=[plain_chat])

    async def text(task):
        async with await db.connect(dsn) as conn:
            return (await tasks.dispatch(conn, task))["text"]

    async def go():
        sessions = [await session_task(dsn, c) for c in (chat, team, plain_chat)]
        for s in sessions:
            async with await db.connect(dsn) as conn:
                await ledger.append(conn, s, "workspace.provisioned", {"fields": {"workspace": "/tmp"}})
        return [await text(s) for s in sessions]

    main, side, plain = run(go())
    line = f"\nRepos: {site} (https://example.com/site.git); {app} (https://example.com/app.git). "
    assert line in main and line in side
    assert '"project"' in main.split("\nRepos: ", 1)[1].split("\n", 1)[0]
    assert "\nRepos: " not in plain


# -- the board covers the listed projects ---------------------------------------------


def test_a_listed_projects_question_is_on_the_board_and_answered_there(dsn, dm):
    """A waiting child under the listed project is in the board's
    snapshot, gets no separate question report, and the board session
    answers it; one started elsewhere is not the board's to carry."""
    chat = channel()
    site, app = names()
    spec(site, chats=[chat], board_chat=chat, projects=[app])
    spec(app)

    async def go():
        here = await session_task(dsn, chat)
        child = await started(dsn, here, app)
        await ask(dsn, child, "Bind snap to ctrl-opt?")
        elsewhere = await new_task(dsn, project={"name": app})
        async with await db.connect(dsn) as conn:
            await notices.owe(conn, child)
            on_board = await board.here(conn, child)
            not_on = await board.here(conn, elsewhere)
            answers = await board.answers(conn, here, child)
            snap = await board.snapshot(conn, workspace.Spec.load(site))
        await owe(dsn)
        rows = await bridges.rows(dsn, here)
        return child, on_board, not_on, answers, snap, rows, await board_rows(dsn, chat)

    child, on_board, not_on, answers, snap, rows, board_ = run(go())
    assert on_board and answers and not not_on
    assert [q["task_id"] for q in snap["open"]] == [child]
    assert [r["payload"]["kind"] for r in rows if r["type"] == "child.reported"] == ["board"]
    assert "Bind snap to ctrl-opt?" in board_[0]["text"]


def test_a_project_naming_its_own_board_resolves_to_itself(tmp_path):
    with bridges.operator(tmp_path):
        chat = channel()
        site, app = names()
        spec(site, chats=[chat], board_chat=chat, projects=[app])
        spec(app)
        assert board.board_spec(site).name == site
        assert board.board_spec(app).name == site
        assert board.board_spec("nosuchproject") is None


# -- the real specs --------------------------------------------------------------------


def real_specs() -> dict[str, workspace.Spec]:
    return test_kernel.real_specs()


def test_yudame_dev_covers_the_site_and_the_mac_app():
    """Tom, 2026-10-28: #yudame-dev is yuda.me and yudame-tools."""
    specs = real_specs()
    yudame, tools = specs["yudame"], specs["yudame-tools"]
    assert yudame.projects == ("yudame-tools",)
    room = yudame.rooms["slack:T0B6E8JPE3C/C0CBX4MBN1L"]
    assert "yudame/yuda.me" in room and "yudame/yudame-tools" in room
    assert tools.repo == tools.merge_url == "https://github.com/yudame/yudame-tools.git"
    assert tools.chats == () and tools.client == "yudame" and tools.branch == "main"
    # Swift Testing only: the report lands at {junit} with --disable-xctest alone.
    assert "--disable-xctest" in tools.suite and "--xunit-output {junit}" in tools.suite


def test_every_listed_project_loads_and_shares_the_machine_of_the_one_listing_it():
    """A child runs on the Mac holding the chat, whatever its spec's
    `machine` (projects/README.md)."""
    specs = real_specs()
    for s in specs.values():
        for name in s.projects:
            assert name in specs, (s.name, name)
            assert (specs[name].machine or settings.default_machine) == (
                s.machine or settings.default_machine
            ), (s.name, name)
