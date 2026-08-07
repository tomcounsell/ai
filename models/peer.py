"""Telegram peer parsing — stdlib-only, no ``popoto`` import.

Split out of ``models/room.py`` (which imports ``popoto`` at module load,
~2.9s wall) so callers that only need "is this chat_id a deliverable
Telegram peer" — notably ``tools/react_with_emoji.py``, invoked on every CLI
reaction including the normal happy path — do not pay that cost. See the
react-transport-derivation plan's Scope & Value critique finding.
"""

from __future__ import annotations


def _numeric_peer(chat_id) -> int | None:
    """Parse a chat_id as a Telegram peer int, or None if it is not numeric.

    ``lstrip("-")`` strips ALL leading hyphens, so "--5" passes ``isdigit``
    but is not an int — return None, never raise.
    """
    raw = str(chat_id).strip() if chat_id is not None else ""
    if not raw or not raw.lstrip("-").isdigit():
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def deliverable_telegram_peer(chat_id) -> bool:
    """True if ``chat_id`` parses to a nonzero Telegram peer int.

    Zero is not a valid Telegram peer (the relay's zero-guard drops it), so
    a ``chat_id="0"`` placeholder (chatless sessions) is not deliverable.
    """
    return _numeric_peer(chat_id) not in (None, 0)
