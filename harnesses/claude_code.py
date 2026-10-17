"""`claude -p` turns, pointed at the kernel's gateway.

`workspace_turn` builds one turn of a task that works in a directory over
several turns. It runs as a developer's Claude Code would in that
directory: the project's own CLAUDE.md, settings, hooks, skills and MCP
servers, Valor's own plugin and the yudame plugins (`settings.plugin_dirs`),
the operator's own user MCP servers (`settings.claude_json`), and the
operator's environment, `gh` login, git credentials and SSH agent. Two things differ:
every model call goes to the gateway (`ANTHROPIC_BASE_URL`), which meters
it against the task and supplies the kernel's credential in place of the
placeholder the turn carries, and the turn runs under a sandbox-exec
profile (`core/workspace.py`) that keeps it away from the kernel's key
directory, the machine cluster's data directory, and the backup disk.
`workspace_turn` refuses a task whose harness settings name no profile.

The dispatched text (the persona, the Brief, Tom's corrections, the
channel and the stage, rendered by `core.tasks.dispatch`) is appended to
Claude Code's own system prompt, byte for byte what `turn.started` records.
"""

import functools
import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

from core import binaries, transcripts
from core.gateway import TURN_TOKEN
from core.runs import TurnCommand
from core.settings import compaction, resolve_model, settings
from core.workspace import current_profile, environment

# The model a turn runs when none is given: the light seat's pinned id, never
# an alias the installed CLI resolves to a model the gateway has no price for
# and charges at a stand-in price.
LIGHT = resolve_model("light")


@functools.cache
def version(path: str) -> str | None:
    """The installed release, from the versioned install directory the
    resolved `claude` path names (`.../versions/2.1.288`), never by running
    the binary. None for a layout this does not recognise."""
    real = Path(os.path.realpath(path))
    return real.name if real.parent.name == "versions" else None


SPAWN_DEPTH = "1000"
HOOKS_ON = json.dumps({"disableAllHooks": False})
SUBAGENT_OPENING = (
    "The Brief of the Valor session that started you follows. Its stage and channel are that "
    "session's; your task is the prompt you were given, and what you find goes back to it."
)


# Claude Code 2.1.302 compacts at 80% of its auto-compact window less the
# 20,000 tokens it keeps for output (the 80% is a fraction its server can
# move; `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` keeps it from rising past 80).
COMPACT_PCT = 80
OUTPUT_RESERVE = 20_000
EMAIL_OPENING = (
    "4. **The thread's opening message, verbatim**: the email that opened this thread, with "
    "when, who and its message id, quoted exactly, cut at 4,000 characters. Carry it forward "
    "unchanged from an earlier summary on every update; it is outside part 2's cap and never dropped."
)
CHAT_OPENING = "The chat's opening message is kept only as parts 2 and 3 keep any message."


def compact_text(channel: str) -> str:
    """A chat session's compaction instructions (`skills/sdlc/compact.md`):
    only an email thread keeps its opening."""
    opening = EMAIL_OPENING if channel == "email" else CHAT_OPENING
    return (Path(settings.stages_dir) / "compact.md").read_text().strip().replace("{opening}", opening)


def subagent_start(brief: str) -> str:
    """The SubagentStart hook output that gives a subagent the dispatched text."""
    context = f"{SUBAGENT_OPENING}\n\n{brief}"
    return json.dumps(
        {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": context}},
        ensure_ascii=False,
    )


class Unsandboxed(ValueError):
    """A workspace turn was asked for with no sandbox profile."""


def parse(stdout: bytes) -> dict:
    """The fields of claude's JSON result the ledger keeps. Claude's own
    cost figure is kept beside the gateway's, never in place of it. Output
    that is not claude's JSON result is kept whole in the turn's stdout
    file, which `turn.ended` names."""
    try:
        result = json.loads(stdout)
    except ValueError:
        return {"unparsed": True}
    return {
        "text": result.get("result"),
        "is_error": result.get("is_error"),
        "num_turns": result.get("num_turns"),
        "harness_reported_usd": result.get("total_cost_usd"),
        "session_id": result.get("session_id"),
    }


def user_mcp_servers() -> dict:
    """The operator's user-scope MCP servers, as a developer's Claude Code
    loads them; none when the config is missing or unreadable."""
    try:
        return json.loads(Path(settings.claude_json).read_text()).get("mcpServers") or {}
    except OSError, ValueError, AttributeError:
        return {}


def workspace_turn(
    prompt: str,
    *,
    cwd: str,
    resume: str | None = None,
    model: str = LIGHT,
    harness: dict | None = None,
    max_output_tokens: int | None = None,
):
    """A builder for one turn of a task that works in `cwd`.

    The turn keeps Claude Code's own system prompt and tools, with the
    dispatched text appended and re-rendered on every turn
    (`--system-prompt-snapshot off`), and resumes `resume` when given. It
    edits files and runs commands without asking (`bypassPermissions`).

    `harness`: `sandbox_profile` (required, else `Unsandboxed`), run with
    the gateway's port as `GATEWAY_PORT` and the turn's id as `VALOR_TURN`,
    which the profile names its processes by so `core.runs` can reap them;
    `env`, variables added to the turn's environment; `max_output_tokens`,
    set as `CLAUDE_CODE_MAX_OUTPUT_TOKENS` when given; `tmpdir`, the turn's
    own `TMPDIR`, which `core.runs` empties at the turn's start and removes
    at its end; `claude_config_dir`, its own Claude Code config directory,
    which holds no login and where its transcript lies; `chat_channel`,
    set for a chat session, which Claude Code compacts only past its
    model's forced compaction (`core.settings.compaction`;
    `CLAUDE_CODE_AUTO_COMPACT_WINDOW` and `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`,
    neither for a model `WINDOWS` does not list), and which compacts
    itself between turns (`core/session.py`), with `compact_text` for instructions (`VALOR_COMPACT`,
    which the PreCompact hook of Valor's plugin prints). A fresh session
    (critique, review, docs) is this with no `resume`.

    A new session gets `--session-id` with an id the kernel chose; a
    resumed one `--resume`.
    """
    harness = harness or {}
    if not harness.get("sandbox_profile"):
        raise Unsandboxed(
            "a workspace turn runs under a sandbox profile; start the task with "
            "--harness-config naming one (`sandbox_profile`)"
        )
    max_output_tokens = harness.get("max_output_tokens", max_output_tokens)

    def build(base_url: str, brief: str, turn_id: str) -> TurnCommand:
        env = environment(harness.get("env", {}))
        env["ANTHROPIC_BASE_URL"] = base_url
        if max_output_tokens is not None:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_output_tokens)
        env["GIT_TERMINAL_PROMPT"] = "0"
        # A `-p` turn ends when the model stops, killing anything it left
        # running in the background; so a turn runs its commands in the
        # foreground or not at all.
        env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] = "1"
        # Subagents start subagents to any depth (Tom, 2026-10-16), so the web
        # agent is always one more away; Claude Code's own cap is 3.
        env["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] = SPAWN_DEPTH
        # Every subagent, at every depth, opens with the dispatched text: the
        # SubagentStart hook of Valor's plugin prints this.
        env["VALOR_SUBAGENT_START"] = subagent_start(brief)
        if channel := harness.get("chat_channel"):
            env["VALOR_COMPACT"] = compact_text(channel)
            if marks := compaction(model):
                env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(marks[1] * 100 // COMPACT_PCT + OUTPUT_RESERVE)
                env["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = str(COMPACT_PCT)
        if harness.get("tmpdir"):
            env["TMPDIR"] = env["CLAUDE_CODE_TMPDIR"] = harness["tmpdir"]
        if harness.get("claude_config_dir"):
            # The turn's own Claude Code state, with no login: the gateway
            # replaces the placeholder with the kernel's credential.
            env["CLAUDE_CONFIG_DIR"] = harness["claude_config_dir"]
            env["CLAUDE_CODE_OAUTH_TOKEN"] = TURN_TOKEN
        argv = [
            settings.claude,
            "-p",
            "--output-format",
            "json",
            "--model",
            model,
            "--permission-mode",
            "bypassPermissions",
            "--system-prompt-snapshot",
            "off",
            "--append-system-prompt",
            brief,
            # Valor's plugin hooks (the web guard, the Brief to subagents) stay on
            # whatever the project's or the turn's own settings say: command-line
            # settings outrank both, and a settings file the turn writes reloads.
            "--settings",
            HOOKS_ON,
        ]
        for d in settings.plugin_dirs:
            if os.path.isdir(d):
                argv += ["--plugin-dir", d]
        if servers := user_mcp_servers():
            argv += ["--mcp-config", json.dumps({"mcpServers": servers})]
        # The session id is the kernel's, so where its transcript lies is
        # known before the turn starts.
        if resume:
            session_id = resume
            argv += ["--resume", resume]
        else:
            session_id = str(uuid.uuid4())
            argv += ["--session-id", session_id]
        argv += ["--", prompt]
        argv = [
            binaries.require(binaries.SANDBOX_EXEC),
            "-D",
            f"GATEWAY_PORT={urlparse(base_url).port}",
            "-D",
            f"VALOR_TURN={turn_id}",
            "-f",
            current_profile(harness["sandbox_profile"]),
            *argv,
        ]
        return TurnCommand(
            argv=argv,
            env=env,
            cwd=cwd,
            harness="claude_code",
            parse=parse,
            harness_version=version(settings.claude),
            transcript=transcripts.anchored(harness.get("claude_config_dir"), session_id),
            tmpdir=harness.get("tmpdir"),
        )

    return build
