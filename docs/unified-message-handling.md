# Unified Message Handling System

## Overview

The unified message handling system replaces the previous 1,994-line monolithic handler with a clean, maintainable architecture featuring a 5-step processing pipeline and clear component separation.

## Architecture

### Core Components

#### 1. **SecurityGate** (`components/security_gate.py`)
- Centralized access control
- Rate limiting
- Whitelist validation
- Bot self-message filtering

#### 2. **ContextBuilder** (`components/context_builder.py`)
- Unified context gathering
- Chat history loading
- Mention processing
- Media info extraction

#### 3. **TypeRouter** (`components/type_router.py`)
- Message type detection
- Special pattern recognition
- Priority determination
- Processing plan creation

#### 4. **AgentOrchestrator** (`components/agent_orchestrator.py`)
- Single point for agent interaction
- Intent classification
- Context preparation
- Response streaming

#### 5. **ResponseManager** (`components/response_manager.py`)
- Response formatting and delivery
- Media attachment handling
- Reaction management
- Error recovery

### Processing Pipeline

```
Message → SecurityGate → ContextBuilder → TypeRouter → AgentOrchestrator → ResponseManager
```

Each step has a single responsibility:

1. **Security Validation**: Access control and rate limiting
2. **Context Building**: Gather all necessary context
3. **Type Routing**: Determine processing strategy
4. **Agent Processing**: Handle with appropriate agent/handler
5. **Response Delivery**: Format and send response

## Data Models

### Core Models (`models.py`)

- `MessageContext`: Unified context for all processing
- `ProcessingPlan`: Strategy for message handling
- `AgentResponse`: Standardized agent output
- `AccessResult`: Security validation result
- `DeliveryResult`: Message delivery status
- `ProcessingResult`: Overall processing outcome

## Usage

### Basic Setup

```python
from integrations.telegram.unified_processor import create_unified_processor
from integrations.telegram.handlers_unified import create_message_handler

# Create processor
processor = await create_unified_processor(bot, valor_agent)

# Create handler
handler = create_message_handler(bot, valor_agent)
await handler.initialize()

# Register with Telegram
application.add_handler(handler.get_handlers()[0])
```

### Processing a Message

```python
# The unified processor handles everything internally
result = await processor.process_message(update, context)

if result.success:
    print(f"Processed successfully: {result.summary}")
else:
    print(f"Processing failed: {result.error}")
```

## Migration Guide

### 1. Run Migration Script

```bash
# Create tables and validate components
python scripts/migrate_to_unified_handler.py

# Start with 10% rollout
python scripts/migrate_to_unified_handler.py --rollout 10

# Check status
python scripts/migrate_to_unified_handler.py --status

# Increase rollout gradually
python scripts/migrate_to_unified_handler.py --rollout 50
python scripts/migrate_to_unified_handler.py --rollout 100

# Rollback if needed
python scripts/migrate_to_unified_handler.py --rollback
```

### 2. Update Integration Code

Replace old handler initialization:

```python
# Old
from integrations.telegram.handlers import MessageHandler
handler = MessageHandler(bot)

# New
from integrations.telegram.handlers_unified import create_message_handler
handler = create_message_handler(bot, valor_agent)
await handler.initialize()
```

### 3. Monitor Performance

The system provides comprehensive metrics:

```python
metrics = processor.get_metrics()
print(f"Messages processed: {metrics['processed_count']}")
print(f"Average time: {metrics['average_processing_time']:.2f}s")
print(f"Error rate: {metrics['error_rate']:.2%}")
```

## Error Handling

The `UnifiedErrorManager` provides sophisticated error handling:

- **Automatic categorization**: Network, rate limit, permission, etc.
- **Retry strategies**: Configurable per error category
- **User-friendly messages**: Context-aware error responses
- **Comprehensive logging**: Detailed debugging information

## Performance Improvements

### Before (Monolithic Handler)
- 1,994 lines in single file
- 19-step processing pipeline
- ~60% test coverage
- 2.5-3s average response time
- Complex debugging

### After (Unified System)
- ~400 lines main handler
- 5-step clear pipeline
- >90% test coverage
- <2s target response time
- Easy debugging with clear boundaries

## Testing

### Unit Tests
```bash
# Test individual components
pytest tests/test_security_gate.py
pytest tests/test_context_builder.py
pytest tests/test_type_router.py
pytest tests/test_unified_processor.py
```

### Integration Tests
```bash
# Test complete pipeline
pytest tests/test_unified_processor.py::TestUnifiedMessageProcessor::test_successful_message_processing
```

### Load Tests
```bash
# Test concurrent message handling
pytest tests/test_unified_processor.py::TestUnifiedMessageProcessor::test_batch_processing
```

## Configuration

### Environment Variables
```bash
# Bot configuration
TELEGRAM_BOT_USER_ID=123456789
TELEGRAM_BOT_USERNAME=mybot

# Rate limiting
RATE_LIMIT_WINDOW=60
RATE_LIMIT_MAX_MESSAGES=30
```

### Feature Flags

Control rollout via database:
- `unified_message_processor`: Enable new system
- `legacy_fallback`: Enable fallback on errors
- `intent_classification`: Enable intent detection
- `advanced_error_handling`: Use enhanced error management

## Monitoring

### Health Checks
```python
health = await processor.health_check()
print(f"Status: {health['status']}")
print(f"Components: {health['components']}")
```

### Error Statistics
```python
from integrations.telegram.error_management import UnifiedErrorManager
error_manager = UnifiedErrorManager()
stats = error_manager.get_error_statistics()
print(f"Total errors: {stats['total_errors']}")
print(f"By category: {stats['by_category']}")
```

## Extending the System

### Adding a New Component

1. Create component in `components/`
2. Integrate with `UnifiedMessageProcessor`
3. Add tests
4. Update documentation

### Adding a New Message Type

1. Add to `MessageType` enum
2. Update `TypeRouter._detect_message_type()`
3. Add routing logic
4. Create handler in `AgentOrchestrator`

## Troubleshooting

### Common Issues

**Access Denied Messages**
- Check workspace configuration
- Verify user whitelist
- Review rate limits

**Processing Timeouts**
- Check agent response time
- Review streaming configuration
- Monitor external service latency

**High Error Rate**
- Review error categories in metrics
- Check external service status
- Verify database connectivity

### Debug Mode

Enable detailed logging:
```python
import logging
logging.getLogger("integrations.telegram").setLevel(logging.DEBUG)
```

## Future Enhancements

- Redis-based rate limiting
- Distributed processing support
- Advanced analytics dashboard
- ML-based intent classification
- Automatic error recovery strategies
