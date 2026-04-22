"""
reflections/ — Standalone reflection callables for the YAML scheduler.

Each module contains one or more async functions that the ReflectionScheduler
can invoke by dotted path. All functions:
  - Accept no arguments
  - Return a dict: {"status": "ok"|"error", "findings": [...], "summary": str}
  - Handle redis.exceptions.ConnectionError gracefully
"""
