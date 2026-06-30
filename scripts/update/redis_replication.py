"""Redis replication + Sentinel propagation module (Fix #5 — #1827).

Seeds the replica/Sentinel topology on a host that has opted in to being a Redis
node.  This is the availability companion to ``redis_persistence.py`` (durability):
durability bounds write loss *within* a running Redis; replication + Sentinel
bound the loss of the *host* itself.

CRITICAL SEMANTICS — this step is **BOOTSTRAP-ONLY / seed-once**, NOT an idempotent
re-apply.  Replication/Sentinel topology is **runtime-mutable and Sentinel-owned**:
``replicaof`` and ``sentinel monitor`` change at every failover.  Re-applying a
static template on an established cluster would demote a promoted master back to a
replica of the dead primary — a catastrophic regression.  Therefore the step:

  1. ROLE GATE — only acts on a host that has opted in via the marker file
     ``data/redis-replication-enabled`` (mirrors ``data/auto-revert-enabled``).
     Most machines are Redis *clients*; absent the marker the step is a clean skip.
  2. PRESENCE CHECK / early-exit — if the node already reports ``role:master`` with
     connected replicas, or already reports ``role:slave``, or a Sentinel already
     monitors it, the topology is established → skip and touch NOTHING.
  3. HARD INVARIANT — NEVER ``CONFIG SET replicaof`` on a node reporting
     ``role:master``.  In fact this module never issues ``CONFIG SET replicaof`` at
     all: seeding a virgin node is purely file-based (stage the template into the
     Redis config dir for the operator to substitute + restart).  The invariant
     therefore holds *by construction*, not by luck.
  4. Only on a virgin, never-monitored node (``role:master`` with zero connected
     replicas and no Sentinel) does it seed the template, returning
     ``applied_with_warning`` so the operator knows to substitute placeholders and
     restart Redis with the staged config.

All failures are **non-fatal**: absent ``redis-cli``, an unreachable Redis, or an
unreadable marker all return a ``skipped``/``failed`` result and log a warning.  It
never raises and never blocks the rest of ``/update``.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Project root (ai/) — scripts/update/redis_replication.py → ../../
_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

# Role-gate marker: a host opts in to being a Redis node by touching this file.
# Mirrors monitoring/bridge_watchdog.py::AUTO_REVERT_ENABLED_FILE.  Absent on every
# client-only machine (the common case), which makes this step a clean skip there.
REPLICATION_MARKER_FILE = _PROJECT_DIR / "data" / "redis-replication-enabled"

# Logical Sentinel master name + Sentinel port.  Provisional defaults — override
# via env for a real deployment (grain of salt: these are tunable, not sacred).
_SENTINEL_MASTER_NAME = os.environ.get("REDIS_SENTINEL_MASTER_NAME", "valor-redis")
_SENTINEL_PORT = os.environ.get("REDIS_SENTINEL_PORT", "26379")

# Stub content staged into the Redis config dir on a virgin opted-in node.  Carries
# the same <PLACEHOLDER> tokens as config/redis/redis-replica.conf.template so the
# operator substitutes real host values and restarts.  We deliberately do NOT
# CONFIG SET replicaof (see HARD INVARIANT) — seeding is file-only.
_REPLICA_STUB_CONTENT = """\
# redis-replica.conf stub staged by scripts/update/redis_replication.py (#1827).
# Substitute <PRIMARY_HOST>/<PRIMARY_PORT>, then restart: redis-server {path}
replicaof <PRIMARY_HOST> <PRIMARY_PORT>
replica-read-only yes
appendonly yes
appendfsync everysec
maxmemory-policy noeviction
"""


@dataclass
class RedisReplicationResult:
    """Result of ``apply_redis_replication`` (mirrors ``RedisPersistenceResult``)."""

    success: bool
    action: str  # "applied", "applied_with_warning", "skipped", "failed"
    warning: str | None = None  # loud warning for the stub-stage path
    error: str | None = None


def _run(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """Run a subprocess, capture stdout/stderr, return the result."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _redis_cli() -> str | None:
    """Return the path to redis-cli, or None if absent."""
    return shutil.which("redis-cli")


def _parse_info_replication(stdout: str) -> tuple[str | None, int]:
    """Parse ``role`` and ``connected_slaves`` from ``INFO replication`` output.

    Returns ``(role, connected_slaves)``; role is None if not found.
    """
    role: str | None = None
    connected_slaves = 0
    for raw in stdout.splitlines():
        line = raw.strip()
        if line.startswith("role:"):
            role = line.split(":", 1)[1].strip()
        elif line.startswith("connected_slaves:"):
            try:
                connected_slaves = int(line.split(":", 1)[1].strip())
            except ValueError:
                connected_slaves = 0
    return role, connected_slaves


def _sentinel_monitors_master(cli: str) -> bool:
    """Return True if a Sentinel on the local host already monitors the master.

    A reachable Sentinel that knows ``<master>`` means the topology is established.
    Any error (no Sentinel, unreachable) is treated as "not monitored".
    """
    try:
        proc = _run([cli, "-p", _SENTINEL_PORT, "SENTINEL", "master", _SENTINEL_MASTER_NAME])
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


def apply_redis_replication() -> RedisReplicationResult:
    """Seed replica/Sentinel config on a virgin, opted-in Redis node.

    Bootstrap-only / seed-once — see the module docstring.  Never raises; always
    returns a ``RedisReplicationResult``.
    """
    # Step 1: ROLE GATE — most machines are clients and skip cleanly.
    try:
        opted_in = REPLICATION_MARKER_FILE.exists()
    except OSError as exc:
        msg = f"could not read replication marker ({exc}) — skipping"
        logger.warning("[redis_replication] %s", msg)
        return RedisReplicationResult(success=False, action="skipped", error=msg)

    if not opted_in:
        msg = (
            "client-only machine (no data/redis-replication-enabled marker) — "
            "skipping Redis replication seeding"
        )
        logger.info("[redis_replication] %s", msg)
        return RedisReplicationResult(success=False, action="skipped", error=msg)

    # Step 2: redis-cli present?
    cli = _redis_cli()
    if cli is None:
        msg = "redis-cli not found on PATH — skipping Redis replication seeding"
        logger.warning("[redis_replication] %s", msg)
        return RedisReplicationResult(success=False, action="skipped", error=msg)

    # Step 3: Redis reachable?  Non-fatal skip if down.
    try:
        ping = _run([cli, "ping"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = f"redis-cli ping failed ({exc}) — skipping Redis replication seeding"
        logger.warning("[redis_replication] %s", msg)
        return RedisReplicationResult(success=False, action="skipped", error=msg)

    if ping.returncode != 0 or "PONG" not in ping.stdout.upper():
        stderr = ping.stderr.strip() or ping.stdout.strip() or "no output"
        msg = f"Redis not reachable (ping returned {ping.returncode}: {stderr}) — skipping"
        logger.warning("[redis_replication] %s", msg)
        return RedisReplicationResult(success=False, action="skipped", error=msg)

    # Step 4: Inspect current role via INFO replication.
    try:
        info = _run([cli, "INFO", "replication"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = f"INFO replication failed ({exc}) — skipping"
        logger.warning("[redis_replication] %s", msg)
        return RedisReplicationResult(success=False, action="skipped", error=msg)

    if info.returncode != 0:
        stderr = info.stderr.strip() or info.stdout.strip() or "no output"
        msg = f"INFO replication returned {info.returncode}: {stderr} — skipping"
        logger.warning("[redis_replication] %s", msg)
        return RedisReplicationResult(success=False, action="skipped", error=msg)

    role, connected_slaves = _parse_info_replication(info.stdout)

    # Step 5: PRESENCE CHECK / early-exit on any established topology.
    if _sentinel_monitors_master(cli):
        msg = (
            f"Sentinel already monitors '{_SENTINEL_MASTER_NAME}' — established "
            "topology; touching nothing"
        )
        logger.info("[redis_replication] %s", msg)
        return RedisReplicationResult(success=True, action="skipped", error=msg)

    if role == "slave":
        msg = "node already reports role:slave — established replica; touching nothing"
        logger.info("[redis_replication] %s", msg)
        return RedisReplicationResult(success=True, action="skipped", error=msg)

    if role == "master" and connected_slaves >= 1:
        msg = (
            f"node reports role:master with {connected_slaves} connected replica(s) — "
            "established master; touching nothing"
        )
        logger.info("[redis_replication] %s", msg)
        return RedisReplicationResult(success=True, action="skipped", error=msg)

    if role != "master":
        # Unknown/unexpected role — be conservative and skip rather than mutate.
        msg = f"unexpected role:{role!r} — skipping out of caution"
        logger.warning("[redis_replication] %s", msg)
        return RedisReplicationResult(success=False, action="skipped", error=msg)

    # Step 6: Virgin opted-in node (role:master, 0 replicas, no Sentinel).
    # HARD INVARIANT: we do NOT CONFIG SET replicaof here — seeding is file-only.
    return _seed_virgin_node(cli)


def _seed_virgin_node(cli: str) -> RedisReplicationResult:
    """Stage the replica template into the Redis config dir (file-only, never CONFIG SET).

    Returns ``applied_with_warning`` so the operator substitutes placeholders and
    restarts Redis.  Falls back to a loud warning if the config dir is undeterminable.
    """
    conf_dir = _redis_config_dir(cli)
    if conf_dir is None or not conf_dir.is_dir():
        # Could not stage anything → nothing was applied → honest failure (non-fatal).
        err = (
            "opted-in Redis node but could not determine the Redis config dir to "
            "stage redis-replica.conf. Substitute config/redis/"
            "redis-replica.conf.template by hand and restart Redis. See the runbook "
            "in docs/features/redis-durability.md."
        )
        logger.warning("[redis_replication] %s", err)
        return RedisReplicationResult(success=False, action="failed", error=err)

    conf_path = conf_dir / "redis-replica.conf"
    try:
        if not conf_path.exists():
            conf_path.write_text(_REPLICA_STUB_CONTENT.format(path=conf_path))
        warning = (
            f"WARNING: staged replica config stub at {conf_path}. Substitute the "
            "<PRIMARY_HOST>/<PRIMARY_PORT> placeholders, bring up Sentinels "
            "(config/redis/sentinel.conf.template), and restart Redis with: "
            f"redis-server {conf_path}. See the failover runbook in "
            "docs/features/redis-durability.md."
        )
        logger.warning("[redis_replication] %s", warning)
        return RedisReplicationResult(success=True, action="applied_with_warning", warning=warning)
    except OSError as exc:
        # Write failed → nothing staged → honest failure (non-fatal).
        err = (
            f"opted-in Redis node but could not stage replica config to {conf_path}: "
            f"{exc}. Substitute config/redis/redis-replica.conf.template by hand and "
            "restart Redis."
        )
        logger.warning("[redis_replication] %s", err)
        return RedisReplicationResult(success=False, action="failed", error=err)


def _redis_config_dir(cli: str) -> Path | None:
    """Return Redis's working ``dir`` via ``CONFIG GET dir``, or None on any error."""
    try:
        proc = _run([cli, "CONFIG", "GET", "dir"])
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    lines = [ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip()]
    # Output is two-line: "dir\n/path/to/dir"
    for i, line in enumerate(lines):
        if line == "dir" and i + 1 < len(lines):
            return Path(lines[i + 1])
    return None
