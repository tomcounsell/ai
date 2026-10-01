#!/usr/bin/env bash
# The demonstration's workspace: psyoptimal at the replay's base commit with
# no history after it, a local bare repository as its only remote, the
# Postgres role its tests expect, and the isolation every turn runs under.
#
#     scripts/demo_workspace.sh            # build it (refuses if it exists)
#     scripts/demo_workspace.sh --rebuild  # delete it and build it again
#
# Layout under $DEMO (default /Users/tomcounsell/src/valor-demo):
#   psyoptimal/          the clone Valor works in, branch valor/profile-completion
#   origin.git/          bare repository, the clone's `origin`; only the
#                        broker's push_branch performer writes it, after Tom's tap
#   home/gitconfig       git's global config for a turn: Valor's identity, no
#                        credential helper
#   home/gh/             an empty gh config directory: gh is unauthenticated
#   home/sandbox.sb      the sandbox-exec profile every turn runs under
#   home/harness.json    the above, as `python -m core start --harness-config`
#
# Live spend: none. Network: one clone from GitHub, run as Tom.

set -euo pipefail

REPO="yudame/psyoptimal"
BASE="ebdbf645a0c3302a90b77652852142f45983e84b"
BRANCH="valor/profile-completion"
DEMO="${VALOR_DEMO:-/Users/tomcounsell/src/valor-demo}"

if [[ "${1:-}" == "--rebuild" ]]; then
    rm -rf "$DEMO/psyoptimal" "$DEMO/origin.git" "$DEMO/home"
fi
if [[ -e "$DEMO/psyoptimal" ]]; then
    echo "$DEMO/psyoptimal exists; pass --rebuild to start over" >&2
    exit 1
fi
mkdir -p "$DEMO"
DEMO="$(cd "$DEMO" && pwd -P)"
cd "$DEMO"

# -- the clone, holding nothing after the base commit ---------------------------
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

# -- the bare origin: main at the base commit, every ref update logged ----------
git init --quiet --bare origin.git
git -C origin.git config core.logAllRefUpdates always
git -C origin.git config receive.denyNonFastForwards true
git -C psyoptimal remote add origin "$DEMO/origin.git"
git -C psyoptimal push --quiet origin "$BASE:refs/heads/main"
git -C psyoptimal fetch --quiet origin

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
# Claude Code transcripts and plans, or his keys; it may not write the bare
# origin or run git's keychain credential helper; and on this Mac's loopback it reaches only the gateway, Postgres
# over TCP for the app's tests, and dev servers on 8000-8009. The kernel's
# Postgres socket and Redis are out of reach.
H="$HOME"
PG_SOCKET="$(cd /tmp && pwd -P)/.s.PGSQL.5432"
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
(deny process-exec (regex #"/git-credential-osxkeychain$"))
(deny network-outbound (remote ip "localhost:*"))
(allow network-outbound
    (remote ip (string-append "localhost:" (param "GATEWAY_PORT")))
    (remote ip "localhost:5432")
$(for p in 8000 8001 8002 8003 8004 8005 8006 8007 8008 8009; do echo "    (remote ip \"localhost:$p\")"; done))
(deny network-outbound (remote unix-socket (path-literal "$PG_SOCKET")))
EOF

cat > home/harness.json <<EOF
{
  "sandbox_profile": "$DEMO/home/sandbox.sb",
  "gitconfig": "$DEMO/home/gitconfig",
  "gh_config_dir": "$DEMO/home/gh"
}
EOF

# -- Postgres for the app's tests ----------------------------------------------
# settings/test.py names postgresql://test:test@localhost:5432/test_psyoptimal,
# so the role lives in this Mac's one cluster. Django's test runner creates
# its own test database, which needs CREATEDB.
psql -h /tmp -d postgres -qAt > /dev/null <<'SQL'
SELECT 'CREATE ROLE test LOGIN CREATEDB PASSWORD ''test''' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'test')\gexec
SELECT 'CREATE DATABASE test_psyoptimal OWNER test' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'test_psyoptimal')\gexec
SQL

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
