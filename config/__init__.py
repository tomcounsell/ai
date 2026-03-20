"""
Configuration module for AI Rebuild system.

This module provides comprehensive configuration management with environment-based
settings, validation, and workspace configuration support.
"""

from .loader import (
    ConfigLoader,
    ConfigurationError,
    config_loader,
    validate_configuration,
)
from .paths import CONFIG_DIR, DATA_DIR, HOME_DIR, LOGS_DIR, PROJECT_ROOT, SRC_DIR, VALOR_DIR
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
    "VALOR_DIR",
    "HOME_DIR",
    "SRC_DIR",
    # Configuration loader
    "ConfigLoader",
    "ConfigurationError",
    "config_loader",
    # Convenience functions
    "validate_configuration",
]
