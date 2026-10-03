"""Pi (`@mariozechner/pi-coding-agent`) turns, pointed at the kernel's gateway.

`workspace_turn` builds one turn of a task that works in a directory over
several turns, matching `claude_code.workspace_turn`: the same `harness`
settings, the same refusal without a sandbox profile, the same `TurnCommand`
back. Pi reaches OpenAI only through the gateway's OpenAI route
(`<gateway>/t/<token>/openai/v1`), so every call it makes is metered
against the task and carries no credential of the turn's.

**What Pi reads, and what the turn is left with.**

- Its agent directory is the turn's own (`PI_CODING_AGENT_DIR`, from
  `harness["pi_agent_dir"]`), and the kernel writes its `models.json` and
  `settings.json` before every turn: one provider, `valor`, whose base URL is
  the gateway and whose key is the placeholder the gateway replaces. The
  machine user's `~/.pi` is denied to every turn profile.
- The system prompt is always passed (`--system-prompt`), so a project's
  `.pi/SYSTEM.md` and `.pi/APPEND_SYSTEM.md` never replace it, and
  `--no-context-files` keeps `AGENTS.md` and `CLAUDE.md` out of it, in the
  clone and every directory above it. `--no-extensions --no-skills
  --no-prompt-templates --no-themes` keep a clone's `.pi/extensions`,
  `.pi/skills`, `.pi/prompts`, and `.pi/themes` from loading anything.
- A clone's `.pi/settings.json` is still read, and Pi has no flag against
  it. Read from the pinned release's source, it can change compaction,
  retry, thinking, the image settings, `shellPath`, `shellCommandPrefix`,
  `npmCommand`, and the package list; it cannot change the provider or model
  (flags), the tools (flags), the system prompt (flags), or the session
  directory (flag), and `--no-extensions` and `--no-skills` keep a settings
  `packages`, `extensions`, or `skills` list from loading anything. The
  working session's builder owns its clone, so its own settings stand there;
  a blind checkout leaves `.pi/` out (`workspace.blind_checkout`), so a
  candidate never sets what the verifier's turn runs with.

**The prompt travels on standard input.** The pinned release reads `--` as
an unknown flag, and a leading `@` on the command line is a file to read
into the prompt, so the prompt is given on stdin (`-p` merges piped stdin
into the prompt), where no character of it is an option or a file.

**Binaries.** Pi is a node script; it runs as `<node> <cli.js>` with both
named by absolute path, never the `env node` its shebang would resolve
through the turn's PATH. Both run inside the sandbox.
"""

import functools
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from core import binaries, spending
from core.gateway import TURN_TOKEN
from core.runs import TurnCommand
from core.settings import settings
from harnesses.claude_code import KEEP_ENV, Unsandboxed

# The release the contract suite ran on. The turn row records what ran.
PINNED = "0.73.1"

PACKAGE = "@mariozechner/pi-coding-agent"

# The provider name in the turn's `models.json`.
PROVIDER = "valor"

# Pi's replacement system prompt: Pi's own default would name Pi's docs and
# examples paths and is overridden by a project file, so the kernel states
# the role itself. The dispatched Brief is appended after it.
SYSTEM_PROMPT = (
    "You are a coding agent working in the current directory. You have four tools: "
    "read (read a file), bash (run a shell command), edit (replace text in a file), and "
    "write (create or overwrite a file). Do the task you are given, and say plainly "
    "what you did and what you could not do."
)

DEFAULT_MAX_OUTPUT_TOKENS = 32000


def context_window(model: str) -> int:
    """The window Pi compacts against: the input the model accepts, which is
    its published context window in the gateway's OpenAI price table
    (`spending.openai_prices`) less its maximum output. A request above that
    is refused as over the window (measured on gpt-6.1-sol: 901,587 input
    tokens accepted, 950,000 refused, against 1,050,000 less 128,000), so a
    window of the whole figure would have Pi compact only after a failed call."""
    price = spending.openai_prices(model)
    if price is None:
        raise ValueError(f"no context window for {model}: the OpenAI price table has no entry")
    return price["context_window"] - price["max_output"]


def _cost(model: str) -> dict[str, float]:
    """Pi's per-million-token cost fields for `model`, from the gateway's
    OpenAI price table (`spending.openai_prices`, task 3a). Pi cannot
    express the table's long-context rates or tier rates, so what Pi
    reports from these is an approximation; the ledger's charge from the
    gateway's meter is the record. A model the table does not price gives
    zeros."""
    price = spending.openai_prices(model) or {}
    rates = price.get("tiers", {}).get("default", {}).get("base", {})

    def per_million(key: str) -> float:
        return rates.get(key, 0) / 1_000_000  # micro-dollars to dollars

    return {
        "input": per_million("input"),
        "output": per_million("output"),
        "cacheRead": per_million("cached"),
        "cacheWrite": per_million("cache_write"),
    }


def models_json(base_url: str, model: str, max_output_tokens: int) -> str:
    return json.dumps(
        {
            "providers": {
                PROVIDER: {
                    "baseUrl": f"{base_url}/openai/v1",
                    "api": "openai-responses",
                    "apiKey": TURN_TOKEN,
                    "models": [
                        {
                            "id": model,
                            "name": model,
                            "reasoning": False,
                            "input": ["text"],
                            "contextWindow": context_window(model),
                            "maxTokens": max_output_tokens,
                            "cost": _cost(model),
                        }
                    ],
                }
            }
        },
        indent=2,
    )


def settings_json(model: str) -> str:
    return json.dumps(
        {
            "defaultProvider": PROVIDER,
            "defaultModel": model,
            "compaction": {"enabled": True},
            "retry": {"enabled": True},
            "quietStartup": True,
        },
        indent=2,
    )


def write_config(agent_dir: str | Path, files: dict[str, str]) -> None:
    """Replace each file in the turn's agent directory. The directory is the
    turn's to write, so the kernel opens it without following a link, removes
    the name (a link or a second hard link goes, its target stays), and
    creates the file new, never following a link."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC
    root = os.open(agent_dir, flags)
    try:
        for name, text in files.items():
            try:
                os.unlink(name, dir_fd=root)
            except FileNotFoundError:
                pass
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644,
                         dir_fd=root)  # fmt: skip
            with os.fdopen(fd, "w") as f:
                f.write(text)
    finally:
        os.close(root)


def _entry(path: str) -> list[str]:
    """The command that runs the installed `pi`: node and the script, by
    absolute path. A `pi` that resolves to something other than a script
    runs as itself."""
    real = os.path.realpath(path)
    return [settings.node, real] if real.endswith(".js") else [real]


@functools.cache
def version(path: str) -> str | None:
    """The installed package's version, from the `package.json` that is the
    parent of the resolved script's directory (`dist/cli.js`), never by
    running the binary. None for a layout this does not recognise."""
    real = Path(os.path.realpath(path))
    try:
        package = json.loads((real.parent.parent / "package.json").read_text())
    except OSError, ValueError:
        return None
    if not isinstance(package, dict) or package.get("name") != PACKAGE:
        return None
    found = package.get("version")
    return found if isinstance(found, str) else None


def _text(message: dict) -> str:
    parts = message.get("content")
    if isinstance(parts, str):
        return parts
    return "".join(p.get("text", "") for p in parts or [] if isinstance(p, dict) and p.get("type") == "text")


def parse(stdout: bytes) -> dict:
    """The fields of Pi's JSON event stream the ledger keeps, under the keys
    Claude Code's `parse` returns. Pi's own cost figure is kept beside the
    gateway's, never in place of it, and is an approximation (`_cost`).

    A stream with no session line, or none ending its run (`agent_end`),
    or whose last assistant message ended in an error or an abort, is an
    error: so is the unknown-session path, where Pi prints a note and exits
    cleanly.

    A compaction a turn triggers runs after `agent_end`, and in print mode
    the stream can end on its `compaction_start` with no `compaction_end`
    while the compaction still finishes and is saved to the session; such an
    entry has `finished: False`."""
    session_id = None
    ended = False
    turns = 0
    usd = 0.0
    last = None
    compactions: list[dict] = []
    for line in stdout.decode(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "session" and session_id is None:
            session_id = event.get("id")
        elif kind == "agent_end":
            ended = True
        elif kind == "turn_end":
            turns += 1
        elif kind == "message_end":
            message = event.get("message") or {}
            if message.get("role") == "assistant":
                last = message
                cost = (message.get("usage") or {}).get("cost") or {}
                usd += float(cost.get("total") or 0)
        elif kind == "compaction_start":
            compactions.append({"reason": event.get("reason"), "finished": False})
        elif kind == "compaction_end":
            result = event.get("result") or {}
            ended_with = {
                "reason": event.get("reason"),
                "finished": True,
                "tokens_before": result.get("tokensBefore"),
                "tokens_after": result.get("tokensAfter"),
                "aborted": event.get("aborted"),
                "will_retry": event.get("willRetry"),
                "error": event.get("errorMessage"),
            }
            if compactions and not compactions[-1]["finished"]:
                compactions[-1] = ended_with
            else:
                compactions.append(ended_with)
    failed = last is not None and last.get("stopReason") in ("error", "aborted")
    return {
        "text": _text(last) if last else None,
        "is_error": session_id is None or not ended or failed,
        "num_turns": turns,
        "harness_reported_usd": usd,
        "session_id": session_id,
        "compaction": compactions,
    }


def workspace_turn(
    prompt: str,
    *,
    cwd: str,
    resume: str | None = None,
    model: str = "gpt-6.1-sol",
    harness: dict | None = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
):
    """A builder for one turn of a task that works in `cwd`.

    `harness` is `claude_code.workspace_turn`'s, plus `pi_agent_dir`, the
    turn's own Pi directory (made by provisioning; required). The builder
    appends the dispatched Brief (and only that: the persona, when a task
    renders one, arrives inside it) to Pi's system prompt, resumes `resume`
    by session id when given, and gives the prompt on stdin. A turn runs
    its commands in the foreground: Pi's `-p` ends the run when the model
    stops.
    """
    harness = harness or {}
    if not harness.get("sandbox_profile"):
        raise Unsandboxed(
            "a workspace turn runs under a sandbox profile; start the task with "
            "--harness-config naming one (`sandbox_profile`)"
        )
    if not harness.get("pi_agent_dir"):
        raise ValueError(
            "a Pi turn needs `pi_agent_dir` in the harness settings: the turn's own Pi directory"
        )
    max_output_tokens = harness.get("max_output_tokens", max_output_tokens)
    agent_dir = harness["pi_agent_dir"]

    def build(base_url: str, brief: str, turn_id: str) -> TurnCommand:
        write_config(agent_dir, {"models.json": models_json(base_url, model, max_output_tokens),
                                 "settings.json": settings_json(model)})  # fmt: skip
        env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
        env.update(harness.get("env", {}))
        env["PI_CODING_AGENT_DIR"] = agent_dir
        env["PI_OFFLINE"] = "1"
        env["GIT_TERMINAL_PROMPT"] = "0"
        if harness.get("gitconfig"):
            env["GIT_CONFIG_GLOBAL"] = harness["gitconfig"]
            env["GIT_CONFIG_NOSYSTEM"] = "1"
        if harness.get("gh_config_dir"):
            env["GH_CONFIG_DIR"] = harness["gh_config_dir"]
        if harness.get("tmpdir"):
            env["TMPDIR"] = harness["tmpdir"]
        argv = [
            *_entry(settings.pi),
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--offline",
            "--provider",
            PROVIDER,
            "--model",
            model,
            "--mode",
            "json",
            "--session-dir",
            str(Path(agent_dir) / "sessions"),
        ]
        if resume:
            argv += ["--session", resume]
        argv += ["--system-prompt", SYSTEM_PROMPT, "--append-system-prompt", brief, "-p"]
        # The sandbox's own launcher runs outside it, so it is the root-owned
        # /usr/bin/sandbox-exec by absolute path, as for Claude Code.
        argv = [
            binaries.require(binaries.SANDBOX_EXEC),
            "-D",
            f"GATEWAY_PORT={urlparse(base_url).port}",
            "-D",
            f"VALOR_TURN={turn_id}",
            "-f",
            harness["sandbox_profile"],
            *argv,
        ]
        return TurnCommand(
            argv=argv,
            env=env,
            cwd=cwd,
            harness="pi",
            parse=parse,
            harness_version=version(settings.pi),
            stdin=prompt.encode(),
        )

    return build
