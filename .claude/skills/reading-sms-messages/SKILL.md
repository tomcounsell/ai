---
name: reading-sms-messages
description: Read SMS and iMessage from macOS Messages app. Use when getting 2FA codes, checking recent texts, or searching message history.
---

# SMS Reader

**Location**: `~/Library/Messages/chat.db` (macOS Messages database)

**Tool**: `tools/sms_reader/__init__.py`

## Quick Usage

```python
from tools.sms_reader import get_2fa, get_recent_messages, search_messages, get_latest_2fa_code

# Get 2FA code from last 5 minutes
code = get_2fa(minutes=5)
print(f"Code: {code}")

# Get detailed 2FA info
result = get_latest_2fa_code(minutes=10)
if result:
    print(f"Code: {result['code']}")
    print(f"From: {result['sender']}")
    print(f"Message: {result['message']}")

# Recent messages
messages = get_recent_messages(limit=10)
for m in messages:
    print(f"{m['sender']}: {m['text']}")

# Search
results = search_messages("verification", limit=5)
```

## Functions

| Function | Description |
|----------|-------------|
| `get_2fa(minutes=5)` | Returns just the code string, or None |
| `get_latest_2fa_code(minutes=10)` | Returns dict with code, message, sender, date |
| `get_recent_messages(limit=20, sender=None, since_minutes=None)` | List of message dicts |
| `search_messages(query, limit=20)` | Search by content |
| `list_senders(limit=50, since_days=30)` | List senders with message counts |

## Common Patterns

### Wait for 2FA code
```python
from tools.sms_reader import get_2fa
import time

for _ in range(30):  # 30 second timeout
    code = get_2fa(minutes=1)
    if code:
        print(f"Got code: {code}")
        break
    time.sleep(1)
```

### Get code from specific sender
```python
from tools.sms_reader import get_latest_2fa_code

# Partial match on sender
result = get_latest_2fa_code(minutes=10, sender="+1555")
```

## Requirements

- macOS with Messages app
- Full Disk Access for Python (System Settings > Privacy & Security > Full Disk Access)
- Python.app added: `/Library/Frameworks/Python.framework/Versions/3.12/Resources/Python.app`

## Database Schema

Key tables in `chat.db`:
- `message` - text, date, is_from_me, handle_id
- `handle` - phone numbers/emails
- `chat` - conversation metadata
- `chat_message_join` - links messages to chats

Apple timestamps are nanoseconds since 2001-01-01 (add 978307200 seconds and divide by 1e9 for Unix time).
