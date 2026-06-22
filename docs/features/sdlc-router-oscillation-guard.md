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
    "last_dispatched_skill": "/do-plan-critique",
    "plan_exists": true,
    "issue_number": 941
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

**New fields added by issue #1640:**

- `plan_exists` — `True` if a plan file is present on disk for the tracked issue. Computed by `_compute_meta()` in `tools/sdlc_stage_query.py`. Used by `_rule_plan_not_critiqued` to require an actual plan file before routing to `/do-plan-critique` (prevents routing to CRITIQUE when PLAN status reads `"ready"` but no file was ever written). Also widened `_rule_no_plan` to catch the bootstrap edge case `PLAN=="ready" AND issue_number AND NOT plan_exists` → routes to `/do-plan`.
- `issue_number` — the resolved issue number (`int | None`) stored in `_meta`. Enables `_rule_no_plan` to distinguish a genuine bootstrap from a stale status string.

Pass `--format legacy` to get the old flat `{"ISSUE": "completed", ...}`
shape for older callers.

## Verdict Normalization

All verdicts stored in `_verdicts` are in a canonical form: uppercase, underscores replaced by spaces, internal whitespace collapsed to a single space.

- **Canonical form**: `"NEEDS REVISION"`, `"APPROVED"`, `"MAJOR REWORK"` (never `"needs_revision"`, `"approved "`, etc.)
- **Helper**: `normalize_verdict(text: str | None) -> str` lives in `tools/_sdlc_utils.py`
- **Write boundary**: `record_verdict()` in `tools/sdlc_verdict.py` calls `normalize_verdict()` before persisting — new records are always stored in canonical form
- **Read side**: all verdict comparisons in `agent/sdlc_router.py` (G1, G3, G5, G6, and all rule predicates) also call `normalize_verdict()` as a belt-and-suspenders guard for legacy records that predate this normalization
- **Observability**: when the normalized form differs from the raw input, a DEBUG log is emitted so desync incidents are traceable

This prevents the guard/rule predicate comparisons from silently failing on casing or underscore variants written by older pipeline versions.

## Stale-Verdict Supersession (Row 8 / Row 8b)

A REVIEW verdict becomes stale when the pipeline has made forward progress after it was recorded. Specifically:

- A REVIEW verdict is **stale** iff its `recorded_at` timestamp predates the latest `/do-patch` dispatch timestamp in `_sdlc_dispatches`
- This is encoded as `_review_verdict_is_stale(stage_states)` in `agent/sdlc_router.py`
- Helper `_latest_dispatch_at(stage_states, skill)` extracts the most recent dispatch timestamp for a given skill from the bounded FIFO dispatch log

**Row 8 behavior with staleness check:**

| Condition | `_rule_review_has_findings` returns | Next dispatch |
|-----------|-------------------------------------|---------------|
| REVIEW verdict is `APPROVED` | False | Not dispatched |
| REVIEW verdict has findings, verdict is fresh | True | `/do-patch` (row 8) |
| REVIEW verdict has findings, verdict is stale | False | Row 8b fires: `/do-pr-review` (re-review) |
| No REVIEW verdict | False | Row 9 / later rows |

All edge cases fail safe to "not stale":
- `recorded_at` missing or unparseable
- No prior `/do-patch` dispatch in the log
- Timestamps equal (tie → not stale, avoids spurious re-review)
- Any exception during parse → not stale

This prevents the router from dispatching `/do-patch` against review findings that were already addressed by a prior patch cycle, which would create an oscillation between row 8 and row 8b.

## Stale-Verdict Supersession — CRITIQUE (Row 2b / Row 3)

The REVIEW staleness pattern above is mirrored for the CRITIQUE path (#1639), fixing the stale-critique dead-end: after a plan is revised in response to a plain `NEEDS REVISION` verdict, the router previously kept matching the stale cached verdict text and re-dispatching `/do-plan` forever.

- A CRITIQUE verdict is **stale** iff its `recorded_at` timestamp predates the latest `/do-plan` dispatch timestamp in `_sdlc_dispatches`. The plan was demonstrably revised after the verdict.
- This is encoded as `_critique_verdict_is_stale(stage_states)` — a structural twin of `_review_verdict_is_stale`, swapping `REVIEW`→`CRITIQUE` and `/do-patch`→`/do-plan`. The two helpers are kept as parallel functions intentionally (no DRY merge) to keep the already-shipped REVIEW path's blast radius zero.
- **Row 3** (`_rule_critique_needs_revision`) steps aside (returns False) when the verdict is stale.
- **Row 2b** (`_rule_critique_verdict_stale`, inserted before row 3) dispatches `/do-plan-critique` for a fresh critique. It is marker-agnostic — the dead-end leaves CRITIQUE at `in_progress`, so the rule must not require any particular marker value; it requires only a stale verdict AND non-empty verdict text.

**Row 2b / Row 3 behavior with staleness check:**

| Condition | Next dispatch |
|-----------|---------------|
| CRITIQUE verdict `NEEDS REVISION`, verdict fresh (recorded after latest `/do-plan`) | `/do-plan` (row 3) — revise |
| CRITIQUE verdict `NEEDS REVISION`, verdict stale (plan revised since) | `/do-plan-critique` (row 2b) — re-critique |
| No CRITIQUE verdict / empty verdict text, CRITIQUE `in_progress`, no PR | `/do-plan-critique` (row 2c) — re-critique (see below) |

All edge cases fail safe to "not stale" (missing/unparseable `recorded_at`, no prior `/do-plan`, equal timestamps, any parse exception), exactly as in the REVIEW twin.

**Row 2c — the empty-verdict twin (#1668).** Row 2b requires a *recorded-but-stale* verdict (it gates on a `recorded_at` timestamp), so it deliberately does not fire when the critique skill ran but **never persisted any verdict at all** — `_verdicts.CRITIQUE` is `{}`, `latest_critique_verdict` is `None`, CRITIQUE marker is `in_progress`, and no PR exists yet. Before #1668 that state hit *every* rule and guard's gate and fell through to `Blocked('no matching dispatch rule')`. **Row 2c** (`_rule_critique_in_progress_no_verdict`, inserted after row 2b, before row 3) closes that hole: it re-dispatches `/do-plan-critique` when `CRITIQUE == in_progress` AND the critique verdict is absent AND no PR exists. It is narrowly gated so it cannot fire once a PR exists (defer to G3 / PR-stage rows), once any verdict is recorded (rows 2b/3/4a own it), or when CRITIQUE is not `in_progress`. Row 2b (stale verdict) and row 2c (empty verdict) are **disjoint** — 2b requires `recorded_at`, 2c requires the verdict be absent — so order between them is immaterial for correctness. Loop-bound: unlike row 2b's 2b↔3 alternation (bounded by G5), row 2c repeats the *same* skill (`/do-plan-critique`) against an unchanged snapshot, so it is bounded by **G4 (`guard_g4_oscillation`)** at `MAX_SAME_STAGE_DISPATCHES`, which escalates to a human — exactly mirroring the bounded manual recovery (re-run once; if it keeps failing, a human looks).

**Row 8c — the REVIEW empty-verdict twin (#1687).** Row 8 requires a *recorded* review verdict (it gates on a non-empty `review_verdict`), and row 8b requires a *patch-applied* state (PATCH == completed AND last_dispatched_skill == /do-patch), so neither fires when the review skill ran but **never persisted any verdict at all** — `_verdicts.REVIEW` is `{}`, `latest_review_verdict` is `None`, REVIEW marker is `in_progress`, and row 8b's three-condition predicate does not match. Before #1687 that state fell through every REVIEW row (7, 8, 8b, 9, 10, 10b) to `Blocked('no matching dispatch rule')`. **Row 8c** (`_rule_review_in_progress_no_verdict`, inserted after row 8b, before row 9) closes that hole: it re-dispatches `/do-pr-review` when `REVIEW == in_progress` AND the review verdict is absent (`.strip()` falsy) AND a PR exists AND row 8b does not own the state. It is narrowly gated so it cannot fire without a PR (REVIEW only exists post-PR), once any verdict is recorded (rows 8/8b own it), or when REVIEW is not `in_progress`. The step-aside for 8b is gated on `_rule_patch_applied_after_review(...)` exactly (not a bare `PATCH == completed` check) — a PATCH-completed state whose `last_dispatched_skill != /do-patch` makes 8b return False, so a bare PATCH-completed check would create a Blocked leak; using the same three-condition predicate as 8b ensures proper disjointness. Loop-bound: unlike row 8 (which alternates `/do-patch` <=> `/do-pr-review`), row 8c repeats the *same* skill (`/do-pr-review`) against an unchanged snapshot, so it is bounded by **G4 (`guard_g4_oscillation`)** at `MAX_SAME_STAGE_DISPATCHES`, which escalates to a human — mirroring row 2c's bounding exactly.

**G5 is the loop-breaker (NOT G4).** The row-2b (`/do-plan-critique`) ↔ row-3 (`/do-plan`) cycle alternates *two different* skills, so `guard_g4_oscillation` (which keys on the *same* skill repeated) never trips it, and `guard_g2_critique_cycle_cap` (which only increments via `fail_stage("CRITIQUE")`) is never reached. The terminating bound is **G5 (`guard_g5_artifact_hash_cache`)**: it runs before the dispatch rows and, when the current plan-file hash equals the cached CRITIQUE verdict's `artifact_hash`, short-circuits the re-critique to the cached verdict's downstream dispatch. Re-critique therefore cannot loop on an unchanged plan — row 2b only progresses when the plan hash genuinely changed.

**G5 activation in the CLI path.** G5 only fires if `context["current_plan_hash"]` is populated. Previously `tools/sdlc_next_skill.py::_build_context` never set it, leaving G5 inert via `sdlc-tool next-skill` (a latent inertness that also affected nothing else, since G5 is CRITIQUE-only). `_build_context` now computes `current_plan_hash = compute_plan_hash(find_plan_path(issue_number))` (None-safe: no plan or unreadable file leaves the key unset), so G5's loop bound on row 2b is real in production.

**Disjointness from G1.** G1 (`guard_g1_critique_loop`) fires only when `last_dispatched_skill == /do-plan-critique` (the critique just ran; plan unchanged) and routes to `/do-plan`. The #1639 dead-end has `last_dispatched_skill == /do-plan` (plan just revised). The two conditions are disjoint on `last_dispatched_skill`, so G1 and row 2b never fire each other's skill.

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
  dispatch rule row (1 through 10b), including `TestReviewInProgressNoVerdictDeadEnd`
  (row 8c, 7 cases mirroring `TestCritiqueInProgressNoVerdictDeadEnd`).
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
  self-authored PR review loop fix), #1638 (verdict normalization),
  #1640 (plan existence evidence gate), #1641 (stale-verdict supersession),
  #1668 (CRITIQUE empty-verdict re-dispatch, row 2c), #1687 (REVIEW empty-verdict
  re-dispatch, row 8c — this PR).
