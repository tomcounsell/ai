# SMS Reader

Read SMS and iMessage messages from the macOS Messages app, with automatic 2FA code extraction.

## Overview

This tool provides programmatic access to the macOS Messages database (`~/Library/Messages/chat.db`), allowing you to:

- Read recent messages from any sender
- Search messages by content or sender
- Automatically extract 2FA verification codes
- List all message senders/contacts

## Requirements

- **macOS** with Messages app
- **Full Disk Access** permission for the terminal/process running the code
  - Go to: System Preferences > Security & Privacy > Privacy > Full Disk Access
  - Add your terminal application (Terminal, iTerm2, VS Code, etc.)

## Installation

The tool is included in the ai repository. No additional installation needed.

## Quick Start

```python
from tools.sms_reader import get_2fa, get_recent_messages, search_messages

# Get the most recent 2FA code (last 5 minutes)
code = get_2fa(minutes=5)
if code:
    print(f"Your 2FA code is: {code}")

# Get recent messages
messages = get_recent_messages(limit=10)
for msg in messages:
    print(f"{msg['sender']}: {msg['text']}")

# Search for specific messages
results = search_messages("verification", limit=5)
```

## API Reference

### `get_2fa(minutes=5, sender=None) -> str | None`

Quick function to get just the 2FA code string.

```python
code = get_2fa(minutes=5)  # Last 5 minutes
code = get_2fa(sender="+1555")  # From specific sender
```

### `get_latest_2fa_code(minutes=10, sender=None) -> dict | None`

Get detailed information about the most recent 2FA code.

```python
result = get_latest_2fa_code(minutes=10)
if result:
    print(result['code'])      # The extracted code
    print(result['message'])   # Full message text
    print(result['sender'])    # Phone number or email
    print(result['date'])      # ISO timestamp
```

### `get_recent_messages(limit=20, sender=None, since_minutes=None, include_sent=False) -> list[dict]`

Get recent messages with optional filtering.

```python
# Last 20 messages (received only)
messages = get_recent_messages()

# Last 10 messages from a specific sender
messages = get_recent_messages(limit=10, sender="+1555")

# Messages from last hour
messages = get_recent_messages(since_minutes=60)

# Include sent messages too
messages = get_recent_messages(include_sent=True)
```

### `search_messages(query, limit=20, since_minutes=None) -> list[dict]`

Search messages by text content.

```python
# Search for "verification"
results = search_messages("verification")

# Search in last hour only
results = search_messages("code", since_minutes=60)
```

### `list_senders(limit=50, since_days=30) -> list[dict]`

List unique message senders with message counts.

```python
senders = list_senders()
for s in senders:
    print(f"{s['sender']}: {s['message_count']} messages")
```

### `extract_codes_from_text(text) -> list[str]`

Extract potential verification codes from text.

```python
codes = extract_codes_from_text("Your code is 123456")
# Returns: ['123456']
```

## Use Cases

### Automated 2FA for Scripts

```python
from tools.sms_reader import get_2fa
import time

# Wait for 2FA code
for _ in range(30):  # Try for 30 seconds
    code = get_2fa(minutes=1)
    if code:
        print(f"Got code: {code}")
        break
    time.sleep(1)
```

### Monitor Messages from Service

```python
from tools.sms_reader import get_recent_messages

# Get messages from a short code
messages = get_recent_messages(sender="12345", since_minutes=60)
```

## Troubleshooting

### "Cannot open Messages database" Error

Grant Full Disk Access to your terminal:
1. Open System Preferences
2. Go to Security & Privacy > Privacy
3. Select Full Disk Access
4. Add your terminal application

### No Messages Found

- Make sure you've used the Messages app to send/receive messages
- Check that you're looking at the right time range
- Verify the sender filter matches (partial matches work)

## Database Schema

The tool reads from these tables:
- `message` - Message content, timestamps, metadata
- `handle` - Phone numbers and email addresses
- `chat` - Conversation metadata
- `chat_message_join` - Links messages to chats
