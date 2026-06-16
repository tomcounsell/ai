"""SDLC liveness token helper (issue #1394).

Emits watchdog-safe liveness tokens to stdout so the session watchdog resets
its stall timer during long-running stages like TEST and DEPLOY.

Usage in a sub-skill script:
    from agent.sdlc_liveness import emit_liveness_token
    emit_liveness_token(elapsed_seconds=elapsed, stage="TEST")

Shell pattern for background heartbeat during pytest runs:
    while sleep 60; do echo "[sdlc-liveness] TEST still running"; done &
    HEARTBEAT_PID=$!
    pytest ...
    kill $HEARTBEAT_PID 2>/dev/null
"""


def emit_liveness_token(elapsed_seconds: int, stage: str) -> None:
    """Emit a stdout liveness token so the watchdog timer resets.

    The token is flushed immediately to ensure the watchdog sees it without
    waiting for Python's output buffering to flush naturally.

    Args:
        elapsed_seconds: Seconds elapsed since the long-running operation started.
        stage: SDLC stage name (e.g. "TEST", "DEPLOY").
    """
    print(f"[sdlc-liveness] {stage} still running ({elapsed_seconds}s elapsed)", flush=True)
