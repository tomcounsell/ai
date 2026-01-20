# Code Execution SOP

**Version**: 1.0.0
**Last Updated**: 2026-01-20
**Owner**: Valor AI System
**Status**: Active

## Overview

This SOP defines the standard procedure for sandboxed code execution. It covers safety measures, resource limits, and result handling for Python, JavaScript, and Bash code.

## Prerequisites

- Python 3.10+ installed
- Node.js installed (for JavaScript)
- Bash available (for shell scripts)
- Temporary directory writable

## Parameters

### Required
- **code** (string): Source code to execute
  - Description: The code to run in the sandbox

### Optional
- **language** (string): Programming language
  - Values: `python` | `javascript` | `bash`
  - Default: `python`

- **timeout_seconds** (integer): Execution timeout
  - Range: 1-300
  - Default: 30

- **memory_limit_mb** (integer): Memory limit
  - Range: 16-1024
  - Default: 128

- **enable_network** (boolean): Allow network access
  - Default: `false`

- **input_data** (string): Stdin input
  - Default: None

## Steps

### 1. Validate Code

**Purpose**: Ensure code is safe to execute.

**Actions**:
- MUST check code is not empty
- MUST validate language is supported
- MUST check for obviously dangerous patterns
- SHOULD warn on potentially risky operations
- MUST NOT execute if validation fails

**Dangerous Patterns**:
```python
BLOCKED_PATTERNS = [
    r"os\.system\s*\(",
    r"subprocess\.(run|call|Popen)",
    r"eval\s*\(",
    r"exec\s*\(",
    r"__import__",
    r"open\s*\([^)]*['\"][wa]",  # Write mode
    r"rm\s+-rf",
    r":(){ :|:& };:",  # Fork bomb
]
```

**Validation**:
- Code passes safety checks
- Language is supported

**Error Handling**:
- If empty: Return error "Code cannot be empty"
- If dangerous: Return error with pattern matched

### 2. Prepare Sandbox

**Purpose**: Create isolated execution environment.

**Actions**:
- MUST create temporary directory for execution
- MUST set resource limits
- MUST disable network if not enabled
- SHOULD isolate from main filesystem
- MAY create virtual environment for Python

**Sandbox Setup**:
```python
with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as f:
    f.write(code)
    temp_path = f.name
```

**Resource Limits**:
- Timeout: Enforced via subprocess timeout
- Memory: Platform-dependent limits
- Disk: Temp directory only

### 3. Execute Code

**Purpose**: Run the code with appropriate interpreter.

**Actions**:
- MUST use subprocess with timeout
- MUST capture stdout and stderr
- MUST run in temporary directory
- MUST NOT inherit environment variables (selective)
- SHOULD track execution time

**Execution**:
```python
proc = subprocess.run(
    [interpreter, temp_path],
    capture_output=True,
    text=True,
    timeout=timeout_seconds,
    input=input_data,
    cwd=tempfile.gettempdir(),
)
```

**Language Interpreters**:
- Python: `sys.executable`
- JavaScript: `node`
- Bash: `bash`

### 4. Capture Results

**Purpose**: Collect execution output and metrics.

**Actions**:
- MUST capture stdout
- MUST capture stderr
- MUST record exit code
- MUST record execution time
- SHOULD truncate large outputs

**Output Limits**:
- stdout: 100KB max
- stderr: 100KB max

### 5. Cleanup

**Purpose**: Remove temporary files and resources.

**Actions**:
- MUST delete temporary code file
- MUST clean up any created files
- SHOULD release resources
- MAY log execution metrics

**Cleanup**:
```python
finally:
    Path(temp_path).unlink(missing_ok=True)
```

## Success Criteria

- Code executes within timeout
- Output captured successfully
- Resources cleaned up
- No security violations

## Error Recovery

| Error Type | Recovery Procedure |
|------------|-------------------|
| Empty code | Return validation error |
| Unsupported language | Return error with supported list |
| Timeout | Kill process, return timeout error |
| Memory exceeded | Kill process, return memory error |
| Syntax error | Return stderr with syntax details |
| Runtime error | Return stderr with stack trace |

## Security Constraints

- MUST NOT allow arbitrary file system access
- MUST NOT allow network access by default
- MUST enforce timeout limits
- MUST run in isolated directory
- MUST NOT expose system environment
- MUST NOT allow process spawning

## Examples

### Example 1: Simple Python

```
Input:
  code: "print('Hello, World!')"
  language: python

Output:
  stdout: "Hello, World!\n"
  stderr: ""
  exit_code: 0
  execution_time_ms: 45
  language: python
```

### Example 2: Python with Error

```
Input:
  code: "x = 1/0"
  language: python

Output:
  stdout: ""
  stderr: "ZeroDivisionError: division by zero..."
  exit_code: 1
  execution_time_ms: 32
  language: python
```

### Example 3: JavaScript

```
Input:
  code: "console.log(JSON.stringify({a: 1, b: 2}))"
  language: javascript

Output:
  stdout: '{"a":1,"b":2}\n'
  stderr: ""
  exit_code: 0
  execution_time_ms: 78
  language: javascript
```

### Example 4: Timeout

```
Input:
  code: "import time; time.sleep(100)"
  language: python
  timeout_seconds: 5

Output:
  error: "Execution timed out after 5 seconds"
  language: python
  timeout_seconds: 5
```

## Related SOPs

- [Test Execution](test-execution.sop.md)
- [Feature Development](../workflows/feature-development.sop.md)

## Version History

- v1.0.0 (2026-01-20): Initial version
