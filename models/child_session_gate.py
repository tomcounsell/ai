"""Temporary stopgap (issue #1633): block creation of NEW child agent sessions.

Post-PTY rationale (plan #1924 cutover): sessions now run as headless
``claude -p`` subprocesses via ``agent/session_runner/`` — there is no PTY
pool, so the original starvation/deadlock argument is gone. The gate is
RETAINED because parent sessions spawning child AgentSessions remain:

- semantically redundant -- dependent work runs as subagents WITHIN the
  session (the D1 topology: the PM continues its ``dev`` subagent across
  turns), and
- unbounded -- with the pool gone there is no independent fanout cap on
  concurrent child sessions until #1633's subagent refactor lands or #1926
  (guardian consolidation) names a cap.

Re-enabling child-session fanout is a real behavior change with no named
replacement bound; removal of this gate is #1633's scope, deferred — not
smuggled into the cutover. Existing child sessions are unaffected: resume,
steer, kill, ``children`` listing, and ``waiting_for_children`` lifecycle
handling all keep working, and PM continuation chains
(``session_completion.py`` create_pm, issue #1195) are deliberately exempt --
their parents are terminal.

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
    "the #1633 block. Child-session fanout has no independent concurrency "
    "cap; dependent work should run as subagents inside the session instead."
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
