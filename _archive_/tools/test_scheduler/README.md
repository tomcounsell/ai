# Test Scheduler Tool

Schedule test runs through background queue with resource management.

## Overview

This tool provides background test execution:
- Schedule tests for execution
- Monitor session status
- Retrieve results
- Cancel pending sessions

## Installation

No external dependencies required.

## Quick Start

```python
from tools.test_scheduler import schedule_tests, get_session_status

# Schedule tests
result = schedule_tests("pytest tests/ -v")
session_id = result["agent_session_id"]

# Check status
status = get_session_status(session_id)
print(f"Status: {status['status']}")

# Get results when complete
if status["status"] == "completed":
    print(f"Passed: {status['summary']['passed']}")
    print(f"Failed: {status['summary']['failed']}")
```

## API Reference

### schedule_tests()

```python
def schedule_tests(
    test_specification: str,
    notification_chat_id: str | None = None,
    max_workers: int = 2,
    timeout_minutes: int = 10,
    priority: Literal["low", "normal", "high"] = "normal",
) -> dict
```

**Parameters:**
- `test_specification`: Test command to run
- `notification_chat_id`: Where to send results
- `max_workers`: Parallel execution limit
- `timeout_minutes`: Maximum runtime
- `priority`: Session priority

**Returns:**
```python
{
    "agent_session_id": str,
    "status": "scheduled",
    "tests_to_run": list[str],
    "test_count": int,
    "estimated_duration": str,
    "timeout_minutes": int
}
```

### get_session_status()

```python
def get_session_status(session_id: str) -> dict
```

Get current status of a session.

**Returns:**
```python
{
    "agent_session_id": str,
    "status": str,  # scheduled, running, completed, cancelled
    "created_at": str,
    "results": list[dict],  # if completed
    "summary": dict         # if completed
}
```

### list_sessions()

```python
def list_sessions(status_filter: str | None = None, limit: int = 10) -> dict
```

List scheduled sessions.

### cancel_session()

```python
def cancel_session(session_id: str) -> dict
```

Cancel a scheduled session.

### get_session_results()

```python
def get_session_results(session_id: str) -> dict
```

Get detailed results for a completed session.

## Workflows

### Schedule and Wait
```python
result = schedule_tests("pytest tests/")
session_id = result["agent_session_id"]

# Poll for completion
import time
while True:
    status = get_session_status(session_id)
    if status["status"] == "completed":
        break
    time.sleep(5)

# Get results
print(f"Summary: {status['summary']}")
```

### With Timeout
```python
result = schedule_tests(
    "pytest tests/ -v",
    timeout_minutes=30
)
```

### List Recent Sessions
```python
sessions = list_sessions(limit=5)
for session in sessions["sessions"]:
    print(f"{session['agent_session_id']}: {session['status']}")
```

## Session Statuses

| Status | Description |
|--------|-------------|
| scheduled | Session created, waiting to run |
| running | Tests executing |
| completed | All tests finished |
| cancelled | Session was cancelled |

## Results Storage

Results are saved to `~/.valor/test_results/<agent_session_id>.json`.

## Error Handling

```python
result = schedule_tests(spec)

if "error" in result:
    print(f"Scheduling failed: {result['error']}")
else:
    print(f"Session scheduled: {result['agent_session_id']}")
```

## Troubleshooting

### Session Stuck in Running
Check if the test command is hanging. Consider reducing timeout.

### Results Not Found
Ensure the session completed. Check session status first.
