---
name: viewing-logs
description: Find system logs and Telegram message history. Use when searching for bridge events, agent responses, errors, or past conversation messages.
---

# Logs - System Logs and Message History

## System Logs (Bridge Events)

**Location**: `/Users/valorengels/src/ai/logs/bridge.events.jsonl`

Event types: `agent_response`, `agent_timeout`, `message_received`, `error`

```bash
# Recent events
tail -50 /Users/valorengels/src/ai/logs/bridge.events.jsonl

# Filter by type
grep '"type": "agent_response"' /Users/valorengels/src/ai/logs/bridge.events.jsonl | tail -20

# Pretty print
tail -10 /Users/valorengels/src/ai/logs/bridge.events.jsonl | jq .
```

## Message History (Telegram)

**Location**: `~/.valor/telegram_history.db` (SQLite)

Tables: `messages`, `links`

```bash
# Recent messages
sqlite3 ~/.valor/telegram_history.db "SELECT sender, substr(content, 1, 200), timestamp FROM messages ORDER BY timestamp DESC LIMIT 10"

# Messages from Valor
sqlite3 ~/.valor/telegram_history.db "SELECT substr(content, 1, 300), timestamp FROM messages WHERE sender='Valor' ORDER BY timestamp DESC LIMIT 10"

# Search messages
sqlite3 ~/.valor/telegram_history.db "SELECT sender, content, timestamp FROM messages WHERE content LIKE '%keyword%' ORDER BY timestamp DESC LIMIT 10"

# List links
sqlite3 ~/.valor/telegram_history.db "SELECT url, title, sender, timestamp FROM links ORDER BY timestamp DESC LIMIT 10"
```

## Important

The file `/Users/valorengels/src/ai/data/telegram_history.db` is NOT used. Always use `~/.valor/telegram_history.db`.
