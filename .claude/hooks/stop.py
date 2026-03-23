#!/usr/bin/env python3
"""Hook: Stop - Save session metadata and back up JSONL transcript."""

import argparse
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

        # Check for stage progress
        sdlc_stages = getattr(session, "sdlc_stages", None)
        stage_states = getattr(session, "stage_states", None)

        has_stages = sdlc_stages and (isinstance(sdlc_stages, dict) and len(sdlc_stages) > 0)
        has_state = stage_states and (isinstance(stage_states, dict) and len(stage_states) > 0)

        if not has_stages and not has_state:
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


if __name__ == "__main__":
    main()
