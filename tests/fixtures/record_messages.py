"""Record the provider responses `tests/test_gateway_meter.py` replays.

    VALOR_LIVE=1 .venv/bin/python tests/fixtures/record_messages.py

One streamed and one non-streamed Messages call to `claude-haiku-4-5`, each
through the kernel's gateway on the test database under a task, so the
spend is metered and ledgered like any other call. The
recorder supplies the API key from the vault `.env` as its own client
header; the kernel never reads it. Only the response bodies are saved, never
headers (they carry the organization id). `recorded.json` keeps what the
gateway charged for each, with the date, the model, and the price's checked
date.

Live spend: two Haiku calls of at most 32 output tokens, under $0.001.
"""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import aiohttp

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from core import db, ledger, tasks
from core.gateway import Gateway
from core.settings import settings

VAULT_ENV = Path(os.environ.get("VALOR_RECORD_ENV", Path.home() / "Desktop" / "Valor" / ".env"))
BODY = {
    "model": "claude-haiku-4-5",
    "max_tokens": 32,
    "messages": [{"role": "user", "content": "Reply with exactly one word: ready"}],
}


def api_key() -> str:
    for line in VAULT_ENV.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "ANTHROPIC_API_KEY":
            return value.strip().strip("'\"")
    raise SystemExit(f"no ANTHROPIC_API_KEY in {VAULT_ENV}")


async def main() -> None:
    if os.environ.get("VALOR_LIVE") != "1":
        raise SystemExit("spends money: set VALOR_LIVE=1")
    dsn = db.migrate(settings.test_database)
    async with await db.connect(dsn) as conn:
        task = await tasks.start(conn, tasks.Brief(instruction="record fixtures"))
    gateway = Gateway(dsn)
    await gateway.start()
    headers = {"x-api-key": api_key(), "anthropic-version": "2023-06-01"}
    try:
        async with aiohttp.ClientSession() as http:
            for name, stream in (("messages_stream_haiku.sse", True), ("messages_haiku.json", False)):
                base = gateway.issue(task, f"record-{name}")
                async with http.post(
                    base + "/v1/messages", json={**BODY, "stream": stream}, headers=headers
                ) as r:
                    body = await r.read()
                    if r.status != 200:
                        raise SystemExit(f"{name}: {r.status} {body[:300]!r}")
                (HERE / name).write_bytes(body)
        await gateway.drain(task)
    finally:
        await gateway.close()
    async with await db.connect(dsn) as conn:
        rows = await ledger.read(conn, task)
    charged = [r["payload"] for r in rows if r["type"] == "gateway.charged"]
    (HERE / "recorded.json").write_text(
        json.dumps(
            {
                "recorded": datetime.now(UTC).date().isoformat(),
                "request": BODY,
                "test_database_task": task,
                "charges": [
                    {
                        "turn_id": c["turn_id"],
                        "model": c["model"],
                        "usd_micros": c["usd_micros"],
                        "price_checked": c["price_checked"],
                        "usage": c["usage"],
                    }
                    for c in charged
                ],
            },
            indent=2,
        )
        + "\n"
    )
    print(json.dumps([c["usd_micros"] for c in charged]))


if __name__ == "__main__":
    asyncio.run(main())
