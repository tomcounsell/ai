---
name: do-plan-critique
description: "Use when reviewing a plan before build. Triggered by 'critique this plan', 'review the plan', 'war room', or 'do-plan-critique'."
argument-hint: "<plan-path-or-issue-number>"
context: fork
---

# Plan Critique (War Room)

## Repo Context Probe

If `docs/sdlc/do-plan-critique.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.

The context file is where a repo layers its SDLC automation onto this generic baseline: stage/status markers around the critique, the repo's mandated plan sections, force-FULL doctrine paths, a resume/roster-completion barrier (a crash-resume probe, a frozen roster manifest, a plan-hash guard, and a membership-gate CLI), a verdict-recording substrate the downstream pipeline reads, and a plan-revising lock. When the file is absent (the common case in a foreign repo), this skill runs entirely on `git`, `gh`, and the Agent tool — it dispatches critics in the foreground, waits for each to return its findings, aggregates, and prints a verdict (no repo-specific tooling required).

## What this skill does

Critiques a plan document from a frozen roster of expert perspectives (1 (LITE) or 3 (FULL) critics selected by a triage step) plus automated structural validation. Each critic has a defined lens and returns severity-rated findings. The skill aggregates, deduplicates, and produces a verdict: READY TO BUILD, NEEDS REVISION, or MAJOR REWORK.

## When to load sub-files

- Spawning war room critics → read [CRITICS.md](CRITICS.md) for critic definitions and prompt templates

## Plan Resolution

Resolve the plan document path and issue number from `$ARGUMENTS`.

**IMPORTANT:** Always assign `ISSUE_NUMBER` unconditionally (never `${ISSUE_NUMBER:-…}`).
A non-empty inherited value (e.g. a stale "1724" latched from a prior context) would survive
deferral and divert recorder writes to the wrong session. Clobber it on every run. (#1731)

```bash
ARG="$ARGUMENTS"

# If argument is a number, resolve from GitHub issue
if [[ "$ARG" =~ ^#?[0-9]+$ ]]; then
  ISSUE_NUMBER="${ARG#\#}"  # assign ISSUE_NUMBER (canonical name) — clobbers any inherited value
  PLAN_PATH=$(gh issue view "$ISSUE_NUMBER" --json body -q '.body' | grep -oP '(?<=docs/plans/)[^\s)]+\.md' | head -1)
  if [ -n "$PLAN_PATH" ]; then
    PLAN_PATH="docs/plans/$PLAN_PATH"
  fi
fi

# If argument is a path, use directly; recover the issue number from plan frontmatter
if [[ "$ARG" == *.md ]]; then
  PLAN_PATH="$ARG"
  # Extract tracking issue from frontmatter: "tracking: https://.../issues/N" or "tracking: #N"
  ISSUE_NUMBER=$(grep -oP '(?<=tracking:[ \t])(https://[^\s]+/issues/|#?)\K[0-9]+' "$PLAN_PATH" 2>/dev/null | head -1)
fi

# Assert ISSUE_NUMBER is a positive integer before any recorder call (#1731).
# An empty or non-integer value here means the caller did not supply a resolvable
# issue reference — fail loudly so the supervisor sees an actionable error rather
# than a silently diverted verdict on a wrong session.
[[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]] || {
  echo "do-plan-critique: could not resolve a positive-integer ISSUE_NUMBER (got: '${ISSUE_NUMBER}'). Pass a numeric issue number or a plan path with a tracking: field." >&2
  exit 1
}

# WS-B (issue #2124): canonicalize PLAN_PATH to an ABSOLUTE path rooted at the repo
# top-level BEFORE the existence check and before it is passed to critics/SOURCE_FILES.
# A repo-root-relative plan path is unresolvable from a `.claude/worktrees/agent-*`
# cwd — the critic then finds nothing and may improvise a critique of a nonexistent
# plan instead of failing loudly. An absolute path removes that failure mode: the
# read either succeeds or the existence check below exits 1.
if [[ -n "$PLAN_PATH" && "$PLAN_PATH" != /* ]]; then
  REPO_TOPLEVEL=$(git rev-parse --show-toplevel 2>/dev/null)
  if [ -n "$REPO_TOPLEVEL" ] && [ -f "$REPO_TOPLEVEL/$PLAN_PATH" ]; then
    PLAN_PATH="$REPO_TOPLEVEL/$PLAN_PATH"
  elif [ -f "$PLAN_PATH" ]; then
    PLAN_PATH="$(cd "$(dirname "$PLAN_PATH")" && pwd)/$(basename "$PLAN_PATH")"
  fi
fi

# Verify plan exists (now at an absolute path — no cwd ambiguity)
if [ ! -f "$PLAN_PATH" ]; then
  echo "Plan not found: $PLAN_PATH"
  exit 1
fi
```

**Pass the absolute `$PLAN_PATH` into SOURCE_FILES and every critic prompt** so no
downstream step re-resolves a relative path against a worktree cwd.

## Instructions

### Step 1: Load Context

1. Read the plan document in full
2. If plan references a tracking issue, fetch it: `gh issue view N --json title,body,comments`
3. If plan has a "Prior Art" section, fetch referenced PRs/issues (up to 5):
   ```bash
   gh issue view N --json title,state,body --jq '{title, state}'
   gh pr view N --json title,state,mergedAt --jq '{title, state, mergedAt}'
   ```

### Step 1.5: Extract and Bundle Source Files

Extract all file paths referenced in the plan and read their contents. This prevents critics from hallucinating file contents by giving them verified source code.

1. Extract file paths from the plan text using regex patterns like `path/to/file.py`, backtick-quoted paths, and paths in code blocks
2. For each extracted path, attempt to read the file:
   - If the file exists: include its full contents in the SOURCE_FILES block
   - If the file does not exist: note it as `[FILE NOT FOUND: path/to/file.py]` -- do NOT ask critics to discover it
3. Bundle all contents into a `SOURCE_FILES` context block formatted as:

```
SOURCE_FILES:
--- path/to/file1.py ---
{file contents}
--- path/to/file2.py ---
{file contents}
--- path/to/missing.py ---
[FILE NOT FOUND]
```

This SOURCE_FILES block is passed to every critic in Step 3.

### Step 2: Structural Checks (Automated)

Run these checks directly — no LLM needed:

**2a. Required Sections**
Verify the plan's required sections exist and are non-empty. The context file
declares which sections this repo mandates; absent a declaration, verify the
plan's own structure is internally complete (problem, solution, tasks,
verification).

**2b. Task Integrity**
- Check for gaps in task numbering (e.g., 1, 2, 4 — missing 3)
- Verify all `Depends On` references point to valid task IDs
- Check for circular dependencies
- Flag any task with no validation command

**2c. Internal References**
- Extract file paths mentioned in the plan (e.g., `models/agent_session.py`, `bridge/observer.py`)
- Check which ones exist and which don't — report non-existent paths as findings
- Extract test file paths from Test Impact section — verify they exist

**2d. Prerequisite Status**
- For each prerequisite with a check command, run it and report current pass/fail status

**2e. Cross-Reference Consistency**
- Every Success Criterion should map to at least one task
- Every No-Go should not appear in the Solution section as planned work
- Every Rabbit Hole should not appear in the tasks as planned work

Report structural findings with severity:
- Missing required section → BLOCKER
- Task numbering gap → CONCERN
- Invalid dependency reference → BLOCKER
- Non-existent file path → CONCERN (could be intentionally new)
- Orphaned success criterion → CONCERN

### Step 2b: Resume Probe (only if the context file declares a roster barrier)

If the context file declares a crash-resume barrier, run its resume probe here:
check for a reusable incomplete run dir from a prior crash, and if found, set
`RESUMED=1`, reuse that dir's frozen roster, skip triage + roster freeze, and
proceed directly to Step 3 to dispatch only the missing critics. Follow the
context file's exact probe invocation and stale-dir GC instructions.

If no barrier is declared (the generic case), set `RESUMED=0` and continue to
Step 2.6 (triage) → Step 3 (dispatch all critics). There is nothing to resume.

### Step 2.6: Triage (fresh path only — skip if RESUMED=1)

Determine LITE (1 consolidated critic) or FULL (3 merged critics):

**Deterministic force-FULL** — use FULL without an LLM call if ANY of:
- The plan frontmatter has `appetite: Large` (or the repo's equivalent large-scope marker)
- The plan touches doctrine paths the context file enumerates (high-risk areas a repo always wants the full war room to vet). If the context file declares no doctrine paths, this trigger does not apply.

A LITE vote can never override force-FULL.

**LLM triage** (when force-FULL does not apply):
Spawn a single short-lived `sonnet` Agent with a brief classification prompt:

```
You are a plan triage agent. Classify this plan as LITE or FULL critique depth.
LITE = purely internal, non-doctrine, small scope change (one bug fix, one CLI flag, one config key).
FULL = anything touching critical paths, cross-component changes, new abstractions, architectural decisions.
Bias to FULL on any ambiguity.
Reply with exactly one line: "LITE: <one-line reason>" or "FULL: <one-line reason>".

PLAN:
{plan frontmatter + first 1000 chars}
```

Set `CRITIQUE_DEPTH` to `LITE` or `FULL` based on the result.

### Step 3a: Fix the Critic Roster

(Skip this step if RESUMED=1 — the surviving run already defines the roster.)

Before dispatching ANY critic, freeze the expected critic roster. This is the
membership set the Step 3.5 completion check verifies — completion cannot be
satisfied by dispatching fewer critics than the roster lists.

The roster follows from the triage depth:
- **LITE** → `["Consolidated Critic"]` (1)
- **FULL** → `["Risk & Robustness", "Scope & Value", "History & Consistency"]` (3)

If the context file declares a roster barrier (a frozen `_roster.json` manifest,
a per-run directory, and a plan-hash stale-resume guard), create those artifacts
exactly as it specifies — the frozen manifest is what makes the Step 3.5 gate
mechanically verifiable across a crash. In the generic case, simply record the
roster names in memory; the Step 3.5 check verifies each named critic returned
its findings.

### Step 3: War Room (Parallel Critics)

Read [CRITICS.md](CRITICS.md) for the full critic definitions and prompt templates.

Dispatch the roster's critics (on a fresh run, all roster members; on a resume, only those not yet completed). Read CRITICS.md for the 3 FULL critics and 1 Consolidated Critic (LITE). Each critic gets:
- The full plan text
- The SOURCE_FILES block (verified file contents from Step 1.5)
- The issue context (if available)
- Prior art summaries (if fetched)
- Their specific lens and instructions from CRITICS.md

Each critic is a general-purpose Agent with a focused prompt. Use `model: "sonnet"` for each critic — fast enough for 0-3 findings, saves cost.

**Generic completion model:** dispatch the critics in the **foreground** and wait for each to return its findings before aggregating.

**If the context file declares a result-file roster barrier**, each critic instead writes its findings to a per-critic result file — atomically: write to `{critic_name}.result.md.tmp`, then rename to `{critic_name}.result.md` — ending in the two-line terminal completion fence `<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED` (the exact convention CRITICS.md embeds in every critic prompt). Completion is observed on the filesystem — independent of whether the driver awaited the agents. Follow the context file's run-dir layout and pass each critic its run-dir path and `{critic_name}`. The barrier is the robust form when the agent driver may return early from a background dispatch; foreground-and-wait suffices when the driver reliably blocks.

Each critic returns **0-3 findings** (or the literal `No findings.`) in this format:

```
SEVERITY: BLOCKER | CONCERN | NIT
LOCATION: Section name or line reference in the plan
FINDING: What's wrong (1-2 sentences)
SUGGESTION: How to fix it (1-2 sentences)
IMPLEMENTATION NOTE: [Required for CONCERN and BLOCKER severity. Exempt for NIT.]
  The specific guard condition, call signature, or gotcha that makes this finding
  implementable without re-investigation. If you cannot write this note, the finding
  is not yet specific enough to ship.
```

NITs are exempt from the Implementation Note field. For CONCERN and BLOCKER findings, the note must be concrete: a specific guard condition (e.g., `if event: event.set()`), a call signature, or a "why" explanation that prevents naive application of the fix.

### Step 3.5: Roster Completion Check (mandatory, runs BEFORE Step 4)

You do NOT proceed to Step 4 (aggregation) until every roster member fixed in
Step 3a has returned its findings, OR you record the `CRITIQUE INCOMPLETE`
fallback after exhausting the re-dispatch cap.

**Generic check:** confirm each named roster member returned findings (or
`No findings.`). If any are missing, re-dispatch ONLY the missing critics in the
**foreground**, then re-check.

**If the context file declares a membership-gate CLI**, invoke it against the run
dir instead — it reads the frozen `_roster.json` manifest and verifies each
named member's result file carries the terminal completion fence, printing a JSON
gate decision (`{"complete": bool, "missing": [...], ...}`) and exiting non-zero
until complete. This filesystem membership check holds whether or not the driver
awaited the agents.

**Grounding leg (issue #2124).** When the context file's membership-gate CLI accepts
a `--plan-path`, pass the plan path so the gate ALSO verifies each result file
verifiably cites the real plan (a verbatim quote or a real section header). A critic
that returned a structurally-valid but **fabricated** critique — reviewing a
different, nonexistent plan with zero grounded reads — carries no substring that
collides with the real plan bytes, so it is reported as an incomplete member
(`ungrounded`) exactly like a missing critic: bounded re-dispatch, then the loud
`MAJOR REWORK (CRITIQUE INCOMPLETE)` STOP. This closes the "hallucinated critique
that looks valid" hole at the gate rather than trusting the fork's self-report.

**Bounded re-dispatch.** `MAX_CRITIC_REDISPATCH = 2`. The total attempt budget
is pinned: **1 initial dispatch (Step 3) + up to 2 re-dispatches = 3 attempts
maximum per critic** — never an unbounded retry loop. Every re-dispatch is
**foreground** (never `run_in_background: true` — a background re-dispatch
re-introduces exactly the fire-and-forget assumption this check replaces).

**STOP-grade verdict on a still-incomplete roster.** If the roster is still
incomplete after `MAX_CRITIC_REDISPATCH` rounds, do NOT aggregate and do NOT
loop further. Jump to **Step 5.5** and record the verdict string:

```
MAJOR REWORK (CRITIQUE INCOMPLETE: roster N/M — missing: {names})
```

(`N` = completed count, `M` = roster count, `{names}` = the critics that never
reported.) The `MAJOR REWORK` substring routes back to `/do-plan` (in a repo
with an SDLC router its guard **G1** consumes it). The stage **ALWAYS produces
a verdict** — never a silent or empty exit. Then set the plan-revising lock per
**Step 5.6** (a `CRITIQUE INCOMPLETE` verdict is revision-grade).

**Run-dir cleanup gating (barrier only).** If the context file's barrier created
a per-run directory, clean it up **ONLY on the `complete: true` path**. On the
incomplete / `CRITIQUE INCOMPLETE` path, **PRESERVE** it for forensics — the
partial/missing result files are the diagnostic evidence of which critics never
reported.

### Step 4: Aggregate and Deduplicate

**Aggregation invariant: iterate every roster member fixed in Step 3a and read
each one's findings** — never just "whatever happened to come back". A missing
member at this point is a visible gap (Step 3.5 should have caught it), never a
member silently dropped. (When the barrier is active, iterate the frozen
`_roster.json` manifest and read each `{name}.result.md` rather than globbing
whatever files exist.)

1. Collect all findings (structural + critic), accounting for every roster member fixed in Step 3a
2. **Deduplicate**: If two critics flagged the same issue, keep the higher-severity version and note which critics agreed
3. **Sort by severity**: BLOCKERs first, then CONCERNs, then NITs
4. **Cross-validate**: If the Skeptic and Simplifier both flagged the same component, elevate to BLOCKER if not already
5. **Implementation Note validation** — for each finding with SEVERITY = CONCERN or BLOCKER:
   - If IMPLEMENTATION NOTE is missing or empty: mark the finding as malformed, exclude it from the report, and log: "Finding [title] missing Implementation Note — excluded (critic should have included it in first pass)"
   - **Do NOT re-run the critic.** The note requirement is enforced in CRITICS.md; a missing note means the finding is not yet specific enough to ship. Exclude and move on.

### Step 5: Report

Emit every section header literally; empty categories emit '## Blockers\n\nNone.' — do not omit the header.

Output the final report in this format:

```markdown
# Plan Critique: {plan name}

**Plan**: {plan_path}
**Issue**: #{issue_number} (if applicable)
**Critics**: {roster members from _roster.json} ({LITE or FULL} depth)
**Findings**: {N} total ({blockers} blockers, {concerns} concerns, {nits} nits)

## Blockers

### {finding title}
- **Severity**: BLOCKER
- **Critics**: {which critics flagged this}
- **Location**: {section reference}
- **Finding**: {description}
- **Suggestion**: {how to fix}
- **Implementation Note**: {the specific guard condition, call signature, or gotcha}

## Concerns

### {finding title}
- **Severity**: CONCERN
- **Critics**: {which critics flagged this}
- **Location**: {section reference}
- **Finding**: {description}
- **Suggestion**: {how to fix}
- **Implementation Note**: {the specific guard condition, call signature, or gotcha}

## Nits

### {finding title}
...

## Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS/FAIL | ... |
| Task numbering | PASS/FAIL | ... |
| Dependencies valid | PASS/FAIL | ... |
| File paths exist | PASS/FAIL | N of M exist |
| Prerequisites met | PASS/FAIL | ... |
| Cross-references | PASS/FAIL | ... |

## Verdict

{One of:}
- **READY TO BUILD (no concerns)** — No CONCERN or BLOCKER findings (NITs do not trigger this variant). Proceed directly to build.
- **READY TO BUILD (with concerns)** — No BLOCKERs, but one or more CONCERN findings exist. A revision pass will embed Implementation Notes before build.
- **NEEDS REVISION** — {N} blockers must be resolved before build.
- **MAJOR REWORK** — Fundamental issues identified. Recommend re-planning.
```

### Step 5.5: Finalize — record the verdict (mandatory, self-contained)

This step is **mandatory and reached on EVERY exit path** — every verdict (READY TO BUILD, NEEDS REVISION, MAJOR REWORK) passes through it. Do not return control to a supervisor before completing it.

**Generic case:** the verdict printed in Step 5 IS the recorded output — the caller reads it from your response. Nothing further to do.

**If the context file declares a verdict-recording substrate** (so a downstream
pipeline router can consume the verdict programmatically), record the verdict via
that substrate now, and on a READY TO BUILD verdict **co-locate** the completion
stage-marker write with the verdict record in the SAME block so the verdict and
the marker can never desync. Follow the context file's exact invocation. Verdict
+ marker are a single unit on the READY path: never record one without the other.
On any non-READY verdict, leave the stage marker at `in_progress`. Do NOT suppress
substrate errors — a failed recording must surface as a visible non-zero exit.

`$VERDICT_STRING` is the exact verdict string emitted in Step 5 (e.g. `"NEEDS REVISION"`, `"READY TO BUILD (with concerns)"`).

### Step 5.6: Set plan-revising lock (only if the context file declares one)

If the context file declares a plan-revising lock (a flag a downstream router
reads to block build dispatch until a revision pass completes), set it after
recording the verdict whenever the verdict requires a revision pass AND
`revision_applied` is not already `true` in the plan frontmatter. Follow the
context file's exact invocation.

Set the lock when the verdict is one of:
- `NEEDS REVISION`
- `MAJOR REWORK`
- `READY TO BUILD (with concerns)` — and `revision_applied` is not already `true` in the plan frontmatter

**Do NOT set the lock** when the verdict is `READY TO BUILD (no concerns)` — no revision pass is needed.

**Do NOT set the lock** when `revision_applied: true` is already in the plan frontmatter — the revision has already been applied.

In the generic case (no lock declared), skip this step — the printed verdict already tells the caller whether a revision pass is needed.

## Outcome Contract

The skill returns a structured verdict that the SDLC pipeline can use:

| Verdict | SDLC Action |
|---------|-------------|
| READY TO BUILD (no concerns) | Proceed directly to `/do-build` |
| READY TO BUILD (with concerns) | Trigger revision pass via `/do-plan` before `/do-build` |
| NEEDS REVISION | Return to `/do-plan` with blocker findings |
| MAJOR REWORK | Return to issue discussion |

**"READY TO BUILD (with concerns)"** triggers a revision pass. This pass incorporates the Implementation Note from each concern into the plan text. CONCERNs are not reclassified as defects — the revision pass is a plan clarity step, not a rework step. The concern is still acknowledged (not blocking), but its Implementation Note is embedded in the plan so the builder has unambiguous implementation guidance without re-investigation.

Use **"READY TO BUILD (no concerns)"** when there are zero CONCERN or BLOCKER findings (NITs do not block and do not trigger revision).

## What This Skill Does NOT Do

- **Does not rewrite the plan** — output is findings, not a revised document
- **Does not expand scope** — critics flag gaps, they don't suggest features
- **Does not re-architect** — validates internal consistency, not whether a different approach is better
- **Does not block on NITs** — only BLOCKERs prevent a READY TO BUILD verdict
