# Step 8.7: Optional Cross-Machine Mesh Network (Headscale + Tailscale)

Load this after Step 8 (worker/reflection services) and alongside the other
optional surfaces (Step 8.5-8.6). macOS only, operator opt-in, same pattern as
BYOB/bcu: **ask before installing anything.**

> Do you want to join this machine to the shared Valor mesh network
> (Headscale + Tailscale)? This lets machines reach each other's services
> (e.g. a shared memory Redis) over a private overlay network instead of
> being confined to their own LAN.

On **no**: skip this entire step.

On **yes**, resolve the role automatically, then confirm with the user before
proceeding:

```bash
HEAD_MACHINE_NAME="Valor the Pirate"
THIS_MACHINE=$(scutil --get ComputerName)
if [ "$THIS_MACHINE" = "$HEAD_MACHINE_NAME" ]; then
  echo "Role: head (this machine runs the Headscale control server)"
else
  echo "Role: client (connects to head machine '$HEAD_MACHINE_NAME')"
fi
```

**Default topology**: `Valor the Pirate` is always the head; every other
machine is a client that dials in. This is a default, not a hard rule — if
the user explicitly wants a different machine to be the head (e.g. `Valor
the Pirate` is offline or being retired), ask which machine should take the
role and adjust which half of this doc you run on which host.

## Head Path (runs once, on the head machine only)

Skip any sub-step whose idempotency check already passes — this must be
safe to re-run.

### 1. Install the pinned Headscale binary

```bash
PIN_TAG=$(python3 -c "import json; print(json.load(open('config/headscale_pin.json'))['release_tag'])")
ARCH=$(uname -m)  # arm64 or x86_64 -> amd64
[ "$ARCH" = "x86_64" ] && ARCH=amd64
INSTALLED=$(headscale version 2>/dev/null | grep -o "$PIN_TAG")
if [ -z "$INSTALLED" ]; then
  curl -sL -o /tmp/headscale "https://github.com/juanfont/headscale/releases/download/${PIN_TAG}/headscale_${PIN_TAG#v}_darwin_${ARCH}"
  chmod +x /tmp/headscale
  mv /tmp/headscale /opt/homebrew/bin/headscale
fi
headscale version
```

### 2. Write the Headscale config

```bash
mkdir -p ~/Library/"Application Support"/headscale ~/Library/Logs/headscale
```

Template `~/Library/Application Support/headscale/config.yaml`, substituting
this machine's `.local` mDNS hostname (lowercased, spaces to hyphens — e.g.
`Valor the Pirate` → `valor-the-pirate.local`) as `server_url`. Key fields
(see `docs/features/...` if a fuller reference doc exists, otherwise base on
the upstream `config-example.yaml` for the pinned tag):

- `server_url: http://<hostname-slug>.local:8080`
- `listen_addr: 0.0.0.0:8080` (must bind all interfaces, not just loopback)
- `dns.magic_dns: false`, `dns.override_local_dns: false` (this mesh is for
  service reachability, not DNS takeover)
- sqlite database + noise key paths under `~/Library/Application Support/headscale/`
- `unix_socket: ~/Library/Application Support/headscale/headscale.sock`

Do not use HTTPS/ACME for a LAN-only head — control-plane traffic is already
encrypted at the Noise-protocol layer regardless of the outer HTTP/HTTPS
scheme.

### 3. Install as a launchd service

Write `~/Library/LaunchAgents/com.valor.headscale.plist` (`RunAtLoad` +
`KeepAlive`, `ProgramArguments` = `headscale --config <path> serve`, logs to
`~/Library/Logs/headscale/`), matching the `com.valor.*` naming convention
used by `com.valor.worker.plist` etc. Then:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.valor.headscale.plist 2>/dev/null || true
sleep 2
curl -s http://localhost:8080/health
```

Expect `{"status":"pass"}`. If the bootstrap fails because it's already
loaded, that's fine — re-run `launchctl kickstart -k gui/$(id -u)/com.valor.headscale`
instead.

### 4. Create the `valor` user (idempotent)

```bash
CONFIG="/Users/$(whoami)/Library/Application Support/headscale/config.yaml"
headscale --config "$CONFIG" users list 2>&1 | grep -q " valor " \
  || headscale --config "$CONFIG" users create valor
```

### 5. Generate (or reuse) a long-lived pre-auth key

Only generate a new key if `HEADSCALE_PREAUTH_KEY` is unset in the vault
`.env` or has expired — a fresh key invalidates nothing for already-joined
nodes, but there's no reason to rotate needlessly.

```bash
USER_ID=$(headscale --config "$CONFIG" users list 2>&1 | awk '/ valor /{print $1}')
KEY=$(headscale --config "$CONFIG" preauthkeys create --user "$USER_ID" --reusable --expiration 2160h)
echo "$KEY"
```

`2160h` (90 days) is provisional/tunable — long enough that client machines
rarely hit an expired key, short enough that a leaked key doesn't stay valid
forever.

### 6. Enroll the head machine itself into its own tailnet

```bash
brew list tailscale >/dev/null 2>&1 || brew install tailscale
```

`sudo brew services start tailscale` needs an interactive password — **ask
the user** to run it themselves (or type `! sudo brew services start
tailscale` in chat). Then:

```bash
SLUG=$(scutil --get ComputerName | tr '[:upper:] ' '[:lower:]-')
tailscale up --login-server="http://${SLUG}.local:8080" --authkey="$KEY" --hostname="$SLUG"
tailscale status
```

### 7. Publish the connection info for client machines

Add to `~/Desktop/Valor/.env` (idempotent — replace if the key already
exists, since it may have just been rotated):

```bash
VAULT_ENV=~/Desktop/Valor/.env
SERVER_URL="http://${SLUG}.local:8080"
for VAR in "HEADSCALE_SERVER_URL=$SERVER_URL" "HEADSCALE_PREAUTH_KEY=$KEY"; do
  NAME="${VAR%%=*}"
  grep -q "^${NAME}=" "$VAULT_ENV" \
    && sed -i '' "s|^${NAME}=.*|${VAR}|" "$VAULT_ENV" \
    || echo "$VAR" >> "$VAULT_ENV"
done
```

This vault file is iCloud-synced, so every other machine picks up the new
values automatically — no manual copy-paste between machines.

## Client Path (runs on every other machine)

### 1. Confirm the head has published connection info

```bash
source ~/Desktop/Valor/.env 2>/dev/null
if [ -z "$HEADSCALE_SERVER_URL" ] || [ -z "$HEADSCALE_PREAUTH_KEY" ]; then
  echo "HEADSCALE_SERVER_URL / HEADSCALE_PREAUTH_KEY not set — run the head path on '$HEAD_MACHINE_NAME' first."
fi
```

If missing, stop and tell the user to run this step on the head machine
first (or wait for iCloud to sync `~/Desktop/Valor/.env`).

### 2. Install and start Tailscale

```bash
brew list tailscale >/dev/null 2>&1 || brew install tailscale
```

Same as the head: `sudo brew services start tailscale` is a **user action**
(interactive password). Ask the user to run it or use the `!` prefix.

### 3. Join the mesh

```bash
SLUG=$(scutil --get ComputerName | tr '[:upper:] ' '[:lower:]-')
tailscale up --login-server="$HEADSCALE_SERVER_URL" --authkey="$HEADSCALE_PREAUTH_KEY" --hostname="$SLUG"
tailscale status
```

Expect this machine to show up `online` with a `100.64.x.x` tailnet IP.
Verify from the head:

```bash
headscale --config "$CONFIG" nodes list
```

## Troubleshooting

- **mDNS (`.local`) doesn't resolve across the LAN** (segmented Wi-Fi,
  corporate network blocking Bonjour) — fall back to a static IP reservation
  for the head machine's LAN IP on the router, and use that IP in
  `HEADSCALE_SERVER_URL` instead of the `.local` hostname.
- **Pre-auth key expired** — re-run head path Step 5 (generates + publishes a
  fresh key); client machines that already joined are unaffected, only new
  enrollments need the current key.
- **`tailscale up` hangs or errors "context deadline exceeded"** — the
  client can't reach `HEADSCALE_SERVER_URL`; check the head's
  `com.valor.headscale` launchd service is loaded (`launchctl list | grep
  headscale`) and that `listen_addr` is `0.0.0.0:8080`, not `127.0.0.1:8080`.

## Not part of this step

Joining the mesh does not, by itself, expose anything new — Redis and other
services stay bound to `127.0.0.1` until someone deliberately opens them to
the tailnet interface (bind to the tailnet IP + set `requirepass`). Treat
that as a separate, explicit decision per service, not something setup does
automatically.
