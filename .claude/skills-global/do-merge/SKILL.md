---
name: do-merge
description: Use when merging a pull request that has cleared the SDLC pipeline. Runs the deterministic verify-then-merge gate — confirms the PR is OPEN, mergeable, CI-green, REVIEW-approved, and links its tracking issue — then authorizes and squash-merges. Triggered by 'merge this PR', 'do-merge', or automatically by /sdlc at the MERGE stage.
---

# Do-Merge (Deterministic Merge Gate)

You perform the **terminal SDLC merge gate**: verify a PR is genuinely finished,
authorize the merge through the merge-guard hook, squash-merge it, and clean up.
This skill is portable — it runs in any repo, not just `~/src/ai`.

The gate is deterministic: every precondition is a checkable fact (PR state, CI
rollup, review verdict, issue link). If any precondition fails, the skill
refuses to merge, surfaces a clear reason, and does NOT create the
authorization file or call the merge command.

## Variables

PR_ARG: the PR number to merge (e.g. `42` or `#42`). Strip any leading `#`.

If PR_ARG is empty, resolve it from the conversation context or the pipeline
state (`sdlc-tool stage-query --issue-number N` → `_meta.pr_number`). If it
still cannot be resolved, STOP and ask the caller for the PR number.

## Step 0: Substrate Probe (degraded-mode awareness)

Before anything else, probe whether the orchestration substrate (PM session +
Redis) is reachable, mirroring the `do-docs` pattern. This lets a forked
sub-skill announce degraded mode instead of silently lagging state:

```bash
sdlc-tool stage-marker --stage MERGE --status in_progress --issue-number {issue_number}
```

Parse the JSON output:
- `{"stage": "MERGE", "status": "in_progress"}` — substrate present, state persisted; proceed normally.
- `{"status": "degraded", ...}` — **announce at the top of your run**: "running in degraded mode (state not persisted)". The merge gate still runs (it depends only on `gh`, not on the substrate), but stage markers will not be recorded. Proceed.
- Non-zero exit — the substrate is present but the write genuinely failed; report the stderr diagnostic and proceed with the gate (do not silently swallow it).

## Step 1: Verify PR State

Read the PR's full state via `gh`:

```bash
gh pr view {PR} --json state,mergeable,mergeStateStatus,statusCheckRollup,body,headRefName
```

ALL of the following must hold, or the gate FAILS:

1. **`state == "OPEN"`** — a merged/closed PR is not mergeable.
2. **`mergeable == "MERGEABLE"`** — GitHub reports no conflicts.
3. **`mergeStateStatus == "CLEAN"`** — no blocking branch-protection state.
   (`BLOCKED`, `BEHIND`, `DIRTY`, `UNSTABLE` all FAIL.)
4. **CI green** — every entry in `statusCheckRollup` has
   `conclusion == "SUCCESS"` (an empty rollup means no required checks — treat
   as pass only if branch protection does not require checks).

If any check fails, STOP: report exactly which precondition failed and its
observed value. Do NOT create the auth file. Do NOT call merge.

> Conflict resolution is explicitly OUT OF SCOPE. The gate verifies
> `mergeable`/`CLEAN` and stops; it never rebases, force-pushes, or resolves
> conflicts.

## Step 2: Verify REVIEW Approved

Read the recorded REVIEW verdict from pipeline state:

```bash
sdlc-tool verdict get --stage REVIEW --issue-number {issue_number}
```

The verdict text must contain `APPROVED` (case-insensitive). A
`CHANGES REQUESTED` / `NEEDS REVISION` verdict, or no verdict at all, FAILS the
gate — route back to `/do-pr-review` or `/do-patch`, do not merge.

In degraded mode (Step 0 reported degraded), the verdict tool may also return
no data. If REVIEW approval cannot be confirmed, FAIL closed — never merge an
unconfirmed-review PR.

## Step 3: Verify Issue Link

The PR body (from Step 1's `body`) must contain a `Closes #{issue_number}` (or
`Closes #N` / `Fixes #N` / `Resolves #N`) line that links the tracking issue,
so the issue auto-closes on merge. If absent, STOP and report — the PR is not
correctly linked to its issue.

## Step 4: Authorize and Merge

Only after Steps 1-3 all pass:

1. **Create the authorization file** the merge-guard hook
   (`.claude/hooks/validators/validate_merge_guard.py`) requires. Without this
   file, the hook blocks the merge command:
   ```bash
   touch data/merge_authorized_{PR}
   ```
2. **Squash-merge** the PR:
   ```bash
   gh pr merge {PR} --squash
   ```
3. **Delete the authorization file** immediately after, success or failure, so
   a stale auth file never lingers:
   ```bash
   rm -f data/merge_authorized_{PR}
   ```

If the merge command itself fails (e.g. a race where branch protection changed
between Step 1 and now), report the failure, ensure the auth file is removed,
and do NOT retry blindly.

## Step 5: Record Completion

Mark the MERGE stage complete (no-op / degraded marker if the substrate is
absent — that is fine):

```bash
sdlc-tool stage-marker --stage MERGE --status completed --issue-number {issue_number}
```

## Step 6: Repo-Specific Addenda

This repo may define additional gate steps (extra lint gates, plan migration,
worktree cleanup, post-merge restarts). Read and follow the repo's addendum if
present:

- **`docs/sdlc/do-merge.md`** — the canonical per-repo addendum. In `~/src/ai`
  it covers ruff gates, the documentation gate, plan migration to
  `docs/plans/completed/`, post-merge memory extraction, worktree cleanup
  (`python scripts/post_merge_cleanup.py {slug}`), and bridge/worker restart.

Apply those steps in addition to the deterministic gate above. The addendum is
additive — it never relaxes the verify-then-merge contract.

## Critical Rules

- **Never bypass the gate.** The auth file is created ONLY after every
  precondition passes. A copy-pasted `touch data/merge_authorized_{PR}` without
  the gate defeats the entire mechanism.
- **Fail closed.** Any unconfirmed precondition (unknown CI state, missing
  review verdict, unresolved mergeability) is a FAIL, not a pass.
- **Clean up the auth file** on every path — success, gate failure after
  creation (should not happen, but defensive), or merge-command error.
- **No conflict resolution.** Out of scope; the gate stops at `mergeable`.

## OUTCOME Contract Emission

As the last line of your final response, emit an OUTCOME contract:

- **Merged**: `<!-- OUTCOME {"status":"success","stage":"MERGE","artifacts":{"pr_url":"<URL>"}} -->`
- **Gate refused / merge failed**: `<!-- OUTCOME {"status":"fail","stage":"MERGE","artifacts":{}} -->`
