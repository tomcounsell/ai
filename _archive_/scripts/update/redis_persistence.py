"""Redis durability configuration module.

Ensures Redis AOF persistence, eviction policy, and related durability settings
are applied on every machine via ``/update``.

Three directives are applied:
  - ``appendonly yes`` — enable AOF persistence
  - ``appendfsync everysec`` — fsync once per second (bounded-loss durability)
  - ``maxmemory-policy noeviction`` — never silently evict durable Popoto keys

After applying CONFIG SET, attempts ``CONFIG REWRITE`` to persist directives into
the active ``redis.conf``.  If CONFIG REWRITE fails (Redis started without
``--config``), writes a stub ``redis.conf`` in Redis's ``dir`` and emits a loud
WARNING so the operator knows to restart Redis with that file.

All failures are **non-fatal**: if ``redis-cli`` is absent or Redis is down the
module returns a ``skipped``/``failed`` result and logs a warning.  It never
raises and never blocks the rest of ``/update``.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# The three directives we pin on every machine.
_DIRECTIVES: list[tuple[str, str]] = [
    ("appendonly", "yes"),
    ("appendfsync", "everysec"),
    ("maxmemory-policy", "noeviction"),
]

# Stub content written when CONFIG REWRITE cannot persist to an existing file.
_STUB_CONTENT = """\
# redis.conf stub written by scripts/update/redis_persistence.py
# Restart Redis with: redis-server {path}
appendonly yes
appendfsync everysec
maxmemory-policy noeviction
"""


@dataclass
class RedisPersistenceResult:
    """Result of ``apply_redis_persistence``."""

    success: bool
    action: str  # "applied", "applied_with_warning", "skipped", "failed"
    warning: str | None = None  # loud warning for stub-write path
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


def apply_redis_persistence() -> RedisPersistenceResult:
    """Apply Redis durability settings idempotently.

    Steps:
    1. Locate redis-cli — skip if absent.
    2. Ping Redis — skip if unreachable.
    3. Apply CONFIG SET for each of the three directives.
    4. CONFIG REWRITE to persist into redis.conf.
       - If REWRITE fails, derive the config dir, write a stub redis.conf, and
         emit a WARNING (AOF set for this session only until Redis is restarted
         with the stub file).
    5. Post-condition asserts: verify aof_enabled:1 and maxmemory-policy=noeviction.

    Never raises.  Always returns an ``RedisPersistenceResult``.
    """
    cli = _redis_cli()
    if cli is None:
        msg = "redis-cli not found on PATH — skipping Redis durability configuration"
        logger.warning("[redis_persistence] %s", msg)
        return RedisPersistenceResult(success=False, action="skipped", error=msg)

    # Step 2: Ping Redis.
    try:
        ping = _run([cli, "ping"])
    except OSError as exc:
        msg = f"redis-cli ping failed (OSError): {exc}"
        logger.warning("[redis_persistence] %s", msg)
        return RedisPersistenceResult(success=False, action="failed", error=msg)
    except subprocess.TimeoutExpired:
        msg = "redis-cli ping timed out"
        logger.warning("[redis_persistence] %s", msg)
        return RedisPersistenceResult(success=False, action="failed", error=msg)

    if ping.returncode != 0 or "PONG" not in ping.stdout.upper():
        stderr = ping.stderr.strip() or ping.stdout.strip() or "no output"
        msg = f"Redis not reachable (redis-cli ping returned {ping.returncode}): {stderr}"
        logger.warning("[redis_persistence] %s", msg)
        return RedisPersistenceResult(success=False, action="failed", error=msg)

    # Step 3: Apply CONFIG SET for each directive.
    try:
        for key, value in _DIRECTIVES:
            proc = _run([cli, "CONFIG", "SET", key, value])
            if proc.returncode != 0:
                stderr = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
                msg = f"CONFIG SET {key} {value} failed: {stderr}"
                logger.warning("[redis_persistence] %s", msg)
                return RedisPersistenceResult(success=False, action="failed", error=msg)
            logger.debug("[redis_persistence] CONFIG SET %s %s -> OK", key, value)
    except OSError as exc:
        msg = f"CONFIG SET failed (OSError): {exc}"
        logger.warning("[redis_persistence] %s", msg)
        return RedisPersistenceResult(success=False, action="failed", error=msg)
    except subprocess.TimeoutExpired:
        msg = "CONFIG SET timed out"
        logger.warning("[redis_persistence] %s", msg)
        return RedisPersistenceResult(success=False, action="failed", error=msg)

    # Step 4: CONFIG REWRITE.
    stub_warning: str | None = None
    try:
        rewrite = _run([cli, "CONFIG", "REWRITE"])
        if rewrite.returncode != 0:
            # REWRITE failed — Redis started without --config.  Write a stub.
            stub_warning = _write_stub_conf(cli)
        else:
            logger.info("[redis_persistence] CONFIG REWRITE succeeded")
    except OSError as exc:
        stub_warning = f"CONFIG REWRITE OSError ({exc}); stub may not be written"
        logger.warning("[redis_persistence] %s", stub_warning)
    except subprocess.TimeoutExpired:
        stub_warning = "CONFIG REWRITE timed out; directives set for this session only"
        logger.warning("[redis_persistence] %s", stub_warning)

    # Step 5: Post-condition assertions.
    post_error = _verify_postconditions(cli)
    if post_error:
        logger.warning("[redis_persistence] post-condition check failed: %s", post_error)
        return RedisPersistenceResult(
            success=False,
            action="failed",
            warning=stub_warning,
            error=post_error,
        )

    if stub_warning:
        logger.warning("[redis_persistence] %s", stub_warning)
        return RedisPersistenceResult(
            success=True,
            action="applied_with_warning",
            warning=stub_warning,
        )

    logger.info("[redis_persistence] AOF + eviction policy applied and persisted")
    return RedisPersistenceResult(success=True, action="applied")


def _write_stub_conf(cli: str) -> str:
    """Write a stub redis.conf to Redis's working directory.

    Returns a loud WARNING string for the operator.
    """
    try:
        dir_proc = _run([cli, "CONFIG", "GET", "dir"])
        conf_dir: Path | None = None
        if dir_proc.returncode == 0 and dir_proc.stdout.strip():
            lines = [ln.strip() for ln in dir_proc.stdout.strip().splitlines() if ln.strip()]
            # Output is two-line: "dir\n/path/to/dir"
            for i, line in enumerate(lines):
                if line == "dir" and i + 1 < len(lines):
                    conf_dir = Path(lines[i + 1])
                    break
    except (OSError, subprocess.TimeoutExpired):
        conf_dir = None

    if conf_dir is None or not conf_dir.is_dir():
        warning = (
            "WARNING: AOF set for this session only; "
            "CONFIG REWRITE failed and could not determine Redis dir. "
            "Restart Redis with --appendonly yes --appendfsync everysec "
            "--maxmemory-policy noeviction to make it durable."
        )
        logger.warning("[redis_persistence] %s", warning)
        return warning

    conf_path = conf_dir / "redis.conf"
    if not conf_path.exists():
        # Write the stub.
        try:
            conf_path.write_text(_STUB_CONTENT.format(path=conf_path))
            warning = (
                f"WARNING: AOF set for this session only; "
                f"stub redis.conf written to {conf_path}. "
                f"Restart Redis with: redis-server {conf_path}"
            )
            logger.warning("[redis_persistence] %s", warning)
            return warning
        except OSError as exc:
            warning = (
                f"WARNING: AOF set for this session only; "
                f"CONFIG REWRITE failed and could not write stub to {conf_path}: {exc}. "
                f"Restart Redis with --appendonly yes --appendfsync everysec "
                f"--maxmemory-policy noeviction."
            )
            logger.warning("[redis_persistence] %s", warning)
            return warning
    else:
        # redis.conf exists but CONFIG REWRITE still failed — unexpected.
        warning = (
            f"WARNING: AOF set for this session only; "
            f"CONFIG REWRITE failed even though {conf_path} exists. "
            f"Manually add: appendonly yes / appendfsync everysec / "
            f"maxmemory-policy noeviction"
        )
        logger.warning("[redis_persistence] %s", warning)
        return warning


def _verify_postconditions(cli: str) -> str | None:
    """Verify AOF is enabled and maxmemory-policy is noeviction.

    Returns None on success, or an error string describing what failed.
    """
    errors: list[str] = []

    # Check aof_enabled via INFO persistence.
    try:
        info = _run([cli, "INFO", "persistence"])
        if info.returncode == 0:
            if "aof_enabled:1" not in info.stdout:
                errors.append("aof_enabled:1 not found in INFO persistence output")
        else:
            errors.append(f"INFO persistence failed (exit {info.returncode})")
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(f"INFO persistence error: {exc}")

    # Check maxmemory-policy.
    try:
        pol = _run([cli, "CONFIG", "GET", "maxmemory-policy"])
        if pol.returncode == 0:
            if "noeviction" not in pol.stdout:
                errors.append(f"maxmemory-policy is not noeviction; got: {pol.stdout.strip()!r}")
        else:
            errors.append(f"CONFIG GET maxmemory-policy failed (exit {pol.returncode})")
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(f"CONFIG GET maxmemory-policy error: {exc}")

    return "; ".join(errors) if errors else None
