# SDLC Pipeline

Overview of the SDLC pipeline routing, guards, and key metadata fields used by the SDLC router (`agent/sdlc_router.py`).

## Pipeline Flow

```
ISSUE → PLAN → CRITIQUE → BUILD → TEST → PATCH → REVIEW → DOCS → MERGE
```

Each stage is tracked in `AgentSession.stage_states` as a JSON dict with stage-status keys (e.g. `{"ISSUE": "completed", "PLAN": "completed", ...}`). The SDLC router reads this state (via `sdlc-tool stage-query`) and dispatches one sub-skill per invocation.

## Legal Dispatch Guards (G1–G7)

Guards are evaluated in order before the dispatch table. If any guard fires, its decision overrides the table.

| Guard | Condition | Forced Dispatch |
|-------|-----------|-----------------|
| G1: Critique loop | Verdict NEEDS REVISION or MAJOR REWORK AND last dispatch was critique | `/do-plan` |
| G2: Critique cycle cap | `critique_cycle_count >= MAX_CRITIQUE_CYCLES` AND CRITIQUE not completed | `blocked` |
| G3: PR lock | PR open AND last/proposed dispatch is plan-stage skill | Redirect to appropriate PR-stage skill |
| G4: Oscillation | Same skill dispatched `MAX_SAME_STAGE_DISPATCHES` times without state change | `blocked` |
| G5: Unchanged plan hash | Critique verdict exists with matching `artifact_hash` | Reuse cached verdict |
| G6: Terminal merge | PR open, CI green, DOCS done, review APPROVED | `/do-merge` |
| G7: Plan-revising lock | `plan_revising=True` AND `revision_applied!=True` AND no open PR | `/do-plan` or `blocked` |

## Plan-Revising Lock (G7)

G7 prevents `/do-build` from being dispatched while a critique-driven revision is still in flight.

### Problem it solves

Without G7, the pipeline had a race condition: a second critique round could revise the plan _after_ build had already consumed it and opened a PR. The cuttlefish #350 incident captured this failure: round-2 critique flagged a defect and revised the plan _after_ `/do-build` had shipped — the defect was rediscovered downstream as a review blocker, requiring a `/do-patch` round to apply the fix the critique had prescribed hours earlier.

### How it works

1. **Critique sets the lock**: `/do-plan-critique` Step 5.6 calls `sdlc-tool meta-set --key plan_revising --value true` when the verdict is NEEDS REVISION, MAJOR REWORK, or READY TO BUILD (with concerns) _and_ `revision_applied` is not already true in the plan frontmatter.

2. **Router blocks build**: G7 in `agent/sdlc_router.py::guard_g7_plan_revising` fires when `_meta.plan_revising` is truthy and `revision_applied` is falsy. If critique just ran (last dispatch was `/do-plan-critique`), G7 returns `Dispatch(/do-plan)`. If the lock has been set for more than `MAX_PLAN_REVISING_DISPATCHES + 1` router turns with no `/do-plan` in the recent dispatch history, G7 escalates to `Blocked`.

3. **Plan clears the lock**: `/do-plan` Phase 4 calls `sdlc-tool meta-set --key plan_revising --value false` in the same step that writes `revision_applied: true` to the plan frontmatter. The two signals always move together.

4. **Build proceeds normally**: Once the lock is cleared and `revision_applied: true` is set, the dispatch table routes to `/do-build` via Row 4c.

### Self-healing

If the lock-clear step is skipped (e.g. the plan skill crashes after writing `revision_applied: true` but before calling `meta-set`), G7 self-heals:

```python
# G7 gate 3: self-heal
if meta.get("revision_applied"):
    return None  # Lock informational only; revision_applied is the source of truth
```

### Deadlock backstop

If the lock is set but no `/do-plan` dispatch occurs within `MAX_PLAN_REVISING_DISPATCHES` (default 2) turns, G7 escalates to `Blocked`. The operator can manually clear the lock:

```bash
sdlc-tool meta-set --key plan_revising --value false --issue-number {N}
```

### Storage

The lock is stored as `stage_states["_plan_revising"]` (bool) on the PM session. It is surfaced in `_meta.plan_revising` by `tools/sdlc_stage_query.py::_compute_meta()`.

A second metadata field, `_plan_hash_at_build_start` (str|None), is written by `/do-build` Step 7 and verified at Step 21 as a defense-in-depth check. If the plan's git commit hash changes mid-build, the build aborts.

## `_meta` Fields

The enriched stage query output (`sdlc-tool stage-query`) includes a `_meta` dict alongside `stages`:

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `patch_cycle_count` | int | `_patch_cycle_count` | Number of patch cycles run |
| `critique_cycle_count` | int | `_critique_cycle_count` | Number of critique cycles run |
| `latest_critique_verdict` | str\|None | `_verdicts["CRITIQUE"]` | Most recent critique verdict text |
| `latest_review_verdict` | str\|None | `_verdicts["REVIEW"]` | Most recent review verdict text |
| `revision_applied` | bool | Plan frontmatter | Whether `revision_applied: true` is in the plan doc |
| `pr_number` | int\|None | Session attr or `gh pr list` | Open PR number for this issue |
| `pr_merge_state` | str\|None | GitHub API | `mergeStateStatus` from `gh pr view` |
| `ci_all_passing` | bool\|None | GitHub API | Whether all status checks pass |
| `same_stage_dispatch_count` | int | `_sdlc_dispatches` | Consecutive same-skill same-state dispatches |
| `last_dispatched_skill` | str\|None | `_sdlc_dispatches` | Most recent skill dispatched |
| `plan_revising` | bool | `_plan_revising` | Plan-revising lock state (G7) |
| `plan_hash_at_build_start` | str\|None | `_plan_hash_at_build_start` | Git commit hash of plan doc at build start |
| `plan_exists` | bool | `_compute_meta()` / `find_plan_path()` | `True` if a plan file is present on disk for the issue. Used by `_rule_plan_not_critiqued` to require real evidence before routing to CRITIQUE (added #1640). |
| `issue_number` | int\|None | `_compute_meta()` | Resolved issue number. Enables `_rule_no_plan` to distinguish a genuine bootstrap from a stale status string (added #1640). |

## CLI Tools

| Tool | Purpose |
|------|---------|
| `sdlc-tool stage-query --issue-number N` | Query current pipeline state |
| `sdlc-tool stage-marker --stage S --status X` | Mark stage progress |
| `sdlc-tool verdict record --stage S --verdict V` | Record critique/review verdict |
| `sdlc-tool dispatch record --skill /do-X` | Record a dispatch event |
| `sdlc-tool next-skill --issue-number N` | Get next dispatch decision |
| `sdlc-tool meta-set --key K --value V` | Set a whitelisted _meta key |
| `sdlc-tool session-ensure --issue-number N` | Ensure a PM session exists |

## See Also

- `agent/sdlc_router.py` — canonical dispatch algorithm
- `agent/pipeline_graph.py` — pipeline stage edges
- `agent/pipeline_state.py` — stage state machine
- `tools/sdlc_stage_query.py` — enriched query and `_compute_meta()`
- `tools/sdlc_meta_set.py` — whitelisted metadata writes
- `docs/sdlc/` — per-stage skill addenda
- `.claude/skills-global/sdlc/SKILL.md` — runtime routing instructions
