# Sub-Skill: PR Checkout

Mechanical setup: clean git state and checkout the PR branch.

## Context Variables

- `$SDLC_PR_NUMBER` — PR number to checkout (fallback: extract from coaching message or `gh pr list`)
- `$SDLC_PR_BRANCH` — expected branch name (informational)

## Steps

1. **Resolve PR number:**
   ```bash
   PR_NUMBER="${SDLC_PR_NUMBER:-}"
   if [ -z "$PR_NUMBER" ]; then
     echo "WARNING: SDLC_PR_NUMBER not set, attempting to detect from context"
     PR_NUMBER=$(gh pr list --state open --limit 1 --json number --jq '.[0].number')
   fi
   ```

2. **Clean git state:**
   ```bash
   python -c "from agent.worktree_manager import ensure_clean_git_state; from pathlib import Path; ensure_clean_git_state(Path('.'))"
   ```

3. **Checkout PR branch:**
   ```bash
   gh pr checkout $PR_NUMBER
   ```

4. **Verify checkout:**
   ```bash
   CURRENT_BRANCH=$(git branch --show-current)
   echo "Checked out branch: $CURRENT_BRANCH"
   ```

## Completion

Report the checked-out branch name and PR number. All subsequent sub-skills
will read files from this branch.
