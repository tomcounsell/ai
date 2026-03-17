# Enhanced Planning

Adds three new phases and supporting infrastructure to the `/do-plan` skill: Spike Resolution (Phase 1.5), RFC Review (Phase 2.8), Infrastructure Documentation, and task validation fields. These enhancements ensure plans validate assumptions before building, collect structured feedback from specialist agents, and track infrastructure state durably.

## Spike Resolution (Phase 1.5)

Time-boxed investigations that validate or invalidate plan assumptions before committing to a build. Runs between Phase 1 (Context Gathering) and Phase 2 (Plan Writing).

- **Methods**: `web-research`, `prototype`, `code-read`
- **Dispatch**: Parallel Agent sub-agents (P-Thread pattern)
- **Appetite limits**: Small (max 2 spikes), Medium (max 4), Large (uncapped)
- **Prototype isolation**: Must use `isolation: "worktree"` to avoid repo pollution
- **Output**: `## Spike Results` section in plan template with per-spike findings

**Skip if**: No verifiable assumptions, or all assumptions require human judgment.

## RFC Review (Phase 2.8)

Specialist critic agents review the draft plan for structural flaws before human review. Runs after plan writing and issue linking.

- **Critics selected by plan characteristics**:
  - All plans: `code-reviewer`
  - Async/concurrent: `async-specialist`
  - External APIs: `api-integration-specialist`
  - Security-sensitive: `security-reviewer`
  - Data model changes: `data-architect`
- **Feedback tiers**: BLOCKER (incorporated immediately), CONCERN (added to plan for human judgment), QUESTION (merged into Open Questions)
- **Output**: `## RFC Feedback` section in plan template

**Skip if**: Small appetite plans (overhead exceeds value).

## Infrastructure Documentation

Durable infrastructure knowledge that survives plan archival. Unlike plan documents (moved to `docs/plans/shipped/`), INFRA docs remain in `docs/infra/` as living references.

- **Created when**: Plan introduces new dependencies, services, external APIs, deployment changes, or resource constraints
- **Scanned during**: Phase 1 research (step 4.7) to discover existing constraints
- **Template sections**: Current State, New Requirements, Rules & Constraints, Rollback Plan
- **Relationship to plan sections**: INFRA docs are about state and constraints; plan sections (Update System, Agent Integration) are about propagation and exposure

See `docs/infra/README.md` for the full template and conventions.

## Task Validation Fields

Two new fields on build tasks in the plan template:

- **Validates**: Test files/patterns that must pass (e.g., `tests/unit/test_component.py`)
- **Informed By**: Spike task IDs with key findings (e.g., `spike-1 (confirmed: API supports batch calls)`)

These create explicit traceability from spike findings to build tasks and from build tasks to their verification criteria.

## Infrastructure Scan (Step 4.7)

New research step in Phase 1 that scans `docs/infra/` for existing infrastructure constraints relevant to the planned work. Ensures plans account for rate limits, API quotas, deployment constraints, and tool rules documented by prior features.

**Skip if**: `docs/infra/` doesn't exist or contains no relevant docs.

## Related

- [Deep Plan Analysis](deep-plan-analysis.md) -- Prior Art, Data Flow, and Failure Analysis sections (steps 4-6)
- [Race Condition Analysis](race-condition-analysis.md) -- Concurrency analysis section (step 9)
- [Plan Prerequisites Validation](plan-prerequisites.md) -- Environment requirement checks before build
- [Code Impact Finder](code-impact-finder.md) -- Blast radius analysis (step 3)

## Files

- `.claude/skills/do-plan/SKILL.md` -- Workflow with new phases 1.5, 2.8, and step 4.7
- `.claude/skills/do-plan/PLAN_TEMPLATE.md` -- Template with Spike Results, RFC Feedback, and Validates/Informed By fields
- `docs/infra/README.md` -- Infrastructure documentation directory conventions
