---
name: do-plan-critique
description: "Use when reviewing a plan before build. Spawns parallel war-room critics (Skeptic, Operator, Archaeologist, Adversary, Simplifier, User) plus automated structural checks. Triggered by 'critique this plan', 'review the plan', 'war room', or 'do-plan-critique'."
argument-hint: "<plan-path-or-issue-number>"
context: fork
---

# Plan Critique (War Room)

## Stage Marker

At the very start of this skill, write an in_progress marker:

```bash
sdlc-tool stage-marker --stage CRITIQUE --status in_progress --issue-number {issue_number} 2>/dev/null || true
```

The completion marker is written in **Step 5.5**, co-located with the verdict record so the two can never desync. On a READY TO BUILD verdict, write the completion marker; on any other verdict, leave it `in_progress`. Step 5.5 is mandatory and reached on every exit path — see that step for the self-contained verdict-record + marker block:

```bash
# On READY TO BUILD verdict (written in Step 5.5, immediately after `verdict record`):
sdlc-tool stage-marker --stage CRITIQUE --status completed --issue-number {issue_number} 2>/dev/null || true
```

Do NOT write the completion marker before the verdict is recorded, and do NOT record an APPROVED/READY verdict without the matching completion marker. They are a single unit.


## What this skill does

Critiques a plan document from six expert perspectives plus automated structural validation. Each critic has a defined lens and returns severity-rated findings. The skill aggregates, deduplicates, and produces a verdict: READY TO BUILD, NEEDS REVISION, or MAJOR REWORK.

## When to load sub-files

- Spawning war room critics → read [CRITICS.md](CRITICS.md) for critic definitions and prompt templates

## Quick start

1. Resolve the plan path from `$ARGUMENTS` (issue number or file path)
2. Read the plan and fetch linked issue/prior art context
3. Run automated structural checks (Step 2)
4. Spawn six parallel critics with the plan text (Step 3)
5. Aggregate findings and output the report (Steps 4-5)

## Plan Resolution

Resolve the plan document path from `$ARGUMENTS`:

```bash
ARG="$ARGUMENTS"

# If argument is a number, resolve from GitHub issue
if [[ "$ARG" =~ ^#?[0-9]+$ ]]; then
  ISSUE_NUM="${ARG#\#}"
  PLAN_PATH=$(gh issue view "$ISSUE_NUM" --json body -q '.body' | grep -oP '(?<=docs/plans/)[^\s)]+\.md' | head -1)
  if [ -n "$PLAN_PATH" ]; then
    PLAN_PATH="docs/plans/$PLAN_PATH"
  fi
fi

# If argument is a path, use directly
if [[ "$ARG" == *.md ]]; then
  PLAN_PATH="$ARG"
fi

# Verify plan exists
if [ ! -f "$PLAN_PATH" ]; then
  echo "Plan not found: $PLAN_PATH"
  exit 1
fi
```

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
Verify these sections exist and are non-empty (per CLAUDE.md):
- `## Documentation`
- `## Update System`
- `## Agent Integration`
- `## Test Impact`

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

### Step 3a: Compute and Freeze Roster Manifest

Before dispatching ANY critic, compute the expected critic roster and **freeze it to a manifest file**. This frozen manifest is the membership set that the Step 3.5 gate checks against — the gate cannot be satisfied by dispatching fewer critics than the manifest lists.

1. **Compute the roster** from CRITICS.md's "Critic Selection" rules:
   - All **seven** critics by default (Skeptic, Operator, Archaeologist, Adversary, Simplifier, User, Consistency Auditor).
   - **Six** critics when Archaeologist + User are skipped for a Small, purely-internal plan with no prior-art section (per CRITICS.md "Critic Selection").

2. **Create the per-run directory** `${CRITIQUE_RUN_DIR}`, defaulting to `.critique-runs/{issue-or-slug}-{timestamp}/`, where `{timestamp}` is a **high-resolution** timestamp (`date +%s%N`, nanoseconds). Create it with `mkdir` **WITHOUT** the `-p` flag so a collision **fails loudly** (non-zero exit) instead of silently reusing a stale run dir's result files:

   ```bash
   ISSUE_OR_SLUG="${ISSUE_NUMBER:-$(basename "$PLAN_PATH" .md)}"
   CRITIQUE_RUN_DIR=".critique-runs/${ISSUE_OR_SLUG}-$(date +%s%N)"
   mkdir "$CRITIQUE_RUN_DIR"   # NO -p: a collision must fail loudly, never reuse a stale run dir
   ```

3. **Write the frozen roster manifest** `${CRITIQUE_RUN_DIR}/_roster.json` **BEFORE any critic is dispatched** — a JSON object with the frozen list of expected critic names and the count:

   ```bash
   cat > "$CRITIQUE_RUN_DIR/_roster.json" <<'JSON'
   {"roster": ["Skeptic","Operator","Archaeologist","Adversary","Simplifier","User","Consistency Auditor"], "count": 7}
   JSON
   ```

   (Drop `"Archaeologist"` and `"User"` and set `"count": 6` on the Small purely-internal skip path.)

This frozen manifest is the **membership set** that the Step 3.5 gate checks against: for every name in `_roster.json`, the corresponding `{name}.result.md` must exist and carry the terminal completion fence. **The gate cannot be satisfied by dispatching fewer critics than the manifest** — under-dispatch leaves a named roster member's result file missing, so the gate reports incomplete.

### Step 3: War Room (Parallel Critics)

Read [CRITICS.md](CRITICS.md) for the full critic definitions and prompt templates.

Dispatch **all critics in the frozen roster** (six or seven, per the `_roster.json` written in Step 3a) using the Agent tool. Each critic gets:
- The full plan text
- The SOURCE_FILES block (verified file contents from Step 1.5)
- The issue context (if available)
- Prior art summaries (if fetched)
- Their specific lens and instructions from CRITICS.md
- The `${CRITIQUE_RUN_DIR}` path and its own `{critic_name}` so it knows where to write its result file

Each critic is a general-purpose Agent with a focused prompt. Use `model: "sonnet"` for each critic — fast enough for 0-3 findings, saves cost.

**Each critic writes its findings to a result file** `${CRITIQUE_RUN_DIR}/{critic_name}.result.md`:
- The critic writes its **findings body FIRST** — 0-3 findings (in the format below) or the literal `No findings.`
- As its **FINAL action**, the critic appends a **two-line terminal completion fence**: the unique delimiter line `<<<CRITIQUE-RESULT-COMPLETE>>>` as the penultimate non-empty line, immediately followed by `STATUS: COMPLETED` as the last non-empty line.
- The write **MUST be atomic**: write the full content to `${CRITIQUE_RUN_DIR}/{critic_name}.result.md.tmp`, then **rename** it to `${CRITIQUE_RUN_DIR}/{critic_name}.result.md`. The `.tmp` file and the canonical `.result.md` are both inside `${CRITIQUE_RUN_DIR}` (same filesystem), so the rename is **atomic** — a partial or truncated file is **never observed** at the canonical path, and a re-dispatched critic's overwrite can never expose a half-written file.

**Foreground vs. background dispatch is now a LATENCY preference only — it is NOT load-bearing for correctness.** The barrier is the **result-file membership check** in Step 3.5 (each named roster member's `{name}.result.md` must exist with the terminal completion fence), not whether the driver awaited the agents. Foreground single-message dispatch is recommended for latency, but completion is observed on the filesystem regardless of spawn mode. **Future readers: never re-introduce a "the harness awaits, so we're safe" prose-await dependency — that fire-and-forget assumption is exactly the bug this barrier replaces. The gate is the artifact, not the driver's await.**

Each critic returns **0-3 findings** (written into its result-file body) in this format:

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

### Step 3.5: Wait and Collect (mandatory)

The six critics from Step 3 were spawned with `run_in_background: true`. Before doing ANY aggregation, you MUST **wait and collect**: block on every one of the six background critic agents and retrieve each critic's complete findings.

- Poll/await all six background agents until each has returned (use the Agent tool's blocking retrieval, e.g. `TaskOutput(block: true)` per critic, or await every background task). Do not proceed while any critic is still running.
- Collect the full findings text from all six. A critic that returned zero findings still counts as collected — record it as "0 findings", do not skip it.
- If a critic failed or returned nothing retrievable, re-spawn it and wait again. Never aggregate a partial set.

**This skill owns finalization end to end.** It does NOT yield to a supervisor for aggregation or verdict recording. The wait-and-collect barrier here is precisely the synchronization that the old "After all critics complete" wording assumed but never enforced — without it, Steps 4, 5, 5.5 silently never run and the critique stalls at `in_progress`. Only after all six are collected do you proceed to Step 4.

### Step 4: Aggregate and Deduplicate

The Step 3.5 gate has confirmed every roster member completed. Now aggregate from the result files.

**Aggregation invariant (mandatory): iterate every roster member in `${CRITIQUE_RUN_DIR}/_roster.json` (the frozen manifest) and read each roster member's `{name}.result.md`.** Name and read EVERY roster member listed in the manifest — do NOT "aggregate from the result files that are present" and do NOT skip a member because its file looks absent. A missing file at this point is a **visible gap** (the gate should already have caught it as incomplete and routed to re-dispatch or `CRITIQUE INCOMPLETE`), never a member silently dropped from aggregation. Reading by iterating the manifest — rather than by globbing whatever files happen to exist — is what guarantees an omitted critic surfaces as a gap instead of vanishing.

1. Collect all findings (structural + critic), reading each roster member's `{name}.result.md` by iterating `_roster.json`
2. **Deduplicate**: If two critics flagged the same issue, keep the higher-severity version and note which critics agreed
3. **Sort by severity**: BLOCKERs first, then CONCERNs, then NITs
4. **Cross-validate**: If the Skeptic and Simplifier both flagged the same component, elevate to BLOCKER if not already
5. **Structural Implementation Note check** — for each finding with SEVERITY = CONCERN or BLOCKER:
   - If IMPLEMENTATION NOTE is missing or empty: downgrade the finding to NEEDS_REVISION and report: "Finding [title] missing Implementation Note — returned to critic for revision"
   - Re-run that critic with the finding and a directive to add a concrete Implementation Note before proceeding
   - Only issue the final verdict after all CONCERN/BLOCKER findings have a non-empty Implementation Note

### Step 5: Report

Emit every section header literally; empty categories emit '## Blockers\n\nNone.' — do not omit the header.

Output the final report in this format:

```markdown
# Plan Critique: {plan name}

**Plan**: {plan_path}
**Issue**: #{issue_number} (if applicable)
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
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

### Step 5.5: Finalize — record verdict AND write completion marker (mandatory, self-contained)

This step is **mandatory and reached on EVERY exit path** — every verdict (READY TO BUILD, NEEDS REVISION, MAJOR REWORK) must pass through it. There is no path out of this skill that skips Step 5.5. Do not return control to a supervisor before completing it.

After printing the verdict (Step 5), record it on the PM session so the SDLC router's Legal Dispatch Guards (G1, G5) can consume it. On a READY TO BUILD verdict, **co-locate** the completion stage-marker write with the verdict record so the verdict and the marker can never desync:

```bash
# 1. Record the verdict (ALL verdicts) — mandatory.
sdlc-tool verdict record --stage CRITIQUE \
  --verdict "$VERDICT_STRING" --issue-number $ISSUE_NUMBER

# 2. On a READY TO BUILD verdict ONLY, write the completion marker in the
#    SAME block, immediately after the verdict record. Verdict + marker are a
#    single unit: never record one without the other on the READY path.
case "$VERDICT_STRING" in
  *"READY TO BUILD"*)
    sdlc-tool stage-marker --stage CRITIQUE --status completed \
      --issue-number $ISSUE_NUMBER 2>/dev/null || true
    ;;
esac
```

Where `$VERDICT_STRING` is the exact verdict string emitted in Step 5 (e.g. `"NEEDS REVISION"`, `"READY TO BUILD (with concerns)"`). **Always pass `--issue-number $ISSUE_NUMBER` when the issue number is known** — it is the authoritative session selector and guarantees the verdict lands on the same session the router reads for that issue (`sdlc-local-{N}` or the bridge PM session that owns the issue). Only if `$ISSUE_NUMBER` is genuinely unknown, omit the flag; the recorder then falls back to the `VALOR_SESSION_ID` / `AGENT_SESSION_ID` env-var session as a *last resort* (subordinate to `--issue-number`), and the artifact_hash will be None. A forked subagent that inherited a parent's env-var session must still pass `--issue-number` so its verdict is not diverted to the parent's session (#1671/#1672).

The recorder exits non-zero on failure (e.g. Redis unreachable) so the operator sees the error in their session log, but it still prints `{}` to stdout for callers parsing JSON. A failed recording surfaces loudly; it does not silently corrupt verdict state. On a non-READY verdict, leave the CRITIQUE marker at `in_progress` (the router's row 3 / row 2b handle re-routing).

### Step 5.6: Set plan-revising lock (mandatory when revision is needed)

After recording the verdict, set the `plan_revising` lock on the PM session whenever the verdict requires a revision pass AND `revision_applied` is not already set in the plan frontmatter. This lock activates guard G7 in the SDLC router, which blocks `/do-build` until `/do-plan` completes the revision and clears the lock.

Set the lock when the verdict is one of:
- `NEEDS REVISION`
- `MAJOR REWORK`
- `READY TO BUILD (with concerns)` — and `revision_applied` is not already `true` in the plan frontmatter

```bash
# Set plan_revising lock after verdict record, when revision is needed
sdlc-tool meta-set --key plan_revising --value true \
  --issue-number $ISSUE_NUMBER 2>/dev/null || true
```

**Do NOT set the lock** when the verdict is `READY TO BUILD (no concerns)` — no revision pass is needed and the lock would incorrectly block build dispatch.

**Do NOT set the lock** when `revision_applied: true` is already in the plan frontmatter — the revision has already been applied and the lock would be immediately self-healed by G7 anyway.

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

## Version history

- v1.4.0 (2026-06-16): Replace fire-and-forget `run_in_background` critic spawn + prose await with an artifact-based roster barrier: each critic atomically writes a result file ending in a two-line terminal fence (`<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED`); synthesis gates on a filesystem membership check against a frozen roster manifest, run before aggregation; incomplete roster after a bounded re-dispatch cap records `MAJOR REWORK (CRITIQUE INCOMPLETE)` (#1690)
- v1.3.0 (2026-06-13): Add explicit Step 3.5 "Wait and Collect" barrier (block on all six background critics before aggregating); make Step 5.5 a mandatory, self-contained verdict-record + completion-marker block reached on every exit path; reinforce the Stage Marker note so the verdict and marker cannot desync (#1654)
- v1.2.0 (2026-04-07): Fix Step 5 Verdict template to show both READY TO BUILD variants so critics output the correct form for SDLC routing
- v1.1.0 (2026-03-23): Add SOURCE_FILES inline context to prevent critic hallucination (Step 1.5)
- v1.0.0 (2026-03-21): Initial — war room critique with six parallel critics + structural checks
