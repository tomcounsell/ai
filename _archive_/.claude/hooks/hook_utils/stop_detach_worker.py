#!/usr/bin/env python3
"""Detached Stop-extraction worker (build-stop-detach, Phase A).

Runs the memory/tui/post-merge extraction that used to execute inline on
`stop.py`'s critical path -- see docs/plans/hook-registration-manifest-dispatcher.md
(spike-3). `stop.py` persists the transcript synchronously, spawns this
script as a real detached subprocess (`start_new_session=True`, redirected
streams -- never a thread, so `stop.py` exiting immediately cannot kill it),
and exits 0 within milliseconds. This process:

1. Reads the already-persisted, stable `transcript.jsonl` (no race -- see
   Race 1 in the plan).
2. Runs Haiku extraction + outcome detection, TUI interaction capture, and
   post-merge learning extraction.
3. Enforces its OWN self-deadline (`HOOK_DETACH_DEADLINE_SECONDS`) via
   SIGALRM. The raised exception subclasses BaseException (NOT Exception) so
   memory_bridge's broad `except Exception` handlers cannot swallow it --
   this is load-bearing, see spike-3.
4. Always releases its concurrency-cap lock slot (try/finally), and always
   logs failures/deadlines to the absolute hooks log -- never silent.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

# Standalone script -- sys.path mutation is safe (never imported as library).
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hook_utils.detach_lock import (  # noqa: E402
    get_deadline_seconds,
    log_hook_absolute,
    release_detach_slot,
)

_HOOK_NAME = "stop_detach_worker"


class DetachDeadlineExceeded(BaseException):
    """Raised by the SIGALRM handler when the self-deadline elapses.

    Deliberately subclasses BaseException, not Exception: a broad
    `except Exception` inside memory_bridge (or anywhere else in the
    extraction call chain) must NOT be able to swallow this -- the worker
    needs to observe the deadline and log it, not silently keep running
    past its own wall. See spike-3 in the plan.
    """


def _raise_deadline(signum, frame) -> None:  # noqa: ARG001
    raise DetachDeadlineExceeded("HOOK_DETACH_DEADLINE_SECONDS exceeded")


def _run_memory_extraction(session_id: str, transcript_path: str | None, cwd: str) -> None:
    """Run Haiku extraction + outcome detection. Logs (does not swallow) failures."""
    try:
        from hook_utils.memory_bridge import extract

        extract(session_id, transcript_path, cwd=cwd)
    except DetachDeadlineExceeded:
        raise
    except Exception as e:
        log_hook_absolute(_HOOK_NAME, f"memory extraction failed (non-fatal): {e}")


def _run_tui_interaction_capture(session_id: str, cwd: str) -> None:
    """Summarize the session's TUI interaction shape into one Memory. Logs failures."""
    try:
        from agent.tui_interaction_capture import summarize_and_store
        from hook_utils.memory_bridge import _get_project_key

        project_key = _get_project_key(cwd)
        summarize_and_store(session_id, project_key)
    except DetachDeadlineExceeded:
        raise
    except Exception as e:
        log_hook_absolute(_HOOK_NAME, f"tui interaction capture failed (non-fatal): {e}")


def _run_post_merge_extraction(session_id: str, cwd: str) -> None:
    """Run post-merge learning extraction if a PR was merged this session. Logs failures."""
    try:
        from hook_utils.memory_bridge import load_agent_session_sidecar, post_merge_extract

        sidecar = load_agent_session_sidecar(session_id)
        if not sidecar.get("merge_detected"):
            return

        pr_number = sidecar.get("merged_pr_number")
        if pr_number:
            post_merge_extract(pr_number, cwd=cwd)
    except DetachDeadlineExceeded:
        raise
    except Exception as e:
        log_hook_absolute(_HOOK_NAME, f"post-merge extraction failed (non-fatal): {e}")


def run_extraction(session_id: str, transcript_path: str | None, cwd: str) -> None:
    """Run all three extraction steps in sequence, under the caller's deadline."""
    _run_memory_extraction(session_id, transcript_path, cwd)
    _run_tui_interaction_capture(session_id, cwd)
    _run_post_merge_extraction(session_id, cwd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--transcript-path", default="")
    parser.add_argument("--cwd", default="")
    args = parser.parse_args(argv)

    slot_path = os.environ.get("HOOK_DETACH_SLOT_PATH")
    deadline_seconds = get_deadline_seconds()
    transcript_path = args.transcript_path or None

    have_alarm = hasattr(signal, "SIGALRM")
    if have_alarm:
        signal.signal(signal.SIGALRM, _raise_deadline)
        signal.alarm(deadline_seconds)

    try:
        run_extraction(args.session_id, transcript_path, args.cwd)
        return 0
    except DetachDeadlineExceeded:
        log_hook_absolute(
            _HOOK_NAME,
            f"deadline-exceeded: session={args.session_id} deadline_s={deadline_seconds}",
        )
        return 1
    except Exception as e:  # noqa: BLE001 -- worker's last-resort catch-all
        log_hook_absolute(_HOOK_NAME, f"worker failed (non-fatal): {e}")
        return 1
    finally:
        if have_alarm:
            signal.alarm(0)
        release_detach_slot(slot_path)


if __name__ == "__main__":
    sys.exit(main())
