---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-16
tracking: https://github.com/tomcounsell/ai/issues/3310
last_comment_id:
---

# apply-defaults embedding provider import cycle

## Problem

Filling in next.

**Current behavior:**

Filling in next.

**Desired outcome:**

Filling in next.

## Freshness Check

Filling in next.

## Prior Art

Filling in next.

## Research

No relevant external findings — proceeding with codebase context and training data.

## Spike Results

Filling in next.

## Data Flow

Filling in next.

## Architectural Impact

Filling in next.

## Appetite

Filling in next.

## Prerequisites

Filling in next.

## Solution

Filling in next.

## Failure Path Test Strategy

Filling in next.

## Test Impact

No existing tests affected — the change only converts a silently-swallowed ImportError into a working provider configuration plus a logged failure; existing memory tests import through the normal path and assert behavior unaffected by import order.

## Rabbit Holes

Filling in next.

## Risks

Filling in next.

## Race Conditions

No race conditions identified — all operations are synchronous and single-threaded at import time.

## No-Gos (Out of Scope)

Filling in next.

## Update System

No update system changes required — this fix is purely internal to import wiring.

## Agent Integration

No agent integration required — no new CLI entry point and no bridge changes; the provider becomes correctly configured wherever `models.memory` is imported.

## Documentation

- [ ] Update `docs/features/subconscious-memory.md` configuration notes with the import-order guarantee
- [ ] Add entry to `docs/features/README.md` index table if a new feature doc is created

## Success Criteria

Filling in next.

## Team Orchestration

Filling in next.

## Step by Step Tasks

Filling in next.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit/test_memory_model.py -q` | exit code 0 |
| Import-order regression | `.venv/bin/python -c "import models.memory; from popoto.fields.embedding_field import get_default_provider; print(get_default_provider())"` | output contains OpenAIProvider |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

Filling in next.
