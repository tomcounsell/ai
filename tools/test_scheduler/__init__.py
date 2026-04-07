"""
Test Scheduler Tool

Schedule test runs through background queue with resource management.
"""

import json
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

# In-memory job storage (for simplicity)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()

DEFAULT_RESULTS_DIR = Path.home() / ".valor" / "test_results"


class TestSchedulerError(Exception):
    """Test scheduler operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


def _parse_test_specification(spec: str) -> list[dict]:
    """
    Parse test specification into individual tests.

    Args:
        spec: Test specification string

    Returns:
        List of test configurations
    """
    tests = []

    # Check for common test patterns
    if spec.startswith("pytest"):
        tests.append(
            {
                "command": spec,
                "type": "pytest",
                "framework": "pytest",
            }
        )
    elif spec.startswith("python -m unittest"):
        tests.append(
            {
                "command": spec,
                "type": "unittest",
                "framework": "unittest",
            }
        )
    elif spec.startswith("npm test") or spec.startswith("jest"):
        tests.append(
            {
                "command": spec,
                "type": "jest",
                "framework": "jest",
            }
        )
    else:
        # Treat as generic command
        tests.append(
            {
                "command": spec,
                "type": "generic",
                "framework": "unknown",
            }
        )

    return tests


def _run_test_job(job_id: str, tests: list[dict], timeout_minutes: int):
    """
    Execute tests in background thread.

    Args:
        job_id: Job identifier
        tests: List of tests to run
        timeout_minutes: Maximum runtime
    """
    with _lock:
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = datetime.now().isoformat()

    results = []
    timeout_seconds = timeout_minutes * 60

    for test in tests:
        start_time = time.time()
        try:
            proc = subprocess.run(
                test["command"],
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )

            results.append(
                {
                    "command": test["command"],
                    "type": test["type"],
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                    "duration_seconds": time.time() - start_time,
                    "passed": proc.returncode == 0,
                }
            )
        except subprocess.TimeoutExpired:
            results.append(
                {
                    "command": test["command"],
                    "type": test["type"],
                    "exit_code": -1,
                    "error": "Test timed out",
                    "duration_seconds": timeout_seconds,
                    "passed": False,
                }
            )
        except Exception as e:
            results.append(
                {
                    "command": test["command"],
                    "type": test["type"],
                    "exit_code": -1,
                    "error": str(e),
                    "duration_seconds": time.time() - start_time,
                    "passed": False,
                }
            )

    # Update job status
    with _lock:
        _jobs[job_id]["status"] = "completed"
        _jobs[job_id]["completed_at"] = datetime.now().isoformat()
        _jobs[job_id]["results"] = results
        _jobs[job_id]["summary"] = {
            "total": len(results),
            "passed": sum(1 for r in results if r["passed"]),
            "failed": sum(1 for r in results if not r["passed"]),
        }

    # Save results to file
    results_dir = DEFAULT_RESULTS_DIR
    results_dir.mkdir(parents=True, exist_ok=True)
    results_file = results_dir / f"{job_id}.json"
    results_file.write_text(json.dumps(_jobs[job_id], indent=2))


def schedule_tests(
    test_specification: str,
    notification_chat_id: str | None = None,
    max_workers: int = 2,
    timeout_minutes: int = 10,
    priority: Literal["low", "normal", "high"] = "normal",
) -> dict:
    """
    Schedule tests for execution.

    Args:
        test_specification: Description of tests to run
        notification_chat_id: Where to send results (optional)
        max_workers: Parallel execution limit (default: 2)
        timeout_minutes: Maximum runtime (default: 10)
        priority: Job priority

    Returns:
        dict with:
            - job_id: Scheduled job identifier
            - status: Scheduling status
            - tests_to_run: List of tests
            - estimated_duration: Estimated run time
    """
    if not test_specification or not test_specification.strip():
        return {"error": "Test specification is required"}

    # Parse specification
    tests = _parse_test_specification(test_specification.strip())

    if not tests:
        return {"error": "No tests found in specification"}

    # Create job
    job_id = str(uuid.uuid4())[:8]

    job = {
        "job_id": job_id,
        "status": "scheduled",
        "created_at": datetime.now().isoformat(),
        "specification": test_specification,
        "tests": tests,
        "notification_chat_id": notification_chat_id,
        "max_workers": max_workers,
        "timeout_minutes": timeout_minutes,
        "priority": priority,
    }

    with _lock:
        _jobs[job_id] = job

    # Start background execution
    thread = threading.Thread(
        target=_run_test_job,
        args=(job_id, tests, timeout_minutes),
        daemon=True,
    )
    thread.start()

    # Estimate duration based on timeout and test count
    estimated_seconds = len(tests) * 30  # Rough estimate
    estimated_duration = f"{estimated_seconds // 60}m {estimated_seconds % 60}s"

    return {
        "job_id": job_id,
        "status": "scheduled",
        "tests_to_run": [t["command"] for t in tests],
        "test_count": len(tests),
        "estimated_duration": estimated_duration,
        "timeout_minutes": timeout_minutes,
    }


def get_job_status(job_id: str) -> dict:
    """
    Get status of a scheduled job.

    Args:
        job_id: Job identifier

    Returns:
        dict with job status
    """
    with _lock:
        job = _jobs.get(job_id)

    if not job:
        # Try loading from file
        results_file = DEFAULT_RESULTS_DIR / f"{job_id}.json"
        if results_file.exists():
            return json.loads(results_file.read_text())
        return {"error": f"Job not found: {job_id}"}

    return job


def list_jobs(
    status_filter: str | None = None,
    limit: int = 10,
) -> dict:
    """
    List scheduled jobs.

    Args:
        status_filter: Filter by status (scheduled, running, completed)
        limit: Maximum jobs to return

    Returns:
        dict with job list
    """
    with _lock:
        jobs = list(_jobs.values())

    if status_filter:
        jobs = [j for j in jobs if j["status"] == status_filter]

    # Sort by creation time, newest first
    jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    return {
        "jobs": jobs[:limit],
        "total": len(jobs),
        "filtered_by": status_filter,
    }


def cancel_job(job_id: str) -> dict:
    """
    Cancel a scheduled job.

    Args:
        job_id: Job identifier

    Returns:
        dict with cancellation result
    """
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return {"error": f"Job not found: {job_id}"}

        if job["status"] == "completed":
            return {"error": "Cannot cancel completed job"}

        job["status"] = "cancelled"
        job["cancelled_at"] = datetime.now().isoformat()

    return {
        "job_id": job_id,
        "status": "cancelled",
    }


def get_job_results(job_id: str) -> dict:
    """
    Get detailed results for a completed job.

    Args:
        job_id: Job identifier

    Returns:
        dict with test results
    """
    job = get_job_status(job_id)

    if "error" in job:
        return job

    if job.get("status") != "completed":
        return {
            "error": "Job not completed",
            "status": job.get("status"),
        }

    return {
        "job_id": job_id,
        "results": job.get("results", []),
        "summary": job.get("summary", {}),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tools.test_scheduler 'pytest tests/'")
        sys.exit(1)

    spec = sys.argv[1]
    print(f"Scheduling: {spec}")

    result = schedule_tests(spec)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    else:
        print(f"Job ID: {result['job_id']}")
        print(f"Tests: {result['test_count']}")
        print(f"Estimated: {result['estimated_duration']}")

        # Wait for completion
        print("\nWaiting for completion...")
        while True:
            status = get_job_status(result["job_id"])
            if status.get("status") == "completed":
                print(f"\nResults: {json.dumps(status.get('summary', {}), indent=2)}")
                break
            time.sleep(1)
