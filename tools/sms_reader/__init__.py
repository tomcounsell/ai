"""
SMS Reader - Read messages from macOS Messages app.

This tool provides access to SMS and iMessage messages stored in the macOS
Messages app database (~/Library/Messages/chat.db).

Key features:
- Read recent messages from any sender
- Search messages by sender, content, or date range
- Extract 2FA verification codes automatically
- List all message senders/contacts

Requirements:
- macOS with Messages app
- Full Disk Access permission for the running process
  (System Preferences > Security & Privacy > Privacy > Full Disk Access)

Usage:
    from tools.sms_reader import get_recent_messages, get_latest_2fa_code

    # Get recent messages
    messages = get_recent_messages(limit=10)

    # Get 2FA code from last 5 minutes
    code = get_latest_2fa_code(minutes=5)
"""

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

# macOS Messages database path
MESSAGES_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"

# Apple's epoch starts at 2001-01-01 (differs from Unix epoch)
# Messages database stores dates in nanoseconds since Apple epoch
APPLE_EPOCH_OFFSET = 978307200  # Seconds between Unix epoch and Apple epoch


class SMSReaderError(Exception):
    """Base exception for SMS reader errors."""

    def __init__(self, message: str, category: str = "general"):
        self.message = message
        self.category = category
        super().__init__(message)


@dataclass
class Message:
    """Represents a message from the Messages database."""

    rowid: int
    guid: str
    text: str | None
    sender: str  # Phone number or email
    is_from_me: bool
    date: datetime
    service: str  # iMessage or SMS
    chat_id: str | None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "rowid": self.rowid,
            "guid": self.guid,
            "text": self.text,
            "sender": self.sender,
            "is_from_me": self.is_from_me,
            "date": self.date.isoformat(),
            "service": self.service,
            "chat_id": self.chat_id,
        }


def _apple_time_to_datetime(apple_time: int | None) -> datetime | None:
    """Convert Apple epoch nanoseconds to datetime."""
    if apple_time is None or apple_time == 0:
        return None
    # Apple time is in nanoseconds since 2001-01-01
    unix_timestamp = (apple_time / 1_000_000_000) + APPLE_EPOCH_OFFSET
    return datetime.fromtimestamp(unix_timestamp)


def _datetime_to_apple_time(dt: datetime) -> int:
    """Convert datetime to Apple epoch nanoseconds."""
    unix_timestamp = dt.timestamp()
    apple_seconds = unix_timestamp - APPLE_EPOCH_OFFSET
    return int(apple_seconds * 1_000_000_000)


def _get_db_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Get database connection to Messages database."""
    path = db_path or MESSAGES_DB_PATH

    if not path.exists():
        raise SMSReaderError(
            f"Messages database not found at {path}. "
            "Make sure Messages app has been used.",
            category="not_found",
        )

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError as e:
        if "unable to open" in str(e).lower():
            raise SMSReaderError(
                "Cannot open Messages database. Grant Full Disk Access to your terminal. "
                "System Preferences > Security & Privacy > Privacy > Full Disk Access",
                category="permission_denied",
            ) from e
        raise SMSReaderError(f"Database error: {e}", category="database") from e


def get_recent_messages(
    limit: int = 20,
    sender: str | None = None,
    since_minutes: int | None = None,
    include_sent: bool = False,
    db_path: Path | None = None,
) -> list[dict]:
    """
    Get recent messages from the Messages database.

    Args:
        limit: Maximum number of messages to return
        sender: Filter by sender (phone number or email, partial match)
        since_minutes: Only get messages from the last N minutes
        include_sent: Include messages sent by the user
        db_path: Override database path (for testing)

    Returns:
        List of message dictionaries, newest first
    """
    conn = _get_db_connection(db_path)

    try:
        query = """
            SELECT
                m.ROWID,
                m.guid,
                m.text,
                m.is_from_me,
                m.date,
                m.service,
                h.id as sender,
                c.chat_identifier
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            LEFT JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            LEFT JOIN chat c ON cmj.chat_id = c.ROWID
            WHERE m.text IS NOT NULL
        """
        params: list = []

        if not include_sent:
            query += " AND m.is_from_me = 0"

        if sender:
            query += " AND (h.id LIKE ? OR c.chat_identifier LIKE ?)"
            params.extend([f"%{sender}%", f"%{sender}%"])

        if since_minutes:
            cutoff = datetime.now() - timedelta(minutes=since_minutes)
            apple_cutoff = _datetime_to_apple_time(cutoff)
            query += " AND m.date > ?"
            params.append(apple_cutoff)

        query += " ORDER BY m.date DESC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

        messages = []
        for row in rows:
            msg = Message(
                rowid=row["ROWID"],
                guid=row["guid"],
                text=row["text"],
                sender=row["sender"] or "Unknown",
                is_from_me=bool(row["is_from_me"]),
                date=_apple_time_to_datetime(row["date"]) or datetime.now(),
                service=row["service"] or "Unknown",
                chat_id=row["chat_identifier"],
            )
            messages.append(msg.to_dict())

        return messages

    finally:
        conn.close()


def search_messages(
    query: str,
    limit: int = 20,
    since_minutes: int | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """
    Search messages by text content.

    Args:
        query: Text to search for (case-insensitive)
        limit: Maximum number of messages to return
        since_minutes: Only search messages from the last N minutes
        db_path: Override database path (for testing)

    Returns:
        List of matching message dictionaries, newest first
    """
    conn = _get_db_connection(db_path)

    try:
        sql = """
            SELECT
                m.ROWID,
                m.guid,
                m.text,
                m.is_from_me,
                m.date,
                m.service,
                h.id as sender,
                c.chat_identifier
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            LEFT JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            LEFT JOIN chat c ON cmj.chat_id = c.ROWID
            WHERE m.text LIKE ?
        """
        params: list = [f"%{query}%"]

        if since_minutes:
            cutoff = datetime.now() - timedelta(minutes=since_minutes)
            apple_cutoff = _datetime_to_apple_time(cutoff)
            sql += " AND m.date > ?"
            params.append(apple_cutoff)

        sql += " ORDER BY m.date DESC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()

        messages = []
        for row in rows:
            msg = Message(
                rowid=row["ROWID"],
                guid=row["guid"],
                text=row["text"],
                sender=row["sender"] or "Unknown",
                is_from_me=bool(row["is_from_me"]),
                date=_apple_time_to_datetime(row["date"]) or datetime.now(),
                service=row["service"] or "Unknown",
                chat_id=row["chat_identifier"],
            )
            messages.append(msg.to_dict())

        return messages

    finally:
        conn.close()


def extract_codes_from_text(text: str) -> list[str]:
    """
    Extract verification/2FA codes from message text.

    Looks for common patterns:
    - 4-8 digit numeric codes
    - Codes preceded by keywords like "code", "PIN", "OTP"
    - Alphanumeric codes with digits (e.g., "A1B2C3")

    Args:
        text: Message text to extract codes from

    Returns:
        List of potential codes found
    """
    if not text:
        return []

    codes = []

    # Keywords that should NOT be treated as codes
    keywords = {
        "code",
        "codes",
        "pin",
        "pins",
        "otp",
        "password",
        "passcode",
        "verification",
        "security",
        "login",
        "access",
        "your",
        "the",
        "is",
    }

    # Pattern 1: Numeric codes (4-8 digits) - most common
    # Look for codes after keywords
    numeric_after_keyword = re.findall(
        r"(?:code|pin|otp|password|passcode|verification)[:\s]+(?:is[:\s]+)?(\d{4,8})\b",
        text,
        re.IGNORECASE,
    )
    codes.extend(numeric_after_keyword)

    # Pattern 2: Numeric codes before "is your code" etc
    numeric_before_keyword = re.findall(
        r"\b(\d{4,8})\s+(?:is your|is the)\s+(?:code|pin|otp|verification)",
        text,
        re.IGNORECASE,
    )
    codes.extend(numeric_before_keyword)

    # Pattern 3: Standalone 4-8 digit numbers (fallback if no keywords matched)
    if not codes:
        numeric_codes = re.findall(r"\b(\d{4,8})\b", text)
        for code in numeric_codes:
            # Exclude years (1900-2099)
            if not (len(code) == 4 and code.startswith(("19", "20"))):
                codes.append(code)

    # Pattern 4: Alphanumeric codes (must contain at least one digit)
    # Only if explicitly after a code keyword
    alphanum_matches = re.findall(
        r"(?:code|pin|otp)[:\s]+(?:is[:\s]+)?([A-Z0-9]{4,8})\b", text, re.IGNORECASE
    )
    for match in alphanum_matches:
        # Must contain at least one digit to be a code
        if any(c.isdigit() for c in match):
            codes.append(match)

    # Deduplicate while preserving order, filter out keywords
    seen = set()
    unique_codes = []
    for code in codes:
        code_upper = code.upper()
        if code_upper not in seen and code_upper.lower() not in keywords:
            seen.add(code_upper)
            unique_codes.append(code_upper)

    return unique_codes


def get_latest_2fa_code(
    minutes: int = 10,
    sender: str | None = None,
    db_path: Path | None = None,
) -> dict | None:
    """
    Get the most recent 2FA/verification code from messages.

    This searches recent messages for verification code patterns and
    returns the most recent one found.

    Args:
        minutes: Look back this many minutes (default 10)
        sender: Filter by specific sender (phone number, partial match)
        db_path: Override database path (for testing)

    Returns:
        Dictionary with 'code', 'message', 'sender', 'date' or None if no code found

    Example:
        >>> code_info = get_latest_2fa_code(minutes=5)
        >>> if code_info:
        ...     print(f"Your code is: {code_info['code']}")
    """
    # Search for messages with code-related keywords
    keywords = ["code", "verify", "verification", "otp", "pin", "password", "passcode"]

    messages = get_recent_messages(
        limit=50,
        sender=sender,
        since_minutes=minutes,
        include_sent=False,
        db_path=db_path,
    )

    for msg in messages:
        text = msg.get("text", "")
        if not text:
            continue

        # Check if message contains code-related keywords
        text_lower = text.lower()
        has_keyword = any(kw in text_lower for kw in keywords)

        # Also check for patterns even without keywords (many services just send the code)
        codes = extract_codes_from_text(text)

        if codes and (has_keyword or len(codes) == 1):
            return {
                "code": codes[0],
                "message": text,
                "sender": msg["sender"],
                "date": msg["date"],
                "all_codes": codes,
            }

    return None


def list_senders(
    limit: int = 50,
    since_days: int | None = 30,
    db_path: Path | None = None,
) -> list[dict]:
    """
    List unique message senders with message counts.

    Args:
        limit: Maximum number of senders to return
        since_days: Only include senders from last N days
        db_path: Override database path (for testing)

    Returns:
        List of dicts with 'sender', 'message_count', 'last_message_date'
    """
    conn = _get_db_connection(db_path)

    try:
        query = """
            SELECT
                h.id as sender,
                COUNT(*) as message_count,
                MAX(m.date) as last_message_date
            FROM message m
            JOIN handle h ON m.handle_id = h.ROWID
            WHERE m.is_from_me = 0
        """
        params: list = []

        if since_days:
            cutoff = datetime.now() - timedelta(days=since_days)
            apple_cutoff = _datetime_to_apple_time(cutoff)
            query += " AND m.date > ?"
            params.append(apple_cutoff)

        query += " GROUP BY h.id ORDER BY last_message_date DESC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

        senders = []
        for row in rows:
            senders.append(
                {
                    "sender": row["sender"],
                    "message_count": row["message_count"],
                    "last_message_date": (
                        _apple_time_to_datetime(row["last_message_date"]).isoformat()
                        if row["last_message_date"]
                        else None
                    ),
                }
            )

        return senders

    finally:
        conn.close()


# Convenience function for quick 2FA retrieval
def get_2fa(minutes: int = 5, sender: str | None = None) -> str | None:
    """
    Quick function to get just the 2FA code.

    Args:
        minutes: Look back this many minutes
        sender: Filter by sender (optional)

    Returns:
        The code string, or None if not found
    """
    result = get_latest_2fa_code(minutes=minutes, sender=sender)
    return result["code"] if result else None
