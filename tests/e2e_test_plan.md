# End-to-End Telegram Agent Test Plan

## Overview
This document outlines a comprehensive testing strategy for the Valor Engels Telegram bot using LLM-based evaluation for subjective pass/fail criteria.

## Test Framework Architecture

### Core Components
1. **Test Simulator**: Simulates Telegram message flows without requiring actual Telegram API
2. **LLM Evaluator**: Uses GPT-4o-mini for objective evaluation of subjective criteria
3. **Scenario Engine**: Manages test scenarios and conversation flows
4. **Results Aggregator**: Collects and reports test outcomes

### Testing Approach
- **Automated Setup**: Mock Telegram client to avoid API dependencies
- **Conversation Simulation**: Generate realistic conversation flows
- **LLM Evaluation**: Use AI to evaluate human-like responses and persona consistency
- **Quantified Scoring**: Convert subjective assessments to numerical scores
- **Regression Detection**: Track performance over time

## Test Categories

### 1. Persona Consistency Tests
**Goal**: Verify Valor Engels maintains consistent personality across interactions

**Test Scenarios**:
- Technical discussions (should be detailed, implementation-focused)
- Casual conversations (should be friendly, slightly German-influenced)
- Work priority questions (should reference Notion data when available)
- Mixed conversation types within single session

**LLM Evaluation Criteria**:
- Personality consistency (1-10 scale)
- Language naturalness (1-10 scale)
- Technical accuracy when appropriate (1-10 scale)
- Absence of AI assistant behavior (1-10 scale)

### 2. Conversation Flow Tests
**Goal**: Ensure natural conversation progression and context awareness

**Test Scenarios**:
- Multi-turn technical discussions
- Context switching between topics
- Reference to previous messages
- Handling of interruptions and topic changes

**LLM Evaluation Criteria**:
- Context awareness (1-10 scale)
- Conversation coherence (1-10 scale)
- Natural response timing (appropriate length/complexity)
- Memory of previous context (1-10 scale)

### 3. Chat History Integration Tests
**Goal**: Verify chat history persistence and context usage

**Test Scenarios**:
- Conversation across simulated server restarts
- References to earlier conversation points
- Chat history cleanup and limits
- Multiple chat isolation

**LLM Evaluation Criteria**:
- Appropriate use of chat history (1-10 scale)
- No inappropriate cross-chat contamination (pass/fail)
- Graceful handling of missing context (1-10 scale)

### 4. Notion Integration Tests
**Goal**: Test project-specific queries and priority checking

**Test Scenarios**:
- Project status queries with valid Notion data
- Priority questions triggering Notion lookups
- Handling of Notion API errors
- Project name resolution (aliases, etc.)

**LLM Evaluation Criteria**:
- Accuracy of Notion data interpretation (1-10 scale)
- Appropriate use of project context (1-10 scale)
- Error handling quality (1-10 scale)

### 5. Group vs Private Chat Tests
**Goal**: Verify correct behavior in different chat contexts

**Test Scenarios**:
- @mention detection in groups
- Private chat immediate responses
- Group conversation context awareness
- Reply-to-message handling

**LLM Evaluation Criteria**:
- Correct mention detection (pass/fail)
- Appropriate response targeting (1-10 scale)
- Context isolation between chat types (pass/fail)

### 6. Edge Case & Error Handling Tests
**Goal**: Test robustness and graceful degradation

**Test Scenarios**:
- API timeout/error conditions
- Malformed input handling
- Very long conversations
- Rapid message sequences

**LLM Evaluation Criteria**:
- Error message quality (1-10 scale)
- Graceful degradation (1-10 scale)
- Recovery capability (1-10 scale)

## Implementation Strategy

### Phase 1: Mock Framework
```python
class MockTelegramClient:
    """Simulates Telegram client without API calls"""
    
class E2ETestRunner:
    """Orchestrates end-to-end test scenarios"""
    
class LLMEvaluator:
    """Uses GPT-4o-mini for subjective evaluation"""
```

### Phase 2: Scenario Definitions
```python
SCENARIOS = {
    "persona_consistency": [
        {
            "name": "Technical Deep Dive",
            "messages": [...],
            "evaluation_criteria": [...]
        }
    ],
    "conversation_flow": [...],
    # etc.
}
```

### Phase 3: Evaluation Engine
```python
class SubjectiveEvaluator:
    """
    Sends conversation transcripts to LLM evaluator with specific criteria
    Returns numerical scores and qualitative feedback
    """
```

### Phase 4: Reporting & CI Integration
```python
class TestReporter:
    """
    Generates comprehensive test reports
    Tracks score trends over time
    Flags significant degradations
    """
```

## Success Metrics

### Quantitative Thresholds
- **Persona Consistency**: ≥ 8.0/10 average across all scenarios
- **Conversation Flow**: ≥ 7.5/10 average coherence score
- **Technical Accuracy**: ≥ 8.5/10 when technical context is appropriate
- **Error Handling**: ≥ 7.0/10 for graceful degradation

### Qualitative Indicators
- Zero instances of obvious AI assistant language ("I'm an AI assistant...")
- Consistent German/Californian cultural references when appropriate
- Technical discussions show deep implementation knowledge
- Natural conversation progression without robotic responses

## Test Data Management

### Conversation Datasets
- **Baseline Conversations**: Known good examples for regression testing
- **Synthetic Scenarios**: Generated test cases covering edge cases
- **Real Conversation Samples**: Anonymized examples from actual usage

### Notion Test Data
- **Mock Database Responses**: Predictable test data for integration tests
- **Error Condition Simulation**: API failures, malformed responses
- **Project Context Variations**: Different project states and priorities

## Continuous Integration

### Automated Execution
```bash
# Daily full test suite
python tests/e2e_runner.py --suite=full --report=daily

# Pre-commit quick checks
python tests/e2e_runner.py --suite=smoke --threshold=strict

# Weekly comprehensive analysis
python tests/e2e_runner.py --suite=comprehensive --analysis=deep
```

### Performance Tracking
- Score trend analysis over time
- Regression detection alerts
- Performance comparison across code changes
- Automated issue creation for significant degradations

## Future Enhancements

### Advanced Scenarios
- Multi-language conversation handling
- Emotional context understanding
- Complex project management discussions
- Cross-platform behavior consistency

### Enhanced Evaluation
- Multiple LLM evaluator consensus
- Human evaluation correlation studies
- Domain-specific evaluation criteria
- Real-time feedback integration

## Implementation Timeline

1. **Week 1**: Mock framework and basic scenario runner
2. **Week 2**: LLM evaluation engine and core test scenarios
3. **Week 3**: Comprehensive scenario library and edge cases
4. **Week 4**: Reporting, CI integration, and documentation

This framework will ensure Valor Engels maintains high-quality, human-like interactions while detecting regressions and guiding improvements through objective measurement of subjective qualities.