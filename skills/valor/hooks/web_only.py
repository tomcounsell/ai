"""The `web.agent_only` guard (docs/plans/orchestration.md, Tom's grant of
2026-10-16): WebFetch and WebSearch run only inside the `valor:web` agent.

Claude Code runs it before each web call with the call as JSON on stdin; the
`agent_type` it carries names the agent making the call, absent in the main
session. Any other caller is denied with the way to the web: start the agent.
"""

import json
import sys

WEB_AGENT = "valor:web"


def decision(call):
    if call.get("agent_type") == WEB_AGENT:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"The web is reached through the {WEB_AGENT} agent: start it with the Agent tool "
                "and ask it your question."
            ),
        }
    }


if __name__ == "__main__":
    if (out := decision(json.load(sys.stdin))) is not None:
        print(json.dumps(out))
