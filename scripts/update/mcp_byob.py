"""Idempotent verification + repair of the BYOB MCP server registration.

Adds the ``byob`` entry to ``~/.claude.json`` ``mcpServers`` so Claude
Code sessions can call the BYOB ``byob_*`` tools (real-Chrome automation
backed by the user's logged-in Chrome session).

Concurrency safety
------------------
``~/.claude.json`` is a 5400+ line file rewritten by Claude Code itself
on session events. Concurrent atomic-rename without a lock would clobber
in-flight writes. This module:

1. Acquires an ``fcntl.flock(LOCK_EX | LOCK_NB)`` (or ``LOCK_SH`` in
   read-only verify mode) on a sidecar lockfile next to ``~/.claude.json``.
2. Retries up to 3x with exponential backoff (50ms / 200ms / 800ms) if
   the lock is contended; logs a warning and returns drift status
   without writing if still held after retries.
3. Backs up the file (``~/.claude.json.bak``), parses, mutates in-memory,
   writes to a temp file, then ``os.rename`` (atomic on POSIX) to the
   final path.
4. Releases the lock in ``try/finally``.

This is the BYOB sibling of ``scripts.update.mcp_memory``. The two
modules deliberately share neither code nor lock state -- each writes
its own MCP server entry under the same lock, and is safe to run in any
order.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

CLAUDE_CONFIG_PATH = Path.home() / ".claude.json"
CLAUDE_CONFIG_LOCK_PATH = Path.home() / ".claude.json.lock"
CLAUDE_CONFIG_BACKUP_PATH = Path.home() / ".claude.json.bak"

MCP_SERVER_KEY = "byob"
# BYOB v0.3+ ships its MCP server as a TypeScript entrypoint executed via
# tsx. Per BYOB README's "Manual MCP registration" section the canonical
# invocation is `<tsx> <byob-mcp.ts>`. Both binaries are workspace-local
# after `bun install` runs in ~/.byob/.
BYOB_HOME = Path.home() / ".byob"
BYOB_MCP_SERVER_TS = BYOB_HOME / "packages" / "mcp-server" / "bin" / "byob-mcp.ts"
BYOB_TSX_BIN = BYOB_HOME / "packages" / "mcp-server" / "node_modules" / ".bin" / "tsx"
# Retained for any external callers; _expected_entry() reads BYOB_TSX_BIN
# live so monkeypatching the path during tests works without also patching
# this constant.
MCP_SERVER_COMMAND = str(BYOB_TSX_BIN)

# Lock retry schedule (matches mcp_memory.py) -- in milliseconds.
_LOCK_RETRY_BACKOFF_MS = (50, 200, 800)


@dataclass
class McpByobResult:
    """Result of a verify_byob_mcp() invocation."""

    ok: bool  # True if config now reflects current install state (entry
    # present and correct when binaries exist; absent when they don't).
    action: str  # "ok", "installed", "repaired", "removed",
    # "drift_detected", "skipped", "failed"
    message: str = ""


def _expected_entry() -> dict:
    """Return the canonical shape of the BYOB MCP entry.

    BYOB security default: ``BYOB_ALLOW_EVAL=0`` keeps ``browser_eval``
    disabled (per BYOB README). Operators who need eval flip the env var
    via their own ~/.byob configuration -- never via this registrar.

    Reads ``BYOB_TSX_BIN`` live (not the cached ``MCP_SERVER_COMMAND``) so
    monkeypatching the path in tests does not also require patching the
    string constant.
    """
    return {
        "type": "stdio",
        "command": str(BYOB_TSX_BIN),
        "args": [str(BYOB_MCP_SERVER_TS)],
        "env": {"BYOB_ALLOW_EVAL": "0"},
    }


def _byob_binaries_present() -> bool:
    """Return True iff both BYOB MCP binaries exist on disk.

    Gates registration so machines that have not run ``/setup`` Step 8.5
    (BYOB clone + ``bun install``) do not get a ``mcpServers.byob`` entry
    pointing at non-existent paths -- which would make Claude Code log
    spawn failures on every session restart.
    """
    return BYOB_TSX_BIN.exists() and BYOB_MCP_SERVER_TS.exists()


def _entry_matches(actual: object, expected: dict) -> bool:
    """Compare actual entry against expected canonical shape."""
    if not isinstance(actual, dict):
        return False
    if actual.get("type") != expected["type"]:
        return False
    if actual.get("command") != expected["command"]:
        return False
    if list(actual.get("args") or []) != list(expected["args"]):
        return False
    actual_env = actual.get("env") or {}
    expected_env = expected["env"]
    if not isinstance(actual_env, dict):
        return False
    if actual_env.get("BYOB_ALLOW_EVAL") != expected_env["BYOB_ALLOW_EVAL"]:
        return False
    return True


def _acquire_lock(read_only: bool):
    """Acquire LOCK_EX (write) or LOCK_SH (read) on the lockfile.

    Returns the open file descriptor on success, or None if all retries
    exhausted. Caller must release via ``fcntl.flock(fd, LOCK_UN)`` and
    ``os.close(fd)`` in ``try/finally``.
    """
    flag = fcntl.LOCK_SH if read_only else fcntl.LOCK_EX
    flag |= fcntl.LOCK_NB

    # Ensure the lockfile exists so we can open it for locking
    try:
        CLAUDE_CONFIG_LOCK_PATH.touch(exist_ok=True)
    except OSError:
        return None

    fd = None
    for attempt, delay_ms in enumerate((0, *_LOCK_RETRY_BACKOFF_MS)):
        if delay_ms:
            time.sleep(delay_ms / 1000.0)
        try:
            fd = os.open(str(CLAUDE_CONFIG_LOCK_PATH), os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            return None
        try:
            fcntl.flock(fd, flag)
            return fd
        except BlockingIOError:
            os.close(fd)
            fd = None
            if attempt == len(_LOCK_RETRY_BACKOFF_MS):
                # Exhausted retries
                break
            continue
        except OSError:
            os.close(fd)
            return None

    return None


def _release_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def _read_config() -> dict | None:
    """Read and parse ``~/.claude.json``. Returns None on read/parse failure."""
    try:
        with open(CLAUDE_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return None


def _write_config_atomic(config: dict) -> bool:
    """Backup -> tmp -> rename. Returns True on success."""
    try:
        # Best-effort backup
        if CLAUDE_CONFIG_PATH.exists():
            try:
                shutil.copy2(str(CLAUDE_CONFIG_PATH), str(CLAUDE_CONFIG_BACKUP_PATH))
            except OSError:
                pass

        tmp_path = CLAUDE_CONFIG_PATH.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        os.rename(str(tmp_path), str(CLAUDE_CONFIG_PATH))
        return True
    except OSError:
        return False


def verify_byob_mcp(*, write: bool = True) -> McpByobResult:
    """Verify (and optionally repair) the BYOB MCP registration.

    Args:
        write: When True (full / cron modes), write the corrected entry
            if missing or drifted. When False (verify mode), only report
            drift -- never write.

    Returns:
        McpByobResult with `ok` (True iff the entry is present + correct
        after the call), `action` (string label for logging), and `message`.
    """
    expected = _expected_entry()
    fd = _acquire_lock(read_only=not write)
    if fd is None:
        return McpByobResult(
            ok=False,
            action="skipped",
            message=("~/.claude.json lock contended after retries; next /update run will retry"),
        )

    try:
        binaries_present = _byob_binaries_present()
        config = _read_config()
        config_missing = config is None or not isinstance(config, dict)

        # Fast path: no config file and BYOB isn't installed -> nothing to do.
        # Without this, the not-installed gate below would still try to mutate
        # an empty in-memory config (write mode) or report drift (verify mode)
        # for a file that doesn't exist.
        if config_missing and not binaries_present:
            return McpByobResult(
                ok=True,
                action="skipped",
                message="BYOB not installed on this machine; skipping registration",
            )

        if config_missing:
            if not write:
                return McpByobResult(
                    ok=False,
                    action="drift_detected",
                    message="~/.claude.json missing or unreadable",
                )
            config = {}

        servers = config.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
            config["mcpServers"] = servers

        actual = servers.get(MCP_SERVER_KEY)

        # Existence gate: machines without BYOB installed must NOT get an
        # entry written. If a stale entry from a prior install lingers,
        # remove it (drift heal in reverse) so Claude Code stops trying to
        # spawn a server whose binaries no longer exist.
        if not binaries_present:
            if actual is None:
                return McpByobResult(
                    ok=True,
                    action="skipped",
                    message="BYOB not installed on this machine; skipping registration",
                )
            if not write:
                return McpByobResult(
                    ok=False,
                    action="drift_detected",
                    message=(
                        "byob entry present but BYOB binaries missing; "
                        "run /update to remove the stale entry"
                    ),
                )
            del servers[MCP_SERVER_KEY]
            config["mcpServers"] = servers
            if not _write_config_atomic(config):
                return McpByobResult(
                    ok=False, action="failed", message="failed to write ~/.claude.json"
                )
            return McpByobResult(
                ok=True,
                action="removed",
                message=("byob MCP entry removed (BYOB binaries not present on this machine)"),
            )

        if _entry_matches(actual, expected):
            return McpByobResult(ok=True, action="ok", message="byob MCP registration: ok")

        if not write:
            return McpByobResult(
                ok=False,
                action="drift_detected",
                message=("byob MCP entry missing or drifted; run /update to repair"),
            )

        # Write mode: install or repair.
        servers[MCP_SERVER_KEY] = expected
        config["mcpServers"] = servers
        if not _write_config_atomic(config):
            return McpByobResult(
                ok=False, action="failed", message="failed to write ~/.claude.json"
            )

        action = "installed" if actual is None else "repaired"
        return McpByobResult(
            ok=True,
            action=action,
            message=f"byob MCP registration: {action} (server={BYOB_MCP_SERVER_TS})",
        )

    finally:
        _release_lock(fd)
