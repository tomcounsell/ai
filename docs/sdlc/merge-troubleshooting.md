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

## Worktree Cleanup Blocked (issue #1357)

**Symptom.** `python scripts/post_merge_cleanup.py {slug}` exits **2** and
prints `worktree busy: in use by session_id=<id>` to stderr. The local
`session/{slug}` branch is still present because `gh pr merge --delete-branch`
cannot delete a branch referenced by an active worktree.

**Diagnose.** The exit code is the signal — exit 2 means the busy guard
fired (distinct from exit 1 generic errors). Inspect the offending session:

```bash
python -m tools.valor_session status --id <session_id>
```

**Remediate.**

1. If the session is genuinely live and still doing useful work, wait for it to
   finish, then re-run `post_merge_cleanup.py {slug}`.
2. If the session is wedged or dead but its row hasn't flipped yet:

   ```bash
   python -m tools.valor_session kill --id <session_id>
   python scripts/post_merge_cleanup.py {slug}
   ```

3. If the cleanup must proceed despite a live session, override programmatically
   by passing `force=True` to `remove_worktree()` (no CLI flag — this is
   deliberate friction). The WARNING log
   `force-removing worktree .worktrees/{slug} despite live session_id=...` is
   grep-able for audit. **Do not make `--force` your reflex.**

**Verify.**

```bash
python scripts/post_merge_cleanup.py {slug}
echo "Exit: $?"  # 0 == clean; 2 == still blocked
```

See [`docs/sdlc/do-merge.md#busy-guard-issue-1357`](do-merge.md#busy-guard-issue-1357) for the full operator workflow and
[`docs/features/session-isolation.md#worktree-busy-guard-issue-1357`](../features/session-isolation.md#worktree-busy-guard-issue-1357) for the runtime invariant.

---

## Quick Reference

| Blocker category | Remediation | Command |
|------------------|-------------|---------|
| PIPELINE_STATE | Re-dispatch `/do-merge` (trusts durable fallback) | `/do-merge {pr}` |
| PARTIAL_PIPELINE_STATE | Same as PIPELINE_STATE | `/do-merge {pr}` |
| REVIEW_COMMENT | Dispatch `/do-pr-review` on session branch | See Stale Review |
| LOCKFILE | `uv lock && git add uv.lock && commit && push` | See Lockfile Drift |
| FULL_SUITE | Investigate new blocking regression | `pytest tests/{node_id}` |
| MERGE_CONFLICT | Rebase onto `origin/main` | See Merge Conflict |
| BUSY_GUARD (`post_merge_cleanup` exit 2) | Kill wedged session, re-run cleanup | See Worktree Cleanup Blocked |

After any remediation, re-dispatch `/do-merge {pr}`. If the same blocker
category recurs 3 times, escalate to the human per the G4 convergence
rule — do not loop further.

---

## Why was my PR classified as `mixed`?

The shape classifier (`scripts/pr_shape_classify.py`) routes each PR
through a gate set proportional to its blast radius. When >=50% of the
changed files match a safe-shape allowlist (`docs-only`, `lockfile-only`,
`small-patch`) AND >=1 file violates it, the PR is classified as `mixed`
and bumped back to the full gate stack with a logged disqualifier list.

### Find the disqualifiers

The merge gate emits the disqualifier list to stderr in a deterministic
shape so you can grep it:

```bash
# In the /do-merge output, look for:
SHAPE: mixed -- claimed safe shape '<X>' touched non-allowlisted paths: ['path/a', 'path/b']
```

Re-run the classifier locally to inspect the verdict directly:

```bash
python -m scripts.pr_shape_classify --pr {N} | python3 -m json.tool
```

The output's `claimed_shape` and `disqualifiers` fields tell you which
safe shape the classifier considered and which files broke the claim.

### Common cases

| Diff signature                                 | Result | Why                                                                  |
|------------------------------------------------|--------|----------------------------------------------------------------------|
| 1 doc + 1 py                                   | mixed  | docs-only claim is exactly 50% (>= threshold); the py is the disqualifier |
| 1 doc + 5 py                                   | feature| docs-only claim is 17%; too thin to call the PR a "claimed safe shape" |
| `uv.lock` + `pyproject.toml`                   | mixed  | lockfile-only claim is 50%; `pyproject.toml` can swap a runtime dep  |
| 1 new `.py` file only (no docs)                | feature| no safe shape matches >=50% with any disqualifiers                   |

The 50% threshold is documented in
`scripts/pr_shape_classify.py::detect_mixed` and unit-tested in
`tests/unit/test_pr_shape_classify.py`. It exists to prevent two failure
modes: a single-file Python change being labelled "claimed docs-only"
just because the file isn't a doc, and a single doc edit attached to a
50-file refactor looking like a "claimed docs-only" PR.

### Resolving a mixed classification

There is nothing to "fix" -- `mixed` PRs run the full gate stack
(unchanged behavior). If the routing was correct, no action is needed.
If you genuinely intended a safe shape and the disqualifier was an
accidental include (e.g. a `__pycache__` slip), drop the offending file
from the diff and the next push will reclassify.

See [`docs/features/pr-shape-aware-merge-gates.md`](../features/pr-shape-aware-merge-gates.md)
for the full shape taxonomy and defect-detection contract.
