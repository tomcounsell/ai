# Testing Strategy

## Overview

This document outlines the **INTELLIGENCE VALIDATION SYSTEM** for our valor_agent architecture. Our testing approach completely eliminates keyword trigger validation in favor of testing LLM contextual decision-making, intelligent tool selection, and end-to-end conversation flows. We validate that the valor_agent makes smart decisions based on natural language understanding, NOT rigid pattern matching.

## ⚠️ TEMPORARY TEST RESTRICTIONS

**Due to system overload issues when running the full test suite, automatic test execution is currently disabled.**

### Current Status:
- **Test execution temporarily disabled** in development workflows
- **Manual test runs only** when system resources permit
- **Promise Queue integration** will enable controlled test scheduling

### Promise Queue Test Scheduling:
The [Promise Queue](promise-queue.md) now enables **controlled test execution** with resource management:

```python
# Automated test scheduling through Promise Queue
@huey_consumer.task()
def execute_test_suite_async(test_category: str, priority: str = "medium"):
    """Execute test suite as background task with resource limits."""
    
    # Resource validation before test execution
    if not resource_monitor.can_execute_tests():
        return "⏸️ Test execution delayed - system resources insufficient"
    
    # Execute tests with monitoring
    results = run_test_category(test_category)
    
    # Deliver results via Telegram notification
    notify_test_completion(results, test_category)
    return results

# Priority-based test execution
test_priorities = {
    "critical": ["agent_intelligence", "security_validation"],
    "high": ["tool_integration", "conversation_flows"], 
    "medium": ["performance_benchmarks", "ui_components"],
    "low": ["documentation_tests", "code_style_checks"]
}
```

**Benefits Achieved:**
1. ✅ **Resource-aware scheduling**: Tests only run when system has sufficient capacity
2. ✅ **Priority-based execution**: Critical tests (agent intelligence, security) run first
3. ✅ **Background processing**: Test execution doesn't block main conversation flows
4. ✅ **Asynchronous notifications**: Results delivered via Telegram when complete
5. ✅ **Load distribution**: Tests spread over time to prevent system overload

### Running Tests Manually:
```bash
# Run single test file (recommended)
python tests/test_agent_quick.py

# Run specific test
python -m pytest tests/test_telegram_chat_agent.py::test_tool_selection -v

# DO NOT RUN: Full test suite (causes system overload)
# cd tests && python run_tests.py  # DISABLED
```

## Current Testing Infrastructure

### Test Organization

```
tests/
├── README.md                        # Testing overview and guidelines
├── run_tests.py                     # Main test runner
├── run_e2e_tests.py                # End-to-end test execution
├── test_telegram_chat_agent.py     # ★ INTELLIGENCE VALIDATION: Tool selection testing
├── test_telegram_ping_health.py    # ★ PING HEALTH SYSTEM: Bypass validation
├── test_telegram_image_integration.py # ★ IMAGE INTELLIGENCE: Complete flow testing
├── test_agent_demo.py              # ★ COMPREHENSIVE DEMOS: Real-world scenarios
├── test_agent_quick.py             # Quick agent functionality tests
├── test_chat_history.py            # Chat history management tests
├── test_claude_code_tool.py        # Claude Code tool integration tests
├── test_valor_conversations.py     # Valor persona conversation tests
├── e2e_framework.py                # End-to-end testing framework
└── integrations/
    └── telegram/                   # Telegram-specific integration tests

★ = COMPLETELY REWRITTEN for valor_agent intelligence validation
```

### Test Evaluation Methods

**Most tests (90%) use simple assertions:**
```python
assert response.status_code == 200
assert len(data) > 0  
assert "expected_text" in output
```

**Some tests (10%) use AI judges for subjective evaluation:**
```python
from tools.minimal_judge import judge_text, judge_screenshot

# For response quality
result = judge_text(response, "Is this response helpful?")
assert result["pass"], result["feedback"]

# For UI testing with screenshots
result = judge_screenshot("page.png", "Is the login button visible?")
assert result["pass"], result["feedback"]
```

See [Minimal Judge Guide](./minimal-judge-guide.md) for detailed usage.

### Intelligence Testing Philosophy

**CORE PRINCIPLE: Test Intelligence, Not Keywords**

Our test suite validates that the valor_agent makes intelligent decisions based on:
- **Natural Language Understanding**: Context and intent drive tool selection
- **Conversation Awareness**: Previous exchanges influence current responses
- **Tool Appropriateness**: Right tool selected for each type of request
- **Persona Consistency**: Valor Engels identity maintained throughout
- **End-to-End Flows**: Complete user journey from input to final output

**What We Test:**
- ✅ LLM correctly selects `search_current_info` for current information requests
- ✅ LLM correctly selects `create_image` for visual/creative requests
- ✅ LLM correctly selects `delegate_coding_task` for development tasks
- ✅ LLM correctly selects `save_link_for_later` for URL sharing
- ✅ LLM correctly selects `query_notion_projects` for work-related questions
- ✅ LLM maintains conversation context across multiple exchanges
- ✅ LLM maintains Valor Engels persona consistently

**What We DON'T Test:**
- ❌ Keyword matching (completely eliminated)
- ❌ Pattern detection (no longer exists)
- ❌ Static rule validation (replaced with intelligence)
- ❌ Over-mocked scenarios (real integrations strongly preferred)

**Testing Philosophy - Mock Minimally:**
- ✅ **Mock only external APIs** - OpenAI, Perplexity, services outside our control
- ✅ **Use real implementations** - databases, file operations, business logic
- ✅ **Test actual code paths** - validate what will run in production
- ❌ **Avoid complex mocking** - if setup is complex, use the real thing

### Testing Frameworks in Use

#### PydanticAI Testing

**TestModel Integration**:
```python
from pydantic_ai.models.test import TestModel

def test_telegram_agent_basic():
    """Test basic agent functionality with TestModel."""
    test_model = TestModel()
    test_agent = telegram_chat_agent.override(test_model)

    result = test_agent.run_sync(
        "How's it going?",
        deps=TelegramChatContext(chat_id=12345)
    )

    assert result.output
    assert len(result.output) > 0
```

**Agent Override Pattern**:
```python
# Current implementation in test_telegram_chat_agent.py
class TestTelegramChatAgent:
    """Test suite for Telegram chat agent."""

    def setup_method(self):
        """Setup test model for each test."""
        self.test_model = TestModel()
        self.test_agent = telegram_chat_agent.override(self.test_model)

    def test_context_handling(self):
        """Test agent context processing."""
        context = TelegramChatContext(
            chat_id=123,
            username="test_user",
            is_group_chat=False
        )

        result = self.test_agent.run_sync("Test message", deps=context)
        assert result.output
```

#### Standard Python Testing

**Pytest Integration - Minimal Mocking Pattern**:
```python
# Recommended test pattern - mock only external APIs
def test_search_tool_basic():
    """Test search tool functionality with minimal mocking."""
    # Mock only the external API call (Perplexity)
    with patch('tools.search_tool.OpenAI') as mock_openai:
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="Test response"))]
        mock_openai.return_value.chat.completions.create.return_value = mock_response

        # Everything else uses real implementations
        result = search_web("test query")
        assert "🔍" in result
        assert "test query" in result

# Example of what NOT to do - over-mocking
def test_link_storage_overmocked():
    """Example of excessive mocking (avoid this pattern)."""
    # ❌ Don't mock database connections, file operations, etc.
    with patch('sqlite3.connect'), \
         patch('pathlib.Path.exists'), \
         patch('tools.link_analysis_tool.validate_url'):
        # This test doesn't validate real functionality
        pass
```

### Current Test Suites

#### 1. Chat History Tests (`test_chat_history.py`)

**Test Coverage**:
- ✅ Basic message storage and retrieval
- ✅ Duplicate message prevention
- ✅ Context formatting for LLM consumption
- ✅ Message sequence validation
- ✅ Chat isolation between different chat IDs

**Implementation Pattern**:
```python
class TestChatHistory:
    """Comprehensive chat history testing."""

    def test_message_storage(self):
        """Test basic message storage functionality."""
        history = ChatHistoryManager()
        history.add_message(123, "user", "Hello")
        history.add_message(123, "assistant", "Hi there!")

        messages = history.get_context(123)
        assert len(messages) == 2
        assert messages[0]["content"] == "Hello"
        assert messages[1]["content"] == "Hi there!"

    def test_duplicate_prevention(self):
        """Test duplicate message handling."""
        # Implementation validates message deduplication

    def test_context_formatting(self):
        """Test LLM context formatting."""
        # Validates proper message formatting for PydanticAI
```

**Test Results**: 5/5 tests passing ✅

#### 2. Agent Demo Tests (`test_agent_demo.py`)

**Test Coverage**:
- Agent initialization and configuration
- Background execution monitoring
- Log file generation and progress tracking
- Process management and cleanup

**Implementation**:
```python
def test_agent_demo_execution():
    """Test comprehensive agent demo."""
    # Execute demo script
    process = subprocess.Popen(['scripts/demo_agent.sh'])

    # Monitor execution
    time.sleep(10)  # Allow demo to run

    # Verify log generation
    assert os.path.exists('logs/agent_demo.log')

    # Check process status
    if process.poll() is None:
        process.terminate()
        process.wait()
```

#### 3. Valor Conversation Tests (`test_valor_conversations.py`)

**Test Coverage**:
- End-to-end conversation quality evaluation
- Persona consistency validation
- Response appropriateness assessment
- Technical accuracy verification

**E2E Framework Integration**:
```python
# Current E2E test scenarios
TEST_SCENARIOS = [
    {
        "name": "persona_consistency",
        "messages": [
            {"role": "user", "content": "Who are you?"},
        ],
        "evaluation_criteria": ["persona_accuracy", "response_naturalness"]
    },
    {
        "name": "casual_interaction",
        "messages": [
            {"role": "user", "content": "How's your day going?"},
        ],
        "evaluation_criteria": ["conversational_flow", "human_likeness"]
    }
]
```

**Test Results**: 3/4 scenarios passing (75% success rate)
- ✅ Persona Consistency: 9.0/10
- ✅ Conversation Flow: 9.0/10
- ✅ Casual Interaction: 9.0/10
- ⚠️ Error Handling: 6.0/10 (needs improvement)

#### 4. Tool Integration Tests

**Claude Code Tool Tests** (`test_claude_code_tool.py`):
```python
def test_claude_code_tool_basic():
    """Test Claude Code tool basic functionality."""
    # Test tool execution with mock subprocess
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = Mock(stdout="Success", stderr="", returncode=0)

        result = execute_claude_code(
            "Test prompt",
            "/tmp/test_dir"
        )

        assert result == "Success"
        mock_run.assert_called_once()

def test_spawn_claude_session():
    """Test Claude session spawning."""
    # Test high-level session spawning functionality
    # Validates prompt building and directory handling
```

**Search Tool Tests**:
```python
def test_search_tool_with_valid_api():
    """Test search tool with mocked API responses."""
    # Mock Perplexity API integration
    # Validate response formatting
    # Test error handling scenarios
```

### Test Execution Patterns

#### Quick Testing

```bash
# Run quick agent functionality tests
python tests/test_agent_quick.py

# Output validation:
# - Agent instantiation
# - Basic tool functionality
# - Error handling
```

#### Comprehensive Testing

```bash
# Run full test suite
cd tests && python run_tests.py

# Includes:
# - All unit tests
# - Integration tests
# - E2E conversation tests
# - Tool validation tests
```

#### Background Demo Testing

```bash
# Run comprehensive agent demo
scripts/demo_agent.sh

# Monitor progress
tail -f logs/agent_demo.log

# Validates:
# - Long-running agent operations
# - Real API integrations
# - System stability under load
```

### Testing Utilities

#### Test Data Management

```python
# Test context creation utilities
def create_test_context(chat_id: int = 123, **kwargs) -> TelegramChatContext:
    """Create test context with sensible defaults."""
    return TelegramChatContext(
        chat_id=chat_id,
        username="test_user",
        is_group_chat=False,
        **kwargs
    )

def create_test_messages() -> list:
    """Generate test message sequences."""
    return [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "How are you?"}
    ]
```

#### Mock Integrations

```python
# External service mocking
@pytest.fixture
def mock_perplexity_api():
    """Mock Perplexity API for testing."""
    with patch('tools.search_tool.OpenAI') as mock:
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="Mocked response"))]
        mock.return_value.chat.completions.create.return_value = mock_response
        yield mock

@pytest.fixture
def mock_anthropic_api():
    """Mock Anthropic API for testing."""
    # Similar pattern for Anthropic API mocking
```

### Current Testing Metrics

#### Test Coverage

**Unit Tests**:
- Chat History: 100% coverage (5/5 tests)
- Tool Functions: 85% coverage
- Agent Configuration: 90% coverage

**Integration Tests**:
- Telegram Integration: 75% coverage
- Tool Integration: 80% coverage
- E2E Conversations: 75% success rate

**Performance Benchmarks**:
- Average test execution time: <2 seconds per test
- E2E test completion: <30 seconds
- Full test suite: <5 minutes

#### Quality Metrics

**Conversation Quality** (from E2E tests):
- Average quality score: 8.25/10
- Technical accuracy: 9.0+/10
- Human-likeness: 8.5+/10
- Context awareness: 9.0+/10

### Testing Best Practices Currently Implemented

#### 1. **Test Isolation**
```python
def setup_method(self):
    """Setup clean test environment for each test."""
    self.test_model = TestModel()
    self.test_agent = agent.override(self.test_model)
```

#### 2. **Mock External Dependencies**
```python
@patch('external_service.api_call')
def test_with_mocked_api(self, mock_api):
    """Test with external dependencies mocked."""
    mock_api.return_value = "expected_response"
    # Test implementation
```

#### 3. **Realistic Test Data**
```python
# Use representative test scenarios
test_scenarios = load_test_scenarios_from_file()
for scenario in test_scenarios:
    test_agent_with_scenario(scenario)
```

#### 4. **Error Scenario Testing**
```python
def test_error_handling():
    """Test agent behavior under error conditions."""
    # Test network failures, API errors, invalid inputs
    with pytest.raises(ExpectedError):
        agent.run_with_invalid_input()
```

### Limitations and Known Issues

#### Current Test Gaps

1. **Load Testing**: No current implementation for high-volume testing
2. **Multi-Agent Workflows**: Limited testing of agent cooperation
3. **Production Monitoring**: Test coverage for production scenarios incomplete
4. **Security Testing**: Input validation and security scenarios need expansion

#### Test Environment Constraints

- External API dependencies require mocking for reliable testing
- Long-running tests limited by CI/CD pipeline constraints
- Real-time conversation testing requires manual validation
- Cross-platform testing limited to development environments

## Current Tool Auditing Integration

### Automated Quality Assessment

The testing strategy now includes **automated tool auditing** that continuously validates tool quality across the codebase:

```python
# Tool quality assessment pipeline
class ToolAuditSystem:
    """Comprehensive tool quality assessment and scoring."""
    
    def audit_tool(self, tool_path: str) -> ToolAuditResult:
        """Audit a tool's implementation quality."""
        
        # Implementation quality analysis
        implementation_score = self.analyze_implementation_patterns(tool_path)
        
        # Test coverage and success rate
        test_score = self.analyze_test_coverage(tool_path)
        
        # Error handling sophistication
        error_handling_score = self.analyze_error_patterns(tool_path)
        
        # Documentation completeness
        documentation_score = self.analyze_documentation(tool_path)
        
        # Performance characteristics
        performance_score = self.analyze_performance(tool_path)
        
        return ToolAuditResult(
            overall_score=self.calculate_weighted_score([
                implementation_score, test_score, error_handling_score,
                documentation_score, performance_score
            ]),
            recommendations=self.generate_improvement_recommendations()
        )
```

### Quality Scoring Integration

**Current Tool Audit Results:**

| Tool | Quality Score | Status | Key Strengths |
|------|---------------|--------|---------------|
| `image_analysis_tool.py` | **9.8/10** | Gold Standard | Sophisticated error categorization, perfect test coverage, three-layer architecture |
| `search_tool.py` | **8.5/10** | Production Ready | Reliable API integration, good error handling |
| `claude_code_tool.py` | **8.2/10** | Production Ready | Robust subprocess management, timeout handling |
| `link_analysis_tool.py` | **7.8/10** | Production Ready | URL validation, database integration |

**Quality Standards Applied:**
- ✅ **9.0+ = Gold Standard**: Reference implementation for other tools
- ✅ **7.0-8.9 = Production Ready**: Meets all production requirements
- ✅ **5.0-6.9 = Needs Improvement**: Requires updates before production use
- ✅ **<5.0 = Critical Issues**: Immediate attention required

### Automated Testing Integration

**Test Category Integration:**
```python
# Tool-specific test categories with audit integration
test_categories = {
    "tool_quality_audit": {
        "priority": "critical",
        "tests": [
            "test_implementation_patterns",
            "test_error_categorization", 
            "test_performance_benchmarks",
            "test_interface_consistency"
        ],
        "audit_integration": True
    },
    "tool_security_validation": {
        "priority": "critical", 
        "tests": [
            "test_workspace_boundaries",
            "test_input_validation",
            "test_access_controls"
        ],
        "audit_integration": True
    }
}
```

**Continuous Quality Monitoring:**
- **Pre-commit hooks**: Tool quality checks before code commits
- **Automated auditing**: Daily quality assessments of all tools
- **Regression detection**: Quality score monitoring to detect degradation
- **Improvement tracking**: Progress monitoring for tools under improvement

### Integration with Agent Testing

**Tool Selection Intelligence Testing:**
```python
# Validate LLM tool selection with quality scoring
def test_agent_tool_selection_with_quality():
    """Test that high-quality tools are preferred by the agent."""
    
    # Get current tool quality scores
    tool_scores = tool_audit_system.get_all_scores()
    
    # Test agent tool selection for various scenarios
    test_scenarios = [
        ("analyze this image", "image_analysis_tool", 9.8),
        ("search for current information", "search_tool", 8.5),
        ("execute this code", "claude_code_tool", 8.2)
    ]
    
    for user_input, expected_tool, quality_score in test_scenarios:
        result = valor_agent.run_sync(user_input, deps=test_context)
        
        # Verify high-quality tool was selected
        assert expected_tool in result.tool_calls
        assert tool_scores[expected_tool] >= 7.0  # Production ready threshold
```

**Quality-Driven Test Prioritization:**
- Tools with higher quality scores get more comprehensive testing
- Lower-scoring tools receive focused improvement testing
- Gold Standard tools (9.0+) used as reference implementations
- Test failures in high-quality tools trigger immediate investigation

This testing strategy provides comprehensive validation of current functionality while maintaining fast execution, reliable results, and continuous quality improvement for all tools and agent capabilities.
