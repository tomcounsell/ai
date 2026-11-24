"""The `watch` routine (docs/plans/a4601299-db-self-recovery.md): a Mac
watches the Macs in `VALOR_WATCH` (the Cowboy the Captain, the Pirate and
the Bald; the Captain the Cowboy) and fixes what the watched Mac could not.
Deterministic: no turn decides anything.

Each run reads each target's `python -m core health` over ssh
(`core/remote.py`: `BatchMode`, a 10 s connect timeout):

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

Each run also reports, once a day, each read target and this Mac whose
free disk is under `DISK_LOW_GB` (`_disk`), and repairs each read target
whose update is failing (`_repair`): a failing update is not reported
(Tom, 2026-11-24: "don't report to me. self-heal"). Only a peer repairs:
every Mac is another's target (the Cowboy reads the other three, the
Captain the Cowboy), so each failure is seen once, and a repair turn on the
failing Mac would itself hold the update it waits for. It starts a task under the `valor`
project while it still fails, one task at a time, at most one a day per Mac (a repair that ended with the failure still open is tried again the next day), the routine's own work, so its
end is a quiet result and only its question, for what only Tom holds,
wakes the operator chat's session; the task finds why, runs `python -m
core realign` on a diverged checkout, and confirms the next update. Each
branch `realign` kept local commits on (`health`'s `kept`) gets one task
to land or drop it.

A row is written only when a target's state changes. A report goes to the
operator chat's session (`notices.request`), Valor's own voice in Tom's DM;
on a Mac with no session to hear it (the Captain, watching the Cowboy whose
session may be what is down), it is a fatal Sentry event instead.
"""

import json
import shlex
import shutil
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import sentry_sdk

from core import intake, ledger, machine, notices, tasks
from core.remote import KERNEL, ssh
from core.routines import Context, Ran
from core.settings import settings

HOLDS = "~/.config/brew-nightly"
UNREACHABLE_REPORT = timedelta(hours=24)
HEALTH = f"{KERNEL} health"
STREAM = "watch"
# Below this much free disk a Mac is reported, once a day (Tom, 2026-10-22).
DISK_LOW_GB = 15

Remote = Callable[[str, str], Awaitable[tuple[int, str]]]


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


async def _disk(ctx, host: str, free: float | None) -> None:
    """Once a day per Mac, a report when its free disk is under `DISK_LOW_GB`.
    No Sentry event where no session hears it: each Mac's watch reports its
    own disk, and the Cowboy's reaches Tom (the Captain's own notices went
    unheard six times before its disk filled, 2026-11-19)."""
    if free is not None and free < DISK_LOW_GB:
        await notices.request(ctx.conn, STREAM, kind="rollout", about_key=f"disk:{host}:{ctx.now:%Y-%m-%d}",
                              text=f"{host} has {free:.0f} GB of disk left. Builds start failing when it fills; "
                              "what should go?")  # fmt: skip


REPAIR = (
    "{host}'s update has failed since {since} UTC, at {step}: {reason}. It takes no merged change until "
    'that is cleared. Repair it yourself; Tom is not told (2026-11-24: "don\'t report to me. self-heal"). '
    "Reach it with `ssh {host}`; a step that reads ~/Desktop/Valor runs as a one-off gui launchd job through "
    "~/Applications/Valor.app's valor-run. Find why from its ~/Library/Logs/valor/kernel.log and its "
    "ledger's rollout.failed rows. Where its live checkout ~/src/ai has diverged from origin (a commit made "
    "there), run `cd ~/src/ai && .venv/bin/python -m core realign` on it: it keeps the local commits on a "
    "branch pushed to origin, puts the checkout back on its branch's tip and requests an update; the watch "
    "gives the kept branch its own task. Any other cause, fix it as a developer at that Mac would, deleting "
    "nothing. Then confirm the next update ends done and its services restarted (`.venv/bin/python -m core "
    "update --wait` there). A fix to Valor's code is a commit with a test here; with nothing to merge, end "
    "with stop.md. Ask in question.md only for what only Tom holds, such as a credential; nothing else "
    "reaches him."
)
LAND = (
    "{name} had local commits on its live checkout of Valor's repository, kept on branch {branch} at {sha} "
    "(pushed to origin) when `python -m core realign` put it back on main. Review that work: land it on main "
    "here with a test if it is sound, or end with stop.md saying why it should be dropped. Delete nothing."
)


async def _started(ctx, key: str, value: str) -> bool:
    """Whether a task already carries `{key: value}` in its `task.started`."""
    found = await (
        await ctx.conn.execute(
            "SELECT 1 FROM events WHERE type = 'task.started' AND payload->>%s = %s LIMIT 1", (key, value)
        )
    ).fetchone()
    return found is not None


async def _start(ctx, key: str, value: str, instruction: str) -> None:
    """One task under the `valor` project for `{key: value}`, the watch's own
    work (`routine`), a child of its objective, unless one was started or
    this run cannot start one."""
    if ctx.start_project is None or await _started(ctx, key, value):
        return
    await ctx.start_project(
        ctx.conn, parent=ctx.objective, instruction=instruction, routine=ctx.routine.name,
        ceiling=ctx.routine.ceiling, model=ctx.routine.model, project=tasks.VALOR, branch=None,
        marker={"sdlc": 1, key: value},
    )  # fmt: skip


async def _repairing(ctx, host: str) -> bool:
    """Whether a repair task for `host` is still open: running, or waiting on
    its question."""
    rows = await (
        await ctx.conn.execute(
            "SELECT task_id FROM events WHERE type = 'task.started' AND payload->>'repair' LIKE %s",
            (f"{host}:%",),
        )
    ).fetchall()
    for (task_id,) in rows:
        if machine.fold(await ledger.read(ctx.conn, task_id)).state not in tasks.TERMINAL:
            return True
    return False


async def _repair(ctx, host: str, h: dict[str, Any]) -> None:
    """A repair of a failing update while it is still failing, at most once a
    day per Mac (the `disk:` report's day stamp) and only when no repair of
    that Mac is open (Tom, 2026-11-24: "self-heal": a repair that ended with
    the failure still open is tried again the next day), and a task for each
    branch its realign kept."""
    update = h.get("update")
    if update and not await _repairing(ctx, host):
        await _start(ctx, "repair", f"{host}:{update.get('since')}:{ctx.now:%Y-%m-%d}", REPAIR.format(
            host=host, since=update.get("since"), step=update.get("step"),
            reason=str(update.get("reason"))[:500]))  # fmt: skip
    for k in h.get("kept") or ():
        await _start(ctx, "kept", f"{host}:{k['sha']}", LAND.format(name=host, **k))


def runner(*, remote: Remote = ssh, holds: Path | None = None, targets: tuple[str, ...] | None = None):
    """The `watch` runner; tests give their own `remote`, hold directory and targets."""

    async def run(ctx: Context) -> Ran:
        watched = settings.watch if targets is None else targets
        if not watched:
            return Ran(None, "nothing_due", "this Mac watches no other")
        here = holds or Path(HOLDS).expanduser()
        last = await _last(ctx.conn, ctx.objective)
        said = []
        await _disk(ctx, settings.machine, shutil.disk_usage(Path.home()).free / 1e9)
        for host in watched:
            was = last.get(host) or {}
            state, detail, h = await _watch(ctx, remote, host, was, here)
            said.append(f"{host}: {state}")
            if state != was.get("type", "watch.healthy").removeprefix("watch."):
                await ledger.append(ctx.conn, ctx.objective, f"watch.{state}", {"host": host, **detail})
            if h is not None:
                await _repair(ctx, host, h)
        return Ran(None, "finished", "; ".join(said))

    async def _watch(ctx, remote, host, was, here) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
        h = await _health(remote, host)
        if h is None:
            if was.get("type") == "watch.unreachable" and ctx.now - was["at"] >= UNREACHABLE_REPORT:
                await _tell(ctx.conn, f"watch:{host}:unreachable:{was['id']}",
                            f"I have not been able to reach {host} since {was['at']:%Y-%m-%d %H:%M} UTC "
                            "(asleep, off, or off the network). Nothing on it is held back for this.")  # fmt: skip
            return "unreachable", {}, None
        await _disk(ctx, host, h.get("free_gb"))
        if h.get("healthy"):
            await _holds(remote, host, False, here)
            return "healthy", {}, h
        await _holds(remote, host, True, here)
        if was.get("type") not in ("watch.held", "watch.failed"):
            return "held", {"detail": _broken(h)}, h
        fixed = await _health(remote, host, fix=True)
        after = await _health(remote, host)
        if after is not None and after.get("healthy"):
            await _holds(remote, host, False, here)
            return "fixed", {"detail": _broken(h), "fixed": (fixed or {}).get("fixed")}, after
        detail = _broken(after or h)
        if was.get("type") != "watch.failed":
            await _tell(ctx.conn, f"watch:{host}:failed:{was.get('id')}",
                        f"{host} is broken and neither it nor I could fix it: {detail}. "
                        "Its nightly updates are held until it is healthy.")  # fmt: skip
        return "failed", {"detail": detail, "fixed": (fixed or {}).get("fixed")}, after or h

    return run
