#!/usr/bin/env python3
"""Hook: Stop - Save session metadata and optionally copy transcript."""

import argparse
import shutil
import sys
from pathlib import Path

# Add utils to path
sys.path.insert(0, str(__file__).rsplit("/", 1)[0])

from utils.constants import (
    ensure_session_log_dir,
    get_session_id,
    read_hook_input,
    write_json_log,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--chat", action="store_true", help="Copy transcript to session dir"
    )
    args = parser.parse_args()

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

    # Optionally copy transcript
    if args.chat:
        transcript_path = hook_input.get("transcript_path")
        if transcript_path:
            src = Path(transcript_path)
            if src.exists():
                dst = session_dir / "chat.json"
                shutil.copy2(src, dst)


if __name__ == "__main__":
    main()
