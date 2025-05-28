# Message Handling Flow Documentation

This document provides a comprehensive overview of how Telegram messages are processed in the AI agent system, from initial receipt to final response.

## Overview

The message handling system processes incoming Telegram messages through a multi-stage pipeline that includes filtering, mention processing, agent routing, and response generation. The system supports both direct messages (DMs) and group chats with different handling logic for each.

## Entry Point

All messages enter through the `MessageHandler.handle_message()` method in `integrations/telegram/handlers.py`.

## Step-by-Step Flow

### 1. Initial Setup & Chat Filtering

```python
chat_id = message.chat.id
is_private_chat = message.chat.type == ChatType.PRIVATE
```

**Chat Filtering Check:**
- Calls `_should_handle_chat(chat_id, is_private_chat)`
- For **DMs**: Returns `self.allow_dms` (from `TELEGRAM_ALLOW_DMS` env var)
- For **Groups**: Returns `chat_id in self.allowed_groups` (from `TELEGRAM_ALLOWED_GROUPS`)
- **If filtered out**: Message is completely ignored, function returns early
- **Logs**: `"Ignoring message from {DM|group} {chat_id} (filtered by server configuration)"`

### 2. Message Type Detection

The system handles different message types with specialized handlers:

- **Photos**: → `_handle_photo_message()`
- **Documents**: → `_handle_document_message()`
- **Audio/Voice**: → `_handle_audio_message()`
- **Video**: → `_handle_video_message()`
- **No text content**: Message ignored
- **Text messages**: Continue to step 3

### 3. Message Age Check & Catch-up Handling

```python
if is_message_too_old(message.date.timestamp()):
```

**For old messages (catch-up scenario):**
- Add to `missed_messages_per_chat[chat_id]` collection
- Store in chat history for context
- Return early (don't process immediately)
- **Logs**: `"Collecting missed message from chat {chat_id}: {text[:50]}..."`

**For current messages:**
- Process any accumulated missed messages first via `_handle_missed_messages()`
- Generate catch-up response using AI if missed messages exist
- Clear missed messages collection

### 4. Bot Info & Mention Processing

```python
me = await client.get_me()
bot_username = me.username
bot_id = me.id
```

**Mention Processing via `_process_mentions()`:**

#### For Direct Messages (DMs):
- `is_mentioned = True` (always respond in private chats)
- `processed_text = message.text` (no mention removal needed)

#### For Group Chats:
- Check for `@{bot_username}` mentions in text
- Check for replies to bot's previous messages
- Check for text mention entities pointing to bot
- Remove mention text from `processed_text`
- `is_mentioned = True` only if bot was mentioned

**Error Handling:**
- If mention processing fails, fallback to `is_mentioned = is_private_chat`

### 5. Response Decision Gate

```python
if not (is_private_chat or is_mentioned):
    # Store message for context but don't respond
    self.chat_history.add_message(chat_id, "user", message.text)
    return
```

- **DMs**: Always proceed (is_private_chat = True)
- **Groups**: Only proceed if bot was mentioned
- **Filtered messages**: Still stored in chat history for context

### 6. Message Storage

```python
self.chat_history.add_message(chat_id, "user", processed_text)
```

Store the processed user message in chat history (with mentions removed).

### 7. Special Content Handling

#### Link-Only Messages:
```python
if is_url_only_message(processed_text):
    await self._handle_link_message(message, chat_id, processed_text)
    return
```

**Link handling process:**
- Extract URL from message
- Store link with AI analysis via `store_link_with_analysis()`
- Reply with "thx, saved." or "thx, saved. (had trouble analyzing)"
- Store response in chat history
- Return (don't continue to agent processing)

### 8. Message Routing

```python
await self._route_message(message, chat_id, processed_text)
```

#### Health Check (Ping):
```python
if text == "ping":
    await self._handle_ping(message, chat_id)
    return
```

**Ping response includes:**
- System health metrics (CPU, memory, disk, uptime)
- Bot status and available tools
- Notion connection status

#### Standard Processing:
All other messages route to `_handle_with_valor_agent()`.

### 9. Valor Agent Processing

```python
await self._handle_with_valor_agent(message, chat_id, processed_text)
```

#### Priority Question Detection:
```python
is_priority = is_user_priority_question(processed_text)
```

#### Notion Context Loading:

**For Group Chats:**
- Automatically get group-specific Notion database via `_get_notion_context_for_group()`
- Uses `get_telegram_group_project(chat_id)` to map group to Notion database
- Sets `notion_scout.db_filter` to project-specific database
- **Logs**: `"Using Notion database for {project_name} (group {chat_id})"`

**For Direct Messages:**
- Only get Notion context if `is_priority = True`
- Uses `_get_notion_context()` with project name detection from message text
- Searches for keywords: "psyoptimal", "flextrip", "psy", "flex"

#### Agent Invocation:
```python
from agents.valor.handlers import handle_telegram_message

answer = await handle_telegram_message(
    message=processed_text,
    chat_id=chat_id,
    username=message.from_user.username,
    is_group_chat=not is_private_chat,
    chat_history_obj=self.chat_history,
    notion_data=notion_data,
    is_priority_question=is_priority,
)
```

### 10. Response Processing

```python
await self._process_agent_response(message, chat_id, answer)
```

#### Image Generation Handling:
- Check for special format: `"TELEGRAM_IMAGE_GENERATED|{path}|{caption}"`
- Send image via `client.send_photo()`
- Clean up temporary image file
- Store caption in chat history

#### Text Response Handling:
- Split long messages (>4000 chars) into multiple parts
- Send via `message.reply()`
- Store full response in chat history

#### Error Handling:
- Catch all exceptions during agent processing
- Send error message: `"❌ Error processing message: {error}"`
- Store error in chat history

## Configuration

### Environment Variables

```bash
# Chat filtering for multi-server deployments
TELEGRAM_ALLOWED_GROUPS=-1001234567890,-1009876543210  # Comma-separated group IDs
TELEGRAM_ALLOW_DMS=true                                # true/false

# Example configurations:
# Server 1 (PsyOPTIMAL only): TELEGRAM_ALLOWED_GROUPS=-1001234567890 TELEGRAM_ALLOW_DMS=false
# Server 2 (FlexTrip only):   TELEGRAM_ALLOWED_GROUPS=-1009876543210 TELEGRAM_ALLOW_DMS=false  
# Server 3 (DMs only):        TELEGRAM_ALLOWED_GROUPS= TELEGRAM_ALLOW_DMS=true
```

### Notion Database Mapping

File: `integrations/notion/database_mapping.json`

```json
{
  "projects": {
    "PsyOPTIMAL": {
      "database_id": "1d22bc89-4d10-8079-8dcb-e7813b006c5c",
      "url": "https://www.notion.so/yudame/1d22bc894d1080798dcbe7813b006c5c",
      "description": "PsyOPTIMAL project tasks and management"
    }
  },
  "telegram_groups": {
    "-1001234567890": "PsyOPTIMAL",
    "-1009876543210": "FlexTrip"
  }
}
```

## Key Differences: DMs vs Groups

| Aspect | Direct Messages (DMs) | Group Chats |
|--------|----------------------|-------------|
| **Filtering** | `TELEGRAM_ALLOW_DMS` (all or none) | `TELEGRAM_ALLOWED_GROUPS` (whitelist) |
| **Response Trigger** | Always respond | Only when mentioned |
| **Mention Processing** | Skipped (always proceed) | Complex mention detection |
| **Notion Context** | Only for priority questions | Automatic group-specific database |
| **Text Processing** | `processed_text = message.text` | Mentions removed from text |

## Error Handling

The system includes comprehensive error handling at multiple levels:

1. **Mention Processing**: Fallback to DM-only mode if mention parsing fails
2. **Notion Context**: Continue without context if Notion queries fail
3. **Agent Processing**: Send error message if agent fails
4. **Response Processing**: Handle image/text response failures gracefully

## Logging

Key log messages to monitor:

- `"Chat filtering enabled: Only handling groups {groups}"`
- `"DM handling: Enabled/Disabled"`
- `"Ignoring message from {type} {chat_id} (filtered by server configuration)"`
- `"Using Notion database for {project} (group {chat_id})"`
- `"Processing message from chat {chat_id} (private: {bool}): '{text[:50]}...'"`

## Multi-Server Deployment

Each server instance can be configured to handle specific chats:

1. **Server filtering** happens immediately after message receipt
2. **Filtered messages** are completely ignored (remain unread for other servers)
3. **Group-specific Notion databases** are automatically selected
4. **No conflicts** between servers handling different chat sets

This architecture enables horizontal scaling across multiple servers while maintaining chat isolation and project-specific context.