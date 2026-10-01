"""`claude -p` turns, pointed at the kernel's gateway.

`turn` builds a single self-contained turn; `workspace_turn` builds one turn
of a task that works in a directory over several turns.

A `turn` runs with safe mode (no hooks, plugins, MCP servers, or CLAUDE.md
from this machine), no session persistence, and `ANTHROPIC_BASE_URL` set to
the gateway, so every model call it makes is metered against the task.
`CLAUDE_CODE_MAX_OUTPUT_TOKENS` caps each call's output, which keeps the
gateway's worst-case reservation close to what a call can really cost.
Claude Code's own session variables are dropped from the environment, so a
turn started from inside a Claude Code session is still a fresh process.
The dispatched Brief, Tom's corrections included, follows the persona in the
system prompt.
"""

import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

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
    """A builder: given the gateway base URL, the dispatched Brief, and the
    turn's id, the command for one turn."""

    def build(base_url: str, brief: str, turn_id: str) -> TurnCommand:
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
            "--output-format",
            "json",
            "--model",
            model,
            "--safe-mode",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--system-prompt",
            f"{system_prompt}\n\n{brief}",
            "--tools",
            tools,
            "--",
            prompt,
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


# The environment a workspace turn inherits. Everything else (tokens, SSH and
# 1Password agents, Claude Code's own session variables) stays behind.
KEEP_ENV = ("HOME", "USER", "LOGNAME", "PATH", "SHELL", "TMPDIR", "LANG", "LC_ALL", "TERM")


def workspace_turn(
    prompt: str,
    *,
    cwd: str,
    resume: str | None = None,
    model: str = "haiku",
    harness: dict | None = None,
    system_prompt: str = "You are Valor.",
    max_output_tokens: int = 32000,
):
    """A builder for one turn of a task that works in `cwd`.

    The turn keeps Claude Code's own system prompt and tools, with the
    persona and the dispatched Brief appended and re-rendered on every turn
    (`--system-prompt-snapshot off`), and resumes `resume` when given. It
    edits files and runs commands without asking (`bypassPermissions`): the
    kernel bounds it, not a permission prompt nobody is there to answer.
    Safe mode keeps this machine's hooks, skills, plugins, CLAUDE.md, and
    MCP servers out; web fetch and web search are off.

    `harness` carries the task's isolation, all optional:
    `sandbox_profile`, a sandbox-exec profile the whole turn runs under,
    given the gateway's port as `GATEWAY_PORT` and the turn's id as
    `VALOR_TURN`, which the profile names its processes by (`core.runs`
    reaps them when the turn ends); `gitconfig`, used as git's global
    config with the system config ignored; `gh_config_dir`, gh's
    config directory; `env`, variables added to the turn's environment (the
    workspace's own settings, such as where its test database listens);
    `max_output_tokens`, the per-call output cap, which sets the gateway's
    worst-case reservation for each call.
    """
    harness = harness or {}
    max_output_tokens = harness.get("max_output_tokens", max_output_tokens)

    def build(base_url: str, brief: str, turn_id: str) -> TurnCommand:
        env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
        env.update(harness.get("env", {}))
        env["ANTHROPIC_BASE_URL"] = base_url
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_output_tokens)
        env["GIT_TERMINAL_PROMPT"] = "0"
        # A `-p` turn ends when the model stops, killing anything it left
        # running in the background; so a turn runs its commands in the
        # foreground or not at all.
        env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] = "1"
        if harness.get("gitconfig"):
            env["GIT_CONFIG_GLOBAL"] = harness["gitconfig"]
            env["GIT_CONFIG_NOSYSTEM"] = "1"
        if harness.get("gh_config_dir"):
            env["GH_CONFIG_DIR"] = harness["gh_config_dir"]
        argv = [
            CLAUDE,
            "-p",
            "--output-format",
            "json",
            "--model",
            model,
            "--safe-mode",
            "--strict-mcp-config",
            "--permission-mode",
            "bypassPermissions",
            "--disallowedTools",
            "WebFetch",
            "WebSearch",
            "--system-prompt-snapshot",
            "off",
            "--append-system-prompt",
            f"{system_prompt}\n\n{brief}",
        ]
        if resume:
            argv += ["--resume", resume]
        argv += ["--", prompt]
        if harness.get("sandbox_profile"):
            port = urlparse(base_url).port
            argv = [
                "sandbox-exec",
                "-D",
                f"GATEWAY_PORT={port}",
                "-D",
                f"VALOR_TURN={turn_id}",
                "-f",
                harness["sandbox_profile"],
                *argv,
            ]
        return TurnCommand(argv=argv, env=env, cwd=cwd, harness="claude_code", parse=parse)

    return build
