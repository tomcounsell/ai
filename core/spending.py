"""Metered spending: prices, and the rows that open and charge each call.

Spending is integer micro-dollars. Tokens are what the provider bills on;
`cost` is the one place the two units meet.

Every model call opens with a `gateway.opened` row before it is forwarded
and closes with a `gateway.charged` row carrying what the provider
reported. A task's metered spending is the sum of its charges
(`tasks.spending`), shown in its status and never a reason to refuse: an
open checks only that the task is not stopped.
"""

import json
from contextvars import ContextVar
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from math import ceil

from core import ledger, tasks
from core.settings import JUDGEMENT_PRICES, PRICES, settings

# The session lock the code opening a call holds while it waits on the
# call (`run:<task>` inside a run), recorded on `gateway.opened` as
# `holder`. After a kernel restart, a call whose holder lock is free is
# charged at its estimate; a call with none is left for `tasks.audit`.
HOLDER: ContextVar[str | None] = ContextVar("holder", default=None)


def prices(model: str) -> dict | None:
    """Micro-dollars per million tokens for a model id, plus the day the
    price was checked (`checked`, ISO date). A dated id
    (`claude-haiku-4-5-20251001`) matches its undated entry; the longest
    matching entry wins, so `claude-opus-5-5` never takes `claude-opus-5`'s
    price."""
    names = [n for n in PRICES if model == n or model.startswith(n + "-")]
    if not names:
        return None
    p = PRICES[max(names, key=len)]
    per_mtok = {
        "input": p.input,
        "output": p.output,
        "cache_write": p.cache_write,
        "cache_write_1h": p.cache_write_1h,
        "cache_read": p.cache_read,
    }
    return {**{k: round(v * 1_000_000) for k, v in per_mtok.items()}, "checked": p.checked.isoformat()}


def judgement_price(model: str) -> dict | None:
    """Micro-dollars per million tokens for a judgement leg's pinned model
    (exact id only: a judgement model is pinned, never matched by prefix),
    with the day it was checked."""
    p = JUDGEMENT_PRICES.get(model)
    if p is None:
        return None
    return {
        "input": round(p.input * 1_000_000),
        "output": round(p.output * 1_000_000),
        "checked": p.checked.isoformat(),
    }


def judgement_worst_case(input_tokens: int, max_tokens: int, price: dict) -> int:
    """A judgement call's worst case: its estimated input and every output
    token it may produce, rounded up, never under one micro-dollar. It is
    the charge when the provider may have billed and reported nothing."""
    return max(
        1, ceil(input_tokens * price["input"] / 1_000_000) + ceil(max_tokens * price["output"] / 1_000_000)
    )


def usd_micros(reported) -> int | None:
    """A provider's reported dollar cost as whole micro-dollars, rounded up.
    Read through `Decimal(str(x))`, so a float's binary rounding never lowers
    a charge. None when it is not a finite, non-negative number."""
    if isinstance(reported, bool) or not isinstance(reported, int | float | str):
        return None
    try:
        value = Decimal(str(reported))
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0:
        return None
    return int((value * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


def judgement_cost(usage: dict, price: dict) -> int:
    """What a judgement call costs from its usage: tokens at the pinned
    price, rounded up, or the provider's reported cost when that is more,
    so the ledger never records less than the invoice."""
    tokens = ceil(int(usage["input_tokens"]) * price["input"] / 1_000_000) + ceil(
        int(usage.get("output_tokens") or 0) * price["output"] / 1_000_000
    )
    reported = usd_micros(usage.get("reported_usd")) if usage.get("reported_usd") is not None else None
    return max(tokens, reported or 0)


def cost(usage: dict, price: dict) -> int:
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
    return ceil(len(json.dumps(body, separators=(",", ":")).encode()) / settings.bytes_per_token)


def worst_case(input_tokens: int, max_tokens: int, price: dict) -> int:
    """Input at the most expensive input rate plus every output token the
    caller allowed: the charge for a call whose provider reports no usage."""
    return ceil(input_tokens * price["cache_write_1h"] / 1_000_000) + ceil(
        max_tokens * price["output"] / 1_000_000
    )


async def open_call(conn, task_id: str, call: dict) -> str:
    """Open one model call with a `gateway.opened` row before it is
    forwarded. A stopped task's call is refused instead, with a
    `gateway.refused` row (reason `stopped`) and `tasks.TaskStopped`;
    nothing else refuses a call. `call` carries `call_id`, `turn_id`,
    `model`, `route`, `estimated_input`, `max_tokens`, and
    `estimate_usd_micros` (the worst case, charged only when the provider
    reports no usage), and `holder` (default `HOLDER`)."""
    call = {**call, "holder": call.get("holder") or HOLDER.get()}
    async with conn.transaction():
        # Under the task's lock, so a stop and an open never interleave.
        await ledger.lock(conn, f"task:{task_id}")
        await tasks.brief(conn, task_id)  # KeyError for an unknown task
        if not await tasks.is_stopped(conn, task_id):
            await ledger.append(conn, task_id, "gateway.opened", call)
            return call["call_id"]
        await ledger.append(conn, task_id, "gateway.refused", {**call, "reason": "stopped"})
    raise tasks.TaskStopped(task_id)


async def charge(conn, task_id: str, call_id: str, usd_micros: int, detail: dict) -> None:
    """Close an opened call with what it actually cost."""
    async with conn.transaction():
        await ledger.append(
            conn,
            task_id,
            "gateway.charged",
            {"call_id": call_id, "usd_micros": usd_micros, **detail},
        )
