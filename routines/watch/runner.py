"""The `watch` routine (docs/plans/a4601299-db-self-recovery.md): a Mac
watches the Macs in `VALOR_WATCH` (the Cowboy the Captain, the Pirate and
the Bald; the Captain the Cowboy) and fixes what the watched Mac could not.
Deterministic: no turn decides anything.

Each run reads each target's `python -m core health` over ssh
(`BatchMode`, a 10 s connect timeout):

- healthy: the hold files `~/.config/brew-nightly/hold-<host>` on the
  target and here are deleted, so the nightly upgrades both again;
- broken: the hold files are written (`watch.held`, the `nightly.hold`
  guard's firing), so neither Mac takes the nightly upgrade that may have
  broken it. Broken at the run before too, the target has had a watch's
  time to heal itself (`core/health.py`), and `health --fix` runs there:
  healthy after it is `watch.fixed`, recorded and never reported; still
  broken is `watch.failed`, reported once;
- unreachable (asleep, off, Tailscale down): `watch.unreachable` and no
  hold, since a Mac that cannot be read holds no one's updates; reported
  once when it has been unreachable `UNREACHABLE_REPORT`.

A row is written only when a target's state changes. A report goes to the
operator chat's session (`notices.request`), Valor's own voice in Tom's DM;
on a Mac with no session to hear it (the Captain, watching the Cowboy whose
session may be what is down), it is a fatal Sentry event instead.
"""

import asyncio
import json
import shlex
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import sentry_sdk

from core import intake, ledger, notices
from core.routines import Context, Ran
from core.settings import settings

HOLDS = "~/.config/brew-nightly"
UNREACHABLE_REPORT = timedelta(hours=24)
SSH = ("/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
HEALTH = "cd ~/src/ai && .venv/bin/python -m core health"
STREAM = "watch"

Remote = Callable[[str, str], Awaitable[tuple[int, str]]]


async def ssh(host: str, command: str) -> tuple[int, str]:
    """Run `command` on `host`: its exit status and output."""
    proc = await asyncio.create_subprocess_exec(
        *SSH, host, command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 300)
    except TimeoutError:
        proc.kill()
        return 255, "ssh did not return in 300 s"
    return proc.returncode or 0, out.decode(errors="replace")


async def _health(remote: Remote, host: str, fix: bool = False) -> dict[str, Any] | None:
    """The target's health report; None when it cannot be read."""
    status, out = await remote(host, HEALTH + (" --fix" if fix else ""))
    lines = [line for line in out.splitlines() if line.startswith("{")]
    if status != 0 or not lines:
        return None
    try:
        return json.loads(lines[-1])
    except ValueError:
        return None


def _hold(host: str, on: bool) -> str:
    path = f"{HOLDS}/hold-{shlex.quote(host)}"
    return f"mkdir -p {HOLDS} && touch {path}" if on else f"rm -f {path}"


async def _holds(remote: Remote, host: str, on: bool, here: Path) -> None:
    """The hold file for `host` on the target and on this Mac."""
    await remote(host, _hold(host, on))
    mine = here / f"hold-{host}"
    if on:
        here.mkdir(parents=True, exist_ok=True)
        mine.touch()
    else:
        mine.unlink(missing_ok=True)


def _broken(h: dict[str, Any]) -> str:
    down = [label for label, pid in (h.get("services") or {}).items() if pid is None]
    parts = [f"database: {h['probe']}"] if h.get("probe") else []
    if down:
        parts.append(f"not running: {', '.join(down)}")
    return "; ".join(parts) + f"; {h.get('free_gb')} GB free"


async def _last(conn, objective: str) -> dict[str, dict[str, Any]]:
    """Each target's latest state row on the objective."""
    found: dict[str, dict[str, Any]] = {}
    for row in await ledger.read(conn, objective):
        if row["type"].startswith("watch."):
            found[row["payload"]["host"]] = {
                "id": row["id"],
                "at": row["at"],
                "type": row["type"],
                **row["payload"],
            }
    return found


async def _tell(conn, key: str, text: str) -> None:
    """Once per `key`: to the operator chat's session, or to Sentry when this
    Mac has none to hear it (the report row, quiet, keeps it once)."""
    made = await notices.request(conn, STREAM, kind="rollout", about_key=key, text=text)
    if made is not None and await intake.operator_session(conn) is None:
        sentry_sdk.capture_message(text, level="fatal")


def runner(*, remote: Remote = ssh, holds: Path | None = None, targets: tuple[str, ...] | None = None):
    """The `watch` runner; tests give their own `remote`, hold directory and targets."""

    async def run(ctx: Context) -> Ran:
        watched = settings.watch if targets is None else targets
        if not watched:
            return Ran(None, "nothing_due", "this Mac watches no other")
        here = holds or Path(HOLDS).expanduser()
        last = await _last(ctx.conn, ctx.objective)
        said = []
        for host in watched:
            was = last.get(host) or {}
            state, detail = await _watch(ctx, remote, host, was, here)
            said.append(f"{host}: {state}")
            if state != was.get("type", "watch.healthy").removeprefix("watch."):
                await ledger.append(ctx.conn, ctx.objective, f"watch.{state}", {"host": host, **detail})
        return Ran(None, "finished", "; ".join(said))

    async def _watch(ctx, remote, host, was, here) -> tuple[str, dict[str, Any]]:
        h = await _health(remote, host)
        if h is None:
            if was.get("type") == "watch.unreachable" and ctx.now - was["at"] >= UNREACHABLE_REPORT:
                await _tell(ctx.conn, f"watch:{host}:unreachable:{was['id']}",
                            f"I have not been able to reach {host} since {was['at']:%Y-%m-%d %H:%M} UTC "
                            "(asleep, off, or off the network). Nothing on it is held back for this.")  # fmt: skip
            return "unreachable", {}
        if h.get("healthy"):
            await _holds(remote, host, False, here)
            return "healthy", {}
        await _holds(remote, host, True, here)
        if was.get("type") not in ("watch.held", "watch.failed"):
            return "held", {"detail": _broken(h)}
        fixed = await _health(remote, host, fix=True)
        after = await _health(remote, host)
        if after is not None and after.get("healthy"):
            await _holds(remote, host, False, here)
            return "fixed", {"detail": _broken(h), "fixed": (fixed or {}).get("fixed")}
        detail = _broken(after or h)
        if was.get("type") != "watch.failed":
            await _tell(ctx.conn, f"watch:{host}:failed:{was.get('id')}",
                        f"{host} is broken and neither it nor I could fix it: {detail}. "
                        "Its nightly updates are held until it is healthy.")  # fmt: skip
        return "failed", {"detail": detail, "fixed": (fixed or {}).get("fixed")}

    return run
