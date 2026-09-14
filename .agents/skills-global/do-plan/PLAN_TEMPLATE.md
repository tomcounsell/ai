---
status: Planning
type: chore
appetite: Small
tracking: null
---

# Plan title

## Problem

Describe the observed problem, affected users, and evidence from current code.

## Solution

Describe the intended behavior, approach, and important tradeoffs.

## Scope and non-goals

State what this change covers and what it deliberately excludes.

## Relevant files

Identify actual implementation, entrypoints, tests, and documentation.

## Prerequisites

Include only real dependencies; resolve them before implementation.

## Step by Step Tasks

List concrete work in dependency order, with observable completion criteria.
Use direct implementation by default; delegate only when useful and authorized.

## Verification

Replace this guidance with executable checks and expected results appropriate to the change.
In Valor, use scripts/pytest-clean.sh for Python tests. Keep check tables separate
from narrative tables, with Check, Command, and Expected columns. Negative criteria
need checks demonstrated to detect a deliberately violating case.

## Test Impact

Explain existing checks affected and meaningful new regression coverage, if needed.

## Agent Integration

Identify callable entrypoint and discovery wiring changes, or state why none are needed.

## Update System

Describe runtime/config/dependency rollout needs, or state why none are needed.

## Documentation

Name affected docs and indexes, or explain why none change.

## Risks and rollback

Describe concrete failure modes and recovery for the actual change.

## Success Criteria

List observable outcomes tied to the request.

## Critique Results

Record verified concerns, their dispositions, and implementation notes when reviewed.

## Open Questions

Keep only decisions that evidence cannot resolve. Remove when settled.
