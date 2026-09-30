"""The one route, `POST /v1/messages`. Plan 04, the call path.

Every model call in the system arrives here, so this module is the place a
call can be refused, metered, recorded, and cut. The order is fixed: read
the token, take the brief's lock, walk the refusal ladder cheapest first,
make the request durable, forward with the stream forced on, and close the
call in one transaction whoever ended it.

The gateway is the enforcement point, never the policy: what a Brief may
spend is the tree's ledger, what model it may call is its token's, and
whether it may run at all is its generation and the revoked set. Nothing
here decides any of those.
"""

import asyncio
import json

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from gateway import budget as gwbudget
from gateway import log as gwlog
from gateway import sse
from gateway.core import Gateway, GatewayError
from schemas.brief import BriefToken
from schemas.budget import Budget

# The bytes a revoked stream ends with, lifted from spike 03. A client
# reading SSE sees an error event rather than a truncated stream.
CUT_EVENT = (
    b"event: error\n"
    b'data: {"type":"error","error":{"type":"permission_error",'
    b'"message":"brief revoked"}}\n\n'
)

REFUSAL_MESSAGE = {
    "unknown_token": "unknown token",
    "revoked": "brief revoked",
    "model": "token is not for this model",
    "stale_generation": "brief generation is stale",
    "budget": "budget exhausted",
}


def error_body(kind: str, message: str) -> dict:
    return {"type": "error", "error": {"type": kind, "message": message}}


def read_key(headers) -> str | None:
    key = headers.get("x-api-key")
    if key:
        return key
    authorization = headers.get("authorization") or ""
    if authorization.lower().startswith("bearer "):
        return authorization[7:]
    return None


def build_app(gateway: Gateway) -> FastAPI:
    app = FastAPI(title="cori gateway")

    @app.post("/v1/messages")
    async def messages(request: Request):
        return await handle(gateway, request)

    return app


async def handle(gw: Gateway, request: Request):
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return JSONResponse(
            error_body("invalid_request_error", "body is not JSON"), status_code=400
        )
    if not isinstance(body, dict):
        return JSONResponse(
            error_body("invalid_request_error", "body is not an object"),
            status_code=400,
        )

    plaintext = read_key(request.headers)
    async with await gw.connect() as conn:
        token = None if plaintext is None else await gw.token(conn, plaintext)
        if token is None:
            # A probe against the gateway is worth seeing, so an unknown
            # token is still a row, with no brief, generation, or space.
            await gwlog.insert_log(conn, event="refused", reason="unknown_token")
            await conn.commit()
            return JSONResponse(
                error_body("authentication_error", REFUSAL_MESSAGE["unknown_token"]),
                status_code=401,
            )

    # Calls are serialized per brief, so one call's pre-check and consume are
    # ordered before the next call's pre-check.
    lock = gw.lock(token.brief_id)
    await lock.acquire()
    # The lock is released in every path, exactly once. Until the runner
    # exists and has taken the call, this frame holds it; from the moment
    # `start()` succeeds the runner's closing path owns it, and `release()`
    # is idempotent so the two never collide.
    runner = None
    runner_holds = False
    try:
        async with await gw.connect() as conn:
            await _warm(gw, conn, token)
            refusal = await _ladder(gw, conn, token=token, body=body)
            if refusal is not None:
                await conn.commit()
                reason, _ = refusal
                return JSONResponse(
                    error_body("permission_error", REFUSAL_MESSAGE[reason]),
                    status_code=403,
                )
            call = await _open_call(gw, conn, token=token, body=body)
            await conn.commit()

        runner = _Call(
            gw,
            token=token,
            body=body,
            call=call,
            lock=lock,
            headers=dict(request.headers),
        )
        if not await runner.start():
            # The provider refused or could not be reached: relay what it
            # said, with its status, and consume nothing.
            return Response(
                runner.error_bytes or b"",
                status_code=runner.upstream_status or 502,
                media_type="application/json",
            )
        runner_holds = True
        if body.get("stream"):
            return StreamingResponse(_Body(runner), media_type="text/event-stream")
        return await runner.assembled()
    finally:
        if not runner_holds:
            # `start()` handles two httpx errors itself. Any other end,
            # a cancellation from a client that went away during the
            # upstream connect included, lands here.
            if runner is not None:
                await runner.abandon()
            elif lock.locked():
                lock.release()


async def _warm(gw: Gateway, conn, token) -> None:
    """The call counter continues from the log's maximum for the id, so
    numbering survives a restart, and a turn's spend is read once from its
    own rows."""
    state = gw.state(token.brief_id)
    if state.warmed:
        return
    state.call = await gwlog.last_call(conn, token.brief_id)
    if token.is_turn:
        state.turn_spent = await gwlog.spent_usd_micros(conn, token.brief_id)
    state.warmed = True


async def _remaining(gw: Gateway, conn, token) -> int:
    """What is left to spend: the turn's cap less what its own rows record,
    or the tree's ledger for a Brief."""
    if token.is_turn:
        return max(
            0, (token.cap_usd_micros or 0) - (gw.state(token.brief_id).turn_spent or 0)
        )
    return (await gw.tree.remaining(conn, token.brief_id)).usd_micros


async def _ladder(gw: Gateway, conn, *, token, body) -> tuple[str, int | None] | None:
    """The refusal ladder, cheapest first. Returns the reason and the
    estimate that was refused, or `None` to let the call through."""
    state = gw.state(token.brief_id)
    # Whether the counting endpoint answered is a fact about this call, not
    # about the Brief: a call that could not be counted falls back, and the
    # next call re-anchors again before it refuses anything (Properties P1,
    # "refused after an exact count or a recorded counting failure").
    state.count_failed = False

    if token.brief_id in gw.revoked:
        return await _refuse(gw, conn, token=token, reason="revoked")
    if body.get("model") != token.model_ref:
        return await _refuse(gw, conn, token=token, reason="model")
    if not token.is_turn:
        from kernel.tree import StaleGeneration

        try:
            await gw.tree.check_generation(
                conn, BriefToken(brief_id=token.brief_id, generation=token.generation)
            )
        except StaleGeneration:
            return await _refuse(gw, conn, token=token, reason="stale_generation")

    prices = gw.prices(token.model_ref)
    max_tokens = int(body.get("max_tokens") or 0)
    estimate, exact = await gw.estimate_input(conn, token=token, body=body, state=state)
    remaining = await _remaining(gw, conn, token)
    if gwbudget.reserve(estimate, max_tokens, prices).usd_micros > remaining:
        if not exact and not state.count_failed:
            # A Brief is never refused by an estimate's margin while the
            # counting endpoint answers. When it does not answer, the
            # recorded failure stands in for the exact count.
            estimate, exact = await gw.estimate_input(
                conn, token=token, body=body, state=state, anchor=True
            )
        if gwbudget.reserve(estimate, max_tokens, prices).usd_micros > remaining:
            return await _refuse(
                gw, conn, token=token, reason="budget", estimated_input=estimate
            )
    state.pending_estimate = estimate
    state.pending_exact = exact
    state.pending_remaining = remaining
    return None


async def _refuse(gw, conn, *, token, reason, estimated_input=None):
    await gwlog.insert_log(
        conn,
        event="refused",
        brief_id=token.brief_id,
        generation=token.generation,
        space_id=token.space_id,
        model=token.model_ref,
        reason=reason,
        estimated_input=estimated_input,
    )
    return reason, estimated_input


async def _open_call(gw: Gateway, conn, *, token, body) -> int:
    """Hash the body as received, store it, and write the `request` row.
    The request is durable before the provider is called."""
    state = gw.state(token.brief_id)
    state.call += 1
    stripped = _decide_cache(gw, token, body)
    state.pending_stripped = stripped
    request_sha256 = gwlog.body_sha256(gwbudget.canonical(body))
    await gwlog.insert_body(conn, request_sha256, body)
    await gwlog.insert_log(
        conn,
        event="request",
        brief_id=token.brief_id,
        generation=token.generation,
        space_id=token.space_id,
        model=token.model_ref,
        call=state.call,
        request_sha256=request_sha256,
        estimated_input=state.pending_estimate,
        # Seams 5.1: a `request` row carries `none` or `unstable` and
        # nothing else. What the cache did is the `response` row's to say.
        cache_state="unstable" if stripped else "none",
    )
    return state.call


def _decide_cache(gw: Gateway, token, body: dict) -> bool:
    """The prefix rule (plan 04). Only calls carrying breakpoints enter the
    history; a prefix that changed on this call and the one before it is
    changing every call, and its breakpoints are stripped."""
    state = gw.state(token.brief_id)
    current = gwbudget.prefix_sha256(body)
    if current is None:
        return False
    stripped = gwbudget.unstable(state.prefixes, current)
    state.prefixes = (state.prefixes + [current])[-2:]
    return stripped


class _Body:
    """The streaming response's byte iterator.

    An async generator that is closed before it is started runs none of its
    body, so `relay`'s own `finally` would not fire for a client that went
    away before the first byte. This wrapper closes the call on `aclose`
    whatever the generator did, which is what "no call ends without a
    closing row" requires.
    """

    def __init__(self, call: "_Call"):
        self.call = call
        self.chunks = call.relay()

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        return await self.chunks.__anext__()

    async def aclose(self) -> None:
        await self.chunks.aclose()
        await self.call._finish()


class _Call:
    """One forwarded call, from the upstream request to the closing row.

    The gateway always streams upstream, whatever the client asked for, so
    a call is always cuttable. A non-streaming client is served the
    assembled Message from the same bytes.
    """

    def __init__(self, gw: Gateway, *, token, body, call, lock, headers=None):
        self.gw = gw
        self.token = token
        self.body = body
        self.call = call
        self.lock = lock
        self.headers = headers or {}
        self.state = gw.state(token.brief_id)
        self.prices = gw.prices(token.model_ref)
        self.max_tokens = int(body.get("max_tokens") or 0)
        self.estimated_input = self.state.pending_estimate or 0
        self.stripped = self.state.pending_stripped
        self.buffer = b""
        self.saw_bytes = False
        self.cut = False
        self.exhausted = False
        self.timed_out = False
        self.upstream_status = None
        self.error_bytes = b""
        self.failure = None
        self.closed = False
        self._ctx = None
        self._ctx_entered = False
        self._ctx_exited = False
        self.response = None

    # -- upstream -----------------------------------------------------------

    def forwarded(self) -> dict:
        forwarded = (
            gwbudget.strip_breakpoints(self.body) if self.stripped else dict(self.body)
        )
        forwarded["stream"] = True
        return forwarded

    async def start(self) -> bool:
        """Open the upstream stream. False when the provider refused or
        could not be reached, in which case the call is already closed and
        the lock released."""
        self._ctx = self.gw.client.stream(
            "POST",
            "/v1/messages",
            json=self.forwarded(),
            headers=self.gw.upstream_headers(self.headers),
        )
        try:
            self.response = await self._ctx.__aenter__()
            self._ctx_entered = True
        except httpx.TimeoutException:
            await self._fail("timeout")
            return False
        except httpx.HTTPError:
            await self._fail("connect")
            return False
        if self.response.status_code != 200:
            self.error_bytes = await self.response.aread()
            self.upstream_status = self.response.status_code
            await self._drop_upstream()
            await self._fail(str(self.upstream_status))
            return False
        return True

    async def _fail(self, reason: str) -> None:
        """An upstream that never produced a byte: nothing was generated, so
        nothing is consumed. The gateway never retries; a relayed 429 or 5xx
        ends the Executor run and the supervisor decides what follows."""
        self.failure = reason
        async with await self.gw.connect() as conn:
            await gwlog.insert_log(
                conn,
                event="upstream_error",
                brief_id=self.token.brief_id,
                generation=self.token.generation,
                space_id=self.token.space_id,
                model=self.token.model_ref,
                call=self.call,
                request_sha256=None,
                estimated_input=self.estimated_input,
                reason=reason,
            )
            await conn.commit()
        self.closed = True
        self.release()

    # -- the two client shapes ----------------------------------------------

    async def relay(self):
        """Bytes upstream sent, unchanged, parsed on the side."""
        try:
            async for chunk in self._chunks():
                yield chunk
            if self.cut:
                yield CUT_EVENT
        finally:
            await self._finish()

    async def assembled(self):
        """The Message a non-streaming client asked for, built from the
        stream the gateway forced."""
        try:
            async for _ in self._chunks():
                pass
            if self.cut:
                return JSONResponse(
                    error_body("permission_error", REFUSAL_MESSAGE["revoked"]),
                    status_code=403,
                )
            return JSONResponse(sse.assemble(self.parsed()))
        finally:
            await self._finish()

    async def _chunks(self):
        try:
            async for chunk in self.response.aiter_bytes():
                # Before every chunk: a revoked brief reads no further.
                if self.token.brief_id in self.gw.revoked:
                    self.cut = True
                    return
                self.saw_bytes = True
                self.buffer += chunk
                yield chunk
            self.exhausted = True
        except httpx.TimeoutException:
            # The provider billed a generation this gateway did not see
            # finish (spike 03, surprise 2).
            self.timed_out = True
        finally:
            await self._drop_upstream()

    async def _drop_upstream(self) -> None:
        """Close the provider connection once. A client that goes away before
        the first byte never enters `_chunks`, so the drop cannot live only
        there."""
        if self._ctx_entered and not self._ctx_exited:
            self._ctx_exited = True
            await self._ctx.__aexit__(None, None, None)

    # -- the client went away ------------------------------------------------

    async def abandon(self) -> None:
        """The call ended before the runner ever took it: an exception
        `start()` does not catch, including the cancellation a client that
        went away during the upstream connect raises. No upstream byte
        arrived, so nothing is consumed, and both the closing row and the
        lock land here (call path step 9, no call ends without a closing
        row and no lock outlives its call)."""
        if self.closed:
            self.release()
            return
        task = asyncio.ensure_future(self._abandon())
        self.gw.closing.add(task)
        task.add_done_callback(self.gw.closing.discard)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            pass

    async def _abandon(self) -> None:
        try:
            await self._drop_upstream()
            await self._fail("connect")
        finally:
            self.release()

    async def _finish(self) -> None:
        """Close the call and release the lock, even when the client is
        gone. A disconnect cancels this generator, so the closing
        transaction runs as its own task and the cancellation does not take
        the row with it."""
        task = asyncio.ensure_future(self._close_and_release())
        self.gw.closing.add(task)
        task.add_done_callback(self.gw.closing.discard)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            pass

    async def _close_and_release(self) -> None:
        try:
            await self._drop_upstream()
            await self._close()
        finally:
            self.release()

    # -- closing -------------------------------------------------------------

    def parsed(self):
        return sse.parse(sse.iter_events(self.buffer))

    async def _close(self) -> None:
        """One transaction: the closing row, then the consume. Runs whoever
        ended the call, so no call ends without a closing row."""
        if self.closed:
            return
        self.closed = True
        parsed = self.parsed()
        if not self.cut and not self.saw_bytes and not self.exhausted:
            # Nothing was generated, so nothing is consumed. A cut is the one
            # ending that charges without a byte: the gateway stopped reading
            # a generation the provider had already started and will bill.
            await self._fail("timeout" if self.timed_out else "client_disconnect")
            return
        usage = sse.usage_of(
            parsed, max_tokens=self.max_tokens, estimated_input=self.estimated_input
        )
        charge = gwbudget.cost(usage, self.prices)
        usage = sse.usage_of(
            parsed,
            max_tokens=self.max_tokens,
            estimated_input=self.estimated_input,
            usd_micros=charge,
        )
        event = "response" if parsed.complete else "cut"
        overrun = charge > (self.state.pending_remaining or 0)
        async with await self.gw.connect() as conn:
            await gwlog.insert_log(
                conn,
                event=event,
                usage=usage,
                brief_id=self.token.brief_id,
                generation=self.token.generation,
                space_id=self.token.space_id,
                model=self.token.model_ref,
                call=self.call,
                estimated_input=self.estimated_input,
                stop_reason=parsed.stop_reason,
                cache_state=gwbudget.cache_state_of(usage, self.stripped),
                reason="overrun" if overrun else None,
            )
            if not self.token.is_turn:
                # The tree records the full amount, clamps at zero, and
                # appends the overrun; the gateway keeps no clip logic.
                await self.gw.tree.consume(
                    conn,
                    self.token.brief_id,
                    Budget(usd_micros=charge),
                    incurred=True,
                )
            await conn.commit()
        self._remember(parsed, usage, charge)

    def _remember(self, parsed, usage, charge: int) -> None:
        if self.token.is_turn:
            self.state.turn_spent = (self.state.turn_spent or 0) + charge
        if not parsed.complete:
            return
        self.state.prev_body = self.body
        self.state.prev_billed_input = (
            usage.input_tokens
            + usage.cache_creation_input_tokens
            + usage.cache_read_input_tokens
        )
        self.state.calls_since_anchor = (
            0 if self.state.pending_exact else self.state.calls_since_anchor + 1
        )

    def release(self) -> None:
        """Give the brief's lock back, once. Called by the closing path and
        by the route when the call never reached it."""
        if self.lock is not None and self.lock.locked():
            self.lock.release()
            self.lock = None
