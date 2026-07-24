---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-07-24
tracking: https://github.com/tomcounsell/ai/issues/1799
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-24T12:13:30Z
---

# Router-level DOCS-skip for doc-free trivial PRs

## Problem

DOCS is the only SDLC pipeline stage with no shape/effort tiering. CRITIQUE has
LITE/FULL (#1714), REVIEW gates the cross-vendor judge on PR shape (#1626), and
`scripts/pr_shape_classify.py` drives shape-aware merge/test gates. But every PR
— including a genuinely doc-free `docs-only` or `lockfile-only` change — still
routes through `/do-docs` at router row 9, spawning a Sonnet dev session that has
nothing to do (there is no code whose docs could drift).

**Current behavior:**
At router row 9 (`_rule_review_approved_docs_not_done`), any PR with an APPROVED
review and `DOCS != completed` dispatches `/do-docs`, regardless of PR shape. A
docs-only or lockfile-only PR wastes a full DOCS turn before it can reach the
merge gate.

**Desired outcome:**
When a PR is APPROVED, DOCS is not yet done, AND its shape is in the narrow
allowlist `{docs-only, lockfile-only}`, the router skips `/do-docs`: it records
DOCS as `completed (skipped: trivial)`, routes straight to the merge gate, and
makes the skip explicit in the router decision `reason` (e.g. `"DOCS skipped:
trivial tier (shape=docs-only)"`) so it is visible in dispatch logs. Any
classifier failure or ambiguity falls back to `feature`, which never skips —
safe by default.

## Freshness Check

**Baseline commit:** `3c1436e9f`
**Issue filed at:** 2026-06-26T06:44:14Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/sdlc_router.py` — issue cites "dispatch rows, guard G6". Confirmed:
  row 9 = `_rule_review_approved_docs_not_done` → `/do-docs`; row 10 =
  `_rule_ready_to_merge`; `guard_g6_terminal_merge_ready` (G6). Line numbers
  drifted heavily (the file grew rows 8e/8f + head_sha gates) but the named
  symbols all still exist.
- `.claude/skills/sdlc/SKILL.md` — issue cites "Step 3.5 guard table + dispatch
  table, guard G6". Confirmed present; Step 4's dispatch table is now
  tool-delegated (narrative rows only, no hand-authored table — enforced by
  `test_sdlc_skill_md_parity.py`).
- `docs/features/pipeline-graph.md` — confirmed present; documents the
  `("DOCS","success"): "MERGE"` edge and the two-tier model split.
- `scripts/pr_shape_classify.py` — confirmed; produces `docs-only`,
  `lockfile-only`, `small-patch`, `mixed`, `feature`; fails closed to `feature`.

**Cited sibling issues/PRs re-checked:**
- #1628 (E1–E5 effort tiering) — CLOSED / COMPLETED (superseded). This issue is
  the one net-new leftover.
- #1714 (CRITIQUE tiering), #1626 (shape-gated REVIEW), #1283 (PR-shape-aware
  merge gates, origin of `pr_shape_classify.py`) — all shipped.
- #1944 (do-merge gates on DOCS-stage completion) — merged 2026-07-09. **Directly
  relevant:** `tools/merge_predicate.py` group (b) now hard-gates on `DOCS ==
  completed`, which is precisely why a DOCS skip must *record* a completed marker
  rather than merely route around `/do-docs`.

**Commits on main since issue was filed (touching referenced files):**
- Router: #2003, #2062, #2076, #2144, #2078, #2029, #1954, #1941 — reworked
  ownership/verdict/head_sha gating and added rows 8e/8f. None address DOCS
  tiering; the row-9/row-10/G6 DOCS-completed contract is unchanged.
- `#1944` (merge_predicate DOCS gate) — changed the *root constraint*: a skip
  now requires a recorded DOCS marker. Folded into the design.

**Active plans in `docs/plans/` overlapping this area:** None. (Nearest is the
completed `do-merge-docs-stage-gate.md`, which is #1944 — a dependency, not an
overlap.)

**Notes:** The issue's premise of an existing `change_tier == trivial` signal is
the only material drift — see the Revised bucket of the issue's Recon Summary.
There is no `change_tier` in the codebase; the condition collapses to
`pr_shape ∈ {docs-only, lockfile-only}`. Plan is built on the corrected premise.

## Prior Art

- **#1283**: PR-shape-aware merge gates — shipped `scripts/pr_shape_classify.py`
  and the `docs-only`/`lockfile-only`/`small-patch` shape taxonomy. This plan
  reuses that classifier verbatim; no new classification logic.
- **#1626**: Shape-gated cross-vendor REVIEW — precedent for shape driving a
  stage decision. Same classifier, invoked the same way (`python -m
  scripts.pr_shape_classify --pr N`).
- **#1944**: `do-merge` gates on DOCS-stage completion — establishes that
  `DOCS == completed` is the authoritative merge precondition. The DOCS-skip must
  satisfy this by recording a completed marker.
- **#1628**: Effort-tiered SDLC (E1–E5) — CLOSED/COMPLETED, superseded. This
  issue is its one net-new surviving behavior; the broader classifier-unification
  refactor was explicitly dropped as not worth the appetite.

No prior attempt to skip DOCS exists — greenfield behavior on a well-worn
substrate.

## Research

No relevant external findings — this is purely internal SDLC-router work
(no external libraries, APIs, or ecosystem patterns). Proceeding with codebase
context.

## Spike Results

All spikes resolved by code-read recon (2026-07-24). No prototypes needed.

### spike-1: Does a `change_tier == trivial` signal exist?
- **Assumption**: "The router already has a `change_tier` classifier producing a `trivial` tier."
- **Method**: code-read (`grep -rn 'change_tier\|trivial\|TIER' agent/ tools/ scripts/`)
- **Finding**: No. `change_tier` appears nowhere. `pr_shape_classify.py` produces
  *shapes*, not tiers. The condition collapses to `pr_shape ∈ {docs-only, lockfile-only}`.
- **Confidence**: high
- **Impact on plan**: The predicate is a shape-membership check, not a tier read.
  No new "tier" abstraction is introduced.

### spike-2: Is `pr_shape` already in the router's `context` dict?
- **Assumption**: "The router can read PR shape from its context today."
- **Method**: code-read (`tools/sdlc_next_skill.py::_build_context`)
- **Finding**: No. `_build_context` assembles `pr_head_sha`, plan-hash, branch,
  and stage-artifact signals — no shape. The router is import-free of `tools/`
  (`test_architectural_constraints.py`), so shape must be injected via context.
- **Confidence**: high
- **Impact on plan**: Adds a `context["pr_shape"]` computation in `_build_context`,
  fail-closed to `feature`, mirroring the existing `pr_head_sha` fail-closed shape.

### spike-3: How is shape classified live, and is it persisted?
- **Assumption**: "Shape is persisted in SDLC meta and readable cheaply."
- **Method**: code-read (`docs/sdlc/do-merge.md`, `docs/sdlc/do-pr-review.md`, grep)
- **Finding**: Shape is computed on demand via `python -m scripts.pr_shape_classify
  --pr N` (cached in `data/pr_shape_verdict_cache.json`); **not** persisted in
  meta. Same invocation is already used by `/do-merge` and `/do-pr-review`.
- **Confidence**: high
- **Impact on plan**: `_build_context` invokes the same module. The call is gated
  to fire only when it matters (pr_number set, REVIEW APPROVED, DOCS not done) so
  it adds no cost to the common non-mergeable path. Cache makes the repeat call cheap.

### spike-4: Do row 10 and G6 require `DOCS == completed`?
- **Assumption**: "The merge path can proceed with DOCS skipped/unmarked."
- **Method**: code-read (`_rule_ready_to_merge`, `guard_g6_terminal_merge_ready`,
  `tools/merge_predicate.py` group (b), #1944)
- **Finding**: Both row 10 and G6 require `DOCS == completed`; #1944's merge
  predicate group (b) independently hard-gates on it. A skip that does not record
  a DOCS marker would deadlock at the merge gate.
- **Confidence**: high
- **Impact on plan**: The skip MUST record `DOCS completed (skipped: trivial)`.
  This is the load-bearing decision (see Technical Approach + Open Questions).

## Data Flow

1. **Entry point**: PM/dev session invokes `/sdlc` (Step 4), which calls
   `sdlc-tool next-skill --issue-number N` → `tools/sdlc_next_skill.py`.
2. **Context assembly** (`_build_context`): when `pr_number` is set, REVIEW is
   APPROVED, and DOCS is not `completed`, invoke `python -m scripts.pr_shape_classify
   --pr N`; set `context["pr_shape"]` (fail-closed to `"feature"` on any error).
3. **Router decision** (`agent/sdlc_router.py::decide_next_dispatch`): the new
   **9s rules** fire when review is APPROVED, DOCS not done, and `context["pr_shape"]`
   is `docs-only` (rule `9s-docs-only`) or `lockfile-only` (rule `9s-lockfile-only`).
   The matching rule returns a `Dispatch(skill="/do-merge", reason="DOCS skipped:
   trivial tier (shape=<X>)")` — the `reason` is the rule's own fixed string, no
   runtime field. Ordered **before** row 9 (`/do-docs`) and **after** rows 8d/8e/8f.
4. **Orchestration seam** (`.claude/skills/sdlc/SKILL.md` Step 4): on a dispatch
   whose serialized `row_id.startswith("9s")`, the `/sdlc` step first records
   `sdlc-tool stage-marker --stage DOCS --status completed` with a
   `skipped: trivial` note, then invokes `/do-merge`.
5. **Merge gate** (`/do-merge` → `tools/merge_predicate.py`): DOCS is now
   `completed`, so group (b) passes and the merge proceeds.
6. **Output**: PR merges; dispatch log shows the explicit skip reason.

## Architectural Impact

- **New dependencies**: None new — reuses `scripts/pr_shape_classify.py`.
- **Interface changes**: `Dispatch` is unchanged. The skip is signalled purely by
  the serialized `row_id` (`9s-docs-only` / `9s-lockfile-only`); no new dataclass
  field. `context` gains a `pr_shape` key. `DISPATCH_RULES` gains two rows.
- **Coupling**: `tools/sdlc_next_skill.py` gains a dependency on
  `scripts/pr_shape_classify.py` (already an accepted shell-out elsewhere). The
  router stays import-free of `tools/` — shape arrives via `context`, preserving
  `test_architectural_constraints.py`.
- **Data ownership**: DOCS stage-marker ownership is unchanged (written via
  `sdlc-tool stage-marker`); the skip writes a normal completed marker with a note.
- **Reversibility**: High. Deleting row 9s + the `context["pr_shape"]` block +
  the SKILL.md skip step restores exact prior behavior. No schema/data migration.

## Appetite

**Size:** Medium (leaning small)

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the marker-write seam — see Open Questions)
- Review rounds: 1

The coding is small; the care is in row ordering (disjointness from rows
8*/9/10), the fail-closed shape default, and choosing the marker-write seam.

## Prerequisites

No external prerequisites — this work has no new secrets, services, or
third-party access. All tooling (`pr_shape_classify.py`, `sdlc-tool`) already
exists.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Shape classifier importable | `python -c "import scripts.pr_shape_classify"` | Reused classifier present |
| sdlc-tool on PATH | `sdlc-tool --help >/dev/null 2>&1 && echo ok` | Stage-marker writes |

## Solution

### Key Elements

- **`context["pr_shape"]` producer** (`tools/sdlc_next_skill.py::_build_context`):
  computes the PR shape via the existing classifier, only when it can matter,
  fail-closed to `feature`.
- **Two 9s dispatch rules** (`agent/sdlc_router.py`): `9s-docs-only` and
  `9s-lockfile-only` — the router-level skip decision. Each matches APPROVED +
  DOCS-not-done + its own trivial shape and returns `/do-merge` with a fixed,
  shape-specific skip reason (`shape=docs-only` / `shape=lockfile-only`). Both
  ordered before row 9. Two rules because a `DispatchRule.reason` is a static
  string with no interpolation seam.
- **DOCS-skip marker seam** (`.claude/skills/sdlc/SKILL.md` Step 4): on a dispatch
  whose `row_id.startswith("9s")`, records `DOCS completed (skipped: trivial)`
  before invoking `/do-merge`, satisfying the #1944 merge-gate DOCS precondition.
- **Docs**: `docs/features/pipeline-graph.md` documents the skip edge; a short
  entry in the pr-shape-aware-merge-gates feature doc ties DOCS into the shape matrix.

### Flow

REVIEW APPROVED (docs-only PR) → `/sdlc` Step 4 calls `next-skill` →
`_build_context` classifies shape=`docs-only` → router row 9s returns `/do-merge`
(reason: "DOCS skipped: trivial tier (shape=docs-only)") → `/sdlc` records
`DOCS completed (skipped: trivial)` → invokes `/do-merge` → merge gate passes
(DOCS completed) → PR merged.

Contrast (feature PR): shape=`feature` → row 9s does not match → row 9 fires
`/do-docs` as before.

### Technical Approach

- **Two dispatch rules, one per allowlisted shape** (`9s-docs-only`,
  `9s-lockfile-only`): a single `DispatchRule` has a *static* `reason: str` that
  is copied verbatim into the returned `Dispatch` (fields are `row_id,
  state_predicate, skill, reason` in `agent/sdlc_router.py`) — it has no seam to
  interpolate the runtime shape token. Since Success Criteria and Error State
  Rendering require the reason to carry the concrete shape (`shape=docs-only` vs
  `shape=lockfile-only`), register **two** rules, each with its own
  shape-specific `state_predicate` and its own fixed reason string:
  - `9s-docs-only`: predicate matches when `context.get("pr_shape") ==
    "docs-only"`; `reason="DOCS skipped: trivial tier (shape=docs-only)"`.
  - `9s-lockfile-only`: predicate matches when `context.get("pr_shape") ==
    "lockfile-only"`; `reason="DOCS skipped: trivial tier (shape=lockfile-only)"`.
  Both dispatch `/do-merge`. Both reuse `_rule_review_approved_docs_not_done`'s
  guards: `pr_number` set, `REVIEW == STATUS_COMPLETED`, `REVIEW_APPROVED in
  normalize_verdict(...)`, `DOCS not completed`. **No head_sha-freshness guard**
  — `_rule_review_approved_docs_not_done` has none (only row 10 / G6 call
  `_review_verdict_head_is_stale`); do not clone a freshness predicate that does
  not exist.
- **Row ordering, not a predicate guard, provides freshness disjointness**: the
  two 9s rules are registered immediately **before** row 9 and **after** rows
  8d/8e/8f. A stale APPROVED verdict is intercepted by row 8f (`/do-pr-review`)
  *before* control reaches the 9s rules, so a stale-verdict docs-only PR routes to
  8f, never to 9s. This is an **ordering invariant** of `DISPATCH_RULES`, not a
  guard inside the 9s predicates. Disjointness from row 10 holds because row 10
  requires DOCS already `completed` while 9s requires DOCS *not* completed.
- **Fail-closed default**: `_build_context` sets `context["pr_shape"] = "feature"`
  on any classifier error, timeout, empty diff, or non-allowlisted shape. Absent
  `pr_shape` (older callers, tests) matches neither 9s predicate → never skips.
- **No `Dispatch.docs_skip` field**: the skip signal is the already-serialized
  `row_id`. The DISPATCH EMIT BLOCK in `tools/sdlc_next_skill.py` returns
  `{skill, reason, row_id, dispatched}`, so SKILL.md Step 4 can observe `row_id`
  directly; a `docs_skip` boolean would never be serialized and would be dead as
  specified. The orchestration seam keys off `row_id.startswith("9s")` (matches
  both `9s-docs-only` and `9s-lockfile-only`). The `Dispatch` dataclass is left
  unchanged.
- **Marker-write seam**: the `/sdlc` SKILL.md Step 4 orchestration layer (where
  dispatch recording already happens) records the DOCS-completed marker before
  invoking `/do-merge`, keeping the router pure and `next-skill` read-only.
  Resolved: this is the chosen seam (see Resolved Decisions) — #1944 hard-gates
  merge on `DOCS == completed`, so the marker MUST be written on the skip path.
- **Idempotency**: if the run crashes between the router decision and the marker
  write, re-invoking `/sdlc` re-fires the matching 9s rule (idempotent); once the
  marker is written, row 10 fires `/do-merge` directly. The loop converges and is
  G4-bounded.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `_build_context` shape block calls the `pr_shape_classify`
  subprocess with an explicit `timeout=` (sourced from `config/settings.py`
  `TimeoutSettings`) and wraps it in try/except that fails **closed to `feature`**
  and logs at warning level. `subprocess.TimeoutExpired` is an `Exception`
  subclass, so the fail-closed `except Exception` catches it *only because*
  `timeout=` is set — without the argument the subprocess hangs and never raises,
  wedging the hot `next-skill` path. Add a test asserting: on a raised classifier,
  the context is `pr_shape == "feature"` and neither 9s rule fires (dispatches
  `/do-docs`). No silent `except: pass`.
- [ ] Add a distinct test that a **timed-out** (not merely raised) classifier —
  simulated by a stubbed subprocess that exceeds the configured `timeout=` and
  raises `subprocess.TimeoutExpired` — yields `pr_shape == "feature"` and no skip.
- [ ] Assert a malformed classifier JSON payload → `feature` → no skip.

### Empty/Invalid Input Handling
- [ ] Empty diff / empty file list → classifier returns `feature` (its documented
  defensive default) → no skip. Add a router unit test with `pr_shape` absent and
  with `pr_shape == "feature"`; both must dispatch `/do-docs`.
- [ ] `pr_shape == "mixed"` (claimed-safe shape that touched non-allowlisted
  paths) → no skip. Explicit test.

### Ordering / Freshness Disjointness
- [ ] A **stale-verdict `docs-only` PR** (APPROVED verdict whose head_sha no
  longer matches the PR head) routes to row 8f (`/do-pr-review`), NOT to a 9s
  rule. This confirms freshness disjointness is an ordering invariant (8f precedes
  9s), not a guard inside the 9s predicates. Assert the dispatched skill is
  `/do-pr-review`.

### Error State Rendering
- [ ] The skip reason string is user/log-visible: assert the dispatch `reason`
  contains `"DOCS skipped"` and the concrete shape. Assert the recorded DOCS
  marker note contains `skipped: trivial` so a human reading stage state sees
  *why* DOCS shows completed with no `/do-docs` run.

## Test Impact

- [ ] `tests/unit/test_sdlc_skill_md_parity.py::test_dispatch_rules_cover_expected_row_ids`
  — UPDATE: add `"9s-docs-only"` and `"9s-lockfile-only"` to the `expected`
  row-id set.
- [ ] `tests/unit/test_sdlc_router_decision.py` (and/or `test_sdlc_router.py`) —
  UPDATE/ADD: new cases for `9s-docs-only` firing on `docs-only`, `9s-lockfile-only`
  firing on `lockfile-only`, each dispatching `/do-merge` with a shape-specific
  reason; stepping aside for `feature`/`mixed`/absent-shape; and the stale-verdict
  `docs-only` → row 8f ordering case. Verify row 9 still owns the non-trivial
  APPROVED-docs-not-done state.
- [ ] `tests/unit/test_sdlc_next_skill.py` — ADD: `_build_context` sets
  `pr_shape` correctly (trivial, feature, fail-closed on classifier error AND on
  `subprocess.TimeoutExpired`) and only when pr_number set + REVIEW APPROVED +
  DOCS not done.
- [ ] `tests/integration/test_do_merge_shape_routing.sh` — REVIEW (no change
  expected): confirm the DOCS-skip path still merges a docs-only PR end-to-end;
  extend only if it does not already exercise the skip.

No other existing tests modify the row-9/row-10/G6 contract, so they remain
valid. New behavior is additive (row 9s intercepts a strict subset of row 9's
prior domain).

## Rabbit Holes

- **Building a real `change_tier` abstraction.** Do not. The condition is a
  two-shape membership check. Introducing a tier enum invites the #1628
  classifier-unification refactor that was deliberately dropped.
- **Persisting shape into SDLC meta.** Tempting for "cleanliness," but shape is
  head-sha-dependent and already cached on disk; a meta field would add a
  staleness-invalidation problem. Compute on demand via the existing classifier.
- **Making the router impure to write the marker.** The router must stay a pure
  decision function (architectural constraint test). Keep the marker write in the
  orchestration/tool layer.
- **Extending the allowlist to `small-patch`.** Out of scope and unsafe — a
  small code patch *can* need docs. Only `docs-only`/`lockfile-only`.

## Risks

### Risk 1: Skipping DOCS on a PR that genuinely needed a doc update
**Impact:** A code change ships without a docs cascade.
**Mitigation:** The allowlist is `docs-only`/`lockfile-only` only. `docs-only`
means *only* docs/markdown files changed (no code whose docs could drift);
`lockfile-only` is strictly `uv.lock`. Anything with a `.py`/config change
classifies as `mixed` or `feature` and never skips. The classifier fails closed
to `feature`. Anti-criterion in Verification asserts no code-shape PR can reach
the skip predicate.

### Risk 2: Deadlock at the merge gate if the DOCS marker is not recorded
**Impact:** Row 10 / G6 / merge_predicate group (b) block forever (DOCS != completed).
**Mitigation:** The marker write is a mandatory, ordered step in the skip path
(SKILL.md Step 4, before `/do-merge`). Integration test merges a docs-only PR
end-to-end. Idempotent re-fire on crash (row 9s again → marker → merge).

### Risk 3: `pr_shape_classify` subprocess cost/latency in the hot router path
**Impact:** Every `next-skill` call could pay a classifier subprocess.
**Mitigation:** Gate the classification to fire only when pr_number is set AND
REVIEW is APPROVED AND DOCS is not completed — a narrow terminal state. The
on-disk shape cache (`data/pr_shape_verdict_cache.json`) makes repeat calls cheap.
The subprocess carries an explicit `timeout=` (from `config/settings.py`
`TimeoutSettings`) so a hung classifier fails closed to `feature` instead of
wedging the router path.

## Race Conditions

No new race conditions identified. `next-skill` is invoked serially by a single
`/sdlc` step; the router is a pure synchronous function; the DOCS marker write is
a single idempotent `sdlc-tool stage-marker` call already serialized under the
issue-ownership lock (run_id). A crash between decision and marker write re-enters
row 9s idempotently. No shared mutable state is introduced.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1628] The broader E1–E5 effort-tiering / classifier-unification
  refactor — closed COMPLETED/superseded; this plan ships only the one net-new
  DOCS-skip behavior.

Nothing else deferred — the shape producer, the row, the marker seam, tests, and
docs are all in scope for this plan.

## Update System

No update system changes required. This is purely internal SDLC-router logic;
`agent/sdlc_router.py`, `tools/sdlc_next_skill.py`, and `.claude/skills/sdlc/SKILL.md`
are already deployed with the repo. No new dependency, config file, or migration.

## Agent Integration

No agent integration required. The router and `sdlc-tool` are consumed by the
`/sdlc` pipeline, not by the Telegram bridge or an MCP tool surface. There is no
new CLI entry point (reuses `python -m scripts.pr_shape_classify` and `sdlc-tool
stage-marker`) and no bridge import. The `test_sdlc_skill_md_parity.py` parity
test is the "integration" guarantee that SKILL.md and the Python rules stay in sync.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/pipeline-graph.md` to document the DOCS-skip edge
  (rules `9s-docs-only` / `9s-lockfile-only`): trivial shapes route REVIEW→MERGE,
  recording DOCS as `completed (skipped: trivial)`.
- [ ] Add a short subsection to `docs/features/pr-shape-aware-merge-gates.md`
  tying DOCS into the shape matrix (docs-only/lockfile-only ⇒ DOCS skipped).
- [ ] Update `.claude/skills/sdlc/SKILL.md`: document the `9s-docs-only` /
  `9s-lockfile-only` rules in the Step 4 dispatch narrative, add the DOCS-skip
  marker-write step (keyed off `row_id.startswith("9s")`), and note the G6/row-10
  interplay (they still require DOCS completed; the skip satisfies that via the
  recorded marker).

### Inline Documentation
- [ ] Docstring on each 9s predicate stating the human-readable state ("Review
  APPROVED, docs NOT done, PR shape is docs-only [resp. lockfile-only] — skip
  DOCS, route to merge") — required by the parity test's docstring check.
- [ ] Comment in `_build_context` explaining the fail-closed-to-feature default,
  the explicit subprocess `timeout=`, and the gate condition.

## Success Criteria

- [ ] A `docs-only` PR with an APPROVED review and `DOCS != completed` routes to
  `/do-merge` (not `/do-docs`) with reason containing `"DOCS skipped"` and the shape.
- [ ] A `lockfile-only` PR behaves identically.
- [ ] A `feature` PR (and `mixed`, and absent `pr_shape`) still routes to
  `/do-docs` at row 9 — no behavior change.
- [ ] A classifier failure/ambiguity yields `pr_shape == "feature"` → no skip
  (fail-closed).
- [ ] The skip records a `DOCS completed (skipped: trivial)` stage marker; a
  subsequent merge-gate check (`tools/merge_predicate.py` group (b)) passes.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` passes with `"9s-docs-only"` and
  `"9s-lockfile-only"` in the expected set.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`) — note: this PR is itself a `feature`
  shape (touches `agent/`, `tools/`, tests) and will run DOCS normally.

## Team Orchestration

### Team Members

- **Builder (router-skip)**
  - Name: router-skip-builder
  - Role: implement the two 9s rules + `_build_context` shape producer (with
    subprocess `timeout=`) + SKILL.md skip seam (keyed off `row_id`)
  - Agent Type: builder
  - Domain: async/subprocess (classifier shell-out fail-closed)
  - Resume: true

- **Builder (tests)**
  - Name: router-skip-tester
  - Role: parity-set update + router-decision cases + next_skill context cases
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: router-skip-validator
  - Role: verify disjointness, fail-closed default, marker seam, all success criteria
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: router-skip-docs
  - Role: pipeline-graph.md + pr-shape-aware-merge-gates.md + SKILL.md narrative
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Shape producer in context assembly
- **Task ID**: build-context-shape
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_next_skill.py
- **Informed By**: spike-2, spike-3 (classifier is on-demand, not in context)
- **Assigned To**: router-skip-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tools/sdlc_next_skill.py::_build_context`, when `pr_number` is set AND
  REVIEW is APPROVED AND DOCS is not `completed`, invoke `python -m
  scripts.pr_shape_classify --pr N` and set `context["pr_shape"]`.
- Call it via `subprocess.run([...], capture_output=True, text=True,
  timeout=<knob>)` where the timeout is read from `config/settings.py`
  `TimeoutSettings` (add a named field there if none fits — see the config-timeout
  catalog). The explicit `timeout=` is load-bearing: without it a hung classifier
  never raises and the fail-closed `except` never fires, wedging the hot router
  path.
- Wrap in try/except `Exception` (which covers `subprocess.TimeoutExpired`); fail
  closed to `"feature"` on any error/timeout/malformed JSON; log at warning.
  Never raise, never `except: pass`.

### 2. Router 9s rules (two rows, one per trivial shape)
- **Task ID**: build-row-9s
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_router_decision.py, tests/unit/test_sdlc_router.py
- **Informed By**: spike-1 (shape membership, not tier), spike-4 (marker required)
- **Assigned To**: router-skip-builder
- **Agent Type**: builder
- **Parallel**: true
- Do NOT add a `docs_skip` field to `Dispatch` — the emit block only serializes
  `{skill, reason, row_id, dispatched}`, so such a field would be dead. Leave the
  `Dispatch` dataclass unchanged; the skip is signalled by `row_id`.
- Register **two** rules in `DISPATCH_RULES`, both immediately BEFORE row 9 and
  after rows 8d/8e/8f:
  - `9s-docs-only`: predicate = row 9's guards (`pr_number`, `REVIEW ==
    STATUS_COMPLETED`, `REVIEW_APPROVED in normalize_verdict(...)`, `DOCS not
    completed`) AND `context.get("pr_shape") == "docs-only"`. Skill `/do-merge`,
    `reason="DOCS skipped: trivial tier (shape=docs-only)"`.
  - `9s-lockfile-only`: same guards AND `context.get("pr_shape") ==
    "lockfile-only"`. Skill `/do-merge`, `reason="DOCS skipped: trivial tier
    (shape=lockfile-only)"`.
- Do NOT clone a head_sha-freshness guard — `_rule_review_approved_docs_not_done`
  has none. Freshness disjointness from row 8f is provided by rule ordering.
- Attach a docstring to each predicate stating the human-readable state (required
  by the parity test's docstring check).

### 3. Orchestration marker-write seam
- **Task ID**: build-skill-seam
- **Depends On**: build-row-9s
- **Assigned To**: router-skip-builder
- **Agent Type**: builder
- **Parallel**: false
- In `.claude/skills/sdlc/SKILL.md` Step 4, on a dispatch whose serialized
  `row_id.startswith("9s")` (i.e. `9s-docs-only` or `9s-lockfile-only`), record
  `sdlc-tool stage-marker --stage DOCS --status completed` with a `skipped:
  trivial` note (carry `--run-id`), then invoke `/do-merge`. Key off `row_id`,
  not a `docs_skip` field (which does not exist).

### 4. Tests
- **Task ID**: build-tests
- **Depends On**: build-context-shape, build-row-9s
- **Assigned To**: router-skip-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `"9s-docs-only"` and `"9s-lockfile-only"` to
  `test_dispatch_rules_cover_expected_row_ids` expected set.
- Router-decision cases: `docs-only` → `9s-docs-only` → `/do-merge` (reason
  contains `shape=docs-only`); `lockfile-only` → `9s-lockfile-only` → `/do-merge`
  (reason contains `shape=lockfile-only`); feature/mixed/absent-shape →
  `/do-docs`; stale-verdict `docs-only` → row 8f (`/do-pr-review`); disjointness
  from rows 8d/8e/8f/10.
- `_build_context` cases: shape set correctly + fail-closed on classifier error +
  fail-closed on `subprocess.TimeoutExpired` + gate condition (only fires in the
  terminal APPROVED/DOCS-not-done state).

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-skill-seam, build-tests
- **Assigned To**: router-skip-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/pipeline-graph.md` and
  `docs/features/pr-shape-aware-merge-gates.md`; finalize the SKILL.md narrative.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-context-shape, build-row-9s, build-skill-seam, build-tests, document-feature
- **Assigned To**: router-skip-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification checks; confirm every Success Criterion; confirm no
  code-shape PR can reach the skip predicate (anti-criterion).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sdlc_router.py tests/unit/test_sdlc_router_decision.py tests/unit/test_sdlc_skill_md_parity.py tests/unit/test_sdlc_next_skill.py -q` | exit code 0 |
| Parity rows present | `python -c "from agent.sdlc_router import DISPATCH_RULES; ids={r.row_id for r in DISPATCH_RULES}; assert {'9s-docs-only','9s-lockfile-only'} <= ids"` | exit code 0 |
| 9s rules dispatch merge | `python -c "from agent.sdlc_router import DISPATCH_RULES; rs=[x for x in DISPATCH_RULES if x.row_id.startswith('9s')]; assert len(rs)==2 and all(r.skill=='/do-merge' for r in rs)"` | exit code 0 |
| Skip reasons carry shape | `python -c "from agent.sdlc_router import DISPATCH_RULES; rs={x.row_id:x.reason for x in DISPATCH_RULES if x.row_id.startswith('9s')}; assert 'shape=docs-only' in rs['9s-docs-only'] and 'shape=lockfile-only' in rs['9s-lockfile-only']"` | exit code 0 |
| Allowlist is narrow (anchored) | `python -c "import inspect,agent.sdlc_router as m; rs=[x for x in m.DISPATCH_RULES if x.row_id.startswith('9s')]; src=''.join(inspect.getsource(r.state_predicate) for r in rs); assert 'small-patch' not in src and '\"mixed\"' not in src and '\"feature\"' not in src"` | exit code 0 |
| Lint clean | `python -m ruff check agent/sdlc_router.py tools/sdlc_next_skill.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/sdlc_router.py tools/sdlc_next_skill.py` | exit code 0 |

(The "Allowlist is narrow (anchored)" row is an anti-criterion scoped to the 9s
predicate *bodies* via `inspect.getsource`, not a whole-file grep — so a future
explanatory comment elsewhere in `sdlc_router.py` cannot false-fail it. It fails
if either 9s predicate ever references a non-trivial shape name. Demonstrate
red-state by temporarily adding `small-patch` to a predicate before implementing,
then confirm it goes green once only `docs-only`/`lockfile-only` remain.)

## Critique Results

<!-- Populated by /do-plan-critique (war room), 2026-07-24. FULL depth (doctrine path). Verdict: READY TO BUILD (with concerns). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness, Scope & Value, History & Consistency (all 3) | `Dispatch.docs_skip: bool` is a dead field as specified — the sole serialization point (the DISPATCH EMIT BLOCK, `tools/sdlc_next_skill.py`) returns only `{skill, reason, row_id, dispatched}`, so SKILL.md Step 4 can never observe `docs_skip`. If a builder keys the marker-write off it, the marker never writes and the PR deadlocks at the merge gate (Risk 2). | Revision pass | Drop `docs_skip` from Task 2; key the SKILL.md Step 4 seam off the already-serialized `row_id == "9s"` (resolves Open Question 2 toward the narrower change). If the field is kept instead, a task MUST add `"docs_skip": result.docs_skip` to the emit dict in `tools/sdlc_next_skill.py` (the `isinstance(result, Dispatch)` branch) plus a round-trip test. |
| CONCERN | Risk & Robustness | A single `DispatchRule` has a static `reason: str` and no seam to interpolate the runtime shape, yet Success Criteria + Error State Rendering require the reason to contain the concrete shape (`shape=docs-only` vs `shape=lockfile-only`). One row-9s rule cannot render both. | Revision pass | `DispatchRule` fields are `row_id, state_predicate, skill, reason` (`agent/sdlc_router.py`); `reason` is a plain str copied verbatim into the returned `Dispatch`. Register TWO rules (`9s-docs-only`, `9s-lockfile-only`) each with its own fixed reason, OR add a `reason_builder` callable to the dispatch machinery. Two-rule split keeps rows disjoint and satisfies `grep -c "DOCS skipped"`. |
| CONCERN | Risk & Robustness | "Fail closed to feature on timeout" is unimplementable as written: a `python -m scripts.pr_shape_classify` subprocess with no `timeout=` hangs without raising, so the mirrored `except Exception` never fires and the hot `next-skill` router path wedges. | Revision pass | In Task 1, call `subprocess.run([...], timeout=<knob>, capture_output=True)` with the timeout sourced from `config/settings.py` `TimeoutSettings`; `subprocess.TimeoutExpired` is an `Exception` subclass so the existing fail-closed `except Exception` catches it once `timeout=` is actually set. Add a test that a timed-out (not merely raised) classifier yields `pr_shape == "feature"`. |
| CONCERN | History & Consistency | The Technical Approach tells the builder to clone `_rule_review_approved_docs_not_done`'s "exact guards" and lists "head_sha-fresh" among them, then rests disjointness from row 8f on "9s requires a fresh APPROVED verdict." But row 9's predicate has NO freshness check (only row 10 / G6 call `_review_verdict_head_is_stale`). The rationale is factually wrong and could mislead the builder. | Revision pass | `_rule_review_approved_docs_not_done` checks only `pr_number`, `REVIEW == STATUS_COMPLETED`, `REVIEW_APPROVED in normalize_verdict(...)`, `DOCS not completed`. Reword the Technical Approach: freshness disjointness from 8f is an ORDERING invariant (row 8f precedes 9s precedes 9), not a predicate guard. Add a test asserting a stale-verdict docs-only PR routes to row 8f (`/do-pr-review`), never 9s. |
| NIT | Scope & Value | `lockfile-only` is the weaker half of the allowlist — a `uv.lock` bump can pull a behavior-changing dependency that warrants a docs note, unlike `docs-only`. | Optional | Consider shipping `docs-only` first and deferring `lockfile-only` (Open Question 3); if kept, state that dep-bump docs are intentionally out of scope. |
| NIT | Scope & Value | Payoff is modest — one saved Sonnet turn on a rare PR subset — vs. a new row + context producer + classifier shell-out + marker seam. | Optional | Acceptable for Medium appetite given the fail-closed design; call out expected trivial-PR frequency so the value is measured, not assumed. |
| NIT | History & Consistency | The "Allowlist is narrow" anti-criterion `grep -c "small-patch\|feature\|mixed" agent/sdlc_router.py == 0` is an unanchored whole-file substring grep that would false-fail on any future explanatory comment near row 9s. | Optional | Anchor the check to the predicate body — extract `_rule_review_approved_docs_skippable` and assert its membership literal is exactly `{"docs-only", "lockfile-only"}`, or grep only within the function's line range. |

---

## Resolved Decisions

Resolved during the post-critique revision pass (2026-07-24); folded into the
Technical Approach and Step by Step Tasks above.

1. **Marker-write seam → SKILL.md orchestration layer.** The `/sdlc` SKILL.md
   Step 4 layer records the `DOCS completed (skipped: trivial)` marker before
   invoking `/do-merge`, keyed off `row_id.startswith("9s")`. This keeps the router
   pure and `next-skill` read-only. #1944 hard-gates merge on `DOCS == completed`,
   so this marker write is mandatory on the skip path — the read-mostly
   next-skill side-effect alternative is rejected.

2. **Skip signal → `row_id`, no new field.** No `docs_skip: bool` is added to
   `Dispatch`. The emit block serializes only `{skill, reason, row_id,
   dispatched}`, so a boolean field would be dead as specified. The orchestration
   seam keys off the already-serialized `row_id.startswith("9s")`.

3. **`lockfile-only` stays in scope.** Both `docs-only` and `lockfile-only` skip
   DOCS (two rules: `9s-docs-only`, `9s-lockfile-only`). Dependency-bump docs
   (changelog notes for a `uv.lock` behavior change) are intentionally out of
   scope for this plan; the fail-closed-to-`feature` default plus the narrow
   two-shape allowlist keeps the risk bounded. If a future need arises, splitting
   `lockfile-only` back out is a one-rule deletion.
