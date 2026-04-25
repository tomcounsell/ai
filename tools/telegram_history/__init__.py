"""
Telegram History Tool

Search Telegram conversation history with relevance scoring.
Store and manage links shared in Telegram chats.

Backend: Redis/Popoto (replaced SQLite as of 2026-02-24).
All data is stored in Redis via Popoto models (TelegramMessage, Link, Chat).
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class TelegramHistoryError(Exception):
    """Telegram history operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


# =============================================================================
# Chat resolution (issue #1163)
# =============================================================================


@dataclass(frozen=True)
class ChatCandidate:
    """A candidate chat match from name resolution.

    Plain dataclass that is decoupled from the Popoto `Chat` model — callers
    that format ambiguity errors are not coupled to model-field churn.

    Attributes:
        chat_id: The chat identifier (string, may be numeric with '-' prefix).
        chat_name: The stored chat name as registered by the bridge.
        last_activity_ts: Unix timestamp of last chat activity (`Chat.updated_at`),
            or None if never updated. Used for recency ordering.
    """

    chat_id: str
    chat_name: str
    last_activity_ts: float | None


class AmbiguousChatError(Exception):
    """Raised when `resolve_chat_id` finds >1 candidate and `strict=True`.

    Carries the full `list[ChatCandidate]` so CLI callers can render a
    "did you mean" style disambiguation message. Candidates are ordered
    by `last_activity_ts` desc with `None` sorting last (i.e., most recently
    active chat first), with `chat_id` as the deterministic tiebreaker.

    Under the default (non-strict) path this exception is NOT raised —
    `resolve_chat_id` returns the most-recent candidate's `chat_id` and
    emits a `logger.warning` listing all candidates instead. The exception
    is also raised unconditionally (regardless of `strict`) when the
    defensive invariant in `resolve_chat_id` fails — i.e., the chosen
    candidate is not the one with the maximum `last_activity_ts`. That
    case is a fail-loud guard against a broken sort.
    """

    def __init__(self, candidates: list["ChatCandidate"]):
        self.candidates = candidates
        names = ", ".join(f"{c.chat_name!r} ({c.chat_id})" for c in candidates)
        super().__init__(f"Ambiguous chat name matched {len(candidates)} candidates: {names}")


# Conservative punctuation set stripped from both sides during name comparison.
# `_` IS stripped (Q2 resolved — see plan): slug/channel naming conventions
# make underscore-vs-space collisions rare enough that the ambiguity detector
# is the appropriate safety net. Keep emoji / non-ASCII intact.
_NORMALIZE_STRIP_CHARS = ":-|_"


def _normalize_chat_name(s: str) -> str:
    """Normalize a chat name for comparison.

    Rules:
      - lowercase
      - collapse any run of whitespace to a single space
      - strip leading/trailing `:`, `-`, `|`, `_` characters
      - preserve emoji and non-ASCII text

    Examples:
      "PM: PsyOptimal"   -> "pm psyoptimal"
      "PM PsyOptimal"    -> "pm psyoptimal"
      "dev_valor"        -> "dev valor"  (note: underscore stripped internally too)
      ""                 -> ""
      "   "              -> ""
      ":::"              -> ""

    Empty/whitespace/all-punctuation inputs normalize to "".
    """
    if not s:
        return ""
    # Lowercase first so downstream comparisons are case-insensitive.
    lowered = s.lower()
    # Replace the conservative punctuation set with a space so we collapse
    # alongside whitespace in one pass. Example: "pm: psyoptimal" -> "pm  psyoptimal".
    for ch in _NORMALIZE_STRIP_CHARS:
        lowered = lowered.replace(ch, " ")
    # Collapse whitespace runs.
    collapsed = " ".join(lowered.split())
    return collapsed


def _chat_to_candidate(chat) -> ChatCandidate:
    """Project a Popoto `Chat` instance to a `ChatCandidate` dataclass.

    Defensive about missing/None attributes — returns a candidate even when
    the chat has never been updated.
    """
    raw_ts = getattr(chat, "updated_at", None)
    try:
        ts = float(raw_ts) if raw_ts is not None else None
    except (TypeError, ValueError):
        ts = None
    return ChatCandidate(
        chat_id=str(chat.chat_id),
        chat_name=str(chat.chat_name or ""),
        last_activity_ts=ts,
    )


def _sort_candidates(candidates: list[ChatCandidate]) -> list[ChatCandidate]:
    """Sort candidates by `last_activity_ts` desc, deterministic on ties.

    Primary key: `last_activity_ts` desc, with `None` sorting last (a chat
    that has never been updated never outranks one with a real timestamp).

    Deterministic tiebreak (per the hotfixed plan): when two candidates
    share `last_activity_ts`, break on `chat_id` ascending — lexicographic
    string compare, so a numeric-prefix chat_id is still stable. This
    makes the warn-and-pick-most-recent default path reproducible: the
    same inputs always pick the same candidate, and tests don't have to
    race sleep-jitter timings to get a deterministic winner.
    """
    return sorted(
        candidates,
        key=lambda c: (
            0 if c.last_activity_ts is not None else 1,
            -(c.last_activity_ts or 0.0),
            c.chat_id,
        ),
    )


def resolve_chat_candidates(chat_name: str) -> list[ChatCandidate]:
    """Return all chat candidates matching `chat_name`, ordered by recency.

    Runs a 3-stage cascade (exact → case-insensitive exact → normalized
    substring) and collects ALL hits at each stage. Only advances to the
    next stage on zero hits. Candidates are projected to `ChatCandidate`
    at collection time — Popoto model instances never escape this function.

    Ordering: `last_activity_ts` desc, with `None` sorting last.

    Args:
        chat_name: The name to resolve. Empty/whitespace-only returns [].

    Returns:
        List of `ChatCandidate`s. Empty if no match.

    Failure mode: Redis / Popoto errors are logged and an empty list is
    returned. No bare-except swallowing (see plan Task 3 exception-handling
    note).
    """
    if not chat_name or not chat_name.strip():
        return []

    from models.chat import Chat

    try:
        import popoto as _popoto_pkg
        import redis as _redis_pkg  # local import — the module is optional at import time

        # Stage 1: exact match on the stored chat_name (uses Popoto KeyField index).
        exact = list(Chat.query.filter(chat_name=chat_name))
        if exact:
            return _sort_candidates([_chat_to_candidate(c) for c in exact])

        # For stages 2 and 3 we need the full chat list (there are hundreds,
        # not thousands — acceptable at this scale; see plan Rabbit Holes).
        all_chats = list(Chat.query.all())
        if not all_chats:
            return []

        chat_name_lower = chat_name.lower()
        normalized_query = _normalize_chat_name(chat_name)

        # Stage 2: case-insensitive exact match.
        ci_hits = [c for c in all_chats if c.chat_name and c.chat_name.lower() == chat_name_lower]
        if ci_hits:
            return _sort_candidates([_chat_to_candidate(c) for c in ci_hits])

        # Stage 3: normalized substring match (covers `PM PsyOptimal` → `PM: PsyOptimal`).
        if not normalized_query:
            return []
        substring_hits = [
            c
            for c in all_chats
            if c.chat_name and normalized_query in _normalize_chat_name(c.chat_name)
        ]
        if substring_hits:
            return _sort_candidates([_chat_to_candidate(c) for c in substring_hits])

        return []
    except (_redis_pkg.RedisError, _popoto_pkg.ModelException, _popoto_pkg.QueryException) as e:
        logger.warning("resolve_chat_candidates failed: %s", e)
        return []


# =============================================================================
# Shared Utilities
# =============================================================================


def _score_relevance(
    query: str,
    content: str,
    timestamp: float,
    max_age_days: int = 30,
) -> float:
    """Compute a relevance score for a message against a query.

    Score components:
    - 0.5 bonus for exact phrase match (case-insensitive)
    - 0.2 per matching word
    - up to 0.3 recency bonus (newer = higher)

    Args:
        query: The search query string.
        content: The message content to score against.
        timestamp: Unix timestamp of the message.
        max_age_days: Window for recency scoring.

    Returns:
        Float relevance score (higher = more relevant).
    """
    query_lower = query.lower()
    content_lower = content.lower() if content else ""
    query_words = set(query_lower.split())

    score = 0.0

    # Exact phrase match
    if query_lower in content_lower:
        score += 0.5

    # Word match bonus
    content_words = set(content_lower.split())
    matching_words = query_words & content_words
    score += len(matching_words) * 0.2

    # Recency bonus
    now = time.time()
    age_seconds = now - timestamp
    age_days = age_seconds / 86400
    if age_days < max_age_days:
        recency_bonus = max(0.0, (max_age_days - age_days) / max_age_days) * 0.3
        score += recency_bonus

    return score


def _ts_to_iso(ts: float | None) -> str | None:
    """Convert unix timestamp to ISO 8601 string, or None."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts).isoformat()
    except (ValueError, OSError):
        return None


def _parse_ts(ts_val) -> float:
    """Parse a timestamp value to unix float.

    Uses ``bridge.utc.to_unix_ts`` to treat naive datetimes as UTC so age
    calculations stay correct on non-UTC hosts.
    """
    from bridge.utc import to_unix_ts

    if ts_val is None:
        return time.time()
    result = to_unix_ts(ts_val)
    return result if result is not None else time.time()


# =============================================================================
# Message Storage Functions
# =============================================================================


def store_message(
    chat_id: str,
    content: str,
    sender: str | None = None,
    message_id: int | None = None,
    timestamp: datetime | None = None,
    message_type: str = "text",
    project_key: str | None = None,
    has_media: bool = False,
    media_type: str | None = None,
    youtube_urls: str | None = None,
    non_youtube_urls: str | None = None,
    reply_to_msg_id: int | None = None,
    classification_type: str | None = None,
    classification_confidence: float | None = None,
    db_path=None,  # Ignored — kept for API compatibility
) -> dict:
    """Store a message in Redis via TelegramMessage.

    Args:
        chat_id: Telegram chat ID.
        content: Message content (stored in full, no truncation).
        sender: Message sender.
        message_id: Telegram message ID.
        timestamp: Message timestamp (defaults to now).
        message_type: Type of message (text, photo, etc.).
        project_key: Project key for direct project association.
        has_media: Whether the message has media attached.
        media_type: Type of media (photo, voice, document, etc.).
        youtube_urls: JSON-encoded list of (url, video_id) tuples.
        non_youtube_urls: JSON-encoded list of URL strings.
        reply_to_msg_id: Telegram message ID of the parent reply.
        classification_type: Message classification type.
        classification_confidence: Classification confidence score.
        db_path: Ignored — kept for backward-compatibility signature.

    Returns:
        dict with storage result: {"stored": True, "id": msg_id, "chat_id": chat_id}
    """
    from models.telegram import TelegramMessage
    from tools.field_utils import log_large_field

    ts = _parse_ts(timestamp)
    direction = "out" if sender and sender.lower() == "valor" else "in"
    log_large_field("TelegramMessage.content", content)

    try:
        msg = TelegramMessage.create(
            chat_id=str(chat_id),
            message_id=message_id,
            direction=direction,
            sender=sender or "unknown",
            content=content or "",
            timestamp=ts,
            message_type=message_type,
            project_key=project_key,
            has_media=has_media,
            media_type=media_type,
            youtube_urls=youtube_urls,
            non_youtube_urls=non_youtube_urls,
            reply_to_msg_id=reply_to_msg_id,
            classification_type=classification_type,
            classification_confidence=classification_confidence,
        )
        return {
            "stored": True,
            "id": msg.msg_id,
            "chat_id": chat_id,
        }
    except Exception as e:
        return {"error": str(e)}


def search_history(
    query: str,
    chat_id: str,
    max_results: int = 5,
    max_age_days: int = 30,
    db_path=None,  # Ignored — kept for API compatibility
) -> dict:
    """Search Telegram conversation history.

    Fetches messages by chat_id within the time window, then filters
    content in Python and ranks by relevance score.

    Args:
        query: Search query.
        chat_id: Telegram chat ID.
        max_results: Maximum results (default: 5).
        max_age_days: Time window in days (default: 30).
        db_path: Ignored — kept for backward-compatibility signature.

    Returns:
        dict with:
            - results: Matching messages with relevance scores.
            - total_matches: Number of matches found.
    """
    if not query or not query.strip():
        return {"error": "Query cannot be empty"}

    if not chat_id:
        return {"error": "Chat ID is required"}

    max_results = max(1, min(100, max_results))

    from models.telegram import TelegramMessage

    cutoff = time.time() - (max_age_days * 86400)
    query_lower = query.lower()

    try:
        messages = TelegramMessage.query.filter(chat_id=str(chat_id))
    except Exception as e:
        return {"error": str(e)}

    results = []
    for msg in messages:
        ts = msg.timestamp or 0.0
        if ts < cutoff:
            continue
        content = msg.content or ""
        if query_lower not in content.lower():
            continue

        score = _score_relevance(query, content, ts, max_age_days)
        results.append(
            {
                "id": msg.msg_id,
                "message_id": msg.message_id,
                "sender": msg.sender,
                "content": content,
                "timestamp": _ts_to_iso(ts),
                "message_type": msg.message_type,
                "relevance_score": round(score, 3),
            }
        )

    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    results = results[:max_results]

    return {
        "query": query,
        "chat_id": chat_id,
        "results": results,
        "total_matches": len(results),
        "time_window_days": max_age_days,
    }


def get_recent_messages(
    chat_id: str,
    limit: int = 10,
    db_path=None,  # Ignored — kept for API compatibility
) -> dict:
    """Get recent messages from a chat.

    Args:
        chat_id: Telegram chat ID.
        limit: Maximum messages to return.
        db_path: Ignored — kept for backward-compatibility signature.

    Returns:
        dict with recent messages.
    """
    if not chat_id:
        return {"error": "Chat ID is required"}

    from models.telegram import TelegramMessage

    try:
        messages = TelegramMessage.query.filter(chat_id=str(chat_id))
    except Exception as e:
        return {"error": str(e)}

    # Sort by timestamp descending, take limit
    msgs_with_ts = [(msg, msg.timestamp or 0.0) for msg in messages]
    msgs_with_ts.sort(key=lambda x: x[1], reverse=True)
    msgs_with_ts = msgs_with_ts[:limit]

    result_msgs = [
        {
            "id": msg.msg_id,
            "message_id": msg.message_id,
            "sender": msg.sender,
            "content": msg.content,
            "timestamp": _ts_to_iso(ts),
            "message_type": msg.message_type,
        }
        for msg, ts in msgs_with_ts
    ]

    return {
        "chat_id": chat_id,
        "messages": result_msgs,
        "count": len(result_msgs),
    }


def get_chat_stats(
    chat_id: str,
    db_path=None,  # Ignored — kept for API compatibility
) -> dict:
    """Get statistics for a chat.

    Args:
        chat_id: Telegram chat ID.
        db_path: Ignored — kept for backward-compatibility signature.

    Returns:
        dict with chat statistics.
    """
    from models.telegram import TelegramMessage

    try:
        messages = list(TelegramMessage.query.filter(chat_id=str(chat_id)))
    except Exception as e:
        return {"error": str(e)}

    if not messages:
        return {
            "chat_id": chat_id,
            "total_messages": 0,
            "unique_senders": 0,
            "first_message": None,
            "last_message": None,
        }

    timestamps = [msg.timestamp or 0.0 for msg in messages]
    senders = {msg.sender for msg in messages if msg.sender}

    return {
        "chat_id": chat_id,
        "total_messages": len(messages),
        "unique_senders": len(senders),
        "first_message": _ts_to_iso(min(timestamps)),
        "last_message": _ts_to_iso(max(timestamps)),
    }


# =============================================================================
# Link Storage Functions
# =============================================================================


def store_link(
    url: str,
    sender: str,
    chat_id: str,
    message_id: int | None = None,
    timestamp: datetime | None = None,
    title: str | None = None,
    description: str | None = None,
    final_url: str | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
    ai_summary: str | None = None,
    project_key: str | None = None,
    db_path=None,  # Ignored — kept for API compatibility
) -> dict:
    """Store a link in Redis via the Link model.

    Uses get-or-create pattern: looks for existing link by url+chat_id
    and updates metadata if found, creates new if not.

    Args:
        url: The URL to store.
        sender: Who shared the link.
        chat_id: Telegram chat ID.
        message_id: Telegram message ID.
        timestamp: When the link was shared.
        title: Page title (optional).
        description: Page description (optional).
        final_url: Final URL after redirects.
        tags: List of tags for categorization.
        notes: User notes about the link.
        ai_summary: AI-generated summary.
        db_path: Ignored — kept for backward-compatibility signature.

    Returns:
        dict with storage result.
    """
    from models.link import Link

    ts = _parse_ts(timestamp)

    # Extract domain from URL
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        domain = domain or None
    except Exception:
        domain = None

    try:
        # Get-or-create: look for existing link by url+chat_id
        existing = list(Link.query.filter(url=url, chat_id=str(chat_id)))
        if existing:
            # Update existing link (prefer new non-None values)
            link = existing[0]
            if title is not None:
                link.title = title
            if description is not None:
                link.description = description
            if final_url is not None:
                link.final_url = final_url
            if ai_summary is not None:
                link.ai_summary = ai_summary
            if message_id is not None:
                link.message_id = message_id
            link.save()
            return {
                "stored": True,
                "id": link.link_id,
                "url": url,
                "domain": domain,
            }

        # Create new link
        link = Link.create(
            url=url,
            chat_id=str(chat_id),
            project_key=project_key,
            message_id=message_id,
            domain=domain,
            sender=sender,
            status="unread",
            timestamp=ts,
            final_url=final_url,
            title=title,
            description=description,
            tags=tags or [],
            notes=notes,
            ai_summary=ai_summary,
        )
        return {
            "stored": True,
            "id": link.link_id,
            "url": url,
            "domain": domain,
        }
    except Exception as e:
        return {"error": str(e)}


def search_links(
    query: str | None = None,
    domain: str | None = None,
    sender: str | None = None,
    status: str | None = None,
    limit: int = 20,
    db_path=None,  # Ignored — kept for API compatibility
) -> dict:
    """Search stored links with various filters.

    Args:
        query: Text search in URL, title, description, notes, ai_summary.
        domain: Filter by domain (exact match).
        sender: Filter by sender (case-insensitive contains).
        status: Filter by status (unread, read, archived).
        limit: Maximum results.
        db_path: Ignored — kept for backward-compatibility signature.

    Returns:
        dict with matching links.
    """
    from models.link import Link

    try:
        # Build filter kwargs for KeyFields (exact match)
        filter_kwargs = {}
        if domain:
            filter_kwargs["domain"] = domain.lower()
        if sender:
            filter_kwargs["sender"] = sender
        if status:
            filter_kwargs["status"] = status

        if filter_kwargs:
            all_links = list(Link.query.filter(**filter_kwargs))
        else:
            all_links = list(Link.query.all())
    except Exception as e:
        return {"error": str(e)}

    # Apply text query filtering in Python
    if query:
        query_lower = query.lower()
        filtered = []
        for link in all_links:
            searchable = " ".join(
                filter(
                    None,
                    [
                        link.url or "",
                        link.title or "",
                        link.description or "",
                        link.notes or "",
                        link.ai_summary or "",
                    ],
                )
            ).lower()
            if query_lower in searchable:
                filtered.append(link)
        all_links = filtered

    # Apply case-insensitive sender filter if not already filtered via KeyField
    if sender and not filter_kwargs.get("sender"):
        sender_lower = sender.lower()
        all_links = [lnk for lnk in all_links if lnk.sender and sender_lower in lnk.sender.lower()]

    # Sort by timestamp descending
    all_links.sort(key=lambda x: x.timestamp or 0.0, reverse=True)
    all_links = all_links[:limit]

    links_out = [
        {
            "id": lnk.link_id,
            "url": lnk.url,
            "final_url": lnk.final_url,
            "title": lnk.title,
            "description": lnk.description,
            "domain": lnk.domain,
            "sender": lnk.sender,
            "chat_id": lnk.chat_id,
            "message_id": lnk.message_id,
            "timestamp": _ts_to_iso(lnk.timestamp),
            "tags": lnk.tags or [],
            "notes": lnk.notes,
            "status": lnk.status,
            "ai_summary": lnk.ai_summary,
        }
        for lnk in all_links
    ]

    return {
        "links": links_out,
        "count": len(links_out),
        "query": query,
        "filters": {
            "domain": domain,
            "sender": sender,
            "status": status,
        },
    }


def list_links(
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
    db_path=None,  # Ignored — kept for API compatibility
) -> dict:
    """List recent links with pagination.

    Args:
        limit: Maximum results.
        offset: Skip first N results.
        status: Filter by status.
        db_path: Ignored — kept for backward-compatibility signature.

    Returns:
        dict with links and pagination info.
    """
    from models.link import Link

    try:
        if status:
            all_links = list(Link.query.filter(status=status))
        else:
            all_links = list(Link.query.all())
    except Exception as e:
        return {"error": str(e)}

    # Sort by timestamp descending
    all_links.sort(key=lambda x: x.timestamp or 0.0, reverse=True)

    total = len(all_links)
    page = all_links[offset : offset + limit]

    links_out = [
        {
            "id": lnk.link_id,
            "url": lnk.url,
            "final_url": lnk.final_url,
            "title": lnk.title,
            "description": lnk.description,
            "domain": lnk.domain,
            "sender": lnk.sender,
            "chat_id": lnk.chat_id,
            "message_id": lnk.message_id,
            "timestamp": _ts_to_iso(lnk.timestamp),
            "tags": lnk.tags or [],
            "notes": lnk.notes,
            "status": lnk.status,
            "ai_summary": lnk.ai_summary,
        }
        for lnk in page
    ]

    return {
        "links": links_out,
        "count": len(links_out),
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < total,
    }


def get_link_by_url(
    url: str,
    max_age_hours: int | None = None,
    db_path=None,  # Ignored — kept for API compatibility
) -> dict | None:
    """Get an existing link record by URL.

    Used for caching - check if we already have a summary for this URL.

    Args:
        url: URL to look up.
        max_age_hours: Only return if summary is newer than this (None = any age).
        db_path: Ignored — kept for backward-compatibility signature.

    Returns:
        dict with link data if found and has ai_summary, None otherwise.
    """
    from models.link import Link

    try:
        matches = list(Link.query.filter(url=url))
    except Exception:
        return None

    if not matches:
        return None

    # Filter to links with ai_summary
    with_summary = [lnk for lnk in matches if lnk.ai_summary]
    if not with_summary:
        return None

    # Sort by timestamp descending, take most recent
    with_summary.sort(key=lambda x: x.timestamp or 0.0, reverse=True)
    link = with_summary[0]

    # Apply max_age_hours filter
    if max_age_hours is not None:
        cutoff = time.time() - (max_age_hours * 3600)
        if (link.timestamp or 0.0) < cutoff:
            return None

    return {
        "id": link.link_id,
        "url": link.url,
        "final_url": link.final_url,
        "title": link.title,
        "description": link.description,
        "domain": link.domain,
        "sender": link.sender,
        "chat_id": link.chat_id,
        "message_id": link.message_id,
        "timestamp": _ts_to_iso(link.timestamp),
        "tags": link.tags or [],
        "notes": link.notes,
        "status": link.status,
        "ai_summary": link.ai_summary,
    }


def update_link(
    link_id: str,
    status: str | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
    ai_summary: str | None = None,
    db_path=None,  # Ignored — kept for API compatibility
) -> dict:
    """Update a stored link by link_id.

    For status changes (KeyField), uses delete-and-recreate pattern.
    For non-KeyField updates (notes, ai_summary), uses direct .save().

    Args:
        link_id: ID (link_id) of the link to update.
        status: New status (unread, read, archived).
        tags: New tags (replaces existing).
        notes: New notes (replaces existing).
        ai_summary: New AI summary.
        db_path: Ignored — kept for backward-compatibility signature.

    Returns:
        dict with update result.
    """
    from models.link import Link

    if not any(v is not None for v in [status, tags, notes, ai_summary]):
        return {"error": "No fields to update"}

    try:
        # Find link by link_id
        all_links = list(Link.query.all())
        target = next((lnk for lnk in all_links if str(lnk.link_id) == str(link_id)), None)

        if target is None:
            return {"error": f"Link {link_id} not found"}

        fields_updated = 0

        if status is not None and status != target.status:
            # Status is a KeyField — delete and recreate
            old_data = {
                "url": target.url,
                "chat_id": target.chat_id,
                "message_id": target.message_id,
                "domain": target.domain,
                "sender": target.sender,
                "timestamp": target.timestamp,
                "final_url": target.final_url,
                "title": target.title,
                "description": target.description,
                "tags": target.tags,
                "notes": notes if notes is not None else target.notes,
                "ai_summary": (ai_summary if ai_summary is not None else target.ai_summary),
            }
            target.delete()
            Link.create(status=status, **old_data)
            fields_updated += 1
            return {
                "updated": True,
                "id": link_id,
                "fields_updated": fields_updated
                + sum(1 for v in [tags, notes, ai_summary] if v is not None),
            }

        # Non-KeyField updates — direct save
        if notes is not None:
            target.notes = notes
            fields_updated += 1
        if ai_summary is not None:
            target.ai_summary = ai_summary
            fields_updated += 1
        if tags is not None:
            target.tags = tags
            fields_updated += 1

        if fields_updated:
            target.save()

        return {
            "updated": True,
            "id": link_id,
            "fields_updated": fields_updated,
        }
    except Exception as e:
        return {"error": str(e)}


def get_link_stats(
    db_path=None,  # Ignored — kept for API compatibility
) -> dict:
    """Get statistics about stored links.

    Args:
        db_path: Ignored — kept for backward-compatibility signature.

    Returns:
        dict with link statistics.
    """
    from models.link import Link

    try:
        all_links = list(Link.query.all())
    except Exception as e:
        return {"error": str(e)}

    if not all_links:
        return {
            "total_links": 0,
            "unique_domains": 0,
            "unique_senders": 0,
            "by_status": {"unread": 0, "read": 0, "archived": 0},
            "first_link": None,
            "last_link": None,
            "top_domains": [],
        }

    domains = [lnk.domain for lnk in all_links if lnk.domain]
    senders = {lnk.sender for lnk in all_links if lnk.sender}
    timestamps = [lnk.timestamp for lnk in all_links if lnk.timestamp]

    status_counts = {"unread": 0, "read": 0, "archived": 0}
    domain_counts: dict[str, int] = {}
    for lnk in all_links:
        s = lnk.status or "unread"
        if s in status_counts:
            status_counts[s] += 1
        if lnk.domain:
            domain_counts[lnk.domain] = domain_counts.get(lnk.domain, 0) + 1

    top_domains = sorted(
        [{"domain": d, "count": c} for d, c in domain_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    return {
        "total_links": len(all_links),
        "unique_domains": len(set(domains)),
        "unique_senders": len(senders),
        "by_status": status_counts,
        "first_link": _ts_to_iso(min(timestamps)) if timestamps else None,
        "last_link": _ts_to_iso(max(timestamps)) if timestamps else None,
        "top_domains": top_domains,
    }


# =============================================================================
# Chat Mapping Functions
# =============================================================================


def register_chat(
    chat_id: str,
    chat_name: str,
    chat_type: str | None = None,
    project_key: str | None = None,
    db_path=None,  # Ignored — kept for API compatibility
) -> dict:
    """Register or update a chat mapping in Redis via Chat model.

    Called by the bridge when messages are received to maintain
    the chat_id -> chat_name mapping.

    Args:
        chat_id: Telegram chat ID.
        chat_name: Human-readable chat name/title.
        chat_type: Type of chat (private, group, supergroup, channel).
        project_key: Project key for direct project association.
        db_path: Ignored — kept for backward-compatibility signature.

    Returns:
        dict with registration result.
    """
    from models.chat import Chat

    try:
        existing = list(Chat.query.filter(chat_id=str(chat_id)))
        if existing:
            chat = existing[0]
            # chat_name is a KeyField — delete-and-recreate if changed
            if chat.chat_name != chat_name or (chat_type and chat.chat_type != chat_type):
                old_type = chat_type or chat.chat_type
                chat.delete()
                Chat.create(
                    chat_id=str(chat_id),
                    chat_name=chat_name,
                    chat_type=old_type,
                    project_key=project_key or getattr(chat, "project_key", None),
                    updated_at=time.time(),
                )
            else:
                if project_key and chat.project_key != project_key:
                    chat.project_key = project_key
                chat.updated_at = time.time()
                chat.save()
        else:
            Chat.create(
                chat_id=str(chat_id),
                chat_name=chat_name,
                chat_type=chat_type,
                project_key=project_key,
                updated_at=time.time(),
            )

        return {
            "registered": True,
            "chat_id": chat_id,
            "chat_name": chat_name,
        }
    except Exception as e:
        return {"error": str(e)}


def list_chats(
    db_path=None,  # Ignored — kept for API compatibility
) -> dict:
    """List all known chats with their message counts.

    Returns:
        dict with list of chats and their stats.
    """
    from models.chat import Chat
    from models.telegram import TelegramMessage

    try:
        all_chats = list(Chat.query.all())
    except Exception as e:
        return {"error": str(e)}

    chats_out = []
    for chat in all_chats:
        # Compute message count
        try:
            msgs = list(TelegramMessage.query.filter(chat_id=str(chat.chat_id)))
            msg_count = len(msgs)
            last_ts = max((m.timestamp or 0.0 for m in msgs), default=None)
            last_msg = _ts_to_iso(last_ts) if last_ts else None
        except Exception:
            msg_count = 0
            last_msg = None

        chats_out.append(
            {
                "chat_id": chat.chat_id,
                "chat_name": chat.chat_name,
                "chat_type": chat.chat_type,
                "updated_at": _ts_to_iso(chat.updated_at),
                "message_count": msg_count,
                "last_message": last_msg,
            }
        )

    # Sort by last_message desc
    chats_out.sort(key=lambda x: x["last_message"] or "", reverse=True)

    return {
        "chats": chats_out,
        "count": len(chats_out),
    }


def resolve_chat_id(
    chat_name: str,
    db_path=None,  # Ignored — kept for API compatibility
    *,
    strict: bool = False,
) -> str | None:
    """Resolve a chat name to its chat_id.

    Runs the 3-stage cascade via `resolve_chat_candidates` and returns a
    single `chat_id` under every path except a failed defensive invariant.

    Default (non-strict) path:
      - Zero candidates → returns None.
      - One candidate   → returns that candidate's `chat_id`.
      - Multiple        → returns the most-recent candidate's `chat_id` AND
                          emits `logger.warning` listing the chosen candidate
                          plus all alternatives (chat_id, chat_name, last-age).
                          Callers do not need to catch anything.

    Strict path (`strict=True`):
      - Zero / one → same as default.
      - Multiple   → raises `AmbiguousChatError(candidates)`.

    Defensive invariant (both paths):
      After candidate selection, the chosen candidate MUST have the maximum
      `last_activity_ts` in the returned set. If this ever fails (a sort bug
      or race), `AmbiguousChatError` is raised unconditionally regardless of
      `strict`. This is fail-loud, not fail-silent-with-wrong-answer.

    Args:
        chat_name: Chat name to search for.
        db_path: Ignored — kept for backward-compatibility signature.
        strict: Keyword-only. Default False = pick-most-recent + warn; True
            = raise `AmbiguousChatError` on >1 candidate. Exists as an
            opt-in escape hatch for scripted callers that need hard-error
            semantics (cannot parse stderr warnings reliably).

    Returns:
        chat_id on single-match, or the most-recent candidate's chat_id on
        multi-match in non-strict mode. None when no candidates match.

    Raises:
        AmbiguousChatError: when >1 candidate matches and `strict=True`, OR
            when the defensive invariant fails (regardless of `strict`).
    """
    candidates = resolve_chat_candidates(chat_name)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0].chat_id

    # >1 candidate.
    if strict:
        raise AmbiguousChatError(candidates)

    chosen = candidates[0]  # _sort_candidates guarantees most-recent first.

    # Defensive invariant: chosen MUST have the max last_activity_ts.
    # If any candidate has a strictly greater timestamp than chosen, the
    # sort is broken — raise unconditionally (this path ignores `strict`).
    # None timestamps are treated as -inf for this comparison.
    def _ts_or_neg_inf(ts: float | None) -> float:
        return ts if ts is not None else float("-inf")

    chosen_ts = _ts_or_neg_inf(chosen.last_activity_ts)
    max_ts = max(_ts_or_neg_inf(c.last_activity_ts) for c in candidates)
    if chosen_ts != max_ts:
        raise AmbiguousChatError(candidates)

    # Build greppable warning: chosen=(id,name,last:age); also=[(id,name,last:age),...]
    def _age(ts: float | None) -> str:
        if ts is None:
            return "never"
        delta = max(0.0, time.time() - float(ts))
        if delta < 60:
            return "<1m"
        if delta < 3600:
            return f"{int(delta // 60)}m"
        if delta < 86400:
            return f"{int(delta // 3600)}h"
        return f"{int(delta // 86400)}d"

    alts = [
        f"({c.chat_id},{c.chat_name!r},last:{_age(c.last_activity_ts)})"
        for c in candidates
        if c.chat_id != chosen.chat_id
    ]
    logger.warning(
        'ambiguous chat "%s" — %d candidates, chose %s: chose=(%s,%r,last:%s); also=[%s]',
        chat_name,
        len(candidates),
        chosen.chat_id,
        chosen.chat_id,
        chosen.chat_name,
        _age(chosen.last_activity_ts),
        ", ".join(alts),
    )
    return chosen.chat_id


def search_all_chats(
    query: str,
    max_results: int = 20,
    max_age_days: int = 30,
    db_path=None,  # Ignored — kept for API compatibility
) -> dict:
    """Search across all chats.

    Args:
        query: Search query.
        max_results: Maximum results total.
        max_age_days: Time window in days.
        db_path: Ignored — kept for backward-compatibility signature.

    Returns:
        dict with results grouped by chat.
    """
    if not query or not query.strip():
        return {"error": "Query cannot be empty"}

    from models.chat import Chat
    from models.telegram import TelegramMessage

    cutoff = time.time() - (max_age_days * 86400)
    query_lower = query.lower()

    # Build chat_id -> chat_name map
    try:
        chat_map = {c.chat_id: c.chat_name for c in Chat.query.all()}
    except Exception:
        chat_map = {}

    try:
        all_messages = list(TelegramMessage.query.all())
    except Exception as e:
        return {"error": str(e)}

    results = []
    for msg in all_messages:
        ts = msg.timestamp or 0.0
        if ts < cutoff:
            continue
        content = msg.content or ""
        if query_lower not in content.lower():
            continue

        score = _score_relevance(query, content, ts, max_age_days)
        results.append(
            {
                "id": msg.msg_id,
                "chat_id": msg.chat_id,
                "chat_name": chat_map.get(str(msg.chat_id), str(msg.chat_id)),
                "message_id": msg.message_id,
                "sender": msg.sender,
                "content": content,
                "timestamp": _ts_to_iso(ts),
                "message_type": msg.message_type,
                "relevance_score": round(score, 3),
            }
        )

    results.sort(key=lambda x: x["relevance_score"], reverse=True)
    results = results[:max_results]

    return {
        "query": query,
        "results": results,
        "total_matches": len(results),
        "time_window_days": max_age_days,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python -m tools.telegram_history 'query' 'chat_id'")
        sys.exit(1)

    query = sys.argv[1]
    chat_id = sys.argv[2]

    print(f"Searching '{query}' in chat {chat_id}...")

    result = search_history(query, chat_id)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    else:
        print(f"\nFound {result['total_matches']} results:")
        for r in result["results"]:
            print(f"\n  [{r['timestamp']}] {r['sender']}: {r['content'][:100]}...")
            print(f"  Relevance: {r['relevance_score']}")
