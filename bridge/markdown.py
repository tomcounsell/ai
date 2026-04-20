"""Telegram Markdown utilities.

Provides escape functions for Telegram's basic Markdown parse mode ('md').
Uses basic Markdown only — no MarkdownV2 (too fragile with escaping).

Supported formatting:
- *bold*
- `inline code`
- [text](url) links
"""

import re

# Characters that need escaping in Telegram basic Markdown
# Only underscore and backtick conflict with our usage
_ESCAPE_CHARS = re.compile(r"(?<!\\)([_])")


def escape_markdown(text: str) -> str:
    """Escape special characters for Telegram basic Markdown.

    Preserves intentional formatting:
    - *bold* markers are kept
    - `code` markers are kept
    - [text](url) links are kept
    - Underscores are escaped to prevent unwanted italic

    Args:
        text: Raw text to escape.

    Returns:
        Text safe for parse_mode='md'.
    """
    # Protect code blocks and links from escaping
    protected: list[tuple[str, str]] = []
    counter = 0

    def protect(match: re.Match) -> str:
        nonlocal counter
        placeholder = f"\x00PROTECTED{counter}\x00"
        protected.append((placeholder, match.group(0)))
        counter += 1
        return placeholder

    # Protect inline code
    result = re.sub(r"`[^`]+`", protect, text)
    # Protect links
    result = re.sub(r"\[[^\]]+\]\([^)]+\)", protect, result)

    # Escape underscores
    result = _ESCAPE_CHARS.sub(r"\\\1", result)

    # Restore protected sections
    for placeholder, original in protected:
        result = result.replace(placeholder, original)

    return result


def format_link(text: str, url: str) -> str:
    """Format a Markdown link for Telegram.

    Args:
        text: Link display text.
        url: Link URL.

    Returns:
        Markdown formatted link: [text](url)
    """
    return f"[{text}]({url})"


TELEGRAM_MAX_LENGTH = 4096


def _split_text(text: str, max_len: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    """Split text into chunks of at most max_len chars, breaking at newlines where possible."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Try to break at a newline within the last 20% of the chunk
        split_at = max_len
        newline_pos = text.rfind("\n", max_len - max_len // 5, max_len)
        if newline_pos > 0:
            split_at = newline_pos + 1
        chunks.append(text[:split_at])
        text = text[split_at:]
    return chunks


async def send_markdown(client, chat_id: int, text: str, reply_to: int | None = None):
    """Send a message with Markdown parse mode, falling back to plain text.

    Automatically splits messages exceeding Telegram's 4096-char limit.

    Args:
        client: Telethon TelegramClient
        chat_id: Target chat ID
        text: Message text (may contain markdown)
        reply_to: Optional message ID to reply to

    Returns:
        The last sent message object
    """
    import logging

    from telethon import errors

    log = logging.getLogger(__name__)
    chunks = _split_text(text)
    last = None
    for i, chunk in enumerate(chunks):
        current_reply_to = reply_to if i == 0 else None
        try:
            last = await client.send_message(
                chat_id, chunk, reply_to=current_reply_to, parse_mode="md"
            )
        except errors.BadRequestError:
            log.debug("Markdown send failed, falling back to plain text")
            last = await client.send_message(chat_id, chunk, reply_to=current_reply_to)
    return last
