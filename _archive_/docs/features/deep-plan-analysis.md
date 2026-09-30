# Deep Plan Analysis

Adds four analytical sections to the `/do-plan` skill template and corresponding investigation steps to the planning workflow. These sections ensure plans investigate before proposing solutions, preventing the pattern of repeated fixes that each address a symptom without resolving the root cause.

## New Template Sections

### Prior Art
Search closed issues and merged PRs for related work before proposing solutions. Prevents re-solving problems that already have working implementations or repeating approaches that already failed.

### Data Flow
Trace end-to-end data flow through components the change touches. Critical for multi-component features -- prevents fixes applied at the wrong architectural layer.

### Why Previous Fixes Failed
Conditional section (only when Prior Art search finds prior failed attempts). Analyzes why each attempt failed, looking for root cause patterns: misidentified root cause, fix at wrong layer, symptom vs. cause treatment.

### Architectural Impact
Assess how the change affects broader system architecture: new dependencies, interface changes, coupling effects, data ownership shifts, and reversibility.

## New Investigation Steps (SKILL.md Phase 1)

Three new steps added between blast radius analysis and appetite setting:

1. **Prior art search (step 4)** -- Uses `gh issue list --state closed` and `gh pr list --state merged` to find related work
2. **Data flow trace (step 5)** -- Follow data from entry point through each component to output
3. **Failure analysis (step 6)** -- Analyze why prior attempts failed (conditional on step 4 results)

Original steps 4-6 (set appetite, rough out solution, race condition analysis) are renumbered to 7-9.

## Complexity Scaling

All new sections include skip criteria to avoid burdening trivial changes:
- **Prior Art**: Skip for Small appetite AND greenfield work
- **Data Flow**: Skip for single-file changes or documentation/config-only work
- **Failure Analysis**: Skip if no prior fixes found
- **Architectural Impact**: Skip for isolated changes with no cross-component effects

## Motivation

Issue #309 documented 20+ PRs that each "fixed" stage progress rendering, each addressing a symptom because no plan ever traced the actual data flow or analyzed why previous attempts failed. These sections institutionalize the investigative work that prevents this pattern.

## Related

- [Enhanced Planning](enhanced-planning.md) -- Spike Resolution (Phase 1.5), RFC Review (Phase 2.8), and Infrastructure Documentation added after these analysis sections
- [Race Condition Analysis](race-condition-analysis.md) -- Prior art for adding analytical sections to the plan template (PR #288)
- [Code Impact Finder](code-impact-finder.md) -- Blast radius analysis tool used in step 3
- [Trace & Verify Protocol](trace-and-verify.md) -- Related root cause analysis at bug-fix time (not planning time)

## Files

- `.claude/skills/do-plan/PLAN_TEMPLATE.md` -- Template with new sections
- `.claude/skills/do-plan/SKILL.md` -- Workflow with new investigation steps
