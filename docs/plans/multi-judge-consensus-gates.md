---
status: Planning
type: feature
appetite: Medium
owner: valor
created: 2026-05-08
tracking: https://github.com/tomcounsell/ai/issues/1309
last_comment_id: null
---

# Multi-Judge Consensus at Review (and Merge) Gates

## Problem

The Review gate runs a single judge. One reviewer pass produces both false positives (blocking acceptable PRs over a stylistic concern) and false negatives (missing real issues that another lens would catch). `/do-plan-critique` already proves the multi-lens pattern works — six personas spawn in parallel and aggregate into one verdict — but Review and Merge lag behind on a single scalar.

**Current behavior:**
- `.claude/skills-global/do-pr-review/SKILL.md` (lines 537–553) records exactly one `sdlc-tool verdict record --stage REVIEW` call per PR.
- `tools/sdlc_verdict.py:155-171` writes one record per stage: `{verdict, recorded_at, artifact_hash, blockers?, tech_debt?}`.
- The merge gate is two-layer: SDLC router guard **G6** (`.claude/skills-global/sdlc/SKILL.md:154`) reads `_verdicts["REVIEW"]` for `APPROVED`; `.claude/commands/do-merge.md:290-304` re-validates against the latest `## Review:` PR comment.

**Desired outcome:**
- Review can spawn K parallel judges (default K=2). Each judge records independently. The gate decision is the consensus, computed from a declared K-of-N rule.
- The verdict shape gains optional `_judges` and `_consensus` fields. Scalar `verdict` / `blockers` / `tech_debt` at the top of the record remain authoritative for existing readers (G6, do-merge.md). Single-judge skills (`/do-plan-critique`) continue to write only the scalar — no breaking change.
- Merge reuses Review's verdicts; it does **not** re-run judges.
- Cost is contained: docs-only / lockfile-only PRs skip multi-judge (reuse `do-merge.md`'s shape classifier). Single env knob `SDLC_REVIEW_K` (default 2) sets K.

## Freshness Check

**Baseline commit:** `f65f6c91a0b9eb9b0d1360b7fc946edcdbe5aa2c` (`main` HEAD at plan time).
**Issue filed at:** 2026-05-06T10:20:22Z. Recon performed 2026-05-08.
**Disposition:** Unchanged.

**File:line references re-verified:**
- `tools/sdlc_verdict.py:27-35` (verdict shape doc) — still holds.
- `tools/sdlc_verdict.py:155-164` (`record_verdict` write site) — still holds; one record per stage at line 171 (`verdicts[stage] = record`).
- `tools/sdlc_verdict.py:67` (`_VERDICT_STAGES = frozenset(["CRITIQUE", "REVIEW"])`) — still holds.
- `.claude/skills-global/do-pr-review/SKILL.md:537-553` (single-judge record_verdict call site, four outcomes) — still holds.
- `.claude/skills-global/do-plan-critique/SKILL.md:241-252` (CRITIQUE record + plan-revising lock semantics) — still holds.
- `.claude/skills-global/sdlc/SKILL.md:154` (G6 reads `_verdicts["REVIEW"]` containing `APPROVED`) — still holds.
- `.claude/commands/do-merge.md:290-304` (PR-comment review check, separate from G6) — still holds.

**Cited sibling issues/PRs re-checked:**
- #1308 (PM multi-dev fan-out) — open. Adjacent but independent: per-child verdicts at fan-out are orthogonal to the per-judge verdicts at one PR's review.

**Commits on main since issue was filed (touching referenced files):** none touching `tools/sdlc_verdict.py`, `.claude/skills-global/do-pr-review/SKILL.md`, `.claude/skills-global/do-plan-critique/SKILL.md`, `.claude/skills-global/sdlc/SKILL.md`, or `.claude/commands/do-merge.md`. Recent commits (`f65f6c91`, `a6e9682e`, `a43c682b`, `cee35387`, `2d96fb1c`) touch unrelated docs/PM/reflections/plan-validator surfaces.

**Active plans in `docs/plans/` overlapping this area:** none. Plan-validator (#1325) work shipped; sdlc-1300/1301 (do-pr-review bot identity) merged. No in-flight plans modify the verdict shape.

**Notes:** Recon's "path drift" call still applies (`.claude/skills-global/...`, not `.claude/skills/...`). Plan honors that throughout.

## Prior Art

- **Issue #1309**: this issue. Recon Summary verified the surfaces. No prior closed attempt at multi-judge consensus.
- **`/do-plan-critique` (existence-proof pattern)**: `.claude/skills-global/do-plan-critique/SKILL.md` and `CRITICS.md`. Six parallel critics (Skeptic, Operator, Archaeologist, Adversary, Simplifier, User), aggregation, single CRITIQUE verdict recorded. Architecture: agent fork via Task tool with `context: fork` per-critic.
- **`tools/test_judge/__init__.py`**: existing 271-line `JudgmentResult` dataclass already returns `pass_fail`, `confidence`, `reasoning`, `criteria_results`, `suggestions`. Candidate judge for the test-quality lens at Review.
- **`tools/sdlc_verdict.py` single-writer invariant**: established by the oscillation-guard fix (see `docs/plans/sdlc-router-oscillation-guard.md` referenced in module docstring). Plan must preserve this invariant.

## Research

External research is not load-bearing here — the design draws entirely from the in-repo `do-plan-critique` precedent and the existing verdict module. The issue cites [ruvnet/ruflo](https://github.com/ruvnet/ruflo) as inspiration for the consensus pattern; the architectural shape (K parallel judges with K-of-N gating) is well-established and not novel.

No external findings — proceeding with codebase context.

## Spike Results

No spikes needed. All assumptions are verifiable in-repo and were validated during recon. Implementation choices below are direct extensions of patterns already in use.

## Data Flow

**Review-time flow with K=2:**

1. **Entry point**: `/do-pr-review {pr_number}` invoked by SDLC router (PM session).
2. **Preflight** (existing): mergeStateStatus check; short-circuit on `BLOCKED_ON_CONFLICT` / `PR_CLOSED` (records single scalar; no multi-judge).
3. **Shape classification** (reuse `do-merge.md`'s classifier — extracted into a shared helper): if `docs-only` or `lockfile-only`, skip to single-judge legacy path.
4. **Judge dispatch (parallel)**: spawn K=`SDLC_REVIEW_K` agent forks via the same Task-tool / `context: fork` pattern `do-plan-critique` already uses. Each fork runs the existing review flow over the same diff, producing per-judge `{verdict, blockers, tech_debt, confidence, reasoning_summary, posted_review_url?}`.
5. **Per-judge verdict record**: each judge calls `sdlc-tool verdict record-judge --stage REVIEW --judge-id <id> ...`. Writes to a new `_judges` array on the stage record; preserves single-writer invariant.
6. **Consensus computation**: after all K judges finish, the parent invokes `sdlc-tool verdict finalize --stage REVIEW`. This populates the top-level scalar `verdict`/`blockers`/`tech_debt` from the per-judge records using the declared rule. It also writes `_consensus: {rule, k, n, mean_confidence, decided_at}`.
7. **Output**: `<!-- OUTCOME ... -->` block aggregates per-judge findings into one PR comment (`## Review: …`). G6 and `do-merge.md` consume the scalar exactly as before.

**Merge-time flow:** unchanged. G6 reads `_verdicts["REVIEW"].verdict` for `APPROVED`; `do-merge.md` reads the latest `## Review:` PR comment. Both are populated by the consensus step.

## Architectural Impact

- **New dependencies**: none (no new libraries; reuses existing fork/Task patterns).
- **Interface changes**: `tools/sdlc_verdict.py` gains two new entry points (`record_judge_verdict`, `finalize_consensus`) and matching CLI subcommands (`record-judge`, `finalize`). `record_verdict` is unchanged — single-judge callers are untouched.
- **Coupling**: lightly increases — Review skill now depends on the new APIs; consensus rule lives in one place (config + finalize). Merge surface is unchanged.
- **Data ownership**: `tools/sdlc_verdict.py` remains the single writer of `_verdicts`. Per-judge records are nested under `_verdicts[stage]._judges`, never written by anything else.
- **Reversibility**: high. New fields are additive. If multi-judge is disabled (`SDLC_REVIEW_K=1`), the Review skill calls `record_verdict` directly and behavior is bit-identical to today.

## Appetite

**Size:** Medium

**Team:** Solo dev, plus PM check-ins for verdict-shape sign-off.

**Interactions:**
- PM check-ins: 1–2 (verdict shape, default K).
- Review rounds: 1.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Existing SDLC tools | `python -c "from tools.sdlc_verdict import record_verdict, get_verdict; print('ok')"` | Plan extends this module. |
| Existing skill paths | `test -f .claude/skills-global/do-pr-review/SKILL.md && test -f .claude/skills-global/do-plan-critique/SKILL.md && test -f .claude/skills-global/sdlc/SKILL.md` | Plan honors `skills-global` path drift. |

## Solution

### Key Elements

- **Per-judge record store**: `_verdicts[stage]._judges` is a list of `{judge_id, verdict, blockers, tech_debt, confidence, recorded_at, reasoning_summary, review_url}`.
- **Consensus metadata**: `_verdicts[stage]._consensus` records `{rule, k, n, mean_confidence, blocker_aggregation, tied: bool, decided_at}`.
- **Scalar pass-through**: `_verdicts[stage].verdict`, `.blockers`, `.tech_debt` remain at the top of the record (single-judge writes them directly; multi-judge writes them via finalize). G6 and do-merge.md read the scalar — they need no changes.
- **Two new APIs in `tools/sdlc_verdict.py`**:
  - `record_judge_verdict(session, stage, judge_id, verdict, blockers, tech_debt, confidence, ...)` — appends to `_judges`. Stage record's scalar fields stay None until finalize runs.
  - `finalize_consensus(session, stage, rule="any-blocker-wins", k=2)` — reads `_judges`, writes scalar + `_consensus`. Idempotent.
- **Consensus rules (initial)**:
  - `any-blocker-wins` (default for Review): if **any** judge returns `CHANGES REQUESTED` (or any judge's `blockers > 0`), top-level verdict is `CHANGES REQUESTED` and `blockers = max(judge.blockers)`. Otherwise `APPROVED`.
  - `unanimous-approved` (alternative): top-level `APPROVED` only if **all** K judges approved. Same blocker logic on the negative side. Documented as opt-in, not the default.
- **Tied / split votes (resolution)**: "any blocker wins" eliminates the tie problem at K=2 — disagreement always decides toward the conservative (blocker) outcome. No human escalation, no fourth judge.
- **K value**: `SDLC_REVIEW_K=2` by default (cheap; one disagreement signal beyond a single judge). Configurable via env var. K=1 is fully supported as a fast path that bypasses the multi-judge code entirely.
- **Same-K at Merge**: Merge reuses Review's verdicts. Merge does **not** re-run judges. Adding judges at Merge would duplicate cost without new signal — Review covers code-quality and the merge gate already adds shape/lockfile/CI checks that judges don't replicate.
- **Judge selection**: fixed roster in v1. Both judges run the existing `/do-pr-review` flow over the same diff, but with distinct system prompts: **Judge A — Code Quality** (correctness, design, tests-against-plan); **Judge B — Risk & Security** (security, dependency hygiene, regression risk). Dynamic / shape-aware selection is explicitly deferred (see No-Gos).
- **Cost ceiling**: shape classifier (extracted from `do-merge.md`) detects `docs-only` and `lockfile-only` PRs and forces K=1. Other shapes use `SDLC_REVIEW_K`. Operators can override with `SDLC_REVIEW_K=1` for emergency cost cuts.
- **Confidence**: each judge returns a confidence float in [0,1]. Stored per-judge; consensus records `mean_confidence`. **Low confidence does not trigger another judge** in v1 — that opens cost spiral. It is surfaced in the OUTCOME block for human inspection only.
- **Single-writer invariant preserved**: every write to `_verdicts` still goes through `tools/sdlc_verdict.py`. The new functions use the same `update_stage_states` helper as `record_verdict`. CRITIQUE remains single-judge (it already aggregates internally) — its writer is `record_verdict` unchanged.

### Flow

PR opened → SDLC router dispatches `/do-pr-review` → preflight (no change) → shape classifier (skip multi-judge for docs-only/lockfile-only) → spawn K judges in parallel (Task fork) → each judge: read plan + diff, post per-judge `## Review (Judge {id}): …` PR comment, record per-judge verdict → parent: `sdlc-tool verdict finalize --stage REVIEW` → parent: post aggregate `## Review: Approved` (or `## Review: Changes Requested`) PR comment → record OUTCOME → return.

### Technical Approach

- Extend `tools/sdlc_verdict.py` with `record_judge_verdict` and `finalize_consensus`. Both reuse `update_stage_states` for safe concurrent writes. New CLI subcommands: `sdlc-tool verdict record-judge` and `sdlc-tool verdict finalize`.
- Extend `_VERDICT_STAGES` semantics: still `frozenset(["CRITIQUE", "REVIEW"])`. CRITIQUE never gains `_judges` (its internal critics aggregate before recording).
- Update `.claude/skills-global/do-pr-review/SKILL.md` so that, when `SDLC_REVIEW_K > 1` and shape is not docs-only/lockfile-only, it spawns K agent forks and finalizes consensus. When `K == 1`, it calls the existing single-judge path verbatim.
- Extract `do-merge.md`'s shape classifier into a shared helper: `tools/pr_shape.py` (or inline bash) consumed by both `do-merge.md` and `do-pr-review`. **Verify location** before code lands — if classifier is too tightly coupled to bash to extract cleanly, both surfaces re-implement against `gh pr diff --name-only` and `pyproject.toml`/`uv.lock` checks; that is acceptable.
- G6 in `agent/sdlc_router.py` and `.claude/skills-global/sdlc/SKILL.md:154` are unchanged. They read `_verdicts["REVIEW"].verdict` containing `APPROVED`. The parity test in `tests/unit/test_sdlc_skill_md_parity.py` is not affected because no new guard is introduced.
- `do-merge.md` is unchanged in its review-comment check, but it consumes the **aggregate** `## Review: …` comment posted by the parent (not per-judge comments). Parent comment posting must run after per-judge comments and use the canonical `## Review:` heading prefix without judge suffix.
- The `<!-- SDLC-AGENT-REVIEW v1 -->` marker is required on every per-judge review comment AND the aggregate comment when `CLAUDE_AGENT_REVIEW=1` (preserves the bot-identity hard rule).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/sdlc_verdict.py`: existing `try/except Exception` blocks return `{}` on failure (lines 86-88, 178-180, 217-218). New `record_judge_verdict` and `finalize_consensus` follow the same pattern — failures must return `{}`/log debug, never raise. Add tests asserting `record_judge_verdict(None, ...)` returns `{}`, malformed `_judges` array on read returns `{}`, `finalize_consensus` with zero recorded judges returns `{}` (no scalar overwrite).
- [ ] Review skill: a single judge's fork crashing must not corrupt verdict state. The parent collects results; if fewer than K results returned, the parent treats missing judges as `CHANGES REQUESTED` with one synthetic blocker (`judge {id} crashed`) and finalizes with that. Test: simulate a fork that exits non-zero — finalize still runs and the consensus is conservative.

### Empty/Invalid Input Handling
- [ ] `finalize_consensus` with `_judges = []` returns `{}` and does NOT overwrite an existing scalar verdict (idempotency / safety).
- [ ] `record_judge_verdict` with empty `verdict` string returns `{}` (mirrors `record_verdict`'s validation at line 146-148).
- [ ] `record_judge_verdict` with `judge_id` collision (same judge_id recorded twice) overwrites the prior entry for that judge_id. Test asserts only one entry per judge_id remains.

### Error State Rendering
- [ ] If `finalize_consensus` fails, the Review skill MUST exit non-zero AND post a fallback `## Review: Changes Requested` comment naming the failure. Silent fallthrough to single-judge would mask the bug.
- [ ] Aggregate PR comment must include each judge's verdict and blocker counts. If a judge's per-judge comment failed to post, the aggregate notes "(judge {id} comment unavailable)" rather than omitting the judge.

## Test Impact

- [ ] `tests/unit/test_sdlc_verdict.py` — UPDATE: add cases for `record_judge_verdict`, `finalize_consensus`, judge_id collision, empty `_judges` finalize, scalar back-compat (existing single-judge `record_verdict` path is unchanged but explicitly re-asserted).
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — VERIFY UNCHANGED: G6 and other guards do not change. If this test fails after the change, the change broke parity.
- [ ] `tests/unit/test_sdlc_router.py` (if it exists; likely the parity/dispatch tests) — UPDATE: add a case asserting G6 reads the scalar populated by `finalize_consensus` and treats it identically to a `record_verdict`-written scalar.
- [ ] **New test** `tests/unit/test_consensus_rules.py` — add: covers `any-blocker-wins` and `unanimous-approved` over K=2 vote matrices (both approve / one approves one blocks / both block / blocker counts max-aggregated correctly / mean confidence computation).
- [ ] **New test** `tests/integration/test_review_multi_judge.py` — add: spawns K=2 judges over a fixture PR, asserts per-judge records exist, scalar matches the consensus rule, aggregate PR comment is posted with the correct `## Review:` heading.
- [ ] `tests/unit/test_do_merge_*` (if any) — VERIFY UNCHANGED: do-merge.md still reads the latest `## Review:` comment; behavior is bit-identical when consensus produces the same scalar a single judge would have produced.

## Rabbit Holes

- **Dynamic judge selection** based on PR shape (e.g. schema-migration PRs get a DB-aware judge). Tempting; large enough to warrant its own plan. Defer.
- **Tiebreaker fourth-judge spawning** on low-confidence consensus. Cost-spiral risk. Defer; if confidence aggregation reveals real false-negative patterns, file a follow-up.
- **Multi-judge at CRITIQUE**: CRITIQUE already runs six parallel personas internally. Adding another layer would just duplicate `do-plan-critique`'s aggregation. Out of scope.
- **Re-running judges at Merge**: doubles cost without new signal; Review's verdict is the authoritative gate. Out of scope.
- **Refactoring `do-pr-review` from bash-heavy SKILL.md into Python**: tempting cleanup but unrelated to consensus. Out of scope.

## Risks

### Risk 1: Cost blow-up on busy days
**Impact:** K=2 doubles per-PR review token cost. On a high-PR day, costs scale linearly.
**Mitigation:** shape classifier forces K=1 on docs-only/lockfile-only PRs (the bulk of high-volume PR types). `SDLC_REVIEW_K` env var is the operator kill switch. Plan ships with monitoring suggestion in docs but does not gate on a metrics dashboard (defer that to a follow-up).

### Risk 2: Two judges race on PR-comment posting, producing interleaved/garbled comments
**Impact:** GitHub PR comment threading becomes confusing; do-merge.md's "latest `## Review:`" check could pick up a per-judge comment instead of the aggregate.
**Mitigation:** per-judge comments use a distinct heading prefix (`## Review (Judge A):`) that do-merge.md's grep does not match. The aggregate uses the canonical `## Review:` prefix. do-merge.md's regex (`^## Review: Approved` / `^## Review: Changes Requested`) only matches the aggregate. Tested in the integration test.

### Risk 3: Single-writer invariant violation
**Impact:** if any non-`tools/sdlc_verdict.py` code writes to `_verdicts`, the oscillation guard regresses.
**Mitigation:** new APIs live in the same module. Add a module-level docstring assertion that `_judges` is also single-writer. Code review gate: any PR touching `stage_states._verdicts` outside this module is rejected.

### Risk 4: Parity test (`test_sdlc_skill_md_parity`) drift
**Impact:** if implementation changes G6's reading semantics by accident, parity breaks.
**Mitigation:** scalar `verdict` field is the contract. `finalize_consensus` writes the exact same field name. Test runs as part of the standard suite.

## Race Conditions

### Race 1: Concurrent judge `record_judge_verdict` calls
**Location:** `tools/sdlc_verdict.py` (new function) writing to `_verdicts[stage]._judges`.
**Trigger:** K parallel forks each call `record_judge_verdict` near-simultaneously.
**Data prerequisite:** the `_judges` list must accumulate all K entries before `finalize_consensus` runs.
**State prerequisite:** the parent must wait for all K forks to complete before invoking `finalize_consensus`.
**Mitigation:** `record_judge_verdict` uses `tools.stage_states_helpers.update_stage_states` (the same helper `record_verdict` uses), which provides atomic read-modify-write semantics on the `stage_states` field. Each call appends to `_judges` under that lock. Parent awaits all forks (Task tool's natural join) before finalizing — an explicit barrier in the Review skill.

### Race 2: `finalize_consensus` runs before all judges have recorded
**Location:** Review skill orchestration.
**Trigger:** parent calls `finalize_consensus` while one fork is still writing.
**Data prerequisite:** all K judges' records present in `_judges`.
**State prerequisite:** N=K when finalize runs.
**Mitigation:** the Review skill waits on all spawned Task forks before invoking `sdlc-tool verdict finalize`. `finalize_consensus` validates `len(_judges) == k_expected` (passed via `--k` flag); on mismatch it treats missing judges as conservative blockers, never silently uses partial data.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1308] Per-child verdicts in PM multi-dev fan-out — adjacent system, separate planning surface, already filed.
- [SEPARATE-SLUG #1309] Dynamic shape-aware judge selection — tracked in this issue's Open Questions and deferred to a follow-up plan; v1 ships fixed K=2 roster. (If this needs its own issue, file post-merge.)
- [SEPARATE-SLUG #1309] Tiebreaker fourth-judge spawning on low confidence — same: deferred until v1 metrics show whether confidence is actionable signal or noise.
- Nothing else deferred — every relevant item for v1 is in scope for this plan.

## Update System

No update system changes required. The change is internal to the bridge/worker process: new Python functions in `tools/sdlc_verdict.py`, new SKILL.md content under `.claude/skills-global/`, and an optional `SDLC_REVIEW_K` env var with a sensible default. No new dependencies, no new services, no migration of existing installations beyond a normal `git pull` covered by the existing `/update` skill.

## Agent Integration

No new agent-facing integration is required. `sdlc-tool verdict record-judge` and `sdlc-tool verdict finalize` are CLI subcommands of the existing `sdlc-tool` entry point already in `pyproject.toml [project.scripts]` (the `sdlc-tool` console script). The Review skill invokes them via Bash exactly as it invokes `sdlc-tool verdict record` today. No `.mcp.json` change. No `bridge/telegram_bridge.py` change. Integration tests validate the CLI surface end-to-end (see Test Impact).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/multi-judge-consensus.md` describing the consensus pattern, the verdict shape extension, the `SDLC_REVIEW_K` env var, and the `any-blocker-wins` rule.
- [ ] Add entry to `docs/features/README.md` index table.

### SDLC docs
- [ ] Update `docs/sdlc/` (review-stage addendum, if present) to document the multi-judge dispatch and the per-judge-vs-aggregate PR comment convention.

### Inline Documentation
- [ ] Update the `tools/sdlc_verdict.py` module docstring (lines 1-49) to document the extended shape: `_judges` list and `_consensus` metadata. Preserve the single-writer note.
- [ ] Update `.claude/skills-global/do-pr-review/SKILL.md` (currently single-judge at lines 537-553) with the K-judge dispatch flow and per-judge / aggregate comment heading convention.
- [ ] Update `.claude/skills-global/sdlc/SKILL.md` G6 description (line 154) to add a note that the consumed `_verdicts["REVIEW"].verdict` may now be a consensus result; reading semantics are unchanged.

## Success Criteria

- [ ] `tools/sdlc_verdict.py` exposes `record_judge_verdict` and `finalize_consensus` with the same graceful-failure contract (`{}` on error, no exceptions raised).
- [ ] `_verdicts[stage]` records contain per-judge `_judges` array and `_consensus` metadata when multi-judge runs; scalar `verdict`/`blockers`/`tech_debt` are populated by `finalize_consensus`.
- [ ] Single-judge skills (`/do-plan-critique`) continue to call `record_verdict` and write only the scalar shape — existing tests pass unchanged.
- [ ] Review skill spawns K=2 parallel judges by default; K=1 fast path is preserved.
- [ ] `any-blocker-wins` consensus rule is implemented and tested over the full K=2 vote matrix.
- [ ] Tied / split votes resolve to `CHANGES REQUESTED` (the conservative outcome) without human escalation.
- [ ] G6 (`_verdicts["REVIEW"]` containing `APPROVED`) and `do-merge.md` PR-comment check both consume the consensus scalar with no code changes.
- [ ] docs-only / lockfile-only PRs skip multi-judge (force K=1).
- [ ] Aggregate `## Review:` PR comment is posted by the parent and is the comment matched by `do-merge.md`.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` passes unchanged.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] `SDLC_REVIEW_K` env var documented and override path works (manually verified by setting K=1 in CI for one PR).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (verdict-shape)**
  - Name: `verdict-builder`
  - Role: extend `tools/sdlc_verdict.py` with `record_judge_verdict`, `finalize_consensus`, and matching CLI subcommands; update module docstring.
  - Agent Type: builder
  - Resume: true

- **Builder (review-skill)**
  - Name: `review-builder`
  - Role: update `.claude/skills-global/do-pr-review/SKILL.md` to spawn K parallel judges, post per-judge + aggregate comments, finalize consensus.
  - Agent Type: builder
  - Resume: true

- **Builder (shape-classifier)**
  - Name: `shape-builder`
  - Role: extract or duplicate the `do-merge.md` shape classifier so the review skill can cheaply detect docs-only / lockfile-only PRs and force K=1.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (consensus-tests)**
  - Name: `consensus-tester`
  - Role: write unit tests for new APIs (`test_sdlc_verdict.py` updates, new `test_consensus_rules.py`) and the integration test `test_review_multi_judge.py`.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `consensus-documentarian`
  - Role: create `docs/features/multi-judge-consensus.md`, update `docs/features/README.md`, update `docs/sdlc/`.
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: `consensus-validator`
  - Role: run all checks; assert single-judge back-compat; assert parity test passes; assert end-to-end Review run on a fixture PR produces the expected verdict shape.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Extend verdict module
- **Task ID**: build-verdict-shape
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_verdict.py`, `tests/unit/test_consensus_rules.py` (new)
- **Assigned To**: verdict-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `record_judge_verdict(session, stage, judge_id, verdict, blockers, tech_debt, confidence, ...)` to `tools/sdlc_verdict.py`. Mirror `record_verdict`'s validation, graceful-failure pattern, and `update_stage_states` usage. Append to `_verdicts[stage]._judges`; collisions on `judge_id` overwrite the prior entry.
- Add `finalize_consensus(session, stage, rule="any-blocker-wins", k_expected=2)`. Read `_judges`; compute scalar `verdict`/`blockers`/`tech_debt` per the rule; write `_consensus` metadata. Idempotent. Returns `{}` on error or empty `_judges`.
- Add CLI subcommands `sdlc-tool verdict record-judge` and `sdlc-tool verdict finalize` mirroring the existing `record`/`get` CLI shape (same `--issue-number` / `--session-id` resolution, same exit-code semantics).
- Update module docstring (lines 1-49) to document the extended shape and reaffirm the single-writer invariant for `_judges`.

### 2. Shape classifier helper
- **Task ID**: build-shape-classifier
- **Depends On**: none
- **Validates**: ad-hoc; consumed by review skill in step 3.
- **Assigned To**: shape-builder
- **Agent Type**: builder
- **Parallel**: true
- Inspect `.claude/commands/do-merge.md` for its existing `SHAPE` classifier (the docs-only / lockfile-only detection). Either: (a) extract into a small Python module `tools/pr_shape.py` exposing `classify_pr(pr_number) -> str`, or (b) inline the same bash logic into `do-pr-review/SKILL.md`. Prefer (a) for testability if the existing classifier is non-trivial.
- Decision criterion (verify on disk): if `do-merge.md`'s classifier is < 30 lines of straightforward bash, inline it. Otherwise extract.

### 3. Review skill multi-judge dispatch
- **Task ID**: build-review-skill
- **Depends On**: build-verdict-shape, build-shape-classifier
- **Validates**: `tests/integration/test_review_multi_judge.py` (new)
- **Assigned To**: review-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `.claude/skills-global/do-pr-review/SKILL.md`:
  - Read `SDLC_REVIEW_K` env var (default 2). If 1, run the existing single-judge path verbatim and call `record_verdict` (no new APIs).
  - When K > 1: invoke the shape classifier. If `docs-only` or `lockfile-only`, force K=1 and proceed via the legacy path.
  - Otherwise: spawn K agent forks via the same Task / `context: fork` pattern `do-plan-critique` uses. Pass each fork a distinct `judge_id` ("A", "B", …) and a distinct system-prompt slice (Code Quality vs Risk & Security).
  - Each fork reads the plan + diff, posts a per-judge `## Review (Judge {id}): …` PR comment, and calls `sdlc-tool verdict record-judge`.
  - Parent waits for all forks, calls `sdlc-tool verdict finalize --stage REVIEW --k 2 --rule any-blocker-wins`, and posts the aggregate `## Review: Approved` / `## Review: Changes Requested` comment summarizing per-judge findings.
  - Preserve the `<!-- SDLC-AGENT-REVIEW v1 -->` marker on every comment when `CLAUDE_AGENT_REVIEW=1`.
  - Preserve the OUTCOME block format and four outcome variants (success / partial / fail-blockers / preflight short-circuit). Preflight short-circuits remain single-judge.

### 4. Validate single-judge back-compat
- **Task ID**: validate-backcompat
- **Depends On**: build-verdict-shape
- **Assigned To**: consensus-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sdlc_verdict.py -x -q` and confirm all pre-existing single-judge cases pass without modification.
- Confirm `/do-plan-critique`'s call to `record_verdict` is untouched and the resulting `_verdicts["CRITIQUE"]` shape is identical to today (no `_judges`, no `_consensus`).

### 5. Tests
- **Task ID**: build-tests
- **Depends On**: build-verdict-shape, build-review-skill
- **Assigned To**: consensus-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `tests/unit/test_consensus_rules.py` covering the K=2 vote matrix for `any-blocker-wins` and `unanimous-approved`, blocker max-aggregation, mean-confidence computation, and `finalize_consensus` idempotency.
- Update `tests/unit/test_sdlc_verdict.py` with cases for `record_judge_verdict` (validation, judge_id collision, graceful failure on None session) and `finalize_consensus` (empty `_judges`, partial `_judges` vs `k_expected`, scalar back-compat assertion).
- Add `tests/integration/test_review_multi_judge.py`: spawn K=2 judges over a fixture PR, assert per-judge records, assert scalar matches consensus, assert aggregate PR comment is the one `do-merge.md` would match.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-review-skill, build-tests
- **Assigned To**: consensus-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/multi-judge-consensus.md` describing the verdict shape, K config, consensus rules, cost ceiling, and back-compat guarantee.
- Add to `docs/features/README.md` index.
- Update `docs/sdlc/` review addendum with the per-judge / aggregate comment convention.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: build-verdict-shape, build-shape-classifier, build-review-skill, validate-backcompat, build-tests, document-feature
- **Assigned To**: consensus-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` and `python -m ruff check .` / `python -m ruff format --check .`.
- Run `tests/unit/test_sdlc_skill_md_parity.py` and confirm it passes.
- Smoke test: drive a fixture PR through `/do-pr-review` with `SDLC_REVIEW_K=2` and confirm the verdict record contains `_judges`, `_consensus`, and a scalar that G6 will accept.
- Smoke test: drive the same fixture PR with `SDLC_REVIEW_K=1` and confirm bit-identical behavior to today (no `_judges` field).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Parity test | `pytest tests/unit/test_sdlc_skill_md_parity.py -x -q` | exit code 0 |
| Verdict shape doc updated | `grep -q '_judges' tools/sdlc_verdict.py` | exit code 0 |
| New CLI subcommand wired | `python -m tools.sdlc_verdict record-judge --help` | exit code 0 |
| New finalize subcommand wired | `python -m tools.sdlc_verdict finalize --help` | exit code 0 |
| Feature doc exists | `test -f docs/features/multi-judge-consensus.md` | exit code 0 |
| Single-writer invariant | `grep -rn '_verdicts\[' --include='*.py' \| grep -v 'tools/sdlc_verdict.py' \| grep -v 'tests/'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

The eight questions from the issue are all decided in this plan. Recording the final answers here for reviewer audit:

1. **K value.** K=2 at Review (default, `SDLC_REVIEW_K` overrides). Merge does not run judges.
2. **Tied / split votes.** "Any blocker wins" — disagreement at K=2 always resolves to `CHANGES REQUESTED`. No human escalation.
3. **Judge selection.** Fixed roster: Judge A (Code Quality), Judge B (Risk & Security). Dynamic selection deferred.
4. **Same-K at Merge.** No. Merge reuses Review's verdict via existing G6 + PR-comment check.
5. **Verdict storage shape.** Scalar `verdict`/`blockers`/`tech_debt` at top of stage record (back-compat); optional `_judges: [...]` array and `_consensus: {...}` metadata when multi-judge ran.
6. **Blocker aggregation.** Max across judges (`blockers = max(judge.blockers)`), and "any blocker wins" semantics for the verdict.
7. **Cost ceiling.** Shape classifier forces K=1 on docs-only / lockfile-only PRs. `SDLC_REVIEW_K` is the operator kill switch.
8. **Confidence per judge.** Yes, each judge returns confidence in [0,1]; consensus records `mean_confidence`. No fourth-judge spawning on low confidence in v1.

If a reviewer disagrees with any of these decisions, flag it in critique — the war-room pass is the right place to challenge them.
