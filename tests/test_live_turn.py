"""One real `claude -p` turn through the gateway, then a real stop mid-stream.

Live spend: at most $0.03 per run (two Haiku turns under 1,024 and 4,096
output tokens; the stopped call is charged its full output allowance).
Runs only when `VALOR_LIVE=1`, so a plain test run spends nothing.
"""

import asyncio
import os

import pytest

from core import db, runs, tasks
from core.gateway import Gateway
from harnesses import claude_code

pytestmark = [
    pytest.mark.spend(usd=0.03),
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
