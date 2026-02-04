"""
Configuration Management System for AI Rebuild

This module provides comprehensive configuration management using pydantic-settings
for environment-based configuration with validation and type safety.
"""

import logging
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LogLevel(str, Enum):
    """Supported logging levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class DatabaseSettings(BaseModel):
    """Database configuration settings."""

    path: Path = Field(
        default=Path("data/ai_rebuild.db"), description="Path to SQLite database file"
    )
    echo: bool = Field(default=False, description="Enable SQL query logging")
    pool_size: int = Field(
        default=20, description="Database connection pool size", ge=1, le=100
    )


class APISettings(BaseModel):
    """API service configuration settings."""

    claude_api_key: str | None = Field(
        default=None, description="Claude API key for AI services"
    )
    openai_api_key: str | None = Field(default=None, description="OpenAI API key")
    perplexity_api_key: str | None = Field(
        default=None, description="Perplexity API key for search"
    )
    notion_api_key: str | None = Field(
        default=None, description="Notion API key for workspace integration"
    )

    @field_validator(
        "claude_api_key", "openai_api_key", "perplexity_api_key", "notion_api_key"
    )
    @classmethod
    def validate_api_keys(cls, v):
        """Validate API key format if provided."""
        if v and len(v.strip()) < 10:
            raise ValueError("API key must be at least 10 characters long")
        return v.strip() if v else None


class TelegramSettings(BaseModel):
    """Telegram integration settings."""

    api_id: int | None = Field(default=None, description="Telegram API ID", ge=1)
    api_hash: str | None = Field(default=None, description="Telegram API hash")
    session_name: str = Field(
        default="ai_rebuild_session", description="Telegram session name"
    )

    @field_validator("api_hash")
    @classmethod
    def validate_api_hash(cls, v):
        """Validate Telegram API hash format."""
        if v and len(v.strip()) != 32:
            raise ValueError("Telegram API hash must be 32 characters long")
        return v.strip() if v else None


class ServerSettings(BaseModel):
    """Server configuration settings."""

    host: str = Field(default="127.0.0.1", description="Server host address")
    port: int = Field(default=8000, description="Server port", ge=1000, le=65535)
    reload: bool = Field(default=False, description="Enable auto-reload in development")
    workers: int = Field(
        default=1, description="Number of worker processes", ge=1, le=16
    )


class SecuritySettings(BaseModel):
    """Security and authentication settings."""

    secret_key: str = Field(
        default="dev-secret-key-change-in-production",
        description="Secret key for session management",
        min_length=32,
    )
    allowed_hosts: list[str] = Field(
        default=["localhost", "127.0.0.1"], description="Allowed hosts for CORS"
    )
    api_rate_limit: int = Field(
        default=100, description="API requests per minute limit", ge=10, le=1000
    )


class LoggingSettings(BaseModel):
    """Logging configuration settings."""

    level: LogLevel = Field(default=LogLevel.INFO, description="Logging level")
    format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log message format",
    )
    file_path: Path | None = Field(
        default=Path("logs/ai_rebuild.log"), description="Log file path"
    )
    max_file_size: int = Field(
        default=10 * 1024 * 1024,  # 10MB
        description="Maximum log file size in bytes",
        ge=1024 * 1024,  # 1MB minimum
    )
    backup_count: int = Field(
        default=5, description="Number of backup log files to keep", ge=1, le=20
    )


class WorkspaceSettings(BaseModel):
    """Workspace configuration settings."""

    config_path: Path = Field(
        default=Path("config/workspace_config.json"),
        description="Path to workspace configuration file",
    )
    data_dir: Path = Field(default=Path("data"), description="Data directory path")
    temp_dir: Path = Field(
        default=Path("temp"), description="Temporary files directory"
    )
    max_file_size: int = Field(
        default=100 * 1024 * 1024,  # 100MB
        description="Maximum file size for uploads",
        ge=1024 * 1024,  # 1MB minimum
    )


class PerformanceSettings(BaseModel):
    """Performance and resource management settings."""

    max_workers: int = Field(
        default=4, description="Maximum number of worker threads", ge=1, le=32
    )
    timeout: int = Field(
        default=30, description="Default request timeout in seconds", ge=5, le=300
    )
    cache_ttl: int = Field(
        default=3600,
        description="Cache time-to-live in seconds",
        ge=60,
        le=86400,  # 24 hours
    )
    memory_limit: int = Field(
        default=1024, description="Memory limit in MB", ge=256, le=8192
    )


class Settings(BaseSettings):
    """Main application settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    # Environment
    environment: str = Field(
        default="development",
        description="Application environment (development, staging, production)",
    )
    debug: bool = Field(default=False, description="Enable debug mode")

    # Component settings
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    api: APISettings = Field(default_factory=APISettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    workspace: WorkspaceSettings = Field(default_factory=WorkspaceSettings)
    performance: PerformanceSettings = Field(default_factory=PerformanceSettings)

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v):
        """Validate environment value."""
        allowed_envs = ["development", "staging", "production", "testing"]
        if v not in allowed_envs:
            raise ValueError(f"Environment must be one of: {', '.join(allowed_envs)}")
        return v

    def setup_logging(self) -> None:
        """Configure logging based on settings."""
        # Create logs directory if it doesn't exist
        if self.logging.file_path:
            self.logging.file_path.parent.mkdir(parents=True, exist_ok=True)

        # Configure logging
        logging.basicConfig(
            level=getattr(logging, self.logging.level.value),
            format=self.logging.format,
            handlers=[
                logging.StreamHandler(),
                (
                    logging.FileHandler(self.logging.file_path)
                    if self.logging.file_path
                    else logging.NullHandler()
                ),
            ],
        )

    def create_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        directories = [
            self.database.path.parent,
            self.workspace.data_dir,
            self.workspace.temp_dir,
            self.workspace.config_path.parent,
        ]

        if self.logging.file_path:
            directories.append(self.logging.file_path.parent)

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def get_database_url(self) -> str:
        """Get the database connection URL."""
        return f"sqlite:///{self.database.path}"

    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment == "production"

    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.environment == "development"

    def get_api_config(self) -> dict[str, Any]:
        """Get API configuration for external services."""
        config = {}

        if self.api.claude_api_key:
            config["claude"] = {"api_key": self.api.claude_api_key}

        if self.api.openai_api_key:
            config["openai"] = {"api_key": self.api.openai_api_key}

        if self.api.perplexity_api_key:
            config["perplexity"] = {"api_key": self.api.perplexity_api_key}

        if self.api.notion_api_key:
            config["notion"] = {"api_key": self.api.notion_api_key}

        if self.telegram.api_id and self.telegram.api_hash:
            config["telegram"] = {
                "api_id": self.telegram.api_id,
                "api_hash": self.telegram.api_hash,
                "session_name": self.telegram.session_name,
            }

        return config


# Global settings instance
settings = Settings()
