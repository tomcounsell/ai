---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-25
tracking: https://github.com/tomcounsell/ai/issues/1628
last_comment_id: 4776602778
---

# Router-Visible `change_tier`: Unify Effort Tiering and Add the One Missing Stage-Skip

## Problem

The SDLC pipeline was originally **fixed and binary** — every change ran the identical
sequence regardless of size or risk. Issue #1628 (PAI E1–E5 inspiration) proposed an
effort/risk tier that scales scrutiny: trivial changes skip heavy stages, high-stakes
changes get mandatory extra review.

**Critical freshness finding (see Freshness Check below): most of #1628's premise has
already shipped via adjacent work that landed _after_ the issue was filed.** Three of the
four "no tiering anywhere" claims are now false:

- **CRITIQUE is already tiered** — #1714 (closed 2026-06-23) added a LITE/FULL triage to
  `do-plan-critique` with force-FULL on doctrine paths + Large appetite.
- **High-stakes REVIEW already gets extra scrutiny** — #1626 (closed 2026-06-23) shipped a
  cross-vendor (non-Claude) judge that fires when `pr_shape_classify.py` returns
  `shape == "feature"` (`do-pr-review/SKILL.md:742-756`). Trivial shapes never pay the cost.
- **MERGE/TEST are already shape-aware** — `pr_shape_classify.py` drives shape-aware merge
  gates and targeted test selection (`docs/features/pr-shape-aware-merge-gates.md`).

**What genuinely does NOT yet exist — the real remaining kernel of #1628:**

1. **No unified, router-visible tier signal.** Tiering today is *scattered and duplicated*:
   the critique skill recomputes doctrine-path detection locally; `do-pr-review` and the
   merge/test gates each recompute `pr_shape_classify` shape independently. The router
   (`agent/sdlc_router.py::decide_next_dispatch`) — the one component that actually decides
   *which stage runs next* — sees **no tier signal at all** in `_meta`. There is no single
   source of truth.
2. **DOCS is the one un-tiered pipeline stage.** Dispatch row 9
   (`_rule_review_approved_docs_not_done`) routes *every* approved PR through a full `/do-docs`
   stage before merge, including `docs-only` and `lockfile-only` PRs where there is nothing
   for `do-docs` to cascade. This is the last "trivial change still runs a heavy stage" case
   that the in-skill shape-awareness does not cover, because it is a *router dispatch* decision,
   not an in-skill behavior.

**Current behavior:** Effort scales *inside* CRITIQUE/REVIEW/MERGE skills via two independent
classifiers (doctrine-paths, `pr_shape_classify`), but the router itself is tier-blind and
DOCS runs unconditionally.

**Desired outcome:** A single `change_tier` signal lives in the router's `_meta` payload,
derived from the classifiers that already exist (not a new one), is recorded in verdicts for
observability, and lets the *router's dispatch decision* scale the one remaining un-tiered
stage (skip DOCS for genuinely doc-free trivial shapes). CRITIQUE stays a hard gate; ambiguity
always biases to the higher tier.

> **This plan is deliberately scoped DOWN from #1628's original text.** It does NOT rebuild
> critique tiering (#1714) or review tiering (#1626) — those shipped. See Open Questions #1:
> the human should confirm whether this reduced kernel is worth a Medium plan or whether
> #1628 should be closed as substantially superseded.

## Freshness Check

**Baseline commit:** `75d2de65` (main at plan time)
**Issue filed at:** 2026-06-11T06:16:27Z
**Disposition:** **Major drift** (adjacent systems now handle most of the concern)

**File:line references re-verified:**
- `agent/sdlc_router.py` — `GUARDS` G1–G7 (590–610), `DISPATCH_RULES` 16 rows (1033–1157),
  `decide_next_dispatch` (1177–1249). Confirmed: **no guard or dispatch rule reads any
  tier/effort/risk signal.** Downstream rows: 7→REVIEW, 9→DOCS, 10→MERGE. Still holds.
- `tools/sdlc_stage_query.py::_compute_meta` (325–416) / `_default_meta` (419–437) — builds the
  `_meta` dict; **no `change_tier` field.** Still holds.
- `tools/sdlc_meta_set.py::_KEY_REGISTRY` (line 57) — writable keys are exactly
  `plan_revising`, `plan_hash_at_build_start`, `pr_number`. A new key needs registry + extraction
  + default. Still holds.
- `agent/pipeline_graph.py` — `MAX_CRITIQUE_CYCLES=2` (L35), `MAX_PATCH_CYCLES=3` (L31). Fixed,
  not per-change tunable. Still holds.
- **Path drift (minor):** the router skill is `.claude/skills-global/sdlc/SKILL.md` (Step 3.5
  guard table), NOT `.claude/skills/sdlc/SKILL.md` as the issue states. Corrected throughout.

**Cited sibling issues/PRs re-checked:**
- #1714 — **CLOSED 2026-06-23 (COMPLETED).** Shipped critique LITE/FULL triage
  (`do-plan-critique/SKILL.md:177-202`, `docs/features/plan-critique-triage.md`). The LITE/FULL
  result is NOT persisted to `_meta` — it lives only in the run-dir `_roster.json`.
- #1626 — **CLOSED 2026-06-23 (COMPLETED).** Shipped the cross-vendor judge gated on
  `shape == "feature"` (`do-pr-review/SKILL.md:734-763`, `tools/cross_vendor_judge.py`). This is
  the "high-stakes REVIEW gets extra scrutiny" half of #1628 — already live behind the
  `SDLC_REVIEW_CROSS_VENDOR` kill switch. **Note: #1626 closed 05:20 UTC on 2026-06-23; the
  #1628 rescope comment was posted 07:03 UTC the same day — the commenter likely did not know
  #1626 had already merged hours earlier, which is why the rescope still assumed cross-vendor was
  downstream future work.**

**Commits / shipped systems on main since the issue was filed (touching referenced areas):**
- `pr_shape_classify.py` + shape-aware merge gates (`docs/features/pr-shape-aware-merge-gates.md`)
  — **already addresses** the diff-based classification + trivial-vs-feature distinction #1628
  Open Question 1 asked for. The classifier safe-defaults to `feature` on ambiguity (the exact
  "bias to higher tier" property Open Question 4 wanted).
- Numerous router hardening PRs (#1638/#1640/#1641, #1668, #1755, #1763) — added rows 2b/2c/8c and
  staleness routing but **did not** introduce any tier awareness. The extension points are unchanged.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/cross_vendor_review_judge.md` (#1626) — the now-shipped cross-vendor plan. It
  explicitly chose to gate on `shape == "feature"` and declined to wait for a `change_tier`
  field. Any unified signal this plan introduces must stay *compatible with* — not contradict —
  the `pr_shape_classify` shape vocabulary that #1626/merge-gates already depend on.

**Notes:** Because three of the four problem premises are now handled by shipped adjacent
systems, the disposition is **Major drift**. The skill's Major-drift rule says do not silently
build for a stale problem. This plan therefore (a) documents the erosion in full, (b) rescopes
to the genuine remaining kernel (unify the scattered signals into one router-visible
`change_tier`; add the single missing router-level DOCS-skip), and (c) puts the
close-vs-proceed decision to the human in Open Questions #1. It is intentionally NOT a rebuild
of #1714/#1626.

## Prior Art

- **#1714** — *Triage-first CRITIQUE.* Added the LITE/FULL critique triage (doctrine-path +
  appetite + cheap Sonnet classifier, biased to FULL). Delivered the critique half of #1628.
  The classification *logic* (doctrine-path allowlist) is reusable as the plan-time tier floor.
- **#1626** — *Cross-vendor verification.* Added a non-Claude reviewer gated on `shape=="feature"`.
  Delivered the "high-stakes REVIEW" half of #1628. Establishes `pr_shape_classify` shape as the
  de-facto high-tier trigger — this plan must reuse, not fork it.
- **`scripts/pr_shape_classify.py`** — deterministic diff classifier
  (`docs-only|lockfile-only|small-patch|mixed|feature`), safe-defaults to `feature`. The single
  source of truth for diff-based change shape; `do-pr-review` and merge gates already consume it
  via `python -m scripts.pr_shape_classify`.

## Research

**Queries used:**
- "Daniel Miessler PAI Personal AI Infrastructure effort tier E1 E2 E3 E4 E5 process scaling"

**Key findings:**
- PAI sets the effort tier (E1–E5) with a *cheap classifier at prompt-submit time* and runs
  **cross-vendor audit only at high tiers (E4/E5)** — source:
  https://github.com/danielmiessler/Personal_AI_Infrastructure . This validates the architecture
  we have *already converged on independently*: a cheap deterministic classifier
  (`pr_shape_classify`) gating the expensive cross-vendor reviewer at the top tier (`feature`
  shape). It also confirms a small number of tiers tied to a hard, checkable signal is the right
  shape — we do not need a literal 5-level taxonomy; 3 router-visible tiers suffice.

## Data Flow

How a `change_tier` signal moves through the system once introduced:

1. **Plan/critique time (provisional floor)**: `do-plan-critique` already detects doctrine paths
   and Large appetite to force FULL. At the same point it writes a *provisional* tier floor:
   `sdlc-tool meta-set --key change_tier --value doctrine` (doctrine path / Large) else leaves it
   unset (defaults to `standard`).
2. **PR-open / review time (authoritative refinement)**: when a PR exists, the diff-based shape is
   computed by `pr_shape_classify` (already run by `do-pr-review`). The tier is the **monotonic max**
   of the persisted floor and the shape-derived tier (`docs-only|lockfile-only|small-patch` → `trivial`;
   `mixed|feature` → `standard`; any doctrine-path file in the diff → `doctrine`). It can only
   escalate, never de-escalate.
3. **Meta enrichment**: `tools/sdlc_stage_query.py::_compute_meta` reads the persisted
   `_change_tier` (and, if a PR exists and no floor forces doctrine, may refine via shape) and emits
   `meta["change_tier"]` in the enriched query the router consumes.
4. **Router dispatch decision**: `decide_next_dispatch` passes `meta["change_tier"]` to the
   tier-aware predicate for row 9 / guard G6. For a `trivial` tier whose shape is `docs-only` or
   `lockfile-only`, DOCS is skipped (recorded as `completed (skipped: trivial)`), routing straight
   to the merge gate. For `standard`/`doctrine`, DOCS runs as today.
5. **Verdict record / observability**: the tier is recorded alongside the REVIEW verdict so the
   dashboard and post-hoc analysis can see which tier each issue ran at.

## Architectural Impact

- **New dependencies**: none. Reuses `scripts/pr_shape_classify.py` and the existing doctrine-path
  allowlist. No new LLM classifier, no new service.
- **Interface changes**: one new whitelisted meta key (`change_tier`) in `sdlc_meta_set`; one new
  field in the `_meta` dict from `sdlc_stage_query`. Router row 9 + guard G6 gain a tier-aware
  branch. `do-plan-critique` gains one `meta-set` call.
- **Coupling**: *reduces* duplication by establishing `change_tier` as a single router-visible
  signal — but only fully pays off if the in-skill classifiers later read it (see Open Questions #2;
  this plan keeps the change additive, so coupling is unchanged in the conservative path).
- **Data ownership**: tier is owned by the SDLC meta substrate (Redis stage_states `_change_tier`),
  written by the critique/review skills, read by the router. Mirrors the existing `plan_revising`
  ownership pattern exactly.
- **Reversibility**: high. Removing the row-9/G6 tier branch and the meta key reverts to today's
  behavior; the persisted `_change_tier` becomes inert. The DOCS-skip is the only behavior change
  and is gated behind a conservative shape allowlist.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer (this is a doctrine-path change — its own critique force-FULLs)

**Interactions:**
- PM check-ins: 1-2 (the scope-reduction decision in Open Questions #1 is the key alignment point)
- Review rounds: 2+ (doctrine-path change to the router; SKILL.md parity + safety of DOCS-skip)

## Prerequisites

No external prerequisites — this work has no new external dependencies, API keys, or services.
It builds entirely on existing internal modules (`sdlc_router`, `sdlc_stage_query`, `sdlc_meta_set`,
`pr_shape_classify`).

## Solution

### Key Elements

- **`change_tier` meta key**: a single string signal `trivial | standard | doctrine` persisted in
  the SDLC meta substrate, written via the existing `sdlc-tool meta-set` whitelist, surfaced in the
  router's `_meta`. The one source of truth the router sees.
- **Monotonic tier derivation**: tier never de-escalates. A doctrine-path/Large floor set at plan
  time cannot be lowered by a later "looks trivial" shape. Ambiguity always resolves upward
  (`pr_shape_classify` already safe-defaults to `feature`).
- **Tier-aware DOCS dispatch**: the one net-new pipeline behavior — router row 9 + guard G6 skip the
  DOCS stage for `trivial` tier whose shape is `docs-only`/`lockfile-only` (nothing for `do-docs` to
  cascade), recording DOCS as `completed (skipped: trivial)` so G6's docs-gate is satisfied honestly.
- **Verdict-recorded tier**: the tier is written into the REVIEW verdict side-channel for
  observability (which tier did this issue run at?).

### Flow

PR approved at REVIEW → router reads `meta["change_tier"]` →
  - tier == `trivial` AND shape ∈ {docs-only, lockfile-only} → mark DOCS skipped → merge gate
  - tier ∈ {standard, doctrine} OR shape == small-patch → run `/do-docs` (today's behavior) → merge gate

### Technical Approach

- **Persist the signal** (`tools/sdlc_meta_set.py`): add `"change_tier": ("_change_tier", str)` to
  `_KEY_REGISTRY` with validation that the value ∈ {`trivial`,`standard`,`doctrine`}.
- **Surface the signal** (`tools/sdlc_stage_query.py`): read `_change_tier` in `_compute_meta`
  (default `"standard"`), add to `_default_meta`. Optionally refine via `pr_shape_classify` when a
  PR exists and the persisted floor is not `doctrine` — but keep this read cheap/memoized (do NOT
  shell out to `pr_shape_classify` on every query unconditionally; gate it behind "PR exists and tier
  not already doctrine").
- **Write the floor** (`do-plan-critique/SKILL.md`): at the existing doctrine-path/appetite triage
  point, emit `sdlc-tool meta-set --key change_tier --value doctrine` when force-FULL fires. Reuses
  the detection already done — no new logic.
- **Tier-aware dispatch** (`agent/sdlc_router.py`): add a `change_tier` field to the stage snapshot /
  meta passed into predicates; branch row 9 (`_rule_review_approved_docs_not_done`) and guard G6 so a
  `trivial`+doc-free shape skips DOCS. Update the SKILL.md Step 3.5 guard table + dispatch table to
  match (parity test enforces this).
- **Record the tier** in the REVIEW verdict for observability.
- **Bias-to-higher invariant**: every derivation path defaults upward on missing/ambiguous input;
  the DOCS-skip allowlist is explicit and narrow (only `docs-only`/`lockfile-only`).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_compute_meta`'s optional `pr_shape_classify` refinement must wrap the subprocess call in
  try/except and fall back to the persisted floor (default `standard`) on any failure — add a test
  asserting that a `pr_shape_classify` crash yields `change_tier == "standard"` (or the persisted
  floor), never an exception that breaks the router query.
- [ ] `sdlc_meta_set` value validation must reject an out-of-vocabulary tier with a clear error and
  non-zero exit (test the rejection path).

### Empty/Invalid Input Handling
- [ ] `change_tier` absent from stage_states → `_compute_meta` returns `"standard"` (safe default),
  not None/empty — test this.
- [ ] Empty/whitespace/None tier value → treated as `"standard"`; never silently skips DOCS.

### Error State Rendering
- [ ] If DOCS is skipped, the router decision `reason` string must explicitly say
  `"DOCS skipped: trivial tier (shape=docs-only)"` so the skip is visible in dispatch logs, never
  silent — assert on the reason text in the router decision test.

## Test Impact

- [ ] `tests/unit/test_sdlc_meta_set.py` — UPDATE: add cases for the new `change_tier` whitelisted
  key (accept valid vocabulary, reject invalid values).
- [ ] `tests/unit/test_sdlc_stage_query.py` — UPDATE: assert `_compute_meta` / `_default_meta` emit
  `change_tier` (default `"standard"`; reads persisted `_change_tier`; safe fallback on classifier
  failure).
- [ ] `tests/unit/test_sdlc_router_decision.py` — UPDATE: add row-9 / G6 tier-branch cases —
  `trivial`+`docs-only` skips DOCS → merge; `standard`/`doctrine` runs DOCS; monotonic non-de-escalation.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — UPDATE: the SKILL.md guard/dispatch prose must
  match the new tier-aware row 9 / G6 — extend the parity invariant.
- [ ] New `tests/unit/test_sdlc_change_tier.py` — REPLACE/ADD: end-to-end tier derivation
  (floor → monotonic max with shape → router-visible meta), bias-to-higher on ambiguity.

## Rabbit Holes

- **Rebuilding critique or review tiering.** Both shipped (#1714, #1626). Reusing/reading their
  signals is in scope; re-implementing them is not.
- **A literal 5-level E1–E5 taxonomy.** Three router-visible tiers tied to existing hard signals is
  sufficient and matches what PAI actually does (cheap classifier + a top-tier gate). Do not invent
  five levels with five sets of thresholds.
- **A new LLM tier classifier.** `pr_shape_classify` (deterministic) + the doctrine-path allowlist
  already cover classification. A new classifier is redundant cost and a new failure mode.
- **Skipping REVIEW for trivial changes.** REVIEW and CRITIQUE stay hard gates — the issue, the
  #1714 rationale, and the "bias high" doctrine all forbid relaxing them. Only DOCS scales down.
- **Calling `pr_shape_classify` on every `stage-query`.** It shells out to git/gh; unconditional
  invocation would slow every router decision. Gate the refinement read tightly.

## Risks

### Risk 1: A code change that *should* update docs gets DOCS skipped
**Impact:** Documentation silently drifts because the router skipped `/do-docs`.
**Mitigation:** DOCS-skip is allowed ONLY for `docs-only` and `lockfile-only` shapes — by
definition these contain no code that could need new docs (`docs-only` already *is* docs;
`lockfile-only` is strictly `uv.lock`). `small-patch` (which contains code) always runs DOCS. The
narrow allowlist is the safety mechanism.

### Risk 2: Misclassification escalation/de-escalation race
**Impact:** A late "looks trivial" signal lowers a tier that a doctrine floor had correctly raised,
sneaking an architectural change through a light path.
**Mitigation:** Tier derivation is **monotonic max** — the persisted floor (set at plan/critique
time from doctrine paths) can only be raised by later signals, never lowered. Unit test asserts
non-de-escalation explicitly.

### Risk 3: SKILL.md ↔ router drift
**Impact:** The hand-authored guard/dispatch prose in `.claude/skills-global/sdlc/SKILL.md` falls out
of sync with the Python, breaking the router's documented contract.
**Mitigation:** `test_sdlc_skill_md_parity.py` already enforces prose↔code parity; extend it to cover
the tier branch. This is a doctrine-path change, so its own critique force-FULLs.

## Race Conditions

### Race 1: Tier floor written by critique vs. read by router during a fast plan→build→review cycle
**Location:** `tools/sdlc_meta_set.py` write (critique) vs. `tools/sdlc_stage_query.py::_compute_meta`
read (router).
**Trigger:** The router queries `_meta` before the critique skill's `meta-set` for `change_tier` has
committed to the substrate.
**Data prerequisite:** `_change_tier` must be persisted before the router's DOCS-skip decision reads it.
**State prerequisite:** The DOCS-skip decision only happens at row 9 (REVIEW already APPROVED), which is
many stages *after* critique writes the floor — so the write strictly precedes the read in pipeline order.
**Mitigation:** The default-`standard` fallback means a missing tier never *skips* DOCS (it runs DOCS,
the safe direction). The monotonic-max derivation means a late floor read still cannot lower an
already-escalated tier. No lock needed — the default biases to the safe (run-DOCS) outcome.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1714] Critique LITE/FULL tiering — already shipped and closed; this plan reads its
  doctrine-path signal but does not modify the critique triage itself.
- [SEPARATE-SLUG #1626] The cross-vendor reviewer and its `shape=="feature"` gate — already shipped
  and closed; this plan keeps `change_tier` *compatible* with that shape vocabulary but does not
  alter the cross-vendor gate. (If Open Questions #2 chooses the refactor path, rewiring the
  cross-vendor gate to read `change_tier` would be a follow-up, not this plan.)

(Design boundaries that are permanent invariants rather than deferred work — never relax the CRITIQUE
or REVIEW hard gates; never de-escalate a tier — are stated in the Solution/Risks, not here, because
they are not "deferred items".)

## Update System

The router skill `.claude/skills-global/sdlc/SKILL.md` is hardlinked to every machine by `/update`
via `scripts/update/hardlinks.py::sync_claude_dirs()` — editing it in place is automatically
propagated; **no new sync wiring is required**. `agent/sdlc_router.py`, `tools/sdlc_meta_set.py`, and
`tools/sdlc_stage_query.py` are repo code deployed by the ordinary `git pull` step of `/update`. The
new `change_tier` meta key is an internal Redis stage_states field — no migration is needed because
its absence safely defaults to `"standard"` (existing in-flight issues simply read the default until
their next critique writes a floor). **No update-script changes required beyond the existing skill
hardlink sync and git pull.**

## Agent Integration

The agent already drives the SDLC via the `sdlc-tool` CLI entry points (`meta-set`, `stage-query`,
`next-skill`) declared in `pyproject.toml`. The new `change_tier` key flows entirely through those
*existing* CLIs — `sdlc-tool meta-set --key change_tier --value doctrine` and the enriched
`stage-query` `_meta` payload. **No new CLI entry point, no new MCP server, and no
`bridge/telegram_bridge.py` change is required.** Integration coverage:
- [ ] A round-trip test: `meta-set --key change_tier --value trivial` then `stage-query` returns
  `meta["change_tier"] == "trivial"` (proves the agent-facing path works end to end).
- [ ] An invalid value (`--key change_tier --value bogus`) is rejected with a non-zero exit (proves
  the whitelist guard the agent would hit).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/sdlc-effort-tiers.md` describing the `change_tier` signal: the three
  tiers, how each is derived (doctrine floor + `pr_shape_classify` shape, monotonic max), where it
  is persisted/read, and the DOCS-skip behavior. Explicitly cross-reference #1714 (critique tiering)
  and #1626 (cross-vendor) as the sibling tiering mechanisms this signal unifies.
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/pipeline-graph.md` to note the tier-aware row 9 / G6 DOCS-skip branch.
- [ ] Cross-link from `docs/features/plan-critique-triage.md` and
  `docs/features/pr-shape-aware-merge-gates.md` so the three tiering surfaces reference the unified
  signal.

### Inline Documentation
- [ ] Docstrings on the new `change_tier` derivation helper and the tier-aware predicate explaining
  the monotonic-max invariant and the doc-free-shape allowlist.

## Success Criteria

- [ ] `change_tier` is a whitelisted `sdlc-tool meta-set` key validated to {trivial,standard,doctrine}.
- [ ] `sdlc-tool stage-query` `_meta` includes `change_tier`, defaulting to `"standard"` when unset.
- [ ] `do-plan-critique` writes `change_tier=doctrine` when its existing force-FULL (doctrine
  path / Large appetite) fires.
- [ ] Router row 9 / guard G6 skip DOCS for `trivial` tier with shape ∈ {docs-only, lockfile-only},
  recording an explicit skip reason; all other cases run DOCS unchanged.
- [ ] Tier derivation is monotonic (never de-escalates) — proven by a unit test.
- [ ] The tier is recorded in the REVIEW verdict for observability.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] grep confirms `do-plan-critique/SKILL.md` references `change_tier` (Agent Integration: the
  critique skill writes the signal).
- [ ] `test_sdlc_skill_md_parity.py` passes with the tier-aware row 9 / G6 prose.

## Team Orchestration

### Team Members

- **Builder (tier-signal)**
  - Name: tier-signal-builder
  - Role: meta-key persistence + stage-query surfacing (`sdlc_meta_set.py`, `sdlc_stage_query.py`)
  - Agent Type: builder
  - Resume: true

- **Builder (router-dispatch)**
  - Name: router-dispatch-builder
  - Role: tier-aware row 9 / G6 + SKILL.md parity + critique `meta-set` write
  - Agent Type: builder
  - Resume: true

- **Validator (tier)**
  - Name: tier-validator
  - Role: verify monotonicity, DOCS-skip allowlist, safe defaults, parity
  - Agent Type: validator
  - Resume: true

### 1. Persist + surface the tier signal
- **Task ID**: build-tier-signal
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_meta_set.py, tests/unit/test_sdlc_stage_query.py
- **Assigned To**: tier-signal-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `change_tier` to `_KEY_REGISTRY` with vocabulary validation.
- Surface `change_tier` in `_compute_meta` / `_default_meta` with safe `"standard"` default and
  guarded (PR-exists, not-already-doctrine) `pr_shape_classify` refinement wrapped in try/except.

### 2. Tier-aware dispatch + critique write
- **Task ID**: build-router-dispatch
- **Depends On**: build-tier-signal
- **Validates**: tests/unit/test_sdlc_router_decision.py, tests/unit/test_sdlc_skill_md_parity.py
- **Assigned To**: router-dispatch-builder
- **Agent Type**: builder
- **Parallel**: false
- Thread `change_tier` into the router snapshot/meta; branch row 9 + G6 for the DOCS-skip with an
  explicit reason string and monotonic-max derivation.
- Add the `meta-set --key change_tier --value doctrine` write at the critique force-FULL point.
- Update `.claude/skills-global/sdlc/SKILL.md` Step 3.5 guard/dispatch prose for parity.

### 3. Tier validation
- **Task ID**: validate-tier
- **Depends On**: build-router-dispatch
- **Assigned To**: tier-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify non-de-escalation, DOCS-skip only for doc-free shapes, safe defaults, classifier-failure
  fallback, and parity.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-tier
- **Assigned To**: tier-validator
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-effort-tiers.md`; update README index, pipeline-graph, and cross-links.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: tier-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification commands; confirm every success criterion (incl. docs) is met.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_sdlc_router_decision.py tests/unit/test_sdlc_meta_set.py tests/unit/test_sdlc_stage_query.py tests/unit/test_sdlc_skill_md_parity.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Tier meta key wired | `grep -c 'change_tier' tools/sdlc_meta_set.py` | output > 0 |
| Tier surfaced in meta | `grep -c 'change_tier' tools/sdlc_stage_query.py` | output > 0 |
| Critique writes the floor | `grep -c 'change_tier' .claude/skills-global/do-plan-critique/SKILL.md` | output > 0 |
| REVIEW never relaxed | `grep -rn "skip.*REVIEW\|REVIEW.*skip" agent/sdlc_router.py` | match count == 0 |
| Feature doc exists | `test -f docs/features/sdlc-effort-tiers.md && echo ok` | output contains ok |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Is the reduced kernel worth building, or should #1628 be closed as substantially superseded?**
   Three of #1628's four premises shipped after it was filed (#1714 critique tiering, #1626
   cross-vendor REVIEW tiering, shape-aware merge/test gates). The only genuinely remaining value is
   (a) a single *router-visible* `change_tier` that unifies the now-scattered classifiers, and
   (b) the one net-new behavior: skipping the DOCS stage for doc-free trivial PRs. Is that Medium-sized
   kernel worth shipping, or do you prefer to close #1628 and (optionally) file a tiny issue for just
   the DOCS-skip?
2. **Additive signal vs. single-source refactor.** This plan's conservative path *adds* `change_tier`
   alongside the existing in-skill `pr_shape_classify`/doctrine reads. A larger version would rewire
   `do-pr-review` (cross-vendor gate) and the merge gates to *read* `change_tier` as the single source
   of truth, removing the duplication entirely. Do you want the additive (smaller, lower-risk) version,
   or the consolidating refactor (more value, touches more doctrine surfaces)?
3. **DOCS-skip scope.** Should the router skip DOCS for `docs-only`/`lockfile-only` only (recommended,
   safest), or also for `small-patch` (contains code, but tiny)? Skipping DOCS for `small-patch` risks
   missing a genuine doc update on a small code change.
