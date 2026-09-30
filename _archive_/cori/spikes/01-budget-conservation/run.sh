#!/usr/bin/env bash
# Spike 01: budget conservation under concurrency.
# Starts a private Postgres cluster on port 5499, loads the schema, runs the
# Hypothesis stateful test and the raw concurrency benchmark, then stops it.
set -euo pipefail
unset VIRTUAL_ENV
HERE="$(cd "$(dirname "$0")" && pwd)"
SPIKES="$(dirname "$HERE")"
export PGDATA="$SPIKES/.pgdata-01"
export PGPORT=5499
export PGHOST=127.0.0.1
export CORI_SPIKE_DSN="postgresql://127.0.0.1:5499/cori_spike_budget"

cleanup() { pg_ctl -D "$PGDATA" stop -m fast >/dev/null 2>&1 || true; }
trap cleanup EXIT

if [ ! -d "$PGDATA" ]; then
  initdb -D "$PGDATA" -U "$USER" --auth=trust >/dev/null
fi
pg_ctl -D "$PGDATA" -o "-p $PGPORT -c listen_addresses=127.0.0.1 -c fsync=on" -l "$PGDATA/server.log" start >/dev/null
until pg_isready -q -h 127.0.0.1 -p $PGPORT; do sleep 0.2; done

psql -q -h 127.0.0.1 -p $PGPORT -d postgres -c "DROP DATABASE IF EXISTS cori_spike_budget" -c "CREATE DATABASE cori_spike_budget"
psql -q -h 127.0.0.1 -p $PGPORT -d cori_spike_budget -f "$HERE/schema.sql"

cd "$SPIKES"
echo "== grants =="
uv run python 01-budget-conservation/check_grants.py
echo "== hypothesis stateful =="
uv run pytest -q 01-budget-conservation/test_conservation.py "$@"
echo "== concurrency =="
uv run python 01-budget-conservation/bench.py
