---
status: Planning
type: feature
appetite: Medium
owner: valor
created: 2026-05-08
tracking: https://github.com/tomcounsell/ai/issues/1309
last_comment_id: null
revision_cycle: 1
revision_applied: true
---

# Multi-Judge Consensus at Review (and Merge) Gates

## Problem

The Review gate runs a single judge. One reviewer pass produces both false positives (blocking acceptable PRs over a stylistic concern) and false negatives (missing real issues that another lens would catch). `/do-plan-critique` already proves the multi-lens pattern works — six personas spawn in parallel and aggregate into one verdict — but Review and Merge lag behind on a single scalar.

**Current behavior:**
- `.claude/skills-global/do-pr-review/SKILL.md` (lines 537–553) records exactly one `sdlc-tool verdict record --stage REVIEW` call per PR.
- `tools/sdlc_verdict.py:155-171` writes one record per stage: `{verdict, recorded_at, artifact_hash, blockers?, tech_debt?}`.
- The merge gate is two-layer: SDLC router guard **G6** (`.claude/skills-global/sdlc/SKILL.md:154`) reads `_verdicts["REVIEW"]` for `APPROVED`; `.claude/commands/do-merge.md:290-304` re-validates against the latest `## Review:` PR comment.

**Desired outcome:**
- Review can spawn K parallel judges (default K=2). Per-judge results are aggregated **in the parent** and written via a **single** `record_verdict` call, mirroring `/do-plan-critique`'s in-parent aggregation pattern.
- The verdict shape gains optional `_judges` and `_consensus` fields, passed as kwargs to the existing `record_verdict` writer. Scalar `verdict` / `blockers` / `tech_debt` at the top of the record remain authoritative for existing readers (G6, do-merge.md). Single-judge skills (`/do-plan-critique`) continue to call `record_verdict` with no `judges` arg — no breaking change.
- Merge reuses Review's verdicts; it does **not** re-run judges.
- Cost is contained on two axes: (a) shape — docs-only / lockfile-only PRs skip multi-judge; (b) per-judge enable — `SDLC_REVIEW_JUDGES` (comma-list) controls which judges run. `SDLC_REVIEW_K` controls K-of-N consensus arithmetic. Both default to safe values; either one is an operator kill switch.

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

**Revision 1 (2026-05-08, in-flight):** Three critique blockers folded in before build:
1. **Aggregation re-architecture** — earlier draft introduced `record_judge_verdict` + `finalize_consensus` writers (two new module entry points plus two new CLI subcommands). That forks the verdict-writer surface. Revised: parent aggregates per-judge dicts in memory, then writes ONE `record_verdict(...)` call passing `judges=[...]` and `consensus={...}` as kwargs. No new entry points, no new CLI subcommands, single-writer invariant preserved verbatim. (Simplifier blocker.)
2. **PR-comment ordering barrier** — earlier draft was implicit about ordering; race meant `do-merge.md`'s "latest `## Review:`" check could match a per-judge comment. Revised: explicit barrier — parent awaits all per-judge comments to flush, THEN posts the aggregate `## Review:` comment LAST. Per-judge headings use the distinct `## Review (Judge {id}):` prefix that does not match `do-merge.md`'s regex, but ordering is still asserted by a regression test. (Operator blocker.)
3. **Cost containment per-judge** — earlier draft had only `SDLC_REVIEW_K` (an integer K) so disabling a single judge required dropping to K=1 (losing both lenses). Revised: added `SDLC_REVIEW_JUDGES` (comma-list, default `code-quality,risk`). Operator can disable Risk in isolation by setting `SDLC_REVIEW_JUDGES=code-quality`. K then auto-clamps to `min(SDLC_REVIEW_K, len(judges))`. Brief monitoring note added (judges-per-PR + consensus-disagreement-rate). (Operator blocker.)

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
3. **Judge selection**: read `SDLC_REVIEW_JUDGES` (comma-list of judge IDs, default `code-quality,risk`). Empty or `none` → fall through to single-judge legacy path. K = `min(SDLC_REVIEW_K, len(enabled_judges))`.
4. **Shape classification** (reuse `do-merge.md`'s classifier — extracted into a shared helper): if `docs-only` or `lockfile-only`, skip multi-judge and go to single-judge legacy path.
5. **Judge dispatch (parallel)**: spawn K agent forks via the same Task-tool / `context: fork` pattern `do-plan-critique` already uses. Each fork runs the existing review flow over the same diff, producing per-judge `{judge_id, verdict, blockers, tech_debt, confidence, reasoning_summary, review_url?}` and **returning the dict to the parent** (no per-judge verdict write to Redis).
6. **Per-judge PR comment posting (barrier)**: parent collects all K judge dicts. Parent posts each `## Review (Judge {id}):` per-judge comment **sequentially** to guarantee ordering. Parent waits for all per-judge comments to be confirmed posted before proceeding.
7. **In-parent consensus computation**: parent applies the consensus rule (`any-blocker-wins`) over the K dicts, producing top-level scalar `verdict` / `blockers` / `tech_debt` and consensus metadata `{rule, k, n, mean_confidence, tied, decided_at}`.
8. **Single verdict write**: parent calls `record_verdict(session, "REVIEW", verdict, blockers=..., tech_debt=..., judges=[...], consensus={...})`. ONE write. Same single-writer invariant as today.
9. **Aggregate PR comment (LAST)**: parent posts `## Review: Approved` / `## Review: Changes Requested` summarizing per-judge findings. This comment is posted **strictly after** all per-judge comments so `do-merge.md`'s "latest `## Review:`" lookup picks up the aggregate (regression test asserts this).
10. **Output**: `<!-- OUTCOME ... -->` block records the aggregate. G6 and `do-merge.md` consume the scalar exactly as before.

**Merge-time flow:** unchanged. G6 reads `_verdicts["REVIEW"].verdict` for `APPROVED`; `do-merge.md` reads the latest `## Review:` PR comment. Both are populated by the in-parent consensus step.

## Architectural Impact

- **New dependencies**: none (no new libraries; reuses existing fork/Task patterns).
- **Interface changes**: `record_verdict()` gains two optional kwargs — `judges: list[dict] | None` and `consensus: dict | None`. When provided, they are persisted as side-fields under `_verdicts[stage]._judges` and `_verdicts[stage]._consensus`. No new module entry points, no new CLI subcommands. The CLI surface gains optional flags `--judges-json` and `--consensus-json` that round-trip JSON strings into the same single `record` subcommand. Single-judge callers (`/do-plan-critique`) pass neither and write only the scalar — bit-identical to today.
- **Coupling**: lightly increases — Review skill now serializes per-judge dicts before the single-write call. Consensus rule lives in one place (the parent skill, applied in-memory before the write). Merge surface is unchanged.
- **Data ownership**: `tools/sdlc_verdict.py` remains the **single writer** of `_verdicts`. `_judges` and `_consensus` are written **in the same single call** — never separately. The single-writer invariant is preserved verbatim; this is a pure shape extension on the existing writer.
- **Reversibility**: high. New fields are additive. If multi-judge is disabled (`SDLC_REVIEW_JUDGES=none` or `SDLC_REVIEW_K=1`), the Review skill calls `record_verdict` without `judges`/`consensus` kwargs and behavior is bit-identical to today.

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

- **Per-judge record store**: `_verdicts[stage]._judges` is a list of `{judge_id, verdict, blockers, tech_debt, confidence, reasoning_summary, review_url}`. Written as a side-field by the same `record_verdict` call that writes the scalar.
- **Consensus metadata**: `_verdicts[stage]._consensus` records `{rule, k, n, mean_confidence, blocker_aggregation, tied: bool, decided_at}`. Same single-write call.
- **Scalar pass-through**: `_verdicts[stage].verdict`, `.blockers`, `.tech_debt` remain at the top of the record. The parent computes them in-memory from the per-judge dicts and passes them as positional/kwarg arguments to `record_verdict`. G6 and do-merge.md read the scalar — they need no changes.
- **Single API extension** (no new entry points): `record_verdict(session, stage, verdict, blockers=None, tech_debt=None, issue_number=None, judges=None, consensus=None, now=None)`. The `judges` and `consensus` kwargs default to `None`; when set, they are persisted as side-fields. CLI gains `--judges-json` / `--consensus-json` flags on the existing `record` subcommand for parity. **No `record-judge` or `finalize` subcommand.** This preserves the writer's single-call shape and avoids forking the API surface.
- **Consensus computed in the parent** (Python `agent/sdlc_review_consensus.py`): the parent skill collects per-judge dicts in memory and calls a pure-Python helper `compute_consensus(judges, rule="any-blocker-wins") -> dict` returning `{verdict, blockers, tech_debt, consensus_meta}`. Pure function: zero I/O, fully unit-testable, no Redis dependency.
- **Consensus rules (initial)**:
  - `any-blocker-wins` (default for Review): if **any** judge returns `CHANGES REQUESTED` (or any judge's `blockers > 0`), top-level verdict is `CHANGES REQUESTED` and `blockers = max(judge.blockers)`. Otherwise `APPROVED`.
  - `unanimous-approved` (alternative): top-level `APPROVED` only if **all** K judges approved. Same blocker logic on the negative side. Documented as opt-in, not the default.
- **Tied / split votes (resolution)**: "any blocker wins" eliminates the tie problem at K=2 — disagreement always decides toward the conservative (blocker) outcome. No human escalation, no fourth judge.
- **K value**: `SDLC_REVIEW_K=2` by default. Configurable via env var. K=1 is fully supported as a fast path.
- **Per-judge enable** (NEW knob): `SDLC_REVIEW_JUDGES` is a comma-list of judge IDs from the fixed roster (`code-quality`, `risk`). Default: `code-quality,risk` (both enabled). Operator can disable Risk in isolation by setting `SDLC_REVIEW_JUDGES=code-quality`. Setting `SDLC_REVIEW_JUDGES=none` (or empty) forces single-judge legacy path. K is auto-clamped to `min(SDLC_REVIEW_K, len(enabled_judges))`.
- **Same-K at Merge**: Merge reuses Review's verdicts. Merge does **not** re-run judges. Adding judges at Merge would duplicate cost without new signal — Review covers code-quality and the merge gate already adds shape/lockfile/CI checks that judges don't replicate.
- **Judge selection (fixed roster, v1)**: **`code-quality`** (correctness, design, tests-against-plan); **`risk`** (security, dependency hygiene, regression risk). Both run the existing `/do-pr-review` flow over the same diff with distinct system prompts. Dynamic / shape-aware selection is explicitly deferred (see No-Gos).
- **Cost ceiling**: shape classifier (extracted from `do-merge.md`) detects `docs-only` and `lockfile-only` PRs and forces single-judge. Other shapes use `SDLC_REVIEW_K` × `len(SDLC_REVIEW_JUDGES)`. Operators have two independent kill switches (`SDLC_REVIEW_K=1` or `SDLC_REVIEW_JUDGES=code-quality`).
- **Monitoring (lightweight)**: the OUTCOME block records `judges_run` (count) and `consensus_disagreement` (bool). Operators can grep these from session state to track multi-judge cost and disagreement rate without a new dashboard. Full dashboard is deferred.
- **Confidence**: each judge returns a confidence float in [0,1]. Stored per-judge; consensus records `mean_confidence`. **Low confidence does not trigger another judge** in v1 — that opens cost spiral. It is surfaced in the OUTCOME block for human inspection only.
- **PR-comment ordering barrier**: parent posts per-judge `## Review (Judge {id}):` comments sequentially, awaits each one's `gh` exit, THEN posts the aggregate `## Review:` comment. The aggregate is provably the **latest** `## Review:` heading on the PR — `do-merge.md`'s regex picks it up correctly. A regression test parses the PR-comment timeline to assert this ordering.
- **Single-writer invariant preserved**: every write to `_verdicts` still goes through `tools/sdlc_verdict.py::record_verdict`. ONE call per stage per session — same as today. CRITIQUE remains single-judge (it already aggregates internally).

### Flow

PR opened → SDLC router dispatches `/do-pr-review` → preflight (no change) → judge selection (`SDLC_REVIEW_JUDGES`) → shape classifier (force single-judge for docs-only/lockfile-only) → spawn K judges in parallel via Task fork — **each fork RETURNS its dict to the parent (no fork-side verdict write)** → parent collects K dicts → parent posts each `## Review (Judge {id}):` per-judge comment **sequentially** (awaits each `gh` exit) → parent computes consensus in-memory via `compute_consensus(judges)` → parent makes ONE `record_verdict(... judges=[...], consensus={...})` call → parent posts aggregate `## Review: Approved` / `## Review: Changes Requested` comment **last** (after all per-judge comments are confirmed posted) → record OUTCOME → return.

### Technical Approach

- **Extend `tools/sdlc_verdict.py::record_verdict`** with two optional kwargs: `judges: list[dict] | None = None`, `consensus: dict | None = None`. When provided, they are persisted under `_verdicts[stage]._judges` and `_verdicts[stage]._consensus` **in the same single write call** (same `update_stage_states` invocation). No new module entry points, no new CLI subcommands. The CLI `record` subcommand gains optional `--judges-json` / `--consensus-json` flags that JSON-decode into the kwargs.
- **Validate judge dict shape** in `record_verdict`: each judge dict must have `judge_id` (str), `verdict` (str), `blockers` (int), and optional `tech_debt`, `confidence`, `reasoning_summary`, `review_url`. Invalid dicts → return `{}` (graceful failure, no partial write).
- **Add `agent/sdlc_review_consensus.py`** with a single pure function `compute_consensus(judges: list[dict], rule: str = "any-blocker-wins") -> dict`. Returns `{verdict, blockers, tech_debt, consensus: {rule, k, n, mean_confidence, blocker_aggregation, tied, decided_at}}`. No I/O. Fully unit-testable.
- `_VERDICT_STAGES` is unchanged: `frozenset(["CRITIQUE", "REVIEW"])`. CRITIQUE never gains `_judges` (its internal critics aggregate before recording — already in-parent).
- Update `.claude/skills-global/do-pr-review/SKILL.md` so that, when `SDLC_REVIEW_JUDGES` enables ≥2 judges and shape is not docs-only/lockfile-only, it spawns K agent forks. Each fork is given a `RETURN_DICT` instruction: do the review, post the per-judge PR comment via parent (NOT directly), and `print()` a JSON dict to stdout. Parent collects, computes consensus, makes ONE `record_verdict` call, posts aggregate. When `SDLC_REVIEW_JUDGES=none` or `code-quality` (single judge), the existing single-judge path runs verbatim with no `judges`/`consensus` kwargs.
- **Per-judge PR comment posting** is parent-driven (not fork-driven) precisely to enforce ordering. Fork returns its dict; parent posts the comment in the parent's bash context. This guarantees the parent controls when the aggregate is posted relative to per-judge comments.
- Extract `do-merge.md`'s shape classifier into a shared helper. Decision criterion: if existing classifier is < 30 lines of straightforward bash, inline the same logic into `do-pr-review/SKILL.md`. Otherwise extract to `tools/pr_shape.py`. **Verify location** before code lands.
- G6 in `agent/sdlc_router.py` and `.claude/skills-global/sdlc/SKILL.md:154` are unchanged. They read `_verdicts["REVIEW"].verdict` containing `APPROVED`. The parity test in `tests/unit/test_sdlc_skill_md_parity.py` is not affected because no new guard is introduced.
- `do-merge.md` is unchanged in its review-comment check. It consumes the **aggregate** `## Review: …` comment, which is posted **last** by construction.
- The `<!-- SDLC-AGENT-REVIEW v1 -->` marker is required on every per-judge review comment AND the aggregate comment when `CLAUDE_AGENT_REVIEW=1` (preserves the bot-identity hard rule).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/sdlc_verdict.py`: existing `try/except Exception` blocks return `{}` on failure (lines 86-88, 178-180, 217-218). Extended `record_verdict` (with `judges`/`consensus` kwargs) follows the same pattern — failures return `{}`/log debug, never raise. Add tests asserting: `record_verdict(..., judges=[bad_dict])` returns `{}` and does NOT partially write; `record_verdict(..., judges=None)` is bit-identical to today's behavior (no `_judges` field).
- [ ] `agent/sdlc_review_consensus.compute_consensus`: pure function, must handle empty `judges=[]` (returns `{verdict: "CHANGES REQUESTED", blockers: 1, tech_debt: 0, consensus: {tied: False, n: 0, ...}}` — conservative on empty input). Must handle malformed dicts (missing `judge_id` or `blockers`) by raising `ValueError` (which the parent catches and converts to a conservative outcome).
- [ ] Review skill: a single judge's fork crashing must not corrupt verdict state. The parent collects results; if fewer than K results returned, the parent treats missing judges as `CHANGES REQUESTED` with one synthetic blocker (`judge {id} crashed`) and aggregates with that. Test: simulate a fork that exits non-zero — the single `record_verdict` call still happens and the consensus is conservative.

### Empty/Invalid Input Handling
- [ ] `record_verdict` with `judges=[]` (empty list, but not `None`) writes `_judges: []` and `_consensus: {n: 0, ...}` if `consensus` is also passed; otherwise returns `{}` (parent must pass both or neither when multi-judge is in play).
- [ ] `record_verdict` with malformed judge dict (missing required key) returns `{}` and does NOT write a partial record.
- [ ] `compute_consensus` over duplicate `judge_id` entries: the LAST entry for a given `judge_id` wins (mirrors single-writer overwrite semantics). Test asserts deduplication.

### Error State Rendering
- [ ] If the parent's `record_verdict` call fails (returns `{}`), the Review skill MUST exit non-zero AND post a fallback `## Review: Changes Requested` comment naming the failure. Silent fallthrough to single-judge would mask the bug.
- [ ] Aggregate PR comment must include each judge's verdict and blocker counts. If a judge's per-judge comment failed to post, the aggregate notes "(judge {id} comment unavailable)" rather than omitting the judge.

## Test Impact

- [ ] `tests/unit/test_sdlc_verdict.py` — UPDATE: add cases for extended `record_verdict` with `judges` / `consensus` kwargs (round-trip persistence, malformed judge dict rejection, `judges=None` back-compat).
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — VERIFY UNCHANGED: G6 and other guards do not change. If this test fails after the change, the change broke parity.
- [ ] `tests/unit/test_sdlc_router.py` (if it exists; likely the parity/dispatch tests) — UPDATE if needed: G6 reads the scalar from the same `record_verdict` write, multi-judge or not. If the existing test only covers the scalar shape, no change is required.
- [ ] **New test** `tests/unit/test_review_multi_judge.py` — covers (a) `compute_consensus` rules over K=2 vote matrices: `any-blocker-wins` and `unanimous-approved`; both-approve/split/both-block; blocker max-aggregation; mean-confidence; tied flag; (b) `record_verdict` with `judges`/`consensus` kwargs writes both side-fields atomically; (c) **PR-comment ordering regression**: simulate parent posting per-judge comments → aggregate, assert the `## Review:` aggregate is the LAST `## Review*:` heading in the simulated PR-comment sequence; (d) `SDLC_REVIEW_JUDGES=code-quality` (single-judge fast path) writes no `_judges` field.
- [ ] `tests/unit/test_do_merge_review_filter.py` — VERIFY UNCHANGED: do-merge.md still reads the latest `## Review:` comment; behavior is bit-identical when consensus produces the same scalar a single judge would have produced.

## Rabbit Holes

- **Dynamic judge selection** based on PR shape (e.g. schema-migration PRs get a DB-aware judge). Tempting; large enough to warrant its own plan. Defer.
- **Tiebreaker fourth-judge spawning** on low-confidence consensus. Cost-spiral risk. Defer; if confidence aggregation reveals real false-negative patterns, file a follow-up.
- **Multi-judge at CRITIQUE**: CRITIQUE already runs six parallel personas internally. Adding another layer would just duplicate `do-plan-critique`'s aggregation. Out of scope.
- **Re-running judges at Merge**: doubles cost without new signal; Review's verdict is the authoritative gate. Out of scope.
- **Refactoring `do-pr-review` from bash-heavy SKILL.md into Python**: tempting cleanup but unrelated to consensus. Out of scope.

## Risks

### Risk 1: Cost blow-up on busy days
**Impact:** K=2 doubles per-PR review token cost. On a high-PR day, costs scale linearly.
**Mitigation:** **two independent kill switches**. (a) Shape classifier forces single-judge on docs-only/lockfile-only PRs (the bulk of high-volume PR types). (b) `SDLC_REVIEW_JUDGES` env var disables individual judges (e.g. `SDLC_REVIEW_JUDGES=code-quality` keeps 1 judge running; `SDLC_REVIEW_JUDGES=none` reverts to legacy single-judge). `SDLC_REVIEW_K` is a third orthogonal knob for K-of-N math but in practice operators will turn off judges via `_JUDGES`. **Lightweight monitoring**: OUTCOME block records `judges_run` and `consensus_disagreement` per Review run — operators can grep these from session state without a new dashboard.

### Risk 2: Two judges race on PR-comment posting, producing interleaved/garbled comments
**Impact:** GitHub PR comment threading becomes confusing; do-merge.md's "latest `## Review:`" check could pick up a per-judge comment instead of the aggregate.
**Mitigation (defense-in-depth)**:
- **Distinct heading prefix**: per-judge comments use `## Review (Judge {id}):` which `do-merge.md`'s regex (`^## Review: Approved` / `^## Review: Changes Requested`) does NOT match.
- **Explicit ordering barrier**: parent posts per-judge comments **sequentially** (awaits each `gh` exit), THEN posts the aggregate `## Review:` comment LAST. Per-judge comments are posted BY THE PARENT (not by forks) — the parent has full control over ordering.
- **Regression test** asserts the aggregate is the last `## Review*:` heading in the PR-comment sequence (`tests/unit/test_review_multi_judge.py`).

### Risk 3: Single-writer invariant violation
**Impact:** if any non-`tools/sdlc_verdict.py` code writes to `_verdicts`, the oscillation guard regresses.
**Mitigation:** the extended `record_verdict` is **still the only writer**. Per-judge dicts and consensus metadata flow through the same single call. No second writer is introduced. Module docstring is updated to make this explicit. Code review gate: any PR touching `stage_states._verdicts` outside this module is rejected.

### Risk 4: Parity test (`test_sdlc_skill_md_parity`) drift
**Impact:** if implementation changes G6's reading semantics by accident, parity breaks.
**Mitigation:** scalar `verdict` field is the contract. `finalize_consensus` writes the exact same field name. Test runs as part of the standard suite.

## Race Conditions

### Race 1: Per-judge fork results returned out-of-order
**Location:** Review skill orchestration (parent collects fork results).
**Trigger:** K parallel forks finish in non-deterministic order.
**Data prerequisite:** parent must collect all K dicts before computing consensus.
**State prerequisite:** parent applies a deterministic sort by `judge_id` before consensus to keep tests stable.
**Mitigation:** Task tool naturally joins; parent only proceeds once all forks return. Forks **do not write verdicts** — they `print()` their dict to stdout, parent parses. No Redis race, no `_judges` accumulation race. Parent computes consensus over the joined list and makes ONE `record_verdict` call.

### Race 2: PR-comment posting order
**Location:** Review skill orchestration (parent posts comments).
**Trigger:** parent posts per-judge comments and aggregate.
**Data prerequisite:** aggregate `## Review:` comment must be the LAST `## Review*:` heading on the PR.
**State prerequisite:** all per-judge `## Review (Judge {id}):` comments are confirmed posted (`gh pr comment` returned 0) before the aggregate is posted.
**Mitigation:** parent posts per-judge comments **sequentially** (`for judge in dicts: gh pr comment ...`). Each `gh` call awaited. Aggregate `## Review:` comment is posted **after** the loop. Regression test in `tests/unit/test_review_multi_judge.py` asserts the ordering invariant.

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

- [ ] `tools/sdlc_verdict.py::record_verdict` accepts optional `judges` and `consensus` kwargs and persists them as side-fields with the same graceful-failure contract (`{}` on error, no exceptions raised). **No new module entry points or CLI subcommands are added.**
- [ ] `agent/sdlc_review_consensus.py::compute_consensus` is a pure function that derives scalar verdict + consensus metadata from a list of per-judge dicts.
- [ ] `_verdicts[stage]` records contain per-judge `_judges` array and `_consensus` metadata when multi-judge runs; scalar `verdict`/`blockers`/`tech_debt` are populated in the same single `record_verdict` call.
- [ ] Single-judge skills (`/do-plan-critique`) continue to call `record_verdict` with no `judges`/`consensus` kwargs — existing tests pass unchanged.
- [ ] Review skill spawns K=2 parallel judges by default; single-judge fast path is preserved (`SDLC_REVIEW_JUDGES=none` or single value).
- [ ] `any-blocker-wins` consensus rule is implemented and tested over the full K=2 vote matrix.
- [ ] Tied / split votes resolve to `CHANGES REQUESTED` (the conservative outcome) without human escalation.
- [ ] G6 (`_verdicts["REVIEW"]` containing `APPROVED`) and `do-merge.md` PR-comment check both consume the consensus scalar with no code changes.
- [ ] docs-only / lockfile-only PRs skip multi-judge (force single-judge).
- [ ] Aggregate `## Review:` PR comment is posted **last** by the parent (after all per-judge `## Review (Judge X):` comments are confirmed posted) — regression test asserts ordering invariant.
- [ ] `SDLC_REVIEW_JUDGES` env var supports per-judge enable/disable (e.g. `SDLC_REVIEW_JUDGES=code-quality` runs only the code-quality judge).
- [ ] OUTCOME block records `judges_run` and `consensus_disagreement` for lightweight monitoring.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` passes unchanged.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] `SDLC_REVIEW_K` and `SDLC_REVIEW_JUDGES` env vars documented and override paths work (manually verified by setting `SDLC_REVIEW_JUDGES=none` for one PR).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (verdict-shape + consensus helper)**
  - Name: `verdict-builder`
  - Role: extend `tools/sdlc_verdict.py::record_verdict` with optional `judges` / `consensus` kwargs (and matching CLI flags `--judges-json` / `--consensus-json`); add `agent/sdlc_review_consensus.py::compute_consensus` pure helper; update module docstring.
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

### 1. Extend verdict module (single writer, side-fields)
- **Task ID**: build-verdict-shape
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_verdict.py`, `tests/unit/test_review_multi_judge.py` (new)
- **Assigned To**: verdict-builder
- **Agent Type**: builder
- **Parallel**: true
- Extend existing `record_verdict(session, stage, verdict, ...)` in `tools/sdlc_verdict.py` with two optional kwargs: `judges: list[dict] | None = None` and `consensus: dict | None = None`. When both are provided, persist them as `_verdicts[stage]._judges` and `_verdicts[stage]._consensus` in the **same** `update_stage_states` call that writes the scalar. **No new module entry points. No new CLI subcommand.**
- Validate judge dict shape inside `record_verdict`: each must have `judge_id` (str), `verdict` (str), `blockers` (int). Optional: `tech_debt`, `confidence`, `reasoning_summary`, `review_url`. Malformed → return `{}` (no partial write).
- Extend the existing CLI `record` subcommand with optional `--judges-json` and `--consensus-json` flags that JSON-decode into the kwargs. **No new subcommand.**
- Update module docstring to document the extended shape and reaffirm that `_judges`/`_consensus` are written by the same single writer.

### 2. Consensus helper (pure function)
- **Task ID**: build-consensus-helper
- **Depends On**: none
- **Validates**: `tests/unit/test_review_multi_judge.py` (new)
- **Assigned To**: verdict-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `agent/sdlc_review_consensus.py` exposing `compute_consensus(judges: list[dict], rule: str = "any-blocker-wins") -> dict`. Returns `{verdict, blockers, tech_debt, consensus: {rule, k, n, mean_confidence, blocker_aggregation, tied, decided_at}}`. Pure function, no I/O. Implements both `any-blocker-wins` (default) and `unanimous-approved` (opt-in).
- Deterministic sort by `judge_id` before computing consensus (test stability).
- Empty `judges=[]` → conservative `CHANGES REQUESTED` with synthetic blocker (matches Failure Path strategy).
- Duplicate `judge_id` → last-wins dedup.

### 3. Shape classifier helper
- **Task ID**: build-shape-classifier
- **Depends On**: none
- **Validates**: ad-hoc; consumed by review skill in step 4.
- **Assigned To**: shape-builder
- **Agent Type**: builder
- **Parallel**: true
- Inspect `.claude/commands/do-merge.md` for its existing `SHAPE` classifier (the docs-only / lockfile-only detection). Either: (a) extract into a small Python module `tools/pr_shape.py` exposing `classify_pr(pr_number) -> str`, or (b) inline the same bash logic into `do-pr-review/SKILL.md`. Prefer (a) for testability if the existing classifier is non-trivial.
- Decision criterion (verify on disk): if `do-merge.md`'s classifier is < 30 lines of straightforward bash, inline it. Otherwise extract.

### 4. Review skill multi-judge dispatch
- **Task ID**: build-review-skill
- **Depends On**: build-verdict-shape, build-consensus-helper, build-shape-classifier
- **Validates**: `tests/unit/test_review_multi_judge.py` (PR-comment ordering case)
- **Assigned To**: review-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `.claude/skills-global/do-pr-review/SKILL.md`:
  - Read `SDLC_REVIEW_JUDGES` env var (default `code-quality,risk`). If `none`/empty/single value, run the existing single-judge path verbatim and call `record_verdict` (no `judges`/`consensus` kwargs).
  - Read `SDLC_REVIEW_K` env var (default 2). Effective K = `min(SDLC_REVIEW_K, len(enabled_judges))`.
  - When effective K > 1: invoke the shape classifier. If `docs-only` or `lockfile-only`, force single-judge and proceed via the legacy path.
  - Otherwise: spawn K agent forks via the same Task / `context: fork` pattern `do-plan-critique` uses. Pass each fork a distinct `judge_id` (`code-quality`, `risk`) and a distinct system-prompt slice. **Each fork RETURNS its dict via stdout — it does NOT write to Redis and does NOT post a PR comment.**
  - Parent collects all K dicts. Parent posts each `## Review (Judge {id}):` per-judge comment **sequentially** (awaits each `gh pr comment` exit code 0).
  - Parent calls `compute_consensus(dicts)` to derive scalar verdict + consensus metadata.
  - Parent makes ONE `record_verdict(session, "REVIEW", verdict, blockers=..., tech_debt=..., judges=dicts, consensus=meta)` call.
  - Parent posts the aggregate `## Review: Approved` / `## Review: Changes Requested` comment **last** (after all per-judge comments confirmed posted).
  - Preserve the `<!-- SDLC-AGENT-REVIEW v1 -->` marker on every comment when `CLAUDE_AGENT_REVIEW=1`.
  - Preserve the OUTCOME block format and four outcome variants (success / partial / fail-blockers / preflight short-circuit). Preflight short-circuits remain single-judge.
  - Add `judges_run` (count) and `consensus_disagreement` (bool) to OUTCOME block for monitoring.

### 5. Validate single-judge back-compat
- **Task ID**: validate-backcompat
- **Depends On**: build-verdict-shape
- **Assigned To**: consensus-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sdlc_verdict.py -x -q` and confirm all pre-existing single-judge cases pass without modification.
- Confirm `/do-plan-critique`'s call to `record_verdict` is untouched and the resulting `_verdicts["CRITIQUE"]` shape is identical to today (no `_judges`, no `_consensus`).

### 6. Tests
- **Task ID**: build-tests
- **Depends On**: build-verdict-shape, build-consensus-helper, build-review-skill
- **Assigned To**: consensus-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `tests/unit/test_review_multi_judge.py` covering: K=2 vote matrix for `any-blocker-wins` and `unanimous-approved`; blocker max-aggregation; mean-confidence; tied flag; `compute_consensus` empty/duplicate handling; `record_verdict` round-trip with `judges`/`consensus` kwargs; `record_verdict` malformed-judge rejection; PR-comment ordering regression (aggregate is last `## Review*:` heading).
- Update `tests/unit/test_sdlc_verdict.py` with explicit back-compat assertion: `record_verdict` without `judges`/`consensus` writes the same shape as today (no `_judges` key).

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: build-review-skill, build-tests
- **Assigned To**: consensus-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/multi-judge-consensus.md` describing the verdict shape, `SDLC_REVIEW_JUDGES` + `SDLC_REVIEW_K` env vars, consensus rules, cost ceiling, monitoring (`judges_run` / `consensus_disagreement`), and back-compat guarantee.
- Add to `docs/features/README.md` index.
- Update `docs/sdlc/` review addendum with the per-judge / aggregate comment convention and explicit ordering rule.

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: build-verdict-shape, build-consensus-helper, build-shape-classifier, build-review-skill, validate-backcompat, build-tests, document-feature
- **Assigned To**: consensus-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` and `python -m ruff check .` / `python -m ruff format --check .`.
- Run `tests/unit/test_sdlc_skill_md_parity.py` and confirm it passes.
- Smoke test: drive a fixture PR through `/do-pr-review` with default env and confirm the verdict record contains `_judges`, `_consensus`, and a scalar that G6 will accept.
- Smoke test: drive the same fixture PR with `SDLC_REVIEW_JUDGES=none` and confirm bit-identical behavior to today (no `_judges` field).

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Parity test | `pytest tests/unit/test_sdlc_skill_md_parity.py -x -q` | exit code 0 |
| Verdict shape doc updated | `grep -q '_judges' tools/sdlc_verdict.py` | exit code 0 |
| Extended record CLI accepts judges-json | `python -m tools.sdlc_verdict record --help \| grep -q -- --judges-json` | exit code 0 |
| Consensus helper module exists | `test -f agent/sdlc_review_consensus.py` | exit code 0 |
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
7. **Cost ceiling.** Three-axis: (a) shape classifier forces single-judge on docs-only / lockfile-only; (b) `SDLC_REVIEW_JUDGES` env var disables individual judges (default `code-quality,risk`); (c) `SDLC_REVIEW_K` controls K-of-N math. Lightweight monitoring via OUTCOME block (`judges_run`, `consensus_disagreement`).
8. **Confidence per judge.** Yes, each judge returns confidence in [0,1]; consensus records `mean_confidence`. No fourth-judge spawning on low confidence in v1.

If a reviewer disagrees with any of these decisions, flag it in critique — the war-room pass is the right place to challenge them.
