---
name: do-merge
description: "Use when merging a pull request that has cleared the SDLC pipeline. Triggered by 'merge this PR', 'do-merge', or automatically by /sdlc at the MERGE stage."
effort: medium
---

# Do-Merge (Deterministic Merge Gate)

The **terminal SDLC merge gate**: verify a PR is genuinely finished,
squash-merge it, and clean up. Merging is irreversible and outward-facing.

The gate is deterministic: every precondition is a checkable fact (PR state, CI
rollup, review verdict, issue link). If any precondition fails, refuse, report
exactly which one and its observed value, and do NOT call the merge command. Do
not argue past a refusal.

**Done when:** the PR is merged and every addendum step has run and been
reported, or the gate refused with its reason; either way the OUTCOME block is
the last line.

## Repo Context Probe

If `docs/sdlc/do-merge.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The addendum layers a repo's automation onto this generic `git`/`gh` gate: a
stage/verdict substrate, a merge-authorization hook, extra deterministic gates,
plan migration, and post-merge cleanup and restarts. Without it, the skill runs
on `git` and `gh` alone.

If the addendum declares a **shared deterministic merge predicate** (one command
that evaluates the whole gate and returns pass/fail legs), run it and honor its
result instead of hand-assembling Steps 1-3: it is the same predicate the repo's
merge-guard hook enforces, so evaluating anything else invites drift.

## Variables

PR_ARG: the PR number to merge (e.g. `42` or `#42`). Strip any leading `#`.

If PR_ARG is empty, resolve it from the conversation context. If the repo-context
file declares a pipeline-state tool, use it to recover the PR number. If it still
cannot be resolved, STOP and ask the caller for the PR number.

## PRs That Did Not Originate in the Pipeline

A dependabot bump, a hand-authored bug fix, and a follow-up filed from another
PR's review findings all reach this gate without a plan document behind them.
They run **every step below, unchanged**: an issue link, a real review, and a
recorded completion. There is no exemption path, because an exempted PR is a PR
that merged without anyone confirming it was ready.

What such a PR needs instead is a way to say, truthfully, that the planning
stages never ran. If the repo-context file declares a stage substrate, it names
the command for recording a stage as *not applicable*; run it before the review
step so the substrate's ordering checks have an honest answer instead of a
missing one. Absent a substrate there is nothing to record and nothing to do.

Detect the dependabot shape by including `author` and `labels` in the Step 1
`gh pr view` call — `author.is_bot == true` with `author.login` matching
`app/dependabot` or `dependabot`, plus a `dependencies` label. Two behaviors are
specific to it:

> **mergeable "UNKNOWN":** GitHub computes mergeability asynchronously. If
> `mergeable == "UNKNOWN"`, wait 8 seconds, re-fetch once, and check again. If
> still `"UNKNOWN"` after the retry, FAIL the gate and ask the user to try again
> shortly. Never treat `"UNKNOWN"` as a pass.

> **PR-body edits do not survive a force-push.** Dependabot rewrites its own PR
> body when it rebases, silently dropping a `Closes #N` line added by hand. If
> you edited the body to add the issue link, re-read it immediately before Step 3
> and re-apply the edit if the rebase ate it.

## Step 0: Stage Marker (only if a substrate exists)

If the context file declares a stage-marker substrate, write the MERGE
`in_progress` marker per its invocation and degraded-mode handling. A missing
context file is not proof there is no substrate (#2419): if your prompt carries
a `run_id` and `sdlc-tool` is on PATH, write it anyway:

```bash
sdlc-tool stage-marker --stage MERGE --status in_progress --issue-number {issue_number} --run-id {run_id}
```

Report a failed write but never block the merge on it; the gate depends only on
`gh`. A genuinely standalone merge (no run identity, no `sdlc-tool`) skips this.

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

If any check fails, STOP and report it. Conflict resolution is out of scope:
the gate never rebases, force-pushes, or resolves conflicts.

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

If the context file declares a DOCS stage-completion substrate, DOCS completion
is a precondition alongside the REVIEW verdict. With no such substrate, emit
this advisory line (announced, not a silent pass) and proceed:

`"DOCS-completion gate: NOT ENFORCED — no substrate; DOCS completion cannot be verified here, merge relies on supervisor sequencing (see #1915)."`

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

If the merge command fails (e.g. branch protection changed since Step 1), report
it, remove any auth file, and do NOT retry blindly.

## Step 5: Record Completion

Under the same detection as Step 0, mark the MERGE stage `completed`. A
standalone merge skips this; the merge itself is the completion signal.

## Step 6: Apply Repo-Specific Addenda

Apply any further steps the addendum declares (extra gates before the merge;
plan migration, cleanup, and restarts after it). The addendum is additive; it
never relaxes the verify-then-merge contract.

Run every step **in-turn, synchronously** (#2051): run each command to
completion and act on its result within this turn, polling a backgrounded
command until it exits. This skill often runs with exactly one turn; no
completion event or wake-up arrives after the turn ends.

## Critical Rules

- **Never bypass the gate.** Create an authorization file only after every
  precondition passes, and remove it on every path.
- **Fail closed.** Any unconfirmed precondition (unknown CI state, missing
  review verdict, unresolved mergeability) is a FAIL.

## OUTCOME Contract Emission

As the last line of your final response, emit an OUTCOME contract:

- **Merged**: `<!-- OUTCOME {"status":"success","stage":"MERGE","artifacts":{"pr_url":"<URL>"}} -->`
- **Gate refused / merge failed**: `<!-- OUTCOME {"status":"fail","stage":"MERGE","artifacts":{}} -->`
