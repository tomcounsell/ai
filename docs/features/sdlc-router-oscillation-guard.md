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

## The Six Guards

Guards run **before** the dispatch table. The first tripped guard wins. G1-G5
are escalation/safety guards; G6 is the terminal-state fast-path and is
evaluated last.

| Guard | Condition | Forced Dispatch |
|-------|-----------|-----------------|
| **G1: Critique loop** | Latest critique verdict is `NEEDS REVISION` or `MAJOR REWORK` AND last dispatched skill was `/do-plan-critique` | `/do-plan` |
| **G2: Critique cycle cap** | `critique_cycle_count >= 2` AND CRITIQUE is still failing | `blocked` — escalate with reason `critique cycle cap reached` |
| **G3: PR lock** | Open PR exists for the issue AND proposed dispatch is `/do-plan` or `/do-plan-critique` | Redirect to `/do-pr-review` / `/do-patch` / `/do-merge` based on `stage_states` |
| **G4: Oscillation (universal)** | `same_stage_dispatch_count >= 3` | `blocked` — escalate with reason `stage oscillation — {skill} dispatched {N} times without state change` |
| **G5: Unchanged critique artifact** | Previous CRITIQUE verdict exists AND current plan file hash matches recorded hash | Use cached verdict — do not re-dispatch `/do-plan-critique`. **Applies to CRITIQUE only.** REVIEW non-determinism is handled by G4 instead. |
| **G6: Terminal merge ready** | `pr_number` set AND `pr_merge_state == "CLEAN"` AND `ci_all_passing == True` AND `DOCS == "completed"` AND `_verdicts["REVIEW"]` contains `APPROVED` | `/do-merge {pr_number}` — fast-path bypasses re-reviewing an already-approved PR |

### Why G6 is Evaluated Last

G6 is an optimization (fast-path), not a safety guard. Escalation guards (G2:
cycle cap, G4: oscillation) take priority — a stuck pipeline should escalate to
the human before merging. G6 only fires when everything is definitively done,
making the `/do-pr-review` re-dispatch loop impossible in the happy path.

Context: issue #1043 / PR #1044. Before G6, the router would dispatch
`/do-pr-review` on every `/sdlc` invocation for a self-authored PR because
`reviewDecision=""` permanently (GitHub rejects self-approvals). G6 bypasses
the `reviewDecision` GitHub field entirely, reading from the stored
`_verdicts["REVIEW"]` verdict instead.

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
    "pr_merge_state": null,
    "ci_all_passing": null,
    "same_stage_dispatch_count": 2,
    "last_dispatched_skill": "/do-plan-critique"
  }
}
```

**New fields added by issue #1043:**

- `pr_merge_state` — live value of `mergeStateStatus` from `gh pr view` (e.g.
  `"CLEAN"`, `"BLOCKED"`, `"DIRTY"`). `null` when no PR exists or `gh` CLI
  fails. Used by G6 to verify the PR is actually mergeable.
- `ci_all_passing` — `True` when all `statusCheckRollup` conclusions are
  `"SUCCESS"` (empty rollup also returns `True` — a repo with no required
  checks has no failing checks). `null` on `gh` failure. Used by G6.

Both fields default to `null` when the `gh` CLI fails (network error, unknown
PR, timeout). G6 will not fire if either field is `null`, safely falling back
to the normal dispatch table.

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
- `tests/unit/test_sdlc_router_oscillation.py` — one test per guard (G1-G6),
  snapshot/counter helpers, guard ordering, the 12-step #1036 replay
  (`test_1036_replay_terminates`), and the 8-step #1043 PR #264 replay
  (`test_1043_pr264_8step_terminates`).
- `tests/unit/test_sdlc_skill_md_parity.py` — markdown-to-Python parity for
  both dispatch rows and guard rows (G1-G6), with positive (table matches)
  and negative (mutation detection) cases, tolerating escaped pipes in cells.
  Includes `parse_guard_rows()`, `test_guard_row_ids_in_python()`, and
  `test_g6_guard_row_present_in_skill_md()` added by issue #1043.
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
  #941 (local session tracking), #1005 (PM-level pipeline completion guards),
  #1036 (the regression G1-G5 fix), #1043 (G6 terminal-state fast-path and
  self-authored PR review loop fix).
