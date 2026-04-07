# Test Scheduler Tool

Schedule test runs through background queue with resource management.

## Overview

This tool provides background test execution:
- Schedule tests for execution
- Monitor job status
- Retrieve results
- Cancel pending jobs

## Installation

No external dependencies required.

## Quick Start

```python
from tools.test_scheduler import schedule_tests, get_job_status

# Schedule tests
result = schedule_tests("pytest tests/ -v")
job_id = result["job_id"]

# Check status
status = get_job_status(job_id)
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
- `priority`: Job priority

**Returns:**
```python
{
    "job_id": str,
    "status": "scheduled",
    "tests_to_run": list[str],
    "test_count": int,
    "estimated_duration": str,
    "timeout_minutes": int
}
```

### get_job_status()

```python
def get_job_status(job_id: str) -> dict
```

Get current status of a job.

**Returns:**
```python
{
    "job_id": str,
    "status": str,  # scheduled, running, completed, cancelled
    "created_at": str,
    "results": list[dict],  # if completed
    "summary": dict         # if completed
}
```

### list_jobs()

```python
def list_jobs(status_filter: str | None = None, limit: int = 10) -> dict
```

List scheduled jobs.

### cancel_job()

```python
def cancel_job(job_id: str) -> dict
```

Cancel a scheduled job.

### get_job_results()

```python
def get_job_results(job_id: str) -> dict
```

Get detailed results for a completed job.

## Workflows

### Schedule and Wait
```python
result = schedule_tests("pytest tests/")
job_id = result["job_id"]

# Poll for completion
import time
while True:
    status = get_job_status(job_id)
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

### List Recent Jobs
```python
jobs = list_jobs(limit=5)
for job in jobs["jobs"]:
    print(f"{job['job_id']}: {job['status']}")
```

## Job Statuses

| Status | Description |
|--------|-------------|
| scheduled | Job created, waiting to run |
| running | Tests executing |
| completed | All tests finished |
| cancelled | Job was cancelled |

## Results Storage

Results are saved to `~/.valor/test_results/<job_id>.json`.

## Error Handling

```python
result = schedule_tests(spec)

if "error" in result:
    print(f"Scheduling failed: {result['error']}")
else:
    print(f"Job scheduled: {result['job_id']}")
```

## Troubleshooting

### Job Stuck in Running
Check if the test command is hanging. Consider reducing timeout.

### Results Not Found
Ensure the job completed. Check job status first.
