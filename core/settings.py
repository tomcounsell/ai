"""Typed settings: every path, tunable, seat, and price the kernel reads.

Each value has a default for this Mac and, where it varies by machine, a
`VALOR_*` environment override, read when `Settings()` is built. Secrets are
never values here: `pg_passfile` is the path of the kernel's libpq password
file, and libpq reads the file itself, so no password passes through this
process as a string.

`python -m core.settings` (or `python -m core settings`) prints every value
as a shell assignment (`SETTING_PGHOST=/tmp`), which is how `scripts/demo_workspace.sh` reads them.
"""

import getpass
import os
import shlex
import shutil
from dataclasses import dataclass, field, fields
from datetime import date
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _span(name: str, default: str) -> tuple[int, int]:
    """A port span from `LOW-HIGH`, so a machine running several kernels
    or test runs side by side gives each its own ports."""
    low, high = _env(name, default).split("-")
    return int(low), int(high)


def _pg_bin() -> str:
    found = shutil.which("pg_dump")
    return str(Path(found).parent) if found else "/opt/homebrew/opt/postgresql@18/bin"


def _git() -> str:
    """The trusted git (`core/binaries.py`), or empty when there is none, in
    which case every kernel git call is refused."""
    from core import binaries

    return binaries.git() or ""


def _claude() -> str:
    return shutil.which("claude") or str(Path.home() / ".local/bin/claude")


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens, as the provider's pricing page lists
    them, and the day they were checked against it."""

    input: float
    output: float
    cache_write: float  # the five-minute write
    cache_write_1h: float
    cache_read: float
    checked: date
    # US dollars per thousand server-side web searches
    # (`usage.server_tool_use.web_search_requests`).
    web_search_per_1k: float = 10.00


# Checked against https://platform.claude.com/docs/en/about-claude/pricing.
# A model absent here is refused by the gateway: an unpriced call cannot be
# metered. A dated id (`claude-haiku-4-5-20251001`) takes its undated entry.
PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(4.00, 20.00, 5.00, 8.00, 0.20, date(2026, 10, 1)),
    "claude-opus-5": Price(5.00, 25.00, 6.25, 10.00, 0.50, date(2026, 10, 1)),
    "claude-opus-4-8": Price(5.00, 25.00, 6.25, 10.00, 0.50, date(2026, 10, 1)),
    "claude-sonnet-5-5": Price(2.00, 10.00, 2.50, 4.00, 0.20, date(2026, 10, 1)),
    "claude-sonnet-5": Price(2.00, 10.00, 2.50, 4.00, 0.20, date(2026, 10, 1)),
    "claude-haiku-4-5": Price(1.00, 5.00, 1.25, 2.00, 0.10, date(2026, 10, 1)),
}


@dataclass(frozen=True)
class OpenAIRates:
    """US dollars per million tokens at one service tier: input, cached
    input (a cache read), a cache write, and output."""

    input: float
    cached: float
    cache_write: float
    output: float


@dataclass(frozen=True)
class OpenAIPrice:
    """One OpenAI model's prices: per service tier, the rates up to the
    long-context threshold and the rates for a whole request whose input is
    above it; the model's context window and maximum output; the day they
    were checked."""

    tiers: dict[str, tuple[OpenAIRates, OpenAIRates]]
    long_context_above: int
    context_window: int
    max_output: int
    checked: date


# Checked against https://developers.openai.com/api/docs/pricing and
# https://developers.openai.com/api/docs/models/gpt-6.1-sol. Kept apart from
# `PRICES`, so the Anthropic route never prices an OpenAI model. A model
# absent here has no price, and the gateway answers its request with a 400;
# a tier absent from its entry is forwarded and charged at the highest tier.
OPENAI_PRICES: dict[str, OpenAIPrice] = {
    "gpt-6.1-sol": OpenAIPrice(
        tiers={
            "default": (OpenAIRates(2.00, 0.10, 2.50, 10.00), OpenAIRates(4.00, 0.20, 5.00, 15.00)),
            "flex": (OpenAIRates(1.00, 0.05, 1.25, 5.00), OpenAIRates(2.00, 0.10, 2.50, 7.50)),
            "fast": (OpenAIRates(4.00, 0.20, 5.00, 20.00), OpenAIRates(8.00, 0.40, 10.00, 30.00)),
        },
        long_context_above=272_000,
        context_window=1_050_000,
        max_output=128_000,
        checked=date(2026, 10, 3),
    ),
}
# Tier names the API accepts for a priced tier.
OPENAI_TIER_ALIASES: dict[str, str] = {"priority": "fast"}


@dataclass(frozen=True)
class OpenAIToolFee:
    """A hosted tool OpenAI bills per call: US dollars per thousand calls,
    the output item type each call shows as, and the day it was checked."""

    usd_per_1k: float
    item: str
    checked: date


# Same page. A tool type matches its line exactly or by a dated or preview
# suffix (`web_search_preview`). A tool billed by something the response
# does not count (a container session, an image model's own rates) has no
# line, so a request using it has no price.
OPENAI_TOOL_FEES: dict[str, OpenAIToolFee] = {
    "web_search": OpenAIToolFee(10.00, "web_search_call", date(2026, 10, 3)),
    "file_search": OpenAIToolFee(2.50, "file_search_call", date(2026, 10, 3)),
}
# Tools billed only as the model's tokens. `shell` is here only with a
# local environment; a hosted one runs in a billed container.
OPENAI_TOKEN_TOOLS = frozenset({"function", "custom", "mcp", "computer_use_preview", "local_shell"})
# Hosted tools that can sample the model several times in one call.
OPENAI_LOOPING_TOOLS = frozenset({"web_search", "file_search", "mcp"})


# The seats a model fills, by pinned id, never a floating alias: a ledger row
# has to describe a fixed thing. Editing this is a change inside the trust
# boundary, reviewed like kernel code.
SEATS: dict[str, str] = {
    "frontier": "claude-opus-5-5",
    "reviewer": "claude-opus-5-5",
    "light": "claude-haiku-4-5",
}


@dataclass(frozen=True)
class JudgementPrice:
    """US dollars per million tokens for a judgement leg's model, the day
    they were checked, and where."""

    input: float
    output: float
    checked: date
    source: str


# The judgement legs' pinned models (docs/judgement-layer.md). The fallback's
# id names the weights, the host OpenRouter must use, and its quantization,
# so a ledger row describes one fixed thing. Changing either is a new
# calibration record before it routes work.
JEV_MODEL = "jev-1.13.0"
OPEN_WEIGHT_MODEL = "qwen/qwen3-235b-a22b-2507"
OPEN_WEIGHT_PROVIDER = "parasail/fp8"  # OpenRouter's endpoint tag
OPEN_WEIGHT_PROVIDER_NAME = "Parasail"  # what OpenRouter names on the response
OPEN_WEIGHT_PIN = f"{OPEN_WEIGHT_MODEL}@{OPEN_WEIGHT_PROVIDER}"

# Kept apart from `PRICES`, so the gateway never prices a model it would
# forward to Anthropic.
JUDGEMENT_PRICES: dict[str, JudgementPrice] = {
    JEV_MODEL: JudgementPrice(0.042, 0.0, date(2026, 10, 2), "https://docs.typesafe.ai/models"),
    OPEN_WEIGHT_PIN: JudgementPrice(
        0.14,
        0.80,
        date(2026, 10, 2),
        "https://openrouter.ai/api/v1/models/qwen/qwen3-235b-a22b-2507/endpoints (Parasail, fp8)",
    ),
}

# The endpoints each leg defaults to. Only these receive a real key; any
# other endpoint must be on loopback and gets a fixed placeholder key
# (`core.judgement.endpoint_key`).
JEV_URL = "https://api.typesafe.ai/v1/systemone"
OPEN_WEIGHT_URL = "https://openrouter.ai/api/v1/chat/completions"
# The variables the kernel's judgement key file holds.
JEV_KEY = "TYPESAFE_API_KEY"
OPEN_WEIGHT_KEY = "OPENROUTER_API_KEY"


def resolve_model(name: str) -> str:
    """A seat name's pinned id, or the name itself."""
    return SEATS.get(name, name)


@dataclass(frozen=True)
class Settings:
    # -- Postgres: the machine cluster holding the kernel's databases --------
    pghost: str = field(default_factory=lambda: _env("VALOR_PGHOST", "/tmp"))
    pgport: int = field(default_factory=lambda: int(_env("VALOR_PGPORT", "5432")))
    database: str = field(default_factory=lambda: _env("VALOR_DB", "valor_rebuild"))
    test_database: str = field(default_factory=lambda: _env("VALOR_TEST_DB", "valor_rebuild_test"))
    kernel_role: str = "valor_kernel"
    owner_role: str = field(default_factory=lambda: _env("VALOR_PG_OWNER", getpass.getuser()))
    pg_bin: str = field(default_factory=lambda: _env("VALOR_PG_BIN", _pg_bin()))
    pg_data_dir: str = field(default_factory=lambda: _env("VALOR_PG_DATA", "/opt/homebrew/var/postgresql@18"))
    pg_passfile: str = field(
        default_factory=lambda: _env(
            "VALOR_PG_PASSFILE", str(Path.home() / ".config" / "valor-kernel" / "pgpass")
        )
    )

    # -- the model provider ---------------------------------------------------
    upstream: str = field(default_factory=lambda: _env("VALOR_UPSTREAM", "https://api.anthropic.com"))
    openai_upstream: str = field(
        default_factory=lambda: _env("VALOR_OPENAI_UPSTREAM", "https://api.openai.com")
    )
    claude: str = field(default_factory=lambda: _env("VALOR_CLAUDE", _claude()))

    # -- the headless browser `look` runs: one fixed Playwright build, never
    # the newest in the cache (a turn cannot write that cache) ---------------
    browser: str = field(
        default_factory=lambda: _env(
            "VALOR_BROWSER",
            str(
                Path.home()
                / "Library/Caches/ms-playwright/chromium_headless_shell-1208"
                / "chrome-headless-shell-mac-arm64/chrome-headless-shell"
            ),
        )
    )

    # -- git, as the kernel runs it: a root-owned install, never looked up on
    # a PATH or through a cache a turn can write (core/binaries.py) -----------
    git_bin: str = field(default_factory=lambda: _env("VALOR_GIT", _git()))
    # The longest one kernel git call may run (a push included) before it is
    # killed and counted as failed; reconcile waits this long, and more,
    # before it reads a missing effect as never having happened.
    git_timeout_s: float = field(default_factory=lambda: float(_env("VALOR_GIT_TIMEOUT_S", "120")))
    # How old a dangling intent must be before `broker.reconcile` reads an
    # effect missing from its target as never having happened: twice the
    # git limit, so no performer can still be pushing it.
    reconcile_after_s: float = field(default_factory=lambda: float(_env("VALOR_RECONCILE_AFTER_S", "240")))

    # -- the judgement legs ----------------------------------------------------
    jev_url: str = field(default_factory=lambda: _env("VALOR_JEV_URL", JEV_URL))
    open_weight_url: str = field(default_factory=lambda: _env("VALOR_OPEN_WEIGHT_URL", OPEN_WEIGHT_URL))
    jev_timeout_s: float = 10.0
    open_weight_timeout_s: float = 30.0
    # Estimated input tokens a leg is sent at most (Jev documents 32k for
    # the state plus the longest question; the fallback's context is 262k
    # and the cap bounds a call's worst case).
    jev_max_input_tokens: int = 30_000
    open_weight_max_input_tokens: int = 100_000
    open_weight_max_tokens: int = 400
    # Judgement calls in flight at once for one diff's governance hunks.
    judgement_concurrency: int = 8
    # Where `python -m core judgement-keys` copies the keys from.
    vault_env: str = field(
        default_factory=lambda: _env("VALOR_VAULT_ENV", str(Path.home() / "Desktop" / "Valor" / ".env"))
    )

    # -- backups: the volume's name is the single character U+F028 ------------
    backup_dir: str = field(default_factory=lambda: _env("VALOR_BACKUP_DIR", "/Volumes//valor_temp"))
    backup_keep: int = field(default_factory=lambda: int(_env("VALOR_BACKUP_KEEP", "30")))
    log_dir: str = field(
        default_factory=lambda: _env("VALOR_LOG_DIR", str(Path.home() / "Library" / "Logs" / "valor"))
    )

    # -- the stage files a turn's Brief renders (skills/sdlc) -----------------
    stages_dir: str = field(
        default_factory=lambda: _env(
            "VALOR_STAGES", str(Path(__file__).resolve().parent.parent / "skills" / "sdlc")
        )
    )

    # -- the persona every turn renders first (persona/) ----------------------
    persona_dir: str = field(
        default_factory=lambda: _env("VALOR_PERSONA", str(Path(__file__).resolve().parent.parent / "persona"))
    )

    # -- the replay and demonstration workspaces ------------------------------
    demo_dir: str = field(default_factory=lambda: _env("VALOR_DEMO", str(Path.home() / "src" / "valor-demo")))

    # -- kernel workspaces: one directory per task, outside ~/src (which the
    # turn sandbox denies); project specs; per-task service ports ----------
    work_dir: str = field(default_factory=lambda: _env("VALOR_WORK", str(Path.home() / "valor-tasks")))
    projects_dir: str = field(
        default_factory=lambda: _env(
            "VALOR_PROJECTS", str(Path(__file__).resolve().parent.parent / "projects")
        )
    )
    pg_ports: tuple[int, int] = field(default_factory=lambda: _span("VALOR_PG_PORTS", "5440-5599"))
    redis_ports: tuple[int, int] = field(default_factory=lambda: _span("VALOR_REDIS_PORTS", "6400-6499"))
    # How long one `setup` command may run at provisioning.
    setup_timeout_s: float = field(default_factory=lambda: float(_env("VALOR_SETUP_TIMEOUT_S", "1200")))
    # The kernel mirror's fetch from a builder's clone: the largest file the
    # receiving git may write, and the footprint past which it is killed
    # (macOS enforces no memory limit on a process).
    mirror_fetch_max_bytes: int = field(
        default_factory=lambda: int(_env("VALOR_MIRROR_FETCH_MAX_BYTES", str(2 * 1024**3)))
    )
    mirror_fetch_max_footprint_mb: int = field(
        default_factory=lambda: int(_env("VALOR_MIRROR_FETCH_MAX_FOOTPRINT_MB", "1024"))
    )
    # The largest verdict file a fresh session may leave.
    verdict_max_bytes: int = 256 * 1024
    # How long one suite run of the test check may take (its setup aside).
    suite_timeout_s: float = field(default_factory=lambda: float(_env("VALOR_SUITE_TIMEOUT_S", "1800")))
    # The largest JUnit file a suite run may leave.
    junit_max_bytes: int = 50 * 1024 * 1024

    # -- tunables -------------------------------------------------------------
    # Bytes per token for the gateway's input estimate, the worst case a
    # call with no reported usage is charged. An underestimate charges less
    # than the invoice, so it leans high: English and JSON run above 3.
    bytes_per_token: int = 3
    # Seconds between SIGTERM and SIGKILL when reaping a turn's processes.
    reap_grace_s: float = 2.0

    def __post_init__(self):
        if self.reconcile_after_s < 2 * self.git_timeout_s:
            raise ValueError(
                f"reconcile_after_s ({self.reconcile_after_s}) must be at least twice git_timeout_s "
                f"({self.git_timeout_s}): reconcile must not read a merge as missing while a perform "
                "could still be pushing it"
            )

    @property
    def judgement_keyfile(self) -> str:
        """The judgement legs' keys, in the kernel key directory beside the
        database password file. Derived from `pg_passfile`, never its own
        setting, so the turn sandbox profiles' deny (derived from the same
        directory) cannot drift from it."""
        return str(Path(self.pg_passfile).parent / "judgement-keys")

    @property
    def claude_token_file(self) -> str:
        """A long-lived Claude token (`claude setup-token`), in the kernel key
        directory, which the gateway sends upstream in place of the dummy a
        turn carries. Absent, the gateway reads the Keychain login."""
        return str(Path(self.pg_passfile).parent / "claude-token")

    @property
    def openai_keyfile(self) -> str:
        """The kernel's OpenAI key (`OPENAI_API_KEY=`), in the kernel key
        directory, which the gateway sends upstream on the OpenAI route.
        Absent, the route forwards the turn's own key."""
        return str(Path(self.pg_passfile).parent / "openai-key")

    @property
    def pg_socket(self) -> str:
        """The machine cluster's Unix socket, as configured."""
        return f"{self.pghost}/.s.PGSQL.{self.pgport}"

    @property
    def pg_socket_real(self) -> str:
        """The same socket with symlinks resolved (`/tmp` is `/private/tmp`)."""
        return f"{os.path.realpath(self.pghost)}/.s.PGSQL.{self.pgport}"

    def dsn(
        self,
        *,
        owner: bool = False,
        database: str | None = None,
        host: str | None = None,
        port: int | None = None,
        passfile: str | None = None,
    ) -> str:
        """A connection string for the kernel role, or the owner's. It names
        the password file and never holds a password."""
        role = self.owner_role if owner else self.kernel_role
        return (
            f"host={host or self.pghost} port={port or self.pgport} "
            f"dbname={database or self.database} user={role} "
            f"passfile={passfile or self.pg_passfile}"
        )

    def as_shell(self) -> str:
        """Every value as `SETTING_NAME=value`, quoted for a POSIX shell."""
        values = {f.name: getattr(self, f.name) for f in fields(self)}
        values["pg_socket"] = self.pg_socket
        values["pg_socket_real"] = self.pg_socket_real
        return "\n".join(f"SETTING_{k.upper()}={shlex.quote(str(v))}" for k, v in values.items())


settings = Settings()


if __name__ == "__main__":
    print(settings.as_shell())
