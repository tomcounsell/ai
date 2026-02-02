"""
Service layer for code execution orchestration.

This package contains the high-level business logic for executing LLM-generated
code safely. It coordinates between validators, sandboxes, and logging.
"""

from .executor import CodeExecutor, ExecutionResult

__all__ = ["CodeExecutor", "ExecutionResult"]
