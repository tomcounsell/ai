# Telegram History Tool

Search Telegram conversation history with relevance scoring.

## Overview

This tool provides local storage and search for Telegram messages:
- Store messages in SQLite database
- Keyword search with relevance scoring
- Time-based filtering
- Chat statistics
- Link storage and management

## Installation

```bash
# Install the package (creates valor-history CLI)
pip install -e /Users/valorengels/src/ai

# Verify installation
valor-history --help
```

## CLI Usage

The `valor-history` command provides easy access to message history:

```bash
# Search messages across all chats
valor-history search "authentication flow"

# Search in a specific group
valor-history search "error handling" --group "Dev: Valor" --days 30

# Show recent messages from a group
valor-history recent --group "Dev: Valor" --limit 20

# List known groups/chats
valor-history groups

# Search/list stored links
valor-history links --domain github.com
valor-history links --status unread

# Show statistics
valor-history stats
valor-history stats --group "Dev: Valor"

# Output as JSON (for scripting)
valor-history search "query" --json
```

### Commands

| Command | Description |
|---------|-------------|
| `search <query>` | Search message history |
| `recent` | Show recent messages from a group |
| `groups` | List known groups/chats |
| `links` | Search or list stored links |
| `stats` | Show statistics |

### Options

| Option | Description |
|--------|-------------|
| `--group, -g` | Group name or chat ID |
| `--days, -d` | Search within last N days |
| `--limit, -n` | Maximum results |
| `--json` | Output as JSON |

## Python API

### Quick Start

```python
from tools.telegram_history import search_history, store_message

# Store a message
store_message(
    chat_id="123456",
    content="Hello, world!",
    sender="Alice"
)

# Search history
result = search_history("hello", chat_id="123456")
for msg in result["results"]:
    print(f"{msg['sender']}: {msg['content']}")
```

## API Reference

### search_history()

```python
def search_history(
    query: str,
    chat_id: str,
    max_results: int = 5,
    max_age_days: int = 30,
    db_path: Path | None = None,
) -> dict
```

**Parameters:**
- `query`: Search query
- `chat_id`: Telegram chat ID
- `max_results`: Maximum results (default: 5)
- `max_age_days`: Time window in days (default: 30)
- `db_path`: Custom database path

**Returns:**
```python
{
    "query": str,
    "chat_id": str,
    "results": [
        {
            "id": int,
            "message_id": int,
            "sender": str,
            "content": str,
            "timestamp": str,
            "relevance_score": float
        }
    ],
    "total_matches": int,
    "time_window_days": int
}
```

### store_message()

```python
def store_message(
    chat_id: str,
    content: str,
    sender: str | None = None,
    message_id: int | None = None,
    timestamp: datetime | None = None,
    message_type: str = "text",
    db_path: Path | None = None,
) -> dict
```

Store a message in the history database.

### get_recent_messages()

```python
def get_recent_messages(
    chat_id: str,
    limit: int = 10,
    db_path: Path | None = None,
) -> dict
```

Get recent messages from a chat.

### get_chat_stats()

```python
def get_chat_stats(chat_id: str, db_path: Path | None = None) -> dict
```

Get statistics for a chat.

## Relevance Scoring

Messages are scored based on:
- **Exact match**: +0.5 for query found in content
- **Word match**: +0.2 for each matching word
- **Recency**: +0.3 for recent messages (scaled by age)

## Storage

Messages are stored in SQLite at `~/.valor/telegram_history.db`.

## Error Handling

```python
result = search_history(query, chat_id)

if "error" in result:
    print(f"Search failed: {result['error']}")
else:
    for msg in result["results"]:
        print(msg["content"])
```

## Troubleshooting

### No Results Found
- Check that messages are stored for the chat
- Expand the time window with `max_age_days`
- Verify the chat_id is correct
