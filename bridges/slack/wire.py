"""The Slack wire: the only module that imports `slack_sdk`.

Two Web API clients over Valor's user token. Reads retry on a lost
connection, a 429 (for its `Retry-After`) and a 500 or 503, twice each.
Writes never retry: `slack_sdk`'s default retries a connection error
once, which could post a message twice, so the write client is built with
no retry handlers, and a 429 surfaces as `RateLimited` with Slack's
seconds. Socket Mode is `slack_sdk`'s aiohttp `SocketModeClient`, which
reconnects through its own session monitor.

What a failure means:
- `Refused`: Slack answered with an error (`channel_not_found`,
  `is_archived`); nothing happened.
- `RateLimited`: a 429; nothing happened.
- `NotConnected`: the request never reached Slack (no connection), or a
  read failed.
- `InDoubt`: a write whose connection failed after the request was
  written, or that got a 5xx: Slack may hold it.
- `Skipped`: a file that is not downloaded, with the reason.

A file is fetched with the token only from Slack's hosts
(`slack.com`, `*.slack.com`, `*.slack-files.com`); each redirect is
followed by hand and checked, so the token never leaves them.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp

log = logging.getLogger("valor.slack")

SLACK_HOSTS = ("slack.com", "slack-files.com")
READ_RETRIES = 2
PAGE = 200  # Slack's largest page for history and replies
CHUNK = 1 << 16


class WireError(Exception):
    pass


class Refused(WireError):
    def __init__(self, error: str):
        super().__init__(error)
        self.error = error


class RateLimited(WireError):
    def __init__(self, seconds: float):
        super().__init__(f"rate limited; retry after {seconds:g} s")
        self.seconds = seconds


class NotConnected(WireError):
    pass


class InDoubt(WireError):
    pass


class Skipped(Exception):
    """A file not downloaded; the message is recorded with the reason."""


@dataclass(frozen=True)
class Downloaded:
    path: Path  # a temporary file beside the destination folder
    sha256: str
    size: int


def host_allowed(url: str, hosts: tuple[str, ...]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in hosts)


def team_of(user_token: str) -> str:
    """The team id `auth.test` names for `user_token`, at the command line."""
    from slack_sdk import WebClient

    return WebClient(token=user_token).auth_test()["team_id"].upper()


Handler = Callable[[str, dict[str, Any]], Awaitable[None]]


class SlackWire:
    """One workspace over `app_token` (Socket Mode) and `user_token`.

    `base_url` and `hosts` are where the Web API and file downloads are;
    Slack's own when not given. `insecure` allows `http` downloads, for a
    local stand-in only."""

    def __init__(
        self,
        app_token: str,
        user_token: str,
        *,
        base_url: str | None = None,
        hosts: tuple[str, ...] = SLACK_HOSTS,
        insecure: bool = False,
    ):
        self._app_token = app_token
        self._user_token = user_token
        self._base = {"base_url": base_url} if base_url else {}
        self.hosts = hosts
        self.insecure = insecure
        self.team_id = ""
        self.user_id = ""
        self._ims: dict[str, str] = {}
        self._names: dict[str, str] = {}
        self._tasks: set[asyncio.Task] = set()
        self._socket = None
        self._reads = None
        self._writes = None
        self._http: aiohttp.ClientSession | None = None

    # -- the connection ----------------------------------------------------

    async def connect(self, on_message: Handler, on_hello: Callable[[], None]) -> None:
        """`auth.test`, then the socket. `on_message(envelope_id, event)`
        runs as its own task for each `message` event and acks it itself;
        every other envelope is acked here on arrival. `on_hello()` is
        called on each new socket session."""
        from slack_sdk.http_retry.builtin_async_handlers import (
            AsyncConnectionErrorRetryHandler,
            AsyncRateLimitErrorRetryHandler,
            AsyncServerErrorRetryHandler,
        )
        from slack_sdk.socket_mode.aiohttp import SocketModeClient
        from slack_sdk.web.async_client import AsyncWebClient

        if self._reads is None:
            self._reads = AsyncWebClient(
                token=self._user_token,
                retry_handlers=[
                    AsyncConnectionErrorRetryHandler(max_retry_count=READ_RETRIES),
                    AsyncRateLimitErrorRetryHandler(max_retry_count=READ_RETRIES),
                    AsyncServerErrorRetryHandler(max_retry_count=READ_RETRIES),
                ],
                **self._base,
            )
            self._writes = AsyncWebClient(token=self._user_token, retry_handlers=[], **self._base)
            self._http = aiohttp.ClientSession()
        auth = await self._read("auth_test")
        self.team_id, self.user_id = auth["team_id"].upper(), auth["user_id"]

        async def request(client, req) -> None:
            event = (req.payload or {}).get("event") or {}
            if req.type == "events_api" and event.get("type") == "message":
                self._spawn(on_message(req.envelope_id, event))
            else:
                await self.ack(req.envelope_id)

        async def message(client, msg: dict, raw: str | None) -> None:
            if msg.get("type") == "hello":
                on_hello()

        self._socket = SocketModeClient(
            app_token=self._app_token,
            web_client=AsyncWebClient(token=self._app_token, **self._base),
            auto_reconnect_enabled=True,
        )
        self._socket.socket_mode_request_listeners.append(request)
        self._socket.message_listeners.append(message)
        await self._socket.connect()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def connected(self) -> bool:
        return self._socket is not None and await self._socket.is_connected()

    async def ack(self, envelope_id: str) -> None:
        from slack_sdk.socket_mode.response import SocketModeResponse

        try:
            await self._socket.send_socket_mode_response(SocketModeResponse(envelope_id=envelope_id))
        except Exception as e:  # noqa: BLE001 - Slack redelivers an unacked envelope
            log.info("ack of %s failed (%s); Slack redelivers it", envelope_id, e)

    async def close(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._socket is not None:
            await self._socket.close()
        for client in (self._reads, self._writes):
            if client is not None and client.session is not None:
                await client.session.close()
        if self._http is not None:
            await self._http.close()

    # -- calls -------------------------------------------------------------

    async def _read(self, method: str, **kw) -> dict[str, Any]:
        return await _call(getattr(self._reads, method), kw, write=False)

    async def _write(self, method: str, **kw) -> dict[str, Any]:
        return await _call(getattr(self._writes, method), kw, write=True)

    async def history(
        self,
        channel: str,
        *,
        oldest: str | None = None,
        inclusive: bool = False,
        cursor: str | None = None,
        limit: int = PAGE,
    ) -> tuple[list[dict], str | None]:
        """One page, newest first, and the cursor of the next."""
        out = await self._read(
            "conversations_history",
            **_drop_none(
                channel=channel,
                oldest=oldest,
                inclusive=inclusive,
                cursor=cursor,
                limit=limit,
                include_all_metadata=True,
            ),
        )
        return out.get("messages") or [], _next(out)

    async def replies(
        self,
        channel: str,
        ts: str,
        *,
        oldest: str | None = None,
        inclusive: bool = False,
        cursor: str | None = None,
        limit: int = PAGE,
    ) -> tuple[list[dict], str | None]:
        """One page of a thread, oldest first (the root leads the first
        page), and the cursor of the next."""
        out = await self._read(
            "conversations_replies",
            **_drop_none(
                channel=channel,
                ts=ts,
                oldest=oldest,
                inclusive=inclusive,
                cursor=cursor,
                limit=limit,
                include_all_metadata=True,
            ),
        )
        return out.get("messages") or [], _next(out)

    async def open_im(self, user: str) -> str:
        """The IM channel with `user`, cached for the process."""
        if user not in self._ims:
            out = await self._read("conversations_open", users=user)
            self._ims[user] = out["channel"]["id"]
        return self._ims[user]

    async def user_name(self, user: str) -> str:
        if user not in self._names:
            try:
                out = await self._read("users_info", user=user)
                u = out.get("user") or {}
                self._names[user] = (
                    (u.get("profile") or {}).get("real_name") or u.get("real_name") or u.get("name", "")
                )
            except WireError as e:
                log.info("users.info %s: %s", user, e)
                return ""
        return self._names[user]

    async def post(self, channel: str, text: str, *, thread_ts: str | None, metadata: dict | None) -> str:
        """One `chat.postMessage`, as written: no markup, no unfurls."""
        out = await self._write(
            "chat_postMessage",
            **_drop_none(
                channel=channel,
                text=text,
                thread_ts=thread_ts,
                metadata=metadata,
                mrkdwn=False,
                unfurl_links=False,
                unfurl_media=False,
            ),
        )
        return out["ts"]

    async def react(self, channel: str, ts: str, name: str) -> None:
        """`reactions.add`; a reaction already there (`already_reacted`)
        counts as added, so a repeat is safe."""
        try:
            await self._write("reactions_add", channel=channel, timestamp=ts, name=name)
        except Refused as e:
            if e.error != "already_reacted":
                raise

    async def unreact(self, channel: str, ts: str, name: str) -> None:
        """`reactions.remove`; a reaction not there (`no_reaction`)
        counts as removed."""
        try:
            await self._write("reactions_remove", channel=channel, timestamp=ts, name=name)
        except Refused as e:
            if e.error != "no_reaction":
                raise

    async def upload(self, channel: str, name: str, data: bytes, *, thread_ts: str | None) -> str:
        """The two-step upload, shared to `channel`; the file id."""
        got = await self._write("files_getUploadURLExternal", filename=name, length=len(data))
        try:
            async with self._http.post(got["upload_url"], data=data) as r:
                if r.status != 200:
                    raise Refused(f"upload answered {r.status}")
        except aiohttp.ClientConnectorError as e:
            raise NotConnected(str(e)) from None
        except (aiohttp.ClientError, TimeoutError) as e:
            raise InDoubt(str(e)) from None
        await self._write(
            "files_completeUploadExternal",
            files=[{"id": got["file_id"], "title": name}],
            channel_id=channel,
            thread_ts=thread_ts,
        )
        return got["file_id"]

    async def shares(self, file_id: str, channel: str) -> list[str]:
        """The `ts` of each share of the file in `channel`."""
        out = await self._read("files_info", file=file_id)
        shares = (out.get("file") or {}).get("shares") or {}
        found = []
        for scope in ("public", "private"):
            found += [s["ts"] for s in (shares.get(scope) or {}).get(channel, []) if s.get("ts")]
        return found

    async def download(self, url: str, folder: Path) -> Downloaded:
        """The file at `url` into a temporary file in `folder`. The token
        goes only to Slack's hosts, and each redirect is checked before it
        is followed."""
        for _ in range(5):
            scheme = urlsplit(url).scheme
            if not host_allowed(url, self.hosts) or scheme not in (
                ("http", "https") if self.insecure else ("https",)
            ):
                raise Skipped("redirected off Slack's hosts")
            try:
                async with self._http.get(
                    url, headers={"Authorization": f"Bearer {self._user_token}"}, allow_redirects=False
                ) as r:
                    if r.status in (301, 302, 303, 307, 308):
                        url = urljoin(url, r.headers.get("Location", ""))
                        continue
                    if r.status != 200:
                        raise Skipped(f"download answered {r.status}")
                    if r.headers.get("Content-Type", "").startswith("text/html"):
                        raise Skipped("not signed in")
                    return await _save(r, folder)
            except aiohttp.ClientConnectorError as e:
                raise NotConnected(str(e)) from None
            except (aiohttp.ClientError, TimeoutError) as e:
                raise NotConnected(f"download cut: {e}") from None
        raise Skipped("too many redirects")


async def _save(r: aiohttp.ClientResponse, folder: Path) -> Downloaded:
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=folder, prefix=".slack-")
    digest, size = hashlib.sha256(), 0
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in r.content.iter_chunked(CHUNK):
                digest.update(chunk)
                size += len(chunk)
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Downloaded(Path(name), digest.hexdigest(), size)


async def _call(fn, kw: dict[str, Any], *, write: bool) -> dict[str, Any]:
    from slack_sdk.errors import SlackApiError, SlackClientError

    try:
        return (await fn(**kw)).data
    except SlackApiError as e:
        status = e.response.status_code
        if status == 429 or e.response.get("error") == "ratelimited":
            raise RateLimited(float(e.response.headers.get("Retry-After", 1))) from None
        if status >= 500:
            raise (InDoubt if write else NotConnected)(f"Slack answered {status}") from None
        raise Refused(str(e.response.get("error") or status)) from None
    except aiohttp.ClientConnectorError as e:
        raise NotConnected(str(e)) from None
    except (aiohttp.ClientError, TimeoutError, SlackClientError) as e:
        raise (InDoubt if write else NotConnected)(f"{type(e).__name__}: {e}") from None


def _next(out: dict[str, Any]) -> str | None:
    if not out.get("has_more"):
        return None
    return ((out.get("response_metadata") or {}).get("next_cursor")) or None


def _drop_none(**kw) -> dict[str, Any]:
    return {k: v for k, v in kw.items() if v is not None}
