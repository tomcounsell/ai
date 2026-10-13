"""`slack.send_message`: perform, lookup, and the notice send.

A payload is `{text, reply_to, files: [{path, sha256}]}`; the target is
`<team>/<channel>`, or `<team>/<user>` for a DM. Text is split by the
port's `split_text` (4,000 characters a post); files follow. Each post
is `chat.postMessage` with no markup and no unfurls, carrying the marker
`{event_type: "valor_send", event_payload: {key: sha256(broker key),
part, parts}}`.

`thread_ts` is the root of `reply_to`'s thread, resolved in order: the
ledger (`intake.root_of`), then one `conversations.replies(ts=reply_to,
limit=1)`, then `reply_to` as given, and Slack's own error on that post
is the outcome.

What a failure means, as the broker reads it:
- the first post refused, rate limited, or never sent is `broker.Failed`
  (a rate limit names Slack's `Retry-After` seconds); so is a target in a
  workspace other than the connected one;
- a 429 on a later post is waited out for its seconds and the send goes
  on; a later post refused settles done with the posts made;
- a post in doubt (the connection cut after the request was written, a
  5xx, a later post never sent, or a file share whose `ts` five
  `files.info` reads two seconds apart do not show) is `broker.Unknown`,
  and the outbox's reconcile settles it through `lookup`.

`lookup` before the bridge has connected is `Unknown`. It scans the
target chat from `since` less five minutes (the thread when there is a
root, else the history), keeping Valor's own posts
not already claimed by a recorded send or notice. When any kept post
carries a marker, a text part is the post with this key's marker and its
part number; otherwise it is the earliest kept post after the previous
part whose text, decoded from Slack's form, equals the part's (both
trimmed) and whose thread is the send's. A file part matches on name and size. All found is done; a found
prefix is finished, then done; a part found after a missing one, or two
candidates for one part, is `Unknown`; nothing found is None.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from bridges.slack import inbound
from bridges.slack.wire import InDoubt, NotConnected, RateLimited, Refused, SlackWire, WireError

log = logging.getLogger("valor.slack")

EVENT_TYPE = "valor_send"
SKEW = timedelta(minutes=5)
_LINK = re.compile(r"<([^<>|]*)(?:\|([^<>]*))?>")
# Slack keeps a mention, channel link or special mention as written.
_CONTROL = re.compile(r"<([@#!][^<>|]*)(?:\|([^<>]*))?>")


def _unlink(pattern: re.Pattern, text: str) -> str:
    return pattern.sub(lambda m: m.group(2) if m.group(2) is not None else m.group(1), text)


def normal(text: str) -> str:
    """A post's text as Slack stores it, back to plain text: `<x|label>`
    to `label`, `<x>` to `x`, `&lt;` `&gt;` `&amp;` decoded, ends
    stripped."""
    return _unlink(_LINK, text).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()


def same(stored: str, requested: str) -> bool:
    """Whether a post Slack stored as `stored` is the text `requested`.
    The requested text is not decoded (a literal `&amp;` stays), but the
    sequences Slack keeps as written in it are unwrapped the way `normal`
    unwraps them: the mentions and channel links, or every `<...>` when
    the text wrote its links that way too."""
    plain = normal(stored)
    return plain in (_unlink(_CONTROL, requested).strip(), _unlink(_LINK, requested).strip())


def marker(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def notice_key(notice_id: str) -> str:
    return f"notice:{notice_id}"


def read_file(entry: dict[str, str]) -> tuple[str, bytes]:
    """The file's bytes, read once and hashed; raises before anything is
    posted when they are not the bytes the request named."""
    path = Path(entry["path"])
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError(f"{path.name} changed after it was requested")
    return path.name, data


@dataclass(frozen=True)
class Part:
    kind: str  # "text" or "file"
    text: str = ""
    name: str = ""
    data: bytes = b""
    size: int | None = None


@dataclass(frozen=True)
class _Notice:
    """A due notice in the shape of a send's action."""

    target: str
    payload: dict[str, Any]


class Sender:
    def __init__(self, wire: SlackWire, kernel, *, share_reads: int = 5, share_wait_s: float = 2.0):
        self.wire = wire
        self.kernel = kernel
        self.split = kernel.split_text
        self.share_reads = share_reads
        self.share_wait_s = share_wait_s
        self._logged: set[str] = set()

    # -- the performer -----------------------------------------------------

    async def perform(self, action, key: str) -> dict[str, Any]:
        target = self._target(action.target)
        p = action.payload
        parts = self._parts(p.get("text") or "", p.get("files") or [])
        try:
            channel = await self._channel(target)
            root = await self._root(target, channel, p.get("reply_to"))
        except Refused as e:
            raise self.kernel.Failed(e.error) from None
        except (NotConnected, RateLimited) as e:
            raise self.kernel.Failed(str(e)) from None
        return {"sent": await self._post(target, channel, root, parts, key, [None] * len(parts))}

    async def _post(self, target, channel, root, parts, key, found) -> list[dict[str, Any]]:
        """Post each part not in `found`, in order. The first post of a
        send (nothing found yet) fails as `Failed`; a later one as above."""
        out = list(found)
        for n, part in enumerate(parts):
            if out[n] is not None:
                continue
            first = not any(out)
            while True:
                try:
                    ts = await self._put(channel, root, part, key, n, len(parts))
                    out[n] = entry(target, ts, root)
                    break
                except RateLimited as e:
                    if first:
                        raise self.kernel.Failed(str(e)) from None
                    log.info("post %s of send %s: %s; waiting it out", n + 1, key, e)
                    await asyncio.sleep(e.seconds)
                except Refused as e:
                    if first:
                        raise self.kernel.Failed(e.error) from None
                    log.warning("send %s settles with %s posts: Slack refused the next (%s)", key, n, e.error)
                    return [o for o in out if o is not None]
                except NotConnected as e:
                    if first:
                        raise self.kernel.Failed(str(e)) from None
                    raise self.kernel.Unknown(f"post {n + 1} not sent: {e}") from None
                except InDoubt as e:
                    raise self.kernel.Unknown(f"post in doubt: {e}") from None
        return [o for o in out if o is not None]

    async def _put(self, channel: str, root: str | None, part: Part, key: str, n: int, parts: int) -> str:
        if part.kind == "text":
            meta = {
                "event_type": EVENT_TYPE,
                "event_payload": {"key": marker(key), "part": n, "parts": parts},
            }
            return await self.wire.post(channel, part.text, thread_ts=root, metadata=meta)
        file_id = await self.wire.upload(channel, part.name, part.data, thread_ts=root)
        for i in range(self.share_reads):
            if i:
                await asyncio.sleep(self.share_wait_s)
            found = await self.wire.shares(file_id, channel)
            if found:
                return found[0]
        raise InDoubt(f"file {part.name} uploaded, its share not shown")

    def _parts(self, text: str, files: list[dict]) -> list[Part]:
        out = [Part("text", text=t) for t in self.split(text)]
        for f in files:
            name, data = read_file(f)
            out.append(Part("file", name=name, data=data, size=len(data)))
        return out

    def _target(self, target: str) -> str:
        target = target.upper()
        team = target.partition("/")[0]
        if team != self.wire.team_id:
            raise self.kernel.Failed(f"{team} is not a connected workspace")
        return target

    async def _channel(self, target: str) -> str:
        ident = target.partition("/")[2]
        return await self.wire.open_im(ident) if inbound.is_dm(target) else ident

    async def _root(self, target: str, channel: str, reply_to: str | None) -> str | None:
        if not reply_to:
            return None
        async with self.kernel.conn() as conn:
            found = await self.kernel.root_of(conn, "slack", target, reply_to)
        if found:
            return found
        try:
            msgs, _ = await self.wire.replies(channel, reply_to, limit=1)
        except Refused:
            msgs = []
        for m in msgs:
            if m.get("ts") == reply_to:
                return m.get("thread_ts") or m["ts"]
        return reply_to

    # -- lookup ------------------------------------------------------------

    async def lookup(self, action, key: str, since: str) -> dict[str, Any] | None:
        p = action.payload
        if not self.wire.team_id:
            # `serve` reconciles before the bridge connects: nothing can be
            # read yet, so the effect stays in flight for the next wake.
            raise self.kernel.Unknown("not connected to Slack yet")
        try:
            target = self._target(action.target)
        except self.kernel.Failed:
            return None  # never posted: the target was refused at perform
        try:
            channel = await self._channel(target)
            root = await self._root(target, channel, p.get("reply_to"))
            expected = self._expected(p.get("text") or "", p.get("files") or [])
            found = await self._scan(target, channel, root, key, expected, since)
            if all(f is None for f in found):
                return None
            if all(f is not None for f in found):
                return {"sent": found}
            if None in found[: max(i for i, f in enumerate(found) if f is not None)]:
                raise self.kernel.Unknown(f"send {key}: a part is on screen after a missing one")
            try:
                parts = self._parts(p.get("text") or "", p.get("files") or [])
            except (OSError, ValueError) as e:
                log.warning("send %s cannot be finished: %s", key, e)
                return {"sent": [f for f in found if f is not None]}
            return {"sent": await self._post(target, channel, root, parts, key, found)}
        except WireError as e:
            raise self.kernel.Unknown(f"lookup could not read Slack: {e}") from None
        except self.kernel.Failed as e:
            raise self.kernel.Unknown(f"finishing send {key} failed: {e}") from None

    def _expected(self, text: str, files: list[dict]) -> list[Part]:
        out = [Part("text", text=t) for t in self.split(text)]
        for f in files:
            path = Path(f["path"])
            out.append(Part("file", name=path.name, size=path.stat().st_size if path.exists() else None))
        return out

    async def _scan(self, target, channel, root, key, expected, since) -> list[dict[str, Any] | None]:
        oldest = None
        if since:
            oldest = f"{(datetime.fromisoformat(since) - SKEW).timestamp():.6f}"
        posts = await self._own_posts(channel, root, oldest)
        async with self.kernel.conn() as conn:
            claimed = await self.kernel.claimed(conn, "slack", target)
        pool = sorted((m for m in posts if m["ts"] not in claimed), key=lambda m: m["ts"])
        marked = any(_marker(m) is not None for m in pool)
        mark = marker(key)
        found: list[dict[str, Any] | None] = []
        prev = ""
        for n, exp in enumerate(expected):
            if exp.kind == "text" and marked:
                matches = [m for m in pool if _marker(m) == (mark, n)]
            else:
                after = [m for m in pool if m["ts"] > prev and _thread(m) == root]
                matches = [m for m in after if _matches(m, exp)]
            if len(matches) > 1:
                raise self.kernel.Unknown(f"{len(matches)} posts in {target} match part {n + 1} of {key}")
            if matches:
                pool.remove(matches[0])
                prev = matches[0]["ts"]
                found.append(entry(target, matches[0]["ts"], root))
            else:
                found.append(None)
        return found

    async def _own_posts(self, channel: str, root: str | None, oldest: str | None) -> list[dict]:
        out, cursor = [], None
        while True:
            if root:
                page, cursor = await self.wire.replies(channel, root, oldest=oldest, cursor=cursor)
            else:
                page, cursor = await self.wire.history(channel, oldest=oldest, cursor=cursor)
            out += [m for m in page if m.get("user") == self.wire.user_id]
            if cursor is None:
                return out

    # -- notices -----------------------------------------------------------

    async def notice(self, item, outbox) -> None:
        """Send one due notice to the chat its row names, finishing what is
        already posted; mark it sent through the outbox. A notice that
        cannot be sent now is logged once and tried on the next wake."""
        action = _Notice(item.chat_id, {"text": item.text, "reply_to": item.reply_to})
        key = notice_key(item.notice_id)
        try:
            found = await self.lookup(action, key, item.at)
            sent = found["sent"] if found else (await self.perform(action, key))["sent"]
        except (self.kernel.Unknown, self.kernel.Failed, WireError) as e:
            if item.notice_id not in self._logged:
                self._logged.add(item.notice_id)
                log.warning("notice %s not sent: %s", item.notice_id, e)
            return
        await outbox.sent(item, sent)


def entry(chat: str, ts: str, root: str | None) -> dict[str, Any]:
    return {"channel": "slack", "chat_id": chat, "message_id": ts, "thread_ts": root}


def _marker(m: dict) -> tuple[str, int] | None:
    meta = m.get("metadata") or {}
    if meta.get("event_type") != EVENT_TYPE:
        return None
    payload = meta.get("event_payload") or {}
    return payload.get("key"), payload.get("part")


def _thread(m: dict) -> str | None:
    return inbound.root(m)


def _matches(m: dict, exp: Part) -> bool:
    if exp.kind == "text":
        return not m.get("files") and same(m.get("text") or "", exp.text)
    return any(
        f.get("name") == exp.name and (exp.size is None or f.get("size") == exp.size)
        for f in m.get("files") or []
    )
