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
| `agent/sdlc_router.py::evaluate_guards()` | Evaluates G1-G8 preconditions, in the pinned order `[G1, G2, G3, G4, G8, G7, G5, G6]`, before the dispatch table runs. |
| `tools/sdlc_verdict.py` | CLI and Python API for recording/reading critique and review verdicts under `stage_states._verdicts`. Sole writer to the `_verdicts` key. |
| `tools/sdlc_stage_query.py` | Extended to return enriched payload: `{stages, _meta}` with cycle counters, verdicts, PR number, dispatch counter, last dispatched skill. `--format legacy` preserves the flat shape for older callers. |
| `tools/stage_states_helpers.py` | `update_stage_states(session, update_fn, max_retries=3)` — optimistic-retry helper for concurrent writes to the JSON `stage_states` field. |
| `agent/pipeline_state.py::classify_outcome` | Routes verdict writes through `sdlc_verdict.record_verdict()` — ONE writer to `_verdicts`. |
| `.claude/skills/sdlc/SKILL.md` | Dispatch table rows cite the Python implementation; a parity test fails CI if markdown and Python drift. |

## The Eight Guards

Guards run **before** the dispatch table. The first tripped guard wins.
**Pinned evaluation order** (`GUARDS` in `agent/sdlc_router.py`, list-literal
order is binding):

```
G1 → G2 → G3 → G4 → G8 → G7 → G5 → G6
```

The table below is numbered `G1`-`G8` for readability, not evaluation order —
the row order in the table does **not** match the pinned order above (G7 sits
before G5/G6; G8 sits between G4 and G7). Cross-reference the pinned order
whenever two guards could otherwise both match the same state.

| Guard | Condition | Forced Dispatch |
|-------|-----------|-----------------|
| **G1: Critique loop** | Latest critique verdict is `NEEDS REVISION` or `MAJOR REWORK` AND last dispatched skill was `/do-plan-critique` | `/do-plan`. **Steps aside if a PR is already open** (`meta["pr_number"]` set), deferring to G3 instead (#1932) — see below. |
| **G2: Critique cycle cap** | `critique_cycle_count >= 2` AND CRITIQUE is still failing | `blocked` — escalate with reason `critique cycle cap reached` |
| **G3: PR lock** | Open PR exists for the issue AND proposed dispatch is `/do-plan` or `/do-plan-critique` | Redirect to `/do-pr-review` / `/do-patch` / `/do-merge` based on `stage_states` |
| **G4: Oscillation (universal)** | `same_stage_dispatch_count >= 3` | `blocked` — escalate with reason `stage oscillation — {skill} dispatched {N} times without state change` |
| **G5: Unchanged critique artifact** | Previous CRITIQUE verdict exists AND current plan file hash matches recorded hash | Use cached verdict — do not re-dispatch `/do-plan-critique`. **Applies to CRITIQUE only.** REVIEW non-determinism is handled by G4 instead. On a cached `NEEDS_REVISION`/`MAJOR_REWORK` verdict, **steps aside if a PR is already open**, mirroring the pre-existing defer on the `READY_TO_BUILD` branch (#1932) — see below. On its `READY_TO_BUILD` branch, also steps aside (returns `None`) when `plan_revising` is set and `revision_applied` is not — the #1871 present-gap short-circuit, see below. |
| **G6: Terminal merge ready** | `pr_number` set AND `pr_merge_state == "CLEAN"` AND `ci_all_passing == True` AND `DOCS == "completed"` AND `_verdicts["REVIEW"]` contains `APPROVED` | `/do-merge {pr_number}` — fast-path bypasses re-reviewing an already-approved PR |
| **G7: Plan-revising lock** | `pr_number` is `None` AND `plan_revising == True` AND `revision_applied != True` | `/do-plan` (if `last_dispatched_skill == /do-plan-critique`); escalate to `blocked` if no `/do-plan` dispatch appears in the last `MAX_PLAN_REVISING_DISPATCHES + 1` turns |
| **G8: Artifact verification** | `context["stage_artifacts_verified"] is False` (an explicit, live-checked mismatch — see below) | Re-dispatch the skill for `context["unverified_stage"]` rather than letting the pipeline advance on the self-attested marker |

### Why G6 is Evaluated Last

G6 is an optimization (fast-path), not a safety guard. Escalation guards (G2:
cycle cap, G4: oscillation) take priority — a stuck pipeline should escalate to
the human before merging. G6 only fires when everything is definitively done,
making the `/do-pr-review` re-dispatch loop impossible in the happy path. This
still holds after the #1871 reorder (below): G6 remains the last guard in the
pinned order, evaluated after G7.

Context: issue #1043 / PR #1044. Before G6, the router would dispatch
`/do-pr-review` on every `/sdlc` invocation for a self-authored PR because
`reviewDecision=""` permanently (GitHub rejects self-approvals). G6 bypasses
the `reviewDecision` GitHub field entirely, reading from the stored
`_verdicts["REVIEW"]` verdict instead.

### G7 precedes G5/G6: the #1871 guard-precedence fix

`guard_g7_plan_revising` (the plan-revising lock) is positioned in `GUARDS`
**before** `guard_g5_artifact_hash_cache` and `guard_g6_terminal_merge_ready`.
Before this fix, G5's `READY_TO_BUILD` branch — a cached-verdict fast path
that gates only on `pr_number` / `BUILD == completed` — could fire and
dispatch `/do-build` while a critique-requested revision pass was still in
flight (`plan_revising == True`), shipping the pre-revision design. Observed
live on #1821 (2026-07-03).

**Why the reorder does not cross G6** (the "already-mergeable PR is never
blocked" invariant): G7's **Gate 1** returns `None` the instant `pr_number` is
set, and G6 only ever fires when `pr_number` is set. So in every state where
G6 could dispatch `/do-merge`, G7 has already deferred at Gate 1 — G6 still
wins regardless of list position. The invariant now rests on G7's `pr_number`
self-gate, not on where either guard sits in `GUARDS`.

**G5's present-gap short-circuit.** The reorder alone is not sufficient: G7's
**Gate 6** returns `None` (defers to the dispatch table) whenever the lock is
set, `revision_applied` is false, and a `/do-plan` dispatch already appears in
recent history — the revision may still legitimately be in flight. Without an
additional check, that fallthrough state would let G5's `READY_TO_BUILD`
branch ship the pre-revision design even with G7 ahead of it in the list. G5's
`READY_TO_BUILD` branch therefore also short-circuits to `None` when
`plan_revising` is set and `revision_applied` is not — this is the guard that
actually prevents `/do-build` in the Gate-6-fallthrough case, not G7 itself.

### G8: stage-advance artifact verification (#1267)

The router previously advanced on stage-completion markers the executing
agent *self-attests* (the `<!-- OUTCOME {...} -->` contract in
`agent/pipeline_state.py`) with nothing independently confirming the claimed
load-bearing artifact — a PR actually opened, a branch actually pushed, a
plan actually committed — exists in the world.

**Where the check runs vs. where the decision is made.** The live check
happens in `tools/sdlc_next_skill.py::_build_context` (via
`_verify_stage_artifacts` / `_verify_stage_artifacts_live`), during context
assembly — deterministic, no LLM, and outside the router, so
`agent/sdlc_router.py` stays import-free of `tools/` (the existing
`test_architectural_constraints.py` boundary). On a mismatch it sets
`context["stage_artifacts_verified"] = False` and
`context["unverified_stage"] = <STAGE>`; it makes no dispatch decision
itself. `guard_g8_artifact_verification` (in `agent/sdlc_router.py`) consumes
those flags and returns `Dispatch(skill=<same stage's skill>)`.

**Verified artifact set (top 3, deterministic):**

| Stage | Claimed artifact | Live check |
|-------|------------------|------------|
| BUILD | PR opened | `gh pr view --json state`; verified when state is `OPEN` or `MERGED` |
| PATCH | branch pushed | `git ls-remote --heads origin session/{slug}`; skipped (treated verified) when the PR state is already `MERGED`, since a delete-branch-on-merge policy removes the ref as an expected side effect of merging, not evidence of a fabricated claim |
| PLAN | plan committed on `main` | `git show main:docs/plans/{slug}.md` |

A stage with no claimed artifact (marker absent, or nothing this function
knows how to check) is a no-op — verification never invents a check.

**cwd threading (issue #2078).** All three live `git` checks
(`_check_plan_committed_on_main`, `_check_branch_pushed`, and the
`branch_exists` probe in `_build_context`) run through a shared
`_target_repo_cwd()` helper in `tools/sdlc_next_skill.py`, which resolves to
`os.environ.get("SDLC_TARGET_REPO") or None` and is passed as `cwd=` to each
`subprocess.run` — mirroring the rung-1 precedent in
`tools/_sdlc_utils.py::_resolve_target_repo` (`SDLC_TARGET_REPO` is a
filesystem path, never a gh slug). Fallback `None` preserves bridge behavior,
where the worker's process cwd already is the target checkout. Without this
threading, the local `/do-sdlc` wrapper's `uv run --directory` pin to the ai
repo made these checks inspect the ai repo instead of the SDLC target — a
plan genuinely committed on the target's `main` read as unverified and G8
re-dispatched `/do-plan` forever. **Any new live check added to this verifier
must pass `cwd=_target_repo_cwd()` or it silently regresses this fix.** Note
the PLAN check reads the target checkout's *local* `main` (`git show
main:...`), so a checkout whose `main` is behind origin still fails
verification until pulled — only the PATCH branch check queries the remote.

**Positioning is load-bearing.** G8 sits immediately **after G4**
(`guard_g4_oscillation`), not before it. On a persistently false claim, G8
alone would re-dispatch the same stage's skill forever; G4 fires first and
escalates to `Blocked` once `same_stage_dispatch_count >=
MAX_SAME_STAGE_DISPATCHES`, bounding the loop. The phase-1 false-claim policy
is "silent re-dispatch, then escalate via the existing G4 cap" — not an
immediate `Blocked` on the first mismatch.

**Fail-open scope is narrow.** The verification catch in
`tools/sdlc_next_skill.py::_verify_stage_artifacts` is limited to
`subprocess.TimeoutExpired`, `subprocess.SubprocessError`, and `OSError` —
infra failures from the underlying `gh`/`git` calls. On those it logs a
warning and returns `{}` (advances; the #2003 merge-gate remains the hard
backstop) so the gate never wedges on network flakiness. Any other exception
(a `TypeError`/`KeyError` from a malformed artifact spec or bad slug — a logic
bug, not infra) is **not** swallowed: it is logged at error level and
re-raised, so a broken gate is visible instead of silently failing open
forever.

### Why G5 is CRITIQUE-only

Plan files are pure text: they only change when the plan file changes, so a
sha256 is a stable cache key. Review verdicts on a PR can legitimately change
without the diff changing (CI status flips, new linked issues, sibling PRs
merging, human review comments arriving), so caching review verdicts on a
diff hash would mask legitimate signal changes. G4's universal oscillation
cap handles REVIEW non-determinism instead.

### G5 hash stability notes

- G5 uses `compute_plan_body_hash` (in `tools/sdlc_verdict.py`), which hashes
  the full UTF-8-encoded plan bytes **with only the `revision_applied:` frontmatter
  key stripped**. All other frontmatter (`status:`, `type:`, `tracking:`,
  `last_comment_id:`) and the full body still contribute to the hash.
- **Exception — `revision_applied:` does NOT bust the cache.** A `/do-plan`
  revision write flipping `revision_applied: false → true` changes only that key;
  `compute_plan_body_hash` produces the same output before and after the write, so
  G5 fires a cache hit and routes straight to `/do-build` (issue #1761).
- Every other frontmatter or body edit still busts the cache and triggers a
  fresh `/do-plan-critique` run.
- Normalize line endings to `\n` before hashing (cross-platform safety).
- Do NOT normalize internal whitespace — a reviewer reflowing a paragraph is
  editing the plan and the critique should re-run.
- `compute_plan_hash` (full-bytes variant) is retained for callers that explicitly
  need the complete fingerprint; it is no longer used by G5 itself.

### G1 / G5 open-PR step-aside (#1932)

Before #1932, a `NEEDS REVISION` critique verdict could route back to `/do-plan` — dead-ending the pipeline on a plan revision the router never re-critiques — through three independent pre-table paths, not just the dispatch-table row (Row 3, see below):

- **G1** fires whenever `last_dispatched_skill == /do-plan-critique` and the latest verdict is `NEEDS REVISION`/`MAJOR REWORK`, regardless of whether a PR already exists. Once a PR is open, G3 (PR lock) is the correct authority — it redirects to `/do-pr-review` / `/do-patch` / `/do-merge` based on `stage_states` instead of blindly sending the router back to planning.
- **G5** short-circuits to a *cached* verdict when the plan-file hash is unchanged. If that cached verdict is `NEEDS_REVISION`/`MAJOR_REWORK`, G5 previously dispatched `/do-plan` from the cache — shadowing both the G1 fix and the Row 3 fix, since G5 runs before the dispatch table regardless of `last_dispatched_skill`.

Both guards now check `meta.get("pr_number")` first and return `None` (step aside) when a PR is open, letting evaluation fall through to G3. G1 and G5's `READY_TO_BUILD` branch already had this defer; #1932 makes the `NEEDS_REVISION`/`MAJOR_REWORK` branches symmetric.

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
| No REVIEW verdict, REVIEW `in_progress`, PR exists, row 8b doesn't own the state | False | Row 8c fires: `/do-pr-review` (re-review, empty-verdict twin) |
| No REVIEW verdict, REVIEW `completed` or `failed` (re-review subagent crashed before recording), last dispatch was `/do-pr-review` | False | Row 8d fires: `/do-pr-review` (re-review, crash-recovery twin, #1932) |
| No REVIEW verdict, none of the above match | False | Falls through past row 9 (verdict gate, #1932) to later rows |

All edge cases fail safe to "not stale":
- `recorded_at` missing or unparseable
- No prior `/do-patch` dispatch in the log
- Timestamps equal (tie → not stale, avoids spurious re-review)
- Any exception during parse → not stale

This prevents the router from dispatching `/do-patch` against review findings that were already addressed by a prior patch cycle, which would create an oscillation between row 8 and row 8b.

## Stale-Verdict Supersession — CRITIQUE (Row 2b / Row 3)

The REVIEW staleness pattern above is mirrored for the CRITIQUE path (#1639), fixing the stale-critique dead-end: after a plan is revised in response to a plain `NEEDS REVISION` verdict, the router previously kept matching the stale cached verdict text and re-dispatching `/do-plan` forever.

- A CRITIQUE verdict is **stale** iff its `recorded_at` timestamp predates the latest `/do-plan` dispatch timestamp in `_sdlc_dispatches`. The plan was demonstrably revised after the verdict.
- This is encoded as `_critique_verdict_is_stale(stage_states, meta)` — a structural twin of `_review_verdict_is_stale`, swapping `REVIEW`→`CRITIQUE` and `/do-patch`→`/do-plan`. The two helpers are kept as parallel functions intentionally (no DRY merge) to keep the already-shipped REVIEW path's blast radius zero.
- **Row 3** (`_rule_critique_needs_revision`) steps aside (returns False) when the verdict is stale.
- **Row 2b** (`_rule_critique_verdict_stale`, inserted before row 3) dispatches `/do-plan-critique` for a fresh critique. It is marker-agnostic — the dead-end leaves CRITIQUE at `in_progress`, so the rule must not require any particular marker value; it requires only a stale verdict AND non-empty verdict text.
- **Row 3 open-PR step-aside (#1932).** Independent of staleness, Row 3 also steps aside (returns False) whenever `meta.get("pr_number")` is set — a `NEEDS REVISION` critique verdict must never route to `/do-plan` once a PR is open, since row 7/G3 already own PR-stage routing. This closes the third of three independent pre-#1932 routes (alongside the G1 and G5 step-asides above) that could all send the router back to planning with a PR already in flight.

**Row 2b / Row 3 behavior with staleness check:**

| Condition | Next dispatch |
|-----------|---------------|
| CRITIQUE verdict `NEEDS REVISION`, verdict fresh (recorded after latest `/do-plan`) | `/do-plan` (row 3) — revise |
| CRITIQUE verdict `NEEDS REVISION`, verdict stale (plan revised since) | `/do-plan-critique` (row 2b) — re-critique |
| No CRITIQUE verdict / empty verdict text, CRITIQUE `in_progress`, no PR | `/do-plan-critique` (row 2c) — re-critique (see below) |

All edge cases fail safe to "not stale" (missing/unparseable `recorded_at`, no prior `/do-plan`, equal timestamps, any parse exception), exactly as in the REVIEW twin.

**Row 2c — the empty-verdict twin (#1668).** Row 2b requires a *recorded-but-stale* verdict (it gates on a `recorded_at` timestamp), so it deliberately does not fire when the critique skill ran but **never persisted any verdict at all** — `_verdicts.CRITIQUE` is `{}`, `latest_critique_verdict` is `None`, CRITIQUE marker is `in_progress`, and no PR exists yet. Before #1668 that state hit *every* rule and guard's gate and fell through to `Blocked('no matching dispatch rule')`. **Row 2c** (`_rule_critique_in_progress_no_verdict`, inserted after row 2b, before row 3) closes that hole: it re-dispatches `/do-plan-critique` when `CRITIQUE == in_progress` AND the critique verdict is absent AND no PR exists. It is narrowly gated so it cannot fire once a PR exists (defer to G3 / PR-stage rows), once any verdict is recorded (rows 2b/3/4a own it), or when CRITIQUE is not `in_progress`. Row 2b (stale verdict) and row 2c (empty verdict) are **disjoint** — 2b requires `recorded_at`, 2c requires the verdict be absent — so order between them is immaterial for correctness. Loop-bound: unlike row 2b's 2b↔3 alternation (bounded by G5), row 2c repeats the *same* skill (`/do-plan-critique`) against an unchanged snapshot, so it is bounded by **G4 (`guard_g4_oscillation`)** at `MAX_SAME_STAGE_DISPATCHES`, which escalates to a human — exactly mirroring the bounded manual recovery (re-run once; if it keeps failing, a human looks).

**Row 8c — the REVIEW empty-verdict twin (#1687).** Row 8 requires a *recorded* review verdict (it gates on a non-empty `review_verdict`), and row 8b requires a *patch-applied* state (PATCH == completed AND last_dispatched_skill == /do-patch), so neither fires when the review skill ran but **never persisted any verdict at all** — `_verdicts.REVIEW` is `{}`, `latest_review_verdict` is `None`, REVIEW marker is `in_progress`, and row 8b's three-condition predicate does not match. Before #1687 that state fell through every REVIEW row (7, 8, 8b, 9, 10, 10b) to `Blocked('no matching dispatch rule')`. **Row 8c** (`_rule_review_in_progress_no_verdict`, inserted after row 8b, before row 9) closes that hole: it re-dispatches `/do-pr-review` when `REVIEW == in_progress` AND the review verdict is absent (`.strip()` falsy) AND a PR exists AND row 8b does not own the state. It is narrowly gated so it cannot fire without a PR (REVIEW only exists post-PR), once any verdict is recorded (rows 8/8b own it), or when REVIEW is not `in_progress`. The step-aside for 8b is gated on `_rule_patch_applied_after_review(...)` exactly (not a bare `PATCH == completed` check) — a PATCH-completed state whose `last_dispatched_skill != /do-patch` makes 8b return False, so a bare PATCH-completed check would create a Blocked leak; using the same three-condition predicate as 8b ensures proper disjointness. Loop-bound: unlike row 8 (which alternates `/do-patch` <=> `/do-pr-review`), row 8c repeats the *same* skill (`/do-pr-review`) against an unchanged snapshot, so it is bounded by **G4 (`guard_g4_oscillation`)** at `MAX_SAME_STAGE_DISPATCHES`, which escalates to a human — mirroring row 2c's bounding exactly.

**Row 8d — crashed re-review recovery (#1932).** Row 8c requires `REVIEW == in_progress`. If the `/do-pr-review` subagent crashes (or is killed) *after* it starts but *before* it records a verdict, REVIEW can land at `completed` or `failed` instead of staying `in_progress` — a state row 8c does not cover. Before #1932 that state either dead-ended (`REVIEW == failed` matched no row, fell through to `Blocked`) or silently misrouted to `/do-docs` (`REVIEW == completed` with no verdict used to satisfy row 9's old `REVIEW == completed`-only check). **Row 8d** (`_rule_review_crashed_after_dispatch`, inserted immediately after row 8c, before row 9) recovers both terminal markers: it re-dispatches `/do-pr-review` when `pr_number` is set, `PATCH == completed`, `REVIEW in (completed, failed)`, no REVIEW verdict is recorded, AND `last_dispatched_skill == /do-pr-review`. It is disjoint from row 7 and row 8b by construction (both require different `last_dispatched_skill`/PATCH states) and disjoint from row 8c structurally (8c requires `REVIEW == in_progress`; 8d requires `REVIEW in (completed, failed)`). Loop-bound: like row 8c, row 8d repeats the same skill against a state that is stable across crash retries, so it is bounded by **G4** — except when the terminal marker itself *alternates* between `completed` and `failed` on successive crashes, which resets G4's same-snapshot streak every turn (a known, deliberately out-of-scope gap; see `TestRow8dChurnLimitation` in the regression suite).

**Row 9 verdict gate (#1932).** `_rule_review_approved_docs_not_done` (row 9) previously dispatched `/do-docs` whenever `REVIEW == completed` and `DOCS` was not yet done — treating "REVIEW marked completed" as a proxy for "REVIEW approved." That proxy breaks in the row 8d crash scenario: REVIEW can be `completed` with **zero** recorded verdict, and row 9 would silently skip review entirely, sending the pipeline straight to docs. Row 9 now additionally requires `REVIEW_APPROVED in normalize_verdict(_latest_review_verdict(stage_states))` before firing. This makes row 8d and row 9 mutually exclusive **by verdict** (8d requires no verdict; row 9 requires a positively-recorded `APPROVED`), not by fragile table-order luck — closing the misroute for every `last_dispatched_skill`, not just the `/do-pr-review` subset row 8d recovers.

### Convergence latch: `revision_applied_at` (#1760)

Even with G5's hash-cache loop bound (below), a residual loop remained: a
revision pass that embeds concern/nit notes into the plan **body** (not just
the `revision_applied:` frontmatter key) busts `compute_plan_body_hash`, so G5
returns no cache hit, AND re-stales the CRITIQUE verdict by timestamp — row 2b
fires, a fresh critique runs, and if it emits fresh non-blocking nits the
cycle repeats. The bare `revision_applied: true` boolean can't break this: a
`/do-plan` revision sets it on **every** revision pass, so the boolean alone
can't distinguish "this is the settle-and-build dispatch that should converge"
from "this is some later, unrelated `/do-plan` dispatch."

**The fix is event-scoped, not a sticky boolean.** `/do-plan` writes a
`revision_applied_at:` ISO-8601 UTC frontmatter timestamp in the *same step*
it sets `revision_applied: true` (`_parse_revision_applied_at` in
`tools/sdlc_stage_query.py`, structural twin of `_parse_revision_applied`,
parsed into `meta["revision_applied_at"]`). `_critique_verdict_is_stale`
suppresses staleness **only** when the latest `/do-plan` dispatch
(`_latest_dispatch_at(stage_states, SKILL_DO_PLAN)`) is **not later than**
`meta["revision_applied_at"]` — i.e. the dispatch that produced *this*
revision is the one being judged. Any `/do-plan` dispatch whose `at` postdates
`revision_applied_at` re-stales normally regardless of the boolean, so a later
unrelated revision never gets a free pass to `/do-build`. When
`revision_applied_at` is absent or unparseable, the latch is inert and the
predicate falls back to its original timestamp-only staleness check
(fail-safe to pre-#1760 behavior).

Once the latch suppresses staleness, row 4c routes to `/do-build` instead of
row 2b re-dispatching critique — removing the loop at the predicate itself,
inside `_critique_verdict_is_stale`, rather than adding a tenth special-case
row. The skill-convention half of this fix (the `date -u` write in
`/do-plan`'s Phase 4 Step 2a, and the `plan_revising` lock clear that depends
on it) is documented in `docs/sdlc/do-plan.md`.

**G5 is the loop-breaker (NOT G4).** The row-2b (`/do-plan-critique`) ↔ row-3 (`/do-plan`) cycle alternates *two different* skills, so `guard_g4_oscillation` (which keys on the *same* skill repeated) never trips it, and `guard_g2_critique_cycle_cap` (which only increments via `fail_stage("CRITIQUE")`) is never reached. The terminating bound is **G5 (`guard_g5_artifact_hash_cache`)**: it runs before the dispatch rows and, when the current plan-file hash equals the cached CRITIQUE verdict's `artifact_hash`, short-circuits the re-critique to the cached verdict's downstream dispatch. Re-critique therefore cannot loop on an unchanged plan — row 2b only progresses when the plan hash genuinely changed. The `revision_applied_at` latch above covers the residual case where the plan hash *does* change (a body-only revision) but the revision was the settle-and-build pass, not a genuinely new concern.

**G5 activation in the CLI path.** G5 only fires if `context["current_plan_hash"]` is populated. Previously `tools/sdlc_next_skill.py::_build_context` never set it, leaving G5 inert via `sdlc-tool next-skill` (a latent inertness that also affected nothing else, since G5 is CRITIQUE-only). `_build_context` now computes `current_plan_hash = compute_plan_body_hash(find_plan_path(issue_number))` (None-safe: no plan or unreadable file leaves the key unset), so G5's loop bound on row 2b is real in production. Using `compute_plan_body_hash` (not `compute_plan_hash`) ensures a `revision_applied: true` write does not bust the cache and send the router back to CRITIQUE (#1761).

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
- `tests/unit/test_sdlc_router.py` — `TestReReviewCrashRecovery` (row 8d, both
  `completed`/`failed` terminal markers recover), `TestRow3OpenPrStepAside`,
  `TestG1OpenPrStepAside`, `TestG5OpenPrStepAside` (NEEDS_REVISION and
  MAJOR_REWORK, G1-then-G3 interaction, no-PR regression), `TestRow9VerdictGate`
  (blocked without a verdict, fires with APPROVED), `TestRow8dLoopBound` (G4
  trips on a stable crash marker), and `TestRow8dChurnLimitation` (documents the
  deliberately out-of-scope alternating-marker gap — issue #1932).
- `tests/unit/test_sdlc_router_oscillation.py` — one test per guard (G1-G6),
  snapshot/counter helpers, guard ordering, the 12-step #1036 replay
  (`test_1036_replay_terminates`), the 8-step #1043 PR #264 replay
  (`test_1043_pr264_8step_terminates`), and the #1267 G8 guard-ordering cases
  (G4 fires before G8 on a persistently-false claim; the G4 cap bounds
  verification-driven re-dispatches).
- `tests/unit/test_sdlc_skill_md_parity.py` — markdown-to-Python parity for
  both dispatch rows and guard rows (G1-G6), with positive (table matches)
  and negative (mutation detection) cases, tolerating escaped pipes in cells.
  Includes `parse_guard_rows()`, `test_guard_row_ids_in_python()`, and
  `test_g6_guard_row_present_in_skill_md()` added by issue #1043.
- `tests/unit/test_sdlc_verdict.py` — record/get round-trip, hash stability
  across line endings and frontmatter edits, graceful failure on bad inputs.
- `tests/unit/test_stage_states_helpers.py` — success path, retry-on-conflict,
  retry exhaustion, deep-copy isolation of the update function's input.
- `tests/unit/test_sdlc_stage_query.py` — enriched payload shape,
  `--format legacy` backward compatibility, and `_parse_revision_applied_at`
  parsing `revision_applied_at` from plan frontmatter into `meta` (#1760).
- `tests/unit/test_pipeline_state_machine.py::TestClassifyOutcomeVerdictUnification`
  — `classify_outcome()` routes verdict writes through `record_verdict`.
- `tests/unit/test_sdlc_next_skill.py` — `TestStageArtifactVerification` (#1267):
  the verification check runs in context assembly, a false BUILD/PATCH/PLAN
  claim sets `stage_artifacts_verified=False` + `unverified_stage` so G8
  re-dispatches instead of advancing, fail-open on infra errors only, and a
  non-infra exception does not silently advance.
- `tests/integration/test_sdlc_session_ensure_integration.py` — end-to-end
  exercise of the G8 stage-artifact-verification gate against a synthesized
  false BUILD claim.

## Related

- [Pipeline State Machine](pipeline-state-machine.md) — underlying stage status
  storage.
- [SDLC Pipeline State](sdlc-pipeline-state.md) — local session state tracking
  (`sdlc_session_ensure`, `sdlc_stage_marker`).
- [SDLC Stage Tracking](sdlc-stage-tracking.md) — stored-state-only stage
  completion (no artifact inference).
- [SDLC Tool Resolver](sdlc-tool-resolver.md) — `sdlc-tool` cwd-independent
  wrapper and cross-repo plan/session resolution that `_verify_stage_artifacts`
  and `_parse_revision_applied_at` build on.
- Related issues: #704 (stage_states as source of truth), #729 (anti-skip),
  #941 (local session tracking), #1005 (PM-level pipeline completion guards),
  #1036 (the regression G1-G5 fix), #1043 (G6 terminal-state fast-path and
  self-authored PR review loop fix), #1638 (verdict normalization),
  #1640 (plan existence evidence gate), #1641 (stale-verdict supersession),
  #1668 (CRITIQUE empty-verdict re-dispatch, row 2c), #1687 (REVIEW empty-verdict
  re-dispatch, row 8c), #1932 (row 8d crashed re-review recovery, row 3/G1/G5
  open-PR step-asides, row 9 APPROVED-verdict gate), #2003 (run-id ownership,
  live-ref PR resolution, merge-gate substrate this redesign builds on),
  #1871 (G7-before-G5/G6 guard-precedence fix), #1760 (`revision_applied_at`
  event-scoped convergence latch), #1267 (G8 stage-advance artifact
  verification gate) — the last three tracked together as the SDLC router
  convergence redesign (#2029).
