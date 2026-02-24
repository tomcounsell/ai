"""
Telegram History Tool

Search Telegram conversation history with relevance scoring.
Store and manage links shared in Telegram chats.

Backend: Redis/Popoto (replaced SQLite as of 2026-02-24).
All data is stored in Redis via Popoto models (TelegramMessage, Link, Chat).
"""

import time
from datetime import datetime, timedelta
from urllib.parse import urlparse


class TelegramHistoryError(Exception):
    """Telegram history operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


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
    """Parse a timestamp value to unix float."""
    if ts_val is None:
        return time.time()
    if isinstance(ts_val, (int, float)):
        return float(ts_val)
    if hasattr(ts_val, "timestamp"):
        return ts_val.timestamp()
    # Try parsing ISO string
    try:
        return datetime.fromisoformat(str(ts_val)).timestamp()
    except (ValueError, TypeError):
        return time.time()


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
        db_path: Ignored — kept for backward-compatibility signature.

    Returns:
        dict with storage result: {"stored": True, "id": msg_id, "chat_id": chat_id}
    """
    from models.telegram import TelegramMessage

    ts = _parse_ts(timestamp)
    direction = "out" if sender and sender.lower() == "valor" else "in"

    try:
        msg = TelegramMessage.create(
            chat_id=str(chat_id),
            message_id=message_id,
            direction=direction,
            sender=sender or "unknown",
            content=content or "",
            timestamp=ts,
            message_type=message_type,
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
        all_links = [
            lnk
            for lnk in all_links
            if lnk.sender and sender_lower in lnk.sender.lower()
        ]

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
        target = next(
            (lnk for lnk in all_links if str(lnk.link_id) == str(link_id)), None
        )

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
                "ai_summary": (
                    ai_summary if ai_summary is not None else target.ai_summary
                ),
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
    db_path=None,  # Ignored — kept for API compatibility
) -> dict:
    """Register or update a chat mapping in Redis via Chat model.

    Called by the bridge when messages are received to maintain
    the chat_id -> chat_name mapping.

    Args:
        chat_id: Telegram chat ID.
        chat_name: Human-readable chat name/title.
        chat_type: Type of chat (private, group, supergroup, channel).
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
            if chat.chat_name != chat_name or (
                chat_type and chat.chat_type != chat_type
            ):
                old_type = chat_type or chat.chat_type
                chat.delete()
                Chat.create(
                    chat_id=str(chat_id),
                    chat_name=chat_name,
                    chat_type=old_type,
                    updated_at=time.time(),
                )
            else:
                chat.updated_at = time.time()
                chat.save()
        else:
            Chat.create(
                chat_id=str(chat_id),
                chat_name=chat_name,
                chat_type=chat_type,
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
) -> str | None:
    """Resolve a chat name to its chat_id.

    Supports partial matching and case-insensitive search.

    Args:
        chat_name: Chat name to search for.
        db_path: Ignored — kept for backward-compatibility signature.

    Returns:
        chat_id if found, None otherwise.
    """
    from models.chat import Chat

    try:
        # Exact match via KeyField
        exact = list(Chat.query.filter(chat_name=chat_name))
        if exact:
            return exact[0].chat_id

        # Fall back to Python case-insensitive/partial matching
        all_chats = list(Chat.query.all())
        chat_name_lower = chat_name.lower()

        # Case-insensitive exact
        for chat in all_chats:
            if chat.chat_name and chat.chat_name.lower() == chat_name_lower:
                return chat.chat_id

        # Partial contains
        for chat in all_chats:
            if chat.chat_name and chat_name_lower in chat.chat_name.lower():
                return chat.chat_id

        return None
    except Exception:
        return None


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
