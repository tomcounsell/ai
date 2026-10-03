"""`python -m bridges.email run | keys | --plist`."""

import argparse
import asyncio
import os
import plistlib
import sys
from pathlib import Path

from core import bridge, credentials
from core.settings import settings

from .config import KEY_NAMES

LABEL = "com.valor.email"
ROOT = Path(__file__).resolve().parent.parent.parent
PLIST_ENV = (
    "VALOR_PGHOST",
    "VALOR_PGPORT",
    "VALOR_DB",
    "VALOR_PG_PASSFILE",
    "VALOR_PROJECTS",
    "VALOR_LOG_DIR",
    "VALOR_MACHINE",
    "VALOR_DEFAULT_MACHINE",
    "VALOR_OPERATOR_EMAIL",
    "VALOR_INBOUND",
    "VALOR_SERVE_TICK_S",
    "VALOR_EMAIL_ADDRESS",
    "VALOR_EMAIL_SINCE",
    "VALOR_EMAIL_AUTHSERV_ID",
    "VALOR_IMAP_HOST",
    "VALOR_IMAP_PORT",
    "VALOR_SMTP_HOST",
    "VALOR_SMTP_PORT",
    "VALOR_MAIL_CAFILE",
)


def plist(python: str | None = None, root: Path = ROOT) -> bytes:
    """The launchd job: the bridge kept alive from this checkout, started
    at load."""
    env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    env.update({k: os.environ[k] for k in PLIST_ENV if k in os.environ})
    log = str(Path(settings.log_dir) / "email.log")
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [
                python or str(root / ".venv" / "bin" / "python"),
                "-m",
                "bridges.email",
                "run",
            ],
            "WorkingDirectory": str(root),
            "EnvironmentVariables": env,
            "KeepAlive": True,
            "RunAtLoad": True,
            "StandardOutPath": log,
            "StandardErrorPath": log,
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bridges.email")
    parser.add_argument("verb", nargs="?", choices=["run", "keys"])
    parser.add_argument("--plist", action="store_true", help="print the launchd job")
    args = parser.parse_args(argv)
    if args.plist:
        print(plist().decode(), end="")
        return 0
    if args.verb == "keys":
        status = credentials.copy_keys(settings.vault_env, settings.mail_keyfile, KEY_NAMES)
        for name in KEY_NAMES:
            print(f"{name}: {status[name]}")
        return 1 if "missing" in status.values() else 0
    if args.verb == "run":
        from . import EmailBridge

        asyncio.run(bridge.serve(EmailBridge()))
        return 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
