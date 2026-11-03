"""`python -m bridges.slack run | keys | manifest | --plist`."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import plistlib
import re
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
    "groups:write",
    "im:read",
    "mpim:read",
    "im:write",
    "chat:write",
    "files:read",
    "files:write",
    "users:read",
    "reactions:read",
    "reactions:write",
    "search:read",
    "canvases:write",
]
EVENTS = ["message.channels", "message.groups", "message.im", "message.mpim", "reaction_added"]


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


def credentials() -> dict[str, tuple[str, str]]:
    """Each workspace's app and user tokens by team, every whole pair in
    `slack-keys`; refuses none."""
    from bridges.slack.kernel import keyfile

    path = keyfile()
    teams = _entries(path) if path.exists() else {}
    whole = {
        t: (k["SLACK_APP_TOKEN"], k["SLACK_USER_TOKEN"])
        for t, k in teams.items()
        if all(n in k for n in KEYS)
    }
    if not whole:
        raise SystemExit(f"no workspace's tokens are in {path}; run `python -m bridges.slack keys`")
    return whole


def vault_names(machine: str, label: str = "") -> dict[str, str]:
    """Each key's name in the vault for `machine`: each Mac has its own app
    in each workspace, so one Socket Mode token never runs on two bridges
    (`SLACK_APP_TOKEN_VALOR_THE_PIRATE`; a further workspace's pair
    `SLACK_APP_TOKEN_VALOR_THE_PIRATE__<LABEL>`)."""
    suffix = re.sub(r"[^A-Z0-9]+", "_", machine.upper()).strip("_")
    return {k: f"{k}_{suffix}" + (f"__{label}" if label else "") for k in KEYS}


def copy(vault_env: str | Path, path: Path, machine: str) -> dict[str, str]:
    """Copy each of `machine`'s pairs from the vault into `slack-keys`,
    under the team id each user token's `auth.test` names. The unlabelled
    pair is required; each `__<LABEL>` pair is another workspace's."""
    from bridges.slack import wire
    from core.credentials import _parse_env, copy_keys

    vault = _parse_env(Path(vault_env).read_text())
    base = vault_names(machine)
    if not vault.get(base["SLACK_USER_TOKEN"]):
        raise SystemExit(f"{base['SLACK_USER_TOKEN']} is missing from {vault_env}")
    prefix = base["SLACK_USER_TOKEN"] + "__"
    labels = sorted(n.removeprefix(prefix) for n in vault if n.startswith(prefix) and vault[n])
    sources: dict[str, str] = {}
    for names in [base, *(vault_names(machine, label) for label in labels)]:
        team = wire.team_of(vault[names["SLACK_USER_TOKEN"]])
        sources |= {f"{team}_{k}": names[k] for k in KEYS}
    return copy_keys(vault_env, path, list(sources), sources)


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
    sub.add_parser(
        "keys",
        help="copy this machine's app and user tokens, a pair per workspace, from the vault into the key directory",
    )
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

        statuses = copy(settings.vault_env, keyfile(), settings.machine)
        for name, status in statuses.items():
            print(f"{name}: {status}")
        return 1 if "missing" in statuses.values() else 0
    if args.verb == "run":
        from bridges.slack.bridge import SlackBridge
        from bridges.slack.kernel import from_core, seen_path
        from bridges.slack.wire import SlackWire
        from core import bridge, sentry

        sentry.init("slack")
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        wires = [SlackWire(app, user, team=team) for team, (app, user) in credentials().items()]
        asyncio.run(bridge.serve(SlackBridge(wires, from_core(), seen=seen_path())))
        return 1  # serve returned: launchd restarts the job
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
