#!/usr/bin/env python3
"""Hook: PreToolUse - Log before tool execution."""

import subprocess
import sys
import time

# Standalone script — sys.path mutation is safe (never imported as library)
# Add utils to path
sys.path.insert(0, str(__file__).rsplit("/", 1)[0])

from utils.constants import (
    append_to_log,
    ensure_session_log_dir,
    get_project_dir,
    get_session_id,
    read_hook_input,
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
    main()
