"""From one Telegram message to the fields of the port's `Inbound`.

The bridge decides nothing here: text is the raw `message.message`, never a
rendered form; Tom is a numeric sender id the kernel compares; a file is
named by its sha256, never by anything the sender chose.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Callable
from datetime import UTC
from pathlib import Path
from typing import Any

from bridges.telegram.wire import Msg, Wire

CHAIN_HOPS = 20  # telegram.md: the reply chain is fetched a fixed number of hops


def media_timeout(size: int) -> float:
    """Seconds to wait for a download: ten, or five plus one per MB."""
    return max(10.0, 5.0 + size / 1_000_000)


def topic_and_reply(msg: Msg) -> tuple[str | None, str | None]:
    """The forum topic and the message this one replies to (#2652).

    In a topic, Telegram sets `reply_to_msg_id` to the topic's root for a
    plain message, and sets `reply_to_top_id` beside it for a reply inside
    the topic. In General, neither is a topic."""
    if msg.forum_topic:
        if msg.top_id:
            return str(msg.top_id), (str(msg.reply_to) if msg.reply_to else None)
        return (str(msg.reply_to) if msg.reply_to else None), None
    return None, (str(msg.reply_to) if msg.reply_to else None)


def skipped(msg: Msg, reason: str) -> dict[str, Any]:
    m = msg.media
    return {"name": m.name or m.kind, "mime": m.mime, "bytes": m.size, "skipped": reason}


async def attachment(
    wire: Wire, msg: Msg, inbound_dir: Path, *, timeout: Callable[[int], float] = media_timeout
) -> dict[str, Any]:
    """Download the message's file into `inbound_dir/telegram/`, named by its
    sha256. A download that times out is tried once more with twice the
    time; any other failure, or a second timeout, lists the file as
    skipped with the reason."""
    leash = timeout(msg.media.size)
    data = None
    for wait in (leash, leash * 2):
        try:
            data = await asyncio.wait_for(wire.download(msg), wait)
            break
        except TimeoutError:
            continue
        except Exception as e:  # noqa: BLE001 - the message is recorded either way
            return skipped(msg, f"download failed: {type(e).__name__}")
    if data is None:
        return skipped(msg, f"download timed out after {leash * 2:.0f} s")
    digest = hashlib.sha256(data).hexdigest()
    folder = Path(inbound_dir) / "telegram"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / digest
    if not path.exists():
        tmp = folder / f".{digest}.{os.getpid()}"
        tmp.write_bytes(data)
        tmp.replace(path)
    m = msg.media
    return {"name": m.name or m.kind, "mime": m.mime, "bytes": len(data), "path": str(path)}


async def thread(wire: Wire, msg: Msg) -> list[dict[str, Any]]:
    """The messages this one replies to, oldest first: `{id, text,
    attachments}`, nothing downloaded. The walk stops at a deleted
    message, a cycle, or `CHAIN_HOPS`."""
    chain: list[dict[str, Any]] = []
    seen = {msg.id}
    nxt = topic_and_reply(msg)[1]
    while nxt and len(chain) < CHAIN_HOPS:
        found = (await wire.get(msg.chat_id, [int(nxt)]))[0]
        if found is None or found.id in seen:
            break
        seen.add(found.id)
        files = [skipped(found, "earlier message")] if found.media else []
        chain.append({"id": str(found.id), "text": found.text, "attachments": files})
        nxt = topic_and_reply(found)[1]
    chain.reverse()
    return chain


def wanted(msg: Msg) -> bool:
    """Outgoing and service messages are not inbound."""
    return not msg.out and not msg.service


def fields(msg: Msg, chain: list[dict[str, Any]], attachments: list[dict[str, Any]]) -> dict[str, Any]:
    """The `Inbound` fields for one message."""
    topic, reply = topic_and_reply(msg)
    headers = {"grouped_id": str(msg.grouped_id)} if msg.grouped_id else {}
    return {
        "channel": "telegram",
        "chat_id": str(msg.chat_id),
        "chat_kind": msg.chat_kind,
        "message_id": str(msg.id),
        "sender_id": str(msg.sender_id) if msg.sender_id is not None else "",
        "sender_name": msg.sender_name,
        "sent_at": msg.date.astimezone(UTC).isoformat(),
        "text": msg.text,
        "reply_to": reply,
        "thread": chain,
        "topic_id": topic,
        "attachments": attachments,
        "headers": headers,
    }


async def build(
    wire: Wire, msg: Msg, inbound_dir: Path, *, timeout: Callable[[int], float] = media_timeout
) -> dict[str, Any]:
    chain = await thread(wire, msg)
    files = [await attachment(wire, msg, inbound_dir, timeout=timeout)] if msg.media else []
    return fields(msg, chain, files)
