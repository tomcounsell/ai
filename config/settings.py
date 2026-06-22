"""
Configuration Management System for AI Rebuild

This module provides comprehensive configuration management using pydantic-settings
for environment-based configuration with validation and type safety.
"""

import logging
import logging.handlers
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LogLevel(StrEnum):
    """Supported logging levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class APISettings(BaseModel):
    """API service configuration settings."""

    claude_api_key: str | None = Field(default=None, description="Claude API key for AI services")
    openai_api_key: str | None = Field(default=None, description="OpenAI API key")
    perplexity_api_key: str | None = Field(
        default=None, description="Perplexity API key for search"
    )
    notion_api_key: str | None = Field(
        default=None, description="Notion API key for workspace integration"
    )

    @field_validator("claude_api_key", "openai_api_key", "perplexity_api_key", "notion_api_key")
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
    session_name: str = Field(default="valor_bridge", description="Telegram session name")

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
    workers: int = Field(default=1, description="Number of worker processes", ge=1, le=16)


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
        default=Path("logs/valor_bridge.log"), description="Log file path"
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

    data_dir: Path = Field(default=Path("data"), description="Data directory path")
    temp_dir: Path = Field(default=Path("temp"), description="Temporary files directory")
    max_file_size: int = Field(
        default=100 * 1024 * 1024,  # 100MB
        description="Maximum file size for uploads",
        ge=1024 * 1024,  # 1MB minimum
    )


class PerformanceSettings(BaseModel):
    """Performance and resource management settings."""

    max_workers: int = Field(default=4, description="Maximum number of worker threads", ge=1, le=32)
    timeout: int = Field(default=30, description="Default request timeout in seconds", ge=5, le=300)
    cache_ttl: int = Field(
        default=3600,
        description="Cache time-to-live in seconds",
        ge=60,
        le=86400,  # 24 hours
    )
    memory_limit: int = Field(default=1024, description="Memory limit in MB", ge=256, le=8192)


class RedisSettings(BaseModel):
    """Redis connection settings."""

    url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL (env: REDIS_URL)",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v):
        """Fall back to default if empty string provided."""
        if not v or not v.strip():
            return "redis://localhost:6379/0"
        return v.strip()


class GoogleAuthSettings(BaseModel):
    """Google OAuth credential settings."""

    credentials_dir: Path = Field(
        default_factory=lambda: Path.home() / "Desktop" / "Valor",
        description="Directory for Google auth credentials (env: GOOGLE_CREDENTIALS_DIR)",
    )

    @field_validator("credentials_dir")
    @classmethod
    def ensure_dir_exists(cls, v):
        """Validate credentials directory path."""
        return v


class ModelSettings(BaseModel):
    """Local model configuration settings."""

    ollama_generation_model: str = Field(
        default="gemma4:31b-cloud",
        description=(
            "Per-machine free-text generation model for memory titles and the "
            "test AI judge (env: MODELS__OLLAMA_GENERATION_MODEL). Default is the "
            "Ollama Cloud variant 'gemma4:31b-cloud' (a lightweight hosted pointer "
            "that fits any machine). RAM-rich Apple-Silicon hosts may set "
            "'gemma4:31b-mlx' to run generation locally. /setup selects the "
            "variant from available RAM (written to ~/.zshenv, machine-local); "
            "/update verifies it via ensure_generation_model(). Classification "
            "uses OLLAMA_CLASSIFIER_MODEL (granite), not this setting."
        ),
    )
    ollama_host: str = Field(
        default="http://localhost:11434",
        description=(
            "Base URL of the local Ollama HTTP API. Serves the granite classifier "
            "and the memory title generator's async title creation on memory save "
            "(env: MODELS__OLLAMA_HOST). Default points at the standard Ollama port."
        ),
    )
    memory_title_timeout_s: float = Field(
        default=5.0,
        description=(
            "HTTP timeout (seconds) for the memory title generator's Ollama "
            "call. Title generation is fire-and-forget — exceeding the timeout "
            "logs at DEBUG and leaves title unchanged. Stubs fall back to "
            "category-only rendering. Env: MODELS__MEMORY_TITLE_TIMEOUT_S."
        ),
    )
    session_default_model: str = Field(
        default="opus",
        description=(
            "Fallback Claude model for sessions where AgentSession.model is None/empty. "
            "Part of the precedence cascade: session.model > settings > codebase default 'opus'. "
            "Short aliases (opus, sonnet, haiku) preferred; "
            "full names (claude-opus-4-7) also accepted. "
            "Env: MODELS__SESSION_DEFAULT_MODEL."
        ),
    )


class FeatureSettings(BaseModel):
    """Feature-flag configuration for optional behaviours.

    All flags are startup-config (read once at process start); default values
    should represent the desired end state, not legacy behavior.
    """

    anthropic_concurrency: int = Field(
        default=5,
        ge=1,
        le=50,
        description=(
            "Maximum concurrent AsyncAnthropic API calls across all call sites "
            "in bridge/, tools/, and agent/. Enforced by a shared asyncio.Semaphore "
            "in agent/anthropic_client.py. Conservative default of 5 covers all "
            "migrated sites fanning out at once without breaching Anthropic's "
            "per-minute request limits on a solo-dev account. Override via "
            "FEATURES__ANTHROPIC_CONCURRENCY env var (pydantic-settings nested "
            "delimiter). See issue #1111."
        ),
    )

    # --- Crash auto-resume policy (issue #1539) ---
    # Enable ONLY on the one designated auto-resume machine; off everywhere else
    # (propose-only mode). Env: FEATURES__CRASH_AUTORESUME_ENABLED.
    crash_autoresume_enabled: bool = Field(
        default=False,
        description=(
            "Enable automatic session resume by the crash-recovery reflection. "
            "Off by default — enable on exactly ONE designated machine. "
            "All other machines run in propose-only mode (log, no action). "
            "Env: FEATURES__CRASH_AUTORESUME_ENABLED. See issue #1539."
        ),
    )
    crash_autoresume_max_attempts: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "Per-session cap on automatic resume attempts. Once a session has been "
            "auto-resumed this many times without recovering, it is left terminal "
            "for human review. Env: FEATURES__CRASH_AUTORESUME_MAX_ATTEMPTS."
        ),
    )
    crash_autoresume_run_budget: int = Field(
        default=5,
        ge=1,
        le=50,
        description=(
            "Maximum number of sessions that can be auto-resumed in a single "
            "reflection run. Guards against a misfiring policy causing a flood "
            "of resumes. Env: FEATURES__CRASH_AUTORESUME_RUN_BUDGET."
        ),
    )
    crash_autoresume_min_occurrences: int = Field(
        default=3,
        ge=1,
        le=100,
        description=(
            "Minimum number of times a crash signature must be observed before "
            "auto-resume is considered eligible for that pattern. Ensures the "
            "policy has enough data to be statistically meaningful. "
            "Env: FEATURES__CRASH_AUTORESUME_MIN_OCCURRENCES."
        ),
    )
    crash_autoresume_min_success_ratio: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum recovery success ratio (recovered / attempts) required "
            "before auto-resume is eligible for a crash pattern. "
            "Env: FEATURES__CRASH_AUTORESUME_MIN_SUCCESS_RATIO."
        ),
    )


class GraniteSettings(BaseModel):
    """Granite PTY container configuration (plan #1572).

    The granite container drives an interactive ``claude`` TUI session via
    two persistent PTYs (PM + Dev) per session. The PTY pool caps
    concurrent interactive pairs at ``pty_pool_size``; over-cap sessions
    wait in the Redis queue. The pool size is intentionally SMALLER than
    ``MAX_CONCURRENT_SESSIONS`` so the Redis queue absorbs over-cap
    sessions, giving operators headroom to handle orphan-after-SIGKILL
    without overcommitting memory.

    Growth path: default 3 → 6 once health/observability and memory
    management land. Each PTY pair is ~400 MB resident, so pool=3 with
    MAX_CONCURRENT_SESSIONS=8 bounds worker memory at ~9.6 GB worst case.
    See ``docs/features/granite-pty-production.md``.
    """

    pty_pool_size: int = Field(
        default=3,
        ge=1,
        le=16,
        description=(
            "Hard maximum concurrent PM+Dev PTY pairs. Each pair is two "
            "interactive ``claude`` processes (~200 MB each) driving the "
            "granite container. The PTY pool is a singleton owned by the "
            "worker process. Override via GRANITE__PTY_POOL_SIZE env var. "
            "Plan #1572 / docs/features/granite-pty-production.md."
        ),
    )
    pm_model: str = Field(
        default="opus",
        description=(
            "Claude model alias for the PM TUI PTY. The PM/Dev sessions run "
            "on the Claude subscription (OAuth, ANTHROPIC_API_KEY blanked), "
            "exactly like the `claude --permission-mode bypassPermissions` "
            "shortcut, with the model chosen at spawn time. Use UNPINNED "
            "aliases (opus, sonnet, haiku) so the substrate tracks the latest "
            "version. ollama models belong to the granite classifier only, "
            "never the PTY substrate. Override via GRANITE__PM_MODEL."
        ),
    )
    dev_model: str = Field(
        default="opus",
        description=(
            "Claude model alias for the Dev TUI PTY. See ``pm_model``. The "
            "Dev role now owns the full SDLC pipeline (issue #1692) and fans "
            "out to Sonnet subagents for parallel work; opus is the default "
            "for the Dev TUI itself. Override via GRANITE__DEV_MODEL."
        ),
    )


class PathSettings(BaseModel):
    """Path settings derived from project root. No hardcoded usernames."""

    project_root: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent,
        description="Project root directory",
    )
    data_dir: Path = Field(default=None, description="Data directory path")
    logs_dir: Path = Field(default=None, description="Logs directory path")
    config_dir: Path = Field(default=None, description="Config directory path")

    def model_post_init(self, __context):
        """Derive paths from project_root after initialization."""
        if self.data_dir is None:
            self.data_dir = self.project_root / "data"
        if self.logs_dir is None:
            self.logs_dir = self.project_root / "logs"
        if self.config_dir is None:
            self.config_dir = self.project_root / "config"


class Settings(BaseSettings):
    """Main application settings with environment variable support."""

    model_config = SettingsConfigDict(
        # Skip reading .env when VALOR_LAUNCHD=1: all vars are already injected
        # into the launchd plist by install_worker.sh. The .env symlinks to
        # ~/Desktop/Valor/.env (iCloud), and pydantic-settings' open() on that
        # file blocks indefinitely under macOS TCC in the launchd environment.
        env_file=None if __import__("os").environ.get("VALOR_LAUNCHD") else ".env",
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

    # SDLC bot identity for /do-pr-review pipeline-driven reviews (issue #1300).
    # PAT for the bot account (e.g. yudame-sdlc-bot) that posts reviews under a
    # non-human identity. When set, sdk_client.py forwards this into the agent env
    # so the skill uses GH_TOKEN=$SDLC_AGENT_GH_TOKEN for the review-post subprocess.
    # See docs/features/do-pr-review-bot-identity.md for provisioning instructions.
    sdlc_agent_gh_token: str | None = Field(
        default=None,
        description="GitHub PAT for SDLC bot account used by /do-pr-review in pipeline context",
    )

    # Cross-vendor review judge (issue #1626).
    # Provisional/tunable — default off; enable on the review machine via vault .env.
    sdlc_review_cross_vendor: bool = Field(
        default=False,
        description=(
            "Enable cross-vendor (non-Claude) reviewer in /do-pr-review. "
            "Default OFF. Enable via SDLC_REVIEW_CROSS_VENDOR=1."
        ),
    )
    # Provisional model id — gpt-4o is the safe default.
    # Override via SDLC_REVIEW_CROSS_VENDOR_MODEL env var.
    sdlc_review_cross_vendor_model: str = Field(
        default="gpt-4o",
        description=(
            "OpenAI model id for the cross-vendor judge. "
            "Provisional — env-overridable via SDLC_REVIEW_CROSS_VENDOR_MODEL."
        ),
    )
    # Provisional token cap — tunable based on cost tolerance.
    sdlc_review_cross_vendor_max_diff_tokens: int = Field(
        default=50000,
        ge=1000,
        description=(
            "Max diff tokens for the cross-vendor judge. "
            "Provisional/tunable. Env: SDLC_REVIEW_CROSS_VENDOR_MAX_DIFF_TOKENS."
        ),
    )
    sdlc_review_cross_vendor_required: bool = Field(
        default=False,
        description=(
            "Fail-closed: if True and cross-vendor judge skips, consensus returns "
            "CHANGES REQUESTED. Default OFF (degrade-to-Claude-only). "
            "Env: SDLC_REVIEW_CROSS_VENDOR_REQUIRED=1."
        ),
    )

    # Component settings
    api: APISettings = Field(default_factory=APISettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    workspace: WorkspaceSettings = Field(default_factory=WorkspaceSettings)
    performance: PerformanceSettings = Field(default_factory=PerformanceSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    google_auth: GoogleAuthSettings = Field(default_factory=GoogleAuthSettings)
    models: ModelSettings = Field(default_factory=ModelSettings)
    paths: PathSettings = Field(default_factory=PathSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    granite: GraniteSettings = Field(default_factory=GraniteSettings)

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
                    logging.handlers.RotatingFileHandler(
                        self.logging.file_path,
                        maxBytes=self.logging.max_file_size,
                        backupCount=self.logging.backup_count,
                    )
                    if self.logging.file_path
                    else logging.NullHandler()
                ),
            ],
        )

    def create_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        directories = [
            self.workspace.data_dir,
            self.workspace.temp_dir,
            self.google_auth.credentials_dir,
        ]

        if self.logging.file_path:
            directories.append(self.logging.file_path.parent)

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

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
