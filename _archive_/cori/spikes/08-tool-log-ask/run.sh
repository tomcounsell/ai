#!/usr/bin/env bash
# Spike 08: tool log and ask. Runs under the root project because it needs
# pydantic-ai; imports spike 03's gateway by path. Makes a few Haiku 4.5
# calls through the gateway with the key from the Keychain.
set -euo pipefail
unset VIRTUAL_ENV
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../.." && uv run python spikes/08-tool-log-ask/run.py "$@"
