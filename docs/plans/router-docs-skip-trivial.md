---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-07-24
tracking: https://github.com/tomcounsell/ai/issues/1799
last_comment_id:
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
   **row 9s** predicate `_rule_review_approved_docs_skippable` fires when review is
   APPROVED, DOCS not done, and `context["pr_shape"] ∈ {docs-only, lockfile-only}`.
   It returns a `Dispatch(skill="/do-merge", reason="DOCS skipped: trivial tier
   (shape=<X>)", docs_skip=True)`. Ordered **before** row 9 (`/do-docs`).
4. **Orchestration seam** (`.claude/skills/sdlc/SKILL.md` Step 4): on a dispatch
   carrying the DOCS-skip signal, the `/sdlc` step first records
   `sdlc-tool stage-marker --stage DOCS --status completed` with a
   `skipped: trivial` note, then invokes `/do-merge`.
5. **Merge gate** (`/do-merge` → `tools/merge_predicate.py`): DOCS is now
   `completed`, so group (b) passes and the merge proceeds.
6. **Output**: PR merges; dispatch log shows the explicit skip reason.

## Architectural Impact

- **New dependencies**: None new — reuses `scripts/pr_shape_classify.py`.
- **Interface changes**: `Dispatch` gains an optional `docs_skip: bool = False`
  flag (or the skip is signalled by `row_id == "9s"`). `context` gains a
  `pr_shape` key.
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
- **Row 9s dispatch rule** (`agent/sdlc_router.py`): the router-level skip
  decision — matches APPROVED + DOCS-not-done + trivial shape, returns `/do-merge`
  with an explicit skip reason. Ordered before row 9.
- **DOCS-skip marker seam** (`.claude/skills/sdlc/SKILL.md` Step 4): records
  `DOCS completed (skipped: trivial)` before invoking `/do-merge`, satisfying the
  #1944 merge-gate DOCS precondition.
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

- **Predicate `_rule_review_approved_docs_skippable`** (row 9s): reuse
  `_rule_review_approved_docs_not_done`'s exact guards (pr_number set, REVIEW ==
  completed, recorded APPROVED verdict, head_sha-fresh, DOCS not completed) and
  add `context.get("pr_shape") in {"docs-only", "lockfile-only"}`. Ordered
  immediately before row 9 so the trivial subset is intercepted and the
  complementary set falls through to `/do-docs` unchanged. Disjoint from rows
  8d/8e/8f (those require *no* / *stale* verdict; 9s requires a fresh APPROVED
  verdict) and from row 10 (which requires DOCS already completed).
- **Fail-closed default**: `_build_context` sets `context["pr_shape"] = "feature"`
  on any classifier error, empty diff, or non-allowlisted shape. Absent
  `pr_shape` (older callers, tests) is treated as non-trivial → never skips.
- **`Dispatch.docs_skip`**: add an optional boolean to the `Dispatch` dataclass
  so the orchestration layer can detect the skip without string-matching the
  reason. Default `False` keeps every existing dispatch unchanged.
- **Marker-write seam**: the recommended seam is the `/sdlc` SKILL.md Step 4
  orchestration layer (where dispatch recording already happens), keeping the
  router pure and `next-skill` read-only. See Open Questions for the alternative
  (next-skill side-effect) and why it is dispreferred.
- **Idempotency**: if the run crashes between the router decision and the marker
  write, re-invoking `/sdlc` re-fires row 9s (idempotent); once the marker is
  written, row 10 fires `/do-merge` directly. The loop converges and is
  G4-bounded.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `_build_context` shape block wraps the `pr_shape_classify`
  subprocess call in try/except that fails **closed to `feature`** and logs at
  warning level. Add a test asserting: on a raised/timed-out classifier, the
  context is `pr_shape == "feature"` and row 9s does NOT fire (dispatches
  `/do-docs`). No silent `except: pass`.
- [ ] Assert a malformed classifier JSON payload → `feature` → no skip.

### Empty/Invalid Input Handling
- [ ] Empty diff / empty file list → classifier returns `feature` (its documented
  defensive default) → no skip. Add a router unit test with `pr_shape` absent and
  with `pr_shape == "feature"`; both must dispatch `/do-docs`.
- [ ] `pr_shape == "mixed"` (claimed-safe shape that touched non-allowlisted
  paths) → no skip. Explicit test.

### Error State Rendering
- [ ] The skip reason string is user/log-visible: assert the dispatch `reason`
  contains `"DOCS skipped"` and the concrete shape. Assert the recorded DOCS
  marker note contains `skipped: trivial` so a human reading stage state sees
  *why* DOCS shows completed with no `/do-docs` run.

## Test Impact

- [ ] `tests/unit/test_sdlc_skill_md_parity.py::test_dispatch_rules_cover_expected_row_ids`
  — UPDATE: add `"9s"` to the `expected` row-id set.
- [ ] `tests/unit/test_sdlc_router_decision.py` (and/or `test_sdlc_router.py`) —
  UPDATE/ADD: new cases for row 9s firing on `docs-only`/`lockfile-only` and
  stepping aside for `feature`/`mixed`/absent-shape. Verify row 9 still owns the
  non-trivial APPROVED-docs-not-done state.
- [ ] `tests/unit/test_sdlc_next_skill.py` — ADD: `_build_context` sets
  `pr_shape` correctly (trivial, feature, fail-closed on classifier error) and
  only when pr_number set + REVIEW APPROVED + DOCS not done.
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
  (row 9s): trivial shapes route REVIEW→MERGE, recording DOCS as
  `completed (skipped: trivial)`.
- [ ] Add a short subsection to `docs/features/pr-shape-aware-merge-gates.md`
  tying DOCS into the shape matrix (docs-only/lockfile-only ⇒ DOCS skipped).
- [ ] Update `.claude/skills/sdlc/SKILL.md`: document row 9s in the Step 4
  dispatch narrative, add the DOCS-skip marker-write step, and note the G6/row-10
  interplay (they still require DOCS completed; the skip satisfies that via the
  recorded marker).

### Inline Documentation
- [ ] Docstring on `_rule_review_approved_docs_skippable` stating the human-readable
  state ("Review APPROVED, docs NOT done, PR shape is trivial (docs-only/lockfile-only)
  — skip DOCS, route to merge") — required by the parity test's docstring check.
- [ ] Comment in `_build_context` explaining the fail-closed-to-feature default
  and the gate condition.

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
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` passes with `"9s"` in the expected set.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`) — note: this PR is itself a `feature`
  shape (touches `agent/`, `tools/`, tests) and will run DOCS normally.

## Team Orchestration

### Team Members

- **Builder (router-skip)**
  - Name: router-skip-builder
  - Role: implement row 9s + `Dispatch.docs_skip` + `_build_context` shape
    producer + SKILL.md skip seam
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
- Wrap in try/except; fail closed to `"feature"` on any error/timeout/malformed
  JSON; log at warning. Never raise.

### 2. Router row 9s + Dispatch flag
- **Task ID**: build-row-9s
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_router_decision.py, tests/unit/test_sdlc_router.py
- **Informed By**: spike-1 (shape membership, not tier), spike-4 (marker required)
- **Assigned To**: router-skip-builder
- **Agent Type**: builder
- **Parallel**: true
- Add optional `docs_skip: bool = False` to the `Dispatch` dataclass.
- Add `_rule_review_approved_docs_skippable` (clone row 9's guards + `pr_shape ∈
  {docs-only, lockfile-only}`), attach a docstring, and register row 9s in
  `DISPATCH_RULES` immediately BEFORE row 9. Dispatch `/do-merge`,
  `reason="DOCS skipped: trivial tier (shape=<X>)"`, `docs_skip=True`.

### 3. Orchestration marker-write seam
- **Task ID**: build-skill-seam
- **Depends On**: build-row-9s
- **Assigned To**: router-skip-builder
- **Agent Type**: builder
- **Parallel**: false
- In `.claude/skills/sdlc/SKILL.md` Step 4, on a dispatch carrying the DOCS-skip
  signal, record `sdlc-tool stage-marker --stage DOCS --status completed` with a
  `skipped: trivial` note (carry `--run-id`), then invoke `/do-merge`.

### 4. Tests
- **Task ID**: build-tests
- **Depends On**: build-context-shape, build-row-9s
- **Assigned To**: router-skip-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `"9s"` to `test_dispatch_rules_cover_expected_row_ids` expected set.
- Router-decision cases: docs-only/lockfile-only → `/do-merge`+docs_skip;
  feature/mixed/absent-shape → `/do-docs`; disjointness from rows 8d/8e/8f/10.
- `_build_context` cases: shape set correctly + fail-closed on classifier error +
  gate condition (only fires in the terminal APPROVED/DOCS-not-done state).

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
| Parity row present | `python -c "from agent.sdlc_router import DISPATCH_RULES; assert '9s' in {r.row_id for r in DISPATCH_RULES}"` | exit code 0 |
| Row 9s dispatches merge | `python -c "from agent.sdlc_router import DISPATCH_RULES; r=[x for x in DISPATCH_RULES if x.row_id=='9s'][0]; assert x if False else r.skill=='/do-merge'"` | exit code 0 |
| Skip reason wired | `grep -c "DOCS skipped" agent/sdlc_router.py` | output > 0 |
| Allowlist is narrow | `grep -c "small-patch\|feature\|mixed" agent/sdlc_router.py` | match count == 0 |
| Lint clean | `python -m ruff check agent/sdlc_router.py tools/sdlc_next_skill.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/sdlc_router.py tools/sdlc_next_skill.py` | exit code 0 |

(The "Allowlist is narrow" row is an anti-criterion: it fails if row 9s ever
references a non-trivial shape name in `sdlc_router.py`. Demonstrate red-state by
temporarily adding `small-patch` to the predicate before implementing, then
confirm it goes green once only `docs-only`/`lockfile-only` remain.)

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Marker-write seam** — recommended: the `/sdlc` SKILL.md Step 4 orchestration
   layer records the `DOCS completed (skipped: trivial)` marker before invoking
   `/do-merge` (keeps the router pure and `next-skill` read-only). Alternative:
   `tools/sdlc_next_skill.py` writes the marker as a side effect when row 9s
   matches (fewer moving parts, but makes a read-mostly path mutate state). Do you
   agree with the SKILL.md seam, or prefer the next-skill side-effect?

2. **Skip signal on `Dispatch`** — plan adds `docs_skip: bool` so the
   orchestration layer detects the skip without string-matching the reason.
   Acceptable, or prefer to key purely off `row_id == "9s"` and add no field?

3. **`lockfile-only` merge safety** — a bare `uv.lock` bump skips DOCS and routes
   to merge. Confirm that is desired (no changelog/docs note wanted for lockfile
   bumps), or should `lockfile-only` still run DOCS and only `docs-only` skip?
