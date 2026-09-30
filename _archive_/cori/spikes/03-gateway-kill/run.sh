#!/usr/bin/env bash
# Spike 03: gateway kill and trace. Makes a handful of Haiku 4.5 calls.
set -euo pipefail
unset VIRTUAL_ENV
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." && uv run python 03-gateway-kill/run.py "$@"
