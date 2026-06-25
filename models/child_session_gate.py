"""Temporary stopgap (issue #1633): block creation of NEW child agent sessions.

PR #1612 cut session execution over to granite PTY containers that run their
own PM+Dev claude TUI pair from a bounded pool. Parent sessions spawning child
AgentSessions (the old PM->Dev pattern):

- double-consume scarce pool slots and risk starvation/deadlock when a parent
  in ``waiting_for_children`` holds a slot its child needs, and
- are semantically redundant -- the container owns the PM/Dev split.

Issue #1633 prescribes the full refactor (dependent work runs as subagents
WITHIN a session). Until that lands, every path that attaches a parent at
session-CREATION time is refused. Existing child sessions are unaffected:
resume, steer, kill, ``children`` listing, and ``waiting_for_children``
lifecycle handling all keep working, and PM continuation chains
(``session_completion.py`` create_pm, issue #1195) are deliberately exempt --
their parents are terminal and hold no pool slot.

Emergency escape hatch: ``VALOR_ALLOW_CHILD_SESSIONS=1`` bypasses the block
with a loud warning at each creation site.
"""

import os

BLOCK_ISSUE = 1633
BYPASS_ENV_VAR = "VALOR_ALLOW_CHILD_SESSIONS"

CHILD_SESSIONS_DISABLED_MESSAGE = (
    "child agent sessions are temporarily disabled (#1633) -- run dependent "
    "work as subagents inside the current session instead. Emergency bypass: "
    f"set {BYPASS_ENV_VAR}=1."
)

BYPASS_WARNING = (
    f"WARNING: {BYPASS_ENV_VAR}=1 -- creating a child agent session despite "
    "the #1633 block. Child sessions double-consume granite PTY pool slots "
    "and can starve or deadlock the pool."
)


def child_sessions_allowed() -> bool:
    """True when the emergency escape hatch (``VALOR_ALLOW_CHILD_SESSIONS=1``) is set."""
    return os.environ.get(BYPASS_ENV_VAR) == "1"


def child_sessions_disabled_json() -> dict:
    """Structured error payload for ``--json`` CLI surfaces."""
    return {
        "error": "child_sessions_disabled",
        "issue": BLOCK_ISSUE,
        "message": CHILD_SESSIONS_DISABLED_MESSAGE,
        "bypass": f"{BYPASS_ENV_VAR}=1",
    }


class ChildSessionsDisabledError(RuntimeError):
    """Raised by the queue chokepoint when a parent-attached create is refused."""

    def __init__(self) -> None:
        super().__init__(CHILD_SESSIONS_DISABLED_MESSAGE)
