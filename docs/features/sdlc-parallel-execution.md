# SDLC Parallel Execution (Removed)

This feature has been removed as part of the PM/Dev to Eng session consolidation (issue #1633, merged via PR for branch `session/merge_pm_dev_into_eng_role`).

## What was here

The SDLC parallel execution feature provided two mechanisms:

- **Phase 1 — Multi-dev fan-out**: `sdlc-decompose` CLI + sub-slug Dev sessions (`{slug}-u{i}`) + `wait-for-children` for concurrent BUILD work units. Capped at `MAX_PARALLEL_DEVS` (default 3).
- **Phase 2 — DAG stage dispatch**: `MultiDispatch` return type from `decide_next_dispatch`, `PARALLEL_SAFE_PAIRS` (e.g. `{DOCS, PATCH}`), and `pthread` skill invocation for post-REVIEW parallel stage pairs.

## Why it was removed

The PM/Dev session split that this feature was built on (PM orchestrates, Dev executes, one Dev session per stage) has been collapsed into a single Eng session type. With a unified Eng session handling both orchestration and execution through the granite PTY container, the multi-dev fan-out machinery (`sdlc-decompose`, sub-slug session creation, merge-integration sessions) no longer has a substrate to run on.

The associated CLI entry point (`sdlc-decompose`) and supporting code have been deleted.
