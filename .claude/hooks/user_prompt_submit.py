#!/usr/bin/env python3
"""Hook: UserPromptSubmit - Ingest user prompts into subconscious memory.

Reads the user's prompt from stdin hook input, applies quality filtering
(minimum length, trivial pattern detection), and saves qualifying prompts
as Memory records via memory_bridge.ingest().

All operations fail silently -- memory errors never block prompt submission.
"""

import sys

# Standalone script -- sys.path mutation is safe (never imported as library)
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
            if not sidecar.get("agent_session_job_id"):
                # First prompt in this session -- create AgentSession
                from models.agent_session import AgentSession

                local_session_id = f"local-{session_id}"
                cwd = hook_input.get("cwd", "")

                # Resolve project key
                from hook_utils.memory_bridge import _get_project_key

                project_key = _get_project_key()

                agent_session = AgentSession.create_local(
                    session_id=local_session_id,
                    project_key=project_key,
                    working_dir=cwd,
                    status="running",
                    message_text=prompt[:500] if prompt else "",
                )

                sidecar["agent_session_job_id"] = agent_session.job_id
                save_agent_session_sidecar(session_id, sidecar)
    except Exception:
        pass  # Silent failure -- never block prompt submission


if __name__ == "__main__":
    main()
