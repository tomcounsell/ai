# Quick Start Guide

**For developers who want to get started quickly.**

## 5-Minute Quick Start

### 1. Basic Execution

```python
from apps.ai.code_execution import CodeExecutor

executor = CodeExecutor()
result = executor.execute("print('Hello, World!')")

if result.success:
    print(result.stdout)  # "Hello, World!"
else:
    print(f"Error: {result.error_message}")
```

### 2. With Context Data

```python
result = executor.execute(
    code="print(f'Hello, {context[\"name\"]}!')",
    context={"name": "Alice"}
)
```

### 3. Check What Happened

```python
result = executor.execute(code)

print(f"Success: {result.success}")
print(f"Output: {result.stdout}")
print(f"Errors: {result.stderr}")
print(f"Execution time: {result.execution_time_seconds}s")
print(f"Violations: {len(result.validation_violations)}")
```

## Common Patterns

### Execute LLM-Generated Code

```python
# Get code from LLM
llm_code = llm.complete("Generate Python code to calculate fibonacci numbers")

# Execute safely
executor = CodeExecutor(user_id=request.user.id)
result = executor.execute(llm_code)

if not result.success:
    # Feed error back to LLM for correction
    feedback = {
        "error": result.error_message,
        "violations": result.validation_violations,
    }
    corrected_code = llm.fix_code(llm_code, feedback)
    result = executor.execute(corrected_code)
```

### Django View Integration

```python
# views.py
from django.http import JsonResponse
from apps.ai.code_execution import CodeExecutor

def execute_code_api(request):
    code = request.POST.get('code')
    executor = CodeExecutor(user_id=request.user.id)
    result = executor.execute(code)

    return JsonResponse({
        "success": result.success,
        "output": result.stdout,
        "error": result.error_message,
    })
```

### Custom Configuration

```python
from apps.ai.code_execution.sandboxes import SandboxConfig

config = SandboxConfig(
    timeout_seconds=10,         # Shorter timeout
    max_output_bytes=100_000,   # Smaller output limit
    allowed_imports=(           # Only these modules
        "math",
        "json",
    ),
)

executor = CodeExecutor(
    sandbox_config=config,
    redact_sensitive_output=True,  # Auto-redact secrets
)
```

## What Can Execute?

### ✅ Allowed

```python
# Math operations
import math
x = math.pi * 2

# Data processing
data = [1, 2, 3]
result = sum(x * 2 for x in data)

# JSON handling
import json
json.dumps({"key": "value"})

# String manipulation
text = "hello"
print(text.upper())

# Datetime operations
import datetime
now = datetime.datetime.now()

# Context access
user_name = context['name']
```

### ❌ Blocked

```python
# File access
open('/etc/passwd')  # ❌ Blocked

# Network access
import requests  # ❌ Blocked
import socket    # ❌ Blocked

# System commands
import os        # ❌ Blocked
import subprocess  # ❌ Blocked

# Dynamic execution
eval("1 + 1")    # ❌ Blocked
exec("print(1)") # ❌ Blocked

# Dangerous functions
__import__('os') # ❌ Blocked
```

## Error Handling

### Check Success First

```python
result = executor.execute(code)

if result.success:
    # Use the output
    print(result.stdout)
else:
    # Handle the error
    if result.validation_violations:
        print("Security violation:", result.validation_violations[0]['message'])
    else:
        print("Runtime error:", result.error_message)
```

### Exception Handling

```python
from apps.ai.code_execution import ValidationError, TimeoutError

try:
    result = executor.execute(code)
except ValidationError as e:
    print(f"Invalid code: {e.message}")
    print(f"Details: {e.details}")
except TimeoutError as e:
    print(f"Code took too long to execute")
```

## Testing Your Code

### Simple Test

```python
import pytest
from apps.ai.code_execution import CodeExecutor

def test_my_code_execution():
    executor = CodeExecutor()
    result = executor.execute("print('test')")

    assert result.success
    assert "test" in result.stdout
```

### Test Security

```python
def test_blocks_dangerous_imports():
    executor = CodeExecutor()
    result = executor.execute("import os")

    assert not result.success or result.validation_violations
```

## Performance Tips

### 1. Reuse Executor (Same User)

```python
# Good: Reuse for same user
executor = CodeExecutor(user_id=123)
result1 = executor.execute(code1)
result2 = executor.execute(code2)

# Bad: Create new executor each time (unnecessary)
result1 = CodeExecutor().execute(code1)
result2 = CodeExecutor().execute(code2)
```

### 2. Disable Validation for Trusted Code

```python
# For code you trust (admin-only features)
executor = CodeExecutor(
    enable_ast_validation=False,
    enable_output_validation=False,
)
```

### 3. Adjust Timeouts

```python
# Quick operations
config = SandboxConfig(timeout_seconds=5)

# Data processing
config = SandboxConfig(timeout_seconds=30)
```

## Common Issues

### "Import of 'X' is not allowed"

**Solution**: Add module to `allowed_imports`:

```python
config = SandboxConfig(
    allowed_imports=(
        "math", "json", "datetime",
        "your_module",  # Add here
    ),
)
```

### "Code execution exceeded time limit"

**Solution**: Increase timeout or optimize code:

```python
config = SandboxConfig(timeout_seconds=60)
```

### "Output too large"

**Solution**: Increase output limit or reduce output:

```python
config = SandboxConfig(max_output_bytes=5_000_000)  # 5MB
```

## Security Checklist

Before deploying to production:

- [ ] Using OS-level sandbox (not RestrictedPythonSandbox)
- [ ] All validation layers enabled
- [ ] Appropriate resource limits set
- [ ] Logging configured
- [ ] Rate limiting implemented
- [ ] Tested with malicious inputs

## Next Steps

1. **Read the README** for complete API documentation
2. **Check EXAMPLES.md** for real-world patterns
3. **Review test_security.py** to understand threats
4. **Read the architecture doc** for production guidance

## Important Warnings

⚠️ **RestrictedPythonSandbox is NOT secure for production**

This proof-of-concept sandbox can be bypassed. For production:
- Use E2B (recommended)
- Use gVisor containers
- Use Firecracker microVMs

⚠️ **Never disable all validation** for untrusted code

Always keep at least AST validation enabled for user-submitted code.

⚠️ **Monitor execution logs** for security violations

High-severity violations may indicate attack attempts.

## Get Help

1. Check the comprehensive README
2. Review EXAMPLES.md for patterns
3. Read IMPLEMENTATION_SUMMARY.md for architecture
4. Consult /docs/SAFE_LLM_CODE_EXECUTION_ARCHITECTURE.md

---

**Ready to start? Try the basic example above!**
