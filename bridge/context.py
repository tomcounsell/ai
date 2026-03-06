"""Context building, conversation history, reply chains, and link summaries."""

import logging
import os
import re
import subprocess
from pathlib import Path

from telethon import TelegramClient

from tools.link_analysis import (
    extract_urls,
    get_metadata,
    summarize_url_content,
)
from tools.telegram_history import (
    get_link_by_url,
    get_recent_messages,
    store_link,
)

logger = logging.getLogger(__name__)

# =============================================================================
# Module-level config variables (set by telegram_bridge.py after loading)
# =============================================================================

CONFIG = {}
DEFAULTS = {}
DM_WHITELIST_CONFIG = {}
_BRIDGE_PROJECT_DIR = None


# =============================================================================
# Link Summarization Constants
# =============================================================================

# Link collectors - usernames whose links are automatically stored
# When these users share a URL, it gets saved with metadata
LINK_COLLECTORS = [
    name.strip().lower()
    for name in os.getenv("TELEGRAM_LINK_COLLECTORS", "").split(",")
    if name.strip()
]

# Link summarization settings
MAX_LINKS_PER_MESSAGE = 5  # Don't summarize more than 5 links per message
LINK_SUMMARY_CACHE_HOURS = 24  # Don't re-summarize URLs within 24 hours


# =============================================================================
# Status Question Patterns
# =============================================================================

# Patterns that indicate the user is asking about current work/status
STATUS_QUESTION_PATTERNS = [
    re.compile(r"what.*(?:working|doing|up to)", re.IGNORECASE),
    re.compile(r"what.*status", re.IGNORECASE),
    re.compile(r"what'?s.*going on", re.IGNORECASE),
    re.compile(r"how.*going", re.IGNORECASE),
    re.compile(r"any.*updates?", re.IGNORECASE),
    re.compile(r"what.*progress", re.IGNORECASE),
    re.compile(r"what.*been doing", re.IGNORECASE),
    re.compile(r"catch me up", re.IGNORECASE),
    re.compile(r"what.*happening", re.IGNORECASE),
]


# =============================================================================
# Tool Log Filtering
# =============================================================================


def filter_tool_logs(response: str) -> str:
    """
    Remove tool execution traces from response.

    Agent may include lines like "🛠️ exec: ls -la" in stdout.
    These are internal logs, not meant for the user.

    Returns:
        Filtered response, or empty string if only logs remain.
    """
    if not response:
        return ""

    lines = response.split("\n")
    filtered = []

    # Generic pattern: emoji followed by word and colon (catches most tool logs)
    generic_tool_pattern = re.compile(
        r"^[\U0001F300-\U0001F9FF\u2600-\u26FF\u2700-\u27BF]\s*\w+:", re.UNICODE
    )

    for line in lines:
        stripped = line.strip()

        # Skip empty lines in sequence (but keep some structure)
        if not stripped:
            # Only add blank line if last line wasn't blank
            if filtered and filtered[-1].strip():
                filtered.append(line)
            continue

        # Skip lines that match the generic tool log pattern
        if generic_tool_pattern.match(stripped):
            continue

        # If we got here, keep the line
        filtered.append(line)

    # Remove leading/trailing blank lines
    while filtered and not filtered[0].strip():
        filtered.pop(0)
    while filtered and not filtered[-1].strip():
        filtered.pop()

    return "\n".join(filtered)


# =============================================================================
# User Permissions
# =============================================================================


def get_user_permissions(sender_id: int | None) -> str:
    """Get the permission level for a whitelisted user.

    Returns:
        "full" - Can do anything (default)
        "qa_only" - Q&A only, no code changes allowed
    """
    if not sender_id or sender_id not in DM_WHITELIST_CONFIG:
        return "full"
    return DM_WHITELIST_CONFIG[sender_id].get("permissions", "full")


# =============================================================================
# Context Building
# =============================================================================


def build_context_prefix(
    project: dict | None, is_dm: bool, sender_id: int | None = None
) -> str:
    """Build project context to inject into agent prompt."""
    context_parts = []

    # Check user permissions - Q&A restrictions only apply to DMs
    permissions = get_user_permissions(sender_id)
    if permissions == "qa_only" and is_dm:
        context_parts.append(
            "RESTRICTION: This user has Q&A-only access. "
            "Do NOT make any code changes, file edits, git commits, or run destructive commands. "
            "Answer questions, explain code, and provide guidance only. "
            "If they ask you to make changes, politely explain you can only help with Q&A for them."
        )

    if not project:
        if is_dm:
            context_parts.append(
                "CONTEXT: Direct message to Valor (no specific project context)"
            )
        return "\n".join(context_parts) if context_parts else ""

    context_parts.append(
        f"PROJECT: {project.get('name', project.get('_key', 'Unknown'))}"
    )

    project_context = project.get("context", {})
    if project_context.get("description"):
        context_parts.append(f"FOCUS: {project_context['description']}")

    if project_context.get("tech_stack"):
        context_parts.append(f"TECH: {', '.join(project_context['tech_stack'])}")

    github = project.get("github", {})
    if github.get("repo"):
        context_parts.append(f"REPO: {github.get('org', '')}/{github['repo']}")

    return "\n".join(context_parts)


def is_status_question(text: str) -> bool:
    """Check if the message is asking about current work or status."""
    return any(pattern.search(text) for pattern in STATUS_QUESTION_PATTERNS)


def build_activity_context(working_dir: str | None = None) -> str:
    """
    Build context about recent project activity.

    This gives Valor awareness of recent work so status questions
    get informed answers instead of "nothing specific."
    """
    context_parts = []

    # Use project working directory or default
    cwd = working_dir or str(Path(__file__).parent.parent)

    # Recent git commits (last 24h)
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "--since=24 hours ago", "-5"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        if result.stdout.strip():
            context_parts.append(f"RECENT COMMITS (last 24h):\n{result.stdout.strip()}")
    except Exception as e:
        logger.debug(f"Could not get git log: {e}")

    # Current branch and status
    try:
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        if branch_result.stdout.strip():
            context_parts.append(f"CURRENT BRANCH: {branch_result.stdout.strip()}")

        status_result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd,
        )
        if status_result.stdout.strip():
            modified_files = status_result.stdout.strip().split("\n")[:5]
            context_parts.append("MODIFIED FILES:\n" + "\n".join(modified_files))
    except Exception as e:
        logger.debug(f"Could not get git status: {e}")

    # Recent plan docs
    plans_dir = Path(cwd) / "docs" / "plans"
    if plans_dir.exists():
        try:
            recent_plans = sorted(
                plans_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True
            )[:3]
            if recent_plans:
                plan_names = [p.stem for p in recent_plans]
                context_parts.append(f"ACTIVE PLANS: {', '.join(plan_names)}")
        except Exception as e:
            logger.debug(f"Could not get plan docs: {e}")

    if not context_parts:
        return ""

    return "ACTIVITY CONTEXT:\n" + "\n".join(context_parts)


def build_conversation_history(chat_id: str, limit: int = 5) -> str:
    """
    Build recent conversation history for context.

    NOTE: This is NOT called by default. The agent should use the valor-history
    CLI tool to fetch relevant history when context cues suggest prior messages
    may be relevant (e.g., "what do you think of these", "as I mentioned",
    references to recent discussions, etc.). For explicit threading, users
    can use Telegram's reply-to feature.

    Args:
        chat_id: Telegram chat ID (numeric, without prefix)
        limit: Number of recent messages to include

    Returns:
        Formatted conversation history string
    """
    result = get_recent_messages(str(chat_id), limit=limit)

    if "error" in result or not result.get("messages"):
        return ""

    messages = result["messages"]
    if not messages:
        return ""

    # Reverse to show oldest first (chronological order)
    messages = list(reversed(messages))

    history_lines = ["RECENT CONVERSATION:"]
    for msg in messages:
        sender = msg.get("sender", "Unknown")
        content = msg.get("content", "")

        # Filter tool logs from Valor's historical responses
        if sender == "Valor":
            content = filter_tool_logs(content)
            if not content:
                continue  # Skip if response was only tool logs

        # Truncate long messages
        if len(content) > 200:
            content = content[:200] + "..."
        history_lines.append(f"  {sender}: {content}")

    # If we only have the header, return empty
    if len(history_lines) <= 1:
        return ""

    return "\n".join(history_lines)


# =============================================================================
# Reply Chain Management
# =============================================================================


async def fetch_reply_chain(
    client: TelegramClient,
    chat_id: int,
    message_id: int,
    max_depth: int = 20,
) -> list[dict]:
    """
    Fetch the entire reply chain for a message.

    Walks backward through reply_to_msg_id references to build the full thread.
    Returns messages in chronological order (oldest first).

    Args:
        client: Telegram client
        chat_id: Chat ID to fetch from
        message_id: Starting message ID (the one being replied to)
        max_depth: Maximum number of messages to fetch in the chain

    Returns:
        List of message dicts with 'sender', 'content', 'message_id', 'date'
    """
    chain = []
    current_id = message_id
    seen_ids = set()

    for _ in range(max_depth):
        if current_id in seen_ids:
            break  # Avoid infinite loops
        seen_ids.add(current_id)

        try:
            msg = await client.get_messages(chat_id, ids=current_id)
            if not msg:
                break

            # Get sender info
            sender = await msg.get_sender()
            sender_name = getattr(sender, "first_name", "Unknown")

            # Check if this is our own message (Valor's response)
            if msg.out:
                sender_name = "Valor"

            chain.append(
                {
                    "sender": sender_name,
                    "content": msg.text or "[media]",
                    "message_id": msg.id,
                    "date": msg.date,
                }
            )

            # Move to parent message
            if msg.reply_to_msg_id:
                current_id = msg.reply_to_msg_id
            else:
                break  # No more parents

        except Exception as e:
            logger.debug(f"Could not fetch message {current_id} in reply chain: {e}")
            break

    # Reverse to get chronological order (oldest first)
    chain.reverse()
    return chain


def format_reply_chain(chain: list[dict]) -> str:
    """
    Format a reply chain for inclusion in agent context.

    Args:
        chain: List of message dicts from fetch_reply_chain()

    Returns:
        Formatted string showing the thread
    """
    if not chain:
        return ""

    lines = ["REPLY THREAD CONTEXT (oldest to newest):"]
    lines.append("-" * 40)

    for msg in chain:
        sender = msg["sender"]
        content = msg["content"]

        # Filter tool logs from Valor's messages
        if sender == "Valor":
            content = filter_tool_logs(content)
            if not content:
                continue

        # Valor's messages are already summarized — include in full
        # so resumed sessions have complete context of what was sent.
        # Other users' messages get truncated to keep context manageable.
        max_len = 2000 if sender == "Valor" else 500
        if len(content) > max_len:
            content = content[:max_len] + "..."

        # Format with timestamp if available
        date_str = ""
        if msg.get("date"):
            date_str = msg["date"].strftime(" [%H:%M]")

        lines.append(f"{sender}{date_str}: {content}")
        lines.append("")  # Blank line between messages

    lines.append("-" * 40)
    return "\n".join(lines)


# =============================================================================
# Link Summarization
# =============================================================================


async def get_link_summaries(
    text: str,
    sender: str,
    chat_id: str,
    message_id: int,
    timestamp,
) -> list[dict]:
    """
    Extract URLs from text and get summaries for each.

    Uses caching to avoid re-summarizing URLs we've seen recently.
    Applies rate limiting (max 5 links per message).

    Args:
        text: Message text containing URLs
        sender: Who shared the link
        chat_id: Telegram chat ID
        message_id: Telegram message ID
        timestamp: When the message was sent

    Returns:
        List of dicts with url, summary, title, and cached flag
    """
    # Extract URLs from message
    urls_result = extract_urls(text)
    urls = urls_result.get("urls", [])

    if not urls:
        return []

    # Rate limit: max 5 links per message
    urls = urls[:MAX_LINKS_PER_MESSAGE]
    if len(urls_result.get("urls", [])) > MAX_LINKS_PER_MESSAGE:
        logger.info(
            f"Rate limiting: only processing {MAX_LINKS_PER_MESSAGE} "
            f"of {len(urls_result.get('urls', []))} links"
        )

    summaries = []

    for url in urls:
        try:
            # Check cache: do we already have a summary for this URL?
            existing = get_link_by_url(url, max_age_hours=LINK_SUMMARY_CACHE_HOURS)

            if existing and existing.get("ai_summary"):
                # Use cached summary
                logger.debug(f"Using cached summary for: {url[:50]}...")
                summaries.append(
                    {
                        "url": url,
                        "summary": existing["ai_summary"],
                        "title": existing.get("title"),
                        "cached": True,
                    }
                )
                continue

            # Need to fetch new summary
            logger.info(f"Fetching summary for: {url[:50]}...")

            # Get metadata (title, description) synchronously
            metadata = get_metadata(url)
            title = metadata.get("title")
            description = metadata.get("description")
            final_url = metadata.get("final_url", url)

            # Get AI summary via Perplexity
            summary = await summarize_url_content(url)

            # Store the link with summary
            store_link(
                url=url,
                sender=sender,
                chat_id=chat_id,
                message_id=message_id,
                timestamp=timestamp,
                title=title,
                description=description,
                final_url=final_url,
                ai_summary=summary,
            )

            if summary:
                summaries.append(
                    {
                        "url": url,
                        "summary": summary,
                        "title": title,
                        "cached": False,
                    }
                )
                logger.info(f"Stored link with summary: {url[:50]}...")
            else:
                logger.warning(f"No summary generated for: {url[:50]}...")

        except Exception as e:
            logger.error(f"Error processing URL {url[:50]}...: {e}")
            continue

    return summaries


def format_link_summaries(summaries: list[dict]) -> str:
    """
    Format link summaries for inclusion in message context.

    Args:
        summaries: List of summary dicts from get_link_summaries()

    Returns:
        Formatted string to append to message
    """
    if not summaries:
        return ""

    parts = []
    for s in summaries:
        url = s["url"]
        summary = s["summary"]
        title = s.get("title", "")

        # Build the summary line
        if title:
            parts.append(f"[Link: {title}]\n{summary}")
        else:
            # Use a truncated URL as the header
            short_url = url[:60] + "..." if len(url) > 60 else url
            parts.append(f"[Link: {short_url}]\n{summary}")

    return "\n\n".join(parts)
