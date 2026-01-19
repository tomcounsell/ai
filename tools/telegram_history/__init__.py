"""
Telegram History Tool

Search Telegram conversation history with relevance scoring.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

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
