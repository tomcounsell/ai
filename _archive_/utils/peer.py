"""Telegram peer parsing — stdlib-only, no ``popoto`` import.

Split out of ``models/room.py`` (which imports ``popoto`` at module load,
~2.9s wall) so callers that only need "is this chat_id a deliverable
Telegram peer" — notably ``tools/react_with_emoji.py``, invoked on every CLI
reaction including the normal happy path — do not pay that cost. See the
react-transport-derivation plan's Scope & Value critique finding.

Lives under ``utils/``, not ``models/``: a first attempt placed this module
at ``models/peer.py``, but importing *any* submodule of the ``models``
package runs ``models/__init__.py`` first (Python always executes a parent
package's ``__init__`` before a submodule), and that ``__init__`` imports
every Popoto model, i.e. ``popoto`` and ``redis``. Being stdlib-only inside
the file bought nothing while the file lived inside the heavy package —
measured ~110x regression (0.02s -> ~2.2-3.5s) on the CLI happy path. See
PR #2651 review. ``utils/__init__.py`` is empty, so `import utils.peer`
alone does not import anything beyond this module.
"""

from __future__ import annotations


def numeric_peer(chat_id) -> int | None:
    """Parse a chat_id as a Telegram peer int, or None if it is not numeric.

    ``lstrip("-")`` strips ALL leading hyphens, so "--5" passes ``isdigit``
    but is not an int — return None, never raise.

    Stricter than a bare ``int()`` on purpose, and the difference is the point:
    ``int()`` accepts ``"+5"``, ``5.9``, and ``True``, none of which is a
    Telegram peer. Callers that need the *reason* a chat_id is undeliverable
    (unparseable vs. the zero placeholder) read it off this return value rather
    than re-parsing — a second parse is how the two answers drift apart.
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
    return numeric_peer(chat_id) not in (None, 0)
