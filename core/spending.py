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
import re
from contextvars import ContextVar
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from math import ceil

from core import ledger, tasks
from core.settings import (
    JUDGEMENT_PRICES,
    OPENAI_LOOPING_TOOLS,
    OPENAI_PRICES,
    OPENAI_TIER_ALIASES,
    OPENAI_TOKEN_TOOLS,
    OPENAI_TOOL_FEES,
    PRICES,
    settings,
)

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
    return {
        **{k: round(v * 1_000_000) for k, v in per_mtok.items()},
        "web_search": round(p.web_search_per_1k * 1_000),  # micro-dollars per search
        "checked": p.checked.isoformat(),
    }


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
    return max(1, _per_mtok(input_tokens * price["input"]) + _per_mtok(max_tokens * price["output"]))


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
    tokens = _per_mtok(int(usage["input_tokens"]) * price["input"]) + _per_mtok(
        int(usage.get("output_tokens") or 0) * price["output"]
    )
    reported = usd_micros(usage.get("reported_usd")) if usage.get("reported_usd") is not None else None
    return max(tokens, reported or 0)


def cost(usage: dict, price: dict) -> int:
    """What the provider bills, rounded up per field, so the ledger never
    records less than the invoice. A cache write counts as a five-minute
    write only where the usage's `cache_creation` split says so; every other
    cache write is charged at the one-hour rate. Server-side web searches
    are charged per search."""
    writes = int(usage.get("cache_creation_input_tokens") or 0)
    short = min(writes, int((usage.get("cache_creation") or {}).get("ephemeral_5m_input_tokens") or 0))
    tokens = {
        "input": int(usage.get("input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "cache_write": short,
        "cache_write_1h": writes - short,
    }
    searches = int((usage.get("server_tool_use") or {}).get("web_search_requests") or 0)
    return sum(_per_mtok(n * price[p]) for p, n in tokens.items()) + searches * price.get("web_search", 0)


def estimate_input(body: dict) -> int:
    return ceil(len(json.dumps(body, separators=(",", ":")).encode()) / settings.bytes_per_token)


def worst_case(input_tokens: int, max_tokens: int, price: dict) -> int:
    """Input at the most expensive input rate plus every output token the
    caller allowed: the charge for a call whose provider reports no usage."""
    return _per_mtok(input_tokens * price["cache_write_1h"]) + _per_mtok(max_tokens * price["output"])


# -- the OpenAI route ------------------------------------------------------------


def _per_mtok(units: int) -> int:
    """Micro-dollars for tokens times a per-million rate, rounded up, in
    integers: exact at any size, where a float would round a large worst
    case down."""
    return -(-int(units) // 1_000_000)


def _micros(rates) -> dict:
    return {k: round(getattr(rates, k) * 1_000_000) for k in ("input", "cached", "cache_write", "output")}


# An OpenAI snapshot date at the end of a model id.
OPENAI_DATED = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def openai_prices(model: str) -> dict | None:
    """Micro-dollars per million tokens for an OpenAI model id, per tier
    (`base` up to the long-context threshold, `long` above it), with the
    threshold, context window, maximum output, and checked date. Only the
    exact id or the id plus a `-YYYY-MM-DD` snapshot date matches, since
    OpenAI ships pricier variants under the base name (`-pro`); None when
    unpriced."""
    name = OPENAI_DATED.sub("", model)
    if name not in OPENAI_PRICES:
        return None
    p = OPENAI_PRICES[name]
    return {
        "tiers": {t: {"base": _micros(b), "long": _micros(lg)} for t, (b, lg) in p.tiers.items()},
        "long_above": p.long_context_above,
        "context_window": p.context_window,
        "max_output": p.max_output,
        "checked": p.checked.isoformat(),
    }


def openai_tier(price: dict, tier: str | None) -> str | None:
    """The table's name for a tier the API names, or None when unpriced."""
    tier = OPENAI_TIER_ALIASES.get(tier, tier)
    return tier if tier in price["tiers"] else None


def _highest_tier(price: dict) -> str:
    return max(price["tiers"], key=lambda t: price["tiers"][t]["long"]["output"])


def tool_fee_line(tool_type: str) -> str | None:
    """The `OPENAI_TOOL_FEES` line a tool type is billed by, if any."""
    for name in OPENAI_TOOL_FEES:
        if tool_type == name or tool_type.startswith(name + "_"):
            return name
    return None


def is_fee_line(name: str) -> bool:
    return name in OPENAI_TOOL_FEES


def tool_fee(name: str) -> int:
    """Micro-dollars per call of a fee line."""
    return round(OPENAI_TOOL_FEES[name].usd_per_1k * 1_000)


def fee_item(item_type: str) -> str | None:
    """The fee line an output item (`web_search_call`) is a call of."""
    for name, fee in OPENAI_TOOL_FEES.items():
        if item_type == fee.item:
            return name
    return None


def openai_unpriced(body: dict) -> str | None:
    """What in a Responses body the table cannot price, named, or None:
    a tool with neither a fee line nor a token-only type, or a stored
    prompt reference. A tier the entry lacks is not one: the charge prices
    it at the highest tier, marked `tier_unpriced`."""
    if body.get("prompt") is not None:
        return "a stored prompt reference"
    for tool in body.get("tools") or []:
        kind = tool.get("type") if isinstance(tool, dict) else None
        if not isinstance(kind, str):
            return "a tool with no type"
        if kind in OPENAI_TOKEN_TOOLS or tool_fee_line(kind):
            continue
        if kind == "shell" and (tool.get("environment") or {}).get("type") == "local":
            continue
        return f"the {kind} tool" + (" with a hosted environment" if kind == "shell" else "")
    return None


def _referenced(node) -> bool:
    """Whether input content is brought in by id or URL rather than carried
    in the body: a file by URL or id, an image by URL (not `data:`) or id,
    or an item reference."""
    if isinstance(node, list):
        return any(_referenced(n) for n in node)
    if not isinstance(node, dict):
        return False
    kind = node.get("type")
    if kind == "item_reference":
        return True
    if kind == "input_file" and (node.get("file_url") or node.get("file_id")):
        return True
    if kind == "input_image":
        url = node.get("image_url")
        if node.get("file_id") or (isinstance(url, str) and not url.startswith("data:")):
            return True
    return any(_referenced(v) for v in node.values() if isinstance(v, list | dict))


def openai_estimate(body: dict, price: dict) -> dict:
    """The worst case of a Responses call before it is sent.

    Input is the body's bytes at `bytes_per_token`; the context window when
    the body brings in content it does not carry (`previous_response_id`,
    `conversation`, a file or image by id or URL, an item reference),
    marked `referenced`; and `max_tool_calls + 1` windows when a hosted tool
    that samples the model more than once is set with `max_tool_calls`.
    Without that bound the input past one window is not counted, and
    `bounded` is false. Priced at the highest input or cache write rate
    of any tier or length, output at `max_output_tokens` (or the model's
    maximum) at the highest output rate, plus `max_tool_calls` times the
    highest fee among the body's fee-bearing tools."""
    window = price["context_window"]
    estimated = estimate_input(body)
    referenced = bool(
        body.get("previous_response_id") or body.get("conversation") or _referenced(body.get("input"))
    )
    if referenced:
        estimated = max(estimated, window)
    kinds = [t.get("type", "") for t in body.get("tools") or [] if isinstance(t, dict)]
    looping = any(k in OPENAI_LOOPING_TOOLS or tool_fee_line(k) in OPENAI_LOOPING_TOOLS for k in kinds)
    max_calls = body.get("max_tool_calls")
    # The protocol takes no negative count; one sent anyway bounds nothing below 0.
    max_calls = max(0, max_calls) if isinstance(max_calls, int) and not isinstance(max_calls, bool) else None
    if looping and max_calls is not None:
        estimated = max(estimated, (max_calls + 1) * window)
    max_output = body.get("max_output_tokens")
    max_output = int(max_output) if isinstance(max_output, int) and max_output > 0 else price["max_output"]
    every = [r for t in price["tiers"].values() for r in (t["base"], t["long"])]
    tokens = _per_mtok(estimated * max(max(r["input"], r["cache_write"]) for r in every)) + _per_mtok(
        max_output * max(r["output"] for r in every)
    )
    fees = [tool_fee(f) for f in (tool_fee_line(k) for k in kinds) if f]
    fee_cap = (max_calls or 0) * max(fees, default=0)
    return {
        "estimated_input": estimated,
        "max_tokens": max_output,
        "tokens_usd_micros": tokens,
        "fee_cap_usd_micros": fee_cap,
        "estimate_usd_micros": tokens + fee_cap,
        "referenced": referenced,
        "bounded": not looping or max_calls is not None,
    }


def openai_cost(usage: dict, tier: str | None, tool_calls: dict, price: dict) -> tuple[int, bool]:
    """What OpenAI bills for a call from its reported usage, at the rates
    of the tier the response reports (the highest tier when the table lacks
    it, returned as `tier_unpriced`), rounded up per field: cache writes at
    the write rate, cached input at the cached rate, the rest of the input
    at the input rate, and output (reasoning included) at the output rate,
    all at the long-context rates when the input is above the threshold;
    plus each fee-bearing tool's calls times its fee."""
    name = openai_tier(price, tier)
    unpriced = name is None
    if unpriced:
        name = _highest_tier(price)
    total = int(usage.get("input_tokens") or 0)
    details = usage.get("input_tokens_details") or {}
    cached = min(total, int(details.get("cached_tokens") or 0))
    written = min(total - cached, int(details.get("cache_write_tokens") or 0))
    rates = price["tiers"][name]["long" if total > price["long_above"] else "base"]
    tokens = {
        "input": total - cached - written,
        "cached": cached,
        "cache_write": written,
        "output": int(usage.get("output_tokens") or 0),
    }
    charged = sum(_per_mtok(n * rates[k]) for k, n in tokens.items())
    charged += sum(n * tool_fee(f) for f, n in tool_calls.items())
    return charged, unpriced


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


async def turn_spent(conn, task_id: str, turn_id: str) -> int:
    """Every charge on one turn's calls, summed. Summed as numeric, as the
    ledger's JSON holds it: a worst case past 2^63 micro-dollars (a turn
    can ask for any `max_tool_calls`) sums exactly."""
    row = await (
        await conn.execute(
            "SELECT COALESCE(sum((payload->>'usd_micros')::numeric), 0) FROM events "
            "WHERE task_id = %s AND type = 'gateway.charged' "
            "AND payload->>'turn_id' = %s",
            (task_id, turn_id),
        )
    ).fetchone()
    return int(row[0])


async def charge(conn, task_id: str, call_id: str, usd_micros: int, detail: dict) -> None:
    """Close an opened call with what it actually cost."""
    async with conn.transaction():
        await ledger.append(
            conn,
            task_id,
            "gateway.charged",
            {"call_id": call_id, "usd_micros": usd_micros, **detail},
        )
