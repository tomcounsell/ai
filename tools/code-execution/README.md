# Code Execution Tool

Sandboxed code execution environment with resource limits and safety measures.

## Overview

This tool provides safe code execution capabilities for:
- Python scripts
- JavaScript (Node.js)
- Bash commands

All code runs in temporary files with configurable timeouts.

## Installation

No additional installation required for Python and Bash. For JavaScript, ensure Node.js is installed:

```bash
node --version  # Should return version
```

## Quick Start

```python
from tools.code_execution import execute_code

# Execute Python
result = execute_code("print('Hello, World!')")
print(result["stdout"])  # Hello, World!

# Execute JavaScript
result = execute_code("console.log('Hello from JS')", language="javascript")
print(result["stdout"])

# Execute Bash
result = execute_code("echo 'Hello from Bash'", language="bash")
print(result["stdout"])
```

## API Reference

### execute_code()

```python
def execute_code(
    code: str,
    language: Literal["python", "javascript", "bash"] = "python",
    timeout_seconds: int = 30,
    memory_limit_mb: int = 128,
    enable_network: bool = False,
    dependencies: list[str] | None = None,
    input_data: str | None = None,
) -> dict
```

**Parameters:**
- `code`: Source code to execute (required)
- `language`: Programming language
- `timeout_seconds`: Max execution time (1-300, default: 30)
- `memory_limit_mb`: Memory limit (16-1024, default: 128)
- `enable_network`: Allow network access (default: False)
- `dependencies`: Package list (noted, not installed)
- `input_data`: Stdin input for the code

**Returns:**
```python
{
    "stdout": str,            # Standard output
    "stderr": str,            # Standard error
    "exit_code": int,         # Process exit code
    "execution_time_ms": int, # Duration in milliseconds
    "language": str,          # Language used
    "error": str              # Error message (if failed)
}
```

### Convenience Functions

```python
# Python-specific
result = execute_python("print('hello')")

# JavaScript-specific
result = execute_javascript("console.log('hello')")

# Bash-specific
result = execute_bash("echo 'hello'")
```

## Workflows

### Basic Execution
```python
result = execute_code("print(2 + 2)")
print(result["stdout"])  # 4
```

### With Input Data
```python
code = """
import json
data = json.loads(input())
print(f"Name: {data['name']}")
"""
result = execute_code(code, input_data='{"name": "Claude"}')
```

### With Timeout
```python
result = execute_code(
    "import time; time.sleep(60)",
    timeout_seconds=5
)
# Will timeout and return error
```

### Multi-line Scripts
```python
code = """
def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)

for i in range(10):
    print(fibonacci(i), end=' ')
"""
result = execute_code(code)
```

## Error Handling

```python
result = execute_code("syntax error here")

if "error" in result:
    print(f"Execution failed: {result['error']}")
elif result["exit_code"] != 0:
    print(f"Code error: {result['stderr']}")
else:
    print(f"Success: {result['stdout']}")
```

## Security Considerations

- Code runs in temporary files that are deleted after execution
- Timeouts prevent infinite loops
- Memory limits can be configured
- Network access is disabled by default
- Execution happens in the system's temp directory

## Troubleshooting

### Timeout Errors
```
Error: Execution timed out after 30 seconds
```
Increase timeout or optimize your code.

### Node.js Not Installed
```
Error: Node.js not installed
```
Install Node.js to run JavaScript code.

### Permission Errors
Ensure the temp directory is writable.
