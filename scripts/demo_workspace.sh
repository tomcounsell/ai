#!/usr/bin/env bash
# The demonstration's workspace: psyoptimal at the replay's base commit with
# no history after it, a local bare repository as its only remote, a
# Postgres cluster of its own for the app's tests, and the isolation every
# turn runs under.
#
#     scripts/demo_workspace.sh             # build what is missing, start Postgres
#     scripts/demo_workspace.sh --rebuild   # delete everything and build it again
#     scripts/demo_workspace.sh --teardown  # stop the workspace's Postgres, delete it
#
# Paths and the Postgres binaries come from the kernel's settings
# (`python -m core.settings`, run with $VALOR_PYTHON, default the
# checkout's .venv). Layout under $DEMO (the `demo_dir` setting):
#   psyoptimal/          the clone Valor works in, branch valor/profile-completion
#   origin.git/          bare repository, the clone's `origin`; only the
#                        broker's push_branch performer writes it, after Tom's tap
#   pg/data/             the workspace's Postgres cluster, on 127.0.0.1:$PG_PORT
#                        with password auth; a turn can neither read nor write it
#   pg/run/              that cluster's unix socket
#   pg/postgres.log      its server log
#   home/gitconfig       git's global config for a turn: Valor's identity, no
#                        credential helper
#   home/gh/             an empty gh config directory: gh is unauthenticated
#   home/sandbox.sb      the sandbox-exec profile every turn runs under
#   home/harness.json    the above, as `python -m core start --harness-config`,
#                        with the environment pointing the app at pg/
#
# Live spend: none. Network: one clone from GitHub, run as Tom.

set -euo pipefail

REPO="yudame/psyoptimal"
BASE="ebdbf645a0c3302a90b77652852142f45983e84b"
BRANCH="valor/profile-completion"
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
eval "$(cd "$ROOT" && "${VALOR_PYTHON:-$ROOT/.venv/bin/python}" -m core.settings)"
DEMO="$SETTING_DEMO_DIR"
PG_BIN="$SETTING_PG_BIN"
PG_PORT="${VALOR_DEMO_PG_PORT:-5439}"
# What a turn may neither read nor write: the kernel's password file, the
# machine cluster's data directory, and the backup disk.
KERNEL_PASSDIR="$(dirname "$SETTING_PG_PASSFILE")"
KERNEL_PGDATA="$SETTING_PG_DATA_DIR"
BACKUP_DIR="$SETTING_BACKUP_DIR"

mkdir -p "$DEMO"
DEMO="$(cd "$DEMO" && pwd -P)"
PG="$DEMO/pg"
cd "$DEMO"

stop_postgres() {
    if [[ -f "$PG/data/postmaster.pid" ]]; then
        "$PG_BIN/pg_ctl" -D "$PG/data" -m fast -w stop
    fi
}

case "${1:-}" in
    --teardown)
        stop_postgres
        rm -rf "$PG"
        echo "workspace Postgres stopped and deleted"
        exit 0
        ;;
    --rebuild)
        stop_postgres
        rm -rf "$DEMO/psyoptimal" "$DEMO/origin.git" "$DEMO/home" "$PG"
        ;;
    "") ;;
    *)
        echo "usage: $0 [--rebuild | --teardown]" >&2
        exit 2
        ;;
esac

# -- the clone, holding nothing after the base commit ---------------------------
if [[ -e psyoptimal ]]; then
    echo "keeping $DEMO/psyoptimal (pass --rebuild to start over)"
else
    scratch="$(mktemp -d)"
    gh repo clone "$REPO" "$scratch/full" -- --quiet
    git -C "$scratch/full" cat-file -e "$BASE^{commit}"
    git -C "$scratch/full" branch --quiet --force replay-base "$BASE"
    git clone --quiet --no-tags --single-branch --branch replay-base "file://$scratch/full" psyoptimal
    rm -rf "$scratch"
    git -C psyoptimal checkout --quiet -b "$BRANCH"
    git -C psyoptimal branch --quiet -D replay-base
    git -C psyoptimal remote remove origin
    git -C psyoptimal reflog expire --expire=now --all
    git -C psyoptimal gc --quiet --prune=now
    echo ".valor/" >> psyoptimal/.git/info/exclude

    # -- the bare origin: main at the base commit, every ref update logged
    git init --quiet --bare origin.git
    git -C origin.git config core.logAllRefUpdates always
    git -C origin.git config receive.denyNonFastForwards true
    git -C psyoptimal remote add origin "$DEMO/origin.git"
    git -C psyoptimal push --quiet origin "$BASE:refs/heads/main"
    git -C psyoptimal fetch --quiet origin
fi

# -- what a turn runs under ----------------------------------------------------
mkdir -p home/gh
cat > home/gitconfig <<EOF
[user]
	name = Valor Engels
	email = valor@yuda.me
[init]
	defaultBranch = main
EOF

# The turn may read and write the demo directory, its own Claude Code
# session files, and everything a toolchain needs. It may not read Tom's
# other checkouts (his psyoptimal holds the answer), his notes, his earlier
# Claude Code transcripts and plans, his keys, the kernel's password file,
# the machine cluster's data directory, or the backup disk (denied last, so
# no allow above can reopen them); it may not write the bare
# origin, touch the workspace cluster's data directory, or run git's keychain
# credential helper; and on this Mac's loopback it reaches only the gateway,
# the workspace's Postgres on $PG_PORT, and dev servers on 8000-8009. This
# Mac's own Postgres (its port and socket, which hold the kernel's
# ledger) and Redis are out of reach. The loopback allows come last: any
# network-outbound rule after them makes sandbox-exec refuse the allowed
# ports for roughly two in five port numbers, so a turn's gateway, which
# listens on whatever port the OS hands it, came out EPERM at random.
# A turn listens only on 8000-8009 and on unix sockets inside the demo
# directory. sandbox-exec matches a bind on its port alone (localhost:8001
# admits 0.0.0.0:8001), so nothing else, loopback or not, can be served from
# a turn. The mach name valor.turn.$VALOR_TURN marks the turn's processes for
# the kernel's reaper, daemonized or not.
H="$HOME"
PG_SOCKET="$SETTING_PG_SOCKET_REAL"
KERNEL_SOCKET="$SETTING_PG_SOCKET"
TRANSCRIPTS="$H/.claude/projects/$(echo "$DEMO/psyoptimal" | tr '/.' '--')"
cat > home/sandbox.sb <<EOF
(version 1)
(allow default)
(deny file-read* file-write*
    (subpath "$H/src")
    (subpath "$H/work-vault")
    (subpath "$H/Desktop")
    (subpath "$H/Documents")
    (subpath "$H/Downloads")
    (subpath "$H/Dropbox")
    (subpath "$H/Library/CloudStorage")
    (subpath "$H/Library/Mobile Documents")
    (subpath "$H/Library/Mail")
    (subpath "$H/Library/Messages")
    (subpath "$H/.ssh")
    (subpath "$H/.config/gh")
    (subpath "$H/.claude/projects")
    (subpath "$H/.claude/plans")
    (subpath "$H/.claude/file-history")
    (literal "$H/.claude/history.jsonl"))
(allow file-read* file-write*
    (subpath "$DEMO")
    (subpath "$TRANSCRIPTS"))
(deny file-write* (subpath "$DEMO/origin.git"))
(deny file-read* file-write* (subpath "$PG/data"))
(deny file-write* (literal "$PG/postgres.log"))
(deny file-read* file-write*
    (subpath "$KERNEL_PASSDIR")
    (subpath "$KERNEL_PGDATA")
    (subpath "$BACKUP_DIR"))
(deny process-exec (regex #"/git-credential-osxkeychain$"))
(deny mach-lookup (global-name (string-append "valor.turn." (param "VALOR_TURN"))))
(deny network-bind network-inbound)
(allow network-bind network-inbound
$(for p in 8000 8001 8002 8003 8004 8005 8006 8007 8008 8009; do echo "    (local ip \"localhost:$p\")"; done)
    (local unix-socket (subpath "$DEMO")))
(deny network-outbound
    (remote ip "localhost:*")
    (remote unix-socket (path-literal "$PG_SOCKET"))
    (remote unix-socket (path-literal "$KERNEL_SOCKET")))
(allow network-outbound
    (remote ip (string-append "localhost:" (param "GATEWAY_PORT")))
    (remote ip "localhost:$PG_PORT")
$(for p in 8000 8001 8002 8003 8004 8005 8006 8007 8008 8009; do echo "    (remote ip \"localhost:$p\")"; done))
EOF

# The app's settings read the database from the environment: settings/test.py
# takes TEST_DB_* (it connects to `postgres` and creates test_psyoptimal
# itself), the dev settings take DATABASE_URL, and psql takes PG*.
cat > home/harness.json <<EOF
{
  "sandbox_profile": "$DEMO/home/sandbox.sb",
  "gitconfig": "$DEMO/home/gitconfig",
  "gh_config_dir": "$DEMO/home/gh",
  "env": {
    "TEST_DB_HOST": "127.0.0.1",
    "TEST_DB_PORT": "$PG_PORT",
    "TEST_DB_USER": "test",
    "TEST_DB_PASSWORD": "test",
    "DATABASE_URL": "postgresql://test:test@127.0.0.1:$PG_PORT/psyoptimal",
    "PGHOST": "127.0.0.1",
    "PGPORT": "$PG_PORT",
    "PGUSER": "test",
    "PGPASSWORD": "test",
    "PGDATABASE": "postgres"
  }
}
EOF

# -- the workspace's own Postgres ----------------------------------------------
# A cluster of its own, so a turn never shares one with the kernel's ledger.
# It listens on 127.0.0.1:$PG_PORT and a socket in pg/run, and every login,
# socket or TCP, needs a password. The superuser's password is random and
# thrown away once the `test` role exists: the server runs outside the
# sandbox, so a superuser login would be a way out of it. `test` has
# CREATEDB, which Django's test runner needs; `psyoptimal` is the dev
# database DATABASE_URL names.
if [[ ! -f "$PG/data/PG_VERSION" ]]; then
    if lsof -nP -iTCP:"$PG_PORT" -sTCP:LISTEN > /dev/null 2>&1; then
        echo "port $PG_PORT is taken; set VALOR_DEMO_PG_PORT" >&2
        exit 1
    fi
    rm -rf "$PG"
    mkdir -p "$PG/run"
    chmod 700 "$PG"
    pwfile="$(mktemp)"
    trap 'rm -f "$pwfile"' EXIT
    openssl rand -hex 32 > "$pwfile"
    "$PG_BIN/initdb" --pgdata="$PG/data" --username=postgres --pwfile="$pwfile" \
        --auth=scram-sha-256 --encoding=UTF8 --locale=C > /dev/null
    cat >> "$PG/data/postgresql.conf" <<EOF
listen_addresses = '127.0.0.1'
port = $PG_PORT
unix_socket_directories = '$PG/run'
EOF
    "$PG_BIN/pg_ctl" -D "$PG/data" -l "$PG/postgres.log" -w start > /dev/null
    PGPASSWORD="$(cat "$pwfile")" "$PG_BIN/psql" -h "$PG/run" -p "$PG_PORT" -U postgres -d postgres -qAt > /dev/null <<'SQL'
CREATE ROLE test LOGIN CREATEDB PASSWORD 'test';
CREATE DATABASE psyoptimal OWNER test;
SQL
    rm -f "$pwfile"
elif ! "$PG_BIN/pg_ctl" -D "$PG/data" status > /dev/null 2>&1; then
    "$PG_BIN/pg_ctl" -D "$PG/data" -l "$PG/postgres.log" -w start > /dev/null
fi
echo "workspace Postgres: test:test@127.0.0.1:$PG_PORT, socket $PG/run, log $PG/postgres.log"

# -- leak check: anything in the base tree that already holds the answer -------
echo "== leak check at $BASE"
cd psyoptimal
hits="$(git grep -n -i -E 'profile_completion|profile[ _-]completion|incomplete[ _-]profile|profile[ _-]incomplete|complete your profile|career start' -- . || true)"
if [[ -n "$hits" ]]; then echo "$hits"; else echo "nothing mentions profile completion"; fi
plans="$(git ls-files | grep -i -E '(^|/)(plans?|specs?)/' | xargs grep -l -i -E 'profile.*(complet|incomplet)|career start' || true)"
if [[ -n "$plans" ]]; then echo "plan docs touching the feature:"; echo "$plans"; else echo "no plan doc touches the feature"; fi
echo "commits after the base: $(git rev-list --all --not "$BASE" | wc -l | tr -d ' ')"
echo "refs: $(git for-each-ref --format='%(refname)' | tr '\n' ' ')"
echo "workspace ready: $DEMO/psyoptimal on $(git branch --show-current) at $(git rev-parse --short HEAD)"
