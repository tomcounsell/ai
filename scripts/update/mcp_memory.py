"""Idempotent verification + repair of the memory MCP server registration.

Adds the ``memory`` entry to ``~/.claude.json`` ``mcpServers`` so Claude
Code sessions can call the ``memory_get`` and ``memory_search`` tools.

Concurrency safety
------------------
``~/.claude.json`` is a 5400+ line file rewritten by Claude Code itself
on session events. Concurrent atomic-rename without a lock would clobber
in-flight writes. This module:

1. Acquires an ``fcntl.flock(LOCK_EX | LOCK_NB)`` (or ``LOCK_SH`` in
   read-only verify mode) on a sidecar lockfile next to ``~/.claude.json``.
2. Retries up to 3× with exponential backoff (50ms / 200ms / 800ms) if
   the lock is contended; logs a warning and returns drift status
   without writing if still held after retries.
3. Backs up the file (``~/.claude.json.bak``), parses, mutates in-memory,
   writes to a temp file, then ``os.rename`` (atomic on POSIX) to the
   final path.
4. Releases the lock in ``try/finally``.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

CLAUDE_CONFIG_PATH = Path.home() / ".claude.json"
CLAUDE_CONFIG_LOCK_PATH = Path.home() / ".claude.json.lock"
CLAUDE_CONFIG_BACKUP_PATH = Path.home() / ".claude.json.bak"

MCP_SERVER_KEY = "memory"
MCP_SERVER_COMMAND = "python3"
MCP_SERVER_MODULE = "mcp_servers.memory_server"

# Lock retry schedule per cycle-2 C4 — in milliseconds.
_LOCK_RETRY_BACKOFF_MS = (50, 200, 800)


@dataclass
class McpMemoryResult:
    """Result of a verify_memory_mcp() invocation."""

    ok: bool  # True if entry is present + correct (after any write)
    action: str  # "ok", "installed", "repaired", "drift_detected", "skipped", "failed"
    message: str = ""


def _resolve_repo_root() -> str | None:
    """Return the absolute repo root via ``git rev-parse --show-toplevel``.

    Falls back to the parent directory of this file if git is unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            root = result.stdout.strip()
            if root:
                return root
    except Exception:
        pass
    # Fallback: this file is at <repo>/scripts/update/mcp_memory.py
    fallback = Path(__file__).resolve().parent.parent.parent
    return str(fallback)


def _expected_entry(repo_root: str) -> dict:
    """Return the canonical shape of the memory MCP entry."""
    return {
        "type": "stdio",
        "command": MCP_SERVER_COMMAND,
        "args": ["-m", MCP_SERVER_MODULE],
        "env": {"PYTHONPATH": repo_root},
    }


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
    if actual_env.get("PYTHONPATH") != expected_env["PYTHONPATH"]:
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
    """Backup → tmp → rename. Returns True on success."""
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


def verify_memory_mcp(*, write: bool = True) -> McpMemoryResult:
    """Verify (and optionally repair) the memory MCP registration.

    Args:
        write: When True (full / cron modes), write the corrected entry
            if missing or drifted. When False (verify mode), only report
            drift — never write.

    Returns:
        McpMemoryResult with `ok` (True iff the entry is present + correct
        after the call), `action` (string label for logging), and `message`.
    """
    repo_root = _resolve_repo_root()
    if not repo_root:
        return McpMemoryResult(ok=False, action="failed", message="could not resolve repo root")

    expected = _expected_entry(repo_root)
    fd = _acquire_lock(read_only=not write)
    if fd is None:
        return McpMemoryResult(
            ok=False,
            action="skipped",
            message=("~/.claude.json lock contended after retries; next /update run will retry"),
        )

    try:
        config = _read_config()
        if config is None or not isinstance(config, dict):
            if not write:
                return McpMemoryResult(
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
        if _entry_matches(actual, expected):
            return McpMemoryResult(ok=True, action="ok", message="memory MCP registration: ok")

        if not write:
            return McpMemoryResult(
                ok=False,
                action="drift_detected",
                message=("memory MCP entry missing or drifted; run /update to repair"),
            )

        # Write mode: install or repair.
        servers[MCP_SERVER_KEY] = expected
        config["mcpServers"] = servers
        if not _write_config_atomic(config):
            return McpMemoryResult(
                ok=False, action="failed", message="failed to write ~/.claude.json"
            )

        action = "installed" if actual is None else "repaired"
        return McpMemoryResult(
            ok=True,
            action=action,
            message=f"memory MCP registration: {action} (PYTHONPATH={repo_root})",
        )

    finally:
        _release_lock(fd)


def check_ollama_for_titles(host: str = "http://localhost:11434") -> tuple[bool, str]:
    """Best-effort ping of Ollama for the title-gen worker.

    Returns ``(available, message)``. Failures are non-fatal — the
    title generator silently degrades to category-only stubs when
    Ollama is unreachable.
    """
    try:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(f"{host.rstrip('/')}/api/tags")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return False, f"Ollama returned non-JSON at {host}"

        try:
            from config.settings import settings  # noqa: PLC0415

            gen_model = settings.models.ollama_generation_model
        except Exception:
            gen_model = "gemma4:31b-cloud"

        # Cloud generation tags are hosted pointers — not present in local
        # /api/tags. Treat a configured cloud tag as available (the real check
        # is cloud-signin, surfaced by /update); only verify local tags by name.
        try:
            from config.models import _is_cloud_tag

            is_cloud = _is_cloud_tag(gen_model)
        except Exception:
            is_cloud = gen_model.endswith(":cloud") or gen_model.endswith("-cloud")
        if is_cloud:
            return True, f"Ollama generation: {gen_model} (cloud)"

        models = data.get("models", []) or []
        names = {(m.get("name") or "").split(":")[0] for m in models if isinstance(m, dict)}
        wanted = gen_model.split(":")[0]
        if wanted in names:
            return True, f"Ollama: {gen_model} available"
        return False, (
            f"Ollama up but {gen_model} not pulled — memory titles will fall back to category-only"
        )
    except (urllib.error.URLError, TimeoutError, OSError):
        return False, "Ollama not running — memory titles disabled (graceful fallback)"
    except Exception as e:  # noqa: BLE001
        return False, f"Ollama check failed: {type(e).__name__}: {e}"
