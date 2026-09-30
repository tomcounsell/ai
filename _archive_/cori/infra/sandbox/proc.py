"""The one subprocess runner for the sandbox. Plan 06.

`adapters/apple_container.py`, `infra/sandbox/images.py`, and
`infra/sandbox/mounts.py` all shell out, and all three run in the kernel
process beside the gateway's uvicorn (tech stack §2), so every call is
`asyncio.create_subprocess_exec` and never a blocking `subprocess.run`. Every
call is timed, because the adapter reports `duration_ms` and the kernel's
timeout accounting depends on a host-side measurement.
"""

import asyncio
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Completed:
    returncode: int  # -1 when the host-side timeout killed the process
    stdout: bytes
    stderr: bytes
    ms: int
    timed_out: bool


async def run(
    *args: str,
    stdin: bytes | None = None,
    timeout: float | None = None,
) -> Completed:
    """Run a command and collect it. A host-side timeout kills the process and
    returns returncode -1; what the command started inside a VM is the
    caller's problem, which is why `exec` also arms a timeout in the guest."""
    t0 = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        ms = int((time.perf_counter() - t0) * 1000)
        return Completed(-1, b"", b"", ms, True)
    ms = int((time.perf_counter() - t0) * 1000)
    return Completed(proc.returncode or 0, out, err, ms, False)
