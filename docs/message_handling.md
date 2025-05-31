# Message Handling Flow Documentation

This document provides a comprehensive overview of how Telegram messages are processed in the AI agent system, from initial receipt to final response.

## Related Documentation

- **[Telegram Integration](telegram-integration.md)** - Higher-level Telegram interface and persona overview
- **[Agent Architecture](agent-architecture.md)** - PydanticAI agent system and tool integration
- **[System Operations](system-operations.md)** - Environment setup and deployment guidance
- **[Testing Strategy](testing-strategy.md)** - Message handling validation and testing approaches

## Overview

The message handling system processes incoming Telegram messages through a multi-stage pipeline that includes filtering, read receipts, processing reactions, mention processing, agent routing, and response generation. The system supports both direct messages (DMs) and group chats with different handling logic for each.

**Key Features:**
- **Immediate confirmation**: Read receipts and "👀" reactions provide instant feedback
- **Multi-server filtering**: Environment-based chat filtering for horizontal scaling
- **Intelligent routing**: Context-aware agent selection with Notion database integration
- **Comprehensive error handling**: Graceful degradation at each processing stage

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

### 2. Message Confirmation & Processing Indicators

**Read Receipt:**
```python
await client.read_chat_history(chat_id, message.id)
```
- Marks the message as read in Telegram
- Sends read receipt to the sender
- Handled with try/catch to prevent processing interruption

**Processing Reaction:**
```python
await client.send_reaction(chat_id, message.id, "👀")
```
- Adds "👀" emoji reaction to the incoming message
- Provides immediate visual feedback that the bot is processing
- Applied to ALL message types (text, photos, documents, etc.)
- Handled with try/catch to prevent processing interruption

### 3. Message Type Detection

The system handles different message types with specialized handlers:

- **Photos**: → `_handle_photo_message()`
- **Documents**: → `_handle_document_message()`
- **Audio/Voice**: → `_handle_audio_message()`
- **Video**: → `_handle_video_message()`
- **No text content**: Message ignored
- **Text messages**: Continue to step 4

### 4. Message Age Check & Catch-up Handling

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

### 5. Bot Info & Mention Processing

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

### 6. Response Decision Gate

```python
if not (is_private_chat or is_mentioned):
    # Store message for context but don't respond
    self.chat_history.add_message(chat_id, "user", message.text)
    return
```

- **DMs**: Always proceed (is_private_chat = True)
- **Groups**: Only proceed if bot was mentioned
- **Filtered messages**: Still stored in chat history for context

### 7. Message Storage

```python
self.chat_history.add_message(chat_id, "user", processed_text)
```

Store the processed user message in chat history (with mentions removed).

### 8. Special Content Handling

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

### 9. Message Routing

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

### 10. Valor Agent Processing

```python
await self._handle_with_valor_agent(message, chat_id, processed_text)
```

#### Priority Question Detection:
```python
is_priority = is_user_priority_question(processed_text)
```

#### Context Enhancement with Message History:

**Recent Chat Context (Always Applied):**
```python
chat_history_obj.get_context(
    chat_id, 
    max_context_messages=8,  # Up to 8 messages total
    max_age_hours=6,         # Only from last 6 hours  
    always_include_last=2    # Always include last 2 messages regardless of age
)
```
- **Guaranteed context**: Last 2 messages always included (supports overnight conversations)
- **Soft time filtering**: Additional messages only from last 6 hours
- **Count limiting**: Maximum 8 messages total for context
- **Applied to**: All conversations (DMs and groups)
- **Format**: "Recent conversation:" with role and content

**Smart Filtering Logic:**
1. **Always include** last 2 messages (even if older than 6 hours)
2. **Fill remaining slots** (up to 8 total) with recent messages within 6 hours
3. **Score by relevance + recency** for optimal context selection

#### Workspace Isolation & Security:

**Workspace Validation (New):**
- All Telegram chats are mapped to specific workspaces in `config/workspace_config.json`
- Each workspace has isolated Notion database access and directory restrictions
- Uses `utilities/workspace_validator.py` for strict access control
- **Cross-workspace access is blocked** - DeckFusion chats cannot access PsyOPTIMAL data

**Workspace Types:**
- `psyoptimal` - PsyOPTIMAL project (restricted to `/Users/valorengels/src/psyoptimal/`)
- `deckfusion` - DeckFusion project (restricted to `/Users/valorengels/src/deckfusion/`)
- `flextrip` - FlexTrip project (restricted to `/Users/valorengels/src/flextrip/`)
- `yudame` - Yudame project (restricted to `/Users/valorengels/src/ai/`)
- `verkstad` - Verkstad project (restricted to `/Users/valorengels/src/verkstad/`)

#### Notion Context Loading:

**For Group Chats:**
- Automatically get group-specific Notion database via `_get_notion_context_for_group()`
- Uses consolidated `get_telegram_group_project(chat_id)` from `config/workspace_config.json`
- Sets `notion_scout.db_filter` to project-specific database
- **Logs**: `"Using Notion database for {project_name} (group {chat_id})"`
- **Security**: Workspace validator ensures chat can only access its mapped database

**For Direct Messages:**
- Only get Notion context if `is_priority = True`
- Uses `_get_notion_context()` with project name detection from message text
- Searches for keywords from workspace aliases in consolidated config
- **Security**: No workspace restrictions for DMs (user has full access)

#### Enhanced Message Construction:

**Context Combination:**
```python
# Chat history + Notion data + current message
enhanced_message = """
Recent conversation:
user: [previous message]
assistant: [previous response]

Current project data:
[notion context if applicable]

Current message: [new user message]
"""
```
- **Chat context**: Always included when available (smart filtered with guaranteed recent messages)
- **Notion context**: Added for priority questions or group-specific databases
- **Current message**: Clearly separated as the primary request

#### Agent Tools for Extended History Access:

**Search Conversation History Tool:**
```python
@valor_agent.tool
def search_conversation_history(ctx, search_query: str, max_results: int = 5)
```
- **Purpose**: Search full message history for specific topics or references
- **Use cases**: "that link I sent", "what we discussed yesterday", finding previous decisions
- **Search scope**: Last 30 days of conversation history
- **Scoring**: Relevance + recency weighted algorithm

**Get Conversation Context Tool:**
```python
@valor_agent.tool  
def get_conversation_context(ctx, hours_back: int = 24)
```
- **Purpose**: Get extended conversation summary beyond immediate context
- **Use cases**: Understanding broader conversation flow, seeing complete recent discussion
- **Scope**: Configurable hours back (default 24 hours)
- **Returns**: Formatted conversation summary with timestamps

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

### 11. Response Processing

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

### Workspace Configuration

**File**: `config/workspace_config.json` (Consolidated Configuration)

```json
{
  "workspaces": {
    "PsyOPTIMAL": {
      "database_id": "1d22bc89-4d10-8079-8dcb-e7813b006c5c",
      "url": "https://www.notion.so/yudame/1d22bc894d1080798dcbe7813b006c5c",
      "description": "PsyOPTIMAL team chat and project management",
      "workspace_type": "psyoptimal",
      "allowed_directories": [
        "/Users/valorengels/src/psyoptimal",
        "/Users/valorengels/src/psyoptimal/"
      ],
      "telegram_chat_ids": ["-1002600253717"],
      "aliases": ["psyoptimal", "PO"]
    },
    "DeckFusion Dev": {
      "database_id": "48a27df3-0342-4aa4-bd4c-0dec1ff908f4",
      "url": "https://www.notion.so/deckfusion/48a27df303424aa4bd4c0dec1ff908f4",
      "description": "DeckFusion development tasks and management",
      "workspace_type": "deckfusion",
      "allowed_directories": [
        "/Users/valorengels/src/deckfusion",
        "/Users/valorengels/src/deckfusion/"
      ],
      "telegram_chat_ids": ["-4851227604"],
      "aliases": ["deckfusion dev", "DF dev"]
    }
  },
  "telegram_groups": {
    "-1002600253717": "PsyOPTIMAL",
    "-4897329503": "PsyOPTIMAL Dev",
    "-4851227604": "DeckFusion Dev",
    "-4891178445": "Yudame Dev",
    "-4719889199": "Yudame",
    "-1002374450243": "Tom's Team",
    "-1002455228990": "Verkstad"
  }
}
```

**Key Features:**
- **Consolidated mapping**: Single file for all workspace configurations
- **Directory isolation**: Each workspace has allowed directory restrictions
- **Telegram integration**: Direct chat ID to workspace mapping
- **Backward compatibility**: Legacy `integrations/notion/database_mapping.json` still supported

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

Each server instance can be configured to handle specific chats with workspace isolation:

1. **Server filtering** happens immediately after message receipt
2. **Filtered messages** are completely ignored (remain unread for other servers)
3. **Group-specific Notion databases** are automatically selected from consolidated config
4. **Workspace isolation** enforced via `utilities/workspace_validator.py`
5. **No conflicts** between servers handling different chat sets

### Current Discovered Groups:

Based on `scripts/list_telegram_groups.py` output:

| Group Name | Chat ID | Members | Workspace | Status |
|------------|---------|---------|-----------|---------|
| **PsyOPTIMAL** | -1002600253717 | 4 | psyoptimal | ✅ Mapped |
| **PsyOPTIMAL Dev** | -4897329503 | 2 | psyoptimal | ✅ Mapped |
| **DeckFusion Dev** | -4851227604 | 2 | deckfusion | ✅ Mapped |
| **Yudame Dev Team** | -4891178445 | 2 | yudame | ✅ Mapped |
| **Yudame** | -4719889199 | 6 | yudame | ✅ Mapped |
| **Tom's Team** | -1002374450243 | 6 | deckfusion | ✅ Mapped |
| **Verkstad** | -1002455228990 | 7 | verkstad | ✅ Mapped |
| **PsyOptimal** | -4503471217 | ? | - | ⚠️ Legacy/Unmapped |
| **Golden Egg** | -1002527205614 | 5 | - | ⚠️ Unmapped |
| **Golden Egg** | -4785378420 | ? | - | ⚠️ Legacy/Unmapped |

**Notes:**
- ✅ **Mapped groups** have workspace configurations in `config/workspace_config.json`
- ⚠️ **Unmapped groups** are listed in `deprecated_mappings` section
- All groups currently show "access denied" until added to `TELEGRAM_ALLOWED_GROUPS` environment variable

### Workspace Security Features:

This architecture enables horizontal scaling across multiple servers while maintaining:
- **Chat isolation** between different workspaces
- **Project-specific context** from appropriate Notion databases  
- **Directory access control** to prevent cross-workspace file operations
- **Audit logging** for all workspace access attempts