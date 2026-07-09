---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-09
tracking: https://github.com/tomcounsell/ai/issues/1944
last_comment_id:
---

# do-merge: gate on DOCS-stage completion, not just docs-file existence

## Problem

`/do-merge` is the terminal SDLC merge gate — the one deterministic checkpoint
every merge path funnels through (the merge-guard hook enforces it). It verifies
`OPEN` / `MERGEABLE` / `CLEAN` / CI-green / REVIEW-approved / issue-linked before
squash-merging. It does NOT verify that the DOCS stage completed. Router row 9
(`_rule_review_approved_docs_not_done`) enforces DOCS-before-MERGE *inside the
router*, but nothing enforces it *at the gate*. When merge is reached by a path
that bypasses the router — a forked `/do-sdlc` run, a raw-terminal `gh pr merge`
through the merge-guard, or a foreign-repo run with no substrate — a PR merges
with its DOCS stage still `in_progress`, killed, or never started.

The existing "Documentation Gate" (`docs/sdlc/do-merge.md:43-45`) checks only
that `docs/features/{slug}.md` *exists* — not that the `/do-docs` cascade *ran*.
Stale route maps, index tables, and cross-references in already-existing docs
sail straight through.

**Current behavior:** the merge gate has no notion of DOCS-stage completion. An
incomplete or absent DOCS stage does not block a merge. Real incident: cuttlefish
PR #577 merged with its DOCS stage still running (later killed), leaving a
canonical route-map doc stale; it had to be patched in a separate manual PR after the fact.

**Desired outcome:** `/do-merge` treats DOCS-stage completion as a first-class
precondition, analogous to the Step 2 REVIEW-verdict gate, and **fails closed**
when DOCS is required but not `completed` — without creating the
merge-authorization file. A legitimate DOCS-skip PR (#1799) still merges.

## Freshness Check

**Baseline commit:** `2fb1f8ef`
**Issue filed at:** 2026-07-08T05:11:56Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `.claude/skills-global/do-merge/SKILL.md:99-118` — Step 2 REVIEW-verdict gate, generic-reads-`gh reviewDecision`-defers-recorded-verdict-to-substrate pattern — still holds.
- `docs/sdlc/do-merge.md:43-45` — "Documentation Gate" checks only `docs/features/{slug}.md` file existence — still holds.
- `docs/sdlc/do-merge.md:19-27` — Step 2 substrate mapping via `sdlc-tool verdict get` — still holds (this is the shape Step 2b mirrors).
- `agent/sdlc_router.py:1082-1095` — `_rule_review_approved_docs_not_done` gates row 9 on `DOCS == STATUS_COMPLETED` in-router only — still holds. Out of scope.
- `agent/sdlc_router.py:599-635` — G6 fast-path also guards on `stage_states["DOCS"] == STATUS_COMPLETED` — still holds. Out of scope.
- `tools/sdlc_stage_marker.py:78` — `_VALID_STATUSES = frozenset(["in_progress","completed"])` — still holds. **This is load-bearing:** there is no writable `skipped` status.
- `tools/sdlc_stage_query.py` — enriched `--format json` output is `{"stages": {"DOCS": "completed", ...}, "_meta": {...}}`; stage states are bare status strings under `stages`. Confirmed live against issues 1965/1980/1962.

**Cited sibling issues/PRs re-checked:**
- #1915 (fork background strands) — **CLOSED** since filing (was OPEN in the issue body). The root cause the issue points at is fixed; this gate remains the deterministic backstop.
- #1799 (router-level DOCS-skip) — still OPEN / unshipped. The gate is forward-compatible regardless (see Solution).

**Commits on main since issue was filed (touching referenced files):**
- `9e2f2b5c` fix(router): ... row 9 verdict gate — touched `agent/sdlc_router.py` (row 9 now also gates on a recorded APPROVED verdict). Read in its current form; does not change this plan's premise (row 9 stays in-router; the gate is separate).
- `0f33567e` SDLC issue ownership lock — touched `agent/sdlc_router.py`, unrelated to the DOCS gate.
- Neither `docs/sdlc/do-merge.md` nor `.claude/skills-global/do-merge/SKILL.md` changed since filing.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** The issue's solution-sketch extraction snippet
(`json.load(sys.stdin).get('DOCS',{}).get('status','')`) is **wrong** for this
repo's output shape and is corrected in Technical Approach to
`.get('stages',{}).get('DOCS','')`.

## Prior Art

- **#1799** — Router-level DOCS-skip for doc-free trivial PRs (OPEN). Proposes recording DOCS as `completed (skipped: trivial)` at router row 9 for `docs-only`/`lockfile-only` trivial PRs. Because a skip resolves to the `completed` status, the gate's `== completed` check admits it automatically — the gate needs no special skip-branch.
- **#1915** — Fork background strands (CLOSED). The root cause of *why* the DOCS dispatch was lost in the incident. This gate is defense-in-depth on top of that fix, not a replacement.
- **enforce-review-docs-stages.md (#418 / PR #421)** — Established mandatory REVIEW+DOCS enforcement via the pipeline state machine and `/do-docs` writing `status: docs_complete` to the plan. This gate is the merge-checkpoint complement.
- **`tests/unit/test_do_merge_review_filter.py`** — The reusable test pattern: extract an embedded shell snippet from `docs/sdlc/do-merge.md` and run it against synthetic input, asserting the gate decision without a live PR. The DOCS gate test mirrors this exactly.

## Data Flow

1. **Entry point:** `/do-merge` invoked with a PR number (via `/sdlc` MERGE dispatch, a forked `/do-sdlc`, or manually).
2. **Repo Context Probe:** the skill reads `docs/sdlc/do-merge.md` (substrate present in this repo).
3. **Step 1-2:** verify PR state + recorded REVIEW verdict via `sdlc-tool`.
4. **Step 2b (NEW):** `sdlc-tool stage-query --issue-number N` → parse `stages.DOCS` → PASS if `completed`, FAIL (route to `/do-docs`) if `in_progress`/`pending`/`""`.
5. **Step 4:** only on all-pass, `touch data/merge_authorized_{PR}` → `gh pr merge` → `rm`.
6. **Output:** merged PR, or a gate refusal naming the DOCS blocker; no auth file created on refusal.

## Architectural Impact

- **New dependencies:** none. Reuses the existing `sdlc-tool stage-query` substrate the gate already calls for PR-number recovery.
- **Interface changes:** none in code — the change is to two skill-body markdown files plus one test. No Python function signatures change.
- **Coupling:** the gate already depends on `sdlc-tool`; Step 2b adds one more read of the same tool. No new coupling.
- **Data ownership:** unchanged. The router still owns DOCS sequencing; the gate reads DOCS status read-only.
- **Reversibility:** trivially reversible — revert the two markdown edits and delete the test.

## Appetite

**Size:** Small

**Team:** Solo dev, plus one validator round

**Interactions:**
- PM check-ins: 1 (ratify the generic-path design decision — see Open Questions)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `sdlc-tool` wrapper resolves | `sdlc-tool stage-query --issue-number 1944 >/dev/null 2>&1; echo $?` | Substrate the gate reads |
| pytest available | `python -m pytest --version` | Run the new gate test |

## Solution

### Key Elements

- **Step 2b — Verify DOCS Stage Completed** (`docs/sdlc/do-merge.md`): a new
  substrate-mapping step placed immediately after the Step 2 REVIEW-verdict
  mapping. Reads `stages.DOCS` from `sdlc-tool stage-query`; PASS on `completed`,
  FAIL closed otherwise, route back to `/do-docs`.
- **Extended "Documentation Gate"** (`docs/sdlc/do-merge.md`): now checks DOCS
  *stage completion* (via Step 2b), not merely `docs/features/{slug}.md` file
  existence. File existence stays as an additional check; stage completion is the
  new authoritative one.
- **Generic-path precondition text** (`.claude/skills-global/do-merge/SKILL.md`):
  a Step-2-adjacent paragraph naming "DOCS-stage completion" as a precondition
  the repo addendum supplies (parallel to how REVIEW verdict is deferred to the
  substrate), with an explicit **announced-skip** for the no-substrate case.
- **Gate test** (`tests/unit/test_do_merge_docs_gate.py`): extracts the Step 2b
  extraction/decision snippet from `docs/sdlc/do-merge.md` and asserts the
  decision across `completed` / `in_progress` / `pending` / missing / skip inputs.

### Flow

`/do-merge {PR}` → Step 1 PR-state PASS → Step 2 REVIEW verdict APPROVED →
**Step 2b query `stages.DOCS`** → `completed` ⇒ continue to Step 4 merge;
`in_progress`/`pending`/`""` ⇒ **refuse, no auth file, route to `/do-docs`**.

### Technical Approach

**Step 2b extraction (correct for this repo's output shape):**

```bash
DOCS_STATUS=$(sdlc-tool stage-query --issue-number {issue_number} 2>/dev/null \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('stages',{}).get('DOCS',''))")
case "$DOCS_STATUS" in
  completed) echo "DOCS_GATE: PASS — DOCS stage completed" ;;
  *)         echo "DOCS_GATE: FAIL — DOCS stage is '${DOCS_STATUS:-<missing>}', not completed"
             echo "GATES_FAILED" ;;   # route back to /do-docs; do NOT create the auth file
esac
```

- **Why `.get('stages',{}).get('DOCS','')` and not the issue's sketch:** the
  enriched query returns bare status strings under `stages`, not
  `{"DOCS": {"status": ...}}`. Verified live.
- **Why no explicit skip-branch (resolves open question #2):**
  `sdlc-tool stage-marker` can only write `in_progress` or `completed`
  (`_VALID_STATUSES`, `tools/sdlc_stage_marker.py:78`). #1799's DOCS-skip records
  DOCS as `completed` (the "skipped" nuance lives in the router `reason` string,
  not the status). So a single `== completed` check admits both a real completion
  and a legitimate skip. The gate never needs to distinguish them.
- **Fail closed on degraded substrate:** if `sdlc-tool` returns empty `stages`
  (Redis down, session gone), `DOCS_STATUS` is `""` → FAIL. This mirrors the
  Step 2 REVIEW-verdict rule ("if approval cannot be confirmed, FAIL closed").
  At merge time the PM session is live, so `stages` is populated; an empty result
  is a genuine substrate fault, correctly treated as a refusal.

**Generic/no-substrate path (resolves open question #1 — decision: option c, announced not silent):**
- The global `SKILL.md` gains a Step-2-adjacent paragraph: DOCS-stage completion
  is a precondition the repo addendum supplies via its substrate; when no
  substrate exists, the gate **cannot deterministically verify DOCS** and MUST
  emit a visible advisory line —
  `"DOCS-completion gate: NOT ENFORCED — no substrate; foreign-repo merges rely on supervisor sequencing (see #1915)."`
  This is an *announced* non-gate, not a silent pass, satisfying the acceptance
  criterion "explicitly specified and documented (not a silent pass)."
- **Justification for rejecting (a) and (b):**
  - (a) require an explicit `docs-verified` signal from the caller — pushes a new
    required argument into a *portable* skill; a caller that forgets it either
    hard-blocks every foreign-repo merge or defaults unsafe. Extra coupling for a
    signal no foreign repo currently produces.
  - (b) classify PR shape and auto-pass trivial shapes — depends on
    `scripts/pr_shape_classify.py`, which is *this repo's* tool and does not exist
    in foreign repos. It cannot be the generic answer.
  - (c) honest determinism limit + announced skip — pairs with the now-CLOSED
    #1915 root-cause fix and makes the gap auditable in the merge log. Chosen.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception: pass` blocks — the change is shell-in-markdown plus a test. State "No exception handlers in scope" for the gate snippet; the test itself asserts observable decision strings.

### Empty/Invalid Input Handling
- [ ] `DOCS_STATUS == ""` (empty `stages`, missing DOCS key) → gate FAILS closed. Covered by a test case feeding `{"stages": {}, "_meta": {}}` and asserting `GATES_FAILED`.
- [ ] Malformed JSON from `sdlc-tool` (non-JSON stdout) → `python3 -c` raises, non-zero exit, `DOCS_STATUS` empty → FAIL. Test feeds non-JSON and asserts non-pass.

### Error State Rendering
- [ ] The refusal path prints `DOCS_GATE: FAIL — ...` and `GATES_FAILED`; the merge report surfaces the specific DOCS blocker. Test asserts the FAIL message names the observed status.

## Test Impact

- [ ] `tests/unit/test_do_merge_docs_gate.py` — REPLACE/CREATE: new test file mirroring `test_do_merge_review_filter.py`; extracts the Step 2b snippet from `docs/sdlc/do-merge.md` and asserts the decision across `completed`/`in_progress`/`pending`/missing/skip inputs.
- [ ] `tests/unit/test_do_merge_baseline.py` — UPDATE (only if it asserts the exact set of gate sections/steps): add the Step 2b / extended Documentation Gate to any section-presence assertion. Verify before editing; leave untouched if it does not enumerate steps.
- [ ] `tests/unit/test_do_merge_review_filter.py` — no change expected (independent snippet); confirm it still passes after the `docs/sdlc/do-merge.md` edit.

No other existing tests are affected — the change adds one gate step to a markdown skill body and one test file; it modifies no Python interfaces, no router logic, and no session-execution code.

## Rabbit Holes

- **Rewriting the router (row 9 / G6).** Out of scope. The router already sequences DOCS-before-MERGE in-router; this issue hardens the *gate*. Do not touch `agent/sdlc_router.py`.
- **Fixing #1915.** Already CLOSED. This gate is the deterministic backstop, not the root-cause fix.
- **Executing the full gate end-to-end in a test.** Infeasible without a live PR + populated Redis session. Follow the review-filter precedent: test the extracted snippet against synthetic JSON, not the whole skill.
- **Adding a new `skipped` status to `sdlc-tool`.** Unnecessary and out of scope — skip already resolves to `completed`. Widening `_VALID_STATUSES` would be a cross-cutting change the gate does not need.
- **Wiring the generic path to shape-classification.** Rejected (option b); do not import this-repo-only scripts into the portable skill.

## Risks

### Risk 1: The extraction snippet drifts from `sdlc-tool`'s actual output shape
**Impact:** the gate reads `""` for a truly-completed DOCS stage and blocks a valid merge, or silently passes.
**Mitigation:** the test extracts the *live* snippet from `docs/sdlc/do-merge.md` and runs it against fixtures matching the real `{"stages": {...}}` shape — a drift breaks the test. The correct shape is pinned in Technical Approach and verified live.

### Risk 2: A legitimate DOCS-skip PR (#1799) is wrongly blocked
**Impact:** doc-free trivial PRs can't merge — a regression the acceptance criteria forbid.
**Mitigation:** the gate passes on `completed`; #1799 records a skip *as* `completed`. A dedicated test case ("DOCS recorded completed via skip") asserts PASS. If #1799 ever changes its recorded value, the test is where that contract is caught.

### Risk 3: Degraded substrate blocks an otherwise-valid merge
**Impact:** a transient Redis outage at merge time fails the DOCS gate, stalling a good PR.
**Mitigation:** fail-closed is the intended posture (mirrors Step 2 REVIEW). The refusal names the substrate fault explicitly so the operator can retry once the substrate recovers. This is a deliberate safety tradeoff, documented in the gate text.

## Race Conditions

No race conditions identified. The gate is a synchronous, single-process sequence
of `sdlc-tool` / `gh` reads executed by one `/do-merge` invocation; there is no
shared mutable state or concurrent access introduced. The DOCS status it reads is
written earlier by the DOCS stage and is a settled value by merge time (row 9
already withholds router-driven MERGE dispatch until DOCS is `completed`; the gate
hardens the bypass paths, which are likewise sequential).

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1799] Router-level DOCS-skip implementation — the gate must *respect* a skip (which resolves to `completed`), but shipping the router skip itself is #1799's work.
- [SEPARATE-SLUG #1915] Fork background-strand root-cause fix — already CLOSED; this gate is defense-in-depth, not that fix.
- Router changes to `agent/sdlc_router.py` (row 9 / G6) — the router already sequences DOCS-before-MERGE; this plan hardens only the gate. (Anti-criterion below asserts no router edit.)
- Adding a daily-reflection backstop for merges that bypass `/do-merge` entirely (issue's open question #3) — the `merged-branch-cleanup` reflection already backstops plan migration on bypass paths; a DOCS-specific reflection is a separable enhancement, not required to close this issue.

## Update System

No update system changes required — this feature edits two skill-body markdown
files (`docs/sdlc/do-merge.md`, `.claude/skills-global/do-merge/SKILL.md`) and
adds one test. `docs/sdlc/do-merge.md` is repo-only (not synced). The global
`do-merge` SKILL.md *is* hardlinked to every machine by `/update`
(`scripts/update/hardlinks.py::sync_claude_dirs`), so the generic-path text
propagates automatically on the next `/update` with no wiring change. No new
deps, no migrations.

## Agent Integration

No agent integration required — this is a change to the `/do-merge` skill body
the agent already invokes at the MERGE stage. No new CLI entry point, no
`.mcp.json` change, no bridge import. The gate continues to call the existing
`sdlc-tool stage-query` substrate the agent already uses.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/enforce-review-docs-stages.md` — add a "Merge-Gate DOCS Precondition" section documenting Step 2b, the `stages.DOCS == completed` contract, the skip-resolves-to-completed rule, and the generic-path announced-skip decision.
- [ ] Verify `docs/features/README.md` index entry for enforce-review-docs-stages still reads correctly (no new file, so likely no index change; confirm).

### Inline Documentation
- [ ] Docstring header in `tests/unit/test_do_merge_docs_gate.py` explaining the snippet-extraction test strategy (mirroring the review-filter test's header).

## Success Criteria

- [ ] `/do-merge` fails closed (prints `GATES_FAILED`, creates no `data/merge_authorized_{PR}` file) when `stages.DOCS` is `in_progress`, `pending`, or missing.
- [ ] A DOCS-skip PR whose DOCS status is `completed` still passes the gate (test asserts PASS).
- [ ] The "Documentation Gate" section verifies DOCS *stage completion*, not only `docs/features/{slug}.md` file existence.
- [ ] The generic/no-substrate path emits an explicit announced-skip advisory line (not a silent pass); documented in the global SKILL.md.
- [ ] Reproducible-then-prevented: a fixture with `stages.DOCS == "in_progress"` is refused by the extracted gate snippet in `test_do_merge_docs_gate.py`.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] No edit to `agent/sdlc_router.py` (anti-criterion below).

## Team Orchestration

### Team Members

- **Builder (merge-gate)**
  - Name: gate-builder
  - Role: Edit `docs/sdlc/do-merge.md` (Step 2b + extended Documentation Gate) and `.claude/skills-global/do-merge/SKILL.md` (generic-path precondition + announced skip); add `tests/unit/test_do_merge_docs_gate.py`.
  - Agent Type: builder
  - Resume: true

- **Validator (merge-gate)**
  - Name: gate-validator
  - Role: Verify the gate snippet passes/fails on the right inputs, the router is untouched, the test passes, and the generic-path text is present.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add Step 2b + extend Documentation Gate in the repo addendum
- **Task ID**: build-addendum
- **Depends On**: none
- **Validates**: tests/unit/test_do_merge_docs_gate.py (create)
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: true
- In `docs/sdlc/do-merge.md`, add a "Step 2b: Verify DOCS Stage Completed" mapping in the Stage/Verdict Substrate section, immediately after the Step 2 REVIEW-verdict bullet, using the `.get('stages',{}).get('DOCS','')` extraction and fail-closed decision from Technical Approach.
- Extend the "Documentation Gate" section so it states the authoritative check is DOCS *stage completion* (Step 2b), with `docs/features/{slug}.md` existence kept as a secondary check.

### 2. Add generic-path precondition + announced skip to the global skill
- **Task ID**: build-global
- **Depends On**: none
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: true
- In `.claude/skills-global/do-merge/SKILL.md`, add a Step-2-adjacent paragraph naming DOCS-stage completion as a substrate-supplied precondition, and specify the announced-skip advisory line for the no-substrate case.

### 3. Add the gate test
- **Task ID**: build-test
- **Depends On**: build-addendum
- **Validates**: tests/unit/test_do_merge_docs_gate.py
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_do_merge_docs_gate.py` mirroring `test_do_merge_review_filter.py`: extract the Step 2b snippet from `docs/sdlc/do-merge.md`, run against synthetic `{"stages": {"DOCS": ...}}` JSON, assert PASS for `completed`, FAIL for `in_progress`/`pending`/missing/malformed, PASS for skip-as-`completed`.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-addendum, build-global, build-test
- **Assigned To**: gate-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Add the "Merge-Gate DOCS Precondition" section to `docs/features/enforce-review-docs-stages.md`.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-addendum, build-global, build-test, document-feature
- **Assigned To**: gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_do_merge_docs_gate.py tests/unit/test_do_merge_review_filter.py -q`.
- Confirm `git diff --name-only` does NOT include `agent/sdlc_router.py`.
- Confirm the announced-skip line is present in `.claude/skills-global/do-merge/SKILL.md`.
- Confirm the Step 2b snippet uses `.get('stages',{}).get('DOCS'` and not `.get('DOCS',{}).get('status'`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Gate test passes | `pytest tests/unit/test_do_merge_docs_gate.py -q` | exit code 0 |
| Review-filter test still passes | `pytest tests/unit/test_do_merge_review_filter.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tests/unit/test_do_merge_docs_gate.py` | exit code 0 |
| Format clean | `python -m ruff format --check tests/unit/test_do_merge_docs_gate.py` | exit code 0 |
| Step 2b present | `grep -c "Step 2b" docs/sdlc/do-merge.md` | output > 0 |
| Correct extraction shape | `grep -c "get('stages'" docs/sdlc/do-merge.md` | output > 0 |
| No wrong extraction shape | `grep -c "get('DOCS',{}).get('status'" docs/sdlc/do-merge.md` | match count == 0 |
| Announced skip in global skill | `grep -c "NOT ENFORCED" .claude/skills-global/do-merge/SKILL.md` | output > 0 |
| Router untouched (anti-criterion) | `git diff --name-only origin/main -- agent/sdlc_router.py \| wc -l` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Ratify the generic-path decision (option c).** The plan picks option (c) —
   the no-substrate path emits an *announced-skip* advisory line rather than
   deterministically gating DOCS, pairing with the now-CLOSED #1915. Confirm this
   is acceptable versus option (a) requiring a caller-supplied `docs-verified`
   signal. (The plan argues (b) is infeasible generically since it needs this
   repo's `pr_shape_classify.py`.)
2. **Issue open-question #3 (daily-reflection backstop).** The plan scopes this
   OUT (the existing `merged-branch-cleanup` reflection already backstops the
   plan-migration invariant on bypass paths; a DOCS-specific reflection is
   separable). Confirm this is acceptable, or whether a DOCS-completion reflection
   backstop should be filed as its own issue.
