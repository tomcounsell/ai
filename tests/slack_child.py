"""The Slack bridge in a child process, for tests that kill it by its pid.

    python -m tests.slack_child URL DSN STATE [--only IDS] [--mark PATH]
        [--receive hold|fail-first] [--budget CALLS/WINDOW_S]
        [--share-reads READS/WAIT_S]

`serve`s the bridge over the test database named by DSN, against the
emulator at URL, with `slack-seen.json` in STATE (a directory), under the
settings the test passes in the environment. The test database holds
other tests' rows for the Slack channel, so the outbox yields and
reconciles only the effect and notice ids in `--only` (comma separated).

`--mark` is written once a gap-fill pass has completed. `--receive hold`
writes `MARK.paused` at the first receive and holds it until `MARK.go`
exists; `--receive fail-first` makes the first receive fail.

The pauses live here, in wrappers around the port; the bridge has no
test hook.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
from pathlib import Path

from bridges.slack import gap
from bridges.slack.bridge import SlackBridge
from bridges.slack.kernel import from_core
from bridges.slack.send import Sender
from bridges.slack.wire import SlackWire
from core import bridge as port


def only_outbox(wanted: set[str]) -> type:
    class Only(port.Outbox):
        async def due(self):
            return [
                i
                for i in await super().due()
                if getattr(i, "effect_id", None) in wanted or getattr(i, "notice_id", None) in wanted
            ]

        async def dangling(self):
            return [e for e in await super().dangling() if e in wanted]

    return Only


def wrap_receive(kernel, mark: str, how: str):
    real = kernel.receive
    calls = [0]

    async def receive(conn, inbound):
        calls[0] += 1
        if calls[0] == 1 and how == "hold":
            Path(mark + ".paused").write_text("paused")
            while not Path(mark + ".go").exists():
                await asyncio.sleep(0.05)
        if calls[0] == 1 and how == "fail-first":
            raise RuntimeError("the first receive fails")
        return await real(conn, inbound)

    return dataclasses.replace(kernel, receive=receive)


async def main(args) -> None:
    port.Outbox = only_outbox(set(filter(None, args.only.split(","))))
    kernel = from_core(args.dsn)
    if args.receive:
        kernel = wrap_receive(kernel, args.mark, args.receive)
    wire = SlackWire(
        "xapp-test", "xoxp-test", base_url=f"{args.url}/api/", hosts=("127.0.0.1",), insecure=True
    )
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
    asyncio.run(main(p.parse_args()))
