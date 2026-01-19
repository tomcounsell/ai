# Test Params Tool

Generate test parameters for subjective AI testing with diverse variations.

## Overview

This tool generates comprehensive test parameters:
- Edge cases for various data types
- Test parameter variations
- Evaluation criteria
- Expected behaviors

## Installation

No external dependencies required.

## Quick Start

```python
from tools.test_params import generate_params

# Generate API test parameters
result = generate_params(
    test_type="api",
    param_categories=["edge_cases", "input_validation"]
)

for param in result["test_params"]:
    print(f"{param['param_type']}: {param['value']}")
```

## API Reference

### generate_params()

```python
def generate_params(
    test_type: str,
    param_categories: list[str],
    num_variations: int = 5,
    complexity_level: Literal["simple", "medium", "complex"] = "medium",
    domain_context: str | None = None,
) -> dict
```

**Parameters:**
- `test_type`: Type of test (api, ui, performance, security, integration)
- `param_categories`: Categories to generate (edge_cases, input_validation, etc.)
- `num_variations`: Variations per category (default: 5)
- `complexity_level`: Complexity of parameters
- `domain_context`: Domain-specific context

**Returns:**
```python
{
    "test_type": str,
    "test_params": [
        {
            "category": str,
            "param_type": str,
            "subset": str,
            "value": any,
            "complexity": str
        }
    ],
    "evaluation_criteria": list[dict],
    "expected_behaviors": list[str],
    "total_params": int
}
```

### generate_edge_cases()

```python
def generate_edge_cases(param_type: str, num_cases: int = 10) -> dict
```

Generate edge cases for a specific parameter type.

### get_param_types()

```python
def get_param_types() -> dict
```

Get available parameter types and test types.

## Parameter Types

| Type | Subsets |
|------|---------|
| strings | empty, short, long, special, unicode |
| numbers | zero, small, large, negative, float, edge |
| arrays | empty, single, mixed, nested, large |
| objects | empty, simple, nested, with_arrays |
| booleans | values, truthy, falsy |
| dates | valid, edge, invalid |
| emails | valid, invalid |
| urls | valid, invalid |

## Test Types

- **api**: Input validation, edge cases, error handling
- **ui**: User input, display, interaction
- **performance**: Load, stress, endurance
- **security**: Injection, authentication, authorization
- **integration**: Data flow, service communication

## Workflows

### API Testing
```python
result = generate_params(
    "api",
    ["input_validation", "edge_cases"],
    complexity_level="complex"
)
```

### Security Testing
```python
result = generate_params(
    "security",
    ["injection"],
    num_variations=10
)
```

### Edge Cases Only
```python
result = generate_edge_cases("strings", num_cases=20)
```

## Error Handling

```python
result = generate_params(test_type, categories)

if "error" in result:
    print(f"Generation failed: {result['error']}")
else:
    for param in result["test_params"]:
        run_test(param["value"])
```
