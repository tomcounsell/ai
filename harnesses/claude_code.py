"""One `claude -p` turn, pointed at the kernel's gateway.

The turn runs with safe mode (no hooks, plugins, MCP servers, or CLAUDE.md
from this machine), no session persistence, and `ANTHROPIC_BASE_URL` set to
the gateway, so every model call it makes is metered against the task.
`CLAUDE_CODE_MAX_OUTPUT_TOKENS` caps each call's output, which keeps the
gateway's worst-case reservation close to what a call can really cost.
Claude Code's own session variables are dropped from the environment, so a
turn started from inside a Claude Code session is still a fresh process.
"""

import json
import os
import shutil
from pathlib import Path

from core.runs import TurnCommand

CLAUDE = shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def turn(
    prompt: str,
    *,
    cwd: str,
    model: str = "haiku",
    system_prompt: str = "You are Valor.",
    tools: str = "",
    max_output_tokens: int = 1024,
):
    """A builder: given the gateway base URL, the command for one turn."""

    def build(base_url: str) -> TurnCommand:
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("CLAUDE", "ANTHROPIC")) and k != "AI_AGENT"
        }
        env["ANTHROPIC_BASE_URL"] = base_url
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_output_tokens)
        argv = [
            CLAUDE,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--model",
            model,
            "--safe-mode",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--system-prompt",
            system_prompt,
            "--tools",
            tools,
        ]
        return TurnCommand(argv=argv, env=env, cwd=cwd, harness="claude_code", parse=parse)

    return build


def parse(stdout: bytes) -> dict:
    """The fields of claude's JSON result the ledger keeps. Claude's own
    cost figure is kept beside the gateway's, never in place of it."""
    try:
        result = json.loads(stdout)
    except ValueError:
        return {"raw": stdout.decode(errors="replace")[-400:]}
    return {
        "text": result.get("result"),
        "is_error": result.get("is_error"),
        "num_turns": result.get("num_turns"),
        "harness_reported_usd": result.get("total_cost_usd"),
        "session_id": result.get("session_id"),
    }
