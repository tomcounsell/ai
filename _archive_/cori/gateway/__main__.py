"""`python -m gateway`: the gateway on its loopback port, on its own.

The kernel process runs `serve` as a task beside the supervisor. This entry
point is for running the gateway by itself, which is what the acceptance
check and the live test do.
"""

import asyncio

from gateway import default_gateway
from gateway.serving import serve


def main() -> None:
    asyncio.run(serve(default_gateway()))


if __name__ == "__main__":
    main()
