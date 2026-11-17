"""SubagentStart: a Valor specialist whose definition (`agents/<name>.md`)
has a `memory:` line keeps a summary of its past work on the project
(docs/plans/specialist-memory.md), and opens with `summary.md`, the rules
for it. Its own hook output, beside the worker's cut (`subagent_start.py`),
so each stays under the 10,000 characters Claude Code gives whole
(docs/harnesses.md, Corrections reaching subagents)."""

import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent


def keeps_summary(agent_type: str) -> bool:
    """A `valor:` agent whose definition's frontmatter has a `memory:` line."""
    plugin, _, name = agent_type.partition(":")
    if plugin != "valor" or not name or "/" in name or name.startswith("."):
        return False
    try:
        parts = (PLUGIN / "agents" / f"{name}.md").read_text().split("---")
    except OSError:
        return False
    return len(parts) > 2 and any(line.startswith("memory:") for line in parts[1].splitlines())


try:
    started = json.load(sys.stdin)
except ValueError:
    started = {}
agent_type = started.get("agent_type") if isinstance(started, dict) else None
if isinstance(agent_type, str) and keeps_summary(agent_type):
    rules = (PLUGIN / "hooks" / "summary.md").read_text().strip()
    out = {"hookEventName": "SubagentStart", "additionalContext": rules}
    sys.stdout.write(json.dumps({"hookSpecificOutput": out}, ensure_ascii=False))
