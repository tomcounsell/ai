"""The bridge's only contact with Telegram.

`Wire` is the narrow interface the rest of the bridge uses; `TelethonWire`
implements it over Telethon and is the only code that imports it. The
tests' emulator implements the same interface over a local server.

Every failure of a request becomes one of the errors below, so the
performer can tell a definite refusal from a send in doubt:

- `FloodWait`: Telegram asked us to wait; nothing was sent.
- `Refused`: Telegram answered with an error; nothing was sent.
- `NotConnected`: no request was written.
- `InDoubt`: the connection was lost after a request was written and
  before its answer; Telegram may hold the message.
- `DuplicateRandomId`: Telegram already holds a message with this
  `random_id`.

Telethon is built with `flood_sleep_threshold=0`, `request_retries=1`,
`connection_retries=1`, `auto_reconnect=False`, and `catch_up=False`, so it
repeats no request on its own and the bridge owns reconnect;
`sequential_updates=True`, so one message's handler finishes before the
next one's starts.
"""

from __future__ import annotations

import asyncio
import mimetypes
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class WireError(Exception):
    """A request to Telegram did not complete."""


class FloodWait(WireError):
    def __init__(self, seconds: float):
        super().__init__(f"flood wait of {seconds:.0f} s")
        self.seconds = seconds


class Refused(WireError):
    """Telegram answered with an error."""


class NotConnected(WireError):
    """No request was written: the wire is not connected."""


class InDoubt(WireError):
    """The connection was lost after the request was written."""


class DuplicateRandomId(WireError):
    """Telegram already holds a message sent with this `random_id`."""


@dataclass(frozen=True)
class Media:
    kind: str  # photo, voice, audio, video, or document
    name: str
    mime: str
    size: int


@dataclass(frozen=True)
class Msg:
    """One Telegram message, as the bridge needs it."""

    chat_id: int
    id: int
    date: datetime
    sender_id: int | None
    sender_name: str = ""
    text: str = ""
    chat_kind: str = "group"  # "dm" or "group"
    out: bool = False
    service: bool = False
    reply_to: int | None = None  # reply_to_msg_id as Telegram gives it
    top_id: int | None = None  # reply_to_top_id
    forum_topic: bool = False
    grouped_id: int | None = None
    media: Media | None = None
    raw: Any = field(default=None, compare=False, repr=False)


Handler = Callable[[Msg], Awaitable[None]]


class Wire(Protocol):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    def connected(self) -> bool: ...
    async def disconnected(self) -> None:
        """Returns when the connection is lost."""

    def on_message(self, handler: Handler) -> None: ...
    async def history(self, chat_id: int, *, offset_id: int = 0, limit: int = 100) -> list[Msg]:
        """Newest first; with `offset_id`, only ids below it."""

    async def own(self, chat_id: int, *, after_id: int) -> list[Msg]:
        """The account's own messages in the chat with ids above
        `after_id`, newest first."""

    async def get(self, chat_id: int, ids: list[int]) -> list[Msg | None]: ...
    async def send_text(
        self, chat_id: int, text: str, *, random_id: int, reply_to: int | None, topic_id: int | None
    ) -> int: ...
    async def send_file(
        self,
        chat_id: int,
        data: bytes,
        name: str,
        *,
        random_id: int,
        reply_to: int | None,
        topic_id: int | None,
    ) -> int: ...
    async def download(self, msg: Msg) -> bytes: ...
    async def mark_read(self, chat_id: int, max_id: int) -> None: ...


# Telegram's test data centre, for the live suite on test accounts.
TEST_DC = (2, "149.154.167.40", 443)


class TelethonWire:
    """`Wire` over Telethon, on the session file in the kernel key
    directory."""

    def __init__(self, session: str | Path, api_id: int, api_hash: str, *, test_dc: bool = False):
        from telethon import TelegramClient

        self.client = TelegramClient(
            str(session),
            api_id,
            api_hash,
            sequential_updates=True,
            flood_sleep_threshold=0,
            request_retries=1,
            connection_retries=1,
            auto_reconnect=False,
            catch_up=False,
        )
        if test_dc:
            self.client.session.set_dc(*TEST_DC)
        self._handlers: list[Handler] = []

    # -- connection --------------------------------------------------------

    async def connect(self) -> None:
        from telethon import errors

        try:
            await self.client.connect()
            authorized = await self.client.is_user_authorized()
        except errors.FloodWaitError as e:
            raise FloodWait(e.seconds) from None
        except (ConnectionError, OSError, TimeoutError) as e:
            raise NotConnected(type(e).__name__) from None
        if not authorized:
            await self.client.disconnect()
            raise Refused("the session is not signed in; run `python -m bridges.telegram login`")

    async def disconnect(self) -> None:
        await self.client.disconnect()

    def connected(self) -> bool:
        return self.client.is_connected()

    async def disconnected(self) -> None:
        await self.client.disconnected

    def on_message(self, handler: Handler) -> None:
        from telethon import events

        async def wrapped(event):
            await handler(await self._msg(event.message))

        self.client.add_event_handler(wrapped, events.NewMessage())

    # -- reading -----------------------------------------------------------

    async def history(self, chat_id: int, *, offset_id: int = 0, limit: int = 100) -> list[Msg]:
        found = await self._call(self.client.get_messages(chat_id, limit=limit, offset_id=offset_id))
        return [await self._msg(m) for m in found if m is not None]

    async def own(self, chat_id: int, *, after_id: int) -> list[Msg]:
        out = []

        async def scan():
            async for m in self.client.iter_messages(chat_id, from_user="me", min_id=after_id):
                out.append(await self._msg(m))

        await self._call(scan())
        return out

    async def get(self, chat_id: int, ids: list[int]) -> list[Msg | None]:
        found = await self._call(self.client.get_messages(chat_id, ids=ids))
        return [await self._msg(m) if m is not None else None for m in found]

    async def download(self, msg: Msg) -> bytes:
        return await self._call(self.client.download_media(msg.raw, file=bytes))

    async def mark_read(self, chat_id: int, max_id: int) -> None:
        await self._call(self.client.send_read_acknowledge(chat_id, max_id=max_id))

    # -- sending -----------------------------------------------------------

    async def send_text(
        self, chat_id: int, text: str, *, random_id: int, reply_to: int | None, topic_id: int | None
    ) -> int:
        from telethon.tl.functions.messages import SendMessageRequest

        peer = await self._call(self.client.get_input_entity(chat_id))
        request = SendMessageRequest(
            peer, text, no_webpage=True, reply_to=_reply(reply_to, topic_id), random_id=random_id
        )
        return _sent_id(await self._send(request), random_id)

    async def send_file(
        self,
        chat_id: int,
        data: bytes,
        name: str,
        *,
        random_id: int,
        reply_to: int | None,
        topic_id: int | None,
    ) -> int:
        from telethon.tl.functions.messages import SendMediaRequest
        from telethon.tl.types import DocumentAttributeFilename, InputMediaUploadedDocument

        peer = await self._call(self.client.get_input_entity(chat_id))
        # The upload sends no message, so a failure there is definite.
        uploaded = await self._call(self.client.upload_file(data, file_name=name))
        media = InputMediaUploadedDocument(
            file=uploaded,
            mime_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
            attributes=[DocumentAttributeFilename(name)],
            force_file=True,
        )
        request = SendMediaRequest(
            peer, media, message="", reply_to=_reply(reply_to, topic_id), random_id=random_id
        )
        return _sent_id(await self._send(request), random_id)

    # -- mapping -----------------------------------------------------------

    async def _call(self, awaitable):
        """A request whose failure is definite: nothing is sent by it."""
        from telethon import errors

        if not self.client.is_connected():
            awaitable.close()
            raise NotConnected("not connected")
        try:
            return await awaitable
        except errors.FloodWaitError as e:
            raise FloodWait(e.seconds) from None
        except errors.RPCError as e:
            raise Refused(f"{type(e).__name__}: {e.message}") from None
        except (ConnectionError, OSError, TimeoutError) as e:
            raise NotConnected(type(e).__name__) from None

    async def _send(self, request):
        """A request that may leave a message on Telegram."""
        from telethon import errors

        if not self.client.is_connected():
            raise NotConnected("not connected")
        try:
            return await self.client(request)
        except errors.FloodWaitError as e:
            raise FloodWait(e.seconds) from None
        except errors.RandomIdDuplicateError:
            raise DuplicateRandomId("random_id already used") from None
        except errors.RPCError as e:
            raise Refused(f"{type(e).__name__}: {e.message}") from None
        except (ConnectionError, OSError, TimeoutError, asyncio.IncompleteReadError) as e:
            raise InDoubt(type(e).__name__) from None

    async def _msg(self, m) -> Msg:
        from telethon import utils
        from telethon.tl import types

        sender = None
        try:
            sender = await m.get_sender()
        except Exception:  # noqa: BLE001 - a name is cosmetic; the id is what binds
            sender = None
        header = getattr(m, "reply_to", None)
        file = getattr(m, "file", None)
        media = None
        if getattr(m, "media", None) is not None and file is not None:
            kind = (
                "photo"
                if m.photo
                else "voice"
                if m.voice
                else "audio"
                if m.audio
                else "video"
                if m.video
                else "document"
            )
            media = Media(kind, file.name or "", file.mime_type or "", file.size or 0)
        date = m.date if m.date.tzinfo else m.date.replace(tzinfo=UTC)
        return Msg(
            chat_id=utils.get_peer_id(m.peer_id),
            id=m.id,
            date=date,
            sender_id=m.sender_id,
            sender_name=_name(sender),
            text=getattr(m, "message", "") or "",
            chat_kind="dm" if isinstance(m.peer_id, types.PeerUser) else "group",
            out=bool(m.out),
            service=isinstance(m, types.MessageService),
            reply_to=getattr(header, "reply_to_msg_id", None),
            top_id=getattr(header, "reply_to_top_id", None),
            forum_topic=bool(getattr(header, "forum_topic", False)),
            grouped_id=getattr(m, "grouped_id", None),
            media=media,
            raw=m,
        )


def _name(sender) -> str:
    if sender is None:
        return ""
    title = getattr(sender, "title", None)
    if title:
        return title
    return " ".join(p for p in (getattr(sender, "first_name", ""), getattr(sender, "last_name", "")) if p)


def _reply(reply_to: int | None, topic_id: int | None):
    from telethon.tl.types import InputReplyToMessage

    if reply_to:
        return InputReplyToMessage(reply_to_msg_id=reply_to, top_msg_id=topic_id)
    if topic_id:
        return InputReplyToMessage(reply_to_msg_id=topic_id)
    return None


def _sent_id(result, random_id: int) -> int:
    from telethon.tl import types

    if isinstance(result, types.UpdateShortSentMessage):
        return result.id
    updates = getattr(result, "updates", [])
    for u in updates:
        if isinstance(u, types.UpdateMessageID) and u.random_id == random_id:
            return u.id
    for u in updates:
        if isinstance(u, types.UpdateNewMessage | types.UpdateNewChannelMessage):
            return u.message.id
    raise InDoubt("Telegram's answer named no message id")
