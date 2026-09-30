"""Cloudflare Tunnel for cori.yudame.dev, remotely managed. Prereqs item 11.

Idempotent: creates the tunnel `cori` if absent, sets its ingress to the
placeholder server on localhost:8787, points the CNAME at it, stores the
tunnel's run token in the Keychain as `cloudflare_tunnel_token`, and installs
two launchd agents (the placeholder server and cloudflared). Nothing here
touches a dotfile: cloudflared receives its token from the Keychain through
`run.sh` at launch.

Needs `cloudflare_api_token` in the Keychain with Account > Cloudflare Tunnel:
Edit and Zone > DNS: Edit on yudame.dev, and `cloudflare_account_id`.

    uv run python -m infra.tunnel.setup
"""

import base64
import json
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from infra.secrets import read_secret

HOSTNAME = "cori.yudame.dev"
ZONE = "yudame.dev"
TUNNEL = "cori"
ORIGIN = "http://localhost:8787"
API = "https://api.cloudflare.com/client/v4"
HERE = Path(__file__).parent
AGENTS = Path.home() / "Library" / "LaunchAgents"


def api(token, method, path, body=None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        r = json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        r = json.load(e)
    if not r.get("success"):
        raise SystemExit(f"{method} {path}: {r.get('errors')}")
    return r["result"]


def ensure_tunnel(token, account):
    for t in api(token, "GET", f"/accounts/{account}/cfd_tunnel?is_deleted=false"):
        if t["name"] == TUNNEL:
            return t["id"]
    secret = base64.b64encode(secrets.token_bytes(32)).decode()
    t = api(
        token,
        "POST",
        f"/accounts/{account}/cfd_tunnel",
        {"name": TUNNEL, "config_src": "cloudflare", "tunnel_secret": secret},
    )
    return t["id"]


def ensure_ingress(token, account, tunnel_id):
    api(
        token,
        "PUT",
        f"/accounts/{account}/cfd_tunnel/{tunnel_id}/configurations",
        {
            "config": {
                "ingress": [
                    {"hostname": HOSTNAME, "service": ORIGIN},
                    {"service": "http_status:404"},
                ]
            }
        },
    )


def ensure_dns(token, tunnel_id):
    zone = api(token, "GET", f"/zones?name={ZONE}")[0]["id"]
    target = f"{tunnel_id}.cfargotunnel.com"
    for rec in api(token, "GET", f"/zones/{zone}/dns_records?name={HOSTNAME}"):
        if rec["type"] == "CNAME" and rec["content"] == target and rec["proxied"]:
            return
        api(token, "DELETE", f"/zones/{zone}/dns_records/{rec['id']}")
    api(
        token,
        "POST",
        f"/zones/{zone}/dns_records",
        {"type": "CNAME", "name": HOSTNAME, "content": target, "proxied": True},
    )


def store_tunnel_token(token, account, tunnel_id):
    run_token = api(token, "GET", f"/accounts/{account}/cfd_tunnel/{tunnel_id}/token")
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-s",
            "cori",
            "-a",
            "cloudflare_tunnel_token",
            "-w",
            run_token,
            "-U",
        ],
        check=True,
    )


def install_agents():
    AGENTS.mkdir(parents=True, exist_ok=True)
    for name in ("dev.yudame.cori.placeholder", "dev.yudame.cori.tunnel"):
        plist = AGENTS / f"{name}.plist"
        plist.write_text(
            (HERE / f"{name}.plist")
            .read_text()
            .replace("__REPO__", str(HERE.parents[1]))
        )
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        subprocess.run(["launchctl", "load", str(plist)], check=True)


def main():
    token = read_secret("cloudflare_api_token")
    account = read_secret("cloudflare_account_id")
    tunnel_id = ensure_tunnel(token, account)
    ensure_ingress(token, account, tunnel_id)
    ensure_dns(token, tunnel_id)
    store_tunnel_token(token, account, tunnel_id)
    install_agents()
    print(f"tunnel {tunnel_id} -> {HOSTNAME} -> {ORIGIN}; agents loaded")


if __name__ == "__main__":
    sys.exit(main())
