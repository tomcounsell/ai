# Merge Troubleshooting Playbook

When the merge gate (`/do-merge`) fails on a PR that is otherwise approved,
mergeable, and green, the PM session can self-resolve the blocker using the
recipes below. Each section follows the house style of other `docs/sdlc/`
pages: terse, command-first, with explicit verify-then-proceed hooks. The
G4 oscillation guard (`.claude/skills/sdlc/SKILL.md`) caps same-category
retries at 3; anything beyond that escalates to a human.

See also:
- `.claude/commands/do-merge.md` — the gate script itself.
- `config/personas/project-manager.md` → **Gate-Recovery Behavior** — the
  PM persona's dispatch table mapping blockers to remediations.
- `docs/features/self-healing-merge-gate.md` — feature-level overview.

---

## Merge Conflict

**Symptom.** `/do-merge` reports `mergeable: CONFLICTING` from the
`gh pr view` check. The PR's branch cannot fast-forward onto the base.

**Diagnose.**

```bash
gh pr view {pr} --json mergeable,mergeStateStatus
git -C .worktrees/{slug} fetch origin main
git -C .worktrees/{slug} log --oneline ^origin/main..HEAD
```

**Remediate.** Rebase the session branch onto origin/main and re-push:

```bash
git -C .worktrees/{slug} fetch origin main
git -C .worktrees/{slug} rebase origin/main
git -C .worktrees/{slug} push --force-with-lease
```

**Verify.**

```bash
gh pr view {pr} --json mergeable -q .mergeable
# Expected: MERGEABLE
```

Then re-dispatch `/do-merge {pr}`.

---

## Unchecked Plan Checkboxes

**Symptom.** The Plan Completion Gate prints `COMPLETION GATE FAILED: N
unchecked plan item(s)` and lists the offending items.

**Diagnose.**

```bash
BRANCH=$(gh pr view {pr} --json headRefName -q .headRefName)
SLUG=$(echo "$BRANCH" | sed 's|^session/||')
git show "origin/$BRANCH:docs/plans/${SLUG}.md" | grep -n '^\s*- \[ \]'
```

**Remediate.** Either complete the outstanding work (preferred) or tick the
boxes in a commit that actually delivers the referenced functionality. The
plan doc is the contract; do not tick unfinished items. Never set
`allow_unchecked: true` on the plan frontmatter — that flag is a human
escape hatch and is out of bounds for autonomous PM self-resolution.

**Verify.**

```bash
git show "origin/$BRANCH:docs/plans/${SLUG}.md" | grep -c '^\s*- \[ \]'
# Expected: 0 (or only items in Open Questions / Critique Results sections)
```

Re-dispatch `/do-merge {pr}`.

---

## G4 Oscillation (Same Skill Dispatched 3x)

**Symptom.** The SDLC router's G4 guard refuses to dispatch the same skill
a fourth time without a state change (`.claude/skills/sdlc/SKILL.md`). The
PM is looping on the same remediation without making progress.

**Diagnose.** Look at the last three dispatches for this issue:

```bash
python -m tools.sdlc_stage_query --session-id "$AGENT_SESSION_ID"
```

**Remediate.** Do NOT re-dispatch the same skill. Escalate to the human
with the specific blocker output. G4 is load-bearing — bypassing it
produces infinite loops that drain compute without finishing work.

**Verify.** N/A — this is the escalation path.

---

## Stale Review (Approved/Changes-Requested Predates Latest Commit)

**Symptom.** The Structured Review Comment Check reports
`REVIEW_COMMENT: FAIL -- No current '## Review:' comment found` even
though the PR page in the GitHub UI shows a visible review. The
commit-SHA filter is correctly dropping a stale review.

**Diagnose.**

```bash
REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)
LATEST=$(gh api repos/$REPO/pulls/{pr}/commits --jq '.[-1].commit.committer.date')
echo "Latest commit date: $LATEST"
gh api repos/$REPO/issues/{pr}/comments \
  --jq '.[] | select(.body | startswith("## Review:")) | {created_at, body: (.body | split("\n")[0])}'
```

**Remediate.** Re-run the PR review so a new `## Review:` comment lands
after the latest commit's committer date:

```bash
python -m tools.valor_session create \
  --role dev --model opus \
  --slug {slug} --parent "$AGENT_SESSION_ID" \
  --message "Stage: REVIEW / Required skill: /do-pr-review / PR: {pr_url} / ..."
```

**Verify.** Re-run the diagnose command; the newest `## Review:` comment
should have `created_at > $LATEST`. Re-dispatch `/do-merge {pr}`.

---

## Lockfile Drift

**Symptom.** The Lockfile Sync Check reports
`LOCKFILE: FAIL -- uv.lock is out of sync with pyproject.toml`.

**Diagnose.**

```bash
uv lock --locked
# Exits non-zero with a diff summary when drift exists.
```

**Remediate.** Regenerate the lockfile on the session branch and commit:

```bash
git -C .worktrees/{slug} checkout session/{slug}
uv lock
git -C .worktrees/{slug} add uv.lock
git -C .worktrees/{slug} commit -m "Sync uv.lock"
git -C .worktrees/{slug} push
```

**Verify.**

```bash
uv lock --locked && echo "LOCKFILE OK"
```

Re-dispatch `/do-merge {pr}`.

---

## Flake False Regression

**Symptom.** The Full Suite Gate reports
`FULL_SUITE: PASS (pre-existing=X, flaky re-occurrences=Y -- all non-blocking)`
but the log also shows a QUARANTINE_HINT for a specific test. No blocker,
but a signal that a test is flaking across merges.

**Diagnose.** Check the hint text and the `_flake_tracker` field of
`data/main_test_baseline.json` for the consecutive-run counter:

```bash
jq '._flake_tracker // {}' data/main_test_baseline.json
```

**Remediate.** Mark the test with `@pytest.mark.flaky` or file an issue —
this is the long-term fix. Short-term, the gate does NOT block on the
hint, so the PR proceeds; the hint is advisory only.

**Verify.** Re-run the full suite and confirm the test either passes
deterministically or the counter resets:

```bash
pytest tests/path/to/test.py -x -q
```

No re-dispatch of `/do-merge` needed — the gate already passed.

---

## Partial Pipeline State

**Symptom.** `python -m tools.sdlc_stage_query --session-id
"$AGENT_SESSION_ID"` returns some stages but not all; `/do-merge` reports
a mix of `pending` and `completed`. This is the "mid-session Redis
eviction" case — the primary state machine has partial history.

**Diagnose.**

```bash
python -m tools.sdlc_stage_query --issue-number {N}
# Read the PR branch's durable artifacts to confirm the missing stages
# actually produced output:
BRANCH=$(gh pr view {pr} --json headRefName -q .headRefName)
git fetch origin "$BRANCH" --quiet
git show "origin/$BRANCH:docs/plans/{slug}.md" | head -5       # PLAN present?
gh pr view {pr} --json statusCheckRollup                       # TEST status?
gh pr view {pr} --json reviews                                 # REVIEW entry?
gh pr diff {pr} --name-only | grep ^docs/                      # DOCS diff?
```

**Remediate.** Re-dispatch `/do-merge {pr}` and trust the durable-signal
fallback in `PipelineStateMachine.derive_from_durable_signals()` (see
`docs/features/pipeline-state-machine.md`) to fill in the missing
stages. The fallback only activates when the primary path is empty; it
does not override valid Redis state.

**Verify.**

```bash
# After re-dispatching /do-merge, the Pre-Merge Pipeline Check should print
# "INFO: Redis state cold -- derived from durable signals." followed by
# a pipeline line where all stages show as completed.
```

If the durable-signal fallback also shows `pending` for a stage, that
stage's artifacts genuinely do not exist — dispatch the appropriate
remediation skill (for example, `/do-docs` for DOCS pending, `/do-test`
for TEST pending).

---

## Quick Reference

| Blocker category | Remediation | Command |
|------------------|-------------|---------|
| PIPELINE_STATE | Re-dispatch `/do-merge` (trusts durable fallback) | `/do-merge {pr}` |
| PARTIAL_PIPELINE_STATE | Same as PIPELINE_STATE | `/do-merge {pr}` |
| REVIEW_COMMENT | Dispatch `/do-pr-review` on session branch | See Stale Review |
| COMPLETION_GATE | Finish work; tick plan checkboxes | See Unchecked Plan Checkboxes |
| LOCKFILE | `uv lock && git add uv.lock && commit && push` | See Lockfile Drift |
| FULL_SUITE | Investigate new blocking regression | `pytest tests/{node_id}` |
| MERGE_CONFLICT | Rebase onto `origin/main` | See Merge Conflict |

After any remediation, re-dispatch `/do-merge {pr}`. If the same blocker
category recurs 3 times, escalate to the human per the G4 convergence
rule — do not loop further.
