# Merge Gate

You are the merge gate for the SDLC pipeline. Your job is to programmatically verify all prerequisites are met, then execute the merge.

## Pre-Merge Pipeline Check

Before running gate checks, take a moment to review what the pipeline actually completed. Run this and share the output with the user:

```bash
BRANCH=$(gh pr view $ARGUMENTS --json headRefName -q .headRefName)
SLUG=$(echo "$BRANCH" | sed 's|^session/||')

python3 -c "
from bridge.pipeline_graph import DISPLAY_STAGES

# Try programmatic state machine first
try:
    from bridge.pipeline_state import PipelineStateMachine
    from models.agent_session import AgentSession
    session = next((s for s in AgentSession.query.all() if s.slug == '$SLUG'), None)
    if session:
        sm = PipelineStateMachine(session)
        states = sm.get_display_progress()
    else:
        states = {}
except Exception:
    states = {}

# Show pipeline progress
icons = {'completed': '✓', 'in_progress': '~', 'failed': '✗'}
stages_display = []
skipped = []
for stage in DISPLAY_STAGES:
    if stage == 'MERGE':
        continue
    status = states.get(stage, 'pending')
    icon = icons.get(status, '·')
    stages_display.append(f'{icon} {stage}')
    if status in ('pending', 'ready'):
        skipped.append(stage)

print('Pipeline: ' + '  '.join(stages_display))
all_pending = all(s in ('pending', 'ready') for s in states.values())
if all_pending and not states:
    print()
    print('WARNING: No pipeline state found (cold start or Redis cleared).')
    print('Stage completion cannot be verified. Treat all stages as unconfirmed.')
    print('Proceed only if you have manually verified all stages completed.')
elif skipped:
    print()
    names = ', '.join(skipped)
    print(f'WARNING: The following stages were NOT recorded as completed: {names}')
    print()
    print('These stages may have been skipped entirely or run without writing state markers.')
    print('Do NOT proceed without explicitly acknowledging each skipped stage.')
    print('For emergency hotfixes only: state which stages you are intentionally skipping and why.')
else:
    print()
    print('All stages completed. Good to merge.')
"
```

If the pipeline state shows ALL stages as pending/ready (cold start — no Redis state recorded), warn
clearly that no pipeline state was found and require explicit acknowledgment before merging.

If specific stages were skipped, STOP and list them. Do NOT proceed until the user explicitly
acknowledges each skipped stage and provides a reason. Emergency hotfixes are the only valid
exception, and even then the acknowledgment must be explicit and on-record.

## Prerequisites Check

Use the pipeline state machine to verify TEST, REVIEW, and DOCS stages are completed:

```bash
# Extract slug from PR branch name
BRANCH=$(gh pr view $ARGUMENTS --json headRefName -q .headRefName)
SLUG=$(echo "$BRANCH" | sed 's|^session/||')

# Run programmatic gate checks via PipelineStateMachine
python -c "
from bridge.pipeline_state import PipelineStateMachine
from models.agent_session import AgentSession

try:
    session = next((s for s in AgentSession.query.all() if s.slug == '$SLUG'), None)
except Exception as e:
    print(f'ERROR: Failed to query sessions: {e}')
    print('Fall back to manual checks if needed.')
    print('GATES_FAILED')
    exit()

if not session:
    print('ERROR: No session found for slug $SLUG')
    print('GATES_FAILED')
    exit()

sm = PipelineStateMachine(session)
states = sm.get_display_progress()
required = ['TEST', 'REVIEW', 'DOCS']
all_pass = True
for stage in required:
    status = states.get(stage, 'pending')
    passed = status == 'completed'
    label = 'PASS' if passed else 'FAIL'
    print(f'{stage}: {label} — {status}')
    if not passed:
        all_pass = False
print()
print('ALL_GATES_PASS' if all_pass else 'GATES_FAILED')
"
```

### Structured Review Comment Check

Before authorizing merge, scan PR issue comments for the most recent `## Review:` comment:

```bash
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
LAST_REVIEW=$(gh api repos/$REPO/issues/$ARGUMENTS/comments \
  --jq '[.[] | select(.body | startswith("## Review:"))] | last | .body // ""' \
  2>/dev/null) || { echo "REVIEW_COMMENT: FAIL — gh api call failed (network/auth error)"; echo "GATES_FAILED"; exit 1; }

if [ -z "$LAST_REVIEW" ]; then
  echo "REVIEW_COMMENT: FAIL — No '## Review:' comment found on PR #$ARGUMENTS"
  echo "Run /do-pr-review before merging."
  echo "GATES_FAILED"
elif echo "$LAST_REVIEW" | grep -q "^## Review: Changes Requested"; then
  BLOCKERS=$(echo "$LAST_REVIEW" | grep "^- \[ \]" | head -20)
  echo "REVIEW_COMMENT: FAIL — Most recent review is 'Changes Requested'"
  echo "Unchecked blockers:"
  echo "$BLOCKERS"
  echo "GATES_FAILED"
else
  echo "REVIEW_COMMENT: PASS — Most recent review is 'Approved'"
fi
```

If the review comment check prints GATES_FAILED, report the specific blocker and do NOT proceed with the merge.

### Plan Completion Gate

Before merging, scan the plan document for unchecked items that indicate unfinished work:

```bash
SLUG=$(echo "$BRANCH" | sed 's|^session/||')
PLAN_PATH="docs/plans/${SLUG}.md"

# Read plan from origin/main (authoritative copy), not from cwd (which may be a stale worktree)
PLAN_TEXT=$(git show origin/main:${PLAN_PATH} 2>/dev/null) || { echo "WARN: No plan found at origin/main:${PLAN_PATH} -- skipping completion gate"; exit 0; }

echo "$PLAN_TEXT" | python3 -c "
import re, sys, yaml

plan_text = sys.stdin.read()
if not plan_text.strip():
    print('WARN: Empty plan at origin/main:$PLAN_PATH -- skipping completion gate')
    sys.exit(0)

# Parse frontmatter for allow_unchecked override
frontmatter_match = re.match(r'^---\n(.*?)\n---', plan_text, re.DOTALL)
if frontmatter_match:
    try:
        fm = yaml.safe_load(frontmatter_match.group(1))
        if fm and fm.get('allow_unchecked'):
            print('WARN: Plan has allow_unchecked: true -- unchecked items will not block merge')
            sys.exit(0)
    except Exception:
        pass

# Sections to exclude from the scan (not deliverables)
exclude_sections = ['Open Questions', 'Critique Results']

# Split into sections and filter
lines = plan_text.splitlines()
current_section = ''
unchecked = []
for line in lines:
    heading_match = re.match(r'^#{1,3} (.+)', line)
    if heading_match:
        current_section = heading_match.group(1).strip()
    if current_section in exclude_sections:
        continue
    checkbox_match = re.match(r'^[ \t]*- \[ \] (.+)', line)
    if checkbox_match:
        unchecked.append(f'  [{current_section}] {checkbox_match.group(1).strip()}')

if unchecked:
    print(f'COMPLETION GATE FAILED: {len(unchecked)} unchecked plan item(s):')
    for item in unchecked:
        print(item)
    print()
    print('GATES_FAILED')
else:
    print('COMPLETION GATE: All plan items checked.')
    print('GATE_PASS')
"
```

If the completion gate prints GATES_FAILED, report the unchecked items as blockers and do NOT proceed with the merge. The plan must have all checkbox items checked (except those in Open Questions and Critique Results sections) before merge is allowed.

### Lockfile Sync Check

Verify `uv.lock` matches `pyproject.toml` so the merge doesn't leave every
machine with a dirty working tree after `uv sync`. `uv lock --locked` is
read-only — it exits non-zero if a regeneration would produce changes, and
never modifies files:

```bash
if uv lock --locked >/dev/null 2>&1; then
  echo "LOCKFILE: PASS"
else
  echo "LOCKFILE: FAIL — uv.lock is out of sync with pyproject.toml"
  echo "Run 'uv lock' and commit the result before merging."
  echo "GATES_FAILED"
fi
```

If this check prints GATES_FAILED, report it as a blocker and do NOT proceed
with the merge. Fix: `uv lock && git add uv.lock && git commit -m "Sync uv.lock"`.

Also verify the PR is mergeable:

```bash
gh pr view $ARGUMENTS --json title,state,mergeable,headRefName --jq '{title,state,mergeable,branch:.headRefName}'
```

## Decision

### If all gates pass AND PR is mergeable:

1. Create the authorization file so the merge guard hook allows the merge:

```bash
mkdir -p data
touch data/merge_authorized_$ARGUMENTS
```

2. Execute the merge:

```bash
gh pr merge $ARGUMENTS --squash --delete-branch
```

3. Clean up the authorization file:

```bash
rm -f data/merge_authorized_$ARGUMENTS
```

4. Delete the plan file now that the PR is merged:

```bash
BRANCH=$(gh pr view $ARGUMENTS --json headRefName -q .headRefName 2>/dev/null || echo "")
SLUG=$(echo "$BRANCH" | sed 's|^session/||')
PLAN_PATH="docs/plans/${SLUG}.md"

# Read the authoritative plan from origin/main (plans are always committed on main, not session branches)
if git show origin/main:${PLAN_PATH} > /tmp/plan_${SLUG}.md 2>/dev/null; then
  python scripts/migrate_completed_plan.py "/tmp/plan_${SLUG}.md" && echo "Plan migrated: $PLAN_PATH" || echo "WARN: Plan migration failed for $PLAN_PATH — delete manually if needed"
  # Delete the plan from the working tree if it exists
  if [ -f "$PLAN_PATH" ]; then
    rm -f "$PLAN_PATH"
    git add "$PLAN_PATH" 2>/dev/null || true
    echo "Plan file removed: $PLAN_PATH"
  fi
  rm -f "/tmp/plan_${SLUG}.md"
else
  echo "No plan at origin/main:$PLAN_PATH — skipping cleanup"
fi
```

   If plan deletion fails, report a warning but do NOT block success reporting. The plan can always be deleted manually.

5. Report success.

### If any gate fails:

Report which gates failed with the missing details from the gate check output. Do NOT create the authorization file or attempt to merge.

```
## Merge Blocked

The following prerequisites are not yet met:
- [STAGE]: FAIL — [missing detail from gate check]

Complete the missing stages before requesting merge.
```

### If gate check script fails (import error, etc.):

Fall back to manual checks:

```bash
# Check for review
gh pr view $ARGUMENTS --json comments --jq '.comments[] | select(.body | contains("Review:")) | .body[:80]'

# Check tests pass
pytest tests/ -x -q --tb=no 2>&1 | tail -5

# Check docs exist
test -f docs/features/*.md && echo "DOCS: exists" || echo "DOCS: missing"
```

## Important

- The merge guard hook (`validate_merge_guard.py`) blocks `gh pr merge` unless an authorization file exists
- This skill creates the authorization file ONLY after all gates pass
- The authorization file is cleaned up immediately after merge (success or failure)
- If the merge fails, clean up the authorization file anyway: `rm -f data/merge_authorized_$ARGUMENTS`
