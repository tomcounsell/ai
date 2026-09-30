#!/bin/sh
# Runs cloudflared with the tunnel token from the Keychain. Launched by
# dev.yudame.cori.tunnel; nothing on disk holds the token.
exec /opt/homebrew/bin/cloudflared tunnel --no-autoupdate run \
  --token "$(/usr/bin/security find-generic-password -s cori -a cloudflare_tunnel_token -w)"
