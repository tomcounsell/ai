#!/usr/bin/env python3
"""Hook: UserPromptSubmit - Ingest user prompts into subconscious memory.

Reads the user's prompt from stdin hook input, applies quality filtering
(minimum length, trivial pattern detection), and saves qualifying prompts
as Memory records via memory_bridge.ingest().

After ingest, runs memory_bridge.prefetch() to surface up to 3 relevant
<thought> blocks against the user's prompt before any tool fires. The
result is emitted as a hookSpecificOutput JSON object on stdout, which
Claude Code prepends to the agent's first system message.

All operations fail silently -- memory errors never block prompt submission.
"""

import json
import os
import signal
import sys
from pathlib import Path

# Standalone script -- sys.path mutation is safe (never imported as library)
# Add project root to path for model imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
# Add utils to path
sys.path.insert(0, str(__file__).rsplit("/", 1)[0])

from hook_utils.constants import read_hook_input  # noqa: E402

# ---------------------------------------------------------------------------
# Hard wall-clock deadline for the memory work (ingest + prefetch) combined.
#
# The Claude Code harness discards a UserPromptSubmit hook's output if it does
# not return within 15s ("hook timed out after 15s -- output discarded"). The
# memory path talks to Redis; a degraded Redis (5s-per-op socket timeout, no
# enforced retry budget) can stack ingest + prefetch socket waits past that
# harness budget. None of the memory ops self-impose a path-level deadline --
# they only warn-log on slowness.
#
# This SIGALRM-based guard hard-kills a hung Redis socket well under the harness
# limit. The hook is a short-lived, single-threaded Unix/macOS process, so
# SIGALRM on the main thread is clean. On deadline we abort the memory work,
# emit NO additionalContext (prefetch contributes nothing that turn), and let
# the rest of main() proceed. Losing a fire-and-forget ingest on a slow turn is
# acceptable.
# ---------------------------------------------------------------------------
MEMORY_HOOK_DEADLINE_SECONDS = 8


class _MemoryDeadlineExceeded(BaseException):
    """Raised by the SIGALRM handler when memory work exceeds the deadline.

    Subclasses ``BaseException`` (not ``Exception``) deliberately: the memory
    ops in ``memory_bridge`` wrap their bodies in broad ``except Exception``
    blocks that would otherwise swallow this signal and defeat the deadline.
    """


def _raise_memory_deadline(signum, frame):  # noqa: ARG001 -- signal handler signature
    raise _MemoryDeadlineExceeded()


def _run_memory_work_with_deadline(prompt: str, cwd: str, session_id: str) -> None:
    """Run ingest + prefetch under a hard SIGALRM wall-clock deadline.

    Emits the prefetch ``hookSpecificOutput`` JSON on stdout on the happy
    path. On deadline (or any failure) emits nothing and returns. Never
    raises -- a deadline must never block prompt submission.
    """
    deadline_armed = False
    previous_handler = None
    try:
        previous_handler = signal.signal(signal.SIGALRM, _raise_memory_deadline)
        signal.alarm(MEMORY_HOOK_DEADLINE_SECONDS)
        deadline_armed = True
    except (ValueError, OSError, AttributeError):
        # signal.alarm only works on the main thread of a Unix process. If we
        # cannot arm it (non-main thread, unsupported platform), degrade to the
        # pre-existing fail-silent behavior with no hard deadline.
        deadline_armed = False

    try:
        # Ingest into memory (quality filter and dedup handled inside)
        try:
            from hook_utils.memory_bridge import ingest

            ingest(prompt, cwd=cwd)
        except Exception:
            pass  # Silent failure -- never block prompt submission

        # Prefetch memories matching the prompt and emit as additionalContext.
        # This runs before any tool call so the agent has memory context on
        # the very first turn. Gates short / trivial prompts and strips PM
        # boilerplate internally; returns None when no thoughts to surface.
        if session_id:
            try:
                from hook_utils.memory_bridge import prefetch

                prefetch_result = prefetch(session_id, prompt, cwd=cwd)
                if prefetch_result:
                    # Use the explicit hookSpecificOutput shape (matches the
                    # form used in agent/health_check.py for PostToolUse).
                    payload = {
                        "hookSpecificOutput": {
                            "hookEventName": "UserPromptSubmit",
                            "additionalContext": prefetch_result,
                        }
                    }
                    print(json.dumps(payload))
            except Exception:
                pass  # Silent failure -- never block prompt submission
    except _MemoryDeadlineExceeded:
        # Deadline hit a hung Redis socket. Abort gracefully: emit NO
        # additionalContext (we never reached a clean prefetch result) and
        # let the rest of main() proceed. This is an ADDITIONAL guard on top
        # of the per-op fail-silent handling, never a replacement.
        pass
    finally:
        if deadline_armed:
            # Always cancel the alarm and restore the prior handler so the
            # deadline cannot fire during the non-memory work below.
            try:
                signal.alarm(0)
            except (ValueError, OSError):
                pass
            if previous_handler is not None:
                try:
                    signal.signal(signal.SIGALRM, previous_handler)
                except (ValueError, OSError, TypeError):
                    pass


def main():
    # Best-effort: give this short-lived hook process the same resilient Redis
    # client (retry / backoff / health-check / bounded timeouts) the bridge and
    # worker use. Without it the hook runs on a bare Popoto client with no
    # enforced retry budget. Idempotent and a no-op under pytest; never crash
    # if the module is unavailable in a foreign checkout.
    try:
        from config.redis_bootstrap import configure_resilient_redis

        configure_resilient_redis()
    except Exception:
        pass  # Silent -- resilient Redis is an optimization, not a requirement

    hook_input = read_hook_input()
    if not hook_input:
        return

    # Extract user prompt content
    # UserPromptSubmit hook receives the prompt in "prompt" field
    prompt = hook_input.get("prompt", "")
    if not prompt or not isinstance(prompt, str):
        return

    cwd = hook_input.get("cwd", "")
    session_id = hook_input.get("session_id", "")

    # Memory work (ingest + prefetch) runs under a hard wall-clock deadline so a
    # degraded Redis can never push the hook past the harness's 15s budget.
    _run_memory_work_with_deadline(prompt, cwd, session_id)

    # Capture TUI interaction patterns (slash commands, mid-run steering) for
    # subconscious-memory recall (#1540, Pillar 3 of epic #1536). Fail-silent.
    if session_id:
        try:
            from agent.tui_interaction_capture import capture_prompt_event

            capture_prompt_event(session_id, prompt, cwd=cwd)
        except Exception:
            pass  # Silent failure -- never block prompt submission

    # Create AgentSession for local CLI session (one per session, idempotent)
    try:
        if session_id:
            from hook_utils.memory_bridge import (
                load_agent_session_sidecar,
                save_agent_session_sidecar,
            )

            sidecar = load_agent_session_sidecar(session_id)
            agent_session_id = sidecar.get("agent_session_id")
            if agent_session_id:
                # Subsequent prompt in same session -- re-activate
                import time

                from models.agent_session import AgentSession

                try:
                    local_sid = f"local-{session_id}"
                    matches = list(AgentSession.query.filter(session_id=local_sid))
                    if matches:
                        agent_session = matches[0]
                        # Use lifecycle module for consistent transition logging
                        from models.session_lifecycle import (
                            TERMINAL_STATUSES,
                            transition_status,
                        )

                        # Guard: do NOT re-activate sessions already in a terminal
                        # state (killed/completed/failed/abandoned/cancelled).
                        # Without this check, a killed PM session would resurrect
                        # every time a new prompt hit the hook — the #1113 zombie
                        # revival bug. Terminal sessions are operator-resumable
                        # only via explicit `valor-session resume`.
                        current_status = getattr(agent_session, "status", None)
                        if current_status in TERMINAL_STATUSES:
                            import logging

                            logging.getLogger(__name__).warning(
                                "[user_prompt_submit] Refusing to re-activate "
                                "terminal session %s (status=%s). Use "
                                "`valor-session resume` to resume intentionally.",
                                getattr(agent_session, "agent_session_id", "?"),
                                current_status,
                            )
                        else:
                            agent_session.updated_at = time.time()
                            agent_session.completed_at = None
                            transition_status(
                                agent_session,
                                "running",
                                reason="subsequent prompt reactivated local session",
                            )
                            # If transition was idempotent (already running), field
                            # changes above were not saved. Ensure they persist.
                            if agent_session.status == "running":
                                agent_session.save(update_fields=["updated_at", "completed_at"])
                except Exception:
                    pass  # Non-fatal
            else:
                # Phantom PM twin prevention (issue #1157).
                #
                # Worker-spawned subprocesses (PM/Teammate/Dev) already have an
                # authoritative AgentSession record created by the worker BEFORE
                # the subprocess is spawned. The worker communicates ownership
                # by setting two env vars (see agent/sdk_client.py:1343-1369):
                #   - AGENT_SESSION_ID : the worker's agent_session_id UUID
                #   - VALOR_SESSION_ID : the worker's bridge session_id
                #
                # If either env var resolves to a live (non-terminal) record,
                # attach the sidecar to THAT record and return. The create_local()
                # call below is never reached — no phantom twin is minted.
                #
                # This is strict PREVENTION, not cleanup: if prevention lands
                # correctly, no phantom ever gets written to Redis.
                worker_agent_session_id = os.environ.get("AGENT_SESSION_ID", "").strip()
                worker_bridge_session_id = os.environ.get("VALOR_SESSION_ID", "").strip()

                if worker_agent_session_id or worker_bridge_session_id:
                    try:
                        from models.agent_session import AgentSession
                        from models.session_lifecycle import TERMINAL_STATUSES

                        attached = None

                        if worker_agent_session_id:
                            attached = AgentSession.get_by_id(worker_agent_session_id)

                        if attached is None and worker_bridge_session_id:
                            try:
                                matches = list(
                                    AgentSession.query.filter(session_id=worker_bridge_session_id)
                                )
                                if matches:
                                    attached = matches[0]
                            except Exception:
                                attached = None

                        if (
                            attached is not None
                            and getattr(attached, "status", None) not in TERMINAL_STATUSES
                        ):
                            sidecar["agent_session_id"] = attached.agent_session_id
                            save_agent_session_sidecar(session_id, sidecar)
                            # Capture the Claude Code session UUID onto the parent
                            # if it isn't already set. Without this, PM/Teammate
                            # sessions that never trigger SDK auth or post_compact
                            # paths leave `claude_session_uuid` as None — defeating
                            # any downstream code that filters by that index
                            # (PreCompact cooldown lookups, etc.).
                            if not getattr(attached, "claude_session_uuid", None):
                                try:
                                    attached.claude_session_uuid = session_id
                                    attached.save(update_fields=["claude_session_uuid"])
                                except Exception:
                                    pass
                            return
                        # If attached is terminal, fall through to the existing
                        # gate (preserves #1113 semantics: terminal sessions are
                        # operator-resume-only).
                    except Exception:
                        # Silent failure -- never block prompt submission.
                        # Falls through to existing gate below.
                        pass

                # Only create AgentSession for worker-spawned sessions.
                # Direct CLI invocations (no parent worker, no session type) produce no record —
                # they add noise to the queue without providing value (issue #1001).
                if not os.environ.get("VALOR_PARENT_SESSION_ID") and not os.environ.get(
                    "SESSION_TYPE"
                ):
                    return

                # First prompt in this session -- create AgentSession
                from models.agent_session import AgentSession

                local_session_id = f"local-{session_id}"

                # Resolve project key from cwd
                from hook_utils.memory_bridge import _get_project_key

                project_key = _get_project_key(cwd)

                # Read SESSION_TYPE from environment to register the correct persona
                session_type_override = os.environ.get("SESSION_TYPE")

                # Read VALOR_PARENT_SESSION_ID so child subprocess sessions link back to the
                # parent PM/Teammate AgentSession (issue #808). The env var carries the parent's
                # agent_session_id UUID (agt_xxx), which is the canonical FK stored in
                # parent_agent_session_id. When absent (non-child sessions), create_local()
                # behaves identically to before.
                parent_agent_session_id = os.environ.get("VALOR_PARENT_SESSION_ID")

                # Stopgap (#1633): do not create NEW parent-linked child records.
                # The granite PTY container owns the PM/Dev split; parent-linked
                # children risk pool-slot starvation. Skip record creation
                # entirely (no Redis write) -- the subprocess itself still runs,
                # and the attach path above keeps EXISTING child sessions
                # working. Escape hatch: VALOR_ALLOW_CHILD_SESSIONS=1.
                if parent_agent_session_id:
                    from models.child_session_gate import child_sessions_allowed

                    if not child_sessions_allowed():
                        return

                agent_session = AgentSession.create_local(
                    session_id=local_session_id,
                    project_key=project_key,
                    working_dir=cwd,
                    status="running",
                    message_text=prompt[:500] if prompt else "",
                    **({"session_type": session_type_override} if session_type_override else {}),
                    **(
                        {"parent_agent_session_id": parent_agent_session_id}
                        if parent_agent_session_id
                        else {}
                    ),
                )

                sidecar["agent_session_id"] = agent_session.agent_session_id
                save_agent_session_sidecar(session_id, sidecar)
    except Exception:
        pass  # Silent failure -- never block prompt submission


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        from hook_utils.constants import log_hook_error

        log_hook_error("user_prompt_submit", str(e))
