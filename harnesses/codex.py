"""Codex CLI (`codex exec`) turns, pointed at the kernel's gateway.

`workspace_turn` builds one turn of a task that works in a directory over
several turns, matching `claude_code.workspace_turn`: the same `harness`
settings, the same refusal without a sandbox profile, the same `TurnCommand`
back. Ported from the old system's adapter
(`agent/session_runner/harness/codex.py`): the argv order, the prompt on
stdin, and the JSONL events `parse` reads.

**What Codex reads.** Its home is the turn's own (`CODEX_HOME`, from
`harness["codex_home"]`), holding no login, and the kernel writes its
`config.toml` before every turn: one provider, `valor`, on the gateway's
OpenAI route (`<gateway>/t/<token>/openai/v1`, the Responses API), whose key
is the placeholder the gateway replaces, so every call is metered against
the task; `CLAUDE.md` as the fallback for a directory with no `AGENTS.md`;
and the operator's user MCP servers (`claude_code.user_mcp_servers`). Each
yudame plugin's `skills` directory is linked into `<CODEX_HOME>/skills`,
which Codex searches beside the project's `.agents/skills` and the
operator's `~/.agents/skills`. The turn edits and runs commands without
asking (`--dangerously-bypass-approvals-and-sandbox`), as Claude Code's
`bypassPermissions`: it already runs under the turn's sandbox profile.

**The Brief.** A new session gets it as `developer_instructions`, after
Codex's own instructions. A resumed session keeps the developer message it
started with and reads no new one (measured on 0.153.4), so a resumed turn's
Brief, re-rendered from the ledger, opens the prompt.

**The prompt travels on standard input** (`-`), so no character of it is
an option.
"""

import functools
import json
import os
import stat
from pathlib import Path
from urllib.parse import urlparse

from core import binaries, transcripts
from core.gateway import TURN_TOKEN
from core.runs import TurnCommand
from core.settings import settings
from core.workspace import current_profile, environment
from harnesses.claude_code import Unsandboxed, user_mcp_servers
from harnesses.pi import context_window, write_config

# The provider name in the turn's `config.toml`, and the variable its key is read from.
PROVIDER = "valor"
KEY_VARIABLE = "VALOR_TURN_KEY"


def _toml(value) -> str:
    """`value` as TOML: JSON's strings, numbers, booleans and arrays are
    TOML's, and a mapping is an inline table. TOML takes no surrogate escape
    and no raw DEL, so a string keeps its characters and DEL is escaped."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_toml(str(k))} = {_toml(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _mcp_server(server: dict) -> dict:
    """A Claude Code MCP server entry as Codex's: a command and its
    arguments, environment and directory, or a URL and its headers."""
    if "url" in server:
        return {
            "url": server["url"],
            **({"http_headers": server["headers"]} if server.get("headers") else {}),
        }
    keys = ("command", "args", "env", "cwd")
    return {k: server[k] for k in keys if server.get(k)}


def config_toml(base_url: str, model: str, brief: str) -> str:
    lines = [
        f"model_provider = {_toml(PROVIDER)}",
        f"model_context_window = {context_window(model)}",
        'approval_policy = "never"',
        'project_doc_fallback_filenames = ["CLAUDE.md"]',
        f"developer_instructions = {_toml(brief)}",
        f"[model_providers.{PROVIDER}]",
        f"name = {_toml(PROVIDER)}",
        f"base_url = {_toml(f'{base_url}/openai/v1')}",
        f"env_key = {_toml(KEY_VARIABLE)}",
        'wire_api = "responses"',
        "supports_websockets = false",
    ]
    for name, server in user_mcp_servers().items():
        lines += [f"[mcp_servers.{_toml(name)}]"]
        lines += [f"{k} = {_toml(v)}" for k, v in _mcp_server(server).items()]
    return "\n".join(lines) + "\n"


def link_skills(codex_home: str) -> None:
    """Make `<codex_home>/skills` hold one link per plugin in `settings.plugin_dirs`
    (Valor's own and yudame's), named by
    the plugin, to its `skills` directory. Every link there is the kernel's,
    so each is removed first; what Codex keeps there itself (`.system`) is
    no link. The home and `skills` are opened without following a link."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC
    home = os.open(codex_home, flags)
    try:
        try:
            os.mkdir("skills", 0o755, dir_fd=home)
        except FileExistsError:
            pass
        skills = os.open("skills", flags, dir_fd=home)
    finally:
        os.close(home)
    try:
        for entry in os.listdir(skills):
            if stat.S_ISLNK(os.stat(entry, dir_fd=skills, follow_symlinks=False).st_mode):
                os.unlink(entry, dir_fd=skills)
        for d in settings.plugin_dirs:
            if os.path.isdir(target := os.path.join(d, "skills")):
                os.symlink(target, os.path.basename(d), dir_fd=skills)
    finally:
        os.close(skills)


@functools.cache
def version(path: str) -> str | None:
    """The installed release, from the standalone install directory the
    resolved `codex` names (`.../releases/0.153.4-aarch64-apple-darwin/bin/codex`),
    never by running the binary. None for a layout this does not recognise."""
    release = Path(os.path.realpath(path)).parent.parent
    return release.name.split("-")[0] if release.parent.name == "releases" else None


def parse(stdout: bytes) -> dict:
    """The fields of Codex's JSONL event stream the ledger keeps, under the
    keys Claude Code's `parse` returns. Codex reports no cost. A stream with
    no `thread.started`, or none ending its turn (`turn.completed`), or with
    `turn.failed` or an `error` event, is an error. An `error` item is a
    warning Codex printed (an unknown model's metadata, a long thread), not
    a failure."""
    session_id = text = None
    completed = failed = False
    for line in stdout.decode(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "thread.started":
            session_id = event.get("thread_id")
        elif kind == "turn.completed":
            completed = True
        elif kind in ("turn.failed", "error"):
            failed = True
        elif kind == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                text = item.get("text")
    return {
        "text": text,
        "is_error": session_id is None or not completed or failed,
        "session_id": session_id,
    }


def workspace_turn(
    prompt: str,
    *,
    cwd: str,
    resume: str | None = None,
    model: str = "gpt-6.1-sol",
    harness: dict | None = None,
):
    """A builder for one turn of a task that works in `cwd`.

    `harness` is `claude_code.workspace_turn`'s, plus `codex_home`, the
    turn's own Codex home (made by provisioning; required), where its
    session's rollout lies. Codex has no output cap to set, so
    `max_output_tokens` is not read. The builder resumes `resume` by thread
    id when given.
    """
    harness = harness or {}
    if not harness.get("sandbox_profile"):
        raise Unsandboxed(
            "a workspace turn runs under a sandbox profile; start the task with "
            "--harness-config naming one (`sandbox_profile`)"
        )
    if not harness.get("codex_home"):
        raise ValueError("a Codex turn needs `codex_home` in the harness settings: the turn's own Codex home")
    codex_home = harness["codex_home"]

    def build(base_url: str, brief: str, turn_id: str) -> TurnCommand:
        write_config(codex_home, {"config.toml": config_toml(base_url, model, brief)})
        link_skills(codex_home)
        env = environment(harness.get("env", {}))
        env["CODEX_HOME"] = codex_home
        env[KEY_VARIABLE] = TURN_TOKEN
        env["VALOR_RECORD_URL"] = f"{base_url}/record"  # valor-record (core/record.py)
        env["GIT_TERMINAL_PROMPT"] = "0"
        if harness.get("tmpdir"):
            env["TMPDIR"] = harness["tmpdir"]
        argv = [settings.codex, "exec", "--json", "--dangerously-bypass-approvals-and-sandbox",
                "--skip-git-repo-check", "-m", model]  # fmt: skip
        if resume:
            argv += ["resume", resume]
        argv += ["-"]
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
            harness="codex",
            parse=parse,
            harness_version=version(settings.codex),
            stdin=(f"{brief}\n\n{prompt}" if resume else prompt).encode(),
            # Codex names the session: the rollout is found by the id `parse` reads.
            transcript=transcripts.anchored(codex_home, None, layout="codex"),
            tmpdir=harness.get("tmpdir"),
        )

    return build
