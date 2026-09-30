"""Driver for spike 03. Runs the gateway in-process and drives three scenarios:

1. clean: a full tool-calling run; reconstruct the tool sequence from the log.
2. revoke: revoke the brief while call 2 is streaming; time the refusal.
3. budget: a brief with a budget too small for the run; see where it stops.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time

import httpx
import uvicorn
from dotenv import dotenv_values

from agent import run_agent
from gateway import AUDIT_PATH, Kernel, build_app

PORT = 8787
BASE = f"http://127.0.0.1:{PORT}"


def load_key() -> str:
    env = dotenv_values(os.path.expanduser("~/src/ai/.env"))
    key = env.get("ANTHROPIC_API_KEY")
    assert key, "ANTHROPIC_API_KEY missing from ~/src/ai/.env"
    return key


def read_audit(brief: str) -> list[dict]:
    with open(AUDIT_PATH) as f:
        return [r for r in map(json.loads, f) if r.get("brief") == brief]


def reconstruct(records: list[dict]) -> list[tuple[str, dict, str]]:
    """Tool call sequence from the gateway log alone: tool_use blocks from each
    response, results from the tool_result blocks of the next request."""
    reqs = {r["call"]: r for r in records if r["event"] == "request"}
    resps = {r["call"]: r for r in records if r["event"] == "response"}
    seq = []
    for call in sorted(resps):
        uses = [b for b in resps[call]["content"] if b.get("type") == "tool_use"]
        nxt = reqs.get(call + 1)
        results = {}
        if nxt:
            last = nxt["body"]["messages"][-1]
            if isinstance(last["content"], list):
                for blk in last["content"]:
                    if blk.get("type") == "tool_result":
                        results[blk["tool_use_id"]] = blk["content"]
        for u in uses:
            seq.append(
                (u["name"], u["input"], results.get(u["id"], "<no result in log>"))
            )
    return seq


async def scenario_clean(http):
    r = await http.post(
        f"{BASE}/kernel/issue", json={"brief_id": "clean", "token_budget": 50_000}
    )
    trace = await run_agent(BASE, r.json()["token"])
    recs = read_audit("clean")
    seq = reconstruct(recs)
    resps = [r for r in recs if r["event"] == "response"]
    reqs = [r for r in recs if r["event"] == "request"]
    print("\n== scenario 1: clean run ==")
    print(
        f"agent: {trace['calls']} calls, {len(trace['tool_calls'])} tool calls, ended {trace['ended']}"
    )
    print(f"gateway log: {len(reqs)} requests, {len(resps)} responses")
    same = seq == trace["tool_calls"]
    print(f"tool sequence reconstructed from gateway log == agent's own trace: {same}")
    for name, inp, out in seq:
        print(f"   {name}({json.dumps(inp)}) -> {out[:40]!r}")
    # provider usage (as the SDK client saw it) vs gateway-counted usage
    gw = [r["usage"] for r in resps]
    diffs = []
    for a, g in zip(trace["usage"], gw):
        for k in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            diffs.append((a.get(k) or 0) - (g.get(k) or 0))
    print(
        f"provider usage (client side) vs gateway-parsed usage: max abs diff = {max(map(abs, diffs))} tokens over {len(diffs)} fields"
    )
    est = [
        (
            r["estimated_input"],
            s["usage"]["input_tokens"]
            + s["usage"].get("cache_read_input_tokens", 0)
            + s["usage"].get("cache_creation_input_tokens", 0),
        )
        for r, s in zip(reqs, resps)
    ]
    print(
        "count_tokens pre-check estimate vs billed input per call: "
        + ", ".join(f"{e}/{a}" for e, a in est)
    )
    print(
        f"count_tokens pre-check latency: median {statistics.median(r['count_tokens_ms'] for r in reqs):.0f} ms"
    )
    print(
        f"end-to-end call latency via gateway: median {statistics.median(r['latency_ms'] for r in resps):.0f} ms"
    )
    total_in = sum(r["usage"]["input_tokens"] for r in resps)
    total_out = sum(r["usage"]["output_tokens"] for r in resps)
    print(
        f"tokens: input {total_in}, output {total_out}; cost about ${total_in/1e6*1 + total_out/1e6*5:.4f}"
    )


async def scenario_revoke(http):
    r = await http.post(
        f"{BASE}/kernel/issue", json={"brief_id": "rev", "token_budget": 50_000}
    )
    token = r.json()["token"]
    agent_task = asyncio.create_task(run_agent(BASE, token))
    # Out-of-band watcher: when call 2 has streamed a few chunks, revoke.
    t_revoke = None
    while t_revoke is None and not agent_task.done():
        st = (await http.get(f"{BASE}/kernel/status/rev")).json()
        if st.get("calls", 0) >= 2 and st.get("chunks", 0) >= 3:
            t_revoke = time.perf_counter()
            await http.post(f"{BASE}/kernel/revoke/rev")
        else:
            await asyncio.sleep(0.002)
    trace = await agent_task
    recs = read_audit("rev")
    cut = next((r for r in recs if r["event"] == "stream_cut"), None)
    refused = [r for r in recs if r["event"] == "refused"]
    print("\n== scenario 2: revoke mid-stream (during call 2) ==")
    print(
        f"agent: {trace['calls']} calls attempted, {len(trace['tool_calls'])} tool calls completed, ended {trace['ended']}"
    )
    if cut:
        print(
            f"in-flight stream cut {cut['since_revoke_ms']:.1f} ms after revoke, after {cut['chunks_before_cut']} chunks"
        )
    else:
        print("in-flight stream was NOT cut (finished before the next chunk arrived)")
    if trace["error_at"] and t_revoke:
        print(
            f"agent observed the failure {(trace['error_at'] - t_revoke)*1000:.1f} ms after revoke"
        )
    print(
        f"later calls refused at the gateway: {len(refused)} "
        + (
            f"(first refusal {refused[0]['since_revoke_ms']:.1f} ms after revoke)"
            if refused and "since_revoke_ms" in refused[0]
            else ""
        )
    )
    # Prove the agent cannot get through even if it retries.
    t0 = time.perf_counter()
    trace2 = await run_agent(BASE, token, max_calls=3)
    print(
        f"retry with the same token: {trace2['calls']} attempt(s), ended {trace2['ended']}, refused in {(time.perf_counter()-t0)*1000:.1f} ms"
    )
    responses = [r for r in recs if r["event"] == "response"]
    print(
        f"gateway still recorded usage for the cut call: {[r['usage'] for r in responses if r['cut']]}"
    )


async def scenario_budget(http):
    r = await http.post(
        f"{BASE}/kernel/issue", json={"brief_id": "bud", "token_budget": 3_300}
    )
    trace = await run_agent(BASE, r.json()["token"])
    recs = read_audit("bud")
    refused = [r for r in recs if r["event"] == "refused"]
    resps = [r for r in recs if r["event"] == "response"]
    print("\n== scenario 3: budget too small (3,300 tokens) ==")
    print(
        f"agent: {trace['calls']} calls attempted, {len(trace['tool_calls'])} tool calls, ended {trace['ended']}"
    )
    print(
        f"gateway: {len(resps)} forwarded, then refused: {[(x['reason'], x.get('used'), x.get('estimated_input'), x.get('max_tokens')) for x in refused]}"
    )
    print(
        f"tokens billed before refusal: {sum(r['billed_total'] for r in resps)} of budget 3300"
    )


async def main():
    if os.path.exists(AUDIT_PATH):
        os.remove(AUDIT_PATH)
    kernel = Kernel(load_key())
    config = uvicorn.Config(
        build_app(kernel), host="127.0.0.1", port=PORT, log_level="warning"
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    async with httpx.AsyncClient(timeout=60) as http:
        for _ in range(50):
            try:
                await http.get(f"{BASE}/kernel/status/none")
                break
            except httpx.HTTPError:
                await asyncio.sleep(0.05)
        await scenario_clean(http)
        await scenario_revoke(http)
        await scenario_budget(http)
    server.should_exit = True
    await server_task
    print(f"\naudit log: {AUDIT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
