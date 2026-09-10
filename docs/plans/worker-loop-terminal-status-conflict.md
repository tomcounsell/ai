---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-09
tracking: https://github.com/tomcounsell/ai/issues/3253
last_comment_id:
---

# Worker loop dies on terminal-status conflict in the session-completion `finally`

## Problem

<!-- skeleton -->

## Freshness Check

<!-- skeleton -->

## Prior Art

<!-- skeleton -->

## Research

<!-- skeleton -->

## Data Flow

<!-- skeleton -->

## Why Previous Fixes Failed

<!-- skeleton -->

## Architectural Impact

<!-- skeleton -->

## Appetite

<!-- skeleton -->

## Prerequisites

<!-- skeleton -->

## Solution

<!-- skeleton -->

## Failure Path Test Strategy

<!-- skeleton -->

## Test Impact

<!-- skeleton -->

## Rabbit Holes

<!-- skeleton -->

## Risks

<!-- skeleton -->

## Race Conditions

<!-- skeleton -->

## No-Gos (Out of Scope)

<!-- skeleton -->

## Update System

<!-- skeleton -->

## Agent Integration

<!-- skeleton -->

## Documentation

<!-- skeleton -->

## Success Criteria

<!-- skeleton -->

## Team Orchestration

<!-- skeleton -->

## Step by Step Tasks

<!-- skeleton -->

## Verification

<!-- skeleton -->

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Structural Checks | The plan document is an unwritten skeleton: all 21 section bodies (Problem, Freshness Check, Prior Art, Research, Data Flow, Why Previous Fixes Failed, Architectural Impact, Appetite, Prerequisites, Solution, Failure Path Test Strategy, Test Impact, Rabbit Holes, Risks, Race Conditions, No-Gos, Update System, Agent Integration, Documentation, Success Criteria, Team Orchestration, Step by Step Tasks, Verification, Open Questions) contain only the literal placeholder `<!-- skeleton -->`. The prior /do-plan dispatch (2026-09-09) never completed; PLAN is still `in_progress`. | pending | Re-run `/do-plan 3253` to author the plan body. Nothing is salvageable from the current file except the frontmatter (`tracking: .../issues/3253`, `appetite: Small`, `type: bug`) and the H1 title; the war room was not dispatched because there is no content to ground a critic read against. |
| BLOCKER | Structural Checks | No `## Step by Step Tasks` content exists, so there is no task numbering, no `Depends On` graph, and no per-task validation command. A build dispatch against this document would have zero executable instructions and would improvise the fix. | pending | The plan must enumerate numbered tasks each carrying a validation command, per the repo plan template. Until tasks exist, guard G7 (`plan_revising`) must stay set so `/do-build` cannot be dispatched. |
| BLOCKER | Structural Checks | The four repo-mandated sections are all placeholders: `## Documentation` (needs a checkbox task with a `docs/features/` path), `## Update System` (needs a `scripts/update/migrations.py` disposition — relevant here because `AgentSession` is a Popoto model and the fix touches its status transitions), `## Agent Integration` (MCP exposure disposition), and `## Test Impact` (per-test UPDATE/DELETE/REPLACE dispositions). | pending | Each of the four sections needs a substantive body. For `## Update System`, state explicitly whether the terminal-status guard changes any Popoto field/index on `AgentSession`; if it does not, say so and why no migration is required rather than leaving the section empty. |
| BLOCKER | Structural Checks | No `## Solution` and no `## Verification` content: the root cause at `agent_session_queue.py:3028` (unguarded retry on a terminal-status conflict that strands every session for the worker_key) has no stated fix, no guard condition, and no acceptance check. Success Criteria are also empty, so no criterion maps to any task. | pending | The Solution must name the exact guard at the conflicting write site (which statuses count as terminal, whether the conflict is swallowed, logged, or escalated) and the Verification section must carry a failure-path test that reproduces a terminal-status conflict and asserts the worker loop survives and continues draining other sessions for the same worker_key. |

---

## Open Questions

<!-- skeleton -->
