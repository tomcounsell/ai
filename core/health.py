"""This Mac's database and services: whether they work, and the restart a
broken database needs (docs/plans/a4601299-db-self-recovery.md).

`probe` opens a new connection and runs an anonymous plpgsql block. On
2026-11-13 the nightly upgrade deleted the running server's keg: every new
backend failed to load plpgsql while old backends and `pg_isready` went on
answering, so only a new connection that loads plpgsql sees it.

`curable` says whether a restart of the server cures a failure: a library
that will not load (SQLSTATE 58P01, or XX000 "could not load library"), or
a refused or lost connection. "The database system is starting up" (57P03)
is waited out, never restarted, and a server that answers a new connection
with any other refusal (a database that does not exist, a password it will
not take) is working: a restart cures none of them.

`restart_postgres` stops the cluster in `settings.pg_data_dir` with a fast
stop (a smart stop waits forever on the kernel's and the bridges'
sessions), an immediate one when that fails, then starts its launchd job
again (`launchctl kickstart`, or bootstrap when it is not loaded): launchd,
not `brew services`, which waits on Homebrew's lock. The job runs the
keg's `opt/` path, so a restart starts the upgraded server. Restarts are
spaced: none within `SPACING_S` of the last, by a stamp beside the data
directory, taken under an exclusive `flock`. The stamp holds while the
server is down and is shared by every restarter on the Mac: the kernel
(`Mend`), and `python -m core health --fix`, which a peer's `watch` runs
over ssh.

`Mend` is the kernel's clock: a curable probe failure that has lasted
`DOWN_S` restarts the server.

`tailscale` is this Mac's Tailscale.app: None when it is not installed,
`not running`, or the app's own `BackendState` (`unreadable` when its CLI
does not answer). Its binary is its CLI only with `TERM` set; without one,
as under launchd, it tries to start a second app and fails, so its calls get
`TERM`. Tailscale is Valor's only route to the other Macs; on
2026-10-28 the Captain fell off the tailnet and nobody was at it.
`reopen_tailscale` opens the app, or runs its `up` when it is `Stopped`;
it leaves `NeedsLogin` and `NeedsMachineAuth` to a person. Each action is
spaced `SPACING_S` by its own stamp in `settings.log_dir`, so an app that
reopens `Stopped` is brought up at the next check, not ten minutes later.
`Tailnet` is the kernel's clock for it, as `Mend` is for the database: a
down state that has lasted `DOWN_S` (Tailscale's own self-update relaunch
passes inside it) is acted on, checked at most every `TAILNET_EVERY_S`.

`services` lists this Mac's launchd services (a loaded `com.valor.*` job
whose plist keeps it alive) with each one's pid, None when it is not
running; `report` is `python -m core health`'s JSON: the probe, the
services, free disk, and an update failing now (`failing_update`), which
the peer's `watch` reports once a day.
"""

import asyncio
import contextlib
import fcntl
import json
import os
import plistlib
import shutil
import subprocess
import time
from datetime import UTC
from pathlib import Path
from typing import Any

import psycopg

from core import binaries, db
from core.settings import settings

SPACING_S = 600.0
DOWN_S = 60.0
STOP_TIMEOUT_S = 60
START_TIMEOUT_S = 120
PROBE = "DO $$BEGIN END$$"
AGENTS = Path.home() / "Library/LaunchAgents"
TAILSCALE_APP = Path("/Applications/Tailscale.app")
TAILSCALE_STATUS_TIMEOUT_S = 10
TAILNET_EVERY_S = 30.0
NEEDS_PERSON = ("NeedsLogin", "NeedsMachineAuth")
PGREP = "/usr/bin/pgrep"
OPEN = "/usr/bin/open"


def _cli_env() -> dict[str, str]:
    """The environment with the `TERM` Tailscale's CLI needs (module docstring)."""
    return {**os.environ, "TERM": os.environ.get("TERM") or "dumb"}


async def probe(dsn: str | None = None) -> Exception | None:
    """None when a new connection loads plpgsql, else the failure."""
    try:
        async with await db.connect(dsn) as conn:
            await conn.execute(PROBE)
    except psycopg.Error as exc:
        return exc
    return None


def curable(exc: BaseException) -> bool:
    """Whether restarting the server cures `exc` (module docstring)."""
    if not isinstance(exc, psycopg.Error):
        return False
    text = str(exc)
    if exc.sqlstate == "57P03" or "starting up" in text:
        return False
    if exc.sqlstate in ("58P01", "XX000"):
        return "could not load library" in text
    if exc.sqlstate is None and isinstance(exc, psycopg.OperationalError):
        return "FATAL:" not in text  # refused, or lost: no server answered
    return exc.sqlstate in ("57P01", "57P02")  # the server ended the session


def _stamp(data_dir: str) -> Path:
    d = Path(data_dir)
    return d.with_name(f"{d.name}.valor-restart")


@contextlib.contextmanager
def _spaced(stamp: Path):
    """Under an exclusive `flock` on `stamp`: True, and the stamp written,
    when nothing was done within `SPACING_S`; else False."""
    with open(stamp, "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        if time.time() - stamp.stat().st_mtime < SPACING_S and stamp.stat().st_size:
            yield False
            return
        stamp.write_text(f"{time.time():.0f}\n")
        yield True


def _job(data_dir: str, agents: Path = AGENTS) -> tuple[str, Path] | None:
    """The launchd job that runs the cluster in `data_dir`: its label and plist."""
    want = os.path.realpath(data_dir)
    for path in sorted(agents.glob("*.plist")):
        try:
            job = plistlib.loads(path.read_bytes())
        except OSError, plistlib.InvalidFileException:
            continue
        argv = [str(a) for a in job.get("ProgramArguments") or []]
        at = argv.index("-D") + 1 if "-D" in argv else len(argv)
        if at < len(argv) and os.path.realpath(argv[at]) == want and job.get("Label"):
            return job["Label"], path
    return None


def _launchctl(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [binaries.require(binaries.LAUNCHCTL), *argv],
        capture_output=True, text=True, timeout=START_TIMEOUT_S, check=False,
    )  # fmt: skip


def restart_postgres(data_dir: str | None = None, pg_bin: str | None = None, agents: Path = AGENTS) -> str:
    """Stop and start the cluster (module docstring). Returns what was done:
    `restarted`, `spaced` (one ran within `SPACING_S`), `no job`, or why
    the start failed."""
    data_dir = data_dir or settings.pg_data_dir
    pg_ctl = str(Path(pg_bin or settings.pg_bin) / "pg_ctl")
    found = _job(data_dir, agents)
    if found is None:
        return "no job"
    label, plist = found
    with _spaced(_stamp(data_dir)) as due:
        if not due:
            return "spaced"
        if (Path(data_dir) / "postmaster.pid").exists():
            for mode in ("fast", "immediate"):
                stopped = subprocess.run(
                    [pg_ctl, "-D", data_dir, "-m", mode, "-t", str(STOP_TIMEOUT_S), "stop"],
                    capture_output=True, text=True, timeout=STOP_TIMEOUT_S + 10, check=False,
                )  # fmt: skip
                if stopped.returncode == 0:
                    break
        domain = f"gui/{os.getuid()}"
        if _launchctl("print", f"{domain}/{label}").returncode != 0:
            started = _launchctl("bootstrap", domain, str(plist))
        else:
            started = _launchctl("kickstart", f"{domain}/{label}")
        if started.returncode != 0:
            return f"launchctl exited {started.returncode}: {started.stderr.strip()[-200:]}"
    return "restarted"


class Mend:
    """The kernel's database clock (module docstring)."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.down_since: float | None = None  # the first curable failure of this outage
        self.cause: str | None = None
        self.restarted: list[str] = []  # what each restart of this outage did

    async def check(self) -> bool:
        """Probe; restart the server when it has failed curably for
        `DOWN_S`. True when the database works."""
        failed = await probe(self.dsn)
        if failed is None:
            if not self.restarted:
                self.down_since = self.cause = None  # it passed without a restart
            return True
        if not curable(failed):
            return False
        now = time.monotonic()
        if self.down_since is None:
            self.down_since, self.cause = now, str(failed).strip().splitlines()[0][:300]
        if now - self.down_since >= DOWN_S:
            done = await asyncio.to_thread(restart_postgres)
            if done != "spaced":
                self.restarted.append(done)
                print(f"database failing since {self.cause!r}: restart {done}", flush=True)
        return False

    def outage(self) -> dict[str, Any] | None:
        """The outage just ended, if a restart was part of it; and reset."""
        ended = None
        if self.restarted:
            ended = {
                "cause": self.cause,
                "down_s": round(time.monotonic() - (self.down_since or time.monotonic())),
                "restarts": self.restarted,
                "machine": settings.machine,
            }
        self.down_since, self.cause, self.restarted = None, None, []
        return ended


def tailscale() -> str | None:
    """This Mac's Tailscale.app state (module docstring)."""
    if not TAILSCALE_APP.is_dir():
        return None
    if subprocess.run([PGREP, "-xq", "Tailscale"], check=False).returncode != 0:
        return "not running"
    try:
        status = subprocess.run(
            [str(TAILSCALE_APP / "Contents/MacOS/Tailscale"), "status", "--json"],
            capture_output=True, text=True, timeout=TAILSCALE_STATUS_TIMEOUT_S, check=False, env=_cli_env(),
        )  # fmt: skip
        return str(json.loads(status.stdout)["BackendState"])
    except OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError:
        return "unreadable"


def reopen_tailscale(state: str, stamps: Path | None = None) -> str:
    """Act on a down Tailscale (module docstring). Returns what was done:
    `opened`, `up`, `spaced`, `needs a person`, or why it failed."""
    if state in NEEDS_PERSON:
        return "needs a person"
    if state == "Stopped":
        action, argv = "up", [str(TAILSCALE_APP / "Contents/MacOS/Tailscale"), "up"]
    else:
        action, argv = "opened", [OPEN, "-a", str(TAILSCALE_APP)]
    stamps = Path(stamps or settings.log_dir)
    stamps.mkdir(parents=True, exist_ok=True)
    with _spaced(stamps / f"tailscale-{action}.valor-reopen") as due:
        if not due:
            return "spaced"
        try:
            done = subprocess.run(
                argv, capture_output=True, text=True, timeout=START_TIMEOUT_S, check=False, env=_cli_env()
            )
        except subprocess.TimeoutExpired:
            return f"{action} timed out"
        if done.returncode != 0:
            return f"{action} exited {done.returncode}: {done.stderr.strip()[-200:]}"
    return action


class Tailnet:
    """The kernel's Tailscale clock (module docstring)."""

    def __init__(self):
        self.down_since: float | None = None
        self.checked: float | None = None

    async def check(self) -> str | None:
        """The state, when one was read; acts on one down for `DOWN_S`."""
        now = time.monotonic()
        if self.checked is not None and now - self.checked < TAILNET_EVERY_S:
            return None
        self.checked = now
        state = await asyncio.to_thread(tailscale)
        if state in (None, "Running", *NEEDS_PERSON):
            self.down_since = None
            return state
        if self.down_since is None:
            self.down_since = now
        if now - self.down_since >= DOWN_S:
            done = await asyncio.to_thread(reopen_tailscale, state)
            if done != "spaced":
                print(f"tailscale {state}: {done}", flush=True)
        return state


def services(agents: Path = AGENTS) -> dict[str, int | None]:
    """Each loaded `com.valor.*` job whose plist keeps it alive, with its pid."""
    listed = _launchctl("list")
    found: dict[str, int | None] = {}
    for line in listed.stdout.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) != 3 or not parts[2].startswith("com.valor."):
            continue
        try:
            job = plistlib.loads((agents / f"{parts[2]}.plist").read_bytes())
        except OSError, plistlib.InvalidFileException:
            continue
        if job.get("KeepAlive"):
            found[parts[2]] = int(parts[0]) if parts[0].isdigit() else None
    return found


def kick(label: str) -> str | None:
    """Start or restart a service (`launchctl kickstart -k`); why it failed, or None."""
    done = _launchctl("kickstart", "-k", f"gui/{os.getuid()}/{label}")
    return None if done.returncode == 0 else f"exit {done.returncode}: {done.stderr.strip()[-200:]}"


# Every `rollout.failed` row whose merge or update has no `rollout.ended`:
# the oldest one's time, and the newest one's step and reason.
FAILING = (
    "SELECT min(f.at), (array_agg(f.payload->>'step' ORDER BY f.id DESC))[1], "
    "(array_agg(f.payload->>'reason' ORDER BY f.id DESC))[1] FROM events f WHERE f.type = 'rollout.failed' "
    "AND NOT EXISTS (SELECT 1 FROM events e WHERE e.type = 'rollout.ended' "
    "AND e.payload->>'effect_id' = f.payload->>'effect_id')"
)


async def failing_update(conn) -> dict[str, Any] | None:
    """This Mac's update failing now (`core/serve.py` `_failed`): `since`
    (UTC ISO), the latest `step` and `reason`; None when every failed
    rollout has since ended (done, up to date or superseded)."""
    since, step, reason = await (await conn.execute(FAILING)).fetchone()
    if since is None:
        return None
    return {"since": since.astimezone(UTC).isoformat(timespec="seconds"), "step": step, "reason": reason}


async def report(dsn: str | None = None, *, fix: bool = False, agents: Path = AGENTS) -> dict[str, Any]:
    """`python -m core health [--fix]`: the probe, the services, Tailscale,
    free disk. `--fix` restarts a curably failing server, starts each
    service with no pid, and reopens a down Tailscale; it touches no
    running process. Tailscale is not part of `healthy`, nor is `update`
    (`failing_update`, None while the database fails its probe)."""
    failed = await probe(dsn)
    running = await asyncio.to_thread(services, agents)
    tailnet = await asyncio.to_thread(tailscale)
    out: dict[str, Any] = {
        "machine": settings.machine,
        "probe": None if failed is None else str(failed).strip().splitlines()[0][:300],
        "curable": failed is not None and curable(failed),
        "services": running,
        "tailscale": tailnet,
        "free_gb": round(shutil.disk_usage(Path.home()).free / 1e9, 1),
        "update": None,
    }
    if failed is None:
        async with await db.connect(dsn) as conn:
            out["update"] = await failing_update(conn)
    if fix:
        fixed: dict[str, Any] = {}
        if out["curable"]:
            fixed["postgres"] = await asyncio.to_thread(restart_postgres, None, None, agents)
        for label, pid in running.items():
            if pid is None:
                fixed[label] = await asyncio.to_thread(kick, label) or "started"
        if tailnet not in (None, "Running"):
            fixed["tailscale"] = await asyncio.to_thread(reopen_tailscale, tailnet)
        out["fixed"] = fixed
    out["healthy"] = failed is None and all(pid is not None for pid in running.values())
    return out
