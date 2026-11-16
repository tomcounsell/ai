"""SubagentStart: every subagent but the web agent opens with the worker's
cut of the turn that started it (its Brief's head, the corrections and the
working rules), which the turn carries in `VALOR_SUBAGENT_START`
(`harnesses/claude_code.py`, `subagent_start`). The web agent reads untrusted
pages with the web in hand, so it is given only its prompt."""

import json
import os
import sys

try:
    started = json.load(sys.stdin)
except ValueError:
    started = {}
if isinstance(started, dict) and started.get("agent_type") != "valor:web":
    sys.stdout.write(os.environ.get("VALOR_SUBAGENT_START", ""))
