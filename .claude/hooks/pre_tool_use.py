#!/usr/bin/env python3
"""Hook: PreToolUse - Log before tool execution."""

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# Standalone script — sys.path mutation is safe (never imported as library)
# Add utils to path
sys.path.insert(0, str(__file__).rsplit("/", 1)[0])

from hook_utils.constants import (
    append_to_log,
    ensure_session_log_dir,
    get_project_dir,
    get_session_id,
    read_hook_input,
)

# ---------------------------------------------------------------------------
# Sidecar-resolved AgentSession liveness write (issue #1843, Gap A)
#
# Granite's PM/Dev `claude` PTY children run this CLI hook (not the SDK
# in-process hooks in agent/hooks/pre_tool_use.py), so `AGENT_SESSION_ID` is
# unset in their env — `agent.hooks.liveness_writers.record_tool_boundary`
# would silently no-op. Resolve the AgentSession the same way
# `post_tool_use.py::_update_agent_session` does: read the per-session
# sidecar JSON directly (no popoto import needed for the sidecar itself),
# then look up the AgentSession record via its `agent_session_id`.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _sidecar_dir(session_id: str) -> Path:
    """Return the per-session sidecar directory (mirrors post_tool_use.py)."""
    return _REPO_ROOT / "data" / "sessions" / session_id


def _load_agent_session_sidecar(session_id: str) -> dict:
    """Read the agent_session.json sidecar directly.

    Behaviour-identical to ``post_tool_use.py::_load_agent_session_sidecar`` —
    returns {} if missing/corrupt.
    """
    path = _sidecar_dir(session_id) / "agent_session.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _record_tool_start(hook_input: dict) -> None:
    """Stamp ``current_tool_name`` / ``last_tool_use_at`` on the sidecar-resolved
    AgentSession so the #1270 tool-timeout tier loop arms for granite PM/Dev
    PTY children (issue #1843, Gap A).

    Mirrors ``post_tool_use.py::_update_agent_session``'s sidecar resolution —
    NOT ``agent.hooks.liveness_writers.record_tool_boundary``, which resolves
    via ``os.environ["AGENT_SESSION_ID"]`` (unset in the granite child env and
    would silently no-op).

    ``last_tool_use_at`` MUST be a ``datetime`` (never ``time.time()``) —
    ``session_health.py::_check_tool_timeout`` gates on
    ``isinstance(last_at, datetime)`` and silently no-ops on a float.

    Fails silently (never blocks or crashes the tool call) but logs a
    warning to stderr on any resolution/save failure.
    """
    session_id = hook_input.get("session_id", "")
    if not session_id:
        return
    try:
        sidecar = _load_agent_session_sidecar(session_id)
        agent_session_id = sidecar.get("agent_session_id")
        if not agent_session_id:
            return

        from models.agent_session import AgentSession

        agent_session = None
        try:
            agent_session = AgentSession.get_by_id(agent_session_id)
        except Exception:
            agent_session = None

        if agent_session is None:
            # Legacy fallback: reconstruct local-{session_id} for direct-CLI
            # paths that still create local-* records.
            local_sid = f"local-{session_id}"
            try:
                matches = list(AgentSession.query.filter(session_id=local_sid))
            except Exception:
                matches = []
            if not matches:
                return
            agent_session = matches[0]

        tool_name = hook_input.get("tool_name", "unknown")
        agent_session.current_tool_name = tool_name
        agent_session.last_tool_use_at = datetime.now(tz=UTC)
        agent_session.save(update_fields=["current_tool_name", "last_tool_use_at"])
    except Exception as e:
        print(
            f"HOOK WARNING: Failed to record tool-start liveness for {session_id}: {e}",
            file=sys.stderr,
        )


def capture_git_baseline_once(hook_input: dict) -> None:
    """Capture a snapshot of dirty code files on the first tool call per session.

    This baseline is used by the stop hook (validate_sdlc_on_stop.py) to
    distinguish pre-existing dirty files from files modified during the session.
    """
    session_id = hook_input.get("session_id", "")
    if not session_id:
        return

    project_dir = get_project_dir()
    baseline_dir = project_dir / "data" / "sessions" / session_id
    baseline_path = baseline_dir / "git_baseline.json"

    # Only capture once per session
    if baseline_path.exists():
        return

    try:
        import json

        code_exts = (".py", ".js", ".ts")
        dirty: list[str] = []

        for cmd in (["git", "diff", "--name-only"], ["git", "diff", "--name-only", "--cached"]):
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            for f in result.stdout.strip().split("\n"):
                if f and any(f.endswith(ext) for ext in code_exts) and f not in dirty:
                    dirty.append(f)

        baseline_dir.mkdir(parents=True, exist_ok=True)
        with open(baseline_path, "w") as fh:
            json.dump(dirty, fh)
    except Exception as e:
        print(
            f"HOOK WARNING: Failed to capture git baseline for {session_id}: {e}",
            file=sys.stderr,
        )


def main():
    hook_input = read_hook_input()
    if not hook_input:
        return

    # Capture git baseline on first tool call (for stop hook comparison)
    capture_git_baseline_once(hook_input)

    # Liveness (issue #1843, Gap A): stamp current_tool_name/last_tool_use_at
    # on the sidecar-resolved AgentSession so the #1270 tool-timeout tier loop
    # arms for granite PM/Dev PTY children.
    _record_tool_start(hook_input)

    session_id = get_session_id(hook_input)
    session_dir = ensure_session_log_dir(session_id)

    tool_name = hook_input.get("tool_name", "unknown")
    tool_input = hook_input.get("tool_input", {})

    entry = {
        "event": "pre_tool_use",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "start_time": time.time(),
    }

    append_to_log(session_dir, "tool_use.jsonl", entry)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        from hook_utils.constants import log_hook_error

        log_hook_error("pre_tool_use", str(e))
