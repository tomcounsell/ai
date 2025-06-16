# Message Handling Flow Documentation

This document provides a comprehensive overview of how Telegram messages are processed in the AI agent system using the **unified message handling architecture** implemented in 2024.

## Architecture Overview - Unified System ✅

The message handling system has been completely redesigned with a **5-step unified pipeline** that replaced the previous complex 19-step monolithic handler:

### Current Architecture (Implemented)
```
UnifiedMessageProcessor (159 lines total)
├── SecurityGate ─────── Access control, whitelisting, rate limiting
├── ContextBuilder ───── History, mentions, replies, workspace detection  
├── TypeRouter ─────── Message type detection & routing
├── AgentOrchestrator ── Unified agent routing with context
└── ResponseManager ─── Output formatting, delivery, error handling
```

### Benefits Achieved
- **91% complexity reduction**: 2,144 → 159 lines in main handler
- **5-step linear pipeline**: Simplified from 19-step complex flow
- **Component isolation**: Each component has single responsibility
- **Comprehensive testing**: >90% test coverage across all components
- **Production monitoring**: Real-time metrics and health checks

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

All messages enter through the unified architecture:
- **Primary**: `UnifiedMessageProcessor.process_message()` in `integrations/telegram/unified_processor.py`
- **Legacy**: `MessageHandler.handle_message()` in `integrations/telegram/handlers.py` (preserved for rollback)

## Unified Message Flow (5 Steps) ✅

The unified system processes all messages through a clean 5-step pipeline:

### 1. SecurityGate - Access Control & Validation

```python
# Step 1: Security validation
access_result = self.security_gate.validate_access(message)
if not access_result.allowed:
    return ProcessingResult.access_denied(access_result.reason)
```

**SecurityGate Component (208 lines)**:
- **Unified access control**: Single method handles all access decisions
- **DM whitelist**: Username-based validation with fallback support
- **Group filtering**: Environment-based chat filtering from `TELEGRAM_ALLOWED_GROUPS`
- **Rate limiting**: Built-in protection against message flooding
- **Early termination**: Filtered messages return immediately with clear reason

### 2. ContextBuilder - Unified Context & History

```python
# Step 2: Context building  
msg_context = await self.context_builder.build_context(message)
```

**ContextBuilder Component (317 lines)**:
- **Workspace detection**: Automatic mapping from chat ID to workspace configuration
- **Chat history**: Smart filtering with guaranteed recent context (last 2 messages + 6 hours)
- **Mention processing**: Unified mention detection and text cleaning
- **Reply context**: Thread awareness and conversation continuity
- **User feedback**: Read receipts and "👀" processing reactions
- **Message age**: Catch-up handling for missed messages

### 3. TypeRouter - Message Type Detection & Routing

```python
# Step 3: Type routing
plan = await self.type_router.route_message(msg_context)
```

**TypeRouter Component (251 lines)**:
- **Smart type detection**: Photos, documents, audio, video, text, URLs
- **Processing plan**: Creates strategy for each message type
- **Dev group logic**: Special handling for development team chats
- **System commands**: Health checks, ping responses, admin functions
- **Content analysis**: URL detection, code snippets, special patterns

### 4. AgentOrchestrator - Unified Agent Processing

```python
# Step 4: Agent processing
agent_response = await self.agent_orchestrator.process_with_agent(msg_context, plan)
```

**AgentOrchestrator Component (308 lines)**:
- **Single agent routing**: Unified entry point for all agent interactions
- **Context injection**: Enhanced prompts with chat_id, username, history
- **Notion integration**: Workspace-specific database queries
- **Priority detection**: Smart handling for priority questions
- **Streaming support**: Real-time response delivery

### 5. ResponseManager - Output Handling & Delivery

```python
# Step 5: Response delivery
delivery_result = await self.response_manager.deliver_response(agent_response, msg_context)
```

**ResponseManager Component (353 lines)**:
- **Format handling**: Text, images, documents, media responses
- **Telegram integration**: Proper message formatting and delivery
- **Error recovery**: Graceful handling of delivery failures
- **History storage**: Conversation tracking and persistence
- **Monitoring**: Response time tracking and health metrics

## Legacy Flow Reference (Archived)

The original 19-step complex flow has been completely replaced by the unified architecture. The legacy system has been archived and included:

- Complex mention processing across multiple methods
- Scattered access control logic
- 19-step processing pipeline
- Duplicate media handlers
- Mixed responsibilities in single methods

### Legacy vs Unified Comparison

| Aspect | Legacy System | Unified System |
|--------|---------------|----------------|
| **Lines of code** | 2,144 lines | 159 lines (91% reduction) |
| **Processing steps** | 19 complex steps | 5 clean steps |
| **Components** | Monolithic handler | 6 isolated components |
| **Duplication** | 6+ duplicate patterns | Single source of truth |
| **Test coverage** | Limited | >90% comprehensive |
| **Debugging** | Complex flow | Linear pipeline |

## Context Enhancement and Processing Features

The unified system provides sophisticated context management and processing capabilities:

### Message History and Context

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
- `psyoptimal` - PsyOPTIMAL project (working directory: `/Users/valorengels/src/psyoptimal/`)
- `deckfusion` - DeckFusion project (working directory: `/Users/valorengels/src/deckfusion/`)
- `flextrip` - FlexTrip project (working directory: `/Users/valorengels/src/flextrip/`)
- `yudame` - Yudame project (working directory: `/Users/valorengels/src/ai/`)
- `verkstad` - Verkstad project (working directory: `/Users/valorengels/src/verkstad/`)

#### Notion Context Loading:

**For Group Chats:**
- Automatically get group-specific Notion database via `_get_notion_context_for_group()`
- Uses consolidated `get_telegram_group_project(chat_id)` from `config/workspace_config.json`
- Sets project context for MCP pm_tools server to access project-specific database
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

### Agent Integration

The unified system integrates seamlessly with the valor_agent through the AgentOrchestrator component:

```python
# Unified agent processing with enhanced context
agent_response = await self.agent_orchestrator.process_with_agent(msg_context, plan)
```

**Key Integration Features:**
- **Single agent routing**: Unified entry point for all agent interactions
- **Context injection**: Enhanced prompts with chat_id, username, history
- **Notion integration**: Workspace-specific database queries
- **Priority detection**: Smart handling for priority questions
- **Streaming support**: Real-time response delivery

### Response Handling

The ResponseManager component handles all output formatting and delivery:

**Image Generation Support:**
- Special format handling: `"TELEGRAM_IMAGE_GENERATED|{path}|{caption}"`
- Automatic image upload and cleanup
- Caption storage in chat history

**Text Response Processing:**
- Message splitting for Telegram limits (>4000 chars)
- Streaming response support
- Full conversation history tracking

**Error Recovery:**
- Graceful error handling with user-friendly messages
- Comprehensive error logging and monitoring
- Automatic retry mechanisms where appropriate

## Configuration

### Environment Variables

```bash
# Chat filtering for multi-server deployments
TELEGRAM_ALLOWED_GROUPS=PsyOPTIMAL,DeckFusion Dev  # Comma-separated workspace names
# Note: DMs now use username whitelist in workspace_config.json instead of TELEGRAM_ALLOW_DMS

# Example configurations:
# Server 1 (PsyOPTIMAL only): TELEGRAM_ALLOWED_GROUPS=PsyOPTIMAL,PsyOPTIMAL Dev
# Server 2 (DeckFusion only): TELEGRAM_ALLOWED_GROUPS=Tom's Team,DeckFusion Dev
# Server 3 (DMs only):        TELEGRAM_ALLOWED_GROUPS= (DM users controlled by dm_whitelist)
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
      "working_directory": "/Users/valorengels/src/psyoptimal",
      "telegram_chat_ids": ["-1002600253717"],
      "aliases": ["psyoptimal", "PO"]
    },
    "DeckFusion Dev": {
      "database_id": "48a27df3-0342-4aa4-bd4c-0dec1ff908f4",
      "url": "https://www.notion.so/deckfusion/48a27df303424aa4bd4c0dec1ff908f4",
      "description": "DeckFusion development tasks and management",
      "workspace_type": "deckfusion",
      "working_directory": "/Users/valorengels/src/deckfusion",
      "telegram_chat_ids": ["-4851227604"],
      "aliases": ["deckfusion dev", "DF dev"]
    }
  },
  "telegram_groups": {
    "-1002600253717": "PsyOPTIMAL",
    "-4851227604": "DeckFusion Dev",
    ...
  }
}
```

**Key Features:**
- **Consolidated mapping**: Single file for all workspace configurations
- **Working directory isolation**: Each workspace has a single working directory for Claude Code execution
- **Telegram integration**: Direct chat ID to workspace mapping
- **Backward compatibility**: Legacy `integrations/notion/database_mapping.json` still supported

## Key Differences: DMs vs Groups vs Dev Groups

| Aspect | Direct Messages (DMs) | Regular Group Chats | Dev Groups (`is_dev_group: true`) |
|--------|----------------------|-------------------|-----------------------------------|
| **Filtering** | **User whitelist** (specific usernames) | `TELEGRAM_ALLOWED_GROUPS` (whitelist) | `TELEGRAM_ALLOWED_GROUPS` (whitelist) |
| **Response Trigger** | Always respond (if whitelisted) | Only when mentioned | **Always respond** |
| **Mention Processing** | Skipped (always proceed) | Complex mention detection | Complex mention detection |
| **Notion Context** | Only for priority questions | Automatic group-specific database | Automatic group-specific database |
| **Text Processing** | `processed_text = message.text` | Mentions removed from text | Mentions removed from text |
| **Working Directory** | User-specific or default | Workspace-specific | Workspace-specific |

### Dev Group Configuration

Dev groups are configured in `config/workspace_config.json` with the `is_dev_group` flag:

```json
{
  "workspaces": {
    "Yudame Dev": {
      "database_id": "****",
      "description": "Yudame development team tasks and management",
      "telegram_chat_ids": ["-4891178445"],
      "is_dev_group": true
    },
    "PsyOPTIMAL Dev": {
      "database_id": "****",
      "description": "PsyOPTIMAL development tasks and management",
      "telegram_chat_ids": ["-4897329503"],
      "is_dev_group": true
    },
    "DeckFusion Dev": {
      "database_id": "****",
      "description": "DeckFusion development tasks and management",
      "telegram_chat_ids": ["-4851227604"],
      "is_dev_group": true
    }
  }
}
```

**Current Dev Groups:**
- **Yudame Dev** (-4891178445) - AI development team
- **PsyOPTIMAL Dev** (-4897329503) - PsyOPTIMAL development team
- **DeckFusion Dev** (-4851227604) - DeckFusion development team

### DM User Whitelisting

Direct messages use a username-based whitelist system for security:

```json
{
  "dm_whitelist": {
    "description": "Users allowed to send direct messages to the bot",
    "default_working_directory": "/Users/valorengels/src/ai",
    "allowed_users": {
      "tomcounsell": {
        "username": "tomcounsell",
        "description": "Tom Counsell - Owner and Boss",
        "working_directory": "/Users/valorengels/src/ai"
      }
    }
  }
}
```

**DM Access Control:**
- **Username validation**: Only whitelisted usernames can send DMs
- **Case-insensitive matching**: @TomCounsell, @tomcounsell, @TOMCOUNSELL all work
- **Working directory isolation**: Each user gets their own Claude Code working directory
- **Security logging**: All DM access attempts are logged for audit

**Currently Whitelisted:**
- **@tomcounsell** (Tom Counsell - Owner/Boss)

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
- All groups currently show "access denied" until their workspace name is added to `TELEGRAM_ALLOWED_GROUPS` environment variable

### Workspace Security Features:

This architecture enables horizontal scaling across multiple servers while maintaining:
- **Chat isolation** between different workspaces
- **Project-specific context** from appropriate Notion databases
- **Directory access control** to prevent cross-workspace file operations
- **Audit logging** for all workspace access attempts
