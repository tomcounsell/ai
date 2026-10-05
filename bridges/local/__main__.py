"""`python -m bridges.local run | open | --plist`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.valor.kernel.local"
ROOT = Path(__file__).resolve().parents[2]
# Settings a launchd job needs in its environment.
PLIST_ENV = (
    "VALOR_PGHOST",
    "VALOR_PGPORT",
    "VALOR_DB",
    "VALOR_PG_PASSFILE",
    "VALOR_MACHINE",
    "VALOR_DEFAULT_MACHINE",
    "VALOR_PROJECTS",
    "VALOR_OPERATOR_CHANNEL",
    "VALOR_LOCAL_PORT",
    "VALOR_SERVE_TICK_S",
)


def plist(*, python: str | None = None) -> bytes:
    from core.settings import settings

    env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    env.update({k: os.environ[k] for k in PLIST_ENV if k in os.environ})
    log = Path(settings.log_dir) / "local.log"
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [python or sys.executable, "-m", "bridges.local", "run"],
            "WorkingDirectory": str(ROOT),
            "EnvironmentVariables": env,
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(log),
            "StandardErrorPath": str(log),
        }
    )


def open_page() -> int:
    """Open the page with its token in the URL fragment, which never
    reaches the server. Reads the token file and never makes one."""
    from core.settings import settings

    path = Path(settings.local_tokenfile)
    if not path.exists():
        print(f"No token at {path}; start the bridge first (`python -m bridges.local run`).", file=sys.stderr)
        return 1
    token = path.read_text().strip()
    subprocess.run(["/usr/bin/open", f"http://127.0.0.1:{settings.local_port}/#{token}"], check=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bridges.local")
    parser.add_argument("--plist", action="store_true", help="print the launchd job")
    sub = parser.add_subparsers(dest="verb")
    sub.add_parser("run", help="run the bridge and serve the page")
    sub.add_parser("open", help="open the page in the browser")
    args = parser.parse_args(argv)

    if args.plist:
        sys.stdout.buffer.write(plist())
        return 0
    if args.verb == "open":
        return open_page()
    if args.verb == "run":
        from bridges.local import LocalBridge
        from core import bridge

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        asyncio.run(bridge.serve(LocalBridge()))
        return 1  # serve returned: launchd restarts the job
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
