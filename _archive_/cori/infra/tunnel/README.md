# cori.yudame.dev

The passkey relying-party origin (tech stack §13), served from this Mac
through a remotely-managed Cloudflare Tunnel. `setup.py` does the whole job
once two secrets are in the Keychain:

    security add-generic-password -s cori -a cloudflare_account_id -w '<account id>' -U
    security add-generic-password -s cori -a cloudflare_api_token -w '<token>' -U
    uv run python -m infra.tunnel.setup

The API token needs: Account > Cloudflare Tunnel > Edit and Zone > DNS >
Edit. The one created on 2026-09-19 (`cori tunnel and dns`, also in the
1Password vault as `CORI_CLOUDFLARE_API_TOKEN`) has DNS Edit on every zone
in the yudame account rather than yudame.dev alone; narrow it in the
dashboard if that ever matters. The older tokens in the vault are read-only
on this zone and cannot do this job.

What setup leaves behind: a tunnel named `cori` with ingress
`cori.yudame.dev -> http://localhost:8787` (8787 is reserved for the placeholder page; the gateway binds 127.0.0.1:8788 and is never routed through the tunnel), a proxied CNAME, the tunnel's
run token in the Keychain as `cloudflare_tunnel_token`, and two launchd
agents in ~/Library/LaunchAgents: `dev.yudame.cori.placeholder` (a Python
http.server on 127.0.0.1:8787 serving `infra/placeholder/`) and
`dev.yudame.cori.tunnel` (`run.sh`, which hands cloudflared the token from
the Keychain). Cloudflare terminates TLS at its edge with its own
certificate, so the phone sees no warning.

Applied 2026-09-19: tunnel `cf27ee8f-6425-47ba-ae63-8b59a9f6ec8f`, `curl -sI https://cori.yudame.dev` returns `HTTP/2 200`.

Check: `curl -sI https://cori.yudame.dev | head -1` from any network.
