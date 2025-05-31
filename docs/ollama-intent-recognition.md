# Ollama-Based Intent Recognition System

This document describes the comprehensive intent recognition preprocessing system implemented for Telegram messages, using local Ollama models for fast and intelligent message classification.

## Overview

The intent recognition system provides intelligent preprocessing for Telegram messages through:

1. **Local Ollama Classification** - Fast, local LLM-based intent detection
2. **Visual Reaction Feedback** - Real-time emoji reactions showing processing status
3. **Intent-Based Tool Control** - Dynamic tool access optimization based on detected intent
4. **Specialized System Prompts** - Intent-specific AI behavior and response optimization

## System Architecture

```
Message Received
       ↓
👀 Initial Reaction (Received)
       ↓
Ollama Intent Classification
       ↓
🎯 Intent-Specific Reaction (⚙️🔍🎨📋💬etc.)
       ↓
Tool Access Control Applied
       ↓
Intent-Specific System Prompt
       ↓
Optimized AI Processing
       ↓
✅ Success / ❌ Error Reaction
```

## Message Intent Types

The system classifies messages into the following intent categories:

### 1. **Casual Chat** (`casual_chat`) 💬
- **Examples**: "Hey, how are you?", "Good morning!", "Thanks for the help"
- **Reaction**: 💬
- **Tools**: Chat history, basic search, conversation context
- **Behavior**: Conversational, warm, context-aware responses

### 2. **Question Answer** (`question_answer`) ❓
- **Examples**: "What is machine learning?", "How does authentication work?"
- **Reaction**: ❓
- **Tools**: Web search, information retrieval, knowledge base
- **Behavior**: Factual, comprehensive, well-structured answers

### 3. **Project Query** (`project_query`) 📋
- **Examples**: "What's the status of PsyOptimal?", "Show me project deadlines"
- **Reaction**: 📋
- **Tools**: Notion integration, project management, task tracking
- **Behavior**: Professional, actionable, status-focused responses

### 4. **Development Task** (`development_task`) ⚙️
- **Examples**: "Fix the login bug", "Implement dark mode", "Run tests"
- **Reaction**: ⚙️
- **Tools**: Full development suite (edit, bash, git, etc.)
- **Behavior**: Technical, systematic, follows code conventions

### 5. **Image Generation** (`image_generation`) 🎨
- **Examples**: "Create an image of a sunset", "Generate a logo design"
- **Reaction**: 🎨
- **Tools**: DALL-E integration, creative tools
- **Behavior**: Creative, detailed artistic prompts, style-focused

### 6. **Image Analysis** (`image_analysis`) 👁️
- **Examples**: Messages containing images, "What's in this photo?"
- **Reaction**: 👁️
- **Tools**: Vision analysis, image processing
- **Behavior**: Observational, detailed visual description

### 7. **Web Search** (`web_search`) 🔍
- **Examples**: "What's the latest news about AI?", "Current weather in SF"
- **Reaction**: 🔍
- **Tools**: Real-time web search, current information
- **Behavior**: Research-focused, source-citing, current information

### 8. **Link Analysis** (`link_analysis`) 🔗
- **Examples**: Messages with URLs, "Analyze this article"
- **Reaction**: 🔗
- **Tools**: Web fetch, content analysis, link storage
- **Behavior**: Analytical, summarizing, insight-focused

### 9. **System Health** (`system_health`) 🏓
- **Examples**: "ping", "status", "health check"
- **Reaction**: 🏓
- **Tools**: System monitoring, health checks
- **Behavior**: Technical, metric-focused, operational

### 10. **Unclear** (`unclear`) 🤔
- **Examples**: Ambiguous or complex mixed requests
- **Reaction**: 🤔
- **Tools**: Safe subset, conversation context
- **Behavior**: Clarifying, helpful, question-asking

## Implementation Components

### 1. Intent Classification (`integrations/ollama_intent.py`)

```python
from integrations.ollama_intent import classify_message_intent, MessageIntent

# Classify a message
intent_result = await classify_message_intent(
    "Fix the authentication bug", 
    context={"chat_id": 12345, "is_group_chat": False}
)

print(f"Intent: {intent_result.intent.value}")
print(f"Confidence: {intent_result.confidence}")
print(f"Emoji: {intent_result.suggested_emoji}")
```

**Features:**
- Local Ollama integration with fallback to rule-based classification
- Confidence scoring and reasoning
- Context-aware classification (group vs DM, has images/links)
- Automatic emoji suggestion for reactions

### 2. Reaction Management (`integrations/telegram/reaction_manager.py`)

```python
from integrations.telegram.reaction_manager import (
    add_message_received_reaction,
    add_intent_based_reaction,
    complete_reaction_sequence
)

# Complete reaction sequence
await add_message_received_reaction(client, chat_id, message_id)
await add_intent_based_reaction(client, chat_id, message_id, intent_result)
await complete_reaction_sequence(client, chat_id, message_id, intent_result, success=True)
```

**Features:**
- Progressive reaction updates showing processing status
- Duplicate reaction prevention
- Error handling and fallback reactions
- Automatic cleanup of old reaction tracking
- Uses only valid Telegram reaction emojis to prevent REACTION_INVALID errors

**Current Emoji Mappings:**
- 😁 CASUAL_CHAT (beaming face)
- 🤔 QUESTION_ANSWER (thinking face) 
- 🕊️ PROJECT_QUERY (dove - peaceful project planning)
- ⚡ DEVELOPMENT_TASK (lightning - fast dev work)
- 🍓 IMAGE_GENERATION (strawberry - sweet creations)
- 🙈 IMAGE_ANALYSIS (see-no-evil monkey)
- 🗿 WEB_SEARCH (moai - ancient wisdom seeking)
- 🍾 LINK_ANALYSIS (champagne - celebrating discoveries)
- 🤝 SYSTEM_HEALTH (handshake - systems working together)
- 🤨 UNCLEAR (raised eyebrow - "what's that about?")
- 💯 Reserved for future AGREEMENT_INTENT

### 3. Tool Access Control (`integrations/intent_tools.py`)

```python
from integrations.intent_tools import get_intent_based_tools, get_claude_code_configuration

# Get allowed tools for intent
allowed_tools = get_intent_based_tools(intent_result)

# Get complete Claude Code configuration
config = get_claude_code_configuration(intent_result)
```

**Tool Access Patterns:**

| Intent | Priority Tools | Restricted Tools | Max Tools |
|--------|---------------|------------------|-----------|
| Development Task | edit, bash, write, read | create_image | 12 |
| Casual Chat | chat_context, telegram_history | edit, bash, write | 4 |
| Image Generation | create_image | edit, bash | 4 |
| Web Search | web_search, search_current_info | edit, bash | 6 |
| Project Query | notion_search, query_notion_projects | edit, bash | 6 |

### 4. System Prompts (`integrations/intent_prompts.py`)

```python
from integrations.intent_prompts import get_intent_system_prompt

# Generate intent-specific system prompt
system_prompt = get_intent_system_prompt(intent_result, context={
    "chat_id": 12345,
    "username": "developer",
    "is_group_chat": False
})
```

**Prompt Components:**
- Base Valor Engels identity and personality
- Intent-specific focus and behavioral guidance
- Tool usage instructions
- Context-aware conversation information

## Message Processing Flow

### 1. **Message Reception**
```python
# In handlers.py
await add_message_received_reaction(client, chat_id, message.id)
```
- Add 👀 reaction immediately to show message was seen
- Mark message as read

### 2. **Intent Classification**
```python
intent_result = await self._classify_message_intent(processed_text, message, chat_id)
```
- Send message to Ollama for classification
- Fall back to rule-based classification if Ollama unavailable
- Extract context (group chat, has images, has links)

### 3. **Intent Reaction**
```python
await add_intent_based_reaction(client, chat_id, message.id, intent_result)
```
- Add intent-specific emoji (⚙️🔍🎨📋💬etc.)
- Show user what type of processing will occur

### 4. **Tool Configuration**
```python
config = get_claude_code_configuration(intent_result)
allowed_tools = get_intent_based_tools(intent_result)
```
- Determine which tools are allowed for this intent
- Set tool priorities and restrictions
- Optimize for the specific task type

### 5. **System Prompt Generation**
```python
system_prompt = get_intent_system_prompt(intent_result, context)
```
- Generate intent-specific behavior instructions
- Include context and conversation information
- Set appropriate communication style

### 6. **AI Processing**
```python
answer = await handle_telegram_message_with_intent(
    message=processed_text,
    intent_result=intent_result,
    # ... other parameters
)
```
- Process message with optimized configuration
- Use intent-specific system prompt
- Apply tool access restrictions

### 7. **Completion Reaction**
```python
await complete_reaction_sequence(client, chat_id, message.id, intent_result, success=True)
```
- Add ✅ for successful completion
- Add ❌ for errors
- Complete the visual feedback cycle

## Configuration and Setup

### 1. **Ollama Setup**
```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a suitable model
ollama pull llama3.2:3b

# Start Ollama server
ollama serve
```

### 2. **Environment Configuration**
No additional environment variables needed - the system uses:
- `OLLAMA_URL`: Default `http://localhost:11434`
- `OLLAMA_MODEL`: Default `llama3.2:3b`

### 3. **Integration Testing**
```bash
# Run intent recognition tests
python tests/test_intent_recognition_system.py

# Test with live Ollama
python -c "
import asyncio
from integrations.ollama_intent import classify_message_intent
result = asyncio.run(classify_message_intent('Fix the login bug'))
print(f'Intent: {result.intent.value}, Confidence: {result.confidence}')
"
```

## Performance Characteristics

### **Intent Classification Speed**
- **Ollama (local)**: 200-500ms depending on model size
- **Fallback (rule-based)**: <1ms
- **Total overhead**: <1 second per message

### **Tool Configuration Speed**
- **Tool selection**: <10ms
- **Prompt generation**: <50ms
- **Overall impact**: Negligible

### **Reaction Management Speed**
- **Single reaction**: 50-100ms
- **Complete sequence**: 200-300ms with delays
- **User feedback**: Real-time progressive updates

## Error Handling

### **Ollama Unavailable**
```python
# Automatic fallback to rule-based classification
if ollama_failed:
    return fallback_classification(message, context)
```

### **Network Issues**
```python
# Graceful degradation
try:
    await classify_message_intent(message)
except Exception:
    # Use UNCLEAR intent with basic tools
    return IntentResult(intent=MessageIntent.UNCLEAR, ...)
```

### **Reaction Failures**
```python
# Continue processing even if reactions fail
try:
    await add_reaction(...)
except Exception as e:
    print(f"Warning: Could not add reaction: {e}")
    # Continue with message processing
```

## Example Flows

### **Development Task Flow**
```
User: "Fix the authentication bug in login.py"
   ↓
👀 (Received)
   ↓
🧠 Intent: development_task (confidence: 0.92)
   ↓
⚙️ (Development task reaction)
   ↓
🎯 Tools: [edit, bash, read, write, git, pytest]
   ↓
🎭 System Prompt: "Execute technical tasks with precision..."
   ↓
💻 AI: Analyzes code, fixes bug, runs tests, commits
   ↓
✅ (Success)
```

### **Image Generation Flow**
```
User: "Create a sunset over mountains"
   ↓
👀 (Received)
   ↓
🧠 Intent: image_generation (confidence: 0.88)
   ↓
🎨 (Creative task reaction)
   ↓
🎯 Tools: [create_image, dalle_generate]
   ↓
🎭 System Prompt: "Create compelling visual content..."
   ↓
🖼️ AI: Generates detailed artistic prompt, creates image
   ↓
✅ (Success)
```

### **Casual Chat Flow**
```
User: "How's your day going?"
   ↓
👀 (Received)
   ↓
🧠 Intent: casual_chat (confidence: 0.95)
   ↓
💬 (Conversation reaction)
   ↓
🎯 Tools: [chat_context, telegram_history]
   ↓
🎭 System Prompt: "Engage in natural, friendly conversation..."
   ↓
💬 AI: Warm, personal response based on conversation history
   ↓
✅ (Success)
```

## Monitoring and Analytics

### **Intent Distribution Tracking**
```python
# Log intent statistics
logger.info(f"Intent classified: {intent_result.intent.value} "
           f"(confidence: {intent_result.confidence:.2f}) - "
           f"{intent_result.reasoning}")
```

### **Performance Monitoring**
```python
# Track classification performance
start_time = time.time()
intent_result = await classify_message_intent(message)
classification_time = time.time() - start_time

if classification_time > 1.0:
    logger.warning(f"Slow intent classification: {classification_time:.2f}s")
```

### **Accuracy Assessment**
```python
# Monitor confidence scores
if intent_result.confidence < 0.7:
    logger.info(f"Low confidence classification: {intent_result.confidence:.2f}")
```

## Future Enhancements

### **1. Learning System**
- Track intent accuracy based on user feedback
- Adjust classification thresholds based on performance
- Improve rule-based fallback with usage patterns

### **2. Context Memory**
- Remember user preferences for intent handling
- Build user-specific intent patterns
- Improve classification with conversation history

### **3. Multi-Model Support**
- Support different Ollama models for different intent types
- A/B test different classification approaches
- Ensemble methods for improved accuracy

### **4. Advanced Tool Orchestration**
- Dynamic tool composition based on intent confidence
- Tool chaining for complex multi-intent messages
- Context-aware tool parameter optimization

## Best Practices

### **1. System Prompt Design**
- Keep intent-specific prompts focused and actionable
- Test prompts with actual user messages
- Balance specificity with flexibility

### **2. Tool Access Control**
- Be conservative with powerful tools (edit, bash)
- Provide clear tool restriction reasoning
- Allow manual override for trusted users

### **3. Reaction Management**
- Keep reaction sequences fast and visually clear
- Handle reaction failures gracefully
- Clean up tracking data regularly

### **4. Performance Optimization**
- Cache frequent classifications when appropriate
- Use async operations for all network calls
- Monitor and optimize slow components

## Troubleshooting

### **Common Issues**

**1. Ollama Not Responding**
```bash
# Check Ollama status
ollama list
curl http://localhost:11434/api/tags

# Restart Ollama
sudo systemctl restart ollama
```

**2. Intent Misclassification**
- Check message preprocessing (mentions, formatting)
- Verify context information accuracy
- Test with fallback classification

**3. Reaction Failures**
- Verify Telegram bot permissions
- Check rate limiting
- Test with simplified reactions

**4. Tool Access Issues**
- Verify tool name consistency
- Check intent-tool mappings
- Test with minimal tool sets

This intent recognition system provides a sophisticated yet practical approach to optimizing AI responses based on user intent, creating a more intelligent and efficient conversational experience.