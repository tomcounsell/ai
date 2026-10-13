"""The Slack bridge in a child process, for tests that kill it by its pid.

    python -m tests.slack_child URL DSN STATE [--only FILE] [--mark PATH]
        [--receive hold|fail-first] [--budget CALLS/WINDOW_S]
        [--share-reads READS/WAIT_S] [--fail-first NAME CHAT] [--page N]

`serve`s the bridge over the test database named by DSN, against the
emulator at URL, with `slack-seen.json` in STATE (a directory), under the
settings the test passes in the environment. The test database holds
other tests' rows for the Slack channel, so the outbox yields and
reconciles only the effects whose ids are listed in the file `--only`
(one a line, read on each wake) and the notices to chats in the
workspace the emulator is (the test's own team).

`--mark` is written once a gap-fill pass has completed. `--receive`
writes `MARK.paused` at the first receive and holds it until `MARK.go`
exists; then `hold` lets it go on and `fail-first` makes it fail.
`--fail-first NAME CHAT` makes the first call of the kernel's `NAME` (a
ledger read, as when Postgres restarts) for `CHAT` raise. `--page N` reads history and
replies `N` messages a page.

The pauses live here, in wrappers around the port; the bridge has no
test hook.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
from pathlib import Path

from bridges.slack import gap
from bridges.slack.bridge import SlackBridge
from bridges.slack.kernel import from_core
from bridges.slack.send import Sender
from bridges.slack.wire import SlackWire
from core import bridge as port


def only_outbox(only: str, wire: SlackWire) -> type:
    def wanted() -> set[str]:
        path = Path(only) if only else None
        return set(path.read_text().split()) if path and path.exists() else set()

    class Only(port.Outbox):
        async def due(self):
            effects = wanted()
            return [
                i
                for i in await super().due()
                if getattr(i, "effect_id", None) in effects
                or (getattr(i, "chat_id", None) or "").partition("/")[0] == wire.team_id
            ]

        async def dangling(self):
            effects = wanted()
            return [e for e in await super().dangling() if e in effects]

    return Only


def wrap_receive(kernel, mark: str, how: str):
    real = kernel.receive
    calls = [0]

    async def receive(conn, inbound):
        calls[0] += 1
        if calls[0] == 1:
            Path(mark + ".paused").write_text("paused")
            while not Path(mark + ".go").exists():
                await asyncio.sleep(0.05)
        if calls[0] == 1 and how == "fail-first":
            raise RuntimeError("the first receive fails")
        return await real(conn, inbound)

    return dataclasses.replace(kernel, receive=receive)


def fail_first(kernel, name: str, chat: str):
    real = getattr(kernel, name)
    failed = [False]

    async def once(conn, channel, of, *args, **kw):
        if of == chat and not failed[0]:
            failed[0] = True
            raise ConnectionError(f"the first {name} of {chat} fails")
        return await real(conn, channel, of, *args, **kw)

    return dataclasses.replace(kernel, **{name: once})


def small_pages(wire: SlackWire, n: int) -> None:
    for name in ("history", "replies"):
        real = getattr(wire, name)

        async def read(*args, _real=real, limit: int = n, **kw):
            return await _real(*args, limit=min(limit, n), **kw)

        setattr(wire, name, read)


async def main(args) -> None:
    kernel = from_core(args.dsn)
    if args.receive:
        kernel = wrap_receive(kernel, args.mark, args.receive)
    if args.fail_first:
        kernel = fail_first(kernel, *args.fail_first)
    wire = SlackWire(
        "xapp-test", "xoxp-test", base_url=f"{args.url}/api/", hosts=("127.0.0.1",), insecure=True
    )
    if args.page:
        small_pages(wire, args.page)
    port.Outbox = only_outbox(args.only, wire)
    calls, window = args.budget.split("/")
    reads, wait = args.share_reads.split("/")
    bridge = SlackBridge(
        wire,
        kernel,
        seen=Path(args.state) / "slack-seen.json",
        budget=gap.Budget(int(calls), float(window)),
        sender=Sender(wire, kernel, share_reads=int(reads), share_wait_s=float(wait)),
    )
    if args.mark:
        real = bridge.fill

        async def fill():
            done = await real()
            if done:
                Path(args.mark).write_text("filled")
            return done

        bridge.fill = fill
    logging.basicConfig(level=logging.INFO, format="%(asctime)s slack-child %(levelname)s %(message)s")
    await port.serve(bridge, args.dsn)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("dsn")
    p.add_argument("state")
    p.add_argument("--only", default="")
    p.add_argument("--mark", default="")
    p.add_argument("--receive", choices=("hold", "fail-first"))
    p.add_argument("--budget", default="50/60")
    p.add_argument("--share-reads", default="5/2")
    p.add_argument("--fail-first", nargs=2, default=None)
    p.add_argument("--page", type=int, default=0)
    asyncio.run(main(p.parse_args()))
