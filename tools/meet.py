"""`meet.join` (`act`): Valor into a Google Meet call, asked for in a
group chat (docs/plans/meet.md).

Offered to the session of a group chat only (Tom, 2026-10-27: anyone in a
chat group, not a DM): a Telegram group (its id negative) or a Slack
channel. The target is a Meet link, or `new` for a Meet made now with the
Meet API (`gws meet spaces create`), open to anyone with its link, which
only the asking thread is given, so no one waits to be let in. The payload
names the thread it was asked in (`reply_to`, Telegram's `topic_id`) and
`prep`, the asking turn's notes on the topic, read before the call.

Under `meet:<machine>`, one call at a time on this Mac: it fails when the
Mac's call profile is not signed in (naming the step that signs it in) or
the Mac is in a call (naming it); otherwise it opens the call session and
its first report, the link (`core.call.begin`), then starts the call's
process, `python -m core call SESSION`, detached, and returns the link and
the session. A failure is reported to the asking session too, so the chat
hears why. `lookup` finds the session by the effect's key; a Meet made
before a crash is left unused, its link given to no one.
"""

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from core import broker, call, db, tasks
from core.settings import settings

ROOT = Path(__file__).resolve().parents[1]
MEET_URL = re.compile(r"https://meet\.google\.com/([a-z]+-[a-z]+-[a-z]+)/?(?:\?.*)?")
PREP_MOST = 4000


def group_chat(chat: dict[str, str] | None) -> bool:
    """A Telegram group (a negative id) or a Slack channel (`<team>/C..`
    or `/G..`); never a DM."""
    if not chat:
        return False
    if chat["channel"] == "telegram":
        return chat["chat_id"].startswith("-")
    if chat["channel"] == "slack":
        return chat["chat_id"].partition("/")[2][:1] in ("C", "G")
    return False


async def create_space() -> tuple[str, str]:
    """A new Meet, open to anyone with its link: (link, meeting code)."""
    proc = await asyncio.create_subprocess_exec(
        "gws", "meet", "spaces", "create", "--json", json.dumps({"config": {"accessType": "OPEN"}}),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise broker.Failed(f"the Meet API refused a new Meet: {err.decode(errors='replace').strip()[-300:]}")
    space = json.loads(out)
    return space["meetingUri"], space["meetingCode"]


def spawn(session_id: str) -> None:
    """The call's process, detached, its output in the log directory."""
    log = Path(settings.log_dir).expanduser() / "meet.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as out:
        subprocess.Popen(
            [sys.executable, "-m", "core", "call", session_id],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True,
        )  # fmt: skip


class MeetJoin:
    action_type = "meet.join"
    effect_class = "act"
    usage = (
        '`meet.join`: target "new" for a new Meet, or a Meet link; payload `{"reply_to": "<the thread it was '
        "asked in: on Slack its root ts, or the asking message's own ts when it was top-level; on Telegram the "
        'asking message\'s id>", "topic_id": null, "prep": "<your notes on the topic or project, from the '
        'thread, the project and its open decisions, at most 4,000 characters>"}`; Valor joins the call ready '
        "on that topic, and your next prompt carries its link to post in that thread."
    )

    def __init__(self, brief: tasks.Brief, dsn: str | None = None, *, create=create_space, start=spawn):
        self.brief, self.dsn, self.create, self.start = brief, dsn, create, start

    async def refuse(self, conn, action: broker.Action) -> str | None:
        if action.target != "new" and not MEET_URL.fullmatch(action.target):
            return 'the target is "new" or a Meet link (https://meet.google.com/abc-defg-hij)'
        p = action.payload
        if not all(p.get(k) is None or isinstance(p[k], str) for k in ("reply_to", "topic_id", "prep")):
            return "reply_to, topic_id and prep must each be a string or null"
        if len(p.get("prep") or "") > PREP_MOST:
            return f"prep is {len(p['prep'])} characters, over {PREP_MOST}"
        return None

    async def perform(self, action: broker.Action, key: str) -> dict[str, Any]:
        async with await db.connect(self.dsn) as conn:
            lock = f"meet:{settings.machine}"
            await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (lock,))
            try:
                session_id, url = await self._open(conn, action, key)
            except broker.Failed as exc:
                async with conn.transaction():
                    await call.to_session(conn, self.brief.chat["channel"], self.brief.chat["chat_id"],
                                          f"call:{key}:failed", f"The call did not open: {exc}",
                                          project=self.brief.project)  # fmt: skip
                raise
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (lock,))
        self.start(session_id)
        return {"url": url, "session_id": session_id}

    async def _open(self, conn, action: broker.Action, key: str) -> tuple[str, str]:
        if not call.profile_ready():
            raise broker.Failed(
                "this Mac's call profile is not signed in to Google; Tom signs it in once, as Valor, with: "
                + call.SIGN_IN
            )
        busy = await call.in_progress(conn)
        if busy is not None:
            asked = busy.get("asked_by") or {}
            raise broker.Failed(
                f"Valor is already in the call {busy.get('url')}, asked for in {asked.get('channel')}:"
                f"{asked.get('chat_id')}; one call at a time on this Mac"
            )
        if action.target == "new":
            url, code = await self.create()
        else:
            url, code = action.target, MEET_URL.fullmatch(action.target)[1]
        p = action.payload
        async with conn.transaction():
            session_id = await call.begin(conn, self.brief, url=url, code=code, reply_to=p.get("reply_to"),
                                          topic_id=p.get("topic_id"), prep=p.get("prep") or "", effect_key=key)  # fmt: skip
        return session_id, url

    async def lookup(self, action: broker.Action, key: str) -> dict[str, Any] | None:
        async with await db.connect(self.dsn) as conn:
            row = await (
                await conn.execute(
                    "SELECT task_id, payload->'call'->>'url' FROM events WHERE type = 'task.started' "
                    "AND payload->>'effect_key' = %s",
                    (key,),
                )
            ).fetchone()
        return {"url": row[1], "session_id": row[0]} if row else None


def performers(brief: tasks.Brief, dsn: str | None = None) -> list:
    """`meet.join` for a group chat's session; none for any other task."""
    return [MeetJoin(brief, dsn)] if group_chat(brief.chat) and brief.workspace else []
