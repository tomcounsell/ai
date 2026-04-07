# SDLC Critique Stage

The CRITIQUE stage sits between PLAN and BUILD in the SDLC pipeline. It validates plans before implementation by running parallel war-room critics and automated structural checks, preventing costly rework from plans with internal contradictions, missing tasks, or architectural gaps.

## Pipeline Position

```
ISSUE -> PLAN -> CRITIQUE -> BUILD -> TEST -> REVIEW -> DOCS -> MERGE
                    |
                    +---(NEEDS REVISION)---> PLAN (revision cycle, max 2)
                    |
                    +---(READY TO BUILD, with concerns)---> PLAN (revision pass) -> BUILD
```

## How It Works

When a plan completes, the Observer routes to CRITIQUE instead of BUILD. The `/do-plan-critique` skill runs:

1. **Source file extraction** (Step 1.5): Reads all files referenced in the plan and bundles their contents into a SOURCE_FILES block. Critics receive verified source code inline, preventing hallucination of file names, constants, or file contents.

2. **Structural checks** (Step 2): Automated validation of required sections, task integrity, dependency chains, file path existence, and cross-reference consistency.

3. **War room critics** (Step 3): Six parallel critics (Skeptic, Operator, Archaeologist, Adversary, Simplifier, User) analyze the plan from different perspectives, each returning 0-3 severity-rated findings.

4. **Aggregation** (Steps 4-5): Findings are deduplicated, sorted by severity, and a verdict is issued.

## Finding Format

Each critic finding includes up to five fields:

```
SEVERITY: BLOCKER | CONCERN | NIT
LOCATION: Section name or line reference in the plan
FINDING: What's wrong (1-2 sentences)
SUGGESTION: How to fix it (1-2 sentences)
IMPLEMENTATION NOTE: [Required for CONCERN/BLOCKER. Exempt for NIT.]
  The specific guard condition, call signature, or gotcha that makes this finding
  implementable without re-investigation.
```

The **Implementation Note** field is enforced structurally: any CONCERN or BLOCKER finding without a non-empty Implementation Note is flagged and returned to the critic before the verdict is issued. NITs are exempt.

**Why this matters:** Critique findings at headline level are often correct but underdetermined. Example: "add `.add_done_callback()` to log error on task exit" is correct but incomplete. The necessary Implementation Note is: `lambda t: logger.error(..., t.exception()) if not t.cancelled() else None` — calling `t.exception()` on a cancelled task raises `CancelledError`, so the guard is required or the callback crashes on normal shutdown.

## Verdicts

| Verdict | Meaning | Pipeline Action |
|---------|---------|----------------|
| READY TO BUILD (no concerns) | Zero BLOCKER or CONCERN findings | Proceed directly to BUILD stage |
| READY TO BUILD (with concerns) | CONCERN findings exist (no BLOCKERs) | Trigger revision pass via `/do-plan` before BUILD |
| NEEDS REVISION | BLOCKER findings exist | Route back to PLAN with blocker findings |
| MAJOR REWORK | Fundamental issues found | Escalate to human (ambiguous outcome) |

## Concern-Triggered Revision Pass

When the verdict is **READY TO BUILD (with concerns)**, the SDLC router dispatches `/do-plan` with a directive to apply the concern findings to the plan text. This is a **plan clarity step**, not a defect fix:

- CONCERNs are not reclassified as blockers — they remain acknowledged risks
- The revision pass embeds each concern's Implementation Note into the plan so the builder has unambiguous implementation guidance without re-investigation
- After the revision pass completes, the plan frontmatter is updated with `revision_applied: true`

The `revision_applied` flag is the mechanism for distinguishing "CRITIQUE complete, revision pending" from "CRITIQUE complete, revision done":

| State | `revision_applied` flag | SDLC Router Action |
|-------|------------------------|-------------------|
| Critique done, concerns found, no revision yet | absent/false | Row 4b: dispatch `/do-plan` revision pass |
| Critique done, revision pass complete | `revision_applied: true` | Row 4c: dispatch `/do-build` |
| Critique done, zero concerns | N/A | Row 4a: dispatch `/do-build` directly |

## Cycle Limits

The CRITIQUE -> PLAN -> CRITIQUE revision loop (for NEEDS REVISION verdicts) is capped at `MAX_CRITIQUE_CYCLES = 2`. After 2 revisions, the pipeline escalates to human review rather than looping indefinitely.

The concern-triggered revision pass (READY TO BUILD with concerns) runs once and does not re-enter CRITIQUE — the `revision_applied` flag ensures it is never repeated.

## Propagation Check (do-plan integration)

The `do-plan` skill adds a **Phase 2.6 Propagation Check** after all tasks are written and before the plan is committed. This check cross-references task bullets against the Technical Approach (or spike findings) to catch stale implementation assumptions before they reach the critic stage.

**Common failure pattern:** After spike-2 confirms `json.dumps()` is the correct encoding, a task bullet still says "msgpack-encoded payload". The propagation check flags and corrects this before commit.

## Source Modules

| Module | Change |
|--------|--------|
| `bridge/pipeline_graph.py` | CRITIQUE edges in PIPELINE_EDGES, STAGE_TO_SKILL, DISPLAY_STAGES |
| `bridge/pipeline_state.py` | CRITIQUE in ALL_STAGES, classify_outcome patterns, critique_cycle_count |
| `models/agent_session.py` | CRITIQUE in SDLC_STAGES |
| `agent/build_pipeline.py` | "critique" in STAGES list |
| `.claude/skills/do-plan-critique/SKILL.md` | Finding format, Implementation Note field, Outcome Contract, structural check |
| `.claude/skills/do-plan-critique/CRITICS.md` | SOURCE_FILES block in critic prompt template |
| `.claude/skills/sdlc/SKILL.md` | Row 4a/4b/4c dispatch split, concern-triggered revision path |
| `.claude/skills/do-plan/SKILL.md` | Phase 2.6 Propagation Check |
| `.claude/skills/do-plan/PLAN_TEMPLATE.md` | Critique Results table with Implementation Note column |
| `config/personas/project-manager.md` | Hard gate rule: CRITIQUE mandatory after PLAN (in-repo fallback) |
| `agent/sdk_client.py` line 1611 | Stage list injection includes CRITIQUE: `<PLAN\|CRITIQUE\|BUILD\|...>` |

## Gate Enforcement

The CRITIQUE gate is enforced at two levels so it cannot be silently bypassed:

1. **PM persona** (`config/personas/project-manager.md`): Hard rule text in the PM system prompt
   states explicitly that there is no path from PLAN to BUILD without CRITIQUE. Loaded as the
   in-repo fallback when `~/Desktop/Valor/personas/project-manager.md` is absent (dev machines).
   The private overlay should include these same rules.

2. **Python stage list** (`agent/sdk_client.py` line 1611): The PM dispatch injection string
   lists valid stages as `<PLAN|CRITIQUE|BUILD|TEST|PATCH|REVIEW|DOCS>`. CRITIQUE is structurally
   present in the canonical sequence at the Python level — no persona text can omit it.

## Outcome Classification

The `classify_outcome("CRITIQUE", ...)` method in `PipelineStateMachine` recognizes:
- "ready to build" in output tail -> `"success"` (routes to Row 4a, 4b, or 4c based on concern count and revision_applied flag)
- "needs revision" in output tail -> `"fail"`
- "major rework" in output tail -> `"ambiguous"` (escalate)

## Related Issues

- Issue #463: SDLC Critique Stage
- Issue #469: Hallucination fix for critique agents
- Issue #472: Add CRITIQUE stage to SDLC pipeline between PLAN and BUILD
- Issue #802: Enforce CRITIQUE and REVIEW gates in PM persona
- Issue #779: SDLC Skill Gaps — Propagation Check, Shallow Critique Findings, No Revision Pass
