# Merge Gate

## Repo Addendum

Before starting, check if `docs/sdlc/do-merge.md` exists in the current repo. If it does, read it and incorporate its guidance as repo-specific addenda to these instructions.

You are the merge gate for the SDLC pipeline. Your job is to programmatically verify all prerequisites are met, then execute the merge.

## Pre-Merge Pipeline Check

Before running gate checks, take a moment to review what the pipeline actually completed. Run this and share the output with the user:

```bash
BRANCH=$(gh pr view $ARGUMENTS --json headRefName -q .headRefName)
SLUG=$(echo "$BRANCH" | sed 's|^session/||')

python3 -c "
from bridge.pipeline_graph import DISPLAY_STAGES

# Try programmatic state machine first
derived = False
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
    session = None

# Fallback: derive from durable signals when Redis is cold (empty or all-pending).
# This runs only when the primary path returned nothing useful -- it NEVER
# overrides a populated state machine. See agent/pipeline_state.py for the
# signal list and failure semantics.
if session is not None and (not states or all(v in ('pending', 'ready') for v in states.values())):
    try:
        fallback = PipelineStateMachine.derive_from_durable_signals(session)
        if fallback:
            states = fallback
            derived = True
    except Exception:
        pass

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
if derived:
    print('INFO: Redis state cold -- derived from durable signals.')
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

Before authorizing merge, scan PR issue comments for the most recent `## Review:` comment.
Stale reviews are filtered by comparing each comment's `created_at` against the PR's latest
commit `committer.date` — comments that predate the latest commit are treated as stale (a
force-push would have superseded them) and are dropped from consideration.

```bash
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)

# Read the latest commit's committer date for commit-SHA-aware filtering.
# On transient API failure we fail the gate with a specific diagnostic rather
# than silently regressing to unfiltered behavior -- a silent fallback would
# defeat the exact class of bug this filter prevents (stale Approved after
# force-push).
LATEST_COMMIT_DATE=$(gh api repos/$REPO/pulls/$ARGUMENTS/commits --jq '.[-1].commit.committer.date' 2>/dev/null)
if [ -z "$LATEST_COMMIT_DATE" ]; then
  echo "REVIEW_COMMENT: FAIL — could not fetch latest commit date for review filter"
  echo "Diagnose: gh api repos/$REPO/pulls/$ARGUMENTS/commits --jq '.[-1]'"
  echo "GATES_FAILED"
  exit 1
fi

LAST_REVIEW=$(gh api repos/$REPO/issues/$ARGUMENTS/comments \
  --jq "[.[] | select(.body | startswith(\"## Review:\")) | select(.created_at >= \"$LATEST_COMMIT_DATE\")] | last | .body // \"\"" \
  2>/dev/null) || { echo "REVIEW_COMMENT: FAIL — gh api call failed (network/auth error)"; echo "GATES_FAILED"; exit 1; }

if [ -z "$LAST_REVIEW" ]; then
  echo "REVIEW_COMMENT: FAIL — No current '## Review:' comment found on PR #$ARGUMENTS"
  echo "(Comments older than the latest commit at $LATEST_COMMIT_DATE were filtered as stale.)"
  echo "Run /do-pr-review before merging."
  echo "GATES_FAILED"
elif echo "$LAST_REVIEW" | grep -q "^## Review: Changes Requested"; then
  BLOCKERS=$(echo "$LAST_REVIEW" | grep "^- \[ \]" | head -20)
  echo "REVIEW_COMMENT: FAIL — Most recent review is 'Changes Requested'"
  echo "Unchecked blockers:"
  echo "$BLOCKERS"
  echo "GATES_FAILED"
else
  echo "REVIEW_COMMENT: PASS — Most recent review is 'Approved' (post-latest-commit)"
fi
```

If the review comment check prints GATES_FAILED, report the specific blocker and do NOT proceed with the merge.

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


### Full Suite Gate

<!--
    Gate logic lives in scripts/baseline_gate.py. See
    docs/features/merge-gate-baseline.md for the full contract. The markdown
    here only orchestrates: run pytest -> junitxml -> invoke the gate script
    -> read its verdict. Every line of comparison logic is reachable from
    tests/unit/test_do_merge_baseline.py via direct function import.
-->

After the Lockfile Sync Check, run a full test suite gate to ensure the PR branch does not introduce new regressions. PR failures are compared against a categorised baseline (`real`, `flaky`, `hung`, `import_error`) -- new `real`/`hung`/`import_error` failures block, new `flaky`-category re-occurrences are reported but non-blocking.

```bash
# Run the full suite on the PR branch (already checked out) and emit junitxml.
# No -p pytest_timeout flag here: the merge gate does not classify hangs per
# test -- that is the refresh tool's job on main. do-test's existing retry
# infrastructure handles flaky PR-branch failures before we reach this gate.
rm -f /tmp/pr_run.xml
pytest tests/ -q --tb=no --junitxml=/tmp/pr_run.xml 2>&1 | tee /tmp/pytest_output.txt
PYTEST_EXIT=$?

BASELINE_FILE="data/main_test_baseline.json"

if [ $PYTEST_EXIT -eq 0 ]; then
    echo "FULL_SUITE: PASS"
    mkdir -p data
    python3 -c "
import json
from datetime import UTC, datetime
from pathlib import Path
import subprocess
sha = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], text=True).strip()
Path('data/main_test_baseline.json').write_text(json.dumps({
    'schema_version': 2,
    'generated_at': datetime.now(UTC).isoformat(),
    'generated_by': 'do-merge.md post-merge reset',
    'runs': 0,
    'commit': sha,
    'tests': {},
}, indent=2, sort_keys=True) + '\n')
"
elif [ ! -f "$BASELINE_FILE" ]; then
    # Bootstrap path: no baseline present and PR has failures.
    # Write every PR failure as a `real` entry plus `bootstrap: true` so the
    # staleness warning always fires until a real refresh runs.
    echo "FULL_SUITE: BOOTSTRAP — no baseline exists; recording PR failures as pre-existing."
    mkdir -p data
    python3 -c "
import json
import subprocess, sys
from datetime import UTC, datetime
from pathlib import Path
sys.path.insert(0, '.')
from scripts._baseline_common import parse_junitxml, failing_node_ids
failing = sorted(failing_node_ids(parse_junitxml('/tmp/pr_run.xml')))
sha = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], text=True).strip()
Path('data/main_test_baseline.json').write_text(json.dumps({
    'schema_version': 2,
    'generated_at': datetime.now(UTC).isoformat(),
    'generated_by': 'do-merge.md bootstrap',
    'runs': 1,
    'commit': sha,
    'bootstrap': True,
    'tests': {n: {'category': 'real', 'fail_rate': 1.0, 'hung_count': 0} for n in failing},
}, indent=2, sort_keys=True) + '\n')
print(f'Bootstrap baseline written with {len(failing)} pre-existing failures (bootstrap=true).')
"
    echo "FULL_SUITE: PASS (bootstrap; run python scripts/refresh_test_baseline.py on main soon)"
else
    # Delegate to scripts/baseline_gate.py for categorised comparison.
    # Exit 0 when no new blocking regressions. Exit 1 otherwise.
    # Prints a JSON verdict to stdout plus any staleness warning to stderr.
    # Capture $? on the SAME line as the assignment: otherwise an
    # intermediate command (e.g. a debug `echo`) would clobber $? and this
    # gate would silently read the wrong exit code.
    GATE_OUTPUT=$(python -m scripts.baseline_gate --pr-junitxml /tmp/pr_run.xml --baseline "$BASELINE_FILE" 2> /tmp/baseline_gate_stderr.txt); GATE_EXIT=$?
    if [ -s /tmp/baseline_gate_stderr.txt ]; then
        cat /tmp/baseline_gate_stderr.txt
    fi
    if [ $GATE_EXIT -eq 0 ]; then
        PREEXISTING=$(echo "$GATE_OUTPUT" | python3 -c "import json, sys; print(json.load(sys.stdin)['preexisting_failures_present'])")
        FLAKY_COUNT=$(echo "$GATE_OUTPUT" | python3 -c "import json, sys; print(len(json.load(sys.stdin)['new_flaky_occurrences']))")
        echo "FULL_SUITE: PASS (pre-existing=$PREEXISTING, flaky re-occurrences=$FLAKY_COUNT -- all non-blocking)"
        # Baseline decay + quarantine hint emission (item 4 of sdlc-1155).
        # Invokes the helpers added in scripts/baseline_gate.py so the gate
        # can age out stale `real` entries and flag repeat flakes without a
        # separate pass.
        python3 scripts/_baseline_post_merge_update.py "$BASELINE_FILE" "$GATE_OUTPUT" /tmp/pr_run.xml || true
    else
        echo "FULL_SUITE: FAIL — new regression(s) not in baseline:"
        echo "$GATE_OUTPUT" | python3 -c "import json, sys; [print(' -', n) for n in json.load(sys.stdin)['new_blocking_regressions'][:20]]"
        echo "GATES_FAILED"
    fi
fi
```

**Red-main recovery path:** If `data/main_test_baseline.json` does not exist and tests fail, write the current failure list as a bootstrap schema-v2 baseline (`bootstrap: true`). This allows the first merge after a red-main period to proceed, establishing the baseline for future comparisons. The `bootstrap: true` flag makes the staleness warning fire on every subsequent gate invocation until `python scripts/refresh_test_baseline.py` writes a properly categorised baseline.

**After a clean merge:** Update `data/main_test_baseline.json` to a schema-v2 shape with an empty `tests` map so future PRs are held to a fully green standard.

**Categories:** The baseline is keyed by test node ID with a `category` field. Categories: `real` (deterministic failure on main), `flaky` (1-99% fail rate across N baseline runs), `hung` (pytest-timeout fired, delegated via `pytest-timeout` on refresh), `import_error` (collection failure). Only the refresh tool (`scripts/refresh_test_baseline.py`) writes categorised baselines. See `docs/features/merge-gate-baseline.md`.

**Backwards compat:** The schema-v1 `{"failing_tests": [...]}` flat shape is promoted to schema v2 in memory (every entry becomes `category="real"`). No file write happens from the merge gate itself; only the refresh tool upgrades the on-disk format.

**Note:** The full suite collects all failures into `/tmp/pr_run.xml` before comparison. Using `-x` (fail-fast) would stop after the first pre-existing failure and hide new regressions.

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
