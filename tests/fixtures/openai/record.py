"""Record the OpenAI Responses traffic `tests/test_gateway_openai.py` replays.

    VALOR_LIVE=1 .venv/bin/python tests/fixtures/openai/record.py

Each call goes through the kernel's gateway on the test database under a
task, built with no OpenAI credential, so the recorder's client sends the
vault's `OPENAI_API_KEY` as its own `authorization` header and the gateway
forwards it, metered, recorded `credential: "turn"`. The kernel never reads
the key. Only response bodies are saved, never headers (they carry the
organization and the request id), and every response, item, and call id is
replaced by a fixed one. `recorded.json` keeps what the gateway charged for
each, with the usage, tier, and the price's checked date.

Live spend: about a dozen GPT-6.1 calls of a few hundred tokens and one web
search, a few cents.
"""

import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import aiohttp

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent.parent))

from core import db, ledger, tasks
from core.gateway import Gateway
from core.settings import settings

VAULT_ENV = Path(os.environ.get("VALOR_RECORD_ENV", Path.home() / "Desktop" / "Valor" / ".env"))
MODEL = "gpt-6.1-sol"
# About 1,400 tokens of fixed text, so a second call can read it from the cache.
LONG = " ".join(
    f"Rule {i}: the kernel records every call it forwards and charges what was billed." for i in range(110)
)
TOOL = {
    "type": "function",
    "name": "get_weather",
    "description": "The weather in a city.",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
}
CASES = [
    (
        "stream_text.sse",
        {"input": "Reply with exactly one word: ready", "max_output_tokens": 200, "stream": True},
    ),
    (
        "stream_function.sse",
        {
            "input": "What is the weather in Paris? Use the tool.",
            "tools": [TOOL],
            "reasoning": {"effort": "low"},
            "max_output_tokens": 800,
            "stream": True,
        },
    ),
    (
        "cache_first.sse",
        {
            "instructions": LONG,
            "input": "Reply with one word: first",
            "prompt_cache_key": "valor-record",
            "max_output_tokens": 200,
            "stream": True,
        },
    ),
    (
        "cache_second.sse",
        {
            "instructions": LONG,
            "input": "Reply with one word: second",
            "prompt_cache_key": "valor-record",
            "max_output_tokens": 200,
            "stream": True,
        },
    ),
    (
        "incomplete.sse",
        {"input": "Write a long poem about the sea.", "max_output_tokens": 16, "stream": True},
    ),
    ("whole.json", {"input": "Reply with exactly one word: ready", "max_output_tokens": 200}),
    (
        "web_search.sse",
        {
            "input": "Search the web: what is the capital of France? Answer in one word.",
            "tools": [{"type": "web_search"}],
            "tool_choice": "required",
            "max_tool_calls": 1,
            "max_output_tokens": 1000,
            "stream": True,
        },
    ),
    (
        "background_stream.sse",
        {
            "input": "Reply with exactly one word: ready",
            "background": True,
            "store": True,
            "max_output_tokens": 200,
            "stream": True,
        },
    ),
    (
        "background_queued.json",
        {
            "input": "Reply with exactly one word: ready",
            "background": True,
            "store": True,
            "max_output_tokens": 200,
        },
    ),
    ("error_400.json", {"input": "hello", "max_output_tokens": 1}),
]
ID = re.compile(r'"((?:resp|msg|rs|ws|fc|call|fs|ctc)_[A-Za-z0-9_-]+)"')


def api_key() -> str:
    for line in VAULT_ENV.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip().removeprefix("export ").strip() == "OPENAI_API_KEY":
            return value.strip().strip("'\"")
    raise SystemExit(f"no OPENAI_API_KEY in {VAULT_ENV}")


def scrub(body: bytes, key: str) -> bytes:
    """Every id replaced by a fixed one per kind and order; the key, if a
    body ever held it, refused."""
    if key.encode() in body or key[-8:].encode() in body:
        raise SystemExit("a response body held the key; nothing written")
    text, names = body.decode(), {}

    def fixed(m):
        real = m.group(1)
        if real not in names:
            kind = real.split("_", 1)[0]
            names[real] = f"{kind}_rec{sum(1 for n in names.values() if n.startswith(kind + '_')):03d}"
        return f'"{names[real]}"'

    return ID.sub(fixed, text).encode()


async def main() -> None:
    if os.environ.get("VALOR_LIVE") != "1":
        raise SystemExit("spends money: set VALOR_LIVE=1")
    only = set(sys.argv[1:])
    key = api_key()
    dsn = db.migrate(settings.test_database)
    async with await db.connect(dsn) as conn:
        task = await tasks.start(conn, tasks.Brief(instruction="record OpenAI fixtures"))
    gateway = Gateway(dsn)
    await gateway.start()
    headers = {"authorization": f"Bearer {key}"}
    try:
        async with aiohttp.ClientSession() as http:
            base = gateway.issue(task, "record-models")
            async with http.get(f"{base}/openai/v1/models/{MODEL}", headers=headers) as r:
                print(f"v1/models/{MODEL}: {r.status}")
                if r.status != 200:
                    raise SystemExit("the model id is not in v1/models")
            for name, body in CASES:
                if only and name not in only:
                    continue
                base = gateway.issue(task, f"record-{name}")
                async with http.post(
                    base + "/openai/v1/responses", json={"model": MODEL, **body}, headers=headers
                ) as r:
                    raw = await r.read()
                    expected = 400 if name.startswith("error") else 200
                    print(f"{name}: {r.status}")
                    if r.status != expected:
                        raise SystemExit(f"{name}: {r.status} {scrub(raw, key)[:400]!r}")
                (HERE / name).write_bytes(scrub(raw, key))
        await gateway.drain(task)
    finally:
        await gateway.close()
    async with await db.connect(dsn) as conn:
        rows = await ledger.read(conn, task)
    charged = [r["payload"] for r in rows if r["type"] == "gateway.charged"]
    record = HERE / "recorded.json"
    previous = json.loads(record.read_text())["charges"] if record.exists() and only else []
    keep = [c for c in previous if c["turn_id"].removeprefix("record-") not in only]
    record.write_text(
        json.dumps(
            {
                "recorded": datetime.now(UTC).date().isoformat(),
                "model": MODEL,
                "charges": keep
                + [
                    {
                        "turn_id": c["turn_id"],
                        "usd_micros": c["usd_micros"],
                        "price_checked": c["price_checked"],
                        "tier": c["tier"],
                        "tool_calls": c["tool_calls"],
                        "usage": c["usage"],
                    }
                    for c in charged
                ],
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps({c["turn_id"]: c["usd_micros"] for c in charged}))


if __name__ == "__main__":
    asyncio.run(main())
