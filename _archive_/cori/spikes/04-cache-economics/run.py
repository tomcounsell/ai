"""Spike 04 driver.

Part A: minimum cacheable prefix sweep on Haiku 4.5.
Part B: 20 consecutive turns, volatility order vs naive importance order,
        only the tail changing each turn, digest changing once and roll-up
        every five turns. Numbers straight from usage fields.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import anthropic
from dotenv import dotenv_values

sys.path.insert(0, os.path.dirname(__file__))
from render import NAIVE_ORDER, VOLATILITY_ORDER, digest_of, render  # noqa: E402
from slices import prose  # noqa: E402

MODEL = "claude-haiku-4-5-20251001"
PRICE_IN, PRICE_WRITE, PRICE_READ, PRICE_OUT = 1.00, 1.25, 0.10, 5.00  # $/MTok
SIZES = {"persona": 4300, "digest": 800, "rollup": 1500, "thread": 400}
TURNS = 20


def load_client() -> anthropic.AsyncAnthropic:
    env = dotenv_values(os.path.expanduser("~/src/ai/.env"))
    return anthropic.AsyncAnthropic(api_key=env["ANTHROPIC_API_KEY"], max_retries=2)


def cost(u: dict) -> float:
    return (
        u.get("input_tokens", 0) * PRICE_IN
        + u.get("cache_creation_input_tokens", 0) * PRICE_WRITE
        + u.get("cache_read_input_tokens", 0) * PRICE_READ
        + u.get("output_tokens", 0) * PRICE_OUT
    ) / 1e6


async def call(client, system, user="Reply with the single word OK."):
    t0 = time.perf_counter()
    r = await client.messages.create(
        model=MODEL,
        max_tokens=5,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    u = r.usage.model_dump()
    u = {k: v for k, v in u.items() if isinstance(v, int)}
    u["latency_ms"] = (time.perf_counter() - t0) * 1000
    return u


def state_at(turn: int, base: dict[str, str]) -> dict[str, str]:
    st = dict(base)
    if turn >= 10:  # a belief was added to the operator record at turn 10
        st["digest"] = (
            base["digest"] + "\n- prefers PRs under 400 lines (direct, 2026-09-19)"
        )
    epoch = turn // 5  # a report lands every five turns and changes the roll-up
    st["rollup"] = base["rollup"] + f"\nreports landed: {epoch}"
    st["thread"] = base["thread"] + f"\nsummary revision {turn // 8}"
    recent = [
        f"turn {t}: {prose(f'turn-{t}', 30)}" for t in range(max(0, turn - 4), turn + 1)
    ]
    st["recent"] = "\n".join(recent)
    st["inbox"] = (
        f"pending reports: {turn % 3}; open questions: {(turn * 7) % 5}; escalations: none"
    )
    return st


async def part_b(client, base, order, label):
    rows = []
    for turn in range(TURNS):
        st = state_at(turn, base)
        system = render(st, order)
        assert digest_of(render(st, order)) == digest_of(system)  # deterministic
        rows.append(await call(client, system))
    tot = {
        k: sum(r[k] for r in rows)
        for k in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
        )
    }
    all_in = (
        tot["input_tokens"]
        + tot["cache_creation_input_tokens"]
        + tot["cache_read_input_tokens"]
    )
    c = sum(cost(r) for r in rows)
    uncached = all_in * PRICE_IN / 1e6 + tot["output_tokens"] * PRICE_OUT / 1e6
    print(f"\n== B: {label} ==")
    print(
        f"{'turn':>4} {'uncached in':>11} {'cache write':>11} {'cache read':>10} {'latency':>8}"
    )
    for i, r in enumerate(rows):
        print(
            f"{i:>4} {r['input_tokens']:>11} {r['cache_creation_input_tokens']:>11} {r['cache_read_input_tokens']:>10} {r['latency_ms']:>7.0f}ms"
        )
    print(
        f"total input tokens {all_in}: {tot['cache_read_input_tokens']/all_in:.1%} billed as cache reads, "
        f"{tot['cache_creation_input_tokens']/all_in:.1%} as cache writes, {tot['input_tokens']/all_in:.1%} uncached"
    )
    print(f"cost ${c:.4f} vs ${uncached:.4f} with no caching ({c/uncached:.0%})")
    return {
        "label": label,
        "cost": c,
        "uncached_cost": uncached,
        **tot,
        "all_in": all_in,
    }


async def main():
    client = load_client()

    async def count(text: str) -> int:
        r = await client.messages.count_tokens(
            model=MODEL, messages=[{"role": "user", "content": text}]
        )
        return r.input_tokens

    # Build sized slices (count_tokens is free).
    base = {}
    for name, tokens in SIZES.items():
        n = int(tokens * 0.8)
        text = prose(name, n)
        t = await count(text)
        while t < tokens:
            n = int(n * max(1.05, tokens / max(t, 1)))
            text = prose(name, n)
            t = await count(text)
        base[name] = text
        print(f"slice {name}: {t} tokens")

    await part_a(client)
    results = []
    results.append(
        await part_b(
            client,
            base,
            VOLATILITY_ORDER,
            "volatility order (persona, digest, rollup | thread, recent, inbox)",
        )
    )
    results.append(
        await part_b(
            client,
            base,
            NAIVE_ORDER,
            "naive importance order (inbox, recent, rollup, thread, persona, digest)",
        )
    )
    print("\nSUMMARY")
    for r in results:
        print(
            f"  {r['label']}: reads {r['cache_read_input_tokens']/r['all_in']:.1%}, cost ${r['cost']:.4f} ({r['cost']/r['uncached_cost']:.0%} of uncached)"
        )
    print(f"  total spend this run: ${sum(r['cost'] for r in results):.4f} plus sweep")


async def part_a(client):
    async def count(text):
        r = await client.messages.count_tokens(
            model=MODEL, messages=[{"role": "user", "content": text}]
        )
        return r.input_tokens

    async def sized_async(seed, target):
        n = int(target * 0.8)
        text = prose(seed, n)
        t = await count(text)
        while t < target:
            n = int(n * max(1.05, target / max(t, 1)))
            text = prose(seed, n)
            t = await count(text)
        return text

    print("== A: minimum cacheable prefix on Haiku 4.5 (2 calls per size) ==")
    print(f"{'prefix tokens':>14} {'call1 created':>14} {'call2 read':>11}  verdict")
    for target in (1024, 2048, 3072, 3900, 4100, 5000):
        text = await sized_async(f"sweep-{target}", target)
        system = [
            {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}
        ]
        u1 = await call(client, system)
        u2 = await call(client, system)
        total = (
            u1["input_tokens"]
            + u1["cache_creation_input_tokens"]
            + u1["cache_read_input_tokens"]
        )
        ok = u2["cache_read_input_tokens"] > 0
        print(
            f"{total:>14} {u1['cache_creation_input_tokens']:>14} {u2['cache_read_input_tokens']:>11}  {'cached' if ok else 'NOT cached'}"
        )


if __name__ == "__main__":
    asyncio.run(main())
