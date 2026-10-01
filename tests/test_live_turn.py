"""Real `claude -p` turns through the gateway: one metered turn and a stop
mid-stream, and one turn whose reply becomes a `propose` effect done at
once and an `act` effect held until Tom approves it from the command line.

Live spend: at most $0.15 per run, the three tasks' committed budgets of
$0.05 each, which the gateway never lets them pass (three Haiku turns
under 1,024 and 4,096 output tokens typically cost about $0.03; the stopped
call is charged its full output allowance).
Runs only when `VALOR_LIVE=1`, so a plain test run spends nothing.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import broker, db, runs, tasks
from core.gateway import Gateway
from harnesses import claude_code
from tests.conftest import TEST_DB
from tests.performers import OutboxAppend, WorkspaceWrite

pytestmark = [
    pytest.mark.spend(usd=0.15),
    pytest.mark.skipif(os.environ.get("VALOR_LIVE") != "1", reason="live spend needs VALOR_LIVE=1"),
]


def test_one_turn_is_metered_and_a_stop_mid_stream_is_lossless(dsn, tmp_path):
    async def go():
        gateway = Gateway(dsn)
        await gateway.start()
        async with await db.connect(dsn) as conn:
            done_task = await tasks.start(conn, tasks.Brief(instruction="t", budget_usd_micros=50_000))
            stop_task = await tasks.start(conn, tasks.Brief(instruction="t", budget_usd_micros=50_000))
        done = await runs.run_turn(
            gateway,
            done_task,
            claude_code.turn("Reply with exactly one word: ready", cwd=str(tmp_path)),
            dsn=dsn,
        )
        turn = asyncio.create_task(
            runs.run_turn(
                gateway,
                stop_task,
                claude_code.turn(
                    "Count from one to four hundred in English words, one per line.",
                    cwd=str(tmp_path),
                    max_output_tokens=4096,
                ),
                dsn=dsn,
            )
        )
        async with await db.connect(dsn) as conn:
            while not await (
                await conn.execute(
                    "SELECT 1 FROM events WHERE task_id = %s AND type = 'gateway.reserved'", (stop_task,)
                )
            ).fetchone():
                await asyncio.sleep(0.1)
            await asyncio.sleep(1.5)
            await tasks.stop(conn, stop_task, reason="test")
        stopped = await turn
        await gateway.close()
        async with await db.connect(dsn) as conn:
            return done, stopped, await tasks.status(conn, done_task), await tasks.status(conn, stop_task)

    done, stopped, done_state, stop_state = asyncio.run(go())
    assert done["outcome"] == "done" and "ready" in done["result"]["text"].lower()
    assert 0 < done["metered_usd_micros"] <= 50_000
    assert stopped["outcome"] == "stopped"
    assert stop_state["charged_usd_micros"] > 0  # the cut call is charged, not forgotten
    assert tasks.audit(done_state) == [] and tasks.audit(stop_state) == []


def test_a_live_reply_is_written_at_once_and_sent_only_after_tom_approves_from_the_cli(dsn, tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    broker.register(WorkspaceWrite(tmp_path))
    broker.register(OutboxAppend(outbox))

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "core", *args],
            cwd=Path(__file__).resolve().parent.parent,
            env={**os.environ, "VALOR_DB": TEST_DB},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    async def go():
        gateway = Gateway(dsn)
        await gateway.start()
        async with await db.connect(dsn) as conn:
            task = await tasks.start(
                conn, tasks.Brief(instruction="t", budget_usd_micros=50_000, max_effect_class="act")
            )
            ended = await runs.run_turn(
                gateway,
                task,
                claude_code.turn("Reply with exactly one word: ready", cwd=str(tmp_path)),
                dsn=dsn,
            )
            text = (ended["result"].get("text") or "").strip()
            wrote = await broker.request(
                conn, task, broker.Action("workspace_write", "reply.txt", {"text": text})
            )
            held = await broker.request(conn, task, broker.Action("outbox_send", "tom", {"text": text}))
            with pytest.raises(broker.NotApproved):
                await broker.release(conn, held.effect_id)
            lines_before = outbox.exists()
            pending = cli("pending")
            cli("approve", held.effect_id, "--note", "yes, send it")
            sent = await broker.release(conn, held.effect_id)
            again = await broker.release(conn, held.effect_id)
            state = await tasks.status(conn, task)
        await gateway.close()
        return ended, text, wrote, held, lines_before, pending, sent, again, state

    ended, text, wrote, held, lines_before, pending, sent, again, state = asyncio.run(go())
    assert ended["outcome"] == "done" and "ready" in text.lower()
    assert wrote.kind == "done" and (tmp_path / "reply.txt").read_text() == text
    assert held.kind == "pending" and not lines_before and held.effect_id in pending
    assert sent.kind == "done" and again.effect_id == held.effect_id
    assert len(outbox.read_text().splitlines()) == 1
    assert state["attention_counts"]["approval"]["total"] == 1
    assert tasks.audit(state) == []
