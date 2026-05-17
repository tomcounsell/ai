"""Private-tag stripper for user-input preprocessing.

The subconscious memory system saves every Telegram message and Claude Code
prompt that clears the trivial-pattern + min-length filters. Sometimes a user
needs an inline opt-out for a specific phrase (an API key paste, a
confidential client name, a one-off speculative comment) without disabling
memory ingestion globally.

This module provides ``strip_private(text)`` -- a pure stdlib helper that
removes ``<private>...</private>`` regions from a string. It is invoked at
every persistent-write boundary that handles raw user input:

    bridge/telegram_bridge.py
        - store_message(content=...)               (TelegramMessage.content)
        - Memory.safe_save(content=...)            (subconscious memory)
        - logger.info(... text[:50] ...)           (bridge.log)
        - AgentSession.message_text                (via safe_text + reply chain)

    .claude/hooks/hook_utils/memory_bridge.py
        - ingest()    : Memory.safe_save() of user prompts
        - prefetch()  : BM25 query against past memories

The wrapped content stays visible to the live agent in the current turn -- the
user is masking *future recall*, not hiding from Claude in the moment. The
``config/personas/segments/private-tag.md`` segment instructs the agent to
treat wrapped content as do-not-quote-back so it does not re-enter Memory via
post-session extraction.

Design constraints:

- Single-level (no nesting). Supporting nested tags requires a real parser
  rather than a regex; the marginal value is zero.
- Case-sensitive. ``<private>`` only -- ``<PRIVATE>`` and ``<Private>`` are
  treated as literal text. Users learn the lowercase form.
- Idempotent. ``strip_private(strip_private(x)) == strip_private(x)``.
- Pure: no side effects (other than a DEBUG log on full-strip-to-empty for
  diagnosability).
- Forward-only. Existing records are unaffected; this is preprocessing for
  the next write, not a backfill or migration.

Usage:

    >>> from agent.private_tag import strip_private
    >>> strip_private("the key is <private>sk-abc123</private>, why?")
    'the key is, why?'
    >>> strip_private("nothing to strip")
    'nothing to strip'

See also: ``docs/features/subconscious-memory.md`` (the "Excluding content
with `<private>` tags" section), and tests at
``tests/unit/test_private_tag.py``.
"""

from __future__ import annotations

import logging
import re

_PRIVATE_TAG_RE = re.compile(r"<private>(.*?)</private>", re.DOTALL)
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")

logger = logging.getLogger(__name__)


def strip_private(text: str) -> str:
    """Remove all ``<private>...</private>`` regions from ``text``.

    Non-greedy, single-level (no nesting), case-sensitive. Unmatched
    ``<private>`` openers without a matching ``</private>`` are left as
    literal text. Idempotent.

    Collapses the multi-space residue left when a tagged region sat between
    two whitespace-bearing tokens, but ONLY when at least one tag was
    actually stripped (sdlc-1179 C2 fix). On no-tag input, the input is
    returned bit-identically -- no whitespace changes whatsoever -- so the
    function is a true no-op for the overwhelming majority of messages.

    Newlines are never touched, so multi-line prompt structure is preserved.

    Emits a DEBUG log line when stripping reduces the content to
    empty / whitespace-only (sdlc-1179 N3): operationally this means the
    downstream length filter in ``ingest()`` will drop the record, and a
    user diagnosing "why didn't my message land?" can grep
    ``private_tag.strip_to_empty`` in the logs.

    Args:
        text: Arbitrary user input. Non-string and ``None`` inputs return
            the empty string defensively.

    Returns:
        The input with all ``<private>...</private>`` regions removed.
    """
    if not isinstance(text, str):
        return ""
    if not text:
        return text
    stripped, n = _PRIVATE_TAG_RE.subn("", text)
    if n == 0:
        # No tags matched -- return input unchanged (no whitespace collapse).
        return text
    stripped = _MULTI_SPACE_RE.sub(" ", stripped)
    if not stripped.strip():
        logger.debug(
            "private_tag.strip_to_empty original_len=%d tags_stripped=%d",
            len(text),
            n,
        )
    return stripped
