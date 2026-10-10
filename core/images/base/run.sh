#!/bin/bash
# The entrypoint of every verification VM (core/container.py), as root.
#
# /valor/src (read-only) holds only source.tar and the kernel's spec.json;
# /valor/out is the output mount. /valor is 0700, so nothing that runs as
# `valor` can read the spec or reach the output. In order:
#
# 1. the source unpacked into /work as `valor`;
# 2. a child cgroup /sys/fs/cgroup/valor with the memory controller;
# 3. the spec's services on loopback, its roles with fresh passwords in
#    /home/valor/.pgpass;
# 4. as `valor`, in the child cgroup: the setup (offline), the suite, the
#    lint. Each command runs in its own session with its output to a file
#    under /var/log/valor, is waited on by its exit, never by end of file,
#    and the rest of its group is killed after it;
# 5. every `valor` process killed, then /valor/out/result.json (exit codes,
#    durations, memory.peak, oom_kill) and the JUnit file, each setup
#    command's output, and the lint output copied out, as root.
#
# No command has a time limit: a stop kills the VM.
set -uo pipefail

SPEC=/valor/src/spec.json
OUT=/valor/out
LOGS=/var/log/valor
CG=/sys/fs/cgroup
JUNIT=/home/valor/junit.xml
PGDATA=/var/lib/valor-pg
PGBIN=/usr/lib/postgresql/18/bin

now() { date +%s.%N; }
since() { awk -v a="$1" -v b="$(now)" 'BEGIN { printf "%.1f", b - a }'; }

# 1. The source, unpacked by `valor` from a file root opens.
setpriv --reuid valor --regid valor --init-groups tar -xf - -C /work --no-same-owner </valor/src/source.tar

# 2. The child cgroup: every process now in the root moves to a leaf, so the
# memory controller can be enabled for children.
mkdir -p "$CG/init"
for pid in $(cat "$CG/cgroup.procs"); do echo "$pid" >"$CG/init/cgroup.procs" 2>/dev/null; done
cgroup=0
if echo +memory >"$CG/cgroup.subtree_control" 2>/dev/null && mkdir -p "$CG/valor" \
   && [ -f "$CG/valor/memory.peak" ]; then
  cgroup=1
fi

# 3. Services. A service that will not start is the kernel's, never the
# candidate's: result.json says so and nothing of the candidate runs.
services_failed() {
  jq -n --arg why "$1" '{services: "failed", why: $why}' >"$OUT/result.json"
  exit 0
}
# Waits until `$2` succeeds, for as long as the process `$1` lives: a
# service that exits is a failure, one that is still starting is waited on
# (a stop kills the VM).
ready_while_alive() {
  until $2 >/dev/null 2>&1; do
    kill -0 "$1" 2>/dev/null || return 1
    sleep 0.1
  done
}
app_password=""
pgpass=/home/valor/.pgpass
if jq -e '.services | index("postgres")' "$SPEC" >/dev/null; then
  super=$(head -c 24 /dev/urandom | base64 | tr -d '/+=')
  app_password=$(head -c 24 /dev/urandom | base64 | tr -d '/+=')
  mkdir -p "$PGDATA" && chown postgres:postgres "$PGDATA" && chmod 0700 "$PGDATA"
  printf '%s\n' "$super" >/tmp/pg.pw && chown postgres /tmp/pg.pw && chmod 0600 /tmp/pg.pw
  setpriv --reuid postgres --regid postgres --init-groups "$PGBIN/initdb" -D "$PGDATA" -U postgres \
    --auth=scram-sha-256 --pwfile=/tmp/pg.pw -E UTF8 --locale=C >"$LOGS/initdb.log" 2>&1 \
    || services_failed "initdb failed"
  rm -f /tmp/pg.pw
  printf "listen_addresses = '127.0.0.1'\nport = 5432\nunix_socket_directories = ''\n" >>"$PGDATA/postgresql.conf"
  setsid setpriv --reuid postgres --regid postgres --init-groups "$PGBIN/postgres" -D "$PGDATA" \
    >"$LOGS/postgres.log" 2>&1 </dev/null &
  ready_while_alive $! "$PGBIN/pg_isready -q -h 127.0.0.1 -p 5432" || services_failed "postgres did not start"
  : >"$pgpass"
  printf '127.0.0.1:5432:*:app:%s\n' "$app_password" >>"$pgpass"
  {
    # The spec's extensions, made by the superuser in template1, so the
    # `app` database and the databases `app` creates hold them.
    echo '\connect template1'
    for e in $(jq -r '.extensions[]?' "$SPEC"); do
      echo "CREATE EXTENSION IF NOT EXISTS \"$e\";"
    done
    echo '\connect postgres'
    roles=$(jq -r '.roles[]?' "$SPEC")
    create=""
    [ -n "$roles" ] && create=" CREATEROLE"
    echo "CREATE ROLE app LOGIN CREATEDB$create PASSWORD '$app_password';"
    echo "GRANT pg_signal_backend, pg_read_all_settings TO app;"
    echo "CREATE DATABASE app OWNER app;"
    for r in $roles; do
      pw=$(head -c 24 /dev/urandom | base64 | tr -d '/+=')
      printf '127.0.0.1:5432:*:%s:%s\n' "$r" "$pw" >>"$pgpass"
      echo "CREATE ROLE \"$r\" LOGIN PASSWORD '$pw';"
      echo "GRANT \"$r\" TO app WITH ADMIN OPTION;"
    done
    echo "ALTER ROLE postgres PASSWORD NULL;"
  } | PGPASSWORD="$super" "$PGBIN/psql" -h 127.0.0.1 -p 5432 -U postgres -d postgres -v ON_ERROR_STOP=1 -q \
    >"$LOGS/roles.log" 2>&1 || services_failed "the roles were not created"
  chown valor:valor "$pgpass" && chmod 0600 "$pgpass"
fi
if jq -e '.services | index("redis")' "$SPEC" >/dev/null; then
  mkdir -p /var/lib/valor-redis && chown redis:redis /var/lib/valor-redis
  setsid setpriv --reuid redis --regid redis --init-groups redis-server --port 6379 --bind 127.0.0.1 \
    --protected-mode yes --save "" --appendonly no --dir /var/lib/valor-redis \
    >"$LOGS/redis.log" 2>&1 </dev/null &
  ready_while_alive $! "redis-cli -p 6379 ping" || services_failed "redis-server did not start"
fi

# The environment every `valor` command gets: the spec's, the password
# filled in by this script.
mapfile -d '' spec_env < <(jq -j --arg pw "$app_password" \
  '.env | to_entries[] | "\(.key)=\(.value | gsub("\\{app_password\\}"; $pw))\u0000"' "$SPEC")
valor_env=(HOME=/home/valor USER=valor LOGNAME=valor LANG=C.UTF-8 "${spec_env[@]}")

# 4. One command as `valor` in the child cgroup, in its own session, its
# output to a file; sets `code` and `took`.
run_as_valor() {
  local name=$1 command=$2 started pid
  started=$(now)
  (
    [ "$cgroup" = 1 ] && echo "$BASHPID" >"$CG/valor/cgroup.procs"
    # The OOM killer picks the candidate's processes first.
    echo 1000 >/proc/self/oom_score_adj
    exec setsid setpriv --reuid valor --regid valor --init-groups \
      env -i "${valor_env[@]}" /bin/bash -c "$command"
  ) >"$LOGS/$name.log" 2>&1 </dev/null &
  pid=$!
  code=0
  wait "$pid" || code=$?
  kill -KILL -- "-$pid" 2>/dev/null
  took=$(since "$started")
}

cd /work
setup='[]'
setup_ok=1
mapfile -d '' commands < <(jq -j '.setup[]? | "\(.)\u0000"' "$SPEC")
n=0
for command in "${commands[@]}"; do
  run_as_valor "setup-$n" "$command"
  setup=$(jq -c --arg c "$command" --argjson e "$code" --argjson d "$took" '. + [{command: $c, exit: $e, duration_s: $d}]' <<<"$setup")
  n=$((n + 1))
  if [ "$code" != 0 ]; then setup_ok=0; break; fi
done

suite=null
lint=null
if [ "$setup_ok" = 1 ]; then
  run_as_valor suite "$(jq -r '.suite' "$SPEC")"
  suite=$(jq -nc --argjson e "$code" --argjson d "$took" '{exit: $e, duration_s: $d}')
  lint_command=$(jq -r '.lint // empty' "$SPEC")
  if [ -n "$lint_command" ]; then
    run_as_valor lint "$lint_command"
    lint=$(jq -nc --argjson e "$code" --argjson d "$took" '{exit: $e, duration_s: $d}')
  fi
fi

# 5. Nothing of `valor` runs past here.
if [ "$cgroup" = 1 ]; then
  echo 1 >"$CG/valor/cgroup.kill"
  # Waited on until the group is empty; a stop kills the VM.
  while [ -n "$(cat "$CG/valor/cgroup.procs")" ]; do sleep 0.05; done
fi
pkill -KILL -u valor 2>/dev/null

peak=null
oom=null
if [ "$cgroup" = 1 ]; then
  peak=$(( $(cat "$CG/valor/memory.peak") / 1048576 ))
  oom=$(awk '$1 == "oom_kill" { print $2 }' "$CG/valor/memory.events")
fi
if [ "$cgroup" = 0 ] && dmesg 2>/dev/null | grep -q "Out of memory"; then oom=1; fi

# Only a plain file is copied out; a link or anything else stays behind.
copy_out() { [ -f "$1" ] && [ ! -L "$1" ] && cp "$1" "$OUT/$2"; }
copy_out "$JUNIT" junit.xml
for ((i = 0; i < n; i++)); do copy_out "$LOGS/setup-$i.log" "setup-$i.out"; done
copy_out "$LOGS/lint.log" lint.out

jq -n --argjson setup "$setup" --argjson suite "$suite" --argjson lint "$lint" \
  --argjson peak "$peak" --argjson oom "${oom:-null}" --argjson cgroup "$cgroup" \
  '{setup: $setup, suite: $suite, lint: $lint, peak_mb: $peak, oom_kill: $oom, cgroup: ($cgroup == 1)}' \
  >"$OUT/result.json"
