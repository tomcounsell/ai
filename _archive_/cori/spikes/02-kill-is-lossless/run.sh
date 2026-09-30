#!/usr/bin/env bash
# Spike 02: kill is lossless. Chaos-kills a stateless control loop at random
# points and checks it converges to the unkilled run's final state.
set -euo pipefail
unset VIRTUAL_ENV
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/../pg.sh"
trap pg_spike_stop EXIT
pg_spike_start cori_spike_kill
psql -q -h 127.0.0.1 -p 5499 -d cori_spike_kill -f "$HERE/schema.sql"
cd "$HERE/.."
uv run python 02-kill-is-lossless/chaos.py "$@"
