---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-14
tracking: https://github.com/tomcounsell/ai/issues/2757
last_comment_id:
---

# G8 must not rebuild shipped work: an absent PR number is unverifiable, not falsified

## Problem

After a PR merges and its issue closes, `sdlc-tool next-skill --issue-number N`
tells the pipeline to build the work it just shipped:

```
stage-artifact-verify: issue #N BUILD claims completed but PR None is not open or merged (state=None)
{"skill": "/do-build", "reason": "G8: BUILD claims completed but its artifact failed live
 verification — re-dispatching /do-build rather than advancing", "row_id": "G8", "dispatched": true}
```

Observed on 2026-08-13 on all three issues merged by that night's PR fan-out —
#2638 (PR #2686), #2566 (PR #2665), #2640 (PR #2671) — identical output for each,
minutes after their merges.

**Current behavior:**

`tools/sdlc_next_skill.py::_verify_stage_artifacts_live` (`:251-278`) reads:

```python
pr_number = meta.get("pr_number")
...
pr_state: str | None = None
if pr_number and (build_claimed or patch_claimed):
    pr_state = _fetch_pr_state(pr_number, repo=repo)

if build_claimed:
    if not pr_number or pr_state not in ("OPEN", "MERGED"):
        ... return {"stage_artifacts_verified": False, "unverified_stage": "BUILD"}
```

The issue title says the verifier "resolves PR None once the PR is closed." It does
not. The live read is itself guarded by `if pr_number` (`:269`), so when `pr_number`
is absent **`_fetch_pr_state` is never called**. The `state=None` in the log line is
the initialized default at `:268`, not an answer from GitHub. The single failing
term is `not pr_number` (`:273`): the verifier treats *nothing to look up* as
*the lookup came back empty*, and reports a fabricated BUILD claim.

The merged-PR path added by #1267 is already correct — `pr_state not in ("OPEN",
"MERGED")` passes a MERGED PR. The hole is one level up, and it is compounded by a
second omission: **the verifier never consults the MERGE marker**, which is sitting
in the `stage_states` dict it was handed. A pipeline that has demonstrably finished
gets its BUILD claim adjudicated as if it were mid-flight.

`_fetch_pr_state`'s own docstring already states the principle this violates
(`:100-118`): *"callers must treat `None` as 'could not determine', never as
evidence of a false claim."* The `not pr_number` term breaks that contract before
the function is even reached. The sibling PATCH check three lines below gets it
right — `patch_claimed = stage_states.get("PATCH") == "completed" and
bool(lane_branch)` (`:257`), with a debug log explaining that a missing artifact
identifier means *skip the probe*, not *fail the claim* (`:258-263`, added by
#2718). BUILD is the same shape missing the same guard.

**Why `pr_number` is absent in the first place — the proximate cause, one layer up.**

The paragraphs above are a correct description of the verifier, but they stop one
layer short of the cause, and the critique's war room was right that this changes
which fix is correct.

`_meta["pr_number"]` is resolved by `tools/sdlc_stage_query.py::_compute_meta`, which
calls `_lookup_pr(issue_number, slug=slug, repo=resolved_repo)` (`:548`) — without a
`state` argument. `_lookup_pr`'s signature defaults to **`state: str = "open"`**
(`:338-342`). A merged PR is not open, so it is invisible to every resolution path in
that function. That is precisely why the symptom appears "minutes after their merges"
and not before: the merge is the event that makes the PR unresolvable.

Measured on the three reported issues (2026-08-16, live `gh`):

| Issue | `_lookup_pr(state="open")` | `_lookup_pr(state="all")` |
|---|---|---|
| #2638 | `None` | **2686** |
| #2566 | `None` | **2665** |
| #2640 | `None` | **2671** |

Those are exactly the PR numbers this plan's own Problem statement names. The
`pr_number` was never unrecoverable; it was looked up under a filter that excludes
the only state it could be in.

`tools/sdlc_stage_marker.py:246-250` already fixed this same defect at a sibling call
site under **#2539**, with a comment that says the quiet part directly: *"the artifact
question is historical, not in-flight. Under the default `open` a merged PR resolves
to None and this probe returned False before ever looking for the artifact."* The
verifier's call site never got the same treatment.

**Why silencing G8 is not sufficient — the rebuild relocates to row 5.**

Making G8 no-op does not stop the rebuild. `agent/sdlc_router.py::_rule_branch_exists_no_pr`
(`:922-927`) returns `True` on `context["branch_exists"] is True` alone whenever
`pr_number` is falsy, regardless of `BUILD == "completed"`, and row 5 precedes every
PR-gated row. Measured through the real router:

| `pr_number` | `branch_exists` | dispatch |
|---|---|---|
| absent | `True` | **`/do-build`, row 5** |
| absent | `False` | `Blocked(NO_RULE)` |
| **2686** | `True` | **`/do-merge`, row 10** |
| **2686** | `False` | **`/do-merge`, row 10** |

This repo reports `deleteBranchOnMerge: false` and merged lane branches persist on
origin, so the `branch_exists: True` row is the **normal** post-merge case here, not
an exotic one. An earlier draft of this plan asserted only the absence of a *G8*
`/do-build` — an assertion that would have gone green while the bug continued
unabated at row 5.

The same table shows the converse: restoring `pr_number` reaches the terminal row
under **both** branch states, which simultaneously stops the row-5 rebuild, lets G8
pass on its own merits rather than by being silenced, and deletes the
`Blocked(NO_RULE)` residual that motivated #2817.

**Desired outcome:**

A terminal pipeline is never told to rebuild — **at any row**, not merely at G8. This
is achieved primarily by *resolving* the artifact identifier rather than by
suppressing the check that misses it. Secondarily, the artifact verifier
distinguishes three states rather than two —

| State | Meaning | Verdict |
|---|---|---|
| verified | the artifact identifier resolves and the world confirms it | no-op |
| falsified | the identifier resolves and the world contradicts it | `stage_artifacts_verified: False` |
| **unverifiable** | there is no identifier to resolve | **no-op, with a debug log** |

Today the third collapses into the second. After this change it collapses into the
first, matching `_fetch_pr_state`'s stated contract and the general rule that a gate
must not manufacture evidence it never gathered.

Two honest caveats on that secondary change, which an earlier draft glossed:

- **It is a real reduction in coverage, not a free win.** #2718's PATCH guard skipped
  a *guessed* identifier, so skipping lost nothing. An absent `pr_number` is
  different: a lane that genuinely never opened a PR stops tripping G8. Risk 2 states
  this plainly and names the surviving owners. The PATCH↔BUILD analogy is a guide to
  the *code shape*, not a claim that the two cases are equivalent in what they give
  up.
- **After the primary fix it is rarely reached.** Once `pr_number` resolves for
  merged PRs, the unverifiable branch covers only a genuinely PR-less lane or a
  degraded ledger. It ships as defense in depth against the residual, not as the
  mechanism that fixes #2757.

## Freshness Check

**Baseline commit:** `1c48b97f2` (`main`, clean except an unrelated in-flight plan
edit by a peer lane; `main` moved twice during plan authoring — the referenced
line numbers were re-read at this SHA)
**Issue filed at:** 2026-08-13T05:22:10Z
**Disposition:** Minor drift

**File:line references re-verified:**

- `tools/sdlc_next_skill.py:251-278` — the BUILD/PATCH artifact block the issue
  describes — **holds verbatim**, including the `if pr_number and (build_claimed or
  patch_claimed)` guard at `:269` that the issue's root-cause paragraph does not
  account for.
- `tools/sdlc_next_skill.py:100-127` — `_fetch_pr_state` and its
  "never as evidence of a false claim" contract — **holds**.
- `tools/sdlc_next_skill.py:257-263` — the PATCH check's `bool(lane_branch)` guard
  and its no-guessing debug log — **holds**; this is the in-file precedent the fix
  copies.
- `agent/sdlc_router.py:739-741` — `guard_g6_terminal_merge_ready` returns `None`
  when `pr_number` is absent — **holds**, and is what makes guard reordering a
  non-fix (see Spike Results).
- `agent/sdlc_router.py:771-780` — `GUARDS` order `[G1, G2, G3, G4, G8, G7, G5, G6]`
  — **holds**.
- `tools/_sdlc_run_identity.py:114-132` — `_pipeline_is_terminal` is exactly
  `states.get("MERGE") == "completed"`, fail-open to `False` — **holds**.

**Cited sibling issues/PRs re-checked:**

- **#2735** — the issue names this as the same G8-verifier family. Closed; shipped
  as PR **#2792** (`e50eba258`), which introduced `tools/lane_identity.py` and
  reshaped the PLAN/PATCH halves of this exact function. Distinct trigger, shared
  function — its line numbers, not its logic, are the drift source.
- **#1267** — the issue that built this gate, including the merged-PR fix the issue
  body assumes is missing. Closed and present.
- **#2062 / PR #2790** (`706fc4da0`, "Honest finalize refusals, a clean verdict
  field, ... a router row that owns the stale-verdict state") — merged 2026-08-13,
  **after** #2757 was filed and touching `agent/sdlc_router.py`. It did not change
  the verifier, but it is the reason the adjacent test is red (see Spike Results,
  spike-5).

**Foreign-PR reconciliation: PR #2794 / issue #2793 (checked 2026-08-16).**

A closed-unmerged PR edited the same function this plan edits, so its disposition was
reconciled before any code was written.

- **#2793 is closed `NOT_PLANNED`** (2026-08-13T13:14:04Z, by the repo owner):
  "Duplicate of #2735 + #2718." Both mechanisms it reported were already tracked and
  were closed by `docs/plans/sdlc-lane-recorded-slug.md`.
- **PR #2794 is closed unmerged** (2026-08-14T03:45:20Z), superseded. It was also
  measured to regress the suite (33 failed / 77 passed) because it removed
  `find_plan_path` from `tools/_sdlc_utils.py` rather than extending it.
- **What actually landed instead: PR #2792 (`e50eba258`).** Verified on main today:
  `find_plan_path` now lives at `tools/lane_identity.py:123` with a single-rung
  `tracking:`-frontmatter contract, and the bare-`#N` fallback was **deleted as a
  behavior**, not made suppressible. #2794's proposed `tracking_only=True` kwarg has
  nothing left to suppress. The `session/{slug}` reconstruction is gated on a
  *recorded* lane slug, so an unrecorded lane no-ops instead of probing a guess.
- **Consequence for this plan's scope:** nothing in #2794 is live work, and no part of
  it needs re-doing here. Its one durable observation — that
  `test_terminal_merged_pipeline_routes_to_merge_not_build` is red on baseline `main`
  at row 8e — is independently reproduced by this plan's spike-5 and is picked up
  inside this fence. The `headRefName` enhancement #2794 also proposed is **not**
  adopted: it is an enhancement on top of a shipped fix, and this plan's revised
  approach removes the need for it (an absent `pr_number` is now *resolved*, not
  worked around).

**Commits on main since issue was filed (touching referenced files):**

- `e50eba258` (#2735 → PR #2792) — **irrelevant to the root cause**; reshaped the
  PLAN/PATCH artifact resolution in the same function, left the BUILD branch alone.
- `706fc4da0` (#2740/#2767/#2769/#2790) — **partially explains an adjacent
  symptom**: it added the `head_sha` verdict field and row 8f. It is why the
  integration test named in this plan fails at row 8e rather than at G8.
- `ec585ceb9` (#2803, merged 2026-08-14) — did not touch the verifier, but see
  Risk 1: the three cited issues' ledgers now read entirely empty.

**Active plans in `docs/plans/` overlapping this area:**

- `next-skill-self-lock-run-identity.md` (#2766, `status: Ready`) — same **file**,
  different function (the issue-lock peek block, `:513-549`). **Explicitly outside
  this plan's fence**; do not touch that block.
- `router-docs-skip-trivial.md` (#1799, `status: Ready`) — extends
  `_build_context` in the same file. Adjacent, non-conflicting: this plan changes
  `_verify_stage_artifacts_live`, which `_build_context` calls, not `_build_context`
  itself.
- `pipeline-graph-single-source-of-truth.md` (#2491, `status: Planning`, Large) —
  would eventually restructure the router/next-skill boundary wholesale. Not a
  blocker; this fix is a ten-line change inside a function that plan would inherit.
- `ledger-integrity.md` (#2730, `status: Ready`) — the ledger-durability lane. Its
  subject matter is the *cause* of the empty ledgers this plan must tolerate, not
  the fix for this bug. No file overlap.

**Notes:** No drift changed the premise. The one substantive correction is to the
issue's own root-cause paragraph — recorded in the Recon Summary now on the issue,
and load-bearing enough that it changes which fix is correct (see Key Elements).

## Prior Art

- **#1267 (the gate itself)** — introduced `_verify_stage_artifacts_live`, guard G8,
  and the three-artifact table. **Succeeded**, and later self-corrected: its own
  docstring (`:199-209`) records a "merged-pipeline misfire" where an already-MERGED
  PR read as an unverified BUILD artifact and re-dispatched `/do-build` forever.
  **#2757 is the same failure mode reached through the other door** — not a PR whose
  state reads wrong, but a PR number that is not there to read. The fix belongs in
  the same paragraph of the same docstring.
- **#2718 → the PATCH `bool(lane_branch)` guard (`:254-263`)** — the closest prior
  fix, and a direct template. A PATCH claim with no recorded lane slug used to probe
  a *guessed* branch name, force-dispatching `/do-patch` against a clean worktree
  until G4's oscillation cap hard-blocked the lane. The fix was to no-op the check
  when the artifact identifier is absent, and log at debug why. This plan applies
  that exact shape to BUILD. **Succeeded.**
- **#2078 → the `_target_repo_cwd()` threading (`:71-83`)** — a third instance of
  the same family: the verifier inspected the wrong repo, a genuinely-committed plan
  read as unverified, and G8 re-dispatched `/do-plan` forever. **Succeeded.** The
  pattern across all three is worth naming because it is the real subject of this
  plan: *every G8 regression to date has been the verifier reporting "falsified" for
  a check it was not actually in a position to perform.*
- **#2735 → PR #2792** — reshaped PLAN/PATCH resolution in this function; supplies
  `find_plan_path` / `resolve_lane_slug` / `lane_branch_name`. Not a fix for this
  bug; it is the reason the plan's line numbers differ from the issue's.
- **PR #2033 (#2029) "dispatch precedence, convergence latch, outcome-verified
  advance"** and **PR #2076 (umbrella #2026)** — the two merged PRs a prior-art
  search surfaces for "artifact verification G8". Neither touched the BUILD branch's
  `not pr_number` term.

No closed issue matches "G8 rebuilds merged work with no pr_number" — #2757 is the
first report of this specific trigger.

## Research

Purely internal. No external library, API, or ecosystem pattern is involved: the
change is a predicate correction inside one private function, plus a fixture repair
in two test files. `gh` and `git` usage is unchanged — in fact the fix *removes* a
call path rather than adding one.

No relevant external findings — proceeding with codebase context and training data.

## Spike Results

All spikes were run read-only against the live system on 2026-08-14 at `1c48b97f2`.
Router spikes call `agent.sdlc_router.decide_next_dispatch` directly (a pure
function over three dicts — no Redis, no `gh`).

> **Spike status after the 2026-08-14 war room and the 2026-08-16 re-measurement.**
> spike-1 is **partially invalidated** (it built its context by hand and so never saw
> `branch_exists`); spike-3 and spike-4 are **superseded** by spike-8/9/10, which
> locate the cause one layer up. They are retained below with correction notes
> because their measurements are still true of the code they probed — the error was
> in scope, not in arithmetic. spike-2, spike-5, spike-6 and spike-7 stand unamended.

### spike-1: After the verifier is fixed, where does a terminal no-`pr_number` pipeline actually route?
- **Assumption**: "Stopping G8 from firing lets the terminal rows (G6 / row 10) own the outcome."
- **Method**: prototype (direct `decide_next_dispatch` calls)
- **CORRECTION (2026-08-16)**: this spike passed a **hand-built context** to
  `decide_next_dispatch`, bypassing `_build_context` — the only producer of
  `branch_exists`. With that key absent, row 5 cannot fire, and the spike concluded
  `Blocked(NO_RULE)` was the only residual. Supplying `branch_exists: True` (the
  normal case in this repo) routes to `/do-build` at **row 5**. See spike-8. The
  conclusion drawn from this spike — "the deliverable is precisely to stop the false
  rebuild" — survives; the belief that stopping G8 *achieves* that does not.
- **Finding (as measured, context hand-built)**: With
  the G8 flags unset and `pr_number` absent:
  - all stages completed including `MERGE` and `PATCH`, no verdict →
    `Blocked(reason='no matching dispatch rule', guard_id='NO_RULE')`
  - same, plus an APPROVED verdict → still `Blocked(NO_RULE)`
  Every terminal route is gated on `pr_number`: `_rule_ready_to_merge` returns
  `False` without it (`agent/sdlc_router.py:1470-1471`) and
  `guard_g6_terminal_merge_ready` returns `None` without it (`:739-741`). A terminal
  pipeline that lost its `pr_number` has **no owner in the dispatch table at all**.
- **Confidence**: high
- **Impact on plan (superseded)**: this originally justified asserting only the
  absence of a **G8** `/do-build`. That assertion is now known to be satisfiable
  while the bug persists, and is replaced throughout by `result.get("skill") !=
  "/do-build"` at **any** row, with an explicit `branch_exists: True` variant.
  `Blocked(NO_RULE)` is also **not** the benign no-op this spike assumed — the router
  documents it as a genuine hole in the table and the SDLC skill surfaces it and
  waits, i.e. it is a human-escalation wedge. Under the primary fix the residual
  disappears entirely and #2817 becomes moot (spike-10).

### spike-2: Would reordering `GUARDS` (G6 before G8) fix the reported case?
- **Assumption**: "Letting the terminal verdict preempt the artifact check is the invasive-but-viable alternative."
- **Method**: code-read + the spike-1 calls
- **Finding**: **It cannot fix this bug at all.** `guard_g6_terminal_merge_ready`
  short-circuits to `None` when `pr_number` is falsy — which is the reported shape.
  Reordering would change behavior only for pipelines that *have* a `pr_number`,
  i.e. exactly the pipelines that do not reproduce #2757. It would also weaken the
  #1267 positioning contract (G8 sits after G4 so the oscillation cap bounds a
  persistently-false claim) for every lane, in exchange for nothing.
- **Confidence**: high
- **Impact on plan**: guard reordering is rejected on evidence, not on taste. It is
  a No-Go with a stated reason, and `GUARDS` is untouched — which is also what keeps
  the fix inside its fence.

### spike-3: Does a MERGE-marker short-circuit alone fix the reported case?
- **Assumption**: "`MERGE == completed` → skip verification" is the whole fix.
- **Method**: code-read against the live ledger state of the three cited issues
- **Finding**: **It fixes the reported case and misses the dangerous one.** The
  three cited issues (#2638, #2566, #2640) read *entirely* empty today — `stages:
  {}`, `pr_number: null`, `slug: null`. The ledger that drops `pr_number` is the
  same ledger that drops `MERGE`; there is no mechanism making one field more
  durable than the other. In the partial-loss shape — `BUILD == completed` survives,
  `MERGE` and `pr_number` are gone — a MERGE-marker check sees nothing terminal, the
  `not pr_number` term still fires, and G8 still re-dispatches `/do-build` on
  shipped work. Measured routing for that shape with the G8 flags unset:
  `{ISSUE, PLAN, CRITIQUE, BUILD} completed`, no `pr_number` → `Blocked(NO_RULE)`;
  `{BUILD}` alone → `Dispatch(/do-plan, row 1)`. Neither rebuilds.
- **Confidence**: high
- **Impact on plan**: the `not pr_number` correction is the **load-bearing** fix and
  the MERGE short-circuit is the **outer belt**, not the reverse. The plan is
  written in that order and the Success Criteria test the partial-loss shape
  explicitly, because it is the shape a MERGE-marker check cannot see.

### spike-4: Is the MERGE short-circuit redundant once the `not pr_number` term is fixed?
- **Assumption**: "Fixing BUILD is sufficient; a terminal short-circuit adds nothing."
- **Method**: code-read + live probes (`_check_branch_pushed`, `gh repo view --json deleteBranchOnMerge`)
- **Finding**: **Not redundant — it covers a hole in the sibling check.** With
  `pr_number` absent, `pr_state` stays `None`, so the PATCH check's merged-skip
  (`pr_state != "MERGED"`, `:281`) **cannot engage**, and the entire PATCH verdict
  rests on `git ls-remote` still finding the lane branch. This repo reports
  `deleteBranchOnMerge: false`, and `session/sdlc-2755` is still on origin after
  merging — so the hole is dormant *here*. But `SDLC_TARGET_REPO` can point at a
  repo with branch deletion on, and #1267's docstring designed that skip *for*
  exactly that policy. Without the short-circuit, the BUILD fix would convert #2757
  from a `/do-build` re-dispatch into a `/do-patch` re-dispatch on the same shipped
  work, in any such repo, whenever a lane slug is recorded.
- **Confidence**: high for the mechanism; high for the local `deleteBranchOnMerge`
  reading; the "other repo" case is structural, not observed.
- **Impact on plan**: both changes ship. The short-circuit is placed at the **top of
  the function**, above all three checks, so it covers PLAN and PATCH as well as
  BUILD rather than being bolted onto one branch.

### spike-5: Why is `test_terminal_merged_pipeline_routes_to_merge_not_build` red, and does this plan's fix turn it green?
- **Assumption**: "The test defends #2757's behavior, so fixing G8 makes it pass."
- **Method**: prototype (ran the test; then replayed its exact ledger shape through `decide_next_dispatch`)
- **Finding**: **Both halves false, and the test is repairable inside this fence.**
  The run fails verbatim with:
  ```
  AssertionError: {'dispatched': True, 'reason': 'REVIEW completed without a recorded
   verdict — re-run review', 'row_id': '8e', 'skill': '/do-pr-review'}
  ```
  The test's fixture sets `pr_number = 918274`, so **G8 never fires on it** — it does
  not exercise #2757's shape at all. Its red is entirely row 8e. Replaying its
  stage_states through the router:
  - as written (thru DOCS, `pr_number`, no verdict) → row 8e
  - plus `MERGE: completed` → **still** row 8e (a terminal marker does not help)
  - plus `latest_review_verdict: "APPROVED"` → **row 10**, the assertion it makes
  Since #2062 WS3a, `_rule_ready_to_merge` requires a *recorded APPROVED verdict*
  (`:1476-1477`); row 10 has been unreachable for this fixture since that shipped,
  independent of anything G8 does. Two further measurements pin the repair: with
  `context["pr_head_sha"]` empty (the fail-closed sentinel `_build_context` sets when
  the head lookup fails) the same state routes to **row 8f**, not row 10 — so the
  test's `subprocess.run` fake must also answer `headRefOid`, and the recorded
  verdict must carry a matching `head_sha`.
- **Confidence**: high (measured, not reasoned)
- **Impact on plan**: **no router change and no fence extension.** The red is a
  stale test fixture that #2062 invalidated, not a second hole. The test is repaired
  in `tests/` — the only files this plan touches besides the verifier — and nothing
  is left red or `xfail`. See Key Elements for why making row 8e defer on a terminal
  pipeline is rejected outright rather than deferred.

### spike-6: Is the `not pr_number` term covered by any existing test?
- **Assumption**: "Changing this predicate will show up as a test failure if it is wrong."
- **Method**: code-read of `tests/unit/test_sdlc_next_skill.py::TestStageArtifactVerification` and `tests/unit/test_lane_identity.py`
- **Finding**: **No test covers it.** Every BUILD-claim test in the suite supplies
  `meta = {"pr_number": 555}` and varies only the live state (`OPEN`, `CLOSED`,
  `MERGED`). The two tests that pass `{"pr_number": None}`
  (`test_lane_identity.py:342,373`) claim `PLAN`/`PATCH` and never `BUILD`, so they
  never reach the term. The only currently-reachable branch of the BUILD check that
  fires in production is the one branch with zero coverage.
- **Confidence**: high
- **Impact on plan**: the fix cannot regress an existing assertion, and the
  red-test-first step is mandatory rather than ceremonial — there is no existing red
  to inherit.

### spike-7: Negative control — does a complete ledger still route correctly?
- **Assumption**: "The bug needs a degraded ledger; a healthy terminal pipeline is fine."
- **Method**: prototype (live `sdlc-tool next-skill` on #2755, read-only)
- **Finding**: **True.** #2755 (merged today, `pr_number = 2815`, APPROVED verdict
  with `head_sha = a0cd7dc68`, `MERGE = completed`) returns `{"skill": "/do-merge",
  "row_id": "10", "dispatched": true}`. The bug is conditional on the ledger having
  lost `pr_number`, not on merge itself.
- **Confidence**: high
- **Impact on plan**: #2755 is named in Verification as the live negative control,
  and the fix must leave its routing unchanged.

### spike-8: Does silencing G8 actually stop the rebuild?
- **Assumption**: "G8 is the only row that re-dispatches `/do-build` on this shape." (spike-1's implicit premise.)
- **Method**: prototype — `decide_next_dispatch` with a full terminal `stage_states`, varying `pr_number` and `branch_exists`
- **Finding**: **No. The rebuild relocates to row 5.** `_rule_branch_exists_no_pr`
  (`agent/sdlc_router.py:922-927`) returns `True` on `context["branch_exists"] is
  True` alone when `pr_number` is falsy — it does not consult `BUILD` at all in that
  branch — and row 5 precedes every PR-gated row. Measured:
  `pr_number` absent + `branch_exists: True` → **`/do-build`, row 5**;
  `pr_number` absent + `branch_exists: False` → `Blocked(NO_RULE)`.
  `deleteBranchOnMerge` is `false` on this repo and merged lane branches persist, so
  the row-5 column is the normal post-merge state here.
- **Confidence**: high (measured through the real router)
- **Impact on plan**: this is the finding that forced the restructure. A
  verifier-only fix cannot deliver the plan's own stated outcome, and the original
  Success Criterion 1 would have certified it anyway. Every acceptance assertion is
  now row-agnostic.

### spike-9: Is `pr_number` actually unrecoverable after merge, or just looked up wrong?
- **Assumption**: "The ledger lost `pr_number`; recovering it means inventing a second identity resolver on a hot path." (The original Rabbit Hole.)
- **Method**: prototype — live `_lookup_pr` calls against the three reported issues, both `state` values
- **Finding**: **It is looked up wrong, and the fix is one keyword argument.**
  `tools/sdlc_stage_query.py::_compute_meta` calls `_lookup_pr(...)` at `:548`
  without `state`; the signature defaults to `state="open"` (`:338-342`), which
  excludes merged PRs by construction. Measured live: #2638 → `None` under `open`,
  **2686** under `all`; #2566 → `None` / **2665**; #2640 → `None` / **2671**. Those
  are the exact PRs this plan's Problem section names.
  `tools/sdlc_stage_marker.py:246-250` already carries the identical fix under
  **#2539**, and `agent/pipeline_state.py:1564` independently uses a
  `for state in ("open", "all")` two-pass fallback with the rationale spelled out in
  its docstring.
- **Confidence**: high (measured live against `gh`)
- **Impact on plan**: the Rabbit Hole "Rebuilding `pr_number` from the world" was
  attacking a strawman — this is the *existing* resolver with the filter corrected,
  not a new one. Restoring `pr_number` becomes the plan's load-bearing fix.

### spike-10: Does restoring `pr_number` produce the right outcome, not merely a different one?
- **Assumption**: "Even with `pr_number` restored, a terminal pipeline still needs a new router row to land somewhere sane." (The premise behind #2817.)
- **Method**: prototype — same terminal `stage_states`, `pr_number = 2686`, recorded APPROVED verdict with a `head_sha` matching `context["pr_head_sha"]`
- **Finding**: **It reaches the terminal row under both branch states.**
  `pr_number: 2686` + `branch_exists: True` → **`/do-merge`, row 10**;
  `branch_exists: False` → **`/do-merge`, row 10**. One dependency is worth naming
  because it caused a false start: with the verdict's `head_sha` absent or mismatched
  against `pr_head_sha`, the same state routes to **row 8f** (`/do-pr-review`), not
  row 10 — the #2062 WS3d stale-verdict row. The head-SHA attribution is load-bearing
  for reaching row 10, which is the same dependency spike-5 identified in the
  integration fixture.
- **Confidence**: high (measured)
- **Impact on plan**: the primary fix produces a *correct* terminal outcome rather
  than a merely silent one, and it deletes the `Blocked(NO_RULE)` residual. **#2817
  is moot** and moves from a No-Go to a close-with-evidence action.

## Data Flow

1. **Entry point**: `sdlc-tool next-skill --issue-number N` → `tools/sdlc_next_skill.py::main` → `decide()`.
2. **Lock peek**: the issue-ownership peek (`:513-549`) short-circuits on foreign
   ownership. **Untouched by this plan** (#2766's lane owns that block).
3. **State read**: `_resolve_enriched` → `tools/sdlc_stage_query.query_enriched`
   returns `{"stages": {...}, "_meta": {...}}`. `_meta.pr_number` and
   `stages["MERGE"]` both originate here, from the same `PipelineLedger` record —
   which is why they are lost together (Risk 1).
4. **Durability recovery**: when `stage_states == {}` *exactly*,
   `_recover_stage_states_from_durable_signals` (#2395) attempts reconstruction. A
   *partially* populated ledger is deliberately left alone — so the partial-loss
   shape spike-3 identifies flows through untouched.
5. **Context assembly**: `_build_context(...)` → `_verify_stage_artifacts(...)` →
   **`_verify_stage_artifacts_live(stage_states, meta, issue_number)`** — the sole
   site this plan modifies. It returns either `{}` or
   `{"stage_artifacts_verified": False, "unverified_stage": <STAGE>}`.
6. **Routing**: `decide_next_dispatch(stage_states, meta, context)` walks
   `GUARDS = [G1, G2, G3, G4, G8, G7, G5, G6]`, then the 18-row dispatch table.
   `guard_g8_artifact_verification` reads only the two flags from step 5.
7. **Output**: JSON on stdout — `{"skill", "reason", "row_id", "dispatched"}` or
   `{"blocked", "reason", "guard_id"}`.

The change is confined to step 5, and it only ever moves an outcome from
"flags set" to "flags unset". It cannot introduce a new dispatch, only remove one.

## Architectural Impact

- **New dependencies**: none. No new import, no new subprocess, no new model access.
  The change *removes* one condition and adds one dict read against data already in
  the function's parameters.
- **Interface changes**: none externally. `_verify_stage_artifacts_live` keeps its
  signature and return contract. One new module-private helper,
  `_pipeline_is_terminal_from_states(stage_states) -> bool`, exists to give the
  terminal predicate a name, a docstring, and a grep anchor.
- **Coupling**: unchanged, and deliberately so. The verifier does **not** gain an
  import of `agent/pipeline_state.py` or of `tools/_sdlc_run_identity.py` — see
  Key Elements for why calling the existing `_pipeline_is_terminal` is the wrong
  move here despite being the same predicate.
- **Data ownership**: unchanged. The verifier remains read-only.
- **Reversibility**: very high. Two edits in one function; reverting restores the
  present behavior exactly.
- **Behavioral blast radius**: G8 fires strictly less often than before. Every state
  in which it fired *and* had a `pr_number` is unchanged (spike-6 confirms the
  existing tests pin those). No state that previously advanced now blocks.

## Appetite

**Size:** Medium

**Team:** Solo dev, validator, documentarian

**Interactions:**
- PM check-ins: 1 (only if the builder believes the fence must widen — it should not)
- Review rounds: 1

The code is roughly ten lines. The Medium sizing is bought by the test-fixture
reconstruction (spike-5's two-part repair) and by three documentation surfaces that
currently describe the BUILD check in terms this change falsifies.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable for the integration test's real ledger | `.venv/bin/python -c "from agent.pipeline_ledger import PipelineLedger; PipelineLedger.query.filter(ledger_key='nonexistent')"` | `TestStageArtifactVerificationGate` uses a real `PipelineLedger` |
| `gh` authenticated for the live negative control | `gh auth status` | spike-7's #2755 re-check in Verification |
| The live negative control is still terminal | `.venv/bin/python -m tools.sdlc_next_skill --issue-number 2755` | Must return row 10 before and after the change |

## Solution

### Key Elements

- **The BUILD check stops treating an absent `pr_number` as a falsified claim.**
  The predicate becomes "claimed *and* identifiable": the check runs only when
  `build_claimed and pr_number`, exactly mirroring `patch_claimed`'s
  `and bool(lane_branch)` two lines above it. When the identifier is absent, log at
  **debug** (matching the PATCH skip's log level and wording at `:258-263`) and
  no-op. This is the load-bearing change: it is the only one that reaches the
  partial-loss shape where `MERGE` was the field the ledger dropped (spike-3).
  It is also the only change that would have prevented the three reported
  reproductions, since their ledgers carry no `MERGE` marker either.
- **A terminal pipeline is not verified at all.** A new module-private predicate
  `_pipeline_is_terminal_from_states(stage_states)` returns
  `stage_states.get("MERGE") == "completed"`, and `_verify_stage_artifacts_live`
  returns `{}` immediately when it is true — **above all three artifact checks**,
  not inside the BUILD branch. Placement matters: it is the only thing that protects
  the PATCH check, whose merged-skip is unreachable without a `pr_number`
  (spike-4), and the PLAN check, which currently no-ops on terminal lanes only by
  accident (`find_plan_path` scans `docs/plans/` non-recursively, so a plan migrated
  to `docs/plans/completed/` resolves to `None`; a terminal lane whose plan has not
  yet been migrated still pays for a `git show`).
- **The terminal predicate is read from the dict in hand, not re-read from the
  ledger — and this is not a second definition.** `tools/_sdlc_run_identity.py::_pipeline_is_terminal`
  already owns the notion ("MERGE == completed", fail-open to `False`), and this
  plan does **not** call it, for three reasons that must appear in the new helper's
  docstring:
  1. It reaches the marker through `PipelineStateMachine.for_issue`, i.e. an import
     of `agent/pipeline_state.py` — a module another lane is actively merging (#2802).
     Putting this fix's correctness on a concurrently-edited module is a needless
     coupling.
  2. It performs a fresh ledger read to obtain the dict `_verify_stage_artifacts_live`
     was already handed as its first parameter. A next-skill tick is on a hot path;
     a redundant Redis round-trip per call is a real cost with no information gain —
     and a *second* read can disagree with the first, which is strictly worse than
     one consistent view.
  3. The *predicate* is byte-identical. What differs is the source of the dict, not
     the definition of terminal. The docstring names `_pipeline_is_terminal` as the
     definitional twin so a future reader finds both, and states that if a third
     consumer ever appears, the correct move is to extract one pure
     `pipeline_is_terminal(states)` helper — not to add a third spelling.
- **`GUARDS` is not reordered, and `agent/sdlc_router.py` is not touched.** Guard
  reordering was the obvious alternative and spike-2 rules it out on evidence: G6
  cannot fire without a `pr_number`, which is the reported shape. Fixing the
  verifier makes G8 no-op and lets whatever the dispatch table decides own the
  outcome, which is the correct division of labor — G8's job is to catch a lie, and
  silence is not a lie.
- **Row 8e is left exactly as it is.** spike-5 establishes that the red integration
  test is a stale fixture, not a second hole: it sets `pr_number`, so it never
  reaches G8; its `row_id == "10"` assertion has been unreachable since #2062 WS3a
  made row 10 require a recorded APPROVED verdict; and supplying that verdict routes
  it to row 10 as written. Making 8e defer on `MERGE == completed` is rejected on
  its merits, not merely fenced out: 8e exists because a `REVIEW: completed` marker
  with no recorded verdict is **unearned** (the #1897 no-owner state). A ledger that
  additionally claims `MERGE: completed` without a verdict is *less* trustworthy,
  not more — it would be asking the router to accept a merge marker from the same
  self-attestation channel whose unverified claim it is currently refusing. So the
  test is repaired rather than the router, nothing ships red, and nothing is
  `xfail`-ed.
- **The repaired test is split in two, because it was doing one job badly.** The
  existing test's name promises "terminal merged pipeline routes to merge, not
  build" but its fixture only ever exercised the merged-PR-state path (#1267's
  concern). It keeps that job, with the verdict and head-SHA its assertion has
  required since #2062. A **new** sibling covers #2757's actual shape — `BUILD` and
  `MERGE` completed with **no** `pr_number` — and asserts the negative that spike-1
  bounds: the result is not a G8 `/do-build`. It deliberately does not assert
  `/do-merge`, because spike-1 measured that outcome as `Blocked(NO_RULE)` and
  pinning a wrong expectation is how the current test became a liability.

### Flow

**`sdlc-tool next-skill --issue-number N`** → lock peek → ledger read
(`stages` + `_meta`) → context assembly → **`_verify_stage_artifacts_live`**:

```
MERGE == completed?  ──yes──► return {}                (nothing to verify; the pipeline is done)
        │ no
        ▼
PLAN claimed + plan path resolves?  ──► git show main:<path>  ──fail──► unverified PLAN
        │
        ▼
BUILD claimed?
   ├─ no pr_number  ──► debug log, skip                (unverifiable, NOT falsified)  ◄── the fix
   └─ pr_number     ──► gh pr view --json state
                          ├─ OPEN | MERGED ──► verified
                          └─ otherwise     ──► unverified BUILD
        │
        ▼
PATCH claimed + lane branch resolves?  ──► merged? skip : git ls-remote  ──fail──► unverified PATCH
        │
        ▼
return {}
```

→ router walks `[G1, G2, G3, G4, G8, G7, G5, G6]` with the flags unset → G8 no-ops →
the dispatch table owns the outcome.

### Technical Approach

All edits are in `tools/sdlc_next_skill.py::_verify_stage_artifacts_live` and its
new helper, plus tests. Line references are at `1c48b97f2`.

- **Add `_pipeline_is_terminal_from_states(stage_states: dict) -> bool`** immediately
  above `_verify_stage_artifacts_live`. Body is a single comparison against
  `"completed"`. Docstring carries: the definitional-twin note naming
  `tools/_sdlc_run_identity.py::_pipeline_is_terminal`; why this reads the parameter
  rather than re-reading the ledger; and the "extract, don't add a third spelling"
  instruction for a future third consumer.
- **Short-circuit at the top of `_verify_stage_artifacts_live`** (before the
  `find_plan_path` / `resolve_lane_slug` block at `:211-219`, so a terminal lane
  pays for no resolution at all): `if _pipeline_is_terminal_from_states(stage_states):`
  log at debug and `return {}`. The debug log names the issue number and the reason,
  in the register of the existing `:259-263` skip.
- **Gate the BUILD check on an identifiable artifact.** Fold `pr_number` into the
  claim the way PATCH already does — the cleanest form keeps the `pr_state` fetch
  guard (`:269`) unchanged and rewrites `:272-278` so the mismatch branch is reached
  only when a `pr_number` exists. When `build_claimed` is true and `pr_number` is
  absent, emit a **debug** log ("BUILD claims completed but no PR number is
  recorded; skipping the live PR check rather than reporting a claim it cannot
  verify") and fall through. Do **not** leave the old term behind as an `elif` — the
  Verification table asserts the removed term is gone, and a demoted copy trips it.
- **Update the function docstring (`:189-210`).** Its `#1267 merged-pipeline
  misfire` paragraph is the natural home: extend it with the #2757 case, stating the
  three-state distinction (verified / falsified / unverifiable) and that the terminal
  short-circuit is the outer belt while the identifiability guard is the load-bearing
  fix. **Paraphrase the removed condition; do not quote it** — the anti-criterion row
  in Verification greps the whole file, comments included, and quoting the deleted
  term in prose explaining its deletion is a self-inflicted red.
- **Do not touch** `_fetch_pr_state`, `_check_branch_pushed`,
  `_check_plan_committed_on_main`, `_build_context`, the lock-peek block, or
  `_verify_stage_artifacts`'s narrow fail-open catch (`:316-330`). The catch's
  scoping is #1267 Concern 4 and is not in question here.
- **Test-side (`tests/integration/test_sdlc_session_ensure_integration.py`)**:
  extend `_fake_gh_pr_view_merged` to answer `headRefOid` for the `gh pr view` call
  and to answer `git ls-remote origin refs/pull/N/head` (the git-first resolver
  `tools/pr_head_resolver.resolve_pr_head_sha` tries first), so the head lookup
  yields a real SHA instead of the fail-closed empty sentinel. Add a `_verdicts`
  entry to the repaired test's ledger: `REVIEW` = APPROVED with `head_sha` equal to
  that SHA. Add `MERGE: "completed"` to its stage_states so the test's name is
  honest. Then add the new no-`pr_number` sibling.
- **Test-side (`tests/unit/test_sdlc_next_skill.py`)**: add to
  `TestStageArtifactVerification` — a claimed BUILD with no `pr_number` runs **zero**
  subprocesses and leaves the flags unset; a terminal `stage_states` runs zero
  subprocesses even with a falsifiable BUILD claim; and the existing CLOSED-PR
  behavior is unchanged. Assert on a `MagicMock` for `subprocess.run` with
  `assert_not_called()`, following `test_no_claimed_artifact_is_a_noop` (`:537-560`).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new exception handler is introduced. `_verify_stage_artifacts`'s existing
  narrowed catch (`subprocess.TimeoutExpired` / `SubprocessError` / `OSError` →
  fail open; anything else → re-raise) is unchanged, and its four existing tests
  (`test_fails_open_on_infra_error`, `test_fails_open_on_os_error`,
  `test_non_infra_exception_does_not_silently_advance`,
  `test_missing_stage_states_or_meta_skips_verification`) must still pass untouched.
- [ ] The terminal short-circuit runs **before** any subprocess, so it cannot itself
  raise an infra error. Assert this positively: a terminal `stage_states` with a
  `subprocess.run` mock that raises on any call must still return `{}`.

### Empty/Invalid Input Handling
- [ ] `meta` missing `pr_number` entirely, `pr_number = None`, and `pr_number = 0`
  all take the unverifiable path. Parametrize into one test — three tests for one
  falsiness check is over-testing.
- [ ] `stage_states` missing `MERGE`, `MERGE = None`, `MERGE = "pending"`,
  `MERGE = "failed"` all read non-terminal. Only the exact string `"completed"` is
  terminal; anything else must leave verification armed.
- [ ] `stage_states = {}` (the wholly-empty ledger) — `build_claimed` is `False`, so
  G8 cannot fire regardless. Assert it, since it is the state the three reported
  issues are in right now and a reader will ask.

### Error State Rendering
- [ ] The unverifiable-BUILD skip logs at **debug**, not warning: it is a normal,
  expected state, and a warning per tick on every ledger-degraded issue is noise
  that trains operators to ignore the channel. The existing falsified-BUILD warning
  (`:274-277`) stays at warning and keeps its wording. Both are asserted by
  `caplog` level in tests — the level distinction is the user-visible contract.
- [ ] The terminal short-circuit logs at debug with the issue number, so an operator
  debugging "why did next-skill do nothing" can see the reason in the log.

## Test Impact

- [ ] `tests/integration/test_sdlc_session_ensure_integration.py::TestStageArtifactVerificationGate::test_terminal_merged_pipeline_routes_to_merge_not_build`
  — **UPDATE (currently RED on main)**: add a recorded APPROVED `REVIEW` verdict
  with a `head_sha`, add `MERGE: "completed"`, and extend the `gh`/`git` fake to
  answer the head-SHA lookup. Keeps its `row_id == "10"` assertion, which spike-5
  measured as reachable once the verdict exists. Its docstring must be rewritten:
  the current text explains a #1267 failure mode that is not why it is red.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py::TestStageArtifactVerificationGate::_fake_gh_pr_view_merged`
  — **UPDATE**: answer `headRefOid` on `gh pr view` and a `refs/pull/N/head` line on
  `git ls-remote`. Its existing "answer branch-gone to prove the merged skip is load
  bearing" behavior for `git ls-remote --heads` is preserved — do not collapse the
  two `git ls-remote` shapes into one response.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py::TestStageArtifactVerificationGate::test_g8_redispatches_build_on_synthesized_false_pr_claim`
  — **UNCHANGED, and must stay green**: `pr_number` present + live state CLOSED must
  still produce `row_id == "G8"` and `/do-build`. This is the control proving the
  fix narrowed the gate rather than disarming it.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py` — **ADD**
  `test_no_pr_number_recorded_does_not_redispatch_build`: `BUILD` and `MERGE`
  completed, **no** `pr_number`, real ledger — assert the result is not
  `{"skill": "/do-build", "row_id": "G8"}`. The #2757 regression test proper; it
  does not exist today in any form.
- [ ] `tests/integration/test_sdlc_session_ensure_integration.py:19-26` — **UPDATE**:
  the module docstring describes the class as driving "a synthesized false
  BUILD-completion claim"; it gains a second, complementary shape.
- [ ] `tests/unit/test_sdlc_next_skill.py::TestStageArtifactVerification` — **ADD**
  four cases: BUILD claimed with no `pr_number` → zero subprocess calls, flags
  unset; terminal `stage_states` → zero subprocess calls even with a falsifiable
  BUILD claim and a resolvable plan/branch; `MERGE` in a non-`completed` state →
  verification still armed; the debug-vs-warning log-level distinction.
- [ ] `tests/unit/test_sdlc_next_skill.py::TestStageArtifactVerification::test_false_build_claim_sets_unverified_stage`
  and the two `test_true_build_claim_*` cases — **UNCHANGED**: all supply
  `pr_number = 555` (spike-6), so none of them touch the modified term. Their
  continued passing is itself an assertion.
- [ ] `tests/unit/test_lane_identity.py:342,373` — **UNCHANGED**: both pass
  `{"pr_number": None}` but claim only `PLAN`/`PATCH`, and neither sets `MERGE`, so
  neither is affected by either change. Re-run them to confirm.

**No `xfail` conversions.** A repo-wide grep for `pytest.mark.xfail` /
`pytest.xfail(` across `tests/` returns nothing at all, so there is no
expected-failure marker to convert and none is introduced by this plan.

**Red-test-first (mandatory).** Before any edit to `tools/`, write
`test_no_pr_number_recorded_does_not_redispatch_build` and watch it fail against the
current module, recording the failure text. spike-6 establishes that no existing
test covers the term being changed — so a suite that passes after the fix proves
nothing on its own.

## Rabbit Holes

- **Rebuilding `pr_number` from the world.** "The PR is discoverable from
  `gh pr list --search`, so resolve it instead of skipping." That is a second
  identity resolver on a hot path, with the cross-repo and multi-PR ambiguity
  hazards `tools/merge_predicate.py`'s docstring catalogs, added inside a *gate*
  whose whole job is to not invent facts. If ledger durability is the problem, fix
  the ledger (#2730's lane) — do not paper over it in a verifier.
- **Making the verifier fail closed on a missing `pr_number`** ("if we cannot verify,
  refuse to advance"). It is superficially the safe posture and it is what the
  current code accidentally does. It is wrong here: the observed consequence is
  re-dispatching `/do-build` on merged work, which is a *duplicate-PR* risk, not a
  safety property. #1267 already chose narrow fail-open for this gate and named the
  #2003 merge gate as the hard backstop.
- **Fixing the ledger-loss mechanism.** Real, live (Risk 1), and someone else's
  lane. This plan's contract is to behave sanely on a degraded ledger, not to
  prevent degradation.
- **Adding a terminal dispatch row or verdict.** Filed as #2817. It is a router
  change, it is not needed to stop the false rebuild, and doing it here would
  require the fence extension this plan exists to avoid.
- **Auditing the other two artifact checks for the same bug.** Tempting for symmetry
  — but PATCH already has the guard (`:257`) and PLAN already has it (`plan_path is
  not None`, `:221`). BUILD was the only one missing it. Confirm by reading, then
  stop; do not restructure all three into a table-driven loop.

## Risks

### Risk 1: The ledger loses state that was written — and it is losing it now
**Impact:** #2638, #2566 and #2640 read *entirely* empty at plan time (`stages: {}`,
`pr_number: null`, `slug: null`), and a full ledger wipe on a live lane was observed
mid-run on 2026-08-14 around the merge of #2803 (`ec585ceb9`). Causation is not
established — the correlation is recorded, not asserted. What matters here is that
"the ledger lost fields that were written" is a live mechanism on this machine, so
the verifier must be correct on partial state, not just on the happy path. The
dangerous case is **partial** loss: `BUILD == completed` survives while `MERGE` and
`pr_number` are gone. A MERGE-marker short-circuit is blind to it by construction.
**Mitigation:** the identifiability guard, not the short-circuit, is the fix for
this shape, and the plan is ordered to say so (spike-3). A wholly-empty ledger is
already safe (`build_claimed` is `False`, so G8 cannot fire) and is asserted as a
test case. The partial shape gets its own test. Measured routing for partial shapes
with G8 silent: `Blocked(NO_RULE)` or `/do-plan` — never a rebuild.

### Risk 2: The gate is narrowed on the one branch nobody tests, so a real false claim slips through
**Impact:** G8's purpose is to catch a self-attested BUILD marker with no PR behind
it. After this change, a lane that genuinely never opened a PR *and* never recorded
a `pr_number` no longer trips G8. That is a real reduction in coverage, and it is
deliberate: the verifier cannot distinguish "never built" from "ledger forgot," and
guessing was the bug.
**Mitigation:** the state is not unowned. The dispatch table still routes on the
surviving stage markers — measured: `{BUILD: completed}` alone routes to
`/do-plan` (row 1), and a branch-with-no-PR routes to `/do-build` via row 5
(`_rule_branch_exists_no_pr`), which is the *correct* owner of "build must create
the PR" and reaches it through evidence rather than through a failed verification.
The #2003 merge gate remains the hard backstop that a PR-less lane cannot merge. And
the control test (`test_g8_redispatches_build_on_synthesized_false_pr_claim`) pins
that a recorded-but-dead PR still fires G8.

### Risk 3: The terminal short-circuit trusts a self-attested marker
**Impact:** `MERGE == completed` arrives through the same `<!-- OUTCOME {...} -->`
self-attestation channel that G8 exists to distrust. A lane that falsely marks
MERGE completed would switch off all three artifact checks for itself.
**Mitigation:** bounded and accepted. The short-circuit only ever *removes* a
re-dispatch; it cannot cause one. A falsely-terminal lane therefore gets less
nagging, not a wrong action — and it still has no `pr_number`-bearing route to
`/do-merge` (spike-1), so it cannot merge on the strength of the lie either.
Recorded in the helper's docstring so the trade is visible rather than implicit.
`MERGE` is also the one marker that the stage-marker write path already refuses to
record casually, and `tools/_sdlc_run_identity.py` already treats it as terminal for
a strictly more consequential decision (declining to re-mint a run identity) — so
this plan is not extending trust, it is matching existing trust.

### Risk 4: The repaired integration test is repaired to the wrong expectation
**Impact:** the test currently asserts `row_id == "10"` on a fixture that cannot
reach row 10. If the repair guesses at the missing pieces, it will either stay red
or be quietly weakened to assert something trivially true — which is how it became
useless the first time.
**Mitigation:** the repair is spelled out from measurement, not inference (spike-5):
the verdict must be APPROVED *and recorded*, its `head_sha` must match what the
head-SHA resolver returns, and the fake must answer both the git-first and `gh`
fallback paths or the fail-closed sentinel routes to row 8f. The Verification table
runs the whole class, and the new #2757 test asserts a *negative* precisely because
spike-1 showed the positive is not what happens.

### Risk 5: `main` moves under the fix
**Impact:** `main` advanced twice during plan authoring, and two active plans
(#2766, #1799) name the same file. A build that starts from a stale checkout will
produce a conflicting patch in a function with four recent contributors.
**Mitigation:** the fence is one function and one helper, and neither active plan
touches `_verify_stage_artifacts_live` (#2766 owns the lock-peek block at `:513-549`;
#1799 owns `_build_context`). Re-read the function at branch time; if either lane
has landed inside it, stop and report rather than rebasing by hand.

## Race Conditions

### Race 1: The ledger changes between the stage read and the routing decision
**Location:** `tools/sdlc_next_skill.py::decide` — `_resolve_enriched` (`:552`)
through `decide_next_dispatch`
**Trigger:** a concurrent stage-marker write (another lane, a supervisor, a
reflection) lands between the ledger read and the guard walk. In particular, `MERGE`
can flip to `completed` in that window.
**Data prerequisite:** none new — `stage_states` and `meta` are read once, together,
from one `PipelineLedger` record, and this plan reads only from that snapshot.
**State prerequisite:** the terminal short-circuit and the BUILD check must agree
about the same snapshot.
**Mitigation:** structural, and improved by this plan. Reading `MERGE` from the
already-materialized `stage_states` guarantees the terminal decision and the artifact
decision see one consistent view; calling `_pipeline_is_terminal` instead would issue
a **second** ledger read that could disagree with the first, creating a window where
the pipeline is judged terminal for one purpose and mid-flight for another. Both
outcomes of a stale read are benign: a just-turned-terminal pipeline read as
non-terminal now falls through to a BUILD check that no-ops anyway (no `pr_number`
after merge is the whole premise), and the next tick sees the marker.

### Race 2: The PR merges between the ledger read and the `gh pr view` call
**Location:** `_verify_stage_artifacts_live:269-278`
**Trigger:** a PR merges in the milliseconds between the ledger snapshot and the
live state read.
**Data prerequisite:** a recorded `pr_number` (otherwise no call is made at all).
**State prerequisite:** none.
**Mitigation:** pre-existing and unchanged — `MERGED` is an accepted state (#1267),
so a mid-flight merge verifies clean either way. This plan adds no new live call and
removes one call path.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2817] **A named terminal verdict for next-skill.** spike-1
  measured that a terminal pipeline with no recoverable `pr_number` returns
  `Blocked(reason='no matching dispatch rule', guard_id='NO_RULE')` once G8 stops
  firing. That is honest but reads like a routing bug, and every terminal route
  (row 10, G6) is gated on `pr_number`. Producing a real "pipeline complete" verdict
  is a change to `agent/sdlc_router.py`'s guard/dispatch surface, outside this
  plan's fence, and filed as #2817.
- [SEPARATE-SLUG #2730] **Fixing the ledger loss itself.** The empty ledgers on
  #2638/#2566/#2640 are the precondition for this bug, not the bug. The
  ledger-durability lane owns them. This plan's obligation is to behave correctly on
  a degraded ledger.
- **Reordering `GUARDS` so G6 precedes G8.** Not deferred — **rejected on
  evidence.** G6 returns `None` without a `pr_number` (spike-2), which is the exact
  reported shape, so the reorder cannot fix this bug while weakening #1267's
  G4-bounds-G8 contract for every lane.
- **Making row 8e defer on a terminal pipeline.** Not deferred — **rejected on the
  merits** (spike-5 + Key Elements). 8e owns the unearned-`REVIEW`-marker state; a
  ledger that also claims `MERGE` without a verdict is less trustworthy, not more.
  The red test it produces is a stale fixture and is repaired inside this fence.
- **Any edit to `agent/pipeline_state.py`, dispatch records, or
  `tools/sdlc_session_ensure.py`.** Active lanes (#2802, #2766). Not touched, not
  imported by anything this plan adds.

## Update System

No update system changes required. This is a predicate correction inside one
existing module: no new dependency, no config file, no secret, no env var, no
Popoto model change and therefore no entry in `scripts/update/migrations.py`.

Standard post-merge propagation applies — `/update` on each machine, then a worker
restart so long-lived sessions pick up the new module. Note that `sdlc-tool
next-skill` is invoked as a fresh subprocess per call, so the fix takes effect on
the next invocation without any restart; the restart is for consistency, not
correctness.

## Agent Integration

No agent integration required. `sdlc-tool next-skill` is already a registered CLI
entry point in `pyproject.toml [project.scripts]` and is already the routing surface
every SDLC skill calls. This plan changes what that command returns in one state; it
adds no command, no flag, no MCP tool, and no bridge import.

The one agent-visible surface is the JSON on stdout, and the change to it is
subtractive: in the reported state it stops returning a `/do-build` dispatch. Skills
consuming that JSON already handle both the `dispatched` and `blocked` shapes
(`.claude/skills/sdlc/SKILL.md:261`), and `NO_RULE` is an existing `guard_id`, not a
new one.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-router-oscillation-guard.md` — the G8 doc of
  record. Its **"Verified artifact set (top 3, deterministic)"** table (`:127-134`)
  says BUILD is "verified when state is `OPEN` or `MERGED`", which omits the case
  that matters: no recorded PR number means no check at all. Add the unverifiable
  column/qualifier to the BUILD row, and add a short paragraph after the table
  stating the three-state distinction (verified / falsified / unverifiable) and the
  terminal short-circuit, with the reason a MERGE-marker check alone is insufficient.
  The existing note at `:135-136` ("A stage with no claimed artifact ... is a no-op —
  verification never invents a check") is the sentence this fix finally makes true
  for BUILD; extend it rather than writing a parallel one.
- [ ] Update `docs/features/sdlc-pipeline.md` — the **"Stage-Advance Verification
  Gate (G8, issue #1267)"** section (`:50-80`). Its bullet list describes where
  verification runs, positioning, firing condition, and contract; add the terminal
  short-circuit to the firing-condition bullet and note that an unresolvable
  artifact identifier is a no-op, not a mismatch.
- [ ] Update `.claude/skills/sdlc/SKILL.md:188` — "**G8 makes no live calls**"
  paragraph. It describes the verifier's live checks; add one clause that a stage
  whose artifact identifier is absent, or a pipeline whose `MERGE` stage is
  completed, is skipped rather than reported as a mismatch. Keep it to one clause —
  this file is read into every SDLC session's context.
- [ ] No new feature doc, and therefore no `docs/features/README.md` index entry.
  Creating `docs/features/g8-terminal-*.md` would be a parallel artifact for a
  behavior already documented in two places.

### Inline Documentation
- [ ] `_pipeline_is_terminal_from_states` docstring: the predicate, the definitional
  twin (`tools/_sdlc_run_identity.py::_pipeline_is_terminal`), why this one reads the
  parameter instead of re-reading the ledger, the self-attestation trade from Risk 3,
  and the "extract, don't add a third spelling" instruction.
- [ ] `_verify_stage_artifacts_live` docstring (`:189-210`): extend the
  merged-pipeline-misfire paragraph with the #2757 case and the three-state
  distinction. **Paraphrase the removed condition** — naming it verbatim trips the
  plan's own anti-criterion grep.
- [ ] The two new debug logs carry their reasons inline (unverifiable BUILD;
  terminal pipeline), in the register of the existing PATCH skip at `:258-263`.

## Success Criteria

- [ ] A pipeline with `BUILD == "completed"` and **no** recorded `pr_number` produces
  **no** G8 `/do-build` dispatch, proven by an integration test against a real
  `PipelineLedger`.
- [ ] The same holds when `MERGE == "completed"` is also absent — the partial-loss
  shape (spike-3), which a MERGE-marker check cannot see. This is the criterion that
  distinguishes a real fix from a narrow one.
- [ ] A pipeline with `MERGE == "completed"` runs **zero** artifact subprocesses —
  no `gh pr view`, no `git ls-remote`, no `git show` — proven by an
  `assert_not_called()` unit test.
- [ ] A recorded `pr_number` whose live state is `CLOSED` **still** sets
  `stage_artifacts_verified: False` and still re-dispatches `/do-build` via G8
  (`test_g8_redispatches_build_on_synthesized_false_pr_claim`, unchanged and green).
  The gate is narrowed, not disarmed.
- [ ] `test_terminal_merged_pipeline_routes_to_merge_not_build` is **green**, with no
  `xfail`, no skip, and no weakened assertion.
- [ ] `agent/sdlc_router.py`, `agent/pipeline_state.py`, and
  `tools/sdlc_session_ensure.py` are **byte-identical to `main`** in the PR diff.
- [ ] The unverifiable-BUILD skip logs at debug and the falsified-BUILD mismatch
  still logs at warning, asserted by level.
- [ ] `sdlc-tool next-skill --issue-number 2755` still returns row 10 (the live
  negative control from spike-7).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

Three agents. One function, two test files, three doc surfaces — a separate
test-engineer would cost a full context handoff for a test file the builder is
already editing line by line, and the red-test-first step makes the builder the
person who must hold both halves anyway.

### Team Members

- **Builder (verifier)**
  - Name: `g8-verifier-builder`
  - Role: write the red regression test first; then the terminal predicate, the
    identifiability guard, and the full unit + integration test surface in
    `tools/sdlc_next_skill.py` and its two test files
  - Agent Type: builder
  - Domain: Redis/Popoto data (the integration test drives a real `PipelineLedger`)
  - Resume: true

- **Validator (verifier)**
  - Name: `g8-verifier-validator`
  - Role: verify every acceptance criterion, especially the negatives — the gate
    still fires on a dead recorded PR, zero subprocesses on a terminal pipeline, no
    file outside the fence changed, nothing left red or `xfail`-ed
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `g8-verifier-documentarian`
  - Role: update `sdlc-router-oscillation-guard.md`, `sdlc-pipeline.md`, and the one
    clause in `.claude/skills/sdlc/SKILL.md` — no new feature doc, no index entry
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Per the template's Tier 1 list. The builder carries a `Domain: Redis/Popoto data`
tag — paste the matching rules from `DOMAIN_FRAMING.md` into its assignment,
particularly the never-raw-Redis rule: the integration test's ledger setup and
teardown go through `PipelineLedger.get_or_create` / `.save()` / `.delete()`, never
raw Redis, and the existing `cleanup_ledger` fixture (`:416-429`) is the pattern.

### Step by Step Tasks

### 0. Red-test-first
- **Task ID**: build-red
- **Depends On**: none
- **Validates**: `tests/integration/test_sdlc_session_ensure_integration.py`
- **Informed By**: spike-6 (no existing test covers the term being changed, so the suite cannot catch a wrong fix), spike-5 (the one test that looks like a regression test does not exercise this shape)
- **Assigned To**: `g8-verifier-builder`
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- Write `test_no_pr_number_recorded_does_not_redispatch_build`: a real
  `PipelineLedger` with `BUILD` and `MERGE` completed and **no** `pr_number`;
  assert the result is not `{"skill": "/do-build", "row_id": "G8"}`.
- Run it against the current module and **watch it fail**. Record the failure text
  in the PR description.
- Do not edit `tools/` in this task.

### 1. Fix the verifier
- **Task ID**: build-verifier
- **Depends On**: build-red
- **Validates**: `tests/unit/test_sdlc_next_skill.py`, `tests/integration/test_sdlc_session_ensure_integration.py`
- **Informed By**: spike-3 (the identifiability guard is the load-bearing fix; the MERGE short-circuit alone misses partial loss), spike-4 (the short-circuit is not redundant — it covers the PATCH check, whose merged-skip is unreachable without a `pr_number`), spike-2 (guard reordering cannot fix this)
- **Assigned To**: `g8-verifier-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `_pipeline_is_terminal_from_states(stage_states) -> bool` above
  `_verify_stage_artifacts_live`, with the docstring specified in Technical Approach
  — including the definitional-twin note naming
  `tools/_sdlc_run_identity.py::_pipeline_is_terminal` and why it is not called.
- Short-circuit `_verify_stage_artifacts_live` on a terminal pipeline **at the top
  of the function**, above the `find_plan_path` / `resolve_lane_slug` resolution, with
  a debug log.
- Gate the BUILD check on a recorded `pr_number`, mirroring `patch_claimed`'s
  `bool(lane_branch)` guard; debug-log the skip. Remove the old condition outright —
  no demoted `elif`.
- Extend the function docstring's merged-pipeline-misfire paragraph. **Paraphrase**
  the removed condition; never quote it, or the anti-criterion grep trips on the
  documentation of its own removal.
- Touch nothing else in the file. `_build_context` and the lock-peek block belong to
  other active lanes.

### 2. Complete the test surface
- **Task ID**: build-tests
- **Depends On**: build-verifier
- **Validates**: `tests/unit/test_sdlc_next_skill.py`, `tests/integration/test_sdlc_session_ensure_integration.py`
- **Informed By**: spike-5 (the exact repair: recorded APPROVED verdict + matching `head_sha` + a fake that answers both the git-first and `gh` head-SHA paths, or the fail-closed sentinel routes to row 8f), spike-6 (existing BUILD tests all set `pr_number = 555` and must stay untouched)
- **Assigned To**: `g8-verifier-builder`
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- Repair `test_terminal_merged_pipeline_routes_to_merge_not_build` per Test Impact:
  add the `_verdicts["REVIEW"]` APPROVED record with `head_sha`, add
  `MERGE: "completed"`, extend `_fake_gh_pr_view_merged` to answer `headRefOid` and
  `git ls-remote origin refs/pull/N/head` while preserving its existing
  branch-gone answer for `git ls-remote --heads`. Rewrite its docstring to say what
  it actually defends.
- Add the unit cases from Test Impact, including the parametrized falsy-`pr_number`
  case, the non-`completed` `MERGE` values, the wholly-empty `stage_states`, the
  `assert_not_called()` subprocess assertions, and the debug-vs-warning log levels.
- Leave `test_g8_redispatches_build_on_synthesized_false_pr_claim` and the three
  `pr_number = 555` unit tests untouched — their passing is an assertion.

### 3. Validate
- **Task ID**: validate-verifier
- **Depends On**: build-tests
- **Assigned To**: `g8-verifier-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row.
- Confirm each negative independently: the control test still produces `G8`; a
  terminal pipeline runs zero artifact subprocesses; no file outside the fence
  differs from `main`; no `xfail`, skip, or weakened assertion was introduced.
- **Demonstrate the removed-term anti-criterion goes red** against a
  deliberately-reintroduced condition, then revert it. A grep row never shown to
  fail has not been verified.
- Confirm no raw Redis access was introduced in the test setup or teardown.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-verifier
- **Assigned To**: `g8-verifier-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Apply every checkbox in the Documentation section. Update the three existing
  surfaces; create no new feature doc and add no index entry.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: `g8-verifier-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run all Verification rows and confirm all Success Criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_next_skill.py -q` | exit code 0 |
| Lane-identity G8 tests pass | `scripts/pytest-clean.sh tests/unit/test_lane_identity.py -q` | exit code 0 |
| Artifact-gate integration tests pass | `scripts/pytest-clean.sh tests/integration/test_sdlc_session_ensure_integration.py -q` | exit code 0 |
| Router tests unaffected | `scripts/pytest-clean.sh tests/unit/test_sdlc_router_oscillation.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| The falsified-on-absence term is gone | `! grep -q 'not pr_number' tools/sdlc_next_skill.py` | exit code 0 |
| Terminal predicate exists and is named | `grep -q '_pipeline_is_terminal_from_states' tools/sdlc_next_skill.py` | exit code 0 |
| The definitional twin is cross-referenced | `grep -q '_sdlc_run_identity' tools/sdlc_next_skill.py` | exit code 0 |
| Fence held — router and off-limits modules untouched | `git diff --quiet origin/main -- agent/sdlc_router.py agent/pipeline_state.py tools/sdlc_session_ensure.py` | exit code 0 |
| No expected-failure marker introduced | `! grep -rq 'xfail' tests/unit/test_sdlc_next_skill.py tests/integration/test_sdlc_session_ensure_integration.py` | exit code 0 |
| Live negative control still routes terminal | `.venv/bin/python -m tools.sdlc_next_skill --issue-number 2755` | output contains `"row_id": "10"` |

**Three notes on these rows.**

The removed-term row is the plan's anti-criterion and it greps the **whole file,
comments and docstrings included**. This plan mandates a rewritten docstring
explaining what changed — if the builder names the deleted condition verbatim in
that prose, the gate trips on its own documentation. Paraphrase ("the old
absent-PR-number branch"), never quote. The validator must demonstrate this row goes
**red** against a deliberately-reintroduced condition before trusting it green: a
gate that cannot fail is worse than no gate.

The "must not appear" rows use `! grep -q` rather than a count compared to zero.
`grep -c` exits 1 on zero matches, which a `set -e` runner scores as a failure, and
`grep -rc` prints per-file count lines rather than a single number. The `!`-form
exits 0 on the clean case, matching the positive rows.

The live-negative-control row depends on `gh` auth and on #2755's ledger still being
intact; it is evidence, not a hermetic gate. If that ledger has been wiped by the
time the row runs (Risk 1 makes this plausible), record the wipe and rely on the
integration tests — do not "fix" the row by weakening it.

## Critique Results

**Verdict: NEEDS REVISION** — war room 2026-08-14, FULL roster (Risk & Robustness, Scope & Value, History & Consistency).

The diagnosis at the verifier is correct and the row-8e call is honest and independently confirmed. But two findings are decisive and I verified both against the live router before recording them: **the planned fix does not fix the reported bug**, and **a cheaper fix one layer up removes the whole residual**. The plan needs restructuring around those, not patching.

### The two measurements that change the plan

**Silencing G8 relocates the rebuild to row 5.** `_rule_branch_exists_no_pr` (`agent/sdlc_router.py:922-927`) returns True on `context["branch_exists"] is True` alone whenever `pr_number` is falsy, regardless of `BUILD == "completed"`, and it sits before every PR-gated row. Measured through the router with the reported shape:

| context | dispatch |
|---|---|
| `branch_exists: True` | `/do-build`, row **5** |
| `branch_exists: False` | `no matching dispatch rule` (NO_RULE) |

This repo has `deleteBranchOnMerge: false` and `session/sdlc-2755` is still on origin after merging an hour ago, so the branch-present column is the normal case here, not the exotic one. spike-1 missed it by calling `decide_next_dispatch` with a hand-built context, bypassing `_build_context`, the only producer of `branch_exists`. Worst of all, Success Criterion 1 asserts only the absence of a **G8** `/do-build`, so the plan as written would go green while the bug persisted.

**The proximate cause is one layer up, and #2539 already solved it.** `_meta["pr_number"]` is resolved via `_lookup_pr` (`tools/sdlc_stage_query.py:548`), whose signature defaults to `state: str = "open"` (`:338-342`). A merged PR is invisible under `open` — that is why the symptom appears "minutes after their merges". `tools/sdlc_stage_marker.py:246-250` already passes `state="all"` for exactly this reason, with the comment that the artifact question is historical, not in-flight. Measured: with `pr_number` present, the same terminal ledger routes to `/do-merge` row 10 under **both** branch states. Restoring `pr_number` makes G8 pass on its own merits, stops row 5 firing, reaches the terminal row, and deletes the `Blocked(NO_RULE)` residual and #2817 entirely.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk | Silencing G8 converts the reported G8 `/do-build` into a row-5 `/do-build` on shipped work. `_rule_branch_exists_no_pr` fires on `branch_exists` alone when `pr_number` is falsy. Verified by direct measurement through the router; this repo keeps merged branches, so the hazard is live. | pending | Restructure around restoring `pr_number` (below). Strengthen the acceptance assertion from "not G8 `/do-build`" to `result.get("skill") != "/do-build"` at any row, and add a `branch_exists: True` variant. |
| BLOCKER | Risk | Root cause misdiagnosed one layer up. `_lookup_pr` defaults to `state="open"` so a merged PR is invisible to the `pr_number` fallback. The Rabbit Hole "Rebuilding `pr_number` from the world" mischaracterizes this as a second identity resolver on a hot path; it is the existing resolver one keyword argument away, with #2539 as direct precedent. | pending | Adopt a terminal-aware `state="all"` resolution in `_compute_meta`. This is the fix that makes the outcome *right* rather than merely silent. Requires a fence extension to `tools/sdlc_stage_query.py` — flagged to the operator. |
| BLOCKER | Risk | "A wholly-empty ledger is already safe" is false at `decide()`. `tools/sdlc_next_skill.py:576-579` recovers an empty `stage_states` via durable signals, and `agent/pipeline_state.py:1503-1510` iterates `("open","all")`, so a merged PR sets `BUILD = completed` while `pr_number` stays None and MERGE is never written. That is the reproduction end to end, and the MERGE short-circuit is provably inert for it. | pending | Replace the planned empty-`stage_states` unit assertion with an integration assertion exercising the recovery path: empty ledger + merged PR + existing branch → no `/do-build` at any row. |
| BLOCKER | History | The regression test passes when `decide()` crashes. `tools/sdlc_next_skill.py:605-610` catches every exception and returns `{"error": ..., "dispatched": False}`, which satisfies a negative-only assertion — so the mandatory red-test-first step could produce a green test before the fix. | pending | Pin the measured shape positively: no `error` key, plus the exact expected dispatch. |
| BLOCKER | History | The duplication rationale's first reason is factually false. `_pipeline_is_terminal`'s import of `agent/pipeline_state.py` is **function-local** (`tools/_sdlc_run_identity.py:122`); module-level imports there are stdlib-only. Importing that module never touches the off-limits one. Two spellings of the predicate already exist (`_sdlc_run_identity.py:129`, `pipeline_state.py:1101`); this plan would make three, which Development Principle 1 forbids. | pending | Add a pure `pipeline_is_terminal(states: dict) -> bool` to `tools/_sdlc_run_identity.py` and have the existing helper delegate, rather than adding a third near-homonym. |
| BLOCKER | Scope | Task `build-red` cannot pin the load-bearing term. The red test is specified with BUILD **and** MERGE completed, but after the short-circuit that state returns `{}` before reaching the BUILD branch — so the test stays green if the `not pr_number` term is reverted. | pending | The red test must set BUILD completed and **no** MERGE. Add the terminal shape as a separate case. |
| BLOCKER | Scope | The MERGE short-circuit does not close the hazard it is justified by. Its own justification (spike-4's PATCH hole) requires `pr_number` absent, but Risk 1 and spike-3 prioritize *partial* loss where MERGE is also gone — and there the short-circuit is blind. It buys partial coverage by trusting the self-attested channel G8 exists to police. | pending | Fix PATCH's unverifiability the same way BUILD's is fixed (skip the branch probe when `pr_number` is absent, since "branch gone" is then indistinguishable from deleted-on-merge). Demote the short-circuit to an optional subprocess-avoidance nicety or cut it. |
| CONCERN | Scope | Adding `MERGE: completed` to `test_terminal_merged_pipeline_routes_to_merge_not_build` makes it vacuous: neither `gh pr view` nor `git ls-remote` is reached, the fixture becomes dead code, and the repo's only end-to-end proof that a MERGED PR is an acceptable BUILD artifact stops proving anything. | pending | Repair it with the recorded verdict and `headRefOid` only. Rename it if the name is the concern. |
| CONCERN | Risk | Risk 3's mitigation is false in one branch ("no `pr_number`-bearing route to `/do-merge`" holds only when `pr_number` is absent), and nothing ever resets a stage — once MERGE is completed it is permanent, so a reopened issue would run its entire second pipeline with artifact checks disarmed. | pending | Restate as "bounded by #2003's merge predicate" and address, or explicitly accept, the reopened-issue case. |
| CONCERN | Risk, History | The MERGE-marker trust argument cites a MERGE-specific property that does not exist. The real gate is generic: `_backfill_predecessors` plus the #2554 verdict invariant (`tools/sdlc_stage_marker.py:744-766`), reinforced by MERGE being non-skippable (`:328-336`). | pending | Cite the actual mechanism by file:line. |
| CONCERN | Risk, History | `Blocked(NO_RULE)` is a human-escalation wedge, not a quiet no-op: the router documents it as "a genuine hole in the table" and the SDLC skill requires surfacing it and waiting. Under the `_lookup_pr` finding this state is routine post-merge, not rare. | pending | Moot if `pr_number` is restored. If any residual remains, state it as shipped user-visible behavior in Success Criteria. |
| CONCERN | History | #2718 and #2735 are the same commit (`e50eba258`), called "irrelevant to the root cause" in Freshness and "the closest prior fix — Succeeded" in Prior Art. The mirrored template is one day old with two unit tests of coverage. | pending | Reconcile the two paragraphs and downgrade the confidence claim. |
| CONCERN | History, Scope | The PATCH↔BUILD analogy is not the same shape: #2718 skipped a *guessed* identifier (skipping lost nothing), while an absent `pr_number` is a real coverage loss. Risk 2 concedes this; the Problem section papers over it. Relatedly, the Rabbit Hole "confirm by reading, then stop" contradicts spike-4. | pending | Make the Problem section say what Risk 2 says, and reconcile the Rabbit Hole. |
| CONCERN | History | Missing prior art: #2091 (`docs/plans/completed/fix-sdlc-router-merge-termination.md`) already adjudicated this exact stale-fixture class and produced a guardrail at `docs/sdlc/do-test.md:156-166` scoped to `tests/unit/test_sdlc_router*.py` — which is why the integration fixture escaped it. | pending | Cite it, and widen that note's scope to integration fixtures as part of Documentation. |
| CONCERN | History | A red integration test has been sitting on `main` under default collection (`pyproject.toml:155` `testpaths = ["tests"]`). Worth its own issue, and a sanity check on spike-5. | pending | File the gate-gap issue; confirm the red reproduces in a clean checkout before Task 0. |
| CONCERN | Risk, History | The fence-verification row diffs against `origin/main`, so it fails whenever main moves and touches those files (#2802 is actively merging one of them) or passes vacuously against a stale ref. | pending | Use `git diff --quiet "$(git merge-base origin/main HEAD)" -- ...` after an explicit fetch. |
| CONCERN | Risk, History | Both anti-criterion grep rows are unsound. `! grep -q 'not pr_number'` is evaded by `if pr_number is None or ...`; `! grep -rq 'xfail'` trips on the plan's own mandated docstring sentence about nothing being left xfail-ed. | pending | Anchor on `pytest.mark.xfail`/`pytest.xfail(`, and promote the behavioral `assert_not_called()` test to the load-bearing gate with the string grep demoted to advisory. |
| CONCERN | Scope | Six tasks and three agents for roughly ten lines plus tests; tasks 3 and 5 are the same work run twice. | pending | Collapse to two agents and three tasks. |
| NIT | Risk, Scope, History | Log-level assertions need `caplog.set_level(logging.DEBUG)` or they pass vacuously. Re-asserting the existing warning's level pins a log level as an interface. `_pipeline_is_terminal_from_states` contains `_pipeline_is_terminal` as a substring, making cross-reference greps ambiguous. `docs/features/sdlc-router-oscillation-guard.md:133` still describes the PLAN check by slug, false post-#2792, in the same table the documentarian edits. spike-3 over-reads a now-empty ledger as testimony about its state a day earlier. | pending | Address inline during revision. |

