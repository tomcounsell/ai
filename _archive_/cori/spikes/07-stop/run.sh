#!/usr/bin/env bash
# Spike 07: stop protocol on apple/container. Needs the image and network
# from spike 06 (infra/sandbox/base.Dockerfile, `cori-hostonly`).
set -euo pipefail
cd "$(dirname "$0")"
container system start --enable-kernel-install >/dev/null 2>&1 || true
container image inspect cori-base:3.14 >/dev/null 2>&1 || \
  (cd ../../infra/sandbox && container build -t cori-base:3.14 -f base.Dockerfile .)
container network inspect cori-hostonly >/dev/null 2>&1 || container network create --internal cori-hostonly
exec python3 spike.py
