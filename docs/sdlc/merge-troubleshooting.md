# Merge Troubleshooting Playbook

When the merge gate (`/do-merge`) fails on a PR that is otherwise approved,
mergeable, and green, the PM session can self-resolve the blocker using the
recipes below. Each section follows the house style of other `docs/sdlc/`
pages: terse, command-first, with explicit verify-then-proceed hooks. The
G4 oscillation guard (`.claude/skills-global/do-sdlc/SKILL.md`) caps same-category
retries at 3; anything beyond that escalates to a human.

See also:
- `.claude/commands/do-merge.md` — the gate script itself.
- `config/personas/engineer.md` → **Gate-Recovery Behavior** — the
  engineer persona's dispatch table mapping blockers to remediations.
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
a fourth time without a state change (`.claude/skills-global/do-sdlc/SKILL.md`). The
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
  --role eng --model opus \
  --slug {slug} --parent "$AGENT_SESSION_ID" \
  --message "Stage: REVIEW / Required skill: /do-pr-review / PR: {pr_url} / ..."
```

**Verify.** Re-run the diagnose command; the newest `## Review:` comment
should have `created_at > $LATEST`. Re-dispatch `/do-merge {pr}`.

---

## Verification Outcomes Hold the PR

**Symptom.** `/do-merge` refuses with one of:

- `verification row '<name>' is UNEVALUATED` (or `is FAIL`)
- `verification outcomes: verification outcome predates PR head commit`
- `verification outcomes: no usable head_sha on the recorded aggregate`
- `verification outcomes: recorded aggregate unreadable (...)`

**Cause.** The merge predicate reads the aggregate the verification runner
recorded for this lane and refuses on a blocking row or on one it cannot show
is fresh. `FAIL` and `UNEVALUATED` both hold the PR (owner ruling, `ba092a06d`):
`FAIL` says the code is wrong, `UNEVALUATED` says the grader could not answer,
and the second is usually a plan-authoring problem, not a code problem.

**Diagnose.** Run the table and read the report:

```bash
python scripts/validate_build.py "$PLAN_PATH"
```

**Fix.** Depends on which line you got:

- **A `FAIL` row** — real finding. Route to `/do-patch`, not around the gate.
- **An `UNEVALUATED` row** — fix the *row*. It names its own reason: an
  unrecognised expectation form, an empty `Expected` cell, a `Command` cell
  with no backticked span, or a timeout. For a timeout on a genuinely slow but
  legitimate suite, re-run on a quiet machine first; raise the bound only if it
  is really too low (`--timeout N`, or `VERIFICATION_TIMEOUT_S`). Contention is
  a load problem, not a bound problem.

  Mind where the edit lands. Plan files live on `main` and never travel in a
  feature-branch PR (`docs/sdlc/do-docs.md`), but `/do-pr-review` grades
  `"$PLAN_PATH"` **in the lane worktree**. So an `Expected`-cell fix committed
  to `main` keeps grading the old cell until you bring `main` into the branch.
  Merge or rebase first, then re-run the table — otherwise the row you just
  fixed refuses the merge again and reads as though the fix did not work.
- **A stale, unanchored, or unreadable aggregate** — nothing is wrong with the
  code; the record just cannot be trusted at this head. Re-record it:

```bash
python scripts/validate_build.py "$PLAN_PATH" --record-outcomes \
  --repo "$TARGET_REPO" --issue "$ISSUE_NUMBER" --pr "$PR_NUMBER"
```

Quote every variable: an empty unquoted one collapses the argument list and the
run refuses to record rather than writing under a garbage key.

**Verify.** Re-dispatch `/do-merge {pr}`. Note that the router reaches this
state on its own too — dispatch row 8g sends a lane with a blocking or unfresh
aggregate back to `/do-pr-review`, which re-runs § 4.5 and re-records, rather
than looping on a merge the predicate will refuse.

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

## Worktree Cleanup Blocked

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
| MERGE_CONFLICT | Rebase onto `origin/main` | See Merge Conflict |
| BUSY_GUARD (`post_merge_cleanup` exit 2) | Kill wedged session, re-run cleanup | See Worktree Cleanup Blocked |

After any remediation, re-dispatch `/do-merge {pr}`. If the same blocker
category recurs 3 times, escalate to the human per the G4 convergence
rule — do not loop further.

