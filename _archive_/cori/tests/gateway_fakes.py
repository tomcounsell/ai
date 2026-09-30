"""A fake provider the gateway can be pointed at, and a fake tree.

Plan 04 names no file for these; tasks 5 through 9 all need the same two,
so they live here, the way the tree build put its fakes in
`tests/tree_fakes.py`. Nothing here reaches the network: the gateway is
built with an httpx client whose transport is the fake ASGI app.
"""

import asyncio
import json

import httpx
from starlette.requests import Request

from schemas.budget import Budget


def sse(events: list[dict]) -> bytes:
    out = b""
    for event in events:
        out += f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
    return out


def request_for(call: dict, headers: dict) -> Request:
    """A Starlette request without a server in front of it, so a test can
    drive `gateway.app.handle` directly and hold the response's own byte
    iterator. Closing that iterator is exactly what a reader that goes away
    does."""
    payload = json.dumps(call).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/messages",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(scope, receive)


def message_start(model: str, **usage) -> dict:
    counts = {
        "input_tokens": 0,
        "output_tokens": 1,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    counts.update(usage)
    return {
        "type": "message_start",
        "message": {
            "id": "msg_fake",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": counts,
        },
    }


def text_stream(
    model: str, text: str, *, output_tokens: int = 5, **usage
) -> list[dict]:
    """A complete stream: one text block, one `message_delta`."""
    return [
        message_start(model, **usage),
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        },
        {"type": "message_stop"},
    ]


class FakeUpstream:
    """An ASGI app answering the two provider endpoints the gateway calls.

    `count` is what `/v1/messages/count_tokens` reports, `status` the status
    it answers with, and `events` the SSE the messages endpoint streams.
    Every request is recorded, so a test can assert the gateway never called
    upstream at all.
    """

    def __init__(self, *, count: int = 100, events: list[dict] | None = None):
        self.count = count
        self.count_status = 200
        self.count_calls: list[dict] = []
        self.message_calls: list[dict] = []
        self.events = events or []
        self.status = 200
        self.error_body = b'{"type":"error","error":{"message":"upstream"}}'
        self.chunk_delay = 0.0
        # Awaited with the request body when the messages endpoint is
        # entered, so a test can assert what the database already holds.
        self.on_message = None
        # Stop sending after this many chunks, the way a provider that goes
        # silent looks to a reader with a read timeout.
        self.stall_after = None
        self.active = 0
        self.most_concurrent = 0

    def transport(self) -> httpx.ASGITransport:
        return httpx.ASGITransport(app=self)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport(), base_url="http://fake")

    async def __call__(self, scope, receive, send):
        body = b""
        while True:
            event = await receive()
            body += event.get("body", b"")
            if not event.get("more_body"):
                break
        payload = json.loads(body or b"{}")
        if scope["path"].endswith("/count_tokens"):
            await self._count_tokens(payload, send)
            return
        await self._messages(payload, send)

    async def _count_tokens(self, payload, send):
        self.count_calls.append(payload)
        if self.count_status != 200:
            await _respond(
                send, self.count_status, b'{"type":"error"}', "application/json"
            )
            return
        await _respond(
            send,
            200,
            json.dumps({"input_tokens": self.count}).encode(),
            "application/json",
        )

    async def _messages(self, payload, send):
        self.message_calls.append(payload)
        self.active += 1
        self.most_concurrent = max(self.most_concurrent, self.active)
        try:
            if self.on_message is not None:
                await self.on_message(payload)
            await self._stream(payload, send)
        finally:
            self.active -= 1

    async def _stream(self, payload, send):
        if self.status != 200:
            await _respond(send, self.status, self.error_body, "application/json")
            return
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        for index, event in enumerate(self.events):
            if self.stall_after is not None and index >= self.stall_after:
                await asyncio.sleep(30)
            if self.chunk_delay:
                await asyncio.sleep(self.chunk_delay)
            await send(
                {"type": "http.response.body", "body": sse([event]), "more_body": True}
            )
        await send({"type": "http.response.body", "body": b"", "more_body": False})


async def _respond(send, status: int, body: bytes, content_type: str):
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", content_type.encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


class FakeTree:
    """The three tree functions the gateway calls, with a ledger per brief.

    Every method raises `KeyError` for an id it holds no objective for, so a
    test proves the gateway never asked about a turn id rather than assuming
    it (plan 04, task 6).
    """

    def __init__(self):
        self.allocations: dict[str, int] = {}
        self.generations: dict[str, int] = {}
        self.consumed: list[tuple[str, Budget, bool]] = []
        self.overruns: list[tuple[str, int]] = []
        self.checks: list[str] = []

    def add(self, brief_id: str, usd_micros: int, generation: int = 1) -> None:
        self.allocations[brief_id] = usd_micros
        self.generations[brief_id] = generation

    async def remaining(self, conn, id: str) -> Budget:
        return Budget(usd_micros=self.allocations[id])

    async def consume(self, conn, brief_id, amount: Budget, *, incurred: bool = False):
        left = self.allocations[brief_id]
        self.consumed.append((brief_id, amount, incurred))
        if amount.usd_micros > left:
            self.overruns.append((brief_id, amount.usd_micros - left))
            self.allocations[brief_id] = 0
            return Budget(usd_micros=0)
        self.allocations[brief_id] = left - amount.usd_micros
        return Budget(usd_micros=self.allocations[brief_id])

    async def check_generation(self, conn, token) -> None:
        self.checks.append(token.brief_id)
        if self.generations[token.brief_id] != token.generation:
            from kernel.tree import StaleGeneration

            raise StaleGeneration(token.brief_id)
