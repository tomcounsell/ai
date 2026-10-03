"""`python -m bridges.telegram run | login [--test-dc] | keys | --plist`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import plistlib
import sys
from pathlib import Path

LABEL = "com.valor.kernel.telegram"
ROOT = Path(__file__).resolve().parents[2]
KEYS = ["TELEGRAM_API_ID", "TELEGRAM_API_HASH"]
# Settings a launchd job needs in its environment.
PLIST_ENV = (
    "VALOR_PGHOST",
    "VALOR_PGPORT",
    "VALOR_DB",
    "VALOR_PG_PASSFILE",
    "VALOR_MACHINE",
    "VALOR_DEFAULT_MACHINE",
    "VALOR_OPERATOR_TELEGRAM_ID",
    "VALOR_OPERATOR_CHAT",
    "VALOR_INBOUND",
    "VALOR_SERVE_TICK_S",
)


def credentials() -> tuple[int, str]:
    from bridges.telegram.kernel import keyfile
    from core.credentials import MissingKey, read_key

    try:
        return int(read_key(keyfile(), "TELEGRAM_API_ID")), read_key(keyfile(), "TELEGRAM_API_HASH")
    except MissingKey:
        raise SystemExit(
            f"TELEGRAM_API_ID or TELEGRAM_API_HASH is missing from {keyfile()}; "
            "run `python -m bridges.telegram keys`"
        ) from None


def plist(*, python: str | None = None) -> bytes:
    from core.settings import settings

    env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    env.update({k: os.environ[k] for k in PLIST_ENV if k in os.environ})
    log = Path(settings.log_dir) / "telegram.log"
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [python or sys.executable, "-m", "bridges.telegram", "run"],
            "WorkingDirectory": str(ROOT),
            "EnvironmentVariables": env,
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(log),
            "StandardErrorPath": str(log),
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bridges.telegram")
    parser.add_argument("--plist", action="store_true", help="print the launchd job")
    sub = parser.add_subparsers(dest="verb")
    sub.add_parser("run", help="run the bridge")
    login = sub.add_parser("login", help="sign Valor's account into the bridge's session")
    login.add_argument("--test-dc", action="store_true", help="a test account on Telegram's test servers")
    login.add_argument("--session", help="session path (test accounts only)")
    sub.add_parser("keys", help="copy the API id and hash from the vault into the key directory")
    args = parser.parse_args(argv)

    if args.plist:
        sys.stdout.buffer.write(plist())
        return 0
    if args.verb == "keys":
        from bridges.telegram.kernel import keyfile
        from core.credentials import copy_keys
        from core.settings import settings

        for name, status in copy_keys(settings.vault_env, keyfile(), KEYS).items():
            print(f"{name}: {status}")
        return 0
    if args.verb == "login":
        from bridges.telegram import login as signin
        from bridges.telegram.kernel import session_path

        api_id, api_hash = credentials()
        session = Path(args.session) if args.session else session_path()
        print(signin.run(session, api_id, api_hash, test_dc=args.test_dc))
        return 0
    if args.verb == "run":
        from bridges.telegram.bridge import TelegramBridge
        from bridges.telegram.kernel import from_core, session_path
        from bridges.telegram.wire import TelethonWire
        from core import bridge

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        api_id, api_hash = credentials()
        wire = TelethonWire(session_path(), api_id, api_hash)
        asyncio.run(bridge.serve(TelegramBridge(wire, from_core())))
        return 1  # serve returned: launchd restarts the job
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
