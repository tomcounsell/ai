"""
Module Registry - Discovery and Management

Provides centralized registration and discovery of modules:
- Register modules from filesystem paths
- Discover modules by capabilities, category, or search
- Track module health and usage statistics
"""

from modules.registry.registry import (
    ModuleRegistry,
    ModuleRegistryEntry,
    ModuleType,
    AuthStatus,
    HealthStatus,
)

__all__ = [
    "ModuleRegistry",
    "ModuleRegistryEntry",
    "ModuleType",
    "AuthStatus",
    "HealthStatus",
]
