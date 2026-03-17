# Infrastructure Documentation

This directory contains durable infrastructure knowledge that survives plan archival. Unlike plan documents (which are moved to `docs/plans/shipped/` after completion), INFRA docs remain here as a living reference for current infrastructure state.

## Purpose

INFRA docs record infrastructure decisions, constraints, and state introduced by features. They prevent future plans from starting with zero knowledge about existing infrastructure.

## When to Create an INFRA Doc

Create `docs/infra/{slug}.md` when a plan introduces:

- **New dependencies** (Python packages, npm modules, system tools)
- **New services** (Redis, databases, external APIs)
- **External API integrations** (with rate limits, quotas, auth requirements)
- **Deployment changes** (new environment variables, config files, service definitions)
- **Resource constraints** (cost ceilings, storage limits, compute budgets)

Do NOT create an INFRA doc for:
- Purely internal code changes with no external dependencies
- Documentation-only changes
- Refactoring that doesn't change infrastructure

## Template

```markdown
# {Feature Name} — Infrastructure

## Current State
- [What infra exists today relevant to this work]
- [Dependencies, services, config already in place]

## New Requirements
- [What this plan adds: new deps, services, API keys, config]
- [Resource estimates: API quotas, storage, compute]

## Rules & Constraints
- [Rate limits, cost ceilings, API quotas]
- [Deployment topology requirements]
- [Tool-specific rules (e.g., "never call X without Y")]

## Rollback Plan
- [How to revert infra changes if the feature is rolled back]
```

## Relationship to Plan Sections

INFRA docs complement but do not replace plan sections:

| Plan Section | Purpose | INFRA Doc Purpose |
|-------------|---------|-------------------|
| **Update System** | How to propagate changes across machines | What infrastructure state exists |
| **Agent Integration** | How the agent accesses new functionality | What service constraints apply |
| **Prerequisites** | What must be in place before building | What was in place when shipped |

INFRA docs are about **state and constraints**. Plan sections are about **propagation and exposure**.

## Usage in Planning

The `/do-plan` skill scans this directory during Phase 1 research to discover existing infrastructure constraints that might affect the new plan. Reference relevant INFRA docs in the plan's Solution and Risks sections.
