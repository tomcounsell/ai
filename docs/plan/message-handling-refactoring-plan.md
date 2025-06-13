# Message Handling Refactoring Plan

## Executive Summary

**Goal**: Transform the current 1,994-line monolithic message handler into a clean, maintainable unified processing system following the [Unified Message Handling Architecture Design](unified-message-handling-design.md).

**Timeline**: 4-week phased implementation with gradual migration and comprehensive testing.

**Success Criteria**: 
- Reduce main handler complexity from 1,994 → ~400 lines
- Eliminate 4+ duplicate processing patterns
- Achieve <2s response time for 95% of messages
- Maintain 100% feature parity during transition

## Current State Analysis

### Critical Issue: Broken Emoji Reaction System

**URGENT**: The sophisticated emoji reaction workflow is partially implemented but critically broken. The system should follow this workflow:

1. **👀 Read Receipt**: Eyes emoji reaction when message first received
2. **Ollama Intent Classification**: Local intent detection with emoji mapping
3. **👨‍💻 Work Indicator**: Intent-specific reaction (coding, searching, etc.)
4. **Claude Code Integration**: Unified system with intent and prompt templates
5. **👍/❌ Completion Status**: Success or error reactions
6. **🔄 Error Recovery**: Automatic bug fixing when errors occur
7. **🔄 Huey Queue**: Promise-based async work management

#### ✅ What's Currently Working:
- **Ollama Intent Classification** (`integrations/ollama_intent.py`): Fully implemented with local models
- **Emoji Mapping** (`integrations/telegram/emoji_mapping.py`): 72 valid reactions with descriptions
- **Promise Queue** (`tasks/promise_tasks.py`): Huey-based async task management
- **Response Manager Reactions** (`integrations/telegram/components/response_manager.py`): Has `_add_reactions()` method

#### ❌ What's Critically Broken:
1. **Missing `reaction_manager.py`**: Referenced but doesn't exist
   ```python
   from .reaction_manager import add_message_received_reaction  # FILE NOT FOUND
   ```

2. **No Read Receipt System**: Missing 👀 emoji on message receipt

3. **Broken Intent→Reaction Bridge**: Ollama classification doesn't trigger reactions

4. **No Status Tracking**: Missing 👍 success / ❌ error reactions

5. **No Progress Indicators**: Missing 👨‍💻 work-in-progress reactions

6. **Incomplete Promise Integration**: Promise status changes don't trigger reactions

7. **No Error Recovery Loop**: System doesn't automatically fix errors

#### 🚨 Priority Fix Required:
The **`reaction_manager.py`** must be implemented to orchestrate:
- Initial read receipts (👀)
- Intent-based work indicators (👨‍💻, 🔍, etc.)
- Promise status reactions (⏳ → 👍/❌)
- Error recovery workflow

### Files Requiring Refactoring

#### Primary Targets (High Priority)
1. **`integrations/telegram/handlers.py`** (1,994 lines) - CRITICAL
   - Monolithic `MessageHandler` class
   - 432-line `handle_message()` method
   - 19-step processing pipeline

2. **`agents/valor/handlers.py`** (345 lines)
   - Duplicate agent routing logic
   - Mixed intent handling patterns

#### Secondary Targets (Medium Priority)  
3. **`integrations/telegram/client.py`** (253 lines)
   - Scattered initialization logic
   - Mixed lifecycle management

4. **`mcp_servers/telegram_tools.py`** (307 lines)
   - Duplicate context handling
   - Inconsistent validation patterns

#### Supporting Files (Low Priority)
5. **`utilities/missed_message_manager.py`** (480 lines)
6. **`integrations/telegram/missed_message_integration.py`** (73 lines)
7. Intent classification files
8. Reaction management utilities

### Duplicate Patterns Identified

#### 1. Message Type Processing (4 duplications)
- `_handle_photo_message()` (140+ lines)
- `_handle_document_message()` (88+ lines)
- `_handle_audio_message()` (168+ lines)
- `_handle_video_message()` (97+ lines)

**Common Pattern**: Mention processing → Dev group check → History storage → Error handling

#### 2. Access Control (6 duplications)
- Main handler whitelist check
- Each media handler access validation
- MCP tool chat ID validation
- Background task filtering
- Valor agent routing checks
- DM whitelist verification

#### 3. Context Building (5 duplications)
- Chat history retrieval in main handler
- History loading in each media handler
- Context injection in MCP tools
- Background context rebuilding
- Intent classification context

## Phase 0: URGENT - Fix Broken Emoji Reaction System (Priority)

### 0.1 Implement Missing ReactionManager

**File**: `integrations/telegram/reaction_manager.py`
```python
class ReactionManager:
    """Orchestrates the sophisticated emoji reaction workflow."""
    
    def __init__(self, client, ollama_classifier, promise_manager):
        self.client = client
        self.ollama_classifier = ollama_classifier  
        self.promise_manager = promise_manager
        
    async def add_read_receipt(self, chat_id: int, message_id: int):
        """Step 1: Add 👀 eyes emoji when message received."""
        
    async def add_intent_reaction(self, chat_id: int, message_id: int, intent: Intent):
        """Step 2: Add work indicator based on Ollama intent classification."""
        # 👨‍💻 for coding, 🔍 for search, 🧠 for analysis, etc.
        
    async def add_progress_reaction(self, chat_id: int, message_id: int, work_type: str):
        """Step 3: Add ⏳ or work-specific progress indicator."""
        
    async def add_completion_reaction(self, chat_id: int, message_id: int, success: bool, error: Exception = None):
        """Step 4: Add 👍 for success or ❌ for error."""
        
    async def trigger_error_recovery(self, chat_id: int, message_id: int, error: Exception):
        """Step 5: Start automated error recovery workflow."""
        
    async def monitor_promise_status(self, promise_id: str, chat_id: int, message_id: int):
        """Step 6: Monitor Huey promise and update reactions accordingly."""
```

**Fixes**: 
- Missing `add_message_received_reaction` import error
- No read receipt system (👀)
- No intent-based reactions (👨‍💻, 🔍, etc.)
- No status completion reactions (👍/❌)

**Timeline**: Day 1-2 (URGENT)

### 0.2 Integrate ReactionManager into UnifiedProcessor

**Modify**: `integrations/telegram/unified_processor.py`
```python
class UnifiedMessageProcessor:
    def __init__(self, telegram_bot: Any | None = None, valor_agent=None):
        # ... existing components ...
        self.reaction_manager = ReactionManager(telegram_bot, ollama_classifier, promise_manager)
        
    async def process_message(self, update: Any, context: Any) -> ProcessingResult:
        message = update.message
        
        # Step 0: Immediate read receipt
        await self.reaction_manager.add_read_receipt(message.chat.id, message.id)
        
        # Step 1: Security validation
        access_result = self.security_gate.validate_access(message)
        if not access_result.allowed:
            await self.reaction_manager.add_completion_reaction(
                message.chat.id, message.id, success=False, 
                error=Exception(access_result.reason)
            )
            return ProcessingResult.denied(access_result.reason)
            
        # Step 2: Context building  
        context = self.context_builder.build_context(message)
        
        # Step 3: Type routing + Intent classification
        plan = self.type_router.route_message(context)
        if plan.requires_intent:
            intent = await self.ollama_classifier.classify_intent(context.cleaned_text)
            await self.reaction_manager.add_intent_reaction(message.chat.id, message.id, intent)
            plan.intent = intent
        
        # Step 4: Agent processing with progress indicator
        try:
            await self.reaction_manager.add_progress_reaction(message.chat.id, message.id, "agent_processing")
            response = await self.agent_orchestrator.process_with_agent(context, plan)
            
            # Step 5: Response delivery
            result = await self.response_manager.deliver_response(response, context)
            
            # Step 6: Success reaction
            await self.reaction_manager.add_completion_reaction(message.chat.id, message.id, success=True)
            
            return result
            
        except Exception as e:
            # Step 6: Error reaction + recovery
            await self.reaction_manager.add_completion_reaction(message.chat.id, message.id, success=False, error=e)
            await self.reaction_manager.trigger_error_recovery(message.chat.id, message.id, e)
            raise
```

**Timeline**: Day 2-3 (URGENT)

### 0.3 Connect Ollama Intent Classification to Reactions

**Modify**: `integrations/ollama_intent.py`
```python
class OllamaIntentClassifier:
    INTENT_EMOJI_MAP = {
        "general_development": "👨‍💻",
        "web_search": "🔍", 
        "image_analysis": "👁️",
        "data_analysis": "📊",
        "system_maintenance": "🔧",
        "documentation": "📚",
        "testing": "🧪",
        "debugging": "🐛",
        "deployment": "🚀",
        "planning": "🧠"
    }
    
    async def classify_intent_with_reaction(self, text: str, chat_id: int, message_id: int, reaction_manager) -> Intent:
        """Classify intent and trigger appropriate reaction."""
        intent = await self.classify_intent(text)
        
        if intent and intent.intent_type in self.INTENT_EMOJI_MAP:
            emoji = self.INTENT_EMOJI_MAP[intent.intent_type]
            await reaction_manager.add_intent_reaction(chat_id, message_id, emoji)
            
        return intent
```

**Timeline**: Day 3 (URGENT)

### 0.4 Connect Promise Status to Reactions

**Modify**: `tasks/promise_tasks.py`
```python
@huey.task()
def update_promise_status_with_reactions(promise_id: str, status: str, chat_id: int = None, message_id: int = None):
    """Update promise status and trigger appropriate reactions."""
    # Update promise in database
    update_promise_status(promise_id, status)
    
    # Trigger reaction if Telegram context available
    if chat_id and message_id:
        from integrations.telegram.reaction_manager import ReactionManager
        from integrations.telegram.client import telegram_client
        
        reaction_manager = ReactionManager(telegram_client.client, None, None)
        
        if status == "completed":
            asyncio.create_task(reaction_manager.add_completion_reaction(chat_id, message_id, success=True))
        elif status == "failed":
            asyncio.create_task(reaction_manager.add_completion_reaction(chat_id, message_id, success=False))
        elif status == "in_progress":
            asyncio.create_task(reaction_manager.add_progress_reaction(chat_id, message_id, "promise_work"))
```

**Timeline**: Day 3-4 (URGENT)

### 0.5 Implement Error Recovery Workflow

**File**: `integrations/telegram/error_recovery.py`
```python
class ErrorRecoveryWorkflow:
    """Automated error recovery and bug fixing."""
    
    def __init__(self, claude_code_delegator, promise_manager):
        self.claude_code_delegator = claude_code_delegator
        self.promise_manager = promise_manager
        
    async def start_recovery(self, error: Exception, context: MessageContext, chat_id: int, message_id: int):
        """Start automated error recovery process."""
        
        # Create recovery promise
        recovery_promise = await self.promise_manager.create_promise(
            "error_recovery",
            {
                "error": str(error),
                "context": context.to_dict(),
                "chat_id": chat_id,
                "message_id": message_id
            }
        )
        
        # Delegate to Claude Code for automated fixing
        recovery_instructions = f"""
        URGENT ERROR RECOVERY NEEDED:
        
        Error: {error}
        Context: {context.cleaned_text}
        
        Please:
        1. Analyze the error and its root cause
        2. Fix any code issues that caused this error
        3. Test the fix to ensure it works
        4. Report back with the solution
        
        This is an automated error recovery triggered by the Telegram reaction system.
        """
        
        # This should trigger Claude Code to actually fix the error
        result = await self.claude_code_delegator.delegate_task(
            recovery_instructions,
            context.working_directory,
            "error_recovery"
        )
        
        # Update promise status based on recovery result
        if result.success:
            await self.promise_manager.complete_promise(recovery_promise.id, result.output)
        else:
            await self.promise_manager.fail_promise(recovery_promise.id, result.error)
```

**Timeline**: Day 4-5 (URGENT)

## Phase 1: Foundation Components (Week 1)

### 1.1 Create Core Data Structures

**File**: `integrations/telegram/models.py`
```python
@dataclass
class MessageContext:
    """Unified context object for all message processing."""
    message: TelegramMessage
    chat_id: int
    username: str
    workspace: str
    working_directory: str
    is_dev_group: bool
    is_mention: bool
    cleaned_text: str
    chat_history: List[Dict]
    reply_context: Optional[Dict]
    media_info: Optional[MediaInfo]

@dataclass  
class ProcessingPlan:
    """Strategy for processing this message."""
    message_type: MessageType
    requires_intent: bool
    agent_config: AgentConfig
    response_format: ResponseFormat

@dataclass
class AgentResponse:
    """Unified response from agent processing."""
    content: str
    media_attachments: List[MediaAttachment]
    reactions: List[str]
    metadata: Dict[str, Any]
```

**Timeline**: Day 1-2

### 1.2 Implement SecurityGate

**File**: `integrations/telegram/security.py`
```python
class SecurityGate:
    """Centralized access control and security validation."""
    
    def __init__(self, workspace_validator: WorkspaceValidator):
        self.workspace_validator = workspace_validator
        
    def validate_access(self, message: TelegramMessage) -> AccessResult:
        """Single method for all access control decisions."""
        # Consolidate all whitelist checking logic here
        
    def is_bot_self_message(self, message: TelegramMessage) -> bool:
        """Check if message is from bot itself."""
        
    def check_rate_limits(self, chat_id: int, username: str) -> bool:
        """Rate limiting if needed."""
```

**Consolidates**:
- `_should_handle_chat()` from main handler
- Whitelist checks from 5+ locations
- Self-message filtering from multiple handlers

**Timeline**: Day 2-3

### 1.3 Build ContextBuilder

**File**: `integrations/telegram/context.py`
```python
class ContextBuilder:
    """Unified context building for all message types."""
    
    def build_context(self, message: TelegramMessage) -> MessageContext:
        """Single method to build complete message context."""
        # Extract workspace
        # Load chat history  
        # Process mentions
        # Detect reply context
        # Build media info
        
    def _extract_workspace(self, chat_id: int) -> WorkspaceInfo:
        """Get workspace info from chat ID."""
        
    def _load_chat_history(self, chat_id: int, limit: int = 10) -> List[Dict]:
        """Load recent conversation history."""
        
    def _process_mentions(self, text: str, entities: List) -> Tuple[bool, str]:
        """Extract mentions and clean text."""
        
    def _detect_reply_context(self, message: TelegramMessage) -> Optional[Dict]:
        """Extract reply-to message context."""
```

**Consolidates**:
- Mention processing from 6+ locations
- Chat history retrieval from 5+ handlers
- Workspace detection logic
- Text cleaning patterns

**Timeline**: Day 3-4

### 1.4 Add Comprehensive Testing

**Files**: `tests/test_message_foundation.py`
```python
class TestSecurityGate:
    def test_access_validation_dev_groups(self):
    def test_whitelist_checking_dms(self):
    def test_rate_limiting(self):
    def test_self_message_detection(self):

class TestContextBuilder:
    def test_context_building_text_messages(self):
    def test_context_building_media_messages(self):
    def test_mention_processing(self):
    def test_workspace_detection(self):
    def test_chat_history_loading(self):
```

**Timeline**: Day 4-5

### 1.5 Integration Testing

**Validate**: Foundation components work together correctly
**Timeline**: Day 5

## Phase 2: Processing Pipeline (Week 2)

### 2.1 Create TypeRouter

**File**: `integrations/telegram/routing.py`
```python
class TypeRouter:
    """Message type detection and routing to specialized handlers."""
    
    def route_message(self, context: MessageContext) -> ProcessingPlan:
        """Determine processing strategy based on message type."""
        
    def _detect_message_type(self, message: TelegramMessage) -> MessageType:
        """Unified message type detection."""
        
    def _detect_special_patterns(self, text: str) -> List[SpecialPattern]:
        """Detect URLs, code blocks, system commands."""
        
    def _requires_intent_classification(self, context: MessageContext) -> bool:
        """Determine if intent classification needed."""
```

**Timeline**: Day 6-7

### 2.2 Build AgentOrchestrator

**File**: `integrations/telegram/orchestration.py`
```python
class AgentOrchestrator:
    """Single point for agent interaction."""
    
    def process_with_agent(self, context: MessageContext, plan: ProcessingPlan) -> AgentResponse:
        """Unified agent processing with context."""
        
    def _prepare_agent_context(self, context: MessageContext) -> ValorContext:
        """Convert MessageContext to agent-specific context."""
        
    def _handle_streaming_response(self, response_stream) -> AgentResponse:
        """Process streaming agent responses."""
        
    def _classify_intent_if_needed(self, context: MessageContext) -> Optional[Intent]:
        """Lightweight intent classification."""
```

**Consolidates**:
- Multiple agent routing methods from handlers.py
- Intent classification from 3-stage pipeline
- Streaming response handling

**Timeline**: Day 7-9

### 2.3 Implement ResponseManager

**File**: `integrations/telegram/response.py`
```python
class ResponseManager:
    """Unified response delivery with error handling."""
    
    def deliver_response(self, response: AgentResponse, context: MessageContext) -> DeliveryResult:
        """Unified response delivery with error handling."""
        
    def _format_for_telegram(self, content: str) -> str:
        """Format response text for Telegram."""
        
    def _handle_media_attachments(self, attachments: List[MediaAttachment]) -> List[TelegramMedia]:
        """Process media attachments for delivery."""
        
    def _store_conversation_history(self, context: MessageContext, response: AgentResponse):
        """Store conversation in unified history."""
        
    def _handle_delivery_errors(self, error: Exception, context: MessageContext) -> FallbackResponse:
        """Unified error handling and fallback responses."""
```

**Timeline**: Day 9-10

## Phase 3: Migration & Cleanup (Week 3)

### 3.1 Create UnifiedMessageProcessor

**File**: `integrations/telegram/unified_processor.py`
```python
class UnifiedMessageProcessor:
    """Main entry point replacing MessageHandler.handle_message()."""
    
    def __init__(self):
        self.security_gate = SecurityGate()
        self.context_builder = ContextBuilder()
        self.type_router = TypeRouter()
        self.agent_orchestrator = AgentOrchestrator()
        self.response_manager = ResponseManager()
        
    async def process_message(self, message: TelegramMessage) -> ProcessingResult:
        """Unified 5-step processing pipeline."""
        # Step 1: Security validation
        access_result = self.security_gate.validate_access(message)
        if not access_result.allowed:
            return ProcessingResult.denied(access_result.reason)
            
        # Step 2: Context building  
        context = self.context_builder.build_context(message)
        
        # Step 3: Type routing
        plan = self.type_router.route_message(context)
        
        # Step 4: Agent processing
        response = await self.agent_orchestrator.process_with_agent(context, plan)
        
        # Step 5: Response delivery
        result = await self.response_manager.deliver_response(response, context)
        
        return result
```

**Timeline**: Day 11-12

### 3.2 Replace Main Handler

**Modify**: `integrations/telegram/handlers.py`
```python
class MessageHandler:
    """Simplified handler using unified processor."""
    
    def __init__(self):
        self.processor = UnifiedMessageProcessor()
        
    async def handle_message(self, client, message):
        """Simplified entry point - delegates to unified processor."""
        try:
            result = await self.processor.process_message(message)
            if result.success:
                logger.info(f"✅ Message processed successfully: {result.summary}")
            else:
                logger.warning(f"⚠️ Message processing failed: {result.error}")
        except Exception as e:
            logger.error(f"❌ Unexpected error in message processing: {str(e)}")
            # Fallback error handling
```

**Reduce**: From 1,994 lines → ~100 lines

**Timeline**: Day 12-13

### 3.3 Remove Duplicate Code

**Delete/Consolidate**:
1. **Media handlers** (`_handle_photo_message`, etc.) → Move logic to TypeRouter
2. **Multiple mention processors** → Use ContextBuilder.process_mentions
3. **Scattered access control** → Use SecurityGate.validate_access  
4. **Duplicate context building** → Use ContextBuilder.build_context
5. **Multiple agent routing methods** → Use AgentOrchestrator.process_with_agent

**Timeline**: Day 13-14

### 3.4 Update MCP Tools

**Modify**: `mcp_servers/telegram_tools.py`
```python
# Update to use unified context injection
@mcp.tool()
def search_conversation_history(query: str, chat_id: str) -> str:
    # Use unified context building instead of duplicate logic
    context_builder = ContextBuilder()
    # ... rest of implementation
```

**Timeline**: Day 14-15

## Phase 4: Polish & Documentation (Week 4)

### 4.1 Error Handling Enhancement

**Create**: `integrations/telegram/error_management.py`
```python
class UnifiedErrorManager:
    """Comprehensive error handling for message processing."""
    
    def handle_processing_error(self, error: Exception, context: MessageContext) -> ErrorResponse:
        """Centralized error categorization and response."""
        
    def should_retry(self, error: Exception) -> bool:
        """Determine if error warrants retry."""
        
    def create_fallback_response(self, error: Exception) -> str:
        """Generate user-friendly error messages."""
```

**Timeline**: Day 16-17

### 4.2 Performance Optimization

**Optimize**:
1. **Context loading**: Batch database queries
2. **Intent classification**: Cache results, skip when not needed  
3. **Media processing**: Async file handling
4. **Response formatting**: Template caching

**Target**: <2s response time for 95% of messages

**Timeline**: Day 17-18

### 4.3 Monitoring Integration

**Add**: Performance metrics to each component
```python
# In each component
@monitor_performance
async def process_with_agent(self, context: MessageContext, plan: ProcessingPlan) -> AgentResponse:
    # Implementation with timing metrics
```

**Timeline**: Day 18-19

### 4.4 Documentation Update

**Update**:
1. **CLAUDE.md** - New architecture overview
2. **docs/message-handling.md** - Updated flow documentation  
3. **README.md** - Installation and setup changes
4. **API documentation** - Component interfaces

**Timeline**: Day 19-20

## Validation & Testing Strategy

### Unit Testing
- **Each component** independently tested
- **Edge cases** covered for all validation logic
- **Error scenarios** tested comprehensively
- **Performance** validated with realistic data

### Integration Testing  
- **End-to-end pipeline** tested with sample messages
- **All message types** (text, photo, document, audio, video)
- **All chat types** (DMs, groups, dev groups)
- **Error recovery** tested with various failure modes

### Load Testing
- **Concurrent message processing** (50+ simultaneous)
- **Large message volumes** (sustained throughput)
- **Memory usage** under load
- **Response time distribution** under stress

### Regression Testing
- **Feature parity** validated against current system
- **All existing functionality** preserved
- **Performance benchmarks** maintained or improved
- **Error rates** not increased

## Risk Mitigation

### Gradual Migration
1. **Feature flags**: Enable new system for subset of chats
2. **A/B testing**: Compare old vs new system performance
3. **Rollback capability**: Keep old system available
4. **Monitoring**: Comprehensive metrics during transition

### Backup Plans
1. **Component-level rollback**: Revert individual components if needed
2. **Feature degradation**: Disable new features if issues arise
3. **Manual override**: Admin controls for emergency situations
4. **Data integrity**: Ensure no message loss during transition

## Success Metrics

### Code Quality
- **Lines of code**: Main handler reduced from 1,994 → ~400 lines
- **Cyclomatic complexity**: <10 per method
- **Test coverage**: >90% for all new components
- **Code duplication**: <5% (currently ~40% duplicate patterns)

### Performance  
- **Response time**: <2s for 95% of messages
- **Error rate**: <1% message processing failures
- **Memory usage**: No increase from current baseline
- **Throughput**: Handle current peak load (50+ concurrent)

### Maintainability
- **Component size**: <500 lines per component
- **Dependencies**: Clear separation of concerns
- **Documentation**: Complete API documentation
- **Debugging**: Clear error messages and logging

## Timeline Summary

| Week | Phase | Key Deliverables | Success Criteria |
|------|-------|-----------------|------------------|
| 1 | Foundation | SecurityGate, ContextBuilder, Testing | Components pass unit tests |
| 2 | Pipeline | TypeRouter, AgentOrchestrator, ResponseManager | Integration tests pass |
| 3 | Migration | UnifiedMessageProcessor, Code cleanup | Feature parity achieved |
| 4 | Polish | Error handling, Performance, Documentation | Production ready |

**Total Duration**: 4 weeks  
**Risk Level**: Medium (gradual migration reduces risk)
**Impact**: High (major maintainability and performance improvements)

## URGENT PRIORITY: Emoji Reaction System

**CRITICAL**: Before proceeding with the full refactoring plan, the broken emoji reaction system must be fixed immediately. The sophisticated workflow described (👀 → intent classification → 👨‍💻 → 👍/❌) is essential for system functionality.

### Immediate Actions Required:

1. **Day 1-2**: Implement `reaction_manager.py` to fix missing import error
2. **Day 2-3**: Integrate ReactionManager into UnifiedProcessor for read receipts
3. **Day 3**: Connect Ollama intent classification to trigger work indicator reactions
4. **Day 3-4**: Connect Promise status updates to completion reactions
5. **Day 4-5**: Implement automated error recovery workflow

### Current System Status:
- ❌ **Broken**: Reaction system completely non-functional
- ✅ **Working**: Ollama intent classification, emoji mapping, promise queue
- 🔧 **Missing**: Orchestration layer (`reaction_manager.py`)

## Next Steps

1. **URGENT**: Fix broken emoji reaction system (Phase 0)
2. **Review and approve** this refactoring plan
3. **Set up development branch** for refactoring work
4. **Begin Phase 1** with foundation component implementation
5. **Establish monitoring** to track migration progress
6. **Schedule regular reviews** to ensure timeline adherence

This plan provides a structured approach to eliminating the message handling complexity while maintaining system stability and performance throughout the transition. However, **the emoji reaction system must be fixed first** as it's currently completely broken despite having partial implementation.