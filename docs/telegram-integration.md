# Telegram Integration Guide

## Overview

The Telegram integration provides the primary user interface for the AI agent system, enabling conversational interactions with Valor Engels through both direct messages and group chats. This guide covers message handling, conversation flow, and the Valor Engels persona implementation.

## Architecture Overview

### Message Flow

```
Telegram Message → Handler → Agent Selection → PydanticAI Agent → Tool Execution → Response
```

**Key Components**:
- **Telegram Client** (`integrations/telegram/client.py`): Connection management
- **Message Handlers** (`integrations/telegram/handlers.py`): Message routing logic
- **Chat History** (`integrations/telegram/chat_history.py`): Conversation persistence
- **Response Handlers** (`integrations/telegram/response_handlers.py`): Output formatting

### Current Implementation

```python
# Message handling workflow
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main message handler for Telegram updates."""

    # Extract message context
    message_text = update.message.text
    chat_id = update.message.chat_id
    username = update.message.from_user.username

    # Determine message type and route appropriately
    if is_search_request(message_text):
        response = await handle_search_query(message_text, chat_id, context)
    elif is_notion_question(message_text):
        response = await handle_notion_question(message_text, chat_id, context)
    elif is_user_priority_question(message_text):
        response = await handle_priority_question(message_text, chat_id, context)
    else:
        response = await handle_general_question(message_text, chat_id, context)

    # Send response back to Telegram
    await update.message.reply_text(response, parse_mode='Markdown')
```

## Valor Engels Persona

### Persona Implementation

The Valor Engels persona is loaded from `integrations/persona.md` and integrated into the PydanticAI agent system prompt:

```python
def load_persona() -> str:
    """Load the Valor Engels persona from the persona document."""
    persona_file = Path(__file__).parent.parent / "integrations" / "persona.md"
    with open(persona_file) as f:
        return f.read()

# Agent configuration
telegram_chat_agent = Agent(
    "anthropic:claude-3-5-sonnet-20241022",
    deps_type=TelegramChatContext,
    system_prompt=f"""Based on this persona document, respond naturally as Valor Engels:

{PERSONA_CONTENT}

Additional context for Telegram conversations:
- Keep responses under 200 words for Telegram compatibility
- Use the chat history and context provided to maintain conversation continuity
- Remember: you're a human software engineer, not an AI assistant"""
)
```

### Persona Characteristics

**Professional Background**:
- Software engineer at Yudame
- German/Californian background
- Technical expertise in AI and development

**Communication Style**:
- Direct and practical approach
- Technical accuracy with accessible explanations
- Context-aware responses based on conversation history

**Capabilities**:
- Web search for current information
- Project management through Notion integration
- Code development delegation via Claude Code
- Technical consultation and problem-solving

## Message Types and Routing

### Search Requests

**Detection Pattern**:
```python
def is_search_request(text: str) -> bool:
    """Detect if message is requesting web search."""
    search_indicators = [
        "search for", "look up", "find information", "what's happening",
        "latest news", "current", "recent", "today"
    ]
    return any(indicator in text.lower() for indicator in search_indicators)
```

**Handling**:
```python
async def handle_search_query(query: str, chat_id: int, context):
    """Handle web search requests via Perplexity integration."""
    response = await telegram_chat_agent.run(
        message=query,
        deps=TelegramChatContext(
            chat_id=chat_id,
            is_search_request=True
        )
    )
    return response.output
```

### Notion/Project Questions

**Detection Pattern**:
```python
def is_notion_question(text: str) -> bool:
    """Detect project-related questions."""
    notion_keywords = [
        "project", "task", "psyoptimal", "flextrip", "notion",
        "database", "status", "progress"
    ]
    return any(keyword in text.lower() for keyword in notion_keywords)
```

**Handling**:
```python
async def handle_notion_question(question: str, chat_id: int, context):
    """Handle project data queries via NotionScout."""
    # Integrate with NotionScout agent for project data
    notion_data = await get_notion_context(question)

    response = await telegram_chat_agent.run(
        message=question,
        deps=TelegramChatContext(
            chat_id=chat_id,
            notion_data=notion_data,
            is_notion_question=True
        )
    )
    return response.output
```

### Priority Questions

**Detection Pattern**:
```python
def is_user_priority_question(text: str) -> bool:
    """Detect questions about work priorities."""
    priority_keywords = [
        "priority", "should i work on", "what should i do",
        "next task", "focus on", "important"
    ]
    return any(keyword in text.lower() for keyword in priority_keywords)
```

**Handling**:
```python
async def handle_priority_question(question: str, chat_id: int, context):
    """Handle priority questions with enhanced context."""
    return await handle_user_priority_question(
        question=question,
        chat_id=chat_id,
        chat_history_obj=context.chat_history,
        notion_scout=context.notion_scout
    )
```

## Chat History Management

### PydanticAI Integration

Chat history is managed through PydanticAI's built-in message history system with additional Telegram-specific context:

```python
async def handle_telegram_message(
    message: str,
    chat_id: int,
    username: str | None = None,
    is_group_chat: bool = False,
    chat_history_obj=None,
    notion_data: str | None = None,
    is_priority_question: bool = False,
) -> str:
    """Handle Telegram message with conversation continuity."""

    # Prepare agent context
    context = TelegramChatContext(
        chat_id=chat_id,
        username=username,
        is_group_chat=is_group_chat,
        notion_data=notion_data,
        is_priority_question=is_priority_question,
    )

    # Build enhanced message with recent context
    enhanced_message = message
    if chat_history_obj:
        telegram_messages = chat_history_obj.get_context(chat_id)
        if telegram_messages:
            recent_context = telegram_messages[-3:]  # Last 3 messages
            context_text = "Recent conversation context:\n"
            for msg in recent_context:
                context_text += f"{msg['role']}: {msg['content']}\n"
            enhanced_message = f"{context_text}\nCurrent message: {message}"

    # Run agent with context
    result = await telegram_chat_agent.run(enhanced_message, deps=context)
    return result.output
```

### Chat History Storage

```python
class ChatHistoryManager:
    """Manages conversation history for Telegram chats."""

    def __init__(self, storage_file: str = "chat_history.json"):
        self.storage_file = storage_file
        self.chat_histories = self._load_histories()

    def add_message(self, chat_id: int, role: str, content: str):
        """Add message to chat history."""
        if chat_id not in self.chat_histories:
            self.chat_histories[chat_id] = []

        self.chat_histories[chat_id].append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })

        # Keep recent history (last 50 messages)
        if len(self.chat_histories[chat_id]) > 50:
            self.chat_histories[chat_id] = self.chat_histories[chat_id][-50:]

        self._save_histories()

    def get_context(self, chat_id: int, limit: int = 10) -> list:
        """Get recent conversation context."""
        return self.chat_histories.get(chat_id, [])[-limit:]
```

## Group vs Direct Message Handling

### Message Filtering

```python
async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages in group chats."""
    message = update.message

    # Only respond to mentions or direct replies
    if not (message.reply_to_message and
            message.reply_to_message.from_user.id == context.bot.id) and \
       not any(entity.type == "mention" for entity in (message.entities or [])):
        return  # Ignore non-directed group messages

    # Process as regular message
    await handle_message(update, context)

async def handle_direct_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle direct messages."""
    # Process all direct messages
    await handle_message(update, context)
```

### Context Differentiation

```python
class TelegramChatContext(BaseModel):
    """Context for Telegram chat interactions."""

    chat_id: int
    username: str | None = None
    is_group_chat: bool = False  # Affects response style
    chat_history: list[dict[str, Any]] = []
    notion_data: str | None = None
    is_priority_question: bool = False
```

## Response Formatting

### Telegram-Specific Formatting

```python
def format_telegram_response(content: str, max_length: int = 4000) -> str:
    """Format response for Telegram compatibility."""

    # Truncate if too long
    if len(content) > max_length:
        content = content[:max_length-50] + "\n\n[Response truncated...]"

    # Escape Markdown special characters
    content = content.replace("_", "\\_")
    content = content.replace("*", "\\*")
    content = content.replace("`", "\\`")

    return content

async def send_telegram_response(update: Update, response: str):
    """Send formatted response to Telegram."""
    formatted_response = format_telegram_response(response)

    try:
        await update.message.reply_text(
            formatted_response,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
    except Exception as e:
        # Fallback to plain text if Markdown fails
        await update.message.reply_text(formatted_response)
```

### Response Length Management

```python
def ensure_telegram_compatibility(response: str) -> str:
    """Ensure response fits Telegram constraints."""

    # Target length for readability
    target_length = 400
    max_length = 4000  # Telegram limit

    if len(response) <= target_length:
        return response

    if len(response) <= max_length:
        return response  # Long but acceptable

    # Truncate intelligently
    sentences = response.split('. ')
    truncated = []
    current_length = 0

    for sentence in sentences:
        if current_length + len(sentence) > max_length - 100:
            break
        truncated.append(sentence)
        current_length += len(sentence)

    return '. '.join(truncated) + ".\n\n[Response truncated for Telegram...]"
```

## Error Handling and Recovery

### Network Error Handling

```python
async def robust_telegram_send(update: Update, message: str, retries: int = 3):
    """Send message with retry logic."""

    for attempt in range(retries):
        try:
            await update.message.reply_text(message, parse_mode='Markdown')
            return
        except NetworkError as e:
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
                continue
            else:
                # Final fallback
                await update.message.reply_text(
                    "⚠️ Network issue detected. Please try again."
                )
        except Exception as e:
            await update.message.reply_text(
                f"❌ Unexpected error: {str(e)[:100]}"
            )
            break
```

### Agent Error Recovery

```python
async def handle_agent_error(update: Update, error: Exception, context: dict):
    """Handle agent execution errors gracefully."""

    error_responses = {
        "rate_limit": "⏱️ Rate limit reached. Please wait a moment and try again.",
        "api_error": "🔌 Service temporarily unavailable. Please try again.",
        "timeout": "⏰ Request timed out. Please try a simpler query.",
        "default": "❌ Something went wrong. Please rephrase your request."
    }

    error_type = classify_error(error)
    response = error_responses.get(error_type, error_responses["default"])

    await update.message.reply_text(response)

    # Log for monitoring
    logger.error(f"Agent error in chat {context.get('chat_id')}: {str(error)}")
```

## Deployment and Configuration

### Environment Setup

```bash
# Required environment variables
TELEGRAM_BOT_TOKEN=your_bot_token
ANTHROPIC_API_KEY=your_anthropic_key
PERPLEXITY_API_KEY=your_perplexity_key
NOTION_API_KEY=your_notion_key

# Optional configuration
TELEGRAM_WEBHOOK_URL=https://your-domain.com/webhook
TELEGRAM_WEBHOOK_SECRET=your_webhook_secret
```

### Bot Configuration

```python
def create_telegram_application() -> Application:
    """Create and configure Telegram bot application."""

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Message handlers
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_direct_message
    ))

    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
        handle_group_message
    ))

    # Error handler
    application.add_error_handler(error_handler)

    return application
```

This integration provides a robust, conversational interface that leverages PydanticAI's capabilities while maintaining the natural feel of chatting with Valor Engels.
