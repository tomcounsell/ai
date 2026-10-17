"""The facts each channel's bridge port differs in, one row per channel.

`core/bridge.py`, `core/intake.py`, `core/notices.py` and
`core/workspace.py` read this table instead of each asking
`if channel == ...`. It imports nothing from `core`, so any of them may
import it.

A row says:

- `verified`: whether every record on the channel is verified, because
  the platform authenticates the sender (Telegram's MTProto, Slack's
  socket, the local bridge's token); an email record is not.
- `replies`: whether Tom replies to a message there, so an exact `stop`
  binds and a near miss is answered "reply `stop`".
- `threaded`: whether a reply names its thread's root, not the message it
  answers (Slack), so binding falls back to the newest of Valor's posts
  in the thread.
- `reacts`: whether Valor can put a reaction on a message there (Telegram,
  Slack), so a message bound to a task gets one (`core/reactions.py`).
- `chat`: the pattern of the id in a project's `chats` entry
  (`<channel>:<id>`), or None when a project cannot list one (the local
  chat has one chat, Tom's).
- `case`: how a `chats` id is normalised before it is stored and
  compared: kept as written, lowercased, or uppercased.
- `hint`: the entry's shape, for a refusal.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Channel:
    name: str
    verified: bool
    replies: bool
    threaded: bool
    reacts: bool
    chat: str | None
    case: str  # "keep", "lower", or "upper"
    hint: str

    def normal(self, ident: str) -> str:
        """`ident` as this channel stores and compares a chat id."""
        if self.case == "lower":
            return ident.lower()
        if self.case == "upper":
            return ident.upper()
        return ident

    def listable(self, ident: str) -> bool:
        """Whether `ident` is a chat id a project's `chats` may name."""
        return self.chat is not None and re.fullmatch(self.chat, self.normal(ident)) is not None


CHANNELS: dict[str, Channel] = {
    "telegram": Channel(
        name="telegram",
        verified=True,
        replies=True,
        threaded=False,
        reacts=True,
        chat=r".+",
        case="keep",
        hint="telegram:<chat id>",
    ),
    "email": Channel(
        name="email",
        verified=False,
        replies=False,
        threaded=False,
        reacts=False,
        chat=r".+",
        case="lower",
        hint="email:<sender address>",
    ),
    "local": Channel(
        name="local",
        verified=True,
        replies=True,
        threaded=False,
        reacts=False,
        chat=None,
        case="keep",
        hint="",
    ),
    # A Slack id is `<team>/<channel>` for a channel and `<team>/<user>`
    # for a DM, both as Slack writes them: capitals and digits.
    "slack": Channel(
        name="slack",
        verified=True,
        replies=True,
        threaded=True,
        reacts=True,
        chat=r"[A-Z0-9]+/[A-Z0-9]+",
        case="upper",
        hint="slack:<team id>/<channel or user id>",
    ),
}


def get(name: str) -> Channel | None:
    return CHANNELS.get(name)


def hints() -> str:
    """Every listable entry shape, for a refusal."""
    return " or ".join(c.hint for c in CHANNELS.values() if c.chat is not None)
