---
name: searching-message-history
description: Search Telegram conversation history and stored links. Use when finding past messages, what someone said, or links shared in chats.
---

# Message History

**Location**: `~/.valor/telegram_history.db` (SQLite)

Tables: `messages`, `links`

## Messages

```bash
# Recent messages
sqlite3 ~/.valor/telegram_history.db "SELECT sender, substr(content, 1, 200), timestamp FROM messages ORDER BY timestamp DESC LIMIT 10"

# Messages from Valor
sqlite3 ~/.valor/telegram_history.db "SELECT substr(content, 1, 300), timestamp FROM messages WHERE sender='Valor' ORDER BY timestamp DESC LIMIT 10"

# Search by keyword
sqlite3 ~/.valor/telegram_history.db "SELECT sender, content, timestamp FROM messages WHERE content LIKE '%keyword%' ORDER BY timestamp DESC LIMIT 10"

# Messages by chat
sqlite3 ~/.valor/telegram_history.db "SELECT DISTINCT chat_id, COUNT(*) as count FROM messages GROUP BY chat_id"
```

## Links

```bash
# Recent links
sqlite3 ~/.valor/telegram_history.db "SELECT url, title, sender, timestamp FROM links ORDER BY timestamp DESC LIMIT 10"

# Search links
sqlite3 ~/.valor/telegram_history.db "SELECT url, title, ai_summary FROM links WHERE title LIKE '%keyword%' OR ai_summary LIKE '%keyword%'"
```

## Note

The file `/Users/valorengels/src/ai/data/telegram_history.db` is NOT used. Always use `~/.valor/telegram_history.db`.
