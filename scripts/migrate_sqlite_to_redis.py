#!/usr/bin/env python3
"""Migrate existing SQLite telegram history to Redis/Popoto.

One-time migration script. Reads all rows from ~/.valor/telegram_history.db
and creates Popoto model instances in Redis.

Usage:
    python scripts/migrate_sqlite_to_redis.py
    python scripts/migrate_sqlite_to_redis.py --dry-run
    python scripts/migrate_sqlite_to_redis.py --verify

Flags:
    --dry-run   Preview what would be migrated without writing to Redis.
    --verify    Compare counts between SQLite and Redis after migration.
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DB_PATH = Path.home() / ".valor" / "telegram_history.db"


def parse_timestamp(ts_val) -> float:
    """Parse a timestamp value to unix float."""
    if ts_val is None:
        return time.time()
    if isinstance(ts_val, (int, float)):
        return float(ts_val)
    # Try parsing ISO/datetime string
    for fmt in [
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]:
        try:
            return datetime.strptime(str(ts_val), fmt).timestamp()
        except ValueError:
            continue
    return time.time()


def migrate_messages(conn: sqlite3.Connection, dry_run: bool) -> dict:
    """Migrate messages table to TelegramMessage Popoto model."""
    from models.telegram import TelegramMessage

    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        "SELECT id, chat_id, message_id, sender, content, timestamp, message_type FROM messages"
    )
    rows = cursor.fetchall()

    total = len(rows)
    created = 0
    skipped = 0
    errors = 0

    print(f"  Messages: {total} rows found in SQLite")

    for row in rows:
        try:
            chat_id = str(row["chat_id"] or "unknown")
            sender = row["sender"] or "unknown"
            direction = "out" if sender.lower() == "valor" else "in"
            ts = parse_timestamp(row["timestamp"])
            content = row["content"] or ""
            msg_type = row["message_type"] or "text"

            if dry_run:
                created += 1
                continue

            # Check for existing (by chat_id + message_id to avoid duplicates)
            if row["message_id"]:
                existing = list(TelegramMessage.query.filter(chat_id=chat_id))
                already_exists = any(
                    m.message_id == row["message_id"] for m in existing
                )
                if already_exists:
                    skipped += 1
                    continue

            TelegramMessage.create(
                chat_id=chat_id,
                message_id=row["message_id"],
                direction=direction,
                sender=sender,
                content=content[:50_000],  # Respect model max_length
                timestamp=ts,
                message_type=msg_type,
            )
            created += 1

        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"    Error on message row {row['id']}: {e}")

    return {"total": total, "created": created, "skipped": skipped, "errors": errors}


def migrate_links(conn: sqlite3.Connection, dry_run: bool) -> dict:
    """Migrate links table to Link Popoto model."""
    from models.link import Link

    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "SELECT id, url, final_url, title, description, domain, sender, "
            "chat_id, message_id, timestamp, tags, notes, status, ai_summary FROM links"
        )
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        print("  Links: table not found, skipping")
        return {"total": 0, "created": 0, "skipped": 0, "errors": 0}

    import json

    total = len(rows)
    created = 0
    skipped = 0
    errors = 0

    print(f"  Links: {total} rows found in SQLite")

    for row in rows:
        try:
            url = row["url"] or ""
            chat_id = str(row["chat_id"] or "unknown")
            ts = parse_timestamp(row["timestamp"])

            if not url:
                skipped += 1
                continue

            if dry_run:
                created += 1
                continue

            # Check for existing by url+chat_id
            existing = list(Link.query.filter(url=url, chat_id=chat_id))
            if existing:
                skipped += 1
                continue

            # Parse tags from JSON string
            tags_raw = row["tags"]
            tags = []
            if tags_raw:
                try:
                    tags = json.loads(tags_raw)
                except (json.JSONDecodeError, TypeError):
                    tags = []

            Link.create(
                url=url,
                chat_id=chat_id,
                message_id=row["message_id"],
                domain=row["domain"],
                sender=row["sender"] or "unknown",
                status=row["status"] or "unread",
                timestamp=ts,
                final_url=row["final_url"],
                title=row["title"],
                description=row["description"],
                tags=tags,
                notes=row["notes"],
                ai_summary=row["ai_summary"],
            )
            created += 1

        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"    Error on link row {row['id']}: {e}")

    return {"total": total, "created": created, "skipped": skipped, "errors": errors}


def migrate_chats(conn: sqlite3.Connection, dry_run: bool) -> dict:
    """Migrate chats table to Chat Popoto model."""
    from models.chat import Chat

    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "SELECT chat_id, chat_name, chat_type, updated_at FROM chats"
        )
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        print("  Chats: table not found, skipping")
        return {"total": 0, "created": 0, "skipped": 0, "errors": 0}

    total = len(rows)
    created = 0
    skipped = 0
    errors = 0

    print(f"  Chats: {total} rows found in SQLite")

    for row in rows:
        try:
            chat_id = str(row["chat_id"] or "")
            chat_name = row["chat_name"] or ""
            ts = parse_timestamp(row["updated_at"])

            if not chat_id or not chat_name:
                skipped += 1
                continue

            if dry_run:
                created += 1
                continue

            # Check for existing
            existing = list(Chat.query.filter(chat_id=chat_id))
            if existing:
                skipped += 1
                continue

            Chat.create(
                chat_id=chat_id,
                chat_name=chat_name,
                chat_type=row["chat_type"],
                updated_at=ts,
            )
            created += 1

        except Exception as e:
            errors += 1
            if errors <= 5:
                print(
                    f"    Error on chat row {row['id'] if 'id' in row.keys() else chat_id}: {e}"
                )

    return {"total": total, "created": created, "skipped": skipped, "errors": errors}


def verify_migration(conn: sqlite3.Connection) -> None:
    """Compare counts between SQLite and Redis."""
    from models.chat import Chat
    from models.link import Link
    from models.telegram import TelegramMessage

    conn.row_factory = sqlite3.Row

    print("\nVerification: SQLite vs Redis counts")
    print("-" * 40)

    # Messages
    msg_count_sql = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    try:
        msg_count_redis = len(list(TelegramMessage.query.all()))
    except Exception as e:
        msg_count_redis = f"error: {e}"
    status = "OK" if msg_count_redis == msg_count_sql else "MISMATCH"
    print(f"  Messages:  SQLite={msg_count_sql}  Redis={msg_count_redis}  [{status}]")

    # Links
    try:
        link_count_sql = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    except sqlite3.OperationalError:
        link_count_sql = 0
    try:
        link_count_redis = len(list(Link.query.all()))
    except Exception as e:
        link_count_redis = f"error: {e}"
    status = "OK" if link_count_redis == link_count_sql else "MISMATCH"
    print(f"  Links:     SQLite={link_count_sql}  Redis={link_count_redis}  [{status}]")

    # Chats
    try:
        chat_count_sql = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
    except sqlite3.OperationalError:
        chat_count_sql = 0
    try:
        chat_count_redis = len(list(Chat.query.all()))
    except Exception as e:
        chat_count_redis = f"error: {e}"
    status = "OK" if chat_count_redis == chat_count_sql else "MISMATCH"
    print(f"  Chats:     SQLite={chat_count_sql}  Redis={chat_count_redis}  [{status}]")


def main():
    parser = argparse.ArgumentParser(
        description="Migrate SQLite telegram history to Redis/Popoto"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview migration without writing to Redis",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Compare counts between SQLite and Redis after migration",
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help=f"Path to SQLite DB (default: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)

    if not db_path.exists():
        print(f"SQLite database not found: {db_path}")
        print("Nothing to migrate.")
        sys.exit(0)

    print(f"Migration: {db_path} -> Redis")
    if args.dry_run:
        print("DRY RUN — no changes will be written to Redis")
    print()

    conn = sqlite3.connect(str(db_path))

    try:
        print("Migrating...")
        msg_stats = migrate_messages(conn, args.dry_run)
        link_stats = migrate_links(conn, args.dry_run)
        chat_stats = migrate_chats(conn, args.dry_run)

        print()
        print("Migration summary:")
        print(
            f"  Messages: {msg_stats['created']} created, {msg_stats['skipped']} skipped, {msg_stats['errors']} errors"
        )
        print(
            f"  Links:    {link_stats['created']} created, {link_stats['skipped']} skipped, {link_stats['errors']} errors"
        )
        print(
            f"  Chats:    {chat_stats['created']} created, {chat_stats['skipped']} skipped, {chat_stats['errors']} errors"
        )

        if args.verify or not args.dry_run:
            verify_migration(conn)

    finally:
        conn.close()

    print()
    if args.dry_run:
        print("Dry run complete. Re-run without --dry-run to migrate.")
    else:
        print("Migration complete.")
        print(f"SQLite backup remains at: {db_path}" " (keep for 30 days then remove)")


if __name__ == "__main__":
    main()
