"""A chat session's system prompt stays byte-identical between its turns,
so its prompt cache holds, while it still sees every child's state and
instruction each turn: what changed goes in the turn's prompt
(`tasks.frozen`, Tom, 2026-11-16)."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from core import db, ledger, tasks
from core.settings import PRICES, compaction, resolve_model
from harnesses import claude_code
from tests import bridges, scripted
from tests.test_compaction import chat_task, say, turns, wake

pytestmark = pytest.mark.spend(usd=0)


async def child(dsn, parent, instruction) -> str:
    async with await db.connect(dsn) as conn:
        return await tasks.start(conn, tasks.Brief(instruction=instruction, parent_id=parent))


def started(rows) -> list[dict]:
    return [r["payload"] for r in rows if r["type"] == "turn.started"]


def test_the_brief_stays_byte_identical_while_children_move_and_the_prompt_says_how(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    scripted.steer(ws, chat=[{}, {"compact.md": "Keep the children."}, {}])

    async def go():
        task = await chat_task(dsn, ws)
        a = await child(dsn, task, "Fix the export")
        b = await child(dsn, task, "Reword the greeting")
        await say(dsn, task, "Start both.", 1)
        await wake(dsn, task)
        # Between turns: a is charged, b is stopped, c starts.
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, a, "gateway.charged", {"call_id": "c1", "usd_micros": 1_234})
            await tasks.stop(conn, b, reason="test")
        c = await child(dsn, task, "Draft the invoice email")
        await say(dsn, task, "How are they doing?", 2)
        await wake(dsn, task)  # the turn, then the /compact it asked for
        await say(dsn, task, "And now?", 3)
        await wake(dsn, task)
        return (a, b, c), await bridges.rows(dsn, task)

    (a, b, c), rows = asyncio.run(go())
    first, second, compacting, after = started(rows)
    said = [t["prompt"] for t in turns(ws)]
    # The second turn's system prompt is the first's, byte for byte.
    assert second["brief"] == first["brief"] and second["brief_sha256"] == first["brief_sha256"]
    assert "brief_sections" in first and "brief_sections" not in second
    news = second["brief_news"]
    assert news in said[1] and "# Your Brief has changed since it was written" in news
    assert f"{a} (" in news and "metered spending $0.001234" in news
    assert f"{b} (stopped" in news
    assert f"{c} (" in news and ", new:" in news and "  > Draft the invoice email" in news
    # Together they hold every child's current state and instruction.
    whole = second["brief"] + news
    for kid, words in ((a, "Fix the export"), (b, "Reword the greeting"), (c, "Draft the invoice email")):
        assert kid in whole and words in whole
    # The /compact turn keeps the snapshot and gets no news; the turn after it starts afresh.
    assert (
        compacting["compaction"] and compacting["brief"] == first["brief"] and "brief_news" not in compacting
    )
    assert said[2] == "/compact Keep the children."
    assert "brief_sections" in after and "brief_news" not in after and after["brief"] != first["brief"]
    assert f"{b} (stopped" in after["brief"] and "Draft the invoice email" in after["brief"]
    assert "# Your Brief has changed" not in said[3]


def test_a_task_and_a_calls_worker_get_a_fresh_brief_and_no_news(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await chat_task(dsn, ws)
        kid = await child(dsn, task, "Fix the export")
        async with await db.connect(dsn) as conn:
            return [await tasks.dispatch(conn, t, **kw) for t, kw in ((kid, {}), (task, {"fresh": "call"}))]

    for d in asyncio.run(go()):
        assert d["news"] == "" and "brief_sections" not in d


def test_a_chat_session_turns_on_the_chat_seat_and_a_task_or_call_keeps_its_model(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    scripted.steer(ws, chat=[{}])
    models = []

    def turn_for(prompt, resume, b):
        models.append(b.model)
        return scripted.turn_for(prompt, resume, b)

    async def go():
        task = await chat_task(dsn, ws)
        await say(dsn, task, "Hello.", 1)
        gateway = await bridges.gateway(dsn)
        try:
            async with await db.connect(dsn) as conn:
                b = await tasks.brief(conn, task)
            from core import session

            await session.run(gateway, task, turn_for, dsn=dsn, performers=scripted.performers(b))
        finally:
            await gateway.close()
        return b

    stored = asyncio.run(go())
    assert stored.model == "claude-opus-5-5" and models == ["claude-fable-5-2"]
    assert tasks.turn_model(stored) == resolve_model("chat") == "claude-fable-5-2"
    assert tasks.turn_model(tasks.Brief(instruction="a task")) == "claude-opus-5-5"
    # A call's worker runs `_turn_for` on its call session's own Brief: its model is the Brief's.
    assert resolve_model("frontier") == "claude-opus-5-5"


def test_fable_is_priced_and_compacted_like_opus():
    assert compaction("claude-fable-5-2") == compaction("claude-opus-5-5")
    fable = PRICES["claude-fable-5-2"]
    assert (fable.input, fable.output, fable.cache_write, fable.cache_write_1h, fable.cache_read) == (
        8.00, 40.00, 10.00, 16.00, 0.20)  # fmt: skip
    assert resolve_model("reviewer") == "claude-fable-5-2"


def test_news_follows_the_prompt_and_reaches_subagents():
    harness = {"sandbox_profile": "valor-turn"}
    plain = claude_code.workspace_turn("hi", cwd=".", harness=harness)("http://h:1", "BRIEF", "t")
    told = claude_code.workspace_turn("hi", cwd=".", harness=harness)("http://h:1", "BRIEF", "t", news="NEWS")
    assert plain.argv[-1] == "hi" and told.argv[-1] == "hi\n\nNEWS"
    assert "BRIEF" in told.env["VALOR_SUBAGENT_START"] and "NEWS" in told.env["VALOR_SUBAGENT_START"]
    assert "NEWS" not in plain.env["VALOR_SUBAGENT_START"]
    assert plain.argv[plain.argv.index("--append-system-prompt") + 1] == "BRIEF"
    assert told.argv[told.argv.index("--append-system-prompt") + 1] == "BRIEF"


# -- when the Brief starts afresh: `tasks.frozen` on rows ------------------------

NOW = datetime.now(UTC)
KID = {"task_id": "k1", "state": "planning", "tree_spent_usd_micros": 5, "last_activity": "2026-11-16 10:00 UTC",
       "instruction": "Fix the export"}  # fmt: skip
SECTIONS = {
    "persona": "P" * 4000,
    "brief": "# Brief\n\nTask: s",
    "channel": "C" * 4000,
    "children": "# Children",
}


def snapshot_rows(
    ended_ago=timedelta(minutes=5), started_ago=timedelta(minutes=6), sections=SECTIONS, **extra
):
    digests = {k: ledger.digest(v) for k, v in sections.items()}
    kids = {"k1": tasks._child_facts(KID)}
    return [
        {"type": "turn.started", "at": NOW - started_ago, "payload": {
            "turn_id": "t1", "brief": "OLD", "brief_sections": digests, "brief_children": kids, **extra}},
        {"type": "turn.ended", "at": NOW - ended_ago, "payload": {"turn_id": "t1"}},
    ]  # fmt: skip


def frozen(rows, sections=SECTIONS, found=(KID,), session="5e55", compacting=False):
    text = "\n\n".join(sections.values())
    return tasks.frozen(rows, SimpleNamespace(session=session), sections, list(found), text, compacting)


def test_nothing_changed_keeps_the_snapshot_with_no_news():
    assert frozen(snapshot_rows()) == ("OLD", "", {})


def test_a_long_turn_that_ended_minutes_ago_keeps_the_cache_and_an_hour_after_its_end_starts_afresh():
    assert frozen(snapshot_rows(started_ago=timedelta(hours=3)))[0] == "OLD"
    assert frozen(snapshot_rows(ended_ago=timedelta(minutes=61), started_ago=timedelta(hours=2)))[0] != "OLD"


@pytest.mark.parametrize(
    "case", ["new conversation", "no snapshot", "after compaction", "persona", "big news"]
)
def test_the_brief_starts_afresh_when_the_cache_is_cold_or_news_would_cost_more(case):
    rows, sections, session = snapshot_rows(), SECTIONS, "5e55"
    if case == "new conversation":
        session = None
    elif case == "no snapshot":
        rows = []
    elif case == "after compaction":
        rows = rows + [{"type": "turn.started", "at": NOW, "payload": {"turn_id": "t2", "compaction": True}}]
    elif case == "persona":
        sections = {**SECTIONS, "persona": "Q" * 4000}
    else:
        sections = {**SECTIONS, "summary": "# Summary\n\n" + "S" * 3000}
    text, news, shot = frozen(rows, sections, session=session)
    assert text == "\n\n".join(sections.values()) and news == ""
    assert set(shot) == {"brief_sections", "brief_children"}


def test_the_compact_turn_keeps_the_snapshot_even_when_things_changed():
    moved = {**KID, "state": "building"}
    assert frozen(snapshot_rows(), found=(moved,), compacting=True) == ("OLD", "", {})


def test_a_changed_section_goes_in_full_and_a_gone_one_is_named():
    sections = {**SECTIONS, "brief": "# Brief\n\nTask: s\nRoom: the dev room"}
    rows = snapshot_rows(sections={**SECTIONS, "remembered": "# Remembered\n\nold"})
    text, news, shot = frozen(rows, sections)
    assert text == "OLD" and shot == {}
    assert "Room: the dev room" in news and "No longer in your Brief: Remembered." in news
    assert "# Children" not in news and "Your children" not in news


def test_a_childs_spending_or_activity_change_costs_only_its_own_line():
    other = {**KID, "task_id": "k2", "instruction": "Second"}
    rows = snapshot_rows()
    rows[0]["payload"]["brief_children"]["k2"] = tasks._child_facts(other)
    moved = {**KID, "tree_spent_usd_micros": 9, "last_activity": "2026-11-16 10:30 UTC"}
    _, news, _ = frozen(rows, found=(moved, other))
    assert "- k1 (planning, metered spending $0.000009, last activity 2026-11-16 10:30 UTC)" in news
    assert "k2" not in news and "Fix the export" not in news


def test_children_lines_carry_last_activity():
    line = tasks.children_text([KID]).splitlines()[2]
    assert line == "- k1 (planning, metered spending $0.000005, last activity 2026-11-16 10:00 UTC)"
