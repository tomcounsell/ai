"""The old system's memory, imported (docs/plans/memory-import.md): the prep
script's choice and deduplication, `memory import`, and the ingest and
recall of what it appends, on real Postgres, with the model pass stubbed."""

import asyncio
import importlib.util
import json
import pathlib
import uuid

import pytest

from core import db, memory, tasks
from tests import bridges

pytestmark = pytest.mark.spend(usd=0)

ROOT = pathlib.Path(__file__).resolve().parent.parent
FLOWERS = "https://example.test/flowers.git"
WEEDS = "https://example.test/weeds.git"


@pytest.fixture
def specs(tmp_path):
    """Two projects on this machine and one placed on another."""
    with bridges.operator(tmp_path) as s:
        projects = pathlib.Path(s.projects_dir)
        for name, repo, extra in (
            ("flowers", FLOWERS, ""),
            ("weeds", WEEDS, ""),
            ("faraway", "https://example.test/faraway.git", 'machine = "another Mac"\n'),
        ):
            (projects / f"{name}.toml").write_text(
                f'name = "{name}"\nclient = "{name}-co"\nrepo = "{repo}"\nkind = "plain"\nsuite = "true"\n{extra}'
            )
        yield s


def line(text: str, project: str = FLOWERS, memory_id: str | None = None) -> str:
    return json.dumps(
        {
            "text": text,
            "project": project,
            "client": "flowers-co",
            "by": "tom",
            "memory_id": memory_id or uuid.uuid4().hex,
            "merged_from": [],
        }
    )


def test_an_imported_row_is_recalled_for_its_own_project(dsn, specs):
    async def go():
        async with await db.connect(dsn) as conn:
            text = "Invoices for the florist go out as PDF, never as a spreadsheet."
            await memory.import_old(conn, [line(text)])
            await memory.ingest(conn)
            from memory.records import Record, use

            use(memory.dsn_for(conn))
            (record,) = [r for r in Record.query.filter(project=FLOWERS) if r.text == text]
            assert (record.client, record.origin, record.ledger) == (
                "flowers-co",
                "imported",
                memory.ledger_name(conn),
            )
            assert (record.meta["by"], record.meta["via"]) == ("tom", "the old system's memory")
            recalled = {}
            for name, repo in (("flowers", FLOWERS), ("weeds", WEEDS)):
                brief = tasks.Brief(
                    instruction="send the florist's invoices as PDF",
                    project={"name": name, "repo": repo, "client": f"{name}-co"},
                )
                recalled[name] = await memory.recall(conn, await tasks.start(conn, brief), None)
            assert text in recalled["flowers"]
            assert text not in recalled["weeds"]

    asyncio.run(go())


def test_import_twice_appends_once_and_only_what_is_served_here(dsn, specs):
    async def go():
        async with await db.connect(dsn) as conn:
            lines = [line("one"), line("two"), line("far", "https://example.test/faraway.git")]
            first = await memory.import_old(conn, lines)
            second = await memory.import_old(conn, lines)
            n = await (
                await conn.execute(
                    "SELECT count(*) FROM events WHERE task_id = 'memory' AND type = 'memory.imported' "
                    "AND payload->>'memory_id' = ANY(%s)",
                    ([json.loads(x)["memory_id"] for x in lines],),
                )
            ).fetchone()
        assert first == {"appended": 2, "already imported": 0, "not served here": 1}
        assert second == {"appended": 0, "already imported": 2, "not served here": 1}
        assert n == (2,)

    asyncio.run(go())


@pytest.fixture
def prep(specs, monkeypatch):
    """The prep script, with northwind and valor specs and nothing judged not
    worth recalling unless a test says so."""
    (pathlib.Path(specs.projects_dir) / "northwind.toml").write_text(
        'name = "northwind"\nclient = "northwind"\nrepo = "https://example.test/northwind.git"\nkind = "plain"\nsuite = "true"\n'
    )
    (pathlib.Path(specs.projects_dir) / "valor.toml").write_text(bridges.VALOR_SPEC)
    spec = importlib.util.spec_from_file_location(
        "memory_import_prep", ROOT / "scripts/memory_import_prep.py"
    )
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    monkeypatch.setattr(prep, "worth", lambda batch: [])
    return prep


def rec(i, content, source="human", agent="Tom", project="valor", superseded=""):
    return {
        "memory_id": i, "source": source, "superseded_by": superseded, "project_key": project,
        "agent_id": agent, "content": content, "title": "", "metadata": {}, "relevance": 0.0,
    }  # fmt: skip


def test_prep_chooses_maps_and_collapses(tmp_path, prep, monkeypatch):
    judged = []
    monkeypatch.setattr(
        prep, "judge", lambda group: judged.append(group) or [[r["memory_id"] for r in group]]
    )

    monday = "every Monday morning the weekly status report goes out to the client by email"
    near = "the deploy goes out on Fridays after the client signs off on the release notes"
    export = [
        rec("summary", "In a 12-event session Valor fixed the build", source="agent", agent="tui-1"),
        rec("superseded", "an old preference", superseded="m9"),
        rec("knowledge", "a knowledge doc", source="knowledge", agent=None),
        rec(
            "northwind", "Amr wants weekly reports", agent="extraction-tg_northwind_-100123_4", source="agent"
        ),
        rec(
            "monday1",
            "Harper sends the weekly status report to the client every Monday morning",
            agent="Harper",
            project="northwind",
        ),
        rec("monday2", monday, agent="Jordan", project="northwind"),
        rec("tom", near),
        rec("jamie", near + " today", agent="Jamie"),
        rec("lost", "nobody knows where this goes", source="agent", agent=None, project=None),
    ]
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text("".join(json.dumps(r) + "\n" for r in export))
    b.write_text(json.dumps(export[6]) + "\n")
    prep.main([str(a), str(b), str(tmp_path / "out")])
    rows = {
        r["memory_id"]: r for r in map(json.loads, (tmp_path / "out/import.jsonl").read_text().splitlines())
    }
    assert set(rows) == {"northwind", "monday2", "tom"}
    assert (rows["monday2"]["text"], rows["monday2"]["merged_from"]) == (monday, ["monday1"])
    assert [{r["memory_id"] for r in g} for g in judged] == [{"monday1", "monday2"}]
    assert (rows["northwind"]["project"], rows["northwind"]["client"], rows["northwind"]["by"]) == (
        "https://example.test/northwind.git", "northwind", "valor",
    )  # fmt: skip
    assert (rows["tom"]["by"], rows["tom"]["text"], rows["tom"]["merged_from"]) == ("tom", near, ["jamie"])
    assert rows["tom"]["project"] == "unused"
    report = (tmp_path / "out/report.md").read_text()
    for stage in ("| read | 9 |", "| knowledge skipped | 1 |", "| superseded skipped | 1 |",
                  "| session summaries dropped | 1 |", "| no project dropped | 1 |", "| after paraphrase | 3 |"):  # fmt: skip
        assert stage in report
    assert "`lost`" in report and near + " today" in report


def test_prep_drops_what_is_not_worth_recalling_and_lists_it(tmp_path, prep, monkeypatch):
    chatter = {"Continue", "Agent session initialized with bypassPermissions mode enabled"}
    batches = []
    monkeypatch.setattr(prep, "BATCH", 2)
    monkeypatch.setattr(
        prep,
        "worth",
        lambda b: batches.append(len(b)) or [n for n, r in enumerate(b, 1) if r["text"] in chatter],
    )
    monkeypatch.setattr(prep, "judge", lambda group: [])
    fact = "Twenty CRM provides native HTTP/SSE MCP server at POST /mcp endpoint"
    export = [
        rec("go", "Continue"),
        rec("crm", fact, source="agent", agent="valor"),
        rec(
            "init", "Agent session initialized with bypassPermissions mode enabled", source="agent", agent="x"
        ),
    ]
    (tmp_path / "a.jsonl").write_text("".join(json.dumps(r) + "\n" for r in export))
    prep.main([str(tmp_path / "a.jsonl"), str(tmp_path / "out")])
    rows = [
        json.loads(line)["memory_id"] for line in (tmp_path / "out/import.jsonl").read_text().splitlines()
    ]
    assert rows == ["crm"] and batches == [2, 1]
    report = (tmp_path / "out/report.md").read_text()
    assert "| not worth recalling dropped | 2 |" in report and "| valor | 3 | 3 | 2 | 1 |" in report
    assert "## Dropped, not worth recalling (2)" in report
    assert "- (valor, by tom): Continue" in report
    assert "- (valor, by valor): Agent session initialized with bypassPermissions mode enabled" in report
