"""From one Slack `message` event to the fields of the port's `Inbound`.

The bridge decides nothing here: text is Slack's `text` as stored, Tom is
a `<team>/<user>` id the kernel compares, and a file is named by its
sha256, never by anything the sender chose.

One function, `person`, builds a person's id, live, in gap fill, and in
send: the team is the message's `user_team`, else its `team`, else the
connected workspace's. Slack sends `user_team` on some copies of a
message and not others, so the fallback makes both copies one person.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bridges.slack.wire import NotConnected, Skipped, SlackWire

log = logging.getLogger("valor.slack")

# Message subtypes that are someone saying something. Edits, deletions,
# joins and bot messages are not part of the port.
SUBTYPES = {None, "file_share", "thread_broadcast", "me_message"}
THREAD = 50  # the root and the replies before this one, at most


def person(team: str, msg: dict[str, Any]) -> str:
    """`<team>/<user>` for the message's sender; `team` is the connected
    workspace's, used when the message names none."""
    return f"{(msg.get('user_team') or msg.get('team') or team).upper()}/{msg['user']}"


def is_dm(chat: str) -> bool:
    """Whether a chat id names a person (a DM), not a channel."""
    return chat.partition("/")[2][:1] in ("U", "W")


def wanted(msg: dict[str, Any], own: str) -> bool:
    """A person's message, not Valor's own and not a bot's."""
    return (
        msg.get("subtype") in SUBTYPES
        and bool(msg.get("user"))
        and msg.get("user") != own
        and not msg.get("bot_id")
    )


def named(text: str, own: str) -> str:
    """`text` with Valor's own mentions, stored by Slack as `<@U..>`,
    labelled `<@U..|valor>`, so the kernel's mention rule and the session
    reading it see that the message names Valor."""
    return text.replace(f"<@{own}>", f"<@{own}|valor>") if own else text


def chat_of(team: str, channel: str, msg: dict[str, Any], *, dm: bool) -> str:
    """The port's `chat_id`: the other person's id for a DM, else
    `<team>/<channel>`."""
    return person(team, msg) if dm else f"{team.upper()}/{channel}"


def root(msg: dict[str, Any]) -> str | None:
    """The thread root of a reply; None for a top-level message."""
    thread = msg.get("thread_ts")
    return thread if thread and thread != msg["ts"] else None


def skipped(f: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "name": f.get("name") or f.get("title") or "file",
        "mime": f.get("mimetype") or "",
        "bytes": f.get("size"),
        "skipped": reason,
    }


async def attachment(wire: SlackWire, f: dict[str, Any], inbound_dir: Path) -> dict[str, Any]:
    """Download one file into `inbound_dir/slack/`, named by its sha256.
    A lost connection is raised, so the message is taken again; a file
    Slack will not give is listed as skipped with the reason."""
    url = f.get("url_private_download") or f.get("url_private")
    if not url or f.get("mode") in ("tombstone", "hidden_by_limit", "external"):
        return skipped(f, "no download")
    folder = Path(inbound_dir) / "slack"
    try:
        got = await wire.download(url, folder)
    except Skipped as e:
        return skipped(f, str(e))
    path = folder / got.sha256
    os.replace(got.path, path)
    return {
        "name": f.get("name") or "file",
        "mime": f.get("mimetype") or "",
        "bytes": got.size,
        "path": str(path),
    }


async def thread(wire: SlackWire, channel: str, msg: dict[str, Any]) -> list[dict[str, Any]]:
    """The thread before this reply, oldest first: the root and the last
    replies, `THREAD` in all, Valor's own posts included; nothing
    downloaded."""
    top = root(msg)
    if top is None:
        return []
    before: list[dict[str, Any]] = []
    cursor = None
    while True:
        page, cursor = await wire.replies(channel, top, cursor=cursor)
        before += [m for m in page if m["ts"] < msg["ts"]]
        if cursor is None or (page and page[-1]["ts"] >= msg["ts"]):
            break
    first = [m for m in before if m["ts"] == top]
    rest = [m for m in before if m["ts"] != top][-(THREAD - len(first)) :]
    return [
        {
            "id": m["ts"],
            "text": named(m.get("text", ""), wire.user_id),
            "attachments": [skipped(f, "earlier message") for f in m.get("files") or []],
        }
        for m in first + rest
    ]


async def build(
    wire: SlackWire, chat: str, channel: str, msg: dict[str, Any], inbound_dir: Path, *, dm: bool
) -> dict[str, Any]:
    """The `Inbound` fields for one message in `chat` (Slack channel
    `channel`)."""
    files = []
    for f in msg.get("files") or []:
        try:
            files.append(await attachment(wire, f, inbound_dir))
        except NotConnected:
            raise
        except Exception as e:  # noqa: BLE001 - the message is recorded either way
            log.info("file %s of %s skipped: %s", f.get("id"), msg["ts"], e)
            files.append(skipped(f, f"download failed: {type(e).__name__}"))
    top = root(msg)
    return {
        "channel": "slack",
        "chat_id": chat,
        "chat_kind": "dm" if dm else "group",
        "message_id": msg["ts"],
        "sender_id": person(wire.team_id, msg),
        "sender_name": await wire.user_name(msg["user"]),
        "sent_at": datetime.fromtimestamp(float(msg["ts"]), UTC).isoformat(),
        "text": named(msg.get("text") or "", wire.user_id),
        "reply_to": top,
        "thread": await thread(wire, channel, msg),
        "topic_id": top,
        "attachments": files,
        "headers": {},
    }
