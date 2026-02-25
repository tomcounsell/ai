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


async def send_markdown(client, chat_id: int, text: str, reply_to: int | None = None):
    """Send a message with Markdown parse mode, falling back to plain text.

    Args:
        client: Telethon TelegramClient
        chat_id: Target chat ID
        text: Message text (may contain markdown)
        reply_to: Optional message ID to reply to

    Returns:
        The sent message object
    """
    import logging

    try:
        return await client.send_message(
            chat_id, text, reply_to=reply_to, parse_mode="md"
        )
    except Exception:
        # Markdown parse failed — strip markdown and send plain
        logging.getLogger(__name__).debug(
            "Markdown send failed, falling back to plain text"
        )
        return await client.send_message(chat_id, text, reply_to=reply_to)
