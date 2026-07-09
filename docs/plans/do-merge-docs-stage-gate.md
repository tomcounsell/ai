---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-09
tracking: https://github.com/tomcounsell/ai/issues/1944
last_comment_id:
revision_applied: true
---

# do-merge: gate on DOCS-stage completion, not just docs-file existence

## Problem

`/do-merge` is the terminal SDLC merge gate — the one deterministic checkpoint
every merge path funnels through (the merge-guard hook enforces it). It verifies
`OPEN` / `MERGEABLE` / `CLEAN` / CI-green / REVIEW-approved / issue-linked before
squash-merging. It does NOT verify that the DOCS stage completed. Router row 9
(`_rule_review_approved_docs_not_done`) enforces DOCS-before-MERGE *inside the
router*, but nothing enforces it *at the gate*. When merge is reached by a path
that bypasses the router — a forked `/do-sdlc` run or a raw-terminal `gh pr
merge` through the merge-guard, **in a substrate repo** — a PR can merge with its
DOCS stage still `in_progress`, killed, or never started.

The existing "Documentation Gate" (`docs/sdlc/do-merge.md:43-45`) checks only
that `docs/features/{slug}.md` *exists* — not that the `/do-docs` cascade *ran*.
Stale route maps, index tables, and cross-references in already-existing docs
sail straight through.

**Current behavior:** the merge gate has no notion of DOCS-stage completion. An
incomplete or absent DOCS stage does not block a merge.

### Scope of the fix: substrate repos (reclassified after critique)

A **substrate repo** ships `docs/sdlc/do-merge.md` and persists per-stage markers
via `sdlc-tool` (this repo, and any repo that adopts the addendum). There, the
DOCS stage records `sdlc-tool stage-marker --stage DOCS --status completed`, which
`sdlc-tool stage-query` can read — so the gate can **deterministically** verify
DOCS completion. This plan hardens exactly those router-bypass paths (forked
`/do-sdlc`, raw `gh pr merge`) in substrate repos.

A **no-substrate / foreign repo** (e.g. `cuttlefish`) persists no stage marker at
all — there is no record of whether the DOCS cascade ran, so the gate **cannot
deterministically verify DOCS by construction**. The original motivating incident
(cuttlefish PR #577: a forked `/do-sdlc` returned control with DOCS still running
as a background strand; the supervisor advanced to MERGE; the generic gate found
`OPEN/MERGEABLE/CLEAN/APPROVED` and merged; DOCS was later killed, leaving a route
map stale) occurred on precisely this no-substrate path. This plan does **not**
claim to deterministically prevent that class on the no-substrate path — the
deterministic prevention for it is the now-CLOSED **#1915** fork-strand fix plus
supervisor sequencing. What this plan adds for the no-substrate path is an
**announced non-gate** (an auditable advisory line in the merge log, not a silent
pass). The same forked-`/do-sdlc`/raw-merge bypass occurring **in a substrate
repo** is caught by the new Step 2b check **when the substrate session is still
live at merge time** (Step 2b reads `stages.DOCS == in_progress` and hard-fails);
**when that session has been reaped** (spike-2 notes this is likely on precisely
these bypass paths), Step 2b degrades to today's file-existence check rather than a
deterministic block. So the honest tradeoff being ratified is: a deterministic
gate on the live-session substrate path, and an advisory/file-existence posture
where the session is gone or no substrate exists (the latter being where the
original incident occurred). No incident-frequency data supports one bypass shape
being more common than another; the substrate-repo win is scoped to the live-session
case, not claimed as the dominant path.

**Desired outcome:** in a substrate repo, `/do-merge` treats DOCS-stage
completion as a first-class precondition, analogous to the Step 2 REVIEW-verdict
gate, and **fails closed** when DOCS is affirmatively unfinished (`in_progress`) —
without creating the merge-authorization file. A never-started (`pending`) or
unreadable (session-reaped) DOCS marker degrades to the pre-existing
file-existence check rather than a false block. A legitimate DOCS-skip PR (#1799)
still merges. In a no-substrate repo, the gate emits an explicit advisory that DOCS
cannot be deterministically verified (not a silent pass).

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

## Spike Results

Two code-read spikes resolve the critique's blocker-1 assumptions. A live
end-to-end `/do-docs` run is deferred to the build/test stage (the gate test
exercises the extracted snippet against the real JSON shapes below).

### spike-1: Is `stages.DOCS == "completed"` written by a real `/do-docs` run, or only `status: docs_complete` on the plan file?
- **Assumption (critique):** "`/do-docs` may write `status: docs_complete` to the plan FILE rather than calling the stage marker `stage-query` reads."
- **Method:** code-read (`.claude/skill-context/do-docs.md`, `.claude/skills-global/do-docs/SKILL.md`).
- **Result:** `/do-docs` in this repo writes **both**. The skill-context file's "Stage marker (wraps the whole skill)" section writes `sdlc-tool stage-marker --stage DOCS --status in_progress` at the start and `--status completed` after Step 4 commit. Separately, "Mark the plan docs-complete (Step 4)" writes `status: docs_complete` to plan frontmatter — that write feeds **plan migration at merge**, not the gate. So `stages.DOCS` IS set to `completed` by a real DOCS run and is authoritative **when readable**.
- **Confidence:** high.
- **Impact if false:** would have to gate on the plan-file frontmatter instead — not the case.

### spike-2: When does `sdlc-tool stage-query` return empty `stages`?
- **Assumption (critique):** "`sdlc_stage_query.py` can return `{\"stages\": {}}` when a session is reaped/orphan-cleaned — on the exact bypass paths this plan targets — so a bare `== completed` check fails CLOSED and refuses merges whose DOCS truly completed."
- **Method:** code-read (`tools/sdlc_stage_query.py::query_enriched`).
- **Result:** **Confirmed.** `query_enriched` returns `{"stages": {}, "_meta": _default_meta()}` when `_find_session_by_issue()` returns `None` (lines 488-489) — i.e. the PM/eng session was reaped or orphan-cleaned. On the router-driven path the session is live at merge time so `stages` is populated; on the router-**bypass** paths this plan targets (forked `/do-sdlc`, cross-machine/raw merge) the session may be gone, yielding empty `stages`. A bare `DOCS_STATUS == "completed"` gate would then false-refuse a merge whose DOCS genuinely completed. There is also a *transient* empty window: `_find_session_by_id` retries a bounded 5×200ms because popoto's `rebuild_indexes()` transiently empties the class set (issue #1720).
- **Confidence:** high.
- **Impact if false:** the file-existence fallback (Technical Approach, degraded branch) would be unnecessary — but the code path is confirmed present, so the fallback is required. This retracts the plan's earlier "No race conditions identified" claim (see Race Conditions).

## Data Flow

1. **Entry point:** `/do-merge` invoked with a PR number (via `/sdlc` MERGE dispatch, a forked `/do-sdlc`, or manually).
2. **Repo Context Probe:** the skill reads `docs/sdlc/do-merge.md` (substrate present in this repo).
3. **Step 1-2:** verify PR state + recorded REVIEW verdict via `sdlc-tool`.
4. **Step 2b (NEW):** `sdlc-tool stage-query --issue-number N` → parse `stages.DOCS`.
   - `completed` → PASS.
   - `in_progress` (affirmative "DOCS unfinished" signal — only reachable via a real `start_stage` call) → FAIL closed, route to `/do-docs`.
   - `pending` (DOCS never started — the default status, indistinguishable from a legitimate skip while #1799 is unshipped) OR `""` / empty `stages` (session reaped — can't read the marker, spike-2) → **degraded fallback** to the pre-existing file-existence check `test -f docs/features/{slug}.md`: present ⇒ PASS (degraded, advisory logged); absent ⇒ FAIL.
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
  mapping. Reads `stages.DOCS` from `sdlc-tool stage-query`; PASS on `completed`;
  FAIL closed on `in_progress` (the only affirmative "DOCS unfinished" signal) and
  route back to `/do-docs`; on `pending` (never-started, indistinguishable from a
  skip) OR empty `stages` (session reaped) **degrade to the file-existence check** —
  PASS if `docs/features/{slug}.md` exists, else FAIL.
- **Extended "Documentation Gate"** (`docs/sdlc/do-merge.md`): now checks DOCS
  *stage completion* (via Step 2b) as the authoritative signal, with the existing
  `docs/features/{slug}.md` existence check retained as the degraded fallback for
  the unreadable-marker case (not a separate, weaker check).
- **Generic-path precondition text** (`.claude/skills-global/do-merge/SKILL.md`):
  a minimal Step-2-adjacent note naming "DOCS-stage completion" as a
  substrate-supplied precondition (mirroring the REVIEW-verdict deferral already
  present), plus an explicit **announced non-gate** advisory for the no-substrate
  case. The deterministic gate lives in the substrate addendum, not the global skill.
- **Gate test** (`tests/unit/test_do_merge_docs_gate.py`): extracts the Step 2b
  snippet from `docs/sdlc/do-merge.md` and asserts the decision across
  `completed` (PASS), `in_progress` (hard FAIL), skip-as-`completed`
  (PASS), `pending`-with-doc (PASS degraded), `pending`-without-doc (FAIL),
  empty-stages-with-doc (PASS degraded), and empty-stages-without-doc (FAIL).

### Flow

`/do-merge {PR}` → Step 1 PR-state PASS → Step 2 REVIEW verdict APPROVED →
**Step 2b query `stages.DOCS`** → `completed` ⇒ continue to Step 4 merge;
`in_progress` ⇒ **refuse, no auth file, route to `/do-docs`**;
`pending` (never-started) or `""` (empty stages, session reaped) ⇒ fall back to
`docs/features/{slug}.md` existence: present ⇒ continue; absent ⇒ **refuse, no auth file**.

### Technical Approach

**Step 2b extraction + decision (correct for this repo's output shape):**

`2>/dev/null` is deliberately **omitted** — a `sdlc-tool` stderr diagnostic
(substrate fault, bad issue number) must surface in the merge log rather than be
swallowed; only stdout is parsed for the status.

```bash
SLUG=$(git rev-parse --abbrev-ref HEAD 2>/dev/null | sed 's|^session/||')
DOCS_STATUS=$(sdlc-tool stage-query --issue-number {issue_number} \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('stages',{}).get('DOCS',''))")
case "$DOCS_STATUS" in
  completed)
    echo "DOCS_GATE: PASS — DOCS stage completed" ;;
  in_progress)
    # Affirmative "DOCS unfinished" signal — only reachable via an actual
    # start_stage call, so a genuinely started-but-stalled DOCS stage (the
    # cuttlefish #577 incident shape). Fail closed.
    echo "DOCS_GATE: FAIL — DOCS stage is 'in_progress', not completed"
    echo "GATES_FAILED" ;;   # route back to /do-docs; do NOT create the auth file
  *)
    # pending (DOCS never started — the DEFAULT status for a stage with no marker,
    # e.g. a docs-free trivial PR before #1799's skip-as-completed ships) OR empty
    # stages (session reaped/orphan-cleaned, spike-2, so the marker is unreadable).
    # In NEITHER case can we AFFIRM 'unfinished': a never-started DOCS is
    # indistinguishable from a legitimate skip, and a reaped session hides a
    # possibly-completed run. Degrade to the pre-existing file-existence
    # Documentation Gate rather than false-refuse a merge whose DOCS truly
    # completed or was legitimately skipped.
    if [ -n "$SLUG" ] && [ -f "docs/features/${SLUG}.md" ]; then
      echo "DOCS_GATE: PASS (degraded) — DOCS marker not authoritative (status='${DOCS_STATUS:-<empty>}'); docs/features/${SLUG}.md present"
    else
      echo "DOCS_GATE: FAIL — DOCS marker not authoritative (status='${DOCS_STATUS:-<empty>}') AND docs/features/${SLUG:-<no-slug>}.md absent"
      echo "GATES_FAILED"
    fi ;;
esac
```

- **Why `.get('stages',{}).get('DOCS','')` and not the issue's sketch:** the
  enriched query returns bare status strings under `stages`, not
  `{"DOCS": {"status": ...}}`. Verified live and in `query_enriched` (spike-2).
- **Why no explicit skip-branch (resolves open question #2):**
  `sdlc-tool stage-marker` can only write `in_progress` or `completed`
  (`_VALID_STATUSES`, `tools/sdlc_stage_marker.py:78`). #1799's DOCS-skip records
  DOCS as `completed` (the "skipped" nuance lives in the router `reason` string,
  not the status). So a single `== completed` check admits both a real completion
  and a legitimate skip. The gate never needs to distinguish them.
- **Why `pending` and empty-`stages` degrade instead of failing closed (resolves blocker 1):**
  `query_enriched` returns `{"stages": {}}` whenever the session is gone
  (spike-2), and `pending` is the DEFAULT status for a DOCS stage that was never
  started (confirmed live: an unrun DOCS reads `pending`) — indistinguishable from
  a legitimate skip, especially since #1799's skip-as-`completed` is UNSHIPPED. Both
  are precisely the router-bypass paths this plan hardens. A blanket fail-closed on
  either would REFUSE merges whose DOCS genuinely completed or was legitimately
  skipped — the regression Risk 2 forbids. Instead both cases degrade to the
  **status-quo Documentation Gate** (`test -f docs/features/{slug}.md`). This makes
  Step 2b a strict, monotonic improvement: when the marker reads `completed` it is
  authoritative PASS; when it reads `in_progress` it can affirmatively *block*
  (the only affirmative-unfinished signal, reachable only via a real `start_stage`
  call); in every other case behavior is exactly today's file-existence gate. `in_progress`
  is therefore the ONLY hard fail.
  - **Residual (accepted):** a docs-free trivial PR whose DOCS is `pending`/empty
    AND whose feature doc is absent (double degradation) falls to FAIL. This is
    the same fail-closed posture the status-quo gate takes when a plan-declared doc
    is missing; it is rare and recoverable (re-run `/do-docs` or re-authorize).
    Chosen over admitting an unverifiable merge.

**Generic/no-substrate path (resolves open question #1; aligned with the blocker-2 reclassification):**
- Per the Problem reclassification, the no-substrate path is **out of deterministic
  reach by construction** — no marker exists to read. Its deterministic prevention
  is the now-CLOSED #1915 fork-strand fix plus supervisor sequencing.
- The global `SKILL.md` edit is intentionally **minimal and parallel to an existing
  convention.** The global skill already defers the REVIEW verdict to the substrate
  ("a repo addendum may supply a recorded-verdict substrate; approval that cannot be
  confirmed FAILS closed"). Step 2b's global-skill text mirrors that exact shape for
  DOCS: one sentence naming DOCS-stage completion as a substrate-supplied
  precondition, plus one advisory line for the no-substrate case —
  `"DOCS-completion gate: NOT ENFORCED — no substrate; DOCS completion cannot be verified here, merge relies on supervisor sequencing (see #1915)."`
  This is an *announced* non-gate, not a silent pass, satisfying the acceptance
  criterion.
- **Proportionality of the fleet-wide edit (addresses the critique's minor concern):**
  the edit propagates to every machine via `sync_claude_dirs`, but it is (a) two
  lines, (b) structurally identical to the REVIEW-verdict deferral text already in
  the global skill, and (c) the only place a foreign-repo agent actually reads the
  merge procedure — so the advisory is seen where it matters. It introduces no new
  fleet-wide machinery; it extends an existing deferral pattern. That is
  proportionate to documenting a determinism limit that the acceptance criteria
  explicitly require be surfaced.
- **Justification for rejecting (a) and (b):**
  - (a) require an explicit `docs-verified` signal from the caller — pushes a new
    required argument into a *portable* skill; a caller that forgets it either
    hard-blocks every foreign-repo merge or defaults unsafe. Extra coupling for a
    signal no foreign repo currently produces.
  - (b) classify PR shape and auto-pass trivial shapes — depends on
    `scripts/pr_shape_classify.py`, which is *this repo's* tool and does not exist
    in foreign repos. It cannot be the generic answer.
  - Chosen: honest determinism limit + announced advisory for no-substrate, with
    the real deterministic gate scoped to substrate repos (where it has teeth).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception: pass` blocks — the change is shell-in-markdown plus a test. State "No exception handlers in scope" for the gate snippet; the test itself asserts observable decision strings.

### Empty/Invalid Input Handling
- [ ] `DOCS_STATUS == in_progress` → gate FAILS closed (the only affirmative "DOCS unfinished" signal). Test feeds `{"stages": {"DOCS": "in_progress"}}` and asserts `GATES_FAILED`.
- [ ] `DOCS_STATUS == pending` (never-started, indistinguishable from a skip) **with** `docs/features/{slug}.md` present → gate PASSES degraded. Test feeds `{"stages": {"DOCS": "pending"}}` on a branch whose slug's feature doc exists, asserts `PASS (degraded)` and NO `GATES_FAILED`. **without** the feature doc → gate FAILS (`GATES_FAILED`).
- [ ] `DOCS_STATUS == ""` (empty `stages`, session reaped) **with** `docs/features/{slug}.md` present → gate PASSES degraded. Test feeds `{"stages": {}}` on a branch whose slug's feature doc exists, asserts `PASS (degraded)` and NO `GATES_FAILED`.
- [ ] `DOCS_STATUS == ""` **without** the feature doc → gate FAILS. Test feeds `{"stages": {}}` with no matching feature doc, asserts `GATES_FAILED`.
- [ ] The malformed-JSON case is **intentionally not tested:** `sdlc-tool stage-query` is contractually guaranteed to emit valid JSON — every error path in `sdlc_stage_query.py::main` prints `{"stages": {}, "_meta": {...}}` (never non-JSON). A malformed-stdin test would exercise an input the substrate cannot produce; and even if it could, an empty `DOCS_STATUS` now routes to the file-existence fallback, not a distinct branch. State "no reachable malformed-JSON path" rather than testing a synthetic one.

### Error State Rendering
- [ ] The hard-fail path (`in_progress`) prints `DOCS_GATE: FAIL — DOCS stage is 'in_progress', not completed` and `GATES_FAILED`; the degraded-fail path prints `DOCS_GATE: FAIL — DOCS marker not authoritative (status='<status>') AND docs/features/<slug>.md absent`. The merge report surfaces the specific blocker. Test asserts the FAIL message names the observed condition.

## Test Impact

- [ ] `tests/unit/test_do_merge_docs_gate.py` — CREATE: new test file mirroring `test_do_merge_review_filter.py`; extracts the Step 2b snippet from `docs/sdlc/do-merge.md` and asserts the decision across `completed` (PASS), `in_progress` (hard FAIL), skip-as-`completed` (PASS), `pending`-with-feature-doc (PASS degraded), `pending`-without-feature-doc (FAIL), empty-stages-with-feature-doc (PASS degraded), and empty-stages-without-feature-doc (FAIL). The `pending`/empty-stages cases set up a temp branch/slug + `docs/features/{slug}.md` fixture so the `test -f` fallback is exercised.
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

**One timing hazard, mitigated** (retracts the plan's earlier "no race conditions"
claim, which the critique correctly flagged as false). The gate itself is a
synchronous, single-process sequence of `sdlc-tool` / `gh` reads with no shared
mutable state. But the value it reads — `stages.DOCS` — can transiently read
**empty** even for a live session:

- **Transient empty read (issue #1720):** popoto's `rebuild_indexes()` briefly
  empties the class set `$Class:AgentSession`; a concurrent
  `query.filter(session_id=...)` returns empty during that window (measured p99
  ≈ 651ms). `sdlc_stage_query.py` already mitigates this with a bounded
  5×200ms = 1000ms retry (`_CLASS_SET_RETRY_ATTEMPTS`) in `_find_session_by_id`,
  covering the measured window before returning `None`.
- **Persistent empty read:** a genuinely reaped/orphan-cleaned session returns
  `{"stages": {}}` for good (spike-2).

The gate cannot distinguish a transient empty read from a persistent one, so it
does not fail closed on empty. Instead, an empty read degrades to the
file-existence fallback (Technical Approach). This means: (a) the stage-query
retry usually resolves the transient case before the gate ever sees empty; and
(b) if it doesn't, the fallback still admits a truly-completed merge (its feature
doc is on disk) rather than false-refusing on a millisecond-scale index rebuild.
The settled, populated-`stages` path (the common router-driven case, where row 9
already withheld MERGE until DOCS `completed`) is unaffected.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1799] Router-level DOCS-skip implementation — the gate must *respect* a skip (which resolves to `completed`), but shipping the router skip itself is #1799's work.
- [SEPARATE-SLUG #1915] Fork background-strand root-cause fix — already CLOSED; this gate is defense-in-depth, not that fix.
- Router changes to `agent/sdlc_router.py` (row 9 / G6) — the router already sequences DOCS-before-MERGE; this plan hardens only the gate. (Anti-criterion below asserts no router edit.)
- Adding a daily-reflection backstop for merges that bypass `/do-merge` entirely (issue's open question #3) — no existing reflection covers DOCS-stage completion (the `merged-branch-cleanup` reflection backstops the *separate* plan-file-migration invariant, not DOCS). A DOCS-completion reflection is genuinely new, separable work, not required to close this issue.

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

- [ ] `/do-merge` fails closed (prints `GATES_FAILED`, creates no `data/merge_authorized_{PR}` file) when `stages.DOCS` is `in_progress` (the only affirmative "DOCS unfinished" signal).
- [ ] On `pending` (never-started, indistinguishable from a legitimate skip) OR empty `stages` (session reaped), the gate degrades to `docs/features/{slug}.md` existence: PASS when the doc is present, FAIL when absent — it does NOT blanket-refuse a truly-completed-or-skipped merge.
- [ ] A DOCS-skip PR whose DOCS status is `completed` still passes the gate (test asserts PASS).
- [ ] The "Documentation Gate" section verifies DOCS *stage completion* as authoritative, with file existence retained as the degraded fallback.
- [ ] The generic/no-substrate path emits an explicit announced non-gate advisory line (not a silent pass); documented in the global SKILL.md and scoped as out of deterministic reach.
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
- In `docs/sdlc/do-merge.md`, add a "Step 2b: Verify DOCS Stage Completed" mapping in the Stage/Verdict Substrate section, immediately after the Step 2 REVIEW-verdict bullet, using the exact extraction + `case` decision from Technical Approach: PASS on `completed`; hard FAIL on `in_progress` only; `pending`/empty-stages ⇒ file-existence fallback (`docs/features/${SLUG}.md`). Do NOT re-add `2>/dev/null` to the `sdlc-tool stage-query` call.
- Extend the "Documentation Gate" section so it states the authoritative check is DOCS *stage completion* (Step 2b), with `docs/features/{slug}.md` existence retained as the degraded fallback for the unreadable-marker case.

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
- Create `tests/unit/test_do_merge_docs_gate.py` mirroring `test_do_merge_review_filter.py`: extract the Step 2b snippet from `docs/sdlc/do-merge.md`, run against synthetic `{"stages": {"DOCS": ...}}` JSON. Assert: PASS for `completed`, PASS for skip-as-`completed`, hard FAIL (`GATES_FAILED`) for `in_progress` only, PASS (degraded) for `pending`-with-feature-doc-fixture, FAIL for `pending`-without-doc, PASS (degraded) for empty-stages-with-feature-doc-fixture, FAIL for empty-stages-without-doc. Do NOT add a malformed-JSON case (unreachable — see Failure Path Test Strategy).

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

<!-- Populated by /do-plan-critique (war room). Re-critique of revised plan; verdict NEEDS REVISION (1 blocker). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Step 2b `case` groups bare `pending` with `in_progress` and hard-fails it, but `pending` is the default unrun status (indistinguishable from stalled). On a live-session trivial docs-free PR reaching the gate via a bypass path (target scope), DOCS reads `pending` because #1799 (skip→`completed`) is unshipped, so the gate hard-refuses a legitimate merge — contradicting Risk 2's own "regression the acceptance criteria forbid." | Technical Approach (line 207), Data Flow step 4, Failure Path Test Strategy, Test Impact | Split `in_progress\|pending)` into two branches: `in_progress)` keeps the hard `GATES_FAILED` (only reachable via an actual `start_stage` call = genuine stalled signal); `pending)` falls through to the same file-existence fallback as the `*)` empty-stages branch. Update `test_do_merge_docs_gate.py` so the `pending` fixture asserts degraded PASS/FAIL by feature-doc presence, not unconditional hard FAIL; adjust the `in_progress\|pending` grouping wording in Failure Path Test Strategy and Test Impact. |
| CONCERN | Scope & Value + History & Consistency | The Problem "Scope of the fix" subsection overstates the substrate-repo win in two ways that bias the Open Question 1 ratification: (a) the unsupported frequency claim "the far more common case, since this repo is a substrate repo" (no incident/volume data), and (b) the determinism claim that the bypass path "*is* now caught by the new Step 2b check," which spike-2 undercuts because the session is likely reaped on exactly those bypass paths, degrading to the file-existence check the Problem elsewhere calls insufficient. | Problem > "Scope of the fix: substrate repos" | Pure prose edit, no code/test/task change. (a) Replace "the far more common case, since this repo is a substrate repo" with an explicitly-flagged, unmeasured assumption or drop the frequency claim. (b) Qualify "is now caught" to "is now caught when the substrate session is still live; when it has been reaped the gate degrades to today's file-existence check, per spike-2." No other artifact depends on this wording. |

---

## Open Questions

1. **Ratify the reclassified scope + generic-path decision.** After critique, the
   deterministic DOCS gate is scoped to **substrate repos** (where a stage marker
   exists to read), which is where this repo's real bypass paths live; the
   no-substrate path emits an *announced non-gate* advisory rather than pretending
   to gate DOCS deterministically (that path's real fix is the now-CLOSED #1915 +
   supervisor sequencing). Confirm this reclassification is acceptable — i.e. that
   dropping the deterministic claim for the no-substrate cuttlefish path (in favor
   of substrate-repo hardening + an honest advisory) is the right call, versus
   option (a) requiring a caller-supplied `docs-verified` signal. (Option (b) is
   infeasible generically — it needs this repo's `pr_shape_classify.py`.)
2. **Issue open-question #3 (daily-reflection backstop).** The plan scopes this
   OUT. Note the category distinction (a critique correction): the existing
   `merged-branch-cleanup` reflection backstops a *different* invariant — plan-file
   migration into `docs/plans/completed/` — not DOCS-stage completion. **No existing
   reflection covers DOCS completion on bypass paths.** A DOCS-completion reflection
   would therefore be genuinely new work, not an extension of the migration
   backstop. It is separable from this gate (which already closes the substrate-repo
   bypass paths deterministically) and, if wanted, should be filed as its own issue.
   Confirm OUT-of-scope, or file the separate issue.
