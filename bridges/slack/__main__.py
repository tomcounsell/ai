"""`python -m bridges.slack run | keys | manifest | --plist`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import plistlib
import sys
from pathlib import Path

LABEL = "com.valor.kernel.slack"
ROOT = Path(__file__).resolve().parents[2]
KEYS = ("SLACK_APP_TOKEN", "SLACK_USER_TOKEN")
# Settings a launchd job needs in its environment.
PLIST_ENV = (
    "VALOR_PGHOST",
    "VALOR_PGPORT",
    "VALOR_DB",
    "VALOR_PG_PASSFILE",
    "VALOR_MACHINE",
    "VALOR_DEFAULT_MACHINE",
    "VALOR_OPERATOR_SLACK_IDS",
    "VALOR_OPERATOR_CHANNEL",
    "VALOR_OPERATOR_CHAT",
    "VALOR_INBOUND",
    "VALOR_SERVE_TICK_S",
)
SCOPES = [
    "channels:history",
    "groups:history",
    "im:history",
    "mpim:history",
    "channels:read",
    "groups:read",
    "im:read",
    "mpim:read",
    "im:write",
    "chat:write",
    "files:read",
    "files:write",
    "users:read",
    "reactions:write",
    "search:read",
]
EVENTS = ["message.channels", "message.groups", "message.im", "message.mpim"]


def manifest() -> dict:
    """The app manifest to paste into api.slack.com, inside the workspace."""
    return {
        "display_information": {"name": "Valor"},
        "oauth_config": {"scopes": {"user": SCOPES}},
        "settings": {
            "event_subscriptions": {"user_events": EVENTS},
            "socket_mode_enabled": True,
            "org_deploy_enabled": False,
            "token_rotation_enabled": False,
        },
    }


def _entries(path: Path) -> dict[str, dict[str, str]]:
    """team id -> {key name: value}, from `slack-keys`."""
    from core.credentials import _parse_env

    out: dict[str, dict[str, str]] = {}
    for name, value in _parse_env(path.read_text()).items():
        team, _, key = name.partition("_")
        if key in KEYS and value:
            out.setdefault(team, {})[key] = value
    return out


def credentials() -> tuple[str, str]:
    """The one workspace's app and user tokens; refuses none or two."""
    from bridges.slack.kernel import keyfile

    path = keyfile()
    teams = _entries(path) if path.exists() else {}
    whole = {t: k for t, k in teams.items() if all(n in k for n in KEYS)}
    if not whole:
        raise SystemExit(f"no workspace's tokens are in {path}; run `python -m bridges.slack keys`")
    if len(teams) > 1:
        raise SystemExit(f"{path} holds {len(teams)} workspaces; this bridge serves one")
    (keys,) = whole.values()
    return keys["SLACK_APP_TOKEN"], keys["SLACK_USER_TOKEN"]


def copy(vault_env: str | Path, path: Path) -> dict[str, str]:
    """Copy the tokens from the vault into `slack-keys`, under the team id
    the user token's `auth.test` names."""
    from bridges.slack.wire import team_of
    from core.credentials import _parse_env, copy_keys

    token = _parse_env(Path(vault_env).read_text()).get("SLACK_USER_TOKEN")
    if not token:
        raise SystemExit(f"SLACK_USER_TOKEN is missing from {vault_env}")
    team = team_of(token)
    return copy_keys(vault_env, path, [f"{team}_{k}" for k in KEYS], {f"{team}_{k}": k for k in KEYS})


def plist(*, python: str | None = None) -> bytes:
    from core.settings import settings

    env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    env.update({k: os.environ[k] for k in PLIST_ENV if k in os.environ})
    log = Path(settings.log_dir) / "slack.log"
    return plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": [settings.launcher, python or sys.executable, "-m", "bridges.slack", "run"],
            "WorkingDirectory": str(ROOT),
            "EnvironmentVariables": env,
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(log),
            "StandardErrorPath": str(log),
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bridges.slack")
    parser.add_argument("--plist", action="store_true", help="print the launchd job")
    sub = parser.add_subparsers(dest="verb")
    sub.add_parser("run", help="run the bridge")
    sub.add_parser("keys", help="copy the app and user tokens from the vault into the key directory")
    sub.add_parser("manifest", help="print the app manifest for api.slack.com")
    args = parser.parse_args(argv)

    if args.plist:
        sys.stdout.buffer.write(plist())
        return 0
    if args.verb == "manifest":
        print(json.dumps(manifest(), indent=2))
        return 0
    if args.verb == "keys":
        from bridges.slack.kernel import keyfile
        from core.settings import settings

        for name, status in copy(settings.vault_env, keyfile()).items():
            print(f"{name}: {status}")
        return 0
    if args.verb == "run":
        from bridges.slack.bridge import SlackBridge
        from bridges.slack.kernel import from_core, seen_path
        from bridges.slack.wire import SlackWire
        from core import bridge

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        app_token, user_token = credentials()
        wire = SlackWire(app_token, user_token)
        asyncio.run(bridge.serve(SlackBridge(wire, from_core(), seen=seen_path())))
        return 1  # serve returned: launchd restarts the job
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
