#!/usr/bin/env bash
# Spike 06: apple/container. Needs `brew install container`, Rosetta for the
# builder VM, and the base image from infra/sandbox.
set -euo pipefail
cd "$(dirname "$0")"
container system start --enable-kernel-install >/dev/null 2>&1 || true
container image inspect cori-base:3.14 >/dev/null 2>&1 || \
  (cd ../../infra/sandbox && container build -t cori-base:3.14 -f base.Dockerfile .)
container network inspect cori-hostonly >/dev/null 2>&1 || container network create --internal cori-hostonly
container network inspect cori-egress   >/dev/null 2>&1 || container network create cori-egress
exec python3 spike.py
