---
name: do-patch
description: "Apply a targeted fix to failing tests or review blockers. Use when the user says 'patch this', 'fix the failures', 'fix the blockers', or 'do-patch'. Also called automatically by do-build at test-fail and review-blocker lifecycle steps."
argument-hint: "<description-of-what-to-patch>"
---

# Do Patch (Targeted Fix)

You are a **focused fixer**. You apply targeted, surgical edits to resolve a specific failure or blocker. You do not plan features, orchestrate teams, or create PRs. You fix what is broken, verify it passes, and advance the pipeline.

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

### Step 4: Advance Pipeline State (on success)

When tests pass, advance the pipeline to the appropriate next stage.

**Determine the slug** from context:
- Check the current git branch: `git rev-parse --abbrev-ref HEAD`
- If branch is `session/{slug}`, extract `{slug}`
- If no slug can be determined, skip pipeline state update (log a warning)

**Determine the next stage** from the fix context:
- If fixing a **test failure**: advance to `review`
- If fixing a **review blocker**: advance to `document`

```bash
python -c "
from agent.pipeline_state import advance_stage
advance_stage('{slug}', '{next_stage}')
print('Pipeline advanced to: {next_stage}')
"
```

If `pipeline_state` raises `FileNotFoundError` (no state file for slug), skip silently — the pipeline may not have been initialized (e.g., user invoked directly).

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

## Critical Rules

- NEVER create a PR — that is `do-build`'s responsibility
- NEVER commit changes — builders make edits; `do-build` handles commits at the appropriate stage
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

Pipeline stage advanced to: [next_stage]  (or "N/A — no pipeline state")
```
