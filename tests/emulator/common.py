"""What the emulator's modules share: where their mess lives, the machine's
turn slots, the kernel's command line, and the metered `claude -p` call the
stand-in and the judge make.

Everything a replay writes (caches, workspaces, results, logs) lives under
`DEMO`, the `demo_dir` setting (`core/settings.py`).

Every stand-in and judge call goes through a `Meter`: the kernel's own
gateway, run by the driver on an event loop in a thread of its own, metering
each call onto one emulator task (a calibration task, which only meters). A
call's process holds no credential: it carries the gateway's placeholder
and a fresh empty Claude Code config, and the gateway sets the kernel's
login on the way out.
"""

import asyncio
import fcntl
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from core import db, ledger, tasks
from core import git as kgit
from core.gateway import TURN_TOKEN, ClaudeLogin, Gateway
from core.settings import settings
from harnesses.claude_code import DROP_ENV
from tests.ports import listen

ROOT = Path(__file__).resolve().parent.parent.parent
DEMO = Path(settings.demo_dir).resolve()
PYTHON = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv").exists() else sys.executable
CLAUDE = settings.claude
LOCK = DEMO / "claude-turn.lock"
SLOTS = int(os.environ.get("VALOR_DEMO_SLOTS", "3"))


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


def core(*args: str) -> str:
    """`python -m core ARGS` against the kernel's own database."""
    return sh(PYTHON, "-m", "core", *args, cwd=ROOT)


def status(task_id: str) -> dict:
    return json.loads(core("status", task_id))


@contextmanager
def machine_lock(holder: str):
    """At most `SLOTS` replays' claude turns at once on this Mac: every
    replay driver holds one slot (a lock file) for its whole run and takes
    whichever is free, waiting while none is."""
    DEMO.mkdir(parents=True, exist_ok=True)
    waited = False
    while True:
        for i in range(SLOTS):
            f = LOCK.with_name(f"{LOCK.name}.{i}").open("a+")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                f.close()
        else:
            if not waited:
                print(f"waiting for one of {SLOTS} machine slots", file=sys.stderr)
                waited = True
            time.sleep(5)
            continue
        break
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
        f.close()


class Meter:
    """The driver's gateway on a loop thread of its own, metering every
    stand-in and judge call onto `task_id`. `issue` and `retire` are the
    gateway's plain methods and `drain` its coroutine; each runs on the
    gateway's loop, called from the driver's thread."""

    def __init__(
        self, task_id: str, *, dsn: str | None = None, upstream: str | None = None, login: bool = True
    ):
        self.task_id = task_id
        self.dsn = dsn or settings.dsn()
        self.gateway = Gateway(self.dsn, upstream=upstream, credential=ClaudeLogin() if login else None)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="emulator-gateway", daemon=True)

    def __enter__(self) -> Self:
        self.thread.start()
        self._await(self.gateway.start(port=listen()))
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.drain()
            self._await(self.gateway.close())
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join()
            self.loop.close()

    def _await(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def _on_loop(self, fn, *args):
        async def call():
            return fn(*args)

        return self._await(call())

    def issue(self, call_id: str) -> str:
        return self._on_loop(self.gateway.issue, self.task_id, call_id)

    def retire(self) -> None:
        self._on_loop(self.gateway.retire, self.task_id)

    def drain(self) -> None:
        """Wait until every call of the task is charged: a call's charge is
        written after its response ends, and can land after its process
        exits."""
        self._await(self.gateway.drain(self.task_id))

    def spend(self) -> dict:
        """The emulator task's metered spending, after a drain."""
        self.drain()
        return spend_of(self.task_id, dsn=self.dsn)


def spend_of(task_id: str, *, dsn: str | None = None) -> dict:
    async def read():
        async with await db.connect(dsn) as conn:
            return await tasks.status(conn, task_id)

    state = asyncio.run(read())
    return {"usd": state["spent_usd_micros"] / 1e6, "open_calls": state["open_calls"]}


def wait_run_lock(task_id: str, *, dsn: str | None = None) -> None:
    """Block until no run of the task holds its run lock (the session
    advisory lock on `run:<task>` that `core/router.py` takes), then let it
    go at once: the next `core run` takes it for itself."""

    async def wait():
        conn = await db.connect(dsn)
        try:
            key = f"run:{task_id}"
            await conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (key,))
            await conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
        finally:
            await conn.close()

    asyncio.run(wait())


def start_emulator_task(run: str, item_task: str | None, *, dsn: str | None = None) -> str:
    """One emulator task per run: a calibration task, which only meters."""

    async def start():
        async with await db.connect(dsn) as conn:
            return await tasks.start_calibration(
                conn,
                "emulator",
                by="replay driver",
                via="python -m tests.emulator.replay",
                detail={
                    "instruction": f"meter emulator run {run}",
                    "emulator": {"run": run, "item_task": item_task},
                },
            )

    return asyncio.run(start())


def call_env(url: str, config_dir: Path) -> dict[str, str]:
    """A stand-in's or judge's environment: the driver's, less every
    credential-shaped variable the kernel's turns drop, pointed at the
    gateway with the placeholder and an empty Claude Code config."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(DROP_ENV) and k != "AI_AGENT"}
    env.update(
        {
            "ANTHROPIC_BASE_URL": url,
            "CLAUDE_CODE_OAUTH_TOKEN": TURN_TOKEN,
            "CLAUDE_CONFIG_DIR": str(config_dir),
        }
    )
    return env


def claude_json(prompt: str, *, system: str, model: str, meter: Meter, call_id: str, workdir: Path) -> dict:
    """One tool-less `claude -p` call through `meter`'s gateway, its token
    issued for this call and retired after it however it ends. `workdir`
    (the run's directory) holds the call's fresh Claude Code config.
    Returns the reply text and the parsed JSON object the reply holds (None
    when it holds none); the spend is in the emulator task's rows."""
    config_dir = Path(workdir) / "claude" / f"{call_id}-{ledger.new_id()}"
    config_dir.mkdir(parents=True)
    url = meter.issue(call_id)
    try:
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
            env=call_env(url, config_dir),
            cwd=workdir,
            check=False,
        )
    finally:
        meter.retire()
    try:
        result = json.loads(out.stdout)
    except ValueError:
        raise RuntimeError(f"claude -p returned no JSON ({out.returncode}): {out.stderr[-600:]}") from None
    text = result.get("result") or ""
    if result.get("is_error"):
        raise RuntimeError(f"claude -p failed: {text[:600]}")
    return {"text": text, "json": _json_in(text), "call_id": call_id, "model": model}


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


# -- kernel-owned reads ----------------------------------------------------------


async def _rows(task_id: str, dsn: str | None = None) -> list[dict]:
    async with await db.connect(dsn) as conn:
        return await ledger.read(conn, task_id)


def rows(task_id: str, *, dsn: str | None = None) -> list[dict]:
    return asyncio.run(_rows(task_id, dsn))


def review_rev(task_id: str, state: dict, *, dsn: str | None = None) -> str | None:
    """The commit a delivery is read at: the done merge's `head_sha` (the
    payload of the task's `effect.intent` row for `merge_effect`), else the
    current candidate."""
    effect = state.get("merge_effect")
    if effect and effect.get("state") == "done":
        for r in rows(task_id, dsn=dsn):
            if r["type"] == "effect.intent" and r["payload"].get("effect_id") == effect["effect_id"]:
                return r["payload"]["payload"]["head_sha"]
    candidate = state.get("candidate")
    return candidate["sha"] if candidate else None


def mirror_diff(mirror: str | Path, base: str, rev: str, *args: str) -> str:
    """`git diff base rev` in the task's kernel mirror, a repository no turn
    writes."""
    return kgit.trusted(mirror, "diff", "--no-ext-diff", "--no-textconv", *args, base, rev)
