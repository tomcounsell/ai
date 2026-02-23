---
name: do-patch
description: "Apply a targeted fix to failing tests or review blockers. Use when the user says 'patch this', 'fix the failures', 'fix the blockers', or 'do-patch'. Also model-invocable: called automatically by do-build at test-fail and review-blocker lifecycle steps."
model-invocable: true
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

## Instructions

### Step 1: Identify What Is Broken

If `PATCH_ARG` is non-empty, use it directly as the description of what needs fixing.

If `PATCH_ARG` is empty, read the most recent failure from session context:
- Look for the most recent pytest output or review comment in the conversation
- If nothing is found, ask the user: "What is failing? Paste the test output or review comment."

Parse the input to classify the fix type:
- **Test failure**: pytest output with `FAILED`, `ERROR`, or traceback lines
- **Review blocker**: prose describing a code issue, race condition, logic bug, or style violation

### Step 2: Deploy a Single Builder Agent

Deploy **one** builder agent to make the targeted fix. Do NOT spawn multiple agents or orchestrate a team — this is a single-focus repair.

```
Task({
  description: "Fix: [one-line summary of the failure]",
  subagent_type: "builder",
  prompt: "
You are fixing a specific failure. Make targeted edits only — do not refactor unrelated code.

CWD: [current working directory — do not navigate away]

FAILURE TO FIX:
[full PATCH_ARG content or failure text from context]

YOUR JOB:
1. Read the failure output carefully. Identify the root cause.
2. Make the minimal code change that fixes the root cause.
3. Do NOT change unrelated code, tests, or files.
4. Do NOT create a PR.
5. Do NOT commit — the caller will handle commits.
6. After editing, report what you changed and why.

If the fix requires understanding surrounding context, read the relevant files first.
If the failure has multiple root causes, fix all of them in this single pass.
"
})
```

### Step 3: Re-run Tests to Verify

After the builder agent reports completion, invoke `/do-test` to verify the fix:

```
/do-test
```

- If all tests pass: proceed to Step 4 (advance pipeline and report success).
- If tests still fail: proceed to Step 5 (retry or report stuck).

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
