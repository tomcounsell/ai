"""Running the gateway's app. Plan 04, Serving.

Loopback only, because the only client is the PydanticAI loop, which runs
in the kernel process (tech stack §5), and a sandbox holds no token (tech
stack §2). Port 8788, because 8787 is the Cloudflare tunnel's ingress to
the placeholder page, and a gateway there would be served at
`cori.yudame.dev`.

`serve` is a coroutine so the kernel process can run it as one task beside
the supervisor rather than owning a thread for it (tech stack §2, one
process). It warms the gateway before the first request is accepted and
drains the closing transactions after the last one, so a shutdown leaves
no call without a closing row.
"""

import uvicorn

from gateway.app import build_app
from gateway.core import Gateway

HOST = "127.0.0.1"
PORT = 8788


async def serve(gateway: Gateway, host: str = HOST, port: int = PORT) -> None:
    await gateway.start()
    config = uvicorn.Config(build_app(gateway), host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await gateway.drain()
        await gateway.aclose()
