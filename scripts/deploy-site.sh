#!/usr/bin/env bash
# Deploy site/ to https://valorengels.com: the Cloudflare Worker valorengels-site
# on Yudame's account (wrangler.jsonc, src/index.js).
#
# The token is read from Valor's 1Password vault at run time and never stored:
# item "Cloudflare API token" in vault m-valor. The CLOUDFLARE_API_TOKEN in the
# vault .env sees no account, so it is not used. op needs
# OP_SERVICE_ACCOUNT_TOKEN, which lives in ~/Desktop/Valor/.env.
set -euo pipefail
cd "$(dirname "$0")/.."

VAULT_ENV="${VALOR_VAULT_ENV:-$HOME/Desktop/Valor/.env}"
if [ -z "${OP_SERVICE_ACCOUNT_TOKEN:-}" ] && [ -f "$VAULT_ENV" ]; then
  OP_SERVICE_ACCOUNT_TOKEN="$(grep '^OP_SERVICE_ACCOUNT_TOKEN=' "$VAULT_ENV" | cut -d= -f2- | tr -d '"')"
  export OP_SERVICE_ACCOUNT_TOKEN
fi

CLOUDFLARE_API_TOKEN="$(op item get 2qm4qqe5zs4gdv5divlz246iga --vault m-valor --fields API_TOKEN --reveal)"
[ -n "$CLOUDFLARE_API_TOKEN" ] || { echo "deploy-site: no token from 1Password; nothing deployed" >&2; exit 1; }
export CLOUDFLARE_API_TOKEN

CLOUDFLARE_ACCOUNT_ID="$(curl -sf -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  https://api.cloudflare.com/client/v4/accounts | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"][0]["id"])')"
export CLOUDFLARE_ACCOUNT_ID

if command -v wrangler >/dev/null 2>&1; then
  WRANGLER=(wrangler)
else
  WRANGLER=(npx --yes wrangler@latest)
fi

# /activity.svg reads Valor's merged pull requests from GitHub with this token.
# It is the signed-in gh account's token and only needs read access to the
# repositories Valor's pull requests are in.
GITHUB_TOKEN="$(gh auth token 2>/dev/null || true)"
[ -n "$GITHUB_TOKEN" ] || { echo "deploy-site: gh auth token is empty; run gh auth login. Nothing deployed" >&2; exit 1; }
printf '%s' "$GITHUB_TOKEN" | "${WRANGLER[@]}" secret put GITHUB_TOKEN
unset GITHUB_TOKEN

"${WRANGLER[@]}" deploy
curl -sf https://valorengels.com/ >/dev/null && echo "deploy-site: live OK" \
  || { echo "deploy-site: valorengels.com did not answer; wrangler rollback undoes the deploy" >&2; exit 1; }
# Report, never a gate. The head is read whole first: under pipefail, head
# closing the pipe early would fail curl and read as a miss.
ACTIVITY="$(curl -sf https://valorengels.com/activity.svg || true)"
if printf '%s' "${ACTIVITY:0:200}" | grep -q '<svg'; then
  echo "deploy-site: activity OK"
else
  echo "deploy-site: warning, /activity.svg did not answer with an SVG" >&2
fi
