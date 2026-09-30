"""The approval surface. Seams §2.3; tech stack §13.

An adapter renders cards and relays replies over a session the kernel owns.
It never constructs an `ApprovalRecord`; `kernel/approvals.py` mints one from
a `Reply`. On the CLI, `session_id` is the terminal session the kernel
attributed to the person's user at start (`approvals.open_terminal_session`).
"""

from typing import AsyncIterator, Protocol

from schemas.approval import Card, Conversation, Inbound
from schemas.ids import ConversationId


class ApprovalSurface(Protocol):
    async def open(self, conversation: Conversation) -> None: ...

    async def show(self, card: Card) -> None: ...

    async def say(self, conversation_id: ConversationId, text: str) -> None: ...

    def inbound(self) -> AsyncIterator[Inbound]: ...
