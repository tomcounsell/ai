"""A kernel in a process of its own, for the kill tests in
`tests/test_serve.py`: `python -m tests.kernel_child`.

`KERNEL_DSN` names the database. `KERNEL_RUNNERS` is `hang` (a plan turn
that starts a marked child, opens a gateway call, and waits on it),
`scripted` (the scripted turns of `tests/scripted.py`, judge excluded), or
`judged` (all of them, for a task started by message, which is judged
first).
The gateway's upstream accepts the connection and never answers, so an
opened call stays open until the process dies.
"""

import asyncio
import os
import sys

from core import serve, session
from core.__main__ import _performers
from core.gateway import Gateway
from core.machine import State
from harnesses import claude_code
from tests import scripted
from tests.ports import listen

HANG = r"""
import json, pathlib, subprocess, sys, urllib.request
url, pidfile = sys.argv[1], sys.argv[2]
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
pathlib.Path(pidfile).write_text(str(child.pid))
body = {"model": "claude-haiku-4-5", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]}
request = urllib.request.Request(url + "/v1/messages", data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
urllib.request.urlopen(request, timeout=600)
"""


def hang_turn_for(prompt, resume, b):
    def build(url, brief, turn_id):
        return claude_code.TurnCommand(
            argv=[sys.executable, "-c", HANG, url, os.environ["KERNEL_PIDFILE"]],
            env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]},
            cwd=b.workspace,
            harness="script",
            parse=claude_code.parse,
        )

    return build


async def hanging(ctx) -> dict:
    return await session.run(
        ctx.gateway, ctx.task_id, hang_turn_for, dsn=ctx.dsn, alive=ctx.alive, performers=ctx.performers
    )


async def silent_upstream() -> str:
    async def hold(reader, writer):
        await reader.read()  # until the peer goes away; never a reply

    server = await asyncio.start_server(hold, "127.0.0.1", listen())
    return f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"


async def main() -> None:
    dsn = os.environ["KERNEL_DSN"]
    gateway = Gateway(dsn, upstream=await silent_upstream())
    await gateway.start(port=listen())
    if os.environ.get("KERNEL_RUNNERS") == "hang":
        runners = {State.PLAN: hanging}
    elif os.environ.get("KERNEL_RUNNERS") == "judged":
        runners = dict(scripted.RUNNERS)
    else:
        runners = {k: v for k, v in scripted.RUNNERS.items() if k is not State.JUDGE}
    print(f"kernel {os.getpid()}", flush=True)
    await serve.serve(runners, _performers, dsn=dsn, gateway=gateway, checkout=None)


if __name__ == "__main__":
    asyncio.run(main())
