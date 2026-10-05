"""The local chat bridge: one page on 127.0.0.1 for chatting with Valor on
a Mac with no Telegram session and no mailbox login.

The page (`chat.html`, `chat.js`) polls `GET /log` and posts Tom's
messages to `POST /send`; both carry the token from the mode-600 file
`settings.local_tokenfile` in the `X-Valor-Token` header. The ledger is
the platform: a notice or an approved `local.send_message` is shown once it
has a `notice.sent` or `effect.outcome` row, so sending is a ledger write
and `lookup` returns what `perform` returns. Governed by
`docs/bridges/local.md`.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp import web

from core import bridge, broker, db, intake
from core.settings import settings

log = logging.getLogger("valor.local")

CHANNEL = "local"
CHAT = intake.LOCAL_CHAT
HERE = Path(__file__).resolve().parent
TOKEN_HEADER = "X-Valor-Token"

# The whole local chat, oldest first: Tom's messages, and the notices and
# sends shown on the page, with their text.
_SHOWN = '[{"channel": "local", "chat_id": "local"}]'
LOG_SQL = f"""
SELECT r.id, r.payload->>'message_id', 'tom', r.payload->>'text', r.payload->>'reply_to'
FROM events r WHERE r.type = 'message.received' AND r.task_id = 'local' AND r.payload->>'chat_id' = 'local'
UNION ALL
SELECT s.id, s.payload->'sent'->0->>'message_id', 'valor', n.payload->>'text', n.payload->>'reply_to'
FROM events s JOIN events n ON n.type = 'notice.requested'
  AND n.payload->>'notice_id' = s.payload->>'notice_id'
WHERE s.type = 'notice.sent' AND s.payload->'sent' @> '{_SHOWN}'
UNION ALL
SELECT o.id, o.payload->>'effect_id', 'valor', h.payload->'payload'->>'text', NULL
FROM events o JOIN events h ON h.type = 'effect.held' AND h.payload->>'effect_id' = o.payload->>'effect_id'
WHERE o.type = 'effect.outcome' AND o.payload->'result'->'sent' @> '{_SHOWN}'
ORDER BY 1
"""


def ensure_token(path: str | Path) -> str:
    """The page's token, made (mode 600) when the file is missing; an
    existing file is read, never rewritten."""
    path = Path(path)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    token = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    return token


def _sent(message_id: str) -> dict[str, Any]:
    return {"sent": [{"channel": CHANNEL, "chat_id": CHAT, "message_id": message_id}]}


class LocalBridge:
    channel = CHANNEL

    def __init__(self, dsn: str | None = None, port: int | None = None, tokenfile: str | None = None):
        """`dsn` is the database the handlers read and write: the one
        `serve` is given, the kernel's when both are None."""
        self.dsn = dsn
        self.port = port or settings.local_port
        self.tokenfile = tokenfile or settings.local_tokenfile
        self.token: str | None = None
        self.runner: web.AppRunner | None = None

    # -- the port's side --------------------------------------------------

    def performers(self):
        return {"local.send_message": (self.perform, self.lookup)}

    async def perform(self, action: broker.Action, key: str) -> dict[str, Any]:
        """Shown on the page once its outcome is written: the message id is
        the effect id, which ends the key."""
        return _sent(key.rsplit(":", 1)[-1])

    async def lookup(self, action: broker.Action, key: str, since: str) -> dict[str, Any] | None:
        """The ledger is the platform: what `perform` returns, so a crash
        between intent and outcome reconciles `done` and is shown once."""
        return _sent(key.rsplit(":", 1)[-1])

    async def run(self, outbox: bridge.Outbox) -> None:
        await self.start()
        try:
            async for item in outbox:
                if isinstance(item, bridge.Release):
                    await outbox.perform(item)
                else:
                    await outbox.sent(item, _sent(item.notice_id)["sent"])
        finally:
            await self.close()

    async def tick(self) -> None:
        return None

    # -- the page ----------------------------------------------------------

    async def start(self) -> None:
        """Make the token when it is missing and serve the page on
        127.0.0.1."""
        self.token = ensure_token(self.tokenfile)
        app = web.Application()
        app.router.add_get("/", self.page)
        app.router.add_get("/chat.js", self.script)
        app.router.add_get("/log", self.log)
        app.router.add_post("/send", self.send)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        log.info("local chat on http://127.0.0.1:%s/", self.port)

    async def close(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None

    def _authorized(self, request: web.Request) -> bool:
        given = request.headers.get(TOKEN_HEADER, "")
        return bool(self.token) and secrets.compare_digest(given.encode(), self.token.encode())

    async def page(self, request: web.Request) -> web.Response:
        return web.Response(
            body=(HERE / "chat.html").read_bytes(),
            content_type="text/html",
            headers={"Content-Security-Policy": "frame-ancestors 'none'"},
        )

    async def script(self, request: web.Request) -> web.Response:
        return web.Response(body=(HERE / "chat.js").read_bytes(), content_type="text/javascript")

    async def log(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        async with await db.connect(self.dsn) as conn:
            rows = await (await conn.execute(LOG_SQL)).fetchall()
        return web.json_response(
            {
                "rows": [
                    {"event_id": e, "message_id": m, "from": who, "text": text or "", "reply_to": reply}
                    for e, m, who, text, reply in rows
                ]
            }
        )

    async def send(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not (
            isinstance(body, dict)
            and isinstance(body.get("id"), str)
            and body["id"]
            and isinstance(body.get("text"), str)
            and (body.get("reply_to") is None or isinstance(body["reply_to"], str))
        ):
            return web.json_response({"error": "expected {id, text, reply_to}"}, status=400)
        inbound = intake.Inbound(
            channel=CHANNEL,
            chat_id=CHAT,
            chat_kind="dm",
            message_id=body["id"],
            sender_id="local",
            sender_name="Tom",
            sent_at=datetime.now(UTC).isoformat(),
            text=body["text"],
            reply_to=body.get("reply_to"),
        )
        async with await db.connect(self.dsn) as conn:
            got = await intake.receive(conn, inbound)
        return web.json_response({"received_id": got.received_id, "duplicate": got.duplicate})
