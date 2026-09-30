"""End to end on Postgres: start a task with a budget, run one `claude -p`
turn through the gateway, record effects (one `act` held for Tom), stop a
second task mid-turn, and print the ledger.

    .venv/bin/python scripts/kernel_smoke.py

Live spend: two Haiku turns, a few cents at most. Each task's committed
budget caps it.
"""

import asyncio
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import broker, db, ledger, runs, tasks
from core.gateway import Gateway
from harnesses import claude_code
from tools.workspace import OutboxAppend, WorkspaceWrite


def cli(*args: str) -> str:
    out = subprocess.run(
        [sys.executable, "-m", "core", *args], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def usd(micros: int) -> str:
    return f"${micros / 1_000_000:.4f}"


async def main() -> None:
    dsn = db.migrate()
    workspace = Path(tempfile.mkdtemp(prefix="valor-task-"))
    outbox = workspace / "outbox.jsonl"
    broker.register(WorkspaceWrite(workspace))
    broker.register(OutboxAppend(outbox))
    gateway = Gateway(dsn)
    print(f"gateway listening at {await gateway.start()}")

    async with await db.connect(dsn) as conn:
        # -- start -------------------------------------------------------------
        brief = tasks.Brief(
            instruction="Say you are ready, then send Tom that reply.",
            budget_usd_micros=100_000,
            max_effect_class="act",
        )
        task = await tasks.start(conn, brief)
        print(
            f"\n== start: task {task}, budget {usd(brief.budget_usd_micros)}, "
            f"ceiling {brief.max_effect_class}, governance_grant {brief.governance_grant}"
        )

        # -- one claude -p turn through the gateway ---------------------------
        ended = await runs.run_turn(
            gateway,
            task,
            claude_code.turn("Reply with exactly one word: ready", cwd=str(workspace)),
        )
        result = ended["result"]
        print(f"\n== turn: {ended['outcome']}, reply {result.get('text')!r}")
        print(
            f"   gateway metered {usd(ended['metered_usd_micros'])}; "
            f"claude reported ${result.get('harness_reported_usd')}"
        )

        # -- effects -----------------------------------------------------------
        text = (result.get("text") or "").strip() or "(the turn produced no reply)"
        wrote = await broker.request(
            conn, task, broker.Action("workspace_write", "reply.txt", {"text": text})
        )
        print(f"\n== effect (propose): workspace_write -> {wrote.kind} {wrote.result}")
        send = broker.Action("outbox_send", "tom", {"text": text})
        held = await broker.request(conn, task, send)
        print(f"== effect (act): outbox_send -> {held.kind}, effect {held.effect_id}")
        try:
            await broker.release(conn, held.effect_id)
            print("   RELEASED WITHOUT APPROVAL: broken")
        except broker.NotApproved as refused:
            print(f"   release before approval refused: {refused}")
        print(f"   outbox lines before approval: {_lines(outbox)}")
        guard = await broker.request(
            conn,
            task,
            broker.Action(
                "workspace_write", "hooks/new_gate.py", {"text": "# a new gate"}, adds_governance=True
            ),
        )
        print(f"== effect adding governance with no grant -> {guard.kind}: {guard.error}")
        print(f"   pending for Tom: {cli('pending')}")
        approval = cli("approve", held.effect_id, "--note", "yes, send it")
        print(f"   Tom approved from the CLI: approval {approval}")
        sent = await broker.release(conn, held.effect_id)
        again = await broker.release(conn, held.effect_id)
        print(f"   release -> {sent.kind}; second release -> {again.kind} (same effect {again.effect_id})")
        print(f"   outbox lines after approval: {_lines(outbox)}")

        # -- stop mid-turn -----------------------------------------------------
        long = tasks.Brief(instruction="Count to four hundred in words.", budget_usd_micros=100_000)
        stopped_task = await tasks.start(conn, long)
        print(f"\n== start: task {stopped_task}, budget {usd(long.budget_usd_micros)}")
        turn = asyncio.create_task(
            runs.run_turn(
                gateway,
                stopped_task,
                claude_code.turn(
                    "Count from one to four hundred in English words, one number per line.",
                    cwd=str(workspace),
                    max_output_tokens=4096,
                ),
            )
        )
        await _streaming(conn, stopped_task)
        await asyncio.sleep(1.5)
        t0 = time.monotonic()
        print(
            f"   first model call in flight; stopping from the CLI: {cli('stop', stopped_task, '--reason', 'Tom pressed stop')}"
        )
        ended = await turn
        print(
            f"== stop: turn {ended['outcome']} {time.monotonic() - t0:.2f}s after the stop "
            f"(returncode {ended['returncode']}), metered {usd(ended['metered_usd_micros'])}"
        )
        late = await broker.request(
            conn, stopped_task, broker.Action("workspace_write", "late.txt", {"text": "x"})
        )
        print(f"   effect requested after stop -> {late.kind}: {late.error}")

        # -- the ledger ---------------------------------------------------------
        for task_id in (task, stopped_task):
            state = await tasks.status(conn, task_id)
            print(f"\n== ledger for task {task_id} ({state['state']})")
            print(ledger.render(await ledger.read(conn, task_id)))
            print(
                f"   committed {usd(state['committed_usd_micros'])}, charged "
                f"{usd(state['charged_usd_micros'])}, remaining {usd(state['remaining_usd_micros'])}"
            )
            print(f"   audit: {tasks.audit(state) or 'consistent'}")
    await gateway.close()


async def _streaming(conn, task_id: str) -> None:
    """Wait until the turn's first model call is under way."""
    while True:
        row = await (
            await conn.execute(
                "SELECT 1 FROM events WHERE task_id = %s AND type = 'gateway.reserved'", (task_id,)
            )
        ).fetchone()
        if row:
            return
        await asyncio.sleep(0.1)


def _lines(path: Path) -> int:
    return len(path.read_text().splitlines()) if path.exists() else 0


if __name__ == "__main__":
    asyncio.run(main())
