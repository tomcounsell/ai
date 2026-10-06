"""A local stand-in for Telegram, run as its own process, and the `Wire`
that talks to it.

The server holds chats and their messages, the account's own messages
(sender `ME`), message ids from one sequence per supergroup and one shared
by private chats and basic groups (so a chat's ids have gaps), `random_id`
duplicate detection, and Telegram's trim of leading and trailing
whitespace. A test injects messages from others (with or without a live
update), flood waits, a lost connection, a send accepted and its answer
dropped, a send lost before Telegram takes it, and a pause after a send
is accepted. `send_after` lets that many sends through before a send fault
applies.

    python -m tests.telegram_emulator

The server listens on a free port and prints it as its first line.

`EmulatorWire` implements `bridges.telegram.wire.Wire` over it: live
updates by long poll, handled one at a time; a lost connection on a send
is `InDoubt`, on anything else `NotConnected`. `Emulator` starts the server
as a child process and drives it from a test.
"""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta

import aiohttp
from aiohttp import web

from bridges.telegram.wire import (
    DuplicateRandomId,
    FloodWait,
    InDoubt,
    Media,
    Msg,
    NotConnected,
    Refused,
)
from tests.ports import listen

ME = 1000

# -- the server --------------------------------------------------------------


class State:
    def __init__(self):
        self.chats: dict[int, dict] = {}
        self.shared_seq = 0
        self.blobs: dict[tuple[int, int], bytes] = {}
        self.randoms: dict[tuple[int, int], int] = {}
        self.dedup = True
        self.flood_send = 0
        self.send_after = 0
        self.lose_send_next = False
        self.flood_connect = 0
        self.flood_history = 0
        self.drop_reply_next = False
        self.pause_next = 0.0
        self.down = False
        self.epoch = 0
        self.updates: list[dict] = []
        self.reads: dict[int, int] = {}
        self.changed = asyncio.Event()

    def chat(self, chat_id: int, kind: str = "group", forum: bool = False) -> dict:
        if chat_id not in self.chats:
            self.chats[chat_id] = {"kind": kind, "forum": forum, "seq": 0, "msgs": {}}
        return self.chats[chat_id]

    def new_id(self, chat_id: int) -> int:
        c = self.chat(chat_id)
        if c["kind"] == "supergroup":
            c["seq"] += 1
            return c["seq"]
        self.shared_seq += 1
        return self.shared_seq

    def store(self, chat_id: int, m: dict, *, live: bool) -> dict:
        c = self.chat(chat_id)
        m["id"] = self.new_id(chat_id)
        m["chat_id"] = chat_id
        m.setdefault("date", datetime.now(UTC).isoformat())
        m["chat_kind"] = "dm" if c["kind"] == "dm" else "group"
        c["msgs"][m["id"]] = m
        if live:
            self.updates.append(m)
            self.changed.set()
            self.changed = asyncio.Event()
        return m


def _public(m: dict) -> dict:
    return {k: v for k, v in m.items() if k != "data"}


def make_app() -> web.Application:
    s = State()
    app = web.Application(client_max_size=64 * 1024 * 1024)

    def ok(body) -> web.Response:
        return web.json_response(body)

    def err(code: str, **extra) -> web.Response:
        return web.json_response({"error": code, **extra})

    async def body(request) -> dict:
        return await request.json() if request.can_read_body else {}

    def guard():
        if s.down:
            raise web.HTTPServiceUnavailable()

    async def ping(request):
        guard()
        if s.flood_connect:
            secs, s.flood_connect = s.flood_connect, 0
            return err("FLOOD_WAIT", seconds=secs)
        return ok({"epoch": s.epoch, "seq": len(s.updates)})

    async def control(request):
        b = await body(request)
        for k in (
            "dedup",
            "flood_send",
            "send_after",
            "lose_send_next",
            "flood_connect",
            "flood_history",
            "drop_reply_next",
            "pause_next",
            "down",
        ):
            if k in b:
                setattr(s, k, b[k])
        for chat in b.get("chats", []):
            s.chat(chat["id"], chat.get("kind", "group"), chat.get("forum", False))
        if b.get("disconnect"):
            s.epoch += 1
            s.changed.set()
            s.changed = asyncio.Event()
        return ok({"epoch": s.epoch})

    async def inject(request):
        b = await body(request)
        chat = b.pop("chat")
        live = b.pop("live", True)
        data = b.pop("data_b64", None)
        ago = b.pop("ago_s", 0)
        if ago:
            b["date"] = (datetime.now(UTC) - timedelta(seconds=ago)).isoformat()
        b.setdefault("sender_id", 2000)
        b.setdefault("sender_name", "Someone")
        b["text"] = b.get("text", "")
        if b.get("media") and data is not None:
            b["media"]["size"] = len(base64.b64decode(data))
        m = s.store(chat, b, live=live)
        if data is not None:
            s.blobs[(chat, m["id"])] = base64.b64decode(data)
        return ok({"id": m["id"]})

    async def updates(request):
        after = int(request.query.get("after", "0"))
        epoch = int(request.query.get("epoch", "0"))
        if len(s.updates) <= after and epoch == s.epoch:
            try:
                await asyncio.wait_for(s.changed.wait(), 0.5)
            except TimeoutError:
                pass
        return ok({"epoch": s.epoch, "seq": len(s.updates), "msgs": [_public(m) for m in s.updates[after:]]})

    async def history(request):
        guard()
        b = await body(request)
        if s.flood_history:
            secs, s.flood_history = s.flood_history, 0
            return err("FLOOD_WAIT", seconds=secs)
        msgs = sorted(s.chat(b["chat"])["msgs"].values(), key=lambda m: -m["id"])
        if b.get("offset_id"):
            msgs = [m for m in msgs if m["id"] < b["offset_id"]]
        return ok([_public(m) for m in msgs[: b.get("limit", 100)]])

    async def own(request):
        guard()
        b = await body(request)
        msgs = sorted(s.chat(b["chat"])["msgs"].values(), key=lambda m: -m["id"])
        return ok([_public(m) for m in msgs if m.get("out") and m["id"] > b["after_id"]])

    async def get(request):
        guard()
        b = await body(request)
        c = s.chat(b["chat"])["msgs"]
        return ok([_public(c[i]) if i in c else None for i in b["ids"]])

    async def send(request):
        guard()
        b = await body(request)
        chat, rid = b["chat"], b["random_id"]
        if s.send_after and (s.flood_send or s.lose_send_next):
            s.send_after -= 1
        elif s.flood_send:
            secs, s.flood_send = s.flood_send, 0
            return err("FLOOD_WAIT", seconds=secs)
        elif s.lose_send_next:
            s.lose_send_next = False
            request.transport.close()
            await asyncio.sleep(0.05)
            raise web.HTTPServiceUnavailable()
        if s.dedup and (chat, rid) in s.randoms:
            return err("RANDOM_ID_DUPLICATE")
        text = (b.get("text") or "").strip()
        media = None
        if "data_b64" in b:
            data = base64.b64decode(b["data_b64"])
            media = {
                "kind": "document",
                "name": b["name"],
                "mime": "application/octet-stream",
                "size": len(data),
            }
        elif not text:
            return err("MESSAGE_EMPTY")
        m = {"sender_id": ME, "sender_name": "Valor", "text": text, "out": True, "media": media}
        topic, reply = b.get("topic_id"), b.get("reply_to")
        if topic:
            m["forum_topic"] = True
            m["reply_to"], m["top_id"] = (reply, topic) if reply else (topic, None)
        else:
            m["reply_to"] = reply
        m = s.store(chat, m, live=False)
        if media:
            s.blobs[(chat, m["id"])] = data
        s.randoms[(chat, rid)] = m["id"]
        if s.pause_next:
            pause, s.pause_next = s.pause_next, 0
            await asyncio.sleep(pause)
        if s.drop_reply_next:
            s.drop_reply_next = False
            request.transport.close()
            await asyncio.sleep(0.05)
            raise web.HTTPServiceUnavailable()
        return ok({"id": m["id"]})

    async def download(request):
        guard()
        b = await body(request)
        m = s.chat(b["chat"])["msgs"][b["id"]]
        media = m.get("media") or {}
        if media.get("delay"):
            await asyncio.sleep(media["delay"])
        if media.get("fail"):
            return err("FILE_REFERENCE_EXPIRED")
        data = s.blobs.get((b["chat"], b["id"]), b"")
        return ok({"data_b64": base64.b64encode(data).decode()})

    async def read(request):
        guard()
        b = await body(request)
        s.reads[b["chat"]] = max(s.reads.get(b["chat"], 0), b["max_id"])
        return ok({})

    async def state(request):
        out = {
            str(c): sorted((_public(m) for m in v["msgs"].values()), key=lambda m: m["id"])
            for c, v in s.chats.items()
        }
        return ok({"chats": out, "reads": {str(k): v for k, v in s.reads.items()}})

    for name, fn in [
        ("ping", ping),
        ("control", control),
        ("inject", inject),
        ("history", history),
        ("own", own),
        ("get", get),
        ("send", send),
        ("download", download),
        ("read", read),
        ("state", state),
    ]:
        app.router.add_post(f"/{name}", fn)
    app.router.add_get("/updates", updates)
    return app


# -- the wire ----------------------------------------------------------------


def to_msg(m: dict | None) -> Msg | None:
    if m is None:
        return None
    media = m.get("media")
    return Msg(
        chat_id=m["chat_id"],
        id=m["id"],
        date=datetime.fromisoformat(m["date"]),
        sender_id=m.get("sender_id"),
        sender_name=m.get("sender_name", ""),
        text=m.get("text", ""),
        chat_kind=m.get("chat_kind", "group"),
        out=bool(m.get("out")),
        service=bool(m.get("service")),
        reply_to=m.get("reply_to"),
        top_id=m.get("top_id"),
        forum_topic=bool(m.get("forum_topic")),
        grouped_id=m.get("grouped_id"),
        media=Media(media["kind"], media.get("name", ""), media.get("mime", ""), media.get("size", 0))
        if media
        else None,
    )


class EmulatorWire:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None
        self._handlers = []
        self._connected = False
        self._lost = asyncio.Event()
        self._poll: asyncio.Task | None = None
        self._epoch = 0
        self._seq = 0

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None))
        return self._session

    async def _raw(self, name: str, body: dict, *, send: bool = False):
        try:
            async with self._http().post(f"{self.url}/{name}", json=body) as r:
                if r.status == 503:
                    raise InDoubt("service unavailable") if send else NotConnected("service unavailable")
                out = await r.json()
        except (
            aiohttp.ServerDisconnectedError,
            aiohttp.ClientConnectionError,
            aiohttp.ClientPayloadError,
        ) as e:
            if send:
                raise InDoubt(type(e).__name__) from None
            raise NotConnected(type(e).__name__) from None
        if isinstance(out, dict) and "error" in out:
            if out["error"] == "FLOOD_WAIT":
                raise FloodWait(out["seconds"])
            if out["error"] == "RANDOM_ID_DUPLICATE":
                raise DuplicateRandomId("random_id already used")
            raise Refused(out["error"])
        return out

    async def _post(self, name: str, body: dict, *, send: bool = False):
        if not self._connected:
            raise NotConnected("not connected")
        return await self._raw(name, body, send=send)

    async def connect(self) -> None:
        info = await self._raw("ping", {})
        self._epoch, self._seq = info["epoch"], info["seq"]
        self._connected = True
        self._lost = asyncio.Event()
        self._poll = asyncio.create_task(self._polling())

    async def disconnect(self) -> None:
        self._connected = False
        self._lost.set()
        if self._poll and self._poll is not asyncio.current_task():
            self._poll.cancel()

    async def close(self) -> None:
        await self.disconnect()
        if self._session:
            await self._session.close()

    def connected(self) -> bool:
        return self._connected

    async def disconnected(self) -> None:
        await self._lost.wait()

    def on_message(self, handler) -> None:
        self._handlers.append(handler)

    async def _polling(self) -> None:
        while self._connected:
            try:
                async with self._http().get(
                    f"{self.url}/updates", params={"after": self._seq, "epoch": self._epoch}
                ) as r:
                    out = await r.json()
            except aiohttp.ClientError, TimeoutError:
                await self.disconnect()
                return
            if out["epoch"] != self._epoch:
                await self.disconnect()
                return
            self._seq = out["seq"]
            for m in out["msgs"]:
                for h in self._handlers:
                    await h(to_msg(m))
                if not self._connected:
                    return

    async def history(self, chat_id: int, *, offset_id: int = 0, limit: int = 100) -> list[Msg]:
        return [
            to_msg(m)
            for m in await self._post("history", {"chat": chat_id, "offset_id": offset_id, "limit": limit})
        ]

    async def own(self, chat_id: int, *, after_id: int) -> list[Msg]:
        return [to_msg(m) for m in await self._post("own", {"chat": chat_id, "after_id": after_id})]

    async def get(self, chat_id: int, ids: list[int]) -> list[Msg | None]:
        return [to_msg(m) for m in await self._post("get", {"chat": chat_id, "ids": ids})]

    async def send_text(self, chat_id, text, *, random_id, reply_to, topic_id) -> int:
        body = {
            "chat": chat_id,
            "text": text,
            "random_id": random_id,
            "reply_to": reply_to,
            "topic_id": topic_id,
        }
        return (await self._post("send", body, send=True))["id"]

    async def send_file(self, chat_id, data, name, *, random_id, reply_to, topic_id) -> int:
        body = {
            "chat": chat_id,
            "data_b64": base64.b64encode(data).decode(),
            "name": name,
            "random_id": random_id,
            "reply_to": reply_to,
            "topic_id": topic_id,
        }
        return (await self._post("send", body, send=True))["id"]

    async def download(self, msg: Msg, progress=None) -> bytes:
        out = await self._post("download", {"chat": msg.chat_id, "id": msg.id})
        data = base64.b64decode(out["data_b64"])
        if progress is not None:
            progress(len(data), len(data))
        return data

    async def mark_read(self, chat_id: int, max_id: int) -> None:
        await self._post("read", {"chat": chat_id, "max_id": max_id})


# -- the test's handle -------------------------------------------------------


class Emulator:
    """The server as a child process of the test, driven over HTTP."""

    def __init__(self):
        self.port = 0
        self.url = ""
        self.proc: subprocess.Popen | None = None

    def start(self) -> Emulator:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "tests.telegram_emulator"], stdout=subprocess.PIPE, text=True
        )
        line = self.proc.stdout.readline()
        if not line.strip().isdigit():
            raise RuntimeError("the emulator did not report its port")
        self.port = int(line)
        self.url = f"http://127.0.0.1:{self.port}"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                self.call("state")
                return self
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("the emulator did not start")

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc.wait(10)

    def call(self, name: str, **body):
        req = urllib.request.Request(
            f"{self.url}/{name}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def inject(self, chat: int, text: str = "", **kw) -> int:
        return self.call("inject", chat=chat, text=text, **kw)["id"]

    def control(self, **kw):
        return self.call("control", **kw)

    def messages(self, chat: int) -> list[dict]:
        return self.call("state")["chats"].get(str(chat), [])

    def own(self, chat: int) -> list[dict]:
        return [m for m in self.messages(chat) if m.get("out")]


def main() -> None:
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", listen()))
    print(sock.getsockname()[1], flush=True)
    web.run_app(make_app(), sock=sock, print=None, shutdown_timeout=1)


if __name__ == "__main__":
    main()
