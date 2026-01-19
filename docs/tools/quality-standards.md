# Tool Quality Standards

## Overview

This document defines practical quality standards for tools in the system. Good tools share common characteristics: they validate inputs early, handle errors gracefully, and provide clear feedback.

## Error Handling

### Error Category Hierarchy

Handle errors in this priority order:

```python
ERROR_CATEGORIES = {
    1: "Configuration Errors",      # Missing API keys, invalid config
    2: "Validation Errors",         # Invalid inputs, wrong formats
    3: "File System Errors",        # File not found, permissions
    4: "Network/API Errors",        # Timeouts, rate limits, API failures
    5: "Processing Errors",         # Encoding, parsing, transformation
    6: "Generic Errors"             # Unexpected issues with context
}
```

### Error Message Format

```python
def format_error_message(emoji: str, category: str, details: str, error_type: str = None) -> str:
    """Standard error message formatting."""
    if error_type:
        return f"{emoji} {category} ({error_type}): {details}"
    else:
        return f"{emoji} {category}: {details}"

# Examples:
# "Search error: Query cannot be empty."
# "Image analysis error (FileNotFoundError): Image file not found."
# "Image generation error: API rate limit exceeded. Try again in 60 seconds."
```

### Exception Handling Pattern

```python
try:
    # Main implementation
    pass
except FileNotFoundError:
    return "Image analysis error: Image file not found."
except OSError as e:
    return f"File error: Failed to read file - {str(e)}"
except Exception as e:
    error_type = type(e).__name__

    # API-specific errors
    if "API" in str(e) or "OpenAI" in str(e):
        return f"API error: {str(e)}"

    # Encoding-specific errors
    if "base64" in str(e).lower() or "encoding" in str(e).lower():
        return f"Encoding error: Failed to process format - {str(e)}"

    # Generic with context
    return f"Error ({error_type}): {str(e)}"
```

## Input Validation

### Validation Order

Validate in this order for efficiency:

```python
VALIDATION_ORDER = [
    "presence",      # Not None, not empty
    "type",          # Correct data type
    "format",        # Valid format (URL, path, etc.)
    "range",         # Within acceptable bounds
    "business",      # Business logic validation
    "security"       # Security constraints
]
```

### Pre-Validation

Validate inputs BEFORE expensive operations:

```python
# Validate format before file I/O
valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
file_extension = Path(image_path).suffix.lower()
if file_extension not in valid_extensions:
    return f"Unsupported format '{file_extension}'. Supported: {', '.join(valid_extensions)}"

# Only after validation do we attempt file operations
with open(image_path, "rb") as image_file:
    image_data = base64.b64encode(image_file.read()).decode("utf-8")
```

### Common Validation Patterns

```python
def validate_file_path(path: str, allowed_extensions: list[str] = None) -> str | None:
    """Validate file path."""
    if not path or not path.strip():
        return "Path cannot be empty"

    path_obj = Path(path)

    # Format: Extension validation
    if allowed_extensions:
        if path_obj.suffix.lower() not in allowed_extensions:
            return f"Unsupported format. Allowed: {', '.join(allowed_extensions)}"

    return None  # Valid

def validate_api_input(text: str, max_length: int = 1000) -> str | None:
    """Validate text input for API calls."""
    if not text or not text.strip():
        return "Input cannot be empty"

    if len(text) > max_length:
        return f"Input too long (max {max_length} characters)"

    return None  # Valid
```

## Three-Layer Architecture

Tools should follow a three-layer pattern when they need to work across different interfaces:

```python
# Layer 1: Core Implementation
def process_data(input_data: str, options: dict = None) -> str:
    """Core implementation with validation and error handling."""
    # Comprehensive implementation with all error handling
    pass

# Layer 2: Agent Tool (if needed)
def process_with_context(ctx: RunContext[ValorContext], input_data: str) -> str:
    """Agent layer: Extract context and delegate."""
    chat_context = extract_recent_context(ctx.deps.chat_history)
    return process_data(input_data, {"context": chat_context})

# Layer 3: MCP Tool (if needed)
@mcp.tool()
def process_data_mcp(input_data: str, chat_id: str = "") -> str:
    """MCP layer: Claude Code integration."""
    chat_id, _ = inject_context_for_tool(chat_id, "")
    return process_data(input_data, {"chat_id": chat_id})
```

## Context-Aware Behavior

Tools should adapt behavior based on context:

```python
if question:
    system_content = (
        "Analyze the image and answer the specific question. "
        "Keep responses under 400 words."
    )
    user_content = f"Question about this image: {question}"
else:
    system_content = (
        "Describe what you see in the image. "
        "Keep responses under 300 words."
    )
    user_content = "What do you see in this image?"
```

## Performance

### Response Time Guidelines

| Operation Type | Target | Maximum |
|----------------|--------|---------|
| Simple Query | <500ms | 1s |
| API Call | <2s | 5s |
| File Processing | <1s | 3s |
| Batch Operation | <5s | 30s |

### Resource Cleanup

```python
def process_with_cleanup(file_path: str) -> str:
    """Process file with guaranteed cleanup."""
    temp_resources = []

    try:
        temp_file = create_temp_file()
        temp_resources.append(temp_file)

        result = process_file(file_path, temp_file)
        return result

    finally:
        for resource in temp_resources:
            try:
                cleanup_resource(resource)
            except Exception:
                pass  # Don't fail on cleanup errors
```

## Documentation

### Function Documentation

```python
def tool_function(required_param: str, optional_param: str = None) -> str:
    """One-line summary of what the tool does.

    Args:
        required_param: Description including format/constraints.
        optional_param: Description with default behavior.

    Returns:
        str: Description of return value.

    Example:
        >>> tool_function("input_value")
        'Success: Processed input_value'
    """
```

## Testing

### Test Categories

Test each aspect of the tool:

```python
# Validation Tests
def test_empty_input_validation():
    """Test empty input handling."""
    result = process_data("")
    assert "cannot be empty" in result

def test_large_input_validation():
    """Test input size limits."""
    large_input = "x" * 10001
    result = process_data(large_input)
    assert "too large" in result

# Error Handling Tests
def test_missing_api_key():
    """Test configuration validation."""
    with patch.dict(os.environ, {}, clear=True):
        result = process_data("test input")
        assert "Missing" in result or "unavailable" in result

def test_network_error_handling():
    """Test network error categorization."""
    with patch('module.api_call') as mock:
        mock.side_effect = ConnectionError("Connection refused")
        result = process_data("test input")
        assert "error" in result.lower()

# Integration Tests
def test_successful_processing():
    """Test the happy path."""
    result = process_data("valid input")
    assert "Success" in result or "Complete" in result
```

## Development Checklist

### Implementation
- [ ] Validate inputs before expensive operations
- [ ] Handle specific exceptions before generic ones
- [ ] Provide actionable error messages
- [ ] Include error context and type
- [ ] Adapt behavior based on context where appropriate

### Testing
- [ ] Test each validation path
- [ ] Test each error category
- [ ] Mock external dependencies
- [ ] Test the successful path

### Documentation
- [ ] Complete function docstrings
- [ ] Document environment variable requirements
- [ ] Include usage examples
