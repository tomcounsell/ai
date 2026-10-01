"""What the replay scripts share: where their mess lives, the machine's one
turn lock, the kernel's command line, and a plain `claude -p` call for the
stand-in and the judge.

Everything a replay writes (caches, workspaces, results, logs) lives under
`DEMO`, `/Users/tomcounsell/src/valor-demo` unless `VALOR_DEMO` says
otherwise.
"""

import fcntl
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = Path(os.environ.get("VALOR_DEMO", "/Users/tomcounsell/src/valor-demo")).resolve()
PYTHON = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv").exists() else sys.executable
CLAUDE = "claude"
COSTS = DEMO / "costs.jsonl"
LOCK = DEMO / "claude-turn.lock"


def now() -> str:
    return datetime.now(UTC).isoformat()


def sh(*args: str, cwd: str | Path | None = None, check: bool = True, **kw) -> str:
    out = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False, **kw)
    if check and out.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, args))} failed ({out.returncode}):\n{out.stderr.strip()}")
    return out.stdout.strip()


def ok(*args: str, **kw) -> bool:
    """Whether a command exits 0."""
    return subprocess.run(args, capture_output=True, check=False, **kw).returncode == 0


def git(cwd: str | Path, *args: str, check: bool = True) -> str:
    return sh("git", "-C", str(cwd), *args, check=check)


def ws_git(workspace: str | Path, *args: str, check: bool = True) -> str:
    """git on a workspace a sandboxed turn could configure, run outside the
    sandbox: no hooks, no fsmonitor. Diffs also pass `--no-ext-diff
    --no-textconv`, and nothing here touches the working tree, so no filter
    the turn defined runs."""
    pinned = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
    return sh("git", "-C", str(workspace), *pinned, *args, check=check)


def core(*args: str) -> str:
    """`python -m core ARGS` against the kernel's own database."""
    return sh(PYTHON, "-m", "core", *args, cwd=ROOT)


def status(task_id: str) -> dict:
    return json.loads(core("status", task_id))


@contextmanager
def machine_lock(holder: str):
    """One claude turn at a time on this Mac (16 GB): every replay driver
    holds this for its whole run, so two drivers never overlap."""
    DEMO.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.seek(0)
            print(f"waiting for the machine lock, held by: {f.read().strip() or 'unknown'}", file=sys.stderr)
            fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        f.truncate()
        f.write(f"{holder} pid {os.getpid()} since {now()}\n")
        f.flush()
        try:
            yield
        finally:
            f.seek(0)
            f.truncate()
            fcntl.flock(f, fcntl.LOCK_UN)


def claude_json(prompt: str, *, system: str, model: str, purpose: str, subject: str) -> dict:
    """One tool-less `claude -p` call outside the kernel and its sandbox,
    with this machine's own credentials. Not metered by any task budget:
    its cost goes to `COSTS` instead. Returns the reply text, the cost, and
    the parsed JSON object the reply holds (None when it holds none)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE") and k != "AI_AGENT"}
    started = time.monotonic()
    out = subprocess.run(
        [
            CLAUDE,
            "-p",
            "--output-format",
            "json",
            "--model",
            model,
            "--safe-mode",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--tools",
            "",
            "--system-prompt",
            system,
        ],
        input=prompt,
        capture_output=True,
        text=True,
        env=env,
        cwd=DEMO,
        check=False,
    )
    try:
        result = json.loads(out.stdout)
    except ValueError:
        raise RuntimeError(f"claude -p returned no JSON ({out.returncode}): {out.stderr[-600:]}") from None
    text = result.get("result") or ""
    usd = result.get("total_cost_usd") or 0.0
    with COSTS.open("a") as f:
        f.write(
            json.dumps(
                {
                    "at": now(),
                    "purpose": purpose,
                    "subject": subject,
                    "model": model,
                    "usd": usd,
                    "seconds": round(time.monotonic() - started, 1),
                    "is_error": result.get("is_error"),
                }
            )
            + "\n"
        )
    if result.get("is_error"):
        raise RuntimeError(f"claude -p failed: {text[:600]}")
    return {"text": text, "usd": usd, "json": _json_in(text)}


def _json_in(text: str) -> dict | None:
    """The last JSON object in a reply, fenced or bare."""
    start = text.find("{")
    end = text.rfind("}")
    while start != -1 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
        start = text.find("{", start + 1)
    return None
