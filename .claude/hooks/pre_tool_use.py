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
# This CLI hook runs in every `claude` subprocess. Session-runner role turns
# carry `AGENT_SESSION_ID` in their env (the executor injects it), but other
# `claude` processes running this hook (local TUI sessions, ad-hoc
# subprocesses) may not — there,
# `agent.hooks.liveness_writers.record_tool_boundary` would silently no-op.
# Resolve the AgentSession the same way
# `post_tool_use.py::_update_agent_session` does: read the per-session
# sidecar JSON directly (no popoto import needed for the sidecar itself),
# then look up the AgentSession record via its `agent_session_id`.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Per-session cooldown window (seconds) bounding the AgentSession liveness
# write rate. Mirrors agent/hooks/liveness_writers.COOLDOWN_WINDOW_SEC.
_LIVENESS_COOLDOWN_SEC = 5.0


def _sidecar_dir(session_id: str) -> Path:
    """Return the per-session sidecar directory (mirrors post_tool_use.py)."""
    return _REPO_ROOT / "data" / "sessions" / session_id


def _liveness_cooldown_ok(session_id: str, now: float) -> bool:
    """File-based per-session cooldown mirroring ``liveness_writers.is_in_cooldown``.

    The CLI hooks run as a fresh process per tool call, so the SDK-path's
    in-memory cooldown cannot coalesce writes across invocations. Persist the
    last-write timestamp in the session sidecar dir so the 5s window bounds the
    AgentSession Redis write rate for EVERY CLI-hook session.
    Without this gate the new pre-hook write would fire uncooled system-wide on
    every tool call for every CLI-hook session.

    Returns True if a write is allowed (and stamps the file), False if still
    inside the cooldown window. Fail-open on IO error so a wedge is never masked
    by a cooldown-file problem.
    """
    path = _sidecar_dir(session_id) / "tool_liveness_cooldown"
    prev: float | None
    try:
        prev = float(path.read_text().strip())
    except (OSError, ValueError):
        prev = None
    if prev is not None and (now - prev) < _LIVENESS_COOLDOWN_SEC:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(repr(now))
    except OSError:
        pass
    return True


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
    AgentSession so the #1270 tool-timeout tier loop arms for CLI-hook
    sessions (issue #1843, Gap A).

    Mirrors ``post_tool_use.py::_update_agent_session``'s sidecar resolution —
    NOT ``agent.hooks.liveness_writers.record_tool_boundary``, which resolves
    via ``os.environ["AGENT_SESSION_ID"]`` (which can be unset in the CLI-hook
    child env and would silently no-op).

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

        # Cooldown gate BEFORE the popoto import so coalesced calls stay cheap.
        if not _liveness_cooldown_ok(session_id, time.time()):
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
        # Declared-timeout capture (issue #2145): Bash's `timeout` param is
        # MILLISECONDS (max 600000). Mirrors the SDK hook's
        # `_extract_declared_timeout_s`; inlined because importing
        # agent.hooks.pre_tool_use would pull claude_agent_sdk into the CLI
        # hook. All three fields ride one save so the pair can't split-brain.
        declared_s = None
        tool_input = hook_input.get("tool_input", {})
        if tool_name == "Bash" and isinstance(tool_input, dict):
            raw = tool_input.get("timeout")
            if (
                not isinstance(raw, bool)
                and isinstance(raw, (int, float))
                and raw == raw
                and raw > 0
            ):
                declared_s = float(raw) / 1000.0
        agent_session.current_tool_timeout_s = declared_s
        agent_session.save(
            update_fields=["current_tool_name", "last_tool_use_at", "current_tool_timeout_s"]
        )
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
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,  # timeout-guard: allow — CLI hook, settings unavailable
            )
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


def _resolve_cli_session(hook_input: dict):
    """Resolve the AgentSession for the CLI hook via the sidecar path (#1821).

    Uses the EXACT resolution path ``post_tool_use.py::_update_agent_session``
    uses: read the per-session sidecar JSON, then ``AgentSession.get_by_id`` on
    its ``agent_session_id``.

    Returns the session, or ``None`` for a GENUINE no-session (no session_id / no
    sidecar / no agent_session_id / ``get_by_id`` returns None). RAISES on an
    infra/resolution error (Redis raised, ``get_by_id`` threw) — the caller
    catches it for the loud "backstop BLIND" path.
    """
    session_id = hook_input.get("session_id", "")
    if not session_id:
        return None
    sidecar = _load_agent_session_sidecar(session_id)
    agent_session_id = sidecar.get("agent_session_id")
    if not agent_session_id:
        return None
    from models.agent_session import AgentSession

    # get_by_id → None is a genuine no-session (silent allow); a raised
    # exception propagates to the caller as an infra error.
    return AgentSession.get_by_id(agent_session_id)


def _enforce_tool_budget(hook_input: dict) -> None:
    """Synchronous per-tool budget backstop for the CLI-hook surface (#1821).

    On an over-budget session, prints the deny reason to stderr and
    ``sys.exit(2)`` (Claude Code's block convention). The ``sys.exit(2)`` raises
    ``SystemExit`` — NOT an ``Exception`` subclass — so it propagates through
    ``main()``'s module-level ``except Exception`` wrapper at the bottom of this
    file and denies the tool. A bug INSIDE this check raises a normal
    ``Exception`` → caught by that wrapper → logged → exit 0 → fails OPEN. The
    deny path therefore MUST live inside ``main()`` and MUST NOT be wrapped in a
    bare ``except``/``except BaseException`` (which would swallow the exit-2).

    Fails OPEN on any resolution/infra error (allow), splitting genuine
    no-session (silent) from an infra error (loud WARNING +
    ``resolution_errors`` counter).
    """
    # Resolution split: genuine no-session vs infra error. This is a plain
    # if/try on the RESOLUTION only — it returns normally (allow); it never
    # intercepts the exit-2 the deny branch raises after a successful resolution.
    try:
        session = _resolve_cli_session(hook_input)
    except Exception as e:
        try:
            from agent.tool_budget import _project_key_env, record_resolution_error

            record_resolution_error(_project_key_env(), e, surface="cli-hook")
        except Exception:
            print(
                f"HOOK WARNING: tool-budget backstop BLIND (resolution error): {e}",
                file=sys.stderr,
            )
        return  # fail open
    if session is None:
        return  # genuine no-session → silent allow

    # Successful resolution: evaluate + actuate. A bug HERE raises Exception →
    # swallowed by main()'s module-level wrapper → exit 0 (fail open). The deny
    # below is sys.exit(2), which propagates (SystemExit is not an Exception).
    from agent.tool_budget import evaluate_tool_budget, record_budget_trip

    verdict = evaluate_tool_budget(session)
    if verdict.allow:
        return
    # DENY. Surface first (fail-quiet), then block — surfacing NEVER flips it.
    record_budget_trip(session, verdict)
    print(f"HOOK BLOCK: {verdict.reason}", file=sys.stderr)
    sys.exit(2)


def main():
    hook_input = read_hook_input()
    if not hook_input:
        return

    # Fix #6 (issue #1821): synchronous per-tool budget backstop. Runs first so
    # it fires inline even when every background health loop is frozen. A genuine
    # DENY raises sys.exit(2) (propagates through the module-level wrapper); a
    # bug inside the check fails OPEN. On an ALLOW this returns and the rest of
    # main() runs normally — including the #1849 _record_tool_start liveness
    # write below, which stays intact and firing for every allowed tool call.
    _enforce_tool_budget(hook_input)

    # Capture git baseline on first tool call (for stop hook comparison)
    capture_git_baseline_once(hook_input)

    # Liveness (issue #1843, Gap A): stamp current_tool_name/last_tool_use_at
    # on the sidecar-resolved AgentSession so the #1270 tool-timeout tier loop
    # arms for CLI-hook sessions.
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
