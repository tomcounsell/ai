"""
Telegram History Tool

Search Telegram conversation history with relevance scoring.
Store and manage links shared in Telegram chats.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_DB_PATH = Path.home() / ".valor" / "telegram_history.db"


class TelegramHistoryError(Exception):
    """Telegram history operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


def _get_db_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get or create database connection."""
    path = db_path or DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    # Create tables if needed
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            chat_id TEXT NOT NULL,
            message_id INTEGER,
            sender TEXT,
            content TEXT,
            timestamp TIMESTAMP,
            message_type TEXT DEFAULT 'text'
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_chat_id ON messages(chat_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)
    """)

    # Links table for storing shared URLs with metadata
    conn.execute("""
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            final_url TEXT,
            title TEXT,
            description TEXT,
            domain TEXT,
            sender TEXT,
            chat_id TEXT,
            message_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            tags TEXT,
            notes TEXT,
            status TEXT DEFAULT 'unread',
            ai_summary TEXT,
            UNIQUE(url, chat_id, message_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_links_domain ON links(domain)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_links_sender ON links(sender)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_links_timestamp ON links(timestamp)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_links_status ON links(status)
    """)

    # Chats table for mapping chat_id → chat_name
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            chat_id TEXT PRIMARY KEY,
            chat_name TEXT NOT NULL,
            chat_type TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_chats_name ON chats(chat_name)
    """)

    conn.commit()

    return conn


def store_message(
    chat_id: str,
    content: str,
    sender: str | None = None,
    message_id: int | None = None,
    timestamp: datetime | None = None,
    message_type: str = "text",
    db_path: Path | None = None,
) -> dict:
    """
    Store a message in the history database.

    Args:
        chat_id: Telegram chat ID
        content: Message content
        sender: Message sender
        message_id: Telegram message ID
        timestamp: Message timestamp
        message_type: Type of message (text, photo, etc.)
        db_path: Custom database path

    Returns:
        dict with storage result
    """
    conn = _get_db_connection(db_path)

    if timestamp is None:
        timestamp = datetime.now()

    try:
        cursor = conn.execute(
            """
            INSERT INTO messages (chat_id, message_id, sender, content, timestamp, message_type)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chat_id, message_id, sender, content, timestamp, message_type),
        )
        conn.commit()

        return {
            "stored": True,
            "id": cursor.lastrowid,
            "chat_id": chat_id,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def search_history(
    query: str,
    chat_id: str,
    max_results: int = 5,
    max_age_days: int = 30,
    db_path: Path | None = None,
) -> dict:
    """
    Search Telegram conversation history.

    Args:
        query: Search query
        chat_id: Telegram chat ID
        max_results: Maximum results (default: 5)
        max_age_days: Time window in days (default: 30)
        db_path: Custom database path

    Returns:
        dict with:
            - results: Matching messages with relevance scores
            - total_matches: Number of matches found
    """
    if not query or not query.strip():
        return {"error": "Query cannot be empty"}

    if not chat_id:
        return {"error": "Chat ID is required"}

    max_results = max(1, min(100, max_results))

    conn = _get_db_connection(db_path)

    # Calculate cutoff date
    cutoff = datetime.now() - timedelta(days=max_age_days)

    try:
        # Search with keyword matching
        cursor = conn.execute(
            """
            SELECT id, message_id, sender, content, timestamp, message_type
            FROM messages
            WHERE chat_id = ?
              AND content LIKE ?
              AND timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (chat_id, f"%{query}%", cutoff, max_results * 3),
        )

        rows = cursor.fetchall()

        # Score and rank results
        results = []
        query_lower = query.lower()
        query_words = set(query_lower.split())

        for row in rows:
            content = row["content"]
            content_lower = content.lower()

            # Calculate relevance score
            score = 0.0

            # Exact match bonus
            if query_lower in content_lower:
                score += 0.5

            # Word match bonus
            content_words = set(content_lower.split())
            matching_words = query_words & content_words
            score += len(matching_words) * 0.2

            # Recency bonus (newer = higher score)
            try:
                msg_time = datetime.fromisoformat(row["timestamp"])
                days_old = (datetime.now() - msg_time).days
                recency_bonus = max(0, (max_age_days - days_old) / max_age_days) * 0.3
                score += recency_bonus
            except (ValueError, TypeError):
                pass

            results.append({
                "id": row["id"],
                "message_id": row["message_id"],
                "sender": row["sender"],
                "content": content,
                "timestamp": row["timestamp"],
                "message_type": row["message_type"],
                "relevance_score": round(score, 3),
            })

        # Sort by relevance and take top results
        results.sort(key=lambda x: x["relevance_score"], reverse=True)
        results = results[:max_results]

        return {
            "query": query,
            "chat_id": chat_id,
            "results": results,
            "total_matches": len(results),
            "time_window_days": max_age_days,
        }

    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def get_recent_messages(
    chat_id: str,
    limit: int = 10,
    db_path: Path | None = None,
) -> dict:
    """
    Get recent messages from a chat.

    Args:
        chat_id: Telegram chat ID
        limit: Maximum messages to return
        db_path: Custom database path

    Returns:
        dict with recent messages
    """
    if not chat_id:
        return {"error": "Chat ID is required"}

    conn = _get_db_connection(db_path)

    try:
        cursor = conn.execute(
            """
            SELECT id, message_id, sender, content, timestamp, message_type
            FROM messages
            WHERE chat_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (chat_id, limit),
        )

        messages = [dict(row) for row in cursor.fetchall()]

        return {
            "chat_id": chat_id,
            "messages": messages,
            "count": len(messages),
        }

    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def get_chat_stats(chat_id: str, db_path: Path | None = None) -> dict:
    """
    Get statistics for a chat.

    Args:
        chat_id: Telegram chat ID
        db_path: Custom database path

    Returns:
        dict with chat statistics
    """
    conn = _get_db_connection(db_path)

    try:
        cursor = conn.execute(
            """
            SELECT
                COUNT(*) as total_messages,
                COUNT(DISTINCT sender) as unique_senders,
                MIN(timestamp) as first_message,
                MAX(timestamp) as last_message
            FROM messages
            WHERE chat_id = ?
            """,
            (chat_id,),
        )

        row = cursor.fetchone()

        return {
            "chat_id": chat_id,
            "total_messages": row["total_messages"],
            "unique_senders": row["unique_senders"],
            "first_message": row["first_message"],
            "last_message": row["last_message"],
        }

    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


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
    db_path: Path | None = None,
) -> dict:
    """
    Store a link in the history database.

    Args:
        url: The URL to store
        sender: Who shared the link
        chat_id: Telegram chat ID
        message_id: Telegram message ID
        timestamp: When the link was shared
        title: Page title (optional, can be fetched)
        description: Page description (optional)
        final_url: Final URL after redirects
        tags: List of tags for categorization
        notes: User notes about the link
        ai_summary: AI-generated summary
        db_path: Custom database path

    Returns:
        dict with storage result
    """
    conn = _get_db_connection(db_path)

    if timestamp is None:
        timestamp = datetime.now()

    # Extract domain from URL
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Remove www. prefix if present
        if domain.startswith("www."):
            domain = domain[4:]
    except Exception:
        domain = None

    # Serialize tags as JSON
    tags_json = json.dumps(tags) if tags else None

    try:
        cursor = conn.execute(
            """
            INSERT INTO links (url, final_url, title, description, domain, sender,
                              chat_id, message_id, timestamp, tags, notes, ai_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url, chat_id, message_id) DO UPDATE SET
                title = COALESCE(excluded.title, links.title),
                description = COALESCE(excluded.description, links.description),
                final_url = COALESCE(excluded.final_url, links.final_url),
                ai_summary = COALESCE(excluded.ai_summary, links.ai_summary)
            """,
            (url, final_url, title, description, domain, sender,
             chat_id, message_id, timestamp, tags_json, notes, ai_summary),
        )
        conn.commit()

        return {
            "stored": True,
            "id": cursor.lastrowid,
            "url": url,
            "domain": domain,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def search_links(
    query: str | None = None,
    domain: str | None = None,
    sender: str | None = None,
    status: str | None = None,
    limit: int = 20,
    db_path: Path | None = None,
) -> dict:
    """
    Search stored links with various filters.

    Args:
        query: Text search in URL, title, description, notes
        domain: Filter by domain
        sender: Filter by sender
        status: Filter by status (unread, read, archived)
        limit: Maximum results
        db_path: Custom database path

    Returns:
        dict with matching links
    """
    conn = _get_db_connection(db_path)

    conditions = []
    params = []

    if query:
        conditions.append("""
            (url LIKE ? OR title LIKE ? OR description LIKE ?
             OR notes LIKE ? OR ai_summary LIKE ?)
        """)
        query_param = f"%{query}%"
        params.extend([query_param] * 5)

    if domain:
        conditions.append("domain = ?")
        params.append(domain.lower())

    if sender:
        conditions.append("sender LIKE ?")
        params.append(f"%{sender}%")

    if status:
        conditions.append("status = ?")
        params.append(status)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    try:
        cursor = conn.execute(
            f"""
            SELECT id, url, final_url, title, description, domain, sender,
                   chat_id, message_id, timestamp, tags, notes, status, ai_summary
            FROM links
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            params + [limit],
        )

        links = []
        for row in cursor.fetchall():
            link = dict(row)
            # Parse tags from JSON
            if link.get("tags"):
                try:
                    link["tags"] = json.loads(link["tags"])
                except json.JSONDecodeError:
                    link["tags"] = []
            links.append(link)

        return {
            "links": links,
            "count": len(links),
            "query": query,
            "filters": {
                "domain": domain,
                "sender": sender,
                "status": status,
            },
        }

    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def list_links(
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """
    List recent links with pagination.

    Args:
        limit: Maximum results
        offset: Skip first N results
        status: Filter by status
        db_path: Custom database path

    Returns:
        dict with links and pagination info
    """
    conn = _get_db_connection(db_path)

    conditions = []
    params = []

    if status:
        conditions.append("status = ?")
        params.append(status)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    try:
        # Get total count
        count_cursor = conn.execute(
            f"SELECT COUNT(*) FROM links WHERE {where_clause}",
            params,
        )
        total = count_cursor.fetchone()[0]

        # Get links
        cursor = conn.execute(
            f"""
            SELECT id, url, final_url, title, description, domain, sender,
                   chat_id, message_id, timestamp, tags, notes, status, ai_summary
            FROM links
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        )

        links = []
        for row in cursor.fetchall():
            link = dict(row)
            if link.get("tags"):
                try:
                    link["tags"] = json.loads(link["tags"])
                except json.JSONDecodeError:
                    link["tags"] = []
            links.append(link)

        return {
            "links": links,
            "count": len(links),
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(links) < total,
        }

    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def get_link_by_url(
    url: str,
    max_age_hours: int | None = None,
    db_path: Path | None = None,
) -> dict | None:
    """
    Get an existing link record by URL.

    Used for caching - check if we already have a summary for this URL.

    Args:
        url: URL to look up
        max_age_hours: Only return if summary is newer than this (None = any age)
        db_path: Custom database path

    Returns:
        dict with link data if found and has summary, None otherwise
    """
    conn = _get_db_connection(db_path)

    try:
        # Build query based on whether we want to filter by age
        if max_age_hours is not None:
            cutoff = datetime.now() - timedelta(hours=max_age_hours)
            cursor = conn.execute(
                """
                SELECT id, url, final_url, title, description, domain, sender,
                       chat_id, message_id, timestamp, tags, notes, status, ai_summary
                FROM links
                WHERE url = ?
                  AND ai_summary IS NOT NULL
                  AND timestamp >= ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (url, cutoff),
            )
        else:
            cursor = conn.execute(
                """
                SELECT id, url, final_url, title, description, domain, sender,
                       chat_id, message_id, timestamp, tags, notes, status, ai_summary
                FROM links
                WHERE url = ?
                  AND ai_summary IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (url,),
            )

        row = cursor.fetchone()
        if row:
            link = dict(row)
            if link.get("tags"):
                try:
                    link["tags"] = json.loads(link["tags"])
                except json.JSONDecodeError:
                    link["tags"] = []
            return link

        return None

    except Exception:
        return None
    finally:
        conn.close()


def update_link(
    link_id: int,
    status: str | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
    ai_summary: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """
    Update a stored link.

    Args:
        link_id: ID of the link to update
        status: New status (unread, read, archived)
        tags: New tags (replaces existing)
        notes: New notes (replaces existing)
        ai_summary: New AI summary
        db_path: Custom database path

    Returns:
        dict with update result
    """
    conn = _get_db_connection(db_path)

    updates = []
    params = []

    if status is not None:
        updates.append("status = ?")
        params.append(status)

    if tags is not None:
        updates.append("tags = ?")
        params.append(json.dumps(tags))

    if notes is not None:
        updates.append("notes = ?")
        params.append(notes)

    if ai_summary is not None:
        updates.append("ai_summary = ?")
        params.append(ai_summary)

    if not updates:
        return {"error": "No fields to update"}

    params.append(link_id)

    try:
        cursor = conn.execute(
            f"""
            UPDATE links
            SET {", ".join(updates)}
            WHERE id = ?
            """,
            params,
        )
        conn.commit()

        if cursor.rowcount == 0:
            return {"error": f"Link {link_id} not found"}

        return {
            "updated": True,
            "id": link_id,
            "fields_updated": len(updates),
        }

    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def get_link_stats(db_path: Path | None = None) -> dict:
    """
    Get statistics about stored links.

    Args:
        db_path: Custom database path

    Returns:
        dict with link statistics
    """
    conn = _get_db_connection(db_path)

    try:
        cursor = conn.execute("""
            SELECT
                COUNT(*) as total_links,
                COUNT(DISTINCT domain) as unique_domains,
                COUNT(DISTINCT sender) as unique_senders,
                SUM(CASE WHEN status = 'unread' THEN 1 ELSE 0 END) as unread,
                SUM(CASE WHEN status = 'read' THEN 1 ELSE 0 END) as read,
                SUM(CASE WHEN status = 'archived' THEN 1 ELSE 0 END) as archived,
                MIN(timestamp) as first_link,
                MAX(timestamp) as last_link
            FROM links
        """)

        row = cursor.fetchone()

        # Get top domains
        domain_cursor = conn.execute("""
            SELECT domain, COUNT(*) as count
            FROM links
            WHERE domain IS NOT NULL
            GROUP BY domain
            ORDER BY count DESC
            LIMIT 10
        """)
        top_domains = [{"domain": r[0], "count": r[1]} for r in domain_cursor.fetchall()]

        return {
            "total_links": row["total_links"],
            "unique_domains": row["unique_domains"],
            "unique_senders": row["unique_senders"],
            "by_status": {
                "unread": row["unread"],
                "read": row["read"],
                "archived": row["archived"],
            },
            "first_link": row["first_link"],
            "last_link": row["last_link"],
            "top_domains": top_domains,
        }

    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


# =============================================================================
# Chat Mapping Functions
# =============================================================================


def register_chat(
    chat_id: str,
    chat_name: str,
    chat_type: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """
    Register or update a chat mapping.

    Called by the bridge when messages are received to maintain
    the chat_id → chat_name mapping.

    Args:
        chat_id: Telegram chat ID
        chat_name: Human-readable chat name/title
        chat_type: Type of chat (private, group, supergroup, channel)
        db_path: Custom database path

    Returns:
        dict with registration result
    """
    conn = _get_db_connection(db_path)

    try:
        conn.execute(
            """
            INSERT INTO chats (chat_id, chat_name, chat_type, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                chat_name = excluded.chat_name,
                chat_type = COALESCE(excluded.chat_type, chats.chat_type),
                updated_at = CURRENT_TIMESTAMP
            """,
            (chat_id, chat_name, chat_type),
        )
        conn.commit()

        return {
            "registered": True,
            "chat_id": chat_id,
            "chat_name": chat_name,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def list_chats(db_path: Path | None = None) -> dict:
    """
    List all known chats with their message counts.

    Returns:
        dict with list of chats and their stats
    """
    conn = _get_db_connection(db_path)

    try:
        cursor = conn.execute("""
            SELECT
                c.chat_id,
                c.chat_name,
                c.chat_type,
                c.updated_at,
                COUNT(m.id) as message_count,
                MAX(m.timestamp) as last_message
            FROM chats c
            LEFT JOIN messages m ON c.chat_id = m.chat_id
            GROUP BY c.chat_id, c.chat_name, c.chat_type, c.updated_at
            ORDER BY last_message DESC NULLS LAST
        """)

        chats = [dict(row) for row in cursor.fetchall()]

        return {
            "chats": chats,
            "count": len(chats),
        }

    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


def resolve_chat_id(
    chat_name: str,
    db_path: Path | None = None,
) -> str | None:
    """
    Resolve a chat name to its chat_id.

    Supports partial matching and case-insensitive search.

    Args:
        chat_name: Chat name to search for
        db_path: Custom database path

    Returns:
        chat_id if found, None otherwise
    """
    conn = _get_db_connection(db_path)

    try:
        # Try exact match first
        cursor = conn.execute(
            "SELECT chat_id FROM chats WHERE chat_name = ?",
            (chat_name,),
        )
        row = cursor.fetchone()
        if row:
            return row["chat_id"]

        # Try case-insensitive match
        cursor = conn.execute(
            "SELECT chat_id FROM chats WHERE LOWER(chat_name) = LOWER(?)",
            (chat_name,),
        )
        row = cursor.fetchone()
        if row:
            return row["chat_id"]

        # Try partial match (contains)
        cursor = conn.execute(
            "SELECT chat_id FROM chats WHERE LOWER(chat_name) LIKE LOWER(?)",
            (f"%{chat_name}%",),
        )
        row = cursor.fetchone()
        if row:
            return row["chat_id"]

        return None

    except Exception:
        return None
    finally:
        conn.close()


def search_all_chats(
    query: str,
    max_results: int = 20,
    max_age_days: int = 30,
    db_path: Path | None = None,
) -> dict:
    """
    Search across all chats.

    Args:
        query: Search query
        max_results: Maximum results total
        max_age_days: Time window in days
        db_path: Custom database path

    Returns:
        dict with results grouped by chat
    """
    if not query or not query.strip():
        return {"error": "Query cannot be empty"}

    conn = _get_db_connection(db_path)
    cutoff = datetime.now() - timedelta(days=max_age_days)

    try:
        cursor = conn.execute(
            """
            SELECT m.id, m.chat_id, m.message_id, m.sender, m.content,
                   m.timestamp, m.message_type,
                   c.chat_name
            FROM messages m
            LEFT JOIN chats c ON m.chat_id = c.chat_id
            WHERE m.content LIKE ?
              AND m.timestamp >= ?
            ORDER BY m.timestamp DESC
            LIMIT ?
            """,
            (f"%{query}%", cutoff, max_results * 3),
        )

        rows = cursor.fetchall()

        # Score and rank results
        results = []
        query_lower = query.lower()
        query_words = set(query_lower.split())

        for row in rows:
            content = row["content"]
            content_lower = content.lower()

            # Calculate relevance score
            score = 0.0

            if query_lower in content_lower:
                score += 0.5

            content_words = set(content_lower.split())
            matching_words = query_words & content_words
            score += len(matching_words) * 0.2

            try:
                msg_time = datetime.fromisoformat(row["timestamp"])
                days_old = (datetime.now() - msg_time).days
                recency_bonus = max(0, (max_age_days - days_old) / max_age_days) * 0.3
                score += recency_bonus
            except (ValueError, TypeError):
                pass

            results.append({
                "id": row["id"],
                "chat_id": row["chat_id"],
                "chat_name": row["chat_name"] or row["chat_id"],
                "message_id": row["message_id"],
                "sender": row["sender"],
                "content": content,
                "timestamp": row["timestamp"],
                "message_type": row["message_type"],
                "relevance_score": round(score, 3),
            })

        results.sort(key=lambda x: x["relevance_score"], reverse=True)
        results = results[:max_results]

        return {
            "query": query,
            "results": results,
            "total_matches": len(results),
            "time_window_days": max_age_days,
        }

    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


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
