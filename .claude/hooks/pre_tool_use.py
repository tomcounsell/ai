#!/usr/bin/env python3
"""Hook: PreToolUse - Log before tool execution and mark SDLC stages in_progress."""

import os
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

# Map SDLC skill names to their pipeline stage
SKILL_TO_STAGE = {
    "sdlc": "ISSUE",
    "do-plan": "PLAN",
    "do-build": "BUILD",
    "do-test": "TEST",
    "do-pr-review": "REVIEW",
    "do-patch": None,
    "do-docs": "DOCS",
    "do-docs-audit": None,
}


def mark_stage_in_progress(hook_input: dict) -> None:
    """Mark an SDLC stage as in_progress when its skill starts."""
    tool_name = hook_input.get("tool_name", "")
    if tool_name != "Skill":
        return

    tool_input = hook_input.get("tool_input", {})
    skill_name = tool_input.get("skill", "")

    if skill_name not in SKILL_TO_STAGE:
        return

    stage = SKILL_TO_STAGE[skill_name]
    if stage is None:
        return

    session_id = hook_input.get("session_id", "")
    if not session_id:
        return

    project_dir = get_project_dir()
    cmd = [
        sys.executable,
        "-m",
        "tools.session_progress",
        "--session-id",
        session_id,
        "--stage",
        stage,
        "--status",
        "in_progress",
    ]

    try:
        subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            timeout=5,
            env={**os.environ, "PYTHONPATH": str(project_dir)},
        )
    except Exception:
        pass  # Fire and forget


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
    except Exception:
        pass  # Fire and forget


def main():
    hook_input = read_hook_input()
    if not hook_input:
        return

    # Capture git baseline on first tool call (for stop hook comparison)
    capture_git_baseline_once(hook_input)

    # Mark SDLC stages in_progress when skills start
    mark_stage_in_progress(hook_input)

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
