# Telegram History Tool

Search Telegram conversation history with relevance scoring.

## Overview

This tool provides local storage and search for Telegram messages:
- Store messages in SQLite database
- Keyword search with relevance scoring
- Time-based filtering
- Chat statistics

## Installation

No external dependencies required. Uses local SQLite storage.

## Quick Start

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
