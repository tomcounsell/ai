"""A local stand-in for Slack, run as its own process.

    python -m tests.slack_emulator

The server listens on a free port and prints it as its first line. It
serves the Web API methods the bridge calls under `/api/<method>`, a
Socket Mode WebSocket at `/ws` (handed out by `apps.connections.open`),
the file upload URL, and file downloads; a test drives it through
`/control`, `/inject`, and `/state`.

Like Slack it:
- stores a post's text the way Slack does: `&`, `<` and `>` as entities,
  each URL wrapped as `<url>`, the ends trimmed;
- keeps a post's `metadata` and returns it with `include_all_metadata`,
  unless told to drop it (`keep_metadata: false`);
- echoes every post, Valor's own included, as a `message` event;
- sends `hello` on each socket session and redelivers an envelope not
  acked within `redeliver_s` (3 s), up to `redeliver_max` (3) times;
- shares an uploaded file at once, after `share_delay_s`, or never in
  `files.info` (`share_mode` `now`, `delay`, `unlisted`), while the file
  message is in the history from the upload on.

A fault (`/control` `faults`) applies to the `nth` call of a method from
the time it is set: `429` (with `retry_after`), `error` (Slack's
`ok: false` with `error`), `500`, `drop` (the connection cut after the
request is read, nothing done), `accept_then_drop` (done, then the
connection cut before the answer), `accept_then_pause` (done, then
`seconds` before the answer), or `pause` (`seconds`, then done).

A file injected with `mode` `redirect` redirects to `localhost` (a host
the bridge does not trust), `slow` streams over `seconds`, and `html`
answers a sign-in page.

`Emulator` starts the server as a child process and drives it.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import re
import socket
import subprocess
import sys
import time
import urllib.request
from typing import Any

from aiohttp import WSMsgType, web

from tests.ports import listen

_URL = re.compile(r"https?://[^\s<>]+")


def slack_text(text: str) -> str:
    """Text as Slack stores a post of it."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return _URL.sub(lambda m: f"<{m.group(0)}>", text).strip()


def _bool(v) -> bool:
    return v is True or str(v).lower() in ("1", "true")


class State:
    def __init__(self):
        self.team = "T0EMU"
        self.me = "U0VALOR"
        self.channels: dict[str, list[dict]] = {}
        self.files: dict[str, dict] = {}
        self.blobs: dict[str, bytes] = {}
        self.faults: list[dict] = []
        self.calls: dict[str, int] = {}
        self.keep_metadata = True
        self.share_mode = "now"
        self.share_delay_s = 2.0
        self.redeliver_s = 3.0
        self.redeliver_max = 3
        self.sockets: set[web.WebSocketResponse] = set()
        self.envelopes: dict[str, dict] = {}
        self.acks: list[str] = []
        self.downloads: dict[str, int] = {}
        self.offhost: list[dict] = []
        self.sessions = 0
        self._last_ts = 0.0
        self._ids = itertools.count(1)

    def ts(self, ago: float = 0.0) -> str:
        t = time.time() - ago
        if not ago:
            t = max(t, self._last_ts + 0.000001)
            self._last_ts = t
        out = f"{t:.6f}"
        while any(m["ts"] == out for msgs in self.channels.values() for m in msgs):
            t += 0.000001
            out = f"{t:.6f}"
        return out

    def find(self, channel: str, ts: str) -> dict | None:
        return next((m for m in self.channels.get(channel, []) if m["ts"] == ts), None)

    def add(self, channel: str, msg: dict) -> dict:
        msgs = self.channels.setdefault(channel, [])
        msgs.append(msg)
        msgs.sort(key=lambda m: m["ts"])
        root_ts = msg.get("thread_ts")
        if root_ts and root_ts != msg["ts"]:
            root = self.find(channel, root_ts)
            if root is not None:
                root["thread_ts"] = root_ts
                root["reply_count"] = root.get("reply_count", 0) + 1
                root["latest_reply"] = max(root.get("latest_reply", ""), msg["ts"])
        return msg


def channel_type(channel: str) -> str:
    return {"D": "im", "G": "group"}.get(channel[:1], "channel")


def _public(m: dict, metadata: bool) -> dict:
    out = {k: v for k, v in m.items() if not k.startswith("_")}
    if not metadata:
        out.pop("metadata", None)
    return out


def make_app() -> web.Application:
    s = State()

    async def params(request: web.Request) -> dict[str, Any]:
        out: dict[str, Any] = dict(request.query)
        if request.can_read_body:
            if request.content_type == "application/json":
                out.update(await request.json())
            else:
                out.update(dict(await request.post()))
        return out

    def ok(body: dict | None = None) -> web.Response:
        return web.json_response({"ok": True, **(body or {})})

    def err(error: str, status: int = 200, headers: dict | None = None) -> web.Response:
        return web.json_response({"ok": False, "error": error}, status=status, headers=headers)

    def fault(method: str) -> dict | None:
        s.calls[method] = s.calls.get(method, 0) + 1
        for f in s.faults:
            if f["method"] == method and f["_at"] == s.calls[method]:
                s.faults.remove(f)
                return f
        return None

    # -- the socket --------------------------------------------------------

    async def deliver(event: dict) -> None:
        eid = f"E{next(s._ids):08d}"
        env = {
            "envelope_id": eid,
            "type": "events_api",
            "accepts_response_payload": False,
            "retry_attempt": 0,
            "retry_reason": "",
            "payload": {"team_id": s.team, "type": "event_callback", "event_id": f"Ev{eid}", "event": event},
        }
        s.envelopes[eid] = {"env": env, "sent": 0, "acked": False}
        asyncio.get_running_loop().create_task(redeliver(eid))

    async def send(env: dict) -> bool:
        sent = False
        for ws in list(s.sockets):
            try:
                await ws.send_str(json.dumps(env))
                sent = True
            except ConnectionError, RuntimeError:
                s.sockets.discard(ws)
        return sent

    async def redeliver(eid: str) -> None:
        e = s.envelopes[eid]
        for attempt in range(s.redeliver_max + 1):
            if e["acked"]:
                return
            e["env"]["retry_attempt"] = attempt
            if await send(e["env"]):
                e["sent"] += 1
            await asyncio.sleep(s.redeliver_s)

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(autoping=True)
        await ws.prepare(request)
        s.sessions += 1
        await ws.send_str(json.dumps({"type": "hello", "num_connections": 1, "connection_info": {}}))
        s.sockets.add(ws)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    body = json.loads(msg.data)
                    eid = body.get("envelope_id")
                    if eid in s.envelopes:
                        s.envelopes[eid]["acked"] = True
                        s.acks.append(eid)
        finally:
            s.sockets.discard(ws)
        return ws

    def event_of(channel: str, m: dict, extra: dict | None = None) -> dict:
        ev = {**_public(m, True), "type": "message", "channel": channel, "channel_type": channel_type(channel)}
        ev["event_ts"] = m["ts"]
        ev.update(extra or {})
        return ev

    # -- the Web API -------------------------------------------------------

    async def api(request: web.Request) -> web.StreamResponse:
        method = request.match_info["method"]
        p = await params(request)
        f = fault(method)
        if f is not None:
            if f["kind"] == "429":
                return err("ratelimited", 429, {"Retry-After": str(f.get("retry_after", 1))})
            if f["kind"] == "error":
                return err(f["error"])
            if f["kind"] == "500":
                return err("internal_error", 500)
            if f["kind"] == "drop":
                request.transport.close()
                await asyncio.sleep(0.05)
                raise web.HTTPServiceUnavailable()
            if f["kind"] == "pause":
                await asyncio.sleep(f.get("seconds", 1))
        handler = METHODS.get(method)
        if handler is None:
            return err("unknown_method")
        out = await handler(request, p)
        if f is not None and f["kind"] == "accept_then_pause":
            await asyncio.sleep(f.get("seconds", 60))
        if f is not None and f["kind"] == "accept_then_drop":
            request.transport.close()
            await asyncio.sleep(0.05)
            raise web.HTTPServiceUnavailable()
        return out

    async def auth_test(request, p):
        return ok({"team_id": s.team, "user_id": s.me, "team": "emulated", "user": "valor"})

    async def connections_open(request, p):
        return ok({"url": f"ws://{request.host}/ws"})

    async def conversations_open(request, p):
        return ok({"channel": {"id": "D" + p["users"]}})

    async def users_info(request, p):
        user = p["user"]
        return ok({"user": {"id": user, "name": user.lower(), "profile": {"real_name": f"Person {user}"}}})

    def page(msgs: list[dict], p: dict) -> web.Response:
        start = int(p.get("cursor") or 0)
        limit = int(p.get("limit") or 100)
        chunk = msgs[start : start + limit]
        more = start + limit < len(msgs)
        metadata = s.keep_metadata and _bool(p.get("include_all_metadata"))
        body = {"messages": [_public(m, metadata) for m in chunk], "has_more": more}
        body["response_metadata"] = {"next_cursor": str(start + limit) if more else ""}
        return ok(body)

    def since(msgs: list[dict], p: dict) -> list[dict]:
        oldest = p.get("oldest")
        if not oldest:
            return msgs
        if _bool(p.get("inclusive")):
            return [m for m in msgs if m["ts"] >= oldest]
        return [m for m in msgs if m["ts"] > oldest]

    async def history(request, p):
        if p["channel"] not in s.channels:
            return err("channel_not_found")
        top = [
            m
            for m in s.channels[p["channel"]]
            if m.get("thread_ts") in (None, m["ts"]) or m.get("subtype") == "thread_broadcast"
        ]
        return page(list(reversed(since(top, p))), p)

    async def replies(request, p):
        msg = s.find(p["channel"], p["ts"])
        if msg is None:
            return err("thread_not_found")
        if msg.get("thread_ts") not in (None, msg["ts"]):
            return page([msg], p)
        rest = [m for m in s.channels[p["channel"]] if m.get("thread_ts") == msg["ts"] and m is not msg]
        return page([msg, *since(rest, p)], p)

    async def post_message(request, p):
        channel = p["channel"]
        m = {"type": "message", "user": s.me, "text": slack_text(p.get("text") or ""), "ts": s.ts()}
        if p.get("thread_ts"):
            m["thread_ts"] = p["thread_ts"]
        meta = p.get("metadata")
        if isinstance(meta, str):
            meta = json.loads(meta)
        if meta:
            m["metadata"] = meta
        s.add(channel, m)
        await deliver(event_of(channel, m, {"team": s.team}))
        return ok({"channel": channel, "ts": m["ts"], "message": m})

    async def upload_url(request, p):
        fid = f"F{next(s._ids):08d}"
        s.files[fid] = {"id": fid, "name": p["filename"], "size": int(p["length"])}
        return ok({"upload_url": f"http://{request.host}/upload/{fid}", "file_id": fid})

    async def complete_upload(request, p):
        files = p["files"] if isinstance(p["files"], list) else json.loads(p["files"])
        channel = p.get("channel_id")
        for entry in files:
            f = s.files[entry["id"]]
            m = {
                "type": "message",
                "user": s.me,
                "text": "",
                "ts": s.ts(),
                "files": [{"id": f["id"], "name": f["name"], "size": len(s.blobs.get(f["id"], b""))}],
                "upload": True,
            }
            if p.get("thread_ts"):
                m["thread_ts"] = p["thread_ts"]
            s.add(channel, m)
            f.update(channel=channel, share_ts=m["ts"], shown_at=time.monotonic(), mode=s.share_mode)
            if s.share_mode == "delay":
                f["shown_at"] += s.share_delay_s
            await deliver(event_of(channel, m, {"team": s.team}))
        return ok({"files": [{"id": e["id"], "title": e.get("title", "")} for e in files]})

    async def files_info(request, p):
        f = s.files.get(p["file"])
        if f is None:
            return err("file_not_found")
        out = {k: v for k, v in f.items() if k in ("id", "name", "size")}
        shares: dict = {}
        if f.get("share_ts") and f.get("mode") != "unlisted" and time.monotonic() >= f["shown_at"]:
            scope = "public" if f["channel"].startswith("C") else "private"
            shares = {scope: {f["channel"]: [{"ts": f["share_ts"]}]}}
        out["shares"] = shares
        return ok({"file": out})

    METHODS = {
        "auth.test": auth_test,
        "apps.connections.open": connections_open,
        "conversations.open": conversations_open,
        "users.info": users_info,
        "conversations.history": history,
        "conversations.replies": replies,
        "chat.postMessage": post_message,
        "files.getUploadURLExternal": upload_url,
        "files.completeUploadExternal": complete_upload,
        "files.info": files_info,
    }

    async def upload(request):
        s.blobs[request.match_info["fid"]] = await request.read()
        return web.Response(text="OK")

    async def download(request):
        fid = request.match_info["fid"]
        s.downloads[fid] = s.downloads.get(fid, 0) + 1
        if not request.headers.get("Authorization", "").startswith("Bearer "):
            return web.Response(status=403)
        f = s.files[fid]
        mode = f.get("download", "ok")
        if mode == "redirect":
            raise web.HTTPFound(f"http://localhost:{request.url.port}/offhost")
        if mode == "html":
            return web.Response(text="<html>sign in</html>", content_type="text/html")
        data = s.blobs[fid]
        resp = web.StreamResponse(headers={"Content-Type": f.get("mimetype", "application/octet-stream")})
        await resp.prepare(request)
        if mode == "slow":
            steps = 10
            for i in range(steps):
                await resp.write(data[i * len(data) // steps : (i + 1) * len(data) // steps])
                await asyncio.sleep(f.get("seconds", 5) / steps)
        else:
            await resp.write(data)
        await resp.write_eof()
        return resp

    async def offhost(request):
        s.offhost.append({"auth": "Authorization" in request.headers})
        return web.Response(body=b"x")

    # -- the test's side ---------------------------------------------------

    async def control(request):
        b = await request.json()
        for k in ("team", "me", "keep_metadata", "share_mode", "share_delay_s", "redeliver_s", "redeliver_max"):
            if k in b:
                setattr(s, k, b[k])
        for f in b.get("faults", []):
            s.faults.append({**f, "_at": s.calls.get(f["method"], 0) + f.get("nth", 1)})
        if b.get("disconnect"):
            for ws in list(s.sockets):
                await ws.close()
        for channel in b.get("channels", []):
            s.channels.setdefault(channel, [])
        return ok()

    async def inject(request):
        b = await request.json()
        channel = b["channel"]
        m = {"type": "message", "user": b.get("user"), "text": b.get("text", ""), "ts": s.ts(b.get("ago", 0))}
        if b.get("user") is None:
            m.pop("user")
        if b.get("thread_ts"):
            m["thread_ts"] = b["thread_ts"]
        m.update(b.get("fields") or {})
        files = []
        for f in b.get("files") or []:
            fid = f"F{next(s._ids):08d}"
            data = base64.b64decode(f.get("data_b64", ""))
            s.blobs[fid] = data
            url = f"http://{request.host}/files/{fid}/{f['name']}"
            s.files[fid] = {
                "id": fid,
                "name": f["name"],
                "size": len(data),
                "mimetype": f.get("mime", "application/octet-stream"),
                "download": f.get("mode", "ok"),
                "seconds": f.get("seconds", 5),
            }
            files.append(
                {
                    "id": fid,
                    "name": f["name"],
                    "mimetype": f.get("mime", "application/octet-stream"),
                    "size": len(data),
                    "url_private": url,
                    "url_private_download": url,
                }
            )
        if files:
            m["files"] = files
            m.setdefault("subtype", "file_share")
        s.add(channel, m)
        if b.get("live", True):
            await deliver(event_of(channel, m, b.get("live_extra")))
        return ok({"ts": m["ts"]})

    async def state(request):
        return ok(
            {
                "team": s.team,
                "me": s.me,
                "channels": {c: [_public(m, True) for m in msgs] for c, msgs in s.channels.items()},
                "acks": s.acks,
                "envelopes": {e: {"sent": v["sent"], "acked": v["acked"]} for e, v in s.envelopes.items()},
                "downloads": s.downloads,
                "offhost": s.offhost,
                "calls": s.calls,
                "sessions": s.sessions,
                "sockets": len(s.sockets),
            }
        )

    app = web.Application()
    app.router.add_route("*", "/api/{method}", api)
    app.router.add_get("/ws", ws_handler)
    app.router.add_post("/upload/{fid}", upload)
    app.router.add_get("/files/{fid}/{name}", download)
    app.router.add_get("/offhost", offhost)
    app.router.add_post("/control", control)
    app.router.add_post("/inject", inject)
    app.router.add_post("/state", state)
    return app


class Emulator:
    """The server as a child process of the test, driven over HTTP."""

    def __init__(self):
        self.port = 0
        self.url = ""
        self.proc: subprocess.Popen | None = None

    def start(self, **control) -> Emulator:
        self.proc = subprocess.Popen([sys.executable, "-m", "tests.slack_emulator"], stdout=subprocess.PIPE, text=True)
        line = self.proc.stdout.readline()
        if not line.strip().isdigit():
            raise RuntimeError("the emulator did not report its port")
        self.port = int(line)
        self.url = f"http://127.0.0.1:{self.port}"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                self.state()
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("the emulator did not start")
        if control:
            self.control(**control)
        return self

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

    def control(self, **kw):
        return self.call("control", **kw)

    def inject(self, channel: str, text: str = "", **kw) -> str:
        return self.call("inject", channel=channel, text=text, **kw)["ts"]

    def state(self) -> dict:
        return self.call("state")

    def messages(self, channel: str) -> list[dict]:
        return self.state()["channels"].get(channel, [])

    def own(self, channel: str) -> list[dict]:
        me = self.state()["me"]
        return [m for m in self.messages(channel) if m.get("user") == me]


def main() -> None:
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", listen()))
    print(sock.getsockname()[1], flush=True)
    web.run_app(make_app(), sock=sock, print=None, shutdown_timeout=1)


if __name__ == "__main__":
    main()
