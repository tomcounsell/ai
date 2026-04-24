#!/usr/bin/env python3
"""Hook: UserPromptSubmit - Ingest user prompts into subconscious memory.

Reads the user's prompt from stdin hook input, applies quality filtering
(minimum length, trivial pattern detection), and saves qualifying prompts
as Memory records via memory_bridge.ingest().

All operations fail silently -- memory errors never block prompt submission.
"""

import os
import sys
from pathlib import Path

# Standalone script -- sys.path mutation is safe (never imported as library)
# Add project root to path for model imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
# Add utils to path
sys.path.insert(0, str(__file__).rsplit("/", 1)[0])

from hook_utils.constants import read_hook_input  # noqa: E402


def main():
    hook_input = read_hook_input()
    if not hook_input:
        return

    # Extract user prompt content
    # UserPromptSubmit hook receives the prompt in "prompt" field
    prompt = hook_input.get("prompt", "")
    if not prompt or not isinstance(prompt, str):
        return

    cwd = hook_input.get("cwd", "")

    # Ingest into memory (quality filter and dedup handled inside)
    try:
        from hook_utils.memory_bridge import ingest

        ingest(prompt, cwd=cwd)
    except Exception:
        pass  # Silent failure -- never block prompt submission

    # Create AgentSession for local CLI session (one per session, idempotent)
    try:
        session_id = hook_input.get("session_id", "")
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
