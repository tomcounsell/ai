# SDLC Critique Stage

The CRITIQUE stage sits between PLAN and BUILD in the SDLC pipeline. It validates plans before implementation by running parallel war-room critics and automated structural checks, preventing costly rework from plans with internal contradictions, missing tasks, or architectural gaps.

## Pipeline Position

```
ISSUE -> PLAN -> CRITIQUE -> BUILD -> TEST -> REVIEW -> DOCS -> MERGE
                    |
                    +---(fail)---> PLAN (revision cycle, max 2)
```

## How It Works

When a plan completes, the Observer routes to CRITIQUE instead of BUILD. The `/do-plan-critique` skill runs:

1. **Source file extraction** (Step 1.5): Reads all files referenced in the plan and bundles their contents into a SOURCE_FILES block. Critics receive verified source code inline, preventing hallucination of file names, constants, or file contents.

2. **Structural checks** (Step 2): Automated validation of required sections, task integrity, dependency chains, file path existence, and cross-reference consistency.

3. **War room critics** (Step 3): Six parallel critics (Skeptic, Operator, Archaeologist, Adversary, Simplifier, User) analyze the plan from different perspectives, each returning 0-3 severity-rated findings.

4. **Aggregation** (Steps 4-5): Findings are deduplicated, sorted by severity, and a verdict is issued.

## Verdicts

| Verdict | Pipeline Action |
|---------|----------------|
| READY TO BUILD | Proceed to BUILD stage |
| NEEDS REVISION | Route back to PLAN for revision |
| MAJOR REWORK | Escalate to human (ambiguous outcome) |

## Cycle Limits

The CRITIQUE -> PLAN -> CRITIQUE revision loop is capped at `MAX_CRITIQUE_CYCLES = 2`. After 2 revisions, the pipeline escalates to human review rather than looping indefinitely.

## Source Modules

| Module | Change |
|--------|--------|
| `bridge/pipeline_graph.py` | CRITIQUE edges in PIPELINE_EDGES, STAGE_TO_SKILL, DISPLAY_STAGES |
| `bridge/pipeline_state.py` | CRITIQUE in ALL_STAGES, classify_outcome patterns, critique_cycle_count |
| `models/agent_session.py` | CRITIQUE in SDLC_STAGES |
| `agent/build_pipeline.py` | "critique" in STAGES list |
| `.claude/skills/do-plan-critique/SKILL.md` | Step 1.5 for SOURCE_FILES extraction |
| `.claude/skills/do-plan-critique/CRITICS.md` | SOURCE_FILES block in critic prompt template |
| `.claude/skills/sdlc/SKILL.md` | CRITIQUE row in dispatch table |

## Outcome Classification

The `classify_outcome("CRITIQUE", ...)` method in `PipelineStateMachine` recognizes:
- "ready to build" in output tail -> `"success"`
- "needs revision" in output tail -> `"fail"`
- "major rework" in output tail -> `"ambiguous"` (escalate)

## Related Issues

- Issue #463: SDLC Critique Stage
- Issue #469: Hallucination fix for critique agents
