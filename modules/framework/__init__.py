"""
Module Framework - Standard Contracts and Base Classes

Provides the core infrastructure for all modules:
- ModuleInput/ModuleOutput contracts
- ExecutionContext for context passing
- BaseModule abstract class for implementations
"""

from modules.framework.contracts import (
    ModuleInput,
    ModuleOutput,
    ExecutionContext,
    ExecutionStatus,
    ErrorDetail,
    SideEffect,
)
from modules.framework.base import BaseModule

__all__ = [
    "ModuleInput",
    "ModuleOutput",
    "ExecutionContext",
    "ExecutionStatus",
    "ErrorDetail",
    "SideEffect",
    "BaseModule",
]
