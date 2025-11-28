"""
Module System for Autonomous Module Generation

This package provides the infrastructure for:
- Standard module contracts (ModuleInput, ModuleOutput)
- Module registry for discovery and management
- Module builder subagent for autonomous generation

Architecture:
- framework/: Core contracts and base classes
- registry/: Module discovery and registration
- builder/: ModuleBuilderAgent for autonomous generation
- generated_modules/: Output directory for generated modules
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
from modules.registry.registry import ModuleRegistry, ModuleRegistryEntry

__all__ = [
    # Contracts
    "ModuleInput",
    "ModuleOutput",
    "ExecutionContext",
    "ExecutionStatus",
    "ErrorDetail",
    "SideEffect",
    # Base classes
    "BaseModule",
    # Registry
    "ModuleRegistry",
    "ModuleRegistryEntry",
]
