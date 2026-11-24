"""Another Mac, over ssh: the one route between Valor's Macs (Tailscale,
docs/machine.md). The `watch` routine reads a peer's health with it, and a
chat session hands a message to a chat another Mac owns
(`tools/chat_tasks.py`, `TaskSteer`). It opens no access: the Macs' own ssh
keys decide which pairs connect.
"""

import asyncio

SSH = ("/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
# A `python -m core` command on the Mac reached, in its checkout.
KERNEL = "cd ~/src/ai && .venv/bin/python -m core"
# ssh's own exit status for a connection it could not make.
NO_ROUTE = 255


def host(machine: str) -> str:
    """The ssh host of a Mac by its name (`settings.machine`, a spec's
    `machine`): its Tailscale name, `Valor the Pirate` -> `valor-the-pirate`,
    as `VALOR_WATCH` names the Macs."""
    return "-".join(machine.lower().split())


async def ssh(host: str, command: str, stdin: bytes | None = None) -> tuple[int, str]:
    """Run `command` on `host`, with `stdin` when given: its exit status and
    output; `NO_ROUTE` with ssh's own message when it could not connect."""
    proc = await asyncio.create_subprocess_exec(
        *SSH, host, command,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )  # fmt: skip
    try:
        out, _ = await asyncio.wait_for(proc.communicate(stdin), 300)
    except TimeoutError:
        proc.kill()
        return NO_ROUTE, "ssh did not return in 300 s"
    return proc.returncode or 0, out.decode(errors="replace")
