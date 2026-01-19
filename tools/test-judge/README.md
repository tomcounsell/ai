# Test Judge Tool

AI-powered test evaluation and quality assessment for subjective testing.

## Overview

This tool uses AI to evaluate test outputs against expected criteria. It's designed for subjective testing where traditional assertions aren't sufficient.

Key features:
- AI-based evaluation of test outputs
- Configurable strictness levels
- Batch judgment for multiple tests
- Confidence scoring
- Detailed reasoning

## Installation

Ensure you have an API key configured:

```bash
export ANTHROPIC_API_KEY=your_api_key
# or
export OPENROUTER_API_KEY=your_api_key
```

## Quick Start

```python
from tools.test_judge import judge_test_result

# Basic judgment
result = judge_test_result(
    test_output="The function returns correct results for all inputs.",
    expected_criteria=[
        "Indicates correctness",
        "Covers all test cases",
    ],
)

if result["pass_fail"] and result["confidence"] > 0.8:
    print("Test passed!")
else:
    print(f"Test failed: {result['reasoning']}")
```

## API Reference

### judge_test_result()

```python
def judge_test_result(
    test_output: str,
    expected_criteria: list[str],
    context: str | None = None,
    strictness: Literal["lenient", "standard", "strict"] = "standard",
) -> dict
```

**Parameters:**
- `test_output`: The actual test output to judge
- `expected_criteria`: List of criteria the output should meet
- `context`: Additional context about the test
- `strictness`: Judgment strictness level

**Returns:**
```python
{
    "pass_fail": bool,              # Overall pass/fail
    "confidence": float,            # 0.0-1.0 confidence score
    "reasoning": str,               # Explanation
    "criteria_results": {           # Per-criterion results
        "criterion text": bool
    },
    "suggestions": list[str]        # Improvement suggestions
}
```

### judge_batch()

```python
def judge_batch(
    test_cases: list[dict],
    strictness: Literal["lenient", "standard", "strict"] = "standard",
) -> dict
```

Judge multiple test cases at once.

**Parameters:**
- `test_cases`: List of dicts with 'output' and 'criteria' keys
- `strictness`: Strictness level for all judgments

**Returns:**
```python
{
    "results": list[dict],         # Individual judgment results
    "summary": {
        "total": int,
        "passed": int,
        "failed": int,
        "pass_rate": float,
        "errors": int
    }
}
```

### create_quality_gate()

```python
def create_quality_gate(
    criteria: list[str],
    min_pass_rate: float = 0.9,
    min_confidence: float = 0.8,
) -> dict
```

Create a quality gate configuration.

## Workflows

### Single Test Judgment
```python
result = judge_test_result(
    test_output=response,
    expected_criteria=[
        "provides specific actionable suggestions",
        "considers user experience principles",
    ],
)
assert result["pass_fail"] and result["confidence"] > 0.8
```

### Batch Testing
```python
results = judge_batch([
    {"output": output1, "criteria": ["criterion1"]},
    {"output": output2, "criteria": ["criterion1", "criterion2"]},
])

if results["summary"]["pass_rate"] >= 0.9:
    print("Quality gate passed!")
```

### With Context
```python
result = judge_test_result(
    test_output="Returns user object with email field.",
    expected_criteria=["Returns complete user data"],
    context="Testing user profile API endpoint",
)
```

### Strictness Levels

```python
# Lenient - accepts reasonable approximations
result = judge_test_result(output, criteria, strictness="lenient")

# Standard - reasonable standards (default)
result = judge_test_result(output, criteria, strictness="standard")

# Strict - rigorous, all criteria must be fully met
result = judge_test_result(output, criteria, strictness="strict")
```

## Error Handling

```python
result = judge_test_result(output, criteria)

if "error" in result:
    print(f"Judgment failed: {result['error']}")
else:
    print(f"Pass: {result['pass_fail']}")
```

## Best Practices

1. **Write clear criteria** - Be specific about what you're testing
2. **Use appropriate strictness** - Match strictness to test importance
3. **Check confidence** - Low confidence may indicate ambiguous criteria
4. **Review reasoning** - Understand why tests pass or fail
5. **Batch similar tests** - More efficient than individual calls

## Troubleshooting

### API Key Not Set
```
Error: ANTHROPIC_API_KEY or OPENROUTER_API_KEY required
```
Set one of the API keys in your environment.

### Low Confidence Scores
Refine your criteria to be more specific and measurable.

### Inconsistent Results
Try increasing strictness or adding more context.
