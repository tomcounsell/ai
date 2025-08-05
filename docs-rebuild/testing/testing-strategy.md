# Testing Philosophy and Strategy

## Overview

This document defines the comprehensive testing strategy for the AI system, emphasizing **intelligence validation over keyword matching** and **real integrations over mocks**. The testing approach validates actual system behavior in production-like conditions, ensuring reliability through real-world scenario testing.

## Testing Philosophy

### 1. Intelligence Validation vs Keyword Matching

**Core Principle**: Tests validate AI decision-making quality, not just output format.

```python
# DON'T: Keyword-based validation
assert "success" in response.lower()

# DO: Intelligence-based validation using AI judges
judgment = judge_test_result(
    test_output=response,
    expected_criteria=[
        "provides specific actionable suggestions",
        "considers user experience principles",
        "appropriate tone for target audience"
    ],
    test_context={"test_type": "ui_feedback"}
)
assert judgment.pass_fail and judgment.confidence > 0.8
```

**Implementation**:
- AI judges evaluate subjective quality using local LLMs (Ollama)
- Structured judgment results with confidence scores
- Criteria-based evaluation rather than exact matching
- Fallback parsing for robustness

### 2. Real Integrations Over Mocks

From CLAUDE.md:
> "Do not write tests that mock real libraries and APIs. Use the actual library and actual API"

**Benefits**:
- Catches real API changes immediately
- Validates actual response formats and edge cases
- Tests rate limiting, quotas, and service availability
- Ensures production-ready reliability

**Example**:
```python
# Real Telegram client initialization
async def real_telegram_client(self):
    client = TelegramClient()
    success = await client.initialize()
    if not success:
        pytest.skip("Cannot connect to Telegram - skipping real E2E tests")
    yield client
    await client.stop()
```

### 3. Happy Path Focus

**Priority Order**:
1. **Primary Flow** (80% focus) - Common user interactions
2. **Integration Points** (15% focus) - API connections and tool orchestration  
3. **Error Handling** (4% focus) - Graceful degradation
4. **Edge Cases** (1% focus) - Only after core stability proven

### 4. No Simplification or Shortcuts

From CLAUDE.md:
> "Don't be tempted to simplify tests to get them working. Don't take shortcuts or cheat"

Tests maintain full complexity to accurately represent production scenarios.

## Test Architecture

### Test Organization

```
tests/
├── __init__.py
├── pytest.ini                          # Pytest configuration
├── test_real_telegram_e2e.py         # TRUE end-to-end tests
├── test_end_to_end_message_handling.py # Message type coverage
├── test_honesty_protocol.py          # Behavior validation
├── test_honesty_quick.py             # Quick validation tests
└── test_pyrogram_compatibility.py    # Integration compatibility

tools/
├── test_judge_tool.py                # AI-powered test evaluation
├── test_params_tool.py               # Parameter generation
└── test_scheduler_tool.py            # Test execution scheduling

run_tests.py                          # Main test orchestrator
run_voice_image_tests.py             # Specialized media testing
```

### PydanticAI TestModel Integration

While the codebase doesn't currently use PydanticAI's TestModel, it implements equivalent patterns:

**Agent Override Pattern**:
```python
class TestEndToEndMessageHandling:
    @pytest.fixture
    def processor(self):
        """Create a real UnifiedMessageProcessor with actual Valor agent."""
        return UnifiedMessageProcessor(
            telegram_bot=None,  # Can inject test bot if needed
            valor_agent=valor_agent  # Real agent, not mock
        )
```

**Benefits**:
- Tests use real agent implementations
- Can override specific components for isolation
- Maintains production behavior integrity

### Mock Strategies (External APIs Only)

**When Mocks Are Acceptable**:
- External service downtime (graceful skip preferred)
- Cost-prohibitive operations (use local alternatives)
- Destructive operations (use test accounts)

**Mock Implementation Pattern**:
```python
# Only mock when absolutely necessary
if not await can_connect_to_service():
    pytest.skip("Service unavailable - skipping integration test")
    
# Prefer local alternatives
judgment = judge_with_local_llm()  # Uses Ollama instead of cloud API
```

## Test Categories

### 1. Unit Tests (Component Isolation)

**Purpose**: Validate individual component behavior in isolation.

**Example - Honesty Protocol Validation**:
```python
def test_honesty_protocol_in_system_prompt(self):
    """Verify honesty protocol is in the condensed system prompt."""
    with open("../agents/valor/agent.py", "r") as f:
        agent_content = f.read()
    
    assert "HONESTY FIRST: Never fabricate completion claims" in agent_content
    assert "If you can't do something, say so" in agent_content
```

**Characteristics**:
- Fast execution (<1s per test)
- Minimal dependencies
- Focus on single responsibility
- No external service calls

### 2. Integration Tests (Component Interaction)

**Purpose**: Validate component interactions and data flow.

**Example - Message Processing Pipeline**:
```python
@pytest.mark.asyncio
async def test_dm_text_message_processing(self, processor, dm_user, dm_chat):
    """Test processing of a text message in DM context."""
    message = Message(
        id=1001,
        from_user=dm_user,
        chat=dm_chat,
        date=datetime.now()
    )
    message.text = "Hello, this is a test message"
    
    update = self.MockUpdate(message)
    result = await processor.process_message(update, None)
    
    assert isinstance(result, ProcessingResult)
    if result.success:
        assert result.summary is not None
```

**Coverage Areas**:
- Message type handling (text, media, commands)
- Context building (DM vs group)
- Component coordination
- Database interactions

### 3. E2E Tests (Full Conversation Flow)

**Purpose**: Validate complete user journeys with real services.

**Example - Real Telegram E2E**:
```python
"""
TRUE End-to-End Telegram Test

Tests the complete message flow using REAL Telegram API with NO MOCKS:
1. REAL Telegram message sent via API
2. REAL message reception through client
3. REAL UnifiedMessageProcessor handling
4. REAL Valor agent execution
5. REAL tool usage (web search, image analysis)
6. REAL database interactions
7. REAL response sent back
"""
```

**Test Flow**:
1. Initialize real Telegram client
2. Send actual message to test account
3. Process through complete pipeline
4. Validate response in Telegram
5. Verify database state changes

### 4. Performance Tests (Benchmarking and Validation)

**Purpose**: Ensure system meets performance requirements.

**Resource Monitoring Integration**:
```python
@dataclass
class ResourceSnapshot:
    timestamp: datetime
    memory_mb: float
    cpu_percent: float
    active_sessions: int
    total_processes: int
```

**Performance Baselines**:
- Memory usage: <500MB baseline, <50MB per session
- CPU usage: <80% sustained, <95% peak
- Response time: <2s for text, <5s for media
- Concurrent sessions: 50+ users

**Health Scoring**:
```python
def calculate_health_score(self) -> float:
    """Calculate overall system health (0-100)."""
    memory_health = max(0, 100 - (memory_percent * 1.5))
    cpu_health = max(0, 100 - (cpu_percent * 1.2))
    session_health = max(0, 100 - (session_load * 100))
    
    return (memory_health * 0.4 + cpu_health * 0.3 + 
            session_health * 0.3)
```

### 5. Intelligence Tests (LLM Decision Validation)

**Purpose**: Validate quality of AI-generated responses and decisions.

**AI Judge Implementation**:
```python
def judge_test_result(
    test_output: str,
    expected_criteria: List[str],
    test_context: Dict[str, Any],
    config: Optional[JudgeConfig] = None
) -> JudgmentResult:
    """Judge test results using local AI model."""
    
    # Structured evaluation with confidence scoring
    return JudgmentResult(
        test_id=test_id,
        overall_score=JudgmentScore.GOOD,
        criteria_scores={"clarity": "excellent", "accuracy": "good"},
        pass_fail=True,
        confidence=0.85,
        reasoning="Response meets all criteria with minor improvements possible"
    )
```

**Test Categories**:
- UI feedback quality
- Code quality assessment
- Response appropriateness
- Tool selection intelligence

## Testing Tools and Frameworks

### 1. Pytest Configuration

```ini
[tool:pytest]
testpaths = tests
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function

addopts = 
    -v                    # Verbose output
    --tb=short           # Short traceback format
    --strict-markers     # Enforce marker definitions
    --disable-warnings   # Clean output
    
markers =
    slow: marks tests as slow
    integration: marks tests as integration tests
    e2e: marks tests as end-to-end tests
    unit: marks tests as unit tests
```

### 2. AI Judges for Subjective Evaluation

**Judge Tool Features**:
- Local LLM integration (Ollama with gemma2:3b)
- Structured judgment results
- Configurable strictness levels
- Batch processing support
- Fallback parsing for robustness

**Judge Configuration**:
```python
class JudgeConfig(BaseModel):
    model: str = "gemma2:3b"        # Local model for speed
    temperature: float = 0.1        # Low for consistency
    strict_mode: bool = True        # High quality standards
    custom_criteria: Optional[List[str]] = None
```

### 3. Performance Benchmarking Tools

**Resource Monitor**:
- Real-time memory and CPU tracking
- Session lifecycle management
- Performance alert system
- Auto-restart recommendations
- Health score calculation

**Metrics Collection**:
```python
@dataclass
class ResourceLimits:
    max_memory_mb: float = 500.0
    max_memory_per_session_mb: float = 50.0
    max_cpu_percent: float = 80.0
    max_sessions: int = 100
    emergency_memory_mb: float = 800.0
    restart_memory_threshold_mb: float = 1200.0
```

### 4. Test Data Management

**Parameter Generation Tool**:
```python
def generate_test_params(config: TestParamConfig) -> List[TestParams]:
    """Generate diverse test parameters for comprehensive coverage."""
    
    # Category-based parameter templates
    param_templates = {
        "ui_feedback": {
            "interface_style": ["minimalist", "modern", "classic"],
            "user_expertise": ["beginner", "intermediate", "expert"],
            "context_urgency": ["low", "medium", "high", "critical"]
        },
        "code_quality": {
            "code_style": ["functional", "object_oriented"],
            "performance_priority": ["readability", "speed", "memory"]
        }
    }
```

**Test Scheduling**:
- Background execution via promise queue
- Resource-limited parallel execution
- Category-based test selection
- Progress notifications

## Quality Metrics

### 1. Test Coverage Requirements

**Minimum Coverage Targets**:
- **Core Components**: 90% coverage
- **Integration Points**: 100% of external APIs tested
- **Message Types**: All Telegram message types covered
- **Tool Usage**: Every tool tested with real execution
- **Error Paths**: Primary error scenarios validated

### 2. Success Rate Targets

**Quality Gates**:
- **Unit Tests**: 100% pass rate required
- **Integration Tests**: 95% pass rate (allowing for service issues)
- **E2E Tests**: 90% pass rate (allowing for network issues)
- **Performance Tests**: Meet all baseline requirements
- **Intelligence Tests**: >0.8 confidence on all judgments

### 3. Performance Benchmarks

**System Requirements**:
```python
# Production benchmarks from actual usage
PERFORMANCE_REQUIREMENTS = {
    "memory_baseline_mb": 300,
    "memory_per_session_mb": 30,
    "cpu_baseline_percent": 20,
    "response_time_text_ms": 2000,
    "response_time_media_ms": 5000,
    "concurrent_sessions": 50,
    "uptime_hours": 48,
    "health_score_minimum": 85
}
```

### 4. Intelligence Validation Criteria

**AI Quality Standards**:
- **Coherence**: Responses logically consistent
- **Relevance**: Addresses user intent accurately
- **Completeness**: Provides comprehensive solutions
- **Accuracy**: Factually correct information
- **Appropriateness**: Suitable tone and complexity

**Validation Process**:
1. Generate response to test prompt
2. Evaluate with AI judge against criteria
3. Score each criterion independently
4. Calculate overall confidence score
5. Pass if confidence > 0.8 and all criteria met

## Test Implementation Examples

### Example 1: Real Integration Test

```python
@pytest.mark.integration
async def test_real_web_search_integration(self, processor, user, chat):
    """Test real web search tool integration."""
    # Create message requesting web search
    message = create_message("What's the weather in Tokyo?", user, chat)
    
    # Process with real agent and tools
    result = await processor.process_message(MockUpdate(message), None)
    
    # Validate real tool was used
    assert result.success
    assert "weather" in result.summary.lower()
    assert result.tools_used and "web_search" in result.tools_used
    
    # Judge response quality
    judgment = judge_response_quality(
        response=result.response_text,
        prompt="What's the weather in Tokyo?",
        evaluation_criteria=[
            "includes current weather information",
            "mentions Tokyo specifically",
            "provides actionable weather details"
        ]
    )
    assert judgment.pass_fail
```

### Example 2: Performance Validation Test

```python
@pytest.mark.slow
async def test_concurrent_session_performance(self, monitor, processor):
    """Test system performance under concurrent load."""
    # Baseline measurement
    baseline = monitor.get_current_snapshot()
    
    # Create concurrent sessions
    sessions = []
    for i in range(20):
        session = await create_test_session(f"user_{i}")
        sessions.append(session)
    
    # Process messages concurrently
    tasks = [process_test_message(s, processor) for s in sessions]
    results = await asyncio.gather(*tasks)
    
    # Validate performance
    final = monitor.get_current_snapshot()
    memory_increase = final.memory_mb - baseline.memory_mb
    
    assert all(r.success for r in results)
    assert memory_increase < 600  # 30MB per session * 20
    assert final.cpu_percent < 80
    assert monitor.calculate_health_score() > 85
```

### Example 3: Intelligence Validation Test

```python
async def test_tool_selection_intelligence(self, processor):
    """Test intelligent tool selection based on context."""
    test_cases = [
        {
            "message": "Remember that I prefer dark mode",
            "expected_tool": "update_user_preferences",
            "not_expected": ["web_search", "image_analysis"]
        },
        {
            "message": "What's in this image? [photo attached]",
            "expected_tool": "image_analysis",
            "not_expected": ["web_search", "code_execution"]
        }
    ]
    
    for case in test_cases:
        result = await processor.process_message(
            create_message_with_context(case["message"]), None
        )
        
        # Validate intelligent tool selection
        assert case["expected_tool"] in result.tools_used
        for tool in case["not_expected"]:
            assert tool not in result.tools_used
        
        # Judge decision quality
        judgment = judge_tool_selection(
            selected_tools=result.tools_used,
            user_intent=case["message"],
            context=result.context_used
        )
        assert judgment.confidence > 0.9
```

## Test Maintenance Guidelines

### 1. Adding New Tests

**Process**:
1. Identify test category (unit/integration/e2e)
2. Create test with real components
3. Define clear success criteria
4. Add appropriate pytest markers
5. Document any service requirements

### 2. Handling Test Failures

**Investigation Steps**:
1. Check service availability (Telegram, APIs)
2. Validate credentials and quotas
3. Review recent code changes
4. Check resource constraints
5. Examine full pytest output with -vv

### 3. Performance Regression Prevention

**Monitoring**:
- Track test execution times
- Monitor resource usage trends
- Set up alerts for degradation
- Regular baseline updates
- Automated performance gates

## Conclusion

This testing strategy ensures system reliability through:
- **Real-world validation** over synthetic tests
- **Intelligence assessment** over pattern matching
- **Production-like conditions** in all tests
- **Comprehensive coverage** of user journeys
- **Continuous monitoring** of system health

The approach prioritizes catching real issues that affect users over achieving traditional metrics, resulting in a more robust and reliable system in production.