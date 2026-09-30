#!/usr/bin/env bash
# Spike 04: cache economics of a deterministic, volatility-ordered render.
set -euo pipefail
unset VIRTUAL_ENV
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." && uv run python 04-cache-economics/run.py "$@"
