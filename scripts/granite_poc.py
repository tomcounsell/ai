"""CLI entrypoint for the granite-agent-loop PoC.

Usage:
    python scripts/granite_poc.py "write a hello world Python file"

Output:
- prints the LoopResult to stdout
- appends per-turn events to logs/granite_poc_trace.jsonl
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.granite_agent_loop import GraniteAgentLoop  # noqa: E402


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if len(sys.argv) < 2:
        print("usage: python scripts/granite_poc.py <task string>", file=sys.stderr)
        return 2
    task = " ".join(sys.argv[1:]).strip()
    if not task:
        print("usage: python scripts/granite_poc.py <task string>", file=sys.stderr)
        return 2

    loop = GraniteAgentLoop()
    result = loop.run(task)
    print(json.dumps(asdict(result), indent=2, default=str))
    return 0 if result.status == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
