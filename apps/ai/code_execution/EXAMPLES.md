# Code Execution Examples

This document provides practical examples of using the code execution module in different scenarios.

## Table of Contents

1. [Basic Examples](#basic-examples)
2. [Data Analysis Use Cases](#data-analysis-use-cases)
3. [LLM Integration Patterns](#llm-integration-patterns)
4. [Django Integration](#django-integration)
5. [Error Handling Patterns](#error-handling-patterns)
6. [Advanced Configuration](#advanced-configuration)

## Basic Examples

### Hello World

```python
from apps.ai.code_execution import CodeExecutor

executor = CodeExecutor()
result = executor.execute("print('Hello, World!')")

if result.success:
    print(result.stdout)  # "Hello, World!"
```

### Simple Calculation

```python
executor = CodeExecutor()
result = executor.execute("""
import math

radius = 10
area = math.pi * radius ** 2
circumference = 2 * math.pi * radius

print(f"Circle with radius {radius}:")
print(f"  Area: {area:.2f}")
print(f"  Circumference: {circumference:.2f}")
""")

print(result.stdout)
```

Output:
```
Circle with radius 10:
  Area: 314.16
  Circumference: 62.83
```

### Using Execution Context

```python
executor = CodeExecutor()
result = executor.execute(
    code="""
name = context['name']
age = context['age']
print(f"{name} will be {age + 10} years old in 10 years")
    """,
    context={"name": "Alice", "age": 25}
)

print(result.stdout)  # "Alice will be 35 years old in 10 years"
```

## Data Analysis Use Cases

### Processing Lists

```python
executor = CodeExecutor()
result = executor.execute("""
# Get user data from context
users = context['users']

# Calculate average age
ages = [user['age'] for user in users]
average_age = sum(ages) / len(ages)

# Find oldest user
oldest = max(users, key=lambda u: u['age'])

print(f"Average age: {average_age:.1f}")
print(f"Oldest user: {oldest['name']} ({oldest['age']})")
""", context={
    "users": [
        {"name": "Alice", "age": 25},
        {"name": "Bob", "age": 30},
        {"name": "Charlie", "age": 35},
    ]
})

print(result.stdout)
```

Output:
```
Average age: 30.0
Oldest user: Charlie (35)
```

### JSON Processing

```python
executor = CodeExecutor()
result = executor.execute("""
import json

# Parse JSON data
raw_data = context['json_string']
data = json.loads(raw_data)

# Transform data
transformed = {
    "summary": {
        "total_items": len(data['items']),
        "total_value": sum(item['value'] for item in data['items']),
    },
    "items": [
        {"id": item['id'], "double_value": item['value'] * 2}
        for item in data['items']
    ]
}

# Output as JSON
print(json.dumps(transformed, indent=2))
""", context={
    "json_string": '{"items": [{"id": 1, "value": 10}, {"id": 2, "value": 20}]}'
})

print(result.stdout)
```

### Statistical Analysis

```python
executor = CodeExecutor()
result = executor.execute("""
import math
import statistics

data = context['measurements']

mean = statistics.mean(data)
median = statistics.median(data)
stdev = statistics.stdev(data) if len(data) > 1 else 0

print(f"Count: {len(data)}")
print(f"Mean: {mean:.2f}")
print(f"Median: {median:.2f}")
print(f"Std Dev: {stdev:.2f}")
print(f"Min: {min(data):.2f}")
print(f"Max: {max(data):.2f}")
""", context={
    "measurements": [23.5, 25.1, 24.8, 26.2, 23.9, 25.5, 24.3]
})
```

## LLM Integration Patterns

### Self-Correcting Execution

```python
from apps.ai.code_execution import CodeExecutor
from typing import Optional

def execute_with_llm_retry(
    code: str,
    llm_client,
    max_attempts: int = 3
) -> dict:
    """
    Execute code with LLM self-correction on errors.

    Args:
        code: Initial code to execute
        llm_client: LLM client for fixing errors
        max_attempts: Maximum retry attempts

    Returns:
        Dictionary with result and metadata
    """
    executor = CodeExecutor()

    for attempt in range(max_attempts):
        result = executor.execute(code)

        if result.success:
            return {
                "success": True,
                "output": result.stdout,
                "attempts": attempt + 1,
            }

        # Prepare error context for LLM
        error_info = {
            "code": code,
            "error_type": result.error_type,
            "error_message": result.error_message,
            "violations": result.validation_violations,
            "attempt": attempt + 1,
        }

        # Ask LLM to fix the code
        prompt = f"""
The following code failed to execute:

```python
{code}
```

Error: {result.error_message}
Violations: {result.validation_violations}

Please provide a corrected version that:
1. Fixes the error
2. Avoids the security violations
3. Produces the same intended output

Return only the corrected Python code.
"""

        code = llm_client.complete(prompt)

    return {
        "success": False,
        "error": "Max retry attempts exceeded",
        "attempts": max_attempts,
    }
```

### Code Generation with Validation

```python
def generate_and_execute(task_description: str, llm_client) -> dict:
    """
    Generate code from description and execute safely.

    Args:
        task_description: Natural language task description
        llm_client: LLM client

    Returns:
        Execution result dictionary
    """
    # Generate code
    prompt = f"""
Generate Python code to: {task_description}

Requirements:
- Only use: math, json, datetime, collections modules
- No file or network access
- Must print results
- Keep it under 50 lines

Return only the code, no explanations.
"""

    code = llm_client.complete(prompt)

    # Execute with validation
    executor = CodeExecutor(
        enable_syntax_validation=True,
        enable_ast_validation=True,
    )

    result = executor.execute(code)

    return {
        "task": task_description,
        "generated_code": code,
        "success": result.success,
        "output": result.stdout if result.success else None,
        "error": result.error_message if not result.success else None,
        "violations": result.validation_violations,
    }
```

### Streaming Feedback Pattern

```python
def execute_with_progress_callback(code: str, callback_fn) -> dict:
    """
    Execute code with progress callbacks (future enhancement).

    Args:
        code: Code to execute
        callback_fn: Function called with progress updates

    Returns:
        Final execution result
    """
    callback_fn({"stage": "validation", "progress": 0.1})

    executor = CodeExecutor()

    callback_fn({"stage": "execution", "progress": 0.5})

    result = executor.execute(code)

    callback_fn({"stage": "complete", "progress": 1.0})

    return result.to_dict()
```

## Django Integration

### View Function Example

```python
# views.py
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
import json

from apps.ai.code_execution import CodeExecutor, ValidationError

@login_required
@require_http_methods(["POST"])
def execute_code(request):
    """
    API endpoint for executing user code.

    POST /api/execute-code/
    Body: {"code": "print('hello')", "context": {...}}
    """
    try:
        data = json.loads(request.body)
        code = data.get("code")
        context = data.get("context", {})

        if not code:
            return JsonResponse(
                {"error": "No code provided"},
                status=400
            )

        # Create executor for this user
        executor = CodeExecutor(user_id=request.user.id)

        # Execute
        result = executor.execute(code, context=context)

        # Return result
        return JsonResponse({
            "success": result.success,
            "output": result.stdout,
            "error": result.error_message,
            "execution_time": result.execution_time_seconds,
            "violations": result.validation_violations + result.output_violations,
        })

    except ValidationError as e:
        return JsonResponse({
            "error": "Code validation failed",
            "details": e.to_dict()
        }, status=400)

    except Exception as e:
        return JsonResponse({
            "error": "Internal server error",
            "message": str(e)
        }, status=500)
```

### Celery Task Example

```python
# tasks.py
from celery import shared_task
from apps.ai.code_execution import CodeExecutor

@shared_task(bind=True, max_retries=3)
def execute_code_async(self, code: str, user_id: int, context: dict = None):
    """
    Execute code asynchronously via Celery.

    Args:
        code: Python code to execute
        user_id: ID of user requesting execution
        context: Optional execution context

    Returns:
        Dictionary with execution results
    """
    try:
        executor = CodeExecutor(user_id=user_id)
        result = executor.execute(code, context=context)

        return result.to_dict()

    except Exception as e:
        # Retry on unexpected errors
        raise self.retry(exc=e, countdown=60)


# Usage in views:
from .tasks import execute_code_async

def start_execution(request):
    task = execute_code_async.delay(
        code=request.POST['code'],
        user_id=request.user.id,
        context={"user_data": "..."}
    )

    return JsonResponse({
        "task_id": task.id,
        "status": "pending"
    })
```

### Model Integration Example

```python
# models.py
from django.db import models
from django.contrib.auth import get_user_model
from apps.ai.code_execution import CodeExecutor

User = get_user_model()

class CodeSnippet(models.Model):
    """Store and execute code snippets."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    code = models.TextField()
    description = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    # Execution results
    last_executed_at = models.DateTimeField(null=True, blank=True)
    last_output = models.TextField(blank=True)
    last_error = models.TextField(blank=True)
    execution_count = models.IntegerField(default=0)

    def execute(self, context=None):
        """Execute this snippet and store results."""
        from django.utils import timezone

        executor = CodeExecutor(user_id=self.user.id)
        result = executor.execute(self.code, context=context)

        # Update execution metadata
        self.last_executed_at = timezone.now()
        self.execution_count += 1

        if result.success:
            self.last_output = result.stdout
            self.last_error = ""
        else:
            self.last_output = ""
            self.last_error = result.error_message

        self.save()

        return result

    class Meta:
        ordering = ["-created_at"]
```

## Error Handling Patterns

### Comprehensive Error Handling

```python
from apps.ai.code_execution import (
    CodeExecutor,
    ValidationError,
    TimeoutError,
    SecurityViolationError,
)

def robust_execute(code: str, user_id: int) -> dict:
    """Execute code with comprehensive error handling."""
    executor = CodeExecutor(user_id=user_id)

    try:
        result = executor.execute(code)

        if not result.success:
            # Categorize errors
            if result.validation_violations:
                high_severity = [
                    v for v in result.validation_violations
                    if v.get("severity") == "high"
                ]
                if high_severity:
                    return {
                        "status": "rejected",
                        "reason": "security_violation",
                        "violations": high_severity,
                    }

            if result.error_type == "TimeoutError":
                return {
                    "status": "timeout",
                    "reason": "execution_too_slow",
                    "suggestion": "Optimize code or reduce iterations",
                }

            return {
                "status": "error",
                "reason": "runtime_error",
                "error": result.error_message,
                "error_type": result.error_type,
            }

        return {
            "status": "success",
            "output": result.stdout,
            "execution_time": result.execution_time_seconds,
            "warnings": result.output_violations,
        }

    except Exception as e:
        return {
            "status": "error",
            "reason": "unexpected_error",
            "error": str(e),
        }
```

### Graceful Degradation

```python
def execute_with_fallback(code: str, fallback_result=None):
    """Execute code with fallback on errors."""
    executor = CodeExecutor()

    try:
        result = executor.execute(code)

        if result.success:
            return result.stdout
        else:
            # Log error but return fallback
            logger.warning(
                f"Code execution failed: {result.error_message}",
                extra={"code": code[:100]}
            )
            return fallback_result

    except Exception as e:
        logger.exception("Unexpected error during code execution")
        return fallback_result
```

## Advanced Configuration

### Per-User Resource Limits

```python
from apps.ai.code_execution.sandboxes import SandboxConfig

def create_executor_for_user(user) -> CodeExecutor:
    """Create executor with limits based on user tier."""

    # Free tier: strict limits
    if user.tier == "free":
        config = SandboxConfig(
            timeout_seconds=5,
            max_memory_mb=128,
            max_output_bytes=50_000,
        )

    # Pro tier: generous limits
    elif user.tier == "pro":
        config = SandboxConfig(
            timeout_seconds=30,
            max_memory_mb=512,
            max_output_bytes=1_000_000,
        )

    # Enterprise: maximum limits
    else:
        config = SandboxConfig(
            timeout_seconds=60,
            max_memory_mb=1024,
            max_output_bytes=10_000_000,
        )

    return CodeExecutor(
        user_id=user.id,
        sandbox_config=config,
    )
```

### Custom Validation Rules

```python
from apps.ai.code_execution.validators import ASTValidator

# Create custom validator with specific rules
custom_validator = ASTValidator(
    forbidden_imports={
        "os", "sys", "subprocess",
        # Allow more modules
    },
    forbidden_functions={
        "eval", "exec",
        # Allow getattr and setattr
    },
    max_operations=5000,  # Higher limit
    allow_getattr=True,
    allow_setattr=True,
)

# Use in executor
executor = CodeExecutor()
executor.ast_validator = custom_validator

result = executor.execute(code)
```

### Sandbox Selection Strategy

```python
def select_sandbox_for_task(task_type: str, user_tier: str):
    """Select appropriate sandbox based on task and user."""

    # Production-ready sandboxes (future implementation)
    if task_type == "critical" and user_tier == "enterprise":
        # Use Firecracker for maximum isolation
        # sandbox_type = FirecrackerSandbox
        pass

    elif task_type == "standard" and user_tier in ["pro", "enterprise"]:
        # Use gVisor for good isolation with better performance
        # sandbox_type = GVisorSandbox
        pass

    else:
        # Development/free tier: RestrictedPython
        sandbox_type = RestrictedPythonSandbox

    return CodeExecutor(sandbox_type=sandbox_type)
```

### Logging and Monitoring

```python
import logging
from apps.ai.code_execution import CodeExecutor

# Configure detailed logging
logger = logging.getLogger('code_execution')
logger.setLevel(logging.DEBUG)

# Custom logging handler
class ExecutionMetricsLogger(logging.Handler):
    def emit(self, record):
        if hasattr(record, 'execution_time'):
            # Send to metrics system
            metrics.histogram(
                'code_execution.duration',
                record.execution_time,
                tags=[f"success:{record.success}"]
            )

logger.addHandler(ExecutionMetricsLogger())

# Use executor with logging
executor = CodeExecutor(log_executions=True)
result = executor.execute(code)
```

## Testing Examples

### Unit Test Example

```python
import pytest
from apps.ai.code_execution import CodeExecutor

class TestCodeExecution:
    def test_simple_print(self):
        executor = CodeExecutor()
        result = executor.execute("print('test')")

        assert result.success
        assert "test" in result.stdout

    def test_import_blocking(self):
        executor = CodeExecutor()
        result = executor.execute("import os")

        assert not result.success or result.validation_violations

    def test_context_passing(self):
        executor = CodeExecutor()
        result = executor.execute(
            "print(context['value'])",
            context={"value": 42}
        )

        assert result.success
        assert "42" in result.stdout
```

## Conclusion

These examples demonstrate the flexibility and safety of the code execution module. Remember:

1. **Always validate** user-provided code
2. **Use appropriate limits** based on user tier/task type
3. **Handle errors gracefully** with fallbacks
4. **Log everything** for debugging and security monitoring
5. **Test thoroughly** with both valid and malicious inputs

For production use, replace `RestrictedPythonSandbox` with OS-level isolation (E2B, gVisor, or Firecracker).
