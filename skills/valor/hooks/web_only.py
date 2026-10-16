"""The `web.agent_only` guard (docs/plans/orchestration.md, Tom's grant of
2026-10-16): WebFetch and WebSearch run only inside the `valor:web` agent.

Claude Code runs it before each web call with the call as JSON on stdin; the
`agent_type` it carries names the agent making the call, absent in the main
session. Any other caller, or a call it cannot read, is denied with the way
to the web: start the agent. `hooks.json` turns any failure of the hook into
exit 2, which Claude Code treats as a block.
"""

import json
import sys

WEB_AGENT = "valor:web"
DENY = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"The web is reached through the {WEB_AGENT} agent: start it with the Agent tool "
            "and ask it your question."
        ),
    }
}


def decision(call):
    return None if isinstance(call, dict) and call.get("agent_type") == WEB_AGENT else DENY


if __name__ == "__main__":
    try:
        call = json.load(sys.stdin)
    except ValueError:
        call = None
    if (out := decision(call)) is not None:
        print(json.dumps(out))
