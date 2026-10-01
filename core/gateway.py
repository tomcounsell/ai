"""The model gateway: the one path a turn's model calls take.

A local HTTP proxy speaking the Anthropic Messages wire format. A turn is
pointed at it through `ANTHROPIC_BASE_URL` with a per-turn token in the
path, so every call the harness makes, including its own side calls, passes
through here. For each `POST /v1/messages` the gateway:

1. prices the model and reserves the call's worst case against the task's
   remaining budget (refused, with a ledger row, if it does not fit or the
   task is stopped);
2. forwards the request upstream with the harness's own credentials and
   streams the response back unchanged, reading the usage the provider
   reports as it passes;
3. charges what the provider reported when the call closes.

A revoke cuts every in-flight call of the task at once. A call that
ends without its final usage (cut, or the client died) is charged its input
as reported plus every output token it was allowed, so the ledger never
records less than the invoice. Other paths (token counting, model lists)
cost nothing and pass through unmetered.
"""

import asyncio
import json
import secrets
from dataclasses import dataclass

import aiohttp
from aiohttp import web

from core import budget, db
from core.ledger import new_id
from core.settings import settings

# Hop-by-hop and length headers are recomputed on each side.
DROP_REQUEST = {"host", "content-length", "accept-encoding", "connection", "transfer-encoding"}
DROP_RESPONSE = {"content-length", "content-encoding", "transfer-encoding", "connection"}


@dataclass
class Grant:
    task_id: str
    turn_id: str


class Meter:
    """Reads provider usage out of a Messages response, streamed or not."""

    def __init__(self):
        self.usage: dict = {}
        self.started = False
        self.complete = False
        self._buffer = b""

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            if line.startswith(b"data:"):
                try:
                    self._event(json.loads(line[5:]))
                except ValueError:
                    continue

    def whole(self, body: bytes) -> None:
        try:
            message = json.loads(body)
        except ValueError:
            return
        if isinstance(message, dict) and "usage" in message:
            self.usage = dict(message["usage"])
            self.started = self.complete = True

    def _event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "message_start":
            self.usage = dict(event.get("message", {}).get("usage") or {})
            self.started = True
        elif kind == "message_delta":
            for key, value in (event.get("usage") or {}).items():
                if value is not None:
                    self.usage[key] = value
        elif kind == "message_stop":
            self.complete = True


class Gateway:
    def __init__(self, dsn: str | None = None, upstream: str | None = None):
        self.dsn = dsn or settings.dsn()
        self.upstream = (upstream or settings.upstream).rstrip("/")
        self.grants: dict[str, Grant] = {}
        self.revoked: set[str] = set()
        self.calls: dict[str, set[asyncio.Task]] = {}
        self.upstream_calls: dict[str, set[asyncio.Task]] = {}
        self.url: str | None = None
        self._runner: web.AppRunner | None = None
        self._session: aiohttp.ClientSession | None = None

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> str:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_route("*", "/t/{token}/{tail:.*}", self.handle)
        self._runner = web.AppRunner(app, handler_cancellation=False)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, port)
        await site.start()
        bound = site._server.sockets[0].getsockname()[1]
        self.url = f"http://{host}:{bound}"
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=300),
            auto_decompress=False,
        )
        return self.url

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
        if self._runner is not None:
            await self._runner.cleanup()

    # -- grants ---------------------------------------------------------------

    def issue(self, task_id: str, turn_id: str) -> str:
        """A base URL for one turn. The token lives only in this process."""
        token = secrets.token_urlsafe(24)
        self.grants[token] = Grant(task_id, turn_id)
        return f"{self.url}/t/{token}"

    def retire(self, task_id: str) -> None:
        """Forget every token of the task. Later calls on them are refused."""
        for token in [t for t, g in self.grants.items() if g.task_id == task_id]:
            del self.grants[token]

    def revoke(self, task_id: str) -> None:
        """Retire the task's tokens and cut its in-flight calls now, whether
        they are streaming or still waiting on the provider."""
        self.revoked.add(task_id)
        self.retire(task_id)
        for call in self.upstream_calls.get(task_id, ()):
            call.cancel()

    async def drain(self, task_id: str) -> None:
        """Wait until every call of the task has been charged."""
        while self.calls.get(task_id):
            await asyncio.wait(list(self.calls[task_id]))

    # -- the call path --------------------------------------------------------

    async def handle(self, request: web.Request) -> web.StreamResponse:
        grant = self.grants.get(request.match_info["token"])
        if grant is None or grant.task_id in self.revoked:
            return _error(403, "permission_error", "turn token revoked or unknown")
        tail = request.match_info["tail"]
        path = "/" + tail + (f"?{request.query_string}" if request.query_string else "")
        body = await request.read()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_REQUEST}
        headers["accept-encoding"] = "identity"
        if request.method == "POST" and tail.rstrip("/") == "v1/messages":
            call = asyncio.create_task(self._metered(request, grant, path, headers, body))
            self.calls.setdefault(grant.task_id, set()).add(call)
            call.add_done_callback(self.calls[grant.task_id].discard)
            return await asyncio.shield(call)
        return await self._forward(request, path, headers, body)

    async def _forward(self, request, path, headers, body) -> web.StreamResponse:
        try:
            async with self._session.request(
                request.method, self.upstream + path, headers=headers, data=body
            ) as up:
                return web.Response(status=up.status, body=await up.read(), headers=_response_headers(up))
        except aiohttp.ClientError:
            return _error(502, "api_error", "upstream failed")

    async def _metered(self, request, grant: Grant, path, headers, body) -> web.StreamResponse:
        try:
            message = json.loads(body)
            model = message["model"]
            max_tokens = int(message.get("max_tokens") or 0)
        except ValueError, KeyError, TypeError:
            return _error(400, "invalid_request_error", "gateway could not read the request")
        price = budget.prices(model)
        if price is None:
            return _error(400, "invalid_request_error", f"model {model} has no price")
        estimated = budget.estimate_input(message)
        call_id = new_id()
        call = {
            "call_id": call_id,
            "turn_id": grant.turn_id,
            "model": model,
            "estimated_input": estimated,
            "max_tokens": max_tokens,
            "usd_micros": budget.worst_case(estimated, max_tokens, price),
        }
        async with await db.connect(self.dsn) as conn:
            try:
                await budget.reserve(conn, grant.task_id, call)
            except budget.BudgetRefused as refused:
                return _error(400, "invalid_request_error", f"budget refused: {refused}")

        meter = Meter()
        status = None
        cut = False
        unsent = False
        response = None
        upstream = self.upstream_calls.setdefault(grant.task_id, set())
        upstream.add(asyncio.current_task())
        try:
            async with self._session.post(self.upstream + path, headers=headers, data=body) as up:
                status = up.status
                response = web.StreamResponse(status=up.status, headers=_response_headers(up))
                await response.prepare(request)
                streaming = "event-stream" in up.headers.get("content-type", "")
                if not streaming:
                    raw = await up.read()
                    meter.whole(raw)
                    await response.write(raw)
                else:
                    async for chunk in up.content.iter_any():
                        if grant.task_id in self.revoked:
                            cut = True
                            break
                        meter.feed(chunk)
                        await response.write(chunk)
                if cut:
                    request.transport.close()
                else:
                    await response.write_eof()
        except aiohttp.ClientConnectorError, aiohttp.ConnectionTimeoutError:
            unsent = True  # never reached the provider: nothing billed
        except ConnectionError, aiohttp.ClientError:
            cut = True
        except asyncio.CancelledError:
            # Revoked: the stop owns this cancellation, and the charge still lands.
            asyncio.current_task().uncancel()
            cut = True
            if request.transport is not None:
                request.transport.close()
        finally:
            upstream.discard(asyncio.current_task())
            await self._close(grant, call, price, meter, status, cut, unsent)
        return response if response is not None else _error(502, "api_error", "upstream failed")

    async def _close(self, grant, call, price, meter: Meter, status, cut, unsent) -> None:
        if unsent:
            charged = 0
        elif meter.complete:
            charged = budget.cost(meter.usage, price)
        elif meter.started:
            # Input as reported, every allowed output token: never under the invoice.
            charged = budget.cost({**meter.usage, "output_tokens": call["max_tokens"]}, price)
        elif status is not None and status >= 400:
            charged = 0  # the provider refused before generating: nothing billed
        else:
            charged = call["usd_micros"]
        detail = {
            "turn_id": grant.turn_id,
            "model": call["model"],
            "price_checked": price["checked"],
            "status": status,
            "cut": cut,
            "unsent": unsent,
            "complete": meter.complete,
            "usage": meter.usage,
        }
        async with await db.connect(self.dsn) as conn:
            await budget.charge(conn, grant.task_id, call["call_id"], charged, detail)


def _response_headers(up: aiohttp.ClientResponse) -> dict[str, str]:
    return {k: v for k, v in up.headers.items() if k.lower() not in DROP_RESPONSE}


def _error(status: int, kind: str, message: str) -> web.Response:
    return web.json_response({"type": "error", "error": {"type": kind, "message": message}}, status=status)
