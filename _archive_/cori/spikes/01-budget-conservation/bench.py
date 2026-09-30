"""Raw concurrency test: N workers hammer one parent with random allocations.

Reports whether the ledger ever over-allocates, throughput, and p95 latency,
for each lock strategy. The "none" strategy is included on purpose to show what
the lock is buying: it should over-allocate."""

import asyncio
import random
import statistics
import time
import uuid

import psycopg

import kernel

WORKERS = 16
ATTEMPTS_PER_WORKER = 60
PARENT_BUDGET = 20_000
MAX_AMOUNT = 100


async def worker(role, parent, lock, latencies, counts):
    async with await psycopg.AsyncConnection.connect(kernel.dsn_for(role)) as conn:
        for _ in range(ATTEMPTS_PER_WORKER):
            amt = random.randint(1, MAX_AMOUNT)
            child = f"c-{uuid.uuid4().hex[:10]}"
            t0 = time.perf_counter()
            try:
                await kernel.delegate(conn, parent, child, amt, lock=lock)
                counts["ok"] += 1
            except kernel.BudgetExceeded:
                counts["refused"] += 1
            except psycopg.Error as e:
                counts["error"] += 1
                counts.setdefault("errors", set()).add(str(e).splitlines()[0][:80])
            latencies.append(time.perf_counter() - t0)


async def run(lock, role, workers=WORKERS):
    parent = f"p-{lock}-{uuid.uuid4().hex[:6]}"
    async with await psycopg.AsyncConnection.connect(kernel.dsn_for(role)) as conn:
        await kernel.create_root(conn, parent, PARENT_BUDGET)
    latencies: list[float] = []
    counts = {"ok": 0, "refused": 0, "error": 0}
    t0 = time.perf_counter()
    await asyncio.gather(
        *(worker(role, parent, lock, latencies, counts) for _ in range(workers))
    )
    wall = time.perf_counter() - t0
    async with await psycopg.AsyncConnection.connect(kernel.dsn_for(role)) as conn:
        cur = await conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM tree.budget_events WHERE node_id=%s",
            (parent,),
        )
        allocated = int((await cur.fetchone())[0])
        overs = await kernel.audit(conn)
    lat_ms = sorted(x * 1000 for x in latencies)
    p50 = statistics.median(lat_ms)
    p95 = lat_ms[int(len(lat_ms) * 0.95) - 1]
    n = len(latencies)
    print(
        f"lock={lock:9s} role={role:15s} workers={workers} attempts={n} "
        f"ok={counts['ok']} refused={counts['refused']} errors={counts['error']}"
    )
    print(
        f"  allocated={allocated} budget={PARENT_BUDGET} "
        f"over_by={max(0, allocated - PARENT_BUDGET)} audit_violations={len(overs)}"
    )
    print(
        f"  throughput={n / wall:.0f} delegate/s  p50={p50:.2f}ms  p95={p95:.2f}ms  wall={wall:.2f}s"
    )
    if counts.get("errors"):
        for e in sorted(counts["errors"]):
            print(f"  error: {e}")
    return allocated <= PARENT_BUDGET and not overs


async def main():
    random.seed(1)
    results = {}
    results["advisory-1"] = await run("advisory", "cori_kernel", workers=1)
    results["advisory"] = await run("advisory", "cori_kernel")
    results["row"] = await run("row", "cori_kernel_fu")
    results["none"] = await run("none", "cori_kernel")
    print("conserved:", results)


asyncio.run(main())
