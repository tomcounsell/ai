# SDLC Router Oscillation Guard

Hardens the `/sdlc` dispatch table with structural preconditions ("Legal
Dispatch Guards") that consume the latest critique/review verdict, cycle
counters, PR-existence, and a same-stage dispatch counter. Prevents the router
from looping on `NEEDS REVISION` critiques, re-critiquing an unchanged plan,
dispatching `/do-plan` after a PR is open, or oscillating indefinitely on any
stage.

Context: issue #1040, PR #TBD. Regression target: issue #1036 / PR #1039, where
the router dispatched `/do-plan-critique` three times on a `NEEDS REVISION`
verdict, then three different verdicts on three consecutive `/do-pr-review`
runs against an unchanged PR.

## Components

| Component | Purpose |
|-----------|---------|
| `agent/sdlc_router.py` | Python reference implementation of the dispatch table — `decide_next_dispatch(stage_states, meta, context)`. Ground truth for the `/sdlc` router. |
| `agent/sdlc_router.py::evaluate_guards()` | Evaluates G1-G5 preconditions before the dispatch table runs. |
| `tools/sdlc_verdict.py` | CLI and Python API for recording/reading critique and review verdicts under `stage_states._verdicts`. Sole writer to the `_verdicts` key. |
| `tools/sdlc_stage_query.py` | Extended to return enriched payload: `{stages, _meta}` with cycle counters, verdicts, PR number, dispatch counter, last dispatched skill. `--format legacy` preserves the flat shape for older callers. |
| `tools/stage_states_helpers.py` | `update_stage_states(session, update_fn, max_retries=3)` — optimistic-retry helper for concurrent writes to the JSON `stage_states` field. |
| `agent/pipeline_state.py::classify_outcome` | Routes verdict writes through `sdlc_verdict.record_verdict()` — ONE writer to `_verdicts`. |
| `.claude/skills/sdlc/SKILL.md` | Dispatch table rows cite the Python implementation; a parity test fails CI if markdown and Python drift. |

## The Five Guards

Guards run **before** the dispatch table. The first tripped guard wins.

| Guard | Condition | Forced Dispatch |
|-------|-----------|-----------------|
| **G1: Critique loop** | Latest critique verdict is `NEEDS REVISION` or `MAJOR REWORK` AND last dispatched skill was `/do-plan-critique` | `/do-plan` |
| **G2: Critique cycle cap** | `critique_cycle_count >= 2` AND CRITIQUE is still failing | `blocked` — escalate with reason `critique cycle cap reached` |
| **G3: PR lock** | Open PR exists for the issue AND proposed dispatch is `/do-plan` or `/do-plan-critique` | Redirect to `/do-pr-review` / `/do-patch` / `/do-merge` based on `stage_states` |
| **G4: Oscillation (universal)** | `same_stage_dispatch_count >= 3` | `blocked` — escalate with reason `stage oscillation — {skill} dispatched {N} times without state change` |
| **G5: Unchanged critique artifact** | Previous CRITIQUE verdict exists AND current plan file hash matches recorded hash | Use cached verdict — do not re-dispatch `/do-plan-critique`. **Applies to CRITIQUE only.** REVIEW non-determinism is handled by G4 instead. |

### Why G5 is CRITIQUE-only

Plan files are pure text: they only change when the plan file changes, so a
sha256 is a stable cache key. Review verdicts on a PR can legitimately change
without the diff changing (CI status flips, new linked issues, sibling PRs
merging, human review comments arriving), so caching review verdicts on a
diff hash would mask legitimate signal changes. G4's universal oscillation
cap handles REVIEW non-determinism instead.

### G5 hash stability notes

- Hash the full UTF-8-encoded plan file bytes **including frontmatter**.
  Frontmatter edits (e.g. `revision_applied: true`) are meaningful plan changes
  that SHOULD bust the cache.
- Normalize line endings to `\n` before hashing (cross-platform safety).
- Do NOT normalize internal whitespace — a reviewer reflowing a paragraph is
  editing the plan and the critique should re-run.

### G4 state machine

`stage_states._sdlc_dispatches` is a bounded FIFO list (max 10 entries) of
`{skill, at, stage_snapshot}` records. The record is written **before** the
sub-skill launches (so oscillation on crashing skills is still detected).

The `stage_snapshot` projection is deliberately narrow to prevent spurious
churn:

- **Included:** stage statuses, `_verdicts`, `_patch_cycle_count`,
  `_critique_cycle_count`, `pr_number`
- **Excluded:** timestamps, `recorded_at`, PR check counts, CI status,
  human review comments, the `_sdlc_dispatches` list itself

Snapshots are canonicalized via `json.dumps(snapshot, sort_keys=True,
separators=(",", ":"))` before comparison — Python dict equality is
insertion-order-insensitive, but mixing raw-dict compares with
JSON-roundtripped dicts (which can happen when a snapshot is loaded from
Redis) creates subtle bugs where equal-looking snapshots compare unequal.

## Enriched Stage Query Payload

`python -m tools.sdlc_stage_query --issue-number N` returns:

```json
{
  "stages": {
    "ISSUE": "completed",
    "PLAN": "completed",
    "CRITIQUE": "failed",
    "BUILD": "pending",
    "TEST": "pending",
    "PATCH": "pending",
    "REVIEW": "pending",
    "DOCS": "pending",
    "MERGE": "pending"
  },
  "_meta": {
    "patch_cycle_count": 0,
    "critique_cycle_count": 1,
    "latest_critique_verdict": "NEEDS REVISION",
    "latest_review_verdict": null,
    "revision_applied": false,
    "pr_number": null,
    "same_stage_dispatch_count": 2,
    "last_dispatched_skill": "/do-plan-critique"
  }
}
```

Pass `--format legacy` to get the old flat `{"ISSUE": "completed", ...}`
shape for older callers.

## Single-Writer Invariant

There is exactly ONE writer for the `_verdicts` metadata key:
`tools.sdlc_verdict.record_verdict()`. Both code paths funnel through it:

1. **CLI path** — `/do-plan-critique` and `/do-pr-review` SKILLs invoke
   `python -m tools.sdlc_verdict record` after posting their verdict.
2. **Bridge path** — `agent/pipeline_state.py::classify_outcome()` extracts
   the verdict from the dev-session output tail and calls
   `tools.sdlc_verdict.record_verdict()` directly (same-process import).

The import direction is strictly `agent/ → tools/` — tools/ MUST NOT import
agent/. A regression test enforces the lazy-import pattern in
`_record_verdict_from_output` to preserve the one-way boundary.

## Concurrency

All writers to `stage_states` use `tools.stage_states_helpers.update_stage_states`,
a read-modify-write helper with optimistic retry (up to 3 attempts). On
exhaustion it emits a WARNING log (`session_id`, `stage`, `update_fn.__name__`)
so sustained contention is traceable. True Redis `WATCH/MULTI` locking is
deferred until optimistic retry proves insufficient in production.

## Regression Coverage

- `tests/unit/test_sdlc_router_decision.py` — pure-function tests for every
  dispatch rule row (1 through 10b).
- `tests/unit/test_sdlc_router_oscillation.py` — one test per guard (G1-G5),
  snapshot/counter helpers, guard ordering, and the 12-step #1036 replay
  (`test_1036_replay_terminates`).
- `tests/unit/test_sdlc_skill_md_parity.py` — markdown-to-Python parity with
  positive (table matches) and negative (mutation detection) cases,
  tolerating escaped pipes in cells.
- `tests/unit/test_sdlc_verdict.py` — record/get round-trip, hash stability
  across line endings and frontmatter edits, graceful failure on bad inputs.
- `tests/unit/test_stage_states_helpers.py` — success path, retry-on-conflict,
  retry exhaustion, deep-copy isolation of the update function's input.
- `tests/unit/test_sdlc_stage_query.py` — enriched payload shape and
  `--format legacy` backward compatibility.
- `tests/unit/test_pipeline_state_machine.py::TestClassifyOutcomeVerdictUnification`
  — `classify_outcome()` routes verdict writes through `record_verdict`.

## Related

- [Pipeline State Machine](pipeline-state-machine.md) — underlying stage status
  storage.
- [SDLC Pipeline State](sdlc-pipeline-state.md) — local session state tracking
  (`sdlc_session_ensure`, `sdlc_stage_marker`).
- [SDLC Stage Tracking](sdlc-stage-tracking.md) — stored-state-only stage
  completion (no artifact inference).
- Related issues: #704 (stage_states as source of truth), #729 (anti-skip),
  #941 (local session tracking), #1005 (PM-level pipeline completion
  guards), #1036 (the regression this plan fixes).
