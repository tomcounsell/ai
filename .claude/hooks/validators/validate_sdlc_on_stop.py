#!/usr/bin/env python3
"""Hook: Stop validator — enforce SDLC quality gates at session end.

Reads the session's sdlc_state.json. If code was modified this session,
all three quality commands (pytest, ruff, ruff-format) must have been run.

Exit codes:
  0 — pass (non-code session, or all gates satisfied)
  2 — block (code modified but quality gates not all run)

Escape hatch:
  Set SKIP_SDLC=1 to bypass enforcement. A warning is logged to stderr.
"""

import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

# Standalone script — sys.path mutation is safe (never imported as library)
# Add hooks dir to path so utils.constants is importable when run directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hook_utils.constants import get_data_sessions_dir  # noqa: E402


def get_sdlc_state_path(session_id: str) -> Path:
    """Return the path to the SDLC state file for a given session."""
    return get_data_sessions_dir() / session_id / "sdlc_state.json"


def _baseline_path(session_id: str) -> Path:
    """Return the path to the git-dirty baseline snapshot for a session."""
    return get_data_sessions_dir() / session_id / "git_baseline.json"


def save_baseline(session_id: str) -> None:
    """Snapshot currently dirty code files so the stop hook can diff against them.

    Called once at session start (or lazily on first stop-hook invocation).
    """
    import subprocess

    code_exts = (".py", ".js", ".ts")
    dirty: list[str] = []

    for cmd in (["git", "diff", "--name-only"], ["git", "diff", "--name-only", "--cached"]):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        for f in result.stdout.strip().split("\n"):
            if f and any(f.endswith(ext) for ext in code_exts) and f not in dirty:
                dirty.append(f)

    bp = _baseline_path(session_id)
    bp.parent.mkdir(parents=True, exist_ok=True)
    with open(bp, "w") as fh:
        json.dump(dirty, fh)


def _load_baseline(session_id: str) -> set[str] | None:
    """Load the baseline snapshot. Returns None if no baseline was captured."""
    bp = _baseline_path(session_id)
    if not bp.exists():
        return None
    try:
        with open(bp) as fh:
            return set(json.load(fh))
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Quality gate logic
# ---------------------------------------------------------------------------

_QUALITY_RUN_HINTS = {
    "pytest": "pytest tests/",
    "ruff": "python -m ruff check .",
    "ruff-format": "python -m ruff format --check .",
}

_ERROR_TEMPLATE = """\
SDLC Quality Gate: Code was modified this session but not all quality checks were run.

Missing:
{missing_lines}
Please run the missing checks before completing this session.
Set SKIP_SDLC=1 to bypass in genuine emergencies (logs warning).
"""


def check_sdlc_quality_gate(session_id: str) -> str | None:
    """Check SDLC quality gates for the given session.

    Returns:
        None   — all good, session may complete (exit 0)
        str    — error message describing what's missing (exit 2)

    The SKIP_SDLC=1 escape hatch is handled here: if set, always return None
    but emit a warning to stderr.
    """
    # Fast path: non-code session — no state file means nothing to enforce
    state_path = get_sdlc_state_path(session_id)
    if not state_path.exists():
        # Fallback: detect code changes on main without SDLC tracking.
        # Only flag files that became dirty DURING this session by comparing
        # against a baseline snapshot captured at session start.
        try:
            import subprocess

            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            if branch == "main":
                code_exts = (".py", ".js", ".ts")
                code_files = []

                # Check 1: unstaged changes in working tree
                wt_diff = subprocess.run(
                    ["git", "diff", "--name-only"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout.strip()
                if wt_diff:
                    code_files.extend(
                        f
                        for f in wt_diff.split("\n")
                        if f and any(f.endswith(ext) for ext in code_exts)
                    )

                # Check 2: staged but not yet committed changes
                staged_diff = subprocess.run(
                    ["git", "diff", "--name-only", "--cached"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout.strip()
                if staged_diff:
                    code_files.extend(
                        f
                        for f in staged_diff.split("\n")
                        if f and any(f.endswith(ext) for ext in code_exts) and f not in code_files
                    )

                # Filter out files that were already dirty at session start.
                # If no baseline was captured, assume all dirty files are
                # pre-existing (fail open — can't prove this session caused them).
                baseline = _load_baseline(session_id)
                if baseline is None:
                    code_files = []  # No baseline → assume all pre-existing
                else:
                    code_files = [f for f in code_files if f not in baseline]

                if code_files:
                    if os.environ.get("SKIP_SDLC") == "1":
                        print(
                            "WARNING: SKIP_SDLC=1 set — bypassing SDLC gate. "
                            f"Modified on main: {', '.join(code_files[:5])}",
                            file=sys.stderr,
                        )
                        return None
                    # Downgrade to warning (exit 0) — no SDLC state means
                    # this wasn't a tracked code session. Warn, don't block.
                    print(
                        "SDLC Warning: Code files modified on main "
                        "without SDLC tracking.\n"
                        f"Files: {', '.join(code_files[:5])}\n"
                        "Consider using /sdlc to create a branch and follow the pipeline.",
                        file=sys.stderr,
                    )
                    return None
        except Exception:
            pass  # Fail open
        return None

    # Load state
    try:
        with open(state_path) as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupt/unreadable state — fail open to avoid blocking the session
        return None

    # No code modified → no enforcement needed
    if not state.get("code_modified", False):
        return None

    # Determine which quality commands are missing
    quality_commands = state.get("quality_commands", {})
    required = ("pytest", "ruff", "ruff-format")
    missing = [cmd for cmd in required if not quality_commands.get(cmd, False)]

    if not missing:
        return None  # All gates passed

    # Handle SKIP_SDLC escape hatch
    if os.environ.get("SKIP_SDLC") == "1":
        print(
            f"WARNING: SKIP_SDLC=1 set — bypassing SDLC quality gate for session "
            f"'{session_id}'. Missing: {', '.join(missing)}",
            file=sys.stderr,
        )
        return None

    # Build human-readable error listing missing checks with run hints
    missing_lines = "\n".join(f"  - {cmd} (run: {_QUALITY_RUN_HINTS[cmd]})" for cmd in missing)
    return _ERROR_TEMPLATE.format(missing_lines=missing_lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def read_session_id_from_stdin() -> str:
    """Read the session_id from stdin JSON (Stop hook protocol)."""
    try:
        raw = sys.stdin.read()
        if raw.strip():
            data = json.loads(raw)
            return data.get("session_id", "unknown")
    except (json.JSONDecodeError, OSError):
        pass
    return "unknown"


def main() -> None:
    session_id = read_session_id_from_stdin()
    error_message = check_sdlc_quality_gate(session_id)

    if error_message is None:
        sys.exit(0)
    else:
        print(error_message, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
