---
title: Remove Job UTC-reattach and 1.8.0-era naive-tzinfo guards
slug: remove-popoto-1-8-0-naive-datetime-scar-tissue
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/3173
last_comment_id:
---

# Remove Job UTC-reattach and 1.8.0-era naive-tzinfo scar tissue

## Problem

`models/job.py` still carries three pieces of machinery built for popoto 1.8.0, plus the docstrings
that justify them:

1. A `Job.save()` override (lines 144-188) that re-attaches UTC to a naive `last_active_at` before
   delegating to `Model.save`.
2. A `Job.renormalize_last_active_scores()` call inside `Job.repair_indexes()` (lines 769-783) that
   sweeps every recency score after every `rebuild_indexes()`.
3. Docstrings at `models/job.py:155`, `:172`, `:533` that describe popoto 1.8.0's decoder in the
   present tense, plus the same story in `docs/features/durability-model.md` (lines 79-108),
   `docs/features/popoto-index-hygiene.md` (line 40), `docs/features/utc-timestamps.md` (lines 87, 94),
   and `scripts/update/migrations.py` (lines 1115-1141).

Commit `8c1a36ad1` raised the floor to `popoto>=1.9.0`. It updated one docstring and one assertion in
`tests/unit/test_job_model.py` and left source and docs untouched. A reader of `Job.save()` is told
"popoto 1.8.0 decodes a stored datetime without tzinfo", which is false on the installed version, and
`repair_indexes()` pays a full-keyspace sweep on every worker start to correct a skew the rebuild no
longer produces.

`agent/session_health.py` and `agent/session_pickup.py` carry inline naive-datetime coercions over
`AgentSession` fields that now always arrive aware.

**Desired outcome.** The Job model, its docs, and the session-health and pickup helpers describe and
rely on popoto 1.9.0's actual contract. Code that existed only to compensate for naive decoding is
gone, along with the tests that pinned it. Every naive-to-UTC coercion that survives lives in exactly
one named helper per module, which is the convention `docs/features/utc-timestamps.md` already
declares.

## Freshness Check

**Disposition: Unchanged.** Baseline commit: `67d714662` (main, 2026-09-05). Issue filed
2026-09-05T09:29:28Z; plan written ~3h later.

| Re-verified | Result |
|---|---|
| `git log --since=<issue createdAt>` over `models/job.py`, `tests/unit/test_job_model.py`, `docs/features/durability-model.md`, `docs/features/utc-timestamps.md`, `agent/session_health.py`, `agent/session_pickup.py` | Zero commits. Only two commits landed on main since filing (`503fb536e`, `67d714662`), both `docs/plans/` for lane `rtr-unconditional-2733`. |
| `models/job.py:155`, `:172`, `:533` (cited 1.8.0-as-present docstrings) | All three exact, no line drift. |
| `docs/features/utc-timestamps.md:87`, `:94` | Both exact. |
| `docs/features/durability-model.md` lines 82-106 (cited) | Passage is at **79-108**, one heading earlier than cited. Same content. Corrected in Technical Approach. |
| `pyproject.toml` popoto pin | `popoto>=1.9.0` (line 21). `.venv` reports 1.9.0. `POPOTO_DATETIME_KEY_LEGACY` unset repo-wide. |
| #2833 (PR) | MERGED 2026-08-18. Introduced the override; unchanged since. |
| #2848 (issue) | CLOSED 2026-09-05T06:42:17Z, ~3h **before** this issue was filed. Its cursored/pipelined sweep is already on main and is what this plan keeps. |
| #2083 (issue) | CLOSED 2026-07-17. The sibling descriptor-pollution audit; same shape, no code overlap. |
| Active plans in `docs/plans/` | `expectation_blocked_state.md` (issue #2862, status Planning) references `models/job.py` 11 times, but only the expectation/goal surface — zero references to `save`, `repair_indexes`, `renormalize`, or `last_active_at`. No conflict. `rtr-unconditional-2733`, `ancestor-safe-service-pid-lookup`, `telegram-forum-topic-awareness`: no overlap. |

**Two premises corrected during the check** (both narrow the work rather than widen it) — see Spike Results:

- The issue's Solution Sketch says "every comment that says popoto strips or drops tzinfo is corrected"
  in `agent/session_health.py` / `agent/session_pickup.py`. **No such comment exists.** Acceptance
  criterion 5 already holds on main, vacuously. What remains in those two files is a pure code
  decision, not a docs correction.
- The Sketch names three tests to delete. There are **five** affected test bodies, not three — see
  Test Impact.

## Prior Art

_placeholder_

## Research

_placeholder_

## Spike Results

_placeholder_

## Data Flow

_placeholder_

## Architectural Impact

_placeholder_

## Appetite

_placeholder_

## Prerequisites

_placeholder_

## Solution

_placeholder_

## Failure Path Test Strategy

_placeholder_

## Test Impact

_placeholder_

## Rabbit Holes

_placeholder_

## Risks

_placeholder_

## Race Conditions

_placeholder_

## No-Gos (Out of Scope)

_placeholder_

## Update System

_placeholder_

## Agent Integration

_placeholder_

## Documentation

_placeholder_

## Success Criteria

_placeholder_

## Step by Step Tasks

_placeholder_

## Verification

_placeholder_

## Open Questions

_placeholder_
