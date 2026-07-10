# Mandatory REVIEW and DOCS Stage Enforcement

Ensures that every SDLC session completing BUILD must also complete REVIEW and DOCS before the Observer delivers output to Telegram.

## Problem

The pipeline graph defined correct edges (TEST -> REVIEW -> DOCS -> MERGE), but the Observer had multiple paths that allowed REVIEW and DOCS to be bypassed — an audit found fewer than 10% of merged PRs reached these stages.

## Solution

### Hard Delivery Gates

> **Note**: The Observer Agent (`bridge/observer.py`) was removed as part of the PM/Dev session architecture redesign. Mandatory gate enforcement is now handled by PM session orchestration and the output router (`agent/output_router.py`, called via `agent/agent_session_queue.py`). The gate check functions in `agent/goal_gates.py` remain the source of truth for deterministic stage validation.

### State Machine Stage Transitions (`agent/pipeline_state.py`)

Stage completion is now managed by the `PipelineStateMachine`. Stages can only complete via explicit `complete_stage()` calls at session completion time — no transcript parsing or pattern matching. This eliminates false completions entirely.

### `has_remaining_stages()` (`agent/pipeline_state.py`)

The `PipelineStateMachine.has_remaining_stages()` method walks the pipeline graph from the current stage forward, checking if any reachable stage is not yet completed.

### Plan Status Update in `/do-docs`

The `/do-docs` skill writes `status: docs_complete` to the plan document's frontmatter after documentation is created/updated. This signals DOCS stage completion to `do-merge`, which verifies all checklist items and then executes the final plan deletion via `scripts/migrate_completed_plan.py`.

## Merge-Gate DOCS Precondition

`/do-merge` treats DOCS-stage completion as a first-class merge precondition,
mirroring the Step 2 REVIEW-verdict gate. The check lives as Step 2b in the
repo addendum `docs/sdlc/do-merge.md`. It reads `stages.DOCS` from
`sdlc-tool stage-query` and decides as follows:

- `stages.DOCS == completed` is the authoritative PASS. A DOCS skip records the
  same `completed` status (the "skipped" nuance lives in the router reason string,
  and `sdlc-tool stage-marker` writes only `in_progress` or `completed`), so the
  gate admits a legitimate skip with the same `== completed` check. It needs no
  special skip-branch.
- `in_progress` is the only hard fail. It fails closed, creates no
  `data/merge_authorized_{PR}` file, and routes back to `/do-docs`. This is the
  sole affirmative "DOCS unfinished" signal, reachable only via a real
  `start_stage` call (the cuttlefish #577 incident shape).
- `pending` (DOCS never started) and an empty `stages` map (the session was
  reaped or orphan-cleaned, so the marker is unreadable) both degrade to the
  file-existence check: `docs/features/{slug}.md` present gives PASS (degraded);
  absent gives FAIL. Neither case can affirm "unfinished", so degrading to the
  previous behavior keeps the gate a monotonic improvement over the old
  file-existence-only check.

The slug for the file-existence fallback comes from the PR head-ref
(`gh pr view {PR} --json headRefName`), because bypass-path operators
(raw `gh pr merge`, cross-machine) commonly run from `main` or a detached HEAD.
A head-ref of `main`/`master`/`HEAD`/empty normalizes to "no usable slug" and
fails closed with a `<no-slug>` message rather than looking up an always-absent
`docs/features/main.md`.

The deterministic gate is scoped to substrate repos (those shipping
`docs/sdlc/do-merge.md` with `sdlc-tool` markers). In a repo with no substrate
the marker cannot be read, so the global `do-merge` skill emits an announced
non-gate advisory line (containing `NOT ENFORCED`) to the merge log. That advisory
is auditable, out of deterministic reach, and relies on supervisor sequencing plus
the now-closed #1915 fork-strand fix.

## Related

- [Pipeline Graph](pipeline-graph.md) — defines the stage transition edges
- [Goal Gates](goal-gates.md) — deterministic gate check functions
- [Eng Session Architecture](eng-session-architecture.md) — PM/Dev session routing model that replaced the Observer

## Tracking

- Issue: [#418](https://github.com/tomcounsell/ai/issues/418)
- PR: [#421](https://github.com/tomcounsell/ai/pull/421)
