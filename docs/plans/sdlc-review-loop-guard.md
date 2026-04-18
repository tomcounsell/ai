---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-04-18
tracking: https://github.com/tomcounsell/ai/issues/1043
last_comment_id:
revision_applied: true
---

# SDLC Review Loop Guard

## Problem

On a Telegram-triggered PM session for a cross-repo PR (yudame/cuttlefish#264), `/sdlc` dispatched `/do-pr-review` eight times in eighty minutes on an already-merged-ready PR. The PR had `mergeStateStatus=CLEAN`, all CI passing, and a recorded "Approved" verdict after the second dispatch — but the router kept re-dispatching because it had no structural terminal-state check.

**Current behavior (on `main`, before PR #1044 merges):**

The SDLC router's Step 2e reads `reviewDecision` from GitHub as the sole review-readiness signal. For self-authored PRs, `gh pr review --approve` is rejected by GitHub, so `/do-pr-review` falls back to `gh pr comment`. This leaves `reviewDecision=""` permanently. The router interprets `""` as "no review yet" and re-dispatches `/do-pr-review` every turn. The G4 oscillation cap (introduced in PR #1044, not yet merged) would eventually catch it after 3 repeats — but G4 is a safety net, not a first-class terminal-state check.

**Desired outcome:**

- After PR #1044 merges, the SDLC router reads `_verdicts["REVIEW"]` (stored in `stage_states` by `sdlc_verdict record`) as the primary review-readiness signal, not GitHub's `reviewDecision`.
- A new terminal-state guard (G6) fires BEFORE the dispatch table when `(mergeStateStatus=CLEAN, all CI SUCCESS, _verdicts["REVIEW"]="APPROVED")` — routing directly to `/do-merge` without an additional `/do-pr-review` run.
- The SKILL.md Step 2e assessment is updated to acknowledge the self-review fallback: `reviewDecision=""` is ambiguous for self-authored PRs; always cross-check `_verdicts["REVIEW"]`.
- Regression test replays the PR #264 8-step sequence and asserts `/do-merge` is dispatched at step 3, not `/do-pr-review`.

## Freshness Check

**Baseline commit:** `b761838c89bc94f7c3da486b38b6dfefa1aaab41`
**Issue filed at:** 2026-04-18T08:44:45Z
**Disposition:** Unchanged — with one important overlap noted below.

**File:line references re-verified:**

- `.claude/skills/sdlc/SKILL.md` — Step 2e at lines 102-105 — still holds: `reviewDecision: ""` is documented as "no review yet" with no self-review exception.
- `.claude/skills/sdlc/SKILL.md` — dispatch table at lines 141-156 — still holds: Row 7 dispatches `/do-pr-review` when PR exists with no review, no guard for `mergeStateStatus=CLEAN + verdict stored`.
- `.claude/skills/do-pr-review/SKILL.md` — Step 6 self-authored fallback at lines 268-357 — still holds: Tier 1/2/3 fall back to `gh pr comment` for self-authored PRs.
- `.claude/skills/do-pr-review/SKILL.md` — `sdlc_stage_marker --stage REVIEW --status completed` at line 64 — still holds: completion marker IS written on approval. This is important.

**Cited sibling issues/PRs re-checked:**

- `#1040` — OPEN, plan at `docs/plans/sdlc-router-oscillation-guard.md`, PR #1044 OPEN implementing G1-G5 guards. NOT yet merged — this is the critical dependency.
- `#1042` — OPEN ("SDLC skill audit: close the five blind spots"). Adjacent but covers different blind spots — not directly overlapping.
- `#1005` — CLOSED (2026-04-16). Fixed PM session completing before merge gate. Covered by PM persona guards, not router-level terminal state.
- PR #1044 — OPEN (`session/sdlc-router-oscillation-guard`). Implements `agent/sdlc_router.py`, `tools/sdlc_verdict.py`, enriched `sdlc_stage_query`, and G1-G5 guards. **This plan's work depends on PR #1044 merging first.**

**Commits on main since issue was filed (touching referenced files):**

- `6b0df57c` — `fix(update): demote bridge/telegram failures to warnings` — irrelevant (scripts/update/run.py only)
- `b761838c` — `Plan: Promote last_stdout_at to tier-1 kill signal` — irrelevant (different plan doc)
- No commits to `.claude/skills/sdlc/SKILL.md`, `.claude/skills/do-pr-review/SKILL.md`, or `agent/` since issue was filed.

**Active plans in `docs/plans/` overlapping this area:**

- `sdlc-router-oscillation-guard.md` — **direct overlap**. PR #1044 adds G1-G5 guards. This plan adds a 6th guard (G6: terminal-state detection) and fixes the SKILL.md Step 2e assessment language for self-authored PRs. The two plans are complementary: #1040 fixes the oscillation cycle, #1043 adds the "skip to merge" fast-path when the PR is already ready.
- `sdlc-stage-skip-prevention.md` — adjacent but orthogonal (stage skipping, not stage looping).

**Notes:**

A key finding from the freshness check changes the scope significantly:

`do-pr-review` already writes `sdlc_stage_marker --stage REVIEW --status completed` on approval (line 64 of the skill). PR #1044 adds `sdlc_verdict record --stage REVIEW --verdict "APPROVED"`. Once #1044 merges, the enriched `sdlc_stage_query` will include `latest_review_verdict = "APPROVED"` and `stage_states["REVIEW"] = "completed"`. The dispatch rules `_rule_review_approved_docs_not_done` and `_rule_ready_to_merge` in `agent/sdlc_router.py` (added by PR #1044) already check `stage_states["REVIEW"] == "completed"` — they do NOT rely on `reviewDecision` from GitHub.

**This means the mechanical review loop is fully fixed by PR #1044 once it merges.** The remaining work for #1043 is:

1. Guard G6: a fast-path terminal-state check that also validates `mergeStateStatus=CLEAN` and CI from the live GitHub API, so the router doesn't even need to call `sdlc_stage_query` for the happy path merge case.
2. SKILL.md Step 2e documentation fix: clarify that `reviewDecision=""` is ambiguous for self-authored PRs; do not document it as "no review" without cross-checking `_verdicts`.
3. Regression test: PR #264 8-step sequence replay.

## Prior Art

- **PR #1044 / Issue #1040** — `feat(sdlc): router oscillation guards G1-G5` — OPEN. Adds the oscillation guards, verdict recorder, and enriched stage query. This plan builds on top of it and must wait for it to merge. G4 (universal oscillation cap after 3 same-skill dispatches) is the safety net; G6 (terminal-state check) is the fast path that avoids hitting G4 in the first place.
- **PR #951 / Issue #941** — `fix: local SDLC pipeline state tracking` — Merged 2026-04-14. Introduced `sdlc_stage_marker` and `sdlc_session_ensure`. Enabled local stage state tracking that this plan relies on.
- **PR #1010 / Issue #1005** — `fix: prevent PM session from completing before merge gate` — Merged 2026-04-16. Added PM-level guards to prevent premature completion. Complementary to router-level guards.

## Research

No relevant external findings — proceeding with codebase context and training data. The fix is entirely internal to `.claude/skills/` and `agent/sdlc_router.py` (added by PR #1044). No external libraries or APIs involved.

## Data Flow

Trace of the review-loop incident:

1. **Entry**: PM session invokes `/sdlc` for PR #264 (cross-repo, self-authored by `valorengels`)
2. **Step 2.0**: `sdlc_stage_query` returns `{}` (no stage states stored — bridge session, pre-#1044)
3. **Step 2d**: `gh pr view --json reviewDecision` returns `""` (self-authored, no formal GitHub review possible)
4. **Step 2e**: Router interprets `""` as "no review yet" → dispatches `/do-pr-review` (Row 7)
5. **`/do-pr-review`**: Posts verdict as `gh pr comment` (self-authored fallback) → `reviewDecision` stays `""`
6. **Next `/sdlc`**: Same as step 3 — `reviewDecision` still `""` → same dispatch
7. **Result**: Infinite loop until user kills process

After PR #1044 merges, step 5 also calls `sdlc_verdict record --stage REVIEW --verdict "APPROVED"` and `sdlc_stage_marker --stage REVIEW --status completed`. Step 2.0 then returns `{"REVIEW": "completed", "_meta": {"latest_review_verdict": "APPROVED"}}`. The router routes to Row 9 (docs) or Row 10 (merge) — no re-dispatch. Loop eliminated.

The remaining gap: Step 2d still reads `reviewDecision` from GitHub and documents `""` as "no review". For self-authored PRs, this documentation is misleading even though the router logic (now delegated to `sdlc_router.py`) correctly reads from `_verdicts`. We need G6 as an explicit fast-path guard that fires before G4 does.

## Architectural Impact

- **New dependencies**: None. G6 is a guard added to `agent/sdlc_router.py` (introduced by PR #1044) and SKILL.md (Step 3.5 updated).
- **Interface changes**: One new guard row in the SKILL.md guard table (G6). The Python parity test in `tests/unit/test_sdlc_skill_md_parity.py` (PR #1044) must be updated to include G6. One new rule function in `agent/sdlc_router.py`.
- **Coupling**: No new coupling. G6 reads from the same `_meta.pr_merge_state` that `sdlc_stage_query` already returns (requires one new field: `pr_merge_state` and `ci_all_passing`).
- **Data ownership**: `sdlc_stage_query` gains two new `_meta` fields: `pr_merge_state` (string from `gh pr view --json mergeStateStatus`) and `ci_all_passing` (bool: all `statusCheckRollup` conclusions are `SUCCESS`). These are read-only lookups; no new write paths.
- **Reversibility**: High. Removing G6 reverts to G4 as the sole oscillation backstop — the loop is caught at 3 dispatches instead of 0.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PR #1044 merged | `python -c "import agent.sdlc_router"` | G6 adds to sdlc_router.py and sdlc_stage_query.py introduced by #1044 |

**Note**: This plan's implementation must wait for PR #1044 to merge. All tasks below assume `agent/sdlc_router.py`, `tools/sdlc_verdict.py`, `tools/sdlc_stage_query.py` (enriched), and `tools/stage_states_helpers.py` already exist.

## Solution

### Key Elements

- **Guard G6 (terminal-state fast path)**: New guard in `agent/sdlc_router.py` that fires when `pr_number` is set AND `pr_merge_state == "CLEAN"` AND `ci_all_passing == True` AND `_verdicts["REVIEW"]` is `"APPROVED"`. Forces dispatch to `/do-merge`.
- **Two new `_meta` fields in `sdlc_stage_query`**: `pr_merge_state` (from `gh pr view --json mergeStateStatus`) and `ci_all_passing` (bool derived from `statusCheckRollup`). Added alongside the existing `_meta` fields in `tools/sdlc_stage_query.py`.
- **SKILL.md Step 2e documentation fix**: Add a note that for self-authored PRs, `reviewDecision=""` is expected even after a review is posted; cross-check `_verdicts["REVIEW"]` from `sdlc_stage_query` before concluding no review exists.
- **SKILL.md Step 3.5 guard table**: Add G6 row.
- **Regression test**: `tests/unit/test_sdlc_router_oscillation.py` gains a new test `test_1043_pr264_8step_terminates` that replays the incident's 8-step sequence with seeded state and asserts `/do-merge` is dispatched at step 3.

### Flow

PM invokes `/sdlc` for PR with CLEAN merge state → Step 3.5 evaluates G6 → G6 fires (merge-ready) → dispatch `/do-merge` → return (no `/do-pr-review` re-dispatch)

### Technical Approach

**1. Add `pr_merge_state` and `ci_all_passing` to enriched `sdlc_stage_query` output.**

In `tools/sdlc_stage_query.py`, when a `pr_number` is available in the session (or derivable from `gh pr list --search "#{issue_number}" --state open`), fetch live PR state:

```bash
gh pr view {pr_number} --json mergeStateStatus,statusCheckRollup --jq '{
  mergeStateStatus: .mergeStateStatus,
  ciAllPassing: ([.statusCheckRollup[].conclusion] | all(. == "SUCCESS"))
}'
```

Add to `_meta`:
- `pr_merge_state: str` — value of `mergeStateStatus` (e.g., `"CLEAN"`, `"BLOCKED"`, `"DIRTY"`)
- `ci_all_passing: bool` — `True` if all `statusCheckRollup` conclusions are `"SUCCESS"` (empty list = `True` for repos with no required checks)

These are read-only lookups. On any `gh` CLI failure, default both to `None` (guard G6 will not fire if either is `None`).

**2. Implement G6 in `agent/sdlc_router.py`.**

```python
def _guard_g6_terminal_merge_ready(stage_states: dict, meta: dict, context: dict) -> Dispatch | None:
    """G6: PR is mergeable, CI green, DOCS done, and review verdict APPROVED — fast-path to /do-merge."""
    pr_number = meta.get("pr_number")
    if not pr_number:
        return None
    if meta.get("pr_merge_state") != "CLEAN":
        return None
    if meta.get("ci_all_passing") is not True:
        return None
    # DOCS must be completed before dispatching merge
    if stage_states.get("DOCS") not in (STATUS_COMPLETED,):
        return None
    verdicts = stage_states.get("_verdicts") or {}
    review_verdict = _verdict_text(verdicts.get("REVIEW"))
    if "APPROVED" not in review_verdict.upper():
        return None
    return Dispatch(skill=SKILL_DO_MERGE, reason="PR is mergeable, CI green, DOCS done, review APPROVED — fast-path to merge", row_id="G6")
```

Add G6 to the guard evaluation order: **G6 is evaluated LAST** (after G1-G5). G2 and G4 (escalation guards) take priority — a pipeline that's oscillating should escalate to `blocked` before the merge guard fires. G3 (PR lock) redirects `/do-plan` dispatches but does not prevent G6 from firing. Order: G1 → G2 → G3 → G4 → G5 → G6.

**3. Update SKILL.md Step 2e assessment documentation.**

Current Step 2e comment:
```
# reviewDecision: "" (empty) means no review yet
```

Updated:
```
# reviewDecision: "APPROVED" means formal GitHub review approved (non-self-authored PRs)
# reviewDecision: "CHANGES_REQUESTED" means formal GitHub review requested changes
# reviewDecision: "" (empty) — AMBIGUOUS for self-authored PRs:
#   - For non-self-authored PRs: no review posted yet
#   - For self-authored PRs: expected even after review — check _verdicts["REVIEW"] from sdlc_stage_query
# Always cross-check _meta.latest_review_verdict before concluding no review exists.
```

**4. Update SKILL.md Step 3.5 guard table.**

Add G6 row:

| Guard | Condition | Forced Dispatch |
|-------|-----------|-----------------|
| G6: Terminal merge ready | `pr_number` set AND `pr_merge_state == "CLEAN"` AND `ci_all_passing == True` AND `DOCS == "completed"` AND `_verdicts["REVIEW"]` contains `APPROVED` | `/do-merge {pr_number}` |

**5. Add guard parity infrastructure and G6 coverage to the parity test.**

`tests/unit/test_sdlc_skill_md_parity.py` (added by PR #1044) currently only covers `DISPATCH_RULES` (rows 1–10b); it has no `parse_guard_rows()` function and no guard-table validation. This step has two parts:

**Part A — Add `parse_guard_rows(md: str) -> list[dict]` to the parity test module.** The function must:
- Find the "## Step 3:" heading (or whichever step contains the guard table in SKILL.md after this plan's SKILL.md updates land).
- Parse consecutive `|`-delimited rows, extracting `guard_id` (first cell, e.g. `"G1"`, `"G6"`), `condition` (second cell), and `forced_dispatch` (third cell).
- Return only rows whose first cell matches the pattern `G\d+`.

**Part B — Add guard parity tests using the new parser.** Specifically:
- `test_guard_row_ids_in_python()`: asserts that every `guard_id` found by `parse_guard_rows()` has a corresponding function name `_guard_g{N}_*` exported by `agent/sdlc_router.py`.
- `test_g6_guard_row_present_in_skill_md()`: asserts that a row with `guard_id == "G6"` exists in the parsed guard table after this plan's SKILL.md edits.

The parity test module imports `DISPATCH_RULES` from `agent.sdlc_router`; extend it to also import `GUARDS` (a new list of guard callables exported by `agent/sdlc_router.py` alongside `DISPATCH_RULES`) so the tests can enumerate guard names programmatically.

**6. Add regression test.**

In `tests/unit/test_sdlc_router_oscillation.py` (added by PR #1044), add:

```python
def test_1043_pr264_8step_terminates():
    """Replay the PR #264 8-step incident: router must dispatch /do-merge at step 3."""
    # Seed: ALL stages completed (ISSUE through DOCS inclusive), REVIEW verdict APPROVED,
    # pr_number=264, pr_merge_state="CLEAN", ci_all_passing=True
    states = {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "completed",
        "TEST": "completed",
        "REVIEW": "completed",
        "DOCS": "completed",   # DOCS must be seeded as completed for G6 to fire
        "_verdicts": {"REVIEW": {"verdict": "APPROVED"}},
    }
    meta = {
        "pr_number": 264,
        "pr_merge_state": "CLEAN",
        "ci_all_passing": True,
        "latest_review_verdict": "APPROVED",
    }
    # Step 3 (after two prior /do-pr-review dispatches with same SHA): G6 fires
    result = decide_next_dispatch(states, meta)
    assert isinstance(result, Dispatch)
    assert result.skill == SKILL_DO_MERGE
    assert result.row_id == "G6"


def test_g6_does_not_fire_when_docs_not_done():
    """G6 must not dispatch /do-merge if DOCS stage is not completed."""
    states = {
        "ISSUE": "completed",
        "PLAN": "completed",
        "CRITIQUE": "completed",
        "BUILD": "completed",
        "TEST": "completed",
        "REVIEW": "completed",
        # DOCS intentionally absent / not completed
        "_verdicts": {"REVIEW": {"verdict": "APPROVED"}},
    }
    meta = {
        "pr_number": 264,
        "pr_merge_state": "CLEAN",
        "ci_all_passing": True,
        "latest_review_verdict": "APPROVED",
    }
    result = decide_next_dispatch(states, meta)
    # G6 must NOT fire — should route to /do-docs (Row 9) instead
    assert isinstance(result, Dispatch)
    assert result.skill != SKILL_DO_MERGE
```

## Failure Path Test Strategy

### Exception Handling Coverage

- [x] `tools/sdlc_stage_query.py`: The `gh pr view` call for `pr_merge_state`/`ci_all_passing` is wrapped in a try/except. On any failure (network error, unknown PR, jq parse error), both fields default to `None` and G6 does not fire. Test: mock a `gh` CLI failure and verify `_meta["pr_merge_state"]` is `None` and the dispatch falls through to the normal table.
- [x] `agent/sdlc_router.py` `_guard_g6`: All guard functions already catch exceptions internally (see existing guards in PR #1044). G6 follows the same pattern.

### Empty/Invalid Input Handling

- [x] `pr_merge_state=None`: G6 returns `None` (does not fire). Test: session with no `pr_number` or failed `gh` lookup.
- [x] `ci_all_passing=False`: G6 does not fire even if verdict is APPROVED. Test: seeded state with CI failure.
- [x] `_verdicts["REVIEW"]` missing or `{}`: G6 returns `None`. Test: session with no recorded verdict.
- [x] `pr_merge_state="BLOCKED"`: G6 returns `None`. Test: branch protection blocks merge.

### Error State Rendering

- [x] G6 fires but `/do-merge` subsequently fails its own gate check: this is `/do-merge`'s responsibility, not the router's. G6 only dispatches; it does not guarantee the merge will succeed.

## Test Impact

- [x] `tests/unit/test_sdlc_router_oscillation.py` — UPDATE: add `test_1043_pr264_8step_terminates` (positive G6 case with explicit `"DOCS": "completed"` seeding) and `test_g6_does_not_fire_when_docs_not_done` (negative case), plus 4 more negative cases: no pr_number, pr not CLEAN, CI not passing, no APPROVED verdict. All assertions use `Dispatch` and `Blocked` types — no `GuardTripped`.
- [x] `tests/unit/test_sdlc_stage_query.py` — UPDATE: add cases for new `_meta` fields `pr_merge_state` and `ci_all_passing` (success path, gh failure path, empty statusCheckRollup → True).
- [x] `tests/unit/test_sdlc_skill_md_parity.py` — UPDATE (two-part): (A) add `parse_guard_rows()` function that parses the guard table from SKILL.md; (B) add `test_guard_row_ids_in_python()` and `test_g6_guard_row_present_in_skill_md()` using the new parser. Also add `GUARDS` list export to `agent/sdlc_router.py` for programmatic enumeration.

No existing tests are broken by this change — all additions are additive.

## Rabbit Holes

- **Caching `pr_merge_state` to avoid extra `gh` calls**: Do not add a caching layer. The `gh` call is a single lightweight API hit per `/sdlc` invocation. Caching would introduce staleness bugs (PR could become un-CLEAN between calls).
- **G6 firing on a PR that still needs docs**: G6 explicitly checks `stage_states.get("DOCS") == "completed"` before firing (added in response to critique). If DOCS is not completed, G6 returns `None` and the normal dispatch table routes to Row 9 (`/do-docs`). The `/do-merge` skill's own gate is a belt-and-suspenders backstop, but G6 must not bypass the DOCS check.
- **Formal GitHub review approval for self-authored PRs**: Do not attempt to work around GitHub's restriction on self-approvals (e.g., using a bot account or Admin bypass). The recorded verdict in `_verdicts` is the correct signal for self-authored flows.
- **Replacing G4 with G6**: G4 remains as the universal oscillation safety net. G6 is an optimization (fast path). Both are needed.

## Risks

### Risk 1: G6 fires on a stale cached verdict from a previous review cycle
**Impact:** A PR that had an APPROVED verdict, was then patched (introducing new bugs), re-reviewed with CHANGES REQUESTED, but `_verdicts["REVIEW"]` was not updated to "CHANGES REQUESTED" — G6 would route to `/do-merge` prematurely.
**Mitigation:** This can only happen if `sdlc_verdict record` is not called after the second review. PR #1044 ensures `do-pr-review` always calls `sdlc_verdict record` as its last step. The `_verdicts["REVIEW"]` is always the most recent verdict. Additionally, `_rule_review_has_findings` (evaluated via the dispatch table AFTER guards) would catch a "CHANGES REQUESTED" verdict if G6 somehow misfired — but G6 checks for "APPROVED" specifically, so a "CHANGES REQUESTED" verdict prevents G6 from firing in the first place.

### Risk 2: `ci_all_passing` computation on empty `statusCheckRollup`
**Impact:** A repo with no required checks has an empty `statusCheckRollup`. `all(. == "SUCCESS")` on an empty list evaluates to `True` in Python/jq. G6 would fire even with no CI results.
**Mitigation:** This is the correct behavior — a repo with no CI checks has no failing CI. If the PR is CLEAN and APPROVED, it should merge. Document this edge case explicitly in the implementation.

## Race Conditions

### Race 1: `pr_merge_state` becomes stale between G6 fire and `/do-merge` execution
**Location:** `tools/sdlc_stage_query.py` fetches `pr_merge_state` at G6 evaluation time; `/do-merge` re-fetches from GitHub at merge time.
**Trigger:** Another PR merges to main between G6 evaluation and `/do-merge` execution, causing a merge conflict that flips `mergeStateStatus` from `CLEAN` to `DIRTY`.
**Mitigation:** `/do-merge` performs its own merge gate check (including live `gh pr view`). If the PR is no longer CLEAN when `/do-merge` runs, it will report the failure and not merge. G6 only dispatches; `/do-merge` is the gatekeeper. No data loss, no incorrect merge.

## No-Gos (Out of Scope)

- Forking `do-pr-review` to post formal reviews via a secondary bot/reviewer account to populate `reviewDecision` for self-authored PRs. The stored verdict is the correct solution.
- Changes to `agent/pipeline_state.py` or `bridge/pipeline_graph.py` — these are outside this plan's scope.
- Updating the `do-merge` skill itself — it already performs its own gate check.
- Dashboard changes to surface `pr_merge_state` or G6 state — deferred.

## Update System

No update system changes required — all changes are in `.claude/skills/` and `agent/sdlc_router.py` (local to the skills layer). No new dependencies, no new config files, no deploy steps.

## Agent Integration

No agent integration required — this is a skills-internal change. The new fields in `sdlc_stage_query` and the G6 guard are consumed by the SDLC router skill itself, not exposed via MCP or the bridge.

## Documentation

### Feature Documentation
- [x] Update `docs/features/sdlc-router-oscillation-guard.md` (created by PR #1044) to include G6 — add a row to the guards table and describe the `pr_merge_state` + `ci_all_passing` fields in the enriched query schema section.
- [x] No new feature doc needed — G6 is part of the same oscillation guard feature.

### Inline Documentation
- [x] Docstring on `_guard_g6_terminal_merge_ready()` explaining the fast-path rationale and the guard ordering (evaluated last).
- [x] Comment in SKILL.md Step 2e explaining the `reviewDecision=""` ambiguity for self-authored PRs.

## Success Criteria

- [x] G6 fires correctly: given `pr_merge_state=CLEAN`, `ci_all_passing=True`, `stage_states["DOCS"]="completed"`, `_verdicts["REVIEW"]="APPROVED"`, `sdlc_router.decide_next_dispatch()` returns a `Dispatch` (not `GuardTripped` — that type does not exist) with `skill=SKILL_DO_MERGE` and `row_id="G6"`. Covered by `test_g6_terminal_merge_ready`.
- [x] G6 does NOT fire when `pr_merge_state != "CLEAN"`. Covered by `test_g6_terminal_merge_ready` negative cases.
- [x] G6 does NOT fire when `ci_all_passing=False`. Covered by negative test.
- [x] G6 does NOT fire when `stage_states["DOCS"]` is not `"completed"`. Covered by `test_g6_does_not_fire_when_docs_not_done`.
- [x] G6 does NOT fire when `_verdicts["REVIEW"]` is missing or contains `"CHANGES REQUESTED"`. Covered by negative tests.
- [x] `sdlc_stage_query` enriched output includes `pr_merge_state` and `ci_all_passing`. Covered by `test_sdlc_stage_query.py` updates.
- [x] `sdlc_stage_query` returns `pr_merge_state=None` on `gh` failure — G6 does not fire. Covered by failure test.
- [x] PR #264 8-step incident replay: the router dispatches `/do-merge` at step 3 (all stages through DOCS seeded as completed, APPROVED verdict, CLEAN state). Covered by `test_1043_pr264_8step_terminates`.
- [x] SKILL.md Step 2e comment updated to document `reviewDecision=""` ambiguity for self-authored PRs.
- [x] SKILL.md Step 3.5 guard table includes G6 row with DOCS condition.
- [x] `test_sdlc_skill_md_parity.py` has `parse_guard_rows()`, `test_guard_row_ids_in_python()`, and `test_g6_guard_row_present_in_skill_md()`. `GUARDS` list exported from `agent/sdlc_router.py`.
- [x] Unit tests pass (`pytest tests/unit/test_sdlc_router_oscillation.py tests/unit/test_sdlc_stage_query.py tests/unit/test_sdlc_skill_md_parity.py -x -q`).
- [x] Lint and format clean (`python -m ruff check . && python -m ruff format --check .`).
- [x] `docs/features/sdlc-router-oscillation-guard.md` updated to include G6.

## Team Orchestration

### Team Members

- **Builder (G6 guard + stage-query fields)**
  - Name: `g6-builder`
  - Role: Implement G6 in `agent/sdlc_router.py`, add `pr_merge_state` and `ci_all_passing` to `tools/sdlc_stage_query.py`, update SKILL.md Step 2e and Step 3.5.
  - Agent Type: `builder`
  - Resume: true

- **Test Engineer (regression suite)**
  - Name: `g6-test-engineer`
  - Role: Add `test_g6_terminal_merge_ready`, `test_1043_pr264_8step_terminates`, update `test_sdlc_stage_query.py` and `test_sdlc_skill_md_parity.py`.
  - Agent Type: `test-engineer`
  - Resume: true

- **Validator**
  - Name: `g6-validator`
  - Role: Run full test suite, verify all success criteria, verify parity test passes.
  - Agent Type: `validator`
  - Resume: true

- **Documentarian**
  - Name: `g6-documentarian`
  - Role: Update `docs/features/sdlc-router-oscillation-guard.md` with G6.
  - Agent Type: `documentarian`
  - Resume: true

## Step by Step Tasks

### 1. Add `pr_merge_state` and `ci_all_passing` to `sdlc_stage_query`
- **Task ID**: build-stage-query-fields
- **Depends On**: none (assumes PR #1044 merged)
- **Validates**: `tests/unit/test_sdlc_stage_query.py` (update)
- **Assigned To**: g6-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tools/sdlc_stage_query.py`, when `pr_number` is available, fetch `gh pr view {pr_number} --json mergeStateStatus,statusCheckRollup` and populate `_meta["pr_merge_state"]` and `_meta["ci_all_passing"]`.
- On any `gh` CLI failure, set both to `None`.
- Empty `statusCheckRollup` → `ci_all_passing=True` (no required checks = no failing checks).

### 2. Implement G6 in `agent/sdlc_router.py`
- **Task ID**: build-g6-guard
- **Depends On**: build-stage-query-fields
- **Validates**: `tests/unit/test_sdlc_router_oscillation.py` (update)
- **Assigned To**: g6-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_guard_g6_terminal_merge_ready()` to the guard list in `_evaluate_guards()`. Return type is `Dispatch | None` (not `GuardResult` — that type does not exist in `agent/sdlc_router.py`).
- Order: G6 is evaluated AFTER G1-G5 (escalation guards take priority).
- G6 fires: `pr_merge_state="CLEAN"` AND `ci_all_passing=True` AND `stage_states.get("DOCS") == STATUS_COMPLETED` AND `_verdicts["REVIEW"]` contains "APPROVED".
- Returns `Dispatch(skill=SKILL_DO_MERGE, reason="PR is mergeable, CI green, DOCS done, review APPROVED — fast-path to merge", row_id="G6")`.
- Export a `GUARDS` list (alongside `DISPATCH_RULES`) containing all guard callables in evaluation order. This enables the parity test to enumerate guard names programmatically.

### 3. Update SKILL.md Step 2e and Step 3.5
- **Task ID**: build-skill-md-updates
- **Depends On**: none
- **Assigned To**: g6-builder
- **Agent Type**: builder
- **Parallel**: true
- Step 2e: update `reviewDecision=""` comment to document self-authored PR ambiguity.
- Step 3.5 guard table: add G6 row after G5.

### 4. Write tests
- **Task ID**: test-g6
- **Depends On**: build-g6-guard, build-stage-query-fields, build-skill-md-updates
- **Validates**: `tests/unit/test_sdlc_router_oscillation.py`, `tests/unit/test_sdlc_stage_query.py`, `tests/unit/test_sdlc_skill_md_parity.py`
- **Assigned To**: g6-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `test_g6_terminal_merge_ready` (positive case with explicit `"DOCS": "completed"` seeding) and 5 negative cases: no pr_number, pr not CLEAN, CI not passing, no APPROVED verdict, **DOCS not completed** (new — tests Concern 1 fix). All assertions use `Dispatch` and `Blocked` types only — do NOT reference `GuardTripped` (type does not exist).
- Add `test_1043_pr264_8step_terminates`: seed state with ALL stages completed (ISSUE through DOCS inclusive), APPROVED verdict, CLEAN merge state, `ci_all_passing=True`. Assert `isinstance(result, Dispatch)` and `result.skill == SKILL_DO_MERGE` and `result.row_id == "G6"`.
- Add `test_g6_does_not_fire_when_docs_not_done`: seed same as above but omit `"DOCS": "completed"`. Assert result is not `/do-merge`.
- Update `test_sdlc_stage_query.py` for new fields (success path, failure path, empty CI list).
- In `test_sdlc_skill_md_parity.py`: (A) implement `parse_guard_rows(md: str) -> list[dict]` parsing the guard table from SKILL.md; (B) add `test_guard_row_ids_in_python()` asserting each guard row ID has a matching callable in `GUARDS`; (C) add `test_g6_guard_row_present_in_skill_md()` asserting G6 row exists in the parsed table.

### 5. Validate
- **Task ID**: validate-all
- **Depends On**: test-g6
- **Assigned To**: g6-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_sdlc_router_oscillation.py tests/unit/test_sdlc_stage_query.py tests/unit/test_sdlc_skill_md_parity.py -x -q`.
- Run `python -m ruff check . && python -m ruff format --check .`.
- Smoke test: `python -m tools.sdlc_stage_query --issue-number 1043` — verify `_meta` shape includes `pr_merge_state` and `ci_all_passing`.
- Verify all success criteria checked.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: g6-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-router-oscillation-guard.md`: add G6 row to the guards table, add `pr_merge_state` and `ci_all_passing` to the enriched query schema section.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_sdlc_router_oscillation.py tests/unit/test_sdlc_stage_query.py tests/unit/test_sdlc_skill_md_parity.py -x -q` | exit code 0 |
| G6 incident replay | `pytest tests/unit/test_sdlc_router_oscillation.py::test_1043_pr264_8step_terminates -v` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Enriched query has new fields | `python -m tools.sdlc_stage_query --issue-number 1043 \| python -c "import json,sys; d=json.load(sys.stdin); m=d['_meta']; assert 'pr_merge_state' in m and 'ci_all_passing' in m"` | exit code 0 |
| SKILL.md parity | `pytest tests/unit/test_sdlc_skill_md_parity.py -v` | exit code 0 |

## Critique Results

**Verdict: READY TO BUILD** (concerns addressed in revision)

**Blockers resolved:**

1. **Wrong return types in G6 code snippets** — The original plan used `GuardResult` and `GuardTripped` which do not exist in `agent/sdlc_router.py` (PR #1044). Corrected to `Dispatch | None` return type and `Dispatch(skill=..., row_id="G6")` return value throughout the plan.

2. **Parity test update under-specified** — The original plan said "add G6 to expected guard list" but `test_sdlc_skill_md_parity.py` has no guard-table parsing infrastructure (only `DISPATCH_RULES` coverage). Expanded to: (A) add `parse_guard_rows()` function, (B) add guard parity tests `test_guard_row_ids_in_python()` and `test_g6_guard_row_present_in_skill_md()`, (C) add `GUARDS` list export to `agent/sdlc_router.py`.

**Concerns addressed:**

1. **G6 DOCS bypass** — G6 now explicitly checks `stage_states.get("DOCS") == STATUS_COMPLETED` before firing. The guard returns `None` if DOCS is not done, routing to Row 9 (`/do-docs`) instead. Guard table entry, code snippet, Rabbit Holes, and Success Criteria all updated.

2. **Regression test type errors and ambiguous DOCS seeding** — `test_1043_pr264_8step_terminates` now seeds all stages through DOCS as `"completed"` explicitly, uses `Dispatch` type assertions only, and asserts `result.row_id == "G6"`. Added companion `test_g6_does_not_fire_when_docs_not_done` for the negative DOCS case.

**Revision applied:** `revision_applied: true`

## Open Questions

None — scope is bounded by the freshness check findings and the dependency on PR #1044.
