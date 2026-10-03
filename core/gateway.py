"""The model gateway: the one path a turn's model calls take.

A local HTTP proxy speaking the Anthropic Messages wire format. A turn is
pointed at it through `ANTHROPIC_BASE_URL` with a per-turn token in the
path, so every call the harness makes, including its own side calls, passes
through here. For each `POST /v1/messages` the gateway:

1. prices the model and opens the call with a ledger row (refused, with a
   ledger row, only when the task is stopped);
2. forwards the request upstream and streams the response back unchanged,
   reading the usage the provider reports as it passes;
3. charges what the provider reported when the call closes.

A revoke cuts every in-flight call of the task at once, and so does the
turn's exit; a call whose client disconnects is cut alone. The gateway sets
no timeout of its own: a call lasts as long as a client waits for it. A call that
ends without its final usage (cut, or the client died) is charged its input
as reported plus every output token it was allowed, so the ledger never
records less than the invoice. Other paths (token counting, model lists)
cost nothing and pass through unmetered.

**The credential.** A turn runs with its own Claude Code config directory,
which holds no login, so it carries a placeholder (`TURN_TOKEN`) and the
gateway, when given a `credential`, drops whatever `authorization` or
`x-api-key` the turn sent and sets the kernel's own: a long-lived token in
the kernel key directory (`claude setup-token`) when one is there, otherwise
the access token of the machine's Claude Code login, read from the Keychain
through the root-owned `security` at most once a minute, except that the
first 401 after a read allows one more (the user's own sessions rotate the
token). The credential is sent only on the Messages API, its token
counting, the model list, and one model by an id of letters, digits, `.`,
`_`, and `-`; any other path is refused. Every path is checked as it
arrived, undecoded (no percent escape, no empty, `.`, or `..` segment), and
forwarded byte for byte, never re-normalised. The kernel never refreshes that login: refresh tokens rotate, and a
refresh here could sign out the user's own sessions. An expired login is a
401 naming the remedy, never a silent fallback. No credential is ever in a
ledger row, an exception, or a log line.
"""

import asyncio
import json
import re
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from aiohttp import web
from yarl import URL

from core import binaries, db, spending, tasks
from core.ledger import new_id
from core.settings import settings

# Hop-by-hop and length headers are recomputed on each side.
DROP_REQUEST = {"host", "content-length", "accept-encoding", "connection", "transfer-encoding"}
DROP_RESPONSE = {"content-length", "content-encoding", "transfer-encoding", "connection"}


# What a turn carries in place of a Claude credential; the gateway replaces it.
TURN_TOKEN = "valor-turn-holds-no-credential"
CREDENTIAL_HEADERS = {"authorization", "x-api-key"}
KEYCHAIN_SERVICE = "Claude Code-credentials"


class CredentialUnavailable(RuntimeError):
    """The kernel has no usable Claude credential; the message names the
    remedy and never any part of a credential."""


class ClaudeLogin:
    """The credential the gateway sends upstream for every turn.

    The Keychain is read at most once a minute (`min_interval_s`), whether
    the last read succeeded or failed, so an expired login does not run
    `security` on every call; the one exception is the first 401 after a
    read, which may read again at once. `keychain` reads the login item's JSON, or returns None when there
    is none; tests pass their own."""

    def __init__(
        self,
        token_file: str | None = None,
        ttl_s: float = 60.0,
        service: str = KEYCHAIN_SERVICE,
        keychain=None,
        min_interval_s: float = 60.0,
    ):
        self.token_file = Path(token_file or settings.claude_token_file)
        self.service = service
        self.ttl_s = ttl_s
        self.min_interval_s = min_interval_s
        self.keychain = keychain or self._security
        self._cached: tuple[str | CredentialUnavailable, float] | None = None
        self._stale = False
        self._retried = False
        self._retry_now = False
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        """A 401: the next call reads again. The first 401 after a read may
        read at once, since the user's own sessions rotate the token; later
        ones wait out `min_interval_s`."""
        with self._lock:
            if not self._stale and not self._retried:
                self._retry_now = True
            self._stale = True

    def token(self) -> str:
        with self._lock:
            now = time.monotonic()
            if self._cached is not None:
                value, at = self._cached
                fresh = not self._stale and now - at < self.ttl_s
                if fresh or (now - at < self.min_interval_s and not self._retry_now):
                    if isinstance(value, CredentialUnavailable):
                        raise value
                    return value
            self._retried = self._retry_now
            self._retry_now = False
            try:
                value = self._read()
            except CredentialUnavailable as exc:
                self._cached, self._stale = (exc, now), False
                raise
            self._cached, self._stale = (value, now), False
            return value

    def _security(self) -> str | None:
        try:
            done = subprocess.run(
                [binaries.require(binaries.SECURITY), "find-generic-password", "-s", self.service, "-w"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (binaries.Untrusted, subprocess.TimeoutExpired) as exc:
            raise CredentialUnavailable(f"the Keychain could not be read: {type(exc).__name__}") from None
        return done.stdout if done.returncode == 0 else None

    def _read(self) -> str:
        if self.token_file.is_file():
            value = self.token_file.read_text().strip()
            if value:
                return value
        raw = self.keychain()
        if raw is None:
            raise CredentialUnavailable(
                "no Claude login in the Keychain: log in with `claude`, or install a long-lived token "
                f"at {self.token_file}"
            )
        try:
            oauth = json.loads(raw)["claudeAiOauth"]
            access, expires = str(oauth["accessToken"]), oauth.get("expiresAt")
            expired = expires is not None and float(expires) / 1000 < time.time()
        except ValueError, KeyError, TypeError:
            raise CredentialUnavailable("the Keychain's Claude login is not in the shape expected") from None
        if expired:
            raise CredentialUnavailable(
                "the Claude login's access token has expired: run any claude session, or install a "
                f"long-lived token at {self.token_file} (`claude setup-token`)"
            )
        if not access:
            raise CredentialUnavailable("the Keychain's Claude login holds no access token")
        return access


# The upstream paths a turn's calls may take with the kernel's credential:
# the Messages API, its token counting, and the model list. Any other path
# is refused rather than sent with the credential.
CREDENTIALED_PATHS = ("v1/messages", "v1/messages/count_tokens", "v1/models")
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def safe_tail(tail: str) -> bool:
    """A path tail as it arrived, undecoded: no percent escape, no
    backslash, and no empty, `.`, or `..` segment (one trailing slash
    allowed). The upstream URL is built from it as is, so nothing a client's
    parser or ours would resolve can move it to another path."""
    if "%" in tail or "\\" in tail or not tail:
        return False
    body = tail.removesuffix("/")
    return all(seg not in ("", ".", "..") for seg in body.split("/"))


def credentialed(tail: str) -> bool:
    """Whether a safe tail may be sent with the kernel's credential: the
    three paths, or one model by an id of letters, digits, `.`, `_`, `-`
    (never `..`)."""
    if not safe_tail(tail):
        return False
    tail = tail.rstrip("/")
    if tail in CREDENTIALED_PATHS:
        return True
    model = tail.removeprefix("v1/models/")
    return model != tail and MODEL_ID.fullmatch(model) is not None and ".." not in model


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
    def __init__(
        self, dsn: str | None = None, upstream: str | None = None, credential: ClaudeLogin | None = None
    ):
        self.dsn = dsn or settings.dsn()
        self.credential = credential
        self.upstream = (upstream or settings.upstream).rstrip("/")
        self.grants: dict[str, Grant] = {}
        self.revoked: set[str] = set()
        self.calls: dict[str, set[asyncio.Task]] = {}
        self.upstream_calls: dict[str, set[asyncio.Task]] = {}
        # Calls whose client left before they were sent: they are not sent.
        self.abandoned: set[asyncio.Task] = set()
        self.url: str | None = None
        self._runner: web.AppRunner | None = None
        self._session: aiohttp.ClientSession | None = None

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> str:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_route("*", "/t/{token}/{tail:.*}", self.handle)
        self._runner = web.AppRunner(app, handler_cancellation=True)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, port)
        await site.start()
        bound = site._server.sockets[0].getsockname()[1]
        self.url = f"http://{host}:{bound}"
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None),
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
        self.cut(task_id)

    def cut(self, task_id: str) -> None:
        """Cut the task's calls that are waiting on or streaming from the
        provider; each is still charged. A turn's exit cuts its calls, since
        no client is left to read them."""
        for call in list(self.upstream_calls.get(task_id, ())):
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
        # The tail as the client sent it, undecoded, so what is checked is
        # what is forwarded.
        raw_path, _, raw_query = request.raw_path.partition("?")
        prefix = f"/t/{request.match_info['token']}/"
        tail = raw_path[len(prefix) :] if raw_path.startswith(prefix) else ""
        if not safe_tail(tail):
            return _error(400, "invalid_request_error", "the gateway forwards no such path")
        path = "/" + tail + (f"?{raw_query}" if raw_query else "")
        body = await request.read()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_REQUEST}
        headers["accept-encoding"] = "identity"
        if self.credential is not None:
            if not credentialed(tail):
                return _error(403, "permission_error", f"the gateway carries no turn to /{tail}")
            headers = {k: v for k, v in headers.items() if k.lower() not in CREDENTIAL_HEADERS}
            try:
                headers["authorization"] = "Bearer " + await asyncio.to_thread(self.credential.token)
            except CredentialUnavailable as exc:
                return _error(401, "authentication_error", str(exc))
        if request.method == "POST" and tail.rstrip("/") == "v1/messages":
            call = asyncio.create_task(self._metered(request, grant, path, headers, body))
            self.calls.setdefault(grant.task_id, set()).add(call)
            call.add_done_callback(self.calls[grant.task_id].discard)
            try:
                response = await asyncio.shield(call)
            except asyncio.CancelledError:
                # The client left. A call waiting on the provider is cut; one
                # not yet sent is never sent; one being charged is left to
                # finish. Either way its charge lands before this returns.
                if call in self.upstream_calls.get(grant.task_id, ()):
                    call.cancel()
                elif not call.done():
                    self.abandoned.add(call)
                    call.add_done_callback(self.abandoned.discard)
                await asyncio.shield(call)
                raise
        else:
            response = await self._forward(request, path, headers, body)
        return response

    def _answered(self, status: int) -> None:
        # Here and not after `handle`'s await: a client that has read the
        # whole 401 may leave before the call returns.
        if status == 401 and self.credential is not None:
            self.credential.invalidate()

    async def _forward(self, request, path, headers, body) -> web.StreamResponse:
        try:
            async with self._session.request(
                request.method, URL(self.upstream + path, encoded=True), headers=headers, data=body
            ) as up:
                self._answered(up.status)
                return web.Response(status=up.status, body=await up.read(), headers=_response_headers(up))
        except aiohttp.ClientConnectorError as exc:
            return _unreachable(exc)
        except aiohttp.ClientError as exc:
            return _ended(exc)

    async def _metered(self, request, grant: Grant, path, headers, body) -> web.StreamResponse:
        try:
            message = json.loads(body)
            model = message["model"]
            max_tokens = int(message.get("max_tokens") or 0)
        except ValueError, KeyError, TypeError:
            return _error(400, "invalid_request_error", "gateway could not read the request")
        price = spending.prices(model)
        if price is None:
            return _error(400, "invalid_request_error", f"model {model} has no price")
        estimated = spending.estimate_input(message)
        call_id = new_id()
        call = {
            "call_id": call_id,
            "turn_id": grant.turn_id,
            "model": model,
            "route": "gateway",
            "estimated_input": estimated,
            "max_tokens": max_tokens,
            "estimate_usd_micros": spending.worst_case(estimated, max_tokens, price),
        }
        async with await db.connect(self.dsn) as conn:
            try:
                await spending.open_call(conn, grant.task_id, call)
            except tasks.TaskStopped:
                return _error(400, "invalid_request_error", "refused: the task is stopped")

        meter = Meter()
        status = None
        cut = False
        unsent = False
        response = None
        failed = None
        me = asyncio.current_task()
        upstream = self.upstream_calls.setdefault(grant.task_id, set())
        upstream.add(me)
        # From here `cut`, `revoke`, and a leaving client cancel this call.
        # One whose client already left, or whose token was retired while it
        # opened, is not sent: a turn that has exited is never waited on.
        if me in self.abandoned or self.grants.get(request.match_info["token"]) is not grant:
            upstream.discard(me)
            await self._close(grant, call, price, meter, status, cut, True)
            return _error(403, "permission_error", "turn token revoked or unknown")
        try:
            async with self._session.post(
                URL(self.upstream + path, encoded=True), headers=headers, data=body
            ) as up:
                status = up.status
                self._answered(status)
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
        except (aiohttp.ClientConnectorError, aiohttp.ConnectionTimeoutError) as exc:
            unsent, failed = True, exc  # never reached the provider: nothing billed
        except (ConnectionError, aiohttp.ClientError) as exc:
            cut, failed = True, exc
        except asyncio.CancelledError as exc:
            # Revoked, cut, or the client left: the charge still lands.
            me.uncancel()
            cut, failed = True, exc
            if request.transport is not None:
                request.transport.close()
        finally:
            # Out of the set first, so no cancel can land in the charge.
            upstream.discard(me)
            await self._close(grant, call, price, meter, status, cut, unsent)
        if response is not None:
            return response
        return _unreachable(failed) if unsent else _ended(failed)

    async def _close(self, grant, call, price, meter: Meter, status, cut, unsent) -> None:
        if unsent:
            charged = 0
        elif meter.complete:
            charged = spending.cost(meter.usage, price)
        elif meter.started:
            # Input as reported, every allowed output token: never under the invoice.
            charged = spending.cost({**meter.usage, "output_tokens": call["max_tokens"]}, price)
        elif status is not None and status >= 400:
            charged = 0  # the provider refused before generating: nothing billed
        else:
            charged = call["estimate_usd_micros"]  # no usage reported: the worst case
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
            await spending.charge(conn, grant.task_id, call["call_id"], charged, detail)


def _response_headers(up: aiohttp.ClientResponse) -> dict[str, str]:
    return {k: v for k, v in up.headers.items() if k.lower() not in DROP_RESPONSE}


def _unreachable(exc: BaseException | None) -> web.Response:
    # The exception's type only: aiohttp's text can carry headers.
    return _error(502, "api_error", f"the gateway could not reach the provider ({type(exc).__name__})")


def _ended(exc: BaseException | None) -> web.Response:
    return _error(
        502, "api_error", f"the provider's connection ended before it answered ({type(exc).__name__})"
    )


def _error(status: int, kind: str, message: str) -> web.Response:
    return web.json_response({"type": "error", "error": {"type": kind, "message": message}}, status=status)
