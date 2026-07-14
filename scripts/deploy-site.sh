#!/usr/bin/env bash
# Deploy site/ to https://valorengels.com. Safe to run anywhere:
# exits 0 with a notice on machines without wrangler or the vault token.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v wrangler >/dev/null 2>&1 || { echo "deploy-site: wrangler not installed — run from a machine with the vault token"; exit 0; }
wrangler deploy
curl -sf https://valorengels.com/ >/dev/null && echo "deploy-site: live OK" || { echo "deploy-site: liveness check FAILED — consider wrangler rollback"; exit 1; }
