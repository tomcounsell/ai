---
name: do-patch
description: "Apply a targeted fix to failing tests or review findings. Triggered by 'patch this', 'fix the failures', 'fix the blockers', or 'do-patch'."
argument-hint: "<description-of-what-to-patch>"
effort: low
---

# Do Patch (Targeted Fix)

Fix a specific test failure or set of review findings with the smallest change
that stays aligned with the plan, verify it, and land it as one commit on the
current branch. You do not plan features, create PRs, or advance pipeline
stages; the SDLC router does that.

**Done when:** every finding has a visible disposition, the test suite and lint
pass in this environment, the fix is committed and pushed as one commit, and
you have reported what changed. Stop there; mention extra work you noticed
instead of doing it.

## Repo Context

If `docs/sdlc/do-patch.md` exists, read it and honor its declarations: how to
recover the plan from the branch, test and lint commands, a plan-checkbox sync
mechanism, cross-repo `gh` targeting, and restart-after-patch rules. Without it,
the skill needs only `git`, `gh`, and the repo's test runner.

## Inputs

PATCH_ARG: $ARGUMENTS
ITERATION_CAP: 3 (a caller may override, e.g. `--max-iterations 5`)

If PATCH_ARG is empty or literally `$ARGUMENTS`, take whatever followed
`/do-patch` in the invoking message. If there is still nothing, use the most
recent test output or review in the conversation; if none exists, stop with a
stuck report saying no failure context was found.

## Instructions

### Step 1: Recover the Build Context

A fix that passes tests but drifts from intent is not a fix. Before dispatching
the builder, gather:

1. **Plan**: read it in full if the caller passed a path or the context file
   says how to resolve it; extract goal, acceptance criteria, No-Gos, relevant
   files, and architectural decisions. No plan (e.g. a hotfix): proceed, and say
   so in the report.
2. **Tracking issue**: `gh issue view N`.
3. **Build history**: `git log --oneline main..HEAD`, and `pwd` (work in the
   current directory or worktree; never create or navigate to another one).
4. **Review findings** (when fixing a review): the PR review comments are the
   authority, not PATCH_ARG, which may be a lossy summary.

   ```bash
   PR_NUMBER=$(gh pr list --head "$(git rev-parse --abbrev-ref HEAD)" --json number -q '.[0].number')
   gh api "repos/{owner}/{repo}/pulls/${PR_NUMBER}/reviews" --jq '.[] | select(.state != "APPROVED") | {user: .user.login, state: .state, body: .body}'
   gh api "repos/{owner}/{repo}/issues/${PR_NUMBER}/comments" --jq '.[] | select(.body | startswith("## Review:")) | {created_at, body}'
   gh api "repos/{owner}/{repo}/pulls/${PR_NUMBER}/comments" --jq '.[] | {path: .path, line: .line, body: .body}'
   ```

**Every review finding gets a disposition**, not just blockers: fix it, or, if
it should stay as-is, annotate it in code (`# NOTE: [finding] -- left as-is
because [rationale]`) so the next reviewer sees a decision rather than a skip.

For multi-component failures or a non-obvious root cause, trace the data flow to
where it diverges and write a failing test that reproduces the bug before
fixing it (the context file may point to a fuller Trace & Verify reference). If
existing tests pass while the bug exists, find the mock hiding it.

### Step 2: Dispatch One Builder

One builder, one focused repair; never a team.

```
Task({
  description: "Fix: [one-line summary of the failure]",
  subagent_type: "builder",
  prompt: "
Fix a specific failure with targeted edits only; do not refactor unrelated code.

CWD: [current working directory — do not navigate away]
PLAN CONTEXT: [full plan: goal, acceptance criteria, no-gos, architectural decisions]
TRACKING ISSUE: [issue title and body, or 'No tracking issue']
RELEVANT FILES: [paths the plan names]
BUILD HISTORY: [git log --oneline main..HEAD]
FAILURE TO FIX: [full PATCH_ARG or failure text]
PR REVIEW FINDINGS: [every finding with path, line, and body, if fixing a review]

Make the minimal change that fixes each root cause, consistent with the plan.
Give every review finding a disposition: fix it, or annotate it in code with
`# NOTE: [finding] -- left as-is because [rationale]`. If a fix would contradict
the plan's No-Gos or architectural decisions, or needs an architectural change,
report the conflict instead of proceeding. Do not create a PR. Do not commit,
including WIP commits before exiting; the caller owns the single commit.

Report what you changed and why, and `criterion_addressed: <exact criterion
text>` naming the plan criterion (`## Acceptance Criteria` or
`## Success Criteria`) your fix satisfies, or `criterion_addressed: null`.
It MUST be null when the fix only touches lint or formatting, is
test-file-only (the test exercises pre-existing behavior), is comment- or
docstring-only, is a typo fix, or only touches `__pycache__/`, `.gitignore`,
`.gitkeep`, or generated artifacts. Otherwise name a criterion only if its
text describes the runtime behavior you changed; when unsure, use null (the
next review ticks it if the fix satisfies it).
",
  run_in_background: false
})
```

### Step 3: Re-run Tests to Verify

Run the test suite and lint yourself (commands per the context file; generic
default is the repo's standard runner and linter). Do not invoke `/do-test`.

- Exit 0 and lint clean → Step 3.5.
- Test failures or an execution error → Step 5.
- No tests collected → treat as pass.

A number the builder reported is a claim, not a measurement: its environment may
differ from yours (a venv missing optional dependencies silently deselects test
files). Report only what you observed here, including the collected/run count,
so a shrunken suite is visible. If your numbers disagree with the builder's,
report both and which environment produced each. A delta ("+0 errors") needs a
baseline you measured the same way; without one, report the absolute number and
say the baseline is unmeasured.

A failing fix never produces a commit.

### Step 3.5: Sync Plan Checkbox and Commit the Fix (Atomic Single Commit)

Commit the fix as one new commit and push it:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
git add -A
git commit -m "fix: ${SUMMARY}"
git push origin "HEAD:${BRANCH}"
```

- If the context file declares a plan-checkbox sync mechanism, run its exact
  invocation to tick the builder's `criterion_addressed` (Step 2) before the
  `git add -A`, so the plan tick and the code land in the same commit; never
  hand-edit the checkbox when a mechanism is declared. A helper failure
  (ambiguous or not-found match) is non-fatal: commit the code change alone.
  With no mechanism declared, skip the tick.
- Do NOT use `git commit --amend`; every patch is a fresh commit. A separate
  follow-up commit is also wrong: on a repo whose merge gate judges review
  freshness against the latest commit, it stales the review.
- You are the commit author: the builder never commits, and no parent skill
  commits on your behalf.
- If the repo's pre-commit hook auto-fixes lint (the context file says so), let
  it run on this commit instead of fixing lint by hand; fix only what it cannot.

### Step 4: Report Completion

```
Patch applied successfully.

Fix summary: [what changed and why]
Findings: [each finding → fixed | annotated (rationale)]
Files modified:
- [file] — [change]
Test result: [passed/failed/skipped, collected count] — measured here
Commit: [sha]
```

### Step 5: Retry or Report Stuck

If tests still fail, count the `/do-patch` attempts on this failure in the
session. Below ITERATION_CAP, dispatch a fresh builder (Step 2) with both the
original and the new failure output. At the cap, or when the fix needs an
architectural change, stop and report:

```
PATCH STUCK — iteration cap reached ({N}/{CAP})

Original failure: [summary]
Current failure after {N} attempts: [key lines]
What was tried:
- Attempt 1: [change]
Root-cause hypothesis: [why the fix is not working]

This requires human review or a different approach.
```
