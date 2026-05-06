---
name: do-patch
description: "Apply a targeted fix to failing tests or review blockers. Use when the user says 'patch this', 'fix the failures', 'fix the blockers', or 'do-patch'. Also called automatically by do-build at test-fail and review-blocker lifecycle steps."
argument-hint: "<description-of-what-to-patch>"
---

# Do Patch (Targeted Fix)

You are a **focused fixer**. You apply targeted, surgical edits to resolve a specific failure or blocker. You do not plan features, orchestrate teams, or create PRs. You fix what is broken, verify it passes, and advance the pipeline.

## Cross-Repo Resolution

For cross-project work, the `GH_REPO` environment variable is automatically set by `sdk_client.py`. The `gh` CLI natively respects this env var, so all `gh` commands automatically target the correct repository. No `--repo` flags or manual parsing needed.

## When This Skill Is Invoked

Two lifecycle points trigger `/do-patch`:

1. **Test failure** — `do-build` hit failing tests after a build iteration. The failure output is passed as `PATCH_ARG`.
2. **Review blocker** — `do-build` hit a review comment blocking merge. The comment text is passed as `PATCH_ARG`.

Users may also invoke directly:
- `do-patch "3 tests failing in test_bridge.py — connection timeout"`
- `do-patch "review blocker: race condition in session lock"`
- `do-patch` (no args — reads most recent failure from context)

## Variables

PATCH_ARG: $ARGUMENTS
ITERATION_CAP: 3  (default; caller may override by appending e.g. `--max-iterations 5`)

## Build Context Recovery

The patch agent is re-entering the build loop. It needs the **same context** that `do-build` originally gave its builder agents — not just a failure message. Without this context, fixes may pass tests but drift from intent, use wrong patterns, or edit the wrong files.

**Before deploying the builder agent, gather ALL of this:**

1. **Plan document** — The full plan, not just a summary:
   - If the caller passed the plan path, read it
   - Otherwise: check git branch (`session/{slug}`) → read `docs/plans/{slug}.md`
   - Extract: goal, acceptance criteria, no-gos, relevant files, architectural decisions
2. **Tracking issue** — `gh issue view N` for the original issue context and discussion
3. **Working directory** — Confirm CWD (worktree path if invoked by do-build, repo root if direct)
4. **What was already built** — Run `git log --oneline main..HEAD` to see what the build has done so far
5. **Relevant file paths** — From the plan's "Relevant Files" section, so the builder knows where to look
6. **PR review comments** (for review blockers) — **MANDATORY** when fixing review feedback:
   ```bash
   # Find the PR for the current branch
   PR_NUMBER=$(gh pr list --head "$(git rev-parse --abbrev-ref HEAD)" --json number -q '.[0].number')

   # Fetch ALL review comments — these are the authoritative blockers
   gh api repos/{owner}/{repo}/pulls/${PR_NUMBER}/reviews --jq '.[] | select(.state != "APPROVED") | {user: .user.login, state: .state, body: .body}'
   gh api repos/{owner}/{repo}/pulls/${PR_NUMBER}/comments --jq '.[] | {path: .path, line: .line, body: .body, user: .user.login}'
   ```
   Do NOT rely solely on the PATCH_ARG text — it may be a summary that misses specific blockers. The PR review comments are the ground truth. Include the full review comment text in the builder prompt.

**If no plan exists** (e.g., user-invoked hotfix), proceed with failure context alone — but note this in the fix report.

**If PATCH_ARG is empty or literally `$ARGUMENTS`**: The skill argument substitution did not run. Look at the user's original message in the conversation — they invoked this as `/do-patch <argument>`. Extract whatever follows `/do-patch` as the value of PATCH_ARG. Do NOT stop or report an error; just use the argument from the message.

## Instructions

### Step 1: Identify What Is Broken

If `PATCH_ARG` is non-empty, use it directly as the description of what needs fixing.

If `PATCH_ARG` is empty, read the most recent failure from session context:
- Look for the most recent pytest output or review comment in the conversation
- If nothing is found, ask the user: "What is failing? Paste the test output or review comment."

Parse the input to classify the fix type:
- **Test failure**: pytest output with `FAILED`, `ERROR`, or traceback lines
- **Review blocker**: prose describing a code issue, race condition, logic bug, or style violation

**If the fix type is "review blocker" or "review findings"**: You MUST fetch the actual PR review comments from GitHub (see Build Context Recovery step 6 above). The PATCH_ARG may be a summary that omits specific findings. The PR comments are the authoritative source of what needs fixing.

**ALL review findings must be addressed** — not just blockers. Nits, tech debt suggestions, and style feedback are all actionable. A "minimum approve" with unresolved findings is not acceptable. For each finding:
- **Fix it** if the fix is straightforward and aligned with the plan
- **Annotate it** if the finding should remain as-is — add an inline code comment: `# NOTE: [finding] -- left as-is because [rationale]` so the next reviewer sees a deliberate decision, not a skipped issue
- **Never silently skip** a finding — every item must have a visible disposition

#### Root Cause Analysis: Trace & Verify

Before jumping to a fix, apply the Trace & Verify protocol (see `docs/features/trace-and-verify.md` for the full reference). This replaces narrative-only reasoning with data-driven verification:

1. **Trace the data flow** from input to expected output. At each boundary between components, capture the actual values being passed. Where does the data diverge from expectations?
2. **Write a failing test** that reproduces the exact broken behavior. The test must fail for the right reason (the bug), not a setup issue.
3. **Identify the fix** based on where the trace diverged.
4. **Verify forward**: After applying the fix, re-run the trace. Show that every step now produces correct values and the test passes.
5. **Check for mocks hiding reality**: If existing tests pass but the bug exists in production, identify which mocks are hiding the real behavior and add integration tests that exercise the actual code paths.

For single-component bugs with obvious fixes (typo, missing import, off-by-one), skip straight to the fix. Use Trace & Verify when the failure involves multiple components or when the root cause is not immediately obvious.

### Step 2: Deploy a Single Builder Agent

Deploy **one** builder agent to make the targeted fix. Do NOT spawn multiple agents or orchestrate a team — this is a single-focus repair.

```
Task({
  description: "Fix: [one-line summary of the failure]",
  subagent_type: "builder",
  prompt: "
You are fixing a specific failure. Make targeted edits only — do not refactor unrelated code.

CWD: [current working directory — do not navigate away]

PLAN CONTEXT:
[full plan document contents — goal, acceptance criteria, no-gos, architectural decisions]

TRACKING ISSUE:
[issue title and body from gh issue view, or 'No tracking issue']

RELEVANT FILES (from plan):
[list of file paths the plan identifies as relevant to the feature]

BUILD HISTORY (commits so far on this branch):
[output of git log --oneline main..HEAD]

FAILURE TO FIX:
[full PATCH_ARG content or failure text from context]

PR REVIEW COMMENTS (if fixing review blockers):
[full review comments from gh api — include path, line number, and comment body for each]

YOUR JOB:
1. Read the failure output carefully. Identify the root cause.
2. Review the plan context and build history to understand what was intended.
3. Make the minimal code change that fixes the root cause while staying aligned with the plan.
4. Do NOT change unrelated code, tests, or files.
5. Do NOT create a PR.
6. Do NOT commit — the caller will handle commits.
7. After editing, report what you changed and why, referencing the plan context.

If the fix requires understanding surrounding context, read the relevant files first.
If the failure has multiple root causes, fix all of them in this single pass.
If a fix would contradict the plan's no-gos or architectural decisions, report the conflict instead of proceeding.

**Annotate rather than skip:** If a review finding is genuinely not worth fixing (e.g., a style nit in legacy code, a suggestion that contradicts the plan), do NOT silently skip it. Instead:
- Add an inline code comment at the relevant location: `# NOTE: [finding summary] -- left as-is because [rationale]`
- This creates a paper trail so the next reviewer does not re-flag the same issue.
- The finding is then 'addressed' (annotated), not 'skipped'.

**Criterion mapping (REQUIRED in your completion report):** If your fix
addresses a specific criterion from the plan's criteria section
(`## Acceptance Criteria` or `## Success Criteria`), identify which criterion
by exact text. Report this in your completion summary as
`criterion_addressed: <text>` (or `criterion_addressed: null` if no clear
match). The patch skill writes the corresponding tick `[x]` to the plan file
in the SAME commit as your code change — atomic single commit, no separate
'tick off' commit.

You MUST report `criterion_addressed: null` when your fix only changes any of
the following (cosmetic-only fixes never tick a criterion):
1. lint or formatting-only edits (whitespace, import order, ruff fixes)
2. test-file-only edits where the test exercises pre-existing behavior
3. comment-only or docstring-only edits
4. typo fixes
5. edits that touch only `__pycache__/`, `.gitignore`, `.gitkeep`, or
   generated artifacts

Edits outside this list MAY tick a criterion if the criterion's text references
the runtime behavior the edit changes. When uncertain, prefer
`criterion_addressed: null` — the next `/do-pr-review` round will tick it
properly if the fix actually satisfies a criterion.
"
})
```

### Step 3: Re-run Tests to Verify

After the builder agent reports completion, run tests and lint directly — do NOT invoke `/do-test` (parallel dispatch is overkill for patch verification):

```bash
# Run full test suite
pytest tests/ -v --tb=short

# Run lint checks
python -m ruff check .
black --check .
```

Parse the results:
- **pytest exit code 0** AND **both lint tools pass**: All tests pass — proceed to Step 4
- **pytest exit code 1**: Some tests failed — proceed to Step 5 (retry or report stuck)
- **pytest exit code 2**: Test execution error — report the error and proceed to Step 5
- **pytest exit code 5**: No tests collected — treat as pass (no tests to break)

Report the test summary (passed/failed/skipped counts) before proceeding.

### Step 3.5: Sync Plan Checkbox (Atomic Commit)

After the test-pass verification in Step 3 succeeds and BEFORE Report
Completion, sync the plan-file checkbox so it lives in the same commit as the
code fix. A separate "tick off completed plan items" commit is exactly the
oscillation symptom this skill avoids — it would invalidate the prior PR
approval (review-comment freshness gate) and force a re-review.

**Procedure:**

```bash
# Read the builder agent's reported `criterion_addressed` from Step 2's output.
SLUG=$(echo "$BRANCH" | sed 's|^session/||')
PLAN_PATH="docs/plans/${SLUG}.md"
TICK_SUFFIX=""

if [ -n "$CRITERION_ADDRESSED" ] && [ "$CRITERION_ADDRESSED" != "null" ]; then
  if python -m tools.plan_checkbox_writer tick "$PLAN_PATH" --criterion "$CRITERION_ADDRESSED"; then
    TICK_SUFFIX=" — addresses \"$CRITERION_ADDRESSED\""
  else
    # Helper failure (MATCH_AMBIGUOUS / MATCH_NOT_FOUND / others) is NON-FATAL.
    # The commit STILL happens (with the code change only); the failure is
    # logged but does NOT abort the patch flow. The next /do-pr-review round
    # will reconcile via tick/untick.
    echo "WARN: plan_checkbox_writer failed for criterion: $CRITERION_ADDRESSED" >&2
  fi
fi

# Atomic commit: `git add -A` captures BOTH the builder's code edits AND the
# helper's plan-file edit (if any). The plan write and the code fix go into
# the SAME commit. Do NOT use `git commit --amend` — every patch is a fresh
# commit per the existing convention at SKILL.md "Commit and Push Rules".
git add -A
git commit -m "fix(#${SDLC_ISSUE_NUMBER}): ${SUMMARY}${TICK_SUFFIX}"
git push origin "HEAD:${BRANCH}"
```

**Why same-commit (and not amend, not separate):** The single-commit invariant
is what makes the merge-gate review-comment freshness check pass on the next
attempt — the latest commit's `committer.date` advances together with the
code change. A separate tick-off commit pushed AFTER the review would force
re-review.

**Builder authorship invariant:** The builder agent does NOT commit (per
SKILL.md "Commit and Push Rules"); the patch skill is the commit author.
Step 3.5 preserves that — the helper invocation and the commit happen at the
patch-skill level, not at the builder-agent level.

**Test ordering invariant:** The test-pass check in Step 3 happens BEFORE the
commit in Step 3.5, so a failing fix never produces a commit. An ambiguous
criterion in Step 3.5 is non-fatal; a failed test in Step 3 aborts the flow.

### Step 4: Report Completion

When tests pass, report success. Pipeline stage advancement is handled by the Observer/SDLC router -- do-patch does not determine or advance pipeline stages.

### Step 5: Handle Failure — Retry or Report Stuck

If tests still fail after the fix attempt:

**Check iteration count.** Count how many times `/do-patch` has been called in this session for the same failure.

- If `iterations < ITERATION_CAP`: retry from Step 2 with the new failure output
  - Re-read the updated test output
  - Deploy a new builder agent with both the original and new failure outputs for context
- If `iterations >= ITERATION_CAP`: report stuck — do NOT retry

**Stuck report format:**
```
PATCH STUCK — iteration cap reached ({N}/{CAP})

Original failure:
[original PATCH_ARG summary]

Current failure after {N} fix attempts:
[current test output — key lines only]

What was tried:
- Attempt 1: [what was changed]
- Attempt 2: [what was changed]
- Attempt N: [what was changed]

Recommendation:
[analysis of why the fix isn't working — root cause hypothesis]

This requires human review or a different approach. Escalating.
```

## Lint Discipline

Lint and formatting are handled automatically -- agents should never waste iterations on lint fixes.

- **Intermediate commits**: Use `--no-verify` to skip the pre-commit hook during WIP commits mid-task. This avoids unnecessary lint interruptions while the agent is still working.
- **Final commits**: Let the pre-commit hook run (no `--no-verify`). The hook auto-fixes all fixable lint/format issues via `ruff format` + `ruff check --fix` and re-stages the changes. Only genuinely unfixable issues block the commit.
- **Never run manual lint checks**: Do NOT run `ruff check .` or `ruff format --check .` as a separate validation step. The pre-commit hook handles this automatically on final commits.
- **PostToolUse hook**: The `format_file.py` hook runs `ruff check --fix` + `ruff format` on individual files after every Write/Edit, so files stay clean as agents work.

## Commit and Push Rules

The commit and push are part of Step 3.5 ("Sync Plan Checkbox") — see that step
for the atomic-commit procedure that bundles the code fix with the plan-file
checkbox tick into a single commit. Do NOT commit elsewhere; the single-commit
invariant is what keeps the merge-gate review-comment freshness check passing.

This skill owns its full lifecycle — no parent skill handles commits on its
behalf.

## Critical Rules

- NEVER create a PR — that is `do-build`'s responsibility
- NEVER touch the Document or PR pipeline stages
- NEVER create new worktrees — work in the CWD/worktree already active
- NEVER refactor unrelated code — targeted fixes only
- Keep fixes minimal: change the least amount of code needed to pass tests
- If a fix would require architectural changes, report stuck immediately — do not attempt it

## Context-Awareness

When invoked by `do-build`, this skill receives structured failure output. When invoked by a user, it may receive:
- A short description ("3 tests failing")
- Full pytest output
- A review comment
- Nothing (empty args)

Adapt to what is provided. Extract the signal from whatever input arrives.

## CWD-Relative Execution

All commands run relative to the current working directory. Do not attempt to detect or navigate to worktrees. When `/do-patch` is invoked:
- From `do-build`: CWD is already the worktree — commands run there
- Directly by user: CWD is wherever the user is — commands run there

Run `pwd` once at the start to confirm and log it.

## Example Invocations

**User-facing (direct invocation):**
```
/do-patch "3 tests failing in test_bridge.py — connection timeout"
/do-patch "review blocker: race condition in session lock"
/do-patch  (no args — reads most recent failure from context)
```

**Model-invocable (called by do-build at test-fail step):**
```
/do-patch FAILED tests/unit/test_bridge_logic.py::test_session_lock_cleanup
AssertionError: Expected lock to be cleared, got 1 active lock
```

**Model-invocable (called by do-build at review-blocker step):**
```
/do-patch The session cleanup in bridge/telegram_bridge.py line 247 has a race
condition — if the cleanup runs while a new session is being initialized,
it will incorrectly clear the new session's lock.
```

## Success Report Format

When the patch succeeds and tests pass:

```
Patch applied successfully.

Fix summary: [what was changed and why]

Files modified:
- [file1.py] — [brief description of change]
- [file2.py] — [brief description of change]

Test result: ALL TESTS PASSED
```
