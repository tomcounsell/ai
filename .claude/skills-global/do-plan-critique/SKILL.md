---
name: do-plan-critique
description: "Use when reviewing a plan before build. Triggered by 'critique this plan', 'review the plan', 'war room', or 'do-plan-critique'."
argument-hint: "<plan-path-or-issue-number>"
effort: medium
---

# Plan Critique (War Room)

Critique a plan document before build and return a verdict the pipeline acts on: READY TO BUILD
(no concerns / with concerns), NEEDS REVISION, or MAJOR REWORK. A frozen roster of independent
critics (1 for LITE, 3 for FULL) plus automated structural checks produce severity-rated,
cited findings; you aggregate them and decide. The output is findings; plan edits belong to
`/do-plan`.

**Runs inline, never as `context: fork`.** A forked skill's subagent sits at the harness
spawn-depth limit, where the Agent tool is withheld, so a forked critique under an SDLC stage
runner could spawn no critics and silently collapsed into one agent (#3137).

**Repo context.** If `docs/sdlc/do-plan-critique.md` exists, read it and honor its declarations; otherwise use the generic defaults described below.
It declares stage markers, mandated plan sections, force-FULL doctrine paths, the
resume/roster barrier (resume probe, frozen `_roster.json`, plan-hash guard, membership-gate
CLI), the verdict-recording substrate, and the plan-revising lock. Without it, the skill runs
on `git`, `gh`, and the Agent tool alone and prints its verdict.

## Plan Resolution

Assign `ISSUE_NUMBER` unconditionally (never `${ISSUE_NUMBER:-…}`): an inherited stale value
would divert recorder writes to the wrong session (#1731).

```bash
ARG="$ARGUMENTS"

# If argument is a number, resolve from GitHub issue
if [[ "$ARG" =~ ^#?[0-9]+$ ]]; then
  ISSUE_NUMBER="${ARG#\#}"  # clobbers any inherited value
  PLAN_PATH=$(gh issue view "$ISSUE_NUMBER" --json body -q '.body' | grep -oP '(?<=docs/plans/)[^\s)]+\.md' | head -1)
  if [ -n "$PLAN_PATH" ]; then
    PLAN_PATH="docs/plans/$PLAN_PATH"
  fi
fi

# If argument is a path, use directly; recover the issue number from plan frontmatter
if [[ "$ARG" == *.md ]]; then
  PLAN_PATH="$ARG"
  # "tracking: https://.../issues/N" or "tracking: #N"
  ISSUE_NUMBER=$(grep -oP '(?<=tracking:[ \t])(https://[^\s]+/issues/|#?)\K[0-9]+' "$PLAN_PATH" 2>/dev/null | head -1)
fi

# Fail loudly before any recorder call rather than divert a verdict (#1731).
[[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]] || {
  echo "do-plan-critique: no positive-integer ISSUE_NUMBER (got: '${ISSUE_NUMBER}')." >&2
  exit 1
}

# Absolute path (#2124): a repo-relative path is unresolvable from an agent worktree cwd,
# and a critic that cannot find the plan may critique an imagined one.
if [[ -n "$PLAN_PATH" && "$PLAN_PATH" != /* ]]; then
  REPO_TOPLEVEL=$(git rev-parse --show-toplevel 2>/dev/null)
  if [ -n "$REPO_TOPLEVEL" ] && [ -f "$REPO_TOPLEVEL/$PLAN_PATH" ]; then
    PLAN_PATH="$REPO_TOPLEVEL/$PLAN_PATH"
  elif [ -f "$PLAN_PATH" ]; then
    PLAN_PATH="$(cd "$(dirname "$PLAN_PATH")" && pwd)/$(basename "$PLAN_PATH")"
  fi
fi

if [ ! -f "$PLAN_PATH" ]; then
  echo "Plan not found: $PLAN_PATH"
  exit 1
fi
```

Pass the absolute `$PLAN_PATH` into SOURCE_FILES and every critic prompt.

## Instructions

### Step 1: Load Context

Read the plan in full, the tracking issue (`gh issue view N --json title,body,comments`), and up
to 5 PRs/issues its Prior Art section cites.

### Step 1.5: Extract and Bundle Source Files

Critics do not read files; they get verified contents, which keeps them from inventing code.
Read every file path the plan references and bundle them, marking missing ones rather than
asking critics to find them:

```
SOURCE_FILES:
--- path/to/file1.py ---
{file contents}
--- path/to/missing.py ---
[FILE NOT FOUND]
```

### Step 2: Structural Checks (Automated)

Run these yourself, no critic needed, and report each with its severity:

| Check | Severity on failure |
|---|---|
| Required sections present and non-empty (the context file declares the repo's mandated sections; otherwise problem, solution, tasks, verification) | BLOCKER |
| Task numbering has no gaps | CONCERN |
| Every `Depends On` points at a valid task; no cycles | BLOCKER |
| Every task has a validation command | CONCERN |
| File paths and Test Impact test paths exist | CONCERN (may be intentionally new) |
| Prerequisites with a check command pass when run | CONCERN |
| Every Success Criterion maps to a task; no No-Go or Rabbit Hole appears as planned work | CONCERN |

### Step 2b: Resume Probe (only if the context file declares a roster barrier)

Run the context file's resume probe. If it finds a reusable incomplete run dir from a crash,
set `RESUMED=1`, reuse that dir's frozen roster, skip triage and roster freeze, and dispatch
only the missing critics in Step 3. Otherwise, and always in the generic case, set `RESUMED=0`.

### Step 2.6: Triage (fresh path only — skip if RESUMED=1)

Choose LITE (1 Consolidated Critic) or FULL (3 critics).

**Force FULL**, with no classifier call, when the plan frontmatter has `appetite: Large` (or the
repo's equivalent) or the plan touches a doctrine path the context file enumerates. A LITE vote
never overrides force-FULL.

Otherwise spawn one `haiku` Agent (`run_in_background: false`) with:

```
Classify this plan's critique depth.
LITE = purely internal, non-doctrine, small scope (one bug fix, one CLI flag, one config key).
FULL = critical paths, cross-component changes, new abstractions, architectural decisions.
Bias to FULL on any ambiguity.
Reply with exactly one line: "LITE: <one-line reason>" or "FULL: <one-line reason>".

PLAN:
{plan frontmatter + first 1000 chars}
```

Set `CRITIQUE_DEPTH` from the reply.

### Step 3a: Fix the Critic Roster

(Skip if RESUMED=1.) Freeze the roster before dispatching any critic; Step 3.5 verifies
membership against it, so dispatching fewer critics can never satisfy completion.

- **LITE** → `["Consolidated Critic"]`
- **FULL** → `["Risk & Robustness", "Scope & Value", "History & Consistency"]`

If the context file declares a roster barrier, create its artifacts exactly as specified (run
dir, frozen `_roster.json` manifest, plan-hash guard). Otherwise hold the names in memory.

### Step 3: War Room (Parallel Critics)

Read [CRITICS.md](CRITICS.md) for the prompt template and each critic's lens. Dispatch the roster
(all members on a fresh run; only uncompleted members on a resume), all calls in one message so
they run concurrently. Each critic gets the full plan, the SOURCE_FILES block, the issue context,
prior-art summaries, and its lens.

Dispatch each critic with `subagent_type: "plan-reviewer"` (pinned to opus at medium effort;
critique is a gate whose misses reach the build). If that agent type is unavailable, use a
general-purpose Agent with `model: "opus"`. Pass `run_in_background: false` explicitly on every
call; an eng session denies a spawn that omits it. Do not pass `name`: a named spawn from inside
a subagent is refused.

**Record the mode**, since independent convergence is the war room's value:

- Roster dispatched as subagents → `CRITIQUE_MODE = independent roster ({M} critics)`.
- Agent tool absent, or every dispatch refused with the spawn-depth error → apply each lens
  yourself in sequence, writing each under its roster name so Step 3.5 still sees the full
  roster, and set `CRITIQUE_MODE = sequential lenses (Agent tool unavailable: {exact error or
  "not in tool list"})`.

The mode goes in the report header and never changes the verdict string.

**Generic completion:** wait for each foreground critic to return its findings.

**With a result-file roster barrier** (context file), each critic writes its findings
atomically: to `{critic_name}.result.md.tmp`, then rename to `{critic_name}.result.md`, ending
with the two-line terminal fence `<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED`.
Completion is then observed on the filesystem whether or not the driver awaited the agents.
Pass each critic its run-dir path and `{critic_name}` per the context file's layout.

Each critic returns 0-3 findings (or `No findings.`):

```
SEVERITY: BLOCKER | CONCERN | NIT
LOCATION: Section name or line reference in the plan
FINDING: What's wrong (1-2 sentences)
SUGGESTION: How to fix it (1-2 sentences)
IMPLEMENTATION NOTE: Required for CONCERN and BLOCKER. The specific guard condition, call
  signature, or gotcha that makes the fix implementable without re-investigation.
```

### Step 3.5: Roster Completion Check (mandatory, runs BEFORE Step 4)

Do not aggregate until every roster member from Step 3a has returned findings, or the
`CRITIQUE INCOMPLETE` fallback below is recorded.

**Generic check:** confirm each named member returned findings (or `No findings.`); re-dispatch
only the missing ones in the foreground, then re-check.

**If the context file declares a membership-gate CLI**, run it against the run dir instead. It
reads the frozen `_roster.json`, checks each member's result file for the terminal fence, prints
`{"complete": bool, "missing": [...], ...}`, and exits non-zero until complete. When it accepts
`--plan-path`, pass it: the gate then also requires each result to quote the real plan, so a
fabricated critique of a nonexistent plan counts as an incomplete (`ungrounded`) member (#2124).

**Bounded re-dispatch.** `MAX_CRITIC_REDISPATCH = 2`: 1 initial dispatch + up to 2
re-dispatches = 3 attempts maximum per critic. Re-dispatches are foreground; never
`run_in_background: true`, which reintroduces the fire-and-forget failure this check exists for.

**Still incomplete after the cap:** do not aggregate or loop. Record, via Step 5.5:

```
MAJOR REWORK (CRITIQUE INCOMPLETE: roster N/M — missing: {names})
```

(`N` completed, `M` roster size.) `MAJOR REWORK` routes back to `/do-plan` (SDLC router guard
G1). The stage always produces a verdict, never an empty exit. Then set the plan-revising lock
per Step 5.6.

**Run-dir cleanup (barrier only):** delete the run dir only on the `complete: true` path; on
the incomplete path, preserve it as evidence of which critics never reported.

### Step 4: Aggregate and Deduplicate

Iterate every roster member fixed in Step 3a (with the barrier, the frozen `_roster.json`, reading
each `{name}.result.md`), never just the files that happen to exist; a missing member is a visible
gap.

1. Collect structural and critic findings.
2. Deduplicate: keep the higher severity and note which critics agreed.
3. Sort BLOCKERs, then CONCERNs, then NITs.
4. When the Skeptic sub-section (of Risk & Robustness, or LITE sub-section A) and the Simplifier
   sub-section (of Scope & Value, or LITE sub-section B) flag the same component, elevate it to
   BLOCKER.
5. Exclude any CONCERN or BLOCKER missing its Implementation Note, logging "Finding [title]
   missing Implementation Note — excluded". Do not re-dispatch for it.

### Step 5: Report

Emit every section header literally; an empty category reads `None.` under its header.

```markdown
# Plan Critique: {plan name}

**Plan**: {plan_path}
**Issue**: #{issue_number} (if applicable)
**Critics**: {roster members} ({LITE or FULL} depth)
**Mode**: {CRITIQUE_MODE}
**Findings**: {N} total ({blockers} blockers, {concerns} concerns, {nits} nits)

## Blockers

### {finding title}
- **Severity**: BLOCKER
- **Critics**: {which critics flagged this}
- **Location**: {section reference}
- **Finding**: {description}
- **Suggestion**: {how to fix}
- **Implementation Note**: {guard condition, call signature, or gotcha}

## Concerns

(same fields, Severity: CONCERN)

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
- **READY TO BUILD (no concerns)** — no CONCERN or BLOCKER findings (NITs allowed).
- **READY TO BUILD (with concerns)** — no BLOCKERs, one or more CONCERNs.
- **NEEDS REVISION** — {N} blockers must be resolved before build.
- **MAJOR REWORK** — fundamental issues; recommend re-planning.
```

### Step 5.5: Finalize — record the verdict (mandatory, self-contained)

Every exit path passes through this step before control returns to a supervisor.

**Generic case:** the printed Step 5 verdict is the output.

**With a verdict-recording substrate** (context file), record the verdict now using the
context file's invocation. On READY TO BUILD, write the CRITIQUE completion stage-marker in the
same block, never as a follow-up; on any other verdict leave the marker `in_progress`. If the
context file declares a durable findings table in the plan, write the aggregated findings into
it before recording the verdict. Never suppress substrate errors.

**A missing context file does not prove there is no substrate (#2419).** If your prompt carries
a `run_id` and `sdlc-tool` is on PATH, a supervisor is tracking the run; record it yourself:

```bash
sdlc-tool verdict record --stage CRITIQUE --verdict "$VERDICT_STRING" --issue-number "$ISSUE_NUMBER" --run-id {run_id}
# READY TO BUILD only — same block, never a follow-up:
sdlc-tool stage-marker --stage CRITIQUE --status completed --issue-number "$ISSUE_NUMBER" --run-id {run_id}
```

`$VERDICT_STRING` is the exact Step 5 verdict. Report a failed write; never continue as though
state landed.

### Step 5.6: Set plan-revising lock (only if the context file declares one)

After recording the verdict, set the lock (context file's invocation) when the verdict is
`NEEDS REVISION`, `MAJOR REWORK`, or `READY TO BUILD (with concerns)`; never for
`READY TO BUILD (no concerns)`. The verdict kind is the only rule: no exemption for
already-revised plans, which would leave the lock permanently inert. Whether a revision has
landed since this verdict is the router's call, not this skill's.

## Outcome Contract

| Verdict | SDLC Action |
|---------|-------------|
| READY TO BUILD (no concerns) | Proceed to `/do-build` |
| READY TO BUILD (with concerns) | Revision pass via `/do-plan`, then re-critique — never `/do-build` directly |
| NEEDS REVISION | Return to `/do-plan` with blocker findings |
| MAJOR REWORK | Return to issue discussion |

The Mode line is part of the contract: `sequential lenses` tells any reader, including one
weighing a critique-cycle cap, that no finding was independently corroborated.

"With concerns" loops through revision and re-critique until a round returns no concerns or the
pipeline's concern re-critique bound is exhausted (the context file names the bound); then the
remaining concerns are accepted on the record and the build proceeds. The revision pass embeds
each concern's Implementation Note in the plan; concerns are not reclassified as defects.
