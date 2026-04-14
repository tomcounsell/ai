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
# Reply Thread Context Header (canonical constant — single source of truth)
# =============================================================================

# Canonical header used by `format_reply_chain` and pre-hydration paths.
# Importing from a single location lets deferred enrichment do an idempotency
# check (skip the fetch if this header is already present in message_text).
# Any change to this string must also update `format_reply_chain` below.
REPLY_THREAD_CONTEXT_HEADER = "REPLY THREAD CONTEXT"


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
# Implicit-Context (Deictic) Patterns
# =============================================================================

# Patterns that indicate the message references prior context without using
# Telegram's native reply-to feature. Narrow and high-precision -- false
# positives cost one agent turn (a valor-telegram read) at most.
#
# Keep this list short. Expansion requires empirical data on missed cases.
DEICTIC_CONTEXT_PATTERNS = [
    re.compile(r"\b(the|that)\s+(bug|issue|ticket|pr|pull request)\b", re.IGNORECASE),
    re.compile(r"\bstill\s+(broken|failing|crashing)\b", re.IGNORECASE),
    re.compile(r"\bwe\s+(fixed|shipped|merged|resolved)\b", re.IGNORECASE),
    re.compile(r"\blast\s+time\b", re.IGNORECASE),
    re.compile(r"\bas\s+i\s+(mentioned|said)\b", re.IGNORECASE),
    re.compile(r"\bdid\s+we\s+", re.IGNORECASE),
    re.compile(r"\bwhat\s+about\s+(that|the)\b", re.IGNORECASE),
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
# Context Building
# =============================================================================


def build_context_prefix(
    project: dict | None, session_type: str | None, sender_id: int | None = None
) -> str:
    """Build project context to inject into agent prompt."""
    from config.enums import SessionType

    context_parts = []

    # Teammate sessions get uniform read-only access - no per-user permission levels
    if session_type == SessionType.TEAMMATE:
        context_parts.append(
            "RESTRICTION: This user has read-only Teammate access. "
            "Do NOT make any code changes, file edits, git commits, or run destructive commands. "
            "Answer questions, explain code, and provide guidance only. "
            "If they ask you to make changes, politely explain you can only help with "
            "informational queries for them."
        )

    if not project:
        return "\n".join(context_parts) if context_parts else ""

    context_parts.append(f"PROJECT: {project.get('name', project.get('_key', 'Unknown'))}")

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


def references_prior_context(text: str) -> bool:
    """Return True if the message text appears to reference prior conversation.

    Used by the bridge handler to decide whether to prepend a
    `[CONTEXT DIRECTIVE]` block to messages that lack a Telegram reply-to
    but still reference earlier state (deictic pronouns, status phrasing,
    or back-references like "the bug", "we fixed", "last time").

    The check composes `STATUS_QUESTION_PATTERNS` (already in use for status
    intent) with a small, narrow list of deictic patterns in
    `DEICTIC_CONTEXT_PATTERNS`. Any single pattern match wins (OR).

    Input contract:
    - `None`, non-string, empty, or whitespace-only input returns `False`.
    - Never raises on unexpected input.

    Args:
        text: The user message text.

    Returns:
        True if any pattern matches; False otherwise.

    Examples:
        >>> references_prior_context("did we get that fixed?")
        True
        >>> references_prior_context("the bug is still broken")
        True
        >>> references_prior_context("hello")
        False
        >>> references_prior_context("")
        False
        >>> references_prior_context(None)
        False
    """
    if not text or not isinstance(text, str):
        return False
    if not text.strip():
        return False
    for pattern in STATUS_QUESTION_PATTERNS:
        if pattern.search(text):
            return True
    for pattern in DEICTIC_CONTEXT_PATTERNS:
        if pattern.search(text):
            return True
    return False


def matched_context_patterns(text: str) -> list[str]:
    """Return the list of pattern source-strings that matched `text`.

    Companion to `references_prior_context` used for structured audit logging
    so we can aggregate false-positive rates post-ship. Returns an empty list
    for non-string/empty/whitespace-only input.
    """
    if not text or not isinstance(text, str) or not text.strip():
        return []
    matched: list[str] = []
    for pattern in STATUS_QUESTION_PATTERNS:
        if pattern.search(text):
            matched.append(pattern.pattern)
    for pattern in DEICTIC_CONTEXT_PATTERNS:
        if pattern.search(text):
            matched.append(pattern.pattern)
    return matched


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

    This is invoked on-demand. When the bridge handler detects that a message
    references prior context (via `references_prior_context`), it prepends a
    `[CONTEXT DIRECTIVE]` to the prompt that instructs the agent to reach for
    the `valor-telegram` CLI (or this helper) before answering. The directive
    is tool-order guidance, not a forced call -- the agent may early-exit if
    auto-recalled memory already covers the reference.

    For explicit threading, users can still use Telegram's reply-to feature;
    that path is handled by `fetch_reply_chain` / `format_reply_chain` and is
    hydrated into the prompt by the bridge handler directly.

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

    lines = [f"{REPLY_THREAD_CONTEXT_HEADER} (oldest to newest):"]
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


async def _get_cached_root(chat_id: int, msg_id: int) -> int | None:
    """Read the authoritative root message ID from Redis for a given message.

    Key schema: session_root:{chat_id}:{msg_id}

    Returns:
        The cached root message ID as an int, or None on cache miss or error.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        _r = POPOTO_REDIS_DB

        key = f"session_root:{chat_id}:{msg_id}"
        value = _r.get(key)
        if value is not None:
            return int(value)
        return None
    except Exception as exc:
        logger.debug(f"[session-root] _get_cached_root({chat_id}, {msg_id}) error: {exc}")
        return None


async def _set_cached_root(chat_id: int, msg_id: int, root_id: int) -> None:
    """Persist the authoritative root message ID to Redis for a given message.

    Uses SET NX EX 604800 (7-day TTL, first writer wins) so concurrent
    callers cannot overwrite each other's resolved root.

    Fails silently on any Redis error.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        _r = POPOTO_REDIS_DB

        key = f"session_root:{chat_id}:{msg_id}"
        # NX = only set if key does not already exist; EX = TTL in seconds (7 days)
        _r.set(key, str(root_id), nx=True, ex=604800)
    except Exception as exc:
        logger.debug(
            f"[session-root] _set_cached_root({chat_id}, {msg_id}, {root_id}) error: {exc}"
        )


async def resolve_root_session_id(
    client: TelegramClient,
    chat_id: int,
    reply_to_msg_id: int,
    project_key: str,
) -> str:
    """Resolve the canonical session_id for a reply-to message chain.

    Walks backward through the reply chain to find the oldest human (non-Valor)
    message, then derives the session_id from that message's ID. This ensures
    that all replies in a conversation thread — even replies to Valor's responses
    — map to the same canonical session_id as the original human message.

    Resolution strategy (in order of preference):
    0. Authoritative Redis cache: check session_root:{chat_id}:{msg_id} first.
       Returns immediately if found — deterministic, concurrent-safe.
    1. Cache-first walk: query TelegramMessage.query.filter(chat_id, message_id)
       to traverse the chain without Telegram API calls.
    2. API fallback: if any cache lookup misses, call fetch_reply_chain() to
       walk the chain via the Telegram API.
    3. Final fallback: on any exception, return a session_id derived directly
       from reply_to_msg_id (preserves previous behavior).

    After any successful resolution (steps 0–2), the result is persisted to
    the authoritative Redis cache so future lookups are deterministic.

    Args:
        client: Telegram client (used for API fallback).
        chat_id: Telegram chat ID.
        reply_to_msg_id: message_id of the message being replied to.
        project_key: Project key for session_id formatting.

    Returns:
        Canonical session_id string, e.g. "tg_{project_key}_{chat_id}_{root_msg_id}".
    """
    fallback = f"tg_{project_key}_{chat_id}_{reply_to_msg_id}"

    try:
        # ── Step 0: Authoritative Redis cache (deterministic, concurrent-safe) ─
        cached_root = await _get_cached_root(chat_id, reply_to_msg_id)
        if cached_root is not None:
            session_id = f"tg_{project_key}_{chat_id}_{cached_root}"
            logger.debug(
                f"[session-root] authoritative cache hit for msg_id={reply_to_msg_id} → "
                f"{cached_root} (session={session_id})"
            )
            return session_id

        # ── Step 1: Cache-first walk via TelegramMessage ──────────────────────
        root_msg_id = await _cache_walk_root(chat_id, reply_to_msg_id)
        if root_msg_id is not None:
            session_id = f"tg_{project_key}_{chat_id}_{root_msg_id}"
            logger.debug(
                f"[session-root] cache walk resolved {reply_to_msg_id} → "
                f"{root_msg_id} (session={session_id})"
            )
            await _set_cached_root(chat_id, reply_to_msg_id, root_msg_id)
            return session_id

        # ── Step 2: API fallback via fetch_reply_chain ────────────────────────
        logger.debug(
            f"[session-root] cache miss for msg_id={reply_to_msg_id}, "
            f"falling back to Telegram API chain walk"
        )
        chain = await fetch_reply_chain(client, chat_id, reply_to_msg_id)
        # Chain is chronological (oldest first). Find the first non-Valor message.
        for entry in chain:
            if entry.get("sender") != "Valor":
                root_msg_id = entry["message_id"]
                session_id = f"tg_{project_key}_{chat_id}_{root_msg_id}"
                logger.debug(
                    f"[session-root] API chain walk resolved {reply_to_msg_id} → "
                    f"{root_msg_id} (session={session_id})"
                )
                await _set_cached_root(chat_id, reply_to_msg_id, root_msg_id)
                return session_id

        # Chain is empty or all Valor messages — use fallback
        logger.debug(
            f"[session-root] chain walk found no human root for msg_id={reply_to_msg_id}, "
            f"using fallback session_id"
        )
        return fallback

    except Exception as exc:
        # ── Step 3: Final fallback ─────────────────────────────────────────────
        logger.debug(
            f"[session-root] exception during root resolution for msg_id={reply_to_msg_id}: "
            f"{exc}. Using fallback session_id."
        )
        return fallback


async def _cache_walk_root(chat_id: int, start_msg_id: int, max_hops: int = 20) -> int | None:
    """Walk the reply chain using TelegramMessage cache records.

    Starting from start_msg_id, follow reply_to_msg_id links through cached
    TelegramMessage records until we find the oldest non-Valor message.

    Returns:
        The message_id of the root human message, or None if any cache lookup
        misses (caller should fall back to the Telegram API).
    """
    from models.telegram import TelegramMessage

    current_id = start_msg_id
    seen = set()

    for _ in range(max_hops):
        if current_id in seen:
            break
        seen.add(current_id)

        try:
            records = list(
                TelegramMessage.query.filter(chat_id=str(chat_id), message_id=current_id)
            )
        except Exception:
            # Filter failure means the index isn't usable — signal cache miss
            return None

        if not records:
            # Cache miss — caller should fall back to API
            return None

        record = records[0]

        # If this message was sent by someone other than Valor, it's the root
        sender = getattr(record, "sender", None)
        if sender != "Valor":
            return current_id

        # This is a Valor message — follow its reply_to_msg_id if present
        parent_id = getattr(record, "reply_to_msg_id", None)
        if not parent_id:
            # Valor message with no parent — use it as the best available root
            return current_id

        current_id = parent_id

    # Hit max_hops — return whatever we landed on
    return current_id


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
