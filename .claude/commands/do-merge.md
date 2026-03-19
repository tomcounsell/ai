# Merge Gate

You are the merge gate for the SDLC pipeline. Your job is to programmatically verify all prerequisites are met, then execute the merge.

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

session = AgentSession.get_by_slug('$SLUG')
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

4. Report success.

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
