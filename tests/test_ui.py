"""The status pages (`ui/app.py`) against a database of their own."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from core import db, ledger, routines, tasks
from tests.bridges import configure
from ui.app import make_app

pytestmark = pytest.mark.spend(usd=0)


async def _get(dsn, *paths: str) -> list[str]:
    async with TestClient(TestServer(make_app(dsn))) as client:
        pages = []
        for path in paths:
            response = await client.get(path)
            assert response.status == 200, path
            pages.append(await response.text())
        return pages


def test_tasks_nest_wait_and_cap(fresh):
    async def go():
        async with await db.connect(fresh) as conn:
            root = await tasks.start(conn, tasks.Brief(instruction="root task"))
            child = await tasks.start_child(conn, root, instruction="child task")
            asks = await tasks.start(conn, tasks.Brief(instruction="asking task"))
            await ledger.append(conn, asks, "question.asked", {"question_id": "q1", "text": "which colour?"})
            oldest = await tasks.start(conn, tasks.Brief(instruction="oldest settled"))
            await ledger.append(conn, oldest, "task.stopped", {})
            for i in range(15):
                settled = await tasks.start(conn, tasks.Brief(instruction=f"settled {i}"))
                await ledger.append(conn, settled, "task.stopped", {})
        return root, child, asks, await _get(fresh, "/", "/?all=1")

    root, child, asks, (page, every) = asyncio.run(go())
    needs = page.split("<h2")[1]
    assert asks in needs and root not in needs
    # The child is a collapsed row in its root's tree: the root's <tbody>, after the toggle.
    tree = page[page.rindex("<tbody>", 0, page.index(f'data-task="{root}"')) :]
    tree = tree[: tree.index("</tbody>")]
    assert "class=toggle" in tree and f'<tr data-task="{child}" class=child>' in tree
    assert "oldest settled" not in page and "oldest settled" in every


def test_live_shows_an_open_turn_and_its_subagents(fresh, tmp_path):
    session = "5e55-1011"
    meta = tmp_path / "work"

    async def go():
        async with await db.connect(fresh) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="busy task"))
            await ledger.append(conn, task, "turn.started", {
                "turn_id": "t1", "state": "build", "harness": "claude_code",
                "argv": ["claude", "--model", "claude-opus-5-5", "--session-id", session],
            })  # fmt: skip
        found = meta / task / "state/work/claude/projects/p" / session / "subagents"
        found.mkdir(parents=True)
        (found / "agent-a1.meta.json").write_text(
            json.dumps({"agentType": "Explore", "description": "find the parser", "spawnDepth": 1})
        )
        (found / "agent-a1.jsonl").write_text("{}\n")
        with configure(work_dir=str(meta)):
            return task, (await _get(fresh, "/live"))[0]

    task, page = asyncio.run(go())
    assert task in page and "busy task" in page and "claude-opus-5-5" in page
    assert "find the parser" in page and "Explore" in page
    # No kernel holds this database's lock: the header shows it down.
    assert "<span data-up=no>kernel" in page


def test_every_page_escapes_what_it_shows(fresh):
    planted = "<script>alert(1)</script>"

    async def go():
        async with await db.connect(fresh) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction=planted))
            await ledger.append(conn, task, "question.asked", {"question_id": "q1", "text": planted})
            await ledger.append(conn, task, "turn.started", {"turn_id": "t1", "state": "build"})
        return await _get(
            fresh, "/", "/?all=1", "/live", "/routines", f"/task/{task}", f"/task/{task}/modal", "/attention"
        )

    for page in asyncio.run(go()):
        assert planted not in page


def test_the_task_page_shows_the_newest_rows_with_payloads_cut(fresh):
    async def go():
        async with await db.connect(fresh) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="long task"))
            await ledger.append(conn, task, "turn.started", {"turn_id": "t1", "state": "build", "argv": ["x" * 5000]})
            for i in range(300):
                await ledger.append(conn, task, "message.heard", {"text": f"row-{i}"})
        return await _get(fresh, f"/task/{task}", f"/task/{task}?all=1")

    page, every = asyncio.run(go())
    assert "row-299" in page and "turn.started" not in page and "newest 300 of 302" in page
    assert "turn.started" in every and "x" * 5000 not in every and "more characters" in every


def test_a_routine_run_stopped_after_its_firing_reads_stopped(fresh):
    async def go():
        async with await db.connect(fresh) as conn:
            objective = await tasks.start(conn, tasks.Brief(instruction="routine gone", routine="gone"))
            await ledger.append(conn, routines.STREAM, "routine.registered", {
                "name": "gone", "ceiling": "read", "need": [], "mission_item": "1", "objective": objective,
            })  # fmt: skip
            run = await tasks.start_child(conn, objective, instruction="a run")
            await tasks.stop(conn, run, reason="held")
            row = {"run": run, "outcome": "running", "summary": "paused", "spent_usd_micros": 0}
            await ledger.append(conn, objective, "routine.ran", row)
            return await routines.report(conn, "gone")

    assert asyncio.run(go())["last"]["outcome"] == "stopped"


def test_the_modal_shows_a_tasks_metadata(fresh):
    async def go():
        async with await db.connect(fresh) as conn:
            parent = await tasks.start(
                conn,
                tasks.Brief(
                    instruction="team chat",
                    chat={"channel": "telegram", "chat_id": "-42"},
                    project={"name": "valor"},
                ),
            )
            child = await tasks.start_child(conn, parent, instruction="make the video")
            await ledger.append(conn, parent, "turn.started", {
                "turn_id": "t1", "state": "clarify", "harness": "claude_code",
                "argv": ["claude", "--model", "claude-opus-5-5", "--session-id", "5e55-1011"],
            })  # fmt: skip
        return child, (await _get(fresh, f"/task/{parent}/modal"))[0]

    child, modal = asyncio.run(go())
    for shown in ("valor", "chat · telegram", "telegram -42", "claude --resume 5e55-1011", "make the video"):
        assert shown in modal, shown
    assert f'data-task="{child}"' in modal and f">{child}<" not in modal
