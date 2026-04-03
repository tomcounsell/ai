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
    session = AgentSession.get_by_slug('$SLUG')
    if session:
        sm = PipelineStateMachine(session)
        states = sm.get_display_progress(slug='$SLUG')
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
if skipped:
    print()
    names = ', '.join(skipped)
    print(f'⚠ These stages appear to have been skipped: {names}')
    print('Are you sure you want to merge without completing them?')
else:
    print()
    print('All stages completed. Good to merge.')
"
```

If stages were skipped, pause and confirm with the user before proceeding. The user may have good reasons — respect their judgment — but make sure they saw what was skipped.

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
states = sm.get_display_progress(slug='$SLUG')
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

### Plan Completion Gate

Before merging, scan the plan document for unchecked items that indicate unfinished work:

```bash
SLUG=$(echo "$BRANCH" | sed 's|^session/||')
PLAN_PATH="docs/plans/${SLUG}.md"

python3 -c "
import re, sys, yaml
from pathlib import Path

plan_path = Path('$PLAN_PATH')
if not plan_path.exists():
    print('WARN: No plan found at $PLAN_PATH -- skipping completion gate')
    sys.exit(0)

plan_text = plan_path.read_text()

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
