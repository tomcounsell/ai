#!/usr/bin/env python3
"""Hook: Stop - Save session metadata and back up JSONL transcript."""

import argparse
import json
import os
import shutil
import subprocess
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
from hook_utils.detach_lock import (  # noqa: E402
    get_absolute_log_path,
    log_hook_absolute,
    try_reserve_detach_slot,
)


def _check_sdlc_stage_progress(session_id: str) -> None:
    """Warn if an SDLC-classified session completed with no stage progress."""
    try:
        from models.agent_session import AgentSession

        sessions = AgentSession.rows_for_session_id(session_id)
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

        sessions = AgentSession.rows_for_session_id(session_id)
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

    # Back up JSONL transcript (always, regardless of --chat flag).
    # This MUST happen, and be flushed/closed, before the detached worker is
    # spawned below -- the worker's only input is this file (Race 1
    # mitigation: stop.py persists+closes synchronously, worker only reads).
    transcript_path = hook_input.get("transcript_path")
    if transcript_path:
        src = Path(transcript_path)
        if src.exists():
            dst = session_dir / "transcript.jsonl"
            shutil.copy2(src, dst)
            _update_agent_session_log_path(session_id, str(dst))

    # Complete AgentSession lifecycle tracking
    _complete_agent_session(session_id, hook_input)

    # Memory/TUI/post-merge extraction -- formerly ran inline here (Haiku
    # round-trips + gh calls), racing the harness's 10s Stop-hook wall and
    # getting SIGKILLed mid-round-trip on the median run (measured: 126/131
    # timeouts). Detach it to a real subprocess so stop.py exits immediately
    # and the extraction actually completes off the critical path. See
    # docs/plans/hook-registration-manifest-dispatcher.md spike-3.
    cwd = hook_input.get("cwd", "")
    _spawn_detached_extraction(session_id, transcript_path, cwd)


def _complete_agent_session(session_id: str, hook_input: dict) -> None:
    """Mark the AgentSession as completed or failed based on stop_reason.

    Reads the agent_session_id from the sidecar file and looks up the
    AgentSession record. Updates status, completed_at timestamp, and log_path.

    Primary lookup path: ``AgentSession.get_by_id(sidecar_agent_session_id)``.
    This is the fast path for worker-spawned subprocesses whose sidecar
    points at the worker-created record (issue #1157).

    Legacy fallback path: ``AgentSession.query.filter(session_id=f"local-{session_id}")``.
    Retained for direct-CLI sessions that still create local-* records
    (answer to open question 3: local CLI use is supported).

    Fails silently -- session completion errors never block stop.
    """
    try:
        from hook_utils.memory_bridge import load_agent_session_sidecar

        sidecar = load_agent_session_sidecar(session_id)
        agent_session_id = sidecar.get("agent_session_id")
        if not agent_session_id:
            return

        from models.agent_session import AgentSession

        # Primary: resolve via sidecar's agent_session_id through the indexed
        # id lookup. For worker-spawned subprocesses after #1157, the sidecar
        # points at the worker-created record (no local-* twin exists).
        agent_session = None
        try:
            agent_session = AgentSession.get_by_id(agent_session_id)
        except Exception:
            agent_session = None

        if agent_session is None:
            # Legacy fallback: reconstruct local-{session_id} for direct-CLI
            # paths that still create local-* records.
            sidecar_session_id = f"local-{session_id}"
            try:
                matches = AgentSession.rows_for_session_id(sidecar_session_id)
            except Exception:
                matches = []
            if not matches:
                return
            agent_session = matches[0]

        # Issue #1156: If this PM is in waiting_for_children, do not collapse the
        # hierarchy from the Stop hook. Children will finalize the parent via
        # _finalize_parent_sync. The stop hook has no visibility into child
        # liveness and must not bypass the parent-sync terminal transition.
        # Silent skip (no log) consistent with hook-local silent-failure policy;
        # the lifecycle log already records when the PM entered
        # waiting_for_children, giving a complete audit trail.
        if getattr(agent_session, "status", None) == "waiting_for_children":
            return

        stop_reason = hook_input.get("stop_reason", "unknown")
        status = "failed" if stop_reason in ("error", "crash") else "completed"

        # Delegate to lifecycle module with skip flags for hooks subprocess context
        # (no heavy imports for auto-tagging or branch checkpointing)
        from models.session_lifecycle import finalize_session

        finalize_session(
            agent_session,
            status,
            reason=f"stop hook: {stop_reason}",
            skip_auto_tag=True,
            skip_checkpoint=True,
        )
    except Exception:
        pass  # Silent failure -- never block session stop


def _spawn_detached_extraction(session_id: str, transcript_path: str | None, cwd: str) -> None:
    """Spawn a detached subprocess to run memory/tui/post-merge extraction.

    Replaces the previous three inline calls (``_run_memory_extraction``,
    ``_run_tui_interaction_capture``, ``_run_post_merge_extraction``), each of
    which used to wrap its work in a genuine bare ``except Exception: pass``
    swallow (formerly at stop.py:225,242,262). All three now run inside
    ``hook_utils/stop_detach_worker.py``, off the Stop hook's 10s timeout
    wall. Any failure to even *spawn* that worker is logged here via the
    absolute hooks log (never silently swallowed).

    Concurrency is capped by ``HOOK_DETACH_MAX_INFLIGHT`` (Risk 3, concern 2):
    a small, env-overridable number of detached workers may run at once,
    tracked via atomically-reserved lock-slot files under an absolute,
    cwd-independent state dir (works the same in foreign repos under
    user-scope hooks). Over-cap invocations skip spawning and log
    ``detach-skipped: at capacity`` rather than fanning out unbounded.

    Detach is a REAL subprocess (``Popen`` with ``start_new_session=True``
    and redirected/closed streams) -- never a thread. stop.py exits right
    after this call; an in-process daemon thread would be killed before the
    Haiku/gh round-trips inside it ever returned (spike-3).
    """
    try:
        slot_path = try_reserve_detach_slot()
        if slot_path is None:
            log_hook_absolute("stop", "detach-skipped: at capacity")
            return

        worker_script = Path(__file__).resolve().parent / "hook_utils" / "stop_detach_worker.py"
        log_path = get_absolute_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        env = dict(os.environ)
        env["HOOK_DETACH_SLOT_PATH"] = str(slot_path)

        with open(log_path, "a") as logf:
            proc = subprocess.Popen(  # noqa: S603 -- fixed argv, no shell, detached worker
                [
                    sys.executable,
                    str(worker_script),
                    "--session-id",
                    session_id,
                    "--transcript-path",
                    transcript_path or "",
                    "--cwd",
                    cwd,
                ],
                stdin=subprocess.DEVNULL,
                stdout=logf,
                stderr=logf,
                start_new_session=True,
                close_fds=True,
                env=env,
            )

        # Record the real child PID so a later stop.py invocation's liveness
        # check (os.kill(pid, 0)) tracks the actual worker, not this process
        # (which is about to exit).
        try:
            slot_path.write_text(str(proc.pid))
        except OSError:
            pass
    except Exception as e:
        log_hook_absolute("stop", f"detach-spawn failed (non-fatal): {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        from hook_utils.constants import log_hook_error

        log_hook_error("stop", str(e))
        log_hook_absolute("stop", str(e))
