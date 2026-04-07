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

from hook_utils.constants import read_hook_input


def main():
    hook_input = read_hook_input()
    if not hook_input:
        return

    # Extract user prompt content
    # UserPromptSubmit hook receives the prompt in "prompt" field
    prompt = hook_input.get("prompt", "")
    if not prompt or not isinstance(prompt, str):
        return

    # Ingest into memory (quality filter and dedup handled inside)
    try:
        from hook_utils.memory_bridge import ingest

        ingest(prompt)
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
                        from models.session_lifecycle import transition_status

                        agent_session.updated_at = time.time()
                        agent_session.completed_at = None
                        transition_status(
                            agent_session,
                            "running",
                            reason="subsequent prompt reactivated local session",
                            reject_from_terminal=False,
                        )
                        # If transition was idempotent (already running), field
                        # changes above were not saved. Ensure they persist.
                        if agent_session.status == "running":
                            agent_session.save()
                except Exception:
                    pass  # Non-fatal
            else:
                # First prompt in this session -- create AgentSession
                from models.agent_session import AgentSession

                local_session_id = f"local-{session_id}"
                cwd = hook_input.get("cwd", "")

                # Resolve project key from cwd
                from hook_utils.memory_bridge import _get_project_key

                project_key = _get_project_key(cwd)

                # Read SESSION_TYPE from environment to register the correct persona
                session_type_override = os.environ.get("SESSION_TYPE")

                agent_session = AgentSession.create_local(
                    session_id=local_session_id,
                    project_key=project_key,
                    working_dir=cwd,
                    status="running",
                    message_text=prompt[:500] if prompt else "",
                    **({"session_type": session_type_override} if session_type_override else {}),
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
