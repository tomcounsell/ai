#!/usr/bin/env bash
# Deploy site/ to https://valorengels.com. Safe to run anywhere:
# exits 0 with a notice on machines that cannot reach Cloudflare.
#
# What makes a machine able to deploy is CLOUDFLARE_API_TOKEN, not a wrangler
# binary on PATH. wrangler is not a repo dependency and no machine here has it
# installed globally, so gating on `command -v wrangler` skipped the deploy
# everywhere. Resolve a runner instead: an installed wrangler if there is one,
# otherwise npx, which fetches it on demand.
set -euo pipefail
cd "$(dirname "$0")/.."

# Load the vault .env so the token is present when this runs outside a shell
# that already sourced it (launchd, cron, a bare `bash scripts/deploy-site.sh`).
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  echo "deploy-site: CLOUDFLARE_API_TOKEN unset. Run from a machine with the vault .env"
  exit 0
fi

if command -v wrangler >/dev/null 2>&1; then
  WRANGLER=(wrangler)
elif command -v npx >/dev/null 2>&1; then
  WRANGLER=(npx --yes wrangler@latest)
else
  echo "deploy-site: neither wrangler nor npx available. Install Node to deploy from here"
  exit 0
fi

"${WRANGLER[@]}" deploy
curl -sf https://valorengels.com/ >/dev/null && echo "deploy-site: live OK" || { echo "deploy-site: liveness check FAILED. Consider wrangler rollback"; exit 1; }
