"""
Configuration Management System for AI Rebuild

This module provides comprehensive configuration management using pydantic-settings
for environment-based configuration with validation and type safety.

VaultSettings (and the module-level `vault` singleton) own resolution of the
master vault directory via a cascade — VALOR_VAULT_DIR > ~/.valor/.env >
~/Desktop/Valor/.env > raise — and expose vault-relative paths (env_path,
projects_path, personas_dir, identity_path, google_credentials_dir,
reflections_yaml).
Per-path env var overrides (GOOGLE_CREDENTIALS_DIR, PROJECTS_CONFIG_PATH,
REFLECTIONS_YAML) win over the master vault dir at property-access time.
VaultSettings does NOT do I/O or path creation — those belong to install
scripts.
"""

import logging
import logging.handlers
import os
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from config.models import PINNED_CLAUDE_VERSION

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vault directory resolution
# ---------------------------------------------------------------------------

# Roots that are not safe as a permanent vault location. Tests that need to
# place a fake vault under tmp_path monkeypatch this tuple to ().
_EPHEMERAL_PATH_PREFIXES: tuple[Path, ...] = (
    Path("/tmp"),
    Path("/private/tmp"),
    Path("/var/folders"),
    Path("/private/var/folders"),
)


class VaultNotResolved(RuntimeError):  # noqa: N818  (plan-mandated name)
    """No vault directory could be resolved from any cascade tier."""


class VaultPathInvalid(ValueError):  # noqa: N818  (plan-mandated name)
    """A candidate vault path is unsafe (inside repo, ephemeral root, etc.)."""


class VaultSettings(BaseModel):
    """Resolves the master vault directory and exposes vault-relative paths.

    Cascade order (first match wins):
      1. ``VALOR_VAULT_DIR`` env var
      2. ``~/.valor/.env`` exists (preferred default — non-TCC path)
      3. ``~/Desktop/Valor/.env`` exists (legacy default — iCloud + TCC)
      4. raise :class:`VaultNotResolved`

    Per-path env vars (``GOOGLE_CREDENTIALS_DIR``, ``PROJECTS_CONFIG_PATH``,
    ``REFLECTIONS_YAML``) are checked at property-access time and win over the
    master vault dir.

    The default ``VaultSettings()`` constructor runs the cascade. Pass
    ``dir=...`` and ``source=...`` explicitly to skip resolution (used by any
    caller that already knows the answer, e.g. a ``--vault-dir`` CLI flag).
    Validation (in-repo / ephemeral-root rejection) always runs, regardless of
    construction path.
    """

    dir: Path
    source: str  # "env" | "default_valor_home" | "default_desktop" | "explicit"

    def __init__(self, **data: Any) -> None:
        # Validation runs outside super().__init__ so VaultPathInvalid /
        # VaultNotResolved propagate bare rather than getting wrapped in a
        # pydantic ValidationError.
        if "dir" not in data:
            resolved_dir, source = self._cascade()
            data["dir"] = resolved_dir
            data.setdefault("source", source)
        self._validate_dir(Path(data["dir"]))
        _logger.info(
            "Vault directory resolved to: %s (source: %s)",
            data["dir"],
            data.get("source"),
        )
        super().__init__(**data)

    @classmethod
    def _cascade(cls) -> tuple[Path, str]:
        env_val = os.environ.get("VALOR_VAULT_DIR")
        if env_val:
            return Path(env_val).expanduser(), "env"

        valor_home = Path.home() / ".valor"
        if (valor_home / ".env").exists():
            return valor_home, "default_valor_home"

        desktop_valor = Path.home() / "Desktop" / "Valor"
        if (desktop_valor / ".env").exists():
            return desktop_valor, "default_desktop"

        raise VaultNotResolved(
            "No vault directory could be resolved. Tried (in order): "
            "VALOR_VAULT_DIR env var (unset), "
            f"~/.valor/.env (not found at {valor_home / '.env'}), "
            f"~/Desktop/Valor/.env (not found at {desktop_valor / '.env'}). "
            "Set VALOR_VAULT_DIR or run /setup."
        )

    @staticmethod
    def _validate_dir(path: Path) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        try:
            path.resolve().relative_to(repo_root)
        except ValueError:
            pass  # not inside repo — good
        else:
            raise VaultPathInvalid(f"vault path {path} is inside repo root {repo_root}")

        resolved = path.resolve()
        for prefix in _EPHEMERAL_PATH_PREFIXES:
            try:
                resolved.relative_to(prefix)
            except ValueError:
                continue
            raise VaultPathInvalid(f"vault path {path} is under ephemeral root {prefix}")

    # --- TCC restriction check -------------------------------------------

    @staticmethod
    def path_is_tcc_restricted(path: Path) -> bool:
        """True if ``path`` lives under a macOS TCC / FileProvider-gated dir.

        Restricted roots:
          * ``~/Desktop``, ``~/Documents`` — classic TCC categories.
          * ``~/iCloud Drive`` — Finder alias for iCloud Drive (may not exist
            on disk on every machine).
          * ``~/Library/Mobile Documents`` — canonical iCloud Drive mount
            (``~/iCloud Drive`` is a symlink/alias to a subdir under here).
          * ``~/Library/CloudStorage`` — macOS Sonoma+ FileProvider mount
            point for iCloud, Dropbox, OneDrive, Google Drive, etc. All
            FileProvider-gated; launchd-spawned processes hang on reads here
            for the same reason classic TCC paths hang.

        ``path`` and each candidate root are resolved (symlinks followed)
        before prefix comparison so that a vault symlinked from a benign
        name into a restricted target is still caught. ``strict=False``
        means non-existent paths still get a best-effort absolute form.

        When the vault is on a restricted path, launchd-managed services
        cannot read ``<vault>/.env`` at runtime and must instead have its
        contents baked into the plist's ``EnvironmentVariables`` dict at
        install time (when the calling terminal still has TCC consent).
        See ``scripts/install/inject_plist_env.py``.
        """
        home = Path.home()
        restricted_roots = (
            home / "Desktop",
            home / "Documents",
            home / "iCloud Drive",
            home / "Library" / "Mobile Documents",
            home / "Library" / "CloudStorage",
        )

        def _safe_resolve(p: Path) -> Path:
            try:
                return p.resolve(strict=False)
            except (OSError, RuntimeError):
                return p.absolute()

        resolved = _safe_resolve(path)
        for root in restricted_roots:
            resolved_root = _safe_resolve(root)
            if resolved == resolved_root or resolved_root in resolved.parents:
                return True
        return False

    @property
    def is_tcc_restricted(self) -> bool:
        """True if this vault's ``dir`` is on a TCC-protected path."""
        return self.path_is_tcc_restricted(self.dir)

    # --- vault-relative properties ----------------------------------------

    @property
    def env_path(self) -> Path:
        return self.dir / ".env"

    @property
    def projects_path(self) -> Path:
        override = os.environ.get("PROJECTS_CONFIG_PATH")
        if override:
            return Path(override).expanduser()
        return self.dir / "projects.json"

    @property
    def personas_dir(self) -> Path:
        return self.dir / "personas"

    @property
    def identity_path(self) -> Path:
        return self.dir / "identity.json"

    @property
    def google_credentials_dir(self) -> Path:
        override = os.environ.get("GOOGLE_CREDENTIALS_DIR")
        if override:
            return Path(override).expanduser()
        return self.dir

    @property
    def reflections_yaml(self) -> Path:
        override = os.environ.get("REFLECTIONS_YAML")
        if override:
            return Path(override).expanduser()
        return self.dir / "reflections.yaml"


# Module-level lazy singleton. Resolved on first attribute access via the
# module __getattr__ below so that `from config.settings import vault` does
# not fail at import time on machines without a configured vault.
_vault_singleton: VaultSettings | None = None


def _get_vault() -> VaultSettings:
    global _vault_singleton
    if _vault_singleton is None:
        _vault_singleton = VaultSettings()
    return _vault_singleton


def __getattr__(name: str) -> Any:
    if name == "vault":
        return _get_vault()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def load_vault_env() -> Path | None:
    """Load ``<vault>/.env`` into ``os.environ`` as a defensive belt-and-suspenders
    load alongside the repo's symlinked ``.env``.

    The repo ``.env`` is a symlink to ``<vault>/.env``, so a plain
    ``load_dotenv()`` from the repo cwd is typically sufficient. This helper
    exists for processes invoked from arbitrary cwd (CLI tools), launchd
    contexts where the cwd-relative ``.env`` isn't auto-detected, and fresh
    checkouts where the symlink hasn't been created yet.

    Returns the path actually loaded, or ``None`` if no reachable .env was
    found.

    When ``VALOR_VAULT_DIR`` is **explicitly set**, this function ONLY tries
    that path — it does NOT fall through to ``~/.valor`` / ``~/Desktop/Valor``
    probes. Falling back would silently inherit secrets from a different vault
    if the explicit path is a typo, an unmounted external disk, or otherwise
    missing. Fail-loud (return None) is safer than silent-wrong (load somebody
    else's .env).

    When ``VALOR_VAULT_DIR`` is **unset**, we probe the two default tiers
    in cascade order so a fresh checkout still picks up secrets before
    ``/setup`` runs.
    """
    from dotenv import load_dotenv

    if os.environ.get("VALOR_VAULT_DIR"):
        # Explicit vault: honor only that path, no fallback.
        try:
            env_path = _get_vault().env_path
        except (VaultNotResolved, VaultPathInvalid):
            return None
        if env_path.exists():
            load_dotenv(env_path)
            return env_path
        return None

    # No explicit env var: probe defaults in cascade order.
    for candidate in (
        Path.home() / ".valor" / ".env",
        Path.home() / "Desktop" / "Valor" / ".env",
    ):
        if candidate.exists():
            load_dotenv(candidate)
            return candidate
    return None


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


def _default_google_credentials_dir() -> Path:
    """Resolve the default credentials directory via the vault cascade.

    Honors ``GOOGLE_CREDENTIALS_DIR`` (via ``vault.google_credentials_dir``)
    and ``VALOR_VAULT_DIR``. Falls back to the same defaults the vault
    cascade probes (~/.valor preferred, ~/Desktop/Valor legacy) when the
    vault is unresolved.
    """
    try:
        return _get_vault().google_credentials_dir
    except VaultNotResolved:
        valor_home = Path.home() / ".valor"
        if valor_home.exists():
            return valor_home
        return Path.home() / "Desktop" / "Valor"


class GoogleAuthSettings(BaseModel):
    """Google OAuth credential settings."""

    credentials_dir: Path = Field(
        default_factory=_default_google_credentials_dir,
        description=(
            "Directory for Google auth credentials. Default: vault.google_credentials_dir "
            "(configurable via VALOR_VAULT_DIR; established default ~/Desktop/Valor). "
            "Override this field directly or via the GOOGLE_CREDENTIALS_DIR env var."
        ),
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

    # --- Stall-recovery action-mode (issue #1768) ---
    # Promotes the stall-advisory reflection from observe-only to an actor that
    # kills demonstrably-wedged sessions and re-enqueues via valor-catchup.
    # Off by default (dry-run); enabling is a reversible per-machine .env edit.
    stall_recovery_enabled: bool = Field(
        default=False,
        description=(
            "Enable the stall-advisory action-mode to kill wedged sessions and "
            "re-enqueue their unanswered work via valor-catchup. Off by default "
            "(dry-run/observe-only); enabling is a documented reversible "
            "per-machine .env edit. "
            "Env: FEATURES__STALL_RECOVERY_ENABLED. See issue #1768."
        ),
    )
    stall_recovery_consecutive_observations: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "N consecutive stalled observations of the same session required "
            "before a kill (at the 300s reflection cadence, 3 is roughly 15 "
            "minutes). Provisional/tunable. "
            "Env: FEATURES__STALL_RECOVERY_CONSECUTIVE_OBSERVATIONS."
        ),
    )
    stall_recovery_run_budget: int = Field(
        default=1,
        ge=1,
        le=20,
        description=(
            "K maximum sessions killed per reflection run (mirrors the "
            "session-recovery-drip 1-per-tick shape). Provisional/tunable. "
            "Env: FEATURES__STALL_RECOVERY_RUN_BUDGET."
        ),
    )
    stall_recovery_per_session_budget: int = Field(
        default=2,
        ge=1,
        le=10,
        description=(
            "Per-session cap on kill attempts to prevent thrash on a session "
            "that keeps re-wedging. Provisional/tunable. "
            "Env: FEATURES__STALL_RECOVERY_PER_SESSION_BUDGET."
        ),
    )
    reflection_pool_workers: int = Field(
        default=2,
        ge=1,
        le=16,
        description=(
            "Thread-pool size for the reflection bulkhead executor. Sync reflections "
            "run in this dedicated pool instead of the asyncio default pool, preventing "
            "heavy scans from starving critical-path work (e.g. bridge routing). "
            "Provisional/tunable. Env: FEATURES__REFLECTION_POOL_WORKERS."
        ),
    )

    # --- Per-message producer claim (issue #1817 B1) ---
    # GRAIN OF SALT: this TTL must stay SHORT -- sized to cross-actor
    # processing skew (seconds), NOT the ~1h iCloud projects.json sync-lag
    # window. The durable 2h DedupRecord membership set (models/dedup.py)
    # already covers the sync-lag/replay window; this gate only needs to
    # survive the brief overlap between two producers racing on the SAME
    # message. A long TTL here was a BLOCKER in an earlier critique round:
    # it would orphan the claim key for up to an hour on a mid-window
    # process death, causing the reconciler's retry to wrongly conclude a
    # peer won and silently drop the message for that entire window --
    # recreating the exact bug this claim exists to fix.
    bridge_msg_claim_ttl_seconds: int = Field(
        default=60,
        ge=5,
        le=300,
        description=(
            "Provisional short TTL (seconds) for the bridge:msgclaim:* SETNX "
            "gate in bridge/dedup.py that prevents two near-simultaneous "
            "producers (e.g. two machines during iCloud config sync lag) from "
            "both enqueueing the same inbound Telegram message. Must stay "
            "short (cross-actor skew, not sync-lag window) -- see the "
            "comment above this field. Env: FEATURES__BRIDGE_MSG_CLAIM_TTL_SECONDS."
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
    reprobe_interval_s: float = Field(
        default=30.0,
        gt=0,
        description=(
            "How often (seconds) to re-probe granite when the circuit is CLOSED. "
            "Provisional/tunable — tune after observing real ollama outage rates. "
            "Override via GRANITE_REPROBE_INTERVAL_S env var."
        ),
    )
    breaker_open_threshold: int = Field(
        default=3,
        ge=1,
        le=100,
        description=(
            "Consecutive probe failures required to trip the circuit to OPEN. "
            "Provisional/tunable. Override via GRANITE_BREAKER_OPEN_THRESHOLD env var."
        ),
    )
    breaker_cooldown_s: float = Field(
        default=120.0,
        gt=0,
        description=(
            "Seconds the circuit stays OPEN before allowing a half-open re-probe. "
            "Provisional/tunable. Override via GRANITE_BREAKER_COOLDOWN_S env var."
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

    # --- Per-role transport hedge (plan #1842) ---
    pm_transport: str = Field(
        default="pty",
        description=(
            "Global default transport for the PM role: ``pty`` (interactive TUI "
            "over a PTY, flat-billed on the subscription) or ``headless`` "
            "(one ``claude -p`` subprocess per turn, metered against the Agent "
            "SDK credit pool). A per-project ``transport.pm`` block in "
            "projects.json overrides this. Default ``pty`` reproduces today's "
            "behavior exactly. Override via GRANITE__PM_TRANSPORT."
        ),
    )
    dev_transport: str = Field(
        default="pty",
        description=(
            "Global default transport for the Dev role: ``pty`` or ``headless``. "
            "See ``pm_transport``. A per-project ``transport.dev`` block in "
            "projects.json overrides this. Override via GRANITE__DEV_TRANSPORT."
        ),
    )

    # --- Hook-driven turn returns (plan #1688) ---
    hook_driven_turn_end: bool = Field(
        default=True,
        description=(
            "Feature flag: when True (default), the granite container treats the "
            "Claude Code ``Stop`` hook edge as the turn-completion authority and "
            "reads the final assistant message from the hook payload's "
            "transcript_path. The PTY idle heuristic (read_until_idle) is demoted "
            "to a running/idle badge, liveness, and crash detection. When False, "
            "the container falls back to the pre-#1688 idle-completion path (the "
            "documented safety valve for a claude version that regresses the hook "
            "contract). Override via GRANITE__HOOK_DRIVEN_TURN_END env var. "
            "Plan #1688 / docs/features/granite-hook-driven-turn-returns.md."
        ),
    )
    hook_turn_end_wait_s: float = Field(
        default=600.0,
        gt=0,
        description=(
            "Outer budget (seconds) the container waits for a ``Stop`` turn-end "
            "edge before the crash/timeout watchdog trips. The wait is always a "
            "race against PTY EOF / !isalive() — this bound only fires when the "
            "PTY is alive but no Stop edge arrives (the silent-hook failure mode). "
            "Override via GRANITE__HOOK_TURN_END_WAIT_S env var."
        ),
    )
    hook_crash_resume_cap: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "Max crash-resume attempts on a single turn before the container "
            "escalates with an operator-terminal message instead of looping "
            "forever. Each crash (PTY EOF with no Stop edge) resumes the same "
            "claude session via --resume <uuid> + a verified `continue` nudge. "
            "Override via GRANITE__HOOK_CRASH_RESUME_CAP env var."
        ),
    )

    # --- Background-task supervisor (Fix #4, issue #1816) ---
    supervisor_max_restarts: int = Field(
        default=5,
        ge=1,
        description=(
            "Max restarts within WORKER_SUPERVISOR_WINDOW_S before the storm cap fires "
            "and recycles the process via SIGABRT. Conservative default — erring toward "
            "NOT killing legitimate work. Provisional/tunable. "
            "Override via WORKER_SUPERVISOR_MAX_RESTARTS env var."
        ),
    )
    supervisor_window_s: float = Field(
        default=300.0,
        gt=0,
        description=(
            "Rolling window (seconds) for the restart-count denominator. "
            "Provisional/tunable. Override via WORKER_SUPERVISOR_WINDOW_S env var."
        ),
    )
    supervisor_base_backoff_s: float = Field(
        default=1.0,
        ge=0,
        description=(
            "Base backoff (seconds) before the first respawn; doubles each restart. "
            "Provisional/tunable. Override via WORKER_SUPERVISOR_BASE_BACKOFF_S env var."
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


def _settings_should_skip_env_file() -> bool:
    """True when pydantic-settings should NOT read ``.env`` at process start.

    The skip is needed only for the narrow case where (a) we are running
    under launchd and (b) the configured vault path is TCC-protected — i.e.
    ``open(<vault>/.env)`` would hang. In every other configuration the
    file is readable and pydantic-settings should load it normally.

    Best-effort: if vault resolution fails (fresh checkout, missing config),
    we conservatively skip when under launchd to preserve the historical
    behavior that kept Tom's machine running. Non-launchd processes always
    read ``.env`` regardless of vault state.
    """
    if not os.environ.get("VALOR_LAUNCHD"):
        return False
    try:
        return _get_vault().is_tcc_restricted
    except (VaultNotResolved, VaultPathInvalid):
        # Vault unresolvable under launchd: assume worst case (TCC-restricted)
        # to avoid re-introducing the indefinite-hang bug.
        return True


class Settings(BaseSettings):
    """Main application settings with environment variable support."""

    model_config = SettingsConfigDict(
        # Skip reading .env only when BOTH conditions hold:
        # (a) we're under launchd (VALOR_LAUNCHD=1), and
        # (b) the resolved vault is on a TCC-protected path (~/Desktop,
        #     ~/Documents, ~/iCloud Drive) — pydantic-settings' open() on
        #     such a .env hangs indefinitely under macOS TCC.
        # When the vault is on a non-TCC path (~/.valor, custom paths),
        # reading .env at runtime is safe and avoids baking secrets into
        # the launchd plist.
        env_file=None if _settings_should_skip_env_file() else ".env",
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

    # Email resolver persistent-unavailability alert (issue #1817, workstream A2).
    # Provisional/tunable — chosen to absorb a one-off transient resolver blip
    # without paging, while still catching a genuinely stuck resolver (e.g. an
    # expired OAuth token) within a handful of inbound emails. Derived from the
    # existing per-project resolver:failures:{project_key} counter maintained by
    # bridge/routing.py::_on_resolver_failure — not a parallel tally.
    # Override via EMAIL_RESOLVER_ALERT_AFTER env var.
    email_resolver_alert_after: int = Field(
        default=3,
        ge=1,
        le=50,
        description=(
            "Consecutive resolver failures (per project) before the "
            "email:resolver_unavailable operator alert arms. "
            "Provisional/tunable. Env: EMAIL_RESOLVER_ALERT_AFTER."
        ),
    )

    # Correctness & delivery-integrity hardening (issue #1817). Documented
    # here as the typed catalog entry; the runtime checks
    # (scripts/update/verify.py, worker/__main__.py,
    # agent/agent_session_queue.py) read these via `os.environ.get(...)`
    # directly rather than through this `settings` singleton, so a value
    # changed after process startup (e.g. via test monkeypatching) takes
    # effect immediately instead of requiring a fresh Settings() instance.
    # The version default itself lives in config/models.py
    # (PINNED_CLAUDE_VERSION) as the single source of truth shared with the
    # nightly ollama canary (scripts/nightly_regression_tests.py).
    pinned_claude_version: str = Field(
        default=PINNED_CLAUDE_VERSION,
        description=(
            "D1a: pinned claude CLI version the D1b scraped-TUI-marker "
            "contract was last verified against (native installer: "
            "~/.local/bin/claude -> "
            "~/.local/share/claude/versions/<version>/). PROVISIONAL — "
            "bumping requires re-verifying the D1b markers against the new "
            "version's actual TUI output first; see "
            "docs/features/deployment.md. Env: PINNED_CLAUDE_VERSION."
        ),
    )
    claude_contract_check_enforce: bool = Field(
        default=False,
        description=(
            "D1a/D1b: shared enforce flag for the claude-CLI-contract checks "
            "(version pin drift in scripts/update/verify.py, TUI-marker "
            "contract-check in worker/__main__.py). Default off (warn-only, "
            "non-blocking). Env: CLAUDE_CONTRACT_CHECK_ENFORCE=1."
        ),
    )
    notify_healthcheck_interval: float = Field(
        default=15.0,
        gt=0,
        description=(
            "D4: interval (seconds) for the session-notify pubsub liveness "
            "watchdog in agent/agent_session_queue.py — a periodic PUBSUB "
            "NUMSUB probe on a SEPARATE short-lived Redis connection (never "
            "the listen() connection) that detects a silently-dropped "
            "subscription and forces a resubscribe. Provisional/tunable. "
            "Env: NOTIFY_HEALTHCHECK_INTERVAL."
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
