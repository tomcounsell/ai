# Message Lifecycle Mapping

## Overview
This document maps the complete message lifecycle through the Telegram integration and Valor agent handlers, identifying all critical steps and integration points.

## Full Message Lifecycle

### Stage 1: Message Reception (integrations/telegram/handlers.py)

#### 1.1 Initial Message Processing (`handle_message`)
**Location**: `MessageHandler.handle_message()` (lines 95-223)

**Steps**:
1. **Extract basic info**: Chat ID, username, private/group detection
2. **Security validation**: `_should_handle_chat()` checks whitelisting
3. **Message acknowledgment**: Read receipt + received reaction
4. **Message type routing**: Photo, document, audio, video, or text
5. **Age filtering**: Check if message is too old (missed messages)
6. **Mention processing**: `_process_mentions()` for group handling
7. **Context building**: Reply chains and conversation history
8. **Response decision**: Private chats, mentions, dev groups only

**Critical Integration Points**:
- Chat whitelist validation
- Reaction manager integration  
- Chat history storage
- Intent classification system

#### 1.2 Message Type Handlers
**Handlers**: `_handle_photo_message`, `_handle_document_message`, etc.

**Flow**:
1. Security check (mentions/private/dev groups)
2. Store in chat history with type markers
3. Route to Valor agent with enhanced context
4. Process agent response

### Stage 2: Intent Classification (`_classify_message_intent`)
**Location**: Lines 355-386

**Steps**:
1. **Context preparation**: Chat type, username, media detection
2. **Ollama classification**: Call `classify_message_intent()`
3. **Fallback handling**: Default to UNCLEAR on errors
4. **Result processing**: Intent, confidence, reasoning

### Stage 3: Message Routing (`_route_message_with_intent`)
**Location**: Lines 449-483

**Steps**:
1. **System commands**: Handle ping separately
2. **Intent classification**: Get intent result
3. **Reaction feedback**: Intent-based visual reactions
4. **Valor agent routing**: Call `_handle_with_valor_agent_intent()`
5. **Completion feedback**: Success/error reactions

### Stage 4: Valor Agent Integration (`_handle_with_valor_agent_intent`)
**Location**: Lines 388-447

**Steps**:
1. **Context gathering**: Notion data, chat history, priority detection
2. **Reply context**: Handle reply chains with internal ID mapping
3. **Agent invocation**: Call `agents.valor.handlers.handle_telegram_message_with_intent()`
4. **Response processing**: Handle text/image responses
5. **Error fallback**: Fall back to regular handler on failure

### Stage 5: Valor Agent Processing (agents/valor/handlers.py)

#### 5.1 Main Handler (`handle_telegram_message_with_intent`)
**Location**: Lines 163-290

**Steps**:
1. **Context preparation**: Build `ValorContext` with all data
2. **Intent integration**: Get intent-specific system prompt
3. **Message enhancement**: Add context, detect mixed content
4. **System prompt modification**: Temporarily use intent-specific prompt
5. **Agent execution**: Run `valor_agent.run()` with enhanced message
6. **Cleanup**: Restore original system prompt

#### 5.2 Legacy Handler (`handle_telegram_message`)
**Location**: Lines 63-160

**Steps** (similar but without intent):
1. **Context preparation**: Build `ValorContext`
2. **Message enhancement**: Add chat history and Notion data
3. **Mixed content detection**: Identify text+image messages
4. **Agent execution**: Run `valor_agent.run()`

### Stage 6: Agent Tool Execution
**Location**: `agents/valor/agent.py` (PydanticAI agent)

**Available Tools**:
- `search_current_info`: Web search via Perplexity
- `create_image`: DALL-E image generation
- `analyze_shared_image`: Vision analysis
- `delegate_coding_task`: Development tasks
- `save_link_for_later`: Link storage
- `search_saved_links`: Link retrieval
- `query_notion_projects`: Project data

### Stage 7: Response Processing (`_process_agent_response`)
**Location**: Lines 969-1056

**Steps**:
1. **Response validation**: Content validation and encoding
2. **Image handling**: Parse `TELEGRAM_IMAGE_GENERATED|` format
3. **Content splitting**: Handle >4000 character responses  
4. **Telegram delivery**: Send via `_safe_reply()`
5. **History storage**: Store response in chat history

## Critical Integration Points

### 1. Chat History Management
- **Storage**: Every step stores messages in chat history
- **Context**: Retrieved for agent context building
- **Reply chains**: Internal ID mapping for threaded conversations

### 2. Intent System Integration
- **Classification**: Ollama-based intent detection
- **Reactions**: Visual feedback based on intent
- **Optimization**: Intent-specific system prompts and tool usage

### 3. Security & Access Control
- **Whitelist validation**: Environment-based chat filtering
- **Workspace isolation**: Chat-to-workspace mapping
- **Permission checks**: Multi-layer security validation

### 4. Error Handling & Fallbacks
- **Intent fallback**: Default to UNCLEAR intent
- **Agent fallback**: Regular handler if intent handler fails
- **Message validation**: Content encoding and safety checks
- **Reaction errors**: Continue processing if reactions fail

## Message Flow Summary

```
Telegram Message
    ↓
1. Security Check (whitelist)
    ↓
2. Message Type Detection
    ↓
3. Intent Classification (Ollama)
    ↓
4. Context Building (history, Notion, replies)
    ↓
5. Valor Agent Processing (with intent optimization)
    ↓
6. Tool Execution (web search, images, Notion, etc.)
    ↓
7. Response Processing (validation, formatting)
    ↓
8. Telegram Delivery + History Storage
```

## Key Data Flows

### Context Data
- **Chat ID**: Flows through entire pipeline
- **Username**: Used for personalization and security
- **Chat History**: Enhanced at each step, used for agent context
- **Intent Result**: Optimizes prompts and tool selection
- **Notion Data**: Added for priority questions and group mappings

### Error Propagation
- **Intent errors**: Fallback to UNCLEAR, continue processing
- **Agent errors**: Fallback to regular handler, then error message
- **Response errors**: Validation and safe fallbacks
- **Reaction errors**: Logged but don't stop message processing

## Testing Strategy Requirements

Based on this mapping, comprehensive tests need to cover:

1. **Each stage independently**: Unit tests for each handler method
2. **Integration flows**: End-to-end message processing
3. **Error scenarios**: All fallback mechanisms
4. **Security boundaries**: Whitelist and permission validation
5. **Context building**: Chat history and Notion integration
6. **Intent optimization**: Intent-specific behavior verification
7. **Response handling**: All message types and formats