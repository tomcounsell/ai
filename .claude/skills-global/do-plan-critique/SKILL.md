---
name: do-plan-critique
description: "Use when reviewing a plan before build. Runs 1 (LITE) or 3 (FULL) critics selected by a triage step plus automated structural checks. Triggered by 'critique this plan', 'review the plan', 'war room', or 'do-plan-critique'."
argument-hint: "<plan-path-or-issue-number>"
context: fork
---

# Plan Critique (War Room)

## Stage Marker

At the very start of this skill, write an in_progress marker:

```bash
sdlc-tool stage-marker --stage CRITIQUE --status in_progress --issue-number "$ISSUE_NUMBER"
```

The completion marker is written in **Step 5.5**, co-located with the verdict record so the two can never desync. On a READY TO BUILD verdict, write the completion marker; on any other verdict, leave it `in_progress`. Step 5.5 is mandatory and reached on every exit path — see that step for the self-contained verdict-record + marker block:

```bash
# On READY TO BUILD verdict (written in Step 5.5, immediately after `verdict record`):
sdlc-tool stage-marker --stage CRITIQUE --status completed --issue-number "$ISSUE_NUMBER"
```

Do NOT write the completion marker before the verdict is recorded, and do NOT record an APPROVED/READY verdict without the matching completion marker. They are a single unit.


## What this skill does

Critiques a plan document from a frozen roster of expert perspectives (1 (LITE) or 3 (FULL) critics selected by a triage step) plus automated structural validation. Each critic has a defined lens and returns severity-rated findings. The skill aggregates, deduplicates, and produces a verdict: READY TO BUILD, NEEDS REVISION, or MAJOR REWORK.

## When to load sub-files

- Spawning war room critics → read [CRITICS.md](CRITICS.md) for critic definitions and prompt templates

## Quick start

1. Resolve the plan path from `$ARGUMENTS` (issue number or file path)
2. Read the plan and fetch linked issue/prior art context
3. Run automated structural checks (Step 2)
4. Freeze the critic roster manifest (Step 3a), then dispatch the frozen roster of critics — each writes a result file (Step 3)
5. Gate on the roster membership check before aggregating, then aggregate and output the report (Steps 3.5-5)

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

### Step 2b: Resume Probe

Before triage or roster freeze, check for a reusable incomplete run dir from a prior crash:

```bash
RESUME_DIR=$(critique-resume-probe --plan "$PLAN_PATH" --issue "$ISSUE_NUMBER" 2>/tmp/critique-resume-stale.txt)
PROBE_EXIT=$?
```

If `$PROBE_EXIT == 0` (a reusable dir was found):
- Set `CRITIQUE_RUN_DIR="$RESUME_DIR"` and `RESUMED=1`
- GC any stale-hash sibling dirs printed on stderr: `cat /tmp/critique-resume-stale.txt | xargs -r rm -rf`
- **Skip Step 2.6 (triage) and Step 3a (roster freeze)** — the surviving `_roster.json` defines the chosen path
- Proceed directly to Step 3 (dispatch only missing critics)

If `$PROBE_EXIT != 0` (no reusable dir):
- Set `RESUMED=0`
- Continue to Step 2.6 (triage) → Step 3a (roster freeze) → Step 3 (dispatch all critics)

### Step 2.6: Triage (fresh path only — skip if RESUMED=1)

Determine LITE (1 consolidated critic) or FULL (3 merged critics):

**Deterministic force-FULL** — use FULL without an LLM call if ANY of:
- The plan touches doctrine paths: `config/personas/`, `.claude/skills/`, `.claude/skills-global/`, `agent/sdlc_router.py`, `agent/pipeline_graph.py`, `.claude/hooks/`
- The plan frontmatter has `appetite: Large`

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

### Step 3a: Compute and Freeze Roster Manifest

(Skip this step if RESUMED=1 — the surviving run dir already has `_roster.json` and `.plan_hash`.)

Before dispatching ANY critic, freeze the expected critic roster to a manifest file. This frozen manifest is the membership set that the Step 3.5 gate checks against — the gate cannot be satisfied by dispatching fewer critics than the manifest lists.

1. **Compute the plan hash** for the stale-resume guard:
   ```bash
   PLAN_HASH=$(python -c "from tools.sdlc_verdict import compute_plan_hash; print(compute_plan_hash('$PLAN_PATH') or '')")
   ```

2. **Create the per-run directory** `${CRITIQUE_RUN_DIR}`, defaulting to `.critique-runs/{issue-or-slug}-{timestamp}/`, where `{timestamp}` is a **high-resolution** timestamp (`date +%s%N`, nanoseconds). Create it with `mkdir` **WITHOUT** the `-p` flag so a collision **fails loudly** (non-zero exit) instead of silently reusing a stale run dir's result files:

   ```bash
   ISSUE_OR_SLUG="${ISSUE_NUMBER:-$(basename "$PLAN_PATH" .md)}"
   CRITIQUE_RUN_DIR=".critique-runs/${ISSUE_OR_SLUG}-$(date +%s%N)"
   mkdir "$CRITIQUE_RUN_DIR"   # NO -p: a collision must fail loudly, never reuse a stale run dir
   ```

3. **Write the frozen roster manifest and plan hash**:

   LITE path (CRITIQUE_DEPTH=LITE):
   ```bash
   cat > "$CRITIQUE_RUN_DIR/_roster.json" <<'JSON'
   {"roster": ["Consolidated Critic"], "count": 1}
   JSON
   echo "$PLAN_HASH" > "$CRITIQUE_RUN_DIR/.plan_hash"
   ```

   FULL path (CRITIQUE_DEPTH=FULL):
   ```bash
   cat > "$CRITIQUE_RUN_DIR/_roster.json" <<'JSON'
   {"roster": ["Risk & Robustness", "Scope & Value", "History & Consistency"], "count": 3}
   JSON
   echo "$PLAN_HASH" > "$CRITIQUE_RUN_DIR/.plan_hash"
   ```

This frozen manifest is the **membership set** that the Step 3.5 gate checks against: for every name in `_roster.json`, the corresponding `{name}.result.md` must exist and carry the terminal completion fence. **The gate cannot be satisfied by dispatching fewer critics than the manifest** — under-dispatch leaves a named roster member's result file missing, so the gate reports incomplete.

### Step 3: War Room (Parallel Critics)

Read [CRITICS.md](CRITICS.md) for the full critic definitions and prompt templates.

Dispatch **only critics whose `{name}.result.md` is absent or lacks the terminal fence** (on a fresh run, all roster members; on a resume, only those not yet completed). Read CRITICS.md for the 3 FULL critics and 1 Consolidated Critic (LITE). Each critic gets:
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

### Step 3.5: Roster Membership Barrier (mandatory, runs BEFORE Step 4)

This is the **barrier**. Completion is **observed on the filesystem** — a membership check against the frozen `_roster.json` manifest written in Step 3a — **independent of whether the driver awaited the critics**. You do NOT proceed to Step 4 (aggregation) until the gate reports the full roster complete, OR you record the `CRITIQUE INCOMPLETE` fallback after exhausting the re-dispatch cap.

**1. Invoke the gate.** Call `critique-roster-check` via the Bash tool against the run dir:

```bash
critique-roster-check --run-dir "$CRITIQUE_RUN_DIR"
```

It prints a JSON gate decision and sets its exit code:

```json
{"complete": false, "missing": ["Adversary","User"], "present": ["Skeptic","Operator","Archaeologist","Simplifier","Consistency Auditor"], "roster_count": 7, "completed_count": 5}
```

It exits `0` when `complete: true` (every frozen-roster member wrote a `{name}.result.md` carrying the terminal two-line completion fence), and **non-zero** otherwise. The gate is a **filesystem membership check against the frozen `_roster.json` manifest** — a missing or fence-less result file means that named roster member did not complete. Because completion is read off the filesystem, the barrier holds whether or not the driver chose to await the critic agents.

**2. Bounded re-dispatch.** Define:

```
MAX_CRITIC_REDISPATCH = 2
```

If the gate reports `complete: false`, **re-dispatch ONLY the critics named in `missing`** — re-run each missing critic so it writes its `{name}.result.md` using the exact same atomic `.tmp`→rename + two-line terminal-fence convention from Step 3 (`<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED` as the last two lines). Then re-run `critique-roster-check`. Repeat up to `MAX_CRITIC_REDISPATCH` rounds.

The total attempt budget is pinned explicitly: **1 initial dispatch (Step 3) + up to 2 re-dispatches = 3 attempts maximum per critic.** There is **NO unbounded retry loop** — the cap is named and fixed.

> **CRITICAL — re-dispatch is FOREGROUND.** The re-dispatch block must NEVER contain `run_in_background: true`. Re-run each missing critic in the foreground. Spawn mode is irrelevant to correctness (the gate is the artifact, not the await), and a background re-dispatch would re-introduce exactly the fire-and-forget assumption this barrier replaces.

**3. STOP-grade verdict on a still-incomplete roster.** If the roster is **STILL incomplete after `MAX_CRITIC_REDISPATCH` rounds**, do NOT aggregate and do NOT loop further. Jump to **Step 5.5** and record the verdict string:

```
MAJOR REWORK (CRITIQUE INCOMPLETE: roster N/M — missing: {names})
```

Substitute `N` = the gate's `completed_count`, `M` = the gate's `roster_count`, and `{names}` = the gate's `missing` list (the critic names that never reported). Because the substring `MAJOR REWORK` matches the SDLC router's guard **G1** verbatim, this is a **router-consumable STOP** that routes back to `/do-plan` — the human sees exactly which critics never reported. The stage **ALWAYS produces a verdict**; it never returns empty and never lingers at `in_progress`. Then set the `plan_revising` lock per **Step 5.6** (a `CRITIQUE INCOMPLETE` verdict is revision-grade).

**Invariants (must hold on every path):**

- **Never aggregate a partial set.** Step 4 runs only after the gate reports `complete: true`.
- **Never record an empty verdict.** Every exit path records a concrete verdict string in Step 5.5.
- **The verdict (Step 5.5) is recorded only when the gate reports `complete: true`, OR as the `CRITIQUE INCOMPLETE` fallback after the re-dispatch cap.** There is no third path.
- **The incomplete-roster STOP is a recorded `MAJOR REWORK` verdict (G1-consumable), not a silent exit.**
- **Only after the gate reports `complete: true` do you proceed to Step 4.**

**Run-dir cleanup gating.** After Step 5.5/5.6, clean up `${CRITIQUE_RUN_DIR}` **ONLY on the `complete: true` path**. On the incomplete / `CRITIQUE INCOMPLETE` path, **PRESERVE** `${CRITIQUE_RUN_DIR}` for forensics — the partial/missing result files are the diagnostic evidence of which critics never reported, and deleting them would destroy exactly what the STOP exists to surface.

```bash
# After the verdict is recorded (Step 5.5/5.6): clean up ONLY on the complete path.
# On the incomplete path, PRESERVE the run dir for forensics — do NOT delete it.
case "$VERDICT_STRING" in
  *"CRITIQUE INCOMPLETE"*)
    : ;;  # incomplete path — PRESERVE "$CRITIQUE_RUN_DIR" as forensic evidence
  *)
    # gate reported complete: true — safe to remove the ephemeral run dir
    rm -rf "$CRITIQUE_RUN_DIR" ;;
esac
```

### Step 4: Aggregate and Deduplicate

The Step 3.5 gate has confirmed every roster member completed. Now aggregate from the result files.

**Aggregation invariant (mandatory): iterate every roster member in `${CRITIQUE_RUN_DIR}/_roster.json` (the frozen manifest) and read each roster member's `{name}.result.md`.** Name and read EVERY roster member listed in the manifest — do NOT "aggregate from the result files that are present" and do NOT skip a member because its file looks absent. A missing file at this point is a **visible gap** (the gate should already have caught it as incomplete and routed to re-dispatch or `CRITIQUE INCOMPLETE`), never a member silently dropped from aggregation. Reading by iterating the manifest — rather than by globbing whatever files happen to exist — is what guarantees an omitted critic surfaces as a gap instead of vanishing.

1. Collect all findings (structural + critic), reading each roster member's `{name}.result.md` by iterating `_roster.json`
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

### Step 5.5: Finalize — record verdict AND write completion marker (mandatory, self-contained)

This step is **mandatory and reached on EVERY exit path** — every verdict (READY TO BUILD, NEEDS REVISION, MAJOR REWORK) must pass through it. There is no path out of this skill that skips Step 5.5. Do not return control to a supervisor before completing it.

After printing the verdict (Step 5), record it on the PM session so the SDLC router's Legal Dispatch Guards (G1, G5) can consume it. On a READY TO BUILD verdict, **co-locate** the completion stage-marker write with the verdict record so the verdict and the marker can never desync:

```bash
# 1. Record the verdict (ALL verdicts) — mandatory.
# Always pass --issue-number "$ISSUE_NUMBER" (quoted); ISSUE_NUMBER was
# unconditionally assigned and validated in Plan Resolution (#1731).
sdlc-tool verdict record --stage CRITIQUE \
  --verdict "$VERDICT_STRING" --issue-number "$ISSUE_NUMBER"

# 2. On a READY TO BUILD verdict ONLY, write the completion marker in the
#    SAME block, immediately after the verdict record. Verdict + marker are a
#    single unit: never record one without the other on the READY path.
# NOTE: do NOT suppress with 2>/dev/null || true — a marker failure must
# surface as a visible non-zero exit so the supervisor sees the error (#1731).
case "$VERDICT_STRING" in
  *"READY TO BUILD"*)
    sdlc-tool stage-marker --stage CRITIQUE --status completed \
      --issue-number "$ISSUE_NUMBER"
    ;;
esac
```

Where `$VERDICT_STRING` is the exact verdict string emitted in Step 5 (e.g. `"NEEDS REVISION"`, `"READY TO BUILD (with concerns)"`). **Always pass `--issue-number "$ISSUE_NUMBER"` (quoted)** — it is the authoritative session selector and guarantees the verdict lands on the same session the router reads for that issue (`sdlc-local-{N}` or the bridge PM session that owns the issue). The variable is unconditionally assigned and validated in Plan Resolution — it is always a positive integer by the time Step 5.5 runs. A forked subagent that inherited a parent's env-var session is protected because the Plan Resolution block clobbers any inherited value and asserts a positive integer (#1731/#1671/#1672).

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
  --issue-number "$ISSUE_NUMBER"
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

- v1.5.0 (2026-06-16): Triage-first LITE/FULL routing (1 consolidated vs 3 merged critics), crash-resume via critique-resume-probe, first-pass Implementation Note requirement (no re-run loop) (#1714)
- v1.4.0 (2026-06-16): Replace fire-and-forget `run_in_background` critic spawn + prose await with an artifact-based roster barrier: each critic atomically writes a result file ending in a two-line terminal fence (`<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED`); synthesis gates on a filesystem membership check against a frozen roster manifest, run before aggregation; incomplete roster after a bounded re-dispatch cap records `MAJOR REWORK (CRITIQUE INCOMPLETE)` (#1690)
- v1.3.0 (2026-06-13): Add explicit Step 3.5 "Wait and Collect" barrier (block on all six background critics before aggregating); make Step 5.5 a mandatory, self-contained verdict-record + completion-marker block reached on every exit path; reinforce the Stage Marker note so the verdict and marker cannot desync (#1654)
- v1.2.0 (2026-04-07): Fix Step 5 Verdict template to show both READY TO BUILD variants so critics output the correct form for SDLC routing
- v1.1.0 (2026-03-23): Add SOURCE_FILES inline context to prevent critic hallucination (Step 1.5)
- v1.0.0 (2026-03-21): Initial — war room critique with six parallel critics + structural checks
