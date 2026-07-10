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
mirroring the REVIEW-verdict gate. As of issue #2003, both checks -- along
with REVIEW-verdict *freshness* -- are extracted into one shared predicate,
`tools/merge_predicate.py::evaluate_merge_predicate()`, consumed by both the
`/do-merge` skill (Step 3 of `docs/sdlc/do-merge.md`) and the merge-guard hook
(`.claude/hooks/validators/validate_merge_guard.py`), so the two enforcement
paths cannot drift apart (the #1944 failure class -- a hook that only checked
"does an auth file exist" while the skill did the real gating). The DOCS leg
reads `stages.DOCS` from `sdlc-tool stage-query` and decides as follows:

- `stages.DOCS == completed` is the authoritative PASS. A DOCS skip records the
  same `completed` status (the "skipped" nuance lives in the router reason string,
  and `sdlc-tool stage-marker` writes only `in_progress` or `completed`), so the
  gate admits a legitimate skip with the same `== completed` check. It needs no
  special skip-branch.
- `in_progress` is the only hard fail. It fails closed and routes back to
  `/do-docs`. This is the sole affirmative "DOCS unfinished" signal, reachable
  only via a real `start_stage` call (the cuttlefish #577 incident shape).
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
fails the check rather than looking up an always-absent `docs/features/main.md`.

## Hook Enforcement and the Manual-Override Format

The merge-guard hook fires on any Bash command matching `gh pr merge` and, as
of issue #2003, evaluates the live predicate rather than checking for a file's
existence:

1. **Break-glass override, checked first.** `data/merge_authorized_{PR}` only
   authorizes a merge if it contains a line `override: <reason>` with a
   non-empty reason. An empty file or a legacy touch-file (the pre-#2003
   format) is treated as **absent** -- it authorizes nothing. A valid override
   is logged at WARNING and emits the `merge_guard.override_used` metric
   (dimensions: `pr_number`, `reason`), so every use is dashboard-visible, not
   just grep-able in logs.
2. **No valid override → evaluate `evaluate_merge_predicate(pr_number)`.**
   Three check groups: (a) PR state -- OPEN, MERGEABLE, `mergeStateStatus`
   CLEAN (or UNSTABLE with a green rollup), CI green, and a word-boundary
   `Closes/Fixes/Resolves #N` issue link in the body; always enforced,
   fail-closed on any `gh` error. (b) the DOCS stage gate above. (c) REVIEW
   verdict freshness: a recorded verdict must exist, contain `APPROVED`
   (case-insensitive), and be fresh against the PR's latest commit -- checked
   via the `REVIEW_CONTEXT head_sha=` trailer when present, else by comparing
   the verdict's recorded timestamp to the latest commit's committer date. A
   bare `"APPROVED" in verdict_text` check with no freshness comparison was
   the exact gap a stale approval could walk through (#2003 critique
   BLOCKER 2) -- fixed by requiring the head-SHA/date comparison.
3. **Substrate probed as a repo property, before evaluation.** Present iff the
   target repo ships `docs/sdlc/do-merge.md` and `sdlc-tool` (or
   `tools/sdlc_stage_query.py`) is resolvable. Substrate **absent** (a foreign
   repo with no SDLC tooling): groups (b)/(c) skip with a logged notice, group
   (a) still enforces. Substrate **present** but a predicate call raises,
   exits non-zero, or returns malformed output: **fail closed**, naming the
   exact failed check. This ordering keeps "foreign repo, nothing to check"
   and "substrate repo, evaluation broke" from collapsing into the same
   observable signal.
4. **No extractable PR number in the command** → block with the generic
   `/do-merge {pr_number}` remediation message; the predicate cannot be
   evaluated without a PR number.

On the happy path, `/do-merge` no longer creates or deletes the auth file --
the hook allows the merge because the predicate passes live. The auth file
survives only as the explicit break-glass override described in step 1, for a
human operator merging while the substrate itself is down.

The deterministic gate is scoped to substrate repos as described in step 3
above. In a substrate-absent repo, the global `do-merge` skill emits an
announced non-gate advisory line (containing `NOT ENFORCED`) to the merge log
for the checks it cannot run; PR-state checks (group a) still enforce via the
hook regardless.

## Related

- [Pipeline Graph](pipeline-graph.md) — defines the stage transition edges
- [Goal Gates](goal-gates.md) — deterministic gate check functions
- [Eng Session Architecture](eng-session-architecture.md) — PM/Dev session routing model that replaced the Observer

## Tracking

- Issue: [#418](https://github.com/tomcounsell/ai/issues/418)
- PR: [#421](https://github.com/tomcounsell/ai/pull/421)
