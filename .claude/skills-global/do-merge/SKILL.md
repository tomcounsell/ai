---
name: do-merge
description: "Use when merging a pull request that has cleared the SDLC pipeline. Triggered by 'merge this PR', 'do-merge', or automatically by /sdlc at the MERGE stage."
---

# Do-Merge (Deterministic Merge Gate)

You perform the **terminal SDLC merge gate**: verify a PR is genuinely finished,
authorize the merge through the merge-guard hook, squash-merge it, and clean up.
This skill is portable — it runs in any repo, not just `~/src/ai`.

The gate is deterministic: every precondition is a checkable fact (PR state, CI
rollup, review verdict, issue link). If any precondition fails, the skill
refuses to merge, surfaces a clear reason, and does NOT call the merge command.

## Repo Context Probe

If `docs/sdlc/do-merge.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

This addendum is where a repo layers SDLC automation onto the generic `git`/`gh`
gate: a stage/verdict substrate (stage markers, recorded REVIEW verdicts), a
merge-authorization hook, extra deterministic gates (lint, lockfile, full
suite), plan migration, and post-merge cleanup/restart. When the file is absent
(the common case in a foreign repo), this skill runs entirely on `git` and `gh`
— no repo-specific tooling required.

## Variables

PR_ARG: the PR number to merge (e.g. `42` or `#42`). Strip any leading `#`.

If PR_ARG is empty, resolve it from the conversation context. If the repo-context
file declares a pipeline-state tool, use it to recover the PR number. If it still
cannot be resolved, STOP and ask the caller for the PR number.

## Dependabot Exemption

Before Step 0, detect whether this PR qualifies for the dependabot fast-path.
Include `author` and `labels` in the Step 1 `gh pr view` call:

```bash
gh pr view {PR} --json author,labels,state,mergeable,mergeStateStatus,statusCheckRollup,body,headRefName
```

A PR is a **dependabot PR** when ALL of:
- `author.is_bot == true` AND `author.login` matches `app/dependabot` or `dependabot`
- At least one label has `name == "dependencies"`

If both conditions hold, apply the exemption path:
- **Skip Step 0** (no tracking issue — no substrate marker).
- **Skip Step 2** (no SDLC pipeline — no REVIEW verdict required).
- **Skip Step 3** (no tracking issue — no `Closes #N` link required).
- **Skip Step 5** (no issue number to record completion against).
- **Skip Step 6** addenda that reference `{issue_number}` (plan migration, post-merge scripts).
- Still **run the mergeability/CI checks in Step 1** and **Step 4** (authorize + squash-merge).

> **mergeable "UNKNOWN" handling (dependabot only):** GitHub computes mergeability
> asynchronously. If `mergeable == "UNKNOWN"`, wait 8 seconds, re-fetch once,
> and check again. If still `"UNKNOWN"` after the retry, FAIL the gate and ask
> the user to try again shortly. Never treat `"UNKNOWN"` as a pass.

Announce the exemption at the top of your run:
> "Dependabot PR detected — skipping pipeline steps (REVIEW verdict, issue link). Running mergeability and CI gate only."

## Step 0: Stage Marker (only if the context file declares a substrate)

If the repo-context file declares a stage-marker substrate, write an
`in_progress` marker for the MERGE stage now, following its exact invocation and
degraded-mode handling. This lets a forked sub-skill announce degraded mode
instead of silently lagging state. The gate itself depends only on `gh`, never
on the substrate, so a missing or degraded substrate never blocks the merge.

If no substrate is declared (the generic case), skip this step.

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

## Step 2: Verify Review Approved

Confirm the PR has an approving review. **Generic baseline** — read GitHub's
native review decision:

```bash
gh pr view {PR} --json reviewDecision
```

`reviewDecision == "APPROVED"` passes. `CHANGES_REQUESTED`, `REVIEW_REQUIRED`,
or an empty decision (no review) FAILS the gate — route back to review/patch, do
not merge.

If the repo-context file declares a recorded-verdict substrate (e.g. an SDLC
REVIEW verdict), use it as the authority instead, following its exact
invocation. The verdict text must contain `APPROVED` (case-insensitive); any
other value or no verdict FAILS.

Whichever source is used: if review approval cannot be confirmed, FAIL closed —
never merge an unconfirmed-review PR.

## Step 3: Verify Issue Link

The PR body (from Step 1's `body`) must contain a `Closes #{issue_number}` (or
`Closes #N` / `Fixes #N` / `Resolves #N`) line that links the tracking issue,
so the issue auto-closes on merge. If absent, STOP and report — the PR is not
correctly linked to its issue.

## Step 4: Authorize and Merge

Only after Steps 1-3 all pass:

1. **Satisfy any merge-authorization guard the repo-context file declares.** If
   the repo gates `gh pr merge` behind a merge-guard hook that requires an
   authorization file, create it now exactly as the context file specifies, and
   delete it immediately after the merge (success or failure) so a stale auth
   file never lingers. In the generic case there is no guard — skip straight to
   the merge.
2. **Squash-merge** the PR:
   ```bash
   gh pr merge {PR} --squash
   ```
3. **Clean up** any authorization file created in sub-step 1, on every path.

If the merge command itself fails (e.g. a race where branch protection changed
between Step 1 and now), report the failure, ensure any auth file is removed,
and do NOT retry blindly.

## Step 5: Record Completion

If the repo-context file declares a stage-marker substrate, mark the MERGE stage
`completed` now (no-op / degraded marker if the substrate is absent is fine).
Otherwise skip — the merge itself is the completion signal.

## Step 6: Apply Repo-Specific Addenda

If the repo-context file (read in the Repo Context Probe) declares additional
gate steps — extra lint gates, lockfile sync, full-suite runs, documentation
gates, plan migration, worktree cleanup, post-merge restarts — apply them now,
in addition to the deterministic gate above. The addendum is additive — it never
relaxes the verify-then-merge contract. In the generic case there is no addendum
and the merge is already complete.

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
