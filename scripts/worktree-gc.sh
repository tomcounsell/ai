#!/usr/bin/env bash
# worktree-gc: identify and prune stale git worktrees.
#
# Stale = unlocked, branch has no open PR, branch is either fully merged
# or has no commits past the merge-base with main. Each worktree
# typically holds 500MB-2GB of duplicated source/venv/node_modules,
# so 117 stale worktrees can easily account for 100+ GB of disk.
#
# Safety: dry-run by default. Only removes worktrees whose branch is
# neither on an open PR nor merged within --protect-days (default 14).
# Use --keep <token> to whitelist specific worktree paths or branch
# substrings; anything matching a token is never pruned.
#
# Usage:
#   scripts/worktree-gc.sh                    # dry-run
#   scripts/worktree-gc.sh --apply            # actually remove
#   scripts/worktree-gc.sh --apply --keep granite-pty-production-cutover
#                                             # prune everything except that one
#   scripts/worktree-gc.sh --apply --protect-days 30
#
# Exit codes:
#   0 - dry-run (or apply succeeded)
#   1 - error
#   2 - usage error

set -euo pipefail

APPLY=0
PROTECT_DAYS=14
KEEP_TOKENS=()
REPO_ROOT="$(git rev-parse --show-toplevel)"

# Manual two-pointer parse so --keep can take a value.
ARGS=("$@")
i=0
while [ $i -lt ${#ARGS[@]} ]; do
    arg="${ARGS[$i]}"
    case "$arg" in
        --apply) APPLY=1 ;;
        --protect-days)
            i=$((i + 1))
            PROTECT_DAYS="${ARGS[$i]:-14}"
            ;;
        --keep)
            i=$((i + 1))
            KEEP_TOKENS+=("${ARGS[$i]:-}")
            ;;
        --help|-h)
            sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
    i=$((i + 1))
done

# --- gather PR data --------------------------------------------------------

echo "Querying open/merged PRs..."
OPEN_BRANCHES=$(gh pr list --state open --limit 500 --json headRefName 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print('\n'.join(p['headRefName'] for p in d))" 2>/dev/null || echo "")
# also: recently merged (within protect window)
MERGED_BRANCHES=$(gh pr list --state merged --limit 500 --json headRefName,mergedAt 2>/dev/null | python3 -c "
import json, sys, datetime
d = json.load(sys.stdin)
protect = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=$PROTECT_DAYS)
for p in d:
    if p.get('mergedAt'):
        t = datetime.datetime.fromisoformat(p['mergedAt'].replace('Z', '+00:00'))
        if t >= protect:
            print(p['headRefName'])
" 2>/dev/null || echo "")

OPEN_COUNT=$(echo -n "$OPEN_BRANCHES" | grep -c . || true)
MERGED_COUNT=$(echo -n "$MERGED_BRANCHES" | grep -c . || true)
echo "Open PR branches: $OPEN_COUNT | Recently-merged (≤${PROTECT_DAYS}d): $MERGED_COUNT"

# --- enumerate worktrees ---------------------------------------------------

# porcelain output: blocks separated by blank line, each block has
# worktree, HEAD, branch, [locked], [prunable].
WT_BLOCKS=()
current=""
while IFS= read -r line; do
    if [ -z "$line" ]; then
        [ -n "$current" ] && WT_BLOCKS+=("$current")
        current=""
    else
        current+="$line"$'\n'
    fi
done < <(git -C "$REPO_ROOT" worktree list --porcelain)
[ -n "$current" ] && WT_BLOCKS+=("$current")
TOTAL_WT=0
PRUNE_CANDIDATES=()
PRUNED_BYTES=0
SKIPPED_OPEN=0
SKIPPED_KEEP=0
SKIPPED_MAIN=0
SKIPPED_LOCKED=0

parse_block() {
    local block="$1"
    local path="" head="" branch="" locked=0
    while IFS= read -r line; do
        case "$line" in
            "worktree "*) path="${line#worktree }" ;;
            "HEAD "*) head="${line#HEAD }" ;;
            "branch "*) branch="${line#branch refs/heads/}" ;;
            "locked "*) locked=1 ;;
        esac
    done <<< "$block"
    echo "$path|$head|$branch|$locked"
}

is_in_keep_tokens() {
    local s="$1"
    for tok in "${KEEP_TOKENS[@]:-}"; do
        [ -z "$tok" ] && continue
        if [[ "$s" == *"$tok"* ]]; then return 0; fi
    done
    return 1
}

for block in "${WT_BLOCKS[@]}"; do
    [ -z "$block" ] && continue
    TOTAL_WT=$((TOTAL_WT + 1))
    line=$(parse_block "$block")
    IFS='|' read -r path head branch locked <<< "$line"

    # Skip the main checkout
    if [ "$path" = "$REPO_ROOT" ]; then
        SKIPPED_MAIN=$((SKIPPED_MAIN + 1))
        continue
    fi

    # Locked → owned by a running claude agent, never touch
    if [ "$locked" = "1" ]; then
        SKIPPED_LOCKED=$((SKIPPED_LOCKED + 1))
        continue
    fi

    # User keep-tokens
    if is_in_keep_tokens "$path" || is_in_keep_tokens "${branch:-}"; then
        SKIPPED_KEEP=$((SKIPPED_KEEP + 1))
        continue
    fi

    # Branch is on an open PR
    if [ -n "$branch" ] && echo "$OPEN_BRANCHES" | grep -qx "$branch"; then
        SKIPPED_OPEN=$((SKIPPED_OPEN + 1))
        continue
    fi

    # Branch was merged within protect window
    if [ -n "$branch" ] && echo "$MERGED_BRANCHES" | grep -qx "$branch"; then
        SKIPPED_OPEN=$((SKIPPED_OPEN + 1))
        continue
    fi

    # Compute disk cost
    bytes=$(du -sk "$path" 2>/dev/null | awk '{print $1}' || echo 0)
    PRUNED_BYTES=$((PRUNED_BYTES + bytes))
    PRUNE_CANDIDATES+=("$path|${branch:-<detached>}|${head:0:8}|${bytes}")
done

# --- report ---------------------------------------------------------------

PRUNE_COUNT=${#PRUNE_CANDIDATES[@]}
PRUNE_MB=$((PRUNED_BYTES / 1024))

echo ""
echo "=== Summary ==="
echo "Total worktrees:        $TOTAL_WT"
echo "  Main checkout:        $SKIPPED_MAIN"
echo "  Locked (in use):      $SKIPPED_LOCKED"
echo "  Open/merged PR:       $SKIPPED_OPEN"
echo "  Keep tokens:          $SKIPPED_KEEP"
echo "  Prune candidates:     $PRUNE_COUNT  (~${PRUNE_MB} MB)"

if [ "$PRUNE_COUNT" -eq 0 ]; then
    echo "Nothing to prune."
    exit 0
fi

echo ""
printf "%-7s %-10s %-50s %s\n" "ACTION" "BRANCH" "PATH" "MB"
printf "%-7s %-10s %-50s %s\n" "------" "------" "----" "--"
for c in "${PRUNE_CANDIDATES[@]}"; do
    IFS='|' read -r path branch head bytes <<< "$c"
    mb=$((bytes / 1024))
    printf "  prune %-50s %5d  %s\n" "${path#$REPO_ROOT/}" "$mb" "$branch"
done

if [ "$APPLY" -eq 0 ]; then
    echo ""
    echo "Dry-run. Re-run with --apply to actually remove worktrees."
    exit 0
fi

# --- apply -----------------------------------------------------------------

echo ""
echo "Pruning ${PRUNE_COUNT} worktree(s)..."
for c in "${PRUNE_CANDIDATES[@]}"; do
    IFS='|' read -r path branch head bytes <<< "$c"
    if git -C "$REPO_ROOT" worktree remove --force "$path" 2>/dev/null; then
        # Try to delete the branch too (best-effort)
        if [ -n "$branch" ] && [ "$branch" != "<detached>" ]; then
            git -C "$REPO_ROOT" branch -D "$branch" 2>/dev/null || true
        fi
        echo "  removed: ${path#$REPO_ROOT/}"
    else
        echo "  FAILED:  ${path#$REPO_ROOT/}" >&2
    fi
done

echo ""
REMAINING=$(git -C "$REPO_ROOT" worktree list | wc -l | tr -d ' ')
echo "Remaining worktrees: $REMAINING"
