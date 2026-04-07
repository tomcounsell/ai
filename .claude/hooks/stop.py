#!/usr/bin/env python3
"""Hook: Stop - Save session metadata and back up JSONL transcript."""

import argparse
import json
import shutil
import sys
from pathlib import Path

# Standalone script — sys.path mutation is safe (never imported as library)
# Add project root to path for model imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Add utils to path
sys.path.insert(0, str(__file__).rsplit("/", 1)[0])

from hook_utils.constants import (  # noqa: E402
    ensure_session_log_dir,
    get_session_id,
    read_hook_input,
    write_json_log,
)


def _check_sdlc_stage_progress(session_id: str) -> None:
    """Warn if an SDLC-classified session completed with no stage progress."""
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if not sessions:
            return

        session = sessions[0]

        # Check if this was an SDLC-classified session
        classification = getattr(session, "classification_type", None)
        if classification != "sdlc":
            return

        # Check for stage progress via stage_states (stored as JSON string)
        stage_states_raw = getattr(session, "stage_states", None)
        if isinstance(stage_states_raw, str):
            try:
                stage_states = json.loads(stage_states_raw)
            except (json.JSONDecodeError, TypeError):
                stage_states = None
        else:
            stage_states = stage_states_raw
        has_state = stage_states and isinstance(stage_states, dict) and len(stage_states) > 0

        if not has_state:
            print(
                f"SDLC WARNING: Session {session_id} classified as SDLC "
                f"but completed with no stage progress",
                file=sys.stderr,
            )
    except Exception:
        pass  # Non-fatal: hook must not break on Redis/model errors


def _update_agent_session_log_path(session_id: str, jsonl_path: str) -> None:
    """Store the JSONL backup path in AgentSession.log_path."""
    try:
        from models.agent_session import AgentSession

        sessions = list(AgentSession.query.filter(session_id=session_id))
        if sessions:
            s = sessions[0]
            s.log_path = jsonl_path
            s.save()
    except Exception:
        pass  # Non-fatal: hook must not break on Redis/model errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Copy transcript to session dir (legacy flag, now always copies)",
    )
    parser.parse_args()  # consume args (--chat is legacy, always copies now)

    hook_input = read_hook_input()
    if not hook_input:
        return

    session_id = get_session_id(hook_input)
    session_dir = ensure_session_log_dir(session_id)

    # Save session metadata
    metadata = {
        "event": "stop",
        "session_id": session_id,
        "cwd": hook_input.get("cwd", ""),
        "stop_reason": hook_input.get("stop_reason", "unknown"),
    }
    write_json_log(session_dir, "stop.json", metadata)

    # Check for SDLC sessions that completed without stage progress
    _check_sdlc_stage_progress(session_id)

    # Back up JSONL transcript (always, regardless of --chat flag)
    transcript_path = hook_input.get("transcript_path")
    if transcript_path:
        src = Path(transcript_path)
        if src.exists():
            dst = session_dir / "transcript.jsonl"
            shutil.copy2(src, dst)
            _update_agent_session_log_path(session_id, str(dst))

    # Complete AgentSession lifecycle tracking
    _complete_agent_session(session_id, hook_input)

    # Memory extraction -- run Haiku extraction and outcome detection
    # on session transcript. Fails silently on any error.
    _run_memory_extraction(session_id, transcript_path)

    # Post-merge learning extraction (if a PR was merged during this session)
    _run_post_merge_extraction(session_id)


def _complete_agent_session(session_id: str, hook_input: dict) -> None:
    """Mark the AgentSession as completed or failed based on stop_reason.

    Reads the agent_session_job_id from the sidecar file, updates the
    AgentSession status, completed_at timestamp, and log_path.

    Fails silently -- session completion errors never block stop.
    """
    try:
        import time

        from hook_utils.memory_bridge import load_agent_session_sidecar

        sidecar = load_agent_session_sidecar(session_id)
        job_id = sidecar.get("agent_session_job_id")
        if not job_id:
            return

        from models.agent_session import AgentSession

        # Popoto AutoKeyField .get() requires a key object, not a raw string.
        # Use filter on session_id instead.
        sidecar_session_id = f"local-{session_id}"
        matches = list(AgentSession.query.filter(session_id=sidecar_session_id))
        if not matches:
            return
        agent_session = matches[0]

        stop_reason = hook_input.get("stop_reason", "unknown")
        if stop_reason in ("error", "crash"):
            agent_session.status = "failed"
        else:
            agent_session.status = "completed"

        agent_session.completed_at = time.time()
        agent_session.save()
    except Exception:
        pass  # Silent failure -- never block session stop


def _run_post_merge_extraction(session_id: str) -> None:
    """Run post-merge learning extraction if a PR was merged during this session.

    Checks the agent session sidecar for the merge_detected flag set by
    PostToolUse when a gh pr merge command is detected.

    Fails silently -- merge learning failures never block session stop.
    """
    try:
        from hook_utils.memory_bridge import load_agent_session_sidecar, post_merge_extract

        sidecar = load_agent_session_sidecar(session_id)
        if not sidecar.get("merge_detected"):
            return

        pr_number = sidecar.get("merged_pr_number")
        if pr_number:
            post_merge_extract(pr_number)
    except Exception:
        pass  # Silent failure -- never block session stop


def _run_memory_extraction(session_id: str, transcript_path: str | None) -> None:
    """Run post-session memory extraction and outcome detection.

    Calls memory_bridge.extract() which handles Haiku extraction
    and outcome detection for injected thoughts. Also cleans up
    session sidecar files.

    Fails silently -- memory errors never block session stop.
    """
    try:
        from hook_utils.memory_bridge import extract

        extract(session_id, transcript_path)
    except Exception:
        pass  # Silent failure -- never block session stop


if __name__ == "__main__":
    main()
