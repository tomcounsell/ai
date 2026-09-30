#!/usr/bin/env bash
set -euo pipefail
unset VIRTUAL_ENV
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/.." && uv run pytest -q 05-capability-attenuation/test_caps.py --hypothesis-show-statistics "$@"
