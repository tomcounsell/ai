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
from .paths import CONFIG_DIR, DATA_DIR, HOME_DIR, LOGS_DIR, PROJECT_ROOT, SECRETS_DIR, SRC_DIR
from .settings import (
    APISettings,
    DatabaseSettings,
    GoogleAuthSettings,
    LoggingSettings,
    LogLevel,
    ModelSettings,
    PathSettings,
    PerformanceSettings,
    RedisSettings,
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
    "RedisSettings",
    "GoogleAuthSettings",
    "ModelSettings",
    "PathSettings",
    "LogLevel",
    # Global settings instance
    "settings",
    # Path constants
    "PROJECT_ROOT",
    "DATA_DIR",
    "LOGS_DIR",
    "CONFIG_DIR",
    "SECRETS_DIR",
    "HOME_DIR",
    "SRC_DIR",
    # Configuration loader
    "ConfigLoader",
    "WorkspaceConfig",
    "ConfigurationError",
    "config_loader",
    # Convenience functions
    "load_workspace_config",
    "validate_configuration",
]
