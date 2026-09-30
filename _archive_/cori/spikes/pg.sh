#!/usr/bin/env bash
# Shared helper: private Postgres cluster for the spikes, port 5499.
# Source this file, then call pg_spike_start <dbname> and pg_spike_stop.
SPIKES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PGDATA="$SPIKES_DIR/.pgdata-01"
export PGPORT=5499
export PGHOST=127.0.0.1

pg_spike_start() {
  local db="$1"
  if [ ! -d "$PGDATA" ]; then
    initdb -D "$PGDATA" -U "$USER" --auth=trust >/dev/null
  fi
  if ! pg_isready -q -h 127.0.0.1 -p $PGPORT; then
    pg_ctl -D "$PGDATA" -o "-p $PGPORT -c listen_addresses=127.0.0.1" -l "$PGDATA/server.log" start >/dev/null
    until pg_isready -q -h 127.0.0.1 -p $PGPORT; do sleep 0.2; done
  fi
  psql -q -h 127.0.0.1 -p $PGPORT -d postgres -c "DROP DATABASE IF EXISTS $db" -c "CREATE DATABASE $db" >/dev/null
  export CORI_SPIKE_DSN="postgresql://127.0.0.1:5499/$db"
}

pg_spike_stop() { pg_ctl -D "$PGDATA" stop -m fast >/dev/null 2>&1 || true; }
