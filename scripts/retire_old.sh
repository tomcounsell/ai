#!/bin/bash
# Retire the old Valor system on this Mac (docs/rollout.md, step 8). Run it
# once the new kernel's bridges read this Mac's chats; from then on nothing
# of the old system runs or loads here. Safe to re-run: what is already gone
# is skipped. Data is moved to $VALOR_RETIRED/<date> (default ~/valor-retired),
# never deleted; only caches and the container runtime's VM images are.
#
#   scripts/retire_old.sh [OLD_CHECKOUT...]
#
# Each OLD_CHECKOUT (an old system's clone kept aside, e.g. ~/src/_archive_/ai-dinosaur)
# is moved whole into the archive, stashes, worktrees and ignored files with it. The kernel's own
# checkout loses only the old system's ignored data/, logs/, telegram_sessions/
# and clean session worktrees; a worktree with changes is kept and listed.
set -u
A=${VALOR_RETIRED:-$HOME/valor-retired}/$(date +%Y%m%d)
D=gui/$(id -u)
LA=~/Library/LaunchAgents
KERNEL=$(cd "$(dirname "$0")/.." && pwd -P)
mkdir -p "$A" && chmod 700 "$A"
say() { printf '%s\n' "$*"; }
keep() { [ -e "$1" ] || [ -L "$1" ] || return 1; mkdir -p "$A/$2" && mv "$1" "$A/$2/"; }

# 1. The old jobs. The updater goes first: it would pull main and restart the rest.
for L in com.valor.update com.valor.bridge-watchdog com.valor.worker-watchdog com.valor.bridge \
         com.valor.email-bridge com.valor.worker com.valor.reflection-worker com.valor.log-rotate \
         com.valor.nightly-tests; do
  launchctl disable "$D/$L" 2>/dev/null; launchctl bootout "$D/$L" 2>/dev/null
  for P in "$LA/$L.plist" "$LA/$L".plist.*; do keep "$P" LaunchAgents && say "job $L retired"; done
done
# A job of the new system's that runs from a checkout other than this one (the rebuild's).
for P in "$LA"/com.valor.*.plist; do
  [ -f "$P" ] && grep -q -e "/src/valor-rebuild" -e "/src/_archive_" "$P" || continue
  launchctl bootout "$D/$(basename "$P" .plist)" 2>/dev/null; keep "$P" LaunchAgents && say "job $(basename "$P") retired"
done
pkill -f "bridge/telegram_bridge.py|-m worker|-m reflections|email_bridge|mcp_servers.memory_server" 2>/dev/null

# 2. The old Redis: stopped, its files kept. The formula stays: tasks start their own redis-server.
if [ -d /opt/homebrew/var/db/redis ]; then
  brew services stop redis >/dev/null 2>&1
  rm -f /opt/homebrew/var/db/redis/temp-*.rdb
  keep /opt/homebrew/var/db/redis redis-data && say "old Redis stopped, files kept"
fi

# 3. Claude Code: the old memory MCP server, its hooks, and the skills, agents and
# commands it synced in. The new system installs none of these; turns use the plugins.
if command -v claude >/dev/null && claude mcp list 2>/dev/null | grep -q "^memory:"; then
  claude mcp remove memory -s user >/dev/null && say "old memory MCP server removed"
fi
S=~/.claude/settings.json
if [ -f "$S" ] && grep -q -e "/.claude/hooks/" -e "/src/ai/scripts/" "$S"; then
  cp -p "$S" "$A/claude-settings.json"
  python3 - "$S" <<'EOF'
import json, sys
p = sys.argv[1]; d = json.load(open(p))
old = lambda h: any(s in h.get("command", "") for s in ("/.claude/hooks/", "/src/ai/scripts/"))
for event, groups in list((d.get("hooks") or {}).items()):
    for g in groups:
        g["hooks"] = [h for h in g.get("hooks", []) if not old(h)]
    kept = [g for g in groups if g["hooks"]]
    if kept:
        d["hooks"][event] = kept
    else:
        del d["hooks"][event]
json.dump(d, open(p, "w"), indent=2)
EOF
  say "old Claude Code hooks removed"
fi
for X in agents commands hooks mcp-servers; do keep ~/.claude/$X claude && say "~/.claude/$X retired"; done
[ -n "$(ls -A ~/.claude/skills 2>/dev/null)" ] && keep ~/.claude/skills claude && mkdir ~/.claude/skills && say "~/.claude/skills retired"

# 4. The old command-line tools and the old system's state and caches.
for B in ~/.local/bin/sdlc-tool ~/.local/bin/valor-*; do keep "$B" bin && say "$(basename "$B") retired"; done
keep ~/.valor dot-valor && say "~/.valor retired"
rm -rf ~/.cache/valor ~/.cache/valor-encoder

# 5. The old system's files in the kernel's checkout, and old checkouts kept aside.
cd "$KERNEL" || exit 1
for X in data logs telegram_sessions; do
  [ -e "$X" ] && git check-ignore -q "$X" && keep "$KERNEL/$X" checkout && say "$X/ retired"
done
git worktree list --porcelain | sed -n 's/^worktree //p' | grep "/.worktrees/" | while read -r W; do
  if [ -z "$(git -C "$W" status --porcelain 2>/dev/null)" ]; then git worktree remove --force "$W"
  else say "kept $W: it has changes"; fi
done
git worktree prune
for O in "$@"; do
  [ -d "$O" ] && [ "$(cd "$O" && pwd -P)" != "$KERNEL" ] || continue
  mv "$O" "$A/" && say "$O moved to $A/$(basename "$O")"
done

# 6. Apple's container runtime: only the removed VM verifier used it.
if [ -x /usr/local/bin/container ]; then
  /usr/local/bin/container system stop >/dev/null 2>&1
  rm -rf ~/Library/Application\ Support/com.apple.container
  say "container runtime stopped and its images removed; uninstall it with:"
  say "  sudo /usr/local/bin/uninstall-container.sh -d"
fi

say "kept in $A"
