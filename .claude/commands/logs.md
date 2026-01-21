# Logs - System Logs and Message History

Find system logs and Telegram message histories quickly.

## Two Separate Data Sources

### 1. System Logs (Bridge Events)
**Location**: `/Users/valorengels/src/ai/logs/bridge.events.jsonl`
**Format**: JSONL (one JSON object per line)
**Contains**: Agent responses, timeouts, errors, system events

**Event types**:
- `agent_response` - Valor's responses to users
- `agent_timeout` - When response generation timed out
- `message_received` - Incoming messages
- `error` - System errors

**How to query**:
```bash
# Recent events (last 50)
tail -50 /Users/valorengels/src/ai/logs/bridge.events.jsonl

# Filter by event type
grep '"type": "agent_response"' /Users/valorengels/src/ai/logs/bridge.events.jsonl | tail -20

# Pretty print recent events
tail -10 /Users/valorengels/src/ai/logs/bridge.events.jsonl | jq .

# Search for specific content
grep -i "keyword" /Users/valorengels/src/ai/logs/bridge.events.jsonl | tail -10
```

### 2. Message History (Telegram Messages)
**Location**: `~/.valor/telegram_history.db` (SQLite)
**Tables**: `messages`, `links`

**Messages table schema**:
- `id` - Primary key
- `chat_id` - Telegram chat ID
- `message_id` - Telegram message ID
- `sender` - Who sent the message
- `content` - Message text
- `timestamp` - When it was sent
- `message_type` - Type (text, photo, etc.)

**Links table schema**:
- `id`, `url`, `final_url`, `title`, `description`, `domain`
- `sender`, `chat_id`, `message_id`, `timestamp`
- `tags`, `notes`, `status`, `ai_summary`

**How to query**:
```bash
# Count all messages
sqlite3 ~/.valor/telegram_history.db "SELECT COUNT(*) FROM messages"

# Recent messages
sqlite3 ~/.valor/telegram_history.db "SELECT sender, substr(content, 1, 200), timestamp FROM messages ORDER BY timestamp DESC LIMIT 10"

# Messages from Valor
sqlite3 ~/.valor/telegram_history.db "SELECT substr(content, 1, 300), timestamp FROM messages WHERE sender='Valor' ORDER BY timestamp DESC LIMIT 10"

# Search messages
sqlite3 ~/.valor/telegram_history.db "SELECT sender, content, timestamp FROM messages WHERE content LIKE '%keyword%' ORDER BY timestamp DESC LIMIT 10"

# List all links
sqlite3 ~/.valor/telegram_history.db "SELECT url, title, sender, timestamp FROM links ORDER BY timestamp DESC LIMIT 10"

# Messages by chat
sqlite3 ~/.valor/telegram_history.db "SELECT DISTINCT chat_id, COUNT(*) as count FROM messages GROUP BY chat_id"
```

## Important Notes

1. **The data directory db is empty**: `/Users/valorengels/src/ai/data/telegram_history.db` exists but is NOT used. Always use `~/.valor/telegram_history.db`.

2. **System logs vs message history**:
   - System logs = raw events from the bridge (debugging, errors, full response payloads)
   - Message history = clean conversation records (searching past conversations)

3. **Python API** (for programmatic access):
```python
from tools.telegram_history import search_history, store_message

# Search
results = search_history(
    query="keyword",
    chat_id="chat_123",
    max_results=10,
    max_age_days=30
)

# Store
store_message(
    chat_id="chat_123",
    content="Hello",
    sender="User"
)
```
