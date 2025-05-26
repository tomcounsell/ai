# Multi-Agent System Testing Suite

This comprehensive testing suite covers the full multi-agent system architecture, from individual agent capabilities to end-to-end workflows.

## Architecture Overview

See `comprehensive_architecture.md` for the complete system design, including:
- Pydantic-based agent system with HG Wells, NotionScout, and TechnicalAdvisor
- Claude Code tool integration for actual development work
- LLM-based evaluation framework for objective quality measurement
- Production-ready monitoring, configuration, and CI/CD integration

## Current Test Coverage

## Features

- **Real LLM Testing**: Uses actual Anthropic API calls to test Valor's responses
- **GPT Evaluation**: Uses OpenAI GPT-4o-mini to evaluate response quality
- **Multiple Scenarios**: Tests casual chat, technical discussions, work priorities, and mixed conversations
- **Human-likeness Scoring**: Evaluates responses on brevity, naturalness, tone, and context appropriateness
- **Detailed Reporting**: Generates comprehensive test reports with scores and suggestions

## Test Scenarios

### 1. Casual Greeting Test
- Tests basic social interactions
- Evaluates brevity and natural tone
- Ensures no over-helping behavior

### 2. Technical Discussion Test
- Tests technical knowledge and communication
- Evaluates clarifying questions and implementation focus
- Checks for appropriate engineer-level expertise

### 3. Work Priority Test
- Tests context awareness and priority handling
- Evaluates empathy and colleague-like responses
- Checks integration with Notion data

### 4. Mixed Conversation Test
- Tests tone adaptation and conversation flow
- Evaluates context retention across topics
- Checks natural transitions between casual and technical

## Running Tests

### Prerequisites
- OpenAI API key in `.env` file (`OPENAI_API_KEY`)
- Anthropic API key in `.env` file (`ANTHROPIC_API_KEY`)
- Optional: Notion API key for full testing (`NOTION_API_KEY`)

### Quick Start
```bash
cd tests
python run_tests.py
```

### Manual Execution
```bash
cd tests
python test_valor_conversations.py
```

## Test Evaluation Criteria

Each response is evaluated on multiple criteria with scores from 1-10:

- **Brevity**: Appropriate length for the question type
- **Human-like**: Sounds like a real person, not an AI assistant
- **No Over-helping**: Doesn't immediately offer technical assistance for casual chat
- **Casual Tone**: Matches conversational energy appropriately
- **Context Appropriate**: Provides relevant context and maintains conversation flow
- **Technical Accuracy**: Provides correct technical information when needed
- **Clarifying Questions**: Asks appropriate follow-up questions
- **Implementation Focus**: Focuses on practical implementation details

## Test Results

Tests generate:
- Real-time console output showing conversation flow
- Pass/fail status for each message
- Overall test scores and pass rates
- Detailed JSON report with all evaluations and suggestions
- Saved results file: `test_results_<timestamp>.json`

## Example Output

```
🧪 Running test: Casual Greeting Test
==================================================

👤 USER: Hey, how are you?
🤖 VALOR: Good! Just debugging some auth issues. You?
✅ Message 1 passed evaluation (score: 8.5)

👤 USER: What's today's date?
🤖 VALOR: Monday, May 26th
✅ Message 2 passed evaluation (score: 9.2)

📊 Test Result: PASS (avg score: 8.8)
```

## Interpreting Results

- **Score 8-10**: Excellent human-like response
- **Score 6-7**: Good but could be more natural
- **Score 4-5**: Needs improvement, sounds AI-like
- **Score 1-3**: Poor, very robotic response

The GPT evaluator provides specific suggestions for improving responses that score below 8.

## Customizing Tests

Add new test scenarios to `TEST_SCENARIOS` in `test_valor_conversations.py`:

```python
{
    "name": "Your Test Name",
    "messages": ["message 1", "message 2"],
    "criteria": {
        "criterion_name": "description of what to evaluate"
    },
    "type": "general"  # or "priority"
}
```

## Troubleshooting

- **Import Errors**: Ensure you're running from the correct directory
- **API Errors**: Check your API keys are valid and have sufficient credits
- **NotionScout Warnings**: Optional for most tests, only affects priority tests
- **Timeout Issues**: Tests may take 30-60 seconds due to LLM API calls
