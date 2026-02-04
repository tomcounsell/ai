"""
Configuration module for AI Rebuild system.

This module provides comprehensive configuration management with environment-based
settings, validation, and workspace configuration support.
"""

from .loader import (
    ConfigLoader,
    ConfigurationError,
    WorkspaceConfig,
    config_loader,
    load_workspace_config,
    validate_configuration,
)
from .settings import (
    APISettings,
    DatabaseSettings,
    LoggingSettings,
    LogLevel,
    PerformanceSettings,
    SecuritySettings,
    ServerSettings,
    Settings,
    TelegramSettings,
    WorkspaceSettings,
    settings,
)

__all__ = [
    # Settings classes
    "Settings",
    "DatabaseSettings",
    "APISettings",
    "TelegramSettings",
    "ServerSettings",
    "SecuritySettings",
    "LoggingSettings",
    "WorkspaceSettings",
    "PerformanceSettings",
    "LogLevel",
    # Global settings instance
    "settings",
    # Configuration loader
    "ConfigLoader",
    "WorkspaceConfig",
    "ConfigurationError",
    "config_loader",
    # Convenience functions
    "load_workspace_config",
    "validate_configuration",
]
