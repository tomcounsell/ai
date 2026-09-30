"""The local terminal surface. Plan 10, "The CLI"; seams §2.3; tech stack §13.

Deterministic ASCII, one block per card, numbered per session. The adapter
formats nothing: every value on a field line is already a string, rendered by
the kernel's constructors through `render_value` (architecture §7), so agent
prose can reach the person only under the `note` marker and never as a line
the kernel appears to have written.

The grammar has four forms and nothing else. The adapter relays a `Reply` and
never mints authority, which is why this file may sit outside the trust
boundary (tech stack §10): it imports `schemas/` and `ports/`, never `kernel/`.
"""

import re
from datetime import UTC, datetime
from typing import AsyncIterator

from schemas.approval import (
    CARD_FIELDS,
    Card,
    Conversation,
    Correction,
    Inbound,
    Reply,
    SpaceSwitch,
    Utterance,
    render_instant,
)
from schemas.ids import CardId, ConversationId

# The label column. Every field line is a key padded to this width and then
# the value, so a block reads as a table without the adapter measuring
# anything about the values.
LABEL = 14
NOTE_MARKER = "note (agent prose, capped): "
OPTION_GAP = "   "

# `#<n> <option> [text]`. The number is the adapter's, the option is one word,
# and everything after it is the free text, or None when there is none.
REPLY_LINE = re.compile(r"^#(\d+)\s+(\S+)(?:\s+(.*?))?\s*$")
SPACE_LINE = re.compile(r"^/space\s+(\S+)\s*$")
CORRECT_LINE = re.compile(r"^/correct\s*$")

WRONG_PROMPT = "What was wrong? "
RIGHT_PROMPT = "What is right? "


class CliSurface:
    """One terminal, one session, one conversation at a time.

    `reader` yields lines as bytes from `readline()`; `writer` takes bytes on
    `write()` and may offer `drain()`. An `asyncio` stream pair over the pty
    at boot, an in-memory pair under test.
    """

    def __init__(self, reader, writer, session_id: str) -> None:
        self._reader = reader
        self._writer = writer
        self._session_id = session_id
        self._conversation: Conversation | None = None
        self._numbers: dict[CardId, int] = {}
        self._cards: dict[int, Card] = {}

    # -- the port ----------------------------------------------------------

    async def open(self, conversation: Conversation) -> None:
        """A new conversation. The numbering is the session's, not the
        conversation's, so a switch does not reuse a number the person can
        still see above them in the scrollback."""
        self._conversation = conversation
        await self._say(
            f"--- conversation {conversation.id} in space {conversation.space}"
        )

    async def show(self, card: Card) -> None:
        await self._say(self.render(card))

    async def say(self, conversation_id: ConversationId, text: str) -> None:
        del conversation_id  # one terminal shows one conversation at a time
        await self._say(text)

    async def inbound(self) -> AsyncIterator[Inbound]:
        """Every line the person types, typed. Ends when the stream does."""
        while True:
            raw = await self._reader.readline()
            if not raw:
                return
            line = raw.decode(errors="replace").rstrip("\r\n")
            if not line.strip():
                continue
            yield await self._parse(line)

    # -- rendering ---------------------------------------------------------

    def render(self, card: Card) -> str:
        """The block the person reads. Fields in `CARD_FIELDS` order, then any
        optional key the card carries, then the options, the note if there is
        one, and how to reply."""
        number = self._number_for(card)
        revision = (
            "-" if card.contract_revision is None else str(card.contract_revision)
        )
        lines = [
            f"--- card #{number}  {card.kind}  space {card.space}  "
            f"regards {card.regards}  rev {revision}  "
            f"expires {render_instant(card.expires_at)}"
        ]
        ordered = CARD_FIELDS[card.kind]
        extra = sorted(key for key in card.fields if key not in ordered)
        for key in list(ordered) + extra:
            lines.append(_labelled(key, card.fields[key]))
        lines.append(
            _labelled("options", OPTION_GAP.join(_option(o) for o in card.options))
        )
        if card.note:
            lines.append(NOTE_MARKER + card.note)
        lines.append(_labelled("reply with", f"#{number} <option> [text]"))
        return "\n".join(lines)

    def number_of(self, card_id: CardId) -> int | None:
        """The number the person sees for a card, for a caller that needs to
        name one in prose."""
        return self._numbers.get(card_id)

    # -- internals ---------------------------------------------------------

    def _number_for(self, card: Card) -> int:
        if card.id not in self._numbers:
            number = len(self._numbers) + 1
            self._numbers[card.id] = number
            self._cards[number] = card
        return self._numbers[card.id]

    def _conversation_id(self) -> ConversationId:
        if self._conversation is None:
            raise ValueError("no conversation is open on this surface")
        return self._conversation.id

    async def _say(self, text: str) -> None:
        self._writer.write((text + "\n").encode())
        drain = getattr(self._writer, "drain", None)
        if drain is not None:
            await drain()

    async def _prompt(self, question: str) -> str:
        self._writer.write(question.encode())
        drain = getattr(self._writer, "drain", None)
        if drain is not None:
            await drain()
        raw = await self._reader.readline()
        return raw.decode(errors="replace").rstrip("\r\n")

    async def _parse(self, line: str) -> Inbound:
        now = datetime.now(UTC)
        reply = REPLY_LINE.match(line)
        if reply is not None:
            card = self._cards.get(int(reply.group(1)))
            if card is not None:
                text = reply.group(3)
                return Reply(
                    card_id=card.id,
                    option=reply.group(2),
                    edit=None,  # the CLI produces no edit at M0
                    raw_message=line,
                    session_id=self._session_id,
                    at=now,
                    text=text if text else None,
                )
            # A number with no card is a line the person typed, not a reply.
        switch = SPACE_LINE.match(line)
        if switch is not None:
            return SpaceSwitch(
                conversation_id=self._conversation_id(),
                to_space=switch.group(1),
                session_id=self._session_id,
                at=now,
            )
        if CORRECT_LINE.match(line) is not None:
            wrong = await self._prompt(WRONG_PROMPT)
            right = await self._prompt(RIGHT_PROMPT)
            return Correction(
                conversation_id=self._conversation_id(),
                space=self._conversation.space,
                what_was_wrong=wrong,
                what_is_right=right,
                regards=None,  # the CLI binds a correction to no card at M0
                raw_message=line,
                session_id=self._session_id,
                at=now,
            )
        return Utterance(
            conversation_id=self._conversation_id(),
            text=line,
            session_id=self._session_id,
            at=now,
        )


def _labelled(key: str, value: str) -> str:
    """The key padded to the label column, and always at least one space, so a
    key longer than the column still reads as a key and a value."""
    return key.ljust(LABEL - 1) + " " + value


def _option(option) -> str:
    label = f"[{option.key}] {option.label}"
    return label + " (recommended)" if option.recommended else label
