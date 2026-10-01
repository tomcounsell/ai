"""Money: prices, the per-call reservation, and conservation.

A budget is integer micro-dollars and nothing else. Tokens are what the
provider bills on; `cost` is the one place the two units meet.

Every model call reserves its worst case before it is forwarded and is
charged what the provider reported when it closes. Both are ledger rows, and
remaining money is always derived from the ledger under an advisory lock on
the task, so two calls racing on one task can never spend the same money.
"""

import json
from math import ceil

from core import ledger, tasks

# US dollars per million tokens, from the provider's public pricing page.
# `cache_write` is the five-minute write (1.25x input); the one-hour write is
# 2x input and derived in `prices`. A model absent here is refused: an
# unpriced call cannot be metered.
PRICES_USD_PER_MTOK = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_write": 5.00, "cache_read": 0.20},
    "claude-opus-5": {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-opus-4-8": {"input": 5.00, "output": 25.00, "cache_write": 6.25, "cache_read": 0.50},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.20},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10},
}

# Bytes per token for the input estimate. An underestimate spends money
# nobody reserved, so it leans high: English and JSON run above 3.
BYTES_PER_TOKEN = 3


class BudgetRefused(RuntimeError):
    pass


def prices(model: str) -> dict[str, int] | None:
    """Micro-dollars per million tokens for a model id, matching a dated id
    (`claude-haiku-4-5-20251001`) to its undated entry. The longest matching
    entry wins, so `claude-opus-5-5` never takes `claude-opus-5`'s price."""
    names = [n for n in PRICES_USD_PER_MTOK if model == n or model.startswith(n + "-")]
    if not names:
        return None
    table = PRICES_USD_PER_MTOK[max(names, key=len)]
    table = {**table, "cache_write_1h": 2 * table["input"]}
    return {k: round(v * 1_000_000) for k, v in table.items()}


def cost(usage: dict, price: dict[str, int]) -> int:
    """What the provider bills, rounded up per field, so the ledger never
    records less than the invoice. A cache write counts as a five-minute
    write only where the usage's `cache_creation` split says so; every other
    cache write is charged at the one-hour rate."""
    writes = int(usage.get("cache_creation_input_tokens") or 0)
    short = min(writes, int((usage.get("cache_creation") or {}).get("ephemeral_5m_input_tokens") or 0))
    tokens = {
        "input": int(usage.get("input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "cache_write": short,
        "cache_write_1h": writes - short,
    }
    return sum(ceil(n * price[p] / 1_000_000) for p, n in tokens.items())


def estimate_input(body: dict) -> int:
    return ceil(len(json.dumps(body, separators=(",", ":")).encode()) / BYTES_PER_TOKEN)


def worst_case(input_tokens: int, max_tokens: int, price: dict[str, int]) -> int:
    """Input at the most expensive input rate plus every output token the
    caller allowed."""
    return ceil(input_tokens * price["cache_write_1h"] / 1_000_000) + ceil(
        max_tokens * price["output"] / 1_000_000
    )


REMAINING_SQL = """
SELECT
  (SELECT (body->>'budget_usd_micros')::bigint FROM documents
    WHERE kind = 'task' AND id = %(t)s)
  - COALESCE((SELECT sum((payload->>'usd_micros')::bigint) FROM events
    WHERE task_id = %(t)s AND type = 'gateway.charged'), 0)
  - COALESCE((SELECT sum((r.payload->>'usd_micros')::bigint) FROM events r
    WHERE r.task_id = %(t)s AND r.type = 'gateway.reserved'
      AND NOT EXISTS (SELECT 1 FROM events c
        WHERE c.task_id = %(t)s AND c.type = 'gateway.charged'
          AND c.payload->>'call_id' = r.payload->>'call_id')), 0)
"""


async def remaining(conn, task_id: str) -> int:
    row = await (await conn.execute(REMAINING_SQL, {"t": task_id})).fetchone()
    if row[0] is None:
        raise KeyError(task_id)
    return int(row[0])


async def reserve(conn, task_id: str, call: dict) -> str:
    """Reserve `call['usd_micros']` for one model call, or refuse. The
    refusal is a ledger row too. `call` carries `call_id`, `turn_id`,
    `model`, `usd_micros`, `estimated_input`, and `max_tokens`."""
    async with conn.transaction():
        await ledger.lock(conn, f"task:{task_id}")
        reason = None
        if await tasks.is_stopped(conn, task_id):
            reason = "task stopped"
        else:
            left = await remaining(conn, task_id)
            if call["usd_micros"] > left:
                reason = f"reservation {call['usd_micros']} exceeds remaining {left}"
        if reason is None:
            await ledger.append(conn, task_id, "gateway.reserved", call)
            return call["call_id"]
        await ledger.append(conn, task_id, "gateway.refused", {**call, "reason": reason})
    raise BudgetRefused(reason)


async def charge(conn, task_id: str, call_id: str, usd_micros: int, detail: dict) -> None:
    """Close a reservation with what the call actually cost."""
    async with conn.transaction():
        await ledger.append(
            conn,
            task_id,
            "gateway.charged",
            {"call_id": call_id, "usd_micros": usd_micros, **detail},
        )
