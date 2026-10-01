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


def _pg_bin() -> str:
    found = shutil.which("pg_dump")
    return str(Path(found).parent) if found else "/opt/homebrew/opt/postgresql@18/bin"


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

# The seats a model fills, by pinned id, never a floating alias: a ledger row
# has to describe a fixed thing. Editing this is a change inside the trust
# boundary, reviewed like kernel code.
SEATS: dict[str, str] = {
    "frontier": "claude-opus-5-5",
    "reviewer": "claude-opus-5-5",
    "light": "claude-haiku-4-5",
}


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
    claude: str = field(default_factory=lambda: _env("VALOR_CLAUDE", _claude()))

    # -- git, as the kernel runs it: an absolute path, never looked up on a
    # PATH a turn can write to (core/git.py) ----------------------------------
    git_bin: str = field(default_factory=lambda: _env("VALOR_GIT", "/usr/bin/git"))

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

    # -- the replay and demonstration workspaces ------------------------------
    demo_dir: str = field(default_factory=lambda: _env("VALOR_DEMO", str(Path.home() / "src" / "valor-demo")))

    # -- tunables -------------------------------------------------------------
    # Bytes per token for the gateway's input estimate. An underestimate
    # spends money nobody reserved, so it leans high: English and JSON run
    # above 3.
    bytes_per_token: int = 3
    # Seconds between SIGTERM and SIGKILL when reaping a turn's processes.
    reap_grace_s: float = 2.0
    # Turns in a row ending with neither a question nor a delivery before a
    # run returns so Tom can look.
    idle_turns: int = 2

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
